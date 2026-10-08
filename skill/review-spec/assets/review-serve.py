#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root non-git-root
"""Serve one or more narrow Spec Chat review mounts."""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import html
import json
import mimetypes
import os
import pwd
import re
import signal
import socket
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
from urllib.error import URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import urlopen

try:
    from jev import MARK_LEVELS, JevService, enumerate_served_specs, extract_anchors, mount_prefix as _mount_prefix
except ModuleNotFoundError:
    import importlib.util
    _jev_spec = importlib.util.spec_from_file_location("review_serve_jev", os.path.join(os.path.dirname(__file__), "jev.py"))
    _jev_module = importlib.util.module_from_spec(_jev_spec)
    _jev_spec.loader.exec_module(_jev_module)
    JevService = _jev_module.JevService
    MARK_LEVELS = _jev_module.MARK_LEVELS
    enumerate_served_specs = _jev_module.enumerate_served_specs
    extract_anchors = _jev_module.extract_anchors
    _mount_prefix = _jev_module.mount_prefix
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
WAKE_CHECK_TIMEOUT_SECONDS = 5
WAKE_SEND_TIMEOUT_SECONDS = 10
WAKE_PLACEHOLDER_RE = re.compile(r"\{(owner|artifact|message)\}")
EVIDENCE_TIMEOUT_SECONDS = 30
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
        required = (
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
        root = os.path.realpath(raw["root"])
        narrow = os.path.realpath(raw["narrow_root"])
        if not os.path.isabs(raw["root"]):
            raise ValueError("resource root must be absolute: " + rid)
        if not trust:
            if not os.path.isdir(root) or not os.path.isdir(narrow) or not _inside(narrow, root, strict=True):
                raise ValueError("resource roots are invalid: " + rid)
            try:
                top = os.path.realpath(_repo_root(root))
            except (OSError, RuntimeError) as exc:
                raise ValueError("resource root is not a Git worktree: " + rid) from exc
            if root != top:
                raise ValueError("resource root must be the Git worktree toplevel: " + rid)
        spec = _normalise_spec(raw["spec"])
        spec_file = os.path.realpath(os.path.join(root, *spec.split("/")))
        if not trust and (not _inside(spec_file, narrow, strict=True) or not os.path.isfile(spec_file)):
            raise ValueError("resource spec is missing or outside its collection: " + rid)
        # The host records each row's served path; rows without one serve at <slug>/<spec>.
        key = raw.get("path") or slug + "/" + spec
        if key in stable:
            raise ValueError("duplicate stable resource path: " + key)
        if not SAFE_CURSOR_RE.fullmatch(raw["cursor_name"]):
            raise ValueError("invalid cursor name: " + raw["cursor_name"])
        if check_refs:
            checked = subprocess.run(
                ("git", "-C", root, "rev-parse", "--verify", raw["base"] + "^{commit}"),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if checked.returncode:
                raise ValueError("resource base is unresolved: " + raw["base"])
        resource = dict(raw)
        resource.update({
            "root": root,
            "narrow_root": narrow,
            "spec": spec,
            "spec_file": spec_file,
            "path": key,
        })
        ids.add(rid)
        stable.add(key)
        result.append(resource)
    return result


def _read_registry(path, *, check_refs=False, trust=False):
    try:
        with open(path, "rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("cannot read registry: %s" % exc) from exc
    if not isinstance(document, dict):
        raise ValueError("registry must be a TOML table")
    return _resource_records(document, check_refs=check_refs, trust=trust)


class MountState:
    """The one mount list used by both command-line modes."""

    def __init__(self, records, path=None):
        self.path = os.path.realpath(path) if path else None
        self.records = tuple(records)
        self.signature = self._signature() if self.path else None
        self.on_change = None  # called with the new rows after each registry reload

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
            records = _read_registry(self.path, trust=True)
        except (OSError, ValueError):
            return self.records
        self.records = tuple(records)
        self.signature = signature
        self.changed()
        return self.records

    def changed(self):
        """Run on_change with the current rows; a failed warm-up start never fails a request or server start."""
        if self.on_change:
            try:
                self.on_change(self.records)
            except Exception as exc:
                print("review-serve: registry change hook failed: %r" % exc, file=sys.stderr, flush=True)


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


def _page_title(path):
    class TitleParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.in_title = False
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag.lower() == "title":
                self.in_title = True

        def handle_endtag(self, tag):
            if tag.lower() == "title":
                self.in_title = False

        def handle_data(self, data):
            if self.in_title:
                self.parts.append(data)

    parser = TitleParser()
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            parser.feed(stream.read())
    except OSError:
        pass
    title = " ".join("".join(parser.parts).split())
    return title or os.path.splitext(os.path.basename(path))[0]


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


def read_evidence(base, project, spec, commit, served, committed):
    """One evidence read for one spec at one commit; None when unavailable."""
    query = urlencode({"spec": "project/%s::%s" % (project, spec), "commit": commit})
    try:
        with urlopen(base.rstrip("/") + "/criteria?" + query, timeout=EVIDENCE_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read())
    except (OSError, URLError, ValueError):
        return None
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
    """(status, title) for one served spec, recomputed only when its file identity or row base changes."""
    try:
        info = os.stat(path)
    except OSError:
        return (_review_status(mount) if row else None), _page_title(path)
    base = mount.get("base") if row else None
    key = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, base)
    cached = _ROW_CACHE.get(path)
    if cached and cached[0] == key:
        return cached[1]
    value = (_review_status(mount) if row else None), _page_title(path)
    if base is None or _COMMIT_ID.fullmatch(base):
        _ROW_CACHE[path] = (key, value)
    return value


_THREAD_CACHE = {}


def _thread_counts(mount, path):
    """(open, resolved) thread counts of one served spec's spool (lane-hosting #index-threads), refolded only
    when its human/ or agent/ directory modification time changes (#index-entry-cost); None when unreadable."""
    review = os.path.realpath(path) + ".review"
    key = []
    for actor in ("human", "agent"):
        try:
            key.append(os.lstat(os.path.join(review, actor)).st_mtime_ns)
        except OSError:
            key.append(None)
    key = tuple(key)
    cached = _THREAD_CACHE.get(review)
    if cached and cached[0] == key:
        return cached[1]
    events = _read_spool_events(review, mount["narrow_root"]) if key != (None, None) else []
    if events is None:
        counts = None
    else:
        statuses = [thread["status"] for thread in spool.fold_threads(events).values()]
        resolved = statuses.count("resolved")
        counts = len(statuses) - resolved, resolved
    _THREAD_CACHE[review] = (key, counts)
    return counts


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


class UnsafeSpool(OSError):
    """A spool path that escapes its collection."""


# A symlink or non-directory where the spool expects its own directory or file: refused, not retried.
UNSAFE_ERRNOS = (errno.ELOOP, errno.ENOTDIR)


@contextlib.contextmanager
def _actor_directory(review, root, actor, create=False):
    relative = os.path.relpath(review, root)
    parts = relative.split(os.sep) + [actor]
    if any(part in ("", ".", "..") for part in parts) or not _inside(review, root, strict=True):
        raise UnsafeSpool("review path escapes collection")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
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


def _read_spool_events(review, root):
    events = []
    for actor in ("human", "agent"):
        try:
            with _actor_directory(review, root, actor) as directory:
                for name in os.listdir(directory):
                    if not name.endswith(".json"):
                        continue
                    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
                    descriptor = os.open(name, flags, dir_fd=directory)
                    with os.fdopen(descriptor, encoding="utf-8") as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            return None
                        try:
                            body = json.load(stream)
                        except ValueError:
                            continue
                    events.append({"actor": actor, "name": name, "body": body})
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return events


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
    with _actor_directory(review, root, actor, create=True) as directory:
        return _write_once(directory, name, json.dumps(event).encode("utf-8"))


def _store_version(review, root, body):
    """Keep `versions/<sha256>.html`, the spec text a page is shown (review-state #model-versions)."""
    name = hashlib.sha256(body).hexdigest() + ".html"
    with _actor_directory(review, root, "versions", create=True) as directory:
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            _write_once(directory, name, body)


def _wake_batch(resource):
    """Return the zero-wait scan batch (spool.handoff_batch): each pending hand-off and the events it lists."""
    review = resource["spec_file"] + ".review"
    try:
        return spool.handoff_batch(review, spool.read_cursor(review, resource["cursor_name"]))
    except (OSError, ValueError):
        return ()


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


class WakeTimeout(Exception):
    """The wake command outlived its timeout and its process group was killed."""


def run_wake(argv, values, *, timeout):
    """Run one wake provider command with its placeholders filled: exit code, None when it cannot start."""
    argv = [WAKE_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], arg) for arg in argv]
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return None
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()
        raise WakeTimeout(argv[0]) from None


class WakeController:
    """Wake each registry row's owner pane once per unchanged hand-off batch."""

    def __init__(self, server):
        self.server = server
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.states = {}
        self.delivered = {}

    def status(self, resource_id):
        with self.lock:
            return self.states.get(resource_id)

    def _deliver(self, resource, batch):
        commands = wake_provider(self.server.state_dir)
        if commands is None:
            return "unavailable"
        check, send = commands
        message = (
            f"Spec Chat human spec review hand-off ready: spec {resource['spec_file']}, "
            f"collection {resource['narrow_root']}, cursor {resource['cursor_name']}, {len(batch)} events. "
            "Run the zero-wait scan, process the batch, then park."
        )
        values = {"owner": resource["owner"], "artifact": resource["spec_file"], "message": message}
        try:
            if run_wake(check, values, timeout=WAKE_CHECK_TIMEOUT_SECONDS) != 0:
                return "failed"
        except WakeTimeout:
            return "failed"
        try:
            code = run_wake(send, values, timeout=WAKE_SEND_TIMEOUT_SECONDS)
        except WakeTimeout:
            return "sent"  # sending, delivery unconfirmed: never resend the same batch
        return {0: "sent", 75: "deferred"}.get(code, "failed")

    def _poll_row(self, resource):
        resource_id = resource["id"]
        if not resource.get("owner") or not resource.get("cursor_name"):
            return None
        batch = _wake_batch(resource)
        if not batch:
            return None
        identity = (resource["owner"], batch)
        if self.delivered.get(resource_id) == identity:
            return "sent"
        state = self._deliver(resource, batch)
        if state == "sent":
            self.delivered[resource_id] = identity
        return state

    def poll(self):
        states = {}
        kept = set()
        with self.lock:
            previous = dict(self.states)
        for resource in self.server.mount_state.snapshot():
            resource_id = resource.get("id") if isinstance(resource, dict) else None
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
        for resource_id in set(self.delivered) - set(states) - kept:
            del self.delivered[resource_id]

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:  # keep waking other rows after an unexpected error
                print("review-serve: wake poll failed: %s" % exc, file=sys.stderr, flush=True)
            self.stop_event.wait(WAKE_POLL_SECONDS)


class MountHandler(SimpleHTTPRequestHandler):
    # Keep-alive: every response carries Content-Length, is a bodiless 304, or
    # closes (send_error); do_POST drains its body before any reply.
    protocol_version = "HTTP/1.1"
    server_version = "SpecChat/1"
    timeout = 2.0

    @property
    def mounts(self):
        return self.server.mount_state.snapshot()

    def setup(self):
        super().setup()
        self.connection.settimeout(self.timeout)

    def end_headers(self, cache_control="no-cache"):
        self.send_header("Cache-Control", cache_control)
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
        lanes = {}
        seen = set()
        query = urlparse(self.path).query
        for mount in self.mounts:
            specs = enumerate_served_specs(mount)
            row = bool(mount.get("slug") and mount.get("spec"))
            # Only a lane-key slug (ann230) is an open lane; any other slug settles.
            lane = mount["slug"] if row and _lane_label(mount["slug"])[1][0] == 0 else None
            project = mount.get("project") or os.path.basename(mount["root"])
            for spec, path in specs:
                stable = _mount_prefix(mount) + spec
                if stable in seen:
                    continue
                seen.add(stable)
                href = ("/" if mount["slug"] else "") + quote(stable, safe="/") + (("?" + query) if query else "")
                status, title = _index_row(mount, path, row)
                lanes.setdefault(lane, {}).setdefault(project, []).append(
                    (status, title, href, _thread_counts(mount, path)))

        settled = lanes.pop(None, {})

        def lane_order(item):
            lane, projects = item
            changed = any(spec[0] for specs in projects.values() for spec in specs)
            return (0 if changed else 1, _lane_label(lane)[1])

        def project_rows(projects):
            parts = []
            for project in sorted(projects):
                parts.append('<p class="label">%s</p><ul>' % html.escape(project))
                for status, title, href, counts in sorted(projects[project], key=lambda spec: (not spec[0], spec[1].casefold(), spec[2])):
                    # The lane count says up to date; a row's only status word is its Changed pill.
                    pill = '<span class="pill">Changed</span>' if status else ""
                    # Count pills, Open then Resolved; a status with no threads has no pill (#index-threads).
                    tallies = "".join(
                        '<span class="tally %s"><b>%d</b> %s</span>' % (word.lower(), count, word)
                        for word, count in zip(("Open", "Resolved"), counts or (0, 0)) if count)
                    parts.append('<li><div class="row"><a href="%s">%s</a>%s</div>%s</li>' % (
                        html.escape(href, quote=True), html.escape(title), pill,
                        '<div class="tallies">%s</div>' % tallies if tallies else ""))
                parts.append('</ul>')
            return "".join(parts)

        cards = []
        for lane, projects in sorted(lanes.items(), key=lane_order):
            statuses = [spec[0] for specs in projects.values() for spec in specs if spec[0] is not None]
            changed = sum(1 for status in statuses if status)
            count = (
                "%d of %d changed" % (changed, len(statuses)) if changed
                else "Up to date" if statuses else "No status"
            )
            key = html.escape(_lane_label(lane)[0])
            cards.append('<section class="lane%s" aria-label="%s"><p class="lane-head"><span class="key">%s</span><span class="count">%s</span></p>%s</section>' % (
                " changed" if changed else "", key, key, count, project_rows(projects)))
        listing = ""
        if cards:
            listing += '<p class="label">In progress</p><nav class="lanes" aria-label="Spec Chat detail pages">%s</nav>' % "\n".join(cards)
        if settled:
            listing += '<details class="settled"><summary>Settled (%d)</summary><div class="card">%s</div></details>' % (
                sum(len(specs) for specs in settled.values()), project_rows(settled))
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
.lane.changed { border-left: var(--ui-space-1) solid var(--ui-attention); }
.lane-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--ui-space-2); margin: 0; }
.key { font-weight: 600; }
.count { flex: none; color: var(--ui-muted); font-size: var(--ui-text-xs); }
.lane .label, .card ul + .label { margin: var(--ui-space-3) 0 var(--ui-space-1); }
ul { list-style: none; margin: 0; padding: 0; }
li { padding: var(--ui-space-2) 0; border-top: 1px solid var(--ui-border); }
.row { display: flex; align-items: center; gap: var(--ui-space-3); }
li a { flex: 1 1 auto; min-width: 0; color: var(--ui-link); font-weight: 600; text-decoration: none; overflow-wrap: anywhere; }
li a:hover, li a:focus-visible { text-decoration: underline; }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--ui-focus); outline-offset: 2px; border-radius: var(--ui-radius-sm); }
.pill { flex: none; margin-left: auto; padding: 0 var(--ui-space-2); line-height: 1.6; border-radius: var(--ui-radius-pill); background: var(--ui-attention-soft); color: var(--ui-attention); font-size: var(--ui-text-xs); font-weight: 600; }
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

    def _events(self, query):
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

    def _jev_board(self):
        """Worklane board facts from this host's own registry rows; takes no parameter."""
        try:
            return self._json(self.server.jev.board(self.mounts))
        except (OSError, RuntimeError, ValueError):
            return self._json({"error": "jev unavailable"}, 503)

    def _post_offer(self, query, body):
        """The one Jev record route: either one-time offer, reconcile or candidates (body kind), sent or dismissed
        (project-rules #bootstrap-offer, #bootstrap-candidates), a rule card's Not for this spec or Not a project
        rule (project-rules #dismiss-store), or a candidate card's Confirm rule with an optional corrected name
        (project-rules #approval)."""
        mount, target, _ = self._resolve_path(query.get("path", [""])[0], spec_only=True)
        if not mount:
            return self._json({"error": "bad path"}, 400)
        try:
            data = json.loads(body)
            action = data.get("offer")
        except (ValueError, AttributeError):
            return self._json({"error": "bad json"}, 400)
        if "dismiss" in data:
            # jev.dismiss is the one check of a dismissal; a refused one is a 400, and the page's re-read brings its note back.
            try:
                ok = self.server.jev.dismiss(mount, target, data.get("dismiss"), data.get("rule"), data.get("record"),
                                             data.get("spec"))
            except OSError:
                return self._json({"error": "jev unavailable"}, 503)
            return self._json({"ok": ok}, 200 if ok else 400)
        if "confirm" in data:
            try:
                ok = self.server.jev.confirm(mount, data.get("rule"), data.get("name"))
            except OSError:
                return self._json({"error": "jev unavailable"}, 503)
            return self._json({"ok": ok}, 200 if ok else 400)
        if action not in ("sent", "dismissed") or data.get("kind", "reconcile") not in ("reconcile", "candidates"):
            return self._json({"error": "offer must be sent or dismissed"}, 400)
        try:
            return self._json({"ok": self.server.jev.record_offer(mount.get("project"), action, data.get("kind", "reconcile"))})
        except OSError:
            return self._json({"error": "jev unavailable"}, 503)

    def _post_event(self, query, body):
        mount, review = self._route_review(query)
        actor = query.get("actor", ["human"])[0]
        if not mount or actor not in ("human", "agent"):
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
        # The page names the event (review-state #live-save); unnamed posts take the service clock.
        name = query.get("name", ["%d-%s-%s.json" % (time.time_ns(), event_name, event_id)])[0]
        stamp, _, rest = name.partition("-")
        if not stamp.isdigit() or not stamp.isascii() or rest != "%s-%s.json" % (event_name, event_id):
            return self._json({"error": "bad event file name"}, 400)
        try:
            stored = _write_event(review, mount["narrow_root"], actor, name, event)
        except OSError as exc:
            if isinstance(exc, UnsafeSpool) or exc.errno in UNSAFE_ERRNOS:
                return self._json({"error": "unsafe spool path"}, 400)
            # an I/O failure is not a refusal: the page keeps the event and sends it again
            return self._json({"error": "event not stored"}, 503)
        if not stored:
            return self._json({"error": "event file exists with different bytes"}, 409)
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
        return self._send_file(parsed.path)

    def do_HEAD(self):
        return self.do_GET()

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
            records = _read_registry(args.registry, check_refs=True)
        except (OSError, ValueError) as exc:
            print("review-serve: %s" % exc, file=sys.stderr)
            return 2
        state = MountState(records, args.registry)
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
    server.state_dir = os.path.dirname(os.path.abspath(args.registry)) if args.registry else None
    server.jev = JevService(api_key=lambda: jev_provider(server.state_dir),
                            llm_model=lambda: jev_llm_model(server.state_dir))
    # Bootstrap (project-rules #bootstrap-home): a project's first registration starts its warm-up here.
    state.on_change = server.jev.warm
    state.changed()
    server.wake_controller = WakeController(server)
    wake_thread = threading.Thread(target=server.wake_controller.run, name="spec-chat-wake", daemon=True)
    wake_thread.start()
    print("spec-chat review-serve on http://%s:%d" % (advertised_host(args), server.server_port), flush=True)
    print("review URL is public and is not a secret in any security sense or an authentication boundary; stop this process when review ends", flush=True)
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
