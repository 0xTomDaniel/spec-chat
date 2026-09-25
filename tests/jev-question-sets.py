import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "skill" / "review-spec" / "assets" / "jev"
EXPECTED = {"type.json", "orphan.json", "resolved.json"}


class JevQuestionSetTest(unittest.TestCase):
    def test_every_set_parses_and_has_descriptions_and_examples(self):
        paths = sorted(SETS.glob("*.json"))
        self.assertTrue(EXPECTED <= {path.name for path in paths})
        for path in paths:
            with self.subTest(path=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["id"], path.stem)
                self.assertIsInstance(data["version"], int)
                self.assertTrue(data["instructions"].strip())
                self.assertGreaterEqual(data["threshold"], 0.5)
                self.assertLessEqual(data["threshold"], 0.8)
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
                    self.assertTrue(all(isinstance(example, str) and example.strip() for example in examples))
                self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
