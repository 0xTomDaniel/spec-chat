"""One spool read the page's way: the service fold, the hand-off batch, and host rows.

A spool is a spec's <spec>.review/, a mockup's <mockup>.review/ beside it, or a scope's hosted threads, the same
shape with no spec.
review-state #model-fold-one, #model-order, #live-handoff, #model-fields-hosted, #model-fields-spot,
#model-fields-stage; host-wake
#wake-rule-batch; comment-layer #boundary-agent; mockups #review-spool, #accept-set, #directions-pick; threads
#boundary-agent, #supersede. Standard library only.
usage: spool.py batch REVIEW_DIR CURSOR_NAME   (prints the batch's file names, one per line)
       spool.py host REVIEW_DIR < file names   (prints host<TAB>name<TAB>subject<TAB>kind<TAB>label<TAB>from<TAB>quote)
       spool.py accept REVIEW_DIR < file names (prints accept<TAB>name<TAB>mockup path<TAB>version)
       spool.py spot REVIEW_DIR < file names   (prints spot<TAB>name<TAB>step<TAB>moment<TAB>box<TAB>picture)
       spool.py stage REVIEW_DIR < file names  (prints stage<TAB>name<TAB>words)
       spool.py answer REVIEW_DIR < file names (prints answer<TAB>name<TAB>#question<TAB>about<TAB>step<TAB>words,
                                                a Pick's #question the directions list, and
                                                superseded<TAB>name<TAB>newer answer name)
"""
from __future__ import annotations

import contextlib
import errno
import importlib.util
import json
import math
import os
import re
import stat
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


SPOT_BOX = ("left", "top", "width", "height")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def comment_spot(body):
    """A comment's step, moment (seconds), and box (left, top, width, height as fractions of the frame), each None
    unless well formed (review-state #model-fields-spot): the page's commentSpot. A thread's spot is its root's."""
    box = body.get("box")
    return {
        "step": body["step"] if isinstance(body.get("step"), str) and body["step"] else None,
        "moment": body["moment"] if _number(body.get("moment")) and body["moment"] >= 0 else None,
        "box": {key: box[key] for key in SPOT_BOX} if isinstance(box, dict) and all(
            _number(box.get(key)) and 0 <= box[key] <= 1 for key in SPOT_BOX) else None,
    }


# threads #box-picture, review-service #store-pictures: a comment's picture, the file in its spool's pictures/ it names.
PICTURE_RE = re.compile(r"[0-9a-f]{64}\.(png|jpg|webp)\Z")
# threads #stage-tag-source: the lane a comment recorded, a lane's review slug.
LANE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")


def comment_picture(body):
    """The file name of the picture a comment carries of its boxed spot (review-state #model-fields-spot), or None
    unless well formed."""
    picture = body.get("picture")
    return picture if isinstance(picture, str) and PICTURE_RE.fullmatch(picture) else None


def thread_lane(review, message_id):
    """threads #stage-tag-source: the lane the thread holding message `message_id` has, the one its comment recorded
    (review-state #model-fields-stage), which a reply to it, the agent's included, takes its stage from; None when its
    comment recorded none or no thread holds the message."""
    for thread in fold_threads(read_events(review)).values():
        if any(event["body"]["id"] == message_id for event in thread["events"]):
            lane = thread["ev"]["body"].get("lane")
            return lane if isinstance(lane, str) and LANE_RE.fullmatch(lane) else None
    return None


# The statuses an event may name (references/event-schema.md): an agent reply's, and the human's resolve. Any other
# value is no status: an agent reply naming none acknowledges, a status event naming none changes nothing.
NAMED_STATUSES = ("acknowledged", "orphaned", "resolved")


def fold_threads(events):
    """The page's foldThreads over events sorted by file name: thread id -> thread with `id`,
    `ev` (root comment), `messages`, `status` (draft, pending, or a named status), `latest_human_id`,
    `history` (message index -> original and edits), `draft_by` (browsers holding a draft in it), `spot` (the root
    comment's, comment_spot), and `events` (every event joined to it, in fold order). An event whose referent sorts
    later waits for it."""
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
                  "history": {}, "draft_by": {}, "spot": comment_spot(b), "events": [e]}
            mark_draft(th, e)
            threads[b["id"]] = th
            message_thread[b["id"]] = b["id"]
            message_slot[b["id"]] = (th, 0)
        elif b["event"] == "reply":
            th = threads.get(by_ref(b))
            if not th:
                return
            th["messages"].append(e)
            th["events"].append(e)
            message_thread[b["id"]] = th["id"]
            message_slot[b["id"]] = (th, len(th["messages"]) - 1)
            if human:
                th["latest_human_id"] = b["id"]
                th["status"] = human_status(e)
                mark_draft(th, e)
            elif b.get("respondsTo") == th["latest_human_id"]:
                th["status"] = b["status"] if b.get("status") in NAMED_STATUSES else "acknowledged"
        elif b["event"] == "edit" and human:
            prior = message_slot.get(b.get("supersedes"))
            th = prior[0] if prior else threads.get(b.get("threadId") or message_thread.get(b.get("supersedes")))
            if not th:
                return
            index = prior[1] if prior else len(th["messages"])
            original = th["messages"][index] if index < len(th["messages"]) else e
            th["history"].setdefault(index, {"original": original, "edits": []})["edits"].append(e)
            th["events"].append(e)
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
                th["events"].append(e)
                if b.get("status") in NAMED_STATUSES:
                    th["status"] = b["status"]

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


HOST_FIELDS = ("subject", "kind", "label", "from")


class UnsafeSpool(OSError):
    """A spool path that escapes its collection, or a link or other non-regular file where a spool directory or an
    event file should be."""


# A symlink or non-directory where the spool expects its own directory or file: refused, not retried.
UNSAFE_ERRNOS = (errno.ELOOP, errno.ENOTDIR)
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath((path, root)) == root and path != root
    except ValueError:
        return False


@contextlib.contextmanager
def actor_directory(review, root, actor, create=False):
    """A descriptor of `<review>/<actor>`, reached from `root` one directory at a time with no link followed, so the
    spool stays inside `root`; each directory made on the way when `create`."""
    parts = os.path.relpath(review, root).split(os.sep) + [actor]
    if any(part in ("", ".", "..") for part in parts) or not _inside(review, root):
        raise UnsafeSpool("review path escapes collection")
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def read_events(review, root=None):
    """Every event of a spool as `{actor, name, body}`: the one spool read, the owner's on disk (comment-layer
    #boundary-agent) and the service's. A name starting with a dot or not ending .json is no event; a file that is not
    JSON is skipped. The spool is reached from `root`, the service's collection, with no link followed; the owner's
    read starts at the spool's own folder. A link or other non-regular file in the spool raises UnsafeSpool, and is
    never waited on."""
    if root is None:
        root = os.path.realpath(os.path.dirname(os.path.abspath(review)))
        review = os.path.join(root, os.path.basename(os.path.abspath(review)))
    events = []
    for actor in ("human", "agent"):
        try:
            with actor_directory(review, root, actor) as directory:
                for name in os.listdir(directory):
                    if name.startswith(".") or not name.endswith(".json"):
                        continue
                    try:
                        descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW, dir_fd=directory)
                    except FileNotFoundError:
                        continue
                    with os.fdopen(descriptor, encoding="utf-8") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            raise UnsafeSpool("not a regular file: " + name)
                        try:
                            body = json.load(stream)
                        except ValueError:
                            continue
                    events.append({"actor": actor, "name": name, "body": body})
        except FileNotFoundError:
            continue
    return events


def _field(value):
    """A string field as one line: whitespace runs read as one space; anything else is empty."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def host_rows(review, names):
    """`(name, subject, kind, label, from, quote)` for each named event of a thread written on a host page
    (comment-layer #boundary-agent): the thread's root comment's fields, so a reply or edit routes as its thread does.
    A spec page's comment carries none of them (review-state #model-fields-hosted), so its thread has no row; on a
    spec a host comment keeps its anchor in place of its subject, and the subject is empty. Whitespace runs read as
    one space, so a row stays one line."""
    thread_of = {}
    for thread in fold_threads(read_events(review)).values():
        root = thread["ev"]["body"]
        if not any(root.get(key) for key in HOST_FIELDS):
            continue
        fields = tuple(_field(root.get(key)) for key in HOST_FIELDS + ("quote",))
        for event in thread["events"]:
            thread_of[event["name"]] = fields
    return [(name, *thread_of[name]) for name in names if name in thread_of]


def _number_field(value):
    return "" if value is None else json.dumps(value)


def spot_rows(review, names):
    """`(name, step, moment, box, picture)` for each named event of a thread whose root comment carries a spot
    (review-state #model-fields-spot, threads #boundary-agent): the root's, so a reply or edit reads at its thread's
    step or moment. The moment is in seconds; the box is its left, top, width, and height, joined by commas; the picture
    is the path of the file its page made of the boxed spot, kept in the spool's pictures/ (threads #box-picture-scan),
    so the agent can look at what the human saw; each is empty when absent."""
    thread_of = {}
    for thread in fold_threads(read_events(review)).values():
        spot = thread["spot"]
        picture = comment_picture(thread["ev"]["body"])
        picture = os.path.join(review, "pictures", picture) if picture else ""
        if not any(value is not None for value in spot.values()) and not picture:
            continue
        box = ",".join(_number_field(spot["box"][key]) for key in SPOT_BOX) if spot["box"] else ""
        fields = (_field(spot["step"]), _number_field(spot["moment"]), box, picture)
        for event in thread["events"]:
            thread_of[event["name"]] = fields
    return [(name, *thread_of[name]) for name in names if name in thread_of]


def stage_rows(review, names):
    """`(name, stage)` for each named message that carries a stage (review-state #model-fields-stage, threads
    #stage-tag): a comment's or reply's own, written with it; an edit's, the stage of the message it edits, since an
    edit carries none of its own and keeps its message's. A message with none has no row. Words on one line."""
    stage_of = {}
    for thread in fold_threads(read_events(review)).values():
        for event in thread["events"]:
            if event["body"]["event"] in ("comment", "reply"):
                stage_of[event["name"]] = _field(event["body"].get("stage"))
        for kept in thread["history"].values():
            for edit in kept["edits"]:
                stage_of[edit["name"]] = _field(kept["original"]["body"].get("stage"))
    return [(name, stage_of[name]) for name in names if stage_of.get(name)]


_PLACE = []


def _place():
    """The mark resolver's module (assets/place.py), whose Document parses a page once; loaded on first use."""
    if not _PLACE:
        found = importlib.util.spec_from_file_location("spool_place", os.path.join(os.path.dirname(__file__), "place.py"))
        module = importlib.util.module_from_spec(found)
        found.loader.exec_module(module)
        _PLACE.append(module)
    return _PLACE[0]


def page_questions(text):
    """What the page asks its reviewer to answer: anchor -> (about, step). Its open TBD questions, each block the
    nearest anchored element holding a `data-spec-tbd` marker, itself included (prompt-first-shaping #answer-buttons),
    whose value is not `later`, which asks nothing now (prompt-first-shaping #finish-tbd-later), as the page and the
    service read them; on a mockup, the parts it is about and the step it is asked at (mockups #markup-questions), read
    from the block, else from the marker. And a mockup's directions list, about no part and at no step, whose answer is
    the picked direction's name (mockups #directions-pick)."""
    questions = {}
    for element in _place().Document(text).elements:
        if "data-mockup-directions" in element.attrs and element.attrs.get("data-anchor"):
            questions.setdefault(element.attrs["data-anchor"], ("", ""))
            continue
        if "data-spec-tbd" not in element.attrs or element.attrs["data-spec-tbd"] == "later":
            continue
        block = element
        while block is not None and not block.attrs.get("data-anchor"):
            block = block.parent
        if block is None or block.attrs["data-anchor"] in questions:
            continue
        questions[block.attrs["data-anchor"]] = tuple(
            _field(block.attrs.get(name) or element.attrs.get(name)) for name in ("data-about", "data-step"))
    return questions


def answer_rows(review, names):
    """Each named event of a TBD question's answer (threads #supersede, mockups #mockup-questions-answer), or of a Pick on
    a mockup's directions (mockups #directions-pick), the list's anchor its `#question`: an answer is a handed-off human
    comment on the question's block or the directions list, by a button, a Pick, or in words, and its edits; the newest
    stands.
    `("answer", name, "#question", about, step, words)` for the newest answer's events, its newest handed-off text as
    words; `("superseded", name, newer)` for each earlier answer's, naming the newest answer's comment file. A draft
    answer is no answer yet. The questions are the page's as it now reads, the spool's spec or mockup; a scope's hosted
    threads have none."""
    review = os.path.abspath(review)
    if not review.endswith(".review"):
        return []
    try:
        with open(review[:-len(".review")], encoding="utf-8") as stream:
            questions = page_questions(stream.read())
    except (OSError, ValueError):
        return []
    if not questions:
        return []
    events = read_events(review)
    drafts = draft_ids(events)
    answers = {}
    for thread in fold_threads(events).values():  # fold order: the newest answer last
        root = thread["ev"]["body"]
        if root.get("anchorId") in questions and thread["id"] not in drafts:
            answers.setdefault(root["anchorId"], []).append(thread)
    wanted, rows = set(names), {}
    for anchor, threads in answers.items():
        newest = threads[-1]
        for thread in threads:
            own = [thread["ev"]] + list(thread["history"].get(0, {}).get("edits", ()))
            if thread is newest:
                about, step = questions[anchor]
                sent = [event for event in own if event["body"]["id"] not in drafts]
                row = ("answer", "#" + anchor, about, step, _field(sent[-1]["body"].get("text")))
            else:
                row = ("superseded", newest["ev"]["name"])
            for event in own:
                if event["name"] in wanted:
                    rows[event["name"]] = (row[0], event["name"]) + row[1:]
    return [rows[name] for name in names if name in rows]


def mockup_set(spec):
    """A spec's mockup set, the folder mockups/<spec name>/ beside it, found from its path alone (mockups #set-where);
    None for a page that is no spec, such as a mockup."""
    name = os.path.basename(spec)
    if not name.endswith(".spec.html"):
        return None
    return os.path.join(os.path.dirname(spec), "mockups", name[:-len(".spec.html")])


def accepted_mockups(spec, body):
    """`(mockup path, version)` for each mockup an Accept spec hand-off `body` in the spool of `spec`, an absolute
    path, accepts with the spec (mockups #accept-set): Accept spec names each mockup of the set by its path from the
    spec's folder, and the version its page showed. The path reads as the scan's rows give the spec's. The page that
    writes the hand-off needs no login, so an entry naming no file in the spec's set is no accepted mockup. A hand-off
    listing its events is a comment hand-off, never Accept spec (review-state #live-accept), and accepts none."""
    folder = mockup_set(spec)
    entries = body.get("mockups") if folder is not None and not isinstance(body.get("events"), list) else None
    rows = []
    for entry in entries if isinstance(entries, list) else ():
        path = entry.get("path") if isinstance(entry, dict) else None
        version = _field(entry.get("version")) if isinstance(entry, dict) else ""
        if not isinstance(path, str) or not version:
            continue
        path = os.path.normpath(os.path.join(os.path.dirname(spec), path))
        if os.path.dirname(path) == folder:
            rows.append((path, version))
    return rows


def accept_rows(review, names):
    """`(name, mockup path, version)` for each mockup a named hand-off in a spec's spool accepts with the spec
    (accepted_mockups); a mockup's own spool has no set, so its entries name none."""
    review = os.path.abspath(review)
    spec = review[:-len(".review")]
    if not review.endswith(".review") or mockup_set(spec) is None:
        return []
    wanted, rows = set(names), []
    for event in sorted(_well_formed(read_events(review)), key=lambda e: e["name"]):
        body = event["body"]
        if event["actor"] == "human" and body["event"] == "handoff" and event["name"] in wanted:
            rows.extend((event["name"],) + row for row in accepted_mockups(spec, body))
    return rows


def read_cursor(review, cursor_name):
    try:
        with open(os.path.join(review, cursor_name), encoding="utf-8") as stream:
            return set(stream.read().splitlines())
    except FileNotFoundError:
        return set()


def main(argv):
    lines = {"host": host_rows, "accept": accept_rows, "spot": spot_rows, "stage": stage_rows}
    if len(argv) == 2 and (argv[0] in lines or argv[0] == "answer"):
        try:
            names = sys.stdin.read().splitlines()
            rows = answer_rows(argv[1], names) if argv[0] == "answer" else [
                (argv[0],) + row for row in lines[argv[0]](argv[1], names)]
        except OSError as exc:
            print("spool.py: %s: %s" % (argv[1], exc), file=sys.stderr)
            return 1
        for row in rows:
            print("\t".join(row))
        return 0
    if len(argv) != 3 or argv[0] != "batch":
        print("usage: spool.py batch REVIEW_DIR CURSOR_NAME | spool.py host|accept|spot|stage|answer REVIEW_DIR < file names",
              file=sys.stderr)
        return 2
    review, cursor_name = argv[1], argv[2]
    for name in handoff_batch(review, read_cursor(review, cursor_name)):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
