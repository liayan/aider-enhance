"""Run the trusted behavioral oracle and candidate in separate CLI subprocesses.

This checker is copied into _bench at evaluation time, after editing finishes.
It runs inside the selected sandbox, never imports candidate code on the host.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

COMMANDS = ('greet', 'add', 'count', 'copy', 'digest')
CASES = [[], ['--help'], ['unknown'], *[[name, '--help'] for name in COMMANDS],
         ['greet', 'Ada'], ['greet', '世界', '--upper'], ['greet'],
         ['add', '2', '-7', '11'], ['add', 'x'], ['add'],
         ['count', 'input.txt'], ['count', 'empty.txt'], ['count', 'missing'],
         ['count', 'binary.dat'], ['digest', 'input.txt'], ['digest', 'empty.txt'],
         ['digest', 'missing'], ['copy', 'input.txt', 'output.txt'],
         ['copy', 'input.txt', 'existing.txt'],
         ['copy', 'input.txt', 'existing.txt', '--force'],
         ['copy', 'missing', 'output.txt'], ['copy', 'input.txt', 'missing/out']]


def observe(project, args):
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        for name, data in {'input.txt': 'one\n世界\nthree'.encode(), 'empty.txt': b'',
                           'binary.dat': b'\xff\x00', 'existing.txt': b'keep'}.items():
            (work / name).write_bytes(data)
        env = dict(os.environ, PYTHONPATH=str(project), PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-m', 'mycli', *args], cwd=work,
                                env=env, capture_output=True, timeout=10)
        files = {p.relative_to(work).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(work.rglob('*')) if p.is_file()}
        return [result.returncode, result.stdout.hex(), result.stderr.hex(), files]


def structure(project):
    errors = []
    for name in COMMANDS:
        path = project / 'mycli/commands' / f'{name}.py'
        if not path.is_file():
            errors.append(f'missing module: {name}')
    try:
        tree = ast.parse((project / 'mycli/main.py').read_text())
        # This is a guard against trivial no-op submissions, not a proof of design quality.
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in COMMANDS:
                errors.append(f'command still defined in main.py: {node.name}')
        for source in (project / 'mycli').rglob('*.py'):
            for node in ast.walk(ast.parse(source.read_text())):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name.split('.')[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                    names = [node.module.split('.')[0]]
                for name in names:
                    if name != 'mycli' and name not in sys.stdlib_module_names:
                        errors.append(f'non-stdlib import: {name}')
        script = ('import mycli; from mycli import commands; '
                  'assert callable(mycli.main); '
                  f'assert all(callable(getattr(commands, n)) for n in {COMMANDS!r})')
        subprocess.run([sys.executable, '-B', '-c', script], cwd=project, check=True,
                       capture_output=True, timeout=10)
    except (OSError, SyntaxError, subprocess.SubprocessError) as exc:
        errors.append(f'structure/export check: {exc}')
    return errors


def evaluate(project, oracle, require_structure=True):
    errors = structure(project) if require_structure else []
    for args in CASES:
        try:
            expected, actual = observe(oracle, args), observe(project, args)
            if expected != actual:
                errors.append(f'behavior mismatch: {args!r}; expected={expected!r}; actual={actual!r}')
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f'{args!r}: {exc}')
    return errors


def main():
    started = time.monotonic()
    project = Path.cwd()
    errors = evaluate(project, Path(__file__).resolve().parent / 'oracle')
    print('BENCH_RESULT=' + json.dumps({'passed': not errors, 'cases': len(CASES),
          'check_seconds': time.monotonic() - started, 'python': platform.python_version(),
          'errors': errors}))
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
