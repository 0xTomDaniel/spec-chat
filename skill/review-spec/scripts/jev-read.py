#!/usr/bin/env python3
"""Compact agent read of a review link's Jev checks (project-rules #agent-read).

Reads GET /api/jev for the spec and base the review link serves, from the running server, and prints:
one line per important mark, warning counts by kind, the rules checked, the pending count; or `off`.
`--anchor <id>` prints full detail for that anchor only. Marks are keyed on each item's `level`.
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


def fetch(url: str, timeout: float = 60.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
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


def _target(item: dict[str, Any]) -> str:
    target = str(item.get("target") or "")
    return target if not target or "#" in target else "#" + target


def _mark(item: dict[str, Any]) -> str:
    # A rule item's label is always `missed`; its kind names it, its target names the rule.
    return "rule" if item.get("kind") == "rule" else str(item.get("label") or item.get("kind") or "")


def _table(rows: list[list[str]], first: int = 0) -> list[str]:
    widths = [max([len(row[i]) for row in rows] + [first if i == 0 else 0]) + 2 for i in range(len(rows[0]) - 1)]
    return ["".join(cell.ljust(width) for cell, width in zip(row, widths)) + row[-1] for row in rows]


def summary(result: dict[str, Any]) -> list[str]:
    if result.get("jev") == "off":
        return ["off"]
    marks = [["important", str(item.get("id") or ""), _mark(item), "important", _target(item)]
             for item in items(result) if item.get("level") == "important"]
    counts = Counter(str(item.get("kind") or "") for item in items(result) if item.get("level") == "warning")
    warnings = ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    rules = ", ".join(str(rule) for rule in result.get("rules") or [])
    tail = [["warnings", warnings or "0"], ["rules", rules or "none"], ["pending", str(pending(result))]]
    return (_table(marks) if marks else []) + _table(tail, len("important") if marks else 0)


def detail(result: dict[str, Any], anchor: str) -> list[str]:
    if result.get("jev") == "off":
        return ["off"]
    chosen = [item for item in items(result) if str(item.get("id")) == anchor]
    if not chosen:
        return [f"{anchor}  no items"]
    lines: list[str] = []
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
                        help="re-read every 2s until nothing is pending, at most this long")
    args = parser.parse_args(argv)
    try:
        url = jev_url(args.review_url)
        result = fetch(url)
        deadline = time.monotonic() + args.wait
        while pending(result) and time.monotonic() < deadline:
            time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
            result = fetch(url)
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
