#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root non-git-root
"""Serve one or more narrow Spec Chat review mounts."""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import html
import http.client
import io
import ipaddress
import json
import mimetypes
import os
import pwd
import re
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
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlparse, urlsplit

try:
    from jev import (MARK_LEVELS, OWNER_RESOLUTIONS, JevService, enumerate_served_specs, extract_anchors,
                     mount_prefix as _mount_prefix, spec_chat_state)
except ModuleNotFoundError:
    import importlib.util
    _jev_spec = importlib.util.spec_from_file_location("review_serve_jev", os.path.join(os.path.dirname(__file__), "jev.py"))
    _jev_module = importlib.util.module_from_spec(_jev_spec)
    _jev_spec.loader.exec_module(_jev_module)
    JevService = _jev_module.JevService
    MARK_LEVELS = _jev_module.MARK_LEVELS
    OWNER_RESOLUTIONS = _jev_module.OWNER_RESOLUTIONS
    enumerate_served_specs = _jev_module.enumerate_served_specs
    extract_anchors = _jev_module.extract_anchors
    _mount_prefix = _jev_module.mount_prefix
    spec_chat_state = _jev_module.spec_chat_state
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
# lane-hosting #index-gates-provider: how long from its ask an index load waits for the review facts, the whole answer,
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
HOSTED = "hosted"  # the store of a scope's hosted threads, as /api/events names it beside a spec row's path
# A serialized origin: a page with no origin's null, or scheme://host[:port].
ORIGIN_RE = re.compile(r"null|[a-z][a-z0-9+.-]*://[^/\s,]+")
# The comment layer is the spec page runtime itself: served at /static/comment-layer.js, it takes layer mode from that
# address (comment-layer #load), so a host page runs the spec page's comment code, not a copy of it.
COMMENT_LAYER = Path(__file__).resolve().parent / "viz" / "runtime.js"
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


class MountState:
    """The one mount list used by both command-line modes, the registry's scope rows, which serve no page, and its
    recorded public base address."""

    def __init__(self, records, path=None, public_base=None):
        self.path = os.path.realpath(path) if path else None
        self._split(records)
        self.base = public_base
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
        self.signature = signature
        return self.records

    def public_base(self):
        """The recorded public base address as of the registry now; box setup changes it with no restart."""
        self.snapshot()
        return self.base

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


class _HeadParser(HTMLParser):
    """A page's title text, the meta names and properties it declares, and its open TBDs: each `data-spec-tbd`
    marker whose value is not `later` (prompt-first-shaping #finish-tbd-later), the open decisions that block
    Accept spec (review-state #live-accept)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.parts = []
        self.meta = set()
        self.open_tbds = 0

    def handle_starttag(self, tag, attrs):
        self.open_tbds += any(key == "data-spec-tbd" and value != "later" for key, value in attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            self.meta.update(value.lower() for key, value in attrs if key in ("name", "property") and value)

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.parts.append(data)

    @property
    def title(self):
        return " ".join("".join(self.parts).split())


HEAD_END_RE = re.compile(rb"</head\s*>", re.IGNORECASE)
SHARE_META = (("name", "description"), ("property", "og:type"), ("property", "og:title"), ("property", "og:description"))


def _share_meta(body, path):
    """The served page with any missing description and Open Graph meta added: some reviewers' clients read them and
    throw when absent (prompt-first-shaping #public-review-anywhere). The file is untouched; the stored version and
    the ETag name this served body (lane-hosting #index-http-caching, review-state #model-versions)."""
    end = HEAD_END_RE.search(body)
    if not end:
        return body
    parser = _HeadParser()
    parser.feed(body[:end.start()].decode("utf-8", errors="replace"))
    title = html.escape(parser.title or os.path.splitext(os.path.basename(path))[0])
    added = "".join('<meta %s="%s" content="%s">\n' % (key, name, "article" if name == "og:type" else title)
                    for key, name in SHARE_META if name not in parser.meta)
    return body[:end.start()] + added.encode("utf-8") + body[end.start():] if added else body


def _spec_page(path):
    """(title, open TBDs, version) of one spec file, read once; the version is the SHA-256 of the body a page is
    served, the one its events record (review-state #model-versions), None when unreadable."""
    try:
        body = Path(path).read_bytes()
    except OSError:
        body = None
    parser = _HeadParser()
    parser.feed((body or b"").decode("utf-8", errors="replace"))
    title = parser.title or os.path.splitext(os.path.basename(path))[0]
    return title, parser.open_tbds, None if body is None else hashlib.sha256(_share_meta(body, path)).hexdigest()


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
    request on an open connection, not a new DNS, TCP, and TLS setup inside its deadline (lane-hosting
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
# A ref base can move under an unchanged key; only a full commit id is cacheable.
_COMMIT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


_HOST_BRIDGE_END = "/* ---------------- end host bridge ---------------- */"


def _host_bridge():
    """The runtime's host bridge block, verbatim: the index runs the spec pages' bridge, not a copy (criterion-evidence #bridge-tab-pages)."""
    runtime = Path(__file__).resolve().parent.joinpath("viz", "runtime.js").read_text(encoding="utf-8")
    start = runtime.index("/* ---------------- host bridge")
    end = runtime.index(_HOST_BRIDGE_END, start) + len(_HOST_BRIDGE_END)
    return runtime[start:end]


def _index_row(mount, path, row):
    """(status, title, open TBDs, version) for one served spec, recomputed only when its file identity or row base
    changes (lane-hosting #index-entry-cost)."""
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


_SPOOL_CACHE = {}


def _spool_reading(mount, path):
    """One served spec's spool as the index reads it, refolded only when its human/ or agent/ directory modification
    time changes (lane-hosting #index-entry-cost); None when unreadable. `counts` is (open, resolved)
    (#index-threads); `waiting` the statuses of its open threads; `accepted` the version its newest hand-off
    accepted when that hand-off is Accept spec, the one with no `events` list (review-state #live-accept), else None."""
    review = os.path.realpath(path) + ".review"
    key = []
    for actor in ("human", "agent"):
        try:
            key.append(os.lstat(os.path.join(review, actor)).st_mtime_ns)
        except OSError:
            key.append(None)
    key = tuple(key)
    cached = _SPOOL_CACHE.get(review)
    if cached and cached[0] == key:
        return cached[1]
    events = _read_spool_events(review, mount["narrow_root"]) if key != (None, None) else []
    if events is None:
        reading = None
    else:
        statuses = [thread["status"] for thread in spool.fold_threads(events).values()]
        resolved = statuses.count("resolved")
        newest = max((event for event in events if event["actor"] == "human" and isinstance(event["body"], dict)
                      and event["body"].get("event") == "handoff"), key=lambda event: event["name"], default=None)
        accepted = newest["body"].get("version") if newest and not isinstance(newest["body"].get("events"), list) else None
        reading = {
            "counts": (len(statuses) - resolved, resolved),
            "waiting": frozenset(status for status in statuses if status != "resolved"),
            "accepted": accepted if isinstance(accepted, str) else None,
        }
    _SPOOL_CACHE[review] = (key, reading)
    return reading


def _gate(open_tbds, reading, version, fact):
    """A lane spec's gate word and whether it needs you: the first row of lane-hosting #index-gates that holds."""
    waiting = reading["waiting"] if reading else frozenset()
    if open_tbds:
        return "Decision open", True
    if waiting - {"draft", "pending"}:
        return "Reply waiting", True  # an agent reply answers the thread's newest human message
    if "draft" in waiting:
        return "Draft not sent", True
    if "pending" in waiting:
        return "With the agent", False
    if fact.get("accepted"):
        return "Accepted " + fact["accepted"][:7], False
    if reading and reading["accepted"] and reading["accepted"] == version:
        return "Accept spec sent", False
    return "Ready to accept", True


def _web_address(value):
    return isinstance(value, str) and urlparse(value).scheme in ("http", "https") and bool(urlparse(value).netloc)


def read_review_facts(base, paths, deadline):
    """lane-hosting #index-gates-provider: one GET <base>/review-facts naming each lane spec's served path; per asked
    path its well-formed `accepted` commit and `proof` {bundle, url}, a key absent when the provider has none. {} when
    the answer fails or is not all in by `deadline`, so the index shows neither fact. Asked once per index load and
    never kept."""
    body = _provider_get(base, "review-facts", [("path", path) for path in paths], deadline)
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
    return result


def _ask_review_facts(base, paths):
    """Start an index load's one review facts ask beside its row reads; the returned wait answers the facts, or {}
    without a provider or once REVIEW_FACTS_SECONDS from the ask have passed, so a slow provider never holds the page.
    The ask's request ends by that same deadline; only opening a connection outlasts it, by at most
    PROVIDER_CONNECT_SECONDS, and the next ask finds that connection kept."""
    if not base or not paths:
        return dict
    deadline = time.monotonic() + REVIEW_FACTS_SECONDS
    answer = []
    asking = threading.Thread(target=lambda: answer.append(read_review_facts(base, paths, deadline)), daemon=True)
    asking.start()

    def wait():
        asking.join(max(0.0, deadline - time.monotonic()))
        return answer[0] if answer else {}
    return wait


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
    """The spool a wake target watches: a spec row's beside its spec, or a scope's hosted threads."""
    return resource.get("review") or resource["spec_file"] + ".review"


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


def run_wake(argv, values, *, timeout):
    """Run one wake provider command with its placeholders filled: (exit, error output).

    exit is its exit code, "unstarted" when it cannot start, or "timeout" once it outlived its timeout and its process
    group was killed. The error output is the tail of what it wrote to stderr, or why it could not start."""
    argv = [WAKE_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], arg) for arg in argv]
    # A file, not a pipe: a child the command leaves holding stderr never delays the exit.
    with tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True,
            )
        except OSError as exc:
            return "unstarted", str(exc)  # names the command, as strerror alone does not
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
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

    def __init__(self, server):
        self.server = server
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.states = {}  # resource id -> (state, reason), what /api/events reports
        self.rows = {}  # resource id -> its waiting batch's wake record, for one identity (owner, batch)
        self.clock = time.monotonic  # when a failed batch's next attempt is due
        self.batches = {}  # resource id -> (its human/ folder and cursor as last read, the batch read then)

    def status(self, resource_id):
        """The X-Spec-Chat-Wake value: the wake state, and for failed "; " and its URI-encoded reason."""
        with self.lock:
            state, reason = self.states.get(resource_id, (None, ""))
        return f"{state}; {quote(reason, safe=WAKE_REASON_SAFE)}" if reason else state

    def _log(self, resource, step, code, error):
        """host-wake #wake-rule-log: one service log line for a send or a failed check, naming the spec or scope."""
        named = "scope=%s" % resource["scope"] if resource.get("scope") else "spec=%s" % resource["spec_file"]
        print("review-serve: wake %s %s owner=%s exit=%s error=%s" % (
            step, named, resource["owner"], code, json.dumps(error, ensure_ascii=False),
        ), file=sys.stderr, flush=True)

    def _attempt(self, resource, batch, commands):
        """One wake attempt with the wake provider's commands: (step, exit, error output) of the step it ended at."""
        check, send = commands
        if resource.get("scope"):  # host-wake #wake-rule-message: hosted threads name the scope and their path
            artifact = resource["review"]
            where = f"Spec Chat human comment hand-off ready: scope {resource['scope']}, hosted threads {artifact}, "
        else:
            artifact = resource["spec_file"]
            where = f"Spec Chat human spec review hand-off ready: spec {artifact}, collection {resource['narrow_root']}, "
        message = (
            f"{where}cursor {resource['cursor_name']}, {len(batch)} events. "
            "Run the zero-wait scan, process the batch, then park."
        )
        values = {"owner": resource["owner"], "artifact": artifact, "message": message}
        code, error = run_wake(check, values, timeout=WAKE_CHECK_TIMEOUT_SECONDS)
        if code != 0:
            return "check", code, error
        return ("send", *run_wake(send, values, timeout=WAKE_SEND_TIMEOUT_SECONDS))

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

    def _targets(self):
        """What a poll watches (host-wake #wake-rule-poll): every spec row's spool, and each scope's hosted threads,
        whose owner is the owner of the scope's row registered last (comment-layer #scope-owner)."""
        state = self.server.mount_state
        records = state.snapshot()
        hosting = state.hosting()
        if not hosting:
            return records
        last = {}
        for index, row in enumerate(records + state.scope_rows):
            key = (row.get("registered_at") or "", index)
            if row["slug"] not in last or key >= last[row["slug"]][0]:
                last[row["slug"]] = (key, row)
        return records + tuple(
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
    # The other origin this answer grants a read, a page with no origin's null included (review-service
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
        origin as any other origin, so another site's frame with no origin reads nothing (review-service #served-private)."""
        listener = ipaddress.ip_address(self.server.server_address[0])
        return not listener.is_loopback or bool(self.server.mount_state.public_base())

    def _granted_origin(self):
        """The Origin a public service grants a read (review-service #served-no-origin): any other origin, a page with
        no origin's null included, since a host page of any site loads the comment layer; none for no Origin or the
        service's own. It stays safe only while the service has no login and no cookie (#served-safe): the grant never
        allows credentials, and an answer that sets a cookie or asks a login drops it (send_header), so adding either
        ends answers to other origins (#acceptance-origin-safe)."""
        origin = (self.headers.get("Origin") or "").lower()
        if not ORIGIN_RE.fullmatch(origin) or not self._public():
            return None
        host = (self.headers.get("Host") or "").lower()
        own = {scheme + "://" + host for scheme in ("http", "https")} if host else set()
        base = urlparse(self.server.mount_state.public_base() or "")
        own.add((base.scheme + "://" + base.netloc).lower())
        return None if origin in own else origin

    def send_header(self, keyword, value):
        if keyword.lower() in ("set-cookie", "www-authenticate"):
            self.cross_origin = None  # a cookie or a login ends this answer's grant (#acceptance-origin-safe)
        super().send_header(keyword, value)

    def end_headers(self, cache_control="no-cache"):
        self.send_header("Cache-Control", cache_control)
        if self.cross_origin:
            # Every answer, errors and 304 included, so the page reads all it reads served directly; its version tag
            # and wake state are not safelisted. No credential: the service guards nothing by origin (#served-safe).
            self.send_header("Access-Control-Allow-Origin", self.cross_origin)
            self.send_header("Access-Control-Expose-Headers", "ETag, X-Spec-Chat-Wake")
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
                self.send_response(304)
                self.send_header("ETag", etag)
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
        # Review facts are asked once per load, beside the row reads, for each lane spec it shows a gate for.
        wait_facts = _ask_review_facts(evidence_provider(self.server.state_dir), [
            stable for lane, _, mount, _, _, _, stable in entries if lane and not mount.get("stale")])
        lanes = {}
        listed = set()
        for lane, project, mount, row, spec, path, stable in entries:
            specs = lanes.setdefault(lane, {}).setdefault(project, [])
            if mount.get("stale"):
                # A stale row: its spec path as plain text, no link, no status, no gate (#index-entry-stale).
                specs.append({"title": spec, "href": None, "status": None, "gate": None, "needs": False})
                continue
            status, title, open_tbds, version = _index_row(mount, path, row)
            reading = _spool_reading(mount, path)
            specs.append({"title": title, "status": status, "counts": reading and reading["counts"], "stable": stable,
                          "href": ("/" if mount["slug"] else "") + quote(stable, safe="/") + (("?" + query) if query else ""),
                          # Settled specs show no gate word (#index-gates-rule).
                          "inputs": (open_tbds, reading, version) if lane else None, "gate": None, "needs": False})
            listed.update((path, os.path.realpath(path) + ".review"))
        # Each cache holds the specs served now, not every spec ever served (review-service #steady-bound).
        for cache in (_ROW_CACHE, _SPOOL_CACHE):
            for key in set(cache) - listed:
                cache.pop(key, None)
        settled = lanes.pop(None, {})
        facts = wait_facts()
        for projects in lanes.values():
            for specs in projects.values():
                for spec in specs:
                    if spec.get("inputs"):
                        spec["fact"] = facts.get(spec["stable"], {})
                        spec["gate"], spec["needs"] = _gate(*spec["inputs"], spec["fact"])

        def ordered(projects):
            """(project, specs) by name; specs that need you first, then changed specs, then by title (#index-entry-order)."""
            return [(project, sorted(projects[project], key=lambda spec: (
                not spec["needs"], not spec["status"], spec["title"].casefold(), spec["href"] or "")))
                for project in sorted(projects)]

        def project_rows(rows):
            """The listing of ordered(projects)."""
            parts = []
            for project, specs in rows:
                parts.append('<p class="label">%s</p><ul>' % html.escape(project))
                for spec in specs:
                    if spec["href"] is None:
                        parts.append('<li><div class="row"><span class="stale-path">%s</span><span class="pills"><span class="pill muted">Stale</span></span></div></li>' % html.escape(spec["title"]))
                        continue
                    # The gate word, muted unless it needs you, then Changed: no word says up to date (#index-entry-title).
                    pills = '<span class="pill%s">%s</span>' % ("" if spec["needs"] else " muted", html.escape(spec["gate"])) if spec["gate"] else ""
                    pills += '<span class="pill">Changed</span>' if spec["status"] else ""
                    pills = '<span class="pills">%s</span>' % pills if pills else ""
                    # Count pills, Open then Resolved; a status with no threads has no pill (#index-threads).
                    tallies = "".join(
                        '<span class="tally %s"><b>%d</b> %s</span>' % (word.lower(), count, word)
                        for word, count in zip(("Open", "Resolved"), spec["counts"] or (0, 0)) if count)
                    parts.append('<li><div class="row"><a href="%s">%s</a>%s</div>%s</li>' % (
                        html.escape(spec["href"], quote=True), html.escape(spec["title"]), pills,
                        '<div class="tallies">%s</div>' % tallies if tallies else ""))
                parts.append('</ul>')
            return "".join(parts)

        def card(lane, projects):
            rows = ordered(projects)
            needs = sum(spec["needs"] for _, specs in rows for spec in specs)
            # The lane's Proof link: the first proof review facts name for its specs, in index order (#index-entry-lane).
            proof = next((spec["fact"]["proof"] for _, specs in rows for spec in specs
                          if spec.get("fact", {}).get("proof")), None)
            link = '<a class="proof" href="%s" target="_blank" rel="noopener" data-evidence-bundle="%s">Proof</a>' % (
                html.escape(proof["url"], quote=True), html.escape(proof["bundle"], quote=True)) if proof else ""
            key = html.escape(_lane_label(lane)[0])
            return '<section class="lane%s" aria-label="%s"><p class="lane-head"><span class="key">%s</span><span class="count">%s</span>%s</p>%s</section>' % (
                " needs" if needs else "", key, key, "%d need you" % needs if needs else "Nothing needs you", link,
                project_rows(rows))

        # Needs you, then In progress, each lanes by issue number, each only when it holds a card (#index-sections).
        parts = {True: [], False: []}
        for lane, projects in sorted(lanes.items(), key=lambda item: _lane_label(item[0])[1]):
            parts[any(spec["needs"] for specs in projects.values() for spec in specs)].append(card(lane, projects))
        listing = "".join(
            '<p class="label">%s</p><nav class="lanes" aria-label="%s">%s</nav>' % (label, label, "\n".join(parts[needs]))
            for needs, label in ((True, "Needs you"), (False, "In progress")) if parts[needs])
        if settled:
            listing += '<details class="settled"><summary>Settled (%d)</summary><div class="card">%s</div></details>' % (
                sum(len(specs) for specs in settled.values()), project_rows(ordered(settled)))
        listing = listing or '<p class="empty">No Spec Chat spec pages are available.</p>'
        body = ('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec Chat index</title><style>
/* ui tokens */
:root {
  --ui-font: "Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --ui-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --ui-text-xs: 12px; --ui-text-sm: 14px; --ui-text-md: 16px; --ui-text-lg: 20px;
  --ui-page: #ffffff; --ui-surface: #ffffff; --ui-ink: #333333; --ui-muted: #525252; --ui-border: #dfdfdf;
  --ui-link: #333333; --ui-focus: #262626; --ui-action: #262626;
  --ui-pass: #005c32; --ui-pass-soft: #e6efea;
  --ui-fail: #a5000f; --ui-fail-soft: #f6e6e7;
  --ui-attention: #b0540e; --ui-attention-soft: #faf4ef;
  --ui-muted-soft: #eeeeee;
  --ui-draft: #b0540e; --ui-draft-soft: #faf4ef;
  --ui-resolved: #005c32; --ui-resolved-soft: #e6efea;
  --ui-space-1: 4px; --ui-space-2: 8px; --ui-space-3: 12px; --ui-space-4: 16px; --ui-space-5: 24px; --ui-space-6: 32px;
  --ui-radius: 8px; --ui-radius-sm: 6px; --ui-radius-pill: 999px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --ui-page: #1a1a1a; --ui-surface: #242424; --ui-ink: #e0e0e0; --ui-muted: #999999; --ui-border: #3a3a3a;
    --ui-link: #e0e0e0; --ui-focus: #cccccc; --ui-action: #cccccc;
    --ui-attention: #e5873a; --ui-attention-soft: #2e2218; --ui-draft: #e5873a; --ui-draft-soft: #2e2218;
    --ui-pass: #3daa6e; --ui-pass-soft: #1a2e22; --ui-resolved: #3daa6e; --ui-resolved-soft: #1a2e22;
    --ui-fail: #ffb4ab; --ui-fail-soft: #2e1a1c;
  }
}
html { color-scheme: light dark; }
body { margin: 0; background: var(--ui-page); color: var(--ui-ink); font-family: var(--ui-font); font-size: var(--ui-text-sm); font-weight: 400; line-height: 1.4; }
main { box-sizing: border-box; max-width: 72rem; margin: 0 auto; padding: var(--ui-space-6); }
h1 { margin: 0 0 var(--ui-space-5); font-size: var(--ui-text-lg); font-weight: 600; line-height: 1.2; }
.label { margin: 0 0 var(--ui-space-2); color: var(--ui-muted); font-size: var(--ui-text-xs); font-weight: 600; letter-spacing: .06em; text-transform: uppercase; overflow-wrap: anywhere; }
.lanes { display: grid; gap: var(--ui-space-2); grid-template-columns: repeat(auto-fill, minmax(min(100%%, calc(72rem / 4 - var(--ui-space-6))), 1fr)); align-items: start; margin: 0 0 var(--ui-space-5); }
.lane, .card { box-sizing: border-box; min-width: 0; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: var(--ui-radius); padding: var(--ui-space-3) var(--ui-space-4); }
.lane.needs { border-left: var(--ui-space-1) solid var(--ui-attention); }
.lane-head { display: flex; align-items: baseline; gap: var(--ui-space-2); margin: 0; }
.key { flex: none; font-weight: 600; white-space: nowrap; }
.count { flex: 1 1 auto; min-width: 0; text-align: right; color: var(--ui-muted); font-size: var(--ui-text-xs); }
.proof { flex: none; color: var(--ui-link); font-size: var(--ui-text-xs); font-weight: 600; text-decoration: none; }
.proof:hover, .proof:focus-visible { text-decoration: underline; }
.lane .label, .card ul + .label { margin: var(--ui-space-3) 0 var(--ui-space-1); }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: var(--ui-space-2) 0; border-top: 1px solid var(--ui-border); }
.row { display: flex; align-items: center; gap: var(--ui-space-3); }
li a { flex: 1 1 auto; min-width: 0; color: var(--ui-link); font-weight: 600; text-decoration: none; overflow-wrap: anywhere; }
li a:hover, li a:focus-visible { text-decoration: underline; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--ui-focus); outline-offset: 2px; border-radius: var(--ui-radius-sm); }
.pills { flex: 0 1 auto; display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--ui-space-1); margin-left: auto; }
.pill { flex: none; padding: 0 var(--ui-space-2); line-height: 1.6; border-radius: var(--ui-radius-pill); background: var(--ui-attention-soft); color: var(--ui-attention); font-size: var(--ui-text-xs); font-weight: 600; }
.pill.muted { background: var(--ui-surface); color: var(--ui-muted); border: 1px solid var(--ui-border); }
li .stale-path { flex: 1 1 auto; min-width: 0; color: var(--ui-muted); overflow-wrap: anywhere; }
.tallies { display: flex; flex-wrap: wrap; gap: var(--ui-space-1); margin-top: var(--ui-space-1); }
.tally { display: inline-flex; gap: var(--ui-space-1); padding: 0 var(--ui-space-2); line-height: 1.6; border-radius: var(--ui-radius-pill); font-size: var(--ui-text-xs); font-weight: 400; }
.tally b { font-weight: 600; }
.tally.open { background: var(--ui-draft-soft); color: var(--ui-draft); }
.tally.resolved { background: var(--ui-resolved-soft); color: var(--ui-resolved); }
.settled summary { cursor: pointer; margin: 0 0 var(--ui-space-2); font-size: var(--ui-text-md); font-weight: 600; }
.empty { color: var(--ui-muted); }
@media (max-width: 640px) { main { padding: var(--ui-space-4); } }
</style></head><body><main><h1>Review index</h1>%s</main><script>%s
listenHost();
</script></body></html>''' % (listing, _host_bridge())).encode("utf-8")
        self._send_body(body, "text/html; charset=utf-8")

    def _resolve_path(self, path, *, spec_only=False):
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
            target_root = mount["root"] if mount["slug"] else mount["narrow_root"]
            target = os.path.realpath(os.path.join(target_root, *relative.split("/")))
            if not _inside(target, mount["narrow_root"]) or not os.path.isfile(target):
                continue
            if any(part.endswith(".review") or part == ".git" for part in Path(target).parts):
                continue
            if spec_only:
                if not relative.endswith(".spec.html"):
                    continue
                if mount.get("spec") and relative != mount["spec"]:
                    continue
            elif mount.get("spec") and relative.endswith(".spec.html") and relative != mount["spec"]:
                continue
            return mount, target, relative
        return None, None, None

    def _route_review(self, query):
        raw = query.get("dir", [""])[0]
        decoded = _decoded_path(raw)
        if decoded is None:
            return None, None
        for mount in self.mounts:
            if mount.get("stale"):
                continue
            prefix = _mount_prefix(mount)
            relative = decoded.lstrip("/")
            if prefix and not relative.startswith(prefix):
                continue
            if prefix:
                relative = relative[len(prefix):]
            # A registry row reviews its spec; a single-root collection reviews any HTML page it serves.
            if not relative.endswith(".spec.html.review" if mount.get("spec") else ".html.review"):
                continue
            spec = relative[:-len(".review")]
            if not mount.get("spec") and (not _safe_relative(spec) or any(
                    part.startswith(".") or part.endswith(".review") for part in spec.split("/"))):
                continue
            if mount.get("spec") and spec != mount["spec"]:
                continue
            target_root = mount["root"] if mount["slug"] else mount["narrow_root"]
            target = os.path.realpath(os.path.join(target_root, *spec.split("/")))
            review = target + ".review"
            if os.path.isfile(target) and _inside(review, mount["narrow_root"], strict=True):
                return mount, review
        return None, None

    def _baseline(self, query):
        mount, target, _ = self._resolve_path(query.get("path", [""])[0], spec_only=True)
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
        return self._json({"events": result, "stores": named})

    def _events(self, query):
        if "scope" in query:
            return self._scope_events(query)
        mount, review = self._route_review(query)
        if not mount:
            return self._json({"error": "bad dir"}, 400)
        events = _read_spool_events(review, mount["narrow_root"])
        if events is None:
            return self._json({"error": "unsafe spool path"}, 400)
        events.sort(key=lambda event: event["name"])
        resolve_events(review[:-len(".review")], events)
        wake = self.server.wake_controller.status(mount.get("id"))
        headers = {"X-Spec-Chat-Wake": wake} if wake else None
        return self._json(events, headers=headers)

    def _jev(self, query):
        mount, target, relative = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        if mount.get("git") is False:
            return self._json({"error": "jev unavailable", "reason": NOT_GIT_REASON}, 409)
        base = query.get("base", [mount.get("base", "")])[0]
        if not base or base.startswith("-"):
            return self._json({"error": "invalid base"}, 400)
        try:
            base_commit = subprocess.check_output(
                ("git", "-C", mount["root"], "rev-parse", "--verify", base + "^{commit}"),
                stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return self._json({"error": "invalid base"}, 400)
        review = target + ".review"
        view = query.get("view", [""])[0]
        events = _read_spool_events(review, mount["narrow_root"])
        if events is None:
            return self._json({"error": "unsafe spool path"}, 400)
        try:
            return self._json(self.server.jev.response(mount, target, relative, base, events, view, self.mounts,
                                                       base_commit=base_commit))
        except (OSError, RuntimeError, ValueError):
            return self._json({"error": "jev unavailable"}, 503)

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
        """GET /api/status (review-service #steady-status): answered at once from the process itself, no store read."""
        waiting, running = self.server.jev.queue_counts()
        return self._json({"rss_bytes": _rss_bytes(), "uptime_seconds": int(time.monotonic() - self.server.started),
                           "connections": self.server.connections,
                           "jev": {"waiting": waiting, "running": running}})

    def _jev_board(self):
        """Worklane board facts from this host's own registry rows; takes no parameter."""
        try:
            return self._json(self.server.jev.board(self.mounts))
        except (OSError, RuntimeError, ValueError):
            return self._json({"error": "jev unavailable"}, 503)

    def _post_offer(self, query, body):
        """The one Jev record route: a rule card's Not for this spec or a candidate card's Not a project rule
        (project-rules #dismiss), a candidate catch-up passed over (repair-specs #catch-up-table), or an owner's resolution of an Important mark from the agent read
        (jev-suggestions #record-resolution)."""
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
            try:
                ok = self.server.jev.resolve(mount, target, data.get("record"), data.get("resolve"), data.get("reason"))
            except OSError:
                return self._json({"error": "jev unavailable"}, 503)
            if not ok:
                return self._json({"ok": False, "error": "refused: resolution is one of " + ", ".join(OWNER_RESOLUTIONS)
                                   + " (dismissed with a reason), on a mark shown here not yet resolved"}, 400)
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
            mount, review = self._route_review(query)
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
        # The page names the event (review-state #live-save); unnamed posts take the service clock.
        name = query.get("name", ["%d-%s-%s.json" % (time.time_ns(), event_name, event_id)])[0]
        stamp, _, rest = name.partition("-")
        if not stamp.isdigit() or not stamp.isascii() or rest != "%s-%s.json" % (event_name, event_id):
            return self._json({"error": "bad event file name"}, 400)
        try:
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
        try:
            body = Path(target).read_bytes()
        except OSError:
            self.send_error(404)
            return
        if relative.endswith(".spec.html"):
            body = _share_meta(body, target)
            try:
                _store_version(target + ".review", mount["narrow_root"], body)
            except OSError as exc:
                print("review-serve: version not stored for %s: %s" % (relative, exc), file=sys.stderr, flush=True)
        self._send_body(
            body, mimetypes.guess_type(target)[0] or "application/octet-stream", immutable=vendor,
            headers={"Last-Modified": self.date_time_string(int(os.stat(target).st_mtime))},
        )

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            return self._index()
        if parsed.path == "/api/events":
            return self._events(query)
        if parsed.path == "/api/baseline":
            return self._baseline(query)
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
        return self._send_file(parsed.path)

    def _comment_layer(self):
        """The comment layer a host page loads (comment-layer #load): the runtime, served while its file is present."""
        try:
            body = COMMENT_LAYER.read_bytes()
        except OSError:
            return self.send_error(404)
        return self._send_body(body, "text/javascript; charset=utf-8")

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
        if parsed.path == "/api/jev/offer":
            return self._post_offer(parse_qs(parsed.query), body)
        if parsed.path != "/api/events":
            return self._json({"error": "not found"}, 404)
        return self._post_event(parse_qs(parsed.query), body)


class ReviewThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

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
        state = MountState(records, args.registry, _public_base(document))
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
    server.state_dir = providers_state(os.path.dirname(os.path.abspath(args.registry))) if args.registry else None
    server.jev = JevService(api_key=lambda: jev_provider(server.state_dir),
                            llm_model=lambda: jev_llm_model(server.state_dir))
    server.wake_controller = WakeController(server)
    wake_thread = threading.Thread(target=server.wake_controller.run, name="spec-chat-wake", daemon=True)
    wake_thread.start()
    print("spec-chat review-serve on http://%s:%d" % (advertised_host(args), server.server_port), flush=True)
    print("review URL is public and is not a secret in any security sense or an authentication boundary; stop this process when review ends", flush=True)
    server.jev.start()
    try:
        server.serve_forever()
    finally:
        server.wake_controller.stop_event.set()
        wake_thread.join(timeout=1)
        server.server_close()
        server.jev.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
