"""Yes/no questions and chains (jev-suggestions #chains, #proof-chain-test)."""

import importlib.util
import json
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("jev_chains_test", ROOT / "tools" / "jev.py")
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
            kind = self.calls[-1]  # a chain asks in order: the fallback follows its own Jev call
            self.general_calls.append(kind)
        return {"choices": [{"message": {"content": json.dumps({"choice": self.general[kind]})}}]}


def draft_check(target_non_goal=False):
    items = [item for item in jev.build_corpus_questions(CURRENT, BASE, "spec.html", "base", "head")
             if item["target"] == ("non-goal" if target_non_goal else "other")]
    return items[0]


class ChainTest(unittest.TestCase):
    def seam(self, provider):
        return jev.JevSeam(SETS, provider=provider, api_key="fake")

    def test_first_failing_chain_test(self):
        # yes to contradicts?: no second call, Contradicts.
        provider = FakeProvider({"contradicts": ("yes", 0.95)})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["contradicts"])
        self.assertEqual(result["answer"]["label"], "contradicts")
        self.assertEqual(result["outcome"], "shown")
        # no below threshold to oversteps?: one general LLM call, and the chain continues from its answer.
        provider = FakeProvider({"contradicts": ("no", 0.95), "oversteps": ("no", 0.1)}, general={"oversteps": "yes"})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["contradicts", "oversteps"])
        self.assertEqual(provider.general_calls, ["oversteps"])
        self.assertEqual(result["answer"]["label"], "oversteps")
        self.assertEqual(result["unsure"], 1)
        provider = FakeProvider({"contradicts": ("no", 0.95), "oversteps": ("no", 0.1)}, general={"oversteps": "no"})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(provider.calls, ["contradicts", "oversteps", "overlaps"])
        self.assertIsNone(result["answer"]["label"])

    def test_draft_chain_labels_and_non_goal_asks_only_contradicts(self):
        provider = FakeProvider({"overlaps": ("yes", 0.95)})
        self.assertEqual(self.seam(provider).ask_chain(draft_check()["chain"])["answer"]["label"], "overlaps")
        source = CURRENT.replace("</section>", '</section><section data-anchor="non-goals"><p data-anchor="non-goal">Offline.</p></section>')
        items = [item for item in jev.build_corpus_questions(source, BASE, "spec.html", "base", "head")
                 if item["target"] == "non-goal"]
        self.assertEqual([step["kind"] for step in items[0]["chain"]], ["contradicts"])
        self.assertTrue(items[0]["chain"][0]["state"]["target_non_goal"])
        provider = FakeProvider({})
        self.assertIsNone(self.seam(provider).ask_chain(items[0]["chain"])["answer"]["label"])
        self.assertEqual(provider.calls, ["contradicts"])

    def test_every_chain_record_is_yes_or_no_and_one_record_per_question(self):
        provider = FakeProvider({"contradicts": ("no", 0.95), "oversteps": ("no", 0.95), "overlaps": ("no", 0.1)},
                                general={"overlaps": "no"})
        seam = self.seam(provider)
        seam.ask_chain(draft_check()["chain"])
        records = list(seam.store.by_key.values())
        self.assertEqual(len(records), 3)
        self.assertEqual({record["answer"]["label"] for record in records}, {"no"})
        for name in ("type", "contradicts", "oversteps", "overlaps", "triggered", "covered"):
            with self.subTest(set=name):
                self.assertEqual(set(SETS[name].criteria()), {"yes", "no"})
                self.assertTrue(SETS[name].fallback)
        # A repeat asks nothing: each question is its own cache entry.
        seam.ask_chain(draft_check()["chain"])
        self.assertEqual(len(provider.calls), 3)

    def test_unavailable_stops_the_chain(self):
        class Down(FakeProvider):
            def decide(self, payload):
                self.calls.append(next(iter(payload["questions"])))
                raise RuntimeError("down")
        provider = Down({})
        result = self.seam(provider).ask_chain(draft_check()["chain"])
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(provider.calls, ["contradicts"])

    def test_change_type_yes_is_behavior_no_is_no_behavior_change(self):
        for choice, label in (("yes", "behavior"), ("no", "no-behavior-change")):
            with self.subTest(choice=choice):
                question = jev.build_type_questions(CURRENT, BASE, "spec.html", "base", "head")[0]
                result = self.seam(FakeProvider({"type": (choice, 0.95)})).ask_chain(question["chain"])
                self.assertEqual(result["answer"]["label"], label)

    def test_rule_chain_asks_covered_only_when_triggered(self):
        scope = {"state": {"criterion": "Every change ships its article."}, "target": "o.spec.html#a", "word": "o"}
        item = jev.build_rule_question(scope, CURRENT, "spec.html", "base", "head", "s")
        for answers, calls, label in (
                ({"triggered": ("no", 0.95)}, ["triggered"], None),
                ({"triggered": ("yes", 0.95), "covered": ("yes", 0.95)}, ["triggered", "covered"], None),
                ({"triggered": ("yes", 0.95), "covered": ("no", 0.95)}, ["triggered", "covered"], "missed")):
            with self.subTest(answers=answers):
                provider = FakeProvider(answers)
                self.assertEqual(self.seam(provider).ask_chain(item["chain"])["answer"]["label"], label)
                self.assertEqual(provider.calls, calls)

    def test_material_from_change_type(self):
        def record(label, outcome="shown"):
            return {"outcome": outcome, "answer": {"label": label}}
        no, yes = record("no-behavior-change"), record("behavior")
        self.assertEqual(jev.material([no, yes]), "yes")
        self.assertEqual(jev.material([no, no]), "no")
        self.assertEqual(jev.material([no, None]), "unknown")
        self.assertEqual(jev.material([no, record(None, "unavailable")]), "unknown")

    def test_levels_hold_only_important_and_warning_kinds(self):
        important = {"human": "important", "agent": "important"}
        self.assertEqual(jev.MARK_LEVELS, {
            "contradicts": important, "missed": important, "no-criterion": important,
            "no-story": important, "qa-failed": important, "qa-stale": important,
            "overlaps": {"human": "warning", "agent": "warning"},
            "oversteps": {"human": "warning", "agent": "important"}})


if __name__ == "__main__":
    unittest.main()
