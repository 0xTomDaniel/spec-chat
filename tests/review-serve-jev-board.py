"""GET /api/jev/board behind a fake provider (worklane-provider #jev-board)."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "skill" / "review-spec" / "assets"
sys.path.insert(0, str(ASSETS))
jev = importlib.import_module("jev")
_serve_spec = importlib.util.spec_from_file_location("review_serve_board_test", ASSETS / "review-serve.py")
serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(serve)


class FakeProvider:
    """Answers by question kind and clause text; records every payload."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls.append(payload)
        kind = next(iter(payload["questions"]))
        choice, confidence = self.answer(kind, payload["state"])
        return {"answers": {kind: {"choice": choice, "confidence": confidence}}}


def git(root, *args):
    return subprocess.check_output(("git", "-C", str(root), *args), text=True).strip()


def repo(directory, spec, before, after):
    """A worktree whose spec is `before` at base and origin/HEAD, `after` on disk."""
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    path = root / spec
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before, encoding="utf-8")
    git(root, "add", spec)
    git(root, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", base)
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    path.write_text(after, encoding="utf-8")
    return base


def row(root, slug, spec, base):
    return {"id": f"spec:{slug}:aa::{spec}", "slug": slug, "root": str(root), "spec": spec,
            "spec_file": str(Path(root) / spec), "path": f"{slug}/{spec}", "base": base}


SPEC = "docs/specs/board.spec.html"


def section(text, leaf="rule"):
    return f'<section data-anchor="root"><p data-anchor="{leaf}">{text}</p></section>\n'


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def service(self, answer):
        provider = FakeProvider(answer)
        service = jev.JevService(state_dir=self.dir / "state", provider=provider, api_key="fake")
        return service, provider

    def idle(self, service):
        self.assertTrue(service._board_idle.wait(10))

    def settle(self, service, rows):
        """First read answers at once; the board thread asks the misses; the second read holds them."""
        first = service.board(rows)
        self.idle(service)
        second = service.board(rows)
        self.idle(service)
        return first, second

    def one_row(self, before="Old words.", after="New words."):
        root = self.dir / "a"
        base = repo(root, SPEC, section(before), section(after))
        return [row(root, "ann1", SPEC, base)]

    def test_cosmetic_change_is_not_material_and_below_threshold_is_unknown(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("cosmetic", 0.9))
        first, second = self.settle(service, rows)
        self.assertEqual(first, {"jev": "on", "rows": [], "conflicts": []})  # unanswered is unknown
        self.assertEqual(second["rows"], [{"id": rows[0]["id"], "material": "no"}])

        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("cosmetic", 0.1))
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "unknown")

    def test_confident_behavioral_is_material(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("behavioral", 0.9))
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "yes")

    def test_material_combines_answers(self):
        shown = lambda label: {"outcome": "shown", "answer": {"label": label}}
        self.assertEqual(jev.material([shown("cosmetic"), shown("clarification")]), "no")
        self.assertEqual(jev.material([shown("cosmetic"), None]), "unknown")
        self.assertEqual(jev.material([shown("cosmetic"), {"outcome": "unsure", "answer": {"label": "scope"}}]), "unknown")
        self.assertEqual(jev.material([None, shown("scope")]), "yes")
        self.assertEqual(jev.material([{"outcome": "off", "answer": {"label": None}}]), "unknown")

    def test_answer_never_waits_for_jev(self):
        gate = threading.Event()

        def blocked(kind, state):
            gate.wait(5)
            return ("cosmetic", 0.9)

        rows = self.one_row()
        service, provider = self.service(blocked)
        self.assertEqual(service.board(rows)["rows"], [])
        while not provider.calls:
            time.sleep(0.01)
        again = service.board(rows)  # in-flight question is not asked twice
        self.assertEqual(again["rows"], [{"id": rows[0]["id"], "material": "unknown"}])
        gate.set()
        self.idle(service)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(service.board(rows)["rows"][0]["material"], "no")
        self.idle(service)

    def test_answer_never_builds_questions(self):
        gate = threading.Event()
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("cosmetic", 0.9))
        slow = service._material_questions
        service._material_questions = lambda row: (gate.wait(5), slow(row))[1]
        started = time.monotonic()
        answer = service.board(rows)
        again = service.board(rows)
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertEqual(answer, {"jev": "on", "rows": [], "conflicts": []})
        self.assertEqual(again["rows"], [])
        gate.set()
        self.idle(service)
        self.assertEqual(service.board(rows)["rows"], [{"id": rows[0]["id"], "material": "no"}])
        self.idle(service)

    def test_unavailable_record_is_reused_until_inputs_change(self):
        def failing(kind, state):
            raise OSError("jev down")

        rows = self.one_row()
        service, provider = self.service(failing)
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "unknown")
        asked = len(provider.calls)
        self.assertEqual(asked, 2)  # each question asked once, never re-asked by the second read
        service.board(rows)
        self.idle(service)
        self.assertEqual(len(provider.calls), asked)  # same key: no provider call
        Path(rows[0]["spec_file"]).write_text(section("Newer words."), encoding="utf-8")
        service.board(rows)
        self.idle(service)
        self.assertGreater(len(provider.calls), asked)  # new head asks again

    def test_off_without_key(self):
        service = jev.JevService(state_dir=self.dir / "state", api_key="")
        self.assertEqual(service.board(self.one_row()), {"jev": "off", "rows": [], "conflicts": []})

    def test_bad_base_is_unknown(self):
        rows = self.one_row()
        rows[0]["base"] = "no-such-ref"
        service, provider = self.service(lambda kind, state: ("cosmetic", 0.9))
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "unknown")
        self.assertFalse([call for call in provider.calls if "type" in call["questions"]])

    def lanes(self):
        a, b = self.dir / "a", self.dir / "b"
        other = "docs/specs/other.spec.html"
        base_a = repo(a, SPEC, section("Keep."), section("Cards sort by age."))
        base_b = repo(b, SPEC, section("Keep."), section("Cards sort by title."))
        (b / other).write_text(section("Unchanged elsewhere.", "else"), encoding="utf-8")
        return [row(a, "ann1", SPEC, base_a), row(b, "ann2", SPEC, base_b)]

    def test_confident_contradiction_lists_pair_once(self):
        rows = self.lanes()

        def answer(kind, state):
            if kind == "corpus":
                return ("contradicts", 0.9)
            return ("behavioral", 0.9)

        service, provider = self.service(answer)
        first, second = self.settle(service, rows)
        self.assertEqual(first["conflicts"], [])
        self.assertEqual(second["conflicts"], [{"a": "ann1/" + SPEC, "b": "ann2/" + SPEC}])
        corpus = [call for call in provider.calls if "corpus" in call["questions"]]
        self.assertEqual(len(corpus), 1)  # one unordered pair of changed leaves, asked once
        self.assertEqual(corpus[0]["state"], {"before": "Keep.", "after": "Cards sort by age.",
                                              "target": "Cards sort by title."})
        self.assertNotIn("Cards", json.dumps(second))

    def test_only_confident_contradicts_lists(self):
        for label, confidence in (("overlaps", 0.9), ("oversteps", 0.9), ("contradicts", 0.1)):
            with self.subTest(label=label, confidence=confidence):
                self.tearDown()
                self.setUp()
                rows = self.lanes()
                service, _ = self.service(lambda kind, state: (label, confidence) if kind == "corpus" else ("scope", 0.9))
                _, second = self.settle(service, rows)
                self.assertEqual(second["conflicts"], [])

    def test_same_slug_clauses_are_never_paired(self):
        rows = self.lanes()
        rows[1]["slug"] = "ann1"
        rows[1]["path"] = "ann1b/" + SPEC
        service, provider = self.service(lambda kind, state: ("contradicts", 0.9))
        self.settle(service, rows)
        self.assertFalse([call for call in provider.calls if "corpus" in call["questions"]])

    def test_route_serves_board_without_parameters(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("cosmetic", 0.9))
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState(rows)
        server.jev = service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = "http://127.0.0.1:%d/api/jev/board" % server.server_port
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=2) as response:
                first = json.loads(response.read())
            self.idle(service)
            with opener.open(url + "?path=ignored", timeout=2) as response:
                second = json.loads(response.read())
            self.idle(service)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(first["rows"], [])
        self.assertEqual(second["rows"], [{"id": rows[0]["id"], "material": "no"}])
        records = (self.dir / "state" / "records.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("New words", records)


if __name__ == "__main__":
    unittest.main()
