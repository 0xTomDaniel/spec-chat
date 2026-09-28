"""Asymmetric threshold and LLM verifier for draft-check questions
(jev-suggestions #asymmetric, #verifier, #proof-asymmetric, #proof-verifier)."""

import importlib.util
import json
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_asymmetric_test", ROOT / "tools" / "jev.py")
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
        self.assertEqual(record["outcome"], "shown")
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
        """All draft-check P(yes) below verify: chain continues through all questions, no mark."""
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
        self.assertEqual(record["outcome"], "shown")
        self.assertEqual(record["answer"]["label"], "yes")
        self.assertEqual(record["answer"]["clause_span"], "text from clause")
        self.assertEqual(record["answer"]["target_span"], "text from target")

    def test_verifier_no(self):
        """Verifier no: mark does not show."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "no", "clause_span": "", "target_span": ""})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "shown")
        self.assertEqual(record["answer"]["label"], "no")

    def test_verifier_yes_without_spans(self):
        """Verifier yes without quoted spans: mark does not show."""
        chain = draft_check()["chain"]
        contradicts_step = chain[1]
        provider = AsymmetricProvider(
            p_yes_map={"contradicts": 0.80},
            verifier_response={"answer": "yes", "clause_span": "", "target_span": ""})
        record = self.seam(provider).ask(contradicts_step)
        self.assertEqual(record["outcome"], "shown")
        self.assertEqual(record["answer"]["label"], "no")

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


if __name__ == "__main__":
    unittest.main()
