"""Record provenance, resolution, and confirmed tests.

Spec: jev-suggestions #proof-provenance, #proof-resolution, #measure-feedback.
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
            pairing={"rule": "cross-spec", "rank": 0, "gate": "yes"})

        result = seam.ask_chain(item["chain"])
        self.assertEqual(result["outcome"], "shown")
        self.assertEqual(result["answer"]["label"], "contradicts")

        # Find the contradicts record
        records = [r for r in store.records() if r["kind"] == "contradicts"]
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
        self.assertEqual(rec["pairing"]["rule"], "cross-spec")
        self.assertEqual(rec["pairing"]["rank"], 0)
        self.assertEqual(rec["pairing"]["gate"], "yes")

        # cutoffs (#record-outcome)
        self.assertIn("show_cutoff", rec)
        self.assertIn("verify_cutoff", rec)
        self.assertEqual(rec["show_cutoff"], SETS["contradicts"].show_cutoff)
        self.assertEqual(rec["verify_cutoff"], SETS["contradicts"].verify_cutoff)

    def test_verifier_field_when_present(self):
        """#proof-provenance, #acceptance-provenance-verifier: a verify-band draft check the verifier ran on
        carries the same inputs, pairing, and cutoffs, plus the verifier's answer and quoted spans."""
        class VerifyBand(FakeProvider):
            def complete(self, payload):
                return {"choices": [{"message": {"content": json.dumps(
                    {"answer": "yes", "clause_span": "conflict here", "target_span": "also here"})}}]}
        provider = VerifyBand({
            "contradicts": {"choice": "yes", "probabilities": {"yes": 0.80, "no": 0.20}, "confidence": 0.80},
        })
        store = jev.JudgmentStore()
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake",
                           record_store=store, clock=lambda: "2026-09-28T00:00:00.000Z")
        question = {
            "kind": "contradicts",
            "state": {"before": "old", "after": "new text", "target": "target text"},
            "sources": ["spec#a"], "revision": {"base": "b", "head": "h"},
            "pairing": {"rule": "cross-spec", "rank": 2, "gate": "yes"},
        }
        record = seam.ask(question)

        self.assertEqual(record["outcome"], "verified")
        self.assertEqual(record["inputs"]["clause_text"], "new text")
        self.assertEqual(record["pairing"], {"rule": "cross-spec", "rank": 2, "gate": "yes"})
        self.assertEqual(record["show_cutoff"], SETS["contradicts"].show_cutoff)
        self.assertEqual(record["verify_cutoff"], SETS["contradicts"].verify_cutoff)
        self.assertEqual(record["verifier"], {"ran": True, "answer": "yes",
                                              "spans": {"clause": "conflict here", "target": "also here"}})

    def test_no_verifier_field_when_verifier_did_not_run(self):
        """#record-verifier: absent when the verifier did not run, even if a question carries one."""
        provider = FakeProvider({
            "contradicts": {"choice": "yes", "probabilities": {"yes": 0.95, "no": 0.05}, "confidence": 0.95},
        })
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake", record_store=jev.JudgmentStore())
        record = seam.ask({"kind": "contradicts", "state": {"before": "o", "after": "n", "target": "t"},
                           "sources": ["spec#a"], "revision": {"base": "b", "head": "h"},
                           "verifier": {"ran": True, "answer": "yes"}})
        self.assertEqual(record["outcome"], "shown")
        self.assertNotIn("verifier", record)

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
    """Resolution: dismissed and fixed stay unconfirmed until audited, eval counts only confirmed (#measure-feedback)."""

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
            "pairing": {"rule": "cross-spec", "rank": 0, "gate": "yes"},
        })
        return store, record

    WHERE = {"project": "p", "spec": "docs/specs/a.spec.html", "anchor": "a", "kind": "contradicts",
             "target": "docs/specs/b.spec.html#b"}

    def test_dismissed_is_one_unconfirmed_ledger_outcome_and_the_record_is_unchanged(self):
        """#proof-resolution: a dismissal with a reason is one dismissed outcome by spec and anchor citing the
        record, unconfirmed, and the record file is byte-identical."""
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl")
            ledger = jev.WarningLedger(Path(tmp) / "ledger.jsonl")
            store.append({"record_id": "judgment-test-001", "cache_key": "sha256:aaa", "kind": "contradicts",
                          "outcome": "shown", "answer": {"label": "yes"}})
            before = {f.name: f.read_bytes() for f in (Path(tmp) / "records").glob("*/*.jsonl")}
            ledger.append({**self.WHERE, "outcome": "dismissed", "judgment": "judgment-test-001", "by": "owner",
                           "reason": "Jev is wrong here"})
            self.assertEqual({f.name: f.read_bytes() for f in (Path(tmp) / "records").glob("*/*.jsonl")}, before)
            lines = [json.loads(line) for line in (Path(tmp) / "ledger.jsonl").read_text().splitlines()]
            self.assertEqual([(e["spec"], e["anchor"], e["outcome"], e["judgment"], e["reason"], e["confirmed"])
                              for e in lines],
                             [("docs/specs/a.spec.html", "a", "dismissed", "judgment-test-001", "Jev is wrong here", False)])

    def test_fixed_is_unconfirmed_until_audited(self):
        """project-rules #acceptance-fixed-unconfirmed: an edit can answer a wrong mark, so fixed proves nothing;
        the eval read's confirmed labels exclude it, and the unconfirmed dismissal, until an audit labels it."""
        store, _ = self._make_store_with_record()
        ledger = jev.WarningLedger()
        ledger.append({**self.WHERE, "anchor": "other", "outcome": "dismissed", "judgment": "judgment-test-001",
                       "by": "owner", "reason": "wrong"})
        fixed = ledger.append({**self.WHERE, "outcome": "fixed", "judgment": "judgment-test-001", "by": "server"})
        self.assertFalse(fixed["confirmed"])
        self.assertEqual(list(jev.eval_read(store.records(), ledger, confirmed=True)), [])
        ledger.append({**self.WHERE, "outcome": "fixed", "judgment": "judgment-test-001", "by": "audit"})
        labels = [(record["record_id"], [(o["outcome"], o["by"]) for o in outcomes])
                  for record, outcomes in jev.eval_read(store.records(), ledger, confirmed=True)]
        self.assertEqual(labels, [("judgment-test-001", [("fixed", "audit")])])


if __name__ == "__main__":
    unittest.main()
