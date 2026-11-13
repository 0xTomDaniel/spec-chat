"""A registered spec's mockup set is served and reviewed under the spec's own row (mockups #review-same,
#review-spool, #before-base, #chip-source, #acceptance-wake; review-service #published-routes, #store-spool-events,
#store-spool-versions; served-pages #acceptance-origin-safe). Fixture worktrees, an in-process service, and a fake
wake provider."""

import contextlib
import hashlib
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
import unittest.mock
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
_serve_spec = importlib.util.spec_from_file_location("review_serve_mockups_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)
_wake_spec = importlib.util.spec_from_file_location("review_host_wake_for_mockups", ROOT / "tests" / "review-host-wake.py")
_wake_tests = importlib.util.module_from_spec(_wake_spec)
_wake_spec.loader.exec_module(_wake_tests)
FakeWaker = _wake_tests.FakeWaker

SPEC = """<!doctype html><html><head><title>Demo</title></head><body><article class="spec">
<p data-anchor="acceptance-card">A card shows the reader's name.</p>
<p data-anchor="acceptance-button">A button saves it.</p>
<p data-anchor="plain">Nothing illustrates this.</p>
</article></body></html>
"""
HOST_BEFORE = """<!doctype html><html><head><title>Host page - mockup</title></head><body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-card"><p>Old card</p></section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-card"><p>Old card</p></section>
</body></html>
"""
HOST = """<!doctype html><html><head><title>Host page - mockup</title></head><body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-card">
<div data-anchor="desktop-card" data-illustrates="acceptance-card"><img src="avatar.png" alt="Ada"><p>Ada Lovelace</p></div>
<button data-anchor="desktop-save" data-illustrates="acceptance-button">Save</button>
</section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-card">
<div data-anchor="phone-card" data-illustrates="acceptance-card acceptance-button"><p>Ada Lovelace</p></div>
</section>
</body></html>
"""
THREAD = """<!doctype html><html><head><title>Thread on phone - mockup</title></head><body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="acceptance-button"></section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="acceptance-button"></section>
</body></html>
"""
SAME = """<!doctype html><html><head><title>Settings - mockup</title></head><body>
<section data-mockup-frame="desktop" data-anchor="desktop" data-illustrates="plain"></section>
<section data-mockup-frame="phone" data-anchor="phone" data-illustrates="plain"></section>
</body></html>
"""
# A walkthrough and questions (mockups #markup-steps, #markup-questions): the second step illustrates acceptance-button;
# one question open at it, one marked later.
WALKTHROUGH = """<ol data-mockup-steps>
<li data-step="name-typed">The reader types a name</li>
<li data-step="saved" data-step-by="desktop-save" data-illustrates="acceptance-button plain">The name is saved</li>
</ol>
<aside data-mockup-questions>
<p data-anchor="question-label" data-spec-tbd data-about="desktop-save" data-step="saved">Save or Save name?
<button data-spec-choice="Save">Save</button></p>
<p data-anchor="question-later" data-spec-tbd="later" data-about="desktop-card" data-step="name-typed">Show a photo?</p>
</aside>
"""
ASKING = HOST.replace("</body>", WALKTHROUGH + "</body>")
SLUG = "qa900"
SET = "docs/specs/mockups/demo"


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True, stderr=subprocess.DEVNULL).strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class MockupCase(unittest.TestCase):
    """One lane worktree whose spec demo has a set of three screens: 01-host-page changed since the row's base,
    02-thread-on-phone new since it, 03-settings unchanged; and spec other, unregistered, with a set of its own."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-mockups-")
        self.addCleanup(temp.cleanup)
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))  # wake log lines
        self.work = Path(temp.name)
        self.repo = self.work / "repo"
        self.specs = self.repo / "docs" / "specs"
        write(self.specs / "demo.spec.html", SPEC)
        write(self.specs / "other.spec.html", SPEC)
        write(self.repo / SET / "01-host-page.mockup.html", HOST_BEFORE)
        write(self.repo / SET / "03-settings.mockup.html", SAME)
        write(self.specs / "mockups/other/01-elsewhere.mockup.html", THREAD)
        git(self.work, "init", "-q", "-b", "main", str(self.repo))
        git(self.repo, "config", "user.name", "Spec Chat Test")
        git(self.repo, "config", "user.email", "spec-chat@example.test")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "-q", "-b", "lane")
        write(self.repo / SET / "01-host-page.mockup.html", HOST)
        write(self.repo / SET / "02-thread-on-phone.mockup.html", THREAD)
        (self.repo / SET / "avatar.png").write_bytes(b"\x89PNG fixture")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "lane")
        self.state = self.work / "state"
        self.state.mkdir()
        self.registry = self.state / "registry.toml"
        self.write_registry()

    def write_registry(self, public_base=None):
        process = '[process]\npublic_base = "%s"\n\n' % public_base if public_base else ""
        self.registry.write_text(process + (
            "[[resource]]\n"
            f'id = "spec:demo::docs/specs/demo.spec.html"\nslug = "{SLUG}"\nroot = "{self.repo}"\n'
            f'narrow_root = "{self.repo}/docs"\nspec = "docs/specs/demo.spec.html"\nbase = "{self.base}"\n'
            'owner = "w1:pOwner"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n'
            'registered_at = "2026-10-27T00:00:00Z"\n'
        ), encoding="utf-8")

    def mount_state(self):
        document = serve._registry_document(self.registry)
        return serve.MountState(serve._resource_records(document, check_refs=True), self.registry,
                                serve._public_base(document))

    def start(self):
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = self.mount_state()
        server.state_dir = str(self.state)
        accepted = []
        server.jev = types.SimpleNamespace(accepted=lambda *args: accepted.append(args), queue_counts=lambda: (0, 0))
        server.accepted = accepted
        server.wake_controller = serve.WakeController(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        return server

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, {name.lower(): value for name, value in response.getheaders()}, data
        finally:
            connection.close()

    @staticmethod
    def page(name, spec_set=SET):
        return "/%s/%s/%s" % (SLUG, spec_set, name)

    def events(self, name, spec_set=SET):
        return "/api/events?" + urlencode({"dir": self.page(name, spec_set) + ".review"})

    def post(self, name, event, file_name):
        query = urlencode({"dir": self.page(name) + ".review", "actor": "human", "name": file_name})
        return self.request("POST", "/api/events?" + query, event)

    def comment(self, name, comment_id="c1", stamp="1000"):
        event = {"event": "comment", "id": comment_id, "anchorId": "desktop-save", "text": "Make it primary."}
        return self.post(name, event, "%s-comment-%s.json" % (stamp, comment_id))

    def hand_off(self, name, listed=("c1",), handoff_id="h1", stamp="1001"):
        event = {"event": "handoff", "id": handoff_id}
        if listed is not None:
            event["events"] = list(listed)
        return self.post(name, event, "%s-handoff-%s.json" % (stamp, handoff_id))

    def spool(self, name):
        return Path(str(self.repo / SET / name) + ".review")


class MockupSpoolTest(MockupCase):
    def test_a_hand_off_on_a_mockup_page_lands_in_the_spool_beside_that_mockup(self):
        """mockups #proof first failing test, #review-spool, review-service #store-spool-events: the mockup's review
        state is <mockup>.review/ beside it, of the spec spool's shape; the spec's own spool is untouched."""
        self.start()
        self.assertEqual(self.comment("01-host-page.mockup.html")[0], 200)
        self.assertEqual(self.hand_off("01-host-page.mockup.html")[0], 200)
        human = self.spool("01-host-page.mockup.html") / "human"
        self.assertEqual(sorted(path.name for path in human.iterdir()),
                         ["1000-comment-c1.json", "1001-handoff-h1.json"])
        self.assertFalse(Path(str(self.specs / "demo.spec.html") + ".review").exists())
        status, _, body = self.request("GET", self.events("01-host-page.mockup.html"))
        self.assertEqual(status, 200)
        listed = json.loads(body)
        self.assertEqual([event["name"] for event in listed], ["1000-comment-c1.json", "1001-handoff-h1.json"])
        self.assertTrue(all(event["actor"] == "human" and "place" in event for event in listed))

    def test_a_mockup_hand_off_never_becomes_accept_spec(self):
        """mockups #review: a mockup page's Hand off never becomes Accept spec, so its spool takes no hand-off without
        its list of events, the one Accept spec writes; the spec spool still takes Accept spec."""
        server = self.start()
        self.comment("01-host-page.mockup.html")
        status, _, body = self.hand_off("01-host-page.mockup.html", listed=None)
        self.assertEqual(status, 400, body)
        self.assertEqual(sorted(path.name for path in (self.spool("01-host-page.mockup.html") / "human").iterdir()),
                         ["1000-comment-c1.json"])
        self.assertEqual(server.accepted, [])
        spec_dir = "/%s/docs/specs/demo.spec.html.review" % SLUG
        query = urlencode({"dir": spec_dir, "actor": "human", "name": "2000-handoff-a1.json"})
        self.assertEqual(self.request("POST", "/api/events?" + query, {"event": "handoff", "id": "a1"})[0], 200)
        self.assertEqual(len(server.accepted), 1)

    def test_agent_events_stay_refused_on_a_mockup_spool(self):
        """served-pages #served-safe: agent events stay refused over HTTP, on a mockup's spool as on a spec's."""
        self.start()
        query = urlencode({"dir": self.page("01-host-page.mockup.html") + ".review", "actor": "agent",
                           "name": "1000-reply-r1.json"})
        self.assertEqual(self.request("POST", "/api/events?" + query, {"event": "reply", "id": "r1"})[0], 403)

    def test_a_row_reviews_only_its_own_specs_set(self):
        """#review-same: the set is the row's spec's; another spec's mockup, its spool, and a page outside a set's
        folder are no mockup of this row."""
        self.start()
        other = "docs/specs/mockups/other"
        self.assertEqual(self.request("GET", self.page("01-elsewhere.mockup.html", other))[0], 404)
        self.assertEqual(self.request("GET", self.events("01-elsewhere.mockup.html", other))[0], 400)
        self.assertEqual(self.request("GET", self.events("missing.mockup.html"))[0], 400)
        query = urlencode({"dir": self.page("01-elsewhere.mockup.html", other) + ".review", "actor": "human",
                           "name": "1000-comment-c1.json"})
        self.assertEqual(self.request("POST", "/api/events?" + query, {"event": "comment", "id": "c1"})[0], 400)
        self.assertFalse(Path(str(self.specs / "mockups/other/01-elsewhere.mockup.html") + ".review").exists())

    def test_a_mark_on_a_mockup_follows_an_agent_edit(self):
        """#review (marks that follow agent edits): the mockup's events are placed against the version kept in its
        spool, so an element the agent inserts above a marked button leaves the mark on that button."""
        self.start()
        _, headers, _ = self.request("GET", self.page("01-host-page.mockup.html"))
        version = headers["etag"].strip('"')
        event = {"event": "comment", "id": "c1", "anchorId": "desktop-save", "version": version, "text": "Primary."}
        self.assertEqual(self.post("01-host-page.mockup.html", event, "1000-comment-c1.json")[0], 200)
        path = self.repo / SET / "01-host-page.mockup.html"
        write(path, path.read_text(encoding="utf-8").replace(
            '<button data-anchor="desktop-save"', '<p data-anchor="desktop-note">Saved names show here.</p>\n'
                                                  '<button data-anchor="desktop-save"'))
        _, _, body = self.request("GET", self.events("01-host-page.mockup.html"))
        place = json.loads(body)[0]["place"]
        self.assertEqual((place["anchorId"], place["state"]), ("desktop-save", "kept"))


class SingleCollectionTest(MockupCase):
    """A single-root collection, served with no registry, reviews a spec's set as a row does."""

    def test_a_single_collection_serves_and_reviews_a_specs_set(self):
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState([serve._single_mount(self.repo / "docs")])
        server.state_dir = None
        server.wake_controller = types.SimpleNamespace(status=lambda _resource: None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        page = "/specs/mockups/demo/01-host-page.mockup.html"
        status, headers, _ = self.request("GET", page)
        self.assertEqual(status, 200)
        kept = self.spool("01-host-page.mockup.html") / "versions" / (headers["etag"].strip('"') + ".html")
        self.assertTrue(kept.is_file())
        query = urlencode({"dir": page + ".review", "actor": "human", "name": "1000-handoff-h1.json"})
        self.assertEqual(self.request("POST", "/api/events?" + query, {"event": "handoff", "id": "h1"})[0], 400)
        status, _, body = self.request("GET", "/api/mockups?" + urlencode({"path": "specs/demo.spec.html"}))
        self.assertEqual([item["page"] for item in json.loads(body)["mockups"]][:1],
                         ["mockups/demo/01-host-page.mockup.html"])
        status, _, body = self.request("GET", "/api/baseline?" + urlencode({"path": page.lstrip("/"), "base": self.base}))
        self.assertEqual((status, json.loads(body)["html"]), (200, HOST_BEFORE))


class MockupPageTest(MockupCase):
    def test_a_mockup_page_is_served_at_the_specs_folder_plus_its_path_in_the_set(self):
        """#review-same, review-service #published-routes, #store-spool-versions: the page sits under the row's
        served folder; the version it is shown, its ETag, is kept in its own spool's versions/; its images too."""
        self.start()
        status, headers, body = self.request("GET", self.page("01-host-page.mockup.html"))
        self.assertEqual(status, 200)
        self.assertIn(b"Ada Lovelace", body)
        self.assertIn(b'<meta property="og:title" content="Host page - mockup">', body)
        version = hashlib.sha256(body).hexdigest()
        self.assertEqual(headers["etag"], '"%s"' % version)
        kept = self.spool("01-host-page.mockup.html") / "versions" / (version + ".html")
        self.assertEqual(kept.read_bytes(), body)
        status, headers, _ = self.request("HEAD", self.page("01-host-page.mockup.html"))
        self.assertEqual((status, headers["etag"]), (200, '"%s"' % version))
        self.assertEqual(self.request("GET", self.page("avatar.png"))[:1], (200,))

    def test_a_mockup_page_compares_against_its_spec_rows_base(self):
        """#before-base: with no base in its address a mockup page compares with its spec row's base, else the base
        its address names; a mockup absent at the base has no text there."""
        self.start()

        def baseline(name, **params):
            status, _, body = self.request("GET", "/api/baseline?" + urlencode(
                {"path": self.page(name).lstrip("/"), **params}))
            self.assertEqual(status, 200, body)
            return json.loads(body)

        changed = baseline("01-host-page.mockup.html")
        self.assertEqual((changed["base"], changed["htmlBase"], changed["html"]), (self.base, self.base, HOST_BEFORE))
        self.assertEqual(baseline("02-thread-on-phone.mockup.html")["html"], None)
        self.assertEqual(baseline("03-settings.mockup.html")["html"], SAME)
        lane = git(self.repo, "rev-parse", "HEAD")
        named = baseline("01-host-page.mockup.html", base=lane)
        self.assertEqual((named["base"], named["html"]), (lane, HOST))

    def test_spec_page_controls_stay_off_a_mockup_page(self):
        """#review: a mockup page shows no Jev or evidence control, so their routes take no mockup page."""
        self.start()
        path = self.page("01-host-page.mockup.html").lstrip("/")
        for route in ("jev", "evidence"):
            with self.subTest(route=route):
                self.assertEqual(self.request("GET", "/api/%s?%s" % (route, urlencode({"path": path, "base": "main"})))[0],
                                 400)


class MockupSetRouteTest(MockupCase):
    def set_of(self, spec="docs/specs/demo.spec.html", headers=None):
        status, answered, body = self.request("GET", "/api/mockups?" + urlencode({"path": "%s/%s" % (SLUG, spec)}),
                                              headers=headers)
        return status, answered, json.loads(body)

    def test_the_service_tells_a_spec_page_its_set(self):
        """#chip-source: each mockup's page, screen name, version, the anchors its parts illustrate, and its open
        threads, in screen order."""
        self.start()
        self.comment("01-host-page.mockup.html")
        status, _, answer = self.set_of()
        self.assertEqual(status, 200)
        mockups = answer["mockups"]
        self.assertEqual([item["page"] for item in mockups], [
            "mockups/demo/01-host-page.mockup.html", "mockups/demo/02-thread-on-phone.mockup.html",
            "mockups/demo/03-settings.mockup.html"])
        self.assertEqual([item["screen"] for item in mockups], ["Host page", "Thread on phone", "Settings"])
        self.assertEqual([item["open"] for item in mockups], [1, 0, 0])
        self.assertEqual(mockups[0]["illustrates"], {
            "acceptance-card": ["desktop", "desktop-card", "phone", "phone-card"],
            "acceptance-button": ["desktop-save", "phone-card"]})
        for item in mockups:
            with self.subTest(page=item["page"]):
                _, headers, _ = self.request("GET", "/%s/docs/specs/%s" % (SLUG, item["page"]))
                self.assertEqual(headers["etag"], '"%s"' % item["version"])

    def test_the_set_follows_edits_threads_and_new_screens(self):
        """#set-where: no list names the screens, so adding a file adds a screen; a resolved thread is no longer open;
        an edit is a new version."""
        self.start()
        self.comment("01-host-page.mockup.html")
        before = self.set_of()[2]["mockups"]
        self.post("01-host-page.mockup.html", {"event": "status", "id": "s1", "threadId": "c1", "status": "resolved"},
                  "1002-status-s1.json")
        write(self.repo / SET / "01-host-page.mockup.html", HOST.replace("Save", "Save name"))
        write(self.repo / SET / "04-empty-state.mockup.html", SAME.replace("Settings", "Empty state"))
        after = self.set_of()[2]["mockups"]
        self.assertEqual(after[0]["open"], 0)
        self.assertNotEqual(after[0]["version"], before[0]["version"])
        self.assertEqual([item["screen"] for item in after][-1], "Empty state")

    def test_the_service_tells_each_mockups_steps_and_open_questions(self):
        """#chip-source: the anchors each mockup's steps illustrate, to those steps' names, and its open questions; a
        question marked later is not open, and a step is no part."""
        write(self.repo / SET / "01-host-page.mockup.html", ASKING)
        self.start()
        mockups = self.set_of()[2]["mockups"]
        self.assertEqual(mockups[0]["steps"], {"acceptance-button": ["saved"], "plain": ["saved"]})
        self.assertEqual(mockups[0]["illustrates"], {
            "acceptance-card": ["desktop", "desktop-card", "phone", "phone-card"],
            "acceptance-button": ["desktop-save", "phone-card"]})
        self.assertEqual([(item["questions"], item["steps"]) for item in mockups[1:]], [(0, {}), (0, {})])
        self.assertEqual(mockups[0]["questions"], 1)
        write(self.repo / SET / "01-host-page.mockup.html", ASKING.replace('data-spec-tbd data-about', 'data-spec-tbd="later" data-about'))
        self.assertEqual(self.set_of()[2]["mockups"][0]["questions"], 0, "an edit settling the question is read again")

    def test_the_service_tells_how_many_directions_each_mockup_shows(self):
        """#chip-source, #screen-index-directions: how many directions each mockup's directions list names, a drawn
        direction's own frames, which name theirs, counting none; 0 with no list, and its count again after an edit. What
        its parts illustrate is the pick's alone: no part of another direction's frames, which take no comment
        (#directions-pick-only), so no See mockup chip opens at one."""
        directions = ('<ul data-mockup-directions data-anchor="directions">'
                      '<li data-direction="cards" data-gives-up="Weeks take a press." data-pick="Totals at a glance.">Cards</li>'
                      '<li data-direction="table" data-gives-up="Narrow columns on a phone.">Table</li></ul>\n')
        table = ('<section data-mockup-frame="desktop" data-direction="table" data-anchor="table-desktop" '
                 'data-illustrates="acceptance-card"><section data-anchor="table-desktop-teams" '
                 'data-illustrates="acceptance-button"><p>Teams</p></section></section>\n')
        write(self.repo / SET / "01-host-page.mockup.html", HOST.replace("</body>", table + directions + "</body>"))
        self.start()
        told = self.set_of()[2]["mockups"]
        self.assertEqual([item["directions"] for item in told], [2, 0, 0])
        self.assertEqual(told[0]["illustrates"], {"acceptance-card": ["desktop", "desktop-card", "phone", "phone-card"],
                                                  "acceptance-button": ["desktop-save", "phone-card"]})
        write(self.repo / SET / "01-host-page.mockup.html", HOST.replace("</body>", directions.replace(
            "</ul>", '<li data-direction="list" data-gives-up="No totals.">List</li></ul>') + "</body>"))
        self.assertEqual(self.set_of()[2]["mockups"][0]["directions"], 3)

    def test_a_spec_with_no_set_is_told_none(self):
        """#chip-source: a spec with no set shows no chip; the route takes a spec page, never a mockup page."""
        self.write_registry()
        with self.registry.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n[[resource]]\n"
                f'id = "spec:demo::docs/specs/plain.spec.html"\nslug = "{SLUG}"\nroot = "{self.repo}"\n'
                f'narrow_root = "{self.repo}/docs"\nspec = "docs/specs/plain.spec.html"\nbase = "{self.base}"\n'
                'owner = "w1:pOwner"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n')
        write(self.specs / "plain.spec.html", SPEC)
        self.start()
        status, _, answer = self.set_of("docs/specs/plain.spec.html")
        self.assertEqual((status, answer), (200, {"mockups": []}))
        self.assertEqual(self.set_of(SET + "/01-host-page.mockup.html")[0], 400)
        self.assertEqual(self.set_of("docs/specs/other.spec.html")[0], 400)

    def test_another_origin_is_answered_without_login_or_cookie(self):
        """served-pages #acceptance-origin-safe, #served-no-origin: a public service answers a page with no origin
        on the set route and a mockup's events, with no cookie and no credential."""
        self.write_registry(public_base="https://spec.203-0-113-9.sslip.io")
        self.start()
        for path in ("/api/mockups?" + urlencode({"path": SLUG + "/docs/specs/demo.spec.html"}),
                     self.events("01-host-page.mockup.html"), self.page("01-host-page.mockup.html")):
            with self.subTest(path=path):
                status, headers, _ = self.request("GET", path, headers={"Origin": "null"})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("access-control-allow-origin"), "null")
                self.assertNotIn("access-control-allow-credentials", headers)
                self.assertNotIn("set-cookie", headers)


class MockupIndexTest(MockupCase):
    """review-index #index-mockups, #index-threads-source, #index-gate-sent; mockups #accept-index: the review index
    reads a lane spec with its mockup set, its counts and gate word over the spec's and its mockups' threads as one,
    and shows no row of its own for a mockup."""

    def event(self, artifact, actor, stamp, **body):
        """One event file written into the spool beside `artifact`, the spec or a mockup, as the page or the agent
        writes it."""
        directory = Path(str(artifact) + ".review") / actor
        directory.mkdir(parents=True, exist_ok=True)
        body = {"actor": actor, "schemaVersion": 1, "createdAt": "2026-10-27T00:00:00Z", "browser": "b", **body}
        (directory / ("%d-%s-%s.json" % (stamp, body["event"], body["id"]))).write_text(json.dumps(body))

    def thread(self, artifact, thread_id, stamp, *steps):
        """A comment, then each step: handoff, reply (the agent's), resolve."""
        self.event(artifact, "human", stamp, id=thread_id, event="comment", anchorId="desktop-save", text="c")
        for at, step in enumerate(steps, stamp + 1):
            if step == "handoff":
                self.event(artifact, "human", at, id="h" + thread_id, event="handoff", events=[thread_id])
            elif step == "reply":
                self.event(artifact, "agent", at, id="r" + thread_id, event="reply", respondsTo=thread_id,
                           status="acknowledged", text="Done.")
            elif step == "resolve":
                self.event(artifact, "human", at, id="s" + thread_id, event="status", respondsTo=thread_id,
                           threadId=thread_id, status="resolved")

    def rows(self):
        """{title: [gate word, meta words...]} of every spec row the index shows."""
        import html as html_lib
        import re

        status, _, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        rows = {}
        for item in re.findall(r'<li class="spec[^"]*"[^>]*>(.*?)</li>', body.decode(), re.S):
            texts = [t.strip() for t in html_lib.unescape(re.sub(r"<[^>]+>", "\n", item)).splitlines() if t.strip()]
            rows[texts[0]] = texts[1:2] + [word for text in texts[2:] for word in text.split(" · ")]
        return rows

    def version(self, path):
        return self.request("GET", path)[1]["etag"].strip('"')

    def test_a_mockups_open_thread_counts_on_its_specs_row(self):
        """mockups #acceptance-index-counts: the spec holds two resolved threads, its mockup one open thread; one row,
        none for a mockup."""
        self.start()
        spec = self.specs / "demo.spec.html"
        self.thread(spec, "a", 100, "handoff", "reply", "resolve")
        self.thread(spec, "b", 200, "handoff", "reply", "resolve")
        self.thread(self.repo / SET / "01-host-page.mockup.html", "m", 300)
        self.assertEqual(self.rows(), {"Demo": ["Draft not sent", "1 open", "2 resolved"]})

    def test_a_mockups_open_question_is_its_specs_open_decision(self):
        """review-index #index-gate-decision, mockups #accept-index: an open question on a mockup of the set reads as
        the spec's open TBD, Decision open, before any thread's gate; one marked later is no decision."""
        self.start()
        self.thread(self.specs / "demo.spec.html", "a", 100, "handoff")
        write(self.repo / SET / "02-thread-on-phone.mockup.html", THREAD.replace("</body>", WALKTHROUGH + "</body>"))
        self.assertEqual(self.rows()["Demo"][0], "Decision open")
        write(self.repo / SET / "02-thread-on-phone.mockup.html", THREAD)
        self.assertEqual(self.rows()["Demo"][0], "With the agent")

    def test_a_reply_waiting_on_a_mockup_is_its_specs_gate(self):
        """mockups #acceptance-index-gate: no open thread on the spec, the mockup's thread ends with the agent's reply;
        a thread on any screen of the set counts, and every spool-fold gate reads the set's threads as one."""
        self.start()
        spec = self.specs / "demo.spec.html"
        self.thread(spec, "a", 100, "handoff", "reply", "resolve")
        self.thread(self.repo / SET / "02-thread-on-phone.mockup.html", "m", 200, "handoff", "reply")
        self.assertEqual(self.rows(), {"Demo": ["Reply waiting", "1 open", "1 resolved"]})
        self.thread(self.repo / SET / "02-thread-on-phone.mockup.html", "n", 300, "handoff")
        self.thread(self.repo / SET / "02-thread-on-phone.mockup.html", "m2", 400, "resolve")
        self.assertEqual(self.rows()["Demo"], ["Reply waiting", "2 open", "2 resolved"])
        self.event(self.repo / SET / "02-thread-on-phone.mockup.html", "human", 500, id="sm", event="status",
                   respondsTo="m", threadId="m", status="resolved")
        self.assertEqual(self.rows()["Demo"], ["With the agent", "1 open", "3 resolved"])
        self.thread(self.repo / SET / "03-settings.mockup.html", "d", 600)
        self.assertEqual(self.rows()["Demo"], ["Draft not sent", "2 open", "3 resolved"])

    def test_accept_spec_sent_holds_while_the_set_is_the_one_it_names_unchanged(self):
        """review-index #index-gate-sent, mockups #accept-set: the newest hand-off is Accept spec, naming each mockup
        and the version its page showed; an edit to a named mockup makes the spec Ready to accept again, as an edit to
        the spec does, and so does a screen it names that the set no longer holds, or one the set holds that it does
        not name (#acceptance-index-gate-sent-new-mockup, below)."""
        self.start()
        spec = self.specs / "demo.spec.html"
        self.thread(spec, "a", 100, "handoff", "reply", "resolve")
        named = ("01-host-page.mockup.html", "02-thread-on-phone.mockup.html", "03-settings.mockup.html")

        def accept(stamp, **versions):
            self.event(spec, "human", stamp, id="acc%d" % stamp, event="handoff",
                       version=self.version("/%s/docs/specs/demo.spec.html" % SLUG),
                       mockups=[{"path": "mockups/demo/" + name, "version": versions.get(name) or self.version(
                           self.page(name))} for name in named])

        accept(200)
        self.assertEqual(self.rows(), {"Demo": ["Accept spec sent", "1 resolved"]})
        write(self.repo / SET / "02-thread-on-phone.mockup.html", THREAD.replace("</section>\n</body>",
                                                                                 "<p>Edited.</p></section>\n</body>"))
        self.assertEqual(self.rows(), {"Demo": ["Ready to accept", "1 resolved"]})
        accept(300)
        self.assertEqual(self.rows()["Demo"][0], "Accept spec sent")
        (self.repo / SET / "01-host-page.mockup.html").unlink()
        self.assertEqual(self.rows()["Demo"][0], "Ready to accept")
        write(self.repo / SET / "01-host-page.mockup.html", HOST)
        self.assertEqual(self.rows()["Demo"][0], "Accept spec sent")
        # A named version that is not the one the page shows was never sent.
        accept(400, **{"01-host-page.mockup.html": "0" * 64})
        self.assertEqual(self.rows()["Demo"][0], "Ready to accept")

    def test_a_screen_added_after_accept_spec_makes_the_spec_ready_to_accept(self):
        """review-index #acceptance-index-gate-sent-new-mockup, mockups #accept-set: no evidence provider; the spec was
        pressed Accept spec with its set of three screens, then the set gained a fourth the hand-off does not name: the
        row reads Ready to accept, and its lane sits under Needs you. Accept spec naming it sends the spec again."""
        import re

        self.start()
        spec = self.specs / "demo.spec.html"
        self.thread(spec, "a", 100, "handoff", "reply", "resolve")
        names = ["01-host-page.mockup.html", "02-thread-on-phone.mockup.html", "03-settings.mockup.html"]

        def accept(stamp):
            self.event(spec, "human", stamp, id="acc%d" % stamp, event="handoff",
                       version=self.version("/%s/docs/specs/demo.spec.html" % SLUG),
                       mockups=[{"path": "mockups/demo/" + name, "version": self.version(self.page(name))}
                                for name in names])

        def part():
            body = self.request("GET", "/")[2].decode()
            return next(label for label, lanes in re.findall(r'<nav class="part" aria-label="([^"]+)"[^>]*>(.*?)</nav>',
                                                            body, re.S) if 'aria-label="QA-900"' in lanes)

        accept(200)
        self.assertEqual((self.rows(), part()), ({"Demo": ["Accept spec sent", "1 resolved"]}, "In progress"))
        write(self.repo / SET / "04-export.mockup.html", SAME.replace("Settings", "Export"))
        self.assertEqual((self.rows(), part()), ({"Demo": ["Ready to accept", "1 resolved"]}, "Needs you"))
        names.append("04-export.mockup.html")
        accept(300)
        self.assertEqual((self.rows(), part()), ({"Demo": ["Accept spec sent", "1 resolved"]}, "In progress"))


class MockupWakeTest(MockupCase):
    def setUp(self):
        super().setUp()
        self.waker = FakeWaker(self.work, self.state)
        self.waker.agent("w1:pOwner")

    def test_a_mockup_hand_off_wakes_the_spec_rows_owner_naming_the_mockup(self):
        """#acceptance-wake: the owner's pane receives a wake prompt for the hand-off naming the mockup, with the
        spec, collection, cursor, and event count (host-wake #wake-rule-message); once per batch."""
        server = self.start()
        self.comment("01-host-page.mockup.html")
        self.hand_off("01-host-page.mockup.html")
        controller = server.wake_controller
        controller.poll()
        controller.poll()
        says = self.waker.says()
        self.assertEqual(len(says), 1)
        owner, artifact, message = says[0]
        mockup = str(self.repo / SET / "01-host-page.mockup.html")
        self.assertEqual((owner, artifact), ("w1:pOwner", mockup))
        for part in ("mockup " + mockup, str(self.specs / "demo.spec.html"), str(self.repo / "docs"), ".cursor-owner",
                     "2 events", "zero-wait scan", "park"):
            self.assertIn(part, message)
        status, headers, _ = self.request("GET", self.events("01-host-page.mockup.html"))
        self.assertEqual((status, headers.get("x-spec-chat-wake")), (200, "sent"))
        _, headers, _ = self.request("GET", "/api/events?" + urlencode(
            {"dir": "/%s/docs/specs/demo.spec.html.review" % SLUG}))
        self.assertIsNone(headers.get("x-spec-chat-wake"), "the spec's own spool has no hand-off waiting")

    def test_a_screen_added_after_the_service_started_is_watched(self):
        """#set-where: adding a screen is adding a file, so its hand-off wakes the owner with no new registration."""
        server = self.start()
        controller = server.wake_controller
        controller.poll()
        write(self.repo / SET / "04-empty-state.mockup.html", SAME)
        self.comment("04-empty-state.mockup.html")
        self.hand_off("04-empty-state.mockup.html")
        controller.poll()
        self.assertEqual([say[1] for say in self.waker.says()], [str(self.repo / SET / "04-empty-state.mockup.html")])

    def test_an_idle_set_costs_stats_alone(self):
        """review-service #steady-idle: with a set and no hand-off waiting, polls read no mockup and no spool event."""
        server = self.start()
        self.comment("01-host-page.mockup.html")
        self.hand_off("01-host-page.mockup.html")
        cursor = self.spool("01-host-page.mockup.html") / ".cursor-owner"
        cursor.write_text("1000-comment-c1.json\n1001-handoff-h1.json\n", encoding="utf-8")
        old = 1_000_000_000
        for path in (self.spool("01-host-page.mockup.html") / "human", cursor, self.repo / SET):
            os.utime(path, (old, old))
        controller = server.wake_controller
        controller.poll()
        reads = []
        listdir, read_events = os.listdir, serve.spool.read_events
        with unittest.mock.patch.object(serve.os, "listdir", lambda *a: (reads.append(a), listdir(*a))[1]), \
                unittest.mock.patch.object(serve.spool, "read_events", lambda *a, **k: (reads.append(a),
                                                                                         read_events(*a, **k))[1]):
            for _ in range(5):
                controller.poll()
        self.assertEqual(reads, [])
        self.assertEqual(self.waker.says(), [])


class MockupLinkedSpecTest(MockupCase):
    """#set-where, #review-same: a spec served at a path that is a link to a file not named *.spec.html; its set is
    found beside the path it is served at, as its mockup pages are served there, by the set route, the index, and wake."""

    LINKED_SET = "docs/specs/mockups/linked"
    event, rows, version = MockupIndexTest.event, MockupIndexTest.rows, MockupIndexTest.version

    def setUp(self):
        super().setUp()
        write(self.specs / "real-linked.html", SPEC)
        (self.specs / "linked.spec.html").symlink_to("real-linked.html")
        write(self.repo / self.LINKED_SET / "01-host-page.mockup.html", HOST)
        self.registry.write_text(
            "[[resource]]\n"
            f'id = "spec:demo::docs/specs/linked.spec.html"\nslug = "{SLUG}"\nroot = "{self.repo}"\n'
            f'narrow_root = "{self.repo}/docs"\nspec = "docs/specs/linked.spec.html"\nbase = "{self.base}"\n'
            'owner = "w1:pOwner"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n'
            'registered_at = "2026-10-27T00:00:00Z"\n', encoding="utf-8")
        self.waker = FakeWaker(self.work, self.state)
        self.waker.agent("w1:pOwner")
        self.mockup = self.repo / self.LINKED_SET / "01-host-page.mockup.html"
        self.mockup_page = "/%s/%s/01-host-page.mockup.html" % (SLUG, self.LINKED_SET)

    def test_the_set_route_the_index_and_wake_find_the_set_beside_the_served_path(self):
        server = self.start()
        query = urlencode({"dir": self.mockup_page + ".review", "actor": "human", "name": "1000-comment-c1.json"})
        self.assertEqual(self.request("POST", "/api/events?" + query, {
            "event": "comment", "id": "c1", "anchorId": "desktop-save", "text": "Make it primary."})[0], 200)
        status, _, body = self.request("GET", "/api/mockups?" + urlencode({"path": SLUG + "/docs/specs/linked.spec.html"}))
        self.assertEqual(status, 200)
        self.assertEqual([(item["page"], item["open"]) for item in json.loads(body)["mockups"]],
                         [("mockups/linked/01-host-page.mockup.html", 1)])
        self.assertEqual(self.rows(), {"Demo": ["Draft not sent", "1 open", "Changed"]})
        # Accept spec, written to the spec's spool beside its file, names the mockup at the version its page showed.
        self.event(self.mockup, "human", 1001, id="s1", event="status", respondsTo="c1", threadId="c1", status="resolved")
        self.event(self.specs / "real-linked.html", "human", 1002, id="acc", event="handoff",
                   version=self.version("/%s/docs/specs/linked.spec.html" % SLUG),
                   mockups=[{"path": "mockups/linked/01-host-page.mockup.html", "version": self.version(self.mockup_page)}])
        self.assertEqual(self.rows(), {"Demo": ["Accept spec sent", "1 resolved", "Changed"]})
        # A hand-off on the mockup page wakes the row's owner naming the mockup.
        query = urlencode({"dir": self.mockup_page + ".review", "actor": "human", "name": "1003-comment-c2.json"})
        self.request("POST", "/api/events?" + query, {"event": "comment", "id": "c2", "anchorId": "desktop-save", "text": "Bigger."})
        query = urlencode({"dir": self.mockup_page + ".review", "actor": "human", "name": "1004-handoff-h2.json"})
        self.request("POST", "/api/events?" + query, {"event": "handoff", "id": "h2", "events": ["c2"]})
        server.wake_controller.poll()
        # The Accept spec hand-off wakes the owner for the spec as well.
        says = [list(say[:2]) for say in self.waker.says()]
        self.assertIn(["w1:pOwner", str(self.mockup)], says)
        self.assertIn(["w1:pOwner", str(self.specs / "real-linked.html")], says)


class MockupSetBoundTest(MockupCase):
    def test_the_set_route_holds_a_bounded_count_of_spool_readings(self):
        """review-service #steady-bound: a spec page asking its set reads each mockup's spool, and what the service
        keeps of those readings stays within a fixed count, however many sets are asked; no index load prunes it."""
        for at in range(6):
            write(self.specs / ("s%d.spec.html" % at), SPEC)
            write(self.specs / ("mockups/s%d/01-host-page.mockup.html" % at), HOST)
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState([serve._single_mount(self.repo / "docs")])
        server.state_dir = None
        server.wake_controller = types.SimpleNamespace(status=lambda _resource: None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.server = server
        with unittest.mock.patch.object(serve._SPOOL_CACHE, "limit", 3):
            for at in range(6):
                status, _, body = self.request("GET", "/api/mockups?" + urlencode({"path": "specs/s%d.spec.html" % at}))
                self.assertEqual((status, len(json.loads(body)["mockups"])), (200, 1))
                self.assertLessEqual(len(serve._SPOOL_CACHE), 3)


if __name__ == "__main__":
    unittest.main()
