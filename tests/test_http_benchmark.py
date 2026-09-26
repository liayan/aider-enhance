"""Regression tests for the HTTP oracle and scope checks before sandbox filtering."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HTTP = ROOT / 'benchmarks/http_client_refactor'
sys.path.insert(0, str(ROOT / 'benchmarks/cli_refactor'))
import benchmark
from file_scope import inventory

spec = importlib.util.spec_from_file_location('http_contract', HTTP / 'check.py')
http_contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(http_contract)


class HTTPBenchmarkTests(unittest.TestCase):
    def copy_reference(self, directory):
        candidate = Path(directory) / 'candidate'
        shutil.copytree(HTTP / 'reference', candidate, ignore=shutil.ignore_patterns('__pycache__'))
        return candidate

    def test_reference_passes_and_baseline_fails_new_contract(self):
        good = http_contract.evaluate(HTTP / 'reference', HTTP / 'baseline')
        self.assertTrue(good['passed'], good['errors'])
        self.assertEqual(good['cases'], 11)
        bad = http_contract.evaluate(HTTP / 'baseline', HTTP / 'baseline')
        self.assertFalse(bad['passed'])

    def test_oracle_detects_transport_regressions(self):
        mutations = [
            ('range(3)', 'range(2)'),  # Too few total attempts.
            ('method.upper() == "GET"', 'method.upper() in ("GET", "POST")'),
            ('500 <= response.status_code < 600', '400 <= response.status_code < 600'),
            ('0.1 * (attempt + 1)', '0'),
            ('kwargs.pop("timeout", DEFAULT_TIMEOUT)', '10'),
            ('from exc', 'from None'),
            ('timeout=5)', 'timeout=10)'),
            ('logger.warning("Retrying request")', 'logger.warning(str(exc))'),
        ]
        for before, after in mutations:
            with self.subTest(mutation=after), tempfile.TemporaryDirectory() as directory:
                candidate = self.copy_reference(directory)
                path = candidate / 'src/http_client.py'
                source = path.read_text()
                self.assertIn(before, source)
                path.write_text(source.replace(before, after))
                result = http_contract.evaluate(candidate, HTTP / 'baseline')
                self.assertFalse(result['passed'], f'Uncaught mutation: {after}')

    def test_prepared_http_baseline_has_reproducible_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            _, first, a = benchmark.prepare(Path(directory) / 'a', 'http-client-refactor')
            _, second, b = benchmark.prepare(Path(directory) / 'b', 'http-client-refactor')
            self.assertEqual(a['starting_commit'], b['starting_commit'])
            self.assertEqual(benchmark.manifest(first), benchmark.manifest(second))
            self.assertTrue(benchmark.validate_scope(first, 'http-client-refactor')['passed'])

    def test_scope_catches_modified_deleted_untracked_and_ignored_files(self):
        for path, operation in [('consumer.py', 'modify'), ('src/__init__.py', 'delete'),
                                ('extra.py', 'modify'), ('.env', 'modify'), ('private.key', 'modify'),
                                ('.aider.unexpected', 'modify'), ('.gitignore', 'modify')]:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as directory:
                candidate = self.copy_reference(directory)
                target = candidate / path
                if operation == 'delete':
                    target.unlink()
                else:
                    target.write_text('changed')
                report = benchmark.validate_scope(candidate, 'http-client-refactor')
                self.assertFalse(report['passed'])
                self.assertIn(path, report['violations'])

    def test_scope_catches_permission_changes_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.copy_reference(directory)
            (candidate / 'consumer.py').chmod(0o755)
            (candidate / 'src/http_client.py').unlink()
            (candidate / 'src/http_client.py').symlink_to('/does-not-exist/outside')
            entries = inventory(candidate)
            self.assertEqual(entries['src/http_client.py']['type'], 'symlink')
            report = benchmark.validate_scope(candidate, 'http-client-refactor')
            self.assertIn('consumer.py', report['violations'])
            self.assertIn('src/http_client.py', report['violations'])

    def test_trusted_helpers_are_checked_before_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.copy_reference(directory)
            benchmark.stage_checker(candidate, 'http-client-refactor')
            (candidate / '_bench/check.py').write_text('print("fake success")')
            report = benchmark.validate_scope(candidate, 'http-client-refactor')
            self.assertIn('_bench/check.py', report['violations'])
            shutil.rmtree(candidate / '_bench')
            report = benchmark.validate_scope(candidate, 'http-client-refactor', require_helpers=True)
            self.assertIn('_bench/check.py', report['violations'])

    def test_only_known_tool_artifacts_are_exempt(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.copy_reference(directory)
            (candidate / '.git').mkdir()
            (candidate / '.aider.tags.cache.v4').mkdir()
            (candidate / '.aider.chat.history.md').write_text('chat')
            (candidate / '__pycache__').mkdir()
            (candidate / '__pycache__/module.pyc').write_bytes(b'bytecode')
            self.assertTrue(benchmark.validate_scope(candidate, 'http-client-refactor')['passed'])
            (candidate / '__pycache__/hidden.py').write_text('bad')
            self.assertFalse(benchmark.validate_scope(candidate, 'http-client-refactor')['passed'])

    def test_replay_fails_scope_even_when_behavior_passes_and_snapshot_excludes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = self.copy_reference(directory)
            (candidate / '.env').write_text('a new file excluded by the sandbox snapshot')
            output = Path(directory) / 'results'
            success = 'BENCH_RESULT=' + json.dumps({'passed': True, 'cases': 11, 'check_seconds': 1})
            with patch.object(benchmark.Runner, 'run', return_value=(0, success)), \
                    patch.object(benchmark, 'command_output', return_value=None):
                code = benchmark.main(['run', '--workload', 'http-client-refactor',
                    '--candidate', str(candidate), '--output', str(output), '--backends', 'container',
                    '--repeats', '1', '--warmups', '0'])
            self.assertEqual(code, 1)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['container']['passed'], 0)
            self.assertEqual(summary['container']['failed'], 1)
            meta = json.loads((output / 'metadata.json').read_text())
            self.assertIn('.env', meta['file_scope']['violations'])

    def test_cli_scope_also_rejects_changes_outside_package(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'cli'
            shutil.copytree(benchmark.HERE / 'reference', candidate)
            (candidate / 'extra.py').write_text('not allowed')
            self.assertIn('extra.py', benchmark.validate_scope(candidate)['violations'])


if __name__ == '__main__':
    unittest.main()
