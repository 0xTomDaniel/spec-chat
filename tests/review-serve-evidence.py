"""GET /api/evidence behind a fake evidence service (criterion-evidence spec)."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
_serve_spec = importlib.util.spec_from_file_location("review_serve_evidence_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)

SPEC = "docs/specs/demo.spec.html"


def criterion(anchor, text):
    return f'<p data-acceptance-criterion data-anchor="{anchor}">{text}</p>\n'


def page(**criteria):
    body = "".join(criterion(anchor.replace("_", "-"), text) for anchor, text in criteria.items())
    return f'<article class="spec"><p data-anchor="intro">Intro.</p>\n{body}</article>\n'


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def repo(root, html, origin="https://github.com/acme/spec-chat.git"):
    root.mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "remote", "add", "origin", origin)
    path = root / SPEC
    path.parent.mkdir(parents=True)
    path.write_text(html, encoding="utf-8")
    git(root, "add", SPEC)
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "spec")
    return git(root, "rev-parse", "HEAD")


class FakeEvidence:
    """Answers GET /criteria with a fixed body; records every request."""

    def __init__(self, body=None, status=200, raw=None):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                owner.requests.append(self.path)
                data = raw if raw is not None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/ev/" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def entry(**values):
    value = {"match": True, "verdict": "pass", "judgment": None, "pr": 58,
             "capturedAt": "2026-09-20T10:00:00Z", "onMain": True,
             "artifact": "/bundles/b1/artifacts/a.png", "bundle": "/bundles/b1",
             "proven": "Shows ok.", "view": "/bundles/b1#criterion=ok"}
    value.update(values)
    return value


class EvidenceRouteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    def fake(self, **kwargs):
        service = FakeEvidence(**kwargs)
        self.addCleanup(service.close)
        return service

    def review_server(self, mounts):
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState(mounts)
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % server.server_port

    def single(self, html):
        root = self.dir / "repo"
        head = repo(root, html)
        return self.review_server([serve._single_mount(root / "docs")]), root, head

    def get(self, url, path):
        with urllib.request.urlopen(url + path, timeout=10) as response:
            self.assertEqual(response.headers["Content-Type"], "application/json")
            return json.loads(response.read())

    def evidence(self, url, path="specs/demo.spec.html", extra=""):
        return self.get(url, "/api/evidence?path=" + path + extra)

    def test_matching_pass_and_material_mismatch_return_absolute_links(self):
        service = self.fake(body={"criteria": {
            "ok": entry(),
            "moved": entry(match=False, judgment="material", pr=61, onMain=False,
                           artifact="/bundles/b2/artifacts/m.png", bundle="/bundles/b2",
                           proven="Shows old.", view="/bundles/b2#criterion=moved"),
        }})
        url, _, head = self.single(page(ok="Shows ok.", moved="Shows moved."))
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            body = self.evidence(url)
        base = service.url.rstrip("/")
        self.assertEqual(body, {"criteria": {
            "ok": entry(artifact=base + "/bundles/b1/artifacts/a.png", bundle=base + "/bundles/b1",
                        view=base + "/bundles/b1#criterion=ok", uncommitted=False),
            "moved": entry(match=False, judgment="material", pr=61, onMain=False,
                           artifact=base + "/bundles/b2/artifacts/m.png", bundle=base + "/bundles/b2",
                           proven="Shows old.", view=base + "/bundles/b2#criterion=moved", uncommitted=False),
        }})
        self.assertEqual(len(service.requests), 1)
        request = urlparse(service.requests[0])
        self.assertEqual(request.path, "/ev/criteria")
        self.assertEqual(parse_qs(request.query), {
            "spec": ["project/spec-chat::" + SPEC], "commit": [head]})

    def test_uncommitted_criterion_is_marked(self):
        service = self.fake(body={"criteria": {"ok": entry(), "edited": entry()}})
        url, root, _ = self.single(page(ok="Shows ok.", edited="Shows old."))
        (root / SPEC).write_text(page(ok="Shows ok.", edited="Shows new."), encoding="utf-8")
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            body = self.evidence(url)
        self.assertFalse(body["criteria"]["ok"]["uncommitted"])
        self.assertTrue(body["criteria"]["edited"]["uncommitted"])

    def test_only_page_criteria_and_known_fields_pass_through(self):
        service = self.fake(body={"criteria": {
            "ok": entry(artifact=None, secret="x"), "intro": entry(), "gone": entry(), "bad": "nope"}})
        url, _, _ = self.single(page(ok="Shows ok.", bad="Bad."))
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            body = self.evidence(url)
        self.assertEqual(list(body["criteria"]), ["ok"])
        self.assertIsNone(body["criteria"]["ok"]["artifact"])
        self.assertNotIn("secret", body["criteria"]["ok"])

    def test_non_relative_paths_are_not_linked(self):
        service = self.fake(body={"criteria": {"ok": entry(
            artifact="javascript:alert(1)", bundle="//evil.example/bundles/b1", view="https://evil.example/")}})
        url, _, _ = self.single(page(ok="Shows ok."))
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            value = self.evidence(url)["criteria"]["ok"]
        self.assertIsNone(value["artifact"])
        self.assertIsNone(value["bundle"])
        self.assertIsNone(value["view"])

    def test_unset_down_error_and_non_json_answer_none(self):
        url, _, _ = self.single(page(ok="Shows ok."))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPEC_CHAT_EVIDENCE_URL", None)
            self.assertEqual(self.evidence(url), {"criteria": None})
        for service in (self.fake(status=500, body={"criteria": {"ok": entry()}}),
                        self.fake(raw=b"<html>"), self.fake(body=["list"])):
            with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
                self.assertEqual(self.evidence(url), {"criteria": None})
            self.assertEqual(len(service.requests), 1)
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": "http://127.0.0.1:9/"}):
            self.assertEqual(self.evidence(url), {"criteria": None})

    def test_parameters_beyond_path_are_ignored(self):
        service = self.fake(body={"criteria": {}})
        url, _, head = self.single(page(ok="Shows ok."))
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            self.assertEqual(self.evidence(url), {"criteria": {}})
            self.evidence(url, extra="&commit=deadbeef&spec=project/x::y&base=HEAD~1&url=http://evil")
        self.assertEqual(service.requests[0], service.requests[1])
        self.assertIn("commit=" + head, service.requests[1])

    def test_lane_and_main_rows_each_name_their_own_head(self):
        service = self.fake(body={"criteria": {}})
        main_root = self.dir / "main"
        main_head = repo(main_root, page(ok="Shows ok."))
        lane_root = self.dir / "lane"
        lane_head = repo(lane_root, page(ok="Shows ok, reworded materially."), origin="git@github.com:acme/spec-chat")
        rows = []
        for slug, root in (("main", main_root), ("ann1", lane_root)):
            rows.append({"id": slug, "slug": slug, "root": str(root), "narrow_root": str(root / "docs"),
                         "spec": SPEC, "spec_file": str(root / SPEC), "path": slug + "/" + SPEC, "base": "HEAD"})
        url = self.review_server(rows)
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            self.evidence(url, path="main/" + SPEC)
            self.evidence(url, path="ann1/" + SPEC)
        commits = [parse_qs(urlparse(request).query) for request in service.requests]
        self.assertEqual(commits, [
            {"spec": ["project/spec-chat::" + SPEC], "commit": [main_head]},
            {"spec": ["project/spec-chat::" + SPEC], "commit": [lane_head]},
        ])

    def watched(self, url, review_dir):
        with urllib.request.urlopen(url + "/api/events?dir=" + review_dir, timeout=10) as response:
            return response.headers.get("X-Spec-Chat-Watched")

    def test_events_report_whether_the_row_has_an_owner(self):
        root = self.dir / "repo"
        repo(root, page(ok="Shows ok."))
        row = {"id": "r", "slug": "lane", "root": str(root), "narrow_root": str(root / "docs"),
               "spec": SPEC, "spec_file": str(root / SPEC), "base": "HEAD", "owner": "w1:pOwner"}
        for mounts, review_dir, expected in (
            ([row], "lane/" + SPEC + ".review", "yes"),
            ([row | {"owner": ""}], "lane/" + SPEC + ".review", "no"),
            ([serve._single_mount(root / "docs")], "specs/demo.spec.html.review", "no"),
        ):
            with self.subTest(expected=expected, mount=mounts[0]["id"]):
                url = self.review_server(mounts)
                self.servers[-1].wake_controller = serve.WakeController(self.servers[-1])
                self.assertEqual(self.watched(url, review_dir), expected)

    def test_bad_path_is_rejected_without_a_read(self):
        service = self.fake(body={"criteria": {}})
        url, _, _ = self.single(page(ok="Shows ok."))
        with patch.dict(os.environ, {"SPEC_CHAT_EVIDENCE_URL": service.url}):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.evidence(url, path="specs/missing.spec.html")
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(service.requests, [])


if __name__ == "__main__":
    unittest.main()
