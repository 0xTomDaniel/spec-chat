import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "skill" / "review-spec" / "assets" / "jev"
EXPECTED = {"type.json", "orphan.json", "resolved.json"}
STATE_KEYS = {
    "about": {"first", "second"},
    "type": {"before", "after"},
    "orphan": {"quote", "candidates"},
    "resolved": {"comment", "before", "after"},
    "contradicts": {"before", "after", "target"},
    "contradicts-nongoal": {"before", "after", "target"},
    "oversteps": {"before", "after", "target"},
    "overlaps": {"before", "after", "target"},
    "coverage": {"story", "criterion"},
    "audience": {"clause"},
    "scope": {"criterion"},
    "rule": {"rule", "spec"},
}
# Moderate start; oversteps? raised by a tuning pass: every confident Jev yes on the train split was wrong.
THRESHOLDS = {"oversteps": 0.7}
# Every chain question is yes or no, and it and scope, coverage, and reading view fall back (jev-suggestions #chains).
YES_NO = {"about", "type", "contradicts", "contradicts-nongoal", "oversteps", "overlaps"}
FALLBACK = YES_NO | {"scope", "coverage", "audience"}
# The rule check goes to the general LLM alone, never to Jev, so no fallback (project-rules #q-rule-llm).
LLM_ONLY = {"rule"}


class JevQuestionSetTest(unittest.TestCase):
    def test_every_set_parses_and_has_descriptions_and_examples(self):
        paths = sorted(SETS.glob("*.json"))
        self.assertTrue(EXPECTED <= {path.name for path in paths})
        for path in paths:
            with self.subTest(path=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["id"], path.stem)
                self.assertIsInstance(data["version"], int)
                # #q-fallback: a set only allows the fallback; the model is the box's llm_model.
                self.assertIs(data.get("fallback", False), path.stem in FALLBACK)
                self.assertIs(data.get("llm_only", False), path.stem in LLM_ONLY)
                self.assertTrue(data["instructions"].strip())
                self.assertEqual(data["threshold"], THRESHOLDS.get(path.stem, 0.4))
                labels = data["labels"]
                self.assertIsInstance(labels, list)
                self.assertGreater(len(labels), 0)
                names = []
                for label in labels:
                    names.append(label["name"])
                    self.assertTrue(label["description"].strip())
                    examples = label["examples"]
                    self.assertGreaterEqual(len(examples), 2)
                    self.assertLessEqual(len(examples), 6)
                    for example in examples:
                        self.assertIsInstance(example, dict)
                        self.assertIsInstance(example.get("input"), dict)
                        self.assertIsInstance(example.get("label"), str)
                        self.assertEqual(set(example["input"]), STATE_KEYS[path.stem])
                        if path.stem == "orphan":
                            self.assertIsInstance(example["input"]["candidates"], list)
                            self.assertIn(example["label"], set(example["input"]["candidates"]) | {"none"})
                        else:
                            self.assertEqual(example["label"], label["name"])
                self.assertNotIn("unsure", names)
                if path.stem in YES_NO:
                    self.assertEqual(names, ["yes", "no"])
                if path.stem == "rule":  # per rule: not triggered, covered, or missed (project-rules #q-rule)
                    self.assertEqual(names, ["not triggered", "covered", "missed"])
                self.assertEqual(len(names), len(set(names)))

    def test_scope_reads_a_convention_as_this_feature_and_a_promise_as_every_feature(self):
        """project-rules #acceptance-convention-scope, #q-scope-promise: the scope question reads a design-system
        convention as this feature and a product promise as every feature, each among its examples, under a new
        set version, so no answer keyed on an earlier version is reused."""
        data = json.loads((SETS / "scope.json").read_text(encoding="utf-8"))
        # v6 names the umbrella as a convention's one exception (#declare-promise), so no v5 answer is reused
        self.assertGreaterEqual(data["version"], 6)
        self.assertIn("promise", data["instructions"])
        self.assertIn("A design-system convention, how the interface looks or moves or which shared part draws a kind "
                      "of thing, is this feature", data["instructions"])
        examples = {label["name"]: [example["input"]["criterion"] for example in label["examples"]]
                    for label in data["labels"]}
        self.assertTrue(any("design system's shared text animation" in text for text in examples["this feature"]))
        self.assertTrue(any("the tab keeps its place" in text and "before reloading" in text
                            for text in examples["every feature"]))

    def test_scope_reads_the_design_system_umbrella_as_every_feature(self):
        """project-rules #declare-promise, #q-scope-promise: the umbrella, that the interface uses the design system's
        shared parts, is a project rule, so a promise, so every feature: the convention sentence names it as its one
        exception, and it is one of the every feature examples, at most four (jev-suggestions #qset-examples)."""
        data = json.loads((SETS / "scope.json").read_text(encoding="utf-8"))
        convention = next(sentence for sentence in data["instructions"].split(". ")
                          if sentence.startswith("A design-system convention"))
        self.assertIn("the one exception is the umbrella rule that the interface uses the design system's shared parts",
                      convention)
        self.assertIn("every feature", convention.split("the one exception", 1)[1])
        examples = {label["name"]: [example["input"]["criterion"] for example in label["examples"]]
                    for label in data["labels"]}
        self.assertTrue(any("uses the design system's shared parts" in text for text in examples["every feature"]))
        self.assertFalse(any("uses the design system's shared parts" in text for text in examples["this feature"]))
        self.assertLessEqual(len(examples["every feature"]), 4)

    def test_examples_reuse_no_text_of_the_spec_under_qa(self):
        # The QA fixture reproduces this spec; examples drawn from it would score the fixture on itself.
        spec = importlib.util.spec_from_file_location("jev_sets_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
        jev = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jev)
        source = (ROOT / "docs/specs/jev-suggestions.spec.html").read_text(encoding="utf-8")
        corpus = " ".join(" ".join(value["text"].split()) for value in jev.extract_anchors(source).values())
        constants = set()

        def strings(value):
            if isinstance(value, str):
                yield " ".join(value.split()).removesuffix("...")
            elif isinstance(value, dict):
                for item in value.values():
                    yield from strings(item)
            elif isinstance(value, list):
                for item in value:
                    yield from strings(item)

        for path in sorted(SETS.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for label in data["labels"]:
                for example in label["examples"]:
                    for text in strings(example["input"]):
                        if len(text) >= 12 and text not in constants:
                            with self.subTest(set=path.stem, text=text[:60]):
                                self.assertNotIn(text, corpus)


if __name__ == "__main__":
    unittest.main()
