#!/usr/bin/env python3
"""Host-side model gateway. The API key stays on the host; the boundary never sees it.

Only meaningful in real-agent mode (RUN_MODE=agent). The workload inside the
sandbox is configured to talk to this gateway instead of the model vendor. The
gateway:
  - listens on a Unix socket (bwrap / container) that the boundary binds in,
  - injects Authorization from the host environment,
  - forwards to the pinned upstream,
  - logs request metadata (never bodies, never the key) so you can show exactly
    what the one allowed network path carried.

For Firecracker, run the same forwarder bound to a vsock port instead of a Unix
socket (a ~15-line asyncio shim); the injection logic is identical.

This is deliberately minimal and is NOT a hardened proxy. Run it only on the
disposable demo host.
"""
import http.server
import json
import os
import socket
import socketserver
import sys
import time
import urllib.request

UPSTREAM = os.environ.get("MODEL_UPSTREAM", "https://api.openai.com").rstrip("/")
API_KEY = os.environ.get("MODEL_API_KEY", "")
LOG = os.environ.get("GATEWAY_LOG", "/dev/stderr")
TRACE = os.environ.get("GATEWAY_TRACE", "")  # jsonl: full turn bodies (redacted)
SOCK = os.environ.get("GATEWAY_SOCK", "")  # unix socket path; if empty use TCP PORT
PORT = int(os.environ.get("GATEWAY_PORT", "8080"))
_turn = [0]


def logline(**kw):
    kw["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(LOG, "a") as fh:
        fh.write(json.dumps(kw) + "\n")


def trace_turn(request_body, response_body, status):
    """Record one full turn to the trajectory. The API key is never here: the
    gateway ADDS it outbound, so neither the request nor response body carries it.
    We defensively strip any authorization-looking fields anyway."""
    if not TRACE:
        return
    _turn[0] += 1

    def safe(raw):
        try:
            obj = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            return {"_unparsed_bytes": len(raw or b"")}
        if isinstance(obj, dict):
            obj.pop("api_key", None)
            for h in ("authorization", "Authorization"):
                obj.pop(h, None)
        return obj

    req = safe(request_body)
    resp = safe(response_body)
    rec = {"turn": _turn[0],
           "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "status": status,
           "request": {"model": (req or {}).get("model"),
                       "messages": (req or {}).get("messages")},
           "response": {"choices": (resp or {}).get("choices"),
                        "usage": (resp or {}).get("usage")}}
    with open(TRACE, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self, method):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=body, method=method)
        for h in ("Content-Type", "Accept"):
            if h in self.headers:
                req.add_header(h, self.headers[h])
        if API_KEY:
            req.add_header("Authorization", f"Bearer {API_KEY}")
        logline(event="forward", method=method, path=self.path.split("?")[0],
                bytes_in=length, upstream=UPSTREAM)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                logline(event="response", status=resp.status, bytes_out=len(data))
                # Account tokens so footprint.py can total the agent's real cost.
                try:
                    usage = json.loads(data).get("usage", {})
                    if usage:
                        logline(event="usage",
                                prompt_tokens=usage.get("prompt_tokens", 0),
                                completion_tokens=usage.get("completion_tokens", 0))
                except Exception:
                    pass
                # Full-turn trajectory (chat completions only).
                if "chat/completions" in self.path:
                    trace_turn(body, data, resp.status)
        except Exception as e:
            logline(event="error", error=str(e))
            self.send_error(502, "gateway upstream error")

    def do_POST(self):  # noqa: N802
        self._forward("POST")

    def do_GET(self):   # noqa: N802
        self._forward("GET")

    def log_message(self, *a):  # silence default stderr spam
        pass


class UnixServer(socketserver.UnixStreamServer):
    allow_reuse_address = True

    def get_request(self):
        conn, _ = self.socket.accept()
        return conn, ("unix", 0)


def main():
    if not API_KEY:
        logline(event="warn", msg="MODEL_API_KEY empty; gateway will 401 upstream")
    if SOCK:
        if os.path.exists(SOCK):
            os.unlink(SOCK)
        srv = UnixServer(SOCK, Handler, bind_and_activate=False)
        srv.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.socket.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.socket.listen(16)
        logline(event="listen", transport="unix", path=SOCK, upstream=UPSTREAM)
    else:
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
        logline(event="listen", transport="tcp", port=PORT, upstream=UPSTREAM)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
