#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root
"""Serve one or more narrow Spec Chat review mounts."""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import mimetypes
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import tomllib
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
SAFE_CURSOR_RE = re.compile(r"[^/\\]+\Z")
EVENT_RE = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
WAKE_POLL_SECONDS = 3


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
    result = subprocess.run(
        ("git", "-C", str(root), *args),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
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
    slug_roots = {}
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
        key = slug + "/" + spec
        if key in stable:
            raise ValueError("duplicate stable resource path: " + key)
        if slug in slug_roots and slug_roots[slug] != root:
            raise ValueError("ambiguous resource slug: " + slug)
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
        })
        ids.add(rid)
        stable.add(key)
        slug_roots[slug] = root
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

    def _signature(self):
        try:
            info = os.stat(self.path)
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size

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
        return self.records


def _single_mount(root):
    root = Path(root).expanduser().resolve()
    try:
        repo = _repo_root(root)
    except RuntimeError as exc:
        raise SystemExit("refusing broad root; serve a narrow review collection strictly inside its Git repository") from exc
    if root == repo:
        raise SystemExit("refusing broad root; serve a narrow review collection strictly inside its Git repository")
    if not root.is_dir() or not _inside(root, repo, strict=True):
        raise SystemExit("review root must be a directory strictly inside its Git repository")
    return {
        "id": "single-root",
        "slug": "",
        "root": str(repo),
        "narrow_root": str(root),
        "spec": None,
        "base": "",
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


def _mount_prefix(mount):
    return (mount["slug"] + "/") if mount["slug"] else ""


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


@contextlib.contextmanager
def _actor_directory(review, root, actor, create=False):
    relative = os.path.relpath(review, root)
    parts = relative.split(os.sep) + [actor]
    if any(part in ("", ".", "..") for part in parts) or not _inside(review, root, strict=True):
        raise OSError("review path escapes collection")
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


def _write_event(review, root, actor, name, event):
    with _actor_directory(review, root, actor, create=True) as directory:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(event, stream)


def _wake_batch(resource):
    """Return the zero-wait scan batch: pending human names through the newest hand-off."""
    review = resource["spec_file"] + ".review"
    try:
        names = sorted(name for name in os.listdir(os.path.join(review, "human")) if not name.startswith("."))
    except OSError:
        return ()
    try:
        with open(os.path.join(review, resource["cursor_name"]), encoding="utf-8") as stream:
            consumed = set(stream.read().splitlines())
    except FileNotFoundError:
        consumed = set()
    except OSError:
        return ()
    pending = [name for name in names if name not in consumed]
    last = max((index for index, name in enumerate(pending) if "-handoff-" in name), default=-1)
    return tuple(pending[:last + 1])


def _herdr(*argv, timeout):
    try:
        return subprocess.run(
            argv, text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def herdr_owner_status(owner):
    """Resolve an owner pane through Herdr: its agent_status, or None when unresolved."""
    result = _herdr("herdr", "agent", "get", owner, timeout=5)
    if result is None or result.returncode:
        return None
    try:
        agent = json.loads(result.stdout)["result"]["agent"]
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(agent, dict) or agent.get("pane_id") != owner:
        return None
    return str(agent.get("agent_status") or "unknown")


def herdr_installed():
    return bool(shutil.which("herdr") and shutil.which("herdr-say"))


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
        if not herdr_installed():
            return "unavailable"
        owner = resource["owner"]
        agent_status = herdr_owner_status(owner)
        if agent_status is None:
            return "failed"
        if agent_status == "working":
            return "deferred"
        message = (
            f"Spec Chat human spec review hand-off ready: spec {resource['spec_file']}, "
            f"collection {resource['narrow_root']}, cursor {resource['cursor_name']}, {len(batch)} events. "
            "Run the zero-wait scan, process the batch, then park."
        )
        result = _herdr("herdr-say", "--kind", "command", "--artifact", resource["spec_file"], owner, message, timeout=10)
        if result is None:
            return "failed"
        return {0: "sent", 75: "deferred"}.get(result.returncode, "failed")

    def poll(self):
        states = {}
        for resource in self.server.mount_state.snapshot():
            resource_id = resource["id"]
            if not resource.get("owner") or not resource.get("cursor_name"):
                continue
            batch = _wake_batch(resource)
            if not batch:
                continue
            identity = (resource["owner"], batch)
            if self.delivered.get(resource_id) == identity:
                states[resource_id] = "sent"
                continue
            state = self._deliver(resource, batch)
            if state == "sent":
                self.delivered[resource_id] = identity
            states[resource_id] = state
        with self.lock:
            self.states = states
        for resource_id in set(self.delivered) - set(states):
            del self.delivered[resource_id]

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.poll()
            except Exception as exc:  # keep waking other rows after an unexpected error
                print("review-serve: wake poll failed: %s" % exc, file=sys.stderr, flush=True)
            self.stop_event.wait(WAKE_POLL_SECONDS)


class MountHandler(SimpleHTTPRequestHandler):
    server_version = "SpecChat/1"
    timeout = 2.0

    @property
    def mounts(self):
        return self.server.mount_state.snapshot()

    def setup(self):
        super().setup()
        self.connection.settimeout(self.timeout)

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

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
        entries = []
        seen = set()
        query = urlparse(self.path).query
        for mount in self.mounts:
            specs = []
            if mount.get("spec"):
                specs = [(mount["spec"], mount["spec_file"])]
            else:
                for directory, directories, names in os.walk(mount["narrow_root"], followlinks=False):
                    directories[:] = sorted(
                        name for name in directories
                        if not name.startswith(".") and not name.endswith(".review")
                        and name.lower() not in {"evidence", "evidence-bundle", "evidence-bundles", "fixture", "fixtures", "support", "supports"}
                    )
                    for name in sorted(names):
                        if name.startswith(".") or not name.endswith(".spec.html"):
                            continue
                        path = os.path.join(directory, name)
                        if not _inside(path, mount["narrow_root"]) or not os.path.isfile(path):
                            continue
                        relative = os.path.relpath(path, mount["narrow_root"]).replace(os.sep, "/")
                        specs.append((relative, path))
            for spec, path in specs:
                stable = _mount_prefix(mount) + spec
                if stable in seen:
                    continue
                seen.add(stable)
                entries.append(
                    '<li><a href="%s">%s</a><span>%s</span></li>' % (
                        html.escape(("/" if mount["slug"] else "") + quote(stable, safe="/") + (("?" + query) if query else ""), quote=True),
                        html.escape(_page_title(path)),
                        html.escape(stable),
                    )
                )
        listing = "\n".join(entries) or '<li class="empty">No Spec Chat spec pages are available.</li>'
        body = ('''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec Chat index</title><style>
:root { color-scheme: light; font-family: system-ui, sans-serif; background: #faf9f6; color: #22242a; }
body { margin: 0; } main { box-sizing: border-box; max-width: 60rem; margin: 0 auto; padding: clamp(1.25rem, 4vw, 3rem); }
h1 { font-size: clamp(1.8rem, 5vw, 2.8rem); line-height: 1.1; margin: 0 0 2rem; }
ul { display: grid; gap: .75rem; list-style: none; margin: 0; padding: 0; }
li { min-width: 0; background: #fff; border: 1px solid #e2e0d8; border-radius: .75rem; padding: .25rem 1rem 1rem; }
li a { color: #087f73; display: flex; align-items: center; min-height: 44px; padding: .25rem 0; font-weight: 700; font-size: 1.05rem; line-height: 1.35; overflow-wrap: anywhere; }
li span { color: #595e68; display: block; font-size: .9rem; overflow-wrap: anywhere; }
.empty { color: #595e68; padding: 1rem; }
</style></head><body><main><h1>Review index</h1><nav aria-label="Spec Chat detail pages"><ul>%s</ul></nav></main></body></html>''' % listing).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

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
            if any(part.endswith(".review") for part in Path(target).parts):
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
            if not relative.endswith(".spec.html.review"):
                continue
            spec = relative[:-len(".review")]
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
        base_ref = next(
            (candidate for candidate in candidates if candidate and subprocess.run(
                ("git", "-C", mount["root"], "rev-parse", "--verify", candidate + "^{commit}"),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode == 0),
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
        wake = self.server.wake_controller.status(mount.get("id"))
        headers = {"X-Spec-Chat-Wake": wake} if wake else None
        return self._json(events, headers=headers)

    def _post_event(self, query):
        mount, review = self._route_review(query)
        actor = query.get("actor", ["human"])[0]
        if not mount or actor not in ("human", "agent"):
            return self._json({"error": "bad dir or actor"}, 400)
        if actor == "agent":
            return self._json({"error": "agent spool writes are disk-only"}, 403)
        try:
            event = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except (TypeError, ValueError):
            return self._json({"error": "bad json"}, 400)
        if not isinstance(event, dict):
            return self._json({"error": "bad json"}, 400)
        event_name, event_id = event.get("event"), event.get("id")
        if not isinstance(event_name, str) or not isinstance(event_id, str) or not EVENT_RE.fullmatch(event_name) or not EVENT_RE.fullmatch(event_id):
            return self._json({"error": "bad event name"}, 400)
        name = "%d-%s-%s.json" % (time.time_ns(), event_name, event_id)
        try:
            _write_event(review, mount["narrow_root"], actor, name, event)
        except OSError:
            return self._json({"error": "unsafe spool path"}, 400)
        return self._json({"ok": True, "name": name})

    def _send_file(self, path):
        handled, bundled = _own_viz_asset(path)
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
        else:
            _, target, _ = self._resolve_path(path)
        if not target:
            self.send_error(404)
            return
        try:
            body = Path(target).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/":
            return self._index()
        if parsed.path == "/api/events":
            return self._events(query)
        if parsed.path == "/api/baseline":
            return self._baseline(query)
        return self._send_file(parsed.path)

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/events":
            return self._json({"error": "not found"}, 404)
        return self._post_event(parse_qs(parsed.query))


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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
