"""Gate question filters unrelated pairs before chain (jev-suggestions #gate, #proof-gate)."""

import importlib.util
import json
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_gate_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jev)
SETS = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")

BASE = '<section data-anchor="s"><p data-anchor="new">Old words.</p><p data-anchor="other">Other clause here.</p></section>'
CURRENT = '<section data-anchor="s"><p data-anchor="new">New clause words here.</p><p data-anchor="other">Other clause here.</p></section>'


class FakeProvider:
    """Jev answers per question kind as (choice, confidence); the general LLM per kind; every call is kept."""

    def __init__(self, answers, general=None):
        self.answers, self.general = dict(answers), dict(general or {})
        self.calls, self.general_calls = [], []
        self.lock = threading.Lock()

    def decide(self, payload):
        kind = next(iter(payload["questions"]))
        with self.lock:
            self.calls.append(kind)
        choice, confidence = self.answers.get(kind, ("no", 0.95))
        return {"model": "fake", "answers": {kind: {"choice": choice, "confidence": confidence,
                                                    "probabilities": {choice: confidence}}}}

    def complete(self, payload):
        with self.lock:
            kind = self.calls[-1]
            self.general_calls.append(kind)
        return {"choices": [{"message": {"content": json.dumps({"choice": self.general[kind]})}}]}


def draft_check():
    items = [item for item in jev.build_corpus_questions(CURRENT, BASE, "spec.html", "base", "head")
             if item["target"] == "other"]
    return items[0]


class GateTest(unittest.TestCase):
    def seam(self, provider):
        return jev.JevSeam(SETS, provider=provider, api_key="fake")

    def test_gate_added_before_chain(self):
        """#gate-chain: the about? gate is the first step of every draft-check chain."""
        chain = draft_check()["chain"]
        self.assertEqual(chain[0]["kind"], "about")
        self.assertEqual(chain[1]["kind"], "contradicts")
        self.assertIn("first", chain[0]["state"])
        self.assertIn("second", chain[0]["state"])

    def test_gate_state_carries_clause_text_and_context(self):
        """#gate-set-state: first/second each with context fields."""
        ctx = {"surface": "My spec", "section": "Rules"}
        tctx = {"surface": "Other spec"}
        item = jev.draft_check("a", "old", "new text", "other", "other text",
                               path="spec", base="base", revision="head",
                               context=ctx, target_context=tctx)
        gate = item["chain"][0]
        self.assertEqual(gate["state"]["first"]["text"], "new text")
        self.assertEqual(gate["state"]["first"]["surface"], "My spec")
        self.assertEqual(gate["state"]["first"]["section"], "Rules")
        self.assertEqual(gate["state"]["second"]["text"], "other text")
        self.assertEqual(gate["state"]["second"]["surface"], "Other spec")

    def test_gate_no_skips_chain(self):
        """#gate-chain: a no gate answer skips the contradicts/oversteps chain entirely."""
        provider = FakeProvider({"about": ("no", 0.95)})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["about"])
        self.assertIsNone(result["answer"]["label"])
        self.assertEqual(result["outcome"], "shown")

    def test_gate_yes_proceeds_to_chain(self):
        """#gate-chain: a yes gate answer proceeds to the contradicts/oversteps chain."""
        provider = FakeProvider({"about": ("yes", 0.95), "contradicts": ("yes", 0.95)})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["about", "contradicts"])
        self.assertEqual(result["answer"]["label"], "contradicts")

    def test_gate_yes_full_chain(self):
        """#gate-chain: full sequence about?(yes) then contradicts?(no) then oversteps?"""
        provider = FakeProvider({"about": ("yes", 0.95), "contradicts": ("no", 0.95),
                                 "oversteps": ("yes", 0.95)})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["about", "contradicts", "oversteps"])
        self.assertEqual(result["answer"]["label"], "oversteps")

    def test_gate_no_fewer_provider_calls(self):
        """#acceptance-gate-cost: a no gate skips the chain, so fewer provider calls."""
        provider = FakeProvider({"about": ("no", 0.95)})
        self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(len(provider.calls), 1)  # only about, no contradicts/oversteps/overlaps

    def test_gate_display_labels_exclude_gate(self):
        """Gate answer is not a display label; only contradicts/oversteps/overlaps show."""
        item = draft_check()
        self.assertEqual(item["display_labels"], ["contradicts", "oversteps", "overlaps"])

    def test_gate_cache_by_content_hash(self):
        """#acceptance-gate-cache: same clause pair reuses cached gate record."""
        provider = FakeProvider({"about": ("no", 0.95)})
        seam = self.seam(provider)
        chain = draft_check()["chain"]
        seam.ask_chain(chain)
        self.assertEqual(len(provider.calls), 1)
        # Same chain again: cached, no new call
        seam.ask_chain(chain)
        self.assertEqual(len(provider.calls), 1)

    def test_gate_failure_not_recorded_as_unrelated(self):
        """#gate-parallel: a failed gate is never recorded as unrelated, but as unavailable."""
        class Down(FakeProvider):
            def decide(self, payload):
                self.calls.append(next(iter(payload["questions"])))
                raise RuntimeError("provider down")
        provider = Down({})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(provider.calls, ["about"])
        # Chain did not proceed past gate
        self.assertEqual(len(provider.calls), 1)

    def test_gate_failure_retry(self):
        """#gate-parallel: a failed gate uses the one-retry rule with a pause."""
        class FailOnce(FakeProvider):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fail_count = 0
            def decide(self, payload):
                kind = next(iter(payload["questions"]))
                with self.lock:
                    self.calls.append(kind)
                if kind == "about" and self.fail_count == 0:
                    self.fail_count += 1
                    raise RuntimeError("transient")
                return super().decide(payload)
        provider = FailOnce({"about": ("yes", 0.95), "contradicts": ("yes", 0.95)})
        seam = self.seam(provider)
        chain = draft_check()["chain"]
        # First ask: gate fails, records unavailable with retry_at
        result = seam.ask_chain(chain)
        self.assertEqual(result["outcome"], "unavailable")
        # Override wall clock: after the pause, asking again retries
        record = seam.store.get(seam.key(chain[0]))
        self.assertIsNotNone(record)
        self.assertEqual(record["outcome"], "unavailable")

    def test_about_question_set_exists(self):
        """#boundary-gate-set: the about question set is loaded with yes/no labels."""
        self.assertIn("about", SETS)
        qset = SETS["about"]
        self.assertEqual(qset.id, "about")
        self.assertEqual(set(qset.criteria()), {"yes", "no"})
        self.assertTrue(qset.fallback)


class LaneGateTest(unittest.TestCase):
    """#gate-lane: cross-lane checks use the same gate."""

    def test_lane_check_has_gate_first(self):
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        self.assertEqual(item["chain"][0]["kind"], "about")
        # 5 steps: about, contradicts, oversteps_fwd, oversteps_back, overlaps
        self.assertEqual(len(item["chain"]), 5)
        kinds = [step["kind"] for step in item["chain"]]
        self.assertEqual(kinds, ["about", "contradicts", "oversteps", "oversteps", "overlaps"])

    def test_lane_result_gate_no_skips_all(self):
        """#gate-lane: gate no skips the entire lane check."""
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        gate_no = {"outcome": "shown", "answer": {"label": "no"}, "record_id": "r1"}
        results = jev.lane_result(item["chain"], lambda step: gate_no if step["kind"] == "about" else None)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["answer"]["label"])
        self.assertEqual(results[0]["outcome"], "shown")

    def test_lane_result_gate_yes_proceeds(self):
        """#gate-lane: gate yes proceeds to contradicts."""
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        gate_yes = {"outcome": "shown", "answer": {"label": "yes"}, "record_id": "r1"}
        contradicts_yes = {"outcome": "shown", "answer": {"label": "yes"}, "record_id": "r2"}

        def get(step):
            if step["kind"] == "about":
                return gate_yes
            if step["kind"] == "contradicts":
                return contradicts_yes
            return None

        results = jev.lane_result(item["chain"], get)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["answer"]["label"], "contradicts")

    def test_lane_result_gate_pending(self):
        """Gate not yet answered: lane result is pending."""
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        results = jev.lane_result(item["chain"], lambda step: None)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0]["outcome"])
        self.assertEqual(results[0]["step"]["kind"], "about")

    def test_ask_lane_steps_gate_no_stops(self):
        """_ask_lane_steps with gate no: only about is asked."""
        provider = FakeProvider({"about": ("no", 0.95)})
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake")
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        service = object.__new__(jev.JevService)
        service.seam = seam
        service._ask_lane_steps(item["chain"], seam)
        self.assertEqual(provider.calls, ["about"])

    def test_ask_lane_steps_gate_yes_proceeds(self):
        """_ask_lane_steps with gate yes: asks contradicts and further."""
        provider = FakeProvider({"about": ("yes", 0.95), "contradicts": ("yes", 0.95)})
        seam = jev.JevSeam(SETS, provider=provider, api_key="fake")
        a = {"path": "a.spec.html", "anchor": "c1", "before": "old", "after": "new a", "base": "abc"}
        b = {"path": "b.spec.html", "anchor": "c2", "before": "old", "after": "new b", "base": "def"}
        item = jev.lane_check("left", a, "right", b)
        service = object.__new__(jev.JevService)
        service.seam = seam
        service._ask_lane_steps(item["chain"], seam)
        # Gate yes, contradicts yes -> chain stops at contradicts
        self.assertEqual(provider.calls, ["about", "contradicts"])


if __name__ == "__main__":
    unittest.main()
