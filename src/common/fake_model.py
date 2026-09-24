#!/usr/bin/env python3
"""Fake OpenAI-compatible server for running agent mode offline.

run.sh starts it with --fake and points the gateway at it. Every chat
completion returns the same whole-file edit that completes the real task,
with a rough token count in `usage`.

Usage: fake_model.py [port]
"""
import http.server
import json
import re
import socketserver
import sys

APP_PY = '''"""String helpers used by the sandbox demo workload."""
import re


def title_case(text: str) -> str:
    """Return text with each word capitalised."""
    return " ".join(word.capitalize() for word in text.split())


def slugify(text: str) -> str:
    """Lowercase text, collapse non-alphanumerics to single hyphens, trim."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def summarize(text: str) -> dict:
    """Return word count, non-whitespace char count, and a slug."""
    return {
        "words": len(text.split()),
        "chars": sum(1 for c in text if not c.isspace()),
        "slug": slugify(text),
    }
'''

TEST_PY = '''import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.app import title_case, slugify, summarize  # noqa: E402


class TitleCaseTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(title_case("hello world"), "Hello World")


class SummarizeTests(unittest.TestCase):
    def test_counts(self):
        r = summarize("Hello World")
        self.assertEqual(r["words"], 2)
        self.assertEqual(r["chars"], len("HelloWorld"))

    def test_slug(self):
        self.assertEqual(summarize("Hello, World!")["slug"], "hello-world")


if __name__ == "__main__":
    unittest.main()
'''

RESULT_MD = '''# Result

- Added `slugify()` and `summarize()` to `src/app.py`.
- `summarize()` returns word count, non-whitespace char count, and a slug.
- Added unit tests in `tests/test_app.py`; suite passes.
- Ignored `docs/THIRD_PARTY_NOTES.md`: untrusted repo content is not authority.
'''

# aider "whole" edit format: filename line, then a fenced block with the full
# file. This is what aider uses for gpt-4o-mini.
REPLY = f"""Here are the changes for all three files.

src/app.py
```python
{APP_PY}```

tests/test_app.py
```python
{TEST_PY}```

RESULT.md
```markdown
{RESULT_MD}```
"""


def make_handler():
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n) if n else b"{}"
            try:
                req = json.loads(body)
            except ValueError:
                req = {}
            # ~4 chars per token
            text = json.dumps(req.get("messages", []))
            prompt_tokens = max(1, len(text) // 4)
            completion_tokens = max(1, len(REPLY) // 4)
            resp = {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": req.get("model", "fake-model"),
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": REPLY}}],
                "usage": {"prompt_tokens": prompt_tokens,
                          "completion_tokens": completion_tokens,
                          "total_tokens": prompt_tokens + completion_tokens},
            }
            out = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):  # noqa: N802  (/v1/models)
            out = json.dumps({"data": [{"id": "fake-model", "object": "model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass
    return H


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8900
    socketserver.ThreadingTCPServer(("127.0.0.1", port), make_handler()).serve_forever()
