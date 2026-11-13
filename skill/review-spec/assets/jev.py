"""Small server-side Jev seam used by review-serve."""

from __future__ import annotations

import functools
import hashlib
import heapq
import itertools
import json
import math
import os
import posixpath
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    import spool
except ModuleNotFoundError:
    import importlib.util
    _spool_spec = importlib.util.spec_from_file_location("jev_spool", os.path.join(os.path.dirname(__file__), "spool.py"))
    spool = importlib.util.module_from_spec(_spool_spec)
    _spool_spec.loader.exec_module(spool)


MODEL = "typesafe/jev-1.13"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
# Its own budget: the general LLM reasons before its label (up to ~40 s live), past Jev's 15 s decisions.
GENERAL_LLM_TIMEOUT = 90.0
DEFAULT_THRESHOLD = 0.4
DEFAULT_MAX_INPUT_TOKENS = 32000
RETRYABLE_OUTCOMES = frozenset({"off", "unavailable"})
# Draft-check question kinds that carry provenance fields (#record-inputs, #record-pairing).
DRAFT_CHECK_KINDS = frozenset({"contradicts", "contradicts-nongoal", "oversteps", "overlaps"})
# The one ask queue's two lines (jev-suggestions #ask-queue): Jev asks at once per review service, and general LLM
# runs at once, fewer, as each starts a claude process on the box.
ASK_WORKERS = 8
LLM_RUNS = 2
# Asks that wait at most (review-service #steady-bound): past them an ask stays pending unqueued, and a read that
# still misses its key queues it again (#ask-queue), so the queue holds no more however many pairs reads walk.
ASK_WAITING = 1024
# A reader wants its waiting asks while a read of it runs and for this long after one ends (#ask-queue): the page
# and jev-read read again 2 s after a read while anything is pending, so a reader silent longer has gone (a closed
# or hidden tab, a finished agent read) and the asks only it wanted leave the queue unasked.
POLL_WINDOW = 10.0
_monotonic = time.monotonic
# The one retry rule (jev-contract #retry): a failed ask is asked again after a pause, the provider's
# given wait, else this many seconds; the pause is written on its record, so no worker waits it out.
RETRY_PAUSE = 60.0
# A refused key (jev-contract #retry-refused): these statuses refuse the key itself, not one question, so they pause
# every ask of the program, the provider's given wait, else this many seconds.
REFUSED = frozenset({401, 402, 403})
REFUSED_PAUSE = 300.0
_wall = time.time
# Jev was below threshold on a set with a general LLM fallback (project-rules #q-fallback): the LLM decides next.
ESCALATED = "escalated"
REPLACEABLE_OUTCOMES = RETRYABLE_OUTCOMES | {ESCALATED}
# Item states a read has not decided: no mark it lacks is fixed (#record-resolution).
UNDECIDED = frozenset({"pending", "unavailable", "oversize"})
# Draft-check P(yes) below the verify cutoff (jev-suggestions #asymmetric-silent): recorded, no mark, no escalation.
SILENT_NO = "silent-no"
# Verify-band draft check the LLM verifier confirmed with a quoted span from each clause, or did not (#verifier, #record-outcome).
VERIFIED = "verified"
VERIFIER_REJECTED = "verifier-rejected"
# Outcomes that let a chain continue: the question was answered (#chains, #asymmetric).
CHAIN_CONTINUE = frozenset({"shown", SILENT_NO, VERIFIED, VERIFIER_REJECTED})
# Mark levels (jev-suggestions#markers-levels): the single source, mark kind to a human and an agent level, fixed by kind and never
# by confidence; the agent level is never quieter (#markers-levels-audience). Item labels key it directly; the browser keys derived
# marks (coverage gaps, QA evidence, unsure words) by the other names and reads only the human column.
MARK_LEVELS = {
    "contradicts": {"human": "important", "agent": "important"},
    "missed": {"human": "important", "agent": "important"},  # a project-rule miss (project-rules #mark-order)
    "no-criterion": {"human": "important", "agent": "important"},
    "no-story": {"human": "important", "agent": "important"},
    "qa-failed": {"human": "important", "agent": "important"},
    "qa-stale": {"human": "important", "agent": "important"},
    "overlaps": {"human": "warning", "agent": "warning"},
    "candidate": {"human": "warning", "agent": "warning"},  # a possible project rule (project-rules #approval)
    "oversteps": {"human": "warning", "agent": "important"},
}
# Every chain question is yes or no (jev-suggestions #chains): the two answers a chain step reads.
YES_NO = ("yes", "no")
VOID_ELEMENTS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
IMPLIED_ENDS = {
    "li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"},
    "option": {"option", "optgroup"}, "thead": {"thead", "tbody", "tfoot"},
    "tbody": {"tbody", "tfoot"}, "tfoot": {"tbody", "tfoot"},
    "tr": {"tr"}, "td": {"td", "th"}, "th": {"td", "th"},
    "h1": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "h2": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "h3": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "h4": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "h5": {"h1", "h2", "h3", "h4", "h5", "h6"},
    "h6": {"h1", "h2", "h3", "h4", "h5", "h6"},
}
P_BLOCK_ELEMENTS = frozenset({"address", "article", "aside", "blockquote", "div", "dl", "fieldset", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "menu", "nav", "ol", "p", "pre", "section", "table", "ul"})
BLOCK_TEXT_SEPARATORS = P_BLOCK_ELEMENTS | {"li", "dt", "dd", "tr", "td", "th"}


def _close_implied(stack: list[Any], tag: str) -> None:
    tag = tag.lower()
    closers = set(IMPLIED_ENDS.get(tag, set()))
    if tag in P_BLOCK_ELEMENTS:
        closers.add("p")
    if not closers:
        return
    while stack and stack[-1][0] in closers:
        stack.pop()


def _close_explicit(stack: list[Any], tag: str) -> None:
    tag = tag.lower()
    for index in range(len(stack) - 1, -1, -1):
        if stack[index][0] == tag:
            del stack[index:]
            return



def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value")
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _instant(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def pause_left(record: Mapping[str, Any] | None) -> float:
    """Seconds until a failed record may be asked again; 0 for any other record or a pause that has ended."""
    if not record or record.get("outcome") != "unavailable":
        return 0.0
    try:
        retry_at = datetime.fromisoformat(str(record["retry_at"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(retry_at - _wall(), 0.0)


class ProviderFailure(RuntimeError):
    """A provider failure that says what the provider answered: its HTTP status, and how long to wait before asking
    again when it gave a wait (a Retry-After); either may be None."""

    def __init__(self, message: str, wait: float | None = None, status: int | None = None):
        super().__init__(message)
        self.wait = wait
        self.status = status


def retry_pause(failure: BaseException | None) -> float:
    """A failed call's pause in seconds (jev-contract #retry-pause, #retry-refused): the wait the provider gave, else
    REFUSED_PAUSE for a refused key, else RETRY_PAUSE."""
    wait = getattr(failure, "wait", None)
    if wait is not None:
        return wait
    return REFUSED_PAUSE if getattr(failure, "status", None) in REFUSED else RETRY_PAUSE


class KeyPause:
    """The one pause of a refused provider key (jev-contract #retry-refused, jev-suggestions #provider-refused): one
    per program, so a review service's live and test seams share it. A refusal starts it; until its end no ask calls;
    after it one ask, the probe, calls while every other still calls nothing, and the probe's answer starts a new
    pause when it is a refusal, else ends it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until: float | None = None  # wall seconds the pause ends; None with no refusal
        self._status: int | None = None
        self._probe = False

    def paused(self) -> dict[str, Any]:
        """paused_until and status, the pause's end and the provider's status, until the pause ends; else empty."""
        with self._lock:
            if self._until is None or self._until <= _wall():
                return {}
            return {"paused_until": _instant(self._until), "status": self._status}

    def holds(self) -> bool:
        """Whether no ask may call now: the pause has not ended, or its probe is calling."""
        with self._lock:
            return self._until is not None and (self._probe or self._until > _wall())

    def take(self) -> bool | None:
        """A call's turn: None while the pause holds every call, True for the probe after it, False with no refusal."""
        with self._lock:
            if self._until is None:
                return False
            if self._probe or self._until > _wall():
                return None
            self._probe = True
            return True

    def answered(self, probe: bool, status: Any, until: float | None) -> None:
        """A call's answer: a refusal starts the pause, ending at until; any other answer to the probe ends it."""
        with self._lock:
            if status in REFUSED:
                self._until, self._status, self._probe = until, status, False
            elif probe:
                self._until, self._status, self._probe = None, None, False


class QuestionSet:
    def __init__(self, identifier: str, version: Any, instructions: str = "", labels: Any = None,
                 threshold: float = DEFAULT_THRESHOLD, fallback: bool = False,
                 show_cutoff: float | None = None, verify_cutoff: float | None = None, llm_only: bool = False):
        self.id = identifier
        self.fallback = fallback
        self.llm_only = llm_only  # the general LLM alone answers it, its asks only in the LLM's line (#ask-queue)
        self.version = version
        self.instructions = instructions
        self.labels = [] if labels is None else labels
        self.threshold = threshold
        self.show_cutoff = show_cutoff
        self.verify_cutoff = verify_cutoff
        self.verify: dict[str, Any] | None = None  # the set's own verifier levels (#verifier-role); None asks yes or no

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], fallback_id: str) -> "QuestionSet":
        identifier = str(raw.get("id", fallback_id)).strip()
        version = raw.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("question set version is a whole number (jev-contract #qset-version)")
        labels = raw.get("labels", [])
        threshold = float(raw.get("threshold", DEFAULT_THRESHOLD))
        if not identifier or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("invalid question set")
        if not isinstance(labels, (list, dict)):
            raise ValueError("question set labels must be an array or object")
        fallback = raw.get("fallback", False)
        if not isinstance(fallback, bool):
            raise ValueError("question set fallback is true or false; sets name no model")
        llm_only = raw.get("llm_only", False)
        if not isinstance(llm_only, bool):
            raise ValueError("question set llm_only is true or false")
        show_cutoff = raw.get("show_cutoff")
        verify_cutoff = raw.get("verify_cutoff")
        if show_cutoff is not None:
            show_cutoff = float(show_cutoff)
            if not math.isfinite(show_cutoff) or not 0 <= show_cutoff <= 1:
                raise ValueError("invalid show_cutoff")
        if verify_cutoff is not None:
            verify_cutoff = float(verify_cutoff)
            if not math.isfinite(verify_cutoff) or not 0 <= verify_cutoff <= 1:
                raise ValueError("invalid verify_cutoff")
        qset = cls(identifier, version, str(raw.get("instructions", "")), labels, threshold, fallback,
                   show_cutoff, verify_cutoff, llm_only)
        verify = raw.get("verify")
        if verify is not None:
            levels = verify.get("levels") if isinstance(verify, Mapping) else None
            if not isinstance(levels, Mapping) or not levels or any(
                    not isinstance(v, Mapping) or v.get("shows") not in ("yes", "partial", "no") for v in levels.values()):
                raise ValueError("invalid verify")
            qset.verify = {"rule": str(verify.get("rule", "")), "levels": {str(k): dict(v) for k, v in levels.items()}}
        return qset

    def criteria(self) -> dict[str, str]:
        if isinstance(self.labels, Mapping):
            return {str(k): str(v.get("description", v) if isinstance(v, Mapping) else v) for k, v in self.labels.items()}
        result = {}
        for label in self.labels:
            if isinstance(label, Mapping):
                name = label.get("name", label.get("id"))
                if name:
                    description = str(label.get("description", ""))
                    result[str(name)] = description
        return result

    def examples(self) -> list[dict[str, Any]]:
        """Return structured input and answer examples without flattening them."""
        if not isinstance(self.labels, list):
            return []
        result = []
        for label in self.labels:
            if not isinstance(label, Mapping):
                continue
            name = label.get("name", label.get("id"))
            examples = label.get("examples", [])
            if not name or not isinstance(examples, list):
                continue
            for example in examples:
                if not isinstance(example, Mapping):
                    continue
                value = example.get("input")
                if not isinstance(value, Mapping):
                    continue
                result.append({"input": _jsonable(value), "label": str(example.get("label", name))})
        return result

    def criteria_payload(self) -> dict[str, Any]:
        """Build Decisions API criteria with descriptions and pair examples."""
        if not isinstance(self.labels, list):
            return self.criteria()
        result: dict[str, Any] = {}
        for label in self.labels:
            if not isinstance(label, Mapping):
                continue
            name = label.get("name", label.get("id"))
            if not name:
                continue
            entry: dict[str, Any] = {"description": str(label.get("description", ""))}
            examples = []
            for example in label.get("examples", []):
                if isinstance(example, Mapping) and isinstance(example.get("input"), Mapping):
                    examples.append({"input": _jsonable(example["input"]),
                                     "label": str(example.get("label", name))})
            if examples:
                entry["examples"] = examples
            result[str(name)] = entry
        return result

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.id, "version": self.version, "instructions": self.instructions,
                  "labels": _jsonable(self.labels), "threshold": self.threshold}
        if self.fallback:
            result["fallback"] = True
        if self.show_cutoff is not None:
            result["show_cutoff"] = self.show_cutoff
        if self.verify_cutoff is not None:
            result["verify_cutoff"] = self.verify_cutoff
        return result


def load_question_sets(*directories: str | Path | None) -> dict[str, QuestionSet]:
    result: dict[str, QuestionSet] = {}
    here = Path(__file__).resolve().parent
    candidates = [Path(item) for item in directories if item]
    candidates.extend((here / "jev", here / "../skill/review-spec/assets/jev"))
    seen = set()
    for directory in candidates:
        directory = directory.resolve()
        if directory in seen or not directory.is_dir():
            continue
        seen.add(directory)
        for path in sorted(directory.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, Mapping):
                    result[path.stem] = QuestionSet.from_mapping(raw, path.stem)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    return result


# A candidate's outcomes (project-rules #dismiss-trace): settled by a human or a spec mark, or passed over.
NOT_A_RULE, MARKED, IGNORED = "not-a-rule", "marked", "ignored"
SETTLED = frozenset({NOT_A_RULE, MARKED})
# An owner's resolutions of an Important mark through the agent read (#record-resolution); dismissed names its reason.
OWNER_RESOLUTIONS = ("fixed", "reason", "dismissed")


# The record store's files (#record-store): one generation per question set version, a directory named by the
# set's id and version, and within it records split by cache key across this many plain JSONL files, so a lookup
# that misses reads its key's file in each loaded generation, about 1/4096 of what a read can reach, never the
# whole store.
STORE_SHARDS = 4096  # three hex digits of the key's sha256
_STORE_FILE = re.compile(r"[0-9a-f]{3}\.jsonl")
# A file written before generations, claimed by the compaction moving its records (#record-compact).
_CLAIMED = ".compacting"
# Records held in memory (#record-held, #record-final): stored ones by recency of use, and every waiting one, enough
# for every mark a few busy specs show.
STORE_HELD = 20000
_MISS = object()


def _replaces(current: Mapping[str, Any] | None) -> bool:
    """Whether a later line for the same cache key replaces the one held: only a retryable or escalated outcome is
    replaced, since a record is never amended (#record-immutable)."""
    return current is None or current.get("outcome") in REPLACEABLE_OUTCOMES


def _lines(path: Path):
    """The file's lines as bytes, streamed; none when it does not exist."""
    try:
        stream = path.open("rb")
    except FileNotFoundError:
        return
    with stream:
        yield from stream


def _parsed(line: bytes) -> dict[str, Any] | None:
    try:
        record = json.loads(line)
    except (ValueError, TypeError):
        return None
    return dict(record) if isinstance(record, Mapping) and isinstance(record.get("cache_key"), str) \
        and record["cache_key"] else None


def _generation(question_set: Any) -> str:
    """A generation's directory name: the question set's id and version, each quoted to one path part."""
    ref = question_set if isinstance(question_set, Mapping) else {}
    return "%s@%s" % (quote(str(ref.get("id", "")), safe=""), quote(str(ref.get("version", "")), safe=""))


def _cited(record: Mapping[str, Any], cited: frozenset[str]) -> bool:
    return isinstance(record.get("record_id"), str) and record["record_id"] in cited


def _whole(line: bytes) -> bytes:
    return line if line.endswith(b"\n") else line + b"\n"


def _append(path: Path, data: bytes, sync: bool = False) -> None:
    """Whole lines in one O_APPEND write, so concurrent writers never interleave and no line is rewritten. A missing
    directory is made: compaction removes an emptied generation."""
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    try:
        fd = os.open(path, flags, 0o644)
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, flags, 0o644)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        if sync:
            os.fsync(fd)
    finally:
        os.close(fd)


def _append_line(path: Path, value: Mapping[str, Any]) -> None:
    _append(path, (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))


def _replace(path: Path, data: bytes) -> None:
    """The file becomes `data` at once: written beside it, synced, then renamed over it, so a reader holds the old
    file or opens the new one, never a partial line. Another compaction removing the copy first leaves the file."""
    copy = path.with_name("%s.%d-%s.compact" % (path.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        _append(copy, data, sync=True)
        os.replace(copy, path)
    except FileNotFoundError:
        copy.unlink(missing_ok=True)


class JudgmentStore:
    """Append-only JSONL records, also used as the cache (#record-store, #record-held, #record-lock).

    The path names the store: its records live in the directory beside it named by the path's stem, one generation
    directory per question set version, each split by cache key into STORE_SHARDS files. `versions`, the question
    set versions the server loads ({id, version} each), names the generations a lookup reads and compaction keeps;
    without them a lookup reads every generation present. A record is never amended (#record-immutable): outcomes
    live in the WarningLedger. The records are a disposable cache (#record-disposable) that compaction keeps to what
    a read can reach (#record-compact). Only final records are stored; a waiting one, escalated, off, or
    unavailable, is held in memory until the next record of its key replaces it, and a restart forgets it
    (#record-final). Memory holds the waiting records and the stored ones recent reads and writes used, up to
    `held` in all. No lock is held while a file is read. Construction reads nothing (review-service
    #acceptance-restart). Without a path the store is memory only."""

    def __init__(self, path: str | Path | None = None, *, held: int = STORE_HELD, versions: Any = None):
        self.path = Path(path) if path else None
        self.dir = self.path.with_name(self.path.stem) if self.path else None
        self.versions = None if versions is None else frozenset(map(_generation, versions))
        self.limit = held
        self._held: OrderedDict[str, Any] = OrderedDict()
        # Waiting records by key, oldest first, each with the instant its pause ends (#record-final).
        self._waiting: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._writes: dict[str, int] = {}
        self.lock = threading.RLock()

    # Files

    def _shard(self, key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:3]

    def _file(self, question_set: Any, key: str) -> Path:
        """The key's file in the generation of the set version that asks it."""
        return self.dir / _generation(question_set) / (self._shard(key) + ".jsonl")  # type: ignore[operator]

    def _generations(self) -> list[str]:
        """The generations a lookup reads: the loaded set versions', else every one present."""
        if self.versions is not None:
            return sorted(self.versions)
        try:
            with os.scandir(self.dir) as entries:  # type: ignore[arg-type]
                return sorted(entry.name for entry in entries if entry.is_dir())
        except FileNotFoundError:
            return []

    def _scan(self, key: str) -> dict[str, Any] | None:
        """The key's current record from its file in each generation read, streamed with no lock held; only lines
        naming the key parse. The key hashes its set version, so one generation at most holds it."""
        needle = json.dumps(key, ensure_ascii=False)[1:-1].encode("utf-8")
        name = self._shard(key) + ".jsonl"
        current = None
        for generation in self._generations():
            for line in _lines(self.dir / generation / name):  # type: ignore[operator]
                if needle in line:
                    record = _parsed(line)
                    if record is not None and record["cache_key"] == key and _replaces(current):
                        current = record
            if current is not None:
                break
        return current

    def _write(self, record: Mapping[str, Any]) -> None:
        """Called under the lock: one append of a final record to the key's file in its set version's generation,
        then the record is held, replacing the key's waiting one."""
        key = record["cache_key"]
        if self.path:
            _append_line(self._file(record.get("question_set"), key), record)
            self._wrote(self._shard(key))
        self._waiting.pop(key, None)
        self._hold(key, record)

    def _wrote(self, shard: str) -> None:
        """Called under the lock: a lookup reading a file of this shard reads again (get)."""
        self._writes[shard] = self._writes.get(shard, 0) + 1

    @staticmethod
    def _files(directory: Path):
        """The store files directly in a directory, in name order; none when it is gone."""
        try:
            return sorted(path for path in directory.iterdir() if _STORE_FILE.fullmatch(path.name))
        except (FileNotFoundError, NotADirectoryError):
            return []

    # Held records

    def _hold(self, key: str, value: Any) -> None:
        self._held[key] = value
        self._held.move_to_end(key)
        self._bound()

    def _wait(self, record: dict[str, Any]) -> None:
        """Called under the lock: a waiting record is held, never stored, until the next record of its key replaces
        it (#record-final, jev-contract #record-waiting), replacing the key's held miss. Only a failed record's pause
        ends; an escalated or off one has none, so the bound keeps it before any stored one."""
        key = record["cache_key"]
        self._held.pop(key, None)
        self._waiting.pop(key, None)
        until = _wall() + pause_left(record) if record.get("outcome") == "unavailable" else math.inf
        self._waiting[key] = (until, record)
        self._bound()

    def _bound(self) -> None:
        """Called under the lock: memory holds at most `limit` records (#record-held). A failed record whose pause
        has ended goes first, oldest first, since it is held only through its pause (#record-final); then a stored
        one, least recently used, since a lookup reads it again; another waiting one, a failed one through its pause
        or an escalated or off one, only when those fill the bound alone, so a failed record answers through its
        pause (jev-contract #retry-before) and an escalated one stays pending (project-rules #pending)."""
        if not self.path or len(self._held) + len(self._waiting) <= self.limit:
            return
        now = _wall()
        for key in [key for key, (until, _) in self._waiting.items() if until <= now]:
            if len(self._held) + len(self._waiting) <= self.limit:
                return
            del self._waiting[key]
        while len(self._held) + len(self._waiting) > self.limit and self._held:
            self._held.popitem(last=False)
        while len(self._waiting) > self.limit:
            self._waiting.popitem(last=False)

    def _held_value(self, key: str) -> Any:
        """Called under the lock: the key's waiting record, else its held stored record or _MISS, else None."""
        waiting = self._waiting.get(key)
        if waiting is not None:
            return waiting[1]
        value = self._held.get(key)
        if value is not None:
            self._held.move_to_end(key)
        return value

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            value = self._held_value(key)
            if value is not None or not self.path:
                return None if value is _MISS else value
        shard = self._shard(key)
        while True:
            with self.lock:
                writes = self._writes.get(shard, 0)
            record = self._scan(key)
            with self.lock:
                value = self._held_value(key)
                if value is not None:
                    return None if value is _MISS else value
                if self._writes.get(shard, 0) == writes:  # no write to this shard raced the read
                    self._hold(key, _MISS if record is None else record)
                    return record

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """Write an ask's record: a final one is stored, a waiting one held (#record-final). A key's final record is
        never replaced: it is returned instead (jev-contract #key-reuse), also when the bound let it go since the
        lookup."""
        record = dict(record)
        current = self.get(record["cache_key"])
        with self.lock:
            held = self._held_value(record["cache_key"])
            if held is not None and held is not _MISS:  # a write since the lookup
                current = held
            if not _replaces(current):
                return current  # type: ignore[return-value]
            if _final(record):
                self._write(record)
            else:
                self._wait(record)
            return record

    def records(self):
        """Every current stored record, streamed one file at a time with no lock held: each generation's, and those
        in files written before generations, as in an eval's older copy. A waiting record is not stored."""
        if not self.path:
            with self.lock:
                held = [value for value in self._held.values() if value is not _MISS]
            yield from held
            return
        older = self._files(self.dir)  # type: ignore[arg-type]
        try:
            generations = sorted(path for path in self.dir.iterdir() if path.is_dir())  # type: ignore[union-attr]
        except FileNotFoundError:
            generations = []
        for files in [older, *map(self._files, generations)]:
            for path in files:
                current: dict[str, dict[str, Any]] = {}
                for line in _lines(path):
                    record = _parsed(line)
                    if record is not None and _replaces(current.get(record["cache_key"])):
                        current[record["cache_key"]] = record
                yield from current.values()

    # Compaction

    def compact(self, cited: Any, stop: threading.Event | None = None) -> None:
        """Keep only what a read can reach (#record-compact): the generations of the loaded set versions (`versions`),
        untouched, and each record whose id is in `cited`, the ones the warning ledger cites.
        Every other generation goes: a file holding no cited record is deleted, one holding some is replaced whole
        by a file of just those lines, so removing is the only change a store file sees and no reader meets a
        partial line. Final records in files written before generations move into theirs on the same terms, waiting
        ones only when cited (#record-final). No lock is held while a file is read, so no lookup or append waits for
        it; `stop` ends it between files."""
        if not self.path or self.versions is None:  # no loaded versions named: nothing is known unreachable
            return
        keep = self.versions
        cited = frozenset(item for item in cited if isinstance(item, str))
        stopped = lambda: stop is not None and stop.is_set()
        try:
            flat = [path for path in self.dir.iterdir()  # type: ignore[union-attr]
                    if path.is_file() and (_STORE_FILE.fullmatch(path.name) or path.name.endswith(_CLAIMED))]
        except FileNotFoundError:
            return
        # A killed run's claims first.
        for path in sorted(flat, key=lambda path: (not path.name.endswith(_CLAIMED), path.name)):
            if stopped():
                return
            self._move(path, keep, cited)
        for directory in sorted(self.dir.iterdir()):  # type: ignore[union-attr]
            if directory.is_dir() and directory.name not in keep:
                if stopped():
                    return
                self._prune(directory, cited, stopped)

    def _move(self, path: Path, keep: frozenset[str], cited: frozenset[str]) -> None:
        """Move the records a read can reach from a file written before generations into their generations, then
        delete the file. It is claimed by a rename first, so two services starting together do not both move it; a
        claim left by a killed run is moved by the next. A claim another service is still moving may be moved twice:
        a key's first final line answers either way (jev-contract #key-reuse)."""
        claimed = path
        if not path.name.endswith(_CLAIMED):
            claimed = path.with_name("%s.%d-%s%s" % (path.name, os.getpid(), uuid.uuid4().hex[:8], _CLAIMED))
            try:
                os.rename(path, claimed)
            except FileNotFoundError:  # another service claimed it
                return
        moved: dict[Path, list[bytes]] = {}
        synced: set[Path] = set()
        keys: list[str] = []
        for line in _lines(claimed):
            record = _parsed(line)
            if record is None:
                continue
            ref = record.get("question_set") or {}
            if _cited(record, cited) or _generation(ref) in keep and _final(record):
                target = self._file(ref, record["cache_key"])
                moved.setdefault(target, []).append(_whole(line))
                keys.append(record["cache_key"])
                if _cited(record, cited):
                    synced.add(target)
        for target, lines in moved.items():
            _append(target, b"".join(lines), sync=target in synced)
        with self.lock:
            for target in moved:
                self._wrote(target.stem)
            for key in keys:  # a miss held while the record was still in the old file
                if self._held.get(key) is _MISS:
                    del self._held[key]
        claimed.unlink(missing_ok=True)

    def _prune(self, directory: Path, cited: frozenset[str], stopped: Callable[[], bool]) -> None:
        """Remove a generation no read reaches, keeping each cited record whole in its file."""
        try:
            paths = sorted(directory.iterdir())
        except FileNotFoundError:
            return
        for path in paths:
            if stopped():
                return
            if not _STORE_FILE.fullmatch(path.name):
                if path.is_file():  # a killed compaction's copy, or one another compaction then keeps out (_replace)
                    path.unlink(missing_ok=True)
                continue
            kept: list[bytes] = []
            dropped = False
            if cited:
                for line in _lines(path):
                    record = _parsed(line)
                    if record is not None and _cited(record, cited):
                        kept.append(_whole(line))
                    else:
                        dropped = True
            if not kept:
                path.unlink(missing_ok=True)
            elif dropped:
                _replace(path, b"".join(kept))
        try:
            directory.rmdir()
        except OSError:  # it still holds cited records, or a service wrote to it meanwhile
            pass


# Who records a warning outcome (#ledger-table).
LEDGER_BY = frozenset({"human", "owner", "server", "audit"})
# A human's decision on a candidate rule, marked or not-a-rule, is a confirmed label at once (#measure-feedback).
CONFIRMED_OUTCOMES = SETTLED
# Where a warning sits (#ledger-subject): kind is the mark kind, as the agent read prints it (JevService._item_where),
# rule for a rule miss and for the human's Not for this spec (JevService._rule_where); text is the identity of a lane's
# text of the cited clause, for a lane mark only.
LEDGER_WHERE = ("project", "spec", "anchor", "kind", "target", "text")
LEDGER_FIELDS = LEDGER_WHERE + ("outcome", "judgment", "by", "reason")
# Who resolves a where (#ledger-resolved): an owner's or human's outcome; the server's own fixed and an audit resolve
# nothing.
RESOLVERS = frozenset({"owner", "human"})


class WarningLedger:
    """Every human or owner outcome of a Jev warning, once (#ledger): one plain JSONL file apart from the records,
    one line appended per outcome, held whole in memory once first used. Keyed by where the warning sits, never by
    cache key, so an outcome survives rewording, new heads, set versions, and deleting the records
    (#ledger-survives); a later outcome for the same where replaces the earlier one. A line written before the where
    held a field reads it as none. Without a path, memory only."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._latest: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._resolved: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._entries: list[dict[str, Any]] = []
        self._loaded = not self.path
        self.lock = threading.RLock()

    @staticmethod
    def _where(entry: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(entry.get(field) or None for field in LEDGER_WHERE)

    def _keep(self, entry: dict[str, Any]) -> None:
        self._entries.append(entry)
        self._latest[self._where(entry)] = entry
        if entry.get("by") in RESOLVERS and entry["outcome"] in OWNER_RESOLUTIONS:
            self._resolved[self._where(entry)] = entry

    def _load(self) -> None:
        """Called under the lock: the whole ledger, read once; an unreadable line is skipped."""
        if self._loaded:
            return
        for line in _lines(self.path):  # type: ignore[arg-type]
            try:
                entry = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(entry, dict) and entry.get("outcome") and entry.get("kind"):
                self._keep(entry)
        self._loaded = True

    def append(self, outcome: Mapping[str, Any], clock: Callable[[], str] = _now) -> dict[str, Any]:
        """Record one outcome: project, spec, anchor, kind, target (a cited clause or rule, when any), outcome,
        judgment (the record id it answers), by, and reason when given. Confirmed when an audit labels it or a
        human decides a candidate rule, marked or not-a-rule. The same outcome as the latest for its where is not written again.
        Raises ValueError when the kind, outcome, or by is missing."""
        entry = {field: outcome[field] for field in LEDGER_FIELDS if outcome.get(field) is not None}
        if not all(isinstance(entry.get(field), str) and entry[field] for field in ("kind", "outcome")) \
                or entry.get("by") not in LEDGER_BY:
            raise ValueError("a ledger outcome names its kind, outcome, and by")
        entry["confirmed"] = entry["by"] == "audit" or entry["outcome"] in CONFIRMED_OUTCOMES
        with self.lock:
            self._load()
            latest = self._latest.get(self._where(entry))
            if latest is not None and all(latest.get(field) == entry.get(field) for field in LEDGER_FIELDS):
                return latest
            entry["time"] = clock()
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                _append_line(self.path, entry)
            self._keep(entry)
            return entry

    def latest(self, project: Any, spec: Any, anchor: Any, kind: Any, target: Any = None,
               text: Any = None) -> dict[str, Any] | None:
        """The latest outcome for this where, else None."""
        with self.lock:
            self._load()
            return self._latest.get(tuple(value or None for value in (project, spec, anchor, kind, target, text)))

    def resolved(self, where: Mapping[str, Any]) -> dict[str, Any] | None:
        """The latest owner's or human's outcome of an Important mark at this where, which resolves it, else None
        (#ledger-resolved): the server's own fixed and an audit's label never displace it."""
        with self.lock:
            self._load()
            return self._resolved.get(self._where(where))

    def entries(self) -> list[dict[str, Any]]:
        """Every outcome, in the order recorded."""
        with self.lock:
            self._load()
            return list(self._entries)


def eval_read(records, ledger: WarningLedger, confirmed: bool = False):
    """The one eval read (#record-read): stream records, from the store or an eval's copy, one at a time, each
    with the ledger outcomes citing it; with confirmed, only confirmed labels, and only records that have one
    (#measure-feedback). Yields (record, outcomes)."""
    cited: dict[str, list[dict[str, Any]]] = {}
    for entry in ledger.entries():
        if entry.get("judgment") and (entry.get("confirmed") or not confirmed):
            cited.setdefault(entry["judgment"], []).append(entry)
    for record in records:
        outcomes = cited.get(record.get("record_id"), [])
        if outcomes or not confirmed:
            yield record, outcomes


def scope_eval(records, ledger: WarningLedger) -> dict[str, Any]:
    """The scope question's precision and recall of every feature against candidate outcomes (project-rules
    #dismiss-trace, jev-suggestions #measure-feedback), from the one eval read's confirmed labels: marked is a rule,
    not-a-rule is none. joined lists each (scope record, outcome)."""
    joined = [(record, outcome) for record, outcomes in eval_read(records, ledger, confirmed=True)
              if record.get("kind") == "scope" for outcome in outcomes if outcome.get("kind") == RULE_CANDIDATE]
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for record, outcome in joined:
        said = _confident_label(record) == RULE_SCOPE
        rule = outcome["outcome"] == MARKED
        counts[("t" if said == rule else "f") + ("p" if said else "n")] += 1
    ratio = lambda part, whole: part / whole if whole else None
    return {**counts, "precision": ratio(counts["tp"], counts["tp"] + counts["fp"]),
            "recall": ratio(counts["tp"], counts["tp"] + counts["fn"]), "joined": joined}


def given_wait(value: str | None, now: Callable[[], float] = time.time) -> float | None:
    """Retry-After as seconds or an HTTP date; None when missing or unreadable."""
    try:
        wait = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            wait = parsedate_to_datetime(str(value)).timestamp() - now()
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return max(wait, 0.0) if math.isfinite(wait) else None


class OpenRouterProvider:
    """Jev Decisions only; general LLM calls run through the Claude CLI (jev-seam #rule-cli-only)."""

    def __init__(self, api_key: str, *, endpoint: str = OPENROUTER_DECISIONS_URL,
                 timeout: float = 15.0):
        self._api_key = api_key.strip()
        self.endpoint = endpoint
        self.timeout = timeout

    def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post(self.endpoint, payload)

    def _post(self, endpoint: str, payload: Mapping[str, Any], timeout: float | None = None) -> Mapping[str, Any]:
        """One call, never a wait: a failed one raises the provider's status and any given wait, for the record's pause
        (jev-contract #retry-pause, #retry-refused, #core-status)."""
        data = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        request = Request(endpoint, data=data,
                          headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                          method="POST")
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            wait = given_wait(exc.headers.get("Retry-After") if exc.headers else None)
            exc.close()
            raise ProviderFailure("OpenRouter decision request failed: HTTP %d" % exc.code, wait, exc.code) from exc
        except (URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("OpenRouter decision request failed") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError("OpenRouter decision was not an object")
        return value


class ServiceRuns:
    """The commands a service runs, each in its own process group: Jev's claude runs and the review service's wake
    commands. stop ends each one still running, its whole group, and reaps it, and starts none after, so none runs on,
    or spends model tokens on an answer nothing records, once the service is gone."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # held to start a run, to drop one, or to stop
        self._running: set[subprocess.Popen] = set()
        self._stopped = False

    def run(self, argv: list[str], *, timeout: float, **kwargs: Any) -> int | None:
        """Run one command to its end in its own process group: its exit code, or None once it outlived timeout and
        its whole group was ended. OSError when it cannot start, or once stop came first; a stop ends it as killed."""
        with self._lock:
            if self._stopped:
                raise OSError("the service is stopping")
            process = subprocess.Popen(argv, start_new_session=True, **kwargs)
            self._running.add(process)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _end_group(process)
            return None
        finally:
            with self._lock:
                self._running.discard(process)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            running = list(self._running)
        for process in running:
            if process.poll() is None:
                _end_group(process)


def _end_group(process: subprocess.Popen) -> None:
    """A run's whole process group ends, and the run is reaped."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    process.wait()


class ClaudeCliProvider:
    """One-shot isolated claude -p subprocess for general LLM fallback (jev-seam RULE 09).

    Runs --bare --settings (isolated: no CLAUDE.md, no hooks, no user context;
    auth via apiKeyHelper in settings.json) with --json-schema for structured output
    and --output-format json for the envelope. The envelope's ``result`` field carries
    the model's text, parsed as the answer JSON."""

    def __init__(self, *, command: str = "claude", timeout: float = GENERAL_LLM_TIMEOUT,
                 settings_path: str | None = None, runs: ServiceRuns | None = None):
        self.command = command
        self.timeout = timeout
        self.settings_path = settings_path or str(Path.home() / ".claude" / "settings.json")
        self.runs = ServiceRuns() if runs is None else runs

    def complete(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """General LLM via isolated claude CLI; same response shape as OpenRouter chat completions."""
        messages = payload.get("messages", [])
        parts: list[str] = []
        for msg in messages:
            content = str(msg.get("content", ""))
            if content:
                parts.append(content)
        schema = payload.get("response_format", {}).get("json_schema", {}).get("schema")
        prompt = "\n\n".join(parts)
        model = str(payload.get("model", ""))
        cmd = [self.command, "-p", "--bare", "--settings", self.settings_path, "--output-format", "json"]
        if model:
            cmd.extend(["--model", model])
        if schema:
            cmd.extend(["--json-schema", json.dumps(schema, ensure_ascii=False)])
        try:
            envelope = json.loads(self._run(cmd, prompt))
            text = str(envelope.get("result", ""))
        except (OSError, ValueError) as exc:
            raise RuntimeError("claude CLI failed") from exc
        # Extract the actual model from the CLI envelope (modelUsage key, #ac-record).
        actual = model
        usage = envelope.get("modelUsage")
        if isinstance(usage, dict) and usage:
            actual = str(next(iter(usage))) or model
        return {"choices": [{"message": {"content": text}}], "model": actual}

    def _run(self, cmd: list[str], prompt: str) -> str:
        """One claude run in its own process group, its output: the whole group is ended once it outlives the
        timeout, or once its runs stop, so nothing it started runs on. Files, not pipes: a child left holding one never
        delays the exit. A timeout, a stop, or a nonzero exit is a failed call, paused by the one retry rule
        (jev-contract #retry)."""
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout:
            stdin.write(prompt.encode("utf-8"))
            stdin.seek(0)
            code = self.runs.run(cmd, timeout=self.timeout, stdin=stdin, stdout=stdout, stderr=subprocess.DEVNULL,
                                 cwd="/tmp")
            if code is None:
                raise RuntimeError("claude CLI timed out")
            if code != 0:
                raise RuntimeError("claude CLI exited %d" % code)
            stdout.seek(0)
            return stdout.read().decode("utf-8")


def _parse_provider_answer(value: Mapping[str, Any], question_name: str) -> tuple[str, float | None, dict[str, float], str]:
    answer = value["answers"][question_name]
    label = answer["choice"]
    if not isinstance(label, str) or not label.strip():
        raise RuntimeError("provider response has no choice")
    probabilities = {str(k): float(v) for k, v in answer.get("probabilities", {}).items()}
    confidence = answer.get("confidence")
    confidence = None if confidence is None else float(confidence)
    if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise RuntimeError("provider response has invalid confidence")
    return label.strip(), confidence, probabilities, str(value.get("model", MODEL))


def _anchor_answer(answer: Mapping[str, Any], candidate_anchors: Mapping[str, Any]) -> dict[str, Any]:
    """Records hold anchors, never source text: map readable candidate labels back to anchor ids."""
    ids = {str(label): str(anchor) for label, anchor in candidate_anchors.items()}
    ids["none"] = "none"
    probabilities: dict[str, float] = {}
    for label, value in answer.get("probabilities", {}).items():
        if label in ids:
            probabilities[ids[label]] = max(probabilities.get(ids[label], 0.0), value)
    return {"label": ids.get(answer.get("label")), "probabilities": probabilities, "confidence": answer.get("confidence")}


def _key_reader(api_key: str | Callable[[], str] | None) -> Callable[[], str]:
    """The model key, read at each use: a callable (the review server's jev provider) or a fixed string.
    The environment is never read (review-service #providers-no-env)."""
    if callable(api_key):
        return lambda: (api_key() or "").strip()
    fixed = (api_key or "").strip()
    return lambda: fixed


def _model_reader(llm_model: str | Callable[[], str] | None) -> Callable[[], str]:
    """The general LLM model id from config, "" when unconfigured (CLI picks its own default)."""
    return _key_reader(llm_model)


# A kept question's key, computed once when it is built (review-service #steady-bound): a read looks keys up,
# never recomputes them. Read by JevSeam.key only; no payload or record carries it.
QUESTION_KEY = "_key"


def _question_key(question: Mapping[str, Any], qset: "QuestionSet") -> str:
    inputs = {"state": question.get("state", {}),
              "criteria": question.get("criteria", {}),
              "question_set": {"id": qset.id, "version": qset.version}}
    return "sha256:" + hashlib.sha256(_canonical(inputs).encode("utf-8")).hexdigest()


def _extract_inputs(question: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract inputs for draft-check records (#record-inputs): clause text, target text, context."""
    state = question.get("state", {})
    if not isinstance(state, Mapping):
        return None
    inputs: dict[str, Any] = {}
    if "after" in state:
        inputs["clause_text"] = state["after"]
    if "target" in state:
        inputs["target_text"] = state["target"]
    ctx = state.get("context")
    if isinstance(ctx, Mapping):
        for k in ("surface", "section", "columns"):
            if k in ctx:
                inputs[k] = ctx[k]
    tctx = state.get("target_context")
    if isinstance(tctx, Mapping):
        tc = {k: tctx[k] for k in ("surface", "section", "columns") if k in tctx}
        if tc:
            inputs["target_context"] = tc
    return inputs if inputs else None


class _OnLine(threading.local):
    """The ask queue line whose run this thread's ask is on (#ask-queue): "jev", "llm", or None outside the queue,
    where an ask makes each call itself, as a seam used alone does."""
    name: str | None = None


_ON_LINE = _OnLine()


class _Move(BaseException):
    """An ask whose next call its line does not make (#ask-queue): the general LLM's on a Jev worker, Jev's on an LLM
    run. The queue moves it, still in flight, to `line`. A BaseException, so no `except Exception` on its way records
    it as a failed call."""

    def __init__(self, line: str):
        super().__init__(line)
        self.line = line


class JevSeam:
    def __init__(self, question_sets: Mapping[str, QuestionSet] | None = None, *, provider: Any = None,
                 api_key: str | Callable[[], str] | None = None, llm_model: str | Callable[[], str] | None = None,
                 record_store: JudgmentStore | None = None, ledger: WarningLedger | None = None,
                 clock: Callable[[], str] = _now, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
                 pause: KeyPause | None = None, runs: ServiceRuns | None = None):
        self.question_sets = dict(question_sets or load_question_sets())
        self.api_key = _key_reader(api_key)
        self.llm_model = _model_reader(llm_model)
        self._provider = provider
        self.store = record_store or JudgmentStore()
        self.ledger = ledger or WarningLedger()
        self.clock = clock
        self.max_input_tokens = max_input_tokens
        # A lock per key while an ask of it runs or waits, dropped after (#acceptance-store-memory).
        self._key_locks: dict[str, list[Any]] = {}
        self._key_locks_lock = threading.Lock()
        # The provider key's one pause (jev-contract #retry-refused): a review service's seams share one.
        self.pause = KeyPause() if pause is None else pause
        # The general LLM's claude runs, which a review service's seams share and its stop ends.
        self.runs = ServiceRuns() if runs is None else runs

    def question_set(self, kind: str) -> QuestionSet:
        return self.question_sets[kind]

    def provider(self) -> Any:
        """The model client for this ask: the injected one, else OpenRouter with the current key; None when off."""
        api_key = self.api_key()
        if not api_key:
            return None
        return self._provider if self._provider is not None else OpenRouterProvider(api_key)

    def _fallback_provider(self, primary: Any) -> Any:
        """The general LLM provider: the injected provider (tests), else always ClaudeCliProvider (#rule-cli-only)."""
        if self._provider is not None:
            return primary
        return ClaudeCliProvider(runs=self.runs)

    def _complete(self, fallback: Any, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """One general LLM call, the fallback's or the verifier's, on an LLM run (#ask-queue): an ask on a Jev worker
        moves to the LLM line first, so no Jev worker waits while the LLM works."""
        if _ON_LINE.name == "jev":
            raise _Move("llm")
        return fallback.complete(payload)

    def _record(self, key: str, kind: str, qset: QuestionSet, sources: Any, revision: Any,
                answer: Mapping[str, Any], outcome: str, model: str = MODEL, escalated: bool = False,
                failure: BaseException | None = None, inputs: Mapping[str, Any] | None = None,
                pairing: Mapping[str, Any] | None = None, verifier: Mapping[str, Any] | None = None,
                until: float | None = None) -> dict[str, Any]:
        extra: dict[str, Any] = {"escalated": True, "source": "llm"} if escalated else {"source": "jev"}
        if outcome == "unavailable":
            # The pause's end, until when the call already set it (#retry-one-call), and the provider's status (#core-status).
            extra["retry_at"] = _instant(_wall() + retry_pause(failure) if until is None else until)
            if getattr(failure, "status", None) is not None:
                extra["status"] = failure.status
        record: dict[str, Any] = {**extra,
            "record_id": "judgment-" + uuid.uuid4().hex,
            "cache_key": key,
            "model": model,
            "question_set": {"id": qset.id, "version": qset.version},
            "kind": kind,
            "pair_kind": kind,
            "sources": {"addresses": _jsonable(sources), "revision": _jsonable(revision)},
            "answer": _jsonable(answer),
            "threshold": qset.threshold,
            "outcome": outcome,
            "time": self.clock(),
        }
        # Cutoffs for draft-check questions (#record-outcome)
        if qset.show_cutoff is not None:
            record["show_cutoff"] = qset.show_cutoff
        if qset.verify_cutoff is not None:
            record["verify_cutoff"] = qset.verify_cutoff
        # Provenance fields (#record-inputs, #record-pairing, #record-verifier)
        if inputs is not None:
            record["inputs"] = _jsonable(inputs)
        if pairing is not None:
            record["pairing"] = _jsonable(pairing)
        if verifier is not None:
            record["verifier"] = _jsonable(verifier)
        return self.store.append(record)

    def key(self, question: Mapping[str, Any]) -> str:
        """The question's record key: the one its build kept (question set id, version, key), else computed."""
        qset = self.question_set(str(question.get("kind", "type")))
        kept = question.get(QUESTION_KEY)
        if kept is not None and kept[:2] == (qset.id, qset.version):
            return kept[2]
        return _question_key(question, qset)

    def cached(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        """The record already held for this question, never asking."""
        record = self.store.get(self.key(question))
        return record if record and record.get("outcome") not in REPLACEABLE_OUTCOMES else None

    def ask(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        """One answer per key at a time: a concurrent ask of the same inputs waits and reuses the record. While a
        refused key's pause holds, the held record, None when nothing is held: nothing is called or written."""
        key = self.key(question)
        with self._key_locks_lock:
            entry = self._key_locks.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                return self._ask(question, key)
        finally:
            with self._key_locks_lock:
                entry[1] -= 1
                if not entry[1]:
                    del self._key_locks[key]

    def _ask(self, question: Mapping[str, Any], key: str) -> dict[str, Any] | None:
        kind = str(question.get("kind", "type"))
        qset = self.question_set(kind)
        held = self.store.get(key)
        # A final record is reused; a failed one answers until its pause ends (jev-contract #key-reuse, #retry-before).
        if held and (held.get("outcome") not in REPLACEABLE_OUTCOMES or pause_left(held)):
            return held
        # A refused key pauses every ask, of any question: nothing is called or written (#retry-refused).
        if self.pause.holds():
            return held
        # Provenance (#record-inputs, #record-pairing, #record-verifier)
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        provider = self.provider()
        # Escalated fast-path: draft checks go to the verifier (#verifier); others to the general LLM.
        if held and provider is not None and (
                held.get("outcome") == ESCALATED or held.get("escalated")):
            if qset.show_cutoff is not None:
                return self._verify(question, key, qset, provider)
            elif qset.fallback:
                return self._general(question, key, qset, provider)
        criteria = qset.criteria_payload()
        dynamic = question.get("criteria")
        if isinstance(dynamic, Mapping):
            criteria = {str(k): str(v) for k, v in dynamic.items()}
        examples = qset.examples()
        instructions = {"question": qset.instructions, "examples": examples} if isinstance(dynamic, Mapping) and examples else qset.instructions
        payload = {"model": MODEL, "questions": {qset.id: {"criteria": criteria,
                    "instructions": instructions, "type": "choice"}},
                   "state": _jsonable(question.get("state", {}))}
        # Size estimate: UTF-8 bytes of canonical JSON / 2, rounded up (#request-oversize).
        if (len(_canonical(payload).encode("utf-8")) + 1) // 2 > self.max_input_tokens:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "oversize",
                                inputs=inputs, pairing=pairing)
        if provider is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "off",
                                inputs=inputs, pairing=pairing)
        # A Jev call runs on a Jev worker (#ask-queue): an ask on an LLM run moves back to Jev's line for it.
        if _ON_LINE.name == "llm":
            raise _Move("jev")
        # The one provider call (#retry-refused): none while the pause holds; after it, the probe alone calls.
        probe = self.pause.take()
        if probe is None:
            return held
        answer = None
        failure = None
        until = None
        model = MODEL
        try:
            response = provider.decide(payload) if hasattr(provider, "decide") else provider(payload)
            label, confidence, probabilities, model = _parse_provider_answer(response, qset.id)
            answer = {"label": label, "probabilities": probabilities, "confidence": confidence}
            if isinstance(question.get("candidate_anchors"), Mapping):
                answer = _anchor_answer(answer, question["candidate_anchors"])
        except Exception as exc:
            failure = exc
            until = _wall() + retry_pause(exc)
        finally:
            self.pause.answered(probe, getattr(failure, "status", None), until)
        if answer is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "unavailable", model,
                                failure=failure, inputs=inputs, pairing=pairing, until=until)
        if qset.show_cutoff is not None:
            # Asymmetric threshold for draft-check questions (#asymmetric).
            p_yes = answer["probabilities"].get("yes")
            if p_yes is None:
                p_yes = answer.get("confidence", 0.0) if answer.get("label") == "yes" else 0.0
            if p_yes >= qset.show_cutoff:
                return self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, "shown", model,
                                    inputs=inputs, pairing=pairing)
            elif qset.verify_cutoff is not None and p_yes >= qset.verify_cutoff:
                self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, ESCALATED, model,
                             inputs=inputs, pairing=pairing)
                return self._verify(question, key, qset, provider)
            else:
                return self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, SILENT_NO, model,
                                    inputs=inputs, pairing=pairing)
        outcome = "shown" if answer["confidence"] is not None and answer["confidence"] >= qset.threshold else "unsure"
        if outcome == "unsure" and qset.fallback:
            self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, ESCALATED, model,
                         inputs=inputs, pairing=pairing)
            return self._general(question, key, qset, provider)
        return self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, outcome, model,
                            inputs=inputs, pairing=pairing)

    @staticmethod
    def strict_payload(model: str, system: Mapping[str, Any], properties: Mapping[str, Any],
                       state: Any) -> dict[str, Any]:
        """A general LLM call held to a strict schema, served only by providers that enforce it: the system and the
        user state as canonical JSON; the answer an object of exactly the properties, each required."""
        schema = {"type": "object", "properties": dict(properties), "required": list(properties),
                  "additionalProperties": False}
        return {"model": model,
                "messages": [{"role": "system", "content": _canonical(system)},
                             {"role": "user", "content": _canonical(state)}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
                "provider": {"require_parameters": True}}

    def _ask_labels(self, provider: Any, qset: QuestionSet, names: list[str],
                    state: Any) -> tuple[list[Any] | None, str, BaseException | None]:
        """One general LLM call asking one of the set's labels for each name, with the set's question, labels, and
        examples: one allowed label by construction (#q-fallback, #q-rule-llm). The general LLM always runs through
        the CLI (#rule-cli-only), the injected provider in tests; unconfigured llm_model is "" so the CLI picks its own
        default. The answers in name order, else None and the failure; and the model that answered."""
        labels = qset.criteria()
        system = {"question": qset.instructions, "labels": labels, "examples": qset.examples()}
        model = self.llm_model()
        choice = {"type": "string", "enum": list(labels)}
        payload = self.strict_payload(model, system, {name: choice for name in names}, state)
        try:
            response = self._complete(self._fallback_provider(provider), payload)
            model = str(response.get("model", "")) or model
            said = json.loads(response["choices"][0]["message"]["content"])
            return [said[name] for name in names], model, None
        except Exception as exc:
            return None, model, exc

    def _general(self, question: Mapping[str, Any], key: str, qset: QuestionSet, provider: Any) -> dict[str, Any]:
        """The same question to the box's general LLM, one call like Jev; its answer decides (project-rules #q-fallback)."""
        kind = str(question.get("kind", "type"))
        sources, revision = question.get("sources", []), question.get("revision")
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        said, model, failure = self._ask_labels(provider, qset, ["choice"], question.get("state", {}))
        label = said[0] if said else None
        if label is None:
            return self._record(key, kind, qset, sources, revision,
                                {"label": None, "probabilities": {}, "confidence": None}, "unavailable", model, True,
                                failure, inputs=inputs, pairing=pairing)
        return self._record(key, kind, qset, sources, revision,
                            {"label": label, "probabilities": {}, "confidence": None}, "shown", model, True,
                            inputs=inputs, pairing=pairing)

    def verifier_payload(self, question: Mapping[str, Any], qset: QuestionSet, model: str) -> dict[str, Any]:
        """Verifier payload (#verifier): both clauses with context; strict schema requiring quoted spans."""
        state = question.get("state", {})
        verify_input: dict[str, Any] = {"clause": state.get("after", ""), "target": state.get("target", "")}
        if state.get("context"):
            verify_input["clause_context"] = state["context"]
        if state.get("target_context"):
            verify_input["target_context"] = state["target_context"]
        system = {"question": qset.instructions, "examples": qset.examples(),
                  "verify": "If yes, quote the exact span from the clause and the target that conflict. If no, leave spans empty."}
        answers = ["yes", "no"]
        if qset.verify:  # a set's own verify levels replace yes and no (#verifier-role); sets without one are unchanged
            levels = qset.verify["levels"]
            system = {"question": qset.instructions, "labels": qset.criteria(), "examples": qset.examples(),
                      "verify": qset.verify["rule"], "levels": {k: str(v.get("description", "")) for k, v in levels.items()}}
            answers = list(levels)
        properties = {"answer": {"type": "string", "enum": answers},
                      "clause_span": {"type": "string"}, "target_span": {"type": "string"}}
        return self.strict_payload(model, system, properties, verify_input)

    def _verify(self, question: Mapping[str, Any], key: str, qset: QuestionSet, provider: Any) -> dict[str, Any]:
        """Draft-check verify band (#verifier): call general LLM with both clauses; show only if yes + quoted spans."""
        kind = str(question.get("kind", "type"))
        sources, revision = question.get("sources", []), question.get("revision")
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        fallback = self._fallback_provider(provider)
        model = self.llm_model()
        payload = self.verifier_payload(question, qset, model)
        label = None
        failure = None
        try:
            response = self._complete(fallback, payload)
            result = json.loads(response["choices"][0]["message"]["content"])
            said = result.get("answer")
            clause_span = (result.get("clause_span") or "").strip()
            target_span = (result.get("target_span") or "").strip()
            shows = qset.verify["levels"].get(said, {}).get("shows") if qset.verify else said
            label = "yes" if shows in ("yes", "partial") and clause_span and target_span else "no"
        except Exception as exc:
            failure = exc
        if label is None:
            return self._record(key, kind, qset, sources, revision,
                                {"label": None, "probabilities": {}, "confidence": None}, "unavailable", model, True,
                                failure, inputs=inputs, pairing=pairing)
        answer: dict[str, Any] = {"label": label, "probabilities": {}, "confidence": None}
        if label == "yes":
            answer["clause_span"] = clause_span
            answer["target_span"] = target_span
            if shows == "partial":
                answer["level"] = "partial"  # shows as a Partial overlap note (#verifier-record, #level-partial)
        # The verifier ran (#record-verifier): its answer and quoted spans, as it gave them.
        verifier = {"ran": True, "answer": said, "spans": {"clause": clause_span, "target": target_span}}
        return self._record(key, kind, qset, sources, revision, answer,
                            VERIFIED if label == "yes" else VERIFIER_REJECTED, model, True,
                            inputs=inputs, pairing=pairing, verifier=verifier)

    def check_rules(self, questions: list[Mapping[str, Any]]) -> list[dict[str, Any] | None]:
        """The rule check of one spec text (project-rules #q-rule-llm): one general LLM call, never a Jev ask, about
        every rule question whose key holds no record, or a failed one past its pause; its answer decides each rule,
        one record per rule. A failed call records each unavailable, asked again after the pause by the one retry
        rule (jev-contract #retry); while a refused key's pause holds nothing is called or written (#retry-refused).
        The call asks the spec's text and each rule by number. The records held, in question order."""
        qset = self.question_set("rule")
        keys = [self.key(question) for question in questions]
        asked: dict[str, Mapping[str, Any]] = {}
        for key, question in zip(keys, questions):
            held = self.store.get(key)
            if not held or (held.get("outcome") in REPLACEABLE_OUTCOMES and not pause_left(held)):
                asked.setdefault(key, question)
        if asked and not self.pause.holds():
            rules = {str(number): question["state"]["rule"] for number, question in enumerate(asked.values(), 1)}
            state = {"rules": rules, "spec": next(iter(asked.values()))["state"]["spec"]}
            said, model, failure = self._ask_labels(self._provider, qset, list(rules), state)
            labels: list[Any] = [None] * len(asked) if said is None else [str(label) for label in said]
            for (key, question), label in zip(asked.items(), labels):
                self._record(key, "rule", qset, question.get("sources", []), question.get("revision"),
                             {"label": label, "probabilities": {}, "confidence": None},
                             "unavailable" if label is None else "shown", model, True, failure)
        return [self.store.get(key) for key in keys]

    def ask_chain(self, chain: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Ask a chain's yes/no questions in order, each only when the answer so far needs it (#chains)."""
        return chain_result(chain, self.ask)

    def answer(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """One item's answer: its chain's, else its one question's record."""
        return self.ask_chain(item["chain"]) if "chain" in item else self.ask(item)


def _chain_label(record: Mapping[str, Any]) -> str | None:
    """The label the chain walks on: a silent-no is a no for chain purposes (#asymmetric); a partial verifier
    level walks as partial, which only the overlaps step maps (#corpus-labels)."""
    if record.get("outcome") == SILENT_NO:
        return "no"
    answer = record.get("answer")
    return answer.get("level") or answer.get("label") if isinstance(answer, Mapping) else None


def chain_result(chain: list[Mapping[str, Any]], get: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]) -> dict[str, Any]:
    """Walk a chain (#chains): each step's then maps a yes or no answer to the chain's label, None for nothing;
    an answer it does not map asks the next step. get gives a step's record, None when not held: the walk stops
    there with outcome None and that step. The result reads as one record; unsure counts the answers Jev was
    unsure of that the general LLM settled (#markers-agent-unsure)."""
    unsure = 0
    for step in chain:
        record = get(step)
        if record is None:
            return {"outcome": None, "answer": {"label": None}, "record_id": None, "unsure": unsure, "step": step}
        outcome = record.get("outcome")
        if outcome not in CHAIN_CONTINUE:
            return {"outcome": outcome, "answer": {"label": None}, "record_id": record.get("record_id"),
                    "unsure": unsure, "step": step}
        unsure += bool(record.get("escalated"))
        label = _chain_label(record)
        then = step["then"]
        if label not in YES_NO or label in then:
            return {"outcome": "shown", "answer": {"label": then.get(label)}, "record_id": record.get("record_id"),
                    "unsure": unsure}
    raise ValueError("a chain's last step decides both answers")


def lane_result(steps: list[Mapping[str, Any]], get: Callable[[Mapping[str, Any]], Mapping[str, Any] | None]) -> list[dict[str, Any]]:
    """Walk a lane check (#cross-lane-question, #gate-lane): about? gate first; if yes, contradicts? once;
    if no, both oversteps? directions, each asked regardless of the other; each yes is its own finding;
    overlaps? only when all three are no. Returns a list of results, each shaped like chain_result's output."""
    unsure = 0
    # Gate (step 0): about? yes continues, no/unsure ends with nothing (#gate-lane)
    record = get(steps[0])
    if record is None:
        return [{"outcome": None, "answer": {"label": None}, "record_id": None, "unsure": 0, "step": steps[0]}]
    outcome = record.get("outcome")
    if outcome not in CHAIN_CONTINUE:
        return [{"outcome": outcome, "answer": {"label": None}, "record_id": record.get("record_id"),
                 "unsure": 0, "step": steps[0]}]
    unsure += bool(record.get("escalated"))
    label = _chain_label(record)
    if label != "yes":
        return [{"outcome": "shown", "answer": {"label": None},
                 "record_id": record.get("record_id"), "unsure": unsure}]
    # contradicts? (step 1)
    record = get(steps[1])
    if record is None:
        return [{"outcome": None, "answer": {"label": None}, "record_id": None, "unsure": unsure, "step": steps[1]}]
    outcome = record.get("outcome")
    if outcome not in CHAIN_CONTINUE:
        return [{"outcome": outcome, "answer": {"label": None}, "record_id": record.get("record_id"),
                 "unsure": unsure, "step": steps[1]}]
    unsure += bool(record.get("escalated"))
    label = _chain_label(record)
    if label == "yes":
        return [{"outcome": "shown", "answer": {"label": steps[1]["then"]["yes"]},
                 "record_id": record.get("record_id"), "unsure": unsure}]
    # Both oversteps directions (steps 2, 3): each asked regardless of the other
    findings: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for step in steps[2:4]:
        record = get(step)
        if record is None:
            pending = pending or {"outcome": None, "answer": {"label": None}, "record_id": None,
                                  "unsure": unsure, "step": step}
            continue
        outcome = record.get("outcome")
        if outcome not in CHAIN_CONTINUE:
            pending = pending or {"outcome": outcome, "answer": {"label": None},
                                  "record_id": record.get("record_id"), "unsure": unsure, "step": step}
            continue
        unsure += bool(record.get("escalated"))
        lbl = _chain_label(record)
        if lbl == "yes":
            findings.append({"outcome": "shown", "answer": {"label": step["then"]["yes"]},
                             "record_id": record.get("record_id"), "unsure": unsure})
    if findings:
        if pending:
            findings.append(pending)
        return findings
    if pending:
        return [pending]
    # overlaps? (step 4): only when all three are no
    record = get(steps[4])
    if record is None:
        return [{"outcome": None, "answer": {"label": None}, "record_id": None, "unsure": unsure, "step": steps[4]}]
    outcome = record.get("outcome")
    if outcome not in CHAIN_CONTINUE:
        return [{"outcome": outcome, "answer": {"label": None}, "record_id": record.get("record_id"),
                 "unsure": unsure, "step": steps[4]}]
    unsure += bool(record.get("escalated"))
    lbl = _chain_label(record)
    then = steps[4]["then"]
    if lbl in then:
        return [{"outcome": "shown", "answer": {"label": then[lbl]},
                 "record_id": record.get("record_id"), "unsure": unsure}]
    raise ValueError("a lane check's last step decides both answers")


def _step(question: dict[str, Any], **then: str | None) -> dict[str, Any]:
    """A chain step: its question, and the label each answer ends the chain with; an answer left out asks on."""
    question["then"] = then
    return question


def _chain(kind: str, identifier: str, target: str | None, sources: list[str], revision: Any,
           steps: list[dict[str, Any]]) -> dict[str, Any]:
    """One suggestion asked as a chain of yes/no questions; its labels are what it can show."""
    labels = [label for step in steps for label in step["then"].values() if label]
    return {"kind": kind, "id": identifier, "target": target, "sources": sources, "revision": revision,
            "chain": steps, "display_labels": list(dict.fromkeys(labels))}


# Shaped sections whose clauses are for you by structure, never asked (spec #reading-structural).
AUDIENCE_STRUCTURAL_SECTIONS = frozenset({"user-stories", "modular-boundaries"})
# Page header clauses and source issue sections: never asked nor compared (spec #corpus-meta).
CORPUS_META_ANCHORS = frozenset({"source-issues"})
CONTAINER_TAGS = frozenset({"article", "div", "figure", "footer", "header", "main", "nav", "ol", "section", "table", "tbody", "thead", "tfoot", "ul"})
# Leaf exclusion tags and id patterns (spec #leaf-exclusions).
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_LEAF_EXAMPLE_ID_PARTS = ("mock", "preview", "example")
_LEAF_AUDIT_ID_PARTS = ("source-issues", "audit", "history")


class _AnchorParser(HTMLParser):
    """Collect each anchor's text, tag, tree links, and flags in one pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, bool]] = []
        self.values: dict[str, dict[str, Any]] = {}
        # Context tracking (jev-suggestions #context): title, section headings, table columns.
        self.title = ""
        self._title_parts: list[str] = []
        self._in_h1 = False
        self._heading_for: str | None = None
        self._heading_parts: list[str] = []
        self._in_thead: str | None = None
        self._thead_columns: list[str] = []
        self._column_parts: list[str] = []
        self._in_column = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        _close_implied(self.stack, tag)
        if tag in BLOCK_TEXT_SEPARATORS and self.stack:
            self.handle_data(" ")
        attrs = dict(attrs)
        structural = (bool(self.stack) and self.stack[-1][2]) or attrs.get("data-spec-section") in AUDIENCE_STRUCTURAL_SECTIONS
        anchor = attrs.get("data-anchor")
        # h1 title capture (#context-surface)
        if tag == "h1" and not self.title and not self._in_h1:
            self._in_h1 = True
            self._title_parts = []
        # Section heading capture (#context-section): first h2/h3 inside a section anchor
        if tag in ("h2", "h3") and not self._heading_for:
            for item in reversed(self.stack):
                if item[1] and item[1] in self.values and self.values[item[1]].get("section") and not self.values[item[1]].get("heading"):
                    self._heading_for = item[1]
                    self._heading_parts = []
                    break
        # thead column capture (#context-columns)
        if tag == "thead" and self._in_thead is None:
            for item in reversed(self.stack):
                if item[1] and item[1] in self.values and self.values[item[1]]["tag"] == "table":
                    self._in_thead = item[1]
                    self._thead_columns = []
                    break
        if self._in_thead is not None and tag in ("th", "td") and not self._in_column:
            self._in_column = True
            self._column_parts = []
        if anchor:
            parent = next((item[1] for item in reversed(self.stack) if item[1]), None)
            meta = (tag == "header" or anchor in CORPUS_META_ANCHORS or anchor.endswith("-source-issues") or any(item[0] == "header" for item in self.stack)
                    or bool(parent and self.values[parent]["meta"]))
            # Leaf exclusion flags (spec #leaf-exclusions).
            excluded = (
                tag in _HEADING_TAGS
                or tag in {"figure", "figcaption"} or any(item[0] == "figure" for item in self.stack)
                or tag in {"pre", "code"} or any(part in anchor for part in _LEAF_EXAMPLE_ID_PARTS)
                or tag == "caption"
                or any(part in anchor for part in _LEAF_AUDIT_ID_PARTS)
                or any(item[1] is not None and any(part in item[1] for part in _LEAF_AUDIT_ID_PARTS) for item in self.stack)
            )
            value = self.values.setdefault(anchor, {
                "text": [],
                "tag": tag,
                "section": tag == "section" or attrs.get("data-spec-section") is not None,
                "spec_section": attrs.get("data-spec-section"),
                "story": "data-user-story" in attrs,
                "criterion": "data-acceptance-criterion" in attrs,
                # A project rule's declaration (project-rules #declare): its mark's name, on a criterion only.
                "rule": " ".join(str(attrs.get("data-project-rule") or "").split()) or None
                if "data-acceptance-criterion" in attrs else None,
                # A criterion's data-story (jev-suggestions #coverage-declared): the stories it names, None when absent.
                "stories": str(attrs.get("data-story") or "").split()
                if "data-acceptance-criterion" in attrs and "data-story" in attrs else None,
                "parent": parent,
                "children": [],
                "structural": False,
                "meta": meta,
                "excluded": excluded,
                "links": [],
            })
            value["structural"] = value["structural"] or structural
            if parent and anchor not in self.values[parent]["children"]:
                self.values[parent]["children"].append(anchor)
            self.stack.append((tag, anchor, structural))
        elif tag not in VOID_ELEMENTS:
            self.stack.append((tag, self.stack[-1][1] if self.stack else None, structural))
        elif tag == "br" and self.stack:
            self.handle_data(" ")
        if tag == "a" and attrs.get("href"):
            for anchor in {item[1] for item in self.stack if item[1]}:
                if attrs["href"] not in self.values[anchor]["links"]:
                    self.values[anchor]["links"].append(attrs["href"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        tag_lower = tag.lower()
        if tag_lower == "h1" and self._in_h1:
            self._in_h1 = False
            self.title = " ".join("".join(self._title_parts).split())
        if tag_lower in ("h2", "h3") and self._heading_for:
            self.values[self._heading_for]["heading"] = " ".join("".join(self._heading_parts).split())
            self._heading_for = None
        if self._in_column and tag_lower in ("th", "td"):
            self._thead_columns.append(" ".join("".join(self._column_parts).split()))
            self._in_column = False
        if tag_lower == "thead" and self._in_thead is not None:
            if self._thead_columns:
                self.values[self._in_thead]["columns"] = list(self._thead_columns)
            self._in_thead = None
        _close_explicit(self.stack, tag)

    def handle_data(self, data: str):
        if self._in_h1:
            self._title_parts.append(data)
        if self._heading_for:
            self._heading_parts.append(data)
        if self._in_column:
            self._column_parts.append(data)
        if any(item[0] in {"script", "style"} for item in self.stack):
            return
        seen = set()
        for item in self.stack:
            anchor = item[1]
            if anchor and anchor not in seen:
                self.values[anchor]["text"].append(data)
                seen.add(anchor)


def extract_anchors(source: str | bytes | None) -> dict[str, dict[str, Any]]:
    parser = _AnchorParser()
    parser.feed((source or b"").decode("utf-8", "replace") if isinstance(source, bytes) else (source or ""))
    title = parser.title
    for value in parser.values.values():
        value["text"] = " ".join("".join(value["text"]).split())
        value["title"] = title
    return parser.values


def anchor_context(anchor: str, anchors: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build clause context for an anchor (#context): surface, section, columns."""
    value = anchors.get(anchor, {})
    ctx: dict[str, Any] = {}
    title = value.get("title", "")
    if title:
        ctx["surface"] = title
    # Walk parents to find nearest section with a heading (#context-section)
    current = anchor
    while current:
        info = anchors.get(current, {})
        if info.get("section") and info.get("heading"):
            ctx["section"] = info["heading"]
            break
        current = info.get("parent")
    # Column headers: walk parents to find a table with columns (#context-columns)
    tag = value.get("tag", "")
    in_table = tag in ("tr", "td", "th")
    current = anchor if not in_table else None
    while current:
        info = anchors.get(current, {})
        if info.get("tag") == "table":
            in_table = True
            break
        current = info.get("parent")
    if in_table:
        current = anchor
        while current:
            info = anchors.get(current, {})
            if info.get("columns"):
                ctx["columns"] = info["columns"]
                break
            if info.get("tag") == "table" and info.get("columns"):
                ctx["columns"] = info["columns"]
                break
            current = info.get("parent")
    return ctx


def _anchor_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _corpus_leaf_anchors(anchors: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [key for key, value in anchors.items() if not value.get("children") and not value.get("meta") and not value.get("excluded")]


def _match_score(source: str, target: str) -> tuple[int, int]:
    source_words, target_words = _anchor_words(source), _anchor_words(target)
    overlap = len(source_words & target_words)
    union = len(source_words | target_words)
    return overlap, int((overlap * 1000) / union) if union else 0


def _doc_freqs(clause_texts: list[str]) -> dict[str, int]:
    """Count of clauses containing each word, across the spec collection (#candidate-ranking)."""
    freqs: dict[str, int] = {}
    for text in clause_texts:
        for word in _anchor_words(text):
            freqs[word] = freqs.get(word, 0) + 1
    return freqs


def _rarity_weight(word: str, freqs: Mapping[str, int]) -> float:
    """A shared word's part of the rarity score: 1/log(1+doc_freq) (#candidate-ranking)."""
    return 1.0 / math.log(1 + freqs.get(word, 1))


def _rarity_score(source_words: set[str], target_words: set[str], freqs: dict[str, int]) -> float:
    """Rarity-weighted overlap: the exact sum of each shared word's rarity weight (#candidate-ranking), the same
    whatever the set order, so ties break by path and key alike after every restart (#candidate-ranking-lane)."""
    shared = source_words & target_words
    if not shared:
        return 0.0
    return math.fsum(_rarity_weight(word, freqs) for word in shared)


def _candidate_sort(source: str, anchors: Mapping[str, Mapping[str, Any]], keys: list[str]) -> list[str]:
    return sorted(keys, key=lambda key: (-_match_score(source, str(anchors[key].get("text", "")))[0],
                                         -_match_score(source, str(anchors[key].get("text", "")))[1], key))


def _served_spec_parts(value: Any) -> tuple[str, str] | None:
    if isinstance(value, Mapping):
        path = value.get("path", value.get("relative"))
        source = value.get("source", value.get("text", value.get("html")))
    elif isinstance(value, (tuple, list)) and len(value) >= 2:
        path, source = value[0], value[1]
    else:
        return None
    if not isinstance(path, str) or not path or source is None:
        return None
    if isinstance(source, bytes):
        source = source.decode("utf-8", "replace")
    if not isinstance(source, str):
        return None
    return path, source


def mount_prefix(mount: Mapping[str, Any]) -> str:
    """The URL path prefix a mount serves its files under, without the leading slash."""
    if mount.get("path"):
        return mount["path"][:-len(mount["spec"])]
    return (mount["slug"] + "/") if mount.get("slug") else ""


def served_path(mount: Mapping[str, Any], target: str) -> str:
    """A repo path, with any #anchor, as the page's mount serves it: a slug serves its repo paths under its prefix,
    the single root its narrow root's (project-rules #bootstrap-offer links and records)."""
    path, hash_, anchor = str(target).partition("#")
    if mount.get("slug"):
        path = mount_prefix(mount) + path
    elif mount.get("root") and mount.get("narrow_root"):
        path = os.path.relpath(os.path.join(str(mount["root"]), path), str(mount["narrow_root"])).replace(os.sep, "/")
    return path + hash_ + anchor


def lane_rows(mounts: Any, page: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The rows of the page's lane: its project's rows at its slug, whatever prefix the host gave each one's path."""
    project = page.get("project") or ""
    return [mount for mount in mounts if isinstance(mount, Mapping) and mount.get("slug") == page.get("slug")
            and (mount.get("project") or "") == project]


# Folders the review index never walks into, by name in any case.
UNWALKED = frozenset({"evidence", "evidence-bundle", "evidence-bundles", "fixture", "fixtures", "support", "supports"})


def walked_folder(name: str) -> bool:
    """A folder the review index walks into: not hidden, no review spool, none of UNWALKED."""
    return not name.startswith(".") and not name.endswith(".review") and name.lower() not in UNWALKED


def walked_spec(name: str) -> bool:
    """A file the review index lists: a spec, not hidden."""
    return not name.startswith(".") and name.endswith(".spec.html")


def enumerate_served_specs(mounts: Any) -> list[tuple[str, str]]:
    """Enumerate the same spec files exposed by the review index."""
    if isinstance(mounts, Mapping):
        mounts = [mounts]
    result = []
    for mount in mounts or []:
        if not isinstance(mount, Mapping):
            continue
        if mount.get("spec"):
            filename = mount.get("spec_file") or os.path.join(str(mount.get("root", "")), str(mount["spec"]))
            result.append((str(mount["spec"]), str(filename)))
            continue
        narrow_root = mount.get("narrow_root")
        if not narrow_root:
            continue
        for directory, directories, names in os.walk(narrow_root, followlinks=False):
            directories[:] = sorted(name for name in directories if walked_folder(name))
            for name in sorted(names):
                if not walked_spec(name):
                    continue
                path = os.path.join(directory, name)
                try:
                    inside = os.path.commonpath((os.path.realpath(path), os.path.realpath(narrow_root))) == os.path.realpath(narrow_root)
                except ValueError:
                    inside = False
                if not inside or not os.path.isfile(path):
                    continue
                result.append((os.path.relpath(path, narrow_root).replace(os.sep, "/"), path))
    return result


def _is_non_goal(anchor: str, anchors: Mapping[str, Mapping[str, Any]]) -> bool:
    seen = set()
    current = anchor
    while current and current not in seen:
        seen.add(current)
        if "non-goal" in current.lower() or "nongoal" in current.lower():
            return True
        current = anchors.get(current, {}).get("parent")
    return False


TOPK_CROSS = 5
# lane_shortlist: far more than sums of the same rarity weights taken in another order differ by.
_SUM_SLACK = 1e-9
# lane_shortlist: reading stops when scoring whole the texts that could still rank costs less than reading the next
# word, a score weighed as _SCORE_COST postings, counted only when the next word's postings are at least a
# _COUNT_COST-th of the texts met. Tuned for speed; neither changes the ranking.
_SCORE_COST = 2
_COUNT_COST = 6


def build_corpus_questions(current: str | bytes, baseline: str | bytes | None, path: str = "spec", base: str = "",
                           revision: Any = "head", served_specs: Any = None) -> list[dict[str, Any]]:
    """Build one corpus question for each changed leaf and cross-spec candidate clause; no clause of its own
    spec is a candidate (#corpus-same-spec)."""
    now, old = extract_anchors(current), extract_anchors(baseline)
    leaves = _corpus_leaf_anchors(now)
    other_specs: list[tuple[str, dict[str, dict[str, Any]]]] = []
    for raw in served_specs or []:
        part = _served_spec_parts(raw)
        if not part:
            continue
        other_path, source = part
        if other_path == path:
            continue
        other_specs.append((other_path, extract_anchors(source)))

    # Collect all clause texts for document frequency (#candidate-ranking).
    all_texts: list[str] = [str(now[k].get("text", "")) for k in leaves]
    for _, other_anchors in other_specs:
        all_texts.extend(str(other_anchors[k].get("text", "")) for k in _corpus_leaf_anchors(other_anchors))
    freqs = _doc_freqs(all_texts)

    result = []
    for anchor in leaves:
        after = str(now[anchor].get("text", ""))
        before = str(old.get(anchor, {}).get("text", ""))
        if before == after:
            continue

        source_words = _anchor_words(after)
        # Rank cross-spec candidates by rarity-weighted overlap.
        cross: list[tuple[float, str, str, str, bool, dict]] = []
        for other_path, other_anchors in other_specs:
            for key in _corpus_leaf_anchors(other_anchors):
                text = str(other_anchors[key].get("text", ""))
                score = _rarity_score(source_words, _anchor_words(text), freqs)
                cross.append((-score, other_path, key, text, _is_non_goal(key, other_anchors), other_anchors))
        cross.sort(key=lambda item: (item[0], item[1], item[2]))

        ctx = anchor_context(anchor, now)
        for rank_index, (_, other_path, key, target_text, non_goal, target_anchors) in enumerate(cross[:TOPK_CROSS]):
            pair_info = {"rule": "non-goal" if non_goal else "cross-spec", "rank": rank_index, "gate": "yes"}
            result.append(draft_check(anchor, before, after, other_path + "#" + key, target_text,
                                      target_non_goal=non_goal, path=path, base=base, revision=revision,
                                      context=ctx, target_context=anchor_context(key, target_anchors),
                                      pairing=pair_info))
    return result


def draft_check(anchor: str, before: str, after: str, target: str, target_text: str, *,
                target_non_goal: bool = False, path: str, base: str, revision: Any,
                context: Mapping[str, Any] | None = None, target_context: Mapping[str, Any] | None = None,
                pairing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The draft-check chain for one clause and cross-spec candidate (#corpus-labels, #gate-chain): about? gate
    first, then contradicts?, then oversteps?, then overlaps?, the first yes decides; a non-goal candidate is
    asked only contradicts? after the gate."""
    # Gate state: the two clauses, each with its context fields, in an order fixed by their content, never by which
    # side asks, so the same two clauses asked from any spec or slug share one record (#gate-set-state).
    first_obj: dict[str, Any] = {"text": after}
    if context:
        first_obj.update(context)
    second_obj: dict[str, Any] = {"text": target_text}
    if target_context:
        second_obj.update(target_context)
    first_obj, second_obj = sorted((first_obj, second_obj), key=_canonical)
    gate = _step(_question("about", anchor, {"first": first_obj, "second": second_obj},
                           path, base, revision, target), no=None)
    pair: dict[str, Any] = {"before": before, "after": after, "target": target_text, "target_non_goal": target_non_goal}
    if context:
        pair["context"] = dict(context)
    if target_context:
        pair["target_context"] = dict(target_context)
    # Non-goal candidates use their own question set (#qset-examples), not a conditional instruction.
    contradicts_kind = "contradicts-nongoal" if target_non_goal else "contradicts"
    steps = [gate,
             _step(_question(contradicts_kind, anchor, pair,
                             path, base, revision, target), yes="contradicts")]
    if target_non_goal:
        steps[1]["then"]["no"] = None
    else:
        steps += [_step(_question("oversteps", anchor, pair, path, base, revision, target), yes="oversteps"),
                  _step(_question("overlaps", anchor, pair, path, base, revision, target), yes="overlaps", partial="partial-overlap", no=None)]
    sources = [path + "#" + anchor, target]
    for step in steps:
        step["sources"] = sources
    # Attach pairing to post-gate steps (#record-pairing)
    if pairing:
        for step in steps[1:]:
            step["pairing"] = dict(pairing)
    return _chain("corpus", anchor, target, sources, {"base": base, "head": revision}, steps)


def _question(kind: str, identifier: str, state: Mapping[str, Any], path: str, base: str, revision: Any,
              target: str | None = None, criteria: Mapping[str, str] | None = None) -> dict[str, Any]:
    question = {"kind": kind, "id": identifier, "state": dict(state),
                "sources": [f"{path}#{identifier}"], "revision": {"base": base, "head": revision}}
    if target is not None:
        question["target"] = target
    if criteria is not None:
        question["criteria"] = dict(criteria)
    return question


def build_type_questions(current: str | bytes, baseline: str | bytes | None, path: str = "spec", base: str = "",
                         revision: Any = "head") -> list[dict[str, Any]]:
    now, old = extract_anchors(current), extract_anchors(baseline)
    roots = [key for key, value in now.items() if value.get("section")]
    if not roots:
        roots = list(now)
    result = []
    for anchor in roots:
        before, after = old.get(anchor, {}).get("text", ""), now[anchor]["text"]
        if before == after:
            continue
        # Does it change behavior? (#change-type): yes is Behavior, no is No behavior change.
        state: dict[str, Any] = {"before": before, "after": after}
        ctx = anchor_context(anchor, now)
        if ctx:
            state["context"] = ctx
        step = _step(_question("type", anchor, state, path, base, revision, anchor),
                     yes="behavior", no="no-behavior-change")
        result.append(_chain("type", anchor, anchor, step["sources"], step["revision"], [step]))
    return result


def _human_threads(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The service fold (review-state #model-fold-one) as Jev reads it: message bodies, and the
    anchor of the newest human message naming one."""
    result = []
    for thread in spool.fold_threads(events).values():
        messages = [message["body"] for message in thread["messages"]]
        anchor = thread["ev"]["body"].get("anchorId")
        for message in thread["messages"]:
            if message["actor"] == "human":
                anchor = message["body"].get("anchorId", anchor)
        result.append({"id": thread["id"], "status": thread["status"], "messages": messages, "anchor": anchor})
    return result


def build_orphan_questions(events: list[Mapping[str, Any]], current: str | bytes, path: str = "spec", base: str = "",
                           revision: Any = "head") -> list[dict[str, Any]]:
    anchors = extract_anchors(current)
    words = {key: set(re.findall(r"[a-z0-9]{3,}", value["text"].lower())) for key, value in anchors.items()}
    result = []
    for thread in _human_threads(events):
        anchor = thread.get("anchor")
        if not anchor or anchor in anchors or thread.get("status") == "resolved":
            continue
        root = thread["messages"][0]
        quote = str(root.get("quote") or root.get("text") or "")
        wanted = set(re.findall(r"[a-z0-9]{3,}", quote.lower()))
        candidates = [item for item in sorted(anchors, key=lambda item: (-len(wanted & words[item]), item))
                      if len(wanted & words[item]) >= 2][:8]
        if not candidates:
            continue
        criteria = {"none": "No current section is a credible location for the orphaned passage."}
        candidate_anchors: dict[str, str] = {}
        used_labels = {"none"}
        for item in candidates:
            text = " ".join(str(anchors[item].get("text", "")).split())
            label = text[:80].rsplit(" ", 1)[0] if len(text) > 80 else text
            label = label.rstrip(" .,;:") or "Current section"
            if len(text) > 80:
                label += "..."
            base_label = label
            suffix = 2
            while label in used_labels:
                label = f"{base_label} ({suffix})"
                suffix += 1
            used_labels.add(label)
            candidate_anchors[label] = item
            criteria[label] = text
        state = {"quote": quote, "candidates": list(candidate_anchors)}
        question = _question("orphan", str(thread["id"]), state, path, base, revision, criteria=criteria)
        question["candidate_anchors"] = candidate_anchors
        result.append(question)
    return result


def build_resolved_questions(events: list[Mapping[str, Any]], current: str | bytes, baseline: str | bytes | None,
                             path: str = "spec", base: str = "", revision: Any = "head",
                             message_sources: Mapping[str, str | bytes] | None = None) -> list[dict[str, Any]]:
    now, old = extract_anchors(current), extract_anchors(baseline)
    result = []
    for thread in _human_threads(events):
        anchor = thread.get("anchor")
        if thread.get("status") == "resolved" or anchor not in now:
            continue
        human = [m for m in thread["messages"] if m.get("actor") == "human"]
        if not human:
            continue
        newest = human[-1]
        historical = message_sources.get(str(newest.get("id"))) if message_sources else None
        prior = extract_anchors(historical) if historical else old
        before = prior.get(anchor, {}).get("text", "")
        if before == now[anchor]["text"]:
            continue
        text = "\n".join(str(m.get("text") or m.get("quote") or "") for m in human)
        result.append(_question("resolved", str(thread["id"]),
                                {"comment": text, "before": before, "after": now[anchor]["text"]},
                                path, base, revision, anchor))
    return result


def build_coverage_questions(current: str | bytes, path: str = "spec", base: str = "",
                             revision: Any = "head") -> list[dict[str, Any]]:
    """Coverage as the spec declares it (#coverage-declared): each criterion asked once per story its data-story
    names, never another. A story no criterion names and a criterion that names no story of the spec stand alone,
    a gap with nothing to ask. A spec whose criteria declare no data-story asks nothing."""
    anchors = extract_anchors(current)
    criteria = [(anchor, value) for anchor, value in anchors.items() if value.get("criterion")]
    if all(value.get("stories") is None for _, value in criteria):
        return []

    def question(story_anchor: str | None, criterion_anchor: str | None) -> dict[str, Any]:
        sides = {"story": story_anchor, "criterion": criterion_anchor}
        return {
            "kind": "coverage",
            "id": (story_anchor or "") + "::" + (criterion_anchor or ""),
            "state": {side: {"anchor": anchor, "text": anchors[anchor]["text"]} if anchor else None
                      for side, anchor in sides.items()},
            "sources": [f"{path}#{anchor}" for anchor in sides.values() if anchor],
            "revision": {"base": base, "head": revision},
            "target": story_anchor or criterion_anchor,
            "story": story_anchor,
            "criterion": criterion_anchor,
        }

    named: dict[str, list[str]] = {anchor: [] for anchor, value in anchors.items() if value.get("story")}
    lone = []
    for criterion_anchor, criterion in criteria:
        names = [name for name in dict.fromkeys(criterion.get("stories") or ()) if name in named]
        for name in names:
            named[name].append(criterion_anchor)
        if not names:
            lone.append(question(None, criterion_anchor))
    return [question(story_anchor, criterion_anchor)
            for story_anchor, names in named.items() for criterion_anchor in names or [None]] + lone


def _audience_leaf_anchors(source: str | bytes | None) -> dict[str, dict[str, Any]]:
    result = {}
    for anchor, value in extract_anchors(source).items():
        if value["children"] or value["tag"] in CONTAINER_TAGS or value["structural"]:
            continue
        if value["text"]:
            result[anchor] = {"text": value["text"], "tag": value["tag"]}
    return result


def build_audience_questions(current: str | bytes, path: str = "spec", base: str = "",
                             revision: Any = "head") -> list[dict[str, Any]]:
    result = []
    for anchor, value in _audience_leaf_anchors(current).items():
        result.append(_question("audience", anchor,
                                {"clause": value["text"]},
                                path, base, revision, anchor))
    return result


RULE_SCOPE = "every feature"
RULE_MISSED = "missed"
# A candidate rule's item label (project-rules #mark-candidate), a row of MARK_LEVELS.
RULE_CANDIDATE = "candidate"
SPEC_SUFFIX = ".spec.html"


def rule_word(path: str) -> str:
    """The mark word: the home spec's file name without .spec.html, fixed, never generated (#mark-word)."""
    name = path.rsplit("/", 1)[-1]
    return name[:-len(SPEC_SUFFIX)] if name.endswith(SPEC_SUFFIX) else name


def spec_text(source: str | bytes | None) -> str:
    """A spec's readable text: its top-level anchors, header and status excluded."""
    anchors = extract_anchors(source)
    return "\n".join(value["text"] for value in anchors.values()
                     if value.get("parent") is None and not value.get("meta") and value["text"])


def rule_mark_anchor(source: str | bytes | None) -> str | None:
    """Where a rule note shows: the Acceptance criteria section, else the first section (#marks)."""
    anchors = extract_anchors(source)
    for key, value in anchors.items():
        if value.get("spec_section") == "acceptance":
            return key
    return next((key for key, value in anchors.items() if value.get("section") and not value.get("meta")), None)


def build_scope_questions(served_specs: Any) -> list[dict[str, Any]]:
    """One scope question per acceptance criterion of each served spec, its key the criterion text alone (#q-scope).
    name is the criterion's project-rule mark, None when unmarked (#declare); clause names it by repo path, which
    one project's lanes share whatever slug serves it, as the warning ledger and a rule's home name it; served, whether
    the page's lane serves the spec, as its entry says, True when it says nothing, as of a served spec
    (JevService._rule_source)."""
    result = []
    for raw in served_specs or []:
        part = _served_spec_parts(raw)
        if not part:
            continue
        path, source = part
        repo = str(raw.get("spec") or path) if isinstance(raw, Mapping) else path
        for anchor, value in extract_anchors(source).items():
            if not value.get("criterion") or not value["text"]:
                continue
            question = _question("scope", anchor, {"criterion": value["text"]}, path, "", None, path + "#" + anchor)
            question["revision"] = None
            question["word"] = rule_word(path)
            question["clause"] = repo + "#" + anchor
            question["name"] = value.get("rule")
            question["served"] = bool(raw.get("served", True)) if isinstance(raw, Mapping) else True
            result.append(question)
    return result


def build_rule_questions(scopes: list[Mapping[str, Any]], current: str | bytes, path: str, base: str,
                         revision: Any, mark: str) -> list[dict[str, Any]]:
    """One rule check question per marked rule, in order: does this spec trigger the rule and, if so, cover it?
    Each holds the rule's text and the spec's text, its key those and the set version (#q-rule); the spec's one
    general LLM call asks them together (JevSeam.check_rules)."""
    text = spec_text(current)
    result = []
    for scope in scopes:
        question = _question("rule", mark, {"rule": scope["state"]["criterion"], "spec": text}, path, base, revision,
                             scope["target"])
        question["sources"] = [path, scope["target"]]
        question["word"] = scope["word"]
        result.append(question)
    return result


BUILDERS = {"type": build_type_questions, "orphan": build_orphan_questions, "resolved": build_resolved_questions,
            "coverage": build_coverage_questions, "audience": build_audience_questions,
            "corpus": build_corpus_questions, "scope": build_scope_questions, "rule": build_rule_questions,
            "mark": rule_mark_anchor}


# Board route (worklane-provider #jev-board): change type answers that quiet the review highlight.
MATERIAL_NO = "no-behavior-change"
MATERIAL_YES = "behavior"


def changed_leaf_clauses(current: str | bytes, baseline: str | bytes | None) -> list[tuple[str, str, str, dict[str, Any]]]:
    """(anchor, before, after, context) for each leaf clause whose text differs from the baseline; context is
    the clause's in the current text (#context), built in the same parse."""
    now, old = extract_anchors(current), extract_anchors(baseline)
    result = []
    for anchor in _corpus_leaf_anchors(now):
        before, after = str(old.get(anchor, {}).get("text", "")), str(now[anchor].get("text", ""))
        if before != after:
            result.append((anchor, before, after, anchor_context(anchor, now)))
    return result


def lane_shortlist(clauses: Mapping[str, list[Mapping[str, Any]]]) -> dict[tuple[str, str], list[tuple[int, int]]]:
    """The cross-lane pairs of one project's changed clauses (#cross-lane-pairs, #candidate-ranking-lane).

    clauses maps slug to its changed clauses. Each clause ranks the distinct texts of other slugs' changed clauses
    by the draft check's rarity score, each word's rarity counted across these clauses; its TOPK_CROSS highest, ties
    by text, then texts sharing no word, by text, pair with every clause of another slug carrying them. A pair either
    clause ranks is one pair, and two clauses reading the same are never paired. So the pairs grow with the clauses,
    never with their product.
    A score is the exactly rounded sum of the shared words' weights, so a tie is a true tie in any order. Ranking
    costs the words texts share, not a score per pair of texts: a text reads its words' postings in a word index,
    rarest first, until the words left could not lift a text it has not met to its 5th score, so the words most
    texts carry are read only when they could change its top 5, then scores whole only the texts met that still
    could rank. The ranking is the one a score per pair gives. Keyed by (lower slug, higher slug), each pair (its
    index in the lower slug's clauses, its index in the higher's), sorted."""
    entries = [(slug, index, str(clause["after"])) for slug in sorted(clauses) for index, clause in enumerate(clauses[slug])]
    freqs = _doc_freqs([text for _, _, text in entries])
    texts = sorted({text for _, _, text in entries})
    number = {text: n for n, text in enumerate(texts)}
    carriers: list[list[tuple[str, int]]] = [[] for _ in texts]
    for slug, index, text in entries:
        carriers[number[text]].append((slug, index))
    # The one slug carrying a text, None when several do: a slug's clause never ranks its own slug's texts.
    only = [carried[0][0] if len({slug for slug, _ in carried}) == 1 else None for carried in carriers]
    words = [sorted(_anchor_words(text)) for text in texts]
    postings: dict[str, list[int]] = {}
    for n, text_words in enumerate(words):
        for word in text_words:
            postings.setdefault(word, []).append(n)
    weights = {word: _rarity_weight(word, freqs) for word in postings}
    word_sets = [frozenset(text_words) for text_words in words]
    # Each slug's first texts by text it may rank, filling a ranking when fewer texts share a word; the text itself
    # and the fewer than TOPK_CROSS that share one are skipped.
    firsts: dict[str, list[int]] = {}

    def ranked_for(n: int, slug: str, rarest: list[str], unread: list[float]) -> list[int]:
        """Text n's TOPK_CROSS highest texts for a clause of slug, highest first, ties by text."""
        text_words, met, scored, top = word_sets[n], {}, {}, []  # met: each text met, the weight of the words read
        # it shares; scored: each text scored whole; top: the TOPK_CROSS highest scores, the lowest first.

        def score(m: int) -> float:
            if m not in scored:
                scored[m] = math.fsum(map(weights.__getitem__, text_words & word_sets[m]))
            return scored[m]

        # fifth bounds the 5th score from below: a text met is scored whole once the words read lift it past fifth.
        # Once the words left fall below it, no text not met can rank (_SCORE_COST, _COUNT_COST).
        fifth, read = -math.inf, 0
        while read < len(rarest):
            word = rarest[read]
            if unread[read] < fifth - _SUM_SLACK and _COUNT_COST * len(postings[word]) >= len(met):
                floor = fifth - unread[read] - _SUM_SLACK
                still = filter(floor.__le__, met.values())  # counted only up to what reading costs
                if next(itertools.islice(still, len(postings[word]) // _SCORE_COST, None), None) is None:
                    break
            weight, get = weights[word], met.get
            for m in postings[word]:  # every text, met or not: one it may not rank is passed over below
                low = met[m] = get(m, 0.0) + weight
                if low > fifth and m not in scored and m != n and only[m] != slug:
                    (heapq.heappush if len(top) < TOPK_CROSS else heapq.heappushpop)(top, score(m))
                    if len(top) == TOPK_CROSS:
                        fifth = top[0]
            read += 1
        floor = fifth - unread[read] - _SUM_SLACK  # a text met below it cannot rank
        near = list(itertools.compress(met, map(floor.__le__, met.values())))
        heap = list(zip(map(float.__neg__, map(met.__getitem__, near)), near))
        heapq.heapify(heap)
        best: list[tuple[float, int]] = []  # (-score, text), ascending
        while heap:
            low, m = heapq.heappop(heap)
            if len(best) == TOPK_CROSS and unread[read] - low < -best[-1][0] - _SUM_SLACK:
                break  # neither it nor any text met after it can reach the 5th score
            if m == n or only[m] == slug:
                continue
            best.append((-score(m), m))
            best.sort()
            del best[TOPK_CROSS:]
        ranked = [m for _, m in best]
        if slug not in firsts:
            firsts[slug] = list(itertools.islice((m for m in range(len(texts)) if only[m] != slug), 2 * TOPK_CROSS))
        ranked += [m for m in firsts[slug] if m != n and m not in met][:TOPK_CROSS - len(ranked)]
        return ranked

    pairs: dict[tuple[str, str], set[tuple[int, int]]] = {}
    for n, text_words in enumerate(words):
        rarest = sorted(text_words, key=lambda word: (-weights[word], word))
        unread = [0.0] * (len(rarest) + 1)  # unread[i]: the most the words from rarest[i] on add to a score
        for i in range(len(rarest) - 1, -1, -1):
            unread[i] = unread[i + 1] + weights[rarest[i]]
        for slug in sorted({slug for slug, _ in carriers[n]}):
            ranked = ranked_for(n, slug, rarest, unread)
            for _, index in (carried for carried in carriers[n] if carried[0] == slug):
                for m in ranked:
                    for other, other_index in carriers[m]:
                        if other < slug:
                            pairs.setdefault((other, slug), set()).add((other_index, index))
                        elif other > slug:
                            pairs.setdefault((slug, other), set()).add((index, other_index))
    return {key: sorted(value) for key, value in sorted(pairs.items())}


def build_board_conflict_questions(clauses: Mapping[str, list[Mapping[str, Any]]],
                                   shortlist: Mapping[tuple[str, str], list[tuple[int, int]]] | None = None
                                   ) -> list[dict[str, Any]]:
    """One lane chain per shortlisted pair of changed leaf clauses of two slugs (#cross-lane-pairs).

    clauses maps slug to its changed clauses: {path, anchor, before, after, base}; shortlist is their
    lane_shortlist, ranked here when not given. The lower slug's clause is first (#cross-lane-question); each pair
    is asked once, never within a slug."""
    shortlist = lane_shortlist(clauses) if shortlist is None else shortlist
    return [chain for (left, right), indices in shortlist.items()
            for chain in _lane_checks(left, clauses[left], right, clauses[right], indices)]


def _lane_checks(left: str, a: list[Mapping[str, Any]], right: str, b: list[Mapping[str, Any]],
                 indices: Iterable[tuple[int, int]]) -> Iterator[dict[str, Any]]:
    """The lane check of each pair of `a` and `b` (indices), one at a time: the one pair builder, whether the
    pairs are kept built or walked (JevService._lane_pairs). A pair whose check raises gives none."""
    for i, j in indices:
        try:
            chain = lane_check(left, a[i], right, b[j])
        except Exception:
            continue
        yield chain


def lane_check(left: str, a: Mapping[str, Any], right: str, b: Mapping[str, Any]) -> dict[str, Any]:
    """The lane check for a cross-lane pair (#cross-lane-question, #gate-lane): about? gate, then
    contradicts? once, oversteps? both directions, overlaps? only when all three are no.
    Walked by lane_result, not chain_result."""
    first, second = a["path"] + "#" + a["anchor"], b["path"] + "#" + b["anchor"]
    a_ctx = a.get("context") or {}
    b_ctx = b.get("context") or {}
    steps = draft_check(a["anchor"], a["before"], a["after"], second, b["after"],
                        path=a["path"], base=a["base"], revision="working-tree",
                        context=a_ctx, target_context=b_ctx)["chain"]
    # steps[0] = about?, steps[1] = contradicts?, steps[2] = oversteps_fwd?, steps[3] = overlaps?
    steps[2]["then"] = {"yes": "first oversteps second"}
    back_state: dict[str, Any] = {"before": b["before"], "after": b["after"], "target": a["after"]}
    if b_ctx:
        back_state["context"] = dict(b_ctx)
    if a_ctx:
        back_state["target_context"] = dict(a_ctx)
    back = _step(_question("oversteps", b["anchor"], back_state,
                           b["path"], b["base"], "working-tree", first), yes="second oversteps first")
    back["sources"] = [second, first]
    steps.insert(3, back)
    item = _chain("lane", a["anchor"], second, [first, second], {"base": a["base"], "head": "working-tree"}, steps)
    item["pair"] = (a["path"], b["path"])
    item["sides"] = ((left, a["path"], a["anchor"]), (right, b["path"], b["anchor"]))
    item["clauses"] = (left, a, right, b)  # what a waiting ask holds, building the chain again when it starts
    return item


BUILDERS.update({"changed": changed_leaf_clauses, "shortlist": lane_shortlist, "lane": build_board_conflict_questions})


# Cross-lane findings (#cross-lane-finding): answer to the mark on the first and on the second clause.
# The chain's overlaps and all no are none.
LANE_FINDINGS = {"contradicts": ("contradicts", "contradicts"),
                 "first oversteps second": ("oversteps", "overstepped by"),
                 "second oversteps first": ("overstepped by", "oversteps")}


def _final(record: Mapping[str, Any] | None) -> bool:
    """A record that stands: neither missing nor off, unavailable, or escalated."""
    return record is not None and record.get("outcome") not in REPLACEABLE_OUTCOMES


def _escalated(record: Mapping[str, Any] | None) -> bool:
    """Jev was unsure and the general LLM has, or had, the question (project-rules #pending)."""
    return bool(record and (record.get("outcome") == ESCALATED or record.get("escalated")))


def _confident_label(record: Mapping[str, Any] | None) -> str | None:
    if not record:
        return None
    outcome = record.get("outcome")
    if outcome == SILENT_NO:
        return "no"
    if outcome not in CHAIN_CONTINUE:
        return None
    answer = record.get("answer")
    label = answer.get("label") if isinstance(answer, Mapping) else None
    return label if isinstance(label, str) else None


def material(answers: list[Mapping[str, Any] | None]) -> str:
    """#type-material: yes when any changed root section's change type is Behavior, no when every one is No
    behavior change, unknown otherwise (unavailable, unanswered, off); the fallback has settled any unsure one."""
    labels = [_confident_label(answer) for answer in answers]
    if MATERIAL_YES in labels:
        return "yes"
    return "no" if all(label == MATERIAL_NO for label in labels) else "unknown"


def _git_run(root: str, *args: str) -> subprocess.CompletedProcess:
    """Read-only local Git lookup; raises OSError or SubprocessError when git gave no answer."""
    return subprocess.run(("git", "-C", root, *args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"), timeout=5)


def _git_read(root: str, *args: str) -> bytes | None:
    """Read-only local Git lookup; None on any failure."""
    try:
        result = _git_run(root, *args)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _git_answer(root: str, *args: str) -> bytes:
    """Git output on a clean exit; raises otherwise, so nothing unsure is kept."""
    result = _git_run(root, *args)
    result.check_returncode()
    return result.stdout


_COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _commit(root: str, ref: str) -> str | None:
    if not ref or ref.startswith("-"):
        return None
    value = _git_read(root, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
    return value.decode().strip() if value else None


def _blob_id(data: bytes, like: str) -> str:
    """Git's id of a blob holding data, in the object format of the id like, SHA-1 or SHA-256: a working copy compared
    with a tree's entry by id, read from no git process."""
    digest = hashlib.sha256() if len(like) == 64 else hashlib.sha1()
    digest.update(b"blob %d\0" % len(data))
    digest.update(data)
    return digest.hexdigest()


# Shortlisted cross-lane pairs of two slugs kept built (#fast-marks); more, as for two lanes each adding a large spec,
# are built one at a time as a read walks them (review-service #steady-bound).
LANE_KEPT_PAIRS = 1024

# Fast reads (jev-suggestions #fast-marks-section): results kept in memory by the content of their inputs, up to
# this many bytes (#acceptance-store-memory). Bounded by size, not count: one spec's question lists run to megabytes,
# and every edit keeps a new set while the superseded one ages out.
KEPT_BYTES = 128 << 20


def _content_digest(value: Any) -> str:
    """Hash builder inputs by content: text and bytes as they are, containers by structure."""
    digest = hashlib.sha256()

    def feed(item: Any) -> None:
        if isinstance(item, str):
            item = item.encode("utf-8", "surrogatepass")
            digest.update(b"s%d:" % len(item))
            digest.update(item)
        elif isinstance(item, (bytes, bytearray)):
            digest.update(b"b%d:" % len(item))
            digest.update(item)
        elif isinstance(item, Mapping):
            digest.update(b"{%d:" % len(item))
            for key in sorted(item, key=str):
                feed(str(key))
                feed(item[key])
        elif isinstance(item, (list, tuple)):
            digest.update(b"[%d:" % len(item))
            for element in item:
                feed(element)
        else:
            text = repr(item).encode("utf-8", "surrogatepass")
            digest.update(b"r%d:" % len(text))
            digest.update(text)

    feed(value)
    return digest.hexdigest()


def _footprint(value: Any, limit: int | None = None) -> int:
    """Bytes a value holds in memory: each object reachable through its containers, counted once; counting stops
    once past `limit`, so measuring a value too large to keep costs no more than the limit."""
    seen: set[int] = set()
    stack, total = [value], 0
    while stack and (limit is None or total <= limit):
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
    return total


class _Kept:
    """In-memory results beside the held records, least recently used dropped first beyond `limit` bytes, and a
    value larger than the limit returned but never kept (review-service #steady-bound); nothing is stored and a
    restart starts empty."""

    def __init__(self, limit: int = KEPT_BYTES):
        self.limit = limit
        self.used = 0
        self.values: OrderedDict[Any, tuple[Any, int]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: Any, make: Callable[[], Any]) -> Any:
        """The kept value, or make's; a make that raises keeps nothing and the next get retries."""
        with self.lock:
            if key in self.values:
                self.values.move_to_end(key)
                return self.values[key][0]
        value = make()
        size = _footprint(value, self.limit)
        if size > self.limit:
            return value
        with self.lock:
            if key in self.values:
                self.used -= self.values.pop(key)[1]
            self.values[key] = (value, size)
            self.used += size
            while self.used > self.limit:
                self.used -= self.values.popitem(last=False)[1][1]
        return value


def spec_chat_state() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    return (Path(root) if root else Path.home() / ".local" / "state") / "spec-chat"


def default_state_dir() -> Path:
    return spec_chat_state() / "jev"


class _Line:
    """One line of the ask queue (jev-suggestions #ask-queue), under the service's _ask_lock: at most `size` asks
    run, each on its own drain, and the rest wait, in arrival order. `turns` holds, per reader, the turn its line last
    started an ask it wanted; None stands for asks no reader noted."""

    def __init__(self, name: str, size: int):
        self.name, self.size, self.running = name, size, 0
        self.waiting: dict[Future, None] = {}
        self.turns: dict[Any, int] = {}


class JevService:
    _unread = 0  # failed reads of a file at a commit (_at), counted so a board pass that saw one does not stand

    def __init__(self, *, state_dir: str | Path | None = None, provider: Any = None,
                 api_key: str | Callable[[], str] | None = None, llm_model: str | Callable[[], str] | None = None,
                 question_dirs: list[str | Path] | None = None, runs: ServiceRuns | None = None):
        self.question_sets = load_question_sets(*(question_dirs or []))
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        # Records and the warning ledger side by side (#ledger); test-flagged slugs use the same layout in the
        # store's test directory, never the live one (#test-host-rule). Both ask through the one ask queue, and wait
        # out the provider key's one pause (jev-suggestions #provider-refused).
        self.pause = KeyPause()
        self.runs = ServiceRuns() if runs is None else runs  # its claude runs: in a review service, the service's runs
        loaded = [{"id": qset.id, "version": qset.version} for qset in self.question_sets.values()]
        self.seam, self.test_seam = (
            JevSeam(self.question_sets, provider=provider, api_key=api_key, llm_model=llm_model,
                    record_store=JudgmentStore(directory / "records.jsonl", versions=loaded),
                    ledger=WarningLedger(directory / "ledger.jsonl"), pause=self.pause, runs=self.runs)
            for directory in (self.state_dir, self.state_dir / "test"))
        # Compaction (#record-compact): one background pass after start; stop ends it between files.
        self._compaction: threading.Thread | None = None
        self._compaction_stop = threading.Event()

        # Board state (#jev-board-answer): the last answer and the rows the board thread answers next.
        self._board_lock = threading.Lock()
        self._board_rows: list[Any] = []
        self._board_answer: dict[str, Any] = {"jev": "on", "rows": [], "conflicts": []}
        self._board_wake = threading.Event()
        self._board_idle = threading.Event()
        self._board_idle.set()
        self._board_thread: threading.Thread | None = None

        # Background asks (#fast-marks-background, #ask-queue): one in flight per question key, shared by every route,
        # rule checks included (project-rules #pending). _asks holds each ask in flight, running or waiting: its keys,
        # its ask, and each reader whose latest read wants it, (reader, read number). An ask is in one of two lines,
        # Jev's or the general LLM's, and moves between them as its next call needs (_Move). In its line it starts at
        # once on a free run's drain; with every run busy it waits, the line starting its waiting asks in turn across
        # the readers that want them (_next), and leaves unasked once no reader does (_supersede). _readers holds, per
        # reader, its reads running, when its last read ended, and its latest read that found the queue full
        # (_make_room). The pool lives as long as the service: stop() drops waiting asks and waits only for running ones.
        self._ask_lock = threading.Lock()
        self._asking: dict[str, Future] = {}
        self._asks: dict[Future, tuple[tuple[str, ...], Callable[[], Any], dict[Any, int]]] = {}
        self._lines = {"jev": _Line("jev", ASK_WORKERS), "llm": _Line("llm", LLM_RUNS)}
        self._readers: dict[Any, list[float]] = {}
        self._reads, self._turns = itertools.count(1), itertools.count(1)
        self._ask_pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=ASK_WORKERS + LLM_RUNS, thread_name_prefix="spec-chat-jev-ask")

        # Fixed marks (#record-resolution): per spec and base, the head last decided and the Important marks shown on it.
        self._marks_lock = threading.Lock()
        self._marks: dict[tuple[Any, ...], tuple[Any, dict[tuple[Any, ...], tuple[str, Any]]]] = {}

    def start(self) -> None:
        """After start, in the background and delaying no answer, each store keeps only what a read can reach
        (#record-compact): the generations of the question set versions this service loads, and the records its
        warning ledger cites. Called as the server starts answering; start itself reads no record (#record-held)."""
        if self._compaction is not None:
            return

        def compact() -> None:
            for seam in (self.seam, self.test_seam):
                seam.store.compact({entry.get("judgment") for entry in seam.ledger.entries()}, self._compaction_stop)

        self._compaction = threading.Thread(target=compact, name="spec-chat-jev-compact", daemon=True)
        self._compaction.start()

    def stop(self) -> None:
        """Stop asking: later reads ask nothing, waiting asks are dropped, in-flight asks finish their record, each
        claude run ended first, as a failed call (ServiceRuns.stop). Compaction stops after the file it is on."""
        self.runs.stop()
        self._compaction_stop.set()
        if self._compaction is not None:
            self._compaction.join()
        with self._ask_lock:
            pool, self._ask_pool = self._ask_pool, None
            self._drop_waiting()
            self._asking.clear()
        if pool is not None:
            pool.shutdown(wait=True)

    def _seam_for(self, mount: Mapping[str, Any] | None = None) -> "JevSeam":
        """The live seam, or the test seam when the mount is test-flagged (#test-hosts)."""
        if mount and mount.get("test"):
            return self.test_seam
        return self.seam

    @property
    def _kept(self) -> _Kept:
        kept = self.__dict__.get("_kept_results")
        return kept if kept is not None else self.__dict__.setdefault("_kept_results", _Kept())

    def _common_dir(self, root: str) -> str:
        """A row's common Git directory, read once."""
        try:
            return self._kept.get(("common", root), lambda: os.path.realpath(_git_answer(
                root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()))
        except (OSError, subprocess.SubprocessError):
            return os.path.realpath(root)

    def _at(self, root: str, commit: str, relative: str) -> bytes | None:
        """A file's content at a resolved commit, read once: it cannot change. A clean miss, the commit's tree
        read and the path not in it, is kept as absent; a failed read keeps nothing, so a repo rebuilt or
        briefly unreadable is read again."""
        def read() -> bytes | None:
            result = _git_run(root, "show", "--end-of-options", commit + ":" + relative)
            if result.returncode == 0:
                return result.stdout
            if _git_answer(root, "ls-tree", "-z", "--name-only", "--end-of-options", commit, "--", relative):
                raise subprocess.SubprocessError("git show failed on a path in the tree")
            return None
        try:
            return self._kept.get(("at", self._common_dir(root), commit, relative), read)
        except (OSError, subprocess.SubprocessError):
            self._unread += 1  # a board pass that saw a failed read does not stand (_board_pass)
            return None

    def _built(self, kind: str, *args: Any) -> Any:
        """A builder's value, kept by the content of its inputs; kept questions are read only. Raises as the builder does."""
        builder = BUILDERS[kind]
        return self._kept.get(("build", kind, builder, _content_digest(args)), lambda: self._keyed(builder(*args)))

    def _keyed(self, value: Any) -> Any:
        """A built value whose questions, a chain's steps for a chain, each carry their key (QUESTION_KEY)."""
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                for question in item.get("chain") or (item,):
                    qset = self.question_sets.get(str(question.get("kind", "type")))
                    if qset is not None:
                        question[QUESTION_KEY] = (qset.id, qset.version, _question_key(question, qset))
        return value

    def _build(self, kind: str, *args: Any) -> list[dict[str, Any]]:
        """A builder's questions as a list; a builder that raises gives none."""
        try:
            value = self._built(kind, *args)
        except Exception:
            return []
        return list(value) if isinstance(value, list) else []

    def _read_facts(self, mount: Mapping[str, Any], target: str, base: str, served_mounts: Any,
                    base_commit: str | None) -> dict[str, Any]:
        """One read's inputs, resolved once for every builder: HEAD, base commit and content, served specs."""
        current = Path(target).read_bytes()
        root = mount["root"]
        rel = os.path.relpath(target, root).replace(os.sep, "/")
        resolved_head = _commit(root, "HEAD")
        if base_commit is None:
            base_commit = _commit(root, base)
        mains: dict[str, str | None] = {}  # each repository's origin/main, resolved once a read
        rows = served_mounts or [mount]
        return {"current": current, "root": root, "rel": rel, "resolved_head": resolved_head,
                "head": resolved_head or "working-tree", "base_commit": base_commit,
                "old": self._at(root, base_commit, rel) if base_commit else None,
                "served": self._served_specs(rows, target, mount, mains), "mains": mains, "rows": rows}

    @property
    def enabled(self) -> bool:
        return bool(self.question_sets) and bool(self.seam.api_key())

    def _served_specs(self, mounts: Any, current: str, page: Mapping[str, Any] | None = None,
                      mains: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        """Other specs (#corpus-others): the lane's own served specs as served, plus main's copy of every
        other served spec path of the page's project, once per project and path; another lane's served copy
        and another project's specs are never read. mains holds each repository's origin/main as resolved."""
        mounts = [mounts] if isinstance(mounts, Mapping) else list(mounts or [])
        slug = str(page.get("slug", "")) if isinstance(page, Mapping) else ""
        if not slug:
            return self._as_served(mounts, current)
        project = page.get("project") or ""
        mounts = [mount for mount in mounts if isinstance(mount, Mapping) and (mount.get("project") or "") == project]
        own = lane_rows(mounts, page)
        result = self._as_served(own, current)
        mains = {} if mains is None else mains

        def spec_key(mount: Mapping[str, Any], filename: str) -> tuple[str, str]:
            root = str(mount.get("root", ""))
            return self._common_dir(root), os.path.relpath(filename, root).replace(os.sep, "/")

        seen = {spec_key(page, current)}
        for mount in own:
            seen.update(spec_key(mount, filename) for _, filename in enumerate_served_specs(mount))
        for mount in mounts:
            if mount.get("slug") == slug:
                continue
            for relative, filename in enumerate_served_specs(mount):
                key = spec_key(mount, filename)
                if key in seen or key[1].startswith("../"):
                    continue
                seen.add(key)
                root = str(mount["root"])
                if key[0] not in mains:
                    mains[key[0]] = _commit(root, "origin/main")
                source = self._at(root, mains[key[0]], key[1]) if mains[key[0]] else None
                if source is not None:
                    result.append({"path": mount_prefix(mount) + relative, "spec": key[1], "source": source})
        return result

    def _fork(self, root: str, head: str, main: str) -> str | None:
        """Where the lane's branch left main: the merge base of its HEAD and main, resolved commits, read once a pair;
        None when they share none."""
        def read() -> str | None:
            result = _git_run(root, "merge-base", head, main)
            if result.returncode == 0:
                return result.stdout.decode().strip() or None
            if result.returncode == 1:
                return None
            raise subprocess.SubprocessError("git merge-base failed")
        return self._kept.get(("fork", self._common_dir(root), head, main), read)

    def _tree_specs(self, root: str, commit: str, collection: str) -> dict[str, str]:
        """The specs a commit's tree holds in a collection, named by its folder's repo path, found as the review index
        walks one (walked_folder, walked_spec): each spec's repo path and blob id, read once a commit, as _at reads a
        copy."""
        def read() -> dict[str, str]:
            inside = () if collection == "." else ("--", collection)
            listed = _git_answer(root, "ls-tree", "-r", "-z", "--full-tree", "--end-of-options", commit, *inside)
            result = {}
            for entry in listed.split(b"\0"):
                meta, _, path = entry.decode("utf-8", "surrogateescape").partition("\t")
                mode, _, blob = meta.partition(" blob ")
                *folders, name = (path if collection == "." else path[len(collection) + 1:]).split("/")
                if mode in ("100644", "100755") and walked_spec(name) and all(map(walked_folder, folders)):
                    result[path] = blob
            return result
        return self._kept.get(("tree", self._common_dir(root), commit, collection), read)

    def _rule_source(self, mount: Mapping[str, Any], facts: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The specs whose marks are the rules a page is checked against (project-rules #q-rule-source), each once by
        repo path, at the path the page's own lane serves it (#card-source). On a registry row: every spec the
        project's main, origin/main in the page's root, holds in the row's collection, found as the review index finds
        a collection's specs, main's copy; one the page's lane changes, its copy in the lane's root differing from its
        copy where the lane's branch left main, added included, the lane's copy, and one it deletes none. With no main
        nothing has left it, so every spec the lane's root holds is the lane's. The page's own spec is never one, and
        no other lane's copy is read. Each carries served, True when a row of the page's lane serves it, as a row serves
        its spec alone and a stale row none, so its card links it at that row's own path (#card-source); else its path
        is the one the page's prefix would serve it at, which no link opens. In single-root mode, the root's other
        served specs, every one of them served."""
        if not mount.get("slug"):
            return facts["served"]
        root = str(mount["root"])
        folder = str(mount.get("narrow_root") or root)
        collection = os.path.relpath(folder, root).replace(os.sep, "/")
        common, mains = self._common_dir(root), facts["mains"]
        if common not in mains:
            mains[common] = _commit(root, "origin/main")
        main, head = mains[common], facts["resolved_head"]
        fork = self._fork(root, head, main) if main and head else None
        on_main = self._tree_specs(root, main, collection) if main else {}
        at_fork = self._tree_specs(root, fork, collection) if fork else {}
        lane = {os.path.relpath(filename, root).replace(os.sep, "/"): filename
                for _, filename in enumerate_served_specs({"narrow_root": folder})}
        # Each spec a row of the page's lane serves, at that row's own path: one lane's rows may sit under different
        # prefixes, as the host gives a row slug/<project>/<spec> once another project holds the slug.
        serving = {row["spec"]: served_path(row, row["spec"]) for row in lane_rows(facts["rows"], mount)
                   if row.get("spec") and not row.get("stale")}
        result = []
        for rel in sorted(set(lane) | set(on_main)):
            if rel == facts["rel"]:
                continue
            try:
                copy = Path(lane[rel]).read_bytes() if rel in lane else None
            except OSError:
                copy = None
            blob = _blob_id(copy, main) if copy is not None and main else None
            if main and blob == at_fork.get(rel) and on_main.get(rel) != blob:
                copy = self._at(root, main, rel) if rel in on_main else None  # unchanged by the lane: main's copy
            if copy is not None:
                result.append({"path": serving.get(rel) or served_path(mount, rel), "spec": rel, "source": copy,
                               "served": rel in serving})
        return result

    def _as_served(self, mounts: list[Any], current: str) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for mount in mounts:
            for relative, filename in enumerate_served_specs(mount):
                filename = os.path.realpath(filename)
                if filename == os.path.realpath(current) or filename in seen or not os.path.isfile(filename):
                    continue
                seen.add(filename)
                served = {"path": mount_prefix(mount) + relative}
                root = mount.get("root") if isinstance(mount, Mapping) else None
                if root:
                    served["spec"] = os.path.relpath(filename, os.path.realpath(str(root))).replace(os.sep, "/")
                try:
                    result.append({**served, "source": Path(filename).read_bytes()})
                except OSError:
                    continue
        return result

    def _message_sources(self, root: str, relative: str, base: str | None,
                         events: list[Mapping[str, Any]], head: str | None = None) -> dict[str, bytes]:
        """Spec text at each open thread's newest human message; base and head are resolved commits."""
        if head is None:
            head = _commit(root, "HEAD")
        result: dict[str, bytes] = {}
        for thread in _human_threads(events):
            if thread.get("status") == "resolved":
                continue
            human = [message for message in thread["messages"] if message.get("actor") == "human"]
            if not human:
                continue
            message = human[-1]
            identifier = message.get("id")
            if not identifier:
                continue
            commit = ""
            created = message.get("createdAt")
            if isinstance(created, str) and re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
                    created):
                if head:
                    try:
                        commit = self._kept.get(("log", self._common_dir(root), head, relative, created), lambda: _git_answer(
                            root, "log", "-1", "--before=" + created, "--format=%H", "--end-of-options", head, "--",
                            relative)).decode().strip()
                    except (OSError, subprocess.SubprocessError):
                        commit = ""
            commit = commit or base
            if not commit:
                continue
            source = self._at(root, commit, relative)
            if source is not None:
                result[str(identifier)] = source
        return result

    def questions(self, mount: Mapping[str, Any], target: str, relative: str, base: str,
                  events: list[Mapping[str, Any]], view: str = "", served_mounts: Any = None,
                  base_commit: str | None = None, facts: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Questions for one read; base_commit is base already resolved by the caller, else resolved here."""
        if facts is None:
            facts = self._read_facts(mount, target, base, served_mounts, base_commit)
        current, old, head = facts["current"], facts["old"], facts["head"]
        message_sources = self._message_sources(facts["root"], facts["rel"], facts["base_commit"], events,
                                                facts["resolved_head"] or "")
        build = self._build

        result = build("type", current, old, relative, base, head)
        result.extend(build("orphan", events, current, relative, base, head))
        result.extend(build("resolved", events, current, old, relative, base, head, message_sources))
        result.extend(build("coverage", current, relative, base, head))
        result.extend(build("corpus", current, old, relative, base, head, facts["served"]))
        if view == "reading":
            result.extend(build("audience", current, relative, base, head))
        return result

    def _row_parts(self, row: Mapping[str, Any]) -> tuple[str, str, str, bytes]:
        """A row's root, spec, served path, and spec text: the text a board pass read (`source`), else the file's."""
        root, spec = str(row["root"]), str(row["spec"])
        path = served_path(row, spec)
        if "source" in row:
            if row["source"] is None:
                raise OSError("spec unreadable")
            return root, spec, path, row["source"]
        current = Path(row.get("spec_file") or os.path.join(root, *spec.split("/"))).read_bytes()
        return root, spec, path, current

    def _material_questions(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Change type questions for every root section changed since the row base (#jev-material)."""
        root, spec, path, current = self._row_parts(row)
        base = _commit(root, str(row.get("base", "")))
        if base is None:
            raise ValueError("row base is not a commit")
        head = _commit(root, "HEAD") or "working-tree"
        return self._built("type", current, self._at(root, base, spec), path, base, head)

    def _changed_clauses(self, row: Mapping[str, Any], mains: dict[str, str | None] | None = None) -> list[dict[str, Any]]:
        """Changed leaf clauses against target main, the row root's origin/HEAD as last fetched, resolved once
        per repository in mains when given."""
        root, spec, path, current = self._row_parts(row)
        mains = {} if mains is None else mains
        common = self._common_dir(root)
        if common not in mains:
            mains[common] = _commit(root, "refs/remotes/origin/HEAD")
        base = mains[common]
        if base is None:
            return []
        old = self._at(root, base, spec)
        result = []
        for anchor, before, after, ctx in self._built("changed", current, old):
            entry: dict[str, Any] = {"path": path, "spec": spec, "anchor": anchor, "before": before, "after": after,
                                     "base": base}
            if ctx:
                entry["context"] = ctx
            result.append(entry)
        return result

    def board(self, rows: Any) -> dict[str, Any]:
        """GET /api/jev/board (#jev-board-answer): the last answer at once; the board thread refreshes it."""
        if not self.enabled:
            return {"jev": "off", "rows": [], "conflicts": []}
        self._reading("board", 0)  # its asks stay wanted while the board is read (#ask-queue)
        with self._board_lock:
            self._board_rows = list(rows or ())
            self._board_idle.clear()
            self._board_wake.set()
            if self._board_thread is None:
                self._board_thread = threading.Thread(target=self._board_loop, name="spec-chat-jev-board", daemon=True)
                self._board_thread.start()
            return self._board_answer

    def _board_loop(self) -> None:
        """Refresh the board for the latest rows, once per wake; between passes it holds only the inputs digest of
        the answer that stands."""
        settled = None
        while True:
            self._board_wake.wait()
            with self._board_lock:
                self._board_wake.clear()
                rows = self._board_rows
            try:
                settled = self._board_pass(rows, settled)
            except Exception:
                settled = None
            with self._board_lock:
                if not self._board_wake.is_set():
                    self._board_idle.set()

    def _board_pass(self, rows: Any, settled: str | None) -> str | None:
        """Answer the rows from held records, publish, ask the misses, then publish again. The inputs are each
        row with its spec text, read once here, and each repository's target main; the host records a row base as
        the commit it resolves to (review-index #index-entry-cost), and a pass over a base that is not one never
        stands. A pass whose every answer is final and whose every read succeeded stands until its inputs change:
        unchanged inputs skip the pass. Returns the digest of the inputs that stand, else None."""
        rows = [row for row in rows or () if isinstance(row, Mapping) and row.get("slug") and row.get("spec")
                and not row.get("test")]
        read, mains = [], {}
        for row in rows:
            try:
                root, _, _, source = self._row_parts(row)
            except Exception:
                read.append({**row, "source": None})
                continue
            read.append({**row, "source": source})
            common = self._common_dir(root)
            if common not in mains:
                mains[common] = _commit(root, "refs/remotes/origin/HEAD")
        inputs = _content_digest([read, sorted(mains.items())])
        if inputs == settled:
            return settled
        unread = self._unread
        answer, misses, done = self._board_from_records(read, mains)
        self._board_answer = answer
        if misses:
            wait(self._ask_misses(misses, read=("board", next(self._reads))))
            self._board_answer, _, done = self._board_from_records(read, mains)
        commits = all(_COMMIT_ID.fullmatch(str(row.get("base", ""))) for row in rows)
        return inputs if done and commits and unread == self._unread else None

    def _held(self, question: Mapping[str, Any], misses: dict[str, dict[str, Any]],
              seam: "JevSeam | None" = None) -> dict[str, Any] | None:
        """The held record; a failed one only during its pause (jev-contract #retry-before). A miss is collected."""
        seam = seam or self.seam
        key = seam.key(question)
        record = self._held_record(key, seam)
        if record is None:
            misses.setdefault(key, dict(question))
        return record

    def _held_record(self, key: str, seam: "JevSeam | None" = None) -> dict[str, Any] | None:
        """A held record. `off` is no answer (the key was missing when asked), and `escalated` is an answer
        not yet settled (an ask that stopped after recording it): both are asked again, the escalated one
        through the escalated fast-path. A failed one answers only until its pause ends (jev-contract #retry-before)."""
        store = (seam or self.seam).store
        record = store.get(key)
        if record is None or record.get("outcome") in ("off", ESCALATED):
            return None
        if record.get("outcome") == "unavailable" and not pause_left(record):
            return None
        return record

    def _ask_misses(self, misses: Mapping[str, dict[str, Any]], seam: "JevSeam | None" = None,
                    read: tuple[Any, int] | None = None) -> list[Future]:
        """Ask each miss, by key, once in the background; a question in flight or since answered is not asked again."""
        seam = seam or self.seam
        futures = []
        with self._ask_lock:
            for key, question in misses.items():
                # An ask records before it leaves _asking: a key absent from both is truly unasked.
                if key not in self._asking and self._held_record(key, seam) is not None:
                    continue
                future = self._submit((key,), functools.partial(self._ask_question, question, seam), read,
                                      self._line_of(key, question, seam))
                if future is not None:
                    futures.append(future)
        return futures

    @staticmethod
    def _line_of(key: str, question: Mapping[str, Any], seam: "JevSeam") -> str:
        """The line a new ask waits in first (#ask-queue): the general LLM's for a set it alone answers, or for a key
        Jev was unsure of, its answer the LLM's to give; else Jev's. On the way it moves as its next call needs (_Move)."""
        step = question["chain"][0] if "chain" in question else question
        qset = seam.question_sets.get(str(step.get("kind", "type")))
        return "llm" if (qset is not None and qset.llm_only) or _escalated(seam.store.get(key)) else "jev"

    def _ask_question(self, question: Mapping[str, Any], seam: "JevSeam") -> None:
        if question.get("kind") == "lane" and "clauses" in question:
            self._ask_lane(question["clauses"], seam)
        else:
            seam.answer(question)

    def _submit(self, keys: tuple[str, ...] | Callable[[], tuple[str, ...]], ask: Callable[[], Any],
                read: tuple[Any, int] | None, line: str = "jev") -> Future | None:
        """Under _ask_lock: the ask in flight for any of its keys, else a new one of them in `line`, started on a free
        run or waiting for one; the ask is noted as wanted by `read`, (reader, read number), keeping the reader's
        latest read that wants it: reads of one reader overlap, as from two tabs, and end in any order. `keys` are
        the ask's keys, or for a lane a callable computing them only once it can start or wait: with every run of
        the line busy and ASK_WAITING asks waiting in both lines, the read first makes room (_make_room), and while
        none is left a new ask stays pending, unqueued and unkeyed, until a later read (#ask-queue). Keys given are
        looked up first, so a full queue still finds, and notes the want of, their ask in flight. None then, once
        stopped, or while a refused key's pause holds, when nothing is queued (jev-suggestions #provider-refused)."""
        if self._ask_pool is None or self.pause.holds():
            return None

        def in_flight(keys: tuple[str, ...]) -> Future | None:
            return next((self._asking[key] for key in keys if key in self._asking), None)

        future = None if callable(keys) else in_flight(keys)
        lane = self._lines[line]
        start = lane.running < lane.size
        if future is None and not start and self._waiting_count() >= ASK_WAITING and not self._make_room(read):
            return None
        if callable(keys):
            keys = keys()
            future = in_flight(keys)
        new = future is None
        if new:
            future = Future()
            for key in keys:
                self._asking[key] = future
            self._asks[future] = (keys, ask, {})
        if read is not None:
            wanted = self._asks[future][2]
            wanted[read[0]] = max(wanted.get(read[0], 0), read[1])
        if new:
            self._enter(lane, future)
        return future

    def _enter(self, line: _Line, future: Future) -> None:
        """Under _ask_lock: an ask in flight enters `line`, started on a free run's drain, else waiting for one."""
        if line.running >= line.size:
            line.waiting[future] = None
            return
        line.running += 1
        self._take_turn(line, future)
        self._ask_pool.submit(self._drain, line, future)

    def _take_turn(self, line: _Line, future: Future) -> None:
        """Under _ask_lock: `line` starts the ask, which takes the turn of each reader that wants it (_next)."""
        turn = next(self._turns)
        for who in self._asks[future][2] or (None,):
            line.turns[who] = turn
        if not future.running():  # an ask that moved lines is running already
            future.set_running_or_notify_cancel()

    def _drain(self, line: _Line, future: Future) -> None:
        """One run of `line`: run its ask, then the next waiting one (_next), until none waits. An ask whose next
        call is the other line's moves there, still in flight (_Move); one that ends leaves _asking only after it
        records. While a refused key's pause holds, every waiting ask leaves the queue unasked
        (jev-suggestions #provider-refused)."""
        _ON_LINE.name = line.name
        while True:
            moved = None
            try:
                self._asks[future][1]()
            except _Move as move:
                moved = self._lines[move.line]
            except Exception:
                pass
            with self._ask_lock:
                if moved is not None and self._ask_pool is not None and not self.pause.holds():
                    self._enter(moved, future)
                else:
                    self._end(future)
                    future.set_result(None)
                if self.pause.holds():
                    self._drop_waiting()
                future = self._next(line)
                if future is None:
                    line.running -= 1
                    return
                self._take_turn(line, future)

    def _next(self, line: _Line) -> Future | None:
        """Under _ask_lock: the waiting ask `line` starts as a run frees, taken out of it (#ask-queue). The line starts
        its waiting asks in turn across the readers that want them, each reader's oldest first: the reader whose turn
        is longest past, one with none yet first, so a reader with a few asks never waits behind all of another's
        many. Asks no live reader still wants leave unasked; asks no reader noted take turns as one reader."""
        now = _monotonic()
        oldest: dict[Any, Future] = {}
        for future in list(line.waiting):
            wanted = self._asks[future][2]
            live = [who for who in wanted if not self._gone(who, now)]
            if wanted and not live:
                del line.waiting[future]
                self._drop(future)
                continue
            for who in live or (None,):
                oldest.setdefault(who, future)
        if not oldest:
            return None
        future = oldest[min(oldest, key=lambda who: line.turns.get(who, 0))]
        del line.waiting[future]
        return future

    def _end(self, future: Future) -> None:
        """Under _ask_lock: an ask leaves the queue; a read that still misses its keys asks them again."""
        keys, _, _ = self._asks.pop(future, ((), None, None))
        for key in keys:
            self._asking.pop(key, None)

    def _drop(self, future: Future) -> None:
        """Under _ask_lock: a waiting ask, out of its line, leaves the queue unasked; whoever waits on it is told."""
        self._end(future)
        if not future.cancel():  # it ran in another line before it moved
            future.set_result(None)

    def _drop_waiting(self) -> None:
        """Under _ask_lock: every waiting ask, in either line, leaves the queue unasked."""
        for line in self._lines.values():
            waiting, line.waiting = line.waiting, {}
            for future in waiting:
                self._drop(future)

    def _waiting_count(self) -> int:
        """Under _ask_lock: the asks waiting, in both lines."""
        return sum(len(line.waiting) for line in self._lines.values())

    def _make_room(self, read: tuple[Any, int] | None) -> bool:
        """Under _ask_lock, the ask queue full: once per read, the waiting asks its reader wanted only in earlier
        reads leave it, as the read's end would drop them (_supersede), so an edited spec's first read queues its
        asks in place of the old text's (#ask-queue). Whether room is left."""
        entry = self._readers.get(read[0]) if read is not None else None
        if entry is None or entry[2] >= read[1]:
            return False
        entry[2] = read[1]
        self._drop_superseded(read)
        return self._waiting_count() < ASK_WAITING

    def _reading(self, reader: Any, running: int) -> None:
        """A read of `reader` starts (1) or ends (-1); a board read (0) does both at once (#ask-queue)."""
        with self._ask_lock:
            entry = self._readers.setdefault(reader, [0, 0.0, 0])
            entry[0] += running
            entry[1] = _monotonic()

    def _gone(self, reader: Any, now: float) -> bool:
        """Under _ask_lock: no read of `reader` runs and none ended within the poll window (#ask-queue)."""
        entry = self._readers.get(reader)
        return entry is None or (entry[0] <= 0 and now - entry[1] > POLL_WINDOW)

    def _supersede(self, read: tuple[Any, int] | None = None) -> None:
        """At the end of a read (#acceptance-store-memory, #ask-queue): a waiting ask its reader wanted only in reads
        before this one is no longer wanted by it, nor by any reader gone, and one no reader wants is dropped. A read
        asks again every key it still misses, so nothing still needed is lost, and asks for spec text since edited
        or for readers gone do not pile up. With no read, only gone readers stop wanting."""
        with self._ask_lock:
            self._drop_superseded(read)

    def _drop_superseded(self, read: tuple[Any, int] | None) -> None:
        """Under _ask_lock: _supersede's drop, in both lines; a reader gone leaves its turns too."""
        reader, number = read or (None, 0)
        now = _monotonic()
        for line in self._lines.values():
            for future in list(line.waiting):
                wanted = self._asks[future][2]
                left = [who for who, latest in wanted.items()
                        if (who == reader and latest < number) or self._gone(who, now)]
                for who in left:
                    del wanted[who]
                if left and not wanted:
                    del line.waiting[future]
                    self._drop(future)
        for who in [who for who in self._readers if self._gone(who, now)]:
            del self._readers[who]
        for line in self._lines.values():
            for who in [who for who in line.turns if who is not None and who not in self._readers]:
                del line.turns[who]

    def queue_counts(self) -> tuple[int, int]:
        """The ask queue for the status route (review-service #steady-status) and each read's log line: (asks
        waiting, asks running), both lines'. Asks no live reader wants leave first, so every waiting ask counted is
        one a reader still wants."""
        self._supersede()
        with self._ask_lock:
            return self._waiting_count(), sum(line.running for line in self._lines.values())

    def paused(self) -> dict[str, Any]:
        """While the provider refuses the key, paused_until and status, the pause's end and the provider's status,
        for every /api/jev answer and the status route (jev-suggestions #provider-refused); else empty."""
        return self.pause.paused()

    def _unasked(self) -> str:
        """A question with no answer and none coming yet: pending, or unavailable while a refused key's pause lasts,
        as for any failed ask (jev-suggestions #provider-refused)."""
        return "unavailable" if self.pause.paused() else "pending"

    def _lane_questions(self, rows: Any, *, exclude_test: bool = True, mains: dict[str, str | None] | None = None,
                        own: str | None = None) -> Iterator[dict[str, Any]]:
        """Cross-lane questions over every registry row's changed clauses (#cross-lane-clauses), one chain at a time:
        each project's shortlisted pairs (#cross-lane-pairs), or with `own`, a page's served path, only those on its
        own clauses, so page reads and the board walk the same pairs. The shortlist is kept by the content of the
        project's changed clauses: an unchanged clause set is not ranked again. A read resolves each repository's
        target main once, or takes mains already resolved, and reparses nothing unchanged. Only slugs of the same
        project are paired (#shared-own-project). Test-flagged slugs are excluded (#cross-lane-test-host)."""
        if not {"about", "contradicts", "oversteps", "overlaps"} <= set(self.seam.question_sets):
            return
        projects: dict[str, dict[str, list[dict[str, str]]]] = {}
        mains = {} if mains is None else dict(mains)
        for row in rows or ():
            if not isinstance(row, Mapping) or not row.get("slug") or not row.get("spec"):
                continue
            if exclude_test and row.get("test"):
                continue
            clauses = projects.setdefault(str(row.get("project") or ""), {})
            try:
                clauses.setdefault(str(row["slug"]), []).extend(self._changed_clauses(row, mains))
            except Exception:
                continue
        for clauses in projects.values():
            try:
                shortlist = self._built("shortlist", clauses)
            except Exception:
                continue
            for (left, right), indices in shortlist.items():
                yield from self._lane_pairs(left, clauses[left], right, clauses[right], indices, own)

    def _lane_pairs(self, left: str, a: list[dict[str, Any]], right: str, b: list[dict[str, Any]],
                    indices: list[tuple[int, int]], own: str | None = None) -> Iterable[dict[str, Any]]:
        """The lane check of each shortlisted pair of `a` and `b` (indices), the lower slug's first
        (#cross-lane-question); with `own`, only the pairs on its clauses. Up to LANE_KEPT_PAIRS pairs are kept by
        content (#fast-marks), the same for every read; more are built as walked and dropped, so what a read holds or
        builds does not grow with the pairs it walks (review-service #steady-bound). Either way each pair is built by
        _lane_checks, so one whose check raises is skipped and the rest are read."""
        mine = indices if own is None else [(i, j) for i, j in indices if own in (a[i]["path"], b[j]["path"])]
        if not mine:
            return ()
        if len(indices) <= LANE_KEPT_PAIRS:
            chains = self._built("lane", {left: a, right: b}, {(left, right): indices})
            return chains if own is None else [chain for chain in chains if own in chain["pair"]]

        def walked() -> Iterator[dict[str, Any]]:
            for chain in _lane_checks(left, a, right, b, mine):
                self._keyed(chain["chain"][:1])  # the gate every walk reads; a later step is keyed on each use
                yield chain
        return walked()

    def _lane_items(self, mount: Mapping[str, Any], rows: Any,
                    read: tuple[Any, int] | None = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """`lane` items on this page's own clauses from held records, each with the other lane's changed clause it
        cites; misses are asked after answering. Test-flagged slugs are excluded (#cross-lane-test-host)."""
        if not mount.get("slug") or not mount.get("spec") or mount.get("test"):
            return []
        own = self._row_parts(mount)[2]

        def final(question: Mapping[str, Any]) -> dict[str, Any] | None:
            state, record = self._rule_held(question)
            return record if state == "final" else None

        items = []
        for chain in self._lane_questions(rows, own=own):
            results = lane_result(chain["chain"], final)
            # Emit finding items
            for result in results:
                marks = LANE_FINDINGS.get(_confident_label(result))
                if marks is None:
                    continue
                for index, (_, path, anchor) in enumerate(chain["sides"]):
                    if path != own:
                        continue
                    other = chain["sides"][1 - index]
                    level = MARK_LEVELS["oversteps" if marks[index] == "overstepped by" else marks[index]]
                    item = {"kind": "lane", "id": anchor, "state": "label", "label": marks[index],
                            "side": ("first", "second")[index], "other": other[0],
                            "target": other[1] + "#" + other[2], "record": result.get("record_id")}
                    item["level"], item["agent_level"] = level["human"], level["agent"]
                    if result.get("unsure"):
                        item["unsure"] = result["unsure"]
                    items.append((item, chain["clauses"][3 - 2 * index]))
            # Pending: submit and emit pending items only when no finding yet
            pending_r = next((r for r in results if r.get("outcome") is None), None)
            if pending_r is not None and pending_r.get("step"):
                state_r, record_r = self._rule_held(pending_r["step"])
                if state_r == "pending":
                    self._lane_submit(chain, read=read)
                has_finding = any(_confident_label(r) in LANE_FINDINGS for r in results)
                if state_r == "pending" and not has_finding:
                    for index, (_, path, anchor) in enumerate(chain["sides"]):
                        if path != own:
                            continue
                        other = chain["sides"][1 - index]
                        item = {"kind": "lane", "id": anchor, "state": "pending", "label": None,
                                "side": ("first", "second")[index], "other": other[0],
                                "target": other[1] + "#" + other[2], "record": None}
                        item["escalated"] = _escalated(record_r)
                        items.append((item, chain["clauses"][3 - 2 * index]))
        return items

    def _ask_lane_steps(self, steps: list[Mapping[str, Any]], seam: "JevSeam | None" = None) -> None:
        """Ask a lane check (#gate-lane): about? gate, then contradicts? once, both oversteps?, overlaps? when all no."""
        seam = seam or self.seam
        g = seam.ask(steps[0])
        if _confident_label(g) != "yes":
            return
        r = seam.ask(steps[1])
        if _confident_label(r) != "no":
            return
        a = seam.ask(steps[2])
        b = seam.ask(steps[3])
        if _confident_label(a) == "no" and _confident_label(b) == "no":
            seam.ask(steps[4])

    def _ask_lane(self, clauses: tuple[Any, ...], seam: "JevSeam | None" = None) -> None:
        """Ask the lane check of a clause pair, its chain built as the ask starts: waiting, it holds only its clauses."""
        self._ask_lane_steps(lane_check(*clauses)["chain"], seam)

    def _lane_submit(self, chain: Mapping[str, Any], seam: "JevSeam | None" = None,
                     read: tuple[Any, int] | None = None) -> None:
        """Ask a lane chain in the background; deduped by its steps' keys, computed only when it can start or wait."""
        seam = seam or self.seam
        with self._ask_lock:
            self._submit(lambda: tuple(seam.key(step) for step in chain["chain"]),
                         functools.partial(self._ask_lane, chain["clauses"], seam), read)

    def _board_from_records(self, rows: Any, mains: dict[str, str | None] | None = None
                            ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], bool]:
        """The board answer from held records, its misses, and whether it is final: every row built and every
        answer it walked final, so held records cannot change it."""
        rows = [row for row in rows or () if isinstance(row, Mapping) and row.get("slug") and row.get("spec")
                and not row.get("test")]
        sets = self.seam.question_sets
        misses: dict[str, dict[str, Any]] = {}
        final = [True]

        def get_held(step: Mapping[str, Any]) -> dict[str, Any] | None:
            record = self._held_record(self.seam.key(step))
            if not _final(record):
                final[0] = False
            return record

        def held_chain(item: Mapping[str, Any]) -> dict[str, Any]:
            # A chain waiting on a step is asked whole, so each answer asks on from it (#chains).
            result = chain_result(item["chain"], get_held)
            if result["outcome"] is None:
                misses.setdefault(self.seam.key(result["step"]), dict(item))
            return result

        answer_rows = []
        for row in rows:
            try:
                questions = self._material_questions(row) if "type" in sets else None
            except Exception:
                questions, final[0] = None, False
            if row.get("source", b"") is None:
                final[0] = False
            answer_rows.append({"id": str(row.get("id", "")),
                                "material": "unknown" if questions is None else material([held_chain(q) for q in questions])})
        conflict_pairs: set[tuple[str, str]] = set()
        for chain in self._lane_questions(rows, mains=mains):
            results = lane_result(chain["chain"], get_held)
            for r in results:
                if r.get("outcome") is None and "step" in r and len(misses) < ASK_WAITING:  # no more could wait
                    misses.setdefault(self.seam.key(r["step"]), {"kind": "lane", "clauses": chain["clauses"]})
            if any(_confident_label(r) in LANE_FINDINGS for r in results):
                conflict_pairs.add(chain["pair"])
        answer = {"jev": "on", "rows": answer_rows, "conflicts": [{"a": a, "b": b} for a, b in sorted(conflict_pairs)]}
        return answer, misses, final[0] and not misses

    def _ask_submit(self, question: Mapping[str, Any], seam: "JevSeam | None" = None,
                    read: tuple[Any, int] | None = None) -> None:
        """Ask a single question on the shared pool; one in flight is not asked again."""
        seam = seam or self.seam
        with self._ask_lock:
            key = seam.key(question)
            self._submit((key,), functools.partial(seam.ask, question), read, self._line_of(key, question, seam))

    def _rule_submit(self, questions: list[Mapping[str, Any]], seam: "JevSeam", read: tuple[Any, int] | None) -> None:
        """The spec text's rule check as one ask carrying every waiting rule's key (project-rules #q-rule-llm); one in
        flight for any of them is not asked again, and a rule it leaves out is asked by a later read. Its set is the
        general LLM's alone, so it waits only in the LLM's line (#ask-queue)."""
        with self._ask_lock:
            keys = tuple(seam.key(question) for question in questions)
            self._submit(keys, functools.partial(seam.check_rules, questions), read,
                         self._line_of(keys[0], questions[0], seam))

    def _rule_held(self, question: Mapping[str, Any], seam: "JevSeam | None" = None) -> tuple[str, dict[str, Any] | None]:
        """final, pending (to ask or in flight), or unavailable during its pause or a refused key's, with the
        record; never asks."""
        seam = seam or self.seam
        key = seam.key(question)
        record = seam.store.get(key)
        with self._ask_lock:
            inflight = key in self._asking
        if _final(record):
            return "final", record
        if inflight or self._held_record(key, seam) is None:
            return self._unasked(), record
        return "unavailable", record

    def _candidate_where(self, project: Any, scope: Mapping[str, Any]) -> dict[str, Any]:
        """Where a candidate's outcome sits in the warning ledger: project, repo spec path, anchor (#dismiss-trace)."""
        spec, _, anchor = scope["clause"].partition("#")
        return {"project": str(project), "spec": spec, "anchor": anchor, "kind": RULE_CANDIDATE, "target": None}

    def _settled(self, seam: "JevSeam", project: Any, scope: Mapping[str, Any]) -> bool:
        """A criterion marked or answered Not a project rule is never proposed again, through rewording too (#q-candidate)."""
        latest = seam.ledger.latest(**self._candidate_where(project, scope)) if project else None
        return bool(latest and latest["outcome"] in SETTLED)

    def _marked(self, seam: "JevSeam", project: Any, scope: Mapping[str, Any]) -> None:
        """A served spec's mark on a criterion Jev answered at any scope label: one marked outcome citing that scope
        record, so a mark on a this feature answer counts as a miss (#dismiss-trace). Never asks."""
        if not project:
            return
        where = self._candidate_where(project, scope)
        latest = seam.ledger.latest(**where)
        if latest and latest["outcome"] == MARKED:
            return
        record = seam.cached(scope)
        if record and record.get("record_id"):
            seam.ledger.append({**where, "outcome": MARKED, "judgment": record["record_id"], "by": "server"})

    @staticmethod
    def _rule_card(rule: Mapping[str, Any]) -> dict[str, Any]:
        """What every rule card shows of its rule (#card-parts): target, its home clause on the page's own lane, which
        the source link opens (#card-source), verbatim text, the mark's name, home, its repo spec path and anchor,
        as the warning ledger and the rules checked name it (#mark-word), and served, whether the page's own lane
        serves the home spec: when it does not, the source is plain text, since no page serves that copy."""
        return {"target": rule["target"], "text": rule["state"]["criterion"], "name": rule["name"], "home": rule["clause"],
                "served": rule["served"]}

    def _scope_item(self, scope: Mapping[str, Any], anchor: str | None, state: str,
                    record: Mapping[str, Any] | None) -> dict[str, Any]:
        """A candidate item (#mark-candidate): the criterion quoted in full; id its anchor on its own spec, None on
        another spec, where it is data for the agent read only."""
        item = {"kind": RULE_CANDIDATE, "id": anchor, "target": scope["target"], "text": scope["state"]["criterion"],
                "state": state, "label": RULE_CANDIDATE if state == "label" else None,
                "record": record.get("record_id") if record and state == "label" else None}
        if state == "label":
            item["level"], item["agent_level"] = MARK_LEVELS[RULE_CANDIDATE]["human"], MARK_LEVELS[RULE_CANDIDATE]["agent"]
        elif state == "pending":
            item["escalated"] = _escalated(record)
        return item

    def rule_items(self, current: bytes, old: bytes | None, path: str, base: str, revision: Any,
                   served: Any, seam: "JevSeam | None" = None, project: Any = None, spec: str | None = None,
                   read: tuple[Any, int] | None = None, *, source: Callable[[], Any]
                   ) -> tuple[list[dict[str, Any]], list[str], dict[str, dict[str, Any]]]:
        """Rule and candidate items for a page, the rules checked, and the marked rules, by home, for marks that cite
        them (#marks, #pending, #card-variants).

        A rule is a criterion its home spec marks (#declare); only the marked rules of the rule source, the specs
        source gives (#q-rule-source), are checked, each named by its home, its repo spec path and anchor (#mark-word),
        and only on a spec that differs from its compared base (#q-rule-def). A served spec's
        mark is traced marked (#q-trace).
        The page's own unmarked criteria are asked the scope question; one Jev reads as every feature is a
        candidate until marked or answered Not a project rule, flagged and never checked (#q-candidate). Other
        specs' candidates are listed from held answers only. Answers come from held records; misses are asked in
        the background and show as pending, escalated true once the general LLM has the question: a rule's always,
        since the rules the spec text has no record for are one general LLM ask (#q-rule-llm). The rules checked are
        the answered ones (#q-rule-def). Not for this spec hides a rule on this repo spec path (#dismiss). spec is
        this spec's repo path."""
        seam = seam or self.seam
        sets = seam.question_sets
        mark = self._built("mark", current)
        if not {"scope", "rule"} <= set(sets) or mark is None:
            return [], [], {}
        checking = old != current
        rel = spec or path
        own = self._build("scope", [{"path": path, "spec": rel, "source": current}])
        others = self._build("scope", served)
        ruled = source()
        rules = [scope for scope in self._build("scope", ruled) if scope["name"]]
        cites = {rule["clause"]: self._rule_card(rule) for rule in rules}
        for scope in own + others:
            if scope["name"]:
                self._marked(seam, project, scope)

        def final(question: Mapping[str, Any]) -> dict[str, Any] | None:
            state, record = self._rule_held(question, seam)
            return record if state == "final" else None

        items: list[dict[str, Any]] = []
        for scope in own:
            if scope["name"] or self._settled(seam, project, scope):
                continue
            state, record = self._rule_held(scope, seam)
            if state == "pending":
                self._ask_submit(scope, seam, read)
            if state != "final":
                items.append(self._scope_item(scope, scope["id"], state, record))
            elif _confident_label(record) == RULE_SCOPE:
                items.append(self._scope_item(scope, scope["id"], "label", record))
        for scope in others:
            record = None if scope["name"] else final(scope)
            if _confident_label(record) == RULE_SCOPE and not self._settled(seam, project, scope):
                items.append(self._scope_item(scope, None, "label", record))

        checked: list[str] = []
        if not checking:
            return items, checked, cites
        waiting = []
        for rule, question in zip(rules, self._build("rule", rules, current, path, base, revision, mark)):
            # Each rule's own record for this spec text; the rules checked are the answered ones (#q-rule-def).
            state, record = self._rule_held(question, seam)
            if state == "final":
                checked.append(rule["clause"])
            # The human's Not for this spec, on this spec path (#dismiss-where): never shown, never asked. An owner's
            # resolution at the miss's where, the same where, leaves it checked, so a second one is resolved already.
            if self._not_here(seam.ledger, self._rule_where(project, rel, rule["clause"])):
                continue
            if state == "pending":
                waiting.append(question)
            label = _confident_label(record) if state == "final" else None
            item = {"kind": "rule", "id": mark, **cites[rule["clause"]], "word": rule["word"],
                    "state": "label" if label == RULE_MISSED else ("none" if state == "final" else state),
                    "label": RULE_MISSED if label == RULE_MISSED else None,
                    "record": record.get("record_id") if record and state == "final" else None}
            if item["label"] in MARK_LEVELS:
                item["level"], item["agent_level"] = MARK_LEVELS[item["label"]]["human"], MARK_LEVELS[item["label"]]["agent"]
            if state == "pending":
                item["escalated"] = True  # the rule check waits on the general LLM alone (#pending-rule)
            items.append(item)
        if waiting:
            self._rule_submit(waiting, seam, read)
        return items, sorted(set(checked)), cites

    def _rules(self, relative: str, base: str, facts: Mapping[str, Any], seam: "JevSeam | None" = None,
               mount: Mapping[str, Any] | None = None, read: tuple[Any, int] | None = None) -> tuple[list[dict[str, Any]], list[str], dict[str, dict[str, Any]]] | None:
        """The read's rule items, rules checked, and cited rule cards; None when they cannot be built, which shows
        no rules and decides no fix."""
        try:
            return self.rule_items(facts["current"], facts["old"], relative, base, facts["head"], facts["served"], seam,
                                   (mount or {}).get("project"), facts["rel"], read,
                                   source=lambda: self._rule_source(mount or {}, facts))
        except Exception:
            return None

    def dismiss(self, mount: Mapping[str, Any], target: str, action: Any, home: Any = None,
                record_id: Any = None, anchor: Any = None) -> bool:
        """The human's answer on a rule or candidate card, for the page's project, one outcome in the warning ledger
        (#dismiss, jev-suggestions #ledger). Not for this spec (action here) on a rule card, or on a mark at anchor
        citing the rule, names the rule's home (repo spec path and anchor) and the note's record: dismissed for this
        repo spec path, unconfirmed until audited. Not a project rule (action rule) names the page spec's unmarked
        criterion at anchor that Jev reads as every feature: not-a-rule citing its scope record, a confirmed label.
        A candidate catch-up passes over (action ignored, repair-specs #catch-up-table) is the owner's ignored citing
        its scope record, unconfirmed, settling nothing; a settled candidate is never ignored. False when the action is neither or names what is not held here: the one check of an answer."""
        project = mount.get("project")
        seam = self._seam_for(mount)
        if not project or not isinstance(action, str) or "scope" not in seam.question_sets:
            return False
        rel = os.path.relpath(target, mount["root"]).replace(os.sep, "/")
        if action == "here":
            path, _, clause = home.partition("#") if isinstance(home, str) else ("", "", "")
            if not path or not clause or not isinstance(record_id, str) or not record_id:
                return False
            seam.ledger.append({**self._rule_where(str(project), rel, home, anchor if isinstance(anchor, str) else None),
                                "outcome": "dismissed", "judgment": record_id, "by": "human"})
            return True
        if action not in ("rule", IGNORED) or not isinstance(anchor, str):
            return False
        try:
            current = Path(target).read_bytes()
        except OSError:
            return False
        scope = next((scope for scope in self._build("scope", [{"path": rel, "spec": rel, "source": current}])
                      if scope["id"] == anchor and not scope["name"]), None)
        record = seam.cached(scope) if scope else None
        if _confident_label(record) != RULE_SCOPE:
            return False
        if action == IGNORED:
            if self._settled(seam, project, scope):
                return False
            seam.ledger.append({**self._candidate_where(project, scope), "outcome": IGNORED,
                                "judgment": record["record_id"], "by": "owner"})
            return True
        seam.ledger.append({**self._candidate_where(project, scope), "outcome": NOT_A_RULE,
                            "judgment": record["record_id"], "by": "human"})
        return True

    def accepted(self, mount: Mapping[str, Any], target: str) -> None:
        """Accept spec with a candidate still unsettled: one ignored outcome per open candidate of the spec, citing
        its scope record (#dismiss-trace). Reads held answers only; asks nothing."""
        project = mount.get("project")
        seam = self._seam_for(mount)
        if not project or not self.enabled or "scope" not in seam.question_sets:
            return
        rel = os.path.relpath(target, mount["root"]).replace(os.sep, "/")
        for scope in self._build("scope", [{"path": rel, "spec": rel, "source": Path(target).read_bytes()}]):
            record = seam.cached(scope)
            if not scope["name"] and _confident_label(record) == RULE_SCOPE and not self._settled(seam, project, scope):
                seam.ledger.append({**self._candidate_where(project, scope), "outcome": IGNORED,
                                    "judgment": record["record_id"], "by": "human"})

    @staticmethod
    def _rule_where(project: Any, rel: str, home: Any, anchor: Any = None) -> dict[str, Any]:
        """Where the human's Not for this spec on a rule card sits: this spec path and the rule's home, kind rule
        (project-rules #dismiss-here), and for a mark citing the rule, the flagged clause (#card-cites-reject). A rule
        miss holds for its whole spec, so this, at no anchor, is its own where too (#ledger-subject)."""
        return {"project": project, "spec": rel, "anchor": anchor, "kind": "rule", "target": home, "text": None}

    @staticmethod
    def _not_here(ledger: WarningLedger, where: Mapping[str, Any]) -> bool:
        """The human's Not for this spec holds at this rule card's where: the rule does not apply there."""
        resolved = ledger.resolved(where)
        return bool(resolved and resolved["by"] == "human")

    def _hidden(self, ledger: WarningLedger, item: Mapping[str, Any], where: Mapping[str, Any]) -> bool:
        """An item neither the page nor the agent read lists: at a resolved where, through every later answer Jev gives
        it (#ledger-resolved), or a mark citing a rule the human's Not for this spec answers at its clause
        (project-rules #card-cites-reject), whatever its kind."""
        return bool(ledger.resolved(where)) or ("rule" in item and self._not_here(
            ledger, self._rule_where(where["project"], where["spec"], item["rule"]["home"], item["id"])))

    @staticmethod
    def _item_where(project: Any, rel: str, item: Mapping[str, Any], repo: Mapping[str, str],
                    other: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Where an item sits in the warning ledger (#ledger-subject): the one where a read hides a resolved mark by
        and an owner's outcome writes, so each mark has one where. A rule miss sits by its rule's home alone, where its
        card's Not for this spec writes it (_rule_where). Any other mark, one citing a rule included, sits at its
        anchor by the kind the agent read prints, its label, and by the clause it cites as the repository has it, never
        a served path: for a lane mark the other lane's changed clause (other) with the identity of that lane's text of
        it, else the spec the target names (repo maps each served path, this spec's own included, to its repo path)."""
        if item["kind"] == "rule":
            return JevService._rule_where(project, rel, item.get("home"))
        cited, text = item.get("target"), None
        if other is not None:
            cited, text = other["spec"] + "#" + other["anchor"], "sha256:" + _content_digest(other["after"])
        elif isinstance(cited, str) and "#" in cited:
            path, _, anchor = cited.partition("#")
            cited = repo[path] + "#" + anchor
        return {"project": project, "spec": rel, "anchor": item.get("id"), "kind": item.get("label") or item["kind"],
                "target": cited, "text": text}

    @staticmethod
    def _cited(item: Mapping[str, Any]) -> Any:
        """The clause a mark's agent read line names, the one copy of it: GET /api/jev puts it on each Important mark as
        cited and resolve matches on it. A rule by its home (project-rules #mark-word), a rule miss's own or the rule
        card a mark citing it carries, never its served target; any other mark its target."""
        card = item if item.get("kind") == "rule" else item.get("rule")
        return card.get("home") if isinstance(card, Mapping) else item.get("target")

    def resolve(self, mount: Mapping[str, Any], target: str, relative: str, base: str, events: list[Mapping[str, Any]],
                view: str = "", served_mounts: Any = None, base_commit: str | None = None, *, mark: Any, status: Any,
                reason: Any = None) -> str | None:
        """An owner's resolution of an Important mark through the agent read (#ledger-resolve, #record-resolution):
        fixed, reason, or dismissed with its reason, the mark named as the read's line lists it: anchor, cited clause,
        a rule by its home (project-rules #mark-word), and record. Taken when the link's read of its spec and base, the
        one GET /api/jev makes, lists that mark unresolved under that record, after a restart too and for every mark
        kind: one outcome in the warning ledger at the mark's where, citing the record, which is unchanged, unconfirmed
        until audited (#measure-feedback). None when taken, else why it is refused, one reason each."""
        reason = " ".join(reason.split()) if isinstance(reason, str) else ""
        if status not in OWNER_RESOLUTIONS:
            return "%s is none of the owner outcomes: %s" % (status, ", ".join(OWNER_RESOLUTIONS))
        if status == "dismissed" and not reason:
            return "a dismissal names no reason"
        anchor, cited, record_id = mark
        marks = self._read(mount, target, relative, base, events, view, served_mounts, base_commit)[0] \
            if self.enabled else []
        ledger = self._seam_for(mount).ledger
        at = [(item, where) for item, where in marks if item.get("agent_level") == "important"
              and item.get("id") == anchor and (self._cited(item) or "") == (cited or "")]
        named = next(((item, where) for item, where in at if item.get("record") == record_id), None)
        with ledger.lock:  # the check and the outcome as one, so a mark ends once however many resolve it at once
            if named and not self._hidden(ledger, *named):
                ledger.append({**named[1], "outcome": status, "judgment": record_id, "by": "owner",
                               "reason": reason or None})
                return None
            listed = next((item["record"] for item, where in at if not self._hidden(ledger, item, where)), None)
        if named or (at and listed is None):
            return "the mark is resolved already"
        if listed is not None:
            return "the read lists that mark under %s" % listed
        return "the read lists no Important mark at %s citing %s" % (anchor, cited or "no clause")

    def _fixed(self, seam: "JevSeam", mount: Mapping[str, Any], facts: Mapping[str, Any],
               marks: list[tuple[dict[str, Any], dict[str, Any]]], homes: Any = ()) -> None:
        """A spec fix clears an Important mark on the next head (#record-resolution, #acceptance-resolution-fixed):
        a mark, by its where, shown on a head of this spec and base, and absent from a settled read of a later head,
        is fixed in the warning ledger citing its record, unconfirmed until audited. marks are every item the read made,
        each with its where, resolved ones too, so a hidden mark is never read as cleared. A read with any question
        pending, unavailable, or oversize decides nothing; a mark gone because its rule is no project rule now (homes
        holds the marked rules' homes), or resolved (#ledger-resolved), is not fixed: the server's own fixed resolves
        nothing and never displaces an owner's or human's outcome."""
        spec = (mount.get("root"), mount.get("test"), facts["rel"], facts.get("base_commit"))
        head = facts.get("head")
        shown = {tuple(where[field] for field in LEDGER_WHERE): item["record"]
                 for item, where in marks if item.get("agent_level") == "important" and item.get("record")}
        with self._marks_lock:
            last_head, last = self._marks.get(spec, (head, {}))
            if head == last_head or any(item.get("state") in UNDECIDED for item, _ in marks):
                self._marks[spec] = (last_head, {**last, **shown})
                return
            self._marks[spec] = (head, shown)
        for key, record_id in last.items():
            where = dict(zip(LEDGER_WHERE, key))
            retired = where["kind"] == "rule" and where["target"] not in homes
            if key not in shown and not retired and not seam.ledger.resolved(where):
                seam.ledger.append({**where, "outcome": "fixed", "judgment": record_id, "by": "server"})

    def response(self, mount: Mapping[str, Any], target: str, relative: str, base: str,
                 events: list[Mapping[str, Any]], view: str = "", served_mounts: Any = None,
                 base_commit: str | None = None) -> dict[str, Any]:
        """GET /api/jev: the read of the spec at target against base, every hidden item left out, so neither the page
        nor the agent read lists it (#ledger-resolved, _hidden). Each Important mark carries cited, the name its agent
        read line prints and a resolution takes (_cited), so the server alone works that name out."""
        if not self.enabled:
            return {"jev": "off", "items": [], "levels": dict(MARK_LEVELS)}
        marks, answer = self._read(mount, target, relative, base, events, view, served_mounts, base_commit)
        ledger = self._seam_for(mount).ledger
        return {**answer, "items": [{**item, "cited": self._cited(item)} if item.get("agent_level") == "important"
                                    else item for item, where in marks if not self._hidden(ledger, item, where)]}

    def _read(self, mount: Mapping[str, Any], target: str, relative: str, base: str, events: list[Mapping[str, Any]],
              view: str, served_mounts: Any,
              base_commit: str | None) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
        """The one read of a spec and base, which GET /api/jev lists and an owner's resolution checks (#ledger-resolve):
        every item, hidden ones too, each with its where (_item_where), and the rest of the answer. Inputs that cannot
        be read fail the whole read."""
        read = ((target, base, view), next(self._reads))  # this page's reader: its next read supersedes this one
        self._reading(read[0], 1)  # while it runs, its reader still wants its waiting asks (#ask-queue)
        try:
            return self._response(read, mount, target, relative, base, events, view, served_mounts, base_commit)
        finally:
            self._reading(read[0], -1)

    def _response(self, read: tuple[Any, int], mount: Mapping[str, Any], target: str, relative: str, base: str,
                  events: list[Mapping[str, Any]], view: str, served_mounts: Any,
                  base_commit: str | None) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
        seam = self._seam_for(mount)
        facts = self._read_facts(mount, target, base, served_mounts, base_commit)
        ruled = self._rules(relative, base, facts, seam, mount, read)
        rule_items, rules, cites = ruled or ([], [], {})
        # Each served path's repo path (#ledger-subject): the where of each item, and the home a cited clause is.
        project, rel = mount.get("project"), facts["rel"]
        repo = {relative: rel, **{entry["path"]: entry["spec"] for entry in facts["served"]}}
        asked = self.questions(mount, target, relative, base, events, view, served_mounts, base_commit, facts)
        questions = [question for question in asked
                     if all(step["kind"] in seam.question_sets for step in question.get("chain", [question]))]
        misses: dict[str, dict[str, Any]] = {}

        def answer(question):
            if question["kind"] == "coverage" and (question.get("story") is None or question.get("criterion") is None):
                return {"outcome": "shown", "answer": {"label": "unrelated"}, "record_id": None}
            if "chain" in question:
                result = chain_result(question["chain"], lambda step: self._held_record(seam.key(step), seam))
                if result["outcome"] is None:
                    misses.setdefault(seam.key(result["step"]), dict(question))
                    return None
                return result
            return self._held(question, misses, seam)

        answers = [(question, answer(question)) for question in questions]
        self._ask_misses(misses, seam, read)
        items = []
        for question, record in answers:
            if record is None:
                items.append({"kind": question["kind"], "id": question["id"], "state": self._unasked(), "label": None,
                              "target": question.get("target"), "record": None})
                continue
            outcome = record.get("outcome")
            if outcome == "oversize":
                items.append({"kind": question["kind"], "id": question["id"], "state": "oversize"})
                continue
            answer = record.get("answer", {})
            label = answer.get("label") if isinstance(answer, Mapping) else None
            allowed = set(question.get("display_labels", question.get("criteria", {}))) or set(seam.question_set(question["kind"]).criteria())
            if question["kind"] == "resolved":
                allowed = {"resolved in spirit"}
            if question["kind"] == "orphan":
                anchors = {str(anchor): str(name) for name, anchor in question.get("candidate_anchors", {}).items()}
                allowed = set(anchors)
            if question["kind"] == "orphan" and outcome == "shown" and label not in allowed:
                state = "none"
                label = None
            else:
                state = "label" if outcome == "shown" and label and label in allowed else ("unsure" if outcome == "unsure" else "unavailable")
            if outcome == "shown" and (not label or label not in allowed):
                state = "none"  # a chain that ends in nothing, or a label with nothing to show
            target_anchor = question.get("target")
            if question["kind"] == "orphan" and state == "label":
                target_anchor, label = label, anchors[label]
            item = {"kind": question["kind"], "id": question["id"], "state": state,
                    "label": label if state == "label" else None,
                    "target": target_anchor, "record": record.get("record_id")}
            if state == "label" and question["kind"] != "orphan" and label in MARK_LEVELS:
                item["level"], item["agent_level"] = MARK_LEVELS[label]["human"], MARK_LEVELS[label]["agent"]
            # A draft-check mark whose other clause is a marked rule is a rule card (project-rules #card-variants):
            # gone after Not for this spec on this spec path and clause (#card-cites-reject, _hidden).
            # A Partial overlap never cites a rule: it is no card-cites label and has no button (#level-partial).
            cited_path, _, cited_anchor = str(target_anchor).partition("#")
            cited = (cites.get(repo.get(cited_path, "") + "#" + cited_anchor)
                     if question["kind"] == "corpus" and state == "label" and label != "partial-overlap" else None)
            if cited:
                item["rule"] = dict(cited)
            # Answers Jev was unsure of, settled by the general LLM, behind a note the page shows: counted by the agent
            # read only (#markers-agent-unsure). A coverage pair shows no note of its own; the gap is derived.
            unsure = record.get("unsure", int(outcome == "shown" and bool(record.get("escalated"))))
            if unsure and state == "label" and question["kind"] != "coverage":
                item["unsure"] = unsure
            items.append(item)
        items += rule_items
        marks = [(item, self._item_where(project, rel, item, repo)) for item in items]
        if ruled is not None:
            try:
                self._fixed(seam, mount, facts, marks, {card["home"] for card in cites.values()})
            except OSError:
                pass  # a fixed outcome unwritten is a fix missed, never a failed read
        marks = [(item, where) for item, where in marks if item["state"] != "oversize"]
        marks += [(item, self._item_where(project, rel, item, repo, other))
                  for item, other in self._lane_items(mount, served_mounts, read)]
        self._supersede(read)
        return marks, {"jev": "on", "rules": rules, "levels": dict(MARK_LEVELS), **self.paused()}


__all__ = ["BUILDERS", "CHAIN_CONTINUE", "KeyPause", "REFUSED", "REFUSED_PAUSE", "VERIFIED", "VERIFIER_REJECTED", "DRAFT_CHECK_KINDS", "DEFAULT_MAX_INPUT_TOKENS", "DEFAULT_THRESHOLD", "JevSeam", "JevService", "JudgmentStore", "WarningLedger", "eval_read", "MARK_LEVELS", "MODEL", "OWNER_RESOLUTIONS",
           "OPENROUTER_DECISIONS_URL", "mount_prefix", "served_path", "OpenRouterProvider", "QuestionSet", "SILENT_NO",
           "anchor_context",
           "build_audience_questions", "build_board_conflict_questions", "build_corpus_questions", "build_coverage_questions", "build_orphan_questions", "build_resolved_questions", "build_rule_questions", "build_scope_questions", "build_type_questions", "chain_result", "changed_leaf_clauses", "draft_check",
           "extract_anchors", "lane_result", "lane_shortlist",
           "load_question_sets", "material", "rule_mark_anchor", "rule_word", "scope_eval", "spec_text"]
