"""Opt in with AIDER_TEST_LIVE=1; requires the demo's Podman image and VM assets."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aider_local.runner import Runner, Settings, run_process


@unittest.skipUnless(os.environ.get('AIDER_TEST_LIVE') == '1', 'set AIDER_TEST_LIVE=1 for real backends')
class LiveBackendTests(unittest.TestCase):
    def make_runner(self, timeout=30):
        assets = Path(__file__).resolve().parents[1] / 'src/firecracker/assets'
        return Runner(Settings(kernel=os.environ.get('FC_KERNEL', str(assets / 'vmlinux')),
                               rootfs=os.environ.get('FC_ROOTFS', str(assets / 'rootfs.ext4')),
                               timeout=timeout))

    def test_task_selected_backends_read_current_edits_without_host_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / 'source.py').write_text('local edit')
            (project / '.env').write_text('test secret')
            command = '''python3 -c 'import os; from pathlib import Path; assert Path("source.py").read_text() == "local edit"; assert not Path(".env").exists(); assert "AIDER_TEST_HOST_CANARY" not in os.environ; print("isolated OK")' '''
            with patch.dict(os.environ, {'AIDER_TEST_HOST_CANARY': 'should-stay-on-host'}):
                runner = self.make_runner()
                for backend in ('container', 'microvm'):
                    with self.subTest(backend=backend):
                        code, output = runner.run(f'AIDER_TEST_BACKEND={backend} {command}', project)
                        self.assertEqual(code, 0, output)
                        self.assertIn('isolated OK', output)

    def test_failed_test_output_and_writes_are_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / 'source.py').write_text('local edit')
            for backend in ('container', 'microvm'):
                with self.subTest(backend=backend):
                    command = f'AIDER_TEST_BACKEND={backend} echo modified > source.py; echo intentional-failure >&2; exit 7'
                    code, output = self.make_runner().run(command, project)
                    self.assertEqual(code, 7, output)
                    self.assertIn('intentional-failure', output)
                    self.assertEqual((project / 'source.py').read_text(), 'local edit')

    def test_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as project:
            for backend in ('container', 'microvm'):
                with self.subTest(backend=backend):
                    code, output = self.make_runner(timeout=1).run(f'AIDER_TEST_BACKEND={backend} sleep 30', project)
                    self.assertEqual(code, 125, output)
                    self.assertIn('timed out', output)
        remaining = run_process(['podman', 'ps', '-aq', '--filter', 'name=^aider-test-'], check=True)
        self.assertEqual(remaining.stdout.strip(), '')
