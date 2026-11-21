"""Local non-streaming OpenAI-compatible proxy recording usage, not prompts or keys."""
from contextlib import contextmanager
import hashlib
import http.server
import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


@contextmanager
def proxy(api_base, api_key, trace_path):
    parsed = urlsplit(api_base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        raise ValueError('Use a complete API base URL without credentials, query or fragment')
    lock = threading.Lock()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None  # Do not send provider credentials to a redirect target.

    opener = urllib.request.build_opener(NoRedirect)


    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16 * 1024 * 1024:
                self.send_error(413)
                return
            body = self.rfile.read(length)
            request_json = json.loads(body)
            if request_json.get('stream'):
                self.send_error(400, 'Benchmark requires --no-stream')
                return
            record = {'request_sha256': hashlib.sha256(body).hexdigest(),
                      'model': request_json.get('model'), 'usage': None}
            started = time.monotonic()
            request = urllib.request.Request(api_base.rstrip('/') + '/chat/completions',
                data=body, headers={'Content-Type': 'application/json',
                                    'Authorization': 'Bearer ' + api_key})
            try:
                with opener.open(request, timeout=120) as response:
                    status, data = response.status, response.read(16 * 1024 * 1024 + 1)
                if len(data) > 16 * 1024 * 1024:
                    raise ValueError('Response too large')
                record['usage'] = json.loads(data).get('usage')
            except urllib.error.HTTPError as exc:
                status = exc.code
                # Do not return potentially sensitive upstream error bodies to aider.
                data = json.dumps({'error': {'message': f'Upstream HTTP {status}'}}).encode()
            except Exception as exc:
                status = 502
                record['error_type'] = type(exc).__name__
                data = b'{"error":{"message":"Upstream request failed"}}'
            record.update(status=status, seconds=time.monotonic() - started)
            with lock:
                with trace_path.open('a') as stream:
                    stream.write(json.dumps(record) + '\n')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False  # Finish in-flight trace records before closing.
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def usage_summary(records):
    def total(field):
        values = [(r.get('usage') or {}).get(field) for r in records]
        return sum(values) if values and all(type(v) is int and v >= 0 for v in values) else None

    return {'requests': len(records),
            'failed_requests': sum(not 200 <= r['status'] < 300 for r in records),
            'prompt_tokens': total('prompt_tokens'),
            'completion_tokens': total('completion_tokens'),
            'total_tokens': total('total_tokens'),
            'request_seconds_sum': sum(r['seconds'] for r in records),
            'note': 'Missing usage stays null. Attempts include retries; identical hashes do not prove a retry. '
                    'Cached/reasoning token details are preserved in requests.jsonl. No cost estimates.'}
