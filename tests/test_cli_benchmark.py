"""Checks for the benchmark's oracle and measurement reporting."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import http.server
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parents[1] / 'benchmarks/cli_refactor'
sys.path.insert(0, str(HERE))
import benchmark
import check
from model_proxy import proxy, usage_summary


class WorkloadTests(unittest.TestCase):
    def test_reference_preserves_every_case_and_meets_structure(self):
        self.assertEqual(check.evaluate(HERE / 'reference', HERE / 'baseline'), [])

    def test_unchanged_baseline_is_not_a_completed_refactor(self):
        self.assertTrue(check.structure(HERE / 'baseline'))

    def test_stdout_and_file_write_regressions_are_caught(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / 'candidate'
            shutil.copytree(HERE / 'reference', candidate)
            (candidate / 'mycli/commands/copy.py').write_text('def copy(args):\n    print("copied")\n    return 0\n')
            path = candidate / 'mycli/commands/greet.py'
            path.write_text(path.read_text().replace('Hello,', 'Goodbye,'))
            errors = check.evaluate(candidate, HERE / 'baseline')
            self.assertTrue(any("'copy'" in error for error in errors))
            self.assertTrue(any("'greet'" in error for error in errors))

    def test_prepare_creates_identical_starting_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            _, first, a = benchmark.prepare(Path(directory) / 'first')
            _, second, b = benchmark.prepare(Path(directory) / 'second')
            self.assertEqual(a['starting_commit'], b['starting_commit'])
            self.assertEqual(benchmark.manifest(first), benchmark.manifest(second))
            # Evaluation replaces an edited checker, including a symlink.
            (first / '_bench/check.py').write_text('raise SystemExit(0)')
            benchmark.stage_checker(first)
            self.assertEqual((first / '_bench/check.py').read_bytes(), (HERE / 'check.py').read_bytes())

    def test_failures_do_not_become_fast_successes(self):
        rows = [{'backend': 'container', 'status': 'error', 'wall_seconds': .001, 'check': {}},
                {'backend': 'container', 'status': 'passed', 'wall_seconds': 3,
                 'check': {'check_seconds': 1}}]
        stats = benchmark.aggregate(rows, ['container', 'microvm'])
        self.assertEqual(stats['container']['median_wall_seconds'], 3)
        self.assertEqual(stats['container']['errors'], 1)
        self.assertIsNone(stats['microvm']['median_wall_seconds'])

    def test_unknown_usage_remains_unknown(self):
        records = [{'status': 200, 'seconds': .5, 'usage': {'prompt_tokens': 20,
                    'completion_tokens': 3, 'total_tokens': 23}},
                   {'status': 502, 'seconds': .5, 'usage': None}]
        stats = usage_summary(records)
        self.assertEqual(stats['requests'], 2)
        self.assertEqual(stats['failed_requests'], 1)
        self.assertIsNone(stats['total_tokens'])
        self.assertEqual(usage_summary(records[:1])['total_tokens'], 23)
        self.assertIsNone(usage_summary([])['total_tokens'])

    def test_proxy_records_success_and_error_without_credentials_or_prompt(self):
        seen = []

        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.path, self.headers['Authorization']))
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                status = 200 if request['model'] == 'ok' else 429
                body = json.dumps({'usage': {'prompt_tokens': 10, 'completion_tokens': 2,
                                  'total_tokens': 12}, 'choices': []}).encode()
                self.send_response(status)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                trace = Path(directory) / 'requests.jsonl'
                with proxy(f'http://127.0.0.1:{server.server_port}/custom/v1', 'secret-key', trace) as base:
                    for model in ('ok', 'rate-limit'):
                        req = urllib.request.Request(base + '/chat/completions',
                            data=json.dumps({'model': model, 'messages': [{'content': 'private prompt'}]}).encode())
                        try:
                            with urllib.request.urlopen(req) as response:
                                self.assertEqual(response.status, 200)
                        except urllib.error.HTTPError as exc:
                            self.assertEqual(exc.code, 429)
                raw = trace.read_text()
                self.assertNotIn('secret-key', raw)
                self.assertNotIn('private prompt', raw)
                records = [json.loads(line) for line in raw.splitlines()]
                self.assertEqual([r['status'] for r in records], [200, 429])
                self.assertEqual(seen[0], ('/custom/v1/chat/completions', 'Bearer secret-key'))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
