#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root non-git-root
"""Serve one or more narrow Spec Chat review mounts."""

from __future__ import annotations

import argparse
import base64
import binascii
import collections
import contextlib
import functools
import hashlib
import html
import importlib.machinery
import importlib.util
import http.client
import io
import ipaddress
import json
import mimetypes
import os
import pwd
import re
import secrets
import select
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlsplit

try:
    from jev import (MARK_LEVELS, JevService, ServiceRuns, _now as _utc_now, enumerate_served_specs,
                     extract_anchors, mount_prefix as _mount_prefix, spec_chat_state)
except ModuleNotFoundError:
    import importlib.util
    _jev_spec = importlib.util.spec_from_file_location("review_serve_jev", os.path.join(os.path.dirname(__file__), "jev.py"))
    _jev_module = importlib.util.module_from_spec(_jev_spec)
    _jev_spec.loader.exec_module(_jev_module)
    JevService = _jev_module.JevService
    ServiceRuns = _jev_module.ServiceRuns
    MARK_LEVELS = _jev_module.MARK_LEVELS
    enumerate_served_specs = _jev_module.enumerate_served_specs
    extract_anchors = _jev_module.extract_anchors
    _mount_prefix = _jev_module.mount_prefix
    spec_chat_state = _jev_module.spec_chat_state
    _utc_now = _jev_module._now
try:
    from place import resolve_events
except ModuleNotFoundError:
    import importlib.util
    _place_spec = importlib.util.spec_from_file_location("review_serve_place", os.path.join(os.path.dirname(__file__), "place.py"))
    _place_module = importlib.util.module_from_spec(_place_spec)
    _place_spec.loader.exec_module(_place_module)
    resolve_events = _place_module.resolve_events

try:
    import spool
except ModuleNotFoundError:
    import importlib.util
    _spool_spec = importlib.util.spec_from_file_location("review_serve_spool", os.path.join(os.path.dirname(__file__), "spool.py"))
    spool = importlib.util.module_from_spec(_spool_spec)
    _spool_spec.loader.exec_module(spool)


SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
SAFE_CURSOR_RE = re.compile(r"[^/\\]+\Z")
EVENT_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
WAKE_POLL_SECONDS = 3
# A file time this recent may be shared by a change made just after it was read (file times tick coarsely), so a
# row's hand-off batch read under it is read again on the next poll (#steady-idle).
WAKE_RACY_NS = 1_000_000_000
WAKE_CHECK_TIMEOUT_SECONDS = 5
WAKE_SEND_TIMEOUT_SECONDS = 10
WAKE_PLACEHOLDER_RE = re.compile(r"\{(owner|artifact|message)\}")
WAKE_FAILED_ATTEMPTS = 3  # host-wake #wake-rule-retry: failed attempts in a row before the state turns failed
# host-wake #wake-rule-retry: once failed, the wait between attempts doubles from one poll up to 3 s x 2^6, about 3 minutes
WAKE_BACKOFF_DOUBLINGS = 6
WAKE_ERROR_BYTES = 4096  # the tail of a wake command's error output kept, so a chatty provider cannot bloat a log line
WAKE_REASON_CHARS = 200  # the reason travels percent-encoded in a header: small for proxies with small header buffers
# Printable ASCII but %: the reason stays readable in the X-Spec-Chat-Wake header and decodes as a URI component.
WAKE_REASON_SAFE = "".join(chr(code) for code in range(0x20, 0x7F) if chr(code) != "%")
EVIDENCE_TIMEOUT_SECONDS = 30
# review-index #index-gates-provider: how long from its ask an index load waits for the review facts, the whole answer,
# before it renders without them; half the warm index budget (#index-entry-cost, under 200 ms), so the rest is the load's.
REVIEW_FACTS_SECONDS = 0.1
# Opening a provider connection, TCP and TLS: it may outlast the ask that opened it, which keeps it for the next ask.
PROVIDER_CONNECT_SECONDS = 10
# Idle provider connections kept between asks, all origins together; an ask finding none idle opens its own.
PROVIDER_CONNECTIONS = 4
PROVIDER_REDIRECTS = 5  # redirects one provider fetch follows, all inside its one deadline
# A spec row's own fields: a scope row holds none of them (review-service #published-table, comment-layer #scope-row).
SPEC_ROW_FIELDS = ("project", "path", "spec", "root", "narrow_root", "base")
SCOPE_ROW_FIELDS = ("id", "slug", "owner", "checker", "cursor_name")
# A criterion ID, project/<project-id>::<spec path>#<anchor> (criterion-evidence #read-spec-id).
SPEC_SUBJECT_RE = re.compile(r"project/([^/:]+)::([^#]+\.spec\.html)#(.+)")
STAGES_HEADER = "X-Spec-Chat-Stages"  # an events answer's lanes' stages (threads #stage-tag-source)
LANES_ASKED = 8  # the most lanes a page's read names for their stages; its items and threads name one or two
# How long one review facts ask for a lane set's stages serves every events read of those lanes, every page's: pages read
# every 2 s, and a lane's stage moves at its gates, far apart (threads #stage-tag-source).
STAGES_SECONDS = 5
HOSTED = "hosted"  # the store of a scope's hosted threads, as /api/events names it beside a spec row's path
# review-service #store-pictures, threads #box-picture: a comment's picture, a PNG, JPEG, or WebP image of at most 1 MB,
# kept as pictures/<sha256>.<extension> in the spool that keeps the comment.
PICTURE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
PICTURE_BYTES = 1024 * 1024
PICTURE_NAME_RE = re.compile(r"[0-9a-f]{64}\.(png|jpg|webp)\Z")
PICTURE_CONTENT = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
# A serialized origin: a page with no origin's null, or scheme://host[:port].
ORIGIN_RE = re.compile(r"null|[a-z][a-z0-9+.-]*://[^/\s,]+")
# The comment layer is the spec page runtime itself: served at /static/comment-layer.js, it takes layer mode from that
# address (comment-layer #load), so a host page runs the spec page's comment code, not a copy of it.
COMMENT_LAYER = Path(__file__).resolve().parent / "viz" / "runtime.js"
# The comment UI page (shell #shell-ui-page): the runtime loaded from the layer's address, relative to the page's
# own, so a public base address with a path holds it; data-comment-ui gives it its comment UI role. No login, no cookie.
COMMENT_UI_PAGE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Comments</title><link rel="icon" href="data:,">
<script defer src="static/comment-layer.js" data-comment-ui></script>
</head><body></body></html>
"""
EVIDENCE_FIELDS = ("match", "verdict", "judgment", "pr", "capturedAt", "onMain", "artifact", "bundle", "proven", "view")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".")
    parser.add_argument("port", nargs="?", type=int, default=None)
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--bind", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--registry", default=None)
    parser.add_argument("--port", dest="option_port", type=int, default=None)
    return parser.parse_args(argv)


def _inside(path, parent, strict=False):
    try:
        common = os.path.commonpath((os.path.realpath(path), os.path.realpath(parent)))
    except ValueError:
        return False
    parent = os.path.realpath(parent)
    return common == parent and (not strict or os.path.realpath(path) != parent)


def _git(root, *args, optional=False):
    try:
        result = subprocess.run(
            ("git", "-C", str(root), *args),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:  # no git binary: Git features are unavailable, never fatal
        if optional:
            return None
        raise RuntimeError("git unavailable") from exc
    if result.returncode:
        if optional:
            return None
        raise RuntimeError("git lookup failed: " + " ".join(args))
    return result.stdout


def _repo_root(root):
    return Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()


def _normalise_spec(value):
    value = value.replace("\\", "/")
    parts = value.split("/")
    if value.startswith("/") or not value.endswith(".spec.html") or any(
        part in ("", ".", "..") for part in parts
    ):
        raise ValueError("invalid resource spec path")
    return value


MOCKUP_SUFFIX = ".mockup.html"


def _mockup_name(name):
    """A mockup's file name, <NN>-<screen>.mockup.html; the validator holds each to that form, so any visible
    *.mockup.html in a set folder is a screen of the set."""
    return name.endswith(MOCKUP_SUFFIX) and not name.startswith(".")


def mockup_spec(page):
    """The spec whose mockup set holds `page`, <dir>/mockups/<spec name>/<file>.mockup.html, as the path beside it,
    <dir>/<spec name>.spec.html; None for any other path: spool.mockup_set read backwards (mockups #set-where)."""
    parts = page.split("/")
    if len(parts) < 3 or parts[-3] != "mockups" or not _mockup_name(parts[-1]):
        return None
    return "/".join(parts[:-3] + [parts[-2] + ".spec.html"])


def _set_files(spec_file, root):
    """The file names of a spec's mockups in name order, so in screen order: each *.mockup.html file in its set's folder
    (spool.mockup_set) that stays inside the collection `root`; () with none. No list names them, so adding a screen is
    adding a file."""
    folder = spool.mockup_set(spec_file)
    try:
        names = os.listdir(folder)
    except OSError:
        return ()
    return tuple(sorted(name for name in names if _mockup_name(name) and _inside(
        os.path.join(folder, name), root, strict=True) and os.path.isfile(os.path.join(folder, name))))


def _served_file(mount, relative):
    """The file `relative`, a path as the mount serves it, names, links unresolved: a spec's set is found beside the
    path it is served at (mockups #set-where), where its mockup pages are served (_set_page)."""
    return os.path.join(mount["root"] if mount["slug"] else mount["narrow_root"], *relative.split("/"))


def _set_page(mount, relative):
    """True when `relative`, a path as the mount serves it, is a mockup page of a spec the mount serves: a row's own
    spec, or in a single collection any spec file it holds. A row reviews its spec's set with it (mockups
    #review-same) and serves no other spec's mockup, as it serves no other spec."""
    spec = mockup_spec(relative)
    if spec is None:
        return False
    if mount.get("spec"):
        return spec == mount["spec"]
    return os.path.isfile(os.path.join(mount["narrow_root"], *spec.split("/")))


def stale_reason(root, narrow_root, spec):
    """Why a row's spec cannot be reached (review-service #service-stale), else None. review-host uses this rule too."""
    root = os.path.realpath(root)
    if not os.path.isdir(root) or _git(root, "rev-parse", "--show-toplevel", optional=True) is None:
        return "worktree is unreachable"
    if not os.path.isdir(os.path.realpath(narrow_root)):
        return "collection is missing"
    if not os.path.isfile(os.path.join(root, *spec.split("/"))):
        return "spec file is missing"
    return None


def is_scope_row(record):
    """A scope row names no spec, so it holds none of a spec row's own fields (review-service #published-table)."""
    return not any(key in record for key in SPEC_ROW_FIELDS)


def _resource_records(document, *, check_refs=False, trust=False):
    records = document.get("resource", [])
    if not isinstance(records, list):
        raise ValueError("registry resource entries must be an array")
    result = []
    ids = set()
    stable = set()
    for raw in records:
        if not isinstance(raw, dict):
            raise ValueError("registry resource entry must be a table")
        scope_row = is_scope_row(raw)
        required = SCOPE_ROW_FIELDS if scope_row else (
            "id", "slug", "root", "narrow_root", "spec", "base", "owner",
            "checker", "cursor_name",
        )
        missing = [
            key for key in required
            if not isinstance(raw.get(key), str) or not raw[key].strip()
        ]
        if missing:
            raise ValueError("resource missing required field: " + ", ".join(missing))
        rid = raw["id"]
        slug = raw["slug"]
        if rid in ids:
            raise ValueError("duplicate resource identity: " + rid)
        if not SLUG_RE.fullmatch(slug) or slug in {"api", "static"}:
            raise ValueError("invalid or reserved resource slug: " + slug)
        if scope_row:
            # A scope row serves no page: it names a scope and its owner (comment-layer #scope-row).
            if not SAFE_CURSOR_RE.fullmatch(raw["cursor_name"]):
                raise ValueError("invalid cursor name: " + raw["cursor_name"])
            ids.add(rid)
            result.append({**raw, "scope_row": True})
            continue
        root = os.path.realpath(raw["root"])
        narrow = os.path.realpath(raw["narrow_root"])
        if not os.path.isabs(raw["root"]):
            raise ValueError("resource root must be absolute: " + rid)
        spec = _normalise_spec(raw["spec"])
        spec_file = os.path.realpath(os.path.join(root, *spec.split("/")))
        # A stale row affects only itself: it stays listed, serves no page, and skips its filesystem checks.
        # Decided on every load, trusted reloads too, so routing and the index read one answer.
        stale = stale_reason(root, narrow, spec)
        if stale and not trust:
            print("review-serve: warning: stale row %s: %s" % (rid, stale), file=sys.stderr, flush=True)
        if not trust and not stale:
            if not _inside(narrow, root, strict=True):
                raise ValueError("resource roots are invalid: " + rid)
            try:
                top = os.path.realpath(_repo_root(root))
            except (OSError, RuntimeError) as exc:
                raise ValueError("resource root is not a Git worktree: " + rid) from exc
            if root != top:
                raise ValueError("resource root must be the Git worktree toplevel: " + rid)
            if not _inside(spec_file, narrow, strict=True):
                raise ValueError("resource spec is outside its collection: " + rid)
        # The host records each row's served path; rows without one serve at <slug>/<spec>.
        key = raw.get("path") or slug + "/" + spec
        if key in stable:
            raise ValueError("duplicate stable resource path: " + key)
        if not SAFE_CURSOR_RE.fullmatch(raw["cursor_name"]):
            raise ValueError("invalid cursor name: " + raw["cursor_name"])
        if check_refs and not stale:
            checked = subprocess.run(
                ("git", "-C", root, "rev-parse", "--verify", raw["base"] + "^{commit}"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if checked.returncode:
                raise ValueError("resource base is unresolved: " + raw["base"])
        resource = dict(raw)
        resource.update({
            "scope_row": False,
            "root": root,
            "narrow_root": narrow,
            "spec": spec,
            "spec_file": spec_file,
            "path": key,
            "stale": stale,
        })
        ids.add(rid)
        stable.add(key)
        result.append(resource)
    return result


def _registry_document(path):
    try:
        with open(path, "rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("cannot read registry: %s" % exc) from exc
    if not isinstance(document, dict):
        raise ValueError("registry must be a TOML table")
    return document


def _public_base(document):
    """The public base address review-host records in [process] (review-service #service-visibility), else None."""
    process = document.get("process")
    base = process.get("public_base") if isinstance(process, dict) else None
    return base if isinstance(base, str) and base else None


def _shells(document):
    """The origins box setup names as a shell, which review-host records in [process] (shell #shell-setting)."""
    process = document.get("process")
    shells = process.get("shells") if isinstance(process, dict) else None
    if not isinstance(shells, list):
        return ()
    return tuple(shell for shell in shells if isinstance(shell, str) and shell != "null" and ORIGIN_RE.fullmatch(shell))


class MountState:
    """The one mount list used by both command-line modes, the registry's scope rows, which serve no page, and its
    recorded public base address and shell origins."""

    def __init__(self, records, path=None, public_base=None, shells=()):
        self.path = os.path.realpath(path) if path else None
        self._split(records)
        self.base = public_base
        self.shell_origins = tuple(shells)
        self.signature = self._signature() if self.path else None

    def _split(self, records):
        records = tuple(records)
        self.records = tuple(record for record in records if not record.get("scope_row"))
        self.scope_rows = tuple(record for record in records if record.get("scope_row"))

    def _signature(self):
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        return info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, info.st_size

    def snapshot(self):
        if not self.path:
            return self.records
        signature = self._signature()
        if signature is None or signature == self.signature:
            return self.records
        try:
            document = _registry_document(self.path)
            records = _resource_records(document, trust=True)
        except (OSError, ValueError):
            return self.records
        self._split(records)
        self.base = _public_base(document)
        self.shell_origins = _shells(document)
        self.signature = signature
        return self.records

    def public_base(self):
        """The recorded public base address as of the registry now; box setup changes it with no restart."""
        self.snapshot()
        return self.base

    def shells(self):
        """The origins that may hold this service's pages as a shell, as of the registry now (shell
        #shell-setting-rule); box setup changes them with no restart."""
        self.snapshot()
        return self.shell_origins

    def rows(self):
        """Every registered row as of the registry now, spec rows then scope rows (comment-layer #scope)."""
        return self.snapshot() + self.scope_rows

    def hosting(self):
        """The service state holding the registry, where hosted threads live (review-service #store-hosted)."""
        return os.path.dirname(self.path) if self.path else None


def place_subject(rows, hosting, scope, subject):
    """Where a host page's thread on `subject` in `scope` is kept (comment-layer #subjects): a criterion ID naming a
    spec a live row of the scope serves is that spec's spool, at its anchor; any other subject, compared whole, is the
    scope's hosted threads, comments/<scope> in the service state. None when no row has the scope or the subject is
    empty. Returns {store, review, root, row, anchor, subject}: the spool and the directory it stays inside."""
    if not isinstance(subject, str) or not subject or not any(row.get("slug") == scope for row in rows):
        return None
    match = SPEC_SUBJECT_RE.fullmatch(subject)
    if match:
        project, spec, anchor = match.groups()
        for row in rows:
            if row.get("slug") != scope or row.get("scope_row") or row.get("stale") or row.get("spec") != spec:
                continue
            if (row.get("project") or os.path.basename(row["root"])) == project:
                return {"store": row["path"], "review": row["spec_file"] + ".review", "root": row["narrow_root"],
                        "row": row, "anchor": anchor, "subject": subject}
    if not hosting:
        return None
    return {"store": HOSTED, "review": os.path.join(hosting, "comments", scope), "root": hosting,
            "row": None, "anchor": None, "subject": subject}


def placed_event(event, placed):
    """A host page's event as its store keeps it (review-state #model-fields-hosted): a comment carries no version,
    and on a spec its subject's anchor as its block anchor, elsewhere the subject itself; other events as sent."""
    if event.get("event") != "comment":
        return event
    kept = {key: value for key, value in event.items() if key not in ("version", "subject", "anchorId")}
    if placed["anchor"] is not None:
        kept["anchorId"] = placed["anchor"]
    else:
        kept["subject"] = placed["subject"]
    return kept


def subject_events(events, placed):
    """The events of the threads on placed's subject, from its store's events: each thread the spool fold roots on the
    subject (in a spec, its place's anchor; elsewhere its subject, compared whole), with every event the fold joins to
    it (spool.fold_threads)."""
    def on_subject(root):
        if placed["anchor"] is None:
            return root["body"].get("subject") == placed["subject"]
        return ((root.get("place") or {}).get("anchorId") or root["body"].get("anchorId")) == placed["anchor"]

    held = {(event["actor"], event["name"]) for thread in spool.fold_threads(events).values()
            if on_subject(thread["ev"]) for event in thread["events"]}
    return [event for event in events if (event.get("actor"), event.get("name")) in held]


BROAD_HOME_CHILDREN = ("Desktop", "Documents", "Downloads")
NOT_GIT_REASON = "not a Git repository"


def _home_directories():
    homes = {os.path.realpath(os.path.expanduser("~"))}
    with contextlib.suppress(KeyError, OSError):
        homes.add(os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))
    return homes


def _broad_root_reason(root):
    """Why a served root is too broad, decided without Git; None for a narrow directory."""
    root = os.path.realpath(root)
    if root == os.path.dirname(root) or os.path.ismount(root):
        return "filesystem or volume root"
    if os.path.dirname(root) == os.path.dirname(os.path.dirname(root)):
        return "top-level system directory"
    if root in {os.path.realpath(path) for path in ("/tmp", "/var/tmp", tempfile.gettempdir())}:
        return "shared temporary directory"
    for home in _home_directories():
        if _inside(home, root):
            return "home directory or its ancestor"
        if root in {os.path.join(home, name) for name in BROAD_HOME_CHILDREN}:
            return "top-level home folder"
    return None


def _contains_git(root):
    """True when the tree under root holds a Git repository or worktree; symlinks are not followed."""
    for directory, directories, names in os.walk(root, followlinks=False):
        if ".git" in directories or ".git" in names:
            return True
        directories[:] = [name for name in directories if not name.endswith(".review")]
    return False


def _single_mount(root):
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit("review root must be a directory; serve a narrow review collection")
    if ".git" in root.parts:
        raise SystemExit("refusing Git metadata directory; serve a narrow review collection")
    reason = _broad_root_reason(root)
    if reason:
        raise SystemExit("refusing broad root (%s); serve a narrow review collection" % reason)
    try:
        repo = _repo_root(root)
    except RuntimeError:
        repo = None
    if repo is None:
        # Plain directory: the served directory is the whole boundary; Git features report unavailable.
        if _contains_git(root):
            raise SystemExit("refusing root that contains a Git repository; serve a narrow review collection strictly inside it")
        return {
            "id": "single-root",
            "slug": "",
            "root": str(root),
            "narrow_root": str(root),
            "spec": None,
            "base": "",
            "git": False,
        }
    if root == repo or not _inside(root, repo, strict=True):
        raise SystemExit("refusing broad root; serve a narrow review collection strictly inside its Git repository")
    return {
        "id": "single-root",
        "slug": "",
        "root": str(repo),
        "narrow_root": str(root),
        "spec": None,
        "base": "",
        "git": True,
        # The repo's name, as the index names it, so rules and their decisions hold here (project-rules #approval).
        "project": os.path.basename(str(repo)),
    }


def _decoded_path(path):
    value = unquote(path)
    if "\x00" in value:
        return None
    return value


def _safe_relative(value):
    value = value.lstrip("/")
    parts = value.split("/")
    if not value or any(part in ("", ".", "..") for part in parts):
        return None
    return "/".join(parts)


def _nest(held, tag, opens):
    """An open element's (tag, depth) as a start tag arrives: its own tag nested in it one deeper; with none open, one
    the start tag `opens` at depth 1."""
    if held:
        return (tag, held[1] + 1) if tag == held[0] else held
    return (tag, 1) if opens else None


def _unnest(held, tag):
    """An open element's (tag, depth) as an end tag arrives: its own tag one shallower, and none at its own end."""
    if held and tag == held[0]:
        return (tag, held[1] - 1) if held[1] > 1 else None
    return held


class _HeadParser(HTMLParser):
    """A page's title text, its first <title>'s alone, so an inline <svg><title> joins no page title (review-index
    #index-entry-title), the meta names and properties it declares, and its open TBDs: each `data-spec-tbd`
    marker whose value is not `later` (prompt-first-shaping #finish-tbd-later), the open decisions that block
    Accept spec (review-state #live-accept); on a mockup, its open questions (mockups #mockup-questions-markup). On a
    mockup, `illustrates`: each spec anchor its parts name in data-illustrates, to the data-anchor of each part naming
    it, in page order (mockups #illustrates-rule), never a part of a frame naming another direction, which its page
    shows unanchored (mockups #directions-pick-only); `steps`: each spec anchor a step of its walkthrough names, to the
    name of each step naming it, in order (mockups #markup-steps); `route`: a prototype mockup's route, its
    <meta name="mockup-prototype"> (mockups #prototype-file), else None; `directions`: how many directions its
    directions list names, each element in it carrying data-direction (mockups #markup-directions)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts = None
        self.meta = set()
        self.route = None
        self.open_tbds = 0
        self.illustrates = {}
        self.steps = {}
        self.walkthrough = None  # (tag, depth) of the open <ol data-mockup-steps>
        self.directions = 0
        self.directions_list = None  # (tag, depth) of the open <ul data-mockup-directions>
        self.direction_frame = None  # (tag, depth) of the open frame naming another direction (mockups #markup-frame)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.open_tbds += any(key == "data-spec-tbd" and value != "later" for key, value in attrs)
        named = next((value for key, value in attrs if key == "data-illustrates"), None)
        step = (values.get("data-step") or "").strip() if self.walkthrough else ""
        if named and step:
            for anchor in named.split():
                steps = self.steps.setdefault(anchor, [])
                if step not in steps:
                    steps.append(step)
        self.direction_frame = _nest(self.direction_frame, tag, "data-mockup-frame" in values and "data-direction" in values)
        part = values.get("data-anchor") if named and not step and not self.direction_frame else None
        for anchor in named.split() if part else ():
            parts = self.illustrates.setdefault(anchor, [])
            if part not in parts:
                parts.append(part)
        self.walkthrough = _nest(self.walkthrough, tag, "data-mockup-steps" in values)
        if self.directions_list:
            self.directions += "data-direction" in values
        self.directions_list = _nest(self.directions_list, tag, "data-mockup-directions" in values)
        if tag == "title" and self.parts is None:
            self.in_title, self.parts = True, []
        elif tag == "meta":
            self.meta.update(value.lower() for key, value in attrs if key in ("name", "property") and value)
            if values.get("name") == "mockup-prototype" and self.route is None:
                self.route = (values.get("content") or "").strip() or None

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        self.walkthrough = _unnest(self.walkthrough, tag)
        self.directions_list = _unnest(self.directions_list, tag)
        self.direction_frame = _unnest(self.direction_frame, tag)

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)

    @property
    def title(self):
        return " ".join("".join(self.parts or ()).split())


HEAD_END_RE = re.compile(rb"</head\s*>", re.IGNORECASE)
SHARE_META = (("name", "description"), ("property", "og:type"), ("property", "og:title"), ("property", "og:description"))


def _share_meta(body, path):
    """The served page with any missing description and Open Graph meta added: some reviewers' clients read them and
    throw when absent (prompt-first-shaping #public-review-anywhere). The file is untouched; the stored version and
    the ETag name this served body (review-service #index-http-caching, review-state #model-versions)."""
    end = HEAD_END_RE.search(body)
    if not end:
        return body
    parser = _HeadParser()
    parser.feed(body[:end.start()].decode("utf-8", errors="replace"))
    title = html.escape(parser.title or os.path.splitext(os.path.basename(path))[0])
    added = "".join('<meta %s="%s" content="%s">\n' % (key, name, "article" if name == "og:type" else title)
                    for key, name in SHARE_META if name not in parser.meta)
    return body[:end.start()] + added.encode("utf-8") + body[end.start():] if added else body


def page_version(body, path):
    """The version of a page, a spec or a mockup, whose file at `path` holds `body`: the SHA-256 of the body it is
    served, its ETag, the one its events record (review-state #model-versions)."""
    return hashlib.sha256(_share_meta(body, path)).hexdigest()


def _read_page(path):
    """(parser, version) of one served page file, a spec or a mockup, read once (page_version); version None when
    unreadable."""
    try:
        body = Path(path).read_bytes()
    except OSError:
        body = None
    parser = _HeadParser()
    parser.feed((body or b"").decode("utf-8", errors="replace"))
    return parser, None if body is None else page_version(body, path)


def _spec_page(path):
    """(title, open TBDs, version) of one spec file, read once (_read_page)."""
    parser, version = _read_page(path)
    return parser.title or os.path.splitext(os.path.basename(path))[0], parser.open_tbds, version


# A mockup's <title> is its screen's name, then " - mockup" (mockups #markup-table).
MOCKUP_TITLE_RE = re.compile(r"\s+-\s+mockup\Z", re.IGNORECASE)


class _Held:
    """Readings by name, each kept with the key it was read at, at most `limit` of them, the least recently asked
    dropped first: what the service keeps does not grow with the pages ever asked (review-service #steady-bound)."""

    def __init__(self, limit):
        self.limit = limit
        self._values = collections.OrderedDict()
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._values)

    def get(self, name, key):
        """(key, value) held for `name` while it was read at `key`, else None."""
        with self._lock:
            held = self._values.get(name)
            if held is None or key is None or held[0] != key:
                return None
            self._values.move_to_end(name)
            return held

    def put(self, name, key, value):
        with self._lock:
            self._values[name] = (key, value)
            self._values.move_to_end(name)
            while len(self._values) > self.limit:
                self._values.popitem(last=False)

    def keep(self, names):
        """Drop every reading but those of `names`."""
        with self._lock:
            for name in set(self._values) - set(names):
                del self._values[name]


_MOCKUP_PAGES = _Held(64)  # mockup pages whose reading is kept


def _mockup_page(path):
    """(screen, version, illustrates, steps, questions, route, directions) of one mockup file (_read_page), read again
    only when its file identity changes, so a spec page asking its set often reads each mockup once per edit. The screen
    is its title without " - mockup", else its file name without .mockup.html; questions, its open questions, each an
    open TBD (mockups #mockup-questions-markup); route, a prototype mockup's route, else None (mockups #prototype-file);
    directions, how many directions its directions list names, 0 with none (mockups #directions-file)."""
    try:
        info = os.stat(path)
        key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except OSError:
        key = None
    held = _MOCKUP_PAGES.get(path, key)
    if held:
        return held[1]
    parser, version = _read_page(path)
    screen = MOCKUP_TITLE_RE.sub("", parser.title) or os.path.basename(path)[:-len(MOCKUP_SUFFIX)]
    value = (screen, version, parser.illustrates, parser.steps, parser.open_tbds, parser.route, parser.directions)
    if key is not None and version is not None:
        _MOCKUP_PAGES.put(path, key, value)
    return value


# mockups #round-video-store: a round's files in its mockup's spool, <mockup>.review/videos/, by the commit it was
# recorded from, full or short, and its viewport; each video's captions beside it.
ROUND_FILE_RE = re.compile(r"([0-9a-f]{7,40})-(desktop|phone)\.(webm|vtt)\Z")
ROUND_TYPES = {"webm": "video/webm", "vtt": "text/vtt; charset=utf-8"}
_COMMIT_TIMES = {}  # (worktree, commit) -> its committer time; a commit never changes


def _commit_time(root, commit):
    """When `commit` was made in the worktree at `root`, in seconds; 0 when Git cannot read it."""
    key = (root, commit)
    if key not in _COMMIT_TIMES:
        out = _git(root, "show", "-s", "--format=%ct", commit + "^{commit}", optional=True) if root else None
        try:
            _COMMIT_TIMES[key] = int(out.decode().strip()) if out else 0
        except ValueError:
            _COMMIT_TIMES[key] = 0
    return _COMMIT_TIMES[key]


def round_videos(mockup, mount):
    """The rounds of a prototype mockup's videos (mockups #round-video), newest first, by when its commit was made,
    then when its files were written: each {commit, desktop, phone}, a viewport {video, captions} by file name, captions
    None without a .vtt, and absent with no video. The lane's recorder writes them; Spec Chat only reads them."""
    folder = os.path.join(os.path.realpath(mockup) + ".review", "videos")
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    rounds = {}
    for name in sorted(names):
        match = ROUND_FILE_RE.fullmatch(name)
        path = os.path.join(folder, name)
        if not match or not _inside(path, mount["narrow_root"], strict=True) or not os.path.isfile(path):
            continue
        commit, viewport, kind = match.groups()
        held = rounds.setdefault(commit, {"commit": commit, "files": {}, "written": 0})
        held["files"][(viewport, kind)] = name
        held["written"] = max(held["written"], os.stat(path).st_mtime_ns)
    result = []
    for held in rounds.values():
        entry = {"commit": held["commit"]}
        for viewport in ("desktop", "phone"):
            video = held["files"].get((viewport, "webm"))
            if video:
                entry[viewport] = {"video": video, "captions": held["files"].get((viewport, "vtt"))}
        if "desktop" in entry or "phone" in entry:
            result.append((_commit_time(mount.get("root"), held["commit"]), held["written"], entry))
    return [entry for _, _, entry in sorted(result, key=lambda item: item[:2], reverse=True)]


_GIT_DIRS = _Held(64)  # worktree -> (its git dir, the common dir its branches live in), asked of Git once
_HEADS = _Held(64)  # worktree -> the commit it is at, kept while _head_key holds


def _head_key(root):
    """What moves when a worktree's head does: its HEAD file, the branch ref HEAD names, and the packed refs, each by
    identity (inode, size, modification time); None when unreadable, so the head is asked of Git."""
    held = _GIT_DIRS.get(root, "dirs")
    if held is None:
        out = _git(root, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir", optional=True)
        found = out.decode().split("\n")[:2] if out else []
        if len(found) != 2 or not all(found):
            return None
        _GIT_DIRS.put(root, "dirs", tuple(found))
        held = ("dirs", tuple(found))
    git_dir, common = held[1]

    def identity(path):
        try:
            info = os.stat(path)
        except OSError:
            return None
        return info.st_ino, info.st_size, info.st_mtime_ns
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as stream:
            head = stream.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return None
    ref = head[len("ref: "):] if head.startswith("ref: refs/") else None
    if ref and any(part in ("", ".", "..") for part in ref.split("/")):
        return None
    return (head, identity(os.path.join(git_dir, "HEAD")), ref and identity(os.path.join(common, ref)),
            identity(os.path.join(common, "packed-refs")))


def _head_commit(mount):
    """The commit the row's worktree is at: the lane branch commit its running prototype serves; None without Git.
    Asked of Git only when the worktree's head moved (_head_key), so a page's poll forks no Git."""
    root = mount.get("root")
    if not root:
        return None
    key = _head_key(root)
    held = _HEADS.get(root, key)
    if held:
        return held[1]
    out = _git(root, "rev-parse", "--verify", "HEAD^{commit}", optional=True)
    commit = out.decode().strip() if out else None
    if commit and key is not None:
        _HEADS.put(root, key, commit)
    return commit


def _mockup_version(mount, path):
    """The version a mockup's page shows, which Accept spec names (mockups #accept-set): a drawn mockup's page version;
    a prototype mockup's round's commit, its newest round video's, else the commit its lane is at."""
    page = _mockup_page(path)
    if page[1] is None or not page[5]:
        return page[1]
    rounds = round_videos(path, mount)
    return rounds[0]["commit"] if rounds else _head_commit(mount)


def prototype_address(rows, slug):
    """Where a lane's running prototype answers (review-service #prototype-proxy): the address its spec rows last
    named, by when each was registered; None when none names one (mockups #prototype-down)."""
    named = [row for row in rows if row.get("slug") == slug and not row.get("scope_row")
             and _web_address(row.get("prototype"))]
    return max(named, key=lambda row: str(row.get("updated_at") or ""))["prototype"] if named else None


# review-service #prototype-proxy: a lane's prototype origin, <slug>-prototype. before the service's host name, a DNS
# label of at most 63 characters, so a lane whose slug makes it longer has no prototype origin.
PROTOTYPE_LABEL = "-prototype"
PROTOTYPE_LABEL_MAX = 63
# The request headers a prototype read forwards: what a browser's read negotiates, revalidates, and seeks with
# (review-service #published-ranges); never a cookie or a credential (served-pages #acceptance-origin-safe).
PROTOTYPE_REQUEST_HEADERS = ("Accept", "Accept-Language", "User-Agent", "If-None-Match", "If-Modified-Since",
                             "Range", "If-Range")
# The answer's headers it never forwards: hop-by-hop ones, a cookie or a login, and those the service writes itself.
PROTOTYPE_DROPPED = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
                     "transfer-encoding", "upgrade", "set-cookie", "set-cookie2", "www-authenticate", "content-length",
                     "cache-control", "date", "server"}
PROTOTYPE_TIMEOUT_SECONDS = 10
HEAD_OPEN_RE = re.compile(rb"<head(?:\s[^>]*)?>", re.IGNORECASE)
HTML_OPEN_RE = re.compile(rb"<html(?:\s[^>]*)?>", re.IGNORECASE)


# A script loading Spec Chat's runtime by a name the service serves it at: a spec or mockup page's .viz/runtime.js, or
# the comment layer's static/comment-layer.js.
SCRIPT_SRC_RE = re.compile(rb"""<script\b[^>]*?\ssrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
RUNTIME_SRC_RE = re.compile(rb"(?:\A|/)(?:\.viz/runtime|static/comment-layer)\.js\Z")


def prototype_suffix(public_base, own_host):
    """The host name after <slug>-prototype. at each lane's prototype origin (review-service #prototype-proxy): the
    service's own, its public base address's when one is recorded; localhost for a loopback address; None for an IP
    address off loopback, which has no prototype origin."""
    name = ((urlsplit(public_base).hostname if public_base else own_host) or "").strip("[]").rstrip(".").lower()
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return name or None
    return "localhost" if address.is_loopback else None


def prototype_label(slug):
    """The first label of a lane's prototype origin, <slug>-prototype; None when it passes a DNS label's 63 characters,
    so the lane has no prototype origin (review-service #prototype-proxy)."""
    label = slug + PROTOTYPE_LABEL
    return label if len(label) <= PROTOTYPE_LABEL_MAX else None


def prototype_lane(host, suffix, slugs):
    """The slug of the lane whose prototype origin `host` names, a Host header or a host name with any port, when
    `slugs` holds it; else None."""
    try:
        name = urlsplit("//" + host).hostname or ""
    except ValueError:
        return None
    label, _, rest = name.rstrip(".").partition(".")
    slug = label.removesuffix(PROTOTYPE_LABEL)
    return slug if suffix and rest == suffix and slug in slugs and prototype_label(slug) == label else None


def runs_spec_chat(body):
    """True when a page loads Spec Chat's runtime of its own (review-service #prototype-proxy), as its pages do run as a
    lane's prototype."""
    for match in SCRIPT_SRC_RE.finditer(body):
        src = next(group for group in match.groups() if group is not None)
        if RUNTIME_SRC_RE.search(re.split(rb"[?#]", src, maxsplit=1)[0]):
            return True
    return False


# A script source trusting scripts by token, a nonce or a hash, or 'strict-dynamic', under which a browser ignores every
# address: where a directive has one, the layer goes in by a nonce (review-service #prototype-proxy).
TRUSTS_BY_TOKEN_RE = re.compile(r"'(?:nonce-|sha256-|sha384-|sha512-|strict-dynamic')", re.IGNORECASE)


def layer_policies(policies, options, script, nonce, ancestors):
    """The Content-Security-Policy values a page the proxy adds the comment layer to is served with (review-service
    #prototype-proxy), one for each of `policies`, the values it was forwarded with, and in place of `options`, its
    X-Frame-Options values, which it drops. In each policy, the directive governing the layer's script element,
    script-src-elem, else script-src, else a script-src of default-src's sources, lets it in by `nonce`, the answer's
    own, where it trusts scripts by nonce, hash, or 'strict-dynamic', else by `script`, the service's origin; and
    `ancestors`, the origins that may frame the page, go to frame-ancestors. An X-Frame-Options that forbids framing,
    with no frame-ancestors to outrank it, stands as a policy of those ancestors, the page's own origin among them for
    SAMEORIGIN. Nothing else changes."""
    served, framed = [], False
    for value in policies:
        kept = []
        for policy in value.split(","):
            directives = [directive.split() for directive in policy.split(";") if directive.strip()]
            named = {}
            for at, directive in enumerate(directives):
                named.setdefault(directive[0].lower(), at)  # a directive named again is ignored, as a browser ignores it
            governs = next((named[name] for name in ("script-src-elem", "script-src") if name in named), None)
            if governs is None and "default-src" in named:
                directives.append(["script-src"] + directives[named["default-src"]][1:])
                governs = len(directives) - 1
            if governs is not None:
                trusts = any(TRUSTS_BY_TOKEN_RE.match(source) for source in directives[governs][1:])
                _with_sources(directives[governs], ["'nonce-%s'" % nonce if trusts else script])
            if "frame-ancestors" in named:
                framed = True
                _with_sources(directives[named["frame-ancestors"]], ancestors)
            if directives:
                kept.append("; ".join(" ".join(directive) for directive in directives))
        if kept:
            served.append(", ".join(kept))
    forbids = {word.strip().lower() for value in options for word in value.split(",") if word.strip()}
    if not framed and forbids & {"sameorigin", "deny"}:
        served.append(" ".join(["frame-ancestors"] + (["'self'"] if forbids == {"sameorigin"} else []) + ancestors))
    return served


def _with_sources(directive, sources):
    """A directive, its name then its sources, with `sources` added, any 'none' it had dropped."""
    have = [source for source in directive[1:] if source.lower() != "'none'"]
    directive[1:] = have + [source for source in sources if source.lower() not in {h.lower() for h in have}]


def _one_range(value):
    """The one byte range a Range header asks, its first and last positions as given, or None when it asks none or
    several (review-service #published-ranges)."""
    span = re.fullmatch(r"bytes=(\d*)-(\d*)", (value or "").strip())
    return span.groups() if span and span.groups() != ("", "") else None


def _prototype_page(answer):
    """Whether a prototype's answer is a page the proxy adds the comment layer to when it is whole: HTML as sent."""
    kind = (answer.getheader("Content-Type") or "").split(";")[0].strip().lower()
    return kind == "text/html" and not answer.getheader("Content-Encoding")


def with_comment_layer(body, service, origin, nonce):
    """A prototype page as the proxy serves it at its lane's prototype `origin` (mockups #prototype-route): the comment
    layer's script added first in its head, from `service`, the service's own address, naming that origin, so the layer
    takes the service from its own address (comment-layer #load) and the prototype from its tag; it carries `nonce`, the
    answer's own, which a policy trusting scripts by nonce lets in (layer_policies)."""
    tag = ('<script defer src="%s/static/comment-layer.js" data-spec-chat-prototype="%s" nonce="%s"></script>'
           % (html.escape(service, quote=True), html.escape(origin, quote=True), nonce)).encode()
    for pattern in (HEAD_OPEN_RE, HTML_OPEN_RE):
        match = pattern.search(body)
        if match:
            return body[:match.end()] + tag + body[match.end():]
    return tag + body


def _review_status(mount):
    """True when changed since the row base, False when equal, None without a row or on failure."""
    base = mount.get("base") or ""
    if not mount.get("slug") or not base or base.startswith("-"):
        return None
    env = dict(os.environ, **{"GIT_OPTIONAL_LOCKS": "0"})

    def git(*args):
        try:
            result = subprocess.run(
                ("git", "-C", mount["root"], *args),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None, ""
        return result.returncode, result.stdout.decode(errors="replace").strip()

    code, commit = git("rev-parse", "--verify", "--quiet", base + "^{commit}")
    if code != 0:
        return None
    code, current = git("hash-object", "--", mount["spec"])
    if code != 0:
        return None
    code, prior = git("rev-parse", "--verify", "--quiet", commit + ":" + mount["spec"])
    if code is None:
        return None
    return code != 0 or prior != current


def _project_id(root):
    """Last path part of the root's origin URL without .git (criterion-evidence spec)."""
    url = _git(root, "remote", "get-url", "origin", optional=True)
    name = re.split(r"[/:]", (url or b"").decode("utf-8", "replace").strip().rstrip("/"))[-1]
    return name[:-4] if name.endswith(".git") else name


def _criterion_texts(source):
    return {anchor: value["text"] for anchor, value in extract_anchors(source).items() if value["criterion"]}


def _evidence_link(base, path):
    """Join a service-relative path to the evidence base; anything else is no link."""
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return None
    return base.rstrip("/") + path


class _DeadlineReader(io.RawIOBase):
    """A socket's reads, each given only the time left before one deadline, so the reads together end by it."""

    def __init__(self, sock, deadline):
        super().__init__()
        self._sock, self._raw, self._deadline = sock, sock.makefile("rb", buffering=0), deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("the answer outlasted its deadline")
        self._sock.settimeout(left)
        return self._raw.readinto(buffer)

    def close(self):
        self._raw.close()
        super().close()


class _DeadlineResponse(http.client.HTTPResponse):
    """An answer whose reads, status, headers, and body, together end by one deadline, not each by a socket timeout:
    a provider sending its answer a byte at a time cannot hold an ask, or its thread and socket, past it."""

    def __init__(self, sock, *args, deadline, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp.close()  # the answer has read nothing yet; it reads through the deadline instead
        self.fp = io.BufferedReader(_DeadlineReader(sock, deadline))


@functools.cache
def _provider_tls():
    """Built once: building one loads the TLS trust store."""
    return ssl.create_default_context()


class _ProviderConnections:
    """The evidence provider's idle connections, kept open between asks, so a warm index load's review facts ask is one
    request on an open connection, not a new DNS, TCP, and TLS setup inside its deadline (review-index
    #index-gates-provider, #index-entry-cost). Only idle connections are kept and counted: an ask finding none idle
    opens its own, so no ask waits on another's. A connection keeps no answer: each ask is asked anew."""

    def __init__(self):
        self._lock = threading.Lock()
        self._kept = []  # (origin, connection), oldest first

    def take(self, origin):
        """(connection, kept) to origin (scheme, host, port), the newest kept one, else a new one; None when opening
        fails. Opening may outlast the ask's deadline, by at most PROVIDER_CONNECT_SECONDS."""
        with self._lock:
            for at in range(len(self._kept) - 1, -1, -1):
                if self._kept[at][0] == origin:
                    return self._kept.pop(at)[1], True
        scheme, host, port = origin
        try:
            connection = (http.client.HTTPSConnection(host, port, timeout=PROVIDER_CONNECT_SECONDS,
                                                      context=_provider_tls()) if scheme == "https"
                          else http.client.HTTPConnection(host, port, timeout=PROVIDER_CONNECT_SECONDS))
        except (ValueError, http.client.HTTPException):
            return None
        try:
            connection.connect()
        except (OSError, ValueError):
            connection.close()
            return None
        return connection, False

    def keep(self, origin, connection):
        """Keep an open connection for the next ask to origin; past PROVIDER_CONNECTIONS kept, the oldest closes."""
        with self._lock:
            self._kept.append((origin, connection))
            closing, self._kept = self._kept[:-PROVIDER_CONNECTIONS], self._kept[-PROVIDER_CONNECTIONS:]
        for _, old in closing:
            old.close()


_PROVIDER_CONNECTIONS = _ProviderConnections()


def _provider_answer(origin, target, deadline):
    """One GET of target on origin through a kept provider connection: (status, Location, body) once the whole answer
    is in by deadline (time.monotonic()), else None. A kept connection that fails short of the deadline, as one the
    provider closed while it was kept does, is dropped and the ask taken again."""
    taken = _PROVIDER_CONNECTIONS.take(origin)
    while taken:
        connection, kept = taken
        left = deadline - time.monotonic()
        if left <= 0:  # opened too late for this ask: kept, the next ask finds it open
            _PROVIDER_CONNECTIONS.keep(origin, connection)
            return None
        try:
            connection.sock.settimeout(left)
            connection.response_class = functools.partial(_DeadlineResponse, deadline=deadline)
            connection.request("GET", target)
            response = connection.getresponse()
            answer = response.status, response.getheader("Location"), response.read()
        except (OSError, ValueError, http.client.HTTPException) as error:
            connection.close()
            taken = kept and not isinstance(error, TimeoutError) and _PROVIDER_CONNECTIONS.take(origin)
            continue
        if response.will_close or connection.sock is None:
            connection.close()
        else:
            _PROVIDER_CONNECTIONS.keep(origin, connection)
        return answer
    return None


def _provider_get(base, path, query, deadline):
    """The evidence provider's one fetch, GET <base>/<path>?<query>, following redirects: its JSON answer, or None
    when the answer fails, is not JSON, or is not all in by `deadline` (time.monotonic()), redirects included."""
    url = base.rstrip("/") + "/" + path + "?" + urlencode(query)
    for _ in range(PROVIDER_REDIRECTS + 1):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        try:
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError:
            return None
        answer = _provider_answer((parts.scheme, parts.hostname, port),
                                  (parts.path or "/") + ("?" + parts.query if parts.query else ""), deadline)
        if answer is None:
            return None
        status, location, body = answer
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        if not 200 <= status < 300:
            return None
        try:
            return json.loads(body)
        except (ValueError, RecursionError):
            return None
    return None


def read_evidence(base, project, spec, commit, served, committed):
    """One evidence read for one spec at one commit; None when unavailable."""
    body = _provider_get(base, "criteria", {"spec": "project/%s::%s" % (project, spec), "commit": commit},
                         time.monotonic() + EVIDENCE_TIMEOUT_SECONDS)
    criteria = body.get("criteria") if isinstance(body, dict) else None
    if not isinstance(criteria, dict):
        return None
    current = _criterion_texts(served)
    at_head = _criterion_texts(committed)
    result = {}
    for anchor, raw in criteria.items():
        if anchor not in current or not isinstance(raw, dict):
            continue
        value = {field: raw.get(field) for field in EVIDENCE_FIELDS}
        value["artifact"] = _evidence_link(base, value["artifact"])
        value["bundle"] = _evidence_link(base, value["bundle"])
        value["view"] = _evidence_link(base, value["view"])
        value["uncommitted"] = at_head.get(anchor) != current[anchor]
        result[anchor] = value
    return result


_ROW_CACHE = {}
# The index's row reads at once: each a few git runs, so as many as keep a small box's cores busy while they wait.
INDEX_ROW_READERS = 8
# A ref base can move under an unchanged key; only a full commit id is cacheable.
_COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


_HOST_BRIDGE_END = "/* ---------------- end host bridge ---------------- */"


# scripts/design-set-sync, the one reader of a copy's design-set regions (region(), held()); the index reads the
# runtime's regions through it, never a second parser.
_SYNC_LOADER = importlib.machinery.SourceFileLoader(
    "review_serve_design_set_sync", str(Path(__file__).resolve().parents[3] / "scripts" / "design-set-sync"))
_SYNC = importlib.util.module_from_spec(importlib.util.spec_from_loader(_SYNC_LOADER.name, _SYNC_LOADER))
_SYNC_LOADER.exec_module(_SYNC)
_RUNTIME_PATH = "skill/review-spec/assets/viz/runtime.js"


def _design_set(runtime):
    """(tokens, marks, icons, find): the pinned design set as the runtime holds it, verbatim, so the index draws on the
    same set at the same pin, never a copy of a value (review-index #index-tokens, #index-marks). tokens is the block the
    chrome CSS opens with; marks and icons are {name: svg}; find is the set's Find field, {file name: text}
    (review-index #find-seam). A malformed region, which the sync names by exiting, leaves its part empty: the index
    still lists every spec."""
    def read(part, empty):
        try:
            return part()
        except (SystemExit, ValueError):
            return empty

    def tokens():
        start, end, _ = _SYNC.region(runtime, _RUNTIME_PATH)
        return runtime[start:end]

    def svgs(name):
        return {Path(path).stem: svg for path, svg in _SYNC.held(runtime, _RUNTIME_PATH, name).items()}

    def find():
        return {Path(path).name: text for path, text in _SYNC.held(runtime, _RUNTIME_PATH, _SYNC.FIND).items()}

    return (read(tokens, ""), read(lambda: svgs(_SYNC.MARKS), {}), read(lambda: svgs(_SYNC.ICONS), {}),
            read(find, {}))


def _host_bridge(runtime):
    """The runtime's host bridge block, verbatim: the index runs the spec pages' bridge, not a copy (host-bridge #bridge-tab-pages)."""
    start = runtime.index("/* ---------------- host bridge")
    end = runtime.index(_HOST_BRIDGE_END, start) + len(_HOST_BRIDGE_END)
    return runtime[start:end]


def _find_lines(*words):
    """A row's data-find mark, the set's Find item (review-index #find-seam-index): its match words, one per line, so
    typed text never matches across two."""
    return ' data-find="%s"' % html.escape("\n".join(dict.fromkeys(word for word in words if word)), quote=True)


def _index_row(mount, path, row):
    """(status, title, open TBDs, version) for one served spec, recomputed only when its file identity or row base
    changes (review-index #index-entry-cost)."""
    try:
        info = os.stat(path)
    except OSError:
        return ((_review_status(mount) if row else None),) + _spec_page(path)
    base = mount.get("base") if row else None
    key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, base)
    cached = _ROW_CACHE.get(path)
    if cached and cached[0] == key:
        return cached[1]
    value = ((_review_status(mount) if row else None),) + _spec_page(path)
    if base is None or _COMMIT_ID.fullmatch(base):
        _ROW_CACHE[path] = (key, value)
    return value


# Spool readings, for the index and each spec page asking its set: enough for every spool a busy box's index lists.
_SPOOL_CACHE = _Held(1024)


def _spool_reading(mount, path):
    """One served spec's or mockup's spool as the index reads it, `path` the file as it is served, refolded only when
    its human/ or agent/ directory modification time changes (review-index #index-entry-cost); None when unreadable.
    `counts` is (open, resolved) (#index-threads); `waiting` the statuses of its open threads; `accepted` the version
    its newest hand-off accepted when that hand-off is Accept spec, the one with no `events` list (review-state
    #live-accept), else None; `accepted_mockups` the (mockup path, version) pairs that hand-off names in the set beside
    `path` (spool.accepted_mockups)."""
    review = os.path.realpath(path) + ".review"
    times = []
    for actor in ("human", "agent"):
        try:
            times.append(os.lstat(os.path.join(review, actor)).st_mtime_ns)
        except OSError:
            times.append(None)
    times = tuple(times)
    key = (path,) + times
    cached = _SPOOL_CACHE.get(review, key)
    if cached:
        return cached[1]
    events = _read_spool_events(review, mount["narrow_root"]) if times != (None, None) else []
    if events is None:
        reading = None
    else:
        statuses = [thread["status"] for thread in spool.fold_threads(events).values()]
        resolved = statuses.count("resolved")
        newest = max((event for event in events if event["actor"] == "human" and isinstance(event["body"], dict)
                      and event["body"].get("event") == "handoff"), key=lambda event: event["name"], default=None)
        accept = newest["body"] if newest and not isinstance(newest["body"].get("events"), list) else {}
        accepted = accept.get("version")
        reading = {
            "counts": (len(statuses) - resolved, resolved),
            "waiting": frozenset(status for status in statuses if status != "resolved"),
            "accepted": accepted if isinstance(accepted, str) else None,
            "accepted_mockups": tuple(spool.accepted_mockups(path, accept)),
        }
    _SPOOL_CACHE.put(review, key, reading)
    return reading


def _set_reading(mount, path):
    """(reading, spools) of one served spec, `path` the file as it is served, read with its mockup set beside that path
    as one (review-index #index-mockups, #index-threads-source): the spool readings (_spool_reading) of the spec and of
    each mockup of its set, counts summed and open statuses joined, so a mockup's open thread or waiting reply shows on
    its spec's row; None when any of them is unreadable, since the spec's review state then cannot be read
    (#index-entry-stale). `accepted` stays the spec's own, and only while its Accept spec names each mockup of the set,
    and no other, at the version its page shows (#index-gate-sent): a screen added to the set after that hand-off is
    one it does not name (mockups #accept-set). `spools` names each spool read, so the index keeps its reading."""
    folder = spool.mockup_set(path)
    mockups = [os.path.join(folder, name) for name in _set_files(path, mount["narrow_root"])]
    readings = [_spool_reading(mount, item) for item in [path] + mockups]
    spools = [os.path.realpath(item) + ".review" for item in [path] + mockups]
    if None in readings:
        return None, spools
    named = readings[0]["accepted_mockups"]
    accepted = readings[0]["accepted"]
    if accepted and ({mockup for mockup, _ in named} != set(mockups)
                     or any(_mockup_version(mount, mockup) != version for mockup, version in named)):
        accepted = None
    return {"counts": tuple(sum(reading["counts"][at] for reading in readings) for at in (0, 1)),
            "waiting": frozenset().union(*(reading["waiting"] for reading in readings)),
            # mockups #accept-index: a mockup's open question is the spec's open TBD.
            "questions": sum(_mockup_page(mockup)[4] for mockup in mockups),
            "accepted": accepted}, spools


def _gate(open_tbds, reading, version, fact):
    """A lane spec's gate word, whether it needs you, and its status mark: the first row of review-index #index-gates
    that holds, and its mark (#index-marks): attention when it needs you, running while with the agent or sent, pass
    once accepted."""
    waiting = reading["waiting"]
    if open_tbds:
        return "Decision open", True, "attention"
    if waiting - {"draft", "pending"}:
        return "Reply waiting", True, "attention"  # an agent reply answers the thread's newest human message
    if "draft" in waiting:
        return "Draft not sent", True, "attention"
    if "pending" in waiting:
        return "With the agent", False, "running"
    if fact.get("accepted"):
        return "Accepted " + fact["accepted"][:7], False, "pass"
    if reading["accepted"] and reading["accepted"] == version:
        return "Accept spec sent", False, "running"
    return "Ready to accept", True, "attention"


def _web_address(value):
    return isinstance(value, str) and urlparse(value).scheme in ("http", "https") and bool(urlparse(value).netloc)


def read_review_facts(base, paths, lanes, deadline):
    """review-index #index-gates-provider: one GET <base>/review-facts naming each lane spec's served path and each
    lane's slug; (facts, stages): per asked path its well-formed `accepted` commit and `proof` {bundle, url}, and per
    asked lane its `stage`, in words as given, one line, a key absent when the provider has none. Both empty when the
    answer fails or is not all in by `deadline`, so no page shows a fact or a stage from it. An index load keeps none;
    an events read's stages serve its lanes' reads for STAGES_SECONDS (_ask_stages)."""
    body = _provider_get(base, "review-facts", [("path", path) for path in paths] + [("lane", lane) for lane in lanes],
                         deadline)
    specs = body.get("specs") if isinstance(body, dict) else None
    result = {}
    for path in paths if isinstance(specs, dict) else ():
        raw = specs.get(path)
        if not isinstance(raw, dict):
            continue
        fact = {}
        accepted = raw.get("accepted")
        if isinstance(accepted, str) and re.fullmatch(r"[0-9a-f]{7,64}", accepted):
            fact["accepted"] = accepted
        proof = raw.get("proof")
        if isinstance(proof, dict) and isinstance(proof.get("bundle"), str) and proof["bundle"] and _web_address(proof.get("url")):
            fact["proof"] = {"bundle": proof["bundle"], "url": proof["url"]}
        result[path] = fact
    named = body.get("lanes") if isinstance(body, dict) else None
    stages = {}
    for lane in lanes if isinstance(named, dict) else ():
        stage = named.get(lane).get("stage") if isinstance(named.get(lane), dict) else None
        words = " ".join(stage.split()) if isinstance(stage, str) else ""
        if words:
            stages[lane] = words
    return result, stages


def _ask_review_facts(base, paths, lanes=(), seconds=None):
    """Start one review facts ask beside a load's own reads, an index load's rows or a page's events; the returned wait
    answers (facts, stages), both empty without a provider or once REVIEW_FACTS_SECONDS from the ask have passed, so a
    slow provider never holds the answer; a later wait answers what has come by then. The ask's request ends by that
    same deadline, or `seconds` from the ask when given, for an answer later waits take (_ask_stages); only opening a
    connection outlasts it, by at most PROVIDER_CONNECT_SECONDS, and the next ask finds that connection kept."""
    if not base or not (paths or lanes):
        return lambda: ({}, {})
    now = time.monotonic()
    deadline = now + REVIEW_FACTS_SECONDS
    ends = now + seconds if seconds is not None else deadline
    answer = []
    asking = threading.Thread(target=lambda: answer.append(read_review_facts(base, paths, lanes, ends)), daemon=True)
    asking.start()

    def wait():
        asking.join(max(0.0, deadline - time.monotonic()))
        return answer[0] if answer else ({}, {})
    return wait


_STAGES = {}  # (provider, lanes) -> (until, wait): the stages ask serving those lanes' events reads (_ask_stages)
_STAGES_LOCK = threading.Lock()


def _ask_stages(base, lanes):
    """threads #stage-tag-source: the wait answering an events read's lanes' stages: the one review facts ask for that
    provider and lane set, started by the first read of them, serving every read of them for STAGES_SECONDS from it, its
    answer once it came, even one past its read's deadline; so pages reading every 2 s start no ask each."""
    if not base or not lanes:
        return lambda: {}
    key, now = (base, tuple(sorted(lanes))), time.monotonic()
    with _STAGES_LOCK:
        for old in [held for held, (until, _) in _STAGES.items() if until <= now]:
            del _STAGES[old]
        if key not in _STAGES:
            _STAGES[key] = (now + STAGES_SECONDS, _ask_review_facts(base, [], lanes, STAGES_SECONDS))
        wait = _STAGES[key][1]
    return lambda: wait()[1]


def _thread_lane(rows, slug):
    """threads #stage-tag-source: a review's or scope's slug is the lane of its threads that name none when it holds a
    lane's specs, a live spec row under that slug, a lane key (#index-gates); else None."""
    if not slug or _lane_label(slug)[1][0] != 0:
        return None
    return slug if any(row.get("slug") == slug and row.get("spec") and not row.get("scope_row") and not row.get("stale")
                       for row in rows) else None


def _asked_lanes(query, lane):
    """threads #stage-tag-source: the lanes an events read asks the stages of: each `lane` it names, its items' anchors'
    and its threads' comments', at most LANES_ASKED, and its own lane."""
    asked = [slug for slug in query.get("lane", []) if SLUG_RE.fullmatch(slug)][:LANES_ASKED]
    return list(dict.fromkeys(asked + ([lane] if lane else [])))


def _stages_header(lane, stages):
    """threads #stage-tag-source: what an events answer tells its page of its lanes' stages, X-Spec-Chat-Stages, the
    percent-encoded JSON {lane, stages}: `lane` the read's own lane (_thread_lane), the one a message that names no lane
    has, told also when no stage is, with no provider or a slow one, so a comment still records its lane; `stages` each
    asked lane the review facts name a stage for, slug to words. Absent when the read has no lane and no stage."""
    if not lane and not stages:
        return {}
    return {STAGES_HEADER: quote(json.dumps({"lane": lane, "stages": stages}, separators=(",", ":")), safe="")}


def _lane_label(slug):
    match = re.fullmatch(r"([a-z]+)-?([0-9]+)", slug)
    if not match:
        return slug, (1, slug, 0)
    return match.group(1).upper() + "-" + match.group(2), (0, match.group(1), int(match.group(2)))


def _own_viz_asset(path):
    decoded = _decoded_path(path)
    if not decoded:
        return False, None
    parts = decoded.split("/")
    try:
        marker = parts.index(".viz")
    except ValueError:
        return False, None
    suffix = parts[marker + 1:]
    if not suffix or any(part in ("", ".", "..") for part in suffix):
        return True, None
    here = os.path.realpath(os.path.dirname(__file__))
    candidates = [
        os.path.join(here, "viz"),
        os.path.join(here, "..", "skill", "review-spec", "assets", "viz"),
    ]
    asset_root = next((os.path.realpath(candidate) for candidate in candidates if os.path.isdir(candidate)), None)
    if not asset_root:
        return True, None
    target = os.path.realpath(os.path.join(asset_root, *suffix))
    if not _inside(target, asset_root, strict=True) or not os.path.isfile(target):
        return True, None
    return True, target


def _read_spool_events(review, root):
    """A served spool's events (spool.read_events, kept inside the collection `root`), or None when its path is unsafe
    or unreadable."""
    try:
        return spool.read_events(review, root)
    except OSError:
        return None


def _write_once(directory, name, data):
    """Store `data` as `name` in `directory`, never overwriting (review-state #trust-immutable).

    Written whole under a hidden temporary name, then linked in, so no reader sees a partial file.
    True when stored or already held with these bytes; False when held with other bytes."""
    temp = ".%s.%d.%d.tmp" % (name, os.getpid(), threading.get_ident())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temp, flags, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        try:
            os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            held = os.open(name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            with os.fdopen(held, "rb") as stream:
                return stat.S_ISREG(os.fstat(stream.fileno()).st_mode) and stream.read() == data
        return True
    finally:
        os.unlink(temp, dir_fd=directory)


def picture_bytes(picture):
    """threads #box-picture: a comment's picture as its page sends it with the comment, {type, data}, its image type and
    its bytes in base64 (shell #shell-fields-pick): (bytes, extension) when the bytes are an image of that type,
    PNG, JPEG, or WebP, of at most 1 MB; else None, a picture refused (review-service #store-pictures)."""
    extension = PICTURE_TYPES.get(picture.get("type")) if isinstance(picture, dict) else None
    data = picture.get("data") if extension else None
    if not isinstance(data, str) or len(data) > (PICTURE_BYTES + 2) // 3 * 4:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        return None
    kinds = {"png": raw.startswith(b"\x89PNG\r\n\x1a\n"), "jpg": raw.startswith(b"\xff\xd8\xff"),
             "webp": raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"}
    return (raw, extension) if 0 < len(raw) <= PICTURE_BYTES and kinds[extension] else None


def _store_picture(review, root, raw, extension):
    """Keep `pictures/<sha256>.<extension>` in the spool that keeps its comment, written once (review-service
    #store-pictures); its file name, which the comment carries in place of its bytes, or None when the name is held with
    other bytes."""
    name = hashlib.sha256(raw).hexdigest() + "." + extension
    with spool.actor_directory(review, root, "pictures", create=True) as directory:
        return name if _write_once(directory, name, raw) else None


def _write_event(review, root, actor, name, event):
    with spool.actor_directory(review, root, actor, create=True) as directory:
        return _write_once(directory, name, json.dumps(event).encode("utf-8"))


def _store_version(review, root, body):
    """Keep `versions/<sha256>.html`, the spec text a page is shown (review-state #model-versions)."""
    name = hashlib.sha256(body).hexdigest() + ".html"
    with spool.actor_directory(review, root, "versions", create=True) as directory:
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            _write_once(directory, name, body)


def _wake_review(resource):
    """The spool a wake target watches: a spec row's beside its spec, a mockup's beside it, or a scope's hosted
    threads."""
    return resource.get("review") or resource["spec_file"] + ".review"


def mockup_target_id(row_id, page):
    """The wake target of the mockup at repo path `page` in a spec row's set; its state is what /api/events reports
    on that mockup's spool."""
    return row_id + "::" + page


def _wake_batch(resource):
    """Return the zero-wait scan batch (spool.handoff_batch): each pending hand-off and the events it lists; () with
    no human/ folder; None when the spool could not be read."""
    review = _wake_review(resource)
    try:
        return spool.handoff_batch(review, spool.read_cursor(review, resource["cursor_name"]))
    except FileNotFoundError:
        return ()
    except (OSError, ValueError):
        return None


def session_state(key=None):
    """A Herdr session's review service state, hosting/<key> (review-service #service-location).

    The key is the caller's ${HERDR_SESSION:-default}, so a box without Herdr, and the box user service, are the
    default session."""
    key = key or os.environ.get("HERDR_SESSION") or "default"
    return (spec_chat_state() / "hosting" / key).resolve()


def providers_state(state):
    """Where a service reads providers/ (review-service #providers-rule): its session's service reads the box's, in the
    default session's state; a service named by --state-dir reads its own."""
    if state and Path(state).resolve() == session_state():
        return str(session_state("default"))
    return state


def read_provider(state, name, *, private=False):
    """providers/<name>.toml in the service state, read at each use (review-service #providers).

    Absent, unreadable, or (when private) not mode 0600 is None: the capability is quietly off."""
    if not state:
        return None
    try:
        with open(os.path.join(state, "providers", name + ".toml"), "rb") as stream:
            if private and stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                return None
            return tomllib.load(stream)
    except (OSError, ValueError):
        return None


def evidence_provider(state):
    """The evidence provider's base URL, or None when it is not plugged."""
    url = (read_provider(state, "evidence") or {}).get("url")
    return url.strip() if isinstance(url, str) and url.strip() else None


def jev_provider(state):
    """The Jev provider's model key, or "" when it is not plugged; the file must be mode 0600."""
    key = (read_provider(state, "jev", private=True) or {}).get("key")
    return key.strip() if isinstance(key, str) else ""


def jev_llm_model(state):
    """The box's one general LLM model, llm_model beside the Jev key, or "" (CLI picks its own default)."""
    model = (read_provider(state, "jev", private=True) or {}).get("llm_model")
    return model.strip() if isinstance(model, str) else ""


def wake_provider(state):
    """The wake provider's (check, send) argument lists, or None when it is not plugged."""
    provider = read_provider(state, "wake") or {}
    commands = tuple(provider.get(key) for key in ("check", "send"))
    for argv in commands:
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            return None
    return commands


def run_wake(argv, values, *, timeout, runs):
    """Run one wake provider command with its placeholders filled, one of the service's runs: (exit, error output).

    exit is its exit code, "unstarted" when it cannot start, or "timeout" once it outlived its timeout and its process
    group was killed. The error output is the tail of what it wrote to stderr, or why it could not start."""
    argv = [WAKE_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], arg) for arg in argv]
    # A file, not a pipe: a child the command leaves holding stderr never delays the exit.
    with tempfile.TemporaryFile() as stderr:
        try:
            code = runs.run(argv, timeout=timeout, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
        except OSError as exc:
            return "unstarted", str(exc)  # names the command, as strerror alone does not
        if code is None:
            code = "timeout"
        stderr.seek(max(0, stderr.seek(0, os.SEEK_END) - WAKE_ERROR_BYTES))
        return code, stderr.read().decode("utf-8", "replace").strip()


def wake_reason(step, code, error):
    """host-wake #wake-rule-reason: why a failed check or send failed, from its latest attempt, cut short."""
    if code == "unstarted":
        reason = f"{step} could not start: {error}"
    elif code == "timeout":
        reason = f"{step} timed out"
    elif error:
        reason = error.splitlines()[-1].strip()
    else:
        reason = f"{'owner pane not found' if step == 'check' else 'send failed'} (exit {code})"
    return reason if len(reason) <= WAKE_REASON_CHARS else reason[:WAKE_REASON_CHARS - 1] + "\u2026"


class WakeController:
    """Wake each registry row's owner pane once per unchanged hand-off batch, retrying while it fails."""

    def __init__(self, server, runs=None):
        self.server = server
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.runs = ServiceRuns() if runs is None else runs  # the wake commands it starts, which stop ends
        self.states = {}  # resource id -> (state, reason), what /api/events reports
        self.rows = {}  # resource id -> its waiting batch's wake record, for one identity (owner, batch)
        self.clock = time.monotonic  # when a failed batch's next attempt is due
        self.batches = {}  # resource id -> (its human/ folder and cursor as last read, the batch read then)
        self.sets = {}  # spec row id -> (its mockup set folder as last read, the mockup file names read then)

    def stop(self):
        """Wake ends with the service: no wake command starts after this, and the one still running is ended, its
        whole process group, and reaped, so no wake command runs on or writes once the service is gone."""
        self.stop_event.set()
        self.runs.stop()

    def status(self, resource_id):
        """The X-Spec-Chat-Wake value: the wake state, and for failed "; " and its URI-encoded reason."""
        with self.lock:
            state, reason = self.states.get(resource_id, (None, ""))
        return f"{state}; {quote(reason, safe=WAKE_REASON_SAFE)}" if reason else state

    def _log(self, resource, step, code, error):
        """host-wake #wake-rule-log: one service log line for a send or a failed check, naming the spec or scope."""
        named = ("scope=%s" % resource["scope"] if resource.get("scope") else
                 "mockup=%s" % resource["mockup"] if resource.get("mockup") else "spec=%s" % resource["spec_file"])
        print("review-serve: wake %s %s owner=%s exit=%s error=%s" % (
            step, named, resource["owner"], code, json.dumps(error, ensure_ascii=False),
        ), file=sys.stderr, flush=True)

    def _attempt(self, resource, batch, commands):
        """One wake attempt with the wake provider's commands: (step, exit, error output) of the step it ended at."""
        check, send = commands
        if resource.get("scope"):  # host-wake #wake-rule-message: hosted threads name the scope and their path
            artifact = resource["review"]
            where = f"Spec Chat human comment hand-off ready: scope {resource['scope']}, hosted threads {artifact}, "
        elif resource.get("mockup"):  # mockups #review-spool: a mockup's hand-off names the mockup, then its spec
            artifact = resource["mockup"]
            where = (f"Spec Chat human mockup review hand-off ready: mockup {artifact}, spec {resource['spec_file']}, "
                     f"collection {resource['narrow_root']}, ")
        else:
            artifact = resource["spec_file"]
            where = f"Spec Chat human spec review hand-off ready: spec {artifact}, collection {resource['narrow_root']}, "
        message = (
            f"{where}cursor {resource['cursor_name']}, {len(batch)} events. "
            "Run the zero-wait scan, process the batch, then park."
        )
        values = {"owner": resource["owner"], "artifact": artifact, "message": message}
        code, error = run_wake(check, values, timeout=WAKE_CHECK_TIMEOUT_SECONDS, runs=self.runs)
        if code != 0:
            return "check", code, error
        return ("send", *run_wake(send, values, timeout=WAKE_SEND_TIMEOUT_SECONDS, runs=self.runs))

    def _batch(self, resource):
        """The row's waiting hand-off batch, read again only when its human/ folder or its cursor changed, so
        noticing a hand-off costs two stats per row however long the review history (review-service #steady-idle)."""
        review = _wake_review(resource)
        signature, now = [review, resource["cursor_name"]], time.time_ns()
        for path in (os.path.join(review, "human"), os.path.join(review, resource["cursor_name"])):
            try:
                info = os.stat(path)
            except OSError:
                signature.append(None)
                continue
            if now - info.st_mtime_ns < WAKE_RACY_NS:
                signature = None
                break
            signature.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
        held = self.batches.get(resource["id"])
        if signature is not None and held and held[0] == signature:
            return held[1]
        batch = _wake_batch(resource)
        self.batches[resource["id"]] = (signature if batch is not None else None, batch or ())  # a failed read: again
        return batch or ()

    def _poll_row(self, resource):
        resource_id = resource["id"]
        if not resource.get("owner") or not resource.get("cursor_name"):
            return None
        batch = self._batch(resource)
        if not batch:
            return None
        identity = (resource["owner"], batch)
        now = self.clock()
        row = self.rows.get(resource_id)
        if row is None or row["identity"] != identity:  # a new batch or owner: its attempts start over
            row = self.rows[resource_id] = {"identity": identity, "sent": False, "failures": 0, "last": None, "due": now}
        if row["sent"]:
            return "sent", ""
        commands = wake_provider(self.server.state_dir)
        if commands is None:
            row["failures"] = 0
            return "unavailable", ""
        if row["failures"] >= WAKE_FAILED_ATTEMPTS and now < row["due"]:  # failed, backing off: no attempt this poll
            return "failed", row["last"][2]
        step, code, error = self._attempt(resource, batch, commands)
        reason = wake_reason(step, code, error)
        if (step, code, reason) != row["last"]:  # host-wake #wake-rule-log: a repeat of the previous attempt, no line
            self._log(resource, step, code, error)
        row["last"] = (step, code, reason)
        if step == "send" and code in (0, "timeout"):  # timeout: sending, delivery unconfirmed: never resend the batch
            row["sent"] = True
            return "sent", ""
        if step == "send" and code == 75:
            row["failures"] = 0
            return "deferred", ""
        row["failures"] += 1
        if row["failures"] < WAKE_FAILED_ATTEMPTS:
            return "retrying", ""
        # host-wake #wake-rule-retry: failed retries less and less often, at most every few minutes, never each poll
        row["due"] = now + WAKE_POLL_SECONDS * 2 ** min(row["failures"] - WAKE_FAILED_ATTEMPTS + 1, WAKE_BACKOFF_DOUBLINGS)
        return "failed", reason

    def _set_names(self, row):
        """The file names of a spec row's mockup set, listed again only when its set folder changed, so watching a
        set costs one stat per poll (review-service #steady-idle)."""
        spec = _served_file(row, row["spec"])
        folder = spool.mockup_set(spec)
        try:
            info = os.stat(folder)
        except OSError:
            self.sets.pop(row["id"], None)
            return ()
        signature = (folder, info.st_dev, info.st_ino, info.st_mtime_ns)
        held = self.sets.get(row["id"])
        if held and held[0] == signature:
            return held[1]
        names = _set_files(spec, row["narrow_root"])
        # A folder time this recent may be shared by a screen added just after the listing: list it again next poll.
        racy = time.time_ns() - info.st_mtime_ns < WAKE_RACY_NS
        self.sets[row["id"]] = (None if racy else signature, names)
        return names

    def _set_targets(self, records):
        """mockups #review-spool: each live spec row's mockups, each its spool beside it, waking the row's owner with
        the row's cursor name, as the row's own spool does; adding a screen needs no registration."""
        targets = []
        for row in records:
            if row.get("stale") or not row.get("spec") or not row.get("owner"):
                continue
            try:
                folder, names = spool.mockup_set(row["spec"]), self._set_names(row)
                # The set beside the spec's served path; each spool beside its mockup's file, as the page writes it.
                served = spool.mockup_set(_served_file(row, row["spec"]))
            except Exception as exc:  # one bad row never stops wake for the others, as in poll
                print("review-serve: wake %s mockups failed: %r" % (row.get("id"), exc), file=sys.stderr, flush=True)
                continue
            for name in names:
                mockup = os.path.join(served, name)
                targets.append({"id": mockup_target_id(row["id"], folder + "/" + name),
                                "review": os.path.realpath(mockup) + ".review",
                                "mockup": mockup, "spec_file": row["spec_file"], "narrow_root": row["narrow_root"],
                                "owner": row["owner"], "cursor_name": row["cursor_name"]})
        for row_id in set(self.sets) - {row.get("id") for row in records}:
            del self.sets[row_id]
        return tuple(targets)

    def _targets(self):
        """What a poll watches (host-wake #wake-rule-poll): every spec row's spool and its mockups' spools, and each
        scope's hosted threads, whose owner is the owner of the scope's row registered last (comment-layer
        #scope-owner)."""
        state = self.server.mount_state
        records = state.snapshot()
        hosting = state.hosting()
        sets = self._set_targets(records)
        if not hosting:
            return records + sets
        last = {}
        for index, row in enumerate(records + state.scope_rows):
            key = (row.get("registered_at") or "", index)
            if row["slug"] not in last or key >= last[row["slug"]][0]:
                last[row["slug"]] = (key, row)
        return records + sets + tuple(
            {"id": "comments:" + scope, "scope": scope, "review": os.path.join(hosting, "comments", scope),
             "owner": row["owner"], "cursor_name": row["cursor_name"]}
            for scope, (_, row) in last.items())

    def poll(self):
        states = {}
        kept = set()
        listed = set()
        with self.lock:
            previous = dict(self.states)
        for resource in self._targets():
            resource_id = resource.get("id") if isinstance(resource, dict) else None
            listed.add(resource_id)
            try:
                state = self._poll_row(resource)
            except Exception as exc:  # one bad row never stops wake for the others
                print("review-serve: wake %s failed: %r" % (resource_id, exc), file=sys.stderr, flush=True)
                kept.add(resource_id)
                if resource_id in previous:
                    states[resource_id] = previous[resource_id]
                continue
            if state is not None:
                states[resource_id] = state
        with self.lock:
            self.states = states
        for resource_id in set(self.rows) - set(states) - kept:
            del self.rows[resource_id]
        for resource_id in set(self.batches) - listed:
            del self.batches[resource_id]

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:  # keep waking other rows after an unexpected error
                print("review-serve: wake poll failed: %s" % exc, file=sys.stderr, flush=True)
            self.stop_event.wait(WAKE_POLL_SECONDS)


class MountHandler(SimpleHTTPRequestHandler):
    # Keep-alive: every response carries Content-Length, is a bodiless 204 or 304,
    # or closes (send_error); do_POST drains its body before any reply.
    protocol_version = "HTTP/1.1"
    server_version = "SpecChat/1"
    timeout = 2.0
    # The other origin this answer grants a read, a page with no origin's null included (served-pages
    # #served-no-origin), or None; decided per request.
    cross_origin = None

    @property
    def mounts(self):
        return self.server.mount_state.snapshot()

    def setup(self):
        super().setup()
        self.connection.settimeout(self.timeout)

    def parse_request(self):
        self.cross_origin = None
        if not super().parse_request():
            return False
        self.cross_origin = self._granted_origin()
        return True

    def _public(self):
        """A public service: a listener outside loopback, or a recorded public base address. A private one answers no
        origin as any other origin, so another site's frame with no origin reads nothing (served-pages #served-private)."""
        listener = ipaddress.ip_address(self.server.server_address[0])
        return not listener.is_loopback or bool(self.server.mount_state.public_base())

    def _granted_origin(self):
        """The Origin a public service grants a read (served-pages #served-no-origin): any other origin, a page with
        no origin's null included, since a host page of any site loads the comment layer; none for no Origin or the
        service's own. It stays safe only while the service has no login and no cookie (#served-safe): the grant never
        allows credentials, and an answer that sets a cookie or asks a login drops it (send_header), so adding either
        ends answers to other origins (#acceptance-origin-safe)."""
        origin = (self.headers.get("Origin") or "").lower()
        if not ORIGIN_RE.fullmatch(origin) or not self._public():
            return None
        host = (self.headers.get("Host") or "").lower()
        own = {scheme + "://" + host for scheme in ("http", "https")} if host else set()
        if not self._prototype_slug():  # at a lane's prototype origin, the service's own pages are another origin
            base = urlparse(self.server.mount_state.public_base() or "")
            own.add((base.scheme + "://" + base.netloc).lower())
        return None if origin in own else origin

    def send_header(self, keyword, value):
        if keyword.lower() in ("set-cookie", "www-authenticate"):
            self.cross_origin = None  # a cookie or a login ends this answer's grant (served-pages #acceptance-origin-safe)
        super().send_header(keyword, value)

    def end_headers(self, cache_control="no-cache"):
        self.send_header("Cache-Control", cache_control)
        if self.cross_origin:
            # Every answer, errors and 304 included, so the page reads all it reads served directly; its version tag
            # and wake state are not safelisted. No credential: the service guards nothing by origin
            # (served-pages #served-safe).
            self.send_header("Access-Control-Allow-Origin", self.cross_origin)
            self.send_header("Access-Control-Expose-Headers",
                             "ETag, X-Spec-Chat-Wake, X-Spec-Chat-Shells, X-Spec-Chat-Commit, X-Spec-Chat-Prototype, "
                             + STAGES_HEADER)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def _send_body(self, body, content_type, *, immutable=False, headers=None):
        if immutable:
            validator, cache_control = {}, "public, max-age=31536000, immutable"
        else:
            etag = '"%s"' % hashlib.sha256(body).hexdigest()
            validator, cache_control = {"ETag": etag}, "no-cache"
            tags = [tag.strip() for tag in self.headers.get("If-None-Match", "").split(",")]
            if etag in tags or "W/" + etag in tags or "*" in tags:
                # The same page's headers, which a cache takes into what it holds: a mockup page's lane commit moves
                # while its bytes stay (mockups #prototype-updated).
                self.send_response(304)
                self.send_header("ETag", etag)
                for name, header in (headers or {}).items():
                    self.send_header(name, header)
                self.end_headers()
                return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, header in {**validator, **(headers or {})}.items():
            self.send_header(name, header)
        self.end_headers(cache_control)
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        return

    def _json(self, value, code=200, headers=None):
        body = json.dumps(value).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, header in (headers or {}).items():
            self.send_header(name, header)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _index(self):
        query = urlparse(self.path).query
        entries = []
        seen = set()
        for mount in self.mounts:
            row = bool(mount.get("slug") and mount.get("spec"))
            # Only a lane-key slug (ann230) is an open lane; any other slug settles.
            lane = mount["slug"] if row and _lane_label(mount["slug"])[1][0] == 0 else None
            project = mount.get("project") or os.path.basename(mount["root"])
            for spec, path in enumerate_served_specs(mount):
                stable = _mount_prefix(mount) + spec
                if stable not in seen:
                    seen.add(stable)
                    entries.append((lane, project, mount, row, spec, path, stable))
        # Review facts are asked once per load, beside the row reads, for each lane spec it shows a gate for, and each
        # lane's slug (#index-gates-provider).
        live = [(lane, stable) for lane, _, mount, _, _, _, stable in entries if lane and not mount.get("stale")]
        wait_facts = _ask_review_facts(evidence_provider(self.server.state_dir), [stable for _, stable in live],
                                       list(dict.fromkeys(lane for lane, _ in live)))
        # Each row read side by side: a new or changed key's git compare is three git runs, so a cold load of many rows,
        # after a start or a registry change, waits on the slowest of them, not on their sum (#index-entry-cost).
        with ThreadPoolExecutor(INDEX_ROW_READERS) as readers:
            row_values = list(readers.map(lambda entry: None if entry[2].get("stale") else _index_row(
                entry[2], entry[5], entry[3]), entries))
        lanes = {}
        listed = set()
        for (lane, project, mount, row, spec, path, stable), row_value in zip(entries, row_values):
            specs = lanes.setdefault(lane, {}).setdefault(project, [])
            # What Find matches the row by: its spec name, the file name without .spec.html, and its lane's key as its
            # header reads; a row with a title, that too (#index-find).
            key = _lane_label(lane)[0] if lane else None
            name = os.path.basename(spec).removesuffix(".spec.html")
            reading = None
            if not mount.get("stale"):
                status, title, open_tbds, version = row_value
                # The spec read with its mockup set: counts and gate over their threads as one, no row for a mockup.
                reading, spools = _set_reading(mount, _served_file(mount, spec))
                listed.update([path] + spools)
            if reading is None:
                # A stale row, or a spec whose review state cannot be read: its spec path as plain text, no link, no
                # status, no gate, so it counts toward no status (#index-entry-stale).
                specs.append({"title": spec, "href": None, "status": None, "counts": None, "gate": None,
                              "needs": False, "mark": "neutral", "find": (name, key)})
                continue
            specs.append({"title": title, "status": status, "counts": reading["counts"], "stable": stable,
                          "href": ("/" if mount["slug"] else "") + quote(stable, safe="/") + (("?" + query) if query else ""),
                          # Settled specs show no gate word, and so the neutral mark (#index-gates-rule, #index-marks).
                          "inputs": (open_tbds + reading["questions"], reading, version) if lane else None,
                          "gate": None, "needs": False,
                          "mark": "neutral", "find": (title, name, key)})
        # Each cache holds the specs served now, not every spec ever served (review-service #steady-bound).
        for key in set(_ROW_CACHE) - listed:
            _ROW_CACHE.pop(key, None)
        _SPOOL_CACHE.keep(listed)
        settled = lanes.pop(None, {})
        facts, _ = wait_facts()
        for projects in lanes.values():
            for specs in projects.values():
                for spec in specs:
                    if spec.get("inputs"):
                        spec["fact"] = facts.get(spec["stable"], {})
                        spec["gate"], spec["needs"], spec["mark"] = _gate(*spec["inputs"], spec["fact"])
        # One read of the runtime per load: the set's tokens, marks, icons, and Find, and the host bridge, all from it.
        runtime = COMMENT_LAYER.read_text(encoding="utf-8")
        tokens, marks, icons, ui_find = _design_set(runtime)

        def ordered(projects):
            """(project, specs) by name; specs that need you first, then changed specs, then by title (#index-entry-order)."""
            return [(project, sorted(projects[project], key=lambda spec: (
                not spec["needs"], not spec["status"], spec["title"].casefold(), spec["href"] or "")))
                for project in sorted(projects)]

        def project_rows(rows):
            """The listing of ordered(projects) (#index-entry-table): each project's muted label, then its spec rows, each
            its status mark, its title in a column of its own, its gate word in another, and one muted meta line."""
            parts = []
            for project, specs in rows:
                parts.append('<div data-find-group><p class="project">%s</p><ul>' % html.escape(project))
                for spec in specs:
                    mark = '<span class="mark">%s</span>' % marks.get(spec["mark"], "")
                    find = _find_lines(*spec["find"])
                    if spec["href"] is None:
                        parts.append('<li class="spec stale"%s>%s<span class="title">%s</span><span class="gate">Stale</span></li>'
                                     % (find, mark, html.escape(spec["title"])))
                        continue
                    gate = '<span class="gate%s">%s</span>' % (
                        " needs" if spec["needs"] else "", html.escape(spec["gate"])) if spec["gate"] else ""
                    # Thread counts, open then resolved, each its number then its word, a status with no threads absent
                    # (#index-threads); then Changed: no word says up to date (#index-entry-title).
                    meta = ["%d %s" % (count, word) for word, count in zip(("open", "resolved"), spec["counts"] or (0, 0)) if count]
                    meta += ["Changed"] if spec["status"] else []
                    parts.append('<li class="spec"%s>%s<a class="title" href="%s">%s</a>%s%s</li>' % (
                        find, mark, html.escape(spec["href"], quote=True), html.escape(spec["title"]), gate,
                        '<span class="meta">%s</span>' % " · ".join(meta) if meta else ""))
                parts.append("</ul></div>")
            return "".join(parts)

        def lane_rows(lane, projects):
            """One lane: how many of its specs need you, and its header, then its rows; a group of rows, never a card
            (#index-entry-purpose). That one count heads the lane and places it (#index-sections)."""
            rows = ordered(projects)
            needs = sum(spec["needs"] for _, specs in rows for spec in specs)
            # The lane's Proof link: the first proof review facts name for its specs, in index order (#index-entry-lane).
            proof = next((spec["fact"]["proof"] for _, specs in rows for spec in specs
                          if spec.get("fact", {}).get("proof")), None)
            link = '<a class="proof" href="%s" target="_blank" rel="noopener" data-evidence-bundle="%s">Proof</a>' % (
                html.escape(proof["url"], quote=True), html.escape(proof["bundle"], quote=True)) if proof else ""
            key = html.escape(_lane_label(lane)[0])
            count = "%d %s you" % (needs, "needs" if needs == 1 else "need") if needs else "Nothing needs you"
            return needs, '<section class="lane" aria-label="%s" data-find-group><h3 class="lane-head"><span class="key">%s</span><span class="count">%s</span>%s</h3>%s</section>' % (
                key, key, count, link, project_rows(rows))

        # Needs you, then In progress, each lanes by issue number, each only when it holds a lane (#index-sections).
        parts = {True: [], False: []}
        for lane, projects in sorted(lanes.items(), key=lambda item: _lane_label(item[0])[1]):
            needs, rows = lane_rows(lane, projects)
            parts[needs > 0].append(rows)
        # The set's Find marks (review-index #find-seam-index): each row an item, each project, lane, and part a group, a
        # part's count its lanes, Settled a disclosure whose count is its specs.
        listing = "".join(
            '<nav class="part" aria-label="%s" data-find-group><h2>%s <span class="n" data-find-count="groups">%d</span></h2>%s</nav>' % (
                label, label, len(parts[needs]), "".join(parts[needs]))
            for needs, label in ((True, "Needs you"), (False, "In progress")) if parts[needs])
        held = sum(len(specs) for specs in settled.values())
        if settled:
            listing += '<details class="settled" data-find-group><summary>%s<span>Settled (<span data-find-count="items">%d</span>)</span></summary>%s</details>' % (
                icons.get("chevron-right", ""), held, project_rows(ordered(settled)))
        # The set's Find field under the title line, above the parts, while the index lists any spec (review-index
        # #index-find): its hint and no-match line in the index's words.
        field = ui_find.get("find.html", "").replace("{placeholder}", html.escape("Title, spec name, or lane")).replace(
            "{none}", html.escape("No spec matches"))
        listing = field + listing if listing else '<p class="empty">No Spec Chat spec pages are available.</p>'
        # The muted count line beside the title: lanes that need you, lanes in progress, settled specs.
        tally = (["%d %s you" % (len(parts[True]), "lane needs" if len(parts[True]) == 1 else "lanes need")] if parts[True] else []) + (
            ["%d in progress" % len(parts[False])] if parts[False] else []) + (["%d settled" % held] if held else [])
        top = '<header><h1>Review index</h1>%s</header>' % ('<p class="tally">%s</p>' % " · ".join(tally) if tally else "")
        body = ('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec Chat index</title><style>
%s/* index: the design set's tokens alone (review-index #index-design): quiet rows, no card, border box, stripe, or pill */
html { color-scheme: light dark; }
body { margin: 0; background: var(--ui-page); color: var(--ui-ink); font-family: var(--ui-font); font-size: var(--ui-text-sm); font-weight: var(--ui-weight-regular); line-height: 1.4; }
main { box-sizing: border-box; max-width: 48rem; margin: 0 auto; padding: var(--ui-space-6) var(--ui-space-5); }
header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: var(--ui-space-1) var(--ui-space-3); margin: 0 0 var(--ui-space-4); }
h1 { margin: 0; font-size: var(--ui-text-lg); font-weight: var(--ui-weight-semibold); line-height: 1.2; }
.tally, .count, h2 .n, .project, .meta { color: var(--ui-muted); font-weight: var(--ui-weight-regular); }
.tally { margin: 0; font-size: var(--ui-text-xs); }
h2 { margin: var(--ui-space-5) 0 0; padding: 0 var(--ui-space-1) var(--ui-space-2); border-bottom: 1px solid var(--ui-border); font-size: var(--ui-text-ui); font-weight: var(--ui-weight-semibold); }
.lane-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0 var(--ui-space-3); margin: var(--ui-space-3) 0 0; padding: 0 var(--ui-space-1); font-size: var(--ui-text-sm); font-weight: var(--ui-weight-semibold); }
.key { white-space: nowrap; }
.count { font-size: var(--ui-text-xs); }
.proof { margin-left: auto; color: var(--ui-link); font-size: var(--ui-text-xs); font-weight: var(--ui-weight-regular); text-decoration: none; }
.proof:hover, .proof:focus-visible { text-decoration: underline; }
.project { margin: var(--ui-space-1) 0 0; padding: 0 var(--ui-space-1) 0 calc(var(--ui-space-1) + var(--ui-space-4) + var(--ui-space-3)); font-size: var(--ui-text-xs); overflow-wrap: anywhere; }
ul { list-style: none; margin: 0; padding: 0; }
.spec { position: relative; display: grid; grid-template-columns: var(--ui-space-4) minmax(0, 1fr) auto; grid-template-areas: "mark title gate" ". meta meta"; align-items: start; gap: 0 var(--ui-space-3); padding: var(--ui-space-2) var(--ui-space-1); border-bottom: 1px solid var(--ui-border); }
.spec:not(.stale):hover { background: var(--ui-surface-soft); }
.mark { grid-area: mark; display: flex; align-items: center; height: calc(var(--ui-text-sm) * 1.4); }
.mark svg { display: block; }
.title { grid-area: title; align-self: baseline; min-width: 0; color: var(--ui-ink); font-weight: var(--ui-weight-medium); text-decoration: none; overflow-wrap: anywhere; }
a.title::after { content: ""; position: absolute; inset: 0; }
a.title:hover, a.title:focus-visible { text-decoration: underline; }
.stale .title { color: var(--ui-muted); font-weight: var(--ui-weight-regular); }
.gate { grid-area: gate; align-self: baseline; color: var(--ui-muted); font-size: var(--ui-text-xs); white-space: nowrap; }
.gate.needs { color: var(--ui-attention); }
.meta { grid-area: meta; font-size: var(--ui-text-xs); overflow-wrap: anywhere; }
a:focus-visible, summary:focus-visible { outline: var(--ui-focus-width) solid var(--ui-focus); outline-offset: var(--ui-focus-offset); border-radius: var(--ui-radius-sm); }
.settled { margin: var(--ui-space-5) 0 0; }
.settled summary { display: flex; align-items: center; gap: var(--ui-space-1); padding: var(--ui-space-1); cursor: pointer; list-style: none; font-size: var(--ui-text-ui); font-weight: var(--ui-weight-semibold); }
.settled summary::-webkit-details-marker { display: none; }
.settled summary svg { flex: none; color: var(--ui-muted); transition: transform var(--ui-motion-duration) var(--ui-motion-ease); }
.settled[open] summary svg { transform: rotate(90deg); }
.empty { margin: var(--ui-space-5) 0 0; color: var(--ui-muted); }
@media (max-width: 640px) {
main { padding: var(--ui-space-5) var(--ui-space-4); }
.spec { grid-template-columns: var(--ui-space-4) minmax(0, 1fr); grid-template-areas: "mark title" ". gate" ". meta"; align-content: center; min-height: calc(var(--ui-space-6) + var(--ui-space-3)); box-sizing: border-box; }
.gate { align-self: start; white-space: normal; }
.settled summary { min-height: calc(var(--ui-space-6) + var(--ui-space-3)); box-sizing: border-box; }
}
%s</style></head><body><main>%s%s</main><script>%s
listenHost();
</script><script>%s</script></body></html>''' % (tokens, ui_find.get("find.css", ""), top, listing, _host_bridge(runtime),
                                                 ui_find.get("find.js", ""))).encode("utf-8")
        self._send_body(body, "text/html; charset=utf-8")

    def _resolve_path(self, path, *, spec_only=False, mockups=False):
        """(mount, file, path as the mount serves it) of a served path. A row serves its spec and its spec's mockup
        set, never another spec or its set; spec_only takes a spec page alone, and with mockups one of its mockup
        pages too (mockups #review-same)."""
        decoded = _decoded_path(path)
        if not decoded:
            return None, None, None
        if not decoded.startswith("/"):
            decoded = "/" + decoded
        for mount in self.mounts:
            if mount.get("stale"):
                continue
            prefix = "/" + _mount_prefix(mount)
            if not mount["slug"]:
                relative = _safe_relative(decoded)
            elif decoded.startswith(prefix):
                relative = _safe_relative(decoded[len(prefix):])
            else:
                continue
            if not relative:
                continue
            target = os.path.realpath(_served_file(mount, relative))
            if not _inside(target, mount["narrow_root"]) or not os.path.isfile(target):
                continue
            if any(part.endswith(".review") or part == ".git" for part in Path(target).parts):
                continue
            if spec_only and not relative.endswith(".spec.html") and not (mockups and _set_page(mount, relative)):
                continue
            if mount.get("spec") and relative.endswith(".spec.html") and relative != mount["spec"]:
                continue
            if mount.get("spec") and relative.endswith(MOCKUP_SUFFIX) and not _set_page(mount, relative):
                continue
            return mount, target, relative
        return None, None, None

    def _route_review(self, query):
        """(mount, spool, page as the mount serves it) of a page's spool: a row's spec's, or a mockup's of its set,
        beside the mockup (mockups #review-spool); a single collection's, any HTML page's it serves."""
        raw = query.get("dir", [""])[0]
        decoded = _decoded_path(raw)
        if decoded is None:
            return None, None, None
        for mount in self.mounts:
            if mount.get("stale"):
                continue
            prefix = _mount_prefix(mount)
            relative = decoded.lstrip("/")
            if prefix and not relative.startswith(prefix):
                continue
            if prefix:
                relative = relative[len(prefix):]
            # A registry row reviews its spec and its spec's mockups; a single-root collection any HTML page it serves.
            if not relative.endswith(".html.review"):
                continue
            page = relative[:-len(".review")]
            if not mount.get("spec") and (not _safe_relative(page) or any(
                    part.startswith(".") or part.endswith(".review") for part in page.split("/"))):
                continue
            if mount.get("spec") and page != mount["spec"] and not _set_page(mount, page):
                continue
            target = os.path.realpath(_served_file(mount, page))
            review = target + ".review"
            if os.path.isfile(target) and _inside(review, mount["narrow_root"], strict=True):
                return mount, review, page
        return None, None, None

    def _baseline(self, query):
        """A spec page's or a mockup page's text at its base: the base its address names, else its spec row's base
        (mockups #before-base)."""
        mount, target, _ = self._resolve_path(query.get("path", [""])[0], spec_only=True, mockups=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        if mount.get("git") is False:
            return self._json({"error": "git baseline unavailable", "reason": NOT_GIT_REASON}, 409)
        requested = query.get("base", [mount.get("base", "")])[0]
        if requested.startswith("-"):
            return self._json({"error": "invalid base"}, 400)
        candidates = [requested] if requested else []
        if not candidates:
            try:
                candidates.append(subprocess.check_output(
                    ("git", "-C", mount["root"], "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"),
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip())
            except subprocess.CalledProcessError:
                candidates.extend(("main", "master"))
            except OSError:
                return self._json({"error": "git baseline unavailable", "reason": "git unavailable"}, 409)
        base_ref = next(
            (candidate for candidate in candidates
             if candidate and _git(mount["root"], "rev-parse", "--verify", candidate + "^{commit}", optional=True) is not None),
            None,
        )
        if not base_ref:
            return self._json({"error": "no local base ref"}, 409)
        try:
            command = ("rev-parse", "--verify", base_ref + "^{commit}") if requested else (
                "merge-base", "HEAD", base_ref,
            )
            base = _git(mount["root"], *command).decode().strip()
            repo_relative = os.path.relpath(target, mount["root"]).replace(os.sep, "/")
            prior = _git(mount["root"], "show", base + ":" + repo_relative, optional=True)
            html_base = base if prior is not None else None
            head = _git(mount["root"], "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
            committed = _git(mount["root"], "show", head + ":" + repo_relative, optional=True)
            dirty = committed is None or Path(target).read_bytes() != committed
            head_date = _git(mount["root"], "show", "-s", "--format=%cs", head).decode().strip()
            base_date = _git(mount["root"], "show", "-s", "--format=%cs", base).decode().strip()
            history = _git(
                mount["root"], "log", "--format=%H%x00%cs%x00%s", "-n", "20", head,
                "--", repo_relative,
            ).decode()
            commits = []
            for line in history.splitlines():
                commit_id, date, subject = line.split("\x00", 2)
                commits.append({"id": commit_id, "date": date, "subject": subject})
            return self._json({
                "base": base,
                "htmlBase": html_base,
                "html": prior.decode("utf-8") if prior is not None else None,
                "head": head,
                "dirty": dirty,
                "headDate": head_date,
                "baseDate": base_date,
                "commits": commits,
            })
        except (OSError, RuntimeError, UnicodeDecodeError):
            return self._json({"error": "git baseline unavailable"}, 409)

    def _scope_events(self, query):
        """GET /api/events?scope=<slug>&subject=<subject>...: {events, stores}. `events` are the threads on each
        subject, wherever kept (comment-layer #subjects), and each store's hand-offs, in file-name order; every event
        names its `store`, a spec row's path or hosted, and each thread event its `subject`. `stores` names the store
        of each subject kept anywhere, threads or none, so a page knows each store before its first comment there and
        hands off once into each (#ui-handoff). 404 when no row has the scope, so a host page reads at each live read
        whether its scope is registered (#scope-rule)."""
        scope = query.get("scope", [""])[0]
        rows, hosting = self.server.mount_state.rows(), self.server.mount_state.hosting()
        if not any(row.get("slug") == scope for row in rows):
            return self._json({"error": "unknown scope"}, 404)
        # threads #stage-tag-source: the stages of the lanes its items' anchors name, each `lane`, and of the scope's own
        # lane, asked beside the spool reads.
        lane = _thread_lane(rows, scope)
        wait_stages = _ask_stages(evidence_provider(self.server.state_dir), _asked_lanes(query, lane))
        stores, named = {}, {}
        for subject in dict.fromkeys(query.get("subject", [])):
            placed = place_subject(rows, hosting, scope, subject)
            if placed is not None:
                stores.setdefault(placed["review"], []).append(placed)
                named[subject] = placed["store"]
        result = []
        for review, placements in stores.items():
            first = placements[0]
            events = _read_spool_events(review, first["root"])
            if events is None:
                return self._json({"error": "unsafe spool path"}, 400)
            if first["row"] is not None:
                resolve_events(first["row"]["spec_file"], events)
            for event in events:
                event.setdefault("place", None)
                event["store"] = first["store"]
            for placed in placements:
                for event in subject_events(events, placed):
                    result.append({**event, "subject": placed["subject"]})
            result.extend(event for event in events
                          if event["actor"] == "human" and isinstance(event["body"], dict)
                          and event["body"].get("event") == "handoff")
        result.sort(key=lambda event: event["name"])
        return self._json({"events": result, "stores": named}, headers=_stages_header(lane, wait_stages()))

    def _events(self, query):
        if "scope" in query:
            return self._scope_events(query)
        mount, review, page = self._route_review(query)
        if not mount:
            return self._json({"error": "bad dir"}, 400)
        # threads #stage-tag-source: the stages of its review's lane and of the lanes its threads' comments recorded, each
        # `lane`, asked beside the spool read.
        lane = _thread_lane(self.mounts, mount["slug"]) if mount.get("spec") else None
        wait_stages = _ask_stages(evidence_provider(self.server.state_dir), _asked_lanes(query, lane))
        events = _read_spool_events(review, mount["narrow_root"])
        if events is None:
            return self._json({"error": "unsafe spool path"}, 400)
        events.sort(key=lambda event: event["name"])
        resolve_events(review[:-len(".review")], events)
        target = mockup_target_id(mount["id"], page) if mount.get("spec") and page != mount["spec"] else mount.get("id")
        wake = self.server.wake_controller.status(target)
        headers = {"X-Spec-Chat-Wake": wake} if wake else {}
        headers.update(_stages_header(lane, wait_stages()))
        return self._json(events, headers=headers)

    def _picture(self, query):
        """GET /api/pictures?dir=<spool>&name=<file>, or ?scope=<slug>&subject=<subject>&name=<file>: a comment's picture,
        kept in the spool that keeps the comment (review-service #store-pictures), routed as /api/events routes that
        spool. No events answer carries a picture's bytes: a focused thread loads its picture by its file (threads
        #box-picture-show). Named by its bytes' hash, so it never changes."""
        name = query.get("name", [""])[0]
        if not PICTURE_NAME_RE.fullmatch(name):
            return self.send_error(404)
        if "scope" in query:
            mount_state = self.server.mount_state
            placed = place_subject(mount_state.rows(), mount_state.hosting(), query["scope"][0],
                                   query.get("subject", [""])[0])
            review, root = (placed["review"], placed["root"]) if placed else (None, None)
        else:
            mount, review, _ = self._route_review(query)
            root = mount["narrow_root"] if mount else None
        if not review:
            return self.send_error(404)
        try:
            with spool.actor_directory(review, root, "pictures") as directory:
                descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
                with os.fdopen(descriptor, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        return self.send_error(404)
                    body = stream.read(PICTURE_BYTES + 1)
        except OSError:
            return self.send_error(404)
        return self._send_body(body, PICTURE_CONTENT[name.rsplit(".", 1)[1]], immutable=True)

    def _mockups(self, query):
        """GET /api/mockups?path=<spec page>: the spec page's mockup set (mockups #chip-source), {mockups: [...]} in
        screen order, each {page, screen, version, route, directions, illustrates, steps, open, questions}: page, its
        path in the set, mockups/<spec name>/<file>, which from the spec page's own address is the mockup page's;
        screen, its screen's name; version, the SHA-256 of the page it is served, its ETag, or a prototype mockup's
        round's commit (_mockup_version); route, a prototype mockup's route, None for a drawn one, untold when its file
        is unreadable, so its Screens index row names no kind (#screen-index-rule); directions, how many directions its
        directions list names, untold as its route is, so its row reads the count while its page shows them
        (#screen-index-directions); illustrates, each spec anchor its parts name, to those parts' anchors in page order;
        steps, each spec anchor its walkthrough's steps name, to those steps' names in order; open, its open threads by
        the spool fold, None when its spool is unreadable; questions, its open questions, None when its file is
        unreadable. A spec with no set has none. The route reads each mockup again only after an edit and each spool
        after a change."""
        mount, _, relative = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        spec = _served_file(mount, relative)
        folder = spool.mockup_set(spec)
        in_set = os.path.relpath(folder, os.path.dirname(spec)).replace(os.sep, "/") + "/"
        result = []
        for name in _set_files(spec, mount["narrow_root"]):
            path = os.path.join(folder, name)
            screen, version, illustrates, steps, questions, route, directions = _mockup_page(path)
            reading = _spool_reading(mount, path)
            result.append({"page": quote(in_set + name), "screen": screen,
                           "version": _mockup_version(mount, path) if version is not None else None,
                           **({"route": route, "directions": directions} if version is not None else {}),
                           "illustrates": illustrates, "steps": steps, "open": reading["counts"][0] if reading else None,
                           "questions": questions if version is not None else None})
        return self._json({"mockups": result})

    def _jev(self, query):
        started = time.monotonic()
        read, refused = self._jev_read(query)
        if refused:
            return self._json(*refused)
        mount, _, relative, base = read[:4]
        try:
            answer, code = self.server.jev.response(*read), 200
        except (OSError, RuntimeError, ValueError):
            answer, code = {"error": "jev unavailable"}, 503
        left = self._reader_left()
        try:
            self._json(answer, code)
        except ConnectionError:  # it left as the answer was sent: a broken pipe or a reset
            left = True
        if left:
            self.close_connection = True
        self._log_jev_read("/" + _mount_prefix(mount) + relative, base, started, answer, left)

    def _jev_read(self, query):
        """The Jev read of the spec and base the query names, which GET /api/jev answers and an owner's resolution
        makes again (jev-suggestions #ledger-resolve): its arguments and None, else None and the refusal's answer."""
        mount, target, relative = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return None, ({"error": "bad path"}, 400)
        if mount.get("git") is False:
            return None, ({"error": "jev unavailable", "reason": NOT_GIT_REASON}, 409)
        base = query.get("base", [mount.get("base", "")])[0]
        if not base or base.startswith("-"):
            return None, ({"error": "invalid base"}, 400)
        try:
            base_commit = subprocess.check_output(
                ("git", "-C", mount["root"], "rev-parse", "--verify", base + "^{commit}"),
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None, ({"error": "invalid base"}, 400)
        review = target + ".review"
        view = query.get("view", [""])[0]
        events = _read_spool_events(review, mount["narrow_root"])
        if events is None:
            return None, ({"error": "unsafe spool path"}, 400)
        return (mount, target, relative, base, events, view, self.mounts, base_commit), None

    def _reader_left(self):
        """Whether the reader closed its connection while its read ran: the socket reads as ended, no next request
        waiting on it (review-service #service-log-jev)."""
        try:
            ready = select.poll()  # poll, not select: a long-lived service's sockets may number past select's limit
            ready.register(self.connection, select.POLLIN)
            return bool(ready.poll(0)) and not self.connection.recv(1, socket.MSG_PEEK)
        except OSError:  # reset
            return True

    def _log_jev_read(self, spec, base, started, answer, left):
        """review-service #service-log-jev: one service log line for an /api/jev read as it ends, its spec and base,
        how long it took, its items and how many are pending, the ask queue's waiting and running asks, and whether
        its reader left before the answer was sent, in place of an error."""
        items = answer.get("items")
        answered = ("items=%d pending=%d" % (len(items), sum(item.get("state") == "pending" for item in items))
                    if isinstance(items, list) else "error=%s" % json.dumps(answer.get("error")))
        waiting, running = self.server.jev.queue_counts()
        print("review-serve: jev read spec=%s base=%s ms=%d %s waiting=%d running=%d%s" % (
            quote(spec), quote(base, safe="/"), (time.monotonic() - started) * 1000, answered, waiting, running,
            " reader=left" if left else "",
        ), file=sys.stderr, flush=True)

    def _evidence(self, query):
        """Criterion evidence for the viewed tree; reads only `path` (criterion-evidence spec)."""
        mount, target, _ = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        base = evidence_provider(self.server.state_dir)
        if not base or mount.get("git") is False:
            return self._json({"criteria": None, "levels": MARK_LEVELS})
        try:
            spec = os.path.relpath(target, mount["root"]).replace(os.sep, "/")
            head = _git(mount["root"], "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
            committed = _git(mount["root"], "show", head + ":" + spec, optional=True)
            served = Path(target).read_bytes()
        except (OSError, RuntimeError):
            return self._json({"criteria": None, "levels": MARK_LEVELS})
        project = _project_id(mount["root"])
        criteria = read_evidence(base, project, spec, head, served, committed) if project else None
        return self._json({"criteria": criteria, "levels": MARK_LEVELS})

    def _status(self):
        """GET /api/status (review-service #steady-status): answered at once from the process itself, no store read;
        while the provider refuses Jev's key, jev also names the pause's end and the provider's status."""
        waiting, running = self.server.jev.queue_counts()
        return self._json({"rss_bytes": _rss_bytes(), "uptime_seconds": int(time.monotonic() - self.server.started),
                           "connections": self.server.connections,
                           "jev": {"waiting": waiting, "running": running, **self.server.jev.paused()}})

    def _jev_board(self):
        """Worklane board facts from this host's own registry rows; takes no parameter."""
        try:
            return self._json(self.server.jev.board(self.mounts))
        except (OSError, RuntimeError, ValueError):
            return self._json({"error": "jev unavailable"}, 503)

    def _post_offer(self, query, body):
        """The one Jev record route: a rule card's Not for this spec or a candidate card's Not a project rule
        (project-rules #dismiss), a candidate catch-up passed over (repair-specs #catch-up-table), or an owner's resolution of an Important mark from the agent read
        (jev-suggestions #record-resolution): named by anchor, cited clause, and record, checked against the read of
        the spec and base the query names, as GET /api/jev makes it, and refused with why (#ledger-resolve)."""
        mount, target, _ = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        try:
            data = json.loads(body)
            data.get("dismiss")
        except (ValueError, AttributeError):
            return self._json({"error": "bad json"}, 400)
        if "dismiss" in data:
            # jev.dismiss is the one check of a dismissal; a refused one is a 400, and the page's re-read brings its note back.
            try:
                ok = self.server.jev.dismiss(mount, target, data.get("dismiss"), data.get("home"), data.get("record"),
                                             data.get("anchor"))
            except OSError:
                return self._json({"error": "jev unavailable"}, 503)
            return self._json({"ok": ok}, 200 if ok else 400)
        if "resolve" in data:
            read, refused = self._jev_read(query)
            if refused:
                return self._json(*refused)
            try:
                mark = (data.get("anchor"), data.get("target"), data.get("record"))
                refusal = self.server.jev.resolve(*read, mark=mark, status=data.get("resolve"), reason=data.get("reason"))
            except (OSError, RuntimeError, ValueError):
                return self._json({"error": "jev unavailable"}, 503)
            if refusal:
                return self._json({"ok": False, "error": "refused: " + refusal}, 400)
            return self._json({"ok": True})
        return self._json({"error": "body names dismiss or resolve"}, 400)

    def _post_event(self, query, body):
        """POST /api/events?dir=<spool>, a spec page's; or ?scope=<slug>&subject=<subject>, a host page's, kept where
        its subject places it (comment-layer #subjects)."""
        placed = None
        if "scope" in query:
            mount_state = self.server.mount_state
            placed = place_subject(mount_state.rows(), mount_state.hosting(), query["scope"][0],
                                   query.get("subject", [""])[0])
            if placed is None:
                return self._json({"error": "unknown scope or subject"}, 404)
            mount, review, root = None, placed["review"], placed["root"]
        else:
            mount, review, page = self._route_review(query)
            root = mount["narrow_root"] if mount else None
        actor = query.get("actor", ["human"])[0]
        if not review or actor not in ("human", "agent"):
            return self._json({"error": "bad dir or actor"}, 400)
        if actor == "agent":
            return self._json({"error": "agent spool writes are disk-only"}, 403)
        try:
            event = json.loads(body)
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        if not isinstance(event, dict):
            return self._json({"error": "bad json"}, 400)
        event_name, event_id = event.get("event"), event.get("id")
        if not isinstance(event_name, str) or not isinstance(event_id, str) or not EVENT_RE.fullmatch(event_name) or not EVENT_RE.fullmatch(event_id):
            return self._json({"error": "bad event name"}, 400)
        if event.get("actor", "human") != "human":
            return self._json({"error": "agent spool writes are disk-only"}, 403)
        if event_name == "status" and event.get("status") != "resolved":
            # Only the human resolves, and resolving is the one status a human writes (event schema): no page draws a
            # status another page sent.
            return self._json({"error": "a human status is resolved"}, 400)
        if placed is not None:
            if event_name == "handoff" and not isinstance(event.get("events"), list):
                # A host page hands off its drafts only; a hand-off with no list is Accept spec (comment-layer #ui-same).
                return self._json({"error": "a host page's hand-off lists its events"}, 400)
            event = placed_event(event, placed)
        elif event_name == "handoff" and not isinstance(event.get("events"), list) and _set_page(mount, page):
            # A mockup page's Hand off never becomes Accept spec: that is the spec page's, over its set (mockups #review).
            return self._json({"error": "a mockup page's hand-off lists its events"}, 400)
        # The page names the event (review-state #live-save); unnamed posts take the service clock.
        name = query.get("name", ["%d-%s-%s.json" % (time.time_ns(), event_name, event_id)])[0]
        stamp, _, rest = name.partition("-")
        if not stamp.isdigit() or not stamp.isascii() or rest != "%s-%s.json" % (event_name, event_id):
            return self._json({"error": "bad event file name"}, 400)
        try:
            # threads #box-picture: a comment's picture travels with it and is kept beside it, the comment naming its file.
            if "picture" in event:
                picture = picture_bytes(event["picture"]) if event_name == "comment" else None
                if picture is None:
                    return self._json({"error": "a picture is a comment's PNG, JPEG, or WebP image of at most 1 MB"}, 400)
                event = {**event, "picture": _store_picture(review, root, *picture)}
                if event["picture"] is None:
                    return self._json({"error": "picture file exists with different bytes"}, 409)
            stored = _write_event(review, root, actor, name, event)
        except OSError as exc:
            if isinstance(exc, spool.UnsafeSpool) or exc.errno in spool.UNSAFE_ERRNOS:
                return self._json({"error": "unsafe spool path"}, 400)
            # an I/O failure is not a refusal: the page keeps the event and sends it again
            return self._json({"error": "event not stored"}, 503)
        if not stored:
            return self._json({"error": "event file exists with different bytes"}, 409)
        if placed is None and event_name == "handoff" and "events" not in event:
            # Accept spec writes the hand-off without a list: open candidates are recorded ignored (project-rules
            # #dismiss-trace). A failed trace never fails the stored hand-off.
            try:
                self.server.jev.accepted(mount, review[:-len(".review")])
            except OSError as exc:
                print("review-serve: candidate trace not recorded: %r" % exc, file=sys.stderr, flush=True)
        return self._json({"ok": True, "name": name})

    def _send_file(self, path):
        handled, bundled = _own_viz_asset(path)
        vendor, mount, relative = False, None, ""
        if handled:
            decoded = _decoded_path(path) or ""
            parts = decoded.split("/")
            if any(part in ("", ".", "..") for part in parts[1:]):
                self.send_error(404)
                return
            if not any(
                (mount["slug"] and len(parts) > 1 and parts[1] == mount["slug"])
                or (not mount["slug"])
                for mount in self.mounts
            ):
                self.send_error(404)
                return
            target = bundled
            vendor = bool(bundled) and parts[parts.index(".viz") + 1] == "vendor"
        else:
            mount, target, relative = self._resolve_path(path)
        if not target:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if content_type.startswith(("video/", "audio/")):  # a page's own video seeks (threads #video-rail)
            return self._send_ranged(target, content_type)
        try:
            body = Path(target).read_bytes()
        except OSError:
            self.send_error(404)
            return
        headers = {"Last-Modified": self.date_time_string(int(os.stat(target).st_mtime))}
        if relative.endswith(".spec.html") or (mount and _set_page(mount, relative)):
            # A mockup page is served and versioned as a spec page is (review-service #store-spool-versions).
            body = _share_meta(body, target)
            try:
                _store_version(target + ".review", mount["narrow_root"], body)
            except OSError as exc:
                print("review-serve: version not stored for %s: %s" % (relative, exc), file=sys.stderr, flush=True)
        if mount and _set_page(mount, relative):
            headers.update(self._mockup_headers(mount, relative))
        self._send_body(body, content_type, immutable=vendor, headers=headers)

    def _mockup_headers(self, mount, relative):
        """A mockup page's answer names the commit its lane is at, X-Spec-Chat-Commit, so an open prototype mockup
        page sees a new lane branch commit (mockups #prototype-updated), and under a row, X-Spec-Chat-Prototype, its
        lane's prototype origin, an absolute address ending in /, or none while the service has no prototype origin or
        no row of the lane names where its prototype answers (#prototype-route). Whether the prototype answers there,
        the page reads from its frames, whose routes say hello back only once they run (#prototype-down,
        review-service #prototype-front)."""
        headers = {}
        commit = _head_commit(mount) if mount.get("git", True) else None
        if commit:
            headers["X-Spec-Chat-Commit"] = commit
        if mount.get("slug"):
            origin = self._prototype_origin(mount["slug"])
            named = prototype_address(self.server.mount_state.rows(), mount["slug"])
            headers["X-Spec-Chat-Prototype"] = origin if origin and named else "none"
        return headers

    def _rounds(self, query):
        """GET /api/rounds?path=<mockup page>: its round videos (mockups #round-video-show), {rounds: [...]} newest
        first (round_videos), none for a drawn mockup, which plays its walkthrough live (#round-video-drawn); with
        &file=<name>, one file of them, a video or its captions, in ranges as a player seeks (#round-video-store).
        Spec Chat only reads them."""
        mount, target, relative = self._resolve_path(query.get("path", [""])[0], spec_only=True, mockups=True)
        if not mount or not _set_page(mount, relative):
            return self._json({"error": "bad path"}, 400)
        if "file" not in query:
            return self._json({"rounds": round_videos(target, mount) if _mockup_page(target)[5] else []})
        name = query["file"][0]
        match = ROUND_FILE_RE.fullmatch(name)
        if not match:
            return self._json({"error": "bad file"}, 400)
        path = os.path.join(os.path.realpath(target) + ".review", "videos", name)
        if not _inside(path, mount["narrow_root"], strict=True):
            return self.send_error(404)
        self._send_ranged(path, ROUND_TYPES[match.group(3)])

    def _send_ranged(self, path, content_type):
        """A video or its captions, whole or in the one byte range a player asks as it seeks: a round's
        (#round-video-store), or one a reviewed page holds (threads #video-rail). Only the bytes it sends are read, and
        its validator is the file's identity (inode, size, modification time), so a seek never reads or hashes the whole
        file; a range asked If-Range of another version is answered whole. Every answer for it, 304 and 416 included,
        names its version and that it seeks (review-service #published-ranges)."""
        try:
            stream = open(path, "rb")
        except OSError:
            return self.send_error(404)
        with stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return self.send_error(404)
            size = info.st_size
            etag = '"%x-%x-%x"' % (info.st_ino, size, info.st_mtime_ns)
            headers = {"ETag": etag, "Last-Modified": self.date_time_string(int(info.st_mtime)), "Accept-Ranges": "bytes"}

            def bare(status, extra=()):
                self.send_response(status)
                for name, value in [*headers.items(), *extra]:
                    self.send_header(name, value)
                self.end_headers()
            span = _one_range(self.headers.get("Range"))
            if_range = self.headers.get("If-Range", "").strip()
            if not span or not size or (if_range and if_range != etag):
                tags = [tag.strip() for tag in self.headers.get("If-None-Match", "").split(",")]
                if etag in tags or "W/" + etag in tags or "*" in tags:
                    return bare(304)
                status, start, end = 200, 0, size - 1
            else:
                first, last = span
                start = size - min(int(last), size) if not first else int(first)
                end = min(int(last), size - 1) if first and last else size - 1
                if start >= size or start > end:
                    return bare(416, [("Content-Range", "bytes */%d" % size), ("Content-Length", "0")])
                status = 206
                headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            stream.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = stream.read(min(left, 1 << 16))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _own_host(self):
        """The host the service's own address names when it has no public base address: the one it prints, else its
        listener's."""
        return self.server.own_host or self.server.server_address[0]

    def _prototype_suffix(self):
        return prototype_suffix(self.server.mount_state.public_base(), self._own_host())

    def _request_port(self):
        """The port the request reached the service at, as its Host names it, a tunnel's own included; the listener's
        with no Host; None for the scheme's own."""
        host = self.headers.get("Host")
        if not host:
            return self.server.server_port
        try:
            return urlsplit("//" + host).port
        except ValueError:
            return None

    def _prototype_origin(self, slug):
        """The lane's prototype origin (review-service #prototype-proxy), an absolute address ending in /: the
        service's own address with <slug>-prototype. before its host name, as http://<slug>-prototype.localhost:<port>/
        for one private on loopback; None when its address names an IP address off loopback, or the slug makes too long
        a label."""
        suffix, label = self._prototype_suffix(), prototype_label(slug)
        if not (suffix and label):
            return None
        base = self.server.mount_state.public_base()
        scheme, port = (urlsplit(base).scheme, urlsplit(base).port) if base else ("http", self._request_port())
        return "%s://%s.%s%s/" % (scheme, label, suffix, ":%d" % port if port else "")

    def _service_address(self):
        """The service's own address as a page at a prototype origin reaches it, with no last slash: its public base
        address when one is recorded, else its own host at the port the request came in on."""
        base = self.server.mount_state.public_base()
        if base:
            return base.rstrip("/")
        host, port = self._own_host(), self._request_port()
        return "http://%s%s" % ("[%s]" % host if ":" in host else host, ":%d" % port if port else "")

    def _service_origins(self):
        """The service's own origins: its address's first, and for one on loopback, which its pages may be reached at
        by either name, both loopback names at that port."""
        own = urlsplit(self._service_address())
        origins = [own.scheme + "://" + own.netloc]
        if self._prototype_suffix() == "localhost":
            port = ":%d" % own.port if own.port else ""
            origins += [o for o in ("%s://localhost%s" % (own.scheme, port), "%s://127.0.0.1%s" % (own.scheme, port))
                        if o not in origins]
        return origins

    def _prototype_slug(self, host=None):
        """The slug of the lane whose prototype origin `host`, else the request's Host, names, while a spec row has
        that slug; else None. The service tells a prototype's request from one for its own pages by its host alone."""
        host = (self.headers.get("Host") or "") if host is None else host
        return prototype_lane(host, self._prototype_suffix(), {row.get("slug") for row in self.mounts})

    def _prototype_host(self, query):
        """GET /api/prototype-host?domain=<host>: the ask of a front that issues a certificate for each host as it is
        first asked, by the query name such a front asks with (review-service #prototype-front): 200 for a lane's
        prototype origin while a row names its slug, 404 for any other host."""
        slug = self._prototype_slug(query.get("domain", [""])[0])
        return self._json({"slug": slug}) if slug else self._json({"error": "no lane's prototype origin"}, 404)

    def _prototype(self, slug):
        """GET or HEAD at a lane's prototype origin: its running prototype, read from the address its rows last named
        at the same route, so it runs at its root as it ships (review-service #prototype-proxy). Only reads go: no
        cookie either way and no login, a page's answer with the comment layer's script added unless it runs Spec
        Chat's runtime of its own (mockups #prototype-route), its Content-Security-Policy and X-Frame-Options letting
        that script load and its mockup page, and a shell holding it, frame it, and nothing more (layer_policies). A
        read's Range goes, with its If-Range, only when it asks one byte range; a page the layer is added to is not the
        prototype's bytes, so a range of a page, or a 416, is read again whole, and that page is answered whole whatever
        range is asked (#published-ranges). A redirect within the prototype stays at its origin, its address's path
        taken off. With no address, or none answering, the answer is 502, Prototype not running, its
        X-Spec-Chat-Prototype header saying which (#prototype-down)."""
        address = prototype_address(self.server.mount_state.rows(), slug)
        if not address:
            return self._prototype_down("none")
        at = urlsplit(address)
        root = at.path.rstrip("/")
        connection = (http.client.HTTPSConnection if at.scheme == "https" else http.client.HTTPConnection)(
            at.netloc, timeout=PROTOTYPE_TIMEOUT_SECONDS)
        headers = {name: self.headers[name] for name in PROTOTYPE_REQUEST_HEADERS if self.headers.get(name)}
        if not _one_range(headers.get("Range")):  # none or several: the prototype answers whole
            headers.pop("Range", None)
            headers.pop("If-Range", None)
        headers["Accept-Encoding"] = "identity"  # the comment layer's script goes into the page as sent
        path = root + self.path
        try:
            # A HEAD is read as a GET, so its answer names the length the page is served at.
            connection.request("GET", path, headers=headers)
            answer = connection.getresponse()
            body = answer.read()
            if "Range" in headers and (answer.status == 416 or answer.status == 206 and _prototype_page(answer)):
                # A 416 names no type of its own, so the whole read decides: a page's is answered, a video's passes on.
                whole = {name: value for name, value in headers.items() if name not in ("Range", "If-Range")}
                connection.request("GET", path, headers=whole)
                again = connection.getresponse()
                if answer.status == 206 or again.status == 200 and _prototype_page(again):
                    answer, body = again, again.read()
        except (OSError, http.client.HTTPException):
            return self._prototype_down("down")
        finally:
            connection.close()
        page = answer.status == 200 and _prototype_page(answer) and not runs_spec_chat(body)
        if page:
            nonce = secrets.token_urlsafe(18)
            body = with_comment_layer(body, self._service_address(), self._prototype_origin(slug), nonce)
        self.send_response(answer.status)
        for name, value in answer.getheaders():
            lower = name.lower()
            if lower in PROTOTYPE_DROPPED or lower.startswith("access-control-"):
                continue
            if (page or answer.status == 304) and lower == "accept-ranges":
                # The page it adds the layer to never answers a range, and a 304 names no type, so it might be that
                # page's; a stored video keeps the Accept-Ranges its 200 carried (#published-ranges).
                continue
            if (page or answer.status == 304) and lower in ("content-security-policy", "x-frame-options"):
                # A page's are served as the layer needs, below; a 304's would replace the stored page's, so none go.
                continue
            if lower == "location":  # a redirect within the prototype stays at its origin, its address's path off
                moved = urlsplit(urljoin("%s://%s%s" % (at.scheme, at.netloc, path), value))
                if (moved.scheme, moved.netloc) == (at.scheme, at.netloc):
                    inside = moved.path == root or moved.path.startswith(root + "/")
                    value = ((moved.path[len(root):] or "/") if inside else moved.path) + (
                        "?" + moved.query if moved.query else "") + ("#" + moved.fragment if moved.fragment else "")
            self.send_header(name, value)
        if page:  # the page's mockup page, at the service's origins, frames it, and a shell holding that page
            own = self._service_origins()
            policies = [value for name, value in answer.getheaders() if name.lower() == "content-security-policy"]
            options = [value for name, value in answer.getheaders() if name.lower() == "x-frame-options"]
            for value in layer_policies(policies, options, own[0], nonce,
                                        own + [o for o in self.server.mount_state.shells() if o not in own]):
                self.send_header("Content-Security-Policy", value)
        if answer.status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers(answer.getheader("Cache-Control") or "no-cache")
        if self.command != "HEAD" and answer.status not in (204, 304):
            self.wfile.write(body)

    def _prototype_down(self, why):
        """mockups #prototype-down: a frame loading the route reads Prototype not running with the route at once, as
        its mockup page reads once its route says no hello."""
        body = ("<!doctype html><title>Prototype not running</title><p>Prototype not running</p><p><code>%s</code></p>\n"
                % html.escape(self.path)).encode("utf-8")
        self.send_response(502)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Spec-Chat-Prototype", why)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        slug = self._prototype_slug()
        if slug:  # review-service #prototype-proxy: every route at a lane's prototype origin is its prototype's
            return self._prototype(slug)
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            return self._index()
        if parsed.path == "/api/events":
            return self._events(query)
        if parsed.path == "/api/baseline":
            return self._baseline(query)
        if parsed.path == "/api/mockups":
            return self._mockups(query)
        if parsed.path == "/api/jev":
            return self._jev(query)
        if parsed.path == "/api/evidence":
            return self._evidence(query)
        if parsed.path == "/api/jev/board":
            return self._jev_board()
        if parsed.path == "/api/status":
            return self._status()
        if parsed.path == "/static/comment-layer.js":
            return self._comment_layer()
        if parsed.path == "/comments":
            return self._comment_ui()
        if parsed.path == "/api/rounds":
            return self._rounds(query)
        if parsed.path == "/api/pictures":
            return self._picture(query)
        if parsed.path == "/api/prototype-host":
            return self._prototype_host(query)
        return self._send_file(parsed.path)

    def _comment_layer(self):
        """The comment layer a host page loads (comment-layer #load): the runtime, served while its file is present."""
        try:
            body = COMMENT_LAYER.read_bytes()
        except OSError:
            return self.send_error(404)
        return self._send_body(body, "text/javascript; charset=utf-8")

    def _comment_ui(self):
        """The comment UI page a shell frames (shell #shell-ui-page): the runtime, from the layer's own address,
        in its comment UI role. Its answer names the origins box setup names as a shell, which every page this service
        serves and the comment layer read from it (#shell-setting-rule): none but those may hold them."""
        return self._send_body(COMMENT_UI_PAGE, "text/html; charset=utf-8",
                               headers={"X-Spec-Chat-Shells": " ".join(self.server.mount_state.shells())})

    def do_HEAD(self):
        return self.do_GET()

    def do_OPTIONS(self):
        """Another origin's preflight; GET, HEAD, and POST are safelisted, so its JSON body type is all it needs.
        Any other OPTIONS keeps the answer of a method the server lacks."""
        if not self.cross_origin:
            return self.send_error(501, "Unsupported method (%r)" % self.command)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        except ValueError:
            return self.send_error(400)
        parsed = urlparse(self.path)
        if self._prototype_slug():
            # review-service #prototype-proxy: a lane's prototype is only read through the service.
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/api/jev/offer":
            return self._post_offer(parse_qs(parsed.query), body)
        if parsed.path != "/api/events":
            return self._json({"error": "not found"}, 404)
        return self._post_event(parse_qs(parsed.query), body)


class ReviewThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    own_host = None  # the host the service's address names, as it prints it (review-service #prototype-proxy)

    def __init__(self, *args, **kwargs):
        self.started = time.monotonic()  # /api/status uptime_seconds counts from here (review-service #steady-status)
        self.connections = 0  # open connections, each one request thread (review-service #steady-connections)
        self._connections_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request_thread(self, request, client_address):
        """One connection, counted while open; the handler closes it after MountHandler.timeout without a request."""
        with self._connections_lock:
            self.connections += 1
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connections_lock:
                self.connections -= 1


def _rss_bytes():
    """This process's resident memory in bytes (/proc/self/statm), None where the system has no such file."""
    try:
        with open("/proc/self/statm", "rb") as stream:
            return int(stream.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def advertised_host(args):
    if args.host:
        return args.host
    if not args.public:
        return "127.0.0.1"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return socket.gethostname()


class _Stamped(io.TextIOBase):
    """`stream` with each line written to it started by the UTC instant the line began, to the millisecond, ending in
    Z, as a Jev record's time (jev-contract #core-time), so each line of the service log says when it was written
    (review-service #service-log): the service's own lines and any traceback alike."""

    def __init__(self, stream):
        self.stream, self.fresh, self.lock = stream, True, threading.Lock()

    def write(self, text):
        with self.lock:
            for line in re.findall(r"[^\n]*\n|[^\n]+", text):
                self.stream.write(_utc_now() + " " + line if self.fresh else line)
                self.fresh = line.endswith("\n")
        return len(text)

    def flush(self):
        self.stream.flush()

    def fileno(self):
        return self.stream.fileno()

    def isatty(self):
        return self.stream.isatty()

    def writable(self):
        return True

    @property
    def encoding(self):
        return self.stream.encoding


def main(argv=None):
    args = parse_args(argv)
    port = args.option_port if args.option_port is not None else (args.port if args.port is not None else (0 if args.public else 7160))
    bind = args.bind or ("0.0.0.0" if args.public else "127.0.0.1")
    if args.registry:
        try:
            document = _registry_document(args.registry)
            records = _resource_records(document, check_refs=True)
        except (OSError, ValueError) as exc:
            print("review-serve: %s" % exc, file=sys.stderr)
            return 2
        state = MountState(records, args.registry, _public_base(document), _shells(document))
    else:
        try:
            state = MountState([_single_mount(args.root)])
        except (OSError, RuntimeError, SystemExit) as exc:
            if isinstance(exc, SystemExit):
                raise
            print("review-serve: %s" % exc, file=sys.stderr)
            return 2
    try:
        server = ReviewThreadingHTTPServer((bind, port), MountHandler)
    except OSError as exc:
        print("review-serve: %s" % exc, file=sys.stderr)
        return 2
    server.mount_state = state
    server.own_host = advertised_host(args)
    server.state_dir = providers_state(os.path.dirname(os.path.abspath(args.registry))) if args.registry else None
    runs = ServiceRuns()  # every command the service runs, wake's and Jev's claude runs, which its stop ends
    server.jev = JevService(api_key=lambda: jev_provider(server.state_dir),
                            llm_model=lambda: jev_llm_model(server.state_dir), runs=runs)
    server.wake_controller = WakeController(server, runs)
    wake_thread = threading.Thread(target=server.wake_controller.run, name="spec-chat-wake", daemon=True)

    def stopped(signum, frame):
        """A stop (SIGTERM, from review-host stop or the service manager): every command the service runs ends, wake's
        and Jev's claude runs, then the process as before."""
        runs.stop()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    # Only the main thread receives a process signal; main run on another (a test's) leaves signals to its caller.
    process = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, stopped) if process else None
    wake_thread.start()
    try:
        print("spec-chat review-serve on http://%s:%d" % (server.own_host, server.server_port), flush=True)
        print("review URL is public and is not a secret in any security sense or an authentication boundary; stop this process when review ends", flush=True)
        server.jev.start()
        server.serve_forever()
    finally:
        if process:
            signal.signal(signal.SIGTERM, previous)
        server.wake_controller.stop()
        wake_thread.join(timeout=1)
        server.server_close()
        server.jev.stop()
    return 0


if __name__ == "__main__":
    # The service: everything it prints goes to its log (review-service #service-log), each line dated.
    sys.stdout, sys.stderr = _Stamped(sys.stdout), _Stamped(sys.stderr)
    raise SystemExit(main())
