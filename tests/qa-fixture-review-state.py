"""QA review-state collections (tests/fixtures/qa/spools.py): each seeded spool resolves
through place.py and spool.py to the state its review-state criterion's Given names."""

import email.utils
import hashlib
import importlib.util
import json
import os
import re
import signal
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
# The review service's one load of scripts/design-set-sync (_SYNC) and _design_set, the set as the index reads it: the
# one reader of the runtime's design-set regions, (tokens, marks, icons).
REVIEW_SERVE = load("qa_review_serve", ASSETS / "review-serve.py")
DESIGN_SET = REVIEW_SERVE._design_set((ASSETS / "viz" / "runtime.js").read_text())
HEAD = (FIXTURE / "head" / "docs" / "specs" / "report.spec.html").read_text(encoding="utf-8")


def started():
    """The collection each qa.toml start point opens: its path's first segment after the service, past the test host's
    named/ route (host.py) and the test shell's native/ and bare/ routes (shell.py), "" for a service's root."""
    nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
    return {hint["path"].split("/", 1)[1].removeprefix("named/").removeprefix("native/").removeprefix("bare/").split("/")[0]
            for hint in nav.values()}


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
        # The spool's own rule (spool.py read_events): a dot name is a write not yet linked in.
        names = sorted(name for name in os.listdir(human) if not name.startswith(".") and name.endswith(".json"))
        return [{"name": name, "body": json.loads((human / name).read_text())} for name in names]

    def places(self, collection):
        return {e["body"]["id"]: e["place"] for e in place.resolve_events(self.spec(collection), self.events(collection))}


class ReviewStateCollections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = Site()

    @classmethod
    def tearDownClass(cls):
        cls.site.tmp.cleanup()

    def current(self, collection, site=None):
        return Path((site or self.site).spec(collection)).read_text(encoding="utf-8")

    def test_every_collection_is_reset_registered_and_navigated(self):
        reset = (FIXTURE / "reset.sh").read_text()
        # The review service's registry, review-plugged's, which holds qa-10, review-sections', which holds qa-12, and
        # review-prototype's, which holds the prototype collections.
        registry = "".join((self.site.root / folder / "registry.toml").read_text()
                           for folder in ("serve", "serve-plugged", "serve-sections", "serve-prototype"))
        hinted = started()
        for collection in spools.COLLECTIONS:
            self.assertIn(collection, spools.LANES if collection in spools.LANES else reset)
            spec = spools.SPEC_OF.get(collection, spools.SPEC).replace(os.sep, "/")
            self.assertIn('"spec:%s::%s"' % (collection, spec), registry)
            # A lane is reached from the index root ("{review}/"), any other from its own path.
            self.assertIn("" if collection in spools.LANES else collection, hinted)

    def test_each_lane_registers_its_specs_alone_on_its_service(self):
        """spools.py LANES is the lanes' one table; reset.sh registers each lane's specs, and only those, on
        the service it names, and no lane on any other."""
        registries = {folder: tomllib.loads((self.site.root / folder / "registry.toml").read_text())["resource"]
                      for folder in ("serve", "serve-plugged", "serve-sections", "serve-second")}
        for lane, (folder, specs, _) in spools.LANES.items():
            for other, rows in registries.items():
                registered = [row["spec"] for row in rows if row["slug"] == lane and row["root"].endswith("/" + lane)]
                self.assertEqual(registered, ["docs/specs/%s.spec.html" % spec for spec in specs] if other == folder else [],
                                 (lane, other))

    def test_second_service_holds_served_storage_service_at_its_own_root(self):
        """served-pages #acceptance-served-storage-service: the second service's served-storage-service spool is its
        own, so a comment the first service stores never shows on the second service's spec; served-storage-return and
        served-storage-stored stay at the review service's roots, one spool on two services."""
        def roots(folder):
            rows = tomllib.loads((self.site.root / folder / "registry.toml").read_text())["resource"]
            return {row["slug"]: row["root"] for row in rows if row["slug"].startswith("served-storage-")}
        first, second = roots("serve"), roots("serve-second")
        self.assertEqual(set(second), {"served-storage-service", "served-storage-return", "served-storage-stored"})
        self.assertNotEqual(second["served-storage-service"], first["served-storage-service"])
        self.assertTrue(Path(second["served-storage-service"], spools.SPEC).is_file())
        for slug in ("served-storage-return", "served-storage-stored"):
            self.assertEqual(second[slug], first[slug], slug)

    def test_every_collection_is_a_criterion_start(self):
        """reset.sh: each collection is a criterion's start, named by its qa.toml nav hint. A lane starts at its
        service's review index, and a comment-layer collection may be proven by its browser test instead (below)."""
        hinted = started()
        lanes = set(spools.LANES)
        for folder in ("serve-plugged", "serve-sections"):
            lanes |= {row["slug"] for row in tomllib.loads((self.site.root / folder / "registry.toml").read_text())["resource"]}
        for collection in os.listdir(self.site.root / "site"):
            if collection not in lanes and collection not in panes.COLLECTIONS:
                self.assertIn(collection, hinted)

    def test_a_resets_wake_log_holds_only_its_own_wakes(self):
        """reset.sh's fake wake provider appends each woken spec to $QA_ROOT/wakes.log; a reset removes the log with the
        site, so after repeated resets on one QA_ROOT, as capture runs them, it holds no earlier reset's wake."""
        site = Site()  # its own site: its resets replace the shared site's collections
        self.addCleanup(site.tmp.cleanup)
        log = site.root / "wakes.log"
        send = tomllib.loads((site.root / "serve" / "providers" / "wake.toml").read_text())["send"]
        subprocess.run([part.replace("{artifact}", site.spec("mobile-handoff")) for part in send], check=True)
        self.assertEqual(log.read_text(), site.spec("mobile-handoff") + "\n")
        site.reset()
        self.assertFalse(log.exists())

    def test_every_pane_collection_has_its_host_pages_and_hosted_threads(self):
        """comment-layer collections (panes.py): host pages naming this reset's project and the collection's scope, and
        hosted threads in the service state only where seeded, a stage collection's in its evidence scope; navigated, as
        a page or the test shell's native page's scope, or proven by tests/runtime-comment-layer.mjs or
        tests/runtime-shell.mjs."""
        reset = (FIXTURE / "reset.sh").read_text()
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        hinted = started()
        browser_test = "".join((ROOT / "tests" / name).read_text() for name in ("runtime-comment-layer.mjs", "runtime-shell.mjs"))
        for collection, (hosted, _, _) in panes.COLLECTIONS.items():
            self.assertIn(collection, reset)
            project = next(row["project"] for row in rows if row["slug"] == collection)
            for page in ("pane.html", "pane-other.html"):
                text = (self.site.root / "site" / collection / "docs" / page).read_text(encoding="utf-8")
                self.assertIn('data-comment-subject="project/%s::docs/specs/report.spec.html#report-totals"' % project, text)
                self.assertIn('data-comment-scope="%s"' % collection, text)
                self.assertNotIn("{{", text)
            scope = panes.STAGE[collection][0] if collection in panes.STAGE else collection
            self.assertEqual((self.site.root / "serve" / "comments" / scope).is_dir(), hosted is not None, collection)
            self.assertTrue(collection in hinted or "'%s'" % collection in browser_test, collection)

    def test_handoff_drafts_are_the_start_browsers_own(self):
        """comment-layer #acceptance-host-handoff, -two; shell #acceptance-shell-handoff: each Given's drafts are seeded, never
        handed off, by the browser its qa.toml start point keeps: on a host page, the page's own reviewer; in the test
        shell with no-origin frames, the shell's browser id the comment UI page takes (shell.py /bare/)."""
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]

        def drafts(review):
            return sorted((th["ev"]["body"]["browser"], th["ev"]["body"]["author"], th["ev"]["body"]["text"])
                          for th in spool.fold_threads(spool.read_events(str(review))).values() if th["status"] == "draft")

        hosted = lambda collection: self.site.root / "serve" / "comments" / collection
        spec = lambda collection: self.site.spec(collection) + ".review"
        for collection, reviews, texts in (
                ("host-handoff", [hosted], ["Saved on the host page."]),
                ("host-handoff-two", [hosted, spec], ["Why the table headers?", "Round to whole units."])):
            me = json.loads(nav["acceptance-" + collection]["storage"]["local"]["spec-chat:reviewer"])
            self.assertEqual([d for review in reviews for d in drafts(review(collection))],
                             [(me["browser"], me["author"], text) for text in texts], collection)
        start = nav["acceptance-shell-handoff"]
        self.assertTrue(start["path"].startswith("{shell}/bare/"))
        self.assertEqual([d[0] for d in drafts(spec("shell-handoff"))], [start["storage"]["local"]["test-shell:browser"]])

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

    def folded(self, collection):
        """The collection's threads as the shared fold reads its spool (spool.py fold_threads): {id: thread}."""
        page = spools.SPEC_OF.get(collection, spools.SPEC)
        review = Path(self.site.spec(collection, page) + ".review")
        events = [{"name": name, "actor": actor, "body": json.loads((review / actor / name).read_text())}
                  for actor in ("human", "agent") if (review / actor).is_dir() for name in os.listdir(review / actor)]
        return spool.fold_threads(sorted(events, key=lambda e: e["name"]))

    def test_threads_collections_hold_their_criteria_givens(self):
        """threads.spec.html#acceptance and spec-page-design #acceptance-color-consistency Givens (spools.py THREADS): each
        collection's threads, in the order they began, with their messages' authors and their statuses."""
        def shape(collection):
            return [(th["ev"]["body"]["anchorId"], [m["actor"] if m["actor"] == "agent" else m["body"]["author"] for m in th["messages"]],
                     th["status"]) for th in self.folded(collection).values()]
        self.assertEqual(shape("threads-order"), [("filter-saved", ["Mango"], "pending"), ("context", ["Papaya"], "pending"),
                                                  ("filter-team", ["Mango"], "pending")])
        for collection in ("threads-sides", "threads-focus-reply"):
            self.assertEqual(shape(collection), [("filter-team", ["Papaya", "agent"], "acknowledged")])
        self.assertEqual(shape("threads-replies"), [("filter-team", ["Mango", "agent", "Mango", "agent"], "acknowledged"),
                                                    ("context", ["Papaya"], "pending")])
        # focus, focus-phone: the thread they open is the capture browser's own (qa.toml nav storage), as their mockup draws.
        self.assertEqual(shape("threads-focus"), [("filter-team", ["Papaya", "agent", "Papaya", "agent"], "acknowledged"),
                                                  ("context", ["Mango"], "pending")])
        self.assertEqual(shape("threads-pin"), [("filter-team", ["Mango", "agent"], "acknowledged")])
        self.assertEqual(shape("threads-chip"), [("filter-team", ["Mango"], "pending")])
        self.assertEqual(shape("threads-sent"), [("filter-team", ["Papaya"], "draft")])
        self.assertEqual(shape("threads-new"), [])
        self.assertEqual(shape("threads-seen"), [("filter-team", ["Mango"], "acknowledged")])
        self.assertEqual(shape("threads-resolved"), [("filter-team", ["Mango", "agent", "Mango"], "resolved")])
        self.assertEqual(shape("threads-waiting"), [("filter-team", ["Mango", "agent"], "acknowledged"),
                                                    ("context", ["Papaya", "agent", "Papaya"], "pending")])
        # waiting-root, on the QA-21 lane at Spec review: the waiting thread folds the reviewer's reply among the agent's,
        # and every message carries the stage it was written at.
        self.assertEqual(shape("qa-21"), [("filter-team", ["Papaya", "agent", "Papaya", "agent"], "acknowledged"),
                                          ("context", ["Papaya", "agent", "Papaya"], "pending")])
        self.assertEqual({m["body"].get("stage") for th in self.folded("qa-21").values() for m in th["messages"]}, {"Spec review"})
        self.assertEqual(shape("threads-question"), [("report-totals", ["Mango"], "pending")])
        for collection in ("threads-answer", "threads-answer-same"):
            self.assertEqual(shape(collection), [])
        self.assertEqual(sorted(th["status"] for th in self.folded("threads-colors").values()), ["acknowledged", "draft", "resolved"])
        # threads #acceptance-video-*: Report walkthrough shows its video, which each video collection holds; the chip
        # collection's one thread is on a box at one moment of it.
        for collection in ("threads-video", "threads-video-box"):
            self.assertEqual(shape(collection), [])
        self.assertEqual(shape("threads-video-chip"), [("walkthrough-video", ["Mango"], "pending")])
        root = next(iter(self.folded("threads-video-chip").values()))["ev"]["body"]
        self.assertEqual((root["target"], root["moment"], root["box"]), ({"type": "element", "key": "video[1]"}, 1.8, spools.VIDEO_BOX))
        video = (FIXTURE / "videos" / "round-desktop.webm").read_bytes()
        for collection in (*spools.VIDEO, *panes.VIDEO):
            self.assertEqual((self.site.root / "site" / collection / spools.VIDEO_FILE).read_bytes(), video, collection)
        self.assertIn('<video src="../walkthrough.webm"', Path(self.site.spec("threads-video", spools.REPORT_VIDEO)).read_text(encoding="utf-8"))
        self.assertIn('<video src="walkthrough.webm"', (self.site.root / "site" / "layer-video" / "docs" / "pane.html").read_text(encoding="utf-8"))
        # The question collections' Weekly report holds one open question, offering two answers.
        for collection in ("threads-question", "threads-answer", "threads-answer-same"):
            text = self.current(collection)
            self.assertEqual(re.findall(r'data-anchor="([^"]+)" data-spec-tbd>', text), ["export-format"], collection)
        # The chip collection's filter criterion sits under forty paragraphs.
        text = Path(self.site.spec("threads-chip", spools.LATER)).read_text(encoding="utf-8")
        self.assertLess(text.index('data-anchor="filler-40"'), text.index('data-anchor="filter-team"'))

    def test_agent_scan_batch_holds_the_rewritten_handed_off_comment(self):
        review = self.site.spec("agent-scan") + ".review"
        batch = spool.handoff_batch(review, set())
        self.assertEqual([name.split("-", 2)[1] for name in batch], ["comment", "handoff"])
        self.assertEqual(self.site.places("agent-scan")["qa-sentence"]["state"], "changed")

    def serve(self, site=None, registry="serve", ready="/qa-fixture/docs/specs/report.spec.html", state="state"):
        """The fixture service (serve.py) on a site, this class's by default, on its review registry, or review-plugged's
        ("serve-plugged", state "state-plugged"), as qa.toml declares them; returns its base url."""
        return self.start(site, registry, ready, state)[0]

    def start(self, site=None, registry="serve", ready="/qa-fixture/docs/specs/report.spec.html", state="state"):
        """serve, returning its base url and its process, stopped (SIGTERM) at cleanup unless stopped already."""
        site = site or self.site
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "HOME": str(site.root / "home"), "XDG_STATE_HOME": str(site.root / state),
               "XDG_CONFIG_HOME": str(site.root / "config")}
        env.pop("HERDR_SOCKET_PATH", None)
        # SIGINT at its default, not an ignore a background job of a non-interactive sh inherits, so the child's
        # Python takes it as KeyboardInterrupt.
        server = subprocess.Popen([sys.executable, str(FIXTURE / "serve.py"), str(site.root / registry / "registry.toml"), str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
        self.addCleanup(lambda: (server.terminate(), server.wait(timeout=10)))
        base = "http://127.0.0.1:%d" % port
        for _ in range(100):
            try:
                urllib.request.urlopen(base + ready).close()
                return base, server
            except OSError:
                time.sleep(0.1)
        self.fail("serve.py did not start")

    def jev(self, base, path, kinds=None, at=None, wait=20):
        """A service's Jev read of path, at base commit `at` when given, asked again until it lists items of kinds
        (every kind by default) and none of them is pending, or wait seconds pass: those items, and the whole answer."""
        query = urllib.parse.urlencode({"path": path, **({"base": at} if at else {})})
        until = time.monotonic() + wait
        while True:
            with urllib.request.urlopen(base + "/api/jev?" + query) as response:
                answer = json.load(response)
            items = [i for i in answer["items"] if kinds is None or i["kind"] in kinds]
            if items and all(i["state"] != "pending" for i in items) or time.monotonic() >= until:
                return items, answer
            time.sleep(0.1)

    def told(self, base, query):
        """The stages an events read tells its page (X-Spec-Chat-Stages), decoded, and its events, or None."""
        with urllib.request.urlopen(base + "/api/events?" + query) as response:
            events, header = json.load(response), response.headers["X-Spec-Chat-Stages"]
        return (header and json.loads(urllib.parse.unquote(header))), events

    def test_stage_collections_tell_each_lanes_stage(self):
        """threads #acceptance-stage-tag, -kept, -reply, -lane, -none Givens: the review service, with no provider, tells
        each stage lane's page the stage spools.py STAGES names (serve.py); QA-18's comment was written at Spec review,
        QA-19's reply at Code review. Each stage host page's first item is in a scope row, which holds no spec, its anchor
        naming QA-20, at QA, or no lane."""
        base = self.serve()
        spool_read = lambda lane: "dir=" + urllib.parse.quote(lane + "/" + spools.LATER + ".review")  # noqa: E731
        for lane, stage in (("qa-17", "Spec review"), ("qa-18", "Code review"), ("qa-19", "Merge ask"), ("qa-21", "Spec review")):
            self.assertEqual(self.told(base, spool_read(lane))[0], {"lane": lane, "stages": {lane: stage}}, lane)
        self.assertEqual([(e["body"]["event"], e["body"].get("stage")) for e in self.told(base, spool_read("qa-19"))[1]],
                         [("comment", "Spec review"), ("handoff", None), ("reply", "Code review")])
        self.assertEqual(self.told(base, spool_read("qa-3"))[0], {"lane": "qa-3", "stages": {}},
                         "a lane STAGES names no stage for: its lane alone, which a comment records")
        registry = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        for collection, (scope, lane) in panes.STAGE.items():
            page = (self.site.root / "site" / collection / "docs" / "pane.html").read_text()
            item = re.search(r'<section class="item"[^>]*data-comment-scope="%s"[^>]*>' % scope, page).group(0)
            self.assertEqual(re.findall(r'data-comment-lane="([^"]*)"', item), [lane] if lane else [], collection)
            self.assertEqual([row.get("spec") for row in registry if row["slug"] == scope], [None], "a scope row")
            query = "scope=%s&subject=qa-run:qa:9" % scope + ("&lane=" + lane if lane else "")
            self.assertEqual(self.told(base, query)[0], {"lane": None, "stages": {"qa-20": "QA"}} if lane else None)

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
        """review-index #acceptance-index-threads, -threads-none, -threads-refresh, -index-stale Givens: QA-1 holds
        every status (3 open, 1 resolved) on its report spec and none on the others; QA-2 one open thread, and a
        spec registered from a removed worktree, shown as its path with the word Stale and no link."""
        import re

        base = self.serve()
        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        lanes = dict(re.findall(r'<section class="lane" aria-label="([^"]+)"[^>]*>(.*?)</section>', body, re.S))
        # QA-13 to QA-15 are the mockup lanes (MockupCollections below), QA-16 the unreadable spool's, QA-17 to QA-19 and
        # QA-21 the stage lanes (test_stage_collections_tell_each_lanes_stage).
        self.assertEqual([key for key in lanes if key not in ("QA-13", "QA-14", "QA-15", "QA-16", "QA-17", "QA-18", "QA-19",
                                                               "QA-21")],
                         ["QA-1", "QA-2", "QA-4", "QA-5", "QA-6", "QA-7", "QA-3"])

        def rows(lane):
            return {title: [gate] + [word for word in meta if word != "Changed"] for title, (gate, meta) in self.rows(lane).items()}

        # Gate words (review-index #index-gates): the report specs hold open TBDs, the others none and no thread.
        quiet = {"Onboarding": ["Ready to accept"], "Report filters": ["Ready to accept"], "Report sorting": ["Decision open"]}
        self.assertEqual(rows(lanes["QA-1"]), {**quiet, "Weekly report": ["Decision open", "3 open", "1 resolved"]})
        stale = "docs/specs/archive.spec.html"
        self.assertEqual(rows(lanes["QA-2"]), {**quiet, "Weekly report": ["Decision open", "1 open"], stale: ["Stale"]})
        links = re.findall(r"<a [^>]*>([^<]*)</a>", lanes["QA-2"])
        self.assertEqual(sorted(links), ["Onboarding", "Report filters", "Report sorting", "Weekly report"])
        self.assertIn(">4 need you<", lanes["QA-2"])  # a stale row has no gate and counts toward nothing
        statuses = {th["id"]: th["status"] for th in spool.fold_threads(self.site.events("qa-1") + [
            {"name": name, "actor": "agent", "body": json.loads((Path(self.site.spec("qa-1") + ".review") / "agent" / name).read_text())}
            for name in os.listdir(self.site.spec("qa-1") + ".review/agent")]).values()}
        self.assertEqual(statuses, {"qa-draft": "draft", "qa-handed": "pending", "qa-replied": "acknowledged",
                                    "qa-resolved": "resolved"})

    @staticmethod
    def rows(lane):
        """{title: (gate word or None, [meta words])} of one lane's spec rows (review-index #index-entry-title)."""
        import html
        import re

        result = {}
        for item in re.findall(r'<li class="spec[^"]*"[^>]*>(.*?)</li>', lane, re.S):
            title = html.unescape(re.search(r'<(?:a|span) class="title"[^>]*>([^<]*)<', item).group(1))
            gate = re.search(r'<span class="gate[^"]*">([^<]*)</span>', item)
            meta = re.search(r'<span class="meta">([^<]*)</span>', item)
            result[title] = (gate and html.unescape(gate.group(1)), meta.group(1).split(" · ") if meta else [])
        return result

    @classmethod
    def lanes(cls, base):
        """{lane key: (part, count, proof link address or None, {title: gate word})} of the index at base."""
        import html
        import re

        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        result = {}
        for part, lanes in re.findall(r'<nav class="part" aria-label="([^"]+)"[^>]*>(.*?)</nav>', body, re.S):
            for key, lane in re.findall(r'<section class="lane" aria-label="([^"]+)"[^>]*>(.*?)</section>', lanes, re.S):
                proof = re.search(r'<a class="proof" href="([^"]*)"', lane)
                result[key] = (part, re.search(r'<span class="count">([^<]*)</span>', lane).group(1),
                               proof and html.unescape(proof.group(1)),
                               {title: gate for title, (gate, _) in cls.rows(lane).items()})
        return result

    def test_index_gate_lanes_read_as_their_criteria_say(self):
        """review-index #acceptance-index-gate-decision, -gate-reply, -gate-draft, -gate-agent, -gate-ready,
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
                    "QA-4": ("Needs you", "1 needs you", None, {"Report filters": "Ready to accept"}),
                    "QA-5": ("Needs you", "1 needs you", None, {"Report filters": "Ready to accept"}),
                    "QA-6": ("Needs you", "1 needs you", None, {"Report filters": "Reply waiting"}),
                    "QA-7": ("Needs you", "1 needs you", None, {"Report filters": "Draft not sent"})}
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

    def test_new_screen_and_unreadable_lanes_read_as_their_criteria_say(self):
        """review-index #acceptance-index-gate-sent-new-mockup, #acceptance-index-stale-unreadable Givens, on the review
        service, which has no evidence provider: QA-15's Report page was pressed Accept spec naming 01 Report page and 02
        Team page at the versions their pages show, and its set has since gained 03 Export dialog; QA-16's one spec,
        Report filters, has a spool holding a link, so its review state cannot be read."""
        site = Site()  # its own site: it removes the screen the hand-off does not name
        self.addCleanup(site.tmp.cleanup)
        base = self.serve(site)
        lanes = self.lanes(base)
        self.assertEqual(lanes["QA-15"], ("Needs you", "1 needs you", None, {"Report page": "Ready to accept"}))
        self.assertEqual(lanes["QA-16"], ("In progress", "Nothing needs you", None,
                                          {spools.LATER.replace(os.sep, "/"): "Stale"}))
        with urllib.request.urlopen(base + "/") as response:
            self.assertNotIn('href="/qa-16/', response.read().decode())
        # The hand-off is otherwise whole: with the set back to the screens it names, it reads Accept spec sent.
        os.remove(site.root / "site" / "qa-15" / MockupCollections.SET / "03-export-dialog.mockup.html")
        self.assertEqual(self.lanes(base)["QA-15"], ("In progress", "Nothing needs you", None,
                                                     {"Report page": "Accept spec sent"}))

    def test_sections_index_holds_one_lane_per_part_and_settles_the_rest(self):
        """review-index #acceptance-index-sections Given, on review-sections: QA-11 a lane holding a spec with an open
        decision, QA-12 a lane whose specs are all with the agent, and the main collection under a slug that is no lane
        key. A registry serves its rows only, so no spec is served without a row (tests/review-serve-multi.py proves
        that case)."""
        import re

        base = self.serve(registry="serve-sections", ready="/qa-11/docs/specs/report.spec.html", state="state-sections")
        self.assertEqual(self.lanes(base), {
            "QA-11": ("Needs you", "1 needs you", None, {"Weekly report": "Decision open"}),
            "QA-12": ("In progress", "Nothing needs you", None, {"Report filters": "With the agent"}),
        })
        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        settled = re.findall(r"<details\b[^>]*>(.*?)</details>", body, re.S)
        self.assertEqual(len(settled), 1)
        self.assertIn('<span>Settled (<span data-find-count="items">4</span>)</span>', settled[0])
        self.assertEqual(sorted(title for title, _ in self.rows(settled[0]).items()),
                         ["Onboarding", "Report filters", "Report sorting", "Weekly report"])
        self.assertEqual(re.findall(r'<a class="title" href="/([^/"]+)/', settled[0]), ["main"] * 4)

    def test_bar_dirty_spec_differs_from_its_head(self):
        """compared-range #acceptance-bar-dirty Given: bar-dirty's report spec holds an uncommitted edit, so its baseline
        reads dirty; qa-fixture's does not."""
        base = self.serve()

        def baseline(collection):
            query = urllib.parse.urlencode({"path": "%s/docs/specs/report.spec.html" % collection})
            with urllib.request.urlopen(base + "/api/baseline?" + query) as response:
                return json.load(response)

        dirty, clean = baseline("bar-dirty"), baseline("qa-fixture")
        self.assertEqual((dirty["dirty"], clean["dirty"]), (True, False))
        self.assertEqual(dirty["head"], clean["head"])
        self.assertIn(spools.INSERTED, self.current("bar-dirty"))

    def test_plugged_index_reads_the_declared_review_facts(self):
        """review-index #acceptance-index-gate-accepted, #acceptance-index-proof, #acceptance-index-marks,
        #acceptance-index-title-column, #acceptance-index-focus Givens, on review-plugged: once the evidence service
        runs, reset.sh declares its review facts answer and the provider file naming it; the provider names QA-8's
        Report filters accepted at the head commit, and a proof bundle for QA-9, whose page it serves. QA-10 holds an
        open decision, a spec with the agent, and a long title waiting on acceptance."""
        import re

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
        long_title = ("Weekly report export to spreadsheets, with per-team totals, saved filters, and the chosen sort kept"
                      " between visits")
        qa10 = ("Needs you", "2 need you", None, {"Weekly report": "Decision open", long_title: "Ready to accept",
                                                  "Report filters": "With the agent"})
        site.reset(QA_STORY="story-review-index", QA_URL_EVIDENCE=url)
        head = subprocess.check_output(["git", "-C", str(site.root / "site" / "qa-8"), "rev-parse", "HEAD"], text=True).strip()
        bundle = url + "/bundles/qa-proof/"
        self.assertEqual(self.lanes(base), {
            "QA-8": ("In progress", "Nothing needs you", None, {"Report filters": "Accepted " + head[:7]}),
            "QA-9": ("Needs you", "1 needs you", bundle, {"Report filters": "Ready to accept"}),
            "QA-10": qa10,
        })
        # #acceptance-index-marks: the decision, the spec with the agent, and the accepted spec lead with the attention,
        # running, and pass marks, the set's own (review-serve-multi.py checks each mark's shape).
        with urllib.request.urlopen(base + "/") as response:
            body = response.read().decode()
        marks = DESIGN_SET[1]
        for title, mark in (("Weekly report", "attention"), ("Report filters", "running"), ("Report filters", "pass")):
            self.assertRegex(body, r'<li class="spec"[^>]*><span class="mark">%s</span><a class="title" href="[^"]*">%s</a>'
                             % (re.escape(marks[mark]), title))
        with urllib.request.urlopen(bundle) as response:
            self.assertIn(b"<title>Evidence bundle qa-proof</title>", response.read())
        # threads #stage-tag-source: a lane page's events read asks the same review facts, which name QA-10's stage and
        # none of QA-8's, whose read still tells its lane. A read whose ask outlasts its deadline tells no stage, so it is
        # read again.
        read = lambda lane: self.told(base, "dir=" + urllib.parse.quote(lane + "/" + spools.LATER + ".review"))[0]  # noqa: E731
        self.assertTrue(any(read("qa-10") == {"lane": "qa-10", "stages": {"qa-10": "Spec review"}} for _ in range(20)))
        self.assertEqual(read("qa-8"), {"lane": "qa-8", "stages": {}})

    def test_stale_page_edit_lands_on_save_and_the_mark_follows(self):
        self.assertEqual(self.current("stale-page"), HEAD)
        self.assertFalse(os.path.exists(self.site.spec("stale-page") + ".review"))
        base = self.serve()
        with urllib.request.urlopen(base + "/stale-page/docs/specs/report.spec.html") as response:
            etag = response.headers["ETag"].strip('"')
            shown = response.read()
        self.assertIn(b'<h1 data-anchor="title">Weekly report</h1>', shown)  # the head spec text
        self.assertEqual(etag, hashlib.sha256(shown).hexdigest())  # of the served body (review-service #index-http-caching)
        body = {"id": "qa-stale", "event": "comment", "actor": "human", "createdAt": "2026-10-01T00:00:00Z",
                "schemaVersion": 1, "browser": "qa-capture", "author": "Lychee", "version": etag,
                "anchorId": "report-export", "target": {"type": "text", "key": spools.KEY},
                "quote": spools.SENTENCE, "text": "Name the columns."}
        self.post(base, "stale-page", body)
        self.assertIn(spools.INSERTED, self.current("stale-page"))
        mark = next(e["place"] for e in self.served_events(base, "stale-page") if e["body"]["id"] == "qa-stale")
        self.assertEqual((mark["state"], mark["anchorId"], mark["quote"]), ("kept", "report-export", spools.SENTENCE))

    def test_name_reply_agent_answers_a_saved_comment_so_a_reply_is_possible(self):
        """acceptance-name: the fixture agent answers a saved comment once, so its thread waits on its reviewer, who replies
        in the focused thread (threads #focus)."""
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

    def test_typed_kept_starts_on_a_spec_under_review_with_no_thread(self):
        """review-state #acceptance-typed-kept Given (qa.toml nav): typed-kept's report spec is under review, served with
        its review index at the service's root, the back link's address, and holds no thread, so the comment typed and
        left unsaved is the one draft its page shows on coming back."""
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        self.assertEqual(nav["acceptance-typed-kept"]["path"], "{review}/typed-kept/docs/specs/report.spec.html")
        self.assertFalse(os.path.exists(self.site.spec("typed-kept") + ".review"))
        base = self.serve()
        self.assertEqual(self.served_events(base, "typed-kept"), [])
        with urllib.request.urlopen(base + "/") as response:
            self.assertIn("/typed-kept/docs/specs/report.spec.html", response.read().decode())


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
        self.assertEqual([r["slug"] for r in rows if r.get("spec") == print_spec], ["coverage-undeclared"])
        base = self.serve()

        def items(path):
            return self.jev(base, path, at=rows[0]["base"])[0]

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
            return self.jev(base, "%s/docs/specs/%s.spec.html" % (collection, spec), ("rule", "candidate"))

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

    def test_rule_source_links_the_lanes_own_home_or_is_plain_text(self):
        """project-rules #acceptance-source-own-lane, #acceptance-source-main-only Givens (qa.toml nav): qa-fixture's
        lane serves the onboarding rule's home, as another lane of its project does, so its rule card's source is its own
        lane's page; rule-main-only, its own project, registers its report spec alone, and its origin/main holds the home,
        unchanged by the lane, so the rule is main's (#q-rule-source), no row of its project serves the home, and the
        card's source is plain text (#card-source)."""
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        home = "docs/specs/onboarding.spec.html"
        base = self.serve()
        for criterion, collection, served in (("acceptance-source-own-lane", "qa-fixture", True),
                                              ("acceptance-source-main-only", "rule-main-only", False)):
            page = collection + "/docs/specs/report.spec.html"
            self.assertEqual(nav[criterion]["path"], "{review}/" + page)
            project = next(r["project"] for r in rows if r["slug"] == collection)
            serving = {r["slug"] for r in rows if r.get("project") == project and r.get("spec") == home}
            items, _ = self.jev(base, page, ("rule",))
            self.assertEqual([(i["name"], i["label"], i["served"]) for i in items], [(RULE_NAME, "missed", served)])
            if served:
                self.assertIn(collection, serving)
                self.assertTrue(serving - {collection}, "another lane of the project serves the home")
                self.assertEqual(items[0]["target"].lstrip("/"), "%s/%s#rule-onboarding" % (collection, home))
            else:
                self.assertEqual(serving, set())
                self.assertEqual([r["spec"] for r in rows if r["slug"] == collection], ["docs/specs/report.spec.html"])
                root = self.site.root / "site" / collection
                git = lambda *args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True).stdout
                self.assertEqual(git("show", "origin/main:" + home), (root / home).read_bytes())

    def test_convention_no_candidate_lists_no_candidate_for_a_design_system_convention(self):
        """project-rules #acceptance-convention-no-candidate Given (qa.toml nav): convention-no-candidate's onboarding
        spec holds, unmarked, a criterion saying every loading state uses the design system's shared skeleton, which the
        fixture's provider reads as this feature (serve.py), so its settled read lists no candidate; the criterion
        verifies its story, so no coverage gap shows on it either."""
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        page = "convention-no-candidate/docs/specs/onboarding.spec.html"
        self.assertEqual(nav["acceptance-convention-no-candidate"]["path"], "{review}/" + page)
        anchor, story, criterion = spools.CONVENTION
        self.assertIn("loading state uses the design system's shared skeleton", criterion)
        text = (self.site.root / "site" / page).read_text(encoding="utf-8")
        tag = re.search(r'<p [^>]*data-anchor="%s"[^>]*>' % anchor, text).group(0)
        self.assertIn("data-acceptance-criterion", tag)
        self.assertNotIn("data-project-rule", tag)
        self.assertIn(criterion, text)
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        row = next(r for r in rows if r["slug"] == "convention-no-candidate" and r["spec"].endswith("onboarding.spec.html"))
        base = self.serve()
        items, _ = self.jev(base, page, at=row["base"])
        self.assertEqual([i for i in items if i["state"] == "pending"], [])
        self.assertEqual([i for i in items if i["kind"] == "candidate"], [])
        self.assertEqual([i["label"] for i in items if i["kind"] == "coverage" and i["id"] == story + "::" + anchor],
                         ["verifies"])

    def held(self, collection, criteria, site=None):
        """A held collection's report spec (spools.py HELD) on a site, this class's by default, the page of these
        criteria (qa.toml nav), holds its own sentence, which no other report spec holds; returns its seconds."""
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        page = collection + "/docs/specs/report.spec.html"
        for criterion in criteria:
            self.assertEqual(nav[criterion]["path"], "{review}/" + page)
        anchor, sentence, seconds = spools.HELD[collection]
        self.assertIn('<p data-anchor="%s">%s' % (anchor, sentence), self.current(collection, site))
        for other in ("qa-fixture", *(c for c in spools.HELD if c != collection)):
            self.assertNotIn(sentence, self.current(other, site))
        return seconds

    def rule_read(self, base, collection, wait=0):
        """The rule items of a service's Jev read of the collection's report spec: one read, or with wait the settled
        one."""
        items, _ = self.jev(base, collection + "/docs/specs/report.spec.html", ("rule",), wait=wait)
        return [(i["state"], i.get("escalated"), i["name"], i.get("label")) for i in items]

    def test_rule_pending_holds_its_rule_check_until_the_service_stops(self):
        """project-rules #acceptance-pending, #acceptance-pending-motion Givens (qa.toml nav): rule-pending's report
        spec gains its held sentence (spools.py HELD), whose rule check the fixture's general LLM holds unanswered
        (serve.py), so its read keeps the rule pending, escalated to the general LLM, while another collection's report
        spec settles: the hold takes one of the LLM's runs. A stop ends it with no record, interrupted (SIGINT, its Jev
        stopping) or terminated (SIGTERM, at cleanup), so a restart holds it again and no process is left."""
        seconds = self.held("rule-pending", ("acceptance-pending", "acceptance-pending-motion"))
        self.assertIsNone(seconds)
        pending = [("pending", True, RULE_NAME, None)]
        base, server = self.start()
        self.assertEqual(self.rule_read(base, "rule-pending"), pending)
        self.assertEqual(self.rule_read(base, "qa-fixture", wait=20), [("label", None, RULE_NAME, "missed")])
        self.assertEqual(self.rule_read(base, "rule-pending"), pending)
        server.send_signal(signal.SIGINT)
        server.wait(timeout=10)
        base, _ = self.start()
        self.assertEqual(self.rule_read(base, "rule-pending"), pending)
        time.sleep(1)
        self.assertEqual(self.rule_read(base, "rule-pending"), pending)

    def test_rule_answer_answers_its_rule_check_after_its_seconds(self):
        """project-rules #acceptance-pending-answer Given (qa.toml nav): rule-answer's report spec gains its held
        sentence (spools.py HELD), whose rule check the fixture's general LLM answers its seconds after the first ask
        (serve.py), so the read that asks lists the rule pending, escalated to the general LLM, and a read within those
        seconds and a little more lists its marker, missed, as on any report spec of the project. The answer is a
        record the state keeps, yet each reset's sentence is its own, so after a reset and restart on the same state,
        as capture reuses one, the first read is pending again."""
        site = Site()  # its own site: its resets replace the shared site's collections
        self.addCleanup(site.tmp.cleanup)
        seconds = self.held("rule-answer", ("acceptance-pending-answer",), site)
        pending, missed = [("pending", True, RULE_NAME, None)], [("label", None, RULE_NAME, "missed")]
        base, server = self.start(site)
        asked = time.monotonic()
        self.assertEqual(self.rule_read(base, "rule-answer"), pending)
        self.assertEqual(self.rule_read(base, "rule-answer", wait=seconds + 5), missed)
        self.assertGreaterEqual(time.monotonic() - asked, seconds)
        server.terminate()
        server.wait(timeout=10)
        before = self.current("rule-answer", site)
        site.reset()
        self.assertNotEqual(self.current("rule-answer", site), before)
        self.held("rule-answer", ("acceptance-pending-answer",), site)
        base, _ = self.start(site)
        asked = time.monotonic()
        self.assertEqual(self.rule_read(base, "rule-answer"), pending)
        self.assertEqual(self.rule_read(base, "rule-answer", wait=seconds + 5), missed)
        self.assertGreaterEqual(time.monotonic() - asked, seconds)


class MockupCollections(unittest.TestCase):
    """Mockup collections (reset.sh mockups, spools.py SET_COLLECTIONS): the Givens of mockups.spec.html#acceptance and
    prompt-first-shaping #acceptance-mockup-*, on the real service. Report page's mockup set holds 01 Report page,
    changed since the base, 02 Team page, unchanged, and 03 Export dialog, new; its screens list the repo's fixture data
    in the pinned design set's tokens."""

    SET = "docs/specs/mockups/report-page/"
    PAGES = ("01-report-page.mockup.html", "02-team-page.mockup.html", "03-export-dialog.mockup.html")
    serve, start = ReviewStateCollections.serve, ReviewStateCollections.start

    @classmethod
    def setUpClass(cls):
        cls.site = Site()
        reset = (FIXTURE / "reset.sh").read_text()
        cls.collections = re.search(r'^mockups="([^"]*)"', reset, re.M).group(1).split() + [
            lane for lane, (_, specs, _) in spools.LANES.items() if specs == ("report-page",)]

    @classmethod
    def tearDownClass(cls):
        cls.site.tmp.cleanup()

    def get(self, base, path, method="GET"):
        request = urllib.request.Request(base + path, method=method)
        with urllib.request.urlopen(request) as response:
            return response.headers, response.read()

    def threads(self, collection, page):
        review = str(self.site.root / "site" / collection / page) + ".review"
        return spool.fold_threads(spool.read_events(review)) if os.path.isdir(review) else {}

    def test_every_mockup_collection_registers_report_page_alone_and_is_navigated(self):
        rows = tomllib.loads((self.site.root / "serve" / "registry.toml").read_text())["resource"]
        nav = tomllib.loads((ROOT / "qa.toml").read_text())["nav"]
        hinted = {hint["path"].split("/")[1] for hint in nav.values() if "path" in hint}
        # The prototype collections are review-prototype's (test_prototype_collections_are_served_private_on_loopback).
        self.assertEqual((set(spools.SET_COLLECTIONS) | set(spools.WATCHED)) - set(self.collections) - set(spools.PROTOTYPES), set())
        for collection in self.collections:
            self.assertEqual([r.get("spec") for r in rows if r["slug"] == collection], ["docs/specs/report-page.spec.html"])
            # A lane is reached from its service's review index ("{review}/"), any other from its own page.
            self.assertIn("" if collection in spools.LANES else collection, hinted)
        # No other collection registers it, so no other page shows its chips or its mockups' threads.
        self.assertEqual({r["slug"] for r in rows if r.get("spec", "").endswith("report-page.spec.html")}, set(self.collections))

    def test_mockup_set_compares_with_the_spec_rows_base(self):
        """mockups #acceptance-before, -after: 01 changed since the base; #acceptance-unchanged: 02 the same;
        #acceptance-new: 03 absent at it; the spec page changed too (prompt-first-shaping #acceptance-mockup-seed)."""
        base = self.serve()
        root = self.site.root / "site" / "mockup-view"

        def at_base(page):
            query = urllib.parse.urlencode({"path": "mockup-view/" + page})
            return json.loads(self.get(base, "/api/baseline?" + query)[1])["html"]

        current = {name: (root / self.SET / name).read_text() for name in self.PAGES}
        changed, same, new = (at_base(self.SET + name) for name in self.PAGES)
        self.assertTrue(changed and changed != current[self.PAGES[0]])
        self.assertNotIn("Export CSV", changed)
        self.assertEqual(same, current[self.PAGES[1]])
        self.assertIsNone(new)
        spec = at_base("docs/specs/report-page.spec.html")
        self.assertTrue(spec and spec != (root / "docs/specs/report-page.spec.html").read_text())

    def test_set_names_its_screens_and_the_criteria_they_illustrate(self):
        """mockups #acceptance-chip, -chip-open: page-teams is illustrated by Report page's frames, page-export by its
        Export CSV button, page-onboarding by no mockup; prompt-first-shaping #acceptance-mockup-seed: the new Export
        dialog illustrates the criterion it shows."""
        base = self.serve()
        query = urllib.parse.urlencode({"path": "mockup-view/docs/specs/report-page.spec.html"})
        mockups = json.loads(self.get(base, "/api/mockups?" + query)[1])["mockups"]
        self.assertEqual([(m["page"], m["screen"], m["open"]) for m in mockups], [
            ("mockups/report-page/" + name, screen, 0)
            for name, screen in zip(self.PAGES, ("Report page", "Team page", "Export dialog"))])
        report, team, export = (m["illustrates"] for m in mockups)
        self.assertEqual(report["page-teams"], ["desktop", "desktop-cards", "phone", "phone-cards"])
        self.assertEqual(report["page-export"], ["desktop-export", "phone-export"])
        self.assertIn("page-team", team)
        self.assertIn("page-export-file", export)
        self.assertNotIn("page-onboarding", {anchor for m in mockups for anchor in m["illustrates"]})
        for name in self.PAGES:
            headers, _ = self.get(base, "/mockup-view/" + self.SET + name)
            self.assertTrue(headers["ETag"])
        for avatar in ("payments.svg", "search.svg", "mobile.svg"):
            self.assertIn(b"<svg", self.get(base, "/mockup-view/" + self.SET + avatar)[1])

    def test_seeded_threads_hold_each_criterions_given(self):
        """The Givens of mockups #acceptance-handoff (a draft on the Export CSV button), -reply (an answered hand-off),
        -after (a thread on the changed mockup), -accept-waits (the spec's threads resolved, one open on the mockup),
        -accept-offered (all resolved), -index-counts (two resolved on the spec, one open on the mockup), -index-gate (no
        thread on the spec, the mockup's ending with the agent's reply), -directions-accept (the spec's thread resolved);
        every other mockup collection has none."""
        mockup = spools.REPORT_MOCKUP
        expected = {
            "mockup-handoff": ({}, {"qa-export": ("draft", "desktop-export")}),
            "mockup-mark-follows": ({}, {"qa-export": ("pending", "desktop-export")}),
            "mockup-reply": ({}, {"qa-card": ("acknowledged", "desktop-card-payments")}),
            "mockup-after": ({}, {"qa-card": ("pending", "desktop-card-search")}),
            "mockup-accept-waits": ({"qa-teams": ("resolved", "page-teams")}, {"qa-card": ("pending", "desktop-card-search")}),
            "mockup-accept-offered": ({"qa-teams": ("resolved", "page-teams")}, {"qa-card": ("resolved", "desktop-card-search")}),
            "qa-13": ({"qa-teams": ("resolved", "page-teams"), "qa-export": ("resolved", "page-export")},
                      {"qa-card": ("pending", "desktop-card-search")}),
            "qa-14": ({}, {"qa-card": ("acknowledged", "desktop-card-payments")}),
            "mockup-question-accept": ({"qa-teams": ("resolved", "page-teams")}, {}),
            "directions-drawn": ({"qa-teams": ("resolved", "page-teams")}, {}),
        }
        for collection in self.collections:
            got = tuple({ident: (th["status"], th["ev"]["body"]["anchorId"]) for ident, th in self.threads(collection, page).items()}
                        for page in (spools.REPORT_PAGE, mockup))
            self.assertEqual(got, expected.get(collection, ({}, {})), collection)
        last = self.threads("qa-14", mockup)["qa-card"]["events"][-1]
        self.assertEqual((last["actor"], last["body"]["event"]), ("agent", "reply"))
        # The page's own browser identity is Papaya's in mockup-handoff's start point, so the draft is its own.
        stored = json.loads(tomllib.loads((ROOT / "qa.toml").read_text())["nav"]["acceptance-handoff"]["storage"]["local"]["spec-chat:reviewer"])
        draft = self.threads("mockup-handoff", mockup)["qa-export"]["ev"]["body"]
        self.assertEqual(stored, {"browser": draft["browser"], "author": draft["author"]})
        # The service reads the mockup's open threads with its set (#accept).
        base = self.serve()
        for collection, open_threads in (("mockup-accept-waits", 1), ("mockup-accept-offered", 0), ("qa-13", 1), ("qa-14", 1)):
            query = urllib.parse.urlencode({"path": collection + "/docs/specs/report-page.spec.html"})
            self.assertEqual(json.loads(self.get(base, "/api/mockups?" + query)[1])["mockups"][0]["open"], open_threads, collection)

    def assert_valid_set(self, collection, pages):
        """At production fidelity, as the validator checks each mockup the change adds (mockups #acceptance-fidelity-rule):
        the collection's set as a whole, and each of `pages` of it as the change adds it."""
        validator = load("qa_validate_set", ROOT / "skill" / "shape-spec" / "scripts" / "validate-style.py")
        root = self.site.root / "site" / collection
        spec = root / "docs" / "specs" / "report-page.spec.html"
        anchors = set(re.findall(r'data-anchor="([^"]+)"', spec.read_text()))
        self.assertEqual(validator.mockup_problems(root, spec, anchors), [], collection)
        spec_page = validator.SpecParser()
        spec_page.feed(spec.read_text())
        spec_page.close()
        problems, warnings = validator.changed_mockup_problems(root, spec, "HEAD", validator.page_runtime(spec, spec_page.runtimes))
        ours = [os.path.basename(page) for page in pages]
        self.assertEqual([p for p in problems + warnings if any(name in p for name in ours)], [], collection)

    def test_walkthrough_collections_hold_each_criterions_given(self):
        """The Givens of mockups #acceptance-steps-bar to -chip-step (spools.py WALKTHROUGHS): Team panel walks three
        steps, its second reached by a button's press and illustrating Pressing a team's card, and Export flow six; a
        thread on the Payments card at the second step in mockup-step-pin and shell-mockup-step (shell
        #acceptance-shell-mockup-step); two open questions in mockup-questions, the
        first on that card at the second step offering two answers, and that one alone in mockup-question-answer, -words,
        -phone, and -accept; the service tells the spec page each screen's steps and open questions."""
        validator = load("qa_validate_walkthrough", ROOT / "skill" / "shape-spec" / "scripts" / "validate-style.py")
        base = self.serve()
        for collection, screens in spools.WALKTHROUGHS.items():
            root = self.site.root / "site" / collection
            query = urllib.parse.urlencode({"path": collection + "/docs/specs/report-page.spec.html"})
            told = {m["page"]: m for m in json.loads(self.get(base, "/api/mockups?" + query)[1])["mockups"]}
            for page, screen, count, questions in screens:
                with self.subTest(collection=collection, page=page):
                    path = root / page
                    parser = validator.MockupParser()
                    parser.feed(path.read_text())
                    self.assertEqual([step["name"] for step in parser.steps], [step[0] for step in spools.STEPS[:count]])
                    self.assertEqual(parser.steps[1]["by"], ["desktop-open", "phone-open"])
                    self.assertEqual([(q["name"], q["about"], q["step"]) for q in parser.questions], [
                        (re.search(r'data-anchor="([^"]+)"', q).group(1), re.search(r'data-about="([^"]+)"', q).group(1).split(),
                         re.search(r'data-step="([^"]+)"', q).group(1)) for q in questions])
                    entry = told["mockups/report-page/" + os.path.basename(page)]
                    self.assertEqual((entry["screen"], entry["questions"], entry["steps"]["page-team"]), (screen, len(questions), ["team-open"]))
            self.assert_valid_set(collection, [page for page, *_ in screens])
        for collection in ("mockup-step-pin", "shell-mockup-step"):
            pinned = self.threads(collection, spools.TEAM_PANEL)["qa-step"]
            self.assertEqual((pinned["status"], pinned["ev"]["body"]["anchorId"], pinned["spot"]["step"]), ("pending", "desktop-card-payments", "team-open"))
        for collection in set(spools.WALKTHROUGHS) - {"mockup-step-pin", "shell-mockup-step"}:
            self.assertEqual(self.threads(collection, spools.TEAM_PANEL), {}, collection)

    def test_screens_index_collection_holds_its_criteria_givens(self):
        """mockups #acceptance-screen-index to -none (qa.toml nav): screens-index is a prototype collection, its set
        three drawn screens and Weekly report, the prototype of the fixture's route, holding one open thread and one open
        question, the drawn ones neither, as the service tells its Screens index; Weekly report's spec has no set."""
        self.assertIn("screens-index", spools.PROTOTYPES)
        base = self.serve(registry="serve-prototype", ready="/screens-index/docs/specs/report-page.spec.html", state="state-prototype")
        told = lambda path: json.loads(self.get(base, "/api/mockups?" + urllib.parse.urlencode({"path": path}))[1])["mockups"]
        self.assertEqual([(m["screen"], m["route"], m["open"], m["questions"]) for m in told("screens-index/docs/specs/report-page.spec.html")], [
            ("Report page", None, 0, 0), ("Team page", None, 0, 0), ("Export dialog", None, 0, 0),
            ("Weekly report", "/reports/weekly", 1, 1)])
        self.assertEqual(told("prototype-own-chrome/docs/specs/report.spec.html"), [])

    def test_prototype_collections_are_served_private_on_loopback(self):
        """mockups #acceptance-prototype-root, #acceptance-prototype-own-chrome (qa.toml nav, review-prototype): the
        prototype collections are review-prototype's registry alone, which records no public base address, so each lane's
        prototype origin is <slug>-prototype.localhost; each row names where its lane's prototype answers, the
        fixture's product, but prototype-down's a closed port and prototype-own-chrome's that service itself, which
        also registers Report spec, the page its Report spec mockup frames."""
        site = Site()  # its own site: the service addresses are a reset's
        self.addCleanup(site.tmp.cleanup)
        site.reset(QA_URL_PROTOTYPE="http://127.0.0.1:7001", QA_URL_REVIEW_PROTOTYPE="http://127.0.0.1:7002")
        registry = tomllib.loads((site.root / "serve-prototype" / "registry.toml").read_text())
        self.assertNotIn("process", registry)
        rows = {(row["slug"], row["spec"]): row.get("prototype") for row in registry["resource"]}
        self.assertEqual({slug for slug, _ in rows}, set(spools.PROTOTYPES))
        for (slug, spec), prototype in rows.items():
            self.assertEqual(prototype, {"prototype-down": "http://127.0.0.1:9", "prototype-own-chrome": "http://127.0.0.1:7002"}
                             .get(slug, "http://127.0.0.1:7001"), slug)
        self.assertEqual(sorted(spec for slug, spec in rows if slug == "prototype-own-chrome"),
                         ["docs/specs/report-page.spec.html", "docs/specs/report.spec.html"])
        for folder in ("serve", "serve-plugged", "serve-sections", "serve-second"):
            slugs = {row["slug"] for row in tomllib.loads((site.root / folder / "registry.toml").read_text())["resource"]}
            self.assertEqual(slugs & set(spools.PROTOTYPES), set(), folder)
        hinted = started()
        for collection in spools.PROTOTYPES:
            self.assertIn(collection, hinted)
        mockup = (site.root / "site" / "prototype-own-chrome" / spools.OWN_CHROME_MOCKUP).read_text()
        self.assertIn('<meta name="mockup-prototype" content="/prototype-own-chrome/docs/specs/report.spec.html">', mockup)
        self.assertIn('data-select="[data-anchor=report-totals]"', mockup)
        self.assertIn('data-anchor="report-totals"', (site.root / "site" / "prototype-own-chrome" / "docs/specs/report.spec.html").read_text())

    def test_directions_collections_hold_their_criteria_givens(self):
        """mockups #acceptance-directions to -directions-accept, #acceptance-screen-index-directions (qa.toml nav): in the
        prototype directions collections Weekly report, a prototype collection's, names two directions, Team cards its own
        route and the agent's pick, Team table the fixture prototype's reports/weekly-table, with no open question and no
        round video, as mockups 05 draws none; in directions-drawn Team panel draws Team table's own desktop and phone
        frames, no thread is open, and the spec's one thread is resolved; the service tells each spec page the count, the
        prototype collections' review-prototype and directions-drawn's review, and the validator passes each mockup."""
        bases = {"directions-drawn": self.serve()}
        prototypes = self.serve(registry="serve-prototype", state="state-prototype",
                                ready="/%s/docs/specs/report-page.spec.html" % spools.PROTOTYPE_DIRECTIONS[0])
        told = lambda collection: [(m["screen"], m["route"], m["directions"], m["open"], m["questions"]) for m in json.loads(
            self.get(bases.get(collection, prototypes), "/api/mockups?" + urllib.parse.urlencode(
                {"path": collection + "/docs/specs/report-page.spec.html"}))[1])["mockups"]]
        drawn = [("Report page", None, 0, 0, 0), ("Team page", None, 0, 0, 0), ("Export dialog", None, 0, 0, 0)]
        for collection in spools.PROTOTYPE_DIRECTIONS:
            self.assertIn(collection, spools.PROTOTYPES)
            self.assertEqual(told(collection), drawn + [("Weekly report", "/reports/weekly", 2, 0, 0)], collection)
            text = (self.site.root / "site" / collection / spools.PROTOTYPE_MOCKUP).read_text()
            self.assertEqual(re.findall(r'<li data-direction="(\w+)"[^>]*?(?: data-route="([^"]+)")?[^>]*>([^<]+)</li>', text),
                             [("cards", "", "Team cards"), ("table", "/reports/weekly-table", "Team table")])
            self.assertFalse(os.path.exists(self.site.root / "site" / collection / (spools.PROTOTYPE_MOCKUP + ".review")), collection)
            self.assert_valid_set(collection, [spools.PROTOTYPE_MOCKUP])
        self.assertEqual(told("directions-drawn"), drawn + [("Team panel", None, 2, 0, 0)])
        text = (self.site.root / "site" / "directions-drawn" / spools.TEAM_PANEL).read_text()
        self.assertEqual(re.findall(r'data-mockup-frame="(\w+)" data-direction="table"', text), ["desktop", "phone"])
        self.assert_valid_set("directions-drawn", [spools.TEAM_PANEL])
        self.assertEqual({t["status"] for t in self.threads("directions-drawn", spools.REPORT_PAGE).values()}, {"resolved"})
        prototype = importlib.util.spec_from_file_location("qa_prototype", FIXTURE / "prototype.py")
        module = importlib.util.module_from_spec(prototype)
        prototype.loader.exec_module(module)
        table = module.page(module.TABLE_ROUTE).decode()
        self.assertEqual(re.findall(r"<tr><td>(\w+)</td>", table), ["Payments", "Search", "Mobile"])

    def test_kept_directions_collection_holds_the_round_after_an_answered_pick(self):
        """prompt-first-shaping #acceptance-directions-kept (qa.toml nav): in directions-kept, a prototype collection, the
        first round of Weekly report names the two directions; the reviewer's Pick of Team table, its answer on the
        directions list, is handed off and answered by the agent; the second round, the one served, is Team table's route
        alone, with no directions list, and no round video (mockups #directions-next)."""
        collection = spools.KEPT_DIRECTIONS[0]
        self.assertIn(collection, spools.PROTOTYPES)
        root = self.site.root / "site" / collection
        git = lambda *args: subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout
        first = git("show", "HEAD~1:" + spools.PROTOTYPE_MOCKUP)
        self.assertEqual(re.findall(r'<li data-direction="(\w+)"', first), ["cards", "table"])
        self.assertEqual(git("show", "HEAD:" + spools.PROTOTYPE_MOCKUP), (root / spools.PROTOTYPE_MOCKUP).read_text())
        kept = (root / spools.PROTOTYPE_MOCKUP).read_text()
        self.assertNotIn("data-mockup-directions", kept)
        self.assertNotIn("data-mockup-steps", kept)
        base = self.serve(registry="serve-prototype", ready="/%s/docs/specs/report-page.spec.html" % collection,
                          state="state-prototype")
        told = json.loads(self.get(base, "/api/mockups?" + urllib.parse.urlencode(
            {"path": collection + "/docs/specs/report-page.spec.html"}))[1])["mockups"]
        self.assertEqual([(m["screen"], m["route"], m["directions"], m["questions"]) for m in told][-1],
                         ("Weekly report", "/reports/weekly-table", 0, 0))
        thread = self.threads(collection, spools.PROTOTYPE_MOCKUP)["qa-pick"]
        body = thread["ev"]["body"]
        self.assertEqual((thread["status"], body["anchorId"], body["text"], body["target"], "version" in body),
                         ("acknowledged", "directions", "Team table", {"type": "element", "key": "li[2]"}, False))
        self.assertEqual([(e["actor"], e["body"]["event"]) for e in thread["events"]], [("human", "comment"), ("agent", "reply")])
        review = str(root / spools.PROTOTYPE_MOCKUP) + ".review"
        self.assertEqual([e["body"].get("events") for e in spool.read_events(review) if e["body"]["event"] == "handoff"], [["qa-pick"]])
        self.assertFalse(os.path.isdir(root / (spools.PROTOTYPE_MOCKUP + ".review") / "videos"))
        self.assert_valid_set(collection, [spools.PROTOTYPE_MOCKUP])

    def test_next_screen_collections_hold_their_criteria_givens(self):
        """mockups #acceptance-next-screen to -next-screen-one (qa.toml nav): mockup-view's set is three screens, Team page
        between the others; mockup-steps' holds Team panel, its three-step walkthrough, among others; next-screen-one's
        holds Report page alone, as the service tells each mockup page's spec page."""
        self.assertEqual(spools.ONE_SCREEN, ("next-screen-one",))
        base = self.serve()
        told = lambda collection: [m["screen"] for m in json.loads(self.get(base, "/api/mockups?" + urllib.parse.urlencode(
            {"path": collection + "/docs/specs/report-page.spec.html"}))[1])["mockups"]]
        self.assertEqual(told("mockup-view"), ["Report page", "Team page", "Export dialog"])
        self.assertEqual(told("mockup-steps"), ["Report page", "Team page", "Export dialog", "Team panel", "Export flow"])
        self.assertEqual(told("next-screen-one"), ["Report page"])

    def test_agent_edit_lands_once_the_open_page_checks_its_file(self):
        """mockups #acceptance-agent-edit, -mark-follows: the page loads the mockup, its first check of the file still
        reads it unchanged, and the agent's edit then lands, a new element above the Export CSV button, newer by
        Last-Modified; the comment on that button stays on it."""
        site = Site()  # its own site: the edits land once per reset
        self.addCleanup(site.tmp.cleanup)
        base = self.serve(site)
        for collection in spools.WATCHED:
            path = "/%s/%s" % (collection, spools.REPORT_MOCKUP.replace(os.sep, "/"))
            _, body = self.get(base, path)
            self.assertNotIn(spools.EXPORT_RULE.strip().encode(), body)
            loaded = self.get(base, path, "HEAD")[0]["Last-Modified"]
            until = time.monotonic() + 10  # it lands just after the check's answer is sent
            while spools.EXPORT_RULE.strip().encode() not in body and time.monotonic() < until:
                time.sleep(0.05)
                _, body = self.get(base, path)
            self.assertIn(spools.EXPORT_RULE.strip().encode(), body)
            edited = self.get(base, path, "HEAD")[0]["Last-Modified"]
            self.assertGreater(email.utils.parsedate_to_datetime(edited), email.utils.parsedate_to_datetime(loaded))
        query = "dir=" + urllib.parse.quote("/mockup-mark-follows/%s.review" % spools.REPORT_MOCKUP.replace(os.sep, "/"))
        events = json.loads(self.get(base, "/api/events?" + query)[1])
        place = next(e["place"] for e in events if e["body"]["id"] == "qa-export")
        self.assertEqual((place["state"], place["anchorId"]), ("kept", "desktop-export"))

    def test_mockups_are_a_valid_set_of_the_fixture_data_in_the_design_set(self):
        """The structural validator finds no problem in the set at the base or the head (mockups #checks);
        prompt-first-shaping #acceptance-mockup-content: both frames of each screen list the repo's fixture items
        (fixtures/weekly-totals.json), and every design token a mockup declares is the pinned set's, by name and value,
        its only other color the white on its action button; the repo pins the set (design-set.toml)."""
        validator = load("qa_validate_style", ROOT / "skill" / "shape-spec" / "scripts" / "validate-style.py")
        head = self.site.root / "site" / "mockup-view"
        for tree in (FIXTURE / "base", head):
            spec = tree / "docs" / "specs" / "report-page.spec.html"
            anchors = set(re.findall(r'data-anchor="([^"]+)"', spec.read_text()))
            self.assertEqual(validator.mockup_problems(tree, spec, anchors), [], tree)
        data = json.loads((FIXTURE / "base" / "fixtures" / "weekly-totals.json").read_text())
        this_week = {t["team"]: "{:,}".format(t["totals"][-1]) for t in data["teams"]}
        payments = ["{:,}".format(n) for n in data["teams"][0]["totals"]]
        token = re.compile(r"(--ui-[a-z0-9-]+):\s*([^;]+);")
        light, dark = REVIEW_SERVE._SYNC.tokens(DESIGN_SET[0])
        for name in self.PAGES:
            text = (head / self.SET / name).read_text()
            frames = re.findall(r"<section data-mockup-frame=\"(?:desktop|phone)\".*?</section>", text, re.S)
            self.assertEqual(len(frames), 2, name)
            for frame in frames:
                listed = ["Payments"] + payments if name == "02-team-page.mockup.html" else [
                    word for team in this_week.items() for word in team]
                for word in listed:
                    self.assertIn(">%s<" % word, frame, name)
            style = text.split("<style>", 1)[1].split("</style>", 1)[0]
            declared_light, declared_dark = (dict(token.findall(part))
                                             for part in style.split("@media (prefers-color-scheme: dark)"))
            self.assertTrue(declared_light and declared_dark, name)
            self.assertEqual({k: light.get(k) for k in declared_light}, declared_light, name)
            self.assertEqual({k: dark.get(k) for k in declared_dark}, declared_dark, name)
            self.assertEqual(set(re.findall(r"#[0-9a-fA-F]{3,6}\b", token.sub("", style))), {"#ffffff"}, name)
        self.assertEqual((head / "design-set.toml").read_text(), (ROOT / "design-set.toml").read_text())


if __name__ == "__main__":
    unittest.main()
