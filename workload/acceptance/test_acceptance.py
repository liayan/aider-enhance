"""Acceptance tests for the REAL coding task.

The runner copies this file into the workspace *after* the agent finishes, so the
model cannot see or tailor its output to these exact checks — this grades the
agent's real work rather than the tests it wrote itself.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.app import summarize  # noqa: E402


class SummarizeAcceptance(unittest.TestCase):
    def test_shape(self):
        r = summarize("Hello World")
        self.assertEqual(set(r), {"words", "chars", "slug"})

    def test_counts(self):
        r = summarize("Hello World")
        self.assertEqual(r["words"], 2)
        self.assertEqual(r["chars"], len("HelloWorld"))  # non-whitespace only

    def test_slug(self):
        self.assertEqual(summarize("Hello, World!")["slug"], "hello-world")

    def test_empty(self):
        r = summarize("")
        self.assertEqual(r["words"], 0)
        self.assertEqual(r["chars"], 0)


if __name__ == "__main__":
    unittest.main()
