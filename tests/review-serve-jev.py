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

    def test_corpus_questions_cover_changed_leaves_non_goals_and_other_specs(self):
        baseline = """
        <section data-anchor="rules"><p data-anchor="changed">The service reads review events.</p></section>
        <section data-anchor="non-goals"><p data-anchor="non-goal-text">The service never writes review events.</p></section>
        """
        current = baseline.replace("reads review events", "writes review events")
        other = """
        <section data-anchor="other"><p data-anchor="same-surface" data-modular-boundary>The review service reads review events.</p></section>
        """
        questions = jev.build_corpus_questions(current, baseline, "docs/current.spec.html", "base", "head",
                                               [{"path": "docs/other.spec.html", "source": other}])
        targets = {question["target"] for question in questions}
        self.assertIn("non-goal-text", targets)
        self.assertIn("docs/other.spec.html#same-surface", targets)
        changed = [question for question in questions if question["id"] == "changed"]
        self.assertTrue(changed)
        self.assertTrue(all(question["sources"][0].endswith("#changed") for question in changed))

    def test_corpus_question_set_has_all_relationship_labels(self):
        sets = jev.load_question_sets(ROOT / "skill" / "review-spec" / "assets" / "jev")
        self.assertEqual(set(sets["corpus"].criteria()),
                         {"contradicts", "overlaps", "oversteps", "unrelated", "unsure"})


if __name__ == "__main__":
    unittest.main()
