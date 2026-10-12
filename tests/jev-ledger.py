"""Warning ledger and disposable records (jev-suggestions #ledger, #ledger-survives, #record-immutable,
#record-disposable, #record-read, #test-host-rule, #measure-feedback, #proof-resolution, #acceptance-ledger-wipe)."""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("jev_ledger_test", ROOT / "skill" / "review-spec" / "assets" / "jev.py")
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)

WHERE = {"project": "p", "spec": "docs/specs/b.spec.html", "anchor": "b-one", "kind": "covered",
         "target": "docs/specs/onboarding.spec.html#acceptance-onboarding"}


def where(**changes):
    return tuple({**WHERE, **changes}[field] for field in ("project", "spec", "anchor", "kind", "target"))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def test_append_is_one_line_and_latest_replaces_by_where(self):
        ledger = jev.WarningLedger(self.path)
        self.assertIsNone(ledger.latest(*where()))
        first = ledger.append({**WHERE, "outcome": "dismissed", "judgment": "judgment-1", "by": "owner",
                               "reason": "Not a screen."})
        self.assertEqual((first["outcome"], first["judgment"], first["by"], first["reason"], first["confirmed"]),
                         ("dismissed", "judgment-1", "owner", "Not a screen.", False))
        self.assertTrue(first["time"].endswith("Z"))
        later = ledger.append({**WHERE, "outcome": "fixed", "judgment": "judgment-1", "by": "audit"})
        self.assertTrue(later["confirmed"], "an audit's label is confirmed")
        self.assertEqual(ledger.latest(*where())["outcome"], "fixed")
        # another kind or cited clause is another warning
        self.assertIsNone(ledger.latest(*where(kind="contradicts")))
        self.assertIsNone(ledger.latest(*where(target=None)))
        lines = self.path.read_text().splitlines()
        self.assertEqual([json.loads(line)["outcome"] for line in lines], ["dismissed", "fixed"])
        # a restart reads the ledger back whole
        self.assertEqual(jev.WarningLedger(self.path).latest(*where())["outcome"], "fixed")

    def test_the_same_outcome_is_never_written_twice(self):
        ledger = jev.WarningLedger(self.path)
        entry = {**WHERE, "outcome": "fixed", "judgment": "judgment-1", "by": "server"}
        ledger.append(entry)
        ledger.append(entry)
        self.assertEqual(len(self.path.read_text().splitlines()), 1)

    def test_a_human_rule_decision_is_confirmed_and_other_outcomes_are_not(self):
        ledger = jev.WarningLedger(self.path)
        human = ledger.append({**WHERE, "kind": "candidate", "outcome": "not-a-rule", "judgment": "j", "by": "human"})
        dismissed = ledger.append({**WHERE, "outcome": "dismissed", "judgment": "j", "by": "human"})
        self.assertEqual((human["confirmed"], dismissed["confirmed"]), (True, False))

    def test_an_outcome_without_kind_outcome_or_by_is_refused(self):
        ledger = jev.WarningLedger(self.path)
        for broken in ({**WHERE, "judgment": "j", "by": "owner"}, {**WHERE, "outcome": "fixed", "judgment": "j"},
                       {**WHERE, "outcome": "fixed", "by": "nobody"}, {"project": "p", "outcome": "fixed", "by": "owner"}):
            with self.assertRaises(ValueError):
                ledger.append(broken)
        self.assertFalse(self.path.exists())

    def test_construction_reads_nothing_and_memory_only_without_a_path(self):
        self.path.write_text("not json\n")
        jev.WarningLedger(self.path)  # unread until first use
        memory = jev.WarningLedger()
        memory.append({**WHERE, "outcome": "thread", "judgment": "j", "by": "owner"})
        self.assertEqual(memory.latest(*where())["outcome"], "thread")
        self.assertEqual(jev.WarningLedger(self.path).latest(*where()), None, "unreadable lines are skipped")


class RecordsImmutableTest(unittest.TestCase):
    """#record-immutable: a written record is never amended; outcomes cite it from the ledger."""

    def test_store_has_no_amendment(self):
        for name in ("amend", "resolve", "by_id", "decided", "confirmed_records"):
            self.assertFalse(hasattr(jev.JudgmentStore, name), name)
        self.assertFalse(hasattr(jev, "DECISIONS"))


class EvalReadTest(unittest.TestCase):
    """#record-read, #measure-feedback, #proof-resolution: one eval read joins ledger outcomes to records."""

    def test_eval_read_joins_outcomes_and_counts_only_confirmed_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl")
            ledger = jev.WarningLedger(Path(tmp) / "ledger.jsonl")
            store.append({"cache_key": "sha256:a", "record_id": "judgment-a", "outcome": "shown"})
            store.append({"cache_key": "sha256:b", "record_id": "judgment-b", "outcome": "shown"})
            ledger.append({**WHERE, "outcome": "dismissed", "judgment": "judgment-a", "by": "owner", "reason": "r"})
            ledger.append({**WHERE, "anchor": "b-two", "outcome": "fixed", "judgment": "judgment-b", "by": "server"})
            joined = {record["record_id"]: [o["outcome"] for o in outcomes]
                      for record, outcomes in jev.eval_read(store.records(), ledger)}
            self.assertEqual(joined, {"judgment-a": ["dismissed"], "judgment-b": ["fixed"]})
            self.assertEqual(list(jev.eval_read(store.records(), ledger, confirmed=True)), [])
            ledger.append({**WHERE, "anchor": "b-two", "outcome": "fixed", "judgment": "judgment-b", "by": "audit"})
            labels = [(record["record_id"], [o["by"] for o in outcomes])
                      for record, outcomes in jev.eval_read(store.records(), ledger, confirmed=True)]
            self.assertEqual(labels, [("judgment-b", ["audit"])])

    def test_eval_read_streams_an_evals_own_copy_of_the_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl")
            store.append({"cache_key": "sha256:a", "record_id": "judgment-a", "outcome": "shown"})
            shutil.copytree(Path(tmp) / "records", Path(tmp) / "copy")
            shutil.rmtree(Path(tmp) / "records")
            ledger = jev.WarningLedger()
            ledger.append({**WHERE, "outcome": "fixed", "judgment": "judgment-a", "by": "audit"})
            copy = jev.JudgmentStore(Path(tmp) / "copy.jsonl")
            self.assertEqual([r["record_id"] for r, _ in jev.eval_read(copy.records(), ledger)], ["judgment-a"])


class ServiceFilesTest(unittest.TestCase):
    """#ledger, #test-host-rule: the live ledger in the state dir apart from records; test namespace its own dir."""

    def test_live_and_test_namespaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = jev.JevService(state_dir=tmp, api_key="fake-key")
            try:
                self.assertEqual(service.seam.ledger.path, Path(tmp) / "ledger.jsonl")
                self.assertEqual(service.seam.store.dir, Path(tmp) / "records")
                self.assertEqual(service.test_seam.ledger.path, Path(tmp) / "test" / "ledger.jsonl")
                self.assertEqual(service.test_seam.store.dir, Path(tmp) / "test" / "records")
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
