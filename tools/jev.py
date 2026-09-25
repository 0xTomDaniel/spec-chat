"""Small server-side Jev seam used by review-serve."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL = "typesafe/jev-1.13"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_THRESHOLD = 0.6
DEFAULT_MAX_INPUT_TOKENS = 32000
RETRYABLE_OUTCOMES = frozenset({"off", "unavailable"})
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

MINIMAL_QUESTION_SETS = {
    "type": {
        "id": "type", "version": 1,
        "instructions": "Classify the kind of change in the compared text.",
        "labels": [
            {"name": "scope", "description": "Adds, removes, or moves a capability, surface, or non-goal."},
            {"name": "behavioral", "description": "Alters what the result does: a rule, interface, edge case, or criterion."},
            {"name": "clarification", "description": "States the same behavior more precisely."},
            {"name": "cosmetic", "description": "Changes wording, order, or style without changing meaning."},
        ],
        "threshold": DEFAULT_THRESHOLD,
    },
    "orphan": {
        "id": "orphan", "version": 1,
        "instructions": "Choose the current section that best matches the orphaned comment quote.",
        "labels": [], "threshold": DEFAULT_THRESHOLD,
    },
    "resolved": {
        "id": "resolved", "version": 1,
        "instructions": "Decide whether the revised section addresses the open comment in spirit.",
        "labels": [
            {"name": "resolved in spirit", "description": "The revised text does what the comment asked."},
            {"name": "unrelated", "description": "The revised text does not address the comment."},
        ],
        "threshold": DEFAULT_THRESHOLD,
    },
    "coverage": {
        "id": "coverage", "version": 1,
        "instructions": "For one user story and one acceptance criterion, decide whether the criterion verifies the story.",
        "labels": [
            {"name": "verifies", "description": "The acceptance criterion directly verifies the user story."},
            {"name": "unrelated", "description": "The acceptance criterion does not verify the user story."},
            {"name": "unsure", "description": "There is not enough evidence to decide whether the criterion verifies the story."},
        ],
        "threshold": DEFAULT_THRESHOLD,
    },
}


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


class QuestionSet:
    def __init__(self, identifier: str, version: Any, instructions: str = "", labels: Any = None,
                 threshold: float = DEFAULT_THRESHOLD):
        self.id = identifier
        self.version = version
        self.instructions = instructions
        self.labels = [] if labels is None else labels
        self.threshold = threshold

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
        return cls(identifier, version, str(raw.get("instructions", "")), labels, threshold)

    def criteria(self) -> dict[str, str]:
        if isinstance(self.labels, Mapping):
            return {str(k): str(v.get("description", v) if isinstance(v, Mapping) else v) for k, v in self.labels.items()}
        result = {}
        for label in self.labels:
            if isinstance(label, Mapping):
                name = label.get("name", label.get("id"))
                if name:
                    description = str(label.get("description", ""))
                    examples = label.get("examples", [])
                    if isinstance(examples, list) and examples:
                        description += " Examples: " + "; ".join(str(item) for item in examples)
                    result[str(name)] = description
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "version": self.version, "instructions": self.instructions,
                "labels": _jsonable(self.labels), "threshold": self.threshold}


def load_question_sets(*directories: str | Path | None) -> dict[str, QuestionSet]:
    result = {kind: QuestionSet.from_mapping(raw, kind) for kind, raw in MINIMAL_QUESTION_SETS.items()}
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


class JudgmentStore:
    """Append-only JSONL records, also used as the cache."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.by_key: dict[str, dict[str, Any]] = {}
        self.lock = threading.RLock()
        if self.path:
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
                    current = self.by_key.get(record["cache_key"])
                    if current is None or current.get("outcome") in RETRYABLE_OUTCOMES:
                        self.by_key[record["cache_key"]] = record

    def get(self, key: str) -> dict[str, Any] | None:
        with self.lock:
            return self.by_key.get(key)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(record)
        with self.lock:
            current = self.by_key.get(record["cache_key"])
            if current is not None and current.get("outcome") not in RETRYABLE_OUTCOMES:
                return current
            self.by_key[record["cache_key"]] = record
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            return record


class OpenRouterProvider:
    def __init__(self, api_key: str, *, endpoint: str = OPENROUTER_DECISIONS_URL, timeout: float = 15.0):
        self._api_key = api_key.strip()
        self.endpoint = endpoint
        self.timeout = timeout

    def decide(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = Request(self.endpoint,
                          data=json.dumps(_jsonable(payload), ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                          method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("OpenRouter decision request failed") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError("OpenRouter decision was not an object")
        return value


def _parse_provider_answer(value: Mapping[str, Any], question_name: str) -> tuple[str, float | None, dict[str, float], str]:
    answers = value.get("answers", value)
    if not isinstance(answers, Mapping):
        raise RuntimeError("provider response has no answers")
    answer = answers.get(question_name)
    if not isinstance(answer, Mapping):
        answer = next((item for item in answers.values() if isinstance(item, Mapping)), None)
    if not isinstance(answer, Mapping):
        raise RuntimeError("provider response has no typed answer")
    label = answer.get("choice", answer.get("label", answer.get("class")))
    probabilities = answer.get("probabilities", answer.get("probs", {}))
    if not isinstance(probabilities, Mapping):
        probabilities = {}
    probabilities = {str(k): float(v) for k, v in probabilities.items() if isinstance(v, (int, float))}
    confidence = answer.get("confidence", answer.get("score"))
    if confidence is None and probabilities:
        confidence = max(probabilities.values())
    if not isinstance(label, str) or not label.strip():
        if probabilities:
            label = max(probabilities, key=probabilities.get)
        else:
            raise RuntimeError("provider response has no label")
    confidence = None if confidence is None else float(confidence)
    if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise RuntimeError("provider response has invalid confidence")
    model = str(value.get("model", MODEL))
    return label.strip(), confidence, probabilities, model


class JevSeam:
    def __init__(self, question_sets: Mapping[str, QuestionSet] | None = None, *, provider: Any = None,
                 api_key: str | None = None, record_store: JudgmentStore | None = None,
                 clock: Callable[[], str] = _now, max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS):
        self.question_sets = dict(question_sets or load_question_sets())
        self.api_key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip()
        self.provider = provider if provider is not None else (OpenRouterProvider(self.api_key) if self.api_key else None)
        self.store = record_store or JudgmentStore()
        self.clock = clock
        self.max_input_tokens = max_input_tokens

    def question_set(self, kind: str) -> QuestionSet:
        return self.question_sets.get(kind) or self.question_sets.get("default") or QuestionSet.from_mapping(MINIMAL_QUESTION_SETS["type"], "type")

    def _record(self, key: str, kind: str, qset: QuestionSet, sources: Any, revision: Any,
                answer: Mapping[str, Any], outcome: str, model: str = MODEL) -> dict[str, Any]:
        return self.store.append({
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
        })

    def ask(self, question: Mapping[str, Any]) -> dict[str, Any]:
        kind = str(question.get("kind", "type"))
        qset = self.question_set(kind)
        inputs = {"state": question.get("state", {}),
                  "criteria": question.get("criteria", {}),
                  "question_set": {"id": qset.id, "version": qset.version}}
        key = "sha256:" + hashlib.sha256(_canonical(inputs).encode("utf-8")).hexdigest()
        cached = self.store.get(key)
        if cached and cached.get("outcome") not in RETRYABLE_OUTCOMES:
            return cached
        criteria = qset.criteria()
        dynamic = question.get("criteria")
        if isinstance(dynamic, Mapping):
            criteria = {str(k): str(v) for k, v in dynamic.items()}
        payload = {"model": MODEL, "questions": {kind: {"criteria": criteria,
                    "instructions": qset.instructions, "type": "choice"}},
                   "state": _jsonable(question.get("state", {}))}
        if (len(_canonical(payload)) + 3) // 4 > self.max_input_tokens:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "oversize")
        if not self.api_key or self.provider is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "off")
        answer = None
        model = MODEL
        for _ in range(2):
            try:
                if hasattr(self.provider, "decide"):
                    response = self.provider.decide(payload)
                else:
                    response = self.provider(payload)
                label, confidence, probabilities, model = _parse_provider_answer(response, kind)
                answer = {"label": label, "probabilities": probabilities, "confidence": confidence}
                break
            except Exception:
                continue
        if answer is None:
            return self._record(key, kind, qset, question.get("sources", []), question.get("revision"),
                                {"label": None, "probabilities": {}, "confidence": None}, "unavailable", model)
        outcome = "shown" if answer["confidence"] is not None and answer["confidence"] >= qset.threshold else "unsure"
        return self._record(key, kind, qset, question.get("sources", []), question.get("revision"), answer, outcome, model)


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.values: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        _close_implied(self.stack, tag)
        if tag in BLOCK_TEXT_SEPARATORS and self.stack:
            self.handle_data(" ")
        attrs = dict(attrs)
        anchor = attrs.get("data-anchor")
        if anchor:
            parent = next((item[1] for item in reversed(self.stack) if item[1]), None)
            value = self.values.setdefault(anchor, {
                "text": [],
                "section": tag == "section" or attrs.get("data-spec-section") is not None,
                "story": "data-user-story" in attrs,
                "criterion": "data-acceptance-criterion" in attrs,
                "parent": parent,
                "children": [],
                "boundary": "data-modular-boundary" in attrs,
            })
            if parent and anchor not in self.values[parent].setdefault("children", []):
                self.values[parent]["children"].append(anchor)
            self.stack.append((tag, anchor))
        elif tag not in VOID_ELEMENTS and self.stack:
            self.stack.append((tag, self.stack[-1][1]))
        elif tag == "br":
            self.handle_data(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        _close_explicit(self.stack, tag)

    def handle_data(self, data: str):
        if any(tag in {"script", "style"} for tag, _ in self.stack):
            return
        seen = set()
        for _, anchor in self.stack:
            if anchor and anchor not in seen:
                self.values[anchor]["text"].append(data)
                seen.add(anchor)


def extract_anchors(source: str | bytes | None) -> dict[str, dict[str, Any]]:
    parser = _AnchorParser()
    parser.feed((source or b"").decode("utf-8", "replace") if isinstance(source, bytes) else (source or ""))
    for value in parser.values.values():
        value["text"] = " ".join("".join(value["text"]).split())
    return parser.values


def _anchor_words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _corpus_leaf_anchors(anchors: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return [key for key, value in anchors.items() if not value.get("children")]


def _match_score(source: str, target: str) -> tuple[int, int]:
    source_words, target_words = _anchor_words(source), _anchor_words(target)
    overlap = len(source_words & target_words)
    union = len(source_words | target_words)
    return overlap, int((overlap * 1000) / union) if union else 0


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

    result = []
    for anchor in leaves:
        after = str(now[anchor].get("text", ""))
        before = str(old.get(anchor, {}).get("text", ""))
        if before == after:
            continue

        own_keys = [key for key in _corpus_leaf_anchors(now) if key != anchor]
        own_ranked = _candidate_sort(after, now, own_keys)
        non_goals = [key for key in own_ranked if _is_non_goal(key, now)]
        own_keys = own_ranked[:8]
        for key in non_goals:
            if key not in own_keys:
                own_keys.append(key)
        for key in own_ranked:
            if now[key].get("boundary") and key not in own_keys:
                own_keys.append(key)
        candidates: list[tuple[str, str, str, bool]] = [
            (key, key, str(now[key].get("text", "")), bool(now[key].get("boundary")))
            for key in own_keys
        ]

        cross: list[tuple[int, int, str, str, str, bool]] = []
        for other_path, anchors in other_specs:
            for key in _corpus_leaf_anchors(anchors):
                text = str(anchors[key].get("text", ""))
                overlap, ratio = _match_score(after, text)
                cross.append((-overlap, -ratio, other_path, key, text, bool(anchors[key].get("boundary"))))
        cross.sort()
        selected = cross[:8]
        # Same-surface modular boundaries remain candidates even when lexical
        # ranking would otherwise put them just outside the cross-spec limit.
        selected_keys = {(item[2], item[3]) for item in selected}
        for item in cross:
            if item[5] and (item[2], item[3]) not in selected_keys:
                selected.append(item)
                selected_keys.add((item[2], item[3]))
        candidates.extend((key, other_path + "#" + key, text, boundary)
                          for _, _, other_path, key, text, boundary in selected)

        for candidate, target, target_text, boundary in candidates:
            question = _question("corpus", anchor,
                                 {"before": before, "after": after, "target": target_text,
                                  "target_boundary": boundary},
                                 path, base, revision, target)
            question["sources"] = [path + "#" + anchor, target]
            question["display_labels"] = ["contradicts", "overlaps", "oversteps"]
            result.append(question)
    return result


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
        result.append(_question("type", anchor, {"before": before, "after": after}, path, base, revision, anchor))
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
        candidates = sorted(anchors, key=lambda item: (-len(wanted & words[item]), item))[:8]
        criteria = {item: str(anchors[item].get("text", ""))[:400] for item in candidates}
        result.append(_question("orphan", str(thread["id"]), {"quote": quote, "candidates": candidates}, path, base, revision,
                                criteria=criteria))
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


class _LeafAnchorParser(HTMLParser):
    """Collect text for anchored elements without anchored descendants."""

    _containers = {"article", "div", "figure", "footer", "header", "main", "nav", "ol", "section", "table", "tbody", "thead", "tfoot", "ul"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None]] = []
        self.values: dict[str, dict[str, Any]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        _close_implied(self.stack, tag)
        if tag in BLOCK_TEXT_SEPARATORS and self.stack:
            self.handle_data(" ")
        anchor = dict(attrs).get("data-anchor")
        parent = next((item[1] for item in reversed(self.stack) if item[1] is not None), None)
        if anchor:
            anchor = str(anchor)
            if parent and parent in self.values:
                self.values[parent]["has_child"] = True
            self.values.setdefault(anchor, {"text": [], "tag": tag, "has_child": False})
            self.stack.append((tag, anchor))
            return
        if tag not in VOID_ELEMENTS and self.stack:
            self.stack.append((tag, self.stack[-1][1]))
        elif tag == "br":
            self.handle_data(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str):
        _close_explicit(self.stack, tag)

    def handle_data(self, data: str):
        if any(tag in {"script", "style"} for tag, _ in self.stack):
            return
        anchor = self.stack[-1][1] if self.stack else None
        if anchor and anchor in self.values:
            self.values[anchor]["text"].append(data)


def _audience_leaf_anchors(source: str | bytes | None) -> dict[str, dict[str, Any]]:
    parser = _LeafAnchorParser()
    parser.feed((source or b"").decode("utf-8", "replace") if isinstance(source, bytes) else (source or ""))
    result = {}
    for anchor, value in parser.values.items():
        if value["has_child"] or value["tag"] in _LeafAnchorParser._containers:
            continue
        text = " ".join("".join(value["text"]).split())
        if text:
            result[anchor] = {"text": text, "tag": value["tag"]}
    return result


def build_audience_questions(current: str | bytes, path: str = "spec", base: str = "",
                             revision: Any = "head") -> list[dict[str, Any]]:
    result = []
    for anchor, value in _audience_leaf_anchors(current).items():
        result.append(_question("audience", anchor,
                                {"clause": value["text"], "reader": "someone who uses the result, not builds it"},
                                path, base, revision, anchor))
    return result


BUILDERS = {"type": build_type_questions, "orphan": build_orphan_questions, "resolved": build_resolved_questions,
            "coverage": build_coverage_questions, "audience": build_audience_questions,
            "corpus": build_corpus_questions}


def default_state_dir() -> Path:
    root = os.environ.get("XDG_STATE_HOME")
    return Path(root) / "spec-chat" / "jev" if root else Path.home() / ".local" / "state" / "spec-chat" / "jev"


class JevService:
    def __init__(self, *, state_dir: str | Path | None = None, provider: Any = None, api_key: str | None = None,
                 question_dirs: list[str | Path] | None = None):
        self.api_key = (api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")).strip()
        self.provider = provider
        self.question_sets = load_question_sets(*(question_dirs or []))
        path = Path(state_dir) / "records.jsonl" if state_dir else default_state_dir() / "records.jsonl"
        self.seam = JevSeam(self.question_sets, provider=provider, api_key=self.api_key,
                            record_store=JudgmentStore(path))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key) or self.provider is not None

    def _served_specs(self, mounts: Any, current: str) -> list[dict[str, str]]:
        result = []
        seen = set()
        for mount in ([mounts] if isinstance(mounts, Mapping) else (mounts or [])):
            prefix = str(mount.get("slug", "")) if isinstance(mount, Mapping) else ""
            for relative, filename in enumerate_served_specs(mount):
                filename = os.path.realpath(filename)
                if filename == os.path.realpath(current) or filename in seen or not os.path.isfile(filename):
                    continue
                seen.add(filename)
                served_path = (prefix + "/" if prefix else "") + relative
                try:
                    result.append({"path": served_path, "source": Path(filename).read_bytes()})
                except OSError:
                    continue
        return result

    def _message_sources(self, root: str, relative: str, base: str,
                         events: list[Mapping[str, Any]]) -> dict[str, bytes]:
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
                try:
                    commit = subprocess.check_output(
                        ("git", "-C", root, "log", "-1", "--before=" + created,
                         "--format=%H", "--end-of-options", "HEAD", "--", relative),
                        stderr=subprocess.DEVNULL, text=True,
                    ).strip()
                except (OSError, subprocess.CalledProcessError):
                    commit = ""
            if not isinstance(commit, str) or not commit.strip():
                commit = base
            if not isinstance(commit, str) or not commit.strip():
                continue
            try:
                result[str(identifier)] = subprocess.check_output(
                    ("git", "-C", root, "show", "--end-of-options", commit + ":" + relative),
                    stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.CalledProcessError):
                continue
        return result

    def questions(self, mount: Mapping[str, Any], target: str, relative: str, base: str,
                  events: list[Mapping[str, Any]], view: str = "", served_mounts: Any = None) -> list[dict[str, Any]]:
        if served_mounts is None and not isinstance(view, str):
            served_mounts, view = view, ""
        current = Path(target).read_bytes()
        root = mount["root"]
        rel = os.path.relpath(target, root).replace(os.sep, "/")
        try:
            head = subprocess.check_output(("git", "-C", root, "rev-parse", "HEAD"), stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            head = "working-tree"
        try:
            old = subprocess.check_output(("git", "-C", root, "show", base + ":" + rel), stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            old = None
        message_sources = self._message_sources(root, rel, base, events)
        def build(kind: str, *args: Any) -> list[dict[str, Any]]:
            try:
                value = BUILDERS[kind](*args)
                return value if isinstance(value, list) else []
            except Exception:
                return []

        result = build("type", current, old, relative, base, head)
        result.extend(build("orphan", events, current, relative, base, head))
        result.extend(build("resolved", events, current, old, relative, base, head, message_sources))
        result.extend(build("coverage", current, relative, base, head))
        result.extend(build("corpus", current, old, relative, base, head,
                            self._served_specs(served_mounts or [mount], target)))
        if view == "reading":
            result.extend(build("audience", current, relative, base, head))
        return result

    def response(self, mount: Mapping[str, Any], target: str, relative: str, base: str,
                 events: list[Mapping[str, Any]], view: str = "", served_mounts: Any = None) -> dict[str, Any]:
        if served_mounts is None and not isinstance(view, str):
            served_mounts, view = view, ""
        if not self.enabled:
            return {"jev": "off", "items": []}
        questions = self.questions(mount, target, relative, base, events, view, served_mounts)

        def answer(question):
            if question["kind"] == "coverage" and (question.get("story") is None or question.get("criterion") is None):
                return question, {"outcome": "shown", "answer": {"label": "unrelated"}, "record_id": None}
            return question, self.seam.ask(question)

        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(answer, questions))
        items = []
        for question, record in answers:
            outcome = record.get("outcome")
            if outcome == "oversize":
                continue
            answer = record.get("answer", {})
            label = answer.get("label") if isinstance(answer, Mapping) else None
            allowed = set(question.get("display_labels", question.get("criteria", {}))) or set(self.seam.question_set(question["kind"]).criteria())
            if question["kind"] == "resolved":
                allowed = {"resolved in spirit"}
            state = "label" if outcome == "shown" and label and label in allowed else ("unsure" if outcome == "unsure" else "unavailable")
            if outcome == "shown" and label and label not in allowed:
                state = "none"
            target_anchor = question.get("target")
            if question["kind"] == "orphan" and state == "label" and label in question.get("criteria", {}):
                target_anchor = label
            items.append({"kind": question["kind"], "id": question["id"], "state": state,
                          "label": label if state == "label" else None,
                          "target": target_anchor, "record": record.get("record_id")})
        return {"jev": "on", "items": items}


__all__ = ["BUILDERS", "DEFAULT_MAX_INPUT_TOKENS", "DEFAULT_THRESHOLD", "JevSeam", "JevService", "JudgmentStore", "MODEL",
           "MINIMAL_QUESTION_SETS", "OPENROUTER_DECISIONS_URL", "OpenRouterProvider", "QuestionSet",
           "build_audience_questions", "build_corpus_questions", "build_coverage_questions", "build_orphan_questions", "build_resolved_questions", "build_type_questions", "extract_anchors",
           "load_question_sets"]
