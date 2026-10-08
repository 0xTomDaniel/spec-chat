"""One spec's spool read the page's way: the service fold and the hand-off batch.

review-state #model-fold-one, #model-order, #live-handoff, host-wake #wake-rule-batch.
Standard library only.
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


_REFS = ("threadId", "respondsTo", "supersedes", "anchorId")


def _well_formed(events):
    """`{name, actor?, body}` events with a string id and event name and string references;
    actor falls back to the body's own, as on disk."""
    result = []
    for event in events:
        body = event.get("body") if isinstance(event, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get("id"), str) or not isinstance(body.get("event"), str):
            continue
        if any(body.get(field) is not None and not isinstance(body[field], str) for field in _REFS):
            continue
        result.append({**event, "actor": event.get("actor") or body.get("actor")})
    return result


def draft_ids(events):
    """Human message ids no hand-off covers: the page's draftIds. A hand-off covers the ids it
    lists in `events`; one without `events` covers every draft before it."""
    events = _well_formed(events)
    listed, legacy = set(), ""
    for e in events:
        if e["actor"] != "human" or e["body"]["event"] != "handoff":
            continue
        if isinstance(e["body"].get("events"), list):
            listed.update(item for item in e["body"]["events"] if isinstance(item, str))
        elif e["name"] > legacy:
            legacy = e["name"]
    return {e["body"]["id"] for e in events if e["actor"] == "human" and e["body"]["event"] in ("comment", "reply", "edit")
            and e["name"] > legacy and e["body"]["id"] not in listed}


def fold_threads(events):
    """The page's foldThreads over events sorted by file name: thread id -> thread with `id`,
    `ev` (root comment), `messages`, `status` (draft, pending, acknowledged, resolved, or an
    agent's status), `latest_human_id`, `history` (message index -> original and edits), and
    `draft_by` (browsers holding a draft in it). An event whose referent sorts later waits for it."""
    events = sorted(_well_formed(events), key=lambda e: e["name"])
    drafts = draft_ids(events)
    ids = {e["body"]["id"] for e in events}
    threads, known, parked, message_thread, message_slot = {}, set(), {}, {}, {}

    def human_status(e):
        return "draft" if e["body"]["id"] in drafts else "pending"

    def mark_draft(th, e):
        if e["body"]["id"] in drafts:
            th["draft_by"].setdefault(e["body"].get("browser") or "")

    def by_ref(b):
        return b.get("threadId") or message_thread.get(b.get("respondsTo")) or (
            b.get("respondsTo") if b.get("respondsTo") in threads else None)

    def apply(e):
        b, human = e["body"], e["actor"] == "human"
        if b["event"] == "comment" and human:
            th = {"id": b["id"], "ev": e, "messages": [e], "status": human_status(e), "latest_human_id": b["id"],
                  "history": {}, "draft_by": {}}
            mark_draft(th, e)
            threads[b["id"]] = th
            message_thread[b["id"]] = b["id"]
            message_slot[b["id"]] = (th, 0)
        elif b["event"] == "reply":
            th = threads.get(by_ref(b))
            if not th:
                return
            th["messages"].append(e)
            message_thread[b["id"]] = th["id"]
            message_slot[b["id"]] = (th, len(th["messages"]) - 1)
            if human:
                th["latest_human_id"] = b["id"]
                th["status"] = human_status(e)
                mark_draft(th, e)
            elif b.get("respondsTo") == th["latest_human_id"]:
                th["status"] = b.get("status") or "acknowledged"
        elif b["event"] == "edit" and human:
            prior = message_slot.get(b.get("supersedes"))
            th = prior[0] if prior else threads.get(b.get("threadId") or message_thread.get(b.get("supersedes")))
            if not th:
                return
            index = prior[1] if prior else len(th["messages"])
            original = th["messages"][index] if index < len(th["messages"]) else e
            th["history"].setdefault(index, {"original": original, "edits": []})["edits"].append(e)
            if index < len(th["messages"]):
                th["messages"][index] = e  # ev stays the root comment: its version, target, and quote place the thread
            else:
                th["messages"].append(e)
            message_thread[b["id"]] = th["id"]
            message_slot[b["id"]] = (th, index)
            th["latest_human_id"] = b["id"]
            th["status"] = human_status(e)
            mark_draft(th, e)
        elif b["event"] == "status":
            th = threads.get(by_ref(b))
            if th:
                th["status"] = b.get("status")

    def visit(e):
        apply(e)
        known.add(e["body"]["id"])
        for waiting in parked.pop(e["body"]["id"], ()):
            visit(waiting)

    for e in events:
        b = e["body"]
        ref = b.get("supersedes") if b["event"] == "edit" else b.get("respondsTo") if b["event"] in ("reply", "status") else None
        if ref and ref != b["id"] and ref not in known and ref in ids:
            parked.setdefault(ref, []).append(e)
        else:
            visit(e)
    return threads


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
