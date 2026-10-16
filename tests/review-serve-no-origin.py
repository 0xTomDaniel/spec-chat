"""A public review service answers a page with no origin; every other request gets no cross-origin permission
(review-service #served-no-origin, #served-private)."""

import contextlib
import http.client
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
_serve_spec = importlib.util.spec_from_file_location("review_serve_no_origin_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)

PAGE = '<article class="spec"><p data-anchor="intro">Intro.</p></article>\n'
EVENT = {"event": "comment", "id": "c1", "anchor": "intro", "text": "Looks right."}
BASE = "https://spec.203-0-113-9.sslip.io"


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def cross_origin(headers):
    """Every header that grants or shapes a cross-origin read."""
    return {name: value for name, value in headers.items() if name.startswith("access-control-") or name == "vary"}


class NoOriginCase(unittest.TestCase):
    """Requests a page sends, and what its answers grant; PAGE_PATH is where the service serves the spec."""

    PAGE_PATH = "/demo.spec.html"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-no-origin-")
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.specs = self.dir / "specs"
        self.specs.mkdir()
        (self.specs / "demo.spec.html").write_text(PAGE, encoding="utf-8")

    def serve(self, bind):
        """A single-root review server listening on bind, reached on loopback."""
        server = serve.ReviewThreadingHTTPServer((bind, 0), serve.MountHandler)
        server.mount_state = serve.MountState([serve._single_mount(self.specs)])
        server.state_dir = None
        server.wake_controller = types.SimpleNamespace(status=lambda _resource: None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_port

    def request(self, port, method, path, origin=None, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            sent = dict(headers or {})
            if origin is not None:
                sent["Origin"] = origin
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            data = response.read()
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        finally:
            connection.close()

    def page_requests(self, port, origin):
        """What a page sends: its spec and version tag, its events, a preflighted comment save, and a miss."""
        page = self.PAGE_PATH
        events = "/api/events?dir=" + page + ".review"
        save = events + "&actor=human&name=1-comment-c1.json"
        answers = {
            "spec": self.request(port, "GET", page, origin),
            "head": self.request(port, "HEAD", page, origin),
            "preflight": self.request(port, "OPTIONS", save, origin, headers={
                "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}),
            "save": self.request(port, "POST", save, origin, body=json.dumps(EVENT),
                                 headers={"Content-Type": "application/json"}),
            "events": self.request(port, "GET", events, origin),
            "missing": self.request(port, "GET", page.replace("demo", "absent"), origin),
        }
        answers["unchanged"] = self.request(port, "GET", page, origin,
                                            headers={"If-None-Match": answers["spec"][1]["etag"]})
        return answers

    def assert_no_origin_answered(self, answers):
        status, headers, _ = answers["preflight"]
        self.assertEqual(status, 204)
        self.assertEqual(headers["access-control-allow-origin"], "null")
        self.assertIn("content-type", headers["access-control-allow-headers"].lower())
        for name in ("spec", "head", "save", "events", "missing", "unchanged"):
            status, headers, _ = answers[name]
            self.assertEqual(headers.get("access-control-allow-origin"), "null", name)
            self.assertNotIn("access-control-allow-credentials", headers, name)
            self.assertEqual(headers.get("vary"), "Origin", name)
            exposed = {value.strip().lower() for value in headers["access-control-expose-headers"].split(",")}
            self.assertLessEqual({"etag", "x-spec-chat-wake"}, exposed, name)
        self.assertEqual(answers["save"][0], 200)
        self.assertEqual(json.loads(answers["save"][2]), {"ok": True, "name": "1-comment-c1.json"})
        self.assertEqual(answers["unchanged"][0], 304)

    def assert_no_cross_origin_read(self, answers):
        status, headers, _ = answers["preflight"]
        self.assertEqual(status, 501)
        for name, (_, headers, _) in answers.items():
            self.assertEqual(cross_origin(headers), {}, name)


class ServedNoOriginTest(NoOriginCase):
    def test_public_listener_answers_no_origin_and_its_preflight(self):
        """acceptance-service-no-origin: the preflight and the request are answered, version tag readable."""
        self.assert_no_origin_answered(self.page_requests(self.serve("0.0.0.0"), "null"))

    def test_page_served_directly_gets_no_cross_origin_permission(self):
        """acceptance-service-direct: no Origin, or the service's own, carries no cross-origin permission."""
        port = self.serve("0.0.0.0")
        self.assert_no_cross_origin_read(self.page_requests(port, None))
        self.assert_no_cross_origin_read(self.page_requests(port, "http://127.0.0.1:%d" % port))

    def test_other_site_gets_no_cross_origin_read(self):
        """acceptance-service-other-origin: only no origin is answered, never another site's."""
        port = self.serve("0.0.0.0")
        for origin in ("https://other.example", "null, https://other.example", "NULL"):
            self.assert_no_cross_origin_read(self.page_requests(port, origin))

    def test_private_service_answers_no_origin_as_any_other_origin(self):
        """acceptance-service-private-no-origin: a loopback service with no public base grants no origin no read."""
        self.assert_no_cross_origin_read(self.page_requests(self.serve("127.0.0.1"), "null"))


class PublicBaseNoOriginTest(NoOriginCase):
    """A loopback service with a recorded public base address answers no origin, from start and as box setup moves."""

    PAGE_PATH = "/demo/docs/demo.spec.html"

    def registry_service(self, process):
        root = self.dir / "repo"
        (root / "docs").mkdir(parents=True)
        (root / "docs" / "demo.spec.html").write_text(PAGE, encoding="utf-8")
        git(root, "init", "-q")
        git(root, "add", ".")
        git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "spec")
        self.registry = self.dir / "state" / "registry.toml"
        self.registry.parent.mkdir()
        self.row = (
            "[[resource]]\n"
            'id = "spec:demo::docs/demo.spec.html"\nslug = "demo"\nroot = "%s"\nnarrow_root = "%s/docs"\n'
            'spec = "docs/demo.spec.html"\nbase = "HEAD"\nowner = "w1:p1"\nchecker = "checker"\n'
            'cursor_name = ".cursor-owner"\n' % (root, root)
        )
        self.record(process)

    def record(self, process):
        """review-host's atomic registry write with this [process] table."""
        lines = "".join('%s = %s\n' % (key, json.dumps(value)) for key, value in process.items())
        fresh = self.registry.with_suffix(".new")
        fresh.write_text("[process]\n" + lines + "\n" + self.row, encoding="utf-8")
        os.replace(fresh, self.registry)

    @contextlib.contextmanager
    def running(self):
        """review-serve as review-host starts it: on loopback, from the registry; yields its port."""
        ready, done = threading.Event(), threading.Event()
        holder = {}
        original = serve.ReviewThreadingHTTPServer.serve_forever

        def forever(server, *args):
            holder["port"] = server.server_port
            thread = threading.Thread(target=original, args=(server,), daemon=True)
            thread.start()
            ready.set()
            done.wait(30)
            server.shutdown()
            thread.join(5)

        env = {"XDG_STATE_HOME": str(self.dir / "xdg")}
        argv = ["--registry", str(self.registry), "--bind", "127.0.0.1", "--port", "0", "--host", "127.0.0.1"]
        with patch.object(serve.ReviewThreadingHTTPServer, "serve_forever", forever), \
                patch.dict("os.environ", env), contextlib.redirect_stdout(io.StringIO()):
            main = threading.Thread(target=serve.main, args=(argv,), daemon=True)
            main.start()
            self.assertTrue(ready.wait(30), "review-serve did not start")
            try:
                yield holder["port"]
            finally:
                done.set()
                main.join(10)

    def test_recorded_public_base_answers_no_origin_from_start(self):
        self.registry_service({"pid": 1, "port": 0, "bind": "127.0.0.1", "host": "127.0.0.1",
                               "public_base": BASE, "url": BASE})
        with self.running() as port:
            self.assert_no_origin_answered(self.page_requests(port, "null"))
            self.assert_no_cross_origin_read(self.page_requests(port, "https://other.example"))
            self.assert_no_cross_origin_read(self.page_requests(port, None))

    def test_public_base_given_and_cleared_while_running(self):
        """Box setup changes the address with no restart (review-service #service-visibility)."""
        private = {"pid": 1, "port": 0, "bind": "127.0.0.1", "host": "127.0.0.1", "url": "http://127.0.0.1:0"}
        self.registry_service(private)
        with self.running() as port:
            self.assert_no_cross_origin_read(self.page_requests(port, "null"))
            self.record({**private, "public_base": "http://box.example:7160", "url": "http://box.example:7160"})
            self.assert_no_origin_answered(self.page_requests(port, "null"))
            self.record(private)
            self.assert_no_cross_origin_read(self.page_requests(port, "null"))


if __name__ == "__main__":
    unittest.main()
