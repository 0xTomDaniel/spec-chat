"""Corpus flag clean-up behind a fake provider (jev-suggestions #corpus, #acceptance-corpus, #acceptance-corpus-scope)."""

import importlib.util
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("review_serve_jev_corpus_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

SPEC = "docs/specs/shared.spec.html"
OWN = "docs/specs/own.spec.html"


def settled(service, *args, timeout=10):
    """Re-read, as the page does, until no item is pending (#fast-marks-background)."""
    deadline = time.monotonic() + timeout
    while True:
        result = service.response(*args)
        if not any(item["state"] == "pending" for item in result["items"]) or time.monotonic() > deadline:
            return result
        time.sleep(0.01)


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


def page(rule, header_context="Context words."):
    return ('<header data-anchor="header"><h1 data-anchor="title">Page</h1>'
            '<p data-anchor="source-issues">Source issues: ANN-1.</p>'
            f'<p data-anchor="context">{header_context}</p></header>'
            f'<section data-anchor="rules"><p data-anchor="rule">{rule}</p></section>'
            '<section data-anchor="non-goals"><p data-anchor="non-goal-text">The service never writes review events.</p></section>'
            '<section data-anchor="traceability-source-issues"><p data-anchor="status-line">Lane status line.</p></section>')


class CorpusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.services = []

    def tearDown(self):
        for service in self.services:  # stop asking before the state directory goes
            service.stop()
        self.tmp.cleanup()

    def jev_service(self, **kwargs):
        service = jev.JevService(**kwargs)
        self.services.append(service)
        return service

    def test_contradicts_shows_and_overlaps_is_recorded_but_hidden(self):
        baseline = page("The service reads review events.")
        current = page("The service writes review events.")
        other = '<section data-anchor="o"><p data-anchor="restated">The service writes review events.</p></section>'
        labels = {"The service never writes review events.": "contradicts",
                  "The service writes review events.": "overlaps"}
        provider = FakeProvider(labels)
        service = self.jev_service(state_dir=self.dir / "state", provider=provider, api_key="fake")
        questions = jev.build_corpus_questions(current, baseline, OWN, "base", "head",
                                               [{"path": "lane/docs/specs/other.spec.html", "source": other}])
        service.questions = lambda *args, **kwargs: questions
        items = settled(service, {}, "", "", "base", [])["items"]
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

    def test_header_and_source_issue_clauses_are_neither_asked_nor_compared(self):
        baseline = page("The service reads review events.")
        current = page("The service writes review events.", "Context changed.").replace(
            "Lane status line.", "Lane status line changed.")
        other = page("Other rule.").replace('data-anchor="', 'data-anchor="x-')
        questions = jev.build_corpus_questions(current, baseline, OWN, "base", "head",
                                               [{"path": "other.spec.html", "source": other}])
        self.assertEqual({q["id"] for q in questions}, {"rule"})
        targets = {q["target"] for q in questions}
        for meta in ("title", "source-issues", "context", "status-line"):
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
        after = before.replace("reads review", "writes review").replace("Context words.", "Context next.")
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

    def test_repeat_read_parses_no_spec_and_runs_git_only_to_resolve_refs(self):
        """#acceptance-fast: unchanged spec, spool, and served files reuse kept builder results."""
        main = {SPEC: page("The service reads review events."), OWN: page("Own rule."),
                "docs/specs/lane-b.spec.html": page("Lane b rule on main.")}
        lane_a = self.repo("a", {SPEC: page("The service writes review events.")}, main)
        base = git(lane_a, "rev-parse", "HEAD")
        lane_b = self.worktree(lane_a, "b", {"docs/specs/lane-b.spec.html": page("Lane b working copy.")})
        other = self.repo("d", {}, {"docs/specs/d.spec.html": page("Another repository reads review events.")})
        mounts = [self.row(lane_a, "a", SPEC), self.row(lane_a, "a", OWN),
                  self.row(lane_b, "b", "docs/specs/lane-b.spec.html"),
                  self.row(other, "d", "docs/specs/d.spec.html")]
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "rule", "text": "Reads or writes?",
            "createdAt": "2020-01-01T00:00:00Z"}}]
        provider = FakeProvider({})
        dirs = [ROOT / "skill" / "review-spec" / "assets" / "jev"]
        service = self.jev_service(state_dir=self.dir / "state", provider=provider, api_key="fake", question_dirs=dirs)
        read = lambda s: s.response(mounts[0], str(lane_a / SPEC), "a/" + SPEC, base, events, "reading", mounts)
        first = settled(service, mounts[0], str(lane_a / SPEC), "a/" + SPEC, base, events, "reading", mounts)
        self.assertTrue(first["items"])

        parses, gits = [], []
        feed, run, check_output = jev._AnchorParser.feed, subprocess.run, subprocess.check_output

        def counted_feed(parser, data):
            parses.append(len(data))
            return feed(parser, data)

        def counted(real):
            def call(argv, *args, **kwargs):
                if argv and argv[0] == "git":
                    gits.append(tuple(argv))
                return real(argv, *args, **kwargs)
            return call

        with patch.object(jev._AnchorParser, "feed", counted_feed), \
                patch.object(jev.subprocess, "run", counted(run)), \
                patch.object(jev.subprocess, "check_output", counted(check_output)):
            second = read(service)
        self.assertEqual(second, first)
        self.assertEqual(parses, [])
        self.assertTrue(all("rev-parse" in argv for argv in gits), gits)
        resolved = sorted(argv[-1].removesuffix("^{commit}") for argv in gits)
        # Cross-lane target main (#cross-lane-clauses) is each repository's origin/HEAD, resolved once per read.
        self.assertEqual(resolved, sorted([base, "HEAD", "origin/main", "origin/main",
                                           "refs/remotes/origin/HEAD", "refs/remotes/origin/HEAD"]))
        self.assertEqual(read(self.jev_service(state_dir=self.dir / "state", provider=provider, api_key="fake",
                                             question_dirs=dirs)), first)  # same as a cold read

        (lane_a / OWN).write_text(page("Own rule now writes review events."), encoding="utf-8")
        with patch.object(jev._AnchorParser, "feed", counted_feed):
            read(service)
        self.assertTrue(parses)  # a changed served file is read fresh

    def healing_setup(self):
        main = {SPEC: page("The service reads review events."), OWN: page("Own rule.")}
        lane = self.repo("h", {SPEC: page("The service writes review events.")}, main)
        base = git(lane, "rev-parse", "HEAD")
        git(lane, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qam", "lane")
        other = self.worktree(lane, "o", {})  # same project: its row must dedupe against this page
        mounts = [self.row(lane, "h", SPEC), self.row(lane, "h", OWN), self.row(other, "o", SPEC)]
        events = [{"name": "1-comment.json", "actor": "human", "body": {  # message after HEAD: text at HEAD
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "rule", "text": "Reads or writes?",
            "createdAt": "2099-01-01T00:00:00Z"}}]
        service = self.jev_service(state_dir=self.dir / "state", provider=FakeProvider({}), api_key="fake",
                                 question_dirs=[ROOT / "skill" / "review-spec" / "assets" / "jev"])
        read = lambda: service.questions(mounts[0], str(lane / SPEC), "h/" + SPEC, base, events, "reading", mounts)
        cold = self.jev_service(state_dir=self.dir / "cold", provider=FakeProvider({}), api_key="fake",
                              question_dirs=[ROOT / "skill" / "review-spec" / "assets" / "jev"])
        expected = cold.questions(mounts[0], str(lane / SPEC), "h/" + SPEC, base, events, "reading", mounts)
        return service, read, expected

    def test_git_read_that_fails_once_is_retried_next_read(self):
        """A timeout, OSError, or failed git exit is never kept: the next read heals."""
        run = subprocess.run
        failures = {
            "timeout": lambda argv: (_ for _ in ()).throw(subprocess.TimeoutExpired(argv, 5)),
            "oserror": lambda argv: (_ for _ in ()).throw(OSError("fork failed")),
            "exit": lambda argv: subprocess.CompletedProcess(argv, 128, b"", b""),
        }
        for name, fail in failures.items():
            for verb in ("show", "log", "--git-common-dir"):
                with self.subTest(failure=name, verb=verb):
                    self.tearDown()
                    self.setUp()
                    service, read, expected = self.healing_setup()
                    if name == "exit" and verb == "show":
                        continue  # a clean nonzero show is a definite "path absent"
                    left = [1]

                    def flaky(argv, *args, **kwargs):
                        if left and argv and argv[0] == "git" and verb in argv:
                            left.pop()
                            return fail(argv)
                        return run(argv, *args, **kwargs)

                    with patch.object(jev.subprocess, "run", flaky):
                        read()
                    self.assertFalse(left)
                    self.assertEqual(read(), expected)

    def test_builder_that_raised_is_rebuilt_next_read(self):
        service, read, expected = self.healing_setup()
        original, calls = jev.BUILDERS["type"], []

        def flaky(*args):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return original(*args)

        with patch.dict(jev.BUILDERS, {"type": flaky}):
            self.assertNotIn("type", {q["kind"] for q in read()})
            self.assertEqual(read(), expected)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
