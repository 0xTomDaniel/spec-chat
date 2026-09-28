"""GET /api/jev/board behind a fake provider (worklane-provider #jev-board)."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
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
DRAFT = {"contradicts", "oversteps", "overlaps"}


# Each slug's changed clause texts, lower slug first.
LANES = ({"Cards sort by age."}, {"Cards sort by title."})
CROSS = ({"Cards sort by age.", "The board edits specs."}, {"Cards sort by title.", "Only the spec owner edits specs."})


def crosses(state, texts):
    """A draft-check state pairing a changed clause of each slug."""
    pair = {state["after"], state["target"]}
    return bool(pair & texts[0] and pair & texts[1])


def section(text, leaf="rule"):
    return f'<section data-anchor="root"><p data-anchor="{leaf}">{text}</p></section>\n'


class BoardTest(unittest.TestCase):
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

    def service(self, answer):
        provider = FakeProvider(answer)
        service = self.jev_service(state_dir=self.dir / "state", provider=provider, api_key="fake")
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

    def test_no_behavior_change_is_not_material_and_unsettled_unsure_is_unknown(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("no", 0.9))
        first, second = self.settle(service, rows)
        self.assertEqual(first, {"jev": "on", "rows": [], "conflicts": []})  # unanswered is unknown
        self.assertEqual(second["rows"], [{"id": rows[0]["id"], "material": "no"}])

        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("no", 0.1))
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "unknown")

    def test_behavior_change_is_material(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("yes", 0.9))
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "yes")

    def test_material_combines_answers(self):
        shown = lambda label: {"outcome": "shown", "answer": {"label": label}}
        no, yes = shown("no-behavior-change"), shown("behavior")
        self.assertEqual(jev.material([no, no]), "no")
        self.assertEqual(jev.material([no, None]), "unknown")
        self.assertEqual(jev.material([no, {"outcome": "unavailable", "answer": {"label": None}}]), "unknown")
        self.assertEqual(jev.material([None, yes]), "yes")
        self.assertEqual(jev.material([{"outcome": "off", "answer": {"label": None}}]), "unknown")

    def test_answer_never_waits_for_jev(self):
        gate = threading.Event()

        def blocked(kind, state):
            gate.wait(5)
            return ("no", 0.9)

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
        service, _ = self.service(lambda kind, state: ("no", 0.9))
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

    def test_unavailable_record_is_reused_during_its_pause(self):
        def failing(kind, state):
            raise OSError("jev down")

        rows = self.one_row()
        service, provider = self.service(failing)
        _, second = self.settle(service, rows)
        self.assertEqual(second["rows"][0]["material"], "unknown")
        asked = len(provider.calls)
        self.assertEqual(asked, 1)  # each question asked once, never re-asked by the second read
        service.board(rows)
        self.idle(service)
        self.assertEqual(len(provider.calls), asked)  # same key: no provider call
        Path(rows[0]["spec_file"]).write_text(section("Newer words."), encoding="utf-8")
        service.board(rows)
        self.idle(service)
        self.assertGreater(len(provider.calls), asked)  # new head asks again

    def test_off_without_key(self):
        service = self.jev_service(state_dir=self.dir / "state", api_key="")
        self.assertEqual(service.board(self.one_row()), {"jev": "off", "rows": [], "conflicts": []})

    def test_bad_base_is_unknown(self):
        rows = self.one_row()
        rows[0]["base"] = "no-such-ref"
        service, provider = self.service(lambda kind, state: ("no", 0.9))
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

    @staticmethod
    def lane_calls(provider, texts):
        """Draft-check questions asked of a cross-lane pair: one clause from each slug's changed texts."""
        return [call for call in provider.calls if set(call["questions"]) <= DRAFT and crosses(call["state"], texts)]

    def test_confident_contradiction_lists_pair_once(self):
        rows = self.lanes()
        service, provider = self.service(lambda kind, state: ("yes", 0.9))  # contradicts?, changes behavior?
        first, second = self.settle(service, rows)
        self.assertEqual(first["conflicts"], [])
        self.assertEqual(second["conflicts"], [{"a": "ann1/" + SPEC, "b": "ann2/" + SPEC}])
        lane = self.lane_calls(provider, LANES)
        # One unordered pair of changed leaves, asked once, lower slug first; a yes to contradicts? stops the chain.
        self.assertEqual([next(iter(call["questions"])) for call in lane], ["contradicts"])
        self.assertEqual(lane[0]["state"], {"before": "Keep.", "after": "Cards sort by age.",
                                            "target": "Cards sort by title.", "target_non_goal": False})
        self.assertEqual(set(lane[0]["questions"]["contradicts"]["criteria"]), {"yes", "no"})
        self.assertNotIn("Cards", json.dumps(second))

    def test_mutual_overstep_marks_both_clauses(self):
        """A mutual overstep marks each clause both Oversteps and Overstepped by."""
        rows = self.lanes()

        def answer(kind, state):
            if kind not in DRAFT:
                return ("yes", 0.9)
            if kind == "oversteps":
                return ("yes", 0.9)
            return ("no", 0.9)

        service, provider = self.service(answer)
        _, second = self.settle(service, rows)
        y = "ann2/" + SPEC
        self.assertEqual(second["conflicts"], [{"a": "ann1/" + SPEC, "b": y}])
        lane = self.lane_calls(provider, LANES)
        age, title = "Cards sort by age.", "Cards sort by title."
        asked = [(next(iter(call["questions"])), call["state"]["after"]) for call in lane]
        self.assertEqual(asked, [("contradicts", age), ("oversteps", age), ("oversteps", title)])
        # Page items: each side gets both marks
        self.wait_asks(service)
        x_items = self.lane_items(self.page(service, rows, 0))
        y_items = self.lane_items(self.page(service, rows, 1))
        self.wait_asks(service)
        strip = lambda items: [{k: v for k, v in item.items() if k != "record"} for item in items]
        self.assertEqual(strip(x_items), [
            {"kind": "lane", "id": "rule", "state": "label", "label": "oversteps", "side": "first", "other": "ann2",
             "target": y + "#rule", "level": "warning", "agent_level": "important"},
            {"kind": "lane", "id": "rule", "state": "label", "label": "overstepped by", "side": "first", "other": "ann2",
             "target": y + "#rule", "level": "warning", "agent_level": "important"},
        ])
        self.assertEqual(strip(y_items), [
            {"kind": "lane", "id": "rule", "state": "label", "label": "overstepped by", "side": "second", "other": "ann1",
             "target": "ann1/" + SPEC + "#rule", "level": "warning", "agent_level": "important"},
            {"kind": "lane", "id": "rule", "state": "label", "label": "oversteps", "side": "second", "other": "ann1",
             "target": "ann1/" + SPEC + "#rule", "level": "warning", "agent_level": "important"},
        ])
        self.assertTrue(all(item["record"] for item in x_items + y_items))

    def test_findings_are_contradicts_or_either_overstep(self):
        age, title = "Cards sort by age.", "Cards sort by title."
        # Each case: the one question answering yes (kind, after), its confidence, whether the pair is listed,
        # and the questions asked.
        contradicts, forth, back, overlaps = (("contradicts", age), ("oversteps", age), ("oversteps", title),
                                              ("overlaps", age))
        # contradicts? once, both oversteps?, overlaps? only when all three are no.
        cases = ((contradicts, 0.9, True, [contradicts]), (forth, 0.9, True, [contradicts, forth, back]),
                 (back, 0.9, True, [contradicts, forth, back]), (overlaps, 0.9, False, [contradicts, forth, back, overlaps]),
                 (None, 0.9, False, [contradicts, forth, back, overlaps]),
                 (contradicts, 0.1, False, [contradicts, forth, back, overlaps]))  # P(yes)<verify: silent-no, chain continues (#asymmetric)
        for yes, confidence, listed, expected in cases:
            with self.subTest(yes=yes, confidence=confidence):
                self.tearDown()
                self.setUp()
                rows = self.lanes()

                def answer(kind, state):
                    if kind not in DRAFT:
                        return ("yes", 0.9)
                    return ("yes", confidence) if (kind, state["after"]) == yes else ("no", 0.9)

                service, provider = self.service(answer)
                _, second = self.settle(service, rows)
                self.assertEqual(second["conflicts"], [{"a": "ann1/" + SPEC, "b": "ann2/" + SPEC}] if listed else [])
                asked = [(next(iter(call["questions"])), call["state"]["after"]) for call in self.lane_calls(provider, LANES)]
                self.assertEqual(asked, expected)

    def cross_lane(self):
        """#acceptance-cross-lane: X changes a and c, Y changes b and d, against target main."""
        x, y = self.dir / "x", self.dir / "y"
        other = "docs/specs/y.spec.html"
        keep = '<section data-anchor="root"><p data-anchor="{0}">Keep.</p><p data-anchor="{1}">Keep too.</p></section>\n'
        base_x = repo(x, SPEC, keep.format("a", "c"),
                      '<section data-anchor="root"><p data-anchor="a">Cards sort by age.</p>'
                      '<p data-anchor="c">The board edits specs.</p></section>\n')
        base_y = repo(y, other, keep.format("b", "d"),
                      '<section data-anchor="root"><p data-anchor="b">Cards sort by title.</p>'
                      '<p data-anchor="d">Only the spec owner edits specs.</p></section>\n')
        return [row(x, "ann1", SPEC, base_x), row(y, "ann2", other, base_y)]

    @staticmethod
    def cross_answer(kind, state):
        if kind == "about":
            return ("yes", 0.9)
        if kind not in DRAFT:
            return ("unrelated", 0.9) if kind == "coverage" else ("no", 0.9)
        pair = (state["after"], state["target"])
        if kind == "contradicts" and pair == ("Cards sort by age.", "Cards sort by title."):
            return ("yes", 0.9)
        if kind == "oversteps" and pair == ("The board edits specs.", "Only the spec owner edits specs."):
            return ("yes", 0.9)
        return ("no", 0.9)

    def page(self, service, rows, index):
        mount = rows[index]
        return service.response(mount, mount["spec_file"], mount["spec"], mount["base"], [], "", rows)

    @staticmethod
    def lane_items(response):
        return sorted((item for item in response["items"] if item["kind"] == "lane"),
                      key=lambda item: (item["id"], item["target"]))

    def wait_asks(self, service):
        deadline = time.monotonic() + 10
        while service._asking and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service._asking)

    def test_lane_items_on_both_specs_after_answers(self):
        rows = self.cross_lane()
        service, provider = self.service(self.cross_answer)
        y = "ann2/docs/specs/y.spec.html"
        first = self.lane_items(self.page(service, rows, 0))
        # Unanswered cross-lane questions on its clauses count as pending, never asked before answering.
        self.assertEqual([(item["id"], item["state"], item["target"]) for item in first],
                         [("a", "pending", y + "#b"), ("a", "pending", y + "#d"),
                          ("c", "pending", y + "#b"), ("c", "pending", y + "#d")])
        self.wait_asks(service)
        # a-b stops at contradicts?, c-d asks both oversteps?, a-d and c-b ask all four.
        self.assertEqual(len(self.lane_calls(provider, CROSS)), 1 + 3 + 4 + 4)
        x_items = self.lane_items(self.page(service, rows, 0))
        y_items = self.lane_items(self.page(service, rows, 1))
        strip = lambda items: [{k: v for k, v in item.items() if k != "record"} for item in items]
        self.assertEqual(strip(x_items), [
            {"kind": "lane", "id": "a", "state": "label", "label": "contradicts", "side": "first", "other": "ann2",
             "target": y + "#b", "level": "important", "agent_level": "important"},
            {"kind": "lane", "id": "c", "state": "label", "label": "oversteps", "side": "first", "other": "ann2",
             "target": y + "#d", "level": "warning", "agent_level": "important"},
        ])
        self.assertEqual(strip(y_items), [
            {"kind": "lane", "id": "b", "state": "label", "label": "contradicts", "side": "second", "other": "ann1",
             "target": "ann1/" + SPEC + "#a", "level": "important", "agent_level": "important"},
            {"kind": "lane", "id": "d", "state": "label", "label": "overstepped by", "side": "second", "other": "ann1",
             "target": "ann1/" + SPEC + "#c", "level": "warning", "agent_level": "important"},
        ])
        self.assertTrue(all(item["record"] for item in x_items + y_items))
        # The board lists the pair of paths once; a repeat read of either route makes no provider call.
        _, board = self.settle(service, rows)
        self.assertEqual(board["conflicts"], [{"a": "ann1/" + SPEC, "b": y}])
        asked = len(provider.calls)
        self.page(service, rows, 0)
        self.page(service, rows, 1)
        self.wait_asks(service)
        service.board(rows)
        self.idle(service)
        self.assertEqual(len(provider.calls), asked)

    def test_lane_miss_is_asked_once_across_routes(self):
        gate = threading.Event()

        def blocked(kind, state):
            if kind in DRAFT and crosses(state, CROSS):
                gate.wait(5)
            return self.cross_answer(kind, state)

        rows = self.cross_lane()
        service, provider = self.service(blocked)
        started = time.monotonic()
        self.page(service, rows, 0)
        service.board(rows)
        self.page(service, rows, 1)
        self.assertLess(time.monotonic() - started, 4)  # answered at once while lane asks wait
        gate.set()
        self.wait_asks(service)
        self.idle(service)
        lane = self.lane_calls(provider, CROSS)
        self.assertEqual(len(lane), 1 + 3 + 4 + 4)
        self.assertEqual(len({json.dumps([call["questions"], call["state"]], sort_keys=True) for call in lane}), len(lane))

    def test_single_root_page_has_no_lane_items(self):
        rows = self.cross_lane()
        service, provider = self.service(self.cross_answer)
        mount = {"id": "single-root", "slug": "", "root": rows[0]["root"],
                 "narrow_root": str(Path(rows[0]["root"]) / "docs"), "spec": None}
        response = service.response(mount, rows[0]["spec_file"], "specs/board.spec.html", rows[0]["base"], [], "", [mount])
        self.assertEqual(self.lane_items(response), [])
        self.wait_asks(service)
        self.assertFalse(self.lane_calls(provider, CROSS))

    def test_same_slug_clauses_are_never_paired(self):
        rows = self.lanes()
        rows[1]["slug"] = "ann1"
        rows[1]["path"] = "ann1b/" + SPEC
        service, provider = self.service(lambda kind, state: ("yes", 0.9))
        self.settle(service, rows)
        self.assertFalse(self.lane_calls(provider, LANES))

    def test_route_serves_board_without_parameters(self):
        rows = self.one_row()
        service, _ = self.service(lambda kind, state: ("no", 0.9))
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

    def test_jev_route_resolves_base_once_and_rejects_bad_base(self):
        """GET /api/jev runs one base resolve per read; a bad base is still 400 invalid base."""
        rows = self.one_row()
        base = rows[0]["base"]
        rows[0]["root"] = str(Path(rows[0]["root"]).resolve())
        rows[0]["narrow_root"] = str(Path(rows[0]["root"]) / "docs")
        service, _ = self.service(lambda kind, state: ("no", 0.9))
        server = serve.ReviewThreadingHTTPServer(("127.0.0.1", 0), serve.MountHandler)
        server.mount_state = serve.MountState(rows)
        server.jev = service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gits = []

        class Counted(subprocess.Popen):
            def __init__(self, argv, *args, **kwargs):
                if argv and argv[0] == "git":
                    gits.append(tuple(argv))
                super().__init__(argv, *args, **kwargs)

        try:
            url = "http://127.0.0.1:%d/api/jev?path=%s" % (server.server_port, rows[0]["path"])
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with patch.object(subprocess, "Popen", Counted):
                with opener.open(url + "&base=" + base, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("items", json.loads(response.read()))
            with self.assertRaises(urllib.error.HTTPError) as bad:
                opener.open(url + "&base=no-such-ref", timeout=10)
            self.assertEqual(bad.exception.code, 400)
            self.assertEqual(json.loads(bad.exception.read()), {"error": "invalid base"})
            bad.exception.close()
        finally:
            server.shutdown()
            server.server_close()
        resolves = [argv for argv in gits if "rev-parse" in argv and any(a.startswith(base) for a in argv)]
        self.assertEqual(len(resolves), 1, gits)


if __name__ == "__main__":
    unittest.main()
