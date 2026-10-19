"""Record store files, held set, lock, and compaction (jev-suggestions #record-store, #record-held, #record-lock,
#record-compact, #proof-store, #record-immutable)."""
import gc
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


# The set version asking every record here: its generation is the directory named by the set's id and version.
SET = {"id": "contradicts", "version": 3}
GENERATION = "contradicts@3"


def key_of(i):
    return "sha256:" + hashlib.sha256(b"%d" % i).hexdigest()


def write_store(path, count):
    """A synthetic store of count records, written straight into its generation's split files (fast: no JSON
    encoding)."""
    directory = Path(path).with_name(Path(path).stem) / GENERATION
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
version = {"id": "contradicts", "version": 3}
store = jev.JudgmentStore(path, held=held, versions=[version])
rng = random.Random(7)
miss = []
for n in range(ops):
    i = rng.randrange(count * 2)  # half the reads miss the store
    t = time.perf_counter(); record = store.get(key_of(i)); miss.append(time.perf_counter() - t)
    assert (record is not None) == (i < count), i
    if n % 10 == 0:
        store.append({"cache_key": key_of(count * 3 + n), "record_id": "judgment-new-%d" % n, "outcome": "hidden",
                      "question_set": version, "answer": {"label": "no", "probabilities": {"no": 0.9}}})
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
    return {"cache_key": key, "record_id": record_id, "outcome": outcome, "question_set": SET, **fields}


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
    ceiling. Kept builder results are bounded by bytes, not count, and an ask's per-key lock goes when it is done.

    No growth with the number of reads, as a ratio, not a byte count: twice as many later reads grow memory less
    than the first reads did. A ceiling the first reads fill passes it with room for allocator and interpreter
    noise; growth per read fails it once each read adds a quarter of that ceiling."""

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
            gc.collect()  # garbage awaiting the cycle collector is no memory the service keeps
            return tracemalloc.get_traced_memory()[0]

        tracemalloc.start()  # counts only what is allocated from here on and still held
        self.addCleanup(tracemalloc.stop)
        first = [read(n) for n in range(4)][-1]
        later = [read(n) for n in range(4, 12)][-1] - first
        print("read memory: the first 4 reads grew %d KB, the next 8 grew %d KB; %d key locks left"
              % (first >> 10, later >> 10, len(service.seam._key_locks)), file=sys.stderr)
        self.assertLess(later, first, "memory grew with reads")
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
                if path == store._file(SET, "sha256:missed"):
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
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl", held=1, versions=[SET])
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
    """#record-store, #acceptance-store-files: plain JSONL in the set version's generation split by key, reused
    after restart."""

    def test_records_are_one_line_each_in_plain_files_split_by_key_and_reused_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            store = jev.JudgmentStore(path)
            for i in range(50):
                store.append(record(key_of(i), "judgment-%d" % i, "shown"))
            self.assertEqual([p.name for p in (Path(tmp) / "records").iterdir()], [GENERATION])
            files = sorted((Path(tmp) / "records" / GENERATION).glob("*.jsonl"))
            self.assertGreater(len(files), 30)
            lines = [json.loads(line) for f in files for line in f.read_text().splitlines()]
            self.assertEqual(sorted(r["record_id"] for r in lines), sorted("judgment-%d" % i for i in range(50)))
            again = jev.JudgmentStore(path)
            self.assertEqual(again.get(key_of(7))["record_id"], "judgment-7")
            self.assertEqual(len(list(again.records())), 50)

    def test_a_lookup_reads_its_keys_file_in_the_loaded_generations_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            retired = {"id": "contradicts", "version": 2}
            writer = jev.JudgmentStore(path)
            writer.append(record("sha256:k", "judgment-k", "shown"))
            jev._append_line(writer._file(retired, "sha256:k"), record("sha256:k", "judgment-retired", "hidden",
                                                                      question_set=retired))
            self.assertTrue(writer._file(retired, "sha256:k").exists())
            reads = []
            lines = jev._lines
            with patch.object(jev, "_lines", lambda path: reads.append(path) or lines(path)):
                found = jev.JudgmentStore(path, versions=[SET, {"id": "oversteps", "version": 1}]).get("sha256:k")
            self.assertEqual(found["record_id"], "judgment-k")
            self.assertEqual(reads, [Path(tmp) / "records" / GENERATION / (writer._shard("sha256:k") + ".jsonl")])

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


class StoreCompactTest(unittest.TestCase):
    """#record-compact, #acceptance-store-compact: after each start, in the background, the store keeps the
    generations of the loaded set versions and each record the warning ledger cites, and removes the rest."""

    WHERE = {"project": "p", "spec": "docs/specs/a.spec.html", "kind": "contradicts",
             "target": "docs/specs/b.spec.html#b"}

    def box(self, tmp):
        """A box's state: answers of a retired contradicts version, one cited by an owner's dismissal, answers of the
        version the service loads, and files written before generations holding both, one of those cited too."""
        state = Path(tmp) / "state"
        loaded = jev.load_question_sets()["contradicts"]
        current = {"id": loaded.id, "version": loaded.version}
        retired = {"id": loaded.id, "version": loaded.version - 1}
        store = jev.JudgmentStore(state / "records.jsonl")
        for i in range(40):
            store.append(record(key_of(i), "judgment-retired-%d" % i, question_set=retired))
            store.append(record(key_of(100 + i), "judgment-current-%d" % i, question_set=current))
        older = {"judgment-older-retired": retired, "judgment-older-cited": retired, "judgment-older-current": current}
        for n, (record_id, version) in enumerate(older.items()):
            key = key_of(200 + n)
            with (state / "records" / (store._shard(key) + ".jsonl")).open("a") as stream:
                stream.write(json.dumps(record(key, record_id, question_set=version), sort_keys=True) + "\n")
        ledger = jev.WarningLedger(state / "ledger.jsonl")
        for anchor, cited in (("a", "judgment-retired-7"), ("c", "judgment-older-cited")):
            ledger.append({**self.WHERE, "anchor": anchor, "outcome": "dismissed", "judgment": cited, "by": "owner",
                           "reason": "Jev is wrong here"})
        return state, current, retired

    @staticmethod
    def generation(state, version):
        return state / "records" / ("%s@%s" % (version["id"], version["version"]))

    @staticmethod
    def lines(directory):
        return [line for path in sorted(directory.iterdir()) for line in path.read_bytes().splitlines(keepends=True)]

    def test_a_restart_keeps_the_loaded_versions_answers_and_the_cited_ones_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, current, retired = self.box(tmp)
            cited_line = next(line for line in self.lines(self.generation(state, retired))
                              if b"judgment-retired-7" in line)
            loaded_before = {path.name: path.read_bytes() for path in self.generation(state, current).iterdir()}
            service = jev.JevService(state_dir=state, api_key="fake-key")
            try:
                service.start()
                service._compaction.join(30)
                self.assertFalse(service._compaction.is_alive(), "compaction did not finish")
                store = service.seam.store
                current_ids = ["judgment-current-%d" % i for i in range(40)] + ["judgment-older-current"]
                self.assertEqual(sorted(r["record_id"] for r in store.records()),
                                 sorted(current_ids + ["judgment-retired-7", "judgment-older-cited"]))
                joined = {r["record_id"]: [(o["outcome"], o["by"]) for o in outcomes]
                          for r, outcomes in jev.eval_read(store.records(), service.seam.ledger) if outcomes}
                self.assertEqual(joined, {"judgment-retired-7": [("dismissed", "owner")],
                                          "judgment-older-cited": [("dismissed", "owner")]})
                for i in range(40):
                    self.assertEqual(store.get(key_of(100 + i))["record_id"], "judgment-current-%d" % i)
                self.assertEqual(store.get(key_of(202))["record_id"], "judgment-older-current")
            finally:
                service.stop()
            retired_lines = self.lines(self.generation(state, retired))
            self.assertIn(cited_line, retired_lines, "the cited record was not kept whole")
            self.assertEqual(sorted(json.loads(line)["record_id"] for line in retired_lines),
                             ["judgment-older-cited", "judgment-retired-7"])
            self.assertEqual(sorted(p.name for p in (state / "records").iterdir()),
                             sorted(p.name for p in (self.generation(state, retired), self.generation(state, current))),
                             "a file written before generations is left")
            for name, data in loaded_before.items():
                self.assertTrue((self.generation(state, current) / name).read_bytes().startswith(data),
                                "a loaded generation's record changed")

    def test_compaction_runs_in_the_background_and_holds_up_no_lookup_or_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, current, retired = self.box(tmp)
            service = jev.JevService(state_dir=state, api_key="fake-key")
            reading, release = threading.Event(), threading.Event()
            lines = jev._lines

            def slow(path):
                if threading.current_thread().name == "spec-chat-jev-compact" and path.name != "ledger.jsonl":
                    reading.set()
                    release.wait(10)
                return lines(path)

            try:
                with patch.object(jev, "_lines", slow):
                    started = time.monotonic()
                    service.start()
                    self.assertTrue(reading.wait(5), "compaction read no store file")
                    self.assertEqual(service.seam.store.get(key_of(105))["record_id"], "judgment-current-5")
                    written = service.seam.store.append(record(key_of(300), "judgment-new", "shown",
                                                               question_set=current))
                    self.assertEqual(written["record_id"], "judgment-new")
                    self.assertLess(time.monotonic() - started, 1,
                                    "start, a lookup, or an append waited for compaction")
                    self.assertTrue(service._compaction.is_alive())
                    release.set()
                    service._compaction.join(30)
            finally:
                release.set()
                service.stop()
            self.assertEqual(jev.JudgmentStore(state / "records.jsonl").get(key_of(300))["record_id"],
                             "judgment-new")

    def test_a_retired_file_becomes_its_cited_lines_whole_and_a_reader_never_meets_a_partial_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jev.JudgmentStore(Path(tmp) / "records.jsonl", versions=[SET])
            retired = Path(tmp) / "records" / "contradicts@2"
            retired.mkdir(parents=True)
            old = {"id": "contradicts", "version": 2}
            line = lambda n: (json.dumps(record(key_of(n), "judgment-%d" % n, question_set=old), sort_keys=True)
                              + "\n").encode()
            held = line(1) + line(2) + line(3) + line(4)[:40]  # the last line a write still in progress
            (retired / "abc.jsonl").write_bytes(held)
            (Path(tmp) / "records" / "oversteps@1").mkdir()
            (Path(tmp) / "records" / "oversteps@1" / "abc.jsonl").write_bytes(line(5))
            with (retired / "abc.jsonl").open("rb") as reader:
                store.compact({"judgment-2"})
                self.assertEqual(reader.read(), held, "a reader holding the file lost lines")
            self.assertEqual((retired / "abc.jsonl").read_bytes(), line(2))
            self.assertEqual(sorted(p.name for p in (Path(tmp) / "records").iterdir()), ["contradicts@2"],
                             "an emptied generation is left")

    def test_services_sharing_the_store_compact_together_and_a_stopped_runs_claim_is_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "records.jsonl"
            records = Path(tmp) / "records"
            records.mkdir()
            old = {"id": "contradicts", "version": 2}
            expected = set()
            for i in range(300):
                version = SET if i % 3 else old
                cited = i % 30 == 0
                if version is SET or cited:
                    expected.add("judgment-%d" % i)
                with (records / (jev.JudgmentStore(path)._shard(key_of(i)) + ".jsonl")).open("a") as stream:
                    stream.write(json.dumps(record(key_of(i), "judgment-%d" % i, question_set=version)) + "\n")
            # A run stopped after claiming a file, before moving its records.
            (records / "fff.jsonl.1-0000.compacting").write_text(
                json.dumps(record(key_of(1000), "judgment-claimed", question_set=SET)) + "\n")
            expected.add("judgment-claimed")
            cited = {"judgment-%d" % i for i in range(0, 300, 30)}
            stores = [jev.JudgmentStore(path, versions=[SET]) for _ in range(2)]
            threads = [threading.Thread(target=store.compact, args=(cited,)) for store in stores]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            self.assertEqual({r["record_id"] for r in jev.JudgmentStore(path).records()}, expected)
            self.assertEqual(sorted(p.name for p in records.iterdir()), ["contradicts@2", GENERATION])
            for line in (line for f in records.glob("*/*") for line in f.read_bytes().splitlines(keepends=True)):
                self.assertTrue(line.endswith(b"\n") and json.loads(line), "a partial line")
            self.assertEqual(stores[0].get(key_of(1000))["record_id"], "judgment-claimed")


if __name__ == "__main__":
    unittest.main()
