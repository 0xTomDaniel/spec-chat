"""Record store files, held set, and lock (jev-suggestions #record-store, #record-held, #record-lock, #proof-store)."""
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
JEV = ROOT / "skill" / "review-spec" / "assets" / "jev.py"
spec = importlib.util.spec_from_file_location("jev_record_store_test", JEV)
jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)

LINE = ('{"answer": {"confidence": 0.91, "label": "no", "probabilities": {"no": 0.91, "yes": 0.09}}, '
        '"cache_key": "%s", "kind": "contradicts", "model": "jev", "outcome": "%s", "pair_kind": "contradicts", '
        '"question_set": {"id": "contradicts", "version": 3}, "record_id": "judgment-%032x", '
        '"sources": {"addresses": ["a.spec.html#x", "b.spec.html#y"], "revision": "head"}, "source": "jev", '
        '"threshold": 0.4, "time": "2026-10-01T00:00:00.000Z"}\n')


def key_of(i):
    return "sha256:" + hashlib.sha256(b"%d" % i).hexdigest()


def write_store(path, count):
    """A synthetic store of count records, written straight into its split files (fast: no JSON encoding)."""
    directory = Path(path).with_name(Path(path).stem)
    directory.mkdir(parents=True)
    for start in range(0, count, 100000):
        shards = {}
        for i in range(start, min(count, start + 100000)):
            key = key_of(i)
            shards.setdefault(hashlib.sha256(key.encode()).hexdigest()[:3], []).append(LINE % (key, "hidden", i))
        for shard, lines in shards.items():
            with (directory / (shard + ".jsonl")).open("a") as stream:
                stream.writelines(lines)


# Peak resident memory of this process since exec (ru_maxrss would carry the forking parent's peak).
PEAK = "int(next(l for l in open('/proc/self/status') if l.startswith('VmHWM')).split()[1])"


CHILD = r"""
import importlib.util, json, random, sys, time
spec = importlib.util.spec_from_file_location("jev", sys.argv[1]); jev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jev)
path, count, ops, held = sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
key_of = lambda i: "sha256:" + __import__("hashlib").sha256(b"%d" % i).hexdigest()
store = jev.JudgmentStore(path, held=held)
rng = random.Random(7)
miss = []
for n in range(ops):
    i = rng.randrange(count * 2)  # half the reads miss the store
    t = time.perf_counter(); record = store.get(key_of(i)); miss.append(time.perf_counter() - t)
    assert (record is not None) == (i < count), i
    if n % 10 == 0:
        store.append({"cache_key": key_of(count * 3 + n), "record_id": "judgment-new-%d" % n, "outcome": "hidden",
                      "answer": {"label": "no", "probabilities": {"no": 0.9}}})
    if n % 100 == 0:
        store.decided("dismissed", "project", "rule text", "a.spec.html")
assert len(store._held) <= held
miss.sort()
print(json.dumps({"rss_kb": PEAK,
                  "p50_ms": miss[len(miss) // 2] * 1000, "p99_ms": miss[int(len(miss) * .99)] * 1000}))
""".replace("PEAK", PEAK)


def child_run(path, count, ops, held):
    out = subprocess.run([sys.executable, "-c", CHILD, str(JEV), str(path), str(count), str(ops), str(held)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def record(key, record_id, outcome="hidden", **fields):
    return {"cache_key": key, "record_id": record_id, "outcome": outcome, **fields}


class StoreMemoryTest(unittest.TestCase):
    """#proof-store, #acceptance-store-memory: peak memory after many reads and writes does not grow with the store."""

    def test_peak_memory_is_independent_of_store_size(self):
        large = int(os.environ.get("JEV_STORE_RECORDS", "1000000"))
        with tempfile.TemporaryDirectory() as tmp:
            write_store(Path(tmp) / "small" / "records.jsonl", 10000)
            write_store(Path(tmp) / "large" / "records.jsonl", large)
            small_run = child_run(Path(tmp) / "small" / "records.jsonl", 10000, 6000, 2000)
            large_run = child_run(Path(tmp) / "large" / "records.jsonl", large, 6000, 2000)
        print("store memory: 10k records %s; %d records %s" % (small_run, large, large_run), file=sys.stderr)
        self.assertLess(large_run["rss_kb"] - small_run["rss_kb"], 16 * 1024, "peak memory grew with the store")
        self.assertLess(large_run["p99_ms"], 50, "a missed lookup read more than its key's file")


class StoreLockTest(unittest.TestCase):
    """#record-lock: no lock is held while a file is read."""

    def test_held_read_and_write_answer_while_another_lookup_reads_its_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl")
            store.append(record("sha256:held", "judgment-held", "shown"))
            reading, release = threading.Event(), threading.Event()
            lines = jev._lines

            def slow(path):
                if path == store._shard_path(store._shard("sha256:missed")):
                    reading.set()
                    release.wait(10)
                return lines(path)

            with patch.object(jev, "_lines", slow):
                other = threading.Thread(target=store.get, args=("sha256:missed",))
                other.start()
                self.assertTrue(reading.wait(5))
                started = time.monotonic()
                self.assertEqual(store.get("sha256:held")["record_id"], "judgment-held")
                store.append(record("sha256:written", "judgment-written", "shown"))
                self.assertLess(time.monotonic() - started, 1, "a held read or a write waited for a file read")
                self.assertTrue(other.is_alive())
                release.set()
                other.join(5)

    def test_a_write_racing_a_missed_lookup_is_seen(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl", held=1)
            lines = jev._lines
            raced = []

            def racing(path):
                if not raced:  # the write lands, and is evicted, while the lookup reads the old file
                    raced.append(path)
                    store.append(record("sha256:k", "judgment-k", "shown"))
                    store.append(record("sha256:other", "judgment-other", "shown"))
                return lines(path)

            store.get("sha256:warm")  # splits the store before the race
            with patch.object(jev, "_lines", racing):
                self.assertEqual(store.get("sha256:k")["record_id"], "judgment-k")


class StoreFilesTest(unittest.TestCase):
    """#record-store, #acceptance-store-files: plain JSONL split by key, reused after restart."""

    def test_records_are_one_line_each_in_plain_files_split_by_key_and_reused_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            for i in range(50):
                store.append(record(key_of(i), "judgment-%d" % i, "shown"))
            files = sorted((Path(tmp) / "records").glob("*.jsonl"))
            self.assertGreater(len(files), 30)
            lines = [json.loads(line) for f in files for line in f.read_text().splitlines()]
            self.assertEqual(sorted(r["record_id"] for r in lines), sorted("judgment-%d" % i for i in range(50)))
            again = jev.JudgmentStore(path)
            self.assertEqual(again.get(key_of(7))["record_id"], "judgment-7")
            self.assertEqual(len(list(again.records())), 50)

    def test_replace_rule_is_unchanged_resolution_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            store.append(record("sha256:k", "judgment-off", "off"))
            store.append(record("sha256:k", "judgment-shown", "shown"))
            self.assertEqual(store.append(record("sha256:k", "judgment-late", "shown"))["record_id"], "judgment-shown")
            store.resolve("judgment-shown", "dismissed", reason="wrong", project="p", rule="r", spec="a.spec.html")
            store.amend("sha256:k", name="named")
            again = jev.JudgmentStore(path)
            held = again.get("sha256:k")
            self.assertEqual((held["record_id"], held["resolution"]["status"], held["name"]),
                             ("judgment-shown", "dismissed", "named"))

    def test_by_id_resolve_and_decisions_work_after_restart_without_holding_the_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            store.append(record("sha256:scope", "judgment-scope", "shown"))
            again = jev.JudgmentStore(path, held=4)
            for i in range(10):
                again.get(key_of(i))
            holds = {"project": "p", "rule": jev.rule_identity("text")}
            self.assertEqual(again.resolve("judgment-scope", "not-a-rule", confirmed=True, **holds)["record_id"],
                             "judgment-scope")
            third = jev.JudgmentStore(path)
            self.assertIsNotNone(third.decided("not-a-rule", "p", "text"))
            self.assertEqual([r["record_id"] for r in third.confirmed_records()], ["judgment-scope"])
            self.assertEqual(third._held, {}, "decisions were read from the records")

    def test_construction_reads_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            path.write_text(json.dumps(record("sha256:k", "judgment-k")) + "\n")
            with patch.object(jev, "_lines", side_effect=AssertionError("read at start")):
                jev.JudgmentStore(path)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["records.jsonl"])


class StoreSplitTest(unittest.TestCase):
    """A store written as one file is split on first use, streaming, safe if interrupted, the file kept renamed."""

    def single(self, tmp):
        path = Path(tmp) / "records.jsonl"
        lines = [json.dumps(record(key_of(i), "judgment-%d" % i, "shown")) for i in range(200)]
        lines += ["not json", json.dumps(record(key_of(0), "judgment-retry", "shown"))]
        lines.insert(0, json.dumps(record(key_of(1), "judgment-off", "off")))
        dismissed = record(key_of(2), "judgment-2", "shown", resolution={
            "status": "dismissed", "project": "p", "rule": jev.rule_identity("text"), "spec": "a.spec.html"})
        lines.append(json.dumps(dismissed))
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_split_keeps_every_answer_and_decision_and_renames_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.single(tmp)
            original = path.read_bytes()
            store = jev.JudgmentStore(path)
            self.assertEqual(store.get(key_of(0))["record_id"], "judgment-0")
            self.assertEqual(store.get(key_of(1))["record_id"], "judgment-1")
            self.assertEqual(store.get(key_of(2))["resolution"]["status"], "dismissed")
            self.assertIsNotNone(store.decided("dismissed", "p", "text", "a.spec.html"))
            self.assertFalse(path.exists())
            self.assertEqual((Path(tmp) / "records.jsonl.unsplit").read_bytes(), original)
            self.assertEqual(len(list(jev.JudgmentStore(path).records())), 200)

    def test_interrupted_split_starts_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.single(tmp)
            scratch = Path(tmp) / "records.splitting"
            scratch.mkdir()
            (scratch / "abc.jsonl").write_text("partial")
            store = jev.JudgmentStore(path)
            with patch.object(jev.os, "rename", side_effect=OSError("killed")):
                with self.assertRaises(OSError):
                    store.get(key_of(5))
            self.assertTrue(path.is_file(), "an interrupted split lost the original")
            self.assertEqual(store.get(key_of(5))["record_id"], "judgment-5")
            self.assertFalse(scratch.exists())
            self.assertEqual(len(list(store.records())), 200)

    def test_split_memory_does_not_grow_with_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            peaks = []
            for count in (2000, 200000):
                path = Path(tmp) / str(count) / "records.jsonl"
                path.parent.mkdir()
                with path.open("w") as stream:
                    stream.writelines(LINE % (key_of(i), "hidden", i) for i in range(count))
                script = ("import importlib.util,sys;s=importlib.util.spec_from_file_location('j',sys.argv[1]);"
                          "j=importlib.util.module_from_spec(s);s.loader.exec_module(j);"
                          "st=j.JudgmentStore(sys.argv[2]);assert st.get(sys.argv[3]);"
                          "print(" + PEAK + ")")
                out = subprocess.run([sys.executable, "-c", script, str(JEV), str(path), key_of(count - 1)],
                                     capture_output=True, text=True, check=True)
                peaks.append(int(out.stdout))
            print("split memory: 2k %d kB, 200k %d kB" % tuple(peaks), file=sys.stderr)
            self.assertLess(peaks[1] - peaks[0], 24 * 1024)


if __name__ == "__main__":
    unittest.main()
