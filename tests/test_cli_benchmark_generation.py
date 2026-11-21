"""Opt-in end-to-end test: fake API, real Aider edits, real process sandbox."""
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
BENCH = ROOT / 'benchmarks/cli_refactor'


@unittest.skipUnless(os.environ.get('AIDER_TEST_PROCESS_LIVE') == '1',
                     'set AIDER_TEST_PROCESS_LIVE=1 for generation smoke test')
class GenerationTests(unittest.TestCase):
    def test_fake_generation_and_frozen_replay(self):
        # The read-only adapter policy contains triple backticks, so Aider chooses four.
        reference = BENCH / 'reference'
        reply = '\n\n'.join(f'{p.relative_to(reference)}\n````python\n{p.read_text()}````'
                            for p in sorted(reference.rglob('*.py')))

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                body = json.dumps({'id': 'fake-refactor', 'object': 'chat.completion', 'created': 1,
                    'model': 'benchmark-fake', 'choices': [{'index': 0, 'message': {
                        'role': 'assistant', 'content': reply}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}}).encode()
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
                command = [sys.executable, str(BENCH / 'benchmark.py')]
                proc = subprocess.run(command + ['generate', '--model', 'openai/benchmark-fake',
                    '--edit-format', 'whole', '--api-base', f'http://127.0.0.1:{server.server_port}/v1',
                    '--backend', 'process-sandbox', '--output', str(output), '--generation-timeout', '90'],
                    env=dict(os.environ, OPENAI_API_KEY='fake-test-key', AIDER_DRY_RUN='true'),
                    capture_output=True, text=True, timeout=150)
                log = (output / 'aider.log').read_text()
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr + log)
                generated = json.loads((output / 'generation.json').read_text())
                self.assertEqual(generated['usage']['total_tokens'], 300)
                self.assertEqual(generated['usage']['requests'], 1)
                self.assertEqual(len(generated['files_changed']), 7)
                self.assertIn('BENCH_RESULT=', log)  # Automatic tests really ran.
                replay = Path(directory) / 'replay'
                proc = subprocess.run(command + ['run', '--candidate', str(output / 'work'),
                    '--backends', 'process-sandbox', '--repeats', '1', '--warmups', '0',
                    '--output', str(replay)], capture_output=True, text=True, timeout=150)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                measured = json.loads((replay / 'metadata.json').read_text())
                self.assertEqual(measured['candidate_sha256'], generated['candidate_sha256'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
