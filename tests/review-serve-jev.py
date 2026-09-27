import importlib.util
import json
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_serve_jev_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)


class FakeProvider:
    def __init__(self, *answers, error=False):
        self.answers = list(answers)
        self.error = error
        self.calls = []

    def decide(self, payload):
        self.calls.append(payload)
        if self.error:
            raise RuntimeError("provider failed")
        value = self.answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class SlowProvider:
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return {"answers": {next(iter(payload["questions"])): {"choice": "behavioral", "confidence": 0.9}}}


def settled(service, *args, timeout=10):
    """Re-read, as the page does, until no item is pending (#fast-marks-background)."""
    deadline = time.monotonic() + timeout
    while True:
        result = service.response(*args)
        if not any(item["state"] == "pending" for item in result["items"]) or time.monotonic() > deadline:
            return result
        time.sleep(0.01)


class GatedProvider:
    """Blocks every ask until released; counts calls."""

    def __init__(self, fail=False):
        self.release = threading.Event()
        self.fail = fail
        self.calls = 0
        self.lock = threading.Lock()

    def decide(self, payload):
        with self.lock:
            self.calls += 1
        self.release.wait(10)
        if self.fail:
            raise RuntimeError("provider failed")
        return {"answers": {next(iter(payload["questions"])): {"choice": "behavioral", "confidence": 0.9}}}


class JevBackgroundTest(unittest.TestCase):
    """jev-suggestions #fast-marks-background, #state-pending, #state-error, #acceptance-background."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.stop()
        self.tmp.cleanup()

    def service(self, provider, count=20, api_key="fake"):
        service = jev.JevService(state_dir=self.tmp.name, provider=provider, api_key=api_key)
        self.services.append(service)
        questions = [{"kind": "type", "id": str(index), "state": {"after": str(index)},
                      "sources": [], "revision": "head"} for index in range(count)]
        service.questions = lambda *args, **kwargs: questions
        return service

    def read(self, service):
        return service.response({}, "", "", "base", [])

    def settled(self, service):
        return settled(service, {}, "", "", "base", [])

    def records(self):
        path = Path(self.tmp.name) / "records.jsonl"
        return path.read_text().splitlines() if path.exists() else []

    def test_first_read_is_pending_and_never_waits_for_jev(self):
        provider = GatedProvider()
        service = self.service(provider)
        try:
            started = time.monotonic()
            first = self.read(service)
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertEqual([item["state"] for item in first["items"]], ["pending"] * 20)
            self.assertTrue(all(item["label"] is None and item["record"] is None for item in first["items"]))
            self.assertEqual(len(service._asking), 20)
        finally:
            provider.release.set()
        second = self.settled(service)
        self.assertEqual([item["state"] for item in second["items"]], ["label"] * 20)
        self.assertEqual(provider.calls, 20)

    def test_read_while_asks_are_in_flight_starts_none(self):
        provider = GatedProvider()
        service = self.service(provider)
        try:
            self.read(service)
            asking = dict(service._asking)
            again = self.read(service)
            self.assertEqual(service._asking, asking)
            self.assertEqual([item["state"] for item in again["items"]], ["pending"] * 20)
        finally:
            provider.release.set()
        self.settled(service)
        self.read(service)
        self.assertEqual(provider.calls, 20)

    def test_failed_is_held_unavailable_during_its_pause_and_changed_inputs_ask_at_once(self):
        provider = GatedProvider(fail=True)
        provider.release.set()
        service = self.service(provider)
        held = self.settled(service)
        self.assertEqual([item["state"] for item in held["items"]], ["unavailable"] * 20)
        self.assertEqual(provider.calls, 20)
        again = self.read(service)
        self.assertEqual(again, held)
        self.assertEqual(provider.calls, 20)
        changed = [{"kind": "type", "id": "0", "state": {"after": "changed"}, "sources": [], "revision": "head"}]
        service.questions = lambda *args, **kwargs: changed
        self.assertEqual(self.read(service)["items"][0]["state"], "pending")
        self.settled(service)
        self.assertEqual(provider.calls, 21)

    def test_failed_ask_waits_its_pause_then_the_first_read_asks_again(self):
        """jev-suggestions #state-error: the one retry rule; no read asks during the pause, and no read waits."""
        now = [1000.0]
        provider = GatedProvider(fail=True)
        provider.release.set()
        with patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider, count=1)
            self.assertEqual(self.settled(service)["items"][0]["state"], "unavailable")
            self.assertEqual(provider.calls, 1)  # one call per ask: no retry inside it
            now[0] += jev.RETRY_PAUSE - 1
            self.assertEqual(self.read(service)["items"][0]["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(provider.calls, 1)
            now[0] += 2
            provider.fail = False
            provider.release.clear()
            started = time.monotonic()
            self.assertEqual(self.read(service)["items"][0]["state"], "pending")
            self.assertLess(time.monotonic() - started, 0.2)
            again = self.read(service)  # in flight: not asked twice
            self.assertEqual(again["items"][0]["state"], "pending")
            provider.release.set()
            self.assertEqual(self.settled(service)["items"][0]["state"], "label")
            self.assertEqual(provider.calls, 2)

    def test_provider_wait_is_the_pause(self):
        """A rate-limit Retry-After sets the pause, not the 60 s default."""
        now = [1000.0]

        class Limited(GatedProvider):
            def decide(self, payload):
                with self.lock:
                    self.calls += 1
                if self.fail:
                    raise jev.ProviderWait("rate limited", 300.0)
                return {"answers": {next(iter(payload["questions"])): {"choice": "behavioral", "confidence": 0.9}}}

        provider = Limited(fail=True)
        with patch.object(jev, "_wall", lambda: now[0]):
            service = self.service(provider, count=1)
            self.assertEqual(self.settled(service)["items"][0]["state"], "unavailable")
            calls = provider.calls
            now[0] += jev.RETRY_PAUSE + 1
            self.assertEqual(self.read(service)["items"][0]["state"], "unavailable")
            time.sleep(0.05)
            self.assertEqual(provider.calls, calls)
            now[0] += 300
            provider.fail = False
            self.assertEqual(self.settled(service)["items"][0]["state"], "label")
            self.assertEqual(provider.calls, calls + 1)

    def test_failed_ask_that_lands_mid_read_is_not_asked_again(self):
        """A read collects a miss; that key's ask fails and leaves before the read submits: not resubmitted."""
        provider = GatedProvider(fail=True)
        provider.release.set()
        service = self.service(provider, count=1)
        collect = service._held

        def held_then_answered(question, misses):
            record = collect(question, misses)
            service.seam.ask(question)  # the in-flight ask records `unavailable` and leaves _asking
            return record

        service._held = held_then_answered
        self.assertEqual(self.read(service)["items"][0]["state"], "pending")
        service._held = collect
        self.assertEqual(self.settled(service)["items"][0]["state"], "unavailable")
        self.assertEqual(provider.calls, 1)

    def test_off_record_is_asked_again_once_a_key_is_present(self):
        """The key went away while asks were queued: `off` is not an answer, so it is never held."""
        key = [""]
        provider = GatedProvider()
        provider.release.set()

        def read_key():  # present when the page reads (Jev on), removed by the time the ask runs
            return key[0] or ("fake" if threading.current_thread() is threading.main_thread() else "")

        service = self.service(provider, count=1, api_key=read_key)
        self.read(service)
        service.stop()  # waits for the ask: it recorded `off`
        self.assertEqual(json.loads(self.records()[-1])["outcome"], "off")
        service = self.service(provider, count=1, api_key=lambda: key[0])
        key[0] = "fake"
        self.assertEqual(self.settled(service)["items"][0]["state"], "label")
        self.assertEqual(provider.calls, 1)

    def test_stop_drops_queued_asks_and_nothing_writes_after_it(self):
        provider = GatedProvider()
        service = self.service(provider)
        try:
            self.read(service)
            queued = [future for future in service._asking.values() if not future.running()]
            stopper = threading.Thread(target=service.stop)
            stopper.start()
            deadline = time.monotonic() + 10
            while not all(future.cancelled() or future.running() for future in queued) and time.monotonic() < deadline:
                time.sleep(0.005)
        finally:
            provider.release.set()
        stopper.join(10)
        self.assertFalse(stopper.is_alive())
        self.assertLessEqual(provider.calls, jev.ASK_WORKERS)
        self.assertEqual(len(self.records()), provider.calls)  # in-flight asks finished their record
        written = self.records()
        after = self.read(service)
        self.assertEqual({item["state"] for item in after["items"]}, {"pending", "label"})
        time.sleep(0.05)
        self.assertEqual(self.records(), written)
        self.assertEqual(provider.calls, len(written))
        service.stop()  # idempotent


class JevSeamTest(unittest.TestCase):
    def question_sets(self):
        return {"type": jev.QuestionSet("type", 1, "Classify the change.",
                                         [{"name": "behavioral", "description": "Changes behavior."}], 0.8)}

    def test_below_threshold_is_unsure_and_record_is_redacted(self):
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.7}}})
        with tempfile.TemporaryDirectory() as directory:
            store = jev.JudgmentStore(Path(directory) / "records.jsonl")
            seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake", record_store=store)
            result = seam.ask({"kind": "type", "id": "rule", "state": {"after": "secret text"},
                               "sources": ["spec#rule"], "revision": "head"})
            self.assertEqual(result["outcome"], "unsure")
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(len(store.by_key), 1)
            encoded = json.dumps(next(iter(store.by_key.values())))
            self.assertNotIn("secret text", encoded)
            self.assertNotIn("fake", encoded)

    def test_failure_is_one_call_then_records_unavailable(self):
        provider = FakeProvider(error=True)
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        result = seam.ask({"kind": "type", "id": "rule", "state": {}, "sources": [], "revision": "head"})
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(seam.store.by_key), 1)

    def test_same_question_uses_record_cache(self):
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.9}}})
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        question = {"kind": "type", "id": "rule", "state": {"after": "new"}, "sources": ["spec#rule"], "revision": "head"}
        first, second = seam.ask(question), seam.ask(question)
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(provider.calls), 1)

    def test_cache_key_uses_text_inputs_not_revision_or_addresses(self):
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.9}}})
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        first = seam.ask({"kind": "type", "id": "rule", "state": {"after": "new"},
                          "sources": ["old.spec#rule"], "revision": {"base": "a", "head": "b"}})
        second = seam.ask({"kind": "type", "id": "rule", "state": {"after": "new"},
                           "sources": ["new.spec#rule"], "revision": {"base": "c", "head": "d"}})
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(provider.calls), 1)

    def test_failed_and_off_records_are_retried(self):
        provider = FakeProvider(RuntimeError("first"),
                                {"answers": {"type": {"choice": "behavioral", "confidence": 0.9}}})
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        question = {"kind": "type", "state": {"after": "new"}, "sources": [], "revision": "head"}
        failed = seam.ask(question)
        recovered = seam.ask(question)
        self.assertEqual(failed["outcome"], "unavailable")
        self.assertEqual(recovered["outcome"], "shown")
        self.assertEqual(len(provider.calls), 2)

        off = jev.JevSeam(self.question_sets(), provider=provider, api_key="")
        first_off = off.ask(question)
        second_off = off.ask(question)
        self.assertEqual(first_off["outcome"], "off")
        self.assertEqual(second_off["outcome"], "off")
        self.assertNotEqual(first_off["record_id"], second_off["record_id"])

    def test_judgment_store_skips_corrupt_and_truncated_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            path.write_bytes(
                b"not json\n"
                b'{"cache_key":"valid","outcome":"shown"}\n'
                b'{"cache_key":"truncated","outcome":"shown"\xe2'
            )
            store = jev.JudgmentStore(path)
        self.assertEqual(set(store.by_key), {"valid"})

    def test_judgment_store_treats_unreadable_state_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            with patch.object(Path, "is_file", return_value=True), patch.object(
                Path, "read_text", side_effect=OSError("unreadable state")
            ):
                store = jev.JudgmentStore(path)
        self.assertEqual(store.by_key, {})

    def test_resolved_unrelated_is_not_displayed(self):
        provider = FakeProvider({"answers": {"resolved": {"choice": "unrelated", "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            question = {"kind": "resolved", "id": "thread", "state": {}, "sources": [], "revision": "head"}
            service.questions = lambda *args, **kwargs: [question]
            result = settled(service, {}, "", "", "base", [])
        self.assertEqual(result["items"][0]["state"], "none")
        self.assertIsNone(result["items"][0]["label"])

    def test_levels_table_is_one_server_source_on_every_response(self):
        # jev-suggestions#markers-levels: Important and Warning mark kinds, fixed by kind.
        # #markers-levels-audience: a human and an agent column per kind; the agent level is never quieter.
        important = {"contradicts", "missed", "no-criterion", "no-story", "qa-failed", "qa-stale"}
        self.assertEqual({kind for kind, level in jev.MARK_LEVELS.items() if level["human"] == "important"}, important)
        self.assertEqual({kind for kind, level in jev.MARK_LEVELS.items() if level["agent"] == "important"}, important | {"oversteps"})
        for kind, level in jev.MARK_LEVELS.items():
            self.assertEqual(set(level), {"human", "agent"}, kind)
            self.assertLessEqual({level["human"], level["agent"]}, {"important", "warning"}, kind)
            self.assertFalse(level["human"] == "important" and level["agent"] == "warning", kind)
        with tempfile.TemporaryDirectory() as directory:
            off = jev.JevService(state_dir=directory, provider=None, api_key="")
            self.assertEqual(off.response({}, "", "", "base", []), {"jev": "off", "items": [], "levels": jev.MARK_LEVELS})
        for kind, choice, confidence, level, agent_level in (("corpus", "contradicts", 0.9, "important", "important"),
                                                              ("corpus", "oversteps", 0.9, "warning", "important"),
                                                              ("corpus", "unrelated", 0.9, None, None), ("type", "scope", 0.9, "warning", "warning"),
                                                              ("corpus", "contradicts", 0.01, None, None)):
            provider = FakeProvider({"answers": {kind: {"choice": choice, "confidence": confidence}}})
            with tempfile.TemporaryDirectory() as directory:
                service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
                question = {"kind": kind, "id": "clause", "state": {"after": choice}, "sources": [], "revision": "head"}
                service.questions = lambda *args, **kwargs: [question]
                result = settled(service, {}, "", "", "base", [])
            self.assertEqual(result["levels"], jev.MARK_LEVELS)
            self.assertEqual(result["items"][0].get("level"), level, (kind, choice, confidence))
            self.assertEqual(result["items"][0].get("agent_level"), agent_level, (kind, choice, confidence))
        # Changing one table row changes the JSON.
        provider = FakeProvider({"answers": {"corpus": {"choice": "oversteps", "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory, patch.dict(jev.MARK_LEVELS, {"oversteps": {"human": "important", "agent": "important"}}):
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            question = {"kind": "corpus", "id": "clause", "state": {"after": "x"}, "sources": [], "revision": "head"}
            service.questions = lambda *args, **kwargs: [question]
            result = settled(service, {}, "", "", "base", [])
        self.assertEqual((result["levels"]["oversteps"]["human"], result["items"][0]["level"]), ("important", "important"))

    def test_response_asks_uncached_questions_in_parallel(self):
        provider = SlowProvider()
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            questions = [{"kind": "type", "id": str(index), "state": {"after": str(index)},
                          "sources": [], "revision": "head"} for index in range(4)]
            service.questions = lambda *args, **kwargs: questions
            result = settled(service, {}, "", "", "base", [])
        self.assertEqual(len(result["items"]), 4)
        self.assertEqual(provider.calls, 4)
        self.assertGreater(provider.max_active, 1)

    def test_coverage_builder_pairs_every_story_with_every_criterion(self):
        source = """
        <p data-user-story data-anchor="story-one">As a reviewer, I see the flag.</p>
        <p data-user-story data-anchor="story-two">As a reader, I see the text.</p>
        <p data-acceptance-criterion data-anchor="criterion-one">The flag appears.</p>
        <p data-acceptance-criterion data-anchor="criterion-two">The text stays readable.</p>
        """
        questions = jev.build_coverage_questions(source, "spec.html", "base", "head")
        self.assertEqual({question["id"] for question in questions}, {
            "story-one::criterion-one", "story-one::criterion-two",
            "story-two::criterion-one", "story-two::criterion-two",
        })
        self.assertTrue(all(question["kind"] == "coverage" for question in questions))
        self.assertEqual(questions[0]["sources"], ["spec.html#story-one", "spec.html#criterion-one"])

    def test_coverage_builder_emits_missing_side_questions(self):
        stories = '<p data-user-story data-anchor="story-one">Story</p><p data-user-story data-anchor="story-two">Story</p>'
        criteria = '<p data-acceptance-criterion data-anchor="criterion-one">Criterion</p>'
        story_questions = jev.build_coverage_questions(stories, "spec.html", "base", "head")
        criterion_questions = jev.build_coverage_questions(criteria, "spec.html", "base", "head")
        self.assertEqual({(item["story"], item["criterion"]) for item in story_questions},
                         {( "story-one", None), ("story-two", None)})
        self.assertEqual({(item["story"], item["criterion"]) for item in criterion_questions},
                         {(None, "criterion-one")})

    def test_type_change_in_anchored_child_changes_section(self):
        baseline = '<section data-anchor="root"><h2 data-anchor="heading">Same</h2><p data-anchor="clause">Before</p></section>'
        current = baseline.replace("Before", "After")
        questions = jev.build_type_questions(current, baseline, "spec.html", "base", "head")
        self.assertEqual([question["id"] for question in questions], ["root"])
        self.assertIn("After", questions[0]["state"]["after"])

    def test_resolved_uses_text_at_newest_human_message(self):
        before = '<section data-anchor="rules"><p data-anchor="clause">Requested behavior</p></section>'
        current = '<section data-anchor="rules"><p data-anchor="clause">Implemented behavior</p></section>'
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "rules",
            "text": "Please implement the behavior",
        }}]
        questions = jev.build_resolved_questions(events, current, current, "spec.html", "base", "head",
                                                  {"u1": before})
        self.assertEqual([question["id"] for question in questions], ["u1"])
        self.assertEqual(questions[0]["state"]["before"], "Requested behavior")

    def test_edit_replaces_superseded_human_message(self):
        current = '<section data-anchor="rules"><p data-anchor="clause">Implemented behavior</p></section>'
        events = [
            {"name": "1-comment.json", "actor": "human", "body": {
                "id": "u1", "event": "comment", "actor": "human", "anchorId": "rules",
                "text": "Please implement the old behavior",
            }},
            {"name": "2-edit.json", "actor": "human", "body": {
                "id": "e1", "event": "edit", "actor": "human", "threadId": "u1",
                "supersedes": "u1", "anchorId": "rules", "text": "Please implement the revised behavior",
            }},
        ]
        questions = jev.build_resolved_questions(events, current, current, "spec.html", "base", "head",
                                                  {"e1": '<section data-anchor="rules"><p data-anchor="clause">Requested behavior</p></section>'})
        self.assertEqual(questions[0]["state"]["comment"], "Please implement the revised behavior")
        self.assertNotIn("old behavior", questions[0]["state"]["comment"])

    def test_anchor_parser_handles_void_and_implied_end_tags(self):
        source = '<section data-anchor="one"><p data-anchor="first">First<br>text</p><p data-anchor="second">Second</p></section>'
        anchors = jev.extract_anchors(source)
        self.assertEqual(anchors["first"]["text"], "First text")
        self.assertEqual(anchors["second"]["text"], "Second")
        self.assertIn("First text Second", anchors["one"]["text"])

    def test_anchor_parsers_ignore_script_and_style_text(self):
        source = '<section data-anchor="one"><p data-anchor="clause">Before<script>secret()</script><style>.x{color:red}</style>After</p></section>'
        self.assertEqual(jev.extract_anchors(source)["clause"]["text"], "BeforeAfter")
        self.assertEqual(jev._audience_leaf_anchors(source)["clause"]["text"], "BeforeAfter")

    def test_malformed_responds_to_is_skipped(self):
        events = [
            {"name": "1-comment.json", "actor": "human", "body": {
                "id": "u1", "event": "comment", "actor": "human", "anchorId": "removed",
                "text": "Please keep this",
            }},
            {"name": "2-reply.json", "actor": "human", "body": {
                "id": "u2", "event": "reply", "actor": "human", "respondsTo": [1],
                "threadId": "u1", "text": "malformed",
            }},
        ]
        questions = jev.build_orphan_questions(events, '<p data-anchor="current">Current clause</p>')
        self.assertEqual(questions, [])

    def test_deleted_orphan_without_overlap_has_no_question(self):
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "deleted", "event": "comment", "actor": "human", "anchorId": "removed",
            "quote": "This passage is deleted in the current head.",
        }}]
        questions = jev.build_orphan_questions(events, '<p data-anchor="today">Today\'s card.</p>')
        self.assertEqual(questions, [])

    def test_non_string_event_is_skipped(self):
        events = [{"name": "1-invalid.json", "actor": "human", "body": {
            "id": "u1", "event": [], "actor": "human", "anchorId": "removed",
            "text": "Please keep this",
        }}]
        self.assertEqual(jev._human_threads(events), [])

    def test_orphan_provider_receives_candidate_text(self):
        source = '<p data-anchor="new-section">Server retries once on timeout.</p>'
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "old-section",
            "quote": "retry once on timeout",
        }}]
        questions = jev.build_orphan_questions(events, source)
        self.assertEqual(len(questions), 1)

        class CandidateProvider:
            def __init__(self):
                self.calls = []

            def decide(self, payload):
                self.calls.append(payload)
                criteria = payload["questions"]["orphan"]["criteria"]
                if criteria:
                    candidate = next(item for item in criteria if item != "none")
                    assert criteria[candidate] == "Server retries once on timeout."
                    assert "none" in criteria
                    choice = candidate
                return {"answers": {"orphan": {"choice": choice, "confidence": 0.9}}}

        provider = CandidateProvider()
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            result = service.seam.ask(questions[0])
        self.assertEqual(result["outcome"], "shown")
        self.assertEqual(result["answer"]["label"], "new-section")
        self.assertEqual(len(provider.calls), 1)

    def test_orphan_record_holds_anchors_not_candidate_text(self):
        source = ('<p data-anchor="new-section">Server retries once on timeout.</p>'
                  '<p data-anchor="other-section">Timeout handling retries nothing once.</p>')
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "old-section",
            "quote": "retry once on timeout",
        }}]
        question = jev.build_orphan_questions(events, source)[0]
        labels = question["state"]["candidates"]
        self.assertEqual(len(labels), 2)
        target = next(label for label, anchor in question["candidate_anchors"].items() if anchor == "new-section")
        other = next(label for label in labels if label != target)
        provider = FakeProvider({"answers": {"orphan": {"choice": target, "confidence": 0.8,
                                                        "probabilities": {target: 0.8, other: 0.15, "none": 0.05}}}})
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            service.questions = lambda *args: [question]
            result = settled(service, {}, "", "", "", [], "")
            stored = (Path(directory) / "records.jsonl").read_text()
        record = json.loads(stored.strip().splitlines()[-1])
        self.assertEqual(record["answer"]["label"], "new-section")
        self.assertEqual(record["answer"]["probabilities"], {"new-section": 0.8, "other-section": 0.15, "none": 0.05})
        for text in labels + ["Server retries once on timeout", "Timeout handling retries nothing once", "retry once on timeout"]:
            self.assertNotIn(text, stored)
        self.assertEqual(result["items"][0]["state"], "label")
        self.assertEqual(result["items"][0]["target"], "new-section")
        self.assertEqual(result["items"][0]["label"], target)

    def test_provider_receives_structured_examples(self):
        sets = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.9}}})
        seam = jev.JevSeam(sets, provider=provider, api_key="fake")
        seam.ask({"kind": "type", "state": {"before": "old", "after": "new"}, "sources": [], "revision": "head"})
        question = provider.calls[0]["questions"]["type"]
        self.assertIsInstance(question["criteria"]["behavioral"], dict)
        example = question["criteria"]["behavioral"]["examples"][0]
        self.assertEqual(set(example), {"input", "label"})
        self.assertIsInstance(example["input"], dict)
        self.assertIn("before", example["input"])
        self.assertIn("after", example["input"])

    def test_orphan_readable_answer_maps_back_to_anchor(self):
        source = '<p data-anchor="new-section">Server retries once on timeout.</p>'
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "old-section",
            "quote": "retry once on timeout",
        }}]
        question = jev.build_orphan_questions(events, source)[0]
        label = question["state"]["candidates"][0]
        provider = FakeProvider({"answers": {"orphan": {"choice": label, "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            service.questions = lambda *args: [question]
            result = settled(service, {}, "", "", "", [], "")
        self.assertEqual(result["items"][0]["label"], label)
        self.assertEqual(result["items"][0]["target"], "new-section")

    def test_orphan_none_answer_has_no_target_suggestion(self):
        source = '<p data-anchor="new-section">Server retries once on timeout.</p>'
        events = [{"name": "1-comment.json", "actor": "human", "body": {
            "id": "u1", "event": "comment", "actor": "human", "anchorId": "old-section",
            "quote": "retry once on timeout",
        }}]
        question = jev.build_orphan_questions(events, source)[0]
        provider = FakeProvider({"answers": {"orphan": {"choice": "none", "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory:
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            service.questions = lambda *args: [question]
            result = settled(service, {}, "", "", "", [], "")
        self.assertEqual(result["items"], [{"kind": "orphan", "id": "u1", "state": "none",
                                            "label": None, "target": None, "record": unittest.mock.ANY}])

    def test_orphan_uses_root_quote_after_human_reply(self):
        source = (
            '<p data-anchor="retry">Server retries once on timeout.</p>'
            '<p data-anchor="toolbar">The toolbar toggle stays visible.</p>'
        )
        events = [
            {"name": "1-comment.json", "actor": "human", "body": {
                "id": "u1", "event": "comment", "actor": "human", "anchorId": "old",
                "quote": "Server retries once on timeout",
            }},
            {"name": "2-reply.json", "actor": "agent", "body": {
                "id": "a1", "event": "reply", "actor": "agent", "threadId": "u1",
                "text": "I will check this.",
            }},
            {"name": "3-reply.json", "actor": "human", "body": {
                "id": "u2", "event": "reply", "actor": "human", "threadId": "u1",
                "quote": None, "text": "Also check the toolbar toggle.",
            }},
        ]
        questions = jev.build_orphan_questions(events, source)
        self.assertEqual(questions[0]["state"]["quote"], "Server retries once on timeout")
        self.assertEqual(questions[0]["state"]["candidates"][0], "Server retries once on timeout")

    def test_served_specs_follow_index_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            narrow = root / "docs"
            narrow.mkdir()
            current = narrow / "current.spec.html"
            current.write_text("current", encoding="utf-8")
            (narrow / "kept.spec.html").write_text("kept", encoding="utf-8")
            for folder in ("fixtures", "evidence", "support"):
                excluded = narrow / folder
                excluded.mkdir()
                (excluded / "hidden.spec.html").write_text("hidden", encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            secret = outside / "secret.spec.html"
            secret.write_text("secret", encoding="utf-8")
            (narrow / "link.spec.html").symlink_to(secret)
            service = object.__new__(jev.JevService)
            mount = {"root": str(root), "narrow_root": str(narrow)}
            served = service._served_specs([mount], str(current))
            self.assertEqual({item["path"] for item in served}, {"kept.spec.html"})
            self.assertNotIn("secret", {item["source"] for item in served})

    def test_builder_failure_is_isolated_to_its_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "-C", str(root), "init", "-q"), check=True)
            spec = root / "spec.html"
            spec.write_text('<section data-anchor="root">Before</section>\n', encoding="utf-8")
            subprocess.run(("git", "-C", str(root), "add", "spec.html"), check=True)
            subprocess.run(("git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "base"), check=True)
            base = subprocess.check_output(("git", "-C", str(root), "rev-parse", "HEAD"), text=True).strip()
            spec.write_text('<section data-anchor="root">After</section>\n', encoding="utf-8")
            service = object.__new__(jev.JevService)
            original = jev.BUILDERS["orphan"]
            jev.BUILDERS["orphan"] = lambda *args: (_ for _ in ()).throw(TypeError("bad event"))
            try:
                questions = service.questions({"root": str(root)}, str(spec), "spec.html", base, [], "", [])
            finally:
                jev.BUILDERS["orphan"] = original
            self.assertIn("type", {question["kind"] for question in questions})

    def test_message_sources_ignore_browser_revision_and_use_base_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(("git", "-C", str(root), "init", "-q"), check=True)
            spec = root / "spec.html"
            spec.write_text("<p data-anchor=\"rule\">Base text</p>\n", encoding="utf-8")
            subprocess.run(("git", "-C", str(root), "add", "spec.html"), check=True)
            subprocess.run(("git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-qm", "base"), check=True)
            base = subprocess.check_output(("git", "-C", str(root), "rev-parse", "HEAD"), text=True).strip()
            service = object.__new__(jev.JevService)
            events = [{"name": "1-comment.json", "actor": "human", "body": {
                "id": "u1", "event": "comment", "actor": "human", "anchorId": "rule",
                "revision": "--output=PWNED", "commit": "--output=PWNED",
            }}]
            sources = service._message_sources(str(root), "spec.html", base, events)
            self.assertEqual(sources["u1"], spec.read_bytes())
            self.assertFalse((root / "PWNED:spec.html").exists())

    def test_coverage_response_accepts_pair_answer_and_keeps_addresses_only(self):
        provider = FakeProvider({"answers": {"coverage": {"choice": "verifies", "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory:
            store = jev.JudgmentStore(Path(directory) / "records.jsonl")
            seam = jev.JevSeam(provider=provider, api_key="fake", record_store=store)
            question = jev.build_coverage_questions(
                '<p data-user-story data-anchor="story">Story words</p>'
                '<p data-acceptance-criterion data-anchor="criterion">Criterion words</p>',
                "spec.html", "base", "head",
            )[0]
            record = seam.ask(question)
            self.assertEqual(record["outcome"], "shown")
            self.assertEqual(record["answer"]["label"], "verifies")
            encoded = json.dumps(record)
            self.assertIn("spec.html#story", encoded)
            self.assertIn("spec.html#criterion", encoded)
            self.assertNotIn("Story words", encoded)
            self.assertNotIn("Criterion words", encoded)

    def test_corpus_questions_cover_changed_leaves_non_goals_and_other_specs(self):
        baseline = """
        <section data-anchor="rules"><p data-anchor="changed">The service reads review events.</p></section>
        <section data-anchor="non-goals"><p data-anchor="non-goal-text">The service never writes review events.</p></section>
        """
        current = baseline.replace("reads review events", "writes review events")
        other = """
        <section data-anchor="other"><p data-anchor="same-surface" data-modular-boundary>The review service reads review events.</p></section>
        """
        questions = jev.build_corpus_questions(current, baseline, "docs/current.spec.html", "base", "head",
                                               [{"path": "docs/other.spec.html", "source": other}])
        targets = {question["target"] for question in questions}
        self.assertIn("non-goal-text", targets)
        self.assertIn("docs/other.spec.html#same-surface", targets)
        non_goal = {question["target"]: question["state"]["target_non_goal"] for question in questions}
        self.assertTrue(non_goal["non-goal-text"])
        self.assertFalse(non_goal["docs/other.spec.html#same-surface"])
        changed = [question for question in questions if question["id"] == "changed"]
        self.assertTrue(changed)
        self.assertTrue(all(question["sources"][0].endswith("#changed") for question in changed))

    def test_corpus_candidates_stay_within_spec_cap(self):
        def leaves(prefix, count, boundary_every):
            return "".join(
                f'<p data-anchor="{prefix}-{index}"{" data-modular-boundary" if index % boundary_every == 0 else ""}>'
                f"Clause {prefix} {index} about review service events.</p>" for index in range(count))
        own = ('<section data-anchor="rules">' + leaves("own", 20, 2) + '</section>'
               '<section data-anchor="non-goals"><p data-anchor="non-goal-a">Never writes events.</p>'
               '<p data-anchor="non-goal-b">Never polls.</p></section>')
        baseline = own + '<p data-anchor="changed">The service reads review events.</p>'
        current = own + '<p data-anchor="changed">The service writes review events.</p>'
        others = [{"path": f"docs/other-{n}.spec.html",
                   "source": '<section data-anchor="s">' + leaves(f"o{n}", 15, 1) + '</section>'} for n in range(3)]
        questions = jev.build_corpus_questions(current, baseline, "docs/current.spec.html", "base", "head", others)
        own_targets = [q["target"] for q in questions if "#" not in q["target"]]
        cross_targets = [q["target"] for q in questions if "#" in q["target"]]
        self.assertEqual({q["id"] for q in questions}, {"changed"})
        self.assertLessEqual(len(own_targets), 8 + 2)
        self.assertIn("non-goal-a", own_targets)
        self.assertIn("non-goal-b", own_targets)
        self.assertLessEqual(len(cross_targets), 8)
        for question in questions:
            self.assertEqual(set(question["state"]), {"before", "after", "target", "target_non_goal"})
            self.assertEqual(question["state"]["target_non_goal"], question["target"].startswith("non-goal-"))

        fresh = jev.build_corpus_questions(current, None, "docs/current.spec.html", "base", "head", others)
        per_leaf = {}
        for question in fresh:
            per_leaf[question["id"]] = per_leaf.get(question["id"], 0) + 1
        self.assertTrue(per_leaf)
        self.assertLessEqual(max(per_leaf.values()), 8 + 2 + 8)

    def test_corpus_question_set_has_all_relationship_labels(self):
        sets = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")
        self.assertEqual(set(sets["corpus"].criteria()),
                         {"contradicts", "overlaps", "oversteps", "unrelated"})


if __name__ == "__main__":
    unittest.main()
