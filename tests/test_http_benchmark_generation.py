"""Opt-in fake-model generation of the HTTP refactor; no external API calls."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'benchmarks/http_client_refactor'
BENCHMARK = ROOT / 'benchmarks/cli_refactor/benchmark.py'


@unittest.skipUnless(os.environ.get('AIDER_TEST_PROCESS_LIVE') == '1',
                     'set AIDER_TEST_PROCESS_LIVE=1 for fake-model generation')
class HTTPGenerationTests(unittest.TestCase):
    def test_fake_model_edits_only_http_client_and_auto_tests_it(self):
        reply = ('src/http_client.py\n````python\n' +
                 (FIXTURE / 'reference/src/http_client.py').read_text() + '````')

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'id': 'fake-http', 'object': 'chat.completion', 'created': 1,
                    'model': 'benchmark-fake', 'choices': [{'index': 0, 'message': {
                        'role': 'assistant', 'content': reply}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 80, 'completion_tokens': 120, 'total_tokens': 200}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'generation'
                command = [sys.executable, str(BENCHMARK)]
                process = subprocess.run(command + ['generate', '--workload', 'http-client-refactor',
                    '--model', 'openai/benchmark-fake', '--edit-format', 'whole',
                    '--api-base', f'http://127.0.0.1:{server.server_port}/v1',
                    '--backend', 'process-sandbox', '--output', str(output),
                    '--generation-timeout', '90'], env=dict(os.environ, OPENAI_API_KEY='fake-key'),
                    capture_output=True, text=True, timeout=150)
                log = (output / 'aider.log').read_text()
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr + log)
                data = json.loads((output / 'generation.json').read_text())
                self.assertTrue(data['file_scope']['passed'], data['file_scope'])
                self.assertEqual(data['files_changed'], ['src/http_client.py'])
                self.assertEqual(data['usage']['total_tokens'], 200)
                self.assertIn('BENCH_RESULT=', log)
                replay = Path(directory) / 'replay'
                process = subprocess.run(command + ['run', '--workload', 'http-client-refactor',
                    '--candidate', str(output / 'work'), '--backends', 'process-sandbox',
                    '--repeats', '1', '--warmups', '0', '--output', str(replay)],
                    capture_output=True, text=True, timeout=150)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                measured = json.loads((replay / 'metadata.json').read_text())
                self.assertEqual(measured['candidate_sha256'], data['candidate_sha256'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
