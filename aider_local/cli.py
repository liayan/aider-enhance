"""Thin adapter around the pinned upstream aider; the submodule stays untouched."""

import argparse
import os
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
import sys
import tempfile

from .runner import Runner, Settings


def parser(description):
    result = argparse.ArgumentParser(description=description, allow_abbrev=False)
    result.add_argument('--test-backend', choices=['auto', 'container', 'microvm', 'process-sandbox'], default='auto')
    result.add_argument('--sandbox-image', default=Settings.image)
    result.add_argument('--sandbox-kernel', default=os.environ.get('FC_KERNEL', ''))
    result.add_argument('--sandbox-rootfs', default=os.environ.get('FC_ROOTFS', ''))
    result.add_argument('--sandbox-timeout', type=int, default=600)
    result.add_argument('--sandbox-exclude', action='append', default=[], metavar='GLOB')
    return result


def settings(args, argument_parser):
    if args.sandbox_timeout <= 0:
        argument_parser.error('--sandbox-timeout must be positive')
    return Settings(backend=args.test_backend, image=args.sandbox_image,
                    kernel=args.sandbox_kernel, rootfs=args.sandbox_rootfs,
                    timeout=args.sandbox_timeout, exclude=tuple(args.sandbox_exclude))


@contextmanager
def isolated_commands(runner):
    import aider.commands
    import aider.coders.base_coder

    def run(command, verbose=False, error_print=None, cwd=None):
        print(f'[command] {command}', flush=True)
        code, output = runner.run(command, cwd or Path.cwd())
        print(output, end='' if output.endswith('\n') else '\n', flush=True)
        return code, output

    # These are the two run_cmd imports in aider 0.86.2. Cover /test, /run,
    # --auto-test, --test, and approved model-suggested shell commands.
    modules = [aider.commands, aider.coders.base_coder]
    originals = [module.run_cmd for module in modules]
    try:
        for module in modules:
            module.run_cmd = run
        yield
    finally:
        for module, original in zip(modules, originals):
            module.run_cmd = original


def main(argv=None):
    argument_parser = parser('Local aider editing with isolated tests. Remaining flags go to aider.')
    args, aider_args = argument_parser.parse_known_args(argv)
    config = settings(args, argument_parser)
    if version('aider-chat') != '0.86.2':
        argument_parser.error('This adapter requires aider-chat==0.86.2; reinstall this package')
    from aider.main import main as aider_main

    print(f'Local editing; test backend policy: {config.backend}.', flush=True)
    with tempfile.TemporaryDirectory(prefix='aider-policy-', dir='/tmp') as temporary:
        policy = Path(temporary) / 'test-policy.md'
        text = Path(__file__).with_name('policy.md').read_text()
        text += f'\nCurrent session backend policy: {config.backend}.\n'
        if config.backend != 'auto':
            text += f'Use {config.backend} for commands, or explain why this pin prevents the task.\n'
        policy.write_text(text)
        with isolated_commands(Runner(config)):
            # Lint hooks are separate host operations. The user can opt in again.
            return aider_main(['--no-auto-lint', '--read', str(policy), *aider_args])


def test_main(argv=None):
    argument_parser = parser('Run a command against a disposable copy of the local project.')
    argument_parser.add_argument('--workspace', type=Path, default=Path.cwd())
    argument_parser.add_argument('command', help='Shell command, quoted as one argument')
    args = argument_parser.parse_args(argv)
    config = settings(args, argument_parser)
    code, output = Runner(config).run(args.command, args.workspace)
    print(output, end='' if output.endswith('\n') else '\n')
    return code


if __name__ == '__main__':
    sys.exit(main())
