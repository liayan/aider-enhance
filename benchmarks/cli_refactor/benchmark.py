#!/usr/bin/env python3
"""Controlled local-editing workload and isolated test benchmark (stdlib only)."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import random
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from aider_local.runner import Runner, RunnerError, Settings, snapshot  # noqa: E402
from model_proxy import proxy, usage_summary
from file_scope import inventory, project_entries, scope_report  # noqa: E402

BACKENDS = ('process-sandbox', 'container', 'microvm')
WORKLOADS = {
    'cli-refactor': {'directory': HERE, 'files': ['mycli/main.py', 'mycli/__init__.py', 'mycli/__main__.py'],
                     'allowed': ['mycli', 'mycli/']},
    'http-client-refactor': {'directory': HERE.parent / 'http_client_refactor',
                             'files': ['src/http_client.py'], 'allowed': ['src/http_client.py']},
}


def workload(name='cli-refactor'):
    return WORKLOADS[name]


def validate_scope(project, name='cli-refactor', require_helpers=False):
    with tempfile.TemporaryDirectory(prefix='benchmark-scope-', dir='/tmp') as directory:
        expected = Path(directory)
        stage_checker(expected, name)
        return scope_report(project, workload(name)['directory'] / 'baseline',
                            workload(name)['allowed'], inventory(expected), require_helpers=require_helpers)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(project):
    return project_entries(inventory(project))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def changed_files(before, after):
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def stage_checker(project, name="cli-refactor"):
    fixture = workload(name)["directory"]
    destination = project / '_bench'
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    shutil.copyfile(fixture / 'check.py', destination / 'check.py')
    shutil.copytree(fixture / 'baseline', destination / 'oracle', ignore=shutil.ignore_patterns('__pycache__'))


def command_output(argv):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        return proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def metadata(name="cli-refactor"):
    fixture = workload(name)["directory"]
    try:
        aider_version = version('aider-chat')
    except PackageNotFoundError:
        aider_version = None
    return {'workload': name, 'allowed_changes': workload(name)['allowed'], 'utc': datetime.now(timezone.utc).isoformat(), 'host': platform.platform(),
            'project_commit': command_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']),
            'aider_version': aider_version,
            'support_files': {str(p.relative_to(ROOT)): file_hash(p) for p in
                [HERE / 'aider_entry.py', HERE / 'model_proxy.py', HERE / 'file_scope.py',
                 *sorted((ROOT / 'aider_local').glob('*.py')),
                 ROOT / 'aider_local/policy.md', ROOT / 'aider_local/inside.sh']},
            'project_dirty': bool(command_output(['git', '-C', str(ROOT), 'status', '--porcelain'])),
            'baseline': manifest(fixture / 'baseline'), 'prompt_sha256': file_hash(fixture / 'prompt.txt'),
            'checker_sha256': file_hash(fixture / 'check.py'),
            'harness_sha256': file_hash(Path(__file__)),
            'runner_sha256': file_hash(ROOT / 'aider_local/runner.py')}


def new_output(path):
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def prepare(path, name="cli-refactor"):
    fixture = workload(name)["directory"]
    if path.resolve().is_relative_to(HERE.parent):
        raise ValueError('Prepare output must be outside benchmark fixtures')
    output = new_output(path)
    work = output / 'work'
    shutil.copytree(fixture / 'baseline', work, ignore=shutil.ignore_patterns('__pycache__'))
    stage_checker(work, name)
    env = dict(os.environ, GIT_AUTHOR_NAME='Benchmark', GIT_AUTHOR_EMAIL='benchmark@example.invalid',
               GIT_COMMITTER_NAME='Benchmark', GIT_COMMITTER_EMAIL='benchmark@example.invalid',
               GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
    for args in (['init', '-q', '--initial-branch=main'], ['add', '--', *[p.name for p in sorted((fixture / 'baseline').iterdir()) if p.name != '__pycache__']],
                 ['-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Fixed benchmark baseline']):
        subprocess.run(['git', *args], cwd=work, env=env, check=True, capture_output=True)
    info = metadata(name)
    info['starting_commit'] = command_output(['git', '-C', str(work), 'rev-parse', 'HEAD'])
    write_json(output / 'prepared.json', info)
    return output, work, info


def config(args, backend):
    return Settings(backend=backend, image=args.image, kernel=args.kernel,
                    rootfs=args.rootfs, timeout=args.timeout)


def generate(args):
    if not args.model.startswith('openai/'):
        raise ValueError('--model must use openai/<provider-model-id> for this usage recorder')
    key = os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('Set OPENAI_API_KEY in your environment')
    # Fail before any model spend if the test backend is unavailable.
    Runner(config(args, args.backend)).select_backend(None)
    output, work, info = prepare(args.output, args.workload)
    base_args = [sys.executable, str(HERE / 'aider_entry.py'), '--test-backend', args.backend,
                 '--sandbox-image', args.image, '--sandbox-kernel', args.kernel,
                 '--sandbox-rootfs', args.rootfs, '--sandbox-timeout', str(args.timeout),
                 '--model', args.model, '--weak-model', args.model,
                 '--edit-format', args.edit_format, '--map-tokens', str(args.map_tokens), '--map-multiplier-no-files', '1',
                 '--no-auto-commits', '--no-gitignore', '--no-dirty-commits', '--no-auto-lint', '--no-stream',
                 '--no-analytics', '--no-check-update', '--no-show-release-notes', '--yes',
                 '--test-cmd', 'python3 _bench/check.py', '--auto-test',
                 '--message-file', str(workload(args.workload)['directory'] / 'prompt.txt'),
                 '--read', '_bench/check.py', *workload(args.workload)['files']]
    env = {k: v for k, v in os.environ.items() if not k.startswith('AIDER_')}
    env['PYTHONPATH'] = str(ROOT)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    trace = output / 'requests.jsonl'
    trace.touch(mode=0o600)
    started = time.monotonic()
    with proxy(args.api_base, key, trace) as local_base:
        env.update(OPENAI_API_BASE=local_base, OPENAI_API_KEY='benchmark-local-proxy')
        with (output / 'aider.log').open('w') as log:
            process = subprocess.Popen(base_args, cwd=work, env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=args.generation_timeout)
            except BaseException as exc:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                if not isinstance(exc, subprocess.TimeoutExpired):
                    raise
                code = 124
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    current = manifest(work)
    scope = validate_scope(work, args.workload, require_helpers=True)
    info.update(mode='model-generation', model=args.model, api_base=args.api_base,
                map_tokens=args.map_tokens, argv=base_args, settings=asdict(config(args, args.backend)),
                aider_exit_code=code, generation_seconds=time.monotonic() - started,
                candidate=current, candidate_sha256=fingerprint(current),
                files_changed=scope['files_changed'], file_scope=scope, usage=usage_summary(records))
    write_json(output / 'generation.json', info)
    print(f'Generation exit {code}; recorded {len(records)} requests in {output}.')
    print(f'Check the frozen candidate with: {sys.executable} {Path(__file__)} run --workload {args.workload} --candidate {work} --output NEW_DIRECTORY')
    if not scope['passed']:
        print('File scope failed: ' + ', '.join(scope['violations']))
    return int(code != 0 or not scope['passed'])


def aggregate(rows, backends):
    result = {}
    for backend in backends:
        values = [r for r in rows if r['backend'] == backend]
        passed = [r for r in values if r['status'] == 'passed']
        walls = [r['wall_seconds'] for r in passed]
        checks = [r['check']['check_seconds'] for r in passed]
        result[backend] = {'runs': len(values), 'passed': len(passed),
                           'failed': sum(r['status'] == 'failed' for r in values),
                           'errors': sum(r['status'] == 'error' for r in values),
                           'median_wall_seconds': statistics.median(walls) if walls else None,
                           'min_wall_seconds': min(walls) if walls else None,
                           'max_wall_seconds': max(walls) if walls else None,
                           'median_check_seconds': statistics.median(checks) if checks else None}
    return result


def run(args):
    fixture = workload(args.workload)['directory']
    candidate = (args.candidate or fixture / 'reference').resolve()
    if not candidate.is_dir():
        raise ValueError('Candidate must be a directory')
    output = args.output.resolve()
    if output == candidate or output.is_relative_to(candidate):
        raise ValueError('Output directory must be outside the candidate')
    output = new_output(output)
    info = metadata(args.workload)
    info.update(mode='candidate-replay' if args.candidate else 'offline-reference',
                repeats=args.repeats, warmups=args.warmups, seed=args.seed,
                backends=args.backends, model_usage=None, settings=asdict(config(args, 'explicit')),
                artifacts={p: file_hash(Path(p)) for p in (args.kernel, args.rootfs) if p and Path(p).is_file()},
                container_image_id=command_output(['podman', 'image', 'inspect', '--format', '{{.Id}}', args.image]))
    rows = []
    # Freeze once outside measured intervals. Each Runner still snapshots it afresh.
    with tempfile.TemporaryDirectory(prefix='cli-benchmark-', dir='/tmp') as temp:
        frozen = Path(temp) / 'candidate'
        # Check original entries BEFORE snapshot exclusions or helper replacement.
        scope = validate_scope(candidate, args.workload,
                               require_helpers=(candidate.parent / 'prepared.json').is_file())
        snapshot(candidate, frozen)
        frozen_scope = validate_scope(frozen, args.workload)
        scope['violations'] = sorted(set(scope['violations'] + frozen_scope['violations']))
        scope['passed'] = not scope['violations']
        stage_checker(frozen, args.workload)
        info['file_scope'] = scope
        info['candidate'] = manifest(frozen)
        info['candidate_sha256'] = fingerprint(info['candidate'])
        info['files_changed'] = scope['files_changed']
        write_json(output / 'metadata.json', info)
        rng = random.Random(args.seed)
        for iteration in range(-args.warmups, args.repeats):
            order = list(args.backends)
            rng.shuffle(order)
            for backend in order:
                started = time.monotonic()
                code, log = Runner(config(args, backend)).run('python3 _bench/check.py', frozen)
                elapsed = time.monotonic() - started
                checks = []
                for line in log.splitlines():
                    if line.startswith('BENCH_RESULT='):
                        try:
                            checks.append(json.loads(line.removeprefix('BENCH_RESULT=')))
                        except ValueError:
                            pass
                check = checks[0] if len(checks) == 1 and isinstance(checks[0], dict) else {}
                valid = (scope['passed'] and check.get('passed') is True and isinstance(check.get('check_seconds'), (int, float))
                         and check['check_seconds'] >= 0)
                status = 'passed' if code == 0 and valid else ('error' if code == 125 or not check else 'failed')
                row = {'iteration': iteration, 'warmup': iteration < 0, 'backend': backend,
                       'exit_code': code, 'status': status, 'wall_seconds': elapsed, 'check': check,
                       'file_scope': scope}
                rows.append(row)
                (output / f'{iteration}-{backend}.log').write_text(log)
                with (output / 'runs.jsonl').open('a') as stream:
                    stream.write(json.dumps(row) + '\n')
                print(f'{backend}: {status}, {elapsed:.3f}s (iteration {iteration})', flush=True)
    measured = [row for row in rows if not row['warmup']]
    summary = aggregate(measured, args.backends)
    write_json(output / 'summary.json', summary)
    lines = [f'# {args.workload} sandbox benchmark', '',
             f'File scope: {"passed" if scope["passed"] else "FAILED"}. Violations: {scope["violations"]}', '',
             f'Mode: {info["mode"]}. No model calls in this replay.', '',
             '| Backend | Passed | Failed | Errors | Median wall (s) | Median checker (s) |',
             '|---|---:|---:|---:|---:|---:|']
    for backend, stats in summary.items():
        fmt = lambda value: 'n/a' if value is None else f'{value:.3f}'
        lines.append(f'| {backend} | {stats["passed"]}/{stats["runs"]} | {stats["failed"]} | {stats["errors"]} | '
                     f'{fmt(stats["median_wall_seconds"])} | {fmt(stats["median_check_seconds"])} |')
    lines += ['', 'Wall time includes readiness checks, snapshotting, setup, execution and cleanup.',
              'Checker time includes the workload acceptance suite. Warmups excluded.',
              'Timing statistics use passing runs only. Failures remain visible; missing results are not zero.',
              'Host/container/guest Python versions may differ (recorded per run). These are warm-cache samples,',
              'not model rankings or measurements of guest CPU time. Review code structure manually too.']
    (output / 'report.md').write_text('\n'.join(lines) + '\n')
    print(f'Report: {output / "report.md"}')
    return int(any(row['status'] != 'passed' for row in rows))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    prep = commands.add_parser('prepare', help='Create a fresh baseline Git repository, without model calls')
    prep.add_argument('--output', type=Path, required=True)
    replay = commands.add_parser('run', help='Replay one frozen candidate across isolated backends; no model calls')
    replay.add_argument('--candidate', type=Path)
    replay.add_argument('--repeats', type=int, default=3)
    replay.add_argument('--warmups', type=int, default=1)
    replay.add_argument('--seed', type=int, default=0)
    replay.add_argument('--backends', nargs='+', choices=BACKENDS, default=list(BACKENDS))
    gen = commands.add_parser('generate', help='Run aider-local against an explicitly selected paid or local model')
    gen.add_argument('--model', required=True)
    gen.add_argument('--api-base', required=True, help='Exact OpenAI-compatible base URL, including /v1 if required')
    gen.add_argument('--backend', choices=BACKENDS, default='container')
    gen.add_argument('--edit-format', choices=['diff', 'whole'], default='diff')
    gen.add_argument('--map-tokens', type=int, default=1024)
    gen.add_argument('--generation-timeout', type=int, default=900)
    for child in (replay, gen):
        child.add_argument('--output', type=Path, required=True)
        child.add_argument('--image', default=Settings.image)
        child.add_argument('--kernel', default=os.environ.get('FC_KERNEL', ''))
        child.add_argument('--rootfs', default=os.environ.get('FC_ROOTFS', ''))
        child.add_argument('--timeout', type=int, default=120)
    for child in (prep, replay, gen):
        child.add_argument('--workload', choices=WORKLOADS, default='cli-refactor')
    args = parser.parse_args(argv)
    for name in ('timeout', 'repeats', 'generation_timeout'):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(f'{name} must be positive')
    for name in ('warmups', 'map_tokens'):
        if hasattr(args, name) and getattr(args, name) < 0:
            parser.error(f'{name} must be nonnegative')
    if args.action == 'run' and len(set(args.backends)) != len(args.backends):
        parser.error('Choose each backend at most once')
    for name in ('kernel', 'rootfs'):
        if hasattr(args, name) and getattr(args, name):
            setattr(args, name, str(Path(getattr(args, name)).resolve()))
    try:
        if args.action == 'prepare':
            print(prepare(args.output, args.workload)[1])
            return 0
        return generate(args) if args.action == 'generate' else run(args)
    except (OSError, ValueError, RunnerError, subprocess.SubprocessError) as exc:
        parser.exit(2, f'Benchmark error: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
