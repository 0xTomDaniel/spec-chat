"""GET /api/evidence, and the review index's gate words and review facts, behind a fake evidence provider
(criterion-evidence spec, review-index #index-gates, review-service #providers)."""

import atexit
import contextlib
import functools
import importlib.util
import itertools
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
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
FACTS_DEADLINE = serve.REVIEW_FACTS_SECONDS  # the server's own review facts deadline, before a test lengthens it


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


def until(holds, seconds=10):
    """Whether holds() comes true within seconds, polled: a wait for a state, not a guess at how long it takes."""
    deadline = time.monotonic() + seconds
    while not holds():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


@functools.cache
def self_signed():
    """(certificate, key) files for 127.0.0.1, trusted only by the client context a test injects."""
    directory = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, directory, True)
    cert, key = os.path.join(directory, "cert.pem"), os.path.join(directory, "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                    "-keyout", key, "-out", cert], check=True, capture_output=True)
    return cert, key


class FakeEvidence:
    """Answers every GET under its address (/criteria, /review-facts) with a fixed body, after `delay` seconds; records
    each such request, and the connection it came on. The address holds a random part: on a shared box another
    process's client can reach this port, as one still asking a port its own server once held does, and its request is
    answered 404 and counted nowhere. With `wire`, sends those bytes as the whole answer, status line and headers too;
    with `drip`, sends an answer that never ends, a byte every `drip` seconds, till the asker hangs up. With
    `redirects`, the first that many asks of a chain answer a redirect to the next. With `keep`, answers HTTP/1.1 and
    keeps each connection open; with `tls`, serves HTTPS on the self-signed certificate. With `held`, an ask whose path
    holds it is answered only once `released` is set; with `open_held`, each TLS handshake waits for it too. close()
    sets it. `connections` counts the connections that carried a recorded request; hang_up() closes each."""

    def __init__(self, body=None, status=200, raw=None, delay=0, wire=None, drip=None, redirects=0, keep=False,
                 tls=False, open_held=False, held=None):
        self.requests = []
        self.carriers = set()
        self.released = threading.Event()
        self.sockets = []
        prefix = "/%s/" % os.urandom(8).hex()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1" if keep else "HTTP/1.0"

            def log_message(self, *args):
                return

            def setup(self):
                owner.sockets.append(self.request)
                if open_held:
                    owner.released.wait(30)
                if tls:
                    self.request.do_handshake()
                super().setup()

            def do_GET(self):
                if not self.path.startswith(prefix):
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        self.send_error(404)
                    return
                owner.carriers.add(self.request)
                owner.requests.append(self.path[len(prefix) - 1:])
                if held and held in self.path:
                    owner.released.wait(30)
                time.sleep(delay)
                hop = int(parse_qs(urlparse(self.path).query).get("hop", ["0"])[0])
                if hop < redirects:
                    self.send_response(302)
                    self.send_header("Location", re.sub(r"&hop=\d+\Z", "", self.path) + "&hop=%d" % (hop + 1))
                    self.send_header("Content-Length", "0")
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # a reader that stopped waiting
                        self.end_headers()
                    return
                if wire:
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        self.wfile.write(wire)
                    return
                if drip:
                    head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % (1 << 30)
                    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                        for at in itertools.count():
                            self.wfile.write(head[at:at + 1] or b" ")
                            time.sleep(drip)
                    return
                data = raw if raw is not None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):  # a reader that stopped waiting
                    self.end_headers()
                    self.wfile.write(data)

        context = None
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*self_signed())

        class Server(ThreadingHTTPServer):
            def get_request(self):
                sock, address = super().get_request()
                return (context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
                        if context else sock), address

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = "%s://127.0.0.1:%d%sev/" % ("https" if tls else "http", self.server.server_port, prefix)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def connections(self):
        return len(self.carriers)

    def hang_up(self):
        for sock in self.sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)

    def close(self):
        self.released.set()
        self.server.shutdown()
        self.server.server_close()
        self.hang_up()


def entry(**values):
    value = {"match": True, "verdict": "pass", "judgment": None, "pr": 58,
             "capturedAt": "2026-09-20T10:00:00Z", "onMain": True,
             "artifact": "/bundles/b1/artifacts/a.png", "bundle": "/bundles/b1",
             "proven": "Shows ok.", "view": "/bundles/b1#criterion=ok"}
    value.update(values)
    return value


class ProviderHarness(unittest.TestCase):
    """A review server in-process and a fake evidence provider plugged by its file; holds no test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "state"
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.tmp.cleanup()

    @contextlib.contextmanager
    def plugged(self, url):
        """The evidence provider file, written atomically for the block and deleted after."""
        providers = self.state / "providers"
        providers.mkdir(parents=True, exist_ok=True)
        (providers / "evidence.toml.tmp").write_text('url = "%s"\n' % url)
        os.replace(providers / "evidence.toml.tmp", providers / "evidence.toml")
        try:
            yield
        finally:
            (providers / "evidence.toml").unlink()

    def fake(self, **kwargs):
        service = FakeEvidence(**kwargs)
        self.addCleanup(service.close)
        return service

    def review_server(self, mounts):
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState(mounts)
        server.state_dir = str(self.state)
        self.servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % server.server_port


class EvidenceRouteTest(ProviderHarness):
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
        with self.plugged(service.url):
            body = self.evidence(url)
        base = service.url.rstrip("/")
        self.assertEqual(body, {"criteria": {
            "ok": entry(artifact=base + "/bundles/b1/artifacts/a.png", bundle=base + "/bundles/b1",
                        view=base + "/bundles/b1#criterion=ok", uncommitted=False),
            "moved": entry(match=False, judgment="material", pr=61, onMain=False,
                           artifact=base + "/bundles/b2/artifacts/m.png", bundle=base + "/bundles/b2",
                           proven="Shows old.", view=base + "/bundles/b2#criterion=moved", uncommitted=False),
        }, "levels": serve.MARK_LEVELS})
        self.assertEqual(len(service.requests), 1)
        request = urlparse(service.requests[0])
        self.assertEqual(request.path, "/ev/criteria")
        self.assertEqual(parse_qs(request.query), {
            "spec": ["project/spec-chat::" + SPEC], "commit": [head]})

    def test_uncommitted_criterion_is_marked(self):
        service = self.fake(body={"criteria": {"ok": entry(), "edited": entry()}})
        url, root, _ = self.single(page(ok="Shows ok.", edited="Shows old."))
        (root / SPEC).write_text(page(ok="Shows ok.", edited="Shows new."), encoding="utf-8")
        with self.plugged(service.url):
            body = self.evidence(url)
        self.assertFalse(body["criteria"]["ok"]["uncommitted"])
        self.assertTrue(body["criteria"]["edited"]["uncommitted"])

    def test_only_page_criteria_and_known_fields_pass_through(self):
        service = self.fake(body={"criteria": {
            "ok": entry(artifact=None, secret="x"), "intro": entry(), "gone": entry(), "bad": "nope"}})
        url, _, _ = self.single(page(ok="Shows ok.", bad="Bad."))
        with self.plugged(service.url):
            body = self.evidence(url)
        self.assertEqual(list(body["criteria"]), ["ok"])
        self.assertIsNone(body["criteria"]["ok"]["artifact"])
        self.assertNotIn("secret", body["criteria"]["ok"])

    def test_non_relative_paths_are_not_linked(self):
        service = self.fake(body={"criteria": {"ok": entry(
            artifact="javascript:alert(1)", bundle="//evil.example/bundles/b1", view="https://evil.example/")}})
        url, _, _ = self.single(page(ok="Shows ok."))
        with self.plugged(service.url):
            value = self.evidence(url)["criteria"]["ok"]
        self.assertIsNone(value["artifact"])
        self.assertIsNone(value["bundle"])
        self.assertIsNone(value["view"])

    def test_unset_down_error_and_non_json_answer_none(self):
        url, _, _ = self.single(page(ok="Shows ok."))
        self.assertEqual(self.evidence(url), {"criteria": None, "levels": serve.MARK_LEVELS})
        # Nesting past the JSON reader's recursion limit, a status line that is no status, a body cut short.
        for service in (self.fake(status=500, body={"criteria": {"ok": entry()}}),
                        self.fake(raw=b"<html>"), self.fake(body=["list"]), self.fake(raw=b"[" * 100000),
                        self.fake(wire=b"NOPE\r\n\r\n"),
                        self.fake(wire=b"HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{}")):
            with self.plugged(service.url):
                self.assertEqual(self.evidence(url), {"criteria": None, "levels": serve.MARK_LEVELS})
            self.assertEqual(len(service.requests), 1)
        with self.plugged("http://127.0.0.1:9/"):
            self.assertEqual(self.evidence(url), {"criteria": None, "levels": serve.MARK_LEVELS})

    def test_provider_plugs_and_unplugs_live(self):
        """review-service #acceptance-provider-no-restart: one server process answers before, while, and after plugged."""
        service = self.fake(body={"criteria": {"ok": entry()}})
        url, _, _ = self.single(page(ok="Shows ok."))
        with self.plugged(service.url):
            self.assertEqual(list(self.evidence(url)["criteria"]), ["ok"])
        self.assertEqual(self.evidence(url), {"criteria": None, "levels": serve.MARK_LEVELS})
        self.assertEqual(len(service.requests), 1)

    def test_environment_is_not_read(self):
        service = self.fake(body={"criteria": {"ok": entry()}})
        url, _, _ = self.single(page(ok="Shows ok."))
        former = {"SPEC_CHAT_EVIDENCE_URL": service.url, "OPENROUTER_API_KEY": "sk-env"}
        with patch.dict(os.environ, former):
            self.assertEqual(self.evidence(url), {"criteria": None, "levels": serve.MARK_LEVELS})
            self.assertEqual(serve.jev_provider(str(self.state)), "")
            self.assertFalse(serve.JevService(state_dir=self.dir / "jev",
                                              api_key=lambda: serve.jev_provider(str(self.state))).enabled)
        self.assertEqual(service.requests, [])

    def test_jev_key_is_read_at_each_use_and_only_from_a_private_file(self):
        providers = self.state / "providers"
        providers.mkdir(parents=True)
        service = serve.JevService(state_dir=self.dir / "jev", api_key=lambda: serve.jev_provider(str(self.state)))
        self.assertFalse(service.enabled)
        key = providers / "jev.toml"
        key.write_text('key = "sk-file"\n')
        key.chmod(0o644)
        self.assertEqual(serve.jev_provider(str(self.state)), "")
        key.chmod(0o600)
        self.assertEqual(serve.jev_provider(str(self.state)), "sk-file")
        self.assertTrue(service.enabled)
        # project-rules #q-fallback: the fallback model is llm_model beside the key, else Jev's default
        model = lambda: serve.jev_llm_model(str(self.state))
        seam = serve.JevService(state_dir=self.dir / "jev", api_key="k", llm_model=model).seam
        self.assertEqual(seam.llm_model(), "")
        key.write_text('key = "sk-file"\nllm_model = "openai/gpt-5"\n')
        self.assertEqual(seam.llm_model(), "openai/gpt-5")
        key.unlink()
        self.assertFalse(service.enabled)

    def test_parameters_beyond_path_are_ignored(self):
        service = self.fake(body={"criteria": {}})
        url, _, head = self.single(page(ok="Shows ok."))
        with self.plugged(service.url):
            self.assertEqual(self.evidence(url), {"criteria": {}, "levels": serve.MARK_LEVELS})
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
        with self.plugged(service.url):
            self.evidence(url, path="main/" + SPEC)
            self.evidence(url, path="ann1/" + SPEC)
        commits = [parse_qs(urlparse(request).query) for request in service.requests]
        self.assertEqual(commits, [
            {"spec": ["project/spec-chat::" + SPEC], "commit": [main_head]},
            {"spec": ["project/spec-chat::" + SPEC], "commit": [lane_head]},
        ])

    def test_bad_path_is_rejected_without_a_read(self):
        service = self.fake(body={"criteria": {}})
        url, _, _ = self.single(page(ok="Shows ok."))
        with self.plugged(service.url):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.evidence(url, path="specs/missing.spec.html")
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(service.requests, [])



def spec_page(title, body=""):
    return "<!doctype html><html><head><title>%s</title></head><body>%s</body></html>\n" % (title, body)


class IndexGatesTest(ProviderHarness):
    """review-index #index-gates, #index-gates-provider, #index-sections: each lane spec's gate word, read from the
    spec file, the spool fold, and the evidence provider's review facts (#boundary-index-gates)."""

    def setUp(self):
        super().setUp()
        # A load waits for review facts as long as the answer takes: a busy box can stall any one ask past the server's
        # own 100 ms, so a test of what the facts show does not ride on it. A test of that deadline sets it for its
        # block (on_deadline) and asserts only what no stall can change: what a load shows, which asks reach the
        # provider, and what happens before what, never how long a load took.
        patcher = patch.object(serve, "REVIEW_FACTS_SECONDS", 5)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def on_deadline():
        """The server's own review facts deadline for a block."""
        return patch.object(serve, "REVIEW_FACTS_SECONDS", FACTS_DEADLINE)

    @contextlib.contextmanager
    def facts_asks(self):
        """For a block, each review facts ask by its provider, once it begins: how far ahead its deadline lies then, and
        an event set once the ask has ended."""
        asks = {}
        read = serve.read_review_facts

        def ask(base, paths, lanes, deadline):
            asks[base] = deadline - time.monotonic(), threading.Event()
            try:
                return read(base, paths, lanes, deadline)
            finally:
                asks[base][1].set()
        with patch.object(serve, "read_review_facts", ask):
            yield asks

    def lanes(self, specs):
        """One repository; each (slug, name, html) a registry row serving docs/specs/<name>.spec.html under a lane."""
        root = self.dir / "repo"
        root.mkdir()
        git(root, "init", "-q")
        rows = []
        for slug, name, text in specs:
            spec = "docs/specs/%s.spec.html" % name
            (root / spec).parent.mkdir(parents=True, exist_ok=True)
            (root / spec).write_text(text, encoding="utf-8")
            rows.append({"id": slug + ":" + name, "slug": slug, "root": str(root), "narrow_root": str(root / "docs"),
                         "spec": spec, "spec_file": str(root / spec), "path": slug + "/" + spec, "base": "HEAD",
                         "project": "demo"})
        git(root, "add", ".")
        git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "specs")
        return self.review_server(rows), root

    def event(self, root, name, actor, stamp, **body):
        directory = root / ("docs/specs/%s.spec.html.review" % name) / actor
        directory.mkdir(parents=True, exist_ok=True)
        body = {"actor": actor, "schemaVersion": 1, "createdAt": "2026-10-01T00:00:00Z", **body}
        (directory / ("%d-%s-%s.json" % (stamp, body["event"], body["id"]))).write_text(json.dumps(body))

    def thread(self, root, name, *steps):
        """A comment, then each step: handoff, reply (the agent's), resolve."""
        self.event(root, name, "human", 100, id="c", event="comment", anchorId="x", text="c", browser="b")
        for stamp, step in enumerate(steps, 101):
            if step == "handoff":
                self.event(root, name, "human", stamp, id="h%d" % stamp, event="handoff", events=["c"], browser="b")
            elif step == "reply":
                self.event(root, name, "agent", stamp, id="r", event="reply", respondsTo="c", status="acknowledged")
            elif step == "resolve":
                self.event(root, name, "human", stamp, id="s", event="status", respondsTo="c", threadId="c",
                           status="resolved")

    def index(self, url):
        """{lane key: (part, count, proof link or None, {title: [gate word, meta words...]})} and the raw body."""
        import html as html_lib
        import re

        with urllib.request.urlopen(url + "/", timeout=10) as response:
            body = response.read().decode()
        result = {}
        for part, lanes in re.findall(r'<nav class="part" aria-label="([^"]+)"[^>]*>(.*?)</nav>', body, re.S):
            for key, lane in re.findall(r'<section class="lane" aria-label="([^"]+)"[^>]*>(.*?)</section>', lanes, re.S):
                count = re.search(r'<span class="count">([^<]*)</span>', lane).group(1)
                proof = re.search(r'<a class="proof"[^>]*>Proof</a>', lane)
                rows = {}
                for item in re.findall(r'<li class="spec[^"]*"[^>]*>(.*?)</li>', lane, re.S):
                    texts = [t.strip() for t in html_lib.unescape(re.sub(r"<[^>]+>", "\n", item)).splitlines() if t.strip()]
                    # The meta line: thread counts, then Changed, one word each here.
                    rows[texts[0]] = texts[1:2] + [word for text in texts[2:] for word in text.split(" · ")]
                result[key] = (part, count, proof and proof.group(0), rows)
        return result, body

    def test_each_gate_word_and_its_part(self):
        """#acceptance-index-gate-decision, -reply, -draft, -agent, -ready, -accepted, #acceptance-index-sections."""
        commit = "0123456789abcdef0123456789abcdef01234567"
        service = self.fake(body={"specs": {"abc7/docs/specs/accepted.spec.html": {"accepted": commit}}})
        url, root = self.lanes([
            ("abc1", "decision", spec_page("Decision", '<p data-spec-tbd>Which one?</p>')),
            ("abc2", "reply", spec_page("Reply")),
            ("abc3", "draft", spec_page("Draft")),
            ("abc4", "agent", spec_page("Agent", '<p data-spec-tbd="later">Later work.</p>')),
            ("abc5", "ready", spec_page("Ready")),
            ("abc7", "accepted", spec_page("Accepted")),
        ])
        self.thread(root, "decision", "handoff")
        self.thread(root, "reply", "handoff", "reply")
        self.thread(root, "draft")
        self.thread(root, "agent", "handoff")
        self.thread(root, "ready", "handoff", "reply", "resolve")
        with self.plugged(service.url):
            lanes, _ = self.index(url)
        self.assertEqual(lanes, {
            "ABC-1": ("Needs you", "1 needs you", None, {"Decision": ["Decision open", "1 open"]}),
            "ABC-2": ("Needs you", "1 needs you", None, {"Reply": ["Reply waiting", "1 open"]}),
            "ABC-3": ("Needs you", "1 needs you", None, {"Draft": ["Draft not sent", "1 open"]}),
            "ABC-5": ("Needs you", "1 needs you", None, {"Ready": ["Ready to accept", "1 resolved"]}),
            "ABC-4": ("In progress", "Nothing needs you", None, {"Agent": ["With the agent", "1 open"]}),
            "ABC-7": ("In progress", "Nothing needs you", None, {"Accepted": ["Accepted 0123456"]}),
        })
        # One ask per load, one path parameter per lane spec's served path and one lane parameter per lane's slug
        # (#index-gates-provider).
        self.assertEqual(len(service.requests), 1)
        asked = urlparse(service.requests[0])
        self.assertEqual(asked.path, "/ev/review-facts")
        pairs = (("abc1", "decision"), ("abc2", "reply"), ("abc3", "draft"), ("abc4", "agent"), ("abc5", "ready"),
                 ("abc7", "accepted"))
        self.assertEqual(sorted(parse_qs(asked.query)["path"]), sorted("%s/docs/specs/%s.spec.html" % pair for pair in pairs))
        self.assertEqual(sorted(parse_qs(asked.query)["lane"]), sorted(slug for slug, _ in pairs))

    def test_first_row_that_holds_wins(self):
        """#index-gates: an open decision outranks every thread; a waiting reply outranks a draft; a thread outranks
        Accepted; one card counts each spec that needs you."""
        commit = "0123456789abcdef0123456789abcdef01234567"
        service = self.fake(body={"specs": {"abc1/docs/specs/%s.spec.html" % name: {"accepted": commit}
                                            for name in ("one", "two", "three")}})
        url, root = self.lanes([
            ("abc1", "one", spec_page("One", '<p data-spec-tbd="">Open?</p>')),
            ("abc1", "two", spec_page("Two")),
            ("abc1", "three", spec_page("Three")),
        ])
        self.thread(root, "one", "handoff", "reply")
        self.thread(root, "two", "handoff", "reply")
        self.event(root, "two", "human", 200, id="d", event="comment", anchorId="x", text="d", browser="b")
        self.thread(root, "three", "handoff")
        with self.plugged(service.url):
            lanes, _ = self.index(url)
        self.assertEqual(lanes["ABC-1"], ("Needs you", "2 need you", None, {
            "One": ["Decision open", "1 open"], "Two": ["Reply waiting", "2 open"],
            "Three": ["With the agent", "1 open"]}))

    def test_accept_spec_sent_holds_until_the_spec_changes(self):
        """#acceptance-index-gate-sent: no provider; the newest hand-off is Accept spec (no `events` list) at the
        version the page showed; a later edit to the spec makes it Ready to accept again."""
        url, root = self.lanes([("abc6", "sent", spec_page("Sent"))])
        self.thread(root, "sent", "handoff", "reply", "resolve")
        with urllib.request.urlopen(url + "/abc6/docs/specs/sent.spec.html", timeout=10) as response:
            version = response.headers["ETag"].strip('"')
        self.event(root, "sent", "human", 300, id="acc", event="handoff", browser="b", version=version)
        lanes, body = self.index(url)
        self.assertEqual(lanes["ABC-6"], ("In progress", "Nothing needs you", None, {"Sent": ["Accept spec sent", "1 resolved"]}))
        self.assertNotIn("error", body.lower())
        # A hand-off that lists its drafts is not Accept spec.
        self.event(root, "sent", "human", 400, id="h4", event="handoff", events=[], browser="b", version=version)
        self.assertEqual(self.index(url)[0]["ABC-6"][3]["Sent"][0], "Ready to accept")
        self.event(root, "sent", "human", 500, id="acc2", event="handoff", browser="b", version=version)
        self.assertEqual(self.index(url)[0]["ABC-6"][3]["Sent"][0], "Accept spec sent")
        (root / "docs/specs/sent.spec.html").write_text(spec_page("Sent", "<p>Edited.</p>"), encoding="utf-8")
        self.assertEqual(self.index(url)[0]["ABC-6"], ("Needs you", "1 needs you", None, {
            "Sent": ["Ready to accept", "1 resolved", "Changed"]}))

    def test_proof_link_opens_the_bundle_page_and_carries_its_bundle(self):
        """#acceptance-index-proof, #index-entry-lane: the card links the proof review facts name, a plain link to
        `url` that the host bridge turns into step 2 with `bundle` (host-bridge #bridge-tab-pages)."""
        proof = {"bundle": "b-1", "url": "https://evidence.example/bundles/b-1"}
        service = self.fake(body={"specs": {"abc8/docs/specs/one.spec.html": {"proof": proof}}})
        url, _ = self.lanes([("abc8", "one", spec_page("One")), ("abc8", "two", spec_page("Two")),
                             ("abc9", "other", spec_page("Other"))])
        with self.plugged(service.url):
            lanes, body = self.index(url)
        self.assertEqual(lanes["ABC-8"][2], '<a class="proof" href="https://evidence.example/bundles/b-1" '
                                            'target="_blank" rel="noopener" data-evidence-bundle="b-1">Proof</a>')
        self.assertIsNone(lanes["ABC-9"][2])
        # A proof alone is no acceptance.
        self.assertEqual(lanes["ABC-8"][3], {"One": ["Ready to accept"], "Two": ["Ready to accept"]})

    def test_no_provider_shows_every_gate_it_reads_itself(self):
        """#acceptance-index-no-provider, review-service #acceptance-ci-alone: file absent, no ask, no Accepted, no
        Proof link, no error; file present, both appear, with no restart."""
        commit = "fedcba9876543210fedcba9876543210fedcba98"
        service = self.fake(body={"specs": {"abc5/docs/specs/ready.spec.html": {
            "accepted": commit, "proof": {"bundle": "b-2", "url": "http://127.0.0.1:1/bundles/b-2"}}}})
        url, root = self.lanes([("abc5", "ready", spec_page("Ready"))])
        self.thread(root, "ready", "handoff", "reply", "resolve")
        absent = ("Needs you", "1 needs you", None, {"Ready": ["Ready to accept", "1 resolved"]})
        lanes, body = self.index(url)
        self.assertEqual(lanes, {"ABC-5": absent})
        self.assertNotIn("Accepted", body)
        with self.plugged(service.url):
            lanes, _ = self.index(url)
        self.assertEqual(lanes["ABC-5"][:2], ("In progress", "Nothing needs you"))
        self.assertEqual(lanes["ABC-5"][3], {"Ready": ["Accepted fedcba9", "1 resolved"]})
        self.assertIn('data-evidence-bundle="b-2"', lanes["ABC-5"][2])
        self.assertEqual(self.index(url)[0], {"ABC-5": absent})
        self.assertEqual(len(service.requests), 1)

    def test_failing_slow_or_malformed_answers_render_at_once_without_facts(self):
        """#index-gates-provider: an error, a non-JSON or malformed answer, a down, slow, unroutable, or dripping
        provider: the index renders with neither fact. A slow, unroutable, or dripping provider never answers, so its
        load renders waiting on no answer; its ask's deadline is no later than the server's own, the dripping ask ends
        with no deadline renewed by a byte, and the slow provider is asked at most once (#index-entry-cost)."""
        good = {"accepted": "0123456789abcdef0123456789abcdef01234567",
                "proof": {"bundle": "b", "url": "https://evidence.example/bundles/b"}}
        url, _ = self.lanes([("abc5", "ready", spec_page("Ready"))])
        path = "abc5/docs/specs/ready.spec.html"
        absent = {"ABC-5": ("Needs you", "1 needs you", None, {"Ready": ["Ready to accept"]})}
        services = [self.fake(status=500, body={"specs": {path: good}}), self.fake(raw=b"<html>"),
                    self.fake(body=["list"]), self.fake(body={"specs": []}),
                    self.fake(body={"specs": {path: {"accepted": "HEAD~1", "proof": {"bundle": "b", "url": "javascript:alert(1)"}}}}),
                    self.fake(body={"specs": {path: {"accepted": 7, "proof": {"bundle": "", "url": "https://e.example/b"}}}}),
                    self.fake(body={"specs": {"other/" + path: good}})]
        for service in services:
            with self.subTest(requests=service.url), self.plugged(service.url):
                self.assertEqual(self.index(url)[0], absent)
        with self.plugged("http://127.0.0.1:9/"):
            self.assertEqual(self.index(url)[0], absent)
        slow = self.fake(body={"specs": {path: good}}, held="/review-facts")  # answers only once the test ends
        drip = self.fake(body={"specs": {path: good}}, drip=0.01)
        # A slow provider, one whose address no host answers (TEST-NET-1), and one sending a byte at a time, without
        # end: none answers while the test runs, so each load that renders waited for no answer, and its ask's deadline
        # is no later than the server's own.
        with self.facts_asks() as asks:
            for provider in (slow.url, "http://192.0.2.1/", drip.url):
                with self.subTest(provider=provider), self.plugged(provider), self.on_deadline():
                    self.assertEqual(self.index(url)[0], absent)
            self.assertTrue(until(lambda: len(asks) == 3))
        self.assertLessEqual(max(ahead for ahead, _ in asks.values()), FACTS_DEADLINE)
        self.assertLessEqual(len(slow.requests), 1)  # asked once; a busy box can stall an ask past its deadline unsent
        # The dripping ask ended by its deadline, not one read timeout after each byte: asks cannot pile up.
        self.assertTrue(asks[drip.url][1].wait(10))

    def test_a_kept_connection_carries_each_warm_load(self):
        """#index-gates-provider, #index-entry-cost: an HTTPS provider whose connection opens only after its load, as
        TCP and TLS to a real provider can outlast the facts deadline: on the server's own deadline that warm load
        renders without facts, waiting on no open; the late connection sends no request and, kept, carries each later
        load's ask as one request, facts in: no later load opens one."""
        path = "abc5/docs/specs/ready.spec.html"
        service = self.fake(body={"specs": {path: {"accepted": "0123456789abcdef0123456789abcdef01234567"}}},
                            keep=True, tls=True, open_held=True)
        origin = ("https", "127.0.0.1", service.server.server_port)
        kept = lambda: any(at == origin for at, _ in list(serve._PROVIDER_CONNECTIONS._kept))
        url, _ = self.lanes([("abc5", "ready", spec_page("Ready"))])
        self.index(url)  # warm, before any provider
        trusted = ssl.create_default_context(cafile=self_signed()[0])
        gate = lambda: self.index(url)[0]["ABC-5"][3]
        accepted = {"Ready": ["Accepted 0123456"]}
        with patch.object(serve, "_provider_tls", lambda: trusted), self.plugged(service.url):
            # The open is held: the load renders before its connection can open, so it waits for no open.
            with self.on_deadline():
                self.assertEqual(gate(), {"Ready": ["Ready to accept"]})
            service.released.set()
            # In past its load's deadline, the open is kept, and carries each later load's ask as one request.
            self.assertTrue(until(kept))
            for _ in range(5):
                self.assertEqual(gate(), accepted)
        self.assertEqual((service.connections, len(service.requests)), (1, 5))

    def test_asks_in_flight_hold_no_other_ask(self):
        """#index-gates-provider, criterion-evidence: only idle connections are kept; an ask finding none idle opens
        its own. Slow criterion evidence asks in flight, as many as the pool keeps, hold neither a review facts ask
        nor one more criterion evidence ask."""
        path = "abc5/docs/specs/ready.spec.html"
        commit = "0123456789abcdef0123456789abcdef01234567"
        facts = {"specs": {path: {"accepted": commit}}}
        service = self.fake(body=facts, keep=True, held="/criteria")
        answers = []

        def criteria():
            answers.append(serve._provider_get(service.url, "criteria", {}, time.monotonic() + 30))

        def asked():
            return sum("/criteria" in request for request in service.requests)

        asks = [threading.Thread(target=criteria, daemon=True) for _ in range(serve.PROVIDER_CONNECTIONS + 1)]
        for ask in asks[:-1]:
            ask.start()
        self.assertTrue(until(lambda: asked() == serve.PROVIDER_CONNECTIONS))
        # Each asks with time to spare: an ask held for a free connection would wait it out and answer nothing.
        self.assertEqual(serve.read_review_facts(service.url, [path], [], time.monotonic() + 10),
                         ({path: {"accepted": commit}}, {}))
        asks[-1].start()
        self.assertTrue(until(lambda: asked() == serve.PROVIDER_CONNECTIONS + 1))
        service.released.set()
        for ask in asks:
            ask.join(10)
        self.assertEqual(answers, [facts] * len(asks))

    def test_a_kept_connection_the_provider_closed_is_reopened(self):
        """#index-gates-provider: a kept connection the provider has since closed is dropped; the ask opens a new
        one, and asks again, inside the same deadline."""
        path = "abc5/docs/specs/ready.spec.html"
        service = self.fake(body={"specs": {path: {"accepted": "0123456789abcdef0123456789abcdef01234567"}}}, keep=True)
        url, _ = self.lanes([("abc5", "ready", spec_page("Ready"))])
        accepted = {"Ready": ["Accepted 0123456"]}
        with self.plugged(service.url):
            self.assertEqual(self.index(url)[0]["ABC-5"][3], accepted)
            self.assertEqual(self.index(url)[0]["ABC-5"][3], accepted)
            self.assertEqual(service.connections, 1)
            service.hang_up()
            self.assertEqual(self.index(url)[0]["ABC-5"][3], accepted)
            self.assertEqual(self.index(url)[0]["ABC-5"][3], accepted)
        self.assertEqual((service.connections, len(service.requests)), (2, 4))

    def test_redirects_are_followed_inside_one_deadline(self):
        """#index-gates-provider: a redirect to an http or https address is followed; a redirect chain shares the
        ask's one deadline, each hop no new timeout of its own, so the chain cannot outlive its load; a loop ends."""
        path = "abc5/docs/specs/ready.spec.html"
        facts = {"specs": {path: {"accepted": "0123456789abcdef0123456789abcdef01234567"}}}
        url, _ = self.lanes([("abc5", "ready", spec_page("Ready"))])
        once = self.fake(body=facts, redirects=1)
        with self.plugged(once.url):
            self.assertEqual(self.index(url)[0]["ABC-5"][3], {"Ready": ["Accepted 0123456"]})
        self.assertEqual(len(once.requests), 2)
        # Each hop answers 60 ms after it is asked: the second ends past the 100 ms deadline, so under that one deadline
        # no third hop is asked however slow the box; a hop with a timeout of its own would follow the chain on.
        chain = self.fake(body=facts, redirects=5, delay=0.06)
        with self.facts_asks() as asks, self.plugged(chain.url), self.on_deadline():
            self.assertEqual(self.index(url)[0]["ABC-5"][3], {"Ready": ["Ready to accept"]})
            self.assertTrue(until(lambda: chain.url in asks))
        self.assertTrue(asks[chain.url][1].wait(10))
        hops = {parse_qs(urlparse(asked).query).get("hop", ["0"])[0] for asked in chain.requests}
        self.assertLessEqual(hops, {"0", "1"}, chain.requests)
        loop = self.fake(body=facts, redirects=1000)
        self.assertIsNone(serve._provider_get(loop.url, "criteria", {}, time.monotonic() + 5))
        self.assertEqual(len(loop.requests), serve.PROVIDER_REDIRECTS + 1)
        # Only to another http or https address.
        away = self.fake(wire=b"HTTP/1.1 302 Found\r\nLocation: file:///etc/hostname\r\nContent-Length: 0\r\n\r\n")
        self.assertIsNone(serve._provider_get(away.url, "criteria", {}, time.monotonic() + 5))

    def test_a_spec_whose_review_state_cannot_be_read_is_a_stale_row(self):
        """#acceptance-index-stale-unreadable, #index-entry-stale: a spec whose spool, or the spool of a mockup of its
        set, cannot be read, here a link in it (review-state #trust-immutable), shows as its path with the word Stale and
        no link, and counts toward no status: nothing in its lane needs you, so the lane sits under In progress."""
        url, root = self.lanes([("abc5", "ready", spec_page("Ready"))])
        stale = {"ABC-5": ("In progress", "Nothing needs you", None, {"docs/specs/ready.spec.html": ["Stale"]})}
        ready = {"ABC-5": ("Needs you", "1 needs you", None, {"Ready": ["Ready to accept"]})}
        (root / "elsewhere.json").write_text(json.dumps({"event": "comment", "id": "x", "actor": "human"}))
        for review in (root / "docs/specs/ready.spec.html.review",
                       root / "docs/specs/mockups/ready/01-screen.mockup.html.review"):
            with self.subTest(review=review.name):
                (review / "human").mkdir(parents=True)
                if "mockup" in review.name:
                    (review.parent / review.stem).write_text("<title>Screen - mockup</title>\n")
                (review / "human" / "100-comment-x.json").symlink_to(root / "elsewhere.json")
                lanes, body = self.index(url)
                self.assertEqual(lanes, stale)
                self.assertNotIn('href="/abc5/docs/specs/ready.spec.html"', body)
                (review / "human" / "100-comment-x.json").unlink()
                self.assertEqual(self.index(url)[0], ready)  # read again once it can be

    def test_settled_and_stale_specs_show_no_gate_and_are_not_asked(self):
        """#index-gates-rule: Settled specs show no gate word; #index-entry-stale: a stale row counts toward nothing."""
        service = self.fake(body={"specs": {}})
        root = self.dir / "repo"
        root.mkdir()
        git(root, "init", "-q")
        (root / "docs/specs").mkdir(parents=True)
        (root / "docs/specs/main.spec.html").write_text(spec_page("Main", "<p data-spec-tbd>Open?</p>"))
        (root / "docs/specs/lane.spec.html").write_text(spec_page("Lane"))
        git(root, "add", ".")
        git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "specs")
        def row(slug, name, **extra):
            spec = "docs/specs/%s.spec.html" % name
            return {"id": slug + name, "slug": slug, "root": str(root), "narrow_root": str(root / "docs"), "spec": spec,
                    "spec_file": str(root / spec), "path": slug + "/" + spec, "base": "HEAD", "project": "demo", **extra}
        url = self.review_server([row("aa", "main"), row("abc1", "lane"), row("abc1", "gone", stale="spec file is missing")])
        with self.plugged(service.url):
            lanes, body = self.index(url)
        self.assertEqual(lanes, {"ABC-1": ("Needs you", "1 needs you", None, {
            "Lane": ["Ready to accept"], "docs/specs/gone.spec.html": ["Stale"]})})
        self.assertNotIn("Decision open", body)
        self.assertEqual(parse_qs(urlparse(service.requests[0]).query)["path"], ["abc1/docs/specs/lane.spec.html"])


class StageTest(ProviderHarness):
    """threads #stage-tag-source, review-index #index-gates-provider: an events read tells its page its lanes' stages
    in X-Spec-Chat-Stages, from the one review facts ask, its lane parameters naming each lane asked."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(serve, "REVIEW_FACTS_SECONDS", 5)  # as IndexGatesTest: no stall decides what shows
        patcher.start()
        self.addCleanup(patcher.stop)
        root = self.dir / "repo"
        (root / SPEC).parent.mkdir(parents=True)
        (root / SPEC).write_text(spec_page("Demo"), encoding="utf-8")

        def row(slug):
            return {"id": slug + ":demo", "slug": slug, "root": str(root), "narrow_root": str(root / "docs"),
                    "spec": SPEC, "spec_file": str(root / SPEC), "path": slug + "/" + SPEC, "base": "HEAD",
                    "project": "demo"}
        # abc1 holds a lane's spec; main holds a spec under a slug that is no lane key; evidence and abc9 hold none.
        self.url = self.review_server([row("abc1"), row("main"), {"id": "scope:evidence", "slug": "evidence",
                                       "scope_row": True}, {"id": "scope:abc9", "slug": "abc9", "scope_row": True}])
        self.servers[-1].wake_controller = type("Wake", (), {"status": lambda self, target: None})()

    def stages(self, path):
        """The X-Spec-Chat-Stages an events read answers, decoded, or None."""
        with urllib.request.urlopen(self.url + path, timeout=10) as response:
            json.loads(response.read())
            header = response.headers["X-Spec-Chat-Stages"]
        return None if header is None else json.loads(urllib.parse.unquote(header))

    def test_a_spec_page_read_names_its_lane_and_its_stage(self):
        """#acceptance-stage-tag's seam: the review's slug is its lane when it holds a lane's specs; the read asks that
        lane alone, no path, and tells its stage. A lane the answer leaves out and no provider tell no stage, still the
        lane, so a comment records it; a slug that is no lane tells nothing."""
        service = self.fake(body={"lanes": {"abc1": {"stage": " Spec\n review "}, "main": {"stage": "QA"}}})
        spool = "/api/events?dir=" + urllib.parse.quote("abc1/" + SPEC + ".review")
        self.assertEqual(self.stages(spool), {"lane": "abc1", "stages": {}})
        with self.plugged(service.url):
            self.assertEqual(self.stages(spool), {"lane": "abc1", "stages": {"abc1": "Spec review"}})
            self.assertIsNone(self.stages("/api/events?dir=" + urllib.parse.quote("main/" + SPEC + ".review")))
        self.assertEqual(len(service.requests), 1)
        asked = parse_qs(urlparse(service.requests[0]).query)
        self.assertEqual(asked, {"lane": ["abc1"]})
        quiet = self.fake(body={"lanes": {"abc2": {"stage": "QA"}, "abc1": {"stage": ""}}})
        with self.plugged(quiet.url):
            self.assertEqual(self.stages(spool), {"lane": "abc1", "stages": {}})

    def test_a_host_page_read_names_its_anchors_lanes_and_its_scopes(self):
        """#acceptance-stage-lane, -none seams: a scope read asks each lane its items' anchors name, and the scope's own
        lane when it holds a lane's specs; a scope that holds none has no lane of its own. Two reads of the same lanes
        share one ask."""
        service = self.fake(body={"lanes": {"abc1": {"stage": "Code review"}, "abc7": {"stage": "QA"},
                                            "abc9": {"stage": "Done"}}})
        with self.plugged(service.url):
            self.assertEqual(self.stages("/api/events?scope=evidence&lane=abc7&lane=abc7&lane=..%2Fx&lane=abc1"),
                             {"lane": None, "stages": {"abc7": "QA", "abc1": "Code review"}})
            self.assertEqual(self.stages("/api/events?scope=abc1&lane=abc7"),
                             {"lane": "abc1", "stages": {"abc7": "QA", "abc1": "Code review"}})
            # A scope row under a lane key holds no spec, so it is no lane of its own.
            self.assertEqual(self.stages("/api/events?scope=abc9"), None)
            self.assertIsNone(self.stages("/api/events?scope=evidence"))
        self.assertEqual([parse_qs(urlparse(request).query) for request in service.requests], [{"lane": ["abc7", "abc1"]}])

    def test_a_read_asks_the_lanes_its_threads_recorded(self):
        """#stage-tag-source: a page asks the stage of each lane its threads' comments recorded, beside its own, so a
        reply on a thread about another lane's work carries that lane's stage; a spec page's read as a host page's."""
        service = self.fake(body={"lanes": {"abc1": {"stage": "Code review"}, "abc7": {"stage": "QA"}}})
        spool = "/api/events?dir=" + urllib.parse.quote("abc1/" + SPEC + ".review")
        with self.plugged(service.url):
            self.assertEqual(self.stages(spool + "&lane=abc7&lane=..%2Fx"),
                             {"lane": "abc1", "stages": {"abc7": "QA", "abc1": "Code review"}})
        self.assertEqual(parse_qs(urlparse(service.requests[0]).query), {"lane": ["abc7", "abc1"]})

    def test_a_slow_provider_still_tells_the_lane_and_its_late_answer_serves_the_next_reads(self):
        """#stage-tag-source: past its read's deadline the answer tells the read's lane with no stage, so a comment still
        records its lane; the one ask's answer, come late, tells the stage to the next reads of those lanes, every page's,
        until STAGES_SECONDS from the ask, and a read after that asks again."""
        service = self.fake(body={"lanes": {"abc1": {"stage": "QA"}}}, delay=0.5)
        spool = "/api/events?dir=" + urllib.parse.quote("abc1/" + SPEC + ".review")
        with self.plugged(service.url), patch.object(serve, "REVIEW_FACTS_SECONDS", 0.05), \
                patch.object(serve, "STAGES_SECONDS", 2):
            asked = time.monotonic()
            self.assertEqual(self.stages(spool), {"lane": "abc1", "stages": {}})
            time.sleep(0.8)  # the answer comes, after its read's deadline
            self.assertEqual(self.stages(spool), {"lane": "abc1", "stages": {"abc1": "QA"}})
            self.assertEqual(self.stages("/api/events?scope=abc1"), {"lane": "abc1", "stages": {"abc1": "QA"}})
            self.assertEqual(len(service.requests), 1, "one ask serves every read of the lane")
            time.sleep(max(0.0, asked + 2.3 - time.monotonic()))
            self.stages(spool)
            self.assertEqual(len(service.requests), 2, "a read past STAGES_SECONDS asks again")

    def test_the_agents_reply_takes_its_threads_lane(self):
        """#acceptance-stage-reply-lane's seam, review-state #model-fields-stage: in a scope that holds no spec, so has
        no lane of its own, the agent's reply to a thread whose comment recorded a lane carries that lane's stage, as
        emit-reply.sh asks it of its session's service; a thread whose comment recorded none has none."""
        service = self.fake(body={"lanes": {"abc7": {"stage": "QA"}}})
        xdg = self.dir / "xdg"
        session = xdg / "spec-chat" / "hosting" / "default"
        hosted = session / "comments" / "evidence"
        (hosted / "human").mkdir(parents=True)
        (session / "registry.toml").write_text(
            '[process]\npid = %d\nport = %d\nhost = "127.0.0.1"\n\n[[resource]]\nid = "scope:evidence"\n'
            'slug = "evidence"\nowner = "o"\nchecker = "c"\ncursor_name = ".cursor-owner"\n'
            % (os.getpid(), urlparse(self.url).port), encoding="utf-8")
        for name, body in (("1-comment-c1.json", {"lane": "abc7"}), ("2-reply-u2.json", {"threadId": "c1", "respondsTo": "c1"}),
                           ("3-comment-c3.json", {})):
            event, ident = name[:-5].split("-")[1:]
            (hosted / "human" / name).write_text(json.dumps({"id": ident, "event": event, "subject": "qa-run:9",
                                                             "text": "Why?", **body}), encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key != "HERDR_SESSION"}

        def reply(to):
            out = subprocess.run(["sh", str(ROOT / "skill/review-spec/scripts/emit-reply.sh"), str(hosted), to, "",
                                  "null", "acknowledged", "no spec change", "Done."], text=True, capture_output=True,
                                 check=True, env={**env, "XDG_STATE_HOME": str(xdg)}).stdout
            return json.loads(Path(re.search(r"-> (\S+\.json)", out).group(1)).read_text())

        with self.plugged(service.url):
            self.assertEqual(reply("c1").get("stage"), "QA")
            self.assertEqual(reply("u2").get("stage"), "QA", "a reply to a reply has its thread's lane")
            self.assertNotIn("stage", reply("c3"))
        self.assertEqual([parse_qs(urlparse(request).query) for request in service.requests], [{"lane": ["abc7"]}],
                         "both replies' reads of the lane share one ask")

    def test_the_agents_reply_carries_the_stage_its_sessions_service_tells(self):
        """#acceptance-stage-reply's seam, review-state #model-fields-stage: emit-reply.sh asks its session's review
        service, as `review-host stage` names it, for the lane of the spool's threads, and stamps the stage it tells;
        with no stage told, or the service down, the reply carries none and is still written."""
        service = self.fake(body={"lanes": {"abc1": {"stage": "Code review"}}})
        xdg = self.dir / "xdg"
        session = xdg / "spec-chat" / "hosting" / "default"
        session.mkdir(parents=True)
        root = self.dir / "repo"
        port = urlparse(self.url).port

        def registry(listening):
            (session / "registry.toml").write_text(
                '[process]\npid = %d\nport = %d\nhost = "127.0.0.1"\n\n[[resource]]\nid = "abc1:demo"\nslug = "abc1"\n'
                'root = "%s"\nnarrow_root = "%s"\nspec = "%s"\nbase = "HEAD"\nowner = "o"\nchecker = "c"\n'
                'cursor_name = ".cursor-owner"\n' % (os.getpid(), listening, root, root / "docs", SPEC), encoding="utf-8")

        review = Path(str(root / SPEC) + ".review")
        env = {key: value for key, value in os.environ.items() if key != "HERDR_SESSION"}

        def reply():
            out = subprocess.run(["sh", str(ROOT / "skill/review-spec/scripts/emit-reply.sh"), str(review), "c1", "x",
                                  "null", "acknowledged", "no spec change", "Done."], text=True, capture_output=True,
                                 check=True, env={**env, "XDG_STATE_HOME": str(xdg)}).stdout
            return json.loads(Path(re.search(r"-> (\S+\.json)", out).group(1)).read_text())

        registry(port)
        with self.plugged(service.url):
            self.assertEqual(reply()["stage"], "Code review")
        self.assertNotIn("stage", reply())
        registry(9)  # nothing listens: the service is down
        with self.plugged(service.url):
            body = reply()
        self.assertNotIn("stage", body)
        self.assertEqual((body["event"], body["text"]), ("reply", "Done."))

    def test_review_facts_read_each_asked_lanes_stage(self):
        """#index-gates-provider: per asked lane its stage, in words as given on one line; a malformed or unasked lane
        has none, and a failed answer has neither facts nor stages."""
        service = self.fake(body={"specs": {}, "lanes": {"a1": {"stage": "Merge ask"}, "a2": {"stage": 7}, "a3": "QA",
                                                          "a4": {}, "a5": {"stage": "Done"}}})
        self.assertEqual(serve.read_review_facts(service.url, [], ["a1", "a2", "a3", "a4", "a6"], time.monotonic() + 10),
                         ({}, {"a1": "Merge ask"}))
        self.assertEqual(serve.read_review_facts(self.fake(status=500).url, [], ["a1"], time.monotonic() + 10), ({}, {}))


if __name__ == "__main__":
    unittest.main()
