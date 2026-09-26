"""Run commands in disposable workspace snapshots. Never fall back to host execution."""

import fnmatch
import json
import os
import re
from pathlib import Path
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass


class RunnerError(RuntimeError):
    pass


MAX_OUTPUT = 1024 * 1024
MAX_WORKSPACE = 256 * 1024 * 1024
EXCLUDES = ('.git', '.venv', 'venv', '__pycache__', '.env', '.env.*', '*.key', '.aider*')


@dataclass(frozen=True)
class Settings:
    backend: str = 'auto'
    image: str = 'localhost/agent-sandbox-demo:1'
    kernel: str = ''
    rootfs: str = ''
    timeout: int = 600
    exclude: tuple = ()


def run_process(argv, timeout=30, check=False):
    """Bound helper/runtime output and kill its process group on interruption."""
    output = bytearray()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, start_new_session=True) as proc:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RunnerError(f'Command timed out after {timeout}s')
                    for key, _ in selector.select(min(remaining, 0.2)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_OUTPUT:
                                raise RunnerError('Command output exceeded 1 MiB')
                proc.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
    result = subprocess.CompletedProcess(argv, proc.returncode, output.decode('utf-8', 'replace'))
    if check and result.returncode:
        raise RunnerError(f'{argv[0]} failed ({result.returncode}): {result.stdout}')
    return result


def snapshot(source, destination, extra_excludes=()):
    """Copy regular files without following links or copying special files."""
    source = Path(source).resolve()
    if not source.is_dir() or source == Path('/'):
        raise RunnerError('Choose a project directory, not the filesystem root')
    if destination.resolve().is_relative_to(source):
        raise RunnerError('Snapshot destination must be outside the project')
    destination.mkdir(mode=0o755)
    total = 0

    def copy_dir(src_fd, relative_dir, dst):
        nonlocal total
        # Directory descriptors prevent a concurrent directory-to-symlink swap
        # from redirecting traversal outside the project.
        for name in os.listdir(src_fd):
            entry = source / relative_dir / name
            relative = (relative_dir / name).as_posix()
            if any(fnmatch.fnmatch(entry.name, pat) or fnmatch.fnmatch(relative, pat)
                   for pat in EXCLUDES + tuple(extra_excludes)):
                continue
            info = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
            target = dst / entry.name
            if stat.S_ISLNK(info.st_mode):
                # Preserve internal links; never expose files outside the project.
                try:
                    resolved = entry.resolve(strict=True)
                    resolved.relative_to(source)
                except (OSError, ValueError, RuntimeError):
                    continue
                target.symlink_to(os.path.relpath(destination / resolved.relative_to(source),
                                                target.parent))
            elif stat.S_ISDIR(info.st_mode):
                target.mkdir(mode=0o755)
                child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=src_fd)
                try:
                    copy_dir(child_fd, relative_dir / name, target)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_WORKSPACE:
                    raise RunnerError('Workspace exceeds 256 MiB; use --sandbox-exclude')
                # O_NOFOLLOW also rejects a file swapped for a link during copying.
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=src_fd)
                with os.fdopen(fd, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise RunnerError(f'Workspace file changed type: {relative}')
                    with target.open('wb') as out:
                        copied = 0
                        while chunk := stream.read(65536):
                            copied += len(chunk)
                            if copied > info.st_size:
                                raise RunnerError(f'Workspace file grew during copy: {relative}')
                            out.write(chunk)
                target.chmod(0o755 if info.st_mode & 0o111 else 0o644)

    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        copy_dir(source_fd, Path(), destination)
    finally:
        os.close(source_fd)


def task_request(command):
    """A literal leading assignment is metadata, never executed on the host."""
    stripped = command.lstrip()
    if not stripped.startswith('AIDER_TEST_BACKEND='):
        return None, command
    match = re.fullmatch(r'AIDER_TEST_BACKEND=(container|microvm|process-sandbox)\s+(.+)', stripped, re.DOTALL)
    if not match or not match[2].strip():
        raise RunnerError('Use AIDER_TEST_BACKEND=container, microvm, or process-sandbox before a command')
    return match[1], match[2]


class Runner:
    def __init__(self, settings):
        self.settings = settings

    def container_ready(self):
        info = run_process(['podman', 'info', '--format',
                            '{{.Host.Security.Rootless}} {{.Host.CgroupsVersion}}'], check=True)
        if info.stdout.strip() != 'true v2':
            raise RunnerError('Container tests require rootless Podman with cgroup v2')
        image = run_process(['podman', 'image', 'exists', self.settings.image])
        if image.returncode:
            raise RunnerError(f'Container image {self.settings.image!r} is unavailable; '
                              'build it or set --sandbox-image to an installed image')

    def microvm_ready(self):
        if not os.access('/dev/kvm', os.R_OK | os.W_OK):
            raise RunnerError('MicroVM tests require access to /dev/kvm')
        for name in ('firecracker', 'mkfs.ext4', 'debugfs'):
            if not shutil.which(name):
                raise RunnerError(f'MicroVM tests require {name}')
        assets = []
        for label, value in (('kernel', self.settings.kernel), ('rootfs', self.settings.rootfs)):
            path = Path(value).expanduser().resolve()
            if not value or not path.is_file():
                raise RunnerError(f'Set --sandbox-{label} to the existing Firecracker {label}')
            assets.append(str(path))
        return assets

    def select_backend(self, requested=None):
        if requested and self.settings.backend not in ('auto', requested):
            raise RunnerError(f'Task requests {requested}, but session is pinned to {self.settings.backend}')
        choice = requested or self.settings.backend
        choices = ('container', 'microvm') if choice == 'auto' else (choice,)
        failures = []
        for backend in choices:
            try:
                if backend == 'container':
                    self.container_ready()
                elif backend == 'microvm':
                    self.microvm_ready()
                elif backend == 'process-sandbox':
                    self.process_sandbox_ready()
                else:
                    raise RunnerError(f'Unknown backend: {backend}')
                return backend
            except (RunnerError, OSError, subprocess.SubprocessError) as exc:
                failures.append(f'{backend}: {exc}')
        raise RunnerError('No requested sandbox is ready.\n' + '\n'.join(failures))

    def run(self, command, cwd):
        try:
            if not isinstance(command, str) or not command.strip():
                raise RunnerError('A non-empty shell command is required')
            if self.settings.timeout <= 0:
                raise RunnerError('Timeout must be positive')
            requested, command = task_request(command)
            backend = self.select_backend(requested)
            print(f'[sandbox: {backend}]', flush=True)
            # Use /tmp rather than a project-controlled TMPDIR.
            with tempfile.TemporaryDirectory(prefix='aider-test-', dir='/tmp') as temporary:
                stage = Path(temporary)
                workspace = stage / 'work'
                snapshot(cwd, workspace, self.settings.exclude)
                if backend == 'container':
                    return self.container(workspace, command)
                if backend == 'process-sandbox':
                    return self.process_sandbox(workspace, command)
                return self.microvm(stage, workspace, command)
        except (RunnerError, OSError, subprocess.SubprocessError) as exc:
            return 125, f'Sandbox error: {exc}\n'

    def process_sandbox_ready(self):
        for name in ('bwrap', 'systemd-run', 'systemctl'):
            if not shutil.which(name):
                raise RunnerError(f'Process sandbox requires {name}')
        if not Path('/usr/bin/python3').is_file():
            raise RunnerError('Process sandbox requires /usr/bin/python3')
        run_process(self.bwrap_base() + ['--', '/usr/bin/true'], check=True)

    @staticmethod
    def bwrap_base():
        argv = [
            'bwrap', '--die-with-parent', '--new-session',
            '--unshare-user', '--unshare-pid', '--unshare-uts',
            '--unshare-ipc', '--unshare-cgroup', '--unshare-net',
            '--uid', '1000', '--gid', '1000', '--cap-drop', 'ALL',
            '--clearenv', '--setenv', 'HOME', '/tmp/home',
            '--setenv', 'PATH', '/usr/local/bin:/usr/bin:/bin',
            '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--dir', '/tmp/home', '--dir', '/etc',
        ]
        for directory in ('/usr', '/bin', '/lib', '/lib64'):
            if Path(directory).exists():
                argv += ['--ro-bind', directory, directory]
        # Only runtime configuration; do not expose the host's entire /etc.
        for name in ('passwd', 'group', 'nsswitch.conf', 'ld.so.cache', 'alternatives'):
            path = '/etc/' + name
            if Path(path).exists():
                argv += ['--ro-bind', path, path]
        return argv

    def process_sandbox(self, workspace, command):
        helper_dir = Path(__file__).resolve().parent
        unit = 'aider-test-' + uuid.uuid4().hex + '.scope'
        argv = [
            'systemd-run', '--user', '--scope', '--quiet', '--unit', unit,
            '-p', 'MemoryMax=1G', '-p', 'MemorySwapMax=0',
            '-p', 'TasksMax=128', '-p', 'CPUQuota=100%',
            '--', '/usr/bin/python3', '-I', str(helper_dir / 'cgroup_guard.py'),
        ]
        argv += self.bwrap_base() + [
            '--ro-bind', str(helper_dir / 'landlock_guard.py'), '/sandbox/landlock_guard.py',
            '--bind', str(workspace), '/work', '--chdir', '/work',
            '--setenv', 'LIMIT_CPU_SEC', str(self.settings.timeout),
            '--', '/usr/bin/python3', '-I', '/sandbox/landlock_guard.py',
            '--enforce', '--', '/bin/sh', '-c', command,
        ]
        try:
            result = run_process(argv, timeout=self.settings.timeout)
            return result.returncode, result.stdout
        finally:
            # Also stop descendants that detached from the launcher process group.
            cleanup = run_process(['systemctl', '--user', 'stop', unit])
            # systemd unloads a successfully completed scope automatically (code 5).
            if cleanup.returncode not in (0, 5):
                raise RunnerError(f'Could not stop process sandbox {unit}: {cleanup.stdout}')

    def container(self, workspace, command):
        name = 'aider-test-' + uuid.uuid4().hex
        argv = [
            'podman', 'run', '--rm', '--name', name, '--pull=never',
            '--userns=keep-id:uid=1000,gid=1000', '--user', '1000:1000',
            '--network=none', '--read-only', '--cap-drop=all',
            '--security-opt=no-new-privileges', '--security-opt=label=disable',
            '--pids-limit=128', '--memory=1g', '--cpus=1',
            '--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777',
            '--volume', f'{workspace}:/work:rw', '--workdir=/work',
            '--env=HOME=/tmp', '--env=PYTHONDONTWRITEBYTECODE=1',
            '--entrypoint=/bin/sh', self.settings.image, '-c', command,
        ]
        try:
            result = run_process(argv, timeout=self.settings.timeout)
            return result.returncode, result.stdout
        finally:
            # A killed podman client can leave conmon and the container alive.
            run_process(['podman', 'rm', '--force', '--ignore', name], check=True)

    def microvm(self, stage, workspace, command):
        kernel, rootfs = self.microvm_ready()
        inputs = stage / 'inputs'
        (inputs / 'input').mkdir(parents=True)
        (inputs / 'src' / 'common').mkdir(parents=True)
        (inputs / 'input' / 'command.json').write_text(json.dumps(command))
        shutil.copyfile(Path(__file__).with_name('inside.sh'), inputs / 'src/common/inside.sh')
        (inputs / 'guest.env').write_text("RUN_MODE='test'\n")
        for name, size, source in [('work', 512, workspace), ('input', 16, inputs)]:
            img = stage / f'{name}.ext4'
            with img.open('wb') as stream:
                stream.truncate(size * 1024 * 1024)
            run_process(['mkfs.ext4', '-q', '-F', '-d', str(source), str(img)], check=True)
        config = {
            'boot-source': {'kernel_image_path': kernel,
                            'boot_args': 'console=ttyS0 reboot=k panic=1 pci=off init=/sbin/guest-init'},
            'drives': [
                {'drive_id': 'rootfs', 'path_on_host': rootfs,
                 'is_root_device': True, 'is_read_only': True},
                {'drive_id': 'work', 'path_on_host': str(stage / 'work.ext4'),
                 'is_root_device': False, 'is_read_only': False},
                {'drive_id': 'input', 'path_on_host': str(stage / 'input.ext4'),
                 'is_root_device': False, 'is_read_only': True},
            ],
            'machine-config': {'vcpu_count': 1, 'mem_size_mib': 1024},
        }
        config_path = stage / 'config.json'
        config_path.write_text(json.dumps(config))
        boot = run_process(['firecracker', '--no-api', '--config-file', str(config_path)],
                           timeout=self.settings.timeout, check=True)

        def read_guest(name):
            # No host mount or arbitrary file extraction from an untrusted image.
            result = run_process(['debugfs', '-R', f'cat /{name}', str(stage / 'work.ext4')],
                                 check=True)
            lines = result.stdout.splitlines(keepends=True)
            if lines and lines[0].startswith('debugfs '):
                lines.pop(0)
            return ''.join(lines)

        status = read_guest('.aider-test-exit').strip()
        output = read_guest('stdout.log') + read_guest('stderr.log')
        try:
            code = int(status)
            if not -64 <= code <= 255:
                raise ValueError()
        except ValueError:
            raise RunnerError(f'Guest did not report a test exit code.\n{output}\n{boot.stdout}')
        return (128 - code if code < 0 else code), output
