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
    items = [item for item in jev.build_corpus_questions(CURRENT, BASE, "spec.html", "base", "head")
             if item["target"] == "other"]
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
        """Same-spec: about(yes) then contradicts P(yes) below verify ends chain, no mark."""
        chain = draft_check()["chain"]
        provider = AsymmetricProvider(
            p_yes_map={"about": 0.95, "contradicts": 0.50})
        result = self.seam(provider).ask_chain(chain)
        self.assertIsNone(result["answer"]["label"])
        self.assertEqual(result["outcome"], "shown")
        self.assertEqual(provider.complete_calls, [])
        self.assertEqual(sorted(provider.decide_calls), ["about", "contradicts"])


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
        _, misses = service._board_from_records([{"slug": "a", "spec": "x", "id": "1"}])
        self.assertIn(service.seam.key(step), misses)


if __name__ == "__main__":
    unittest.main()
