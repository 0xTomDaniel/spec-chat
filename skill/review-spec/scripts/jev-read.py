#!/usr/bin/env python3
"""Compact agent read of a review link's Jev checks (project-rules #agent-read).

Reads GET /api/jev for the spec and base the review link serves, from the running server, and prints:
one line per important mark, its anchor, kind, level, the clause the server names it by, its `cited` (for a rule miss
or a mark citing a rule the rule's home, project-rules #mark-word), and record (project-rules #agent-read-example),
warning counts by kind with the count of answers Jev was unsure of and the
general LLM settled (jev-suggestions #markers-agent-unsure), the rules checked, the candidate rules awaiting the
human (project-rules #agent-ask), the pending count; or `off`. While the provider refuses Jev's key, it prints `paused`
with the pause's end and the provider's status, and waits for nothing (project-rules #agent-read-paused).
`--anchor <id>` prints full detail for that anchor only. Marks are keyed on each item's `agent_level` (jev-suggestions #markers-levels-source).
`--resolve <anchor> <clause> <record> <resolution>` records the owner's resolution of an Important mark, named as its
line lists it: fixed, reason, or dismissed with `--reason`. The server takes it when its read of the link's spec and
base lists that mark unresolved under that record, one outcome in the warning ledger citing it, and else prints why it
refuses (jev-suggestions #ledger-resolve), through the one Jev record route.
`--not-a-rule <anchor>` records the human's no to the shaping ask on a candidate of the link's spec: Not a project rule,
not-a-rule in the warning ledger (project-rules #agent-ask, #dismiss-rule). `--not-for-spec <home> <record>` records
Not for this spec on a rule miss the read showed, its home and record from `--anchor`: dismissed for the link's spec
(project-rules #dismiss, repair-specs #catch-up). `--ignored <anchor>` records a candidate the human skipped in
catch-up: ignored in the warning ledger, settling nothing, so it is proposed again (repair-specs #catch-up-table).
All go through the same record route; the server checks each answer.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any


def jev_url(review_url: str) -> str:
    parts = urllib.parse.urlsplit(review_url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not a review link: {review_url}")
    params = {"path": urllib.parse.unquote(parts.path).lstrip("/")}
    base = urllib.parse.parse_qs(parts.query).get("base")
    if base:
        params["base"] = base[0]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/api/jev", urllib.parse.urlencode(params), ""))


def record_url(review_url: str) -> str:
    """The one Jev record route for the link's spec and base (project-rules #dismiss-store), whose read a resolution
    is checked against (jev-suggestions #ledger-resolve)."""
    parts = urllib.parse.urlsplit(jev_url(review_url))
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/api/jev/offer", parts.query, ""))


def fetch(url: str, timeout: float = 60.0, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"}) if body is not None else url
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            reason = json.load(exc).get("error")
        except (ValueError, AttributeError):
            reason = None
        raise ValueError(f"HTTP {exc.code}: {reason or exc.reason}") from None


def items(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in result.get("items") or [] if isinstance(item, dict)]


def pending(result: dict[str, Any]) -> int:
    return sum(1 for item in items(result) if item.get("state") == "pending")


def paused(result: dict[str, Any]) -> list[str]:
    """The paused line while the provider refuses Jev's key (jev-suggestions #provider-refused), else none."""
    if not result.get("paused_until"):
        return []
    return _table([["paused", f"until {result['paused_until']}, status {result.get('status')}"]])


def _target(item: dict[str, Any]) -> str:
    # The clause the server names the mark by, its cited (jev.py JevService._cited), which --resolve hands back; a
    # target within the spec as #anchor.
    target = str(item.get("cited") or "")
    return target if not target or "#" in target else "#" + target


def _mark(item: dict[str, Any]) -> str:
    # A rule item's label is always `missed`; its kind names it, its home names the rule (project-rules #mark-word).
    return "rule" if item.get("kind") == "rule" else str(item.get("label") or item.get("kind") or "")


def _table(rows: list[list[str]], first: int = 0) -> list[str]:
    widths = [max([len(row[i]) for row in rows] + [first if i == 0 else 0]) + 2 for i in range(len(rows[0]) - 1)]
    return ["".join(cell.ljust(width) for cell, width in zip(row, widths)) + row[-1] for row in rows]


def summary(result: dict[str, Any]) -> list[str]:
    if result.get("jev") == "off":
        return ["off"]
    if paused(result):
        return paused(result)
    marks = [["important", str(item.get("id") or ""), _mark(item), "important", _target(item),
              str(item.get("record") or "")] for item in items(result) if item.get("agent_level") == "important"]
    # Candidates await the human, are the human's to settle (project-rules #agent-ask): own line, not warnings.
    counts = Counter(str(item.get("kind") or "") for item in items(result)
                     if item.get("agent_level") == "warning" and item.get("kind") != "candidate")
    warnings = ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    unsure = sum(item["unsure"] for item in items(result) if isinstance(item.get("unsure"), int))
    if unsure:
        warnings = ", ".join(filter(None, (warnings, f"unsure {unsure}")))
    rules = ", ".join(str(rule) for rule in result.get("rules") or [])
    candidates = ", ".join(dict.fromkeys(str(item.get("target")) for item in items(result)
                                         if item.get("kind") == "candidate" and item.get("state") == "label"))
    tail = [["warnings", warnings or "0"], ["rules", rules or "none"], ["candidates", candidates or "none"],
            ["pending", str(pending(result))]]
    return (_table(marks, len("candidates")) if marks else []) + _table(tail)


def detail(result: dict[str, Any], anchor: str) -> list[str]:
    if result.get("jev") == "off":
        return ["off"]
    chosen = [item for item in items(result) if str(item.get("id")) == anchor]
    if not chosen:
        return paused(result) + [f"{anchor}  no items"]
    lines: list[str] = paused(result)
    for item in chosen:
        if lines:
            lines.append("")
        lines.extend(f"{key}  {json.dumps(value) if isinstance(value, (dict, list)) else value}"
                     for key, value in item.items())
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("review_url", help="the review link, with its base")
    parser.add_argument("--anchor", help="print full detail for this anchor only")
    parser.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                        help="re-read every 2s until nothing is pending, for at most this long, the first read included; "
                             "a read already sent may finish up to 60s past it; while Jev is paused, no re-read")
    answer = parser.add_mutually_exclusive_group()
    answer.add_argument("--resolve", nargs=4, metavar=("ANCHOR", "CLAUSE", "RECORD", "RESOLUTION"),
                        help="record an Important mark's resolution, the mark named as its line lists it: "
                             "fixed, reason, or dismissed (the server checks it)")
    answer.add_argument("--not-a-rule", metavar="ANCHOR",
                        help="record Not a project rule for the candidate at this anchor of the link's spec")
    answer.add_argument("--ignored", metavar="ANCHOR",
                        help="record a candidate at this anchor of the link's spec skipped in catch-up: ignored")
    answer.add_argument("--not-for-spec", nargs=2, metavar=("HOME", "RECORD"),
                        help="record Not for this spec for a rule miss: its home and record, from --anchor")
    parser.add_argument("--reason", help="the reason; a dismissal needs one")
    args = parser.parse_args(argv)
    if args.resolve:
        anchor, clause, record, resolution = args.resolve
        # the line lists a target within the spec as #anchor (_target)
        target = clause[1:] if clause.startswith("#") else clause
        body = {"resolve": resolution, "anchor": anchor, "target": target, "record": record,
                **({"reason": args.reason} if args.reason else {})}
        shown = tuple(args.resolve)
    elif args.not_a_rule:
        body, shown = {"dismiss": "rule", "anchor": args.not_a_rule}, (args.not_a_rule, "not-a-rule")
    elif args.ignored:
        body, shown = {"dismiss": "ignored", "anchor": args.ignored}, (args.ignored, "ignored")
    elif args.not_for_spec:
        home, record = args.not_for_spec
        body, shown = {"dismiss": "here", "home": home, "record": record}, (home, "dismissed")
    else:
        body = None
    if body is not None:
        try:
            fetch(record_url(args.review_url), body=body)
        except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            print(f"jev-read: {exc}", file=sys.stderr)
            return 1
        print("  ".join(shown))
        return 0
    try:
        url = jev_url(args.review_url)
        deadline = time.monotonic() + args.wait
        # The first read may answer slowly, building the page's questions: --wait covers it too. A read gets at least
        # 60s, so one sent near the end of the wait still answers.
        result = fetch(url, timeout=max(60.0, args.wait))
        while pending(result) and not paused(result) and time.monotonic() < deadline:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
            result = fetch(url, timeout=max(60.0, deadline - time.monotonic()))
    except (ValueError, OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"jev-read: {exc}", file=sys.stderr)
        return 1
    if not isinstance(result, dict) or "jev" not in result:
        print("jev-read: bad response", file=sys.stderr)
        return 1
    print("\n".join(detail(result, args.anchor) if args.anchor else summary(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
