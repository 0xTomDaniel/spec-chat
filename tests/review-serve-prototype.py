"""A prototype mockup: the review service serves a lane's running prototype at the lane's own prototype origin, at its
root, tells a mockup page its lane's commit and prototype origin, answers a front's ask about a host, and reads each
round's videos from the mockup's spool (mockups #prototype-route, #prototype-updated, #prototype-down, #round-video,
#accept-set; review-service #prototype-proxy, #prototype-front, #published-routes, #acceptance-prototype-origin,
#acceptance-prototype-runtime-once, #acceptance-prototype-host-ask; served-pages #acceptance-origin-safe). Fixture
worktrees, a fake prototype on loopback, and an in-process service."""

import html.parser
import http.client
import http.server
import importlib.util
import json
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_mockups_for_prototype", ROOT / "tests" / "review-serve-mockups.py")
_mockups = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mockups)
serve, git, write, SLUG, SET = _mockups.serve, _mockups.git, _mockups.write, _mockups.SLUG, _mockups.SET

# A prototype mockup (mockups #prototype-file): its route, regions, steps with their actions, and a question; no frame.
PROTOTYPE = """<!doctype html><html><head><title>Weekly report - mockup</title>
<meta name="mockup-prototype" content="/reports/weekly"></head><body>
<ul data-mockup-regions>
<li data-anchor="totals" data-select="main .totals" data-illustrates="acceptance-card">Weekly totals</li>
<li data-anchor="export" data-select="#export" data-illustrates="acceptance-button">Export button</li>
</ul>
<ol data-mockup-steps>
<li data-step="totals">Every team's total</li>
<li data-step="team-open" data-illustrates="acceptance-button">Payments opens<ul><li data-press=".team"></li></ul></li>
</ol>
<aside data-mockup-questions><p data-anchor="question-trend" data-spec-tbd data-about="totals" data-step="team-open">
Trend line? <button data-spec-choice="No">No</button></p></aside>
</body></html>
"""
PAGE = (b"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Weekly</title></head>"
        b"<body><main><p class=\"totals\">1,284</p><button id=\"export\">Export CSV</button></main></body></html>")
# Helmet's default Content-Security-Policy, as an Express product ships it, with its X-Frame-Options.
HELMET = ("default-src 'self';base-uri 'self';font-src 'self' https: data:;form-action 'self';frame-ancestors 'self';"
          "img-src 'self' data:;object-src 'none';script-src 'self';script-src-attr 'none';"
          "style-src 'self' https: 'unsafe-inline';upgrade-insecure-requests")
# A policy trusting scripts by nonce and 'strict-dynamic', which ignores any address, as Next.js ships one.
STRICT = "script-src 'nonce-abc' 'strict-dynamic'; object-src 'none'; base-uri 'none'"
# A video on the prototype's route, which the prototype itself serves in ranges at its version VIDEO_TAG, as it serves
# PAGE at PAGE_TAG.
VIDEO, VIDEO_TAG, PAGE_TAG = bytes(range(64)), '"v1"', '"p1"'


def nonce_of(body):
    """The nonce the comment layer's tag carries, the answer's own (review-service #prototype-proxy)."""
    found = re.search(rb'comment-layer\.js" data-spec-chat-prototype="[^"]*" nonce="([A-Za-z0-9_-]{16,})"', body)
    return found.group(1) if found else b"(none)"


class FakePrototype(http.server.ThreadingHTTPServer):
    """The lane's running prototype on loopback, serving at its root as a product ships: it records every request and
    sets a cookie on each answer, which the proxy must never carry either way."""

    daemon_threads = True

    def __init__(self):
        self.seen = []
        super().__init__(("127.0.0.1", 0), FakeHandler)


class FakeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def answer(self, status, body, kind, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "session=prototype; Path=/")
        self.send_header("Cache-Control", "no-store")
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        self.server.seen.append((self.command, self.path, dict(self.headers.items())))
        if self.path.startswith("/reports/weekly"):
            return self.ranged(PAGE, "text/html; charset=utf-8", PAGE_TAG)
        if self.path == "/app.js":
            return self.answer(200, b"document.title = 'app';", "text/javascript")
        if self.path == "/demo.webm":
            return self.ranged(VIDEO, "video/webm", VIDEO_TAG)
        if self.path == "/old":
            location = "http://127.0.0.1:%d/reports/weekly" % self.server.server_port
            return self.answer(302, b"", "text/plain", [("Location", location)])
        if self.path == "/secure":  # helmet's defaults
            return self.answer(200, PAGE, "text/html", [("Content-Security-Policy", HELMET),
                                                        ("X-Frame-Options", "SAMEORIGIN")])
        if self.path == "/deny":  # X-Frame-Options alone, and a policy naming no frame-ancestors
            return self.answer(200, PAGE, "text/html", [("X-Frame-Options", "DENY"),
                                                        ("Content-Security-Policy", "script-src 'self'")])
        if self.path == "/strict":  # scripts trusted by nonce and 'strict-dynamic', as Next.js ships them
            return self.answer(200, PAGE, "text/html", [("Content-Security-Policy", STRICT)])
        if self.path == "/secure.js":  # no page: its policy passes as sent
            return self.answer(200, b"", "text/javascript", [("Content-Security-Policy", HELMET),
                                                             ("X-Frame-Options", "SAMEORIGIN")])
        moved = {"/app/home": "/app", "/app/reports/old": "weekly?team=payments", "/app/far": "/elsewhere",
                 "/app/full": "http://127.0.0.1:%d/app/reports/weekly#totals" % self.server.server_port}
        if self.path in moved:  # a prototype whose address names a path, /app, redirecting
            return self.answer(302, b"", "text/plain", [("Location", moved[self.path])])
        return self.answer(404, b"not found", "text/plain")

    def ranged(self, body, kind, tag):
        """body as the prototype serves it at its version tag: one range with 206, a range asked If-Range of another
        version whole, a range past its end 416 with an HTML error page, whatever body's type, and a read naming its
        version in If-None-Match 304 with no type and helmet's headers, as express sends it."""
        size, tags = len(body), [("Accept-Ranges", "bytes"), ("ETag", tag)]
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            for name, value in tags + [("Content-Security-Policy", HELMET), ("X-Frame-Options", "SAMEORIGIN")]:
                self.send_header(name, value)
            return self.end_headers()
        span = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if not span or self.headers.get("If-Range", tag) != tag:
            return self.answer(200, body, kind, tags)
        start, end = int(span.group(1)), min(int(span.group(2) or size - 1), size - 1)
        if start >= size:
            return self.answer(416, b"<p>Range Not Satisfiable</p>", "text/html; charset=utf-8",
                               tags + [("Content-Range", "bytes */%d" % size)])
        self.answer(206, body[start:end + 1], kind, tags + [("Content-Range", "bytes %d-%d/%d" % (start, end, size))])

    do_HEAD = do_GET

    def do_POST(self):
        self.server.seen.append((self.command, self.path, dict(self.headers.items())))
        self.answer(200, b"posted", "text/plain")


class PrototypeCase(_mockups.MockupCase):
    """The lane worktree of MockupCase, its set also holding 04-weekly-report, a prototype mockup, and a row naming
    the fake prototype's address."""

    def setUp(self):
        super().setUp()
        write(self.repo / SET / "04-weekly-report.mockup.html", PROTOTYPE)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "prototype mockup")
        self.prototype = FakePrototype()
        threading.Thread(target=self.prototype.serve_forever, daemon=True).start()
        self.addCleanup(self.prototype.server_close)
        self.addCleanup(self.prototype.shutdown)
        self.address = "http://127.0.0.1:%d" % self.prototype.server_port
        self.write_registry(prototype=self.address)

    def write_registry(self, public_base=None, prototype=None, extra=""):
        super().write_registry(public_base)
        if prototype is not None:
            text = self.registry.read_text(encoding="utf-8")
            self.registry.write_text(text.replace('owner = "w1:pOwner"\n', 'owner = "w1:pOwner"\nprototype = "%s"\n' % prototype)
                                     + extra, encoding="utf-8")

    def origin_host(self, suffix="localhost"):
        """The Host a browser names at the lane's prototype origin: on loopback, <slug>-prototype.localhost:<port>."""
        return "%s-prototype.%s" % (SLUG, suffix) + (":%d" % self.server.server_port if suffix == "localhost" else "")

    def proxied(self, method, route, headers=None, suffix="localhost"):
        return self.request(method, route, headers={"Host": self.origin_host(suffix), **(headers or {})})

    def videos(self, name="04-weekly-report.mockup.html"):
        return self.repo / SET / (name + ".review") / "videos"


class PrototypeProxyTest(PrototypeCase):
    def test_the_route_is_served_at_the_lanes_prototype_origin_with_the_comment_layer_added(self):
        """review-service #acceptance-prototype-origin, #prototype-proxy, mockups #prototype-route: at the lane's
        prototype origin, <slug>-prototype.localhost for a service private on loopback, a route is the prototype's same
        route at its root, the comment layer's script added first in each HTML page's head, from the service's own
        address and naming that origin; a page that is no HTML passes unchanged, and every route there is the
        prototype's, the service's own included."""
        self.start()
        port = self.server.server_port
        status, headers, body = self.proxied("GET", "/reports/weekly?team=payments")
        self.assertEqual(status, 200)
        self.assertEqual(self.prototype.seen[-1][:2], ("GET", "/reports/weekly?team=payments"))
        tag = (b'<script defer src="http://127.0.0.1:%d/static/comment-layer.js" '
               b'data-spec-chat-prototype="http://%s-prototype.localhost:%d/" nonce="%s"></script>'
               % (port, SLUG.encode(), port, nonce_of(body)))
        self.assertTrue(body.startswith(b'<!doctype html><html lang="en"><head>' + tag + b'<meta charset'), body[:200])
        self.assertEqual(body.replace(tag, b""), PAGE)
        self.assertEqual(int(headers["content-length"]), len(body))
        self.assertEqual(headers["cache-control"], "no-store")
        status, _, body = self.proxied("GET", "/app.js")
        self.assertEqual((status, body), (200, b"document.title = 'app';"))
        self.assertEqual(self.proxied("GET", "/missing")[0], 404)
        for route in ("/", "/static/comment-layer.js", "/api/status", "/%s/docs/specs/demo.spec.html" % SLUG):
            with self.subTest(route=route):
                self.assertEqual(self.proxied("GET", route)[2], b"not found")
                self.assertEqual(self.prototype.seen[-1][1], route)
        status, _, script = self.request("GET", "/static/comment-layer.js")
        self.assertEqual(status, 200)
        self.assertIn(b"spec-chat-capabilities:", script)

    def test_the_service_tells_a_prototype_by_the_host_alone(self):
        """review-service #prototype-proxy, #published-routes: a request names a lane's prototype origin by its Host
        alone, in any case and at any port; the service's own host, another lane's or a host after another name, and
        the gone /<slug>/prototype/ path all serve the service's own pages."""
        self.start()
        seen = len(self.prototype.seen)
        port = self.server.server_port
        for host in ("127.0.0.1:%d" % port, "localhost:%d" % port, "other-lane-prototype.localhost:%d" % port,
                     "%s-prototype.example.test:%d" % (SLUG, port), "%s.localhost:%d" % (SLUG, port),
                     "x.%s-prototype.localhost:%d" % (SLUG, port)):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/reports/weekly", headers={"Host": host})[0], 404)
                self.assertEqual(self.request("GET", "/", headers={"Host": host})[0], 200)
        self.assertEqual(self.request("GET", "/%s/prototype/reports/weekly" % SLUG)[0], 404)
        self.assertEqual(len(self.prototype.seen), seen)
        for host in ("%s-PROTOTYPE.LocalHost:%d" % (SLUG.upper(), port), "%s-prototype.localhost:9000" % SLUG,
                     "%s-prototype.localhost" % SLUG):
            with self.subTest(host=host):
                self.assertEqual(self.request("GET", "/reports/weekly", headers={"Host": host})[0], 200)

    def test_it_forwards_only_reads(self):
        """review-service #prototype-proxy: GET and HEAD only; a HEAD names the length the page is served at, and a
        POST reaches no prototype."""
        self.start()
        _, got, body = self.proxied("GET", "/reports/weekly")
        status, headers, empty = self.proxied("HEAD", "/reports/weekly")
        self.assertEqual((status, empty, headers["content-length"]), (200, b"", str(len(body))))
        seen = len(self.prototype.seen)
        status, headers, _ = self.proxied("POST", "/reports/weekly")
        self.assertEqual((status, headers.get("allow")), (405, "GET, HEAD"))
        status, headers, _ = self.proxied("POST", "/api/events")
        self.assertEqual((status, headers.get("allow")), (405, "GET, HEAD"))
        self.assertEqual(len(self.prototype.seen), seen)

    def test_no_cookie_goes_either_way_and_another_origin_is_still_answered(self):
        """review-service #prototype-proxy, served-pages #acceptance-origin-safe: the reviewer's cookie and credentials
        never reach the prototype, its cookie never reaches the reviewer, so a public service still answers another
        origin, its own pages' origin included, which at a prototype origin is another."""
        base = "https://spec.203-0-113-9.sslip.io"
        self.write_registry(public_base=base, prototype=self.address)
        self.start()
        suffix = "spec.203-0-113-9.sslip.io"
        status, headers, _ = self.proxied("GET", "/reports/weekly", suffix=suffix, headers={
            "Cookie": "review=secret", "Authorization": "Basic c2VjcmV0", "Origin": "null"})
        self.assertEqual(status, 200)
        sent = {name.lower() for name in self.prototype.seen[-1][2]}
        self.assertFalse(sent & {"cookie", "authorization", "origin"}, sent)
        self.assertNotIn("set-cookie", headers)
        self.assertEqual(headers.get("access-control-allow-origin"), "null")
        self.assertNotIn("access-control-allow-credentials", headers)
        status, headers, _ = self.proxied("HEAD", "/reports/weekly", suffix=suffix, headers={"Origin": base})
        self.assertEqual((status, headers.get("access-control-allow-origin")), (200, base))
        own = "https://%s-prototype.%s" % (SLUG, suffix)
        self.assertNotIn("access-control-allow-origin",
                         self.proxied("HEAD", "/reports/weekly", suffix=suffix, headers={"Origin": own})[1])
        own_page = self.request("HEAD", "/", headers={"Host": suffix, "Origin": base})[1]
        self.assertNotIn("access-control-allow-origin", own_page)

    def test_a_video_on_the_route_is_read_in_ranges_as_the_prototype_serves_it(self):
        """review-service #published-ranges, #proof-ranges: a read's Range, when it asks one byte range, and its
        If-Range reach the prototype, and its answer passes on, its 416 too, so a video on its route seeks as the
        prototype serves it; several ranges reach it as no range, so it answers whole."""
        self.start()
        status, headers, body = self.proxied("GET", "/demo.webm", headers={"Range": "bytes=4-10"})
        self.assertEqual((status, headers.get("content-range"), body), (206, "bytes 4-10/64", VIDEO[4:11]))
        self.assertEqual((headers["accept-ranges"], headers["etag"], headers["content-length"]), ("bytes", VIDEO_TAG, "7"))
        self.assertEqual(self.prototype.seen[-1][2].get("Range"), "bytes=4-10")
        status, _, body = self.proxied("GET", "/demo.webm", headers={"Range": "bytes=4-10", "If-Range": '"old"'})
        self.assertEqual((status, body), (200, VIDEO))
        self.assertEqual(self.prototype.seen[-1][2].get("If-Range"), '"old"')
        status, _, body = self.proxied("GET", "/demo.webm", headers={"Range": "bytes=4-10", "If-Range": VIDEO_TAG})
        self.assertEqual((status, body), (206, VIDEO[4:11]))
        status, headers, _ = self.proxied("GET", "/demo.webm", headers={"Range": "bytes=999-"})
        self.assertEqual((status, headers.get("content-range")), (416, "bytes */64"))
        for several in ("bytes=0-3,10-20", "bytes=-"):
            status, headers, body = self.proxied("GET", "/demo.webm", headers={"Range": several, "If-Range": VIDEO_TAG})
            self.assertEqual((status, "content-range" in headers, body), (200, False, VIDEO), several)
            self.assertFalse({"Range", "If-Range"} & set(self.prototype.seen[-1][2]), several)

    def test_a_page_is_read_whole_never_in_ranges_of_the_prototypes_bytes(self):
        """review-service #published-ranges, #prototype-proxy: a page the service adds the comment layer to is not the
        prototype's bytes, so a range read of it, inside the prototype's page, past its end, or several ranges, of any
        version, answers the whole page as served, never a part of the prototype's page or its 416; revalidated, it
        answers 304 with no Accept-Ranges, and no Content-Security-Policy or X-Frame-Options, so the stored page never
        claims a range again and its policy, as the layer needs it, stands."""
        self.start()
        status, headers, page = self.proxied("GET", "/reports/weekly")
        self.assertEqual((status, "accept-ranges" in headers), (200, False))
        self.assertIn(b"comment-layer.js", page)
        status, headers, body = self.proxied("GET", "/reports/weekly", headers={"If-None-Match": headers["etag"]})
        self.assertEqual((status, headers.get("etag"), "accept-ranges" in headers, body), (304, PAGE_TAG, False, b""))
        self.assertEqual(("content-security-policy" in headers, "x-frame-options" in headers), (False, False))
        for asked in ("bytes=100-", "bytes=%d-" % (len(PAGE) + 5), "bytes=0-3,10-20"):
            for if_range in ({}, {"If-Range": PAGE_TAG}, {"If-Range": '"old"'}):
                for method in ("GET", "HEAD"):
                    with self.subTest(asked=asked, if_range=if_range, method=method):
                        status, headers, body = self.proxied(method, "/reports/weekly", headers=dict(if_range, Range=asked))
                        self.assertEqual((status, "content-range" in headers, "accept-ranges" in headers),
                                         (200, False, False))
                        self.assertEqual((headers["content-length"], body.replace(nonce_of(body), nonce_of(page))),
                                         (str(len(page)), page if method == "GET" else b""))

    def test_a_redirect_stays_at_the_prototype_origin(self):
        self.start()
        status, headers, _ = self.proxied("GET", "/old")
        self.assertEqual((status, headers["location"]), (302, "/reports/weekly"))

    def test_a_redirect_within_an_address_with_a_path_stays_at_the_origins_root(self):
        """review-service #prototype-proxy: a prototype whose address names a path runs at the origin's root, so a
        redirect within it, to that path itself, relative to the route asked, or absolute, names the same route at the
        origin, the path taken off; one outside it keeps its path."""
        self.write_registry(prototype=self.address + "/app")
        self.start()
        for route, location in (("/home", "/"), ("/reports/old", "/reports/weekly?team=payments"),
                                ("/full", "/reports/weekly#totals"), ("/far", "/elsewhere")):
            with self.subTest(route=route):
                status, headers, _ = self.proxied("GET", route)
                self.assertEqual((status, headers["location"]), (302, location))
                self.assertEqual(self.prototype.seen[-1][1], "/app" + route)

    def raw(self, route):
        """The status and every header, repeated ones each, of a read at the lane's prototype origin."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("GET", route, headers={"Host": self.origin_host()})
            answer = connection.getresponse()
            answer.read()
            return answer.status, [(name.lower(), value) for name, value in answer.getheaders()]
        finally:
            connection.close()

    def test_a_pages_policy_lets_the_layer_in_and_its_mockup_page_frame_it(self):
        """review-service #prototype-proxy, mockups #prototype-route: on a page the proxy adds the comment layer to,
        a product's Content-Security-Policy, helmet's defaults, lets the layer's script load from the service and its
        mockup page frame it, the service's origin by both loopback names added to script-src and frame-ancestors, its
        X-Frame-Options SAMEORIGIN dropped, and nothing else changes; X-Frame-Options alone stands as frame-ancestors
        naming the service; anything that is no page passes as sent."""
        self.start()
        port = self.server.server_port
        own = "http://127.0.0.1:%d" % port
        frames = "http://127.0.0.1:%d http://localhost:%d" % (port, port)
        status, headers = self.raw("/secure")
        self.assertEqual(status, 200)
        self.assertNotIn("x-frame-options", dict(headers))
        self.assertEqual([value for name, value in headers if name == "content-security-policy"], [HELMET.replace(
            "frame-ancestors 'self'", "frame-ancestors 'self' " + frames).replace(
            "script-src 'self'", "script-src 'self' " + own, 1).replace(";", "; ")])
        status, headers = self.raw("/deny")
        self.assertEqual(status, 200)
        self.assertNotIn("x-frame-options", dict(headers))
        self.assertEqual([value for name, value in headers if name == "content-security-policy"],
                         ["script-src 'self' " + own, "frame-ancestors " + frames])
        _, headers = self.raw("/secure.js")
        self.assertEqual([(n, v) for n, v in headers if n in ("content-security-policy", "x-frame-options")],
                         [("content-security-policy", HELMET), ("x-frame-options", "SAMEORIGIN")])

    def test_a_policy_trusting_scripts_by_nonce_lets_the_layer_in_by_a_nonce_of_the_answers_own(self):
        """review-service #prototype-proxy: under a policy that trusts scripts by nonce and 'strict-dynamic', which
        ignores the service's address, the layer's tag carries a nonce of that answer's own, which the policy's
        script-src names beside its own; each answer has its own, and nothing else changes."""
        self.start()
        nonces = []
        for _ in range(2):
            _, headers, body = self.proxied("GET", "/strict")
            nonce = nonce_of(body).decode()
            nonces.append(nonce)
            self.assertEqual(headers["content-security-policy"], STRICT.replace(
                "'strict-dynamic';", "'strict-dynamic' 'nonce-%s';" % nonce))
        self.assertNotEqual(nonces[0], nonces[1], "each answer its own nonce")
        self.assertNotIn("(none)", nonces)

    def test_the_boxs_shell_origins_may_frame_a_prototype_page(self):
        """review-service #prototype-proxy, shell #shell-setting: a shell holding a prototype mockup page is an
        ancestor of the route its page frames, so the box's shell origins, as box setup names them with no restart,
        join the service's own in frame-ancestors, in a policy's own and in one standing for X-Frame-Options."""
        self.start()
        port = self.server.server_port
        frames = "http://127.0.0.1:%d http://localhost:%d" % (port, port)
        self.registry.write_text('[process]\nshells = ["https://app.example", "https://ops.example:8443"]\n\n'
                                 + self.registry.read_text(encoding="utf-8"), encoding="utf-8")
        shells = " https://app.example https://ops.example:8443"
        _, headers = self.raw("/secure")
        policy = [value for name, value in headers if name == "content-security-policy"][0]
        self.assertIn("; frame-ancestors 'self' " + frames + shells + ";", policy)
        _, headers = self.raw("/deny")
        self.assertEqual([value for name, value in headers if name == "content-security-policy"][-1],
                         "frame-ancestors " + frames + shells)

    def test_the_layers_policy_loosens_nothing_else(self):
        """review-service #prototype-proxy: in each policy of a header, the directive governing a script element,
        script-src-elem, else script-src, else a script-src of default-src's sources, lets the layer in, in place of
        'none': by the answer's nonce where it trusts scripts by nonce, hash, or 'strict-dynamic', else by the
        service's origin; the frames' origins go to frame-ancestors; a policy with neither, or a directive named again,
        is left as it is. X-Frame-Options that forbids framing stands as frame-ancestors, unless a policy names one."""
        service, frames = "https://spec.example", ["https://spec.example"]
        layer = lambda policies, options=(): serve.layer_policies(policies, options, service, "N0nce", frames)
        for policy, served in (
                ("script-src 'self'; img-src 'self'", "script-src 'self' https://spec.example; img-src 'self'"),
                ("script-src-elem 'self'; script-src 'none'", "script-src-elem 'self' https://spec.example; script-src 'none'"),
                ("default-src 'self'", "default-src 'self'; script-src 'self' https://spec.example"),
                ("default-src 'none'; frame-ancestors 'none'",
                 "default-src 'none'; frame-ancestors https://spec.example; script-src https://spec.example"),
                ("img-src *", "img-src *"),
                ("script-src https://spec.example; script-src 'none'", "script-src https://spec.example; script-src 'none'"),
                ("script-src 'self', frame-ancestors 'self'",
                 "script-src 'self' https://spec.example, frame-ancestors 'self' https://spec.example"),
                ("script-src 'self' 'nonce-abc' 'unsafe-inline'", "script-src 'self' 'nonce-abc' 'unsafe-inline' 'nonce-N0nce'"),
                ("script-src 'SHA256-abc='", "script-src 'SHA256-abc=' 'nonce-N0nce'"),
                ("default-src 'strict-dynamic' 'nonce-abc'",
                 "default-src 'strict-dynamic' 'nonce-abc'; script-src 'strict-dynamic' 'nonce-abc' 'nonce-N0nce'"),
                ("script-src-elem 'nonce-abc'; script-src 'self'", "script-src-elem 'nonce-abc' 'nonce-N0nce'; script-src 'self'")):
            with self.subTest(policy=policy):
                self.assertEqual(layer([policy]), [served])
        self.assertEqual(layer(["script-src 'self'", "img-src *"]), ["script-src 'self' https://spec.example", "img-src *"])
        for options, policies in ((["SAMEORIGIN"], ["frame-ancestors 'self' https://spec.example"]),
                                  (["deny"], ["frame-ancestors https://spec.example"]),
                                  (["SAMEORIGIN, DENY"], ["frame-ancestors https://spec.example"]),
                                  (["SAMEORIGIN", "DENY"], ["frame-ancestors https://spec.example"]),
                                  (["ALLOW-FROM https://x.example"], [])):
            with self.subTest(options=options):
                self.assertEqual(layer([], options), policies)
        self.assertEqual(layer(["frame-ancestors 'self'"], ["DENY"]), ["frame-ancestors 'self' https://spec.example"],
                         "a policy's frame-ancestors outranks X-Frame-Options")

    def test_under_a_public_base_the_origin_is_before_its_host_and_the_layer_under_its_path(self):
        """review-service #prototype-proxy, mockups #prototype-route: with a public base address, the prototype origin
        is its scheme with <slug>-prototype. before its host name, whatever path it has; the comment layer's script
        comes from that address, its path included, so the page reaches it through the front; a redirect within the
        prototype stays at the origin's root."""
        self.write_registry(public_base="https://box.example/review/", prototype=self.address)
        self.start()
        _, _, body = self.proxied("GET", "/reports/weekly", suffix="box.example")
        tag = (b'<script defer src="https://box.example/review/static/comment-layer.js" '
               b'data-spec-chat-prototype="https://%s-prototype.box.example/" nonce="%s"></script>'
               % (SLUG.encode(), nonce_of(body)))
        self.assertTrue(body.startswith(b'<!doctype html><html lang="en"><head>' + tag), body[:200])
        self.assertEqual(self.proxied("GET", "/old", suffix="box.example")[1]["location"], "/reports/weekly")
        self.assertEqual(self.proxied("GET", "/reports/weekly")[0], 404, "a loopback name is no origin of this service")
        self.write_registry(public_base="https://spec.203-0-113-9.sslip.io:8443", prototype=self.address)
        self.server.mount_state = self.mount_state()
        self.assertIn(b'src="https://spec.203-0-113-9.sslip.io:8443/static/comment-layer.js" data-spec-chat-prototype='
                      b'"https://%s-prototype.spec.203-0-113-9.sslip.io:8443/"' % SLUG.encode(),
                      self.proxied("GET", "/reports/weekly", suffix="spec.203-0-113-9.sslip.io:8443")[2])

    def test_an_ip_address_off_loopback_has_no_prototype_origin(self):
        """review-service #prototype-proxy: a service whose address names its host by an IP address off loopback, its
        public base address's or its own, has no prototype origin: its mockup pages name none, and no host is one."""
        self.write_registry(public_base="http://203.0.113.9:8080", prototype=self.address)
        self.start()
        page = self.page("04-weekly-report.mockup.html")
        self.assertEqual(self.request("HEAD", page)[1]["x-spec-chat-prototype"], "none")
        self.assertEqual(self.request("GET", "/api/prototype-host?domain=%s-prototype.203.0.113.9" % SLUG)[0], 404)
        self.write_registry(prototype=self.address)
        self.server.mount_state = self.mount_state()
        self.server.own_host = "203.0.113.9"
        self.assertEqual(self.request("HEAD", page)[1]["x-spec-chat-prototype"], "none")
        self.server.own_host = "box.example"
        self.assertEqual(self.request("HEAD", page)[1]["x-spec-chat-prototype"],
                         "http://%s-prototype.box.example:%d/" % (SLUG, self.server.server_port))

    def test_with_no_address_or_none_answering_the_prototype_is_not_running(self):
        """mockups #prototype-down: no address on the lane's rows, or none answering, reads Prototype not running with
        the route asked, escaped, so a frame loading it reads so at once."""
        self.write_registry()
        self.start()
        status, headers, body = self.proxied("GET", "/reports/weekly")
        self.assertEqual((status, headers["x-spec-chat-prototype"]), (502, "none"))
        self.assertIn(b"Prototype not running", body)
        self.assertIn(b"<code>/reports/weekly</code>", body)
        self.prototype.shutdown()
        self.prototype.server_close()
        self.write_registry(prototype=self.address)
        self.server.mount_state = self.mount_state()
        status, headers, _ = self.proxied("HEAD", "/reports/weekly")
        self.assertEqual((status, headers["x-spec-chat-prototype"]), (502, "down"))
        status, headers, body = self.proxied("GET", "/reports/weekly?team=<b>&week=1")
        self.assertEqual((status, headers["x-spec-chat-prototype"]), (502, "down"))
        self.assertIn(b"Prototype not running", body)
        self.assertIn(b"<code>/reports/weekly?team=&lt;b&gt;&amp;week=1</code>", body)

    def test_the_address_the_lanes_rows_last_named(self):
        """review-service #prototype-proxy: from the address its rows last named, by when each was registered."""
        other = FakePrototype()
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        row = ("\n[[resource]]\n"
               f'id = "spec:demo::docs/specs/other.spec.html"\nslug = "{SLUG}"\nroot = "{self.repo}"\n'
               f'narrow_root = "{self.repo}/docs"\nspec = "docs/specs/other.spec.html"\nbase = "{self.base}"\n'
               'owner = "w1:pOwner"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n'
               f'prototype = "http://127.0.0.1:{other.server_port}"\n'
               'registered_at = "2026-10-27T00:00:00Z"\nupdated_at = "2026-10-28T00:00:00Z"\n')
        self.write_registry(prototype=self.address, extra=row)
        self.start()
        self.assertEqual(self.proxied("GET", "/reports/weekly")[0], 200)
        self.assertEqual((len(self.prototype.seen), len(other.seen)), (0, 1))
        self.assertEqual(self.request("GET", "/reports/weekly", headers={
            "Host": "other-lane-prototype.localhost:%d" % self.server.server_port})[0], 404)

    def test_a_page_running_spec_chats_runtime_loads_it_once(self):
        """review-service #acceptance-prototype-runtime-once, #prototype-proxy: a lane whose prototype is a review
        service serving a spec page: at the lane's prototype origin the page loads Spec Chat's runtime once, its own,
        which the proxy serves it, and gets no comment layer's script added."""
        lane = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        lane.mount_state = self.mount_state()
        lane.state_dir = str(self.state)
        lane.wake_controller = serve.WakeController(lane)
        threading.Thread(target=lane.serve_forever, daemon=True).start()
        self.addCleanup(lane.server_close)
        self.addCleanup(lane.shutdown)
        runtime = (ROOT / "docs" / "specs" / ".viz" / "runtime.js").read_bytes()
        write(self.specs / ".viz" / "runtime.js", runtime.decode("utf-8"))
        # A spec page as Spec Chat's own are, loading the runtime beside it.
        write(self.specs / "demo.spec.html",
              _mockups.SPEC.replace("</title>", '</title><script defer src="./.viz/runtime.js"></script>'))
        self.write_registry(prototype="http://127.0.0.1:%d" % lane.server_port)
        self.start()
        route = "/%s/docs/specs/demo.spec.html" % SLUG
        status, _, body = self.proxied("GET", route)
        self.assertEqual(status, 200)
        sources = [next(g for g in m.groups() if g is not None) for m in serve.SCRIPT_SRC_RE.finditer(body)]
        self.assertEqual([src for src in sources if serve.RUNTIME_SRC_RE.search(src)], [b"./.viz/runtime.js"])
        self.assertNotIn(b"data-spec-chat-prototype", body)
        status, _, script = self.proxied("GET", "/%s/docs/specs/.viz/runtime.js" % SLUG)
        self.assertEqual((status, script), (200, runtime))

    def test_a_page_runs_spec_chat_only_by_the_names_its_runtime_is_served_at(self):
        """review-service #prototype-proxy: a page runs Spec Chat's runtime of its own when a script loads it as
        .viz/runtime.js or static/comment-layer.js, by any address; a product's own runtime.js is none."""
        for page, runs in (
                (b'<script defer src="./.viz/runtime.js"></script>', True),
                (b"<script src='../../../.viz/runtime.js?v=2'></script>", True),
                (b'<SCRIPT defer SRC=/static/comment-layer.js data-comment-ui></SCRIPT>', True),
                (b'<script defer src="https://spec.example/review/static/comment-layer.js"></script>', True),
                (b'<script src="/assets/runtime.js"></script><script src="/viz/runtime.js"></script>', False),
                (b'<p>loads .viz/runtime.js</p><script>import("./.viz/runtime.js")</script>', False),
                (PAGE, False)):
            with self.subTest(page=page):
                self.assertIs(serve.runs_spec_chat(page), runs)


class PrototypeHostTest(PrototypeCase):
    def test_a_front_asks_the_service_about_a_host(self):
        """review-service #acceptance-prototype-host-ask, #prototype-front: a front that issues a certificate for each
        host as it is first asked asks /api/prototype-host?domain=: yes, 200, for a lane's prototype origin while a row
        names its slug, at any port; no, 404, for any other host, the service's own included, and for an ask by
        another query name."""
        self.write_registry(public_base="https://spec.203-0-113-9.sslip.io")
        self.start()
        ask = lambda host: self.request("GET", "/api/prototype-host?" + urlencode({"domain": host}))  # noqa: E731
        status, _, body = ask("%s-prototype.spec.203-0-113-9.sslip.io" % SLUG)
        self.assertEqual((status, json.loads(body)), (200, {"slug": SLUG}))
        self.assertEqual(ask("%s-prototype.spec.203-0-113-9.sslip.io:443" % SLUG)[0], 200)
        for host in ("other-lane-prototype.spec.203-0-113-9.sslip.io", "spec.203-0-113-9.sslip.io",
                     "%s-prototype.evil.example" % SLUG, "%s-prototype.localhost" % SLUG,
                     "a.%s-prototype.spec.203-0-113-9.sslip.io" % SLUG, ""):
            with self.subTest(host=host):
                self.assertEqual(ask(host)[0], 404)
        self.assertEqual(self.request("GET", "/api/prototype-host")[0], 404)
        self.assertEqual(self.request("HEAD", "/api/prototype-host?domain=%s-prototype.spec.203-0-113-9.sslip.io"
                                      % SLUG)[0], 200)
        self.assertEqual(self.request("GET", "/api/prototype-host?host=%s-prototype.spec.203-0-113-9.sslip.io"
                                      % SLUG)[0], 404)


class PrototypeMockupTest(PrototypeCase):
    def test_a_mockup_page_names_its_lanes_commit_and_prototype(self):
        """mockups #prototype-updated, #prototype-route, #prototype-down: a mockup page's answer names the commit its
        lane is at, and the lane's prototype origin as an absolute address, at the port the page was reached at, or
        none while no row of the lane names where its prototype answers."""
        self.start()
        status, headers, _ = self.request("HEAD", self.page("04-weekly-report.mockup.html"))
        self.assertEqual(status, 200)
        self.assertEqual(headers["x-spec-chat-commit"], git(self.repo, "rev-parse", "HEAD"))
        named = lambda **kw: self.request("HEAD", self.page("04-weekly-report.mockup.html"), **kw)[1][  # noqa: E731
            "x-spec-chat-prototype"]
        self.assertEqual(headers["x-spec-chat-prototype"], "http://%s-prototype.localhost:%d/" % (SLUG, self.server.server_port))
        self.assertEqual(named(headers={"Host": "localhost:9000"}), "http://%s-prototype.localhost:9000/" % SLUG)
        self.write_registry(public_base="https://spec.203-0-113-9.sslip.io", prototype=self.address)
        self.server.mount_state = self.mount_state()
        self.assertEqual(named(), "https://%s-prototype.spec.203-0-113-9.sslip.io/" % SLUG)
        self.write_registry()
        self.server.mount_state = self.mount_state()
        self.assertEqual(named(), "none")
        self.write_registry(prototype=self.address)
        self.server.mount_state = self.mount_state()
        write(self.repo / "app.txt", "next round")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "round")
        self.assertEqual(self.request("HEAD", self.page("04-weekly-report.mockup.html"))[1]["x-spec-chat-commit"],
                         git(self.repo, "rev-parse", "HEAD"))
        # A page revalidating its own answer learns the new commit though its bytes stay the same.
        status, headers, _ = self.request("HEAD", self.page("04-weekly-report.mockup.html"),
                                          headers={"If-None-Match": headers["etag"]})
        self.assertEqual((status, headers["x-spec-chat-commit"]), (304, git(self.repo, "rev-parse", "HEAD")))

    def test_a_mockup_page_is_answered_without_asking_its_prototype(self):
        """mockups #prototype-down, review-service #prototype-front: a mockup page's answer names its lane's prototype
        origin whether or not the prototype answers, and asks it nothing: the page reads down from frames whose routes
        never say hello back, a prototype that does not answer, or a front that does not answer the origin."""
        self.start()
        page = self.page("04-weekly-report.mockup.html")
        origin = "http://%s-prototype.localhost:%d/" % (SLUG, self.server.server_port)
        self.assertEqual(self.request("GET", page)[1]["x-spec-chat-prototype"], origin)
        self.assertEqual(self.request("HEAD", page)[1]["x-spec-chat-prototype"], origin)
        self.assertEqual(self.prototype.seen, [])
        self.prototype.shutdown()
        self.prototype.server_close()
        self.assertEqual(self.request("GET", page)[1]["x-spec-chat-prototype"], origin)

    def test_a_poll_asks_git_for_the_lanes_commit_only_once_its_head_moved(self):
        """mockups #prototype-updated: a page polling its lane's commit forks no Git while the lane worktree's head stays
        where it was; a commit there, or a checkout of another commit, moves it, and the next poll names it."""
        self.start()
        asked, real = [], serve._git

        def counting(root, *args, **kwargs):
            if "HEAD^{commit}" in args:
                asked.append(args)
            return real(root, *args, **kwargs)

        commit = lambda: self.request("HEAD", self.page("04-weekly-report.mockup.html"))[1]["x-spec-chat-commit"]  # noqa: E731
        with patch.object(serve, "_git", counting):
            self.assertEqual(commit(), git(self.repo, "rev-parse", "HEAD"))
            before = len(asked)
            for _ in range(3):
                self.assertEqual(commit(), git(self.repo, "rev-parse", "HEAD"))
            self.assertEqual(len(asked), before, "polls of an unmoved head ask no Git")
            write(self.repo / "app.txt", "next round")
            git(self.repo, "add", "app.txt")  # the page's spool, which its polls write, stays untracked
            git(self.repo, "commit", "-q", "-m", "round")
            self.assertEqual(commit(), git(self.repo, "rev-parse", "HEAD"))
            branch = git(self.repo, "symbolic-ref", "--short", "HEAD")
            git(self.repo, "checkout", "-q", "--detach", "HEAD~1")
            self.assertEqual(commit(), git(self.repo, "rev-parse", "HEAD"))
            git(self.repo, "pack-refs", "--all")  # back on a branch whose ref is packed alone
            git(self.repo, "checkout", "-q", branch)
            self.assertEqual(commit(), git(self.repo, "rev-parse", "HEAD"))
            self.assertEqual(len(asked), before + 3, "each move asks once")

    def round(self, commit, stamp, viewports=("desktop", "phone"), captions=True):
        folder = self.videos()
        folder.mkdir(parents=True, exist_ok=True)
        for viewport in viewports:
            (folder / f"{commit}-{viewport}.webm").write_bytes(b"\x1aE\xdf\xa3" + viewport.encode() + bytes(range(64)))
            if captions:
                (folder / f"{commit}-{viewport}.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:01.000\n1 Totals\n")
            for path in folder.glob(commit + "-*"):
                os.utime(path, ns=(stamp, stamp))

    def rounds(self, name="04-weekly-report.mockup.html"):
        status, _, body = self.request("GET", "/api/rounds?" + urlencode({"path": self.page(name)}))
        self.assertEqual(status, 200)
        return json.loads(body)["rounds"]

    def test_each_rounds_videos_newest_first(self):
        """mockups #round-video-store, #round-video-show: the mockup's spool's videos by round, newest first, each
        viewport's video with its captions beside it; a drawn mockup has none (#round-video-drawn)."""
        base, head = self.base, git(self.repo, "rev-parse", "HEAD")
        self.round(head[:7], 2_000_000_000_000_000_000, viewports=("desktop",), captions=False)
        self.round(base, 1_000_000_000_000_000_000)
        (self.videos() / "notes.txt").write_text("not a round")
        self.start()
        self.assertEqual(self.rounds(), [
            {"commit": head[:7], "desktop": {"video": f"{head[:7]}-desktop.webm", "captions": None}},
            {"commit": base, "desktop": {"video": f"{base}-desktop.webm", "captions": f"{base}-desktop.vtt"},
             "phone": {"video": f"{base}-phone.webm", "captions": f"{base}-phone.vtt"}},
        ])
        self.assertEqual(self.rounds("01-host-page.mockup.html"), [])

    def test_a_round_file_is_read_in_ranges(self):
        """mockups #round-video-store: a player seeks a round's video in ranges; captions are served as text/vtt; a
        name that is no round file is refused."""
        commit = git(self.repo, "rev-parse", "HEAD")
        self.round(commit, 1_000_000_000_000_000_000)
        video = (self.videos() / f"{commit}-desktop.webm").read_bytes()
        self.start()
        query = lambda name: "/api/rounds?" + urlencode({"path": self.page("04-weekly-report.mockup.html"), "file": name})
        status, headers, body = self.request("GET", query(f"{commit}-desktop.webm"))
        self.assertEqual((status, headers["content-type"], headers["accept-ranges"], body), (200, "video/webm", "bytes", video))
        status, headers, body = self.request("GET", query(f"{commit}-desktop.webm"), headers={"Range": "bytes=4-10"})
        self.assertEqual((status, headers["content-range"], body), (206, "bytes 4-10/%d" % len(video), video[4:11]))
        status, headers, body = self.request("GET", query(f"{commit}-desktop.webm"), headers={"Range": "bytes=-5"})
        self.assertEqual((status, body), (206, video[-5:]))
        # Its validator is the file's identity, the same whole or in a range, so a seek reads only the bytes it sends
        # and hashes nothing; a range asked If-Range of another version is answered whole.
        tag = headers["etag"]
        self.assertEqual(self.request("GET", query(f"{commit}-desktop.webm"))[1]["etag"], tag)
        self.assertEqual(self.request("GET", query(f"{commit}-desktop.webm"), headers={"If-None-Match": tag})[0], 304)
        status, _, body = self.request("GET", query(f"{commit}-desktop.webm"), headers={"Range": "bytes=4-10", "If-Range": '"old"'})
        self.assertEqual((status, body), (200, video))
        read, real = [], open

        def counted(path, *args, **kwargs):
            stream = real(path, *args, **kwargs)
            if str(path).endswith(".webm"):
                reader = stream.read
                stream.read = lambda size=-1: read.append(reader(size)) or read[-1]
            return stream
        with patch.object(serve, "open", counted, create=True):
            status, _, body = self.request("GET", query(f"{commit}-desktop.webm"), headers={"Range": "bytes=4-10", "If-Range": tag})
        self.assertEqual((status, body, b"".join(read)), (206, video[4:11], video[4:11]), "only the range is read")
        status, headers, body = self.request("GET", query(f"{commit}-desktop.webm"), headers={"Range": "bytes=999-"})
        self.assertEqual((status, headers["content-range"], headers["accept-ranges"], headers["etag"], body),
                         (416, "bytes */%d" % len(video), "bytes", tag, b""))
        status, headers, _ = self.request("GET", query(f"{commit}-phone.vtt"))
        self.assertEqual((status, headers["content-type"]), (200, "text/vtt; charset=utf-8"))
        for name in ("../../../demo.spec.html", "notes.txt", f"{commit}-tablet.webm"):
            with self.subTest(name=name):
                self.assertEqual(self.request("GET", query(name))[0], 400)

    def test_a_video_a_page_holds_is_read_in_ranges(self):
        """threads #video-rail, #root-chip-moment: a video a reviewed page holds is read in ranges as a player seeks it
        to a thread's moment, as a round's is; the page beside it is served whole."""
        video = b"\x1aE\xdf\xa3" + bytes(range(64))
        (self.repo / "docs" / "specs" / "clip.webm").write_bytes(video)
        self.start()
        path = "/%s/docs/specs/clip.webm" % SLUG
        status, headers, body = self.request("GET", path)
        self.assertEqual((status, headers["content-type"], headers["accept-ranges"], body), (200, "video/webm", "bytes", video))
        status, headers, body = self.request("GET", path, headers={"Range": "bytes=4-10"})
        self.assertEqual((status, headers["content-range"], body), (206, "bytes 4-10/%d" % len(video), video[4:11]))
        tag = headers["etag"]
        status, headers, body = self.request("GET", path, headers={"Range": "bytes=999-"})
        self.assertEqual((status, headers["content-range"], headers["accept-ranges"], headers["etag"], body),
                         (416, "bytes */%d" % len(video), "bytes", tag, b""))
        status, headers, _ = self.request("GET", self.page("01-host-page.mockup.html"), headers={"Range": "bytes=0-3"})
        self.assertEqual((status, "accept-ranges" in headers), (200, False))

    def test_a_prototype_mockups_version_is_its_rounds_commit(self):
        """mockups #accept-set: a prototype mockup's version in the hand-off is the round's commit, its newest round
        video's, else the commit its lane is at; a drawn mockup's stays its page's."""
        self.start()
        listed = lambda: {m["page"].rsplit("/", 1)[1]: m["version"] for m in json.loads(
            self.request("GET", "/api/mockups?" + urlencode({"path": SLUG + "/docs/specs/demo.spec.html"}))[2])["mockups"]}
        head = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(listed()["04-weekly-report.mockup.html"], head)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", listed()["01-host-page.mockup.html"]))
        self.round(head[:7], 1_000_000_000_000_000_000)
        write(self.repo / "spec-only.txt", "a spec-only commit records nothing")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "spec only")
        self.assertEqual(listed()["04-weekly-report.mockup.html"], head[:7])

    def test_the_set_tells_each_mockups_route(self):
        """mockups #chip-source, #screen-index-rule: the set tells a prototype mockup's route and a drawn one's none,
        and tells no route of a mockup whose file it cannot read, so its Screens index row names no kind."""
        self.start()
        told = lambda: {m["page"].rsplit("/", 1)[1]: m.get("route", "untold") for m in json.loads(
            self.request("GET", "/api/mockups?" + urlencode({"path": SLUG + "/docs/specs/demo.spec.html"}))[2])["mockups"]}
        self.assertEqual(told(), {"01-host-page.mockup.html": None, "02-thread-on-phone.mockup.html": None,
                                  "03-settings.mockup.html": None, "04-weekly-report.mockup.html": "/reports/weekly"})
        unreadable, read = str(self.repo / SET / "04-weekly-report.mockup.html"), serve._read_page
        with patch.object(serve, "_read_page", lambda path: (serve._HeadParser(), None) if path == unreadable else read(path)):
            serve._MOCKUP_PAGES.keep(())
            self.assertEqual(told()["04-weekly-report.mockup.html"], "untold")

    def test_a_reader_finds_the_route_and_steps_at_the_accepted_commit(self):
        """mockups #acceptance-reader-prototype, #boundary-readers: a tool reading the spec's mockup set at the commit
        it was accepted at finds the prototype mockup's route and its steps by name, in order, with their captions and
        the spec anchors they illustrate, from the set's folder beside the spec and the markup alone."""
        accepted = git(self.repo, "rev-parse", "HEAD")
        write(self.repo / SET / "04-weekly-report.mockup.html", PROTOTYPE.replace("/reports/weekly", "/reports/daily"))
        folder = os.path.relpath(_mockups.serve.spool.mockup_set(str(self.repo / "docs/specs/demo.spec.html")), self.repo)
        names = git(self.repo, "ls-tree", "--name-only", accepted, folder + "/").splitlines()
        found = {}
        for name in (n for n in names if n.endswith(".mockup.html")):
            reader = MockupReader()
            reader.feed(git(self.repo, "show", "%s:%s" % (accepted, name)))
            if reader.route:
                found[os.path.basename(name)] = (reader.route, reader.steps)
        self.assertEqual(found, {"04-weekly-report.mockup.html": ("/reports/weekly", [
            ("totals", "Every team's total", []), ("team-open", "Payments opens", ["acceptance-button"])])})

    def test_a_reader_finds_the_picks_route_at_the_accepted_commit(self):
        """mockups #acceptance-reader-pick, #directions-next: a tool reading a prototype mockup that holds two directions
        at the commit it was accepted at finds the agent's pick's route, the file's own, never another direction's, from
        the markup alone, though a later commit makes the other direction the screen."""
        path = self.repo / SET / "04-weekly-report.mockup.html"
        listed = ('<ul data-mockup-directions data-anchor="directions">'
                  '<li data-direction="table" data-gives-up="Trends read slower" data-pick="Totals compare at a glance">Table</li>'
                  '<li data-direction="cards" data-gives-up="Fewer teams fit" data-route="/reports/weekly-cards">Cards</li></ul>\n')
        write(path, PROTOTYPE.replace("<body>\n", "<body>\n" + listed))
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "directions")
        accepted = git(self.repo, "rev-parse", "HEAD")
        write(path, PROTOTYPE.replace("/reports/weekly", "/reports/weekly-cards"))
        git(self.repo, "commit", "-qam", "the reviewer's pick")
        reader = MockupReader()
        reader.feed(git(self.repo, "show", "%s:%s" % (accepted, path.relative_to(self.repo).as_posix())))
        self.assertEqual(reader.route, "/reports/weekly")


class MockupReader(html.parser.HTMLParser):
    """A reader of accepted mockups, as a QA check might write one: a prototype mockup's route, and its steps in order,
    each its name, its caption (its text, the list of its actions none of it), and the spec anchors it illustrates."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.route, self.steps, self.depth, self.skip, self.walkthrough = None, [], 0, 0, False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta" and values.get("name") == "mockup-prototype":
            self.route = values.get("content")
        self.walkthrough = self.walkthrough or "data-mockup-steps" in values
        if self.depth:
            self.depth += tag == "li"
            self.skip += tag in ("ul", "ol")
        elif self.walkthrough and "data-step" in values:
            self.depth = 1
            self.steps.append((values["data-step"], "", (values.get("data-illustrates") or "").split()))

    def handle_endtag(self, tag):
        if self.depth:
            self.skip -= tag in ("ul", "ol")
            self.depth -= tag == "li"
        elif tag == "ol":
            self.walkthrough = False

    def handle_data(self, data):
        if self.depth and not self.skip:
            name, caption, shows = self.steps[-1]
            self.steps[-1] = (name, " ".join((caption + data).split()), shows)


if __name__ == "__main__":
    unittest.main()
