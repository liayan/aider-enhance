import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from aider_local.runner import Runner, RunnerError, Settings, run_process, snapshot, task_request


class RoutingTests(unittest.TestCase):
    def test_task_request_preserves_shell_syntax(self):
        command = 'AIDER_TEST_BACKEND=microvm python3 test.py && echo "done; ok"'
        self.assertEqual(task_request(command), ('microvm', 'python3 test.py && echo "done; ok"'))
        self.assertEqual(task_request('echo AIDER_TEST_BACKEND=microvm'),
                         (None, 'echo AIDER_TEST_BACKEND=microvm'))

    def test_invalid_task_request_is_rejected(self):
        for command in ('AIDER_TEST_BACKEND=host pytest', 'AIDER_TEST_BACKEND=microvm '):
            with self.subTest(command=command), self.assertRaises(RunnerError):
                task_request(command)

    def test_auto_prefers_container(self):
        runner = Runner(Settings())
        with patch.object(runner, 'container_ready'), patch.object(runner, 'microvm_ready') as vm:
            self.assertEqual(runner.select_backend(), 'container')
            vm.assert_not_called()

    def test_auto_uses_microvm_when_container_unavailable(self):
        runner = Runner(Settings())
        with patch.object(runner, 'container_ready', side_effect=RunnerError('missing image')):
            with patch.object(runner, 'microvm_ready'):
                self.assertEqual(runner.select_backend(), 'microvm')

    def test_task_microvm_request_never_downgrades(self):
        runner = Runner(Settings())
        with patch.object(runner, 'container_ready') as container:
            with patch.object(runner, 'microvm_ready', side_effect=RunnerError('no KVM')):
                code, output = runner.run('AIDER_TEST_BACKEND=microvm false', '.')
        self.assertEqual(code, 125)
        self.assertIn('no KVM', output)
        container.assert_not_called()

    def test_session_pin_rejects_conflicting_request(self):
        runner = Runner(Settings(backend='microvm'))
        with self.assertRaisesRegex(RunnerError, 'pinned'):
            runner.select_backend('container')

    def test_no_backend_never_executes(self):
        runner = Runner(Settings())
        with patch.object(runner, 'container_ready', side_effect=RunnerError('no container')):
            with patch.object(runner, 'microvm_ready', side_effect=RunnerError('no vm')):
                with patch.object(runner, 'container') as container, patch.object(runner, 'microvm') as vm:
                    code, output = runner.run('touch must-not-run', '.')
        self.assertEqual(code, 125)
        self.assertIn('no container', output)
        self.assertIn('no vm', output)
        container.assert_not_called()
        vm.assert_not_called()

    def test_failed_test_does_not_trigger_fallback(self):
        runner = Runner(Settings())
        with tempfile.TemporaryDirectory() as project:
            with patch.object(runner, 'container_ready'), patch.object(runner, 'container', return_value=(7, 'failed')):
                with patch.object(runner, 'microvm') as vm:
                    self.assertEqual(runner.run('false', project), (7, 'failed'))
        vm.assert_not_called()


class SnapshotTests(unittest.TestCase):
    def test_copy_includes_local_edits_but_not_secrets_or_external_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            src = base / 'project'
            src.mkdir()
            (src / 'source.py').write_text('new local edit')
            (src / '.env').write_text('secret')
            (src / 'api.key').write_text('secret')
            (src / '.git').mkdir()
            (src / '.git' / 'config').write_text('secret')
            (src / 'cache').mkdir()
            (src / 'cache' / 'big').write_text('excluded')
            (base / 'outside').write_text('secret')
            (src / 'outside').symlink_to(base / 'outside')
            (src / 'alias.py').symlink_to(src / 'source.py')
            os.mkfifo(src / 'pipe')
            dst = base / 'copy'
            snapshot(src, dst, ('cache',))
            self.assertEqual({p.name for p in dst.iterdir()}, {'source.py', 'alias.py'})
            self.assertEqual((dst / 'alias.py').read_text(), 'new local edit')
            (dst / 'source.py').write_text('test write')
            self.assertEqual((src / 'source.py').read_text(), 'new local edit')

    def test_snapshot_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RunnerError, 'outside the project'):
                snapshot(temporary, Path(temporary) / 'copy')

    def test_oversized_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            src = base / 'project'
            src.mkdir()
            (src / 'large').write_bytes(b'x' * 10)
            with patch('aider_local.runner.MAX_WORKSPACE', 5), self.assertRaises(RunnerError):
                snapshot(src, base / 'copy')


class ProcessTests(unittest.TestCase):
    def test_exit_status_and_stderr(self):
        result = run_process([sys.executable, '-c', 'import sys; print("failure", file=sys.stderr); sys.exit(9)'])
        self.assertEqual(result.returncode, 9)
        self.assertIn('failure', result.stdout)

    def test_timeout(self):
        with self.assertRaises(RunnerError):
            run_process([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.1)

    def test_output_limit(self):
        with patch('aider_local.runner.MAX_OUTPUT', 100), self.assertRaises(RunnerError):
            run_process([sys.executable, '-c', 'print("x" * 1000)'])

    def test_container_cleanup_after_timeout(self):
        runner = Runner(Settings())
        with patch('aider_local.runner.run_process', side_effect=[RunnerError('timeout'), Mock(returncode=0)]) as run:
            with self.assertRaises(RunnerError):
                runner.container(Path('/tmp/example'), 'sleep 1000')
        self.assertEqual(run.call_args_list[1].args[0][:4], ['podman', 'rm', '--force', '--ignore'])


class AiderIntegrationTests(unittest.TestCase):
    def test_failure_feedback_and_hook_restoration(self):
        from aider.commands import Commands
        import aider.commands
        import aider.coders.base_coder
        from aider_local.cli import isolated_commands

        runner = Mock(settings=Settings(), run=Mock(return_value=(3, 'assertion failed\n')))
        coder = Mock(root='/project', test_cmd='python3 -m unittest', cur_messages=[])
        coder.main_model.token_count.return_value = 5
        commands = Commands(Mock(), coder)
        original = aider.commands.run_cmd
        original_base = aider.coders.base_coder.run_cmd
        with contextlib.redirect_stdout(io.StringIO()), isolated_commands(runner):
            errors = commands.cmd_test('')
            self.assertIn('assertion failed', errors)
            self.assertIn('assertion failed', coder.cur_messages[0]['content'])
            runner.run.assert_called_once_with('python3 -m unittest', '/project')
            self.assertIs(aider.coders.base_coder.run_cmd, aider.commands.run_cmd)
            clone = commands.clone()
            clone.coder = coder
            clone.cmd_test('AIDER_TEST_BACKEND=microvm false')
            self.assertEqual(runner.run.call_args.args[0], 'AIDER_TEST_BACKEND=microvm false')
        self.assertIs(aider.commands.run_cmd, original)
        self.assertIs(aider.coders.base_coder.run_cmd, original_base)

    def test_approved_model_command_uses_task_directive(self):
        from aider.coders.base_coder import Coder
        from aider_local.cli import isolated_commands
        runner = Mock(settings=Settings(), run=Mock(return_value=(0, 'tested plugin')))
        coder = Mock(root='/project')
        coder.io.confirm_ask.return_value = True
        command = 'AIDER_TEST_BACKEND=microvm python3 tests/test_plugin.py'
        with contextlib.redirect_stdout(io.StringIO()), isolated_commands(runner):
            output = Coder.handle_shell_commands(coder, command, None)
        runner.run.assert_called_once_with(command, '/project')
        self.assertIn('tested plugin', output)

    def test_launcher_supplies_session_policy_and_forwards_aider_flags(self):
        from aider_local.cli import main
        observed = {}

        def start_aider(args):
            policy_path = Path(args[args.index('--read') + 1])
            observed['policy_path'] = policy_path
            self.assertIn('Current session backend policy: microvm', policy_path.read_text())
            self.assertIn('AIDER_TEST_BACKEND=microvm', policy_path.read_text())
            self.assertEqual(args[-3:], ['--model', 'test-model', 'app.py'])
            self.assertEqual(args[0], '--no-auto-lint')
            return 0

        with patch('aider.main.main', side_effect=start_aider):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--test-backend', 'microvm', '--model', 'test-model', 'app.py']), 0)
        self.assertFalse(observed['policy_path'].exists())

    def test_success_does_not_add_error_feedback(self):
        from aider.commands import Commands
        from aider_local.cli import isolated_commands
        runner = Mock(settings=Settings(), run=Mock(return_value=(0, 'OK\n')))
        coder = Mock(root='/project', test_cmd='test', cur_messages=[])
        coder.main_model.token_count.return_value = 1
        with contextlib.redirect_stdout(io.StringIO()), isolated_commands(runner):
            self.assertIsNone(Commands(Mock(), coder).cmd_test('test'))
        self.assertEqual(coder.cur_messages, [])


if __name__ == '__main__':
    unittest.main()
