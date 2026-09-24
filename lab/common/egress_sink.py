#!/usr/bin/env python3
"""Host-side HTTP sink on 127.0.0.1. Logs every request to egress.log.

Any entry means egress got out of the boundary. Bodies are logged as digest
and length only.

Usage: egress_sink.py <port> <egress_log_path>
"""
import hashlib
import http.server
import json
import sys
import time


def make_handler(log_path):
    class H(http.server.BaseHTTPRequestHandler):
        def _record(self, method):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b""
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "method": method, "path": self.path,
                "body_len": len(body),
                "body_sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
            }
            with open(log_path, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):  # noqa: N802
            self._record("POST")

        def do_GET(self):   # noqa: N802
            self._record("GET")

        def log_message(self, *a):
            pass
    return H


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: egress_sink.py <port> <egress_log_path>")
    port, log_path = int(sys.argv[1]), sys.argv[2]
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(log_path))
    srv.serve_forever()
