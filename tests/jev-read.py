"""Compact agent read of a review link's Jev checks (project-rules #agent-read, acceptance-agent-read)."""

import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skill" / "review-spec" / "scripts" / "jev-read.py"
RULE = "specs/onboarding.spec.html#acceptance-onboarding"

ON = {
    "jev": "on",
    "items": [
        {"kind": "rule", "id": "acceptance", "state": "label", "label": "missed", "target": RULE,
         "word": "onboarding", "record": "r1", "level": "important", "agent_level": "important"},
        {"kind": "corpus", "id": "save-rule", "state": "label", "label": "contradicts",
         "target": "non-goal-offline", "record": "r2", "level": "important", "agent_level": "important"},
        {"kind": "type", "id": "a", "state": "label", "label": "behavioral", "target": None, "record": "r3", "level": "warning", "agent_level": "warning"},
        {"kind": "type", "id": "b", "state": "label", "label": "scope", "target": None, "record": "r4", "level": "warning", "agent_level": "warning"},
        {"kind": "type", "id": "save-rule", "state": "label", "label": "cosmetic", "target": None, "record": "r5", "level": "warning", "agent_level": "warning"},
        {"kind": "audience", "id": "c", "state": "label", "label": "internals", "target": None, "record": "r6", "level": "warning", "agent_level": "warning"},
        {"kind": "audience", "id": "d", "state": "label", "label": "internals", "target": None, "record": "r7", "level": "warning", "agent_level": "warning"},
        {"kind": "corpus", "id": "e", "state": "none", "label": None, "target": "x", "record": "r8"},
        {"kind": "coverage", "id": "f", "state": "label", "label": "verifies", "target": "g", "record": "r9"},
    ],
    "rules": [RULE],
}


class Server:
    """Serves one canned /api/jev answer per request, in order, and keeps every query."""

    def __init__(self, *answers, status=200):
        self.answers, self.status, self.queries = list(answers), status, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/jev":  # other box processes may probe loopback ports
                    self.send_error(404)
                    return
                owner.queries.append((parsed.path, parse_qs(parsed.query)))
                body = json.dumps(owner.answers.pop(0) if len(owner.answers) > 1 else owner.answers[0]).encode()
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run(*args):
    return subprocess.run((sys.executable, str(SCRIPT), *args), capture_output=True, text=True, timeout=30)


class JevReadTest(unittest.TestCase):
    def serve(self, *answers, status=200):
        server = Server(*answers, status=status)
        self.addCleanup(server.close)
        return server

    def test_first_failing_compact_read_of_the_link_spec_and_base(self):
        server = self.serve(ON)
        out = run(server.url + "/specs/b.spec.html?focus=changes&base=abc123")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(server.queries, [("/api/jev", {"path": ["specs/b.spec.html"], "base": ["abc123"]})])
        self.assertEqual(out.stdout.splitlines(), [
            "important  acceptance  rule         important  " + RULE,
            "important  save-rule   contradicts  important  #non-goal-offline",
            "warnings   type 3, audience 2",
            "rules      " + RULE,
            "pending    0",
        ])
        self.assertNotIn("{", out.stdout)

    def test_keys_on_agent_level_not_level(self):
        items = [
            {"kind": "corpus", "id": "over", "state": "label", "label": "oversteps", "target": "non-goal-x",
             "record": "r1", "level": "warning", "agent_level": "important"},
            {"kind": "corpus", "id": "rev", "state": "label", "label": "contradicts", "target": "y",
             "record": "r2", "level": "important", "agent_level": "warning"},
        ]
        server = self.serve({"jev": "on", "items": items, "rules": []})
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc").stdout.splitlines(), [
            "important  over  oversteps  important  #non-goal-x",
            "warnings   corpus 1",
            "rules      none",
            "pending    0",
        ])

    def test_agent_level_absent_is_neither_mark_nor_warning(self):
        server = self.serve({"jev": "on", "items": [dict(item, agent_level=None) for item in ON["items"]], "rules": []})
        out = run(server.url + "/specs/b.spec.html?base=abc")
        self.assertEqual(out.stdout.splitlines(), ["warnings  0", "rules     none", "pending   0"])

    def test_pending_counted_and_wait_rereads_until_none(self):
        pending = {"jev": "on", "items": [{"kind": "rule", "id": "acceptance", "state": "pending", "target": RULE}], "rules": []}
        server = self.serve(pending)
        self.assertIn("pending   1", run(server.url + "/specs/b.spec.html?base=abc").stdout.splitlines())
        server = self.serve(pending, pending, ON)
        out = run(server.url + "/specs/b.spec.html?base=abc", "--wait", "20")
        self.assertEqual(len(server.queries), 3)
        self.assertEqual(out.stdout.splitlines()[-1], "pending    0")

    def test_off(self):
        server = self.serve({"jev": "off", "items": []})
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc").stdout, "off\n")
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "a").stdout, "off\n")

    def test_anchor_prints_full_detail_for_that_anchor_only(self):
        server = self.serve(ON)
        out = run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "save-rule").stdout
        self.assertIn("label  contradicts", out)
        self.assertIn("record  r2", out)
        self.assertIn("label  cosmetic", out)
        self.assertNotIn("acceptance", out)
        self.assertNotIn("behavioral", out)
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "zz").stdout, "zz  no items\n")

    def test_server_error_fails_loudly(self):
        server = self.serve({"error": "invalid base"}, status=400)
        out = run(server.url + "/specs/b.spec.html?base=nope")
        self.assertEqual(out.returncode, 1)
        self.assertIn("invalid base", out.stderr)
        self.assertEqual(out.stdout, "")


if __name__ == "__main__":
    unittest.main()
