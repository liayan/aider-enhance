import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from aider_local import cgroup_guard
from aider_local.cli import parser, settings
from aider_local.runner import Runner, RunnerError, Settings, task_request


class ProcessSandboxTests(unittest.TestCase):
    def test_cli_and_task_directive(self):
        arguments = parser('test')
        config = settings(arguments.parse_args(['--test-backend', 'process-sandbox']), arguments)
        self.assertEqual(config.backend, 'process-sandbox')
        self.assertEqual(task_request('AIDER_TEST_BACKEND=process-sandbox echo ok'),
                         ('process-sandbox', 'echo ok'))

    def test_explicit_choice_does_not_fall_back(self):
        runner = Runner(Settings(backend='process-sandbox'))
        with patch.object(runner, 'process_sandbox_ready', side_effect=RunnerError('no namespaces')):
            with patch.object(runner, 'container') as container, patch.object(runner, 'microvm') as vm:
                code, output = runner.run('touch must-not-run', '.')
        self.assertEqual(code, 125)
        self.assertIn('no namespaces', output)
        container.assert_not_called()
        vm.assert_not_called()

    def test_auto_never_selects_process_sandbox(self):
        runner = Runner(Settings())
        with patch.object(runner, 'container_ready', side_effect=RunnerError('no container')):
            with patch.object(runner, 'microvm_ready', side_effect=RunnerError('no vm')):
                with patch.object(runner, 'process_sandbox_ready') as process:
                    with self.assertRaises(RunnerError):
                        runner.select_backend()
        process.assert_not_called()

    def test_missing_dependency_is_reported(self):
        with patch('aider_local.runner.shutil.which', return_value=None):
            code, output = Runner(Settings(backend='process-sandbox')).run('false', '.')
        self.assertEqual(code, 125)
        self.assertIn('requires bwrap', output)

    def test_timeout_stops_the_systemd_scope(self):
        runner = Runner(Settings(backend='process-sandbox'))
        cleanup = subprocess.CompletedProcess([], 0, '')
        with patch('aider_local.runner.run_process', side_effect=[RunnerError('timeout'), cleanup]) as run:
            with self.assertRaisesRegex(RunnerError, 'timeout'):
                runner.process_sandbox(Path('/tmp/example'), 'sleep 1000')
        start = run.call_args_list[0].args[0]
        unit = start[start.index('--unit') + 1]
        self.assertEqual(run.call_args_list[1].args[0], ['systemctl', '--user', 'stop', unit])

    def test_landlock_unavailable_warns(self):
        from aider_local import landlock_guard
        output = io.StringIO()
        with patch.object(landlock_guard, 'abi', return_value=0), contextlib.redirect_stderr(output):
            landlock_guard.enforce()
        self.assertIn('not supported', output.getvalue())

    def test_landlock_setup_failure_refuses_execution(self):
        from aider_local import landlock_guard
        with patch.object(landlock_guard, 'abi', return_value=4):
            with patch.object(landlock_guard.libc, 'syscall', return_value=-1):
                with self.assertRaises(SystemExit):
                    landlock_guard.enforce()


class CgroupGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.proc = root / 'cgroup'
        self.proc.write_text('0::/test.scope\n')
        self.root = root / 'cgroups'
        self.scope = self.root / 'test.scope'
        self.scope.mkdir(parents=True)
        for name, value in {'memory.max': '1073741824', 'memory.swap.max': '0',
                            'pids.max': '128', 'cpu.max': '100000 100000'}.items():
            (self.scope / name).write_text(value)

    def test_expected_limits_are_accepted(self):
        cgroup_guard.verify_limits(self.proc, self.root)

    def test_missing_or_unenforced_limits_are_rejected(self):
        for name, value in [('memory.max', 'max'), ('memory.swap.max', '1'),
                            ('pids.max', '129'), ('cpu.max', 'max 100000'),
                            ('cpu.max', '200000 100000')]:
            with self.subTest(name=name, value=value):
                path = self.scope / name
                original = path.read_text()
                path.write_text(value)
                with self.assertRaises(ValueError):
                    cgroup_guard.verify_limits(self.proc, self.root)
                path.write_text(original)
        (self.scope / 'cpu.max').unlink()
        with self.assertRaises(OSError):
            cgroup_guard.verify_limits(self.proc, self.root)

    def test_bad_limits_prevent_exec(self):
        with patch.object(cgroup_guard, 'verify_limits', side_effect=ValueError('no memory limit')):
            with patch.object(cgroup_guard.os, 'execvp') as execute, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cgroup_guard.main(['touch', '/must-not-run']), 125)
        execute.assert_not_called()


@unittest.skipUnless(os.environ.get('AIDER_TEST_PROCESS_LIVE') == '1',
                     'set AIDER_TEST_PROCESS_LIVE=1 for real process sandbox checks')
class LiveProcessSandboxTests(unittest.TestCase):
    def test_isolation_and_exit_status(self):
        import socket
        import shlex
        with tempfile.TemporaryDirectory() as temporary, socket.socket() as listener:
            project = Path(temporary) / 'project'
            project.mkdir()
            canary = Path(temporary) / 'outside'
            canary.write_text('host data')
            (project / 'source.py').write_text('local edit')
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            port = listener.getsockname()[1]
            script = f'''import os, socket
from pathlib import Path
assert Path('source.py').read_text() == 'local edit'
assert not Path({str(canary)!r}).exists()
assert 'AIDER_TEST_HOST_CANARY' not in os.environ
assert not Path('/sys/fs/cgroup').exists()
assert not Path('/run/user').exists()
assert not Path('/etc/shadow').exists()
try:
    Path('/usr/aider-write-probe').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('system directory is writable')
s = socket.socket()
s.settimeout(1)
assert s.connect_ex(('127.0.0.1', {port})) != 0
Path('source.py').write_text('sandbox edit')
print('isolation checked')
raise SystemExit(7)
'''
            with patch.dict(os.environ, {'AIDER_TEST_HOST_CANARY': 'host-only'}):
                code, output = Runner(Settings(backend='process-sandbox')).run(
                    'python3 -c ' + shlex.quote(script), project)
            self.assertEqual(code, 7, output)
            self.assertIn('isolation checked', output)
            self.assertIn('[landlock]', output)
            self.assertEqual((project / 'source.py').read_text(), 'local edit')
            self.assertEqual(canary.read_text(), 'host data')

    def test_timeout_leaves_no_scope(self):
        from aider_local.runner import run_process
        with tempfile.TemporaryDirectory() as project:
            code, output = Runner(Settings(backend='process-sandbox', timeout=1)).run('sleep 30', project)
        self.assertEqual(code, 125, output)
        self.assertIn('timed out', output)
        result = run_process(['systemctl', '--user', 'list-units', '--state=active',
                              '--no-legend', 'aider-test-*.scope'], check=True)
        self.assertEqual(result.stdout.strip(), '')

    def test_example_tests(self):
        root = Path(__file__).resolve().parents[1]
        code, output = Runner(Settings()).run(
            'AIDER_TEST_BACKEND=process-sandbox python3 -m unittest discover -s tests',
            root / 'examples/hello-project')
        self.assertEqual(code, 0, output)
        self.assertIn('Ran 2 tests', output)
