"""Small server-side Jev seam used by review-serve."""

from __future__ import annotations

import hashlib
import json
import math
import os
import posixpath
import re
import subprocess
import threading
import time
import tomllib
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL = "typesafe/jev-1.13"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
# Its own budget: the general LLM reasons before its label (up to ~40 s live), past Jev's 15 s decisions.
GENERAL_LLM_TIMEOUT = 90.0
DEFAULT_THRESHOLD = 0.4
DEFAULT_MAX_INPUT_TOKENS = 32000
RETRYABLE_OUTCOMES = frozenset({"off", "unavailable"})
# Draft-check question kinds that carry provenance fields (#record-inputs, #record-pairing).
DRAFT_CHECK_KINDS = frozenset({"contradicts", "contradicts-nongoal", "oversteps", "overlaps"})
# The one retry rule (jev-suggestions #state-error): a failed ask is asked again after a pause, the provider's
# given wait, else this many seconds; the pause is written on its record, so no worker waits it out.
RETRY_PAUSE = 60.0
_wall = time.time
# Jev was below threshold on a set with a general LLM fallback (project-rules #q-fallback): the LLM decides next.
ESCALATED = "escalated"
REPLACEABLE_OUTCOMES = RETRYABLE_OUTCOMES | {ESCALATED}
# Draft-check P(yes) below the verify cutoff (jev-suggestions #asymmetric-silent): recorded, no mark, no escalation.
SILENT_NO = "silent-no"
# Outcomes that let a chain continue: the question was answered (#chains, #asymmetric).
CHAIN_CONTINUE = frozenset({"shown", SILENT_NO})
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


class ProviderWait(RuntimeError):
    """A provider failure that says how long to wait before asking again (a rate-limit Retry-After)."""

    def __init__(self, message: str, wait: float):
        super().__init__(message)
        self.wait = wait


class QuestionSet:
    def __init__(self, identifier: str, version: Any, instructions: str = "", labels: Any = None,
                 threshold: float = DEFAULT_THRESHOLD, fallback: bool = False,
                 show_cutoff: float | None = None, verify_cutoff: float | None = None):
        self.id = identifier
        self.fallback = fallback
        self.version = version
        self.instructions = instructions
        self.labels = [] if labels is None else labels
        self.threshold = threshold
        self.show_cutoff = show_cutoff
        self.verify_cutoff = verify_cutoff

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], fallback_id: str) -> "QuestionSet":
        identifier = str(raw.get("id", fallback_id)).strip()
        version = raw.get("version", 1)
        labels = raw.get("labels", [])
        threshold = float(raw.get("threshold", DEFAULT_THRESHOLD))
        if not identifier or not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError("invalid question set")
        if not isinstance(labels, (list, dict)):
            raise ValueError("question set labels must be an array or object")
        fallback = raw.get("fallback", False)
        if not isinstance(fallback, bool):
            raise ValueError("question set fallback is true or false; sets name no model")
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
        return cls(identifier, version, str(raw.get("instructions", "")), labels, threshold, fallback,
                   show_cutoff, verify_cutoff)

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


def rule_identity(text: str) -> str:
    """What a dismissal holds for in place of the rule's criterion text, so no record names it (project-rules #dismiss-scope)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# The human's two rule-note dismissals (project-rules #dismiss): the resolution each records.
DISMISSALS = {"here": "dismissed", "rule": "not-a-rule"}


class JudgmentStore:
    """Append-only JSONL records, also used as the cache.

    The file loads on a background thread so a server start never waits on its size (review-service
    #service-restart); every read and write takes the lock, so the first use waits for the load instead."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._by_key: dict[str, dict[str, Any]] = {}
        # Every rule dismissal ever recorded, by what it holds for, never by cache key (project-rules #dismiss-scope).
        self._dismissals: set[tuple[str, str, str, str | None]] = set()
        self.lock = threading.RLock()
        self._loaded = not self.path
        if self.path:
            threading.Thread(target=self._load, daemon=True, name="spec-chat-jev-store").start()

    @property
    def by_key(self) -> dict[str, dict[str, Any]]:
        self._load()
        return self._by_key

    @property
    def dismissals(self) -> set[tuple[str, str, str, str | None]]:
        self._load()
        return self._dismissals

    def _load(self) -> None:
        with self.lock:
            if self._loaded:
                return
            self._loaded = True
            try:
                if not self.path.is_file():
                    return
                lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return
            for line in lines:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(record, Mapping) and record.get("cache_key"):
                    record = dict(record)
                    self._hold(record.get("resolution"))
                    current = self._by_key.get(record["cache_key"])
                    if current is None or current.get("outcome") in REPLACEABLE_OUTCOMES or "resolution" in record:
                        self._by_key[record["cache_key"]] = record

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            return self.by_key.get(key)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(record)
        with self.lock:
            current = self.by_key.get(record["cache_key"])
            if current is not None and current.get("outcome") not in REPLACEABLE_OUTCOMES:
                return current
            self.by_key[record["cache_key"]] = record
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            return record

    def _hold(self, resolution: Any) -> None:
        if isinstance(resolution, Mapping) and resolution.get("status") in DISMISSALS.values() and resolution.get("project"):
            self._dismissals.add((resolution["status"], str(resolution["project"]), str(resolution.get("rule")),
                                 resolution.get("spec")))

    def dismissed(self, status: str, project: Any, text: str, spec: str | None = None) -> bool:
        """Whether the project dismissed this rule text: on this spec path (dismissed), or everywhere (not-a-rule)."""
        with self.lock:
            return bool(project) and (status, str(project), rule_identity(text), spec) in self.dismissals

    def by_id(self, record_id: str) -> dict[str, Any] | None:
        with self.lock:
            return next((record for record in self.by_key.values() if record.get("record_id") == record_id), None)

    def resolve(self, record_id: str, resolution: str, reason: str | None = None,
                confirmed: bool | None = None, **holds: Any) -> dict[str, Any] | None:
        """Set resolution and confirmed on an existing record (#record-resolution, #record-confirmed).

        A 'fixed' resolution auto-confirms as a true positive (#measure-feedback). holds names what a rule
        dismissal holds for (project-rules #dismiss-scope). The amended record is appended to the JSONL so it
        survives a restart."""
        with self.lock:
            for key, record in self.by_key.items():
                if record.get("record_id") != record_id:
                    continue
                updated = dict(record)
                res: dict[str, Any] = {"status": resolution, **holds}
                if reason:
                    res["reason"] = reason
                updated["resolution"] = res
                self._hold(res)
                if confirmed is not None:
                    updated["confirmed"] = confirmed
                elif resolution == "fixed":
                    updated["confirmed"] = True
                self.by_key[key] = updated
                if self.path:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(updated, ensure_ascii=False, sort_keys=True) + "\n")
                return updated
        return None

    def confirmed_records(self) -> list[dict[str, Any]]:
        """All confirmed records, the labels the eval replay scores (#measure-feedback)."""
        with self.lock:
            return [dict(r) for r in self.by_key.values() if r.get("confirmed")]


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
        """One call, never a wait: a 429 raises its given wait for the record's pause (jev-suggestions #state-error)."""
        data = json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8")
        request = Request(endpoint, data=data,
                          headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                          method="POST")
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            wait = given_wait(exc.headers.get("Retry-After") if exc.headers else None) if exc.code == 429 else None
            exc.close()
            if wait is None:
                raise RuntimeError("OpenRouter decision request failed") from exc
            raise ProviderWait("OpenRouter rate limited", wait) from exc
        except (URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("OpenRouter decision request failed") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError("OpenRouter decision was not an object")
        return value


class ClaudeCliProvider:
    """One-shot isolated claude -p subprocess for general LLM fallback (jev-seam RULE 09).

    Runs --bare --settings (isolated: no CLAUDE.md, no hooks, no user context;
    auth via apiKeyHelper in settings.json) with --json-schema for structured output
    and --output-format json for the envelope. The envelope's ``result`` field carries
    the model's text, parsed as the answer JSON."""

    def __init__(self, *, command: str = "claude", timeout: float = GENERAL_LLM_TIMEOUT,
                 settings_path: str | None = None):
        self.command = command
        self.timeout = timeout
        self.settings_path = settings_path or str(Path.home() / ".claude" / "settings.json")

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
            result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                    timeout=self.timeout, cwd="/tmp")
            if result.returncode != 0:
                raise RuntimeError("claude CLI exited %d" % result.returncode)
            envelope = json.loads(result.stdout)
            text = str(envelope.get("result", ""))
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("claude CLI timed out") from exc
        except (OSError, ValueError) as exc:
            raise RuntimeError("claude CLI failed") from exc
        # Extract the actual model from the CLI envelope (modelUsage key, #ac-record).
        actual = model
        usage = envelope.get("modelUsage")
        if isinstance(usage, dict) and usage:
            actual = str(next(iter(usage))) or model
        return {"choices": [{"message": {"content": text}}], "model": actual}


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


class JevSeam:
    def __init__(self, question_sets: Mapping[str, QuestionSet] | None = None, *, provider: Any = None,
                 api_key: str | Callable[[], str] | None = None, llm_model: str | Callable[[], str] | None = None,
                 record_store: JudgmentStore | None = None,
                 clock: Callable[[], str] = _now, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS):
        self.question_sets = dict(question_sets or load_question_sets())
        self.api_key = _key_reader(api_key)
        self.llm_model = _model_reader(llm_model)
        self._provider = provider
        self.store = record_store or JudgmentStore()
        self.clock = clock
        self.max_input_tokens = max_input_tokens
        self._key_locks: dict[str, threading.Lock] = {}
        self._key_locks_lock = threading.Lock()

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
        return ClaudeCliProvider()

    def _record(self, key: str, kind: str, qset: QuestionSet, sources: Any, revision: Any,
                answer: Mapping[str, Any], outcome: str, model: str = MODEL, escalated: bool = False,
                failure: BaseException | None = None, inputs: Mapping[str, Any] | None = None,
                pairing: Mapping[str, Any] | None = None, verifier: Mapping[str, Any] | None = None) -> dict[str, Any]:
        extra: dict[str, Any] = {"escalated": True, "source": "llm"} if escalated else {"source": "jev"}
        if outcome == "unavailable":
            wait = getattr(failure, "wait", None)
            extra["retry_at"] = _instant(_wall() + (RETRY_PAUSE if wait is None else wait))
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
        qset = self.question_set(str(question.get("kind", "type")))
        inputs = {"state": question.get("state", {}),
                  "criteria": question.get("criteria", {}),
                  "question_set": {"id": qset.id, "version": qset.version}}
        return "sha256:" + hashlib.sha256(_canonical(inputs).encode("utf-8")).hexdigest()

    def cached(self, question: Mapping[str, Any]) -> dict[str, Any] | None:
        """The record already held for this question, never asking."""
        record = self.store.get(self.key(question))
        return record if record and record.get("outcome") not in REPLACEABLE_OUTCOMES else None

    def _lock(self, key: str) -> threading.Lock:
        with self._key_locks_lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def ask(self, question: Mapping[str, Any]) -> dict[str, Any]:
        """One answer per key at a time: a concurrent ask of the same inputs waits and reuses the record."""
        key = self.key(question)
        with self._lock(key):
            return self._ask(question, key)

    def _ask(self, question: Mapping[str, Any], key: str) -> dict[str, Any]:
        kind = str(question.get("kind", "type"))
        qset = self.question_set(kind)
        held = self.store.get(key)
        if held and held.get("outcome") not in REPLACEABLE_OUTCOMES:
            return held
        # Provenance (#record-inputs, #record-pairing, #record-verifier)
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        verifier_info = question.get("verifier") if kind in DRAFT_CHECK_KINDS else None
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
        payload = {"model": MODEL, "questions": {kind: {"criteria": criteria,
                    "instructions": instructions, "type": "choice"}},
                   "state": _jsonable(question.get("state", {}))}
        if (len(_canonical(payload)) + 3) // 4 > self.max_input_tokens:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "oversize",
                                inputs=inputs, pairing=pairing)
        if provider is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "off",
                                inputs=inputs, pairing=pairing)
        answer = None
        failure = None
        model = MODEL
        try:
            response = provider.decide(payload) if hasattr(provider, "decide") else provider(payload)
            label, confidence, probabilities, model = _parse_provider_answer(response, kind)
            answer = {"label": label, "probabilities": probabilities, "confidence": confidence}
            if isinstance(question.get("candidate_anchors"), Mapping):
                answer = _anchor_answer(answer, question["candidate_anchors"])
        except Exception as exc:
            failure = exc
        if answer is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "unavailable", model,
                                failure=failure, inputs=inputs, pairing=pairing, verifier=verifier_info)
        if qset.show_cutoff is not None:
            # Asymmetric threshold for draft-check questions (#asymmetric).
            p_yes = answer["probabilities"].get("yes")
            if p_yes is None:
                p_yes = answer.get("confidence", 0.0) if answer.get("label") == "yes" else 0.0
            if p_yes >= qset.show_cutoff:
                return self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, "shown", model,
                                    inputs=inputs, pairing=pairing, verifier=verifier_info)
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
                            inputs=inputs, pairing=pairing, verifier=verifier_info)

    def general_payload(self, question: Mapping[str, Any], qset: QuestionSet, model: str) -> dict[str, Any]:
        """One allowed label by construction: a strict enum schema, served only by providers that enforce it."""
        labels = qset.criteria()
        system = {"question": qset.instructions, "labels": labels, "examples": qset.examples()}
        schema = {"type": "object", "properties": {"choice": {"type": "string", "enum": list(labels)}},
                  "required": ["choice"], "additionalProperties": False}
        return {"model": model,
                "messages": [{"role": "system", "content": _canonical(system)},
                             {"role": "user", "content": _canonical(question.get("state", {}))}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
                "provider": {"require_parameters": True}}

    def _general(self, question: Mapping[str, Any], key: str, qset: QuestionSet, provider: Any) -> dict[str, Any]:
        """The same question to the box's general LLM, one call like Jev; its answer decides (project-rules #q-fallback)."""
        kind = str(question.get("kind", "type"))
        sources, revision = question.get("sources", []), question.get("revision")
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        fallback = self._fallback_provider(provider)
        # The general LLM always runs through the CLI (#rule-cli-only);
        # unconfigured llm_model is "" so the CLI picks its own default.
        model = self.llm_model()
        payload = self.general_payload(question, qset, model)
        label = None
        failure = None
        try:
            response = fallback.complete(payload)
            model = str(response.get("model", "")) or model
            label = json.loads(response["choices"][0]["message"]["content"])["choice"]
        except Exception as exc:
            failure = exc
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
        system = {"question": qset.instructions,
                  "verify": "If yes, quote the exact span from the clause and the target that conflict. If no, leave spans empty."}
        schema = {"type": "object",
                  "properties": {"answer": {"type": "string", "enum": ["yes", "no"]},
                                 "clause_span": {"type": "string"}, "target_span": {"type": "string"}},
                  "required": ["answer", "clause_span", "target_span"], "additionalProperties": False}
        return {"model": model,
                "messages": [{"role": "system", "content": _canonical(system)},
                             {"role": "user", "content": _canonical(verify_input)}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "strict": True, "schema": schema}},
                "provider": {"require_parameters": True}}

    def _verify(self, question: Mapping[str, Any], key: str, qset: QuestionSet, provider: Any) -> dict[str, Any]:
        """Draft-check verify band (#verifier): call general LLM with both clauses; show only if yes + quoted spans."""
        kind = str(question.get("kind", "type"))
        sources, revision = question.get("sources", []), question.get("revision")
        inputs = _extract_inputs(question) if kind in DRAFT_CHECK_KINDS else None
        pairing = question.get("pairing") if kind in DRAFT_CHECK_KINDS else None
        fallback = self._fallback_provider(provider)
        model = self.llm_model()
        if isinstance(fallback, ClaudeCliProvider) and not self._raw_llm_model():
            model = ""
        payload = self.verifier_payload(question, qset, model)
        label = None
        failure = None
        try:
            response = fallback.complete(payload)
            result = json.loads(response["choices"][0]["message"]["content"])
            clause_span = (result.get("clause_span") or "").strip()
            target_span = (result.get("target_span") or "").strip()
            if result.get("answer") == "yes" and clause_span and target_span:
                label = "yes"
            else:
                label = "no"
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
        return self._record(key, kind, qset, sources, revision, answer, "shown", model, True,
                            inputs=inputs, pairing=pairing)

    def ask_chain(self, chain: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Ask a chain's yes/no questions in order, each only when the answer so far needs it (#chains)."""
        return chain_result(chain, self.ask)

    def answer(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """One item's answer: its chain's, else its one question's record."""
        return self.ask_chain(item["chain"]) if "chain" in item else self.ask(item)


def _chain_label(record: Mapping[str, Any]) -> str | None:
    """The label the chain walks on: a silent-no is a no for chain purposes (#asymmetric)."""
    if record.get("outcome") == SILENT_NO:
        return "no"
    answer = record.get("answer")
    return answer.get("label") if isinstance(answer, Mapping) else None


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


def _rarity_score(source_words: set[str], target_words: set[str], freqs: dict[str, int]) -> float:
    """Rarity-weighted overlap: each shared word weighted by 1/log(1+doc_freq) (#candidate-ranking)."""
    shared = source_words & target_words
    if not shared:
        return 0.0
    return sum(1.0 / math.log(1 + freqs.get(word, 1)) for word in shared)


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
            directories[:] = sorted(
                name for name in directories
                if not name.startswith(".") and not name.endswith(".review")
                and name.lower() not in {"evidence", "evidence-bundle", "evidence-bundles",
                                         "fixture", "fixtures", "support", "supports"}
            )
            for name in sorted(names):
                if name.startswith(".") or not name.endswith(".spec.html"):
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


TOPK_SAME = 5
TOPK_CROSS = 5


def build_corpus_questions(current: str | bytes, baseline: str | bytes | None, path: str = "spec", base: str = "",
                           revision: Any = "head", served_specs: Any = None) -> list[dict[str, Any]]:
    """Build one corpus question for each changed leaf and candidate clause."""
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
        own_keys = [key for key in _corpus_leaf_anchors(now) if key != anchor]
        # Rank same-spec candidates by rarity-weighted overlap.
        own_scored = sorted(own_keys, key=lambda k: (-_rarity_score(source_words, _anchor_words(str(now[k].get("text", ""))), freqs), k))
        non_goals = [key for key in own_scored if _is_non_goal(key, now)]
        own_keys = own_scored[:TOPK_SAME]
        for key in non_goals:
            if key not in own_keys:
                own_keys.append(key)
        candidates = [(key, str(now[key].get("text", "")), key in non_goals, now) for key in own_keys]

        # Rank cross-spec candidates by rarity-weighted overlap.
        cross: list[tuple[float, str, str, str, bool, dict]] = []
        for other_path, other_anchors in other_specs:
            for key in _corpus_leaf_anchors(other_anchors):
                text = str(other_anchors[key].get("text", ""))
                score = _rarity_score(source_words, _anchor_words(text), freqs)
                cross.append((-score, other_path, key, text, _is_non_goal(key, other_anchors), other_anchors))
        cross.sort(key=lambda item: (item[0], item[1], item[2]))
        candidates.extend((other_path + "#" + key, text, non_goal, ca) for _, other_path, key, text, non_goal, ca in cross[:TOPK_CROSS])

        ctx = anchor_context(anchor, now)
        for rank_index, (target, target_text, non_goal, target_anchors) in enumerate(candidates):
            tctx = anchor_context(target.split("#")[-1], target_anchors)
            if non_goal:
                rule = "non-goal"
            elif "#" in str(target):
                rule = "cross-spec"
            else:
                rule = "same-spec"
            pair_info = {"rule": rule, "rank": rank_index, "gate": "yes"}
            result.append(draft_check(anchor, before, after, target, target_text, target_non_goal=non_goal,
                                      same_spec=(rule == "same-spec"),
                                      path=path, base=base, revision=revision,
                                      context=ctx, target_context=tctx, pairing=pair_info))
    return result


def draft_check(anchor: str, before: str, after: str, target: str, target_text: str, *,
                target_non_goal: bool = False, same_spec: bool = False, path: str, base: str, revision: Any,
                context: Mapping[str, Any] | None = None, target_context: Mapping[str, Any] | None = None,
                pairing: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The draft-check chain for one clause and candidate (#corpus-labels, #gate-chain): about? gate first,
    then contradicts?, then oversteps?, then overlaps?, the first yes decides; a non-goal candidate is asked
    only contradicts? after the gate."""
    # Gate state: first/second each with context fields (#gate-set-state)
    first_obj: dict[str, Any] = {"text": after}
    if context:
        first_obj.update(context)
    second_obj: dict[str, Any] = {"text": target_text}
    if target_context:
        second_obj.update(target_context)
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
    if target_non_goal or same_spec:
        steps[1]["then"]["no"] = None
    else:
        steps += [_step(_question("oversteps", anchor, pair, path, base, revision, target), yes="oversteps"),
                  _step(_question("overlaps", anchor, pair, path, base, revision, target), yes="overlaps", no=None)]
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
    events = sorted(events, key=lambda item: str(item.get("name", "")))
    threads: dict[str, dict[str, Any]] = {}
    message_to_thread: dict[str, str] = {}
    message_slots: dict[str, tuple[str, int]] = {}
    for event in events:
        body = event.get("body", event)
        actor = event.get("actor", body.get("actor")) if isinstance(body, Mapping) else None
        if not isinstance(body, Mapping):
            continue
        if not isinstance(body.get("id"), str) or any(
                field in body and not isinstance(body[field], str)
                for field in ("threadId", "respondsTo", "anchorId", "supersedes")):
            continue
        event = body.get("event")
        if not isinstance(event, str):
            continue
        if event == "comment" and actor == "human":
            thread = {"id": body.get("id"), "status": "pending", "messages": [body], "anchor": body.get("anchorId")}
            threads[thread["id"]] = thread
            message_to_thread[body.get("id")] = thread["id"]
            message_slots[body["id"]] = (thread["id"], 0)
        elif event in {"reply", "edit", "status"}:
            key = body.get("threadId") or message_to_thread.get(body.get("respondsTo"))
            if not key or key not in threads:
                continue
            thread = threads[key]
            if body.get("event") == "status":
                thread["status"] = body.get("status", thread["status"])
            elif body.get("event") == "edit" and actor == "human":
                prior = message_slots.get(body.get("supersedes"))
                if prior is None or prior[0] != key:
                    continue
                thread["messages"][prior[1]] = body
                thread["anchor"] = body.get("anchorId", thread["anchor"])
                thread["status"] = "pending"
                message_to_thread[body.get("id")] = key
                message_slots[body["id"]] = prior
            else:
                thread["messages"].append(body)
                message_slots[body["id"]] = (key, len(thread["messages"]) - 1)
                if actor == "human":
                    thread["anchor"] = body.get("anchorId", thread["anchor"])
                    thread["status"] = "pending"
                message_to_thread[body.get("id")] = key
    return list(threads.values())


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
    anchors = extract_anchors(current)
    stories = [(anchor, value) for anchor, value in anchors.items() if value.get("story")]
    criteria = [(anchor, value) for anchor, value in anchors.items() if value.get("criterion")]
    result = []
    if stories and not criteria:
        for story_anchor, story in stories:
            result.append({
                "kind": "coverage", "id": story_anchor + "::",
                "state": {"story": {"anchor": story_anchor, "text": story["text"]}, "criterion": None},
                "sources": [f"{path}#{story_anchor}"], "revision": {"base": base, "head": revision},
                "target": story_anchor, "story": story_anchor, "criterion": None,
            })
        return result
    if criteria and not stories:
        for criterion_anchor, criterion in criteria:
            result.append({
                "kind": "coverage", "id": "::" + criterion_anchor,
                "state": {"story": None, "criterion": {"anchor": criterion_anchor, "text": criterion["text"]}},
                "sources": [f"{path}#{criterion_anchor}"], "revision": {"base": base, "head": revision},
                "target": criterion_anchor, "story": None, "criterion": criterion_anchor,
            })
        return result
    for story_anchor, story in stories:
        for criterion_anchor, criterion in criteria:
            identifier = story_anchor + "::" + criterion_anchor
            result.append({
                "kind": "coverage",
                "id": identifier,
                "state": {"story": {"anchor": story_anchor, "text": story["text"]},
                          "criterion": {"anchor": criterion_anchor, "text": criterion["text"]}},
                "sources": [f"{path}#{story_anchor}", f"{path}#{criterion_anchor}"],
                "revision": {"base": base, "head": revision},
                "target": story_anchor,
                "story": story_anchor,
                "criterion": criterion_anchor,
            })
    return result


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


def _linked_criterion(path: str, href: str) -> str | None:
    """A link to a clause of another spec, as that spec's repo path and anchor; None for any other link."""
    file, _, anchor = href.partition("#")
    if not file or not anchor or file.startswith("/") or ":" in file:
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), file))
    return None if target == path else target + "#" + anchor


def build_scope_questions(served_specs: Any) -> list[dict[str, Any]]:
    """One scope question per acceptance criterion of every other spec; the key is its text alone (#q-scope).
    clause names the criterion by repo path, which one project's lanes share whatever slug serves it, and links
    names the other specs' clauses it links in that same form, so copies resolve to their home (#q-copies)."""
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
            question["links"] = [link for link in (_linked_criterion(repo, href) for href in value["links"]) if link]
            result.append(question)
    return result


def rule_homes(scopes: list[Mapping[str, Any]], is_rule: Callable[[Mapping[str, Any]], bool]) -> dict[str, str]:
    """Each rule's home (#q-copies): a rule's links are followed through rules to the criterion that links no
    further rule. A link loop, which no copy should form, has its least target as home, so the loop is one rule."""
    by_target = {scope["clause"]: scope for scope in scopes}
    rules = {target for target, scope in by_target.items() if is_rule(scope)}
    homes: dict[str, str] = {}
    for start in rules:
        chain = [start]
        while True:
            step = next((link for link in by_target[chain[-1]].get("links", ()) if link in rules and link != chain[-1]), None)
            if step is None:
                homes[start] = chain[-1]
                break
            if step in chain:
                homes[start] = min(chain[chain.index(step):])
                break
            chain.append(step)
    return homes


def build_rule_question(scope: Mapping[str, Any], current: str | bytes, path: str, base: str,
                        revision: Any, mark: str) -> dict[str, Any]:
    """Does this spec trigger the rule? then, only if yes, does it cover it? Each sees the rule text and the
    spec's text (#q-rule); triggered and not covered is missed."""
    state = {"rule": scope["state"]["criterion"], "spec": spec_text(current)}
    sources = [path, scope["target"]]
    steps = [_step(_question("triggered", mark, state, path, base, revision, scope["target"]), no=None),
             _step(_question("covered", mark, state, path, base, revision, scope["target"]), yes=None, no=RULE_MISSED)]
    for step in steps:
        step["sources"] = sources
    item = _chain("rule", mark, scope["target"], sources, steps[0]["revision"], steps)
    item["word"] = scope["word"]
    return item


def build_rule_questions(scopes: list[Mapping[str, Any]], current: str | bytes, path: str, base: str,
                         revision: Any, mark: str) -> list[dict[str, Any]]:
    """One rule question per scope, in scope order."""
    return [build_rule_question(scope, current, path, base, revision, mark) for scope in scopes]


BUILDERS = {"type": build_type_questions, "orphan": build_orphan_questions, "resolved": build_resolved_questions,
            "coverage": build_coverage_questions, "audience": build_audience_questions,
            "corpus": build_corpus_questions, "scope": build_scope_questions, "rule": build_rule_questions,
            "mark": rule_mark_anchor}


# Board route (worklane-provider #jev-board): change type answers that quiet the review highlight.
MATERIAL_NO = "no-behavior-change"
MATERIAL_YES = "behavior"
ASK_WORKERS = 8


def changed_leaf_clauses(current: str | bytes, baseline: str | bytes | None) -> list[tuple[str, str, str]]:
    """(anchor, before, after) for each leaf clause whose text differs from the baseline."""
    now, old = extract_anchors(current), extract_anchors(baseline)
    result = []
    for anchor in _corpus_leaf_anchors(now):
        before, after = str(old.get(anchor, {}).get("text", "")), str(now[anchor].get("text", ""))
        if before != after:
            result.append((anchor, before, after))
    return result


def build_board_conflict_questions(clauses: Mapping[str, list[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """One lane chain per unordered pair of changed leaf clauses of two slugs (#cross-lane-pairs).

    clauses maps slug to its changed clauses: {path, anchor, before, after, base}. The lower slug's
    clause is first (#cross-lane-question); each pair is asked once, never within a slug."""
    slugs = sorted(clauses)
    result = []
    for index, left in enumerate(slugs):
        for right in slugs[index + 1:]:
            for a in clauses[left]:
                for b in clauses[right]:
                    result.append(lane_check(left, a, right, b))
    return result


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
    return item


BUILDERS.update({"changed": changed_leaf_clauses, "lane": build_board_conflict_questions})


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
    if outcome != "shown":
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


def _commit(root: str, ref: str) -> str | None:
    if not ref or ref.startswith("-"):
        return None
    value = _git_read(root, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
    return value.decode().strip() if value else None


EXCLUDED_COLLECTION_DIRS = frozenset({"evidence", "evidence-bundle", "evidence-bundles", "fixture", "fixtures",
                                      "support", "supports"})
# Warm-up status: one table per project in Spec Chat's one onboarding.toml (project-rules #bootstrap-status).
ONBOARDING_TABLE = "project"
OFFER_ACTIONS = frozenset({"sent", "dismissed"})


def _main_commit(root: str) -> str | None:
    """Main's commit for a project: origin/main, else a local main (#bootstrap-table)."""
    return next((commit for commit in (_commit(root, ref) for ref in ("origin/main", "main")) if commit), None)


def _collection_paths(root: str, commit: str, collection: str) -> list[str]:
    """Repo-relative spec paths in main's tree under one collection, as the review index would list them."""
    listing = _git_read(root, "ls-tree", "-r", "-z", "--name-only", "--end-of-options", commit, "--",
                        collection if collection not in ("", ".") else ".")
    result = []
    for name in (listing or b"").decode("utf-8", "replace").split("\0"):
        parts = name.split("/")
        if not name.endswith(SPEC_SUFFIX) or parts[-1].startswith("."):
            continue
        if any(part.startswith(".") or part.endswith(".review") or part.lower() in EXCLUDED_COLLECTION_DIRS
               for part in parts[:-1]):
            continue
        result.append(name)
    return result


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, Mapping):
        return "{" + ", ".join(_toml_key(k) + " = " + _toml_value(v) for k, v in value.items() if v is not None) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _toml_key(key: Any) -> str:
    key = str(key)
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else json.dumps(key, ensure_ascii=False)


def dump_toml(document: Mapping[str, Any]) -> str:
    """Top-level keys, then one [table."name"] per entry of each table of tables; None values are left out."""
    lines = [_toml_key(k) + " = " + _toml_value(v) for k, v in document.items()
             if v is not None and not isinstance(v, Mapping)]
    for table, entries in document.items():
        if not isinstance(entries, Mapping):
            continue
        for name, entry in entries.items():
            if isinstance(entry, Mapping):
                lines += ["", "[" + _toml_key(table) + "." + _toml_key(name) + "]"]
                lines += [_toml_key(k) + " = " + _toml_value(v) for k, v in entry.items() if v is not None]
    return "\n".join(lines).lstrip("\n") + "\n"


# Fast reads (jev-suggestions #fast-marks-section): results kept in memory by the content of their inputs.
KEPT_LIMIT = 512


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


class _Kept:
    """Bounded in-memory results beside the held records; nothing is stored and a restart starts empty."""

    def __init__(self, limit: int = KEPT_LIMIT):
        self.limit = limit
        self.values: OrderedDict[Any, Any] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: Any, make: Callable[[], Any]) -> Any:
        """The kept value, or make's; a make that raises keeps nothing and the next get retries."""
        with self.lock:
            if key in self.values:
                self.values.move_to_end(key)
                return self.values[key]
        value = make()
        with self.lock:
            self.values[key] = value
            while len(self.values) > self.limit:
                self.values.popitem(last=False)
        return value


def spec_chat_state() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    return (Path(root) if root else Path.home() / ".local" / "state") / "spec-chat"


def default_state_dir() -> Path:
    return spec_chat_state() / "jev"


class JevService:
    def __init__(self, *, state_dir: str | Path | None = None, provider: Any = None,
                 api_key: str | Callable[[], str] | None = None, llm_model: str | Callable[[], str] | None = None,
                 question_dirs: list[str | Path] | None = None, onboarding_path: str | Path | None = None):
        self.question_sets = load_question_sets(*(question_dirs or []))
        self.state_dir = Path(state_dir) if state_dir else default_state_dir()
        # Spec Chat's one onboarding.toml (review-service #published-onboarding); beside the records under a test state.
        self.onboarding_path = Path(onboarding_path) if onboarding_path else (
            self.state_dir / "onboarding.toml" if state_dir else spec_chat_state() / "onboarding.toml")
        path = self.state_dir / "records.jsonl"
        self.seam = JevSeam(self.question_sets, provider=provider, api_key=api_key, llm_model=llm_model,
                            record_store=JudgmentStore(path))
        # Test store (#test-hosts): a second JudgmentStore for test-flagged slugs, beside the live one.
        test_path = self.state_dir / "records-test.jsonl"
        self.test_seam = JevSeam(self.question_sets, provider=provider, api_key=api_key, llm_model=llm_model,
                                 record_store=JudgmentStore(test_path))

        # Board state (#jev-board-answer): the last answer and the rows the board thread answers next.
        self._board_lock = threading.Lock()
        self._board_rows: list[Any] = []
        self._board_answer: dict[str, Any] = {"jev": "on", "rows": [], "conflicts": []}
        self._board_wake = threading.Event()
        self._board_idle = threading.Event()
        self._board_idle.set()
        self._board_thread: threading.Thread | None = None

        # Background asks (#fast-marks-background): one in flight per question key, shared by every route,
        # rule checks included (project-rules #pending).
        # The pool lives as long as the service: stop() drops queued asks and waits only for in-flight ones.
        self._ask_lock = threading.Lock()
        self._asking: dict[str, Future] = {}
        self._ask_pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=ASK_WORKERS, thread_name_prefix="spec-chat-jev-ask")

        # Bootstrap (project-rules #bootstrap): one background warm-up per project, its table in onboarding.toml.
        self._status_lock = threading.Lock()
        self._warm_started: set[str] = set()
        self._warm_threads: list[threading.Thread] = []

    def stop(self) -> None:
        """Stop asking: later reads ask nothing, queued asks are dropped, in-flight asks finish their record."""
        with self._ask_lock:
            pool, self._ask_pool = self._ask_pool, None
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            self._asking.clear()
        if pool is not None:
            pool.shutdown(wait=True)
        for t in self._warm_threads:
            t.join(timeout=5)
        self._warm_threads.clear()

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
        """A file's content at a resolved commit, read once: it cannot change. A clean miss is kept as absent."""
        def read() -> bytes | None:
            result = _git_run(root, "show", "--end-of-options", commit + ":" + relative)
            return result.stdout if result.returncode == 0 else None
        try:
            return self._kept.get(("at", self._common_dir(root), commit, relative), read)
        except (OSError, subprocess.SubprocessError):
            return None

    def _built(self, kind: str, *args: Any) -> Any:
        """A builder's value, kept by the content of its inputs; kept questions are read only. Raises as the builder does."""
        builder = BUILDERS[kind]
        return self._kept.get(("build", kind, builder, _content_digest(args)), lambda: builder(*args))

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
        return {"current": current, "root": root, "rel": rel, "resolved_head": resolved_head,
                "head": resolved_head or "working-tree", "base_commit": base_commit,
                "old": self._at(root, base_commit, rel) if base_commit else None,
                "served": self._served_specs(served_mounts or [mount], target, mount)}

    @property
    def enabled(self) -> bool:
        return bool(self.question_sets) and bool(self.seam.api_key())

    def _served_specs(self, mounts: Any, current: str, page: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Other specs (#corpus-others): the lane's own served specs as served, plus main's copy of every
        other served spec path of the page's project, once per project and path; another lane's served copy
        and another project's specs are never read."""
        mounts = [mounts] if isinstance(mounts, Mapping) else list(mounts or [])
        slug = str(page.get("slug", "")) if isinstance(page, Mapping) else ""
        if not slug:
            return self._as_served(mounts, current)
        project = page.get("project") or ""
        mounts = [mount for mount in mounts if isinstance(mount, Mapping) and (mount.get("project") or "") == project]
        own = [mount for mount in mounts if mount.get("slug") == slug]
        result = self._as_served(own, current)
        mains: dict[str, str | None] = {}

        def spec_key(mount: Mapping[str, Any], filename: str) -> tuple[str, str]:
            root = str(mount.get("root", ""))
            return self._common_dir(root), os.path.relpath(filename, root).replace(os.sep, "/")

        seen = {spec_key(page, current)}
        for mount in own:
            seen.update(spec_key(mount, filename) for _, filename in enumerate_served_specs(mount))
        for mount in mounts:
            if mount.get("slug") == slug:
                continue
            prefix = str(mount.get("slug", ""))
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
                    result.append({"path": (prefix + "/" if prefix else "") + relative, "spec": key[1], "source": source})
        return result

    def _as_served(self, mounts: list[Any], current: str) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for mount in mounts:
            prefix = str(mount.get("slug", "")) if isinstance(mount, Mapping) else ""
            for relative, filename in enumerate_served_specs(mount):
                filename = os.path.realpath(filename)
                if filename == os.path.realpath(current) or filename in seen or not os.path.isfile(filename):
                    continue
                seen.add(filename)
                served = {"path": (prefix + "/" if prefix else "") + relative}
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
        root, spec = str(row["root"]), str(row["spec"])
        path = row.get("path") or str(row["slug"]) + "/" + spec
        current = Path(row.get("spec_file") or os.path.join(root, *spec.split("/"))).read_bytes()
        return root, spec, str(path), current

    def _material_questions(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Change type questions for every root section changed since the row base (#jev-material)."""
        root, spec, path, current = self._row_parts(row)
        base = _commit(root, str(row.get("base", "")))
        if base is None:
            raise ValueError("row base is not a commit")
        head = _commit(root, "HEAD") or "working-tree"
        return build_type_questions(current, _git_read(root, "show", base + ":" + spec), path, base, head)

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
        now = extract_anchors(current)
        result = []
        for anchor, before, after in self._built("changed", current, old):
            ctx = anchor_context(anchor, now)
            entry: dict[str, Any] = {"path": path, "anchor": anchor, "before": before, "after": after, "base": base}
            if ctx:
                entry["context"] = ctx
            result.append(entry)
        return result

    def board(self, rows: Any) -> dict[str, Any]:
        """GET /api/jev/board (#jev-board-answer): the last answer at once; the board thread refreshes it."""
        if not self.enabled:
            return {"jev": "off", "rows": [], "conflicts": []}
        with self._board_lock:
            self._board_rows = list(rows or ())
            self._board_idle.clear()
            self._board_wake.set()
            if self._board_thread is None:
                self._board_thread = threading.Thread(target=self._board_loop, name="spec-chat-jev-board", daemon=True)
                self._board_thread.start()
            return self._board_answer

    def _board_loop(self) -> None:
        """Answer the latest rows from held records, publish, ask the misses, then publish again."""
        while True:
            self._board_wake.wait()
            with self._board_lock:
                self._board_wake.clear()
                rows = self._board_rows
            try:
                answer, misses = self._board_from_records(rows)
                self._board_answer = answer
                if misses:
                    wait(self._ask_misses(misses))
                    self._board_answer, _ = self._board_from_records(rows)
            except Exception:
                pass
            with self._board_lock:
                if not self._board_wake.is_set():
                    self._board_idle.set()

    def _held(self, question: Mapping[str, Any], misses: dict[str, dict[str, Any]],
              seam: "JevSeam | None" = None) -> dict[str, Any] | None:
        """The held record; a failed one only during its pause (#state-error). A miss is collected."""
        seam = seam or self.seam
        key = seam.key(question)
        record = self._held_record(key, seam)
        if record is None:
            misses.setdefault(key, dict(question))
        return record

    def _held_record(self, key: str, seam: "JevSeam | None" = None) -> dict[str, Any] | None:
        """A held record. `off` is no answer (the key was missing when asked), so it is asked again, and a
        failed one answers only until its pause ends (#state-error)."""
        store = (seam or self.seam).store
        record = store.get(key)
        if record is None or record.get("outcome") == "off":
            return None
        if record.get("outcome") == "unavailable" and not pause_left(record):
            return None
        return record

    def _ask_misses(self, misses: Mapping[str, dict[str, Any]], seam: "JevSeam | None" = None) -> list[Future]:
        """Ask each miss, by key, once in the background; a question in flight or since answered is not asked again."""
        seam = seam or self.seam
        futures = []
        with self._ask_lock:
            if self._ask_pool is None:
                return futures
            for key, question in misses.items():
                if key not in self._asking:
                    # An ask records before it leaves _asking: a key absent from both is truly unasked.
                    if self._held_record(key, seam) is not None:
                        continue
                    self._asking[key] = self._ask_pool.submit(self._ask_quietly, key, question, seam)
                futures.append(self._asking[key])
        return futures

    def _ask_quietly(self, key: str, question: Mapping[str, Any], seam: "JevSeam | None" = None) -> None:
        seam = seam or self.seam
        if question.get("kind") == "lane" and "chain" in question:
            self._asked_quietly((key,), lambda: self._ask_lane_steps(question["chain"], seam))
        elif "chain" in question:
            self._asked_quietly((key,), lambda: seam.ask_chain(question["chain"]))
        else:
            self._asked_quietly((key,), lambda: seam.ask(question))

    def _asked_quietly(self, keys: tuple[str, ...], ask: Callable[[], Any]) -> None:
        """Run a background ask; the keys it holds leave _asking only after it records."""
        try:
            ask()
        except Exception:
            pass
        finally:
            with self._ask_lock:
                for key in keys:
                    self._asking.pop(key, None)

    def _lane_questions(self, rows: Any, *, exclude_test: bool = True) -> list[dict[str, Any]]:
        """Cross-lane questions over every registry row's changed clauses (#cross-lane-clauses), kept by
        content (#fast-marks): a read resolves each repository's target main once and reparses nothing unchanged.
        Test-flagged slugs are excluded from cross-lane checks (#cross-lane-test-host)."""
        if not {"about", "contradicts", "oversteps", "overlaps"} <= set(self.seam.question_sets):
            return []
        clauses: dict[str, list[dict[str, str]]] = {}
        mains: dict[str, str | None] = {}
        for row in rows or ():
            if not isinstance(row, Mapping) or not row.get("slug") or not row.get("spec"):
                continue
            if exclude_test and row.get("test"):
                continue
            try:
                clauses.setdefault(str(row["slug"]), []).extend(self._changed_clauses(row, mains))
            except Exception:
                continue
        try:
            return list(self._built("lane", clauses))
        except Exception:
            return []

    def _lane_items(self, mount: Mapping[str, Any], rows: Any) -> list[dict[str, Any]]:
        """`lane` items on this page's own clauses from held records; misses are asked after answering.
        Test-flagged slugs are excluded (#cross-lane-test-host)."""
        if not mount.get("slug") or not mount.get("spec") or mount.get("test"):
            return []
        own = self._row_parts(mount)[2]

        def final(question: Mapping[str, Any]) -> dict[str, Any] | None:
            state, record = self._rule_held(question)
            return record if state == "final" else None

        items = []
        for chain in self._lane_questions(rows):
            if own not in chain["pair"]:
                continue
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
                    items.append(item)
            # Pending: submit and emit pending items only when no finding yet
            pending_r = next((r for r in results if r.get("outcome") is None), None)
            if pending_r is not None and pending_r.get("step"):
                state_r, record_r = self._rule_held(pending_r["step"])
                if state_r == "pending":
                    self._lane_submit(chain)
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
                        items.append(item)
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

    def _lane_submit(self, chain: Mapping[str, Any], seam: "JevSeam | None" = None) -> None:
        """Ask a lane chain in the background; deduped by its steps' keys."""
        seam = seam or self.seam
        with self._ask_lock:
            if self._ask_pool is None:
                return
            keys = tuple(seam.key(step) for step in chain["chain"])
            if any(k in self._asking for k in keys):
                return
            future = self._ask_pool.submit(self._asked_quietly, keys, lambda: self._ask_lane_steps(chain["chain"], seam))
            for k in keys:
                self._asking[k] = future

    def _board_from_records(self, rows: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        rows = [row for row in rows or () if isinstance(row, Mapping) and row.get("slug") and row.get("spec")
                and not row.get("test")]
        sets = self.seam.question_sets
        misses: dict[str, dict[str, Any]] = {}

        def held_chain(item: Mapping[str, Any]) -> dict[str, Any]:
            # A chain waiting on a step is asked whole, so each answer asks on from it (#chains).
            result = chain_result(item["chain"], lambda step: self.seam.store.get(self.seam.key(step)))
            if result["outcome"] is None:
                misses.setdefault(self.seam.key(result["step"]), dict(item))
            return result

        answer_rows = []
        for row in rows:
            try:
                questions = self._material_questions(row) if "type" in sets else None
            except Exception:
                questions = None
            answer_rows.append({"id": str(row.get("id", "")),
                                "material": "unknown" if questions is None else material([held_chain(q) for q in questions])})
        get_held = lambda step: self.seam.store.get(self.seam.key(step))
        conflict_pairs: set[tuple[str, str]] = set()
        for chain in self._lane_questions(rows):
            results = lane_result(chain["chain"], get_held)
            for r in results:
                if r.get("outcome") is None and "step" in r:
                    misses.setdefault(self.seam.key(r["step"]), dict(chain))
            if any(_confident_label(r) in LANE_FINDINGS for r in results):
                conflict_pairs.add(chain["pair"])
        answer = {"jev": "on", "rows": answer_rows, "conflicts": [{"a": a, "b": b} for a, b in sorted(conflict_pairs)]}
        return answer, misses

    def _rule_task(self, scope: Mapping[str, Any], rule: Mapping[str, Any] | None,
                   seam: "JevSeam | None" = None) -> None:
        seam = seam or self.seam
        if _confident_label(seam.ask(scope)) == RULE_SCOPE and rule is not None:
            seam.ask_chain(rule["chain"])

    def _ask_submit(self, question: Mapping[str, Any], seam: "JevSeam | None" = None) -> None:
        """Ask a single question on the shared pool; a question in flight is not asked again."""
        seam = seam or self.seam
        key = seam.key(question)
        with self._ask_lock:
            if self._ask_pool is None or key in self._asking:
                return
            self._asking[key] = self._ask_pool.submit(self._asked_quietly, (key,), lambda: seam.ask(question))

    def _rule_held(self, question: Mapping[str, Any], seam: "JevSeam | None" = None) -> tuple[str, dict[str, Any] | None]:
        """final, pending (to ask or in flight), or unavailable during its pause, with the record; never asks."""
        seam = seam or self.seam
        key = seam.key(question)
        record = seam.store.get(key)
        with self._ask_lock:
            inflight = key in self._asking
        if _final(record):
            return "final", record
        if inflight or self._held_record(key, seam) is None or record.get("outcome") == ESCALATED:
            return "pending", record
        return "unavailable", record

    def _rule_submit(self, scope: Mapping[str, Any], rule: Mapping[str, Any], seam: "JevSeam | None" = None) -> None:
        """Ask a scope, then its rule chain, on the one ask pool; a rule in flight is not asked again."""
        seam = seam or self.seam
        with self._ask_lock:
            rule_keys = tuple(seam.key(step) for step in rule["chain"])
            last_key = rule_keys[-1]
            if self._ask_pool is None or last_key in self._asking:
                return
            # The task holds only keys no other ask holds, so each ask releases exactly its own.
            keys = tuple(key for key in (seam.key(scope), *rule_keys) if key not in self._asking)
            future = self._ask_pool.submit(self._asked_quietly, keys, lambda: self._rule_task(scope, rule, seam))
            for key in keys:
                self._asking[key] = future

    def rule_items(self, current: bytes, old: bytes | None, path: str, base: str, revision: Any,
                   served: Any, seam: "JevSeam | None" = None, project: Any = None,
                   spec: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        """Rule items for a spec that differs from its compared base, and the rules checked (#marks, #pending).

        Answers come from held records only; misses are asked in the background and show as pending, with
        escalated true once Jev was unsure and the general LLM has the question. A copy is no rule of its own:
        its home is checked instead, and a copy of this spec's own rule is none (#q-copies). spec is this spec's
        repo path, as other specs' links name it. A rule the project dismissed shows nothing: Not here on this
        repo spec path, Dismiss rule everywhere, its copies included, and is then no rule checked (#dismiss)."""
        seam = seam or self.seam
        sets = seam.question_sets
        mark = self._built("mark", current)
        if not {"scope", "triggered", "covered"} <= set(sets) or mark is None or old == current:
            return [], []
        items: list[dict[str, Any]] = []
        rules: list[str] = []
        scopes = self._build("scope", served)
        def final(question: Mapping[str, Any]) -> dict[str, Any] | None:
            state, record = self._rule_held(question, seam)
            return record if state == "final" else None

        page = {"path": path, "spec": spec or path, "source": current}
        corpus = {scope["clause"]: scope for scope in scopes + self._build("scope", [page])}
        homes = rule_homes(list(corpus.values()), lambda scope: _confident_label(final(scope)) == RULE_SCOPE)

        def linked(scope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
            return [corpus[target] for target in scope.get("links", ()) if target in corpus]

        def ask_pending(questions: list[Mapping[str, Any]]) -> None:
            for question in questions:
                if self._rule_held(question, seam)[0] == "pending":
                    self._ask_submit(question, seam)

        for scope, rule in zip(scopes, self._build("rule", scopes, current, path, base, revision, mark)):
            text = scope["state"]["criterion"]
            if seam.store.dismissed(DISMISSALS["rule"], project, text):
                continue
            state, record = self._rule_held(scope, seam)
            if state == "unavailable":
                # An undecided scope is no rule yet: show nothing (non-goals: no scope mark) until its pause ends.
                continue
            unsure = 0
            # A criterion linking others may be a copy (#q-copies): its check waits until they are decided.
            copy_unknown = [question for question in linked(scope) if self._rule_held(question, seam)[0] != "final"]
            decided = state == "final"
            if decided:
                if _confident_label(record) != RULE_SCOPE or homes.get(scope["clause"]) != scope["clause"]:
                    continue
                if copy_unknown:
                    ask_pending(copy_unknown)
                    continue
                rules.append(scope["target"])
                if seam.store.dismissed(DISMISSALS["here"], project, text, spec):
                    continue
                # triggered? then covered?, from held records; a step not final here is pending or unavailable.
                result = chain_result(rule["chain"], final)
                if result["outcome"] == "oversize":
                    continue
                unsure = result["unsure"]
                if result["outcome"] is None:
                    state, record = self._rule_held(result["step"], seam)
                else:
                    record = result
            if state == "pending" and not decided and linked(scope):
                ask_pending([scope, *copy_unknown])
            elif state == "pending":
                self._rule_submit(scope, rule, seam)
            label = _confident_label(record) if state == "final" else None
            item = {"kind": "rule", "id": mark, "target": scope["target"], "word": scope["word"],
                    "text": text,
                    "state": "label" if label == RULE_MISSED else ("none" if state == "final" else state),
                    "label": RULE_MISSED if label == RULE_MISSED else None,
                    "record": record.get("record_id") if record and state == "final" else None}
            if item["label"] in MARK_LEVELS:
                item["level"], item["agent_level"] = MARK_LEVELS[item["label"]]["human"], MARK_LEVELS[item["label"]]["agent"]
            if state == "pending":
                item["escalated"] = _escalated(record)
            if unsure:
                item["unsure"] = unsure
            items.append(item)
        return items, sorted(set(rules))

    def _rules(self, relative: str, base: str, facts: Mapping[str, Any], seam: "JevSeam | None" = None,
               mount: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
        try:
            return self.rule_items(facts["current"], facts["old"], relative, base, facts["head"], facts["served"], seam,
                                   (mount or {}).get("project"), facts["rel"])
        except Exception:
            return [], []

    def _onboarding(self) -> dict[str, Any]:
        """onboarding.toml, empty when missing; raises OSError or ValueError when unreadable, so no write
        ever replaces a file it could not read (install's status and other projects' tables)."""
        try:
            text = self.onboarding_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        return tomllib.loads(text)

    def onboarding_status(self, project: str) -> dict[str, Any] | None:
        """One project's warm-up table in onboarding.toml (#bootstrap-status); None before the first warm-up
        or while the file cannot be read."""
        try:
            table = self._onboarding().get(ONBOARDING_TABLE)
        except (OSError, ValueError):
            return None
        value = table.get(project) if isinstance(table, dict) else None
        return value if isinstance(value, dict) else None

    def _write_status(self, project: str, status: Mapping[str, Any]) -> None:
        """Replace one project's table, keeping install's status and doc and every other project's table."""
        document = self._onboarding()
        table = document.get(ONBOARDING_TABLE)
        document[ONBOARDING_TABLE] = {**(table if isinstance(table, dict) else {}), project: _jsonable(status)}
        path = self.onboarding_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(dump_toml(document), encoding="utf-8")
        os.replace(temporary, path)

    def warm(self, rows: Any) -> list[str]:
        """Start the warm-up for each registered project not yet warmed; never waits (#bootstrap-once).

        A project is warmed once: a done status is never redone. A failed or interrupted warm-up is started
        again on the next server start. With Jev off nothing runs and nothing is written, so no offer shows."""
        if not self.enabled:
            return []
        projects: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows or []:
            if isinstance(row, Mapping) and row.get("project") and row.get("root") and not row.get("test"):
                projects.setdefault(str(row["project"]), []).append(row)
        started = []
        for project, members in projects.items():
            status: dict[str, Any] = {"state": "running",
                                      "started_at": _now(), "criteria_classified": 0, "rules": [],
                                      "specs_to_reconcile": 0, "reconcile": []}
            with self._status_lock:
                if project in self._warm_started:
                    continue
                if (self.onboarding_status(project) or {}).get("state") == "done":
                    self._warm_started.add(project)
                    continue
                try:
                    self._write_status(project, status)
                except (OSError, ValueError):
                    continue  # unreadable onboarding.toml: never overwritten; the next change or start retries
                self._warm_started.add(project)
            t = threading.Thread(target=self._warm_project, args=(project, members, status), daemon=True,
                                name="spec-chat-jev-warm")
            self._warm_threads.append(t)
            t.start()
            started.append(project)
        return started

    def _main_specs(self, rows: list[Mapping[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Main's copy of every spec in the project's collections, once per repo-relative path."""
        root = str(rows[0]["root"])
        commit = _main_commit(root)
        if not commit:
            raise RuntimeError("project has no main")
        paths: list[str] = []
        for row in rows:
            narrow = str(row.get("narrow_root") or row["root"])
            collection = os.path.relpath(narrow, str(row["root"])).replace(os.sep, "/")
            if collection.startswith("../"):
                continue
            paths.extend(path for path in _collection_paths(root, commit, collection) if path not in paths)
        specs = []
        for path in paths:
            source = self._at(root, commit, path)
            if source is not None:
                specs.append({"path": path, "source": source})
        return commit, specs

    def _warm_submit(self, questions: list[Mapping[str, Any]]) -> None:
        """Submit questions on the shared pool, skipping final records but re-asking unavailable ones.
        Chain questions use the first step's key for dedup and finality check."""
        futures: list[Future] = []
        with self._ask_lock:
            if self._ask_pool is None:
                return
            for question in questions:
                if "chain" in question:
                    key = self.seam.key(question["chain"][0])
                else:
                    key = self.seam.key(question)
                if key in self._asking:
                    futures.append(self._asking[key])
                elif not _final(self.seam.store.get(key)):
                    self._asking[key] = self._ask_pool.submit(self._ask_quietly, key, dict(question))
                    futures.append(self._asking[key])
        if futures:
            wait(futures)

    def _warm_project(self, project: str, rows: list[Mapping[str, Any]], status: dict[str, Any]) -> None:
        """Classify every criterion and check every spec of main in parallel, then publish the status
        (#bootstrap-table). Answers are ordinary records, so later pages reuse them without new calls.
        Asks use the shared _ask_pool with its in-flight dedupe; stop() drops queued warm-up asks."""
        try:
            commit, specs = self._main_specs(rows)
            scopes = list(self._built("scope", specs))
            self._warm_submit(scopes)
            decided = [self.seam.store.get(self.seam.key(s)) for s in scopes]
            labels = [_confident_label(record) for record in decided]
            decided_rules = {scope["clause"] for scope, label in zip(scopes, labels) if label == RULE_SCOPE}
            homes = rule_homes(scopes, lambda scope: scope["clause"] in decided_rules)
            rules = [scope for scope in scopes if homes.get(scope["clause"]) == scope["clause"]]
            checks: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            for spec in specs:
                mark = self._built("mark", spec["source"])
                if mark is None:
                    continue
                others = [scope for scope in rules if scope["target"].split("#", 1)[0] != spec["path"]]
                built = self._built("rule", others, spec["source"], spec["path"], commit, commit, mark)
                checks.extend((spec["path"], scope, rule) for scope, rule in zip(others, built))
            self._warm_submit([c[2] for c in checks])
            def _chain_answer(rule: Mapping[str, Any]) -> dict[str, Any] | None:
                if "chain" in rule:
                    return chain_result(rule["chain"], lambda step: self.seam.store.get(self.seam.key(step)))
                return self.seam.store.get(self.seam.key(rule))
            answers = [_chain_answer(c[2]) for c in checks]
            # Done only once every ask ended in a final outcome; an outage or bad key leaves it for the next start.
            unfinished = sum(not _final(record) for record in decided + answers)
            if unfinished:
                raise RuntimeError("%d of %d answers not final" % (unfinished, len(decided) + len(answers)))
            missed: dict[str, list[dict[str, str]]] = {}
            for (path, scope, _), record in zip(checks, answers):
                if _confident_label(record) == RULE_MISSED:
                    missed.setdefault(path, []).append({"target": scope["target"], "word": scope["word"]})
            status.update({
                "state": "done", "main": commit,
                "criteria_classified": sum(label is not None for label in labels),
                "rules": sorted(scope["target"] for scope in rules),
                "specs_to_reconcile": len(missed),
                "reconcile": [{"spec": path, "rules": missed[path]} for path in sorted(missed)],
            })
        except Exception as exc:
            status.update({"state": "failed", "error": str(exc) or type(exc).__name__})
        if self._ask_pool is None:
            return  # stopped: nothing writes after stop returns
        status["finished_at"] = _now()
        with self._status_lock:
            try:
                self._write_status(project, status)
            except (OSError, ValueError):
                pass  # unreadable onboarding.toml is never overwritten; the next start warms again

    def offer(self, project: Any, root: str | None = None) -> dict[str, Any] | None:
        """The one-time reconcile offer (#bootstrap-offer): after warm-up, N above zero, never sent or dismissed.
        A rule the project dismissed is not listed, and a spec missing only such rules is not counted (#dismiss-rule);
        its text is read from the warm-up's main, where the listed home clause lives."""
        status = self.onboarding_status(str(project)) if project else None
        if not status or status.get("state") != "done" or status.get("offer") or not status.get("specs_to_reconcile"):
            return None
        specs = status.get("reconcile", [])
        if root and status.get("main"):
            gone = {target for target in {rule["target"] for entry in specs for rule in entry["rules"]}
                    if self._dismissed_at_main(project, root, status["main"], target)}
            specs = [{**entry, "rules": [rule for rule in entry["rules"] if rule["target"] not in gone]} for entry in specs]
            specs = [entry for entry in specs if entry["rules"]]
        return {"count": len(specs), "specs": specs} if specs else None

    def _dismissed_at_main(self, project: Any, root: str, main: str, target: str) -> bool:
        path = target.partition("#")[0]
        source = self._at(root, main, path)
        scopes = self._build("scope", [{"path": path, "source": source}]) if source is not None else []
        text = next((scope["state"]["criterion"] for scope in scopes if scope["target"] == target), None)
        return text is not None and self.seam.store.dismissed(DISMISSALS["rule"], project, text)

    def dismiss(self, mount: Mapping[str, Any], target: str, action: str, text: str,
                record_id: str | None = None) -> bool:
        """The human's Not here (action here, on the note's rule-check record_id) or Dismiss rule (action rule, on
        the rule's scope record), for the page's project (#dismiss). Held by project, repo spec path for Not
        here, and rule text, never by cache key; unconfirmed until audited (jev-suggestions #measure-feedback).
        False when the action is neither, the text is no rule held here, or the record is no rule check: the one
        check of a dismissal, so the record route only relays it."""
        project = mount.get("project")
        seam = self._seam_for(mount)
        if action not in ("here", "rule") or not project or not isinstance(text, str) or not text \
                or "scope" not in seam.question_sets:
            return False
        scope = seam.store.get(seam.key({"kind": "scope", "state": {"criterion": text}}))
        if _confident_label(scope) != RULE_SCOPE:
            return False
        holds: dict[str, Any] = {"project": str(project), "rule": rule_identity(text)}
        if action == "rule":
            return seam.store.resolve(scope["record_id"], DISMISSALS["rule"], **holds) is not None
        record = seam.store.by_id(record_id) if isinstance(record_id, str) else None
        if not record or record.get("kind") != "covered":
            return False
        holds["spec"] = os.path.relpath(target, mount["root"]).replace(os.sep, "/")
        return seam.store.resolve(record_id, DISMISSALS["here"], **holds) is not None

    def record_offer(self, project: Any, action: str) -> bool:
        """Record the offer sent or dismissed in the project's table; the first record stands."""
        if action not in OFFER_ACTIONS or not project:
            return False
        with self._status_lock:
            status = self.onboarding_status(str(project))
            if not status or status.get("state") != "done":
                return False
            if not status.get("offer"):
                status.update({"offer": action, "offer_at": _now()})
                self._write_status(str(project), status)
        return True

    def response(self, mount: Mapping[str, Any], target: str, relative: str, base: str,
                 events: list[Mapping[str, Any]], view: str = "", served_mounts: Any = None,
                 base_commit: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"jev": "off", "items": [], "levels": dict(MARK_LEVELS)}
        seam = self._seam_for(mount)
        try:
            facts = self._read_facts(mount, target, base, served_mounts, base_commit)
        except Exception:
            facts = None  # no rules; questions reads again and fails as it always has
        rule_items, rules = self._rules(relative, base, facts, seam, mount) if facts else ([], [])
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
        self._ask_misses(misses, seam)
        items = []
        for question, record in answers:
            if record is None:
                items.append({"kind": question["kind"], "id": question["id"], "state": "pending", "label": None,
                              "target": question.get("target"), "record": None})
                continue
            outcome = record.get("outcome")
            if outcome == "oversize":
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
            # Answers Jev was unsure of, settled by the general LLM: counted by the agent read only (#markers-agent-unsure).
            unsure = record.get("unsure", int(outcome == "shown" and bool(record.get("escalated"))))
            if unsure:
                item["unsure"] = unsure
            items.append(item)
        offer = None if mount.get("test") else self.offer(mount.get("project"), mount.get("root"))
        return {"jev": "on", "items": items + rule_items + self._lane_items(mount, served_mounts), "rules": rules,
                "offer": offer, "levels": dict(MARK_LEVELS)}


__all__ = ["BUILDERS", "CHAIN_CONTINUE", "DRAFT_CHECK_KINDS", "DEFAULT_MAX_INPUT_TOKENS", "DEFAULT_THRESHOLD", "JevSeam", "JevService", "JudgmentStore", "MARK_LEVELS", "MODEL",
           "OPENROUTER_DECISIONS_URL", "OpenRouterProvider", "QuestionSet", "SILENT_NO",
           "anchor_context",
           "build_audience_questions", "build_board_conflict_questions", "build_corpus_questions", "build_coverage_questions", "build_orphan_questions", "build_resolved_questions", "build_rule_question", "build_scope_questions", "build_type_questions", "chain_result", "changed_leaf_clauses", "draft_check",
           "extract_anchors", "lane_result",
           "load_question_sets", "material", "rule_homes", "rule_identity", "rule_mark_anchor", "rule_word", "spec_text"]
