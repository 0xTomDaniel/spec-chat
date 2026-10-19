"""Asymmetric threshold and LLM verifier for draft-check questions
(jev-suggestions #asymmetric, #verifier, #proof-asymmetric, #proof-verifier)."""

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_asymmetric_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
SETS = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")

BASE = '<section data-anchor="s"><p data-anchor="new">Old words.</p><p data-anchor="other">Other clause here.</p></section>'
CURRENT = '<section data-anchor="s"><p data-anchor="new">New clause words here.</p><p data-anchor="other">Other clause here.</p></section>'
# Draft checks pair only with clauses of other specs (#corpus-same-spec).
OTHER = '<section data-anchor="s"><p data-anchor="other">Other clause here.</p><p data-anchor="non-goal">Offline.</p></section>'
SERVED = [{"path": "other.spec.html", "source": OTHER}]


class AsymmetricProvider:
    """Jev answers with per-kind P(yes); the general LLM returns a verifier response."""

    def __init__(self, p_yes_map=None, verifier_response=None):
        self.p_yes_map = p_yes_map or {}
        self.default_p_yes = 0.95
        self.verifier_response = verifier_response
        self.decide_calls = []
        self.complete_calls = []
        self.lock = threading.Lock()

    def decide(self, payload):
        kind = next(iter(payload["questions"]))
        with self.lock:
            self.decide_calls.append(kind)
        p_yes = self.p_yes_map.get(kind, self.default_p_yes)
        choice = "yes" if p_yes >= 0.5 else "no"
        return {"model": "fake-jev", "answers": {kind: {
            "choice": choice,
            "confidence": p_yes if choice == "yes" else 1 - p_yes,
            "probabilities": {"yes": p_yes, "no": 1 - p_yes}}}}

    def complete(self, payload):
        with self.lock:
            self.complete_calls.append(payload)
        if self.verifier_response is None:
            raise RuntimeError("no verifier configured")
        return {"choices": [{"message": {"content": json.dumps(self.verifier_response)}}]}


def draft_check():
    items = [item for item in jev.build_corpus_questions(CURRENT, BASE, "spec.html", "base", "head", SERVED)
             if item["target"] == "other.spec.html#other"]
    return items[0]


class AsymmetricThresholdTest(unittest.TestCase):
    """#proof-asymmetric: fake Jev at P(yes)=0.95 shows directly; P(yes)=0.80 calls verifier;
    P(yes)=0.50 silent no (no verifier, no fallback)."""

    def seam(self, provider):
        return jev.JevSeam(SETS, provider=provider, api_key="fake")

    def test_show_directly(self):
        """P(yes)=0.95 >= show_cutoff(0.90): show the mark directly, no verifier."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(p_yes_map={"contradicts": 0.95})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "shown")
        self.assertEqual(record["answer"]["label"], "yes")
        self.assertEqual(provider.complete_calls, [])

    def test_verify_band(self):
        """P(yes)=0.80 in [verify_cutoff, show_cutoff): verifier is called."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "x", "target_span": "y"})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "verified")
        self.assertEqual(len(provider.complete_calls), 1)
        self.assertTrue(record.get("escalated"))

    def test_silent_no(self):
        """P(yes)=0.50 < verify_cutoff(0.70): silent no, no verifier, no fallback."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(p_yes_map={"contradicts": 0.50})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "silent-no")
        self.assertEqual(provider.complete_calls, [])
        self.assertFalse(record.get("escalated"))

    def test_silent_no_full_chain(self):
        """about(yes) then every P(yes) below verify runs the chain to no mark."""
        chain = draft_check()["chain"]
        provider = AsymmetricProvider(
            p_yes_map={"about": 0.95, "contradicts": 0.50, "oversteps": 0.50, "overlaps": 0.50})
        result = self.seam(provider).ask_chain(chain)
        self.assertIsNone(result["answer"]["label"])
        self.assertEqual(result["outcome"], "shown")
        self.assertEqual(provider.complete_calls, [])
        self.assertEqual(sorted(provider.decide_calls), ["about", "contradicts", "overlaps", "oversteps"])


class VerifierTest(unittest.TestCase):
    """#proof-verifier: fake verifier returning yes+spans shows mark; returning no or
    yes-without-spans does not show."""

    def seam(self, provider):
        return jev.JevSeam(SETS, provider=provider, api_key="fake")

    def test_verifier_yes_with_spans(self):
        """Verifier yes + quoted spans from each clause: mark shows."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "text from clause", "target_span": "text from target"})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "verified")
        self.assertEqual(record["answer"]["label"], "yes")
        self.assertEqual(record["answer"]["clause_span"], "text from clause")
        self.assertEqual(record["answer"]["target_span"], "text from target")
        self.assertEqual(record["verifier"], {"ran": True, "answer": "yes",
                                              "spans": {"clause": "text from clause", "target": "text from target"}})
        result = self.seam(provider).ask_chain(chain)
        self.assertEqual(result["answer"]["label"], "contradicts")

    def test_verifier_no(self):
        """Verifier no: mark does not show."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "no", "clause_span": "", "target_span": ""})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "verifier-rejected")
        self.assertEqual(record["answer"]["label"], "no")
        self.assertEqual(record["verifier"], {"ran": True, "answer": "no", "spans": {"clause": "", "target": ""}})
        result = self.seam(provider).ask_chain(chain)
        self.assertNotEqual(result["answer"]["label"], "contradicts")

    def test_verifier_yes_without_spans(self):
        """Verifier yes without quoted spans: mark does not show."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "", "target_span": ""})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "verifier-rejected")
        self.assertEqual(record["answer"]["label"], "no")
        self.assertEqual(record["verifier"], {"ran": True, "answer": "yes", "spans": {"clause": "", "target": ""}})

    def test_verifier_record_fields(self):
        """Verifier record has kind=draft-check kind, model=LLM model (#verifier-record)."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "a", "target_span": "b"})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["kind"], "contradicts")
        self.assertNotEqual(record["model"], "fake-jev")  # LLM model, not Jev model

    def test_verifier_sends_the_sets_examples(self):
        """The verifier reads the same set's examples, no second example store (#qset-examples)."""
        qset = SETS["overlaps"]
        payload = self.seam(AsymmetricProvider()).verifier_payload({"state": {"after": "a", "target": "b"}}, qset, "m")
        system = json.loads(payload["messages"][0]["content"])
        self.assertEqual(system["examples"], qset.examples())
        self.assertTrue(any(example["label"] == "no" for example in system["examples"]))


def default_verifier_payload(question, qset, model):
    """The verifier payload of a set without verify levels, as it was before levels (contradicts, oversteps)."""
    state = question.get("state", {})
    verify_input = {"clause": state.get("after", ""), "target": state.get("target", "")}
    if state.get("context"):
        verify_input["clause_context"] = state["context"]
    if state.get("target_context"):
        verify_input["target_context"] = state["target_context"]
    system = {"question": qset.instructions, "examples": qset.examples(),
              "verify": "If yes, quote the exact span from the clause and the target that conflict. If no, leave spans empty."}
    schema = {"type": "object",
              "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                             "clause_span": {"type": "string"}, "target_span": {"type": "string"}},
              "required": ["answer", "clause_span", "target_span"], "additionalProperties": False}
    return {"model": model,
            "messages": [{"role": "system", "content": jev._canonical(system)},
                         {"role": "user", "content": jev._canonical(verify_input)}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
            "provider": {"require_parameters": True}}


class VerifyLevelsTest(unittest.TestCase):
    """#proof-verifier, #verifier-role, #verifier-scope: on the overlaps set a verifier returning partial with spans
    shows Partial overlap, same shows Overlaps, different shows nothing; contradicts and oversteps keep yes/no."""

    def seam(self, provider):
        return jev.JevSeam(SETS, provider=provider, api_key="fake")

    def overlaps(self, said, clause_span="shared words", target_span="shared words too"):
        chain = draft_check()["chain"]
        provider = AsymmetricProvider(
            p_yes_map={"about": 0.95, "contradicts": 0.10, "oversteps": 0.10, "overlaps": 0.80},
            verifier_response={"answer": said, "clause_span": clause_span, "target_span": target_span})
        seam = self.seam(provider)
        return seam.ask(chain[3]), seam.ask_chain(chain), provider

    def test_partial_shows_partial_overlap(self):
        record, result, _ = self.overlaps("partial")
        self.assertEqual(record["outcome"], jev.VERIFIED)
        self.assertEqual(record["answer"]["label"], "yes")
        self.assertEqual(record["answer"]["level"], "partial")
        self.assertEqual(record["verifier"], {"ran": True, "answer": "partial",
                                              "spans": {"clause": "shared words", "target": "shared words too"}})
        self.assertEqual(result["answer"]["label"], "partial-overlap")
        self.assertNotIn("partial-overlap", jev.MARK_LEVELS)  # None/None: a quiet grey dot (#level-partial)

    def test_partial_without_spans_shows_nothing(self):
        record, result, _ = self.overlaps("partial", "", "")
        self.assertEqual(record["outcome"], jev.VERIFIER_REJECTED)
        self.assertNotIn("level", record["answer"])
        self.assertIsNone(result["answer"]["label"])

    def test_same_shows_overlaps(self):
        record, result, _ = self.overlaps("same")
        self.assertEqual(record["outcome"], jev.VERIFIED)
        self.assertNotIn("level", record["answer"])
        self.assertEqual(result["answer"]["label"], "overlaps")

    def test_different_shows_nothing(self):
        record, result, _ = self.overlaps("different", "", "")
        self.assertEqual(record["outcome"], jev.VERIFIER_REJECTED)
        self.assertEqual(record["verifier"]["answer"], "different")
        self.assertIsNone(result["answer"]["label"])

    def test_overlaps_payload_asks_its_levels(self):
        _, _, provider = self.overlaps("same")
        payload = provider.complete_calls[0]
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["answer"]["enum"], ["same", "partial", "different"])
        system = json.loads(payload["messages"][0]["content"])
        qset = SETS["overlaps"]
        self.assertEqual(system["verify"], qset.verify["rule"])
        self.assertEqual(set(system["levels"]), {"same", "partial", "different"})
        self.assertEqual(system["labels"], qset.criteria())

    def test_sets_without_levels_keep_their_payload(self):
        """Contradicts and oversteps define no levels: their verifier payload is byte-identical to before."""
        chain = draft_check()["chain"]
        seam = self.seam(AsymmetricProvider())
        for kind in ("contradicts", "contradicts-nongoal", "oversteps"):
            with self.subTest(kind=kind):
                qset = SETS[kind]
                self.assertIsNone(qset.verify)
                self.assertEqual(json.dumps(seam.verifier_payload(chain[1], qset, "m")),
                                 json.dumps(default_verifier_payload(chain[1], qset, "m")))

    def test_partial_item_has_no_level(self):
        """The served item is labeled partial-overlap with no level or agent level: a quiet grey dot (#level-partial)."""
        with tempfile.TemporaryDirectory() as directory:
            provider = AsymmetricProvider(
                p_yes_map={"about": 0.95, "contradicts": 0.10, "oversteps": 0.10, "overlaps": 0.80},
                verifier_response={"answer": "partial", "clause_span": "a", "target_span": "b"})
            service = jev.JevService(state_dir=directory, provider=provider, api_key="fake")
            self.addCleanup(service.stop)
            draft = draft_check()
            service.questions = lambda *args, **kwargs: [draft]
            service.seam.ask_chain(draft["chain"])
            item = service.response({}, "", "", "base", [])["items"][0]
            self.assertEqual((item["state"], item["label"], item["target"]), ("label", "partial-overlap", "other.spec.html#other"))
            self.assertNotIn("level", item)
            self.assertNotIn("agent_level", item)

    def test_lane_partial_overlap_is_no_finding(self):
        """A cross-lane overlaps answer, partial or same, is no finding (#cross-lane-finding)."""
        self.assertNotIn("partial-overlap", jev.LANE_FINDINGS)

    def test_overlaps_levels_are_a_new_version(self):
        """#qset-data, #cache-key: adding verify levels is a new set version, so held v5 Overlaps records miss."""
        self.assertEqual(SETS["overlaps"].version, 6)

    def test_invalid_verify_levels_rejected(self):
        for verify in ({}, {"levels": {}}, {"levels": {"a": {"shows": "maybe"}}}, {"levels": {"a": "x"}}, "x"):
            with self.subTest(verify=verify):
                with self.assertRaises(ValueError):
                    jev.QuestionSet.from_mapping({"id": "s", "version": 1, "verify": verify}, "s")


class HeldEscalationTest(unittest.TestCase):
    """#state-error: an escalated record left without its answer (the ask crashed after recording it) is
    pending on every read path and asked again through the escalated fast-path, never a lasting unavailable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def service(self, provider, questions):
        service = jev.JevService(state_dir=tempfile.mkdtemp(dir=self.tmp.name), provider=provider, api_key="fake")
        self.addCleanup(service.stop)
        service.questions = lambda *args, **kwargs: questions
        return service

    def crash_left(self, service, step):
        """Record the step escalated, as the old ask did before its verifier raised."""
        def crash(*args, **kwargs):
            raise AttributeError("crash after escalated was recorded")
        service.seam._verify = crash
        with self.assertRaises(AttributeError):
            service.seam.ask(step)
        del service.seam._verify
        self.assertEqual(service.seam.store.get(service.seam.key(step))["outcome"], jev.ESCALATED)

    def settled(self, service):
        deadline = time.monotonic() + 10
        while True:
            result = service.response({}, "", "", "base", [])
            if all(item["state"] != "pending" for item in result["items"]) or time.monotonic() > deadline:
                return result
            time.sleep(0.01)

    def test_escalated_draft_check_is_pending_then_asked_again(self):
        draft = draft_check()
        provider = AsymmetricProvider(
            p_yes_map={"about": 0.95, "contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "a", "target_span": "b"})
        for question in (draft, dict(draft["chain"][1], id="single")):
            with self.subTest(chain="chain" in question):
                service = self.service(provider, [question])
                service.seam.ask(draft["chain"][0])  # the about gate answered yes: the chain waits on contradicts
                self.crash_left(service, draft["chain"][1])
                calls = len(provider.complete_calls)
                first = service.response({}, "", "", "base", [])
                self.assertEqual(first["items"][0]["state"], "pending")
                self.settled(service)
                self.assertEqual(len(provider.complete_calls), calls + 1)
                held = service.seam.store.get(service.seam.key(draft["chain"][1]))
                self.assertEqual(held["outcome"], jev.VERIFIED)
                if "chain" in question:
                    self.assertEqual(self.settled(service)["items"][0]["label"], "contradicts")

    def test_escalated_lane_step_is_a_miss(self):
        provider = AsymmetricProvider(p_yes_map={"contradicts": 0.80})
        service = self.service(provider, [])
        step = draft_check()["chain"][1]
        self.crash_left(service, step)
        chain = {"kind": "lane", "pair": ("a", "b"), "chain": [step]}
        service._material_questions = lambda row: []
        service._lane_questions = lambda rows, **kwargs: [chain]
        _, misses, _ = service._board_from_records([{"slug": "a", "spec": "x", "id": "1"}])
        self.assertIn(service.seam.key(step), misses)


if __name__ == "__main__":
    unittest.main()
