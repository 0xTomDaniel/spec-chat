import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_serve_jev_test", ROOT / "tools" / "jev.py")
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)


class FakeProvider:
    def __init__(self, *answers, error=False):
        self.answers = list(answers)
        self.error = error
        self.calls = []

    def decide(self, payload):
        self.calls.append(payload)
        if self.error:
            raise RuntimeError("provider failed")
        value = self.answers.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class JevSeamTest(unittest.TestCase):
    def question_sets(self):
        return {"type": jev.QuestionSet("type", 1, "Classify the change.",
                                         [{"name": "behavioral", "description": "Changes behavior."}], 0.8)}

    def test_below_threshold_is_unsure_and_record_is_redacted(self):
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.7}}})
        with tempfile.TemporaryDirectory() as directory:
            store = jev.JudgmentStore(Path(directory) / "records.jsonl")
            seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake", record_store=store)
            result = seam.ask({"kind": "type", "id": "rule", "state": {"after": "secret text"},
                               "sources": ["spec#rule"], "revision": "head"})
            self.assertEqual(result["outcome"], "unsure")
            self.assertEqual(len(provider.calls), 1)
            self.assertEqual(len(store.records), 1)
            encoded = json.dumps(store.records[0])
            self.assertNotIn("secret text", encoded)
            self.assertNotIn("fake", encoded)

    def test_failure_retries_once_then_records_unavailable(self):
        provider = FakeProvider(error=True)
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        result = seam.ask({"kind": "type", "id": "rule", "state": {}, "sources": [], "revision": "head"})
        self.assertEqual(result["outcome"], "unavailable")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(seam.store.records), 1)

    def test_same_question_uses_record_cache(self):
        provider = FakeProvider({"answers": {"type": {"choice": "behavioral", "confidence": 0.9}}})
        seam = jev.JevSeam(self.question_sets(), provider=provider, api_key="fake")
        question = {"kind": "type", "id": "rule", "state": {"after": "new"}, "sources": ["spec#rule"], "revision": "head"}
        first, second = seam.ask(question), seam.ask(question)
        self.assertEqual(first["record_id"], second["record_id"])
        self.assertEqual(len(provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
