#!/usr/bin/env python3
"""Own a multi-resource Spec Chat review host.

The launcher is deliberately stdlib-only.  It owns the registry, receipt, and
server process, while review-serve owns HTTP routing and spool writes.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[3]
SERVER = SCRIPT.parents[1] / "assets" / "review-serve.py"
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
SAFE_CURSOR_RE = re.compile(r"[^/\\]+\Z")


class LauncherError(RuntimeError):
    pass


class ProofError(LauncherError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_git(root: Path, *args: str, check: bool = True) -> str:
    try:
        return subprocess.check_output(
            ("git", "-C", str(root), *args), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        if check:
            raise LauncherError(f"git command failed in {root}: {' '.join(args)}") from exc
        return ""


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        return "{ " + ", ".join(f"{key} = {toml_value(item)}" for key, item in value.items()) + " }"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if value is None:
        return '""'
    raise TypeError(f"unsupported TOML value: {type(value)!r}")


def dump_document(document: Mapping[str, Any], array_key: str = "resource") -> str:
    lines: list[str] = []
    arrays = document.get(array_key, [])
    for key, value in document.items():
        if key == array_key:
            continue
        lines.append(f"{key} = {toml_value(value)}")
    if lines and arrays:
        lines.append("")
    for index, record in enumerate(arrays):
        lines.append(f"[[{array_key}]]")
        for key, value in record.items():
            lines.append(f"{key} = {toml_value(value)}")
        if index != len(arrays) - 1:
            lines.append("")
    return "\n".join(lines) + "\n"


def read_toml(path: Path, *, missing: Any = None) -> Any:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except FileNotFoundError:
        if missing is not None:
            return missing
        raise LauncherError(f"missing state file: {path}")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LauncherError(f"invalid TOML state file {path}: {exc}") from exc


def parse_ports(value: str | Sequence[int]) -> tuple[int, ...]:
    tokens = re.split(r"[\s,;]+", value.strip()) if isinstance(value, str) else list(value)
    ports: list[int] = []
    for token in tokens:
        if token == "":
            continue
        try:
            port = int(token)
        except (TypeError, ValueError) as exc:
            raise LauncherError(f"invalid approved ingress port: {token}") from exc
        if not 1 <= port <= 65535:
            raise LauncherError(f"approved ingress port outside TCP range: {port}")
        if port in ports:
            raise LauncherError(f"approved ingress port duplicated: {port}")
        ports.append(port)
    if not ports:
        raise LauncherError("approved ingress ports are not configured")
    return tuple(ports)


def firewall_ports() -> tuple[int, ...] | None:
    """Read simple UFW status output without changing firewall policy."""
    try:
        result = subprocess.run(
            ("ufw", "status"), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False,
        )
    except OSError:
        return None
    found: list[int] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\/tcp(?:\s|$)", line)
        if match:
            port = int(match.group(1))
            if port not in found:
                found.append(port)
    return tuple(found) or None


def approved_ports(environ: Mapping[str, str] | None = None) -> tuple[int, ...]:
    env = os.environ if environ is None else environ
    for name in ("SPEC_CHAT_APPROVED_INGRESS_PORTS", "REVIEW_APPROVED_INGRESS_PORTS"):
        value = env.get(name)
        if value:
            return parse_ports(value)
    values = firewall_ports()
    if values:
        return parse_ports(values)
    raise LauncherError("approved ingress ports are unavailable; configure SPEC_CHAT_APPROVED_INGRESS_PORTS")


def select_port(ports: Sequence[int], bind: str) -> int:
    family = socket.AF_INET6 if ":" in bind else socket.AF_INET
    for port in ports:
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind, port))
        except OSError:
            continue
        finally:
            sock.close()
        return port
    raise LauncherError("no approved ingress port is free")


def process_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return [part.decode(errors="replace") for part in raw.split(b"\0") if part]


def process_owned(pid: Any, registry: Path) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    command = process_cmdline(pid)
    if not command:
        return False
    joined = "\0".join(command)
    return "review-serve.py" in joined and "--registry" in command and str(registry) in command


def kill_owned(pid: int, registry: Path) -> None:
    if not process_owned(pid, registry):
        raise LauncherError("receipt process is not an owned review-serve process")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if process_cmdline(pid) is None:
            return
        time.sleep(0.05)
    if process_cmdline(pid) is not None:
        os.kill(pid, signal.SIGKILL)


def cursor_snapshot(spec_file: Path, cursor_name: str) -> dict[str, Any]:
    cursor = Path(str(spec_file) + ".review") / cursor_name
    try:
        data = cursor.read_bytes()
    except FileNotFoundError:
        data = b""
    except OSError as exc:
        raise LauncherError(f"cannot read cursor {cursor}: {exc}") from exc
    lines = data.decode("utf-8", errors="replace").splitlines()
    return {"lines": len(lines), "last": lines[-1] if lines else "", "sha256": sha256(data)}


def path_inside(path: Path, parent: Path, *, strict: bool = False) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return not strict or path != parent


def resource_toplevel(root: Path) -> Path:
    root = root.expanduser().resolve()
    top = Path(run_git(root, "rev-parse", "--show-toplevel"))
    return top.resolve()


def nearest_collection(spec_file: Path, top: Path) -> Path:
    for parent in (spec_file.parent, *spec_file.parents):
        if parent == top:
            break
        if parent.name == "docs":
            return parent
    raise LauncherError("spec has no docs collection strictly inside its worktree")


def resolve_collection(value: str | None, top: Path, spec_file: Path) -> Path:
    if value:
        raw = Path(value).expanduser()
        collection = (raw if raw.is_absolute() else top / raw).resolve()
    else:
        collection = nearest_collection(spec_file, top)
    if not collection.is_dir() or not path_inside(collection, top, strict=True):
        raise LauncherError("collection must be a directory strictly inside the worktree")
    if not path_inside(spec_file, collection, strict=True):
        raise LauncherError("collection does not contain the registered spec")
    return collection


def parse_resource_spec(value: str, owner: str, checker: str, cursor_name: str,
                        slug: str | None = None, collection: str | None = None) -> dict[str, Any]:
    if not owner.strip() or not checker.strip() or not cursor_name.strip():
        raise LauncherError("--owner, --checker, and --cursor-name are required")
    if not SAFE_CURSOR_RE.fullmatch(cursor_name) or cursor_name in {".", ".."}:
        raise LauncherError("cursor name must be a single file name")
    try:
        project, rest = value.split("=", 1)
        root_text, base = rest.rsplit("@", 1)
        root_text, spec = root_text.split(":", 1)
    except ValueError as exc:
        raise LauncherError("resource must be PROJECT_ID=ROOT:SPEC_PATH@BASE") from exc
    project = project.strip()
    base = base.strip()
    if not project or not base or not root_text or not spec:
        raise LauncherError("resource must name project, root, spec, and base")
    top = resource_toplevel(Path(root_text))
    spec = spec.replace("\\", "/")
    spec_path = Path(spec)
    if spec_path.is_absolute() or spec_path.suffix != ".html" or not spec.endswith(".spec.html"):
        raise LauncherError("resource spec must be a repository-relative *.spec.html path")
    if any(part in {"", ".", ".."} for part in spec_path.parts):
        raise LauncherError("resource spec path escapes its repository")
    spec_file = (top / spec_path).resolve()
    if not path_inside(spec_file, top, strict=True) or not spec_file.is_file():
        raise LauncherError("resource spec does not exist inside the worktree")
    narrow = resolve_collection(collection, top, spec_file)
    selected_slug = (slug or project).strip()
    if not SLUG_RE.fullmatch(selected_slug) or selected_slug in {"api", "static"}:
        raise LauncherError(f"invalid or reserved resource slug: {selected_slug}")
    resolved_base = run_git(top, "rev-parse", "--verify", base + "^{commit}")
    identity = f"spec:{project}::{spec}"
    return {
        "id": identity,
        "slug": selected_slug,
        "root": str(top),
        "narrow_root": str(narrow),
        "spec": spec,
        "spec_file": str(spec_file),
        "base": base,
        "resolved_base_commit": resolved_base,
        "owner": owner.strip(),
        "checker": checker.strip(),
        "cursor_name": cursor_name,
    }


def registry_record(resource: Mapping[str, Any], *, lifecycle: str = "serving",
                    registered_at: str | None = None) -> dict[str, Any]:
    spec_file = Path(resource["spec_file"])
    stamp = registered_at or now()
    return {
        "id": resource["id"], "slug": resource["slug"], "root": resource["root"],
        "narrow_root": resource["narrow_root"], "spec": resource["spec"],
        "base": resource["base"], "owner": resource["owner"], "checker": resource["checker"],
        "lifecycle": lifecycle, "cursor_name": resource["cursor_name"],
        "cursor_snapshot": cursor_snapshot(spec_file, resource["cursor_name"]),
        "finish_event": "", "registered_at": stamp, "updated_at": stamp,
    }


def stable_path(resource: Mapping[str, Any]) -> str:
    return f"/{resource['slug']}/{resource['spec']}"


def validate_records(records: Sequence[Mapping[str, Any]]) -> None:
    ids: set[str] = set()
    stable: set[str] = set()
    sources: set[str] = set()
    slug_roots: dict[str, str] = {}
    for record in records:
        required = ("id", "slug", "root", "narrow_root", "spec", "base", "owner", "checker", "lifecycle", "cursor_name")
        missing = [key for key in required if not isinstance(record.get(key), str) or not record[key].strip()]
        if missing:
            raise LauncherError("resource missing required field: " + ", ".join(missing))
        rid = record["id"]
        if rid in ids:
            raise LauncherError(f"duplicate resource identity: {rid}")
        slug = record["slug"]
        if not SLUG_RE.fullmatch(slug) or slug in {"api", "static"}:
            raise LauncherError(f"invalid or reserved resource slug: {slug}")
        root = Path(record["root"])
        if not root.is_absolute():
            raise LauncherError(f"resource root must be absolute: {rid}")
        top = resource_toplevel(root)
        if root.resolve() != top:
            raise LauncherError(f"resource root must be worktree toplevel: {rid}")
        narrow = Path(record["narrow_root"]).resolve()
        if not narrow.is_dir() or not path_inside(narrow, top, strict=True):
            raise LauncherError(f"resource collection is invalid: {rid}")
        spec = record["spec"].replace("\\", "/")
        if spec.startswith("/") or not spec.endswith(".spec.html"):
            raise LauncherError(f"resource spec path is invalid: {spec}")
        spec_file = (top / spec).resolve()
        if not path_inside(spec_file, narrow, strict=True) or not spec_file.is_file():
            raise LauncherError(f"resource spec is missing or outside its collection: {rid}")
        key = f"{slug}/{spec}"
        if key in stable:
            raise LauncherError(f"duplicate stable resource path: {key}")
        source = str(spec_file)
        if source in sources:
            raise LauncherError(f"ambiguous resource source: {source}")
        if slug in slug_roots and slug_roots[slug] != str(root.resolve()):
            raise LauncherError(f"ambiguous resource slug: {slug}")
        run_git(root, "rev-parse", "--verify", record["base"] + "^{commit}")
        if record["lifecycle"] not in {"serving", "parked", "finished", "removed"}:
            raise LauncherError(f"invalid resource lifecycle: {record['lifecycle']}")
        if not SAFE_CURSOR_RE.fullmatch(record["cursor_name"]):
            raise LauncherError(f"invalid cursor name: {record['cursor_name']}")
        ids.add(rid); stable.add(key); sources.add(source); slug_roots[slug] = str(root.resolve())


def read_registry(path: Path) -> list[dict[str, Any]]:
    document = read_toml(path)
    records = document.get("resource", [])
    if not isinstance(records, list):
        raise LauncherError("registry resource entries must be an array")
    result = [dict(item) for item in records]
    validate_records(result)
    return result


def write_registry(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    validate_records(records)
    atomic_write(path, dump_document({"resource": [dict(record) for record in records]}))


def read_receipt(path: Path) -> dict[str, Any]:
    document = read_toml(path)
    if not isinstance(document, dict):
        raise LauncherError("receipt must be a TOML table")
    document["resource"] = [dict(item) for item in document.get("resource", [])]
    return document


def write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    atomic_write(path, dump_document(receipt))


def direct_request(url: str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method="GET")
    try:
        with opener.open(request, timeout=3) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (OSError, urllib.error.URLError) as exc:
        raise ProofError("public URL is unreachable") from exc


def prove_resource(public_url: str, resource: Mapping[str, Any]) -> dict[str, Any]:
    spec_url = public_url.rstrip("/") + urllib.parse.quote(stable_path(resource), safe="/")
    status, body = direct_request(spec_url)
    expected = Path(resource["spec_file"]).read_bytes()
    if status != 200 or body != expected:
        raise ProofError(f"served spec bytes differ for {resource['id']}")
    query = urllib.parse.urlencode({"path": stable_path(resource).lstrip("/"), "base": resource["base"]})
    baseline_url = public_url.rstrip("/") + "/api/baseline?" + query
    baseline_status, baseline_body = direct_request(baseline_url)
    if baseline_status != 200:
        raise ProofError(f"baseline route returned HTTP {baseline_status} for {resource['id']}")
    try:
        baseline = json.loads(baseline_body)
    except (TypeError, ValueError) as exc:
        raise ProofError(f"baseline route returned invalid JSON for {resource['id']}") from exc
    expected_commit = resource.get("resolved_base_commit") or run_git(Path(resource["root"]), "rev-parse", "--verify", resource["base"] + "^{commit}")
    baseline_html = subprocess.run(
        ("git", "-C", resource["root"], "show", expected_commit + ":" + resource["spec"]),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ).stdout
    if baseline.get("base") != expected_commit or baseline.get("htmlBase") != expected_commit:
        raise ProofError(f"baseline route returned a different base for {resource['id']}")
    if baseline.get("html") != baseline_html.decode("utf-8"):
        raise ProofError(f"baseline route returned different bytes for {resource['id']}")
    host = urllib.parse.urlsplit(public_url).hostname or ""
    return {
        "http_status": status, "bytes_sha256": sha256(body),
        "baseline_base": resource["base"], "baseline_commit": expected_commit,
        "proof_host": host, "verified_at": now(),
    }


def event_files(review: Path, actor: str | None = None) -> list[tuple[str, str, dict[str, Any]]]:
    actors = (actor,) if actor else ("human", "agent")
    events: list[tuple[str, str, dict[str, Any]]] = []
    for selected in actors:
        folder = review / selected
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or not path.name.endswith(".json"):
                continue
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(body, dict):
                events.append((path.name, selected, body))
    return sorted(events, key=lambda item: item[0])


def finish_event_for(resource: Mapping[str, Any]) -> str:
    spec = Path(resource.get("spec_file") or (Path(resource["root"]) / resource["spec"]))
    review = Path(str(spec) + ".review")
    humans = event_files(review, "human")
    if not humans:
        raise LauncherError("Finish review hand-off is missing")
    handoff_name, _, handoff_body = humans[-1]
    if "-handoff-" not in handoff_name or handoff_body.get("event") != "handoff":
        raise LauncherError("the latest human event is not a Finish review hand-off")
    cursor = review / resource["cursor_name"]
    try:
        cursor_lines = cursor.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LauncherError("Finish review hand-off is not processed by the registered cursor") from exc
    if handoff_name not in cursor_lines:
        raise LauncherError("Finish review hand-off is not processed by the registered cursor")
    previous = [name for name, _, _ in humans[:-1] if "-handoff-" in name]
    previous_name = previous[-1] if previous else ""
    batch = [item for item in humans if previous_name < item[0] <= handoff_name]
    for name, _, body in batch[:-1]:
        if body.get("event") != "status" or body.get("status") != "resolved":
            raise LauncherError(f"Finish batch contains non-resolution event: {name}")
    current = spec.read_text(encoding="utf-8", errors="replace")
    if re.search(r"\bdata-spec-tbd(?:\s*=|\s|>)", current, re.IGNORECASE):
        raise LauncherError("current spec still has data-spec-tbd")
    folded = event_files(review)
    folded = [item for item in folded if item[0] <= handoff_name]
    threads: dict[str, dict[str, Any]] = {}
    message_thread: dict[str, str] = {}
    message_slot: dict[str, tuple[str, int]] = {}
    last_handoff = handoff_name

    def status_for(name: str) -> str:
        return "draft" if name > last_handoff else "pending"

    for name, actor, body in folded:
        event = body.get("event")
        event_id = body.get("id")
        if event == "comment" and actor == "human" and isinstance(event_id, str):
            threads[event_id] = {"status": status_for(name), "latest": event_id, "messages": [name]}
            message_thread[event_id] = event_id
            message_slot[event_id] = (event_id, 0)
        elif event == "reply":
            responds = body.get("respondsTo")
            thread_id = body.get("threadId") or message_thread.get(responds) or (responds if responds in threads else None)
            if not thread_id or thread_id not in threads or not isinstance(event_id, str):
                continue
            thread = threads[thread_id]
            index = len(thread["messages"])
            thread["messages"].append(name)
            message_thread[event_id] = thread_id
            message_slot[event_id] = (thread_id, index)
            if actor == "human":
                thread["latest"] = event_id
                thread["status"] = status_for(name)
            elif responds == thread.get("latest"):
                thread["status"] = body.get("status") or "acknowledged"
        elif event == "edit" and actor == "human":
            supersedes = body.get("supersedes")
            prior = message_slot.get(supersedes)
            thread_id = body.get("threadId") or message_thread.get(supersedes)
            thread_id = prior[0] if prior else thread_id
            if not thread_id or thread_id not in threads or not isinstance(event_id, str):
                continue
            thread = threads[thread_id]
            if prior:
                thread["messages"][prior[1]] = name
            else:
                thread["messages"].append(name)
            message_thread[event_id] = thread_id
            message_slot[event_id] = (thread_id, prior[1] if prior else len(thread["messages"]) - 1)
            thread["latest"] = event_id
            thread["status"] = status_for(name)
        elif event == "status":
            responds = body.get("respondsTo")
            thread_id = body.get("threadId") or message_thread.get(responds) or (responds if responds in threads else None)
            if thread_id in threads:
                threads[thread_id]["status"] = body.get("status")
    if any(thread.get("status") != "resolved" for thread in threads.values()):
        raise LauncherError("Finish review requires every folded thread to be resolved")
    return handoff_name


def resource_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--resource", action="append", required=True)
    parser.add_argument("--owner", action="append", required=True)
    parser.add_argument("--checker", action="append", required=True)
    parser.add_argument("--cursor-name", action="append", required=True)
    parser.add_argument("--slug", action="append")
    parser.add_argument("--collection", action="append")


def select_flag(values: Sequence[str] | None, index: int, count: int, name: str, default: str | None = None) -> str | None:
    if not values:
        return default
    if len(values) == 1:
        return values[0]
    if len(values) != count:
        raise LauncherError(f"{name} must be supplied once or once per resource")
    return values[index]


def parse_resources(args: argparse.Namespace) -> list[dict[str, Any]]:
    values = args.resource
    count = len(values)
    result = []
    for index, value in enumerate(values):
        owner = select_flag(args.owner, index, count, "--owner")
        checker = select_flag(args.checker, index, count, "--checker")
        cursor = select_flag(args.cursor_name, index, count, "--cursor-name")
        slug = select_flag(args.slug, index, count, "--slug")
        collection = select_flag(args.collection, index, count, "--collection")
        assert owner is not None and checker is not None and cursor is not None
        result.append(parse_resource_spec(value, owner, checker, cursor, slug, collection))
    return result


def bind_host(args: argparse.Namespace) -> str:
    return args.bind or os.environ.get("SPEC_CHAT_BIND_HOST") or "0.0.0.0"


def proof_host(args: argparse.Namespace, bind: str) -> str:
    selected = args.proof_host or os.environ.get("SPEC_CHAT_PROOF_HOST") or os.environ.get("REVIEW_PROOF_HOST")
    if selected:
        return selected
    if bind not in {"0.0.0.0", "::"}:
        return bind
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError as exc:
        raise ProofError("cannot determine proof host; set SPEC_CHAT_PROOF_HOST") from exc
    finally:
        probe.close()


def state_dir(args: argparse.Namespace) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    base = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return (base / "spec-chat" / "hosting" / "default").resolve()


def paths(state: Path) -> tuple[Path, Path, Path]:
    return state / "registry.toml", state / "receipt.toml", state / "server.log"


def initial_receipt(records: Sequence[Mapping[str, Any]], *, pid: int, port: int, bind: str,
                    public_url: str, source_revision: str, proofs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    resources = []
    for record in records:
        resource = dict(record)
        resource.pop("spec_file", None)
        resource.pop("resolved_base_commit", None)
        resource["stable_path"] = stable_path(record)
        resource["resolved_base_commit"] = record.get("resolved_base_commit") or run_git(
            Path(record["root"]), "rev-parse", "--verify", record["base"] + "^{commit}"
        )
        resource["spec_sha256"] = sha256((Path(record["root"]) / record["spec"]).read_bytes())
        resource["finish_event"] = ""
        resource["proof"] = dict(proofs[record["id"]])
        resources.append(resource)
    return {
        "service_kind": "spec-chat", "state": "running", "pid": pid, "port": port,
        "bind": bind, "public_url": public_url, "source_revision": source_revision,
        "started_at": now(), "resource": resources,
    }


def receipt_resource(receipt: dict[str, Any], rid: str) -> dict[str, Any]:
    for item in receipt.get("resource", []):
        if item.get("id") == rid:
            return item
    raise LauncherError(f"unknown resource id: {rid}")


def sync_receipt_lifecycle(receipt: dict[str, Any], record: Mapping[str, Any]) -> None:
    item = receipt_resource(receipt, record["id"])
    item["lifecycle"] = record["lifecycle"]
    item["cursor_snapshot"] = record.get("cursor_snapshot", item.get("cursor_snapshot", {}))
    item["finish_event"] = record.get("finish_event", item.get("finish_event", ""))


def owned_running(receipt: Mapping[str, Any], registry: Path) -> int:
    pid = receipt.get("pid")
    if receipt.get("state") != "running" or not process_owned(pid, registry):
        raise LauncherError("review host is not running under the recorded owned process")
    assert isinstance(pid, int)
    return pid


def launch(args: argparse.Namespace) -> int:
    state = state_dir(args); registry, receipt_path, log_path = paths(state)
    state.mkdir(parents=True, exist_ok=True); state.chmod(0o700)
    prior_bytes = registry.read_bytes() if registry.exists() else None
    if receipt_path.exists():
        old = read_receipt(receipt_path)
        if process_owned(old.get("pid"), registry):
            raise LauncherError("receipt names a live owned review host")
    resources = parse_resources(args)
    records = [registry_record(resource) for resource in resources]
    validate_records(records)
    write_registry(registry, records)
    child: subprocess.Popen[str] | None = None
    try:
        bind = bind_host(args)
        selected_host = proof_host(args, bind)
        port = select_port(approved_ports(), bind)
        command = [sys.executable, str(SERVER), "--registry", str(registry), "--bind", bind,
                   "--port", str(port), "--host", selected_host]
        with log_path.open("w", encoding="utf-8") as log:
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True, text=True)
        url = None
        deadline = time.monotonic() + 8
        pattern = re.compile(r"spec-chat review-serve on (https?://\S+)")
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise LauncherError("review server exited before printing its URL")
            try:
                text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            matches = pattern.findall(text)
            if matches:
                url = matches[-1]
                break
            time.sleep(0.05)
        if not url:
            raise LauncherError("review server did not print a URL")
        proofs = {resource["id"]: prove_resource(url, resource) for resource in resources}
        receipt = initial_receipt(records, pid=child.pid, port=port, bind=bind,
                                  public_url=url, source_revision=run_git(REPO, "rev-parse", "HEAD"), proofs=proofs)
        write_receipt(receipt_path, receipt)
        print(f"service URL: {url}")
        for resource in resources:
            print(f"{resource['id']} URL: {url.rstrip('/')}{stable_path(resource)}")
        return 0
    except BaseException as exc:
        if child is not None and child.poll() is None:
            with contextlib.suppress(Exception):
                os.kill(child.pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                child.wait(timeout=3)
        failure = {"service_kind": "spec-chat", "state": "failed", "failed_at": now(), "error": str(exc), "resource": []}
        with contextlib.suppress(Exception):
            write_receipt(receipt_path, failure)
        if prior_bytes is None:
            with contextlib.suppress(FileNotFoundError):
                registry.unlink()
        else:
            with contextlib.suppress(Exception):
                atomic_write(registry, prior_bytes.decode("utf-8"))
        raise


def add_resources(args: argparse.Namespace) -> int:
    state = state_dir(args); registry, receipt_path, _ = paths(state)
    receipt = read_receipt(receipt_path); pid = owned_running(receipt, registry)
    existing = read_registry(registry)
    additions = parse_resources(args)
    new_records = [registry_record(item) for item in additions]
    candidate = existing + new_records
    validate_records(candidate)
    old_bytes = registry.read_bytes()
    write_registry(registry, candidate)
    try:
        url = receipt["public_url"]
        proofs = {item["id"]: prove_resource(url, item) for item in additions}
        for item in additions:
            record = next(record for record in new_records if record["id"] == item["id"])
            resource = dict(record)
            resource.pop("spec_file", None); resource.pop("resolved_base_commit", None)
            resource["stable_path"] = stable_path(item)
            resource["resolved_base_commit"] = item["resolved_base_commit"]
            resource["spec_sha256"] = sha256(Path(item["spec_file"]).read_bytes())
            resource["finish_event"] = ""; resource["proof"] = proofs[item["id"]]
            receipt.setdefault("resource", []).append(resource)
        receipt["updated_at"] = now(); write_receipt(receipt_path, receipt)
        for item in additions:
            print(f"{item['id']} URL: {url.rstrip('/')}{stable_path(item)}")
        return 0
    except BaseException:
        atomic_write(registry, old_bytes.decode("utf-8"))
        raise


def lifecycle(args: argparse.Namespace) -> int:
    state = state_dir(args); registry, receipt_path, _ = paths(state)
    receipt = read_receipt(receipt_path); owned_running(receipt, registry)
    records = read_registry(registry)
    record = next((item for item in records if item.get("id") == args.id), None)
    if record is None:
        raise LauncherError(f"unknown resource id: {args.id}")
    command = args.command
    current = record["lifecycle"]
    if command == "park":
        if current != "serving": raise LauncherError("only a serving resource can be parked")
        record["lifecycle"] = "parked"; record["cursor_snapshot"] = cursor_snapshot(Path(record["root"]) / record["spec"], record["cursor_name"])
    elif command == "resume":
        if current != "parked": raise LauncherError("only a parked resource can resume")
        record["lifecycle"] = "serving"
    elif command == "finish":
        if current not in {"serving", "parked"}: raise LauncherError("only a serving or parked resource can finish")
        record["finish_event"] = finish_event_for(record); record["cursor_snapshot"] = cursor_snapshot(Path(record["root"]) / record["spec"], record["cursor_name"]); record["lifecycle"] = "finished"
    elif command == "remove":
        if current == "removed": raise LauncherError("resource is already removed")
        record["lifecycle"] = "removed"
    record["updated_at"] = now()
    write_registry(registry, records)
    sync_receipt_lifecycle(receipt, record); receipt["updated_at"] = now(); write_receipt(receipt_path, receipt)
    print(f"{args.id}: {record['lifecycle']}")
    return 0


def stop_host(args: argparse.Namespace) -> int:
    state = state_dir(args); registry, receipt_path, _ = paths(state)
    receipt = read_receipt(receipt_path); pid = owned_running(receipt, registry)
    records = read_registry(registry)
    unfinished = [item["id"] for item in records if item["lifecycle"] in {"serving", "parked"}]
    if unfinished:
        raise LauncherError("cannot stop while resources are serving or parked: " + ", ".join(unfinished))
    kill_owned(pid, registry)
    receipt["state"] = "stopped"; receipt["stopped_at"] = now(); write_receipt(receipt_path, receipt)
    print("review host stopped")
    return 0


def status_host(args: argparse.Namespace) -> int:
    _, receipt_path, _ = paths(state_dir(args)); receipt = read_receipt(receipt_path)
    for key in ("service_kind", "state", "pid", "port", "bind"):
        if key in receipt: print(f"{key}={receipt[key]}")
    for item in receipt.get("resource", []):
        print(f"resource={item.get('id')} lifecycle={item.get('lifecycle')}")
    if args.show_url and receipt.get("public_url"):
        print(f"public_url={receipt['public_url']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", help="launcher state directory")
    parser.add_argument("--bind", help="server bind address")
    parser.add_argument("--proof-host", help="host used for box-side proof")
    parser.add_argument("--show-url", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "add"):
        sub = commands.add_parser(name); resource_flags(sub)
        sub.add_argument("--state-dir", dest="state_dir", default=argparse.SUPPRESS)
        sub.add_argument("--bind", dest="bind", default=argparse.SUPPRESS)
        sub.add_argument("--proof-host", dest="proof_host", default=argparse.SUPPRESS)
    for name in ("park", "resume", "finish", "remove"):
        sub = commands.add_parser(name); sub.add_argument("--id", required=True)
        sub.add_argument("--state-dir", dest="state_dir", default=argparse.SUPPRESS)
    sub = commands.add_parser("stop"); sub.add_argument("--state-dir", dest="state_dir", default=argparse.SUPPRESS)
    sub = commands.add_parser("status"); sub.add_argument("--show-url", action="store_true"); sub.add_argument("--state-dir", dest="state_dir", default=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "start": return launch(args)
        if args.command == "add": return add_resources(args)
        if args.command in {"park", "resume", "finish", "remove"}: return lifecycle(args)
        if args.command == "stop": return stop_host(args)
        return status_host(args)
    except LauncherError as exc:
        print(f"review-host: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
