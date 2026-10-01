"""Hand-off batch over one spec's spool: one rule for the zero-wait scan and host wake.

review-state #live-handoff, host-wake #wake-rule-batch. Standard library only.
usage: spool.py batch REVIEW_DIR CURSOR_NAME   (prints the batch's file names, one per line)
"""
from __future__ import annotations

import json
import os
import sys


def _event_id(name):
    """`<ns>-<event>-<id>.json` -> id; event names hold no dash."""
    parts = name[:-len(".json")].split("-", 2) if name.endswith(".json") else ()
    return parts[2] if len(parts) == 3 else None


def handoff_batch(review, consumed):
    """Each hand-off not in the cursor and the events it lists, in file-name order.

    A hand-off without a list `events` (written before that field) hands off every
    pending human event up to it, as before."""
    human = os.path.join(review, "human")
    names = sorted(name for name in os.listdir(human) if not name.startswith("."))
    pending = [name for name in names if name not in consumed]
    by_id = {_event_id(name): name for name in pending}
    batch = set()
    for index, name in enumerate(pending):
        if "-handoff-" not in name:
            continue
        batch.add(name)
        try:
            with open(os.path.join(human, name), encoding="utf-8") as stream:
                listed = json.load(stream).get("events")
        except (OSError, ValueError, AttributeError):
            listed = None
        if isinstance(listed, list):
            batch.update(by_id[item] for item in listed if isinstance(item, str) and item in by_id)
        else:
            batch.update(pending[:index])
    return tuple(sorted(batch))


def read_cursor(review, cursor_name):
    try:
        with open(os.path.join(review, cursor_name), encoding="utf-8") as stream:
            return set(stream.read().splitlines())
    except FileNotFoundError:
        return set()


def main(argv):
    if len(argv) != 3 or argv[0] != "batch":
        print("usage: spool.py batch REVIEW_DIR CURSOR_NAME", file=sys.stderr)
        return 2
    review, cursor_name = argv[1], argv[2]
    for name in handoff_batch(review, read_cursor(review, cursor_name)):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
