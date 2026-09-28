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
    "triggered": {"rule", "spec"},
    "covered": {"rule", "spec"},
}
# Moderate start; the rule chain keeps rule v2's raise so uncertain answers escalate to the fallback (ANN-309).
# oversteps? raised by the ANN-347 tuning pass: every confident Jev yes on the train split was wrong.
THRESHOLDS = {"triggered": 0.7, "covered": 0.7, "oversteps": 0.7}
# Every chain question is yes or no, and it and scope, coverage, and reading view fall back (jev-suggestions #chains).
YES_NO = {"about", "type", "contradicts", "contradicts-nongoal", "oversteps", "overlaps", "triggered", "covered"}
FALLBACK = YES_NO | {"scope", "coverage", "audience"}


class JevQuestionSetTest(unittest.TestCase):
    def test_seam_copies_are_byte_identical(self):
        self.assertEqual(
            (ROOT / "tools/jev.py").read_bytes(),
            (ROOT / "skill/review-spec/assets/jev.py").read_bytes(),
        )

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
                self.assertEqual(len(names), len(set(names)))

    def test_examples_reuse_no_text_of_the_spec_under_qa(self):
        # The QA fixture reproduces this spec; examples drawn from it would score the fixture on itself.
        spec = importlib.util.spec_from_file_location("jev_sets_test", ROOT / "tools" / "jev.py")
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
