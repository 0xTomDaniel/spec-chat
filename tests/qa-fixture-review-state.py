"""QA review-state collections (tests/fixtures/qa/spools.py): each seeded spool resolves
through place.py and spool.py to the state its review-state criterion's Given names."""

import hashlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "qa"
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


place = load("qa_place", ASSETS / "place.py")
spool = load("qa_spool", ASSETS / "spool.py")
spools = load("qa_spools", FIXTURE / "spools.py")
panes = load("qa_panes", FIXTURE / "panes.py")
ONBOARDING = load("qa_rules", ROOT / "tests" / "review-serve-jev-rules.py").ONBOARDING
RULE_NAME = load("qa_serve", FIXTURE / "serve.py").RULE_NAME
HEAD = (FIXTURE / "head" / "docs" / "specs" / "report.spec.html").read_text(encoding="utf-8")


class Site:
    """One run of the real reset.sh into a temporary QA_ROOT."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reset()

    def reset(self, **extra):
        """reset.sh, as capture runs it: with QA_STORY and QA_URL_REVIEW once the service runs."""
        env = {**os.environ, "QA_ROOT": str(self.root), "QA_FIXTURE": str(FIXTURE), "QA_REPO_SPEC_CHAT": str(ROOT), **extra}
        subprocess.run(["sh", str(FIXTURE / "reset.sh")], check=True, env=env)

    def spec(self, collection, spec=spools.SPEC):
        return str(self.root / "site" / collection / spec)

    def events(self, collection, spec=spools.SPEC):
        human = Path(self.spec(collection, spec) + ".review") / "human"
        return [{"name": name, "body": json.loads((human / name).read_text())} for name in sorted(os.listdir(human))]

    def places(self, collection):
        return {e["body"]["id"]: e["place"] for e in place.resolve_events(self.spec(collection), self.events(collection))}


class ReviewStateCollections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site()

    @classmethod
    def tearDownClass(cls):
        cls.site.tmp.cleanup()

    def current(self, collection):
        return Path(self.site.spec(collection)).read_text(encoding="utf-8")

    def test_every_collection_is_reset_registered_and_navigated(self):
        reset = (FIXTURE / "reset.sh").read_text()
        registry = (self.site.root / "serve" / "registry.toml").read_text()
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        hinted = {hint["path"].split("/")[1] for hint in nav.values()}
        for collection in spools.COLLECTIONS:
            self.assertIn(collection, reset)
            spec = spools.SPEC_OF.get(collection, spools.SPEC).replace(os.sep, "/")
            self.assertIn('"spec:%s::%s"' % (collection, spec), registry)
            # An index lane is reached from the index root ("{review}/"), any other from its own path.
            self.assertIn("" if collection in spools.INDEX_LANES else collection, hinted)

    def test_every_pane_collection_has_its_host_pages_and_hosted_threads(self):
        """comment-layer collections (panes.py): host pages naming this reset's project and the collection's scope, and
        hosted threads in the service state only where seeded; navigated, or proven by tests/runtime-comment-layer.mjs."""
        reset = (FIXTURE / "reset.sh").read_text()
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        hinted = {hint["path"].split("/")[1] for hint in tomllib.loads((ROOT / "qa.toml").read_text())["nav"].values()}
        browser_test = (ROOT / "tests" / "runtime-comment-layer.mjs").read_text()
        for collection, (hosted, _, _) in panes.COLLECTIONS.items():
            self.assertIn(collection, reset)
            project = next(row["project"] for row in rows if row["slug"] == collection)
            for page in ("pane.html", "pane-other.html"):
                text = (self.site.root / "site" / collection / "docs" / page).read_text(encoding="utf-8")
                self.assertIn('data-comment-subject="project/%s::docs/specs/report.spec.html#report-totals"' % project, text)
                self.assertIn('data-comment-scope="%s"' % collection, text)
                self.assertNotIn("{{", text)
            self.assertEqual((self.site.root / "serve" / "comments" / collection).is_dir(), hosted is not None)
            self.assertTrue(collection in hinted or "'%s'" % collection in browser_test, collection)

    def test_seeded_events_name_their_version_and_file(self):
        for collection in spools.COLLECTIONS:
            spec = spools.SPEC_OF.get(collection, spools.SPEC)
            text = (FIXTURE / "head" / spec).read_text(encoding="utf-8")
            version = hashlib.sha256(text.encode("utf-8")).hexdigest()
            review = self.site.spec(collection, spec) + ".review"
            for event in self.site.events(collection, spec) if os.path.isdir(review) else ():
                body = event["body"]
                self.assertEqual(event["name"].split("-", 1)[1], "%s-%s.json" % (body["event"], body["id"]))
                self.assertEqual(body["version"], version)
                self.assertEqual(place.read_version(review, version), text)
                self.assertEqual(body["actor"], "human")
                self.assertTrue(body["browser"] and body["author"])

    def test_anchor_moved_keeps_the_same_sentence(self):
        self.assertIn(spools.INSERTED, self.current("anchor-moved"))
        mark = self.site.places("anchor-moved")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"], mark["quote"]), ("kept", "report-export", spools.SENTENCE))
        self.assertEqual(self.current("anchor-moved")[mark["start"]:mark["end"]], spools.SENTENCE)

    def test_seeded_text_key_is_the_pages_40_char_prefix(self):
        """The page keys a text target by the selection's first 40 characters; the rewrite lies past it."""
        body = next(e["body"] for e in self.site.events("anchor-changed") if e["body"]["id"] == "qa-sentence")
        self.assertEqual((body["target"]["key"], body["quote"]), (spools.SENTENCE[:40], spools.SENTENCE))
        self.assertNotIn(spools.REWRITE[0], body["target"]["key"])

    def test_anchor_changed_sits_on_remaining_text(self):
        mark = self.site.places("anchor-changed")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"]), ("changed", "report-export"))
        self.assertTrue(mark["quote"].startswith("The export button downloads a CSV of"))
        self.assertNotEqual(mark["quote"], spools.SENTENCE)

    def test_anchor_gone_falls_to_nearest_surviving_block(self):
        self.assertNotIn(spools.SENTENCE, self.current("anchor-gone"))
        mark = self.site.places("anchor-gone")["qa-sentence"]
        self.assertEqual((mark["state"], mark["anchorId"], mark["start"]), ("gone", "acceptance", None))

    def test_agent_scan_batch_holds_the_rewritten_handed_off_comment(self):
        review = self.site.spec("agent-scan") + ".review"
        batch = spool.handoff_batch(review, set())
        self.assertEqual([name.split("-", 2)[1] for name in batch], ["comment", "handoff"])
        self.assertEqual(self.site.places("agent-scan")["qa-sentence"]["state"], "changed")

    def serve(self, site=None, registry="serve", ready="/qa-fixture/docs/specs/report.spec.html", state="state"):
        """The fixture service (serve.py) on a site, this class's by default, on its review registry, or review-plugged's
        ("serve-plugged", state "state-plugged"), as qa.toml declares them; returns its base url."""
        site = site or self.site
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "HOME": str(site.root / "home"), "XDG_STATE_HOME": str(site.root / state),
               "XDG_CONFIG_HOME": str(site.root / "config")}
        env.pop("HERDR_SOCKET_PATH", None)
        server = subprocess.Popen([sys.executable, str(FIXTURE / "serve.py"), str(site.root / registry / "registry.toml"), str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (server.terminate(), server.wait(timeout=10)))
        base = "http://127.0.0.1:%d" % port
        for _ in range(100):
            try:
                urllib.request.urlopen(base + ready).close()
                return base
            except OSError:
                time.sleep(0.1)
        self.fail("serve.py did not start")

    def post(self, base, collection, body):
        name = "%d-%s-%s.json" % (time.time_ns(), body["event"], body["id"])
        query = "dir=/%s/docs/specs/report.spec.html.review&actor=human&name=%s" % (collection, name)
        request = urllib.request.Request(base + "/api/events?" + query, data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(json.load(response)["name"], name)
        return name

    def served_events(self, base, collection):
        with urllib.request.urlopen(base + "/api/events?dir=/%s/docs/specs/report.spec.html.review" % collection) as response:
            return json.load(response)

    def test_index_lanes_show_their_seeded_thread_counts(self):
        """lane-hosting #acceptance-index-threads, -threads-none, -threads-refresh, -index-stale Givens: QA-1 holds
        every status (3 Open, 1 Resolved) on its report spec and none on the others; QA-2 one open thread, and a
        spec registered from a removed worktree, shown as its path with a Stale pill and no link."""
        import html
        import re

        base = self.serve()
        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        cards = dict(re.findall(r'<section class="lane[^"]*" aria-label="([^"]+)">(.*?)</section>', body, re.S))
        self.assertEqual(list(cards), ["QA-1", "QA-2", "QA-4", "QA-5", "QA-6", "QA-7", "QA-3"])

        def rows(card):
            result = {}
            for item in re.findall(r"<li>(.*?)</li>", card, re.S):
                parts = [p.strip() for p in html.unescape(re.sub(r"<[^>]+>", "\n", item)).splitlines() if p.strip()]
                result[parts[0]] = [p for p in parts[1:] if p != "Changed"]
            return result

        # Gate words (lane-hosting #index-gates): the report specs hold open TBDs, the others none and no thread.
        quiet = {"Onboarding": ["Ready to accept"], "Report filters": ["Ready to accept"], "Report sorting": ["Decision open"]}
        self.assertEqual(rows(cards["QA-1"]), {**quiet, "Weekly report": ["Decision open", "3", "Open", "1", "Resolved"]})
        stale = "docs/specs/archive.spec.html"
        self.assertEqual(rows(cards["QA-2"]), {**quiet, "Weekly report": ["Decision open", "1", "Open"], stale: ["Stale"]})
        links = re.findall(r"<a [^>]*>([^<]*)</a>", cards["QA-2"])
        self.assertEqual(sorted(links), ["Onboarding", "Report filters", "Report sorting", "Weekly report"])
        self.assertIn(">4 need you<", cards["QA-2"])  # a stale row has no gate and counts toward nothing
        statuses = {th["id"]: th["status"] for th in spool.fold_threads(self.site.events("qa-1") + [
            {"name": name, "actor": "agent", "body": json.loads((Path(self.site.spec("qa-1") + ".review") / "agent" / name).read_text())}
            for name in os.listdir(self.site.spec("qa-1") + ".review/agent")]).values()}
        self.assertEqual(statuses, {"qa-draft": "draft", "qa-handed": "pending", "qa-replied": "acknowledged",
                                    "qa-resolved": "resolved"})

    @staticmethod
    def lanes(base):
        """{lane key: (part, count, proof link address or None, {title: gate word})} of the index at base."""
        import html
        import re

        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        result = {}
        for part, cards in re.findall(r'<p class="label">([^<]*)</p><nav class="lanes"[^>]*>(.*?)</nav>', body, re.S):
            for key, card in re.findall(r'<section class="lane[^"]*" aria-label="([^"]+)">(.*?)</section>', cards, re.S):
                rows = {html.unescape(title): html.unescape(gate) for title, gate in re.findall(
                    r'<a href="[^"]*">([^<]*)</a><span class="pills"><span class="pill[^"]*">([^<]*)</span>', card)}
                proof = re.search(r'<a class="proof" href="([^"]*)"', card)
                result[key] = (part, re.search(r'<span class="count">([^<]*)</span>', card).group(1),
                               proof and html.unescape(proof.group(1)), rows)
        return result

    def test_index_gate_lanes_read_as_their_criteria_say(self):
        """lane-hosting #acceptance-index-sections, -gate-decision, -gate-reply, -gate-draft, -gate-agent, -gate-ready,
        -gate-sent, -no-provider Givens, on the review service, which has no evidence provider: qa-3 to qa-7 hold
        Report filters alone with one thread each; Accept spec, as the page writes it, turns QA-5 to Accept spec sent."""
        site = Site()  # its own site: the Accept spec save leaves the shared seeded spools untouched
        self.addCleanup(site.tmp.cleanup)
        self.assertFalse(os.path.exists(site.root / "serve" / "providers" / "evidence.toml"))
        base = self.serve(site)
        lanes = self.lanes(base)
        self.assertEqual(lanes["QA-1"][0], "Needs you")
        self.assertEqual(lanes["QA-1"][3]["Weekly report"], "Decision open")  # open TBDs and a handed-off thread
        expected = {"QA-3": ("In progress", "Nothing needs you", None, {"Report filters": "With the agent"}),
                    "QA-4": ("Needs you", "1 need you", None, {"Report filters": "Ready to accept"}),
                    "QA-5": ("Needs you", "1 need you", None, {"Report filters": "Ready to accept"}),
                    "QA-6": ("Needs you", "1 need you", None, {"Report filters": "Reply waiting"}),
                    "QA-7": ("Needs you", "1 need you", None, {"Report filters": "Draft not sent"})}
        self.assertEqual({key: lanes[key] for key in expected}, expected)
        # Accept spec: the page's empty hand-off, naming the version it was served (review-state #live-accept).
        path = "/qa-5/" + spools.LATER.replace(os.sep, "/")
        with urllib.request.urlopen(base + path) as response:
            version = response.headers["ETag"].strip('"')
        name = "%d-handoff-qa-accept.json" % time.time_ns()
        request = urllib.request.Request(base + "/api/events?" + urllib.parse.urlencode(
            {"dir": path + ".review", "actor": "human", "name": name}), method="POST", data=json.dumps({
                "id": "qa-accept", "event": "handoff", "anchorId": "", "target": None, "quote": None,
                "text": "batch from http", "actor": "human", "createdAt": "2026-10-01T00:00:00Z", "schemaVersion": 1,
                "browser": "qa-capture", "author": "Lychee", "version": version}).encode())
        urllib.request.urlopen(request).close()
        self.assertEqual(self.lanes(base)["QA-5"],
                         ("In progress", "Nothing needs you", None, {"Report filters": "Accept spec sent"}))

    def test_plugged_index_reads_the_declared_review_facts(self):
        """lane-hosting #acceptance-index-gate-accepted, #acceptance-index-proof Givens, on review-plugged: once the
        evidence service runs, reset.sh declares its review facts answer and the provider file naming it; the provider
        names QA-8's Report filters accepted at the head commit, and a proof bundle for QA-9, whose page it serves."""
        site = Site()
        self.addCleanup(site.tmp.cleanup)
        self.assertEqual(sorted(os.listdir(site.root / "serve-plugged" / "providers")), [])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        evidence = subprocess.Popen([sys.executable, "-I", "-m", "http.server", str(port), "--bind", "127.0.0.1",
                                     "--directory", str(site.root / "evidence")],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (evidence.terminate(), evidence.wait(timeout=10)))
        url = "http://127.0.0.1:%d" % port
        base = self.serve(site, "serve-plugged", "/qa-8/docs/specs/later-only.spec.html", "state-plugged")
        self.assertEqual(self.lanes(base)["QA-8"][3], {"Report filters": "Ready to accept"})  # no provider file yet
        site.reset(QA_STORY="story-review-index", QA_URL_EVIDENCE=url)
        head = subprocess.check_output(["git", "-C", str(site.root / "site" / "qa-8"), "rev-parse", "HEAD"], text=True).strip()
        bundle = url + "/bundles/qa-proof/"
        self.assertEqual(self.lanes(base), {
            "QA-8": ("In progress", "Nothing needs you", None, {"Report filters": "Accepted " + head[:7]}),
            "QA-9": ("Needs you", "1 need you", bundle, {"Report filters": "Ready to accept"}),
        })
        with urllib.request.urlopen(bundle) as response:
            self.assertIn(b"<title>Evidence bundle qa-proof</title>", response.read())

    def test_stale_page_edit_lands_on_save_and_the_mark_follows(self):
        self.assertEqual(self.current("stale-page"), HEAD)
        self.assertFalse(os.path.exists(self.site.spec("stale-page") + ".review"))
        base = self.serve()
        with urllib.request.urlopen(base + "/stale-page/docs/specs/report.spec.html") as response:
            etag = response.headers["ETag"].strip('"')
            shown = response.read()
        self.assertIn(b'<h1 data-anchor="title">Weekly report</h1>', shown)  # the head spec text
        self.assertEqual(etag, hashlib.sha256(shown).hexdigest())  # of the served body (lane-hosting #index-http-caching)
        body = {"id": "qa-stale", "event": "comment", "actor": "human", "createdAt": "2026-10-01T00:00:00Z",
                "schemaVersion": 1, "browser": "qa-capture", "author": "Lychee", "version": etag,
                "anchorId": "report-export", "target": {"type": "text", "key": spools.KEY},
                "quote": spools.SENTENCE, "text": "Name the columns."}
        self.post(base, "stale-page", body)
        self.assertIn(spools.INSERTED, self.current("stale-page"))
        mark = next(e["place"] for e in self.served_events(base, "stale-page") if e["body"]["id"] == "qa-stale")
        self.assertEqual((mark["state"], mark["anchorId"], mark["quote"]), ("kept", "report-export", spools.SENTENCE))

    def test_name_reply_agent_answers_a_saved_comment_so_a_reply_is_possible(self):
        """acceptance-name: the page offers Reply only after an agent message (runtime threadReplyAction)."""
        site = Site()  # its own site: these saves leave the shared seeded spools untouched
        self.addCleanup(site.tmp.cleanup)
        self.assertFalse(os.path.exists(site.spec("name-reply") + ".review"))
        base = self.serve(site)
        common = {"actor": "human", "schemaVersion": 1, "browser": "qa-capture", "author": "Lychee",
                  "anchorId": "report-export", "target": None, "createdAt": "2026-10-01T00:00:00Z",
                  "version": hashlib.sha256(HEAD.encode("utf-8")).hexdigest()}
        name = self.post(base, "name-reply", dict(common, id="qa-first", event="comment", text="First note."))
        events = self.served_events(base, "name-reply")
        agent = [e["body"] for e in events if e["body"].get("actor") == "agent"]
        self.assertEqual([(a["event"], a["respondsTo"], a["status"]) for a in agent], [("reply", "qa-first", "acknowledged")])
        # a resend of the same save is answered once; a human reply is not answered
        request = urllib.request.Request(
            base + "/api/events?dir=/name-reply/docs/specs/report.spec.html.review&actor=human&name=" + name,
            data=json.dumps(dict(common, id="qa-first", event="comment", text="First note.")).encode(), method="POST")
        urllib.request.urlopen(request).close()
        self.post(base, "name-reply", dict(common, id="qa-reply", event="reply", respondsTo=agent[0]["id"],
                                           threadId="qa-first", text="Reply after reload."))
        agent = [e for e in self.served_events(base, "name-reply") if e["body"].get("actor") == "agent"]
        self.assertEqual(len(agent), 1)


    def test_coverage_undeclared_shows_change_types_and_no_coverage(self):
        """jev-suggestions #acceptance-coverage-undeclared Given: coverage-undeclared's print spec, registered there only,
        has stories and criteria and no data-story; read at its base, Jev labels each changed section's change type and
        asks no coverage. Every other spec's change type answers unrelated, so shows no label."""
        import re

        print_spec = "docs/specs/print.spec.html"
        text = (self.site.root / "site" / "coverage-undeclared" / print_spec).read_text()
        self.assertIn("data-user-story", text)
        self.assertIn("data-acceptance-criterion", text)
        self.assertIsNone(re.search(r"\bdata-story=", text))
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        self.assertEqual([r["slug"] for r in rows if r["spec"] == print_spec], ["coverage-undeclared"])
        base = self.serve()

        def items(path):
            query = urllib.parse.urlencode({"path": path, "base": rows[0]["base"]})
            until = time.monotonic() + 20
            while True:
                with urllib.request.urlopen(base + "/api/jev?" + query) as response:
                    found = json.load(response)["items"]
                if all(i["state"] != "pending" for i in found) or time.monotonic() > until:
                    return found
                time.sleep(0.1)

        found = items("coverage-undeclared/" + print_spec)
        self.assertEqual({i["id"]: i["label"] for i in found if i["kind"] == "type"},
                         {"user-stories": "no-behavior-change", "acceptance": "no-behavior-change"})
        self.assertEqual([i for i in found if i["kind"] == "coverage"], [])
        report = [i for i in items("coverage-undeclared/docs/specs/report.spec.html") if i["kind"] == "type"]
        self.assertTrue(report)
        self.assertEqual({i["state"] for i in report}, {"none"})

    def test_rule_state_follows_the_marks_and_a_reset_forgets_clicks(self):
        """project-rules #declare: every collection's report spec shows the marked onboarding rule's card under its
        mark's name; rule-candidate's unmarked onboarding criterion is a candidate on its home spec, checked nowhere;
        a reset registers a fresh project, so a story's Not for this spec never reaches the next one."""
        site = Site()  # its own site: its resets replace the shared site's collections
        self.addCleanup(site.tmp.cleanup)
        base = self.serve(site)

        def jev(collection, spec="report"):
            query = urllib.parse.urlencode({"path": "%s/docs/specs/%s.spec.html" % (collection, spec)})
            until = time.monotonic() + 20
            while True:
                with urllib.request.urlopen(base + "/api/jev?" + query) as response:
                    answer = json.load(response)
                items = [i for i in answer["items"] if i["kind"] in ("rule", "candidate")]
                if items and all(i["state"] != "pending" for i in items) or time.monotonic() > until:
                    return items, answer
                time.sleep(0.1)

        site.reset(QA_STORY="story-miss", QA_URL_REVIEW=base)
        items, answer = jev("qa-fixture")
        self.assertEqual([(i["kind"], i["label"], i["name"]) for i in items], [("rule", "missed", RULE_NAME)])
        self.assertNotIn("offer", answer)
        self.assertNotIn("candidate_offer", answer)
        items, answer = jev("rule-candidate", "onboarding")
        self.assertEqual([(i["kind"], i["id"], i["text"]) for i in items], [("candidate", "rule-onboarding", ONBOARDING)])
        self.assertEqual(jev("rule-candidate")[1]["rules"], [])
        items, _ = jev("not-a-rule", "onboarding")
        self.assertEqual([(i["kind"], i["id"]) for i in items], [("candidate", "rule-onboarding")])

        def dismiss(collection):
            rule = jev(collection)[0][0]
            body = json.dumps({"dismiss": "here", "home": rule["home"], "record": rule["record"]}).encode()
            request = urllib.request.Request(base + "/api/jev/offer?path=%s/docs/specs/report.spec.html" % collection,
                                             data=body, method="POST")
            with urllib.request.urlopen(request) as response:
                self.assertTrue(json.load(response)["ok"])
            self.assertEqual(jev(collection)[0], [])

        # a dismissing criterion's own project: its click hides no sibling criterion's marker
        dismiss("dismiss")
        for collection in ("qa-fixture", "dismiss-reload"):
            self.assertEqual([i["kind"] for i in jev(collection)[0]], ["rule"], collection)
        dismiss("qa-fixture")
        site.reset(QA_STORY="story-dismiss", QA_URL_REVIEW=base)
        self.assertEqual([i["kind"] for i in jev("qa-fixture")[0]], ["rule"])

if __name__ == "__main__":
    unittest.main()
