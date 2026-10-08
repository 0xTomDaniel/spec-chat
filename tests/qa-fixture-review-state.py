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

    def spec(self, collection):
        return str(self.root / "site" / collection / spools.SPEC)

    def events(self, collection):
        human = Path(self.spec(collection) + ".review") / "human"
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
            self.assertIn('"spec:%s::docs/specs/report.spec.html"' % collection, registry)
            # An index lane is reached from the index root ("{review}/"), any other from its own path.
            self.assertIn("" if collection in spools.INDEX_LANES else collection, hinted)

    def test_seeded_events_name_their_version_and_file(self):
        version = hashlib.sha256(HEAD.encode("utf-8")).hexdigest()
        for collection in spools.COLLECTIONS:
            review = self.site.spec(collection) + ".review"
            for event in self.site.events(collection) if os.path.isdir(review) else ():
                body = event["body"]
                self.assertEqual(event["name"].split("-", 1)[1], "%s-%s.json" % (body["event"], body["id"]))
                self.assertEqual(body["version"], version)
                self.assertEqual(place.read_version(review, version), HEAD)
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

    def serve(self, site=None):
        """The fixture service (serve.py) on a site, this class's by default; returns its base url."""
        site = site or self.site
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "HOME": str(site.root / "home"), "XDG_STATE_HOME": str(site.root / "state"),
               "XDG_CONFIG_HOME": str(site.root / "config")}
        env.pop("HERDR_SOCKET_PATH", None)
        server = subprocess.Popen([sys.executable, str(FIXTURE / "serve.py"), str(site.root / "serve" / "registry.toml"), str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (server.terminate(), server.wait(timeout=10)))
        base = "http://127.0.0.1:%d" % port
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/qa-fixture/docs/specs/report.spec.html").close()
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
        """lane-hosting #acceptance-index-threads, -threads-none, -threads-refresh Givens: QA-1 holds every
        status (3 Open, 1 Resolved) on its report spec and none on the others; QA-2 one open thread."""
        import html
        import re

        base = self.serve()
        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        cards = dict(re.findall(r'<section class="lane[^"]*" aria-label="([^"]+)">(.*?)</section>', body, re.S))
        self.assertEqual(list(cards), ["QA-1", "QA-2"])

        def rows(card):
            result = {}
            for item in re.findall(r"<li>(.*?)</li>", card, re.S):
                parts = [p.strip() for p in html.unescape(re.sub(r"<[^>]+>", "\n", item)).splitlines() if p.strip()]
                result[parts[0]] = [p for p in parts[1:] if p != "Changed"]
            return result

        quiet = {"Onboarding": [], "Report filters": [], "Report sorting": []}
        self.assertEqual(rows(cards["QA-1"]), {**quiet, "Weekly report": ["3", "Open", "1", "Resolved"]})
        self.assertEqual(rows(cards["QA-2"]), {**quiet, "Weekly report": ["1", "Open"]})
        statuses = {th["id"]: th["status"] for th in spool.fold_threads(self.site.events("qa-1") + [
            {"name": name, "actor": "agent", "body": json.loads((Path(self.site.spec("qa-1") + ".review") / "agent" / name).read_text())}
            for name in os.listdir(self.site.spec("qa-1") + ".review/agent")]).values()}
        self.assertEqual(statuses, {"qa-draft": "draft", "qa-handed": "pending", "qa-replied": "acknowledged",
                                    "qa-resolved": "resolved"})

    def test_stale_page_edit_lands_on_save_and_the_mark_follows(self):
        self.assertEqual(self.current("stale-page"), HEAD)
        self.assertFalse(os.path.exists(self.site.spec("stale-page") + ".review"))
        base = self.serve()
        with urllib.request.urlopen(base + "/stale-page/docs/specs/report.spec.html") as response:
            etag = response.headers["ETag"].strip('"')
        self.assertEqual(etag, hashlib.sha256(HEAD.encode("utf-8")).hexdigest())
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


    def test_rule_state_follows_the_story_and_the_criterion_given(self):
        """project-rules #approval: a confirmed-rule story's entry shows the rule card and reconcile offer, story-approve's
        the candidate; rule-candidate and rule-confirmed hold their state in either story; a reset forgets clicks."""
        site = Site()  # its own site: its resets replace the shared site's collections
        self.addCleanup(site.tmp.cleanup)
        base = self.serve(site)

        def jev(collection):
            query = urllib.parse.urlencode({"path": "%s/docs/specs/report.spec.html" % collection})
            with urllib.request.urlopen(base + "/api/jev?" + query) as response:
                answer = json.load(response)
            return {item["kind"] for item in answer["items"]} & {"rule", "candidate"}, answer

        def confirmed(collection):
            kinds, answer = jev(collection)
            self.assertEqual(kinds, {"rule"}, collection)
            self.assertIsNone(answer["candidate_offer"], collection)
            self.assertEqual(answer["offer"]["count"], 1, collection)
            self.assertEqual(len(answer["rules"]), 1, collection)

        def candidate(collection):
            kinds, answer = jev(collection)
            self.assertNotIn("rule", kinds, collection)
            self.assertEqual(answer["candidate_offer"]["count"], 1, collection)
            self.assertIsNone(answer["offer"], collection)
            self.assertEqual(answer["rules"], [], collection)

        site.reset(QA_STORY="story-miss", QA_URL_REVIEW=base)
        confirmed("qa-fixture"), confirmed("rule-confirmed"), candidate("rule-candidate")
        until = time.monotonic() + 20  # the card's title is the fixture's plain-words name, written by the general LLM
        while (names := {i.get("name") for i in jev("qa-fixture")[1]["items"] if i["kind"] == "rule"}) != {RULE_NAME}:
            self.assertLess(time.monotonic(), until, names)
            time.sleep(0.2)
        body = json.dumps({"dismiss": "rule", "rule": ONBOARDING}).encode()
        request = urllib.request.Request(base + "/api/jev/offer?path=qa-fixture/docs/specs/report.spec.html", data=body, method="POST")
        with urllib.request.urlopen(request) as response:
            self.assertTrue(json.load(response)["ok"])
        self.assertEqual(jev("qa-fixture")[0], set())
        site.reset(QA_STORY="story-approve", QA_URL_REVIEW=base)
        candidate("qa-fixture"), confirmed("rule-confirmed"), candidate("rule-candidate")
        site.reset(QA_STORY="story-dismiss", QA_URL_REVIEW=base)
        confirmed("qa-fixture")


if __name__ == "__main__":
    unittest.main()
