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
        {"kind": "corpus", "id": "a", "state": "label", "label": "overlaps", "target": "x", "record": "r3", "level": "warning", "agent_level": "warning", "unsure": 1},
        {"kind": "corpus", "id": "b", "state": "label", "label": "overlaps", "target": "y", "record": "r4", "level": "warning", "agent_level": "warning"},
        {"kind": "corpus", "id": "save-rule", "state": "label", "label": "overlaps", "target": "z", "record": "r5", "level": "warning", "agent_level": "warning"},
        {"kind": "type", "id": "save-rule", "state": "label", "label": "no-behavior-change", "target": None, "record": "r10", "unsure": 1},
        {"kind": "audience", "id": "c", "state": "label", "label": "internals", "target": None, "record": "r6", "unsure": 2},
        {"kind": "type", "id": "d", "state": "label", "label": "behavior", "target": None, "record": "r7"},
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
                self.answer()

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/jev/offer":
                    self.send_error(404)
                    return
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.queries.append((parsed.path, parse_qs(parsed.query), data))
                self.answer()

            def answer(self):
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
            "important   acceptance  rule         important  " + RULE,
            "important   save-rule   contradicts  important  #non-goal-offline",
            "warnings    corpus 3, unsure 4",
            "rules       " + RULE,
            "candidates  none",
            "pending     0",
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
            "important   over  oversteps  important  #non-goal-x",
            "warnings    corpus 1",
            "rules       none",
            "candidates  none",
            "pending     0",
        ])

    def test_agent_level_absent_is_neither_mark_nor_warning(self):
        server = self.serve({"jev": "on", "items": [dict(item, agent_level=None, unsure=None) for item in ON["items"]], "rules": []})
        out = run(server.url + "/specs/b.spec.html?base=abc")
        self.assertEqual(out.stdout.splitlines(), ["warnings    0", "rules       none", "candidates  none", "pending     0"])

    def test_pending_counted_and_wait_rereads_until_none(self):
        pending = {"jev": "on", "items": [{"kind": "rule", "id": "acceptance", "state": "pending", "target": RULE}], "rules": []}
        server = self.serve(pending)
        self.assertIn("pending     1", run(server.url + "/specs/b.spec.html?base=abc").stdout.splitlines())
        server = self.serve(pending, pending, ON)
        out = run(server.url + "/specs/b.spec.html?base=abc", "--wait", "20")
        self.assertEqual(len(server.queries), 3)
        self.assertEqual(out.stdout.splitlines()[-1], "pending     0")

    def test_off(self):
        server = self.serve({"jev": "off", "items": []})
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc").stdout, "off\n")
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "a").stdout, "off\n")

    def test_anchor_prints_full_detail_for_that_anchor_only(self):
        server = self.serve(ON)
        out = run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "save-rule").stdout
        self.assertIn("label  contradicts", out)
        self.assertIn("record  r2", out)
        self.assertIn("label  no-behavior-change", out)
        self.assertNotIn("acceptance", out)
        self.assertNotIn("record  r7", out)
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc", "--anchor", "zz").stdout, "zz  no items\n")

    def test_candidates_listed_on_their_own_line_not_as_warnings(self):
        cand = "specs/peer-seams.spec.html#acceptance-ci"
        items = [
            {"kind": "candidate", "id": None, "state": "label", "label": "candidate", "target": cand, "text": "CI runs.",
             "name": "CI runs", "record": "s1", "level": "warning", "agent_level": "warning"},
            {"kind": "candidate", "id": None, "state": "label", "label": "candidate", "target": cand, "text": "CI runs.",
             "name": "CI runs", "record": "s1", "level": "warning", "agent_level": "warning"},
            {"kind": "candidate", "id": None, "state": "pending", "label": "candidate", "target": "specs/x.spec.html#acceptance-y",
             "record": "s2", "level": "warning", "agent_level": "warning", "escalated": True},
            {"kind": "type", "id": "d", "state": "label", "label": "internals", "target": None, "record": "r7", "level": "warning", "agent_level": "warning"},
        ]
        server = self.serve({"jev": "on", "items": items, "rules": [RULE]})
        self.assertEqual(run(server.url + "/specs/b.spec.html?base=abc").stdout.splitlines(), [
            "warnings    type 1",
            "rules       " + RULE,
            "candidates  " + cand,
            "pending     1",
        ])

    def test_no_candidates_reads_none(self):
        server = self.serve({"jev": "on", "items": [], "rules": []})
        self.assertIn("candidates  none", run(server.url + "/specs/b.spec.html?base=abc").stdout.splitlines())

    def test_resolve_posts_an_owner_resolution_to_the_one_record_route(self):
        # jev-suggestions #record-resolution, #acceptance-resolution
        server = self.serve({"ok": True})
        out = run(server.url + "/specs/b.spec.html?base=abc", "--resolve", "r2", "dismissed", "--reason", "Jev is wrong here")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout, "r2  dismissed\n")
        self.assertEqual(server.queries, [("/api/jev/offer", {"path": ["specs/b.spec.html"]},
                                           {"resolve": "dismissed", "record": "r2", "reason": "Jev is wrong here"})])
        out = run(server.url + "/specs/b.spec.html?base=abc", "--resolve", "r1", "thread")
        self.assertEqual((out.returncode, server.queries[-1][2]), (0, {"resolve": "thread", "record": "r1"}))

    def test_resolve_refused_fails_loudly_with_the_server_reason(self):
        # the server is the one check of a resolution; its refusal reason is printed
        server = self.serve({"ok": False, "error": "refused: dismissed needs a reason"}, status=400)
        out = run(server.url + "/specs/b.spec.html?base=abc", "--resolve", "r2", "dismissed")
        self.assertEqual(out.returncode, 1)
        self.assertIn("HTTP 400: refused: dismissed needs a reason", out.stderr)
        self.assertEqual(server.queries[-1][2], {"resolve": "dismissed", "record": "r2"})
        # a link with no spec path posts to the record route without one, never a traceback
        out = run(server.url + "/", "--resolve", "r2", "fixed")
        self.assertEqual(out.returncode, 1)
        self.assertNotIn("Traceback", out.stderr)

    def test_server_error_fails_loudly(self):
        server = self.serve({"error": "invalid base"}, status=400)
        out = run(server.url + "/specs/b.spec.html?base=nope")
        self.assertEqual(out.returncode, 1)
        self.assertIn("invalid base", out.stderr)
        self.assertEqual(out.stdout, "")


if __name__ == "__main__":
    unittest.main()
