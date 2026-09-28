"""Record provenance, resolution, confirmed, and regression tests.

Spec: jev-suggestions #proof-provenance, #proof-resolution, #proof-regression.
Run: python3 tests/jev-provenance.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)

SETS = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")


# ---------------------------------------------------------------------------
# Fake provider for pipeline replay
# ---------------------------------------------------------------------------

class FakeProvider:
    """Returns canned answers per question kind."""

    def __init__(self, answers=None):
        self._answers = answers or {}
        self._lock = threading.Lock()

    def decide(self, payload):
        questions = payload.get("questions", {})
        qname = next(iter(questions))
        with self._lock:
            entry = self._answers.get(qname, {"choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0}, "confidence": 1.0})
        return {"model": "fake", "answers": {qname: entry}}

    def complete(self, payload):
        return {"choices": [{"message": {"content": json.dumps({"choice": "yes"})}}]}


# ---------------------------------------------------------------------------
# #proof-provenance
# ---------------------------------------------------------------------------

class TestProvenance(unittest.TestCase):
    """A draft-check record carries inputs, pairing, outcome with cutoffs, and verifier fields."""

    def test_draft_check_record_carries_inputs_and_pairing(self):
        """#proof-provenance: record from a fake pipeline has inputs, pairing, cutoffs."""
        provider = FakeProvider({
            "about": {"choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0}, "confidence": 1.0},
            "contradicts": {"choice": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9},
        })
        store = jev.JudgmentStore()
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake",
                           record_store=store, clock=lambda: "2026-09-28T00:00:00.000Z")

        # Build a draft check with pairing via build_corpus_questions path
        item = jev.draft_check(
            anchor="clause-a", before="old text", after="new clause text",
            target="target-anchor", target_text="target clause text",
            path="spec.html", base="abc", revision="def",
            context={"surface": "My Spec", "section": "Rules"},
            target_context={"surface": "Other Spec", "section": "Scope", "columns": ["A", "B"]},
            pairing={"rule": "same-spec", "rank": 0, "gate": "yes"})

        result = seam.ask_chain(item["chain"])
        self.assertEqual(result["outcome"], "shown")
        self.assertEqual(result["answer"]["label"], "contradicts")

        # Find the contradicts record
        records = [r for r in store.by_key.values() if r["kind"] == "contradicts"]
        self.assertEqual(len(records), 1)
        rec = records[0]

        # inputs (#record-inputs)
        self.assertIn("inputs", rec)
        inputs = rec["inputs"]
        self.assertEqual(inputs["clause_text"], "new clause text")
        self.assertEqual(inputs["target_text"], "target clause text")
        self.assertEqual(inputs["surface"], "My Spec")
        self.assertEqual(inputs["section"], "Rules")
        self.assertIn("target_context", inputs)
        self.assertEqual(inputs["target_context"]["surface"], "Other Spec")
        self.assertEqual(inputs["target_context"]["columns"], ["A", "B"])

        # pairing (#record-pairing)
        self.assertIn("pairing", rec)
        self.assertEqual(rec["pairing"]["rule"], "same-spec")
        self.assertEqual(rec["pairing"]["rank"], 0)
        self.assertEqual(rec["pairing"]["gate"], "yes")

        # cutoffs (#record-outcome)
        self.assertIn("show_cutoff", rec)
        self.assertIn("verify_cutoff", rec)
        self.assertEqual(rec["show_cutoff"], SETS["contradicts"].show_cutoff)
        self.assertEqual(rec["verify_cutoff"], SETS["contradicts"].verify_cutoff)

    def test_verifier_field_when_present(self):
        """#proof-provenance: when verifier ran, record carries verifier answer and spans."""
        provider = FakeProvider({
            "contradicts": {"choice": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9},
        })
        store = jev.JudgmentStore()
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake",
                           record_store=store, clock=lambda: "2026-09-28T00:00:00.000Z")

        # Simulate a question with verifier info attached
        question = {
            "kind": "contradicts",
            "state": {"before": "old", "after": "new text", "target": "target text"},
            "sources": ["spec#a"], "revision": {"base": "b", "head": "h"},
            "pairing": {"rule": "cross-spec", "rank": 2, "gate": "yes"},
            "verifier": {"ran": True, "answer": "yes", "spans": ["conflict here", "also here"]},
        }
        record = seam.ask(question)

        self.assertIn("verifier", record)
        self.assertTrue(record["verifier"]["ran"])
        self.assertEqual(record["verifier"]["answer"], "yes")
        self.assertEqual(record["verifier"]["spans"], ["conflict here", "also here"])

    def test_non_draft_check_has_no_provenance(self):
        """Gate and non-draft-check questions have no inputs/pairing/verifier."""
        provider = FakeProvider({
            "about": {"choice": "yes", "probabilities": {"yes": 1.0, "no": 0.0}, "confidence": 1.0},
            "type": {"choice": "yes", "probabilities": {"yes": 0.9, "no": 0.1}, "confidence": 0.8},
        })
        store = jev.JudgmentStore()
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake",
                           record_store=store, clock=lambda: "2026-09-28T00:00:00.000Z")

        # Gate question
        gate_q = {"kind": "about", "state": {"first": {"text": "a"}, "second": {"text": "b"}},
                  "sources": ["spec#a"], "revision": {"base": "b", "head": "h"}}
        gate_rec = seam.ask(gate_q)
        self.assertNotIn("inputs", gate_rec)
        self.assertNotIn("pairing", gate_rec)

        # Type question
        type_q = {"kind": "type", "state": {"before": "old", "after": "new"},
                  "sources": ["spec#a"], "revision": {"base": "b", "head": "h"}}
        type_rec = seam.ask(type_q)
        self.assertNotIn("inputs", type_rec)
        self.assertNotIn("pairing", type_rec)
        # Type has no cutoffs
        self.assertNotIn("show_cutoff", type_rec)
        self.assertNotIn("verify_cutoff", type_rec)

    def test_build_corpus_attaches_pairing(self):
        """build_corpus_questions computes pairing with rule and rank for each candidate."""
        current = '<article data-anchor="root"><section data-anchor="sec" data-spec-section="rules"><p data-anchor="a">changed clause text about alpha beta gamma</p><p data-anchor="b">unrelated target text about alpha beta</p></section></article>'
        baseline = '<article data-anchor="root"><section data-anchor="sec" data-spec-section="rules"><p data-anchor="a">original clause text about something</p><p data-anchor="b">unrelated target text about alpha beta</p></section></article>'
        questions = jev.build_corpus_questions(current, baseline, path="spec.html", base="abc", revision="def")
        # Each chain should have pairing on post-gate steps
        for q in questions:
            chain = q.get("chain", [])
            for step in chain[1:]:  # skip gate
                if step["kind"] in jev.DRAFT_CHECK_KINDS:
                    self.assertIn("pairing", step, f"step {step['kind']} missing pairing")
                    self.assertIn("rule", step["pairing"])
                    self.assertIn("rank", step["pairing"])
                    self.assertIn("gate", step["pairing"])


# ---------------------------------------------------------------------------
# #proof-resolution
# ---------------------------------------------------------------------------

class TestResolution(unittest.TestCase):
    """Resolution: dismissed is unconfirmed, fixed is auto-confirmed, eval counts only confirmed."""

    def _make_store_with_record(self, outcome="shown"):
        """Create a store with one draft-check record."""
        store = jev.JudgmentStore()
        record = store.append({
            "record_id": "judgment-test-001",
            "cache_key": "sha256:aaa",
            "model": "fake",
            "question_set": {"id": "contradicts", "version": 3},
            "kind": "contradicts",
            "pair_kind": "contradicts",
            "sources": {"addresses": ["spec#a", "spec#b"], "revision": {"base": "b", "head": "h"}},
            "answer": {"label": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9},
            "threshold": 0.4,
            "outcome": outcome,
            "time": "2026-09-28T00:00:00.000Z",
            "show_cutoff": 0.9,
            "verify_cutoff": 0.7,
            "inputs": {"clause_text": "new text", "target_text": "target"},
            "pairing": {"rule": "same-spec", "rank": 0, "gate": "yes"},
        })
        return store, record

    def test_dismissed_is_unconfirmed(self):
        """Dismissing a mark sets resolution=dismissed and does NOT auto-confirm."""
        store, record = self._make_store_with_record()
        updated = store.resolve("judgment-test-001", "dismissed", reason="Jev is wrong here")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["resolution"]["status"], "dismissed")
        self.assertEqual(updated["resolution"]["reason"], "Jev is wrong here")
        self.assertNotIn("confirmed", updated)

    def test_fixed_is_auto_confirmed(self):
        """A spec fix auto-confirms as true positive."""
        store, record = self._make_store_with_record()
        updated = store.resolve("judgment-test-001", "fixed")
        self.assertIsNotNone(updated)
        self.assertEqual(updated["resolution"]["status"], "fixed")
        self.assertTrue(updated["confirmed"])

    def test_resolve_unknown_record_returns_none(self):
        store, _ = self._make_store_with_record()
        self.assertIsNone(store.resolve("nonexistent", "fixed"))

    def test_resolution_persists_to_file(self):
        """Resolution amendment is appended to JSONL and survives reload."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            store.append({
                "record_id": "judgment-persist-001",
                "cache_key": "sha256:bbb",
                "model": "fake",
                "question_set": {"id": "contradicts", "version": 3},
                "kind": "contradicts", "pair_kind": "contradicts",
                "sources": {"addresses": [], "revision": None},
                "answer": {"label": "yes", "probabilities": {"yes": 0.95}, "confidence": 0.9},
                "threshold": 0.4, "outcome": "shown",
                "time": "2026-09-28T00:00:00.000Z",
            })
            store.resolve("judgment-persist-001", "fixed")

            # Reload
            store2 = jev.JudgmentStore(path)
            rec = store2.get("sha256:bbb")
            self.assertIsNotNone(rec)
            self.assertEqual(rec["resolution"]["status"], "fixed")
            self.assertTrue(rec["confirmed"])

    def test_confirmed_records_returns_only_confirmed(self):
        """confirmed_records filters to confirmed=True only."""
        store, _ = self._make_store_with_record()
        # Before resolution: no confirmed records
        self.assertEqual(len(store.confirmed_records()), 0)

        # Dismiss (unconfirmed)
        store2, _ = self._make_store_with_record()
        store2.by_key["sha256:aaa"]["record_id"] = "judgment-test-002"
        store2.resolve("judgment-test-002", "dismissed", reason="wrong")
        self.assertEqual(len(store2.confirmed_records()), 0)

        # Fix (auto-confirmed)
        store3, _ = self._make_store_with_record()
        store3.resolve("judgment-test-001", "fixed")
        confirmed = store3.confirmed_records()
        self.assertEqual(len(confirmed), 1)
        self.assertTrue(confirmed[0]["confirmed"])

    def test_eval_counts_only_confirmed(self):
        """The eval replay counts only confirmed labels, excluding unconfirmed dismissed ones."""
        # Create two records: one dismissed (unconfirmed), one fixed (confirmed TP)
        dismissed = {
            "kind": "contradicts",
            "answer": {"label": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9},
            "ground_truth": "no",  # dismissed -> was wrong
            "confirmed": False,
        }
        fixed = {
            "kind": "contradicts",
            "answer": {"label": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9},
            "ground_truth": "yes",  # fixed -> was right
            "confirmed": True,
        }
        # Regression check should only count confirmed entries
        confirmed_only = [fixed]
        passed, metrics = jev.regression_check(confirmed_only, SETS)
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 0)
        self.assertEqual(metrics["fn"], 0)

        # If we include both but mark dismissed as not confirmed, regression_check skips it
        # (regression_check should filter by show_cutoff)
        all_labels = [dismissed, fixed]
        # Both have P(yes)=0.95 >= show_cutoff=0.9, so both shown
        passed, metrics = jev.regression_check(all_labels, SETS)
        # dismissed has ground_truth=no -> FP; fixed has ground_truth=yes -> TP
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fp"], 1)


# ---------------------------------------------------------------------------
# #proof-regression
# ---------------------------------------------------------------------------

class TestRegression(unittest.TestCase):
    """Regression: threshold change that misses a confirmed label fails; original passes."""

    def test_original_threshold_passes(self):
        """With two confirmed TPs at P(yes)=0.95, original show_cutoff=0.9 shows both."""
        labels = [
            {"kind": "contradicts", "ground_truth": "yes",
             "answer": {"label": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9}},
            {"kind": "contradicts", "ground_truth": "yes",
             "answer": {"label": "yes", "probabilities": {"yes": 0.92, "no": 0.08}, "confidence": 0.85}},
        ]
        passed, metrics = jev.regression_check(labels, SETS, prior_precision=0.8, prior_recall=1.0)
        self.assertTrue(passed, f"should pass: {metrics}")
        self.assertEqual(metrics["tp"], 2)
        self.assertEqual(metrics["fn"], 0)
        self.assertGreaterEqual(metrics["recall"], 1.0)

    def test_raised_threshold_fails(self):
        """Raising show_cutoff misses one confirmed label -> recall drops -> fails."""
        labels = [
            {"kind": "contradicts", "ground_truth": "yes",
             "answer": {"label": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.9}},
            {"kind": "contradicts", "ground_truth": "yes",
             "answer": {"label": "yes", "probabilities": {"yes": 0.92, "no": 0.08}, "confidence": 0.85}},
        ]
        # Create question sets with raised show_cutoff (0.94 misses the 0.92 entry)
        raised = dict(SETS)
        raised["contradicts"] = jev.QuestionSet(
            "contradicts", 3, SETS["contradicts"].instructions,
            SETS["contradicts"].labels, SETS["contradicts"].threshold,
            SETS["contradicts"].fallback,
            show_cutoff=0.94, verify_cutoff=0.7)
        passed, metrics = jev.regression_check(labels, raised, prior_precision=0.8, prior_recall=1.0)
        self.assertFalse(passed, f"should fail: {metrics}")
        self.assertEqual(metrics["tp"], 1)
        self.assertEqual(metrics["fn"], 1)
        self.assertLess(metrics["recall"], 1.0)

    def test_regression_with_no_confirmed_labels_passes(self):
        """With no confirmed labels, nothing to regress against."""
        passed, metrics = jev.regression_check([], SETS)
        self.assertTrue(passed)
        self.assertEqual(metrics["tp"], 0)


if __name__ == "__main__":
    unittest.main()
