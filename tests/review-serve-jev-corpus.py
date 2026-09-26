"""Corpus flag clean-up behind a fake provider (jev-suggestions #corpus, #acceptance-corpus, #acceptance-corpus-scope)."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_jev_corpus_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

SPEC = "docs/specs/shared.spec.html"
OWN = "docs/specs/own.spec.html"


class FakeProvider:
    """Answers corpus questions by target text; records every payload."""

    def __init__(self, labels):
        self.labels = labels
        self.calls = []

    def decide(self, payload):
        self.calls.append(payload)
        kind = next(iter(payload["questions"]))
        label = self.labels.get(payload["state"].get("target"), "unrelated") if kind == "corpus" else "cosmetic"
        return {"answers": {kind: {"choice": label, "confidence": 0.95}}}


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def page(rule, header_status="Status: draft."):
    return ('<header data-anchor="header"><h1 data-anchor="title">Page</h1>'
            f'<p data-anchor="source-issues">Source issues: ANN-1.</p><p data-anchor="status">{header_status}</p>'
            '<p data-anchor="context">Context words.</p></header>'
            f'<section data-anchor="rules"><p data-anchor="rule">{rule}</p></section>'
            '<section data-anchor="non-goals"><p data-anchor="non-goal-text">The service never writes review events.</p></section>'
            '<section data-anchor="traceability-source-issues"><p data-anchor="status-line">Lane status line.</p></section>')


class CorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_contradicts_shows_and_overlaps_is_recorded_but_hidden(self):
        baseline = page("The service reads review events.")
        current = page("The service writes review events.")
        other = '<section data-anchor="o"><p data-anchor="restated">The service writes review events.</p></section>'
        labels = {"The service never writes review events.": "contradicts",
                  "The service writes review events.": "overlaps"}
        provider = FakeProvider(labels)
        service = jev.JevService(state_dir=self.dir / "state", provider=provider, api_key="fake")
        questions = jev.build_corpus_questions(current, baseline, OWN, "base", "head",
                                               [{"path": "lane/docs/specs/other.spec.html", "source": other}])
        service.questions = lambda *args, **kwargs: questions
        items = service.response({}, "", "", "base", [])["items"]
        by_target = {item["target"]: item for item in items}
        self.assertEqual(by_target["non-goal-text"]["state"], "label")
        self.assertEqual(by_target["non-goal-text"]["label"], "contradicts")
        restated = by_target["lane/docs/specs/other.spec.html#restated"]
        self.assertEqual(restated["state"], "none")
        self.assertIsNone(restated["label"])
        recorded = service.seam.store.get(service.seam.key(
            next(q for q in questions if q["target"] == "lane/docs/specs/other.spec.html#restated")))
        self.assertEqual(recorded["answer"]["label"], "overlaps")
        self.assertTrue(all(item["label"] in {None, "contradicts", "oversteps"} for item in items))

    def test_header_and_status_clauses_are_neither_asked_nor_compared(self):
        baseline = page("The service reads review events.")
        current = page("The service writes review events.", "Status: accepted.").replace(
            "Lane status line.", "Lane status line changed.").replace("Context words.", "Context changed.")
        other = page("Other rule.").replace('data-anchor="', 'data-anchor="x-')
        questions = jev.build_corpus_questions(current, baseline, OWN, "base", "head",
                                               [{"path": "other.spec.html", "source": other}])
        self.assertEqual({q["id"] for q in questions}, {"rule"})
        targets = {q["target"] for q in questions}
        for meta in ("title", "status", "source-issues", "context", "status-line"):
            self.assertNotIn(meta, targets)
            self.assertNotIn("other.spec.html#x-" + meta, targets)
        self.assertIn("other.spec.html#x-rule", targets)

    def repo(self, name, files, main_files):
        root = self.dir / name
        root.mkdir()
        git(root, "init", "-q")
        for relative, text in main_files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(root, "add", "-A")
        git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "main")
        git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return root

    def worktree(self, source, name, files):
        root = self.dir / name
        git(source, "worktree", "add", "-q", "--detach", str(root), "HEAD")
        for relative, text in files.items():
            (root / relative).write_text(text, encoding="utf-8")
        return root

    @staticmethod
    def row(root, slug, spec):
        return {"slug": slug, "root": str(root), "spec": spec, "spec_file": str(Path(root) / spec)}

    def test_other_specs_are_own_lane_as_served_plus_main_copies_once(self):
        main = {SPEC: "main shared", OWN: "main own", "docs/specs/lane-b.spec.html": "main b",
                "docs/specs/third.spec.html": "main third"}
        lane_a = self.repo("a", {SPEC: "lane a shared", OWN: "lane a own"}, main)
        lane_b = self.worktree(lane_a, "b", {SPEC: "lane b shared", "docs/specs/lane-b.spec.html": "lane b",
                                             "docs/specs/new.spec.html": "not on main"})
        lane_c = self.worktree(lane_a, "c", {"docs/specs/third.spec.html": "lane c third"})
        mounts = [self.row(lane_a, "a", SPEC), self.row(lane_a, "a", OWN),
                  self.row(lane_b, "b", SPEC), self.row(lane_b, "b", "docs/specs/lane-b.spec.html"),
                  self.row(lane_b, "b", "docs/specs/new.spec.html"),
                  self.row(lane_c, "c", "docs/specs/third.spec.html"),
                  self.row(lane_c, "c", "docs/specs/lane-b.spec.html")]
        service = object.__new__(jev.JevService)
        served = service._served_specs(mounts, str(lane_a / SPEC), mounts[0])
        sources = {item["path"]: item["source"] for item in served}
        self.assertEqual(sources.pop("a/" + OWN), b"lane a own")
        self.assertEqual(sorted(sources.values()), [b"main b", b"main third"])
        self.assertEqual(len(served), 3)  # lane b's copy of the page, new, and duplicate paths never appear

    def test_single_root_mode_uses_root_served_specs(self):
        root = self.repo("single", {}, {SPEC: "shared", OWN: "own"})
        mount = {"slug": "", "root": str(root), "narrow_root": str(root / "docs")}
        service = object.__new__(jev.JevService)
        served = service._served_specs([mount], str(root / SPEC), mount)
        self.assertEqual([(item["path"], item["source"]) for item in served], [("specs/own.spec.html", b"own")])

    def test_question_count_drops_against_multi_lane_registry(self):
        leaves = "".join(f'<p data-anchor="c{i}">Clause {i} about review events.</p>' for i in range(12))
        before = page("The service reads review events.") + f'<section data-anchor="more">{leaves}</section>'
        after = before.replace("reads review", "writes review").replace("Status: draft.", "Status: next.")
        lane_a = self.repo("a", {SPEC: after}, {SPEC: before})
        lanes = [lane_a] + [self.worktree(lane_a, f"l{n}", {SPEC: after.replace("Clause", f"Lane {n} clause")})
                            for n in range(4)]
        mounts = [self.row(root, f"l{n}", SPEC) for n, root in enumerate(lanes)]
        service = object.__new__(jev.JevService)
        now = jev.build_corpus_questions(after, before, "l0/" + SPEC, "base", "head",
                                         service._served_specs(mounts, str(lane_a / SPEC), mounts[0]))
        every_copy = jev.build_corpus_questions(after, before, "l0/" + SPEC, "base", "head",
                                                service._as_served(mounts, str(lane_a / SPEC)))
        self.assertLess(len(now), len(every_copy))
        self.assertEqual({q["id"] for q in now}, {"rule"})
        self.assertFalse([q for q in now if q["target"].startswith(("l1/", "l2/", "l3/", "l4/"))])


if __name__ == "__main__":
    unittest.main()
