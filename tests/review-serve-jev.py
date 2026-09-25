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

    def test_coverage_builder_pairs_every_story_with_every_criterion(self):
        source = """
        <p data-user-story data-anchor="story-one">As a reviewer, I see the flag.</p>
        <p data-user-story data-anchor="story-two">As a reader, I see the text.</p>
        <p data-acceptance-criterion data-anchor="criterion-one">The flag appears.</p>
        <p data-acceptance-criterion data-anchor="criterion-two">The text stays readable.</p>
        """
        questions = jev.build_coverage_questions(source, "spec.html", "base", "head")
        self.assertEqual({question["id"] for question in questions}, {
            "story-one::criterion-one", "story-one::criterion-two",
            "story-two::criterion-one", "story-two::criterion-two",
        })
        self.assertTrue(all(question["kind"] == "coverage" for question in questions))
        self.assertEqual(questions[0]["sources"], ["spec.html#story-one", "spec.html#criterion-one"])

    def test_coverage_response_accepts_pair_answer_and_keeps_addresses_only(self):
        provider = FakeProvider({"answers": {"coverage": {"choice": "verifies", "confidence": 0.9}}})
        with tempfile.TemporaryDirectory() as directory:
            store = jev.JudgmentStore(Path(directory) / "records.jsonl")
            seam = jev.JevSeam(provider=provider, api_key="fake", record_store=store)
            question = jev.build_coverage_questions(
                '<p data-user-story data-anchor="story">Story words</p>'
                '<p data-acceptance-criterion data-anchor="criterion">Criterion words</p>',
                "spec.html", "base", "head",
            )[0]
            record = seam.ask(question)
            self.assertEqual(record["outcome"], "shown")
            self.assertEqual(record["answer"]["label"], "verifies")
            encoded = json.dumps(record)
            self.assertIn("spec.html#story", encoded)
            self.assertIn("spec.html#criterion", encoded)
            self.assertNotIn("Story words", encoded)
            self.assertNotIn("Criterion words", encoded)


if __name__ == "__main__":
    unittest.main()
