"""One fold per language (review-state #model-fold-one): spool.py's service fold gives the
page fold's threads, messages, statuses, and drafts for the same events."""

import importlib.util
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
RUNTIME = ASSETS / "viz" / "runtime.js"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spool = load("fold_spool", ASSETS / "spool.py")
spools = load("fold_qa_spools", ROOT / "tests" / "fixtures" / "qa" / "spools.py")

PAGE = r"""
const fs = require('fs');
const runtime = fs.readFileSync(process.argv[1], 'utf8');
const start = runtime.indexOf('function foldThreads(events)');
const end = runtime.indexOf('\n\n// This browser', start);
const { foldThreads, draftIds } = Function(runtime.slice(start, end) + '; return { foldThreads, draftIds };')();
const spools = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(spools.map(events => ({
  drafts: [...draftIds(events)],
  threads: [...foldThreads(events).values()].map(th => ({
    id: th.id, status: th.status === undefined ? null : th.status, root: th.ev.body.id,
    messages: th.messages.map(m => m.body.id), latestHumanId: th.latestHumanId,
    draftBy: [...th.draftBy], history: [...th.history].map(([i, h]) => [i, h.original.body.id, h.edits.map(e => e.body.id)]),
  })),
}))));
"""


def page_fold(cases):
    out = subprocess.run(["node", "-e", PAGE, str(RUNTIME)], input=json.dumps(cases),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def service_fold(events):
    return {
        "drafts": sorted(spool.draft_ids(events)),
        "threads": [{
            "id": th["id"], "status": th["status"], "root": th["ev"]["body"]["id"],
            "messages": [m["body"]["id"] for m in th["messages"]], "latestHumanId": th["latest_human_id"],
            "draftBy": list(th["draft_by"]),
            "history": [[i, h["original"]["body"]["id"], [e["body"]["id"] for e in h["edits"]]]
                        for i, h in th["history"].items()],
        } for th in spool.fold_threads(events).values()],
    }


def ev(name, actor, **body):
    return {"name": name, "actor": actor, "body": {"actor": actor, "schemaVersion": 1, **body}}


MANGO = {"browser": "b-mango", "author": "Mango"}
PAPAYA = {"browser": "b-papaya", "author": "Papaya"}

CASES = {
    "handoff lists": [
        ev("100-comment-a.json", "human", id="a", event="comment", anchorId="x", text="A", **MANGO),
        ev("110-comment-b.json", "human", id="b", event="comment", anchorId="x", text="B", **PAPAYA),
        ev("120-handoff-h1.json", "human", id="h1", event="handoff", events=["a"], **MANGO),
    ],
    "legacy handoff": [
        ev("090-comment-o.json", "human", id="o", event="comment", anchorId="x", text="before authors"),
        ev("100-comment-a.json", "human", id="a", event="comment", anchorId="x", text="A", **MANGO),
        ev("110-handoff-old.json", "human", id="old", event="handoff"),
        ev("120-comment-b.json", "human", id="b", event="comment", anchorId="x", text="B", **PAPAYA),
    ],
    "acknowledged and resolved": [
        ev("100-comment-a.json", "human", id="a", event="comment", anchorId="x", text="A", **MANGO),
        ev("101-comment-b.json", "human", id="b", event="comment", anchorId="x", text="B", **MANGO),
        ev("102-comment-c.json", "human", id="c", event="comment", anchorId="x", text="C", **MANGO),
        ev("110-handoff-h.json", "human", id="h", event="handoff", events=["a", "b", "c"], **MANGO),
        ev("120-reply-r1.json", "agent", id="r1", event="reply", respondsTo="a", text="ok"),
        ev("121-reply-r2.json", "agent", id="r2", event="reply", respondsTo="b", status="resolved", text="done"),
        ev("122-status-s1.json", "agent", id="s1", event="status", respondsTo="c", status="resolved"),
        ev("130-reply-u.json", "human", id="u", event="reply", respondsTo="r1", threadId="a", text="more", **PAPAYA),
        ev("140-reply-r3.json", "agent", id="r3", event="reply", respondsTo="r1", text="stale answer"),
    ],
    "parked out-of-order refs": [
        ev("050-reply-late.json", "human", id="u2", event="reply", respondsTo="r1", threadId="u1", anchorId="x", text="f", **MANGO),
        ev("060-status-st.json", "agent", id="st", event="status", respondsTo="u2", status="resolved"),
        ev("100-comment-u1.json", "human", id="u1", event="comment", anchorId="x", text="root", **MANGO),
        ev("110-handoff-h.json", "human", id="h", event="handoff", events=["u1"], **MANGO),
        ev("120-reply-r1.json", "agent", id="r1", event="reply", respondsTo="u1", status="acknowledged", text="ok"),
        ev("130-reply-orphan.json", "agent", id="z", event="reply", respondsTo="missing", text="nowhere"),
    ],
    "edit race and chain": [
        ev("090-edit-e2.json", "human", id="e2", event="edit", supersedes="e1", threadId="m", anchorId="x", text="2", **MANGO),
        ev("100-comment-m.json", "human", id="m", event="comment", anchorId="x", text="orig", **MANGO),
        ev("105-comment-n.json", "human", id="n", event="comment", anchorId="x", text="n", **MANGO),
        ev("110-edit-e1.json", "human", id="e1", event="edit", supersedes="m", threadId="m", anchorId="x", text="1", **MANGO),
        ev("120-handoff-h.json", "human", id="h", event="handoff", events=["m", "e1", "e2", "n"], **MANGO),
        ev("130-edit-p1.json", "human", id="p1", event="edit", supersedes="n", threadId="n", anchorId="x", text="P", **PAPAYA),
        ev("131-edit-p2.json", "human", id="p2", event="edit", supersedes="n", threadId="n", anchorId="x", text="M", **MANGO),
    ],
}


def qa_case(seed):
    fixture = spools.Spool("v")
    seed(fixture)
    return [{"name": name, "actor": body["actor"], "body": body} for name, body in fixture.files]


for _collection, _seed in spools.COLLECTIONS.items():
    CASES["qa " + _collection] = qa_case(_seed)


class FoldParity(unittest.TestCase):
    def test_service_fold_matches_page_fold(self):
        names = list(CASES)
        page = page_fold([CASES[name] for name in names])
        for name, expected in zip(names, page):
            with self.subTest(name):
                self.assertEqual(service_fold(CASES[name]), {**expected, "drafts": sorted(expected["drafts"])})

    def test_index_counts_match_page_resolved_statuses(self):
        """lane-hosting #acceptance-index-threads-fold: the review index counts a thread resolved exactly
        when the page's fold shows it resolved, open otherwise, read from each case's spool on disk."""
        import os
        import tempfile

        serve = load("fold_review_serve", ASSETS / "review-serve.py")
        names = list(CASES)
        page = page_fold([CASES[name] for name in names])
        for name, expected in zip(names, page):
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                spec = os.path.join(root, "docs", "x.spec.html")
                os.makedirs(os.path.dirname(spec))
                for event in CASES[name]:
                    actor = os.path.join(spec + ".review", event["actor"])
                    os.makedirs(actor, exist_ok=True)
                    with open(os.path.join(actor, event["name"]), "w", encoding="utf-8") as stream:
                        json.dump(event["body"], stream)
                with open(spec, "w", encoding="utf-8") as stream:
                    stream.write("<title>x</title>")
                resolved = sum(1 for th in expected["threads"] if th["status"] == "resolved")
                counts = serve._thread_counts({"narrow_root": os.path.join(root, "docs")}, spec)
                self.assertEqual(counts, (len(expected["threads"]) - resolved, resolved))

    def test_statuses(self):
        threads = spool.fold_threads(CASES["acknowledged and resolved"])
        self.assertEqual({key: th["status"] for key, th in threads.items()},
                         {"a": "draft", "b": "resolved", "c": "resolved"})
        threads = spool.fold_threads(CASES["handoff lists"])
        self.assertEqual({key: th["status"] for key, th in threads.items()}, {"a": "pending", "b": "draft"})

    def test_bare_bodies_and_malformed_events(self):
        events = [
            {"name": "1-comment-u1.json", "body": {"id": "u1", "event": "comment", "actor": "human", "anchorId": "x"}},
            {"name": "2-reply-u2.json", "body": {"id": "u2", "event": "reply", "actor": "human", "respondsTo": [1], "threadId": "u1"}},
            {"name": "3-x.json", "body": {"id": "u3", "event": [], "actor": "human"}},
            {"name": "4-x.json", "body": ["not", "a", "body"]},
        ]
        threads = spool.fold_threads(events)
        self.assertEqual([m["body"]["id"] for m in threads["u1"]["messages"]], ["u1"])
        self.assertEqual(threads["u1"]["status"], "draft")


if __name__ == "__main__":
    unittest.main()
