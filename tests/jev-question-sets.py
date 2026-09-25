import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "skill" / "review-spec" / "assets" / "jev"
EXPECTED = {"type.json", "orphan.json", "resolved.json"}
STATE_KEYS = {
    "type": {"before", "after"},
    "orphan": {"quote", "candidates"},
    "resolved": {"comment", "before", "after"},
    "corpus": {"before", "after", "target", "target_boundary"},
    "coverage": {"story", "criterion"},
    "audience": {"clause", "reader"},
}


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
                self.assertTrue(data["instructions"].strip())
                self.assertEqual(data["threshold"], 0.4)
                labels = data["labels"]
                self.assertIsInstance(labels, list)
                self.assertGreater(len(labels), 0)
                names = []
                for label in labels:
                    names.append(label["name"])
                    self.assertTrue(label["description"].strip())
                    examples = label["examples"]
                    self.assertGreaterEqual(len(examples), 2)
                    self.assertLessEqual(len(examples), 4)
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
                self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
