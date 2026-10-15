"""Record store files, held set, and lock (jev-suggestions #record-store, #record-held, #record-lock, #proof-store, #record-immutable)."""
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
import tracemalloc
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
    """#proof-store, #acceptance-store-memory: peak memory after many reads and writes does not grow with the store;
    #acceptance-store-flow-speed: a missed lookup on a large store is as fast (p99), and held reads never wait (StoreLockTest)."""

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


def spec_page(words, n=12):
    """A spec with n stories, n criteria, and n rule clauses: its coverage and corpus questions run to megabytes."""
    return "".join(
        f'<p data-user-story data-anchor="story-{i}">As a reader of {words} {i}, I want item {i} kept.</p>'
        f'<p data-acceptance-criterion data-anchor="acceptance-{i}" data-story="story-{i}">Given {words} {i}, '
        f'When it is read, Then item {i} shows.</p>'
        f'<section data-anchor="part-{i}"><p data-anchor="rule-{i}">The service {words} rule {i} holds.</p></section>'
        for i in range(n))


class ReadMemoryTest(unittest.TestCase):
    """#acceptance-store-memory (sc#157): Jev reads of specs being edited keep the service's memory under a fixed
    ceiling. Kept builder results are bounded by bytes, not count, and an ask's per-key lock goes when it is done."""

    def test_memory_does_not_grow_with_reads_of_edited_specs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)  # last: after the service stops
        root = Path(tmp.name) / "repo"
        specs = root / "docs" / "specs"
        specs.mkdir(parents=True)
        for name in ("a", "b"):
            (specs / f"{name}.spec.html").write_text(spec_page("review events " + name))
        run = lambda *args: subprocess.run(("git", "-C", str(root), *args), check=True, capture_output=True)
        run("init", "-q")
        run("add", "-A")
        run("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-qm", "base")
        run("update-ref", "refs/remotes/origin/main", "HEAD")
        rows = [{"slug": "a", "root": str(root), "spec": f"docs/specs/{name}.spec.html", "project": "p",
                 "spec_file": str(specs / f"{name}.spec.html")} for name in ("a", "b")]

        class Provider:
            def decide(self, payload):
                return {"answers": {next(iter(payload["questions"])): {"choice": "no", "confidence": 0.9}}}

        service = jev.JevService(state_dir=Path(tmp.name) / "state", provider=Provider(), api_key="fake")
        self.addCleanup(service.stop)
        limit = 1 << 20
        service.__dict__["_kept_results"] = jev._Kept(limit)
        service.seam.store.limit = 200  # held records have their own fixed bound (#record-held)

        def read(n):
            for row in rows:
                page = Path(row["spec_file"])
                page.write_text(spec_page("review events %s edit %d" % (row["spec"], n)))  # every clause changed
                service.response(row, str(page), row["spec"], "HEAD", [], "", rows)
            deadline = time.monotonic() + 30
            while service._asking and time.monotonic() < deadline:
                time.sleep(0.01)

        tracemalloc.start()
        self.addCleanup(tracemalloc.stop)
        for n in range(3):
            read(n)
        warm = tracemalloc.get_traced_memory()[0]
        for n in range(3, 11):
            read(n)
        grown = tracemalloc.get_traced_memory()[0] - warm
        print("read memory: grew %d KB over 8 reads per edited spec; %d key locks left"
              % (grown >> 10, len(service.seam._key_locks)), file=sys.stderr)
        self.assertLess(grown, 1 << 20, "memory grew with reads")
        self.assertLessEqual(service._kept.used, limit)
        self.assertEqual(len(service.seam._key_locks), 0, "a lock kept per asked key")


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

            store.get("sha256:warm")
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

    def test_replace_rule_is_unchanged_and_a_record_is_never_amended(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            store.append(record("sha256:k", "judgment-off", "off"))
            store.append(record("sha256:k", "judgment-shown", "shown"))
            self.assertEqual(store.append(record("sha256:k", "judgment-late", "shown"))["record_id"], "judgment-shown")
            again = jev.JudgmentStore(path)
            self.assertEqual(again.get("sha256:k")["record_id"], "judgment-shown")
            self.assertEqual([r["record_id"] for r in again.records()], ["judgment-shown"])

    def test_construction_reads_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            path.write_text(json.dumps(record("sha256:k", "judgment-k")) + "\n")
            with patch.object(jev, "_lines", side_effect=AssertionError("read at start")):
                jev.JudgmentStore(path)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["records.jsonl"])


if __name__ == "__main__":
    unittest.main()
