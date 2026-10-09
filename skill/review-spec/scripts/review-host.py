#!/usr/bin/env python3
"""Register Spec Chat review resources and own their review server."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import ipaddress
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
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT = Path(__file__).resolve()
REPO = SCRIPT.parents[3]
SERVER = SCRIPT.parents[1] / "assets" / "review-serve.py"
SERVER_PATH = str(SERVER.resolve())
VERIFY_SCRIPT = SCRIPT.parent / "verify-review.py"
_verify_spec = importlib.util.spec_from_file_location("spec_chat_verify_review", VERIFY_SCRIPT)
_verify_module = importlib.util.module_from_spec(_verify_spec)
assert _verify_spec.loader is not None
_verify_spec.loader.exec_module(_verify_module)
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
SAFE_CURSOR_RE = re.compile(r"[^/\\]+\Z")
PROCESS_FIELDS = ("pid", "port", "bind", "host", "url")
DEFAULT_CURSOR = ".cursor-owner"
RESOURCE_FIELDS = (
    "id", "slug", "project", "root", "narrow_root", "spec", "path", "base", "accepted", "owner", "checker",
    "cursor_name", "test", "registered_at", "updated_at",
)


class LauncherError(RuntimeError):
    pass


class ProofError(LauncherError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    raise TypeError(f"unsupported TOML value: {type(value)!r}")


def dump_registry(process: Mapping[str, Any] | None, records: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    if process is not None:
        lines.append("[process]")
        for key in PROCESS_FIELDS:
            if key in process:
                lines.append(f"{key} = {toml_value(process[key])}")
        if records:
            lines.append("")
    for index, record in enumerate(records):
        lines.append("[[resource]]")
        for key in RESOURCE_FIELDS:
            if key in record:
                lines.append(f"{key} = {toml_value(record[key])}")
        if index != len(records) - 1:
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
        if match and int(match.group(1)) not in found:
            found.append(int(match.group(1)))
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


def process_owns_registry(pid: Any, registry: Path) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    command = process_cmdline(pid)
    if not command or str(registry.resolve()) not in command:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_exited(pid: int) -> bool:
    """Every thread gone or a zombie: only then are its sockets closed (its cmdline empties earlier in exit)."""
    try:
        tasks = list(Path(f"/proc/{pid}/task").iterdir())
    except OSError:
        return True
    for task in tasks:
        try:
            stat = (task / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if stat.rpartition(")")[2].split()[:1] not in (["Z"], ["X"]):
            return False
    return True


def stop_process(pid: int, registry: Path) -> None:
    if not process_owns_registry(pid, registry):
        raise LauncherError("registry process is not running under the recorded review server")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if process_exited(pid):
            return
        time.sleep(0.05)
    raise LauncherError("review server did not stop after SIGTERM")


def path_inside(path: Path, parent: Path, *, strict: bool = False) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return not strict or path != parent


def resource_toplevel(root: Path) -> Path:
    root = root.expanduser().resolve()
    return Path(run_git(root, "rev-parse", "--show-toplevel")).resolve()


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


def git_project(top: Path) -> str:
    """The last path part of origin without .git, else the worktree directory name."""
    origin = run_git(top, "remote", "get-url", "origin", check=False).rstrip("/")
    name = re.split(r"[/:]", origin)[-1].removesuffix(".git") if origin else ""
    return name or top.name


def default_base(top: Path) -> str:
    """The merge base of HEAD with the default branch: origin/HEAD, else main."""
    for ref in ("origin/HEAD", "main"):
        base = run_git(top, "merge-base", "HEAD", ref, check=False)
        if base:
            return base
    raise LauncherError(f"cannot infer a base in {top}: no origin/HEAD or main; pass --base <ref>")


def plain_resource(value: str, base: str | None) -> tuple[str, str, str, str]:
    """A plain spec path: (project, root, spec, base) inferred from its Git worktree."""
    spec_file = Path(value).expanduser().resolve()
    if not spec_file.is_file():
        raise LauncherError(f"spec path does not exist: {value}")
    top = resource_toplevel(spec_file.parent)
    if not path_inside(spec_file, top, strict=True):
        raise LauncherError(f"spec path is outside its worktree: {value}")
    return git_project(top), str(top), spec_file.relative_to(top).as_posix(), base or default_base(top)


def parse_resource_spec(value: str, owner: str, checker: str, cursor_name: str,
                        slug: str | None = None, collection: str | None = None) -> dict[str, Any]:
    try:
        project, rest = value.split("=", 1)
        root_text, base = rest.rsplit("@", 1)
        root_text, spec = root_text.split(":", 1)
    except ValueError as exc:
        raise LauncherError("resource must be PROJECT_ID=ROOT:SPEC_PATH@BASE") from exc
    return build_resource(project, root_text, spec, base, owner, checker, cursor_name, slug, collection)


def build_resource(project: str, root_text: str, spec: str, base: str, owner: str, checker: str, cursor_name: str,
                   slug: str | None = None, collection: str | None = None) -> dict[str, Any]:
    if not owner.strip() or not checker.strip() or not cursor_name.strip():
        raise LauncherError("--owner, --checker, and --cursor-name are required")
    if not SAFE_CURSOR_RE.fullmatch(cursor_name) or cursor_name in {".", ".."}:
        raise LauncherError("cursor name must be a single file name")
    project = project.strip()
    base = base.strip()
    if not project or not base or not root_text or not spec:
        raise LauncherError("resource must name project, root, spec, and base")
    top = resource_toplevel(Path(root_text))
    spec = spec.replace("\\", "/")
    spec_path = Path(spec)
    if spec_path.is_absolute() or not spec.endswith(".spec.html"):
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
    base = run_git(top, "rev-parse", "--verify", base + "^{commit}")
    return {
        "id": f"spec:{selected_slug}:{project}::{spec}", "slug": selected_slug, "project": project, "root": str(top),
        "narrow_root": str(narrow), "spec": spec, "spec_file": str(spec_file),
        "base": base,
        "owner": owner.strip(), "checker": checker.strip(), "cursor_name": cursor_name,
    }


def registry_record(resource: Mapping[str, Any], old: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One upsert rule: an existing row keeps every recorded field register was not given; defaults fill only a new row."""
    stamp = now()
    fields = ("id", "slug", "project", "root", "narrow_root", "spec", "path", "base", "owner", "checker", "cursor_name")
    defaulted = resource.get("defaulted", ())
    given = {key: resource[key] for key in fields if key not in defaulted}
    defaults = {key: resource[key] for key in defaulted}
    result = {"registered_at": stamp, **defaults, **(old or {}), **given, "updated_at": stamp}
    if resource.get("test"):
        result["test"] = True
    return result


def row_path(record: Mapping[str, Any]) -> str:
    return record.get("path") or f"{record['slug']}/{record['spec']}"


def stable_path(resource: Mapping[str, Any]) -> str:
    return "/" + row_path(resource)


def assign_path(rows: Sequence[Mapping[str, Any]], resource: dict[str, Any]) -> None:
    """Re-registering a row id keeps its path; a new row is plain unless another project holds the slug.

    A row at the same slug and spec is the same row, keeping its id and path, when it already carries this project
    (an adopted legacy row, at any root) or carries no project and sits at the same resolved root (a legacy row).
    """
    def same(row: Mapping[str, Any]) -> bool:
        if row["slug"] != resource["slug"] or row["spec"].replace("\\", "/") != resource["spec"]:
            return False
        if row.get("project") == resource["project"]:
            return True
        return not row.get("project") and Path(row["root"]).resolve() == Path(resource["root"]).resolve()

    old = next((row for row in rows if row["id"] == resource["id"]), None) or next(filter(same, rows), None)
    if old:
        resource["id"] = old["id"]
    slug, project, spec = resource["slug"], resource["project"], resource["spec"]
    shared = any(row["slug"] == slug and row.get("project") != project for row in rows)
    resource["path"] = row_path(old) if old else (f"{slug}/{project}/{spec}" if shared else f"{slug}/{spec}")


def require_fields(record: Mapping[str, Any]) -> None:
    required = ("id", "slug", "root", "narrow_root", "spec", "base", "owner", "checker", "cursor_name")
    missing = [key for key in required if not isinstance(record.get(key), str) or not record[key].strip()]
    if missing:
        raise LauncherError("resource missing required field: " + ", ".join(missing))


def validate_records(records: Sequence[Mapping[str, Any]], stale_ids: frozenset[str] = frozenset()) -> None:
    """Structural checks always run. Filesystem checks (worktree, spec file, base commit) are skipped for stale_ids."""
    ids: set[str] = set()
    stable: set[str] = set()
    for record in records:
        require_fields(record)
        rid = record["id"]
        if rid in ids:
            raise LauncherError(f"duplicate resource identity: {rid}")
        slug = record["slug"]
        if not SLUG_RE.fullmatch(slug) or slug in {"api", "static"}:
            raise LauncherError(f"invalid or reserved resource slug: {slug}")
        root = Path(record["root"])
        if not root.is_absolute():
            raise LauncherError(f"resource root must be absolute: {rid}")
        spec = record["spec"].replace("\\", "/")
        if spec.startswith("/") or not spec.endswith(".spec.html"):
            raise LauncherError(f"resource spec path is invalid: {spec}")
        if rid not in stale_ids:
            top = resource_toplevel(root)
            if root.resolve() != top:
                raise LauncherError(f"resource root must be worktree toplevel: {rid}")
            narrow = Path(record["narrow_root"]).resolve()
            if not narrow.is_dir() or not path_inside(narrow, top, strict=True):
                raise LauncherError(f"resource collection is invalid: {rid}")
            spec_file = (top / spec).resolve()
            if not path_inside(spec_file, narrow, strict=True) or not spec_file.is_file():
                raise LauncherError(f"resource spec is missing or outside its collection: {rid}")
            run_git(root, "rev-parse", "--verify", record["base"] + "^{commit}")
        if not SAFE_CURSOR_RE.fullmatch(record["cursor_name"]):
            raise LauncherError(f"invalid cursor name: {record['cursor_name']}")
        key = row_path(record)
        if key in stable:
            raise LauncherError(f"duplicate stable resource path: {key}")
        ids.add(rid)
        stable.add(key)


def is_row_stale(record: Mapping[str, Any]) -> str | None:
    """Return a reason when the row's spec file cannot be reached, else None."""
    try:
        top = resource_toplevel(Path(record["root"]))
    except LauncherError:
        return "worktree is unreachable"
    spec_file = (top / record["spec"].replace("\\", "/")).resolve()
    if not spec_file.is_file():
        return "spec file is missing"
    return None


def stale_rows(records: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    """Ids of untouched rows whose spec cannot be reached, each reported; they skip filesystem checks."""
    stale_ids: set[str] = set()
    for item in records:
        reason = is_row_stale(item)
        if reason:
            print(f"review-host: warning: stale row {item['id']}: {reason}", file=sys.stderr)
            stale_ids.add(item["id"])
    return frozenset(stale_ids)


def read_registry_document(path: Path, validate: bool = True) -> dict[str, Any]:
    document = read_toml(path, missing={"resource": []})
    if not isinstance(document, dict):
        raise LauncherError("registry must be a TOML table")
    records = document.get("resource", [])
    if not isinstance(records, list):
        raise LauncherError("registry resource entries must be an array")
    if any(not isinstance(item, dict) for item in records):
        raise LauncherError("registry resource entry must be a table")
    result = [dict(item) for item in records]
    if validate:
        validate_records(result)
    else:
        for record in result:
            require_fields(record)
    process = document.get("process")
    if process is not None:
        if not isinstance(process, dict) or not isinstance(process.get("pid"), int) or not isinstance(process.get("port"), int):
            raise LauncherError("registry process must contain integer pid and port")
        bind = process.get("bind")
        if bind is not None and not isinstance(bind, str):
            raise LauncherError("registry process bind must be a string")
        extra = {key: process[key] for key in ("host", "url") if isinstance(process.get(key), str) and process[key]}
        process = {"pid": process["pid"], "port": process["port"], **({"bind": bind} if bind else {}), **extra}
    return {"resource": result, "process": process}


def write_registry(path: Path, records: Sequence[Mapping[str, Any]], process: Mapping[str, Any] | None = None,
                   stale_ids: frozenset[str] = frozenset()) -> None:
    validate_records(records, stale_ids)
    cleaned = [{key: record[key] for key in RESOURCE_FIELDS if key in record} for record in records]
    atomic_write(path, dump_registry(process, cleaned))


def prove_resource(public_url: str, resource: Mapping[str, Any]) -> dict[str, Any]:
    exact_base = run_git(
        Path(resource["root"]), "rev-parse", "--verify", resource["base"] + "^{commit}"
    )
    review_url = public_url.rstrip("/") + urllib.parse.quote(stable_path(resource), safe="/")
    review_url += "?focus=changes&base=" + urllib.parse.quote(exact_base, safe="")
    try:
        facts = _verify_module.verify(
            resource["root"], resource["spec_file"], review_url, resource["base"]
        )
    except (OSError, ValueError) as exc:
        raise ProofError(f"review proof failed for {resource['id']}: {exc}") from exc
    return {
        "http_status": 200,
        "bytes_sha256": facts["specSha256"],
        "baseline_commit": facts["base"],
        "verified_at": now(),
    }


def resource_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("specs", nargs="*", metavar="SPEC_PATH",
                        help="a spec path; project, root, and base are inferred from its Git worktree")
    parser.add_argument("--resource", action="append", default=[], metavar="PROJECT_ID=ROOT:SPEC_PATH@BASE")
    parser.add_argument("--base", action="append", help="base ref for plain spec paths; default the merge base "
                                                         "of HEAD with origin/HEAD, else main")
    parser.add_argument("--owner", action="append", required=True)
    parser.add_argument("--checker", action="append", help="default: the owner")
    parser.add_argument("--cursor-name", action="append", help=f"default: {DEFAULT_CURSOR}")
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
    """--resource entries, then plain spec paths; per-resource flags follow that order."""
    count = len(args.resource) + len(args.specs)
    if not count:
        raise LauncherError("name a spec path or --resource PROJECT_ID=ROOT:SPEC_PATH@BASE")
    if args.base and not args.specs:
        raise LauncherError("--base applies only to plain spec paths")
    result = []
    for index in range(count):
        owner = select_flag(args.owner, index, count, "--owner")
        assert owner is not None
        checker = select_flag(args.checker, index, count, "--checker", owner)
        cursor = select_flag(args.cursor_name, index, count, "--cursor-name", DEFAULT_CURSOR)
        slug = select_flag(args.slug, index, count, "--slug")
        collection = select_flag(args.collection, index, count, "--collection")
        assert checker is not None and cursor is not None
        defaulted = [key for key, flag in (("checker", args.checker), ("cursor_name", args.cursor_name)) if not flag]
        if index < len(args.resource):
            resource = parse_resource_spec(args.resource[index], owner, checker, cursor, slug, collection)
        else:
            plain = index - len(args.resource)
            ref = select_flag(args.base, plain, len(args.specs), "--base")
            resource = build_resource(*plain_resource(args.specs[plain], ref), owner, checker, cursor, slug, collection)
            if ref is None:
                defaulted.append("base")
        resource["defaulted"] = tuple(defaulted)
        result.append(resource)
    return result


LOOPBACK = "127.0.0.1"


def resolved_addresses(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise LauncherError(f"host address cannot be resolved: {host}") from exc
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise LauncherError(f"host address cannot be resolved: {host}")
        return addresses


def is_loopback(host: str) -> bool:
    addresses = resolved_addresses(host)
    return all((getattr(address, "ipv4_mapped", None) or address).is_loopback for address in addresses)


def public_host(host: str, *, role: str, allow_wildcard: bool = False) -> str:
    addresses = resolved_addresses(host)
    effective = [getattr(address, "ipv4_mapped", None) or address for address in addresses]
    if any(address.is_loopback for address in effective):
        raise LauncherError(f"public {role} must not be loopback; omit --public for a private page")
    if any(address.is_unspecified for address in effective) and not allow_wildcard:
        raise LauncherError(f"{role} must be a concrete host address")
    return str(next((address for address in addresses if address.version == 4), addresses[0]))


def select_bind(args: argparse.Namespace, process: Mapping[str, Any] | None) -> str:
    """Explicit --public/--private wins; otherwise keep the recorded choice; default private loopback."""
    if args.public:
        return public_host(args.public, role="bind host", allow_wildcard=True)
    if args.private:
        return LOOPBACK
    recorded = (process or {}).get("bind")
    return recorded if recorded else LOOPBACK


def proof_host(args: argparse.Namespace, bind: str) -> str:
    if is_loopback(bind):
        return bind
    selected = args.proof_host or os.environ.get("SPEC_CHAT_PROOF_HOST") or os.environ.get("REVIEW_PROOF_HOST")
    if not selected and bind not in {"0.0.0.0", "::"}:
        selected = bind
    if not selected:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.connect(("8.8.8.8", 80))
                selected = probe.getsockname()[0]
            except OSError as exc:
                raise ProofError("cannot determine proof host; set SPEC_CHAT_PROOF_HOST") from exc
    return public_host(selected, role="proof host")


def bind_ports(bind: str) -> tuple[int, ...]:
    """Public binds need an approved ingress port; loopback takes one if configured, else any free port."""
    if not is_loopback(bind):
        return approved_ports()
    for name in ("SPEC_CHAT_APPROVED_INGRESS_PORTS", "REVIEW_APPROVED_INGRESS_PORTS"):
        if os.environ.get(name):
            return parse_ports(os.environ[name])
    return (0,)


def start_plan(args: argparse.Namespace, process: Mapping[str, Any] | None) -> tuple[str, str, int | None]:
    """Bind, proof host, and recorded port to try first; a service on its recorded bind keeps its host and port.

    A changed bind keeps the recorded port only where that bind may take it: any port on loopback, else an
    approved one."""
    recorded = process or {}
    bind = select_bind(args, process)
    same = (recorded.get("bind") or LOOPBACK) == bind
    host = recorded.get("host") if same and not args.proof_host else None
    port = recorded.get("port")
    if port and not same and not is_loopback(bind) and port not in approved_ports():
        port = None
    return bind, host or proof_host(args, bind), port


def select_start_port(bind: str, recorded: int | None) -> int:
    if recorded:
        with contextlib.suppress(LauncherError):
            return select_port((recorded,), bind)
    return select_port(bind_ports(bind), bind)


def server_command(registry: Path, bind: str, port: int, host: str) -> list[str]:
    return [str(Path(sys.executable).resolve()), SERVER_PATH, "--registry", str(registry.resolve()),
            "--bind", bind, "--port", str(port), "--host", host]


def state_dir(args: argparse.Namespace) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    base = Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser()
    return (base / "spec-chat" / "hosting" / "default").resolve()


def paths(state: Path) -> tuple[Path, Path, Path]:
    return state / "registry.toml", state / "server.log", state / ".state.lock"


@contextlib.contextmanager
def state_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(state / ".state.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def registry_state(path: Path, validate: bool = True) -> tuple[list[dict[str, Any]], dict[str, int] | None]:
    document = read_registry_document(path, validate)
    return document["resource"], document["process"]


def running_url(log_path: Path) -> str:
    """The server prints its actual URL at start; without it the bind is unknown, so never guess."""
    if log_path.exists():
        match = re.search(r"spec-chat review-serve on (https?://\S+)", log_path.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(1).rstrip("/")
    raise LauncherError("cannot read the running review URL from its log; run stop, then register again")


def wake_status(owner: str, state: Path) -> str:
    """Report whether the registered wake provider's check resolves the owner; never prompts it
    (review-service #providers). No valid providers/wake.toml means unavailable."""
    try:
        provider = tomllib.loads((state / "providers" / "wake.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unavailable"
    commands = [provider.get(key) for key in ("check", "send")]
    if not all(isinstance(argv, list) and argv and all(isinstance(arg, str) for arg in argv) for argv in commands):
        return "unavailable"
    argv = [arg.replace("{owner}", owner).replace("{artifact}", "").replace("{message}", "") for arg in commands[0]]
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return "unavailable"
    try:
        code = process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return "unavailable"
    return "verified" if code == 0 else "unavailable"


def print_access(url: str, bind: str | None) -> None:
    port = urllib.parse.urlsplit(url).port
    if bind and not is_loopback(bind):
        print(f"warning: public review page on {bind}:{port} has no login; anyone who can reach it can read and comment",
              file=sys.stderr)
    else:
        print(f"private: loopback only; reach it with ssh -L {port}:127.0.0.1:{port} <box>, "
              "or restart with --public <tailscale-address>")


def print_urls(url: str, additions: list[dict[str, str]], state: Path) -> None:
    print(f"review URL: {url}")
    for item in additions:
        owner = item["owner"]
        print(f"{item['id']} URL: {url.rstrip('/')}{stable_path(item)} wake={wake_status(owner, state)} owner={owner}")


def register(args: argparse.Namespace) -> int:
    state = state_dir(args)
    registry, log_path, _ = paths(state)
    parsed = parse_resources(args)
    if args.test:
        for item in parsed:
            item["test"] = True
    with state_lock(state):
        old_bytes = registry.read_bytes() if registry.exists() else None
        # Rows being replaced may point at a deleted root; only the resulting candidate set is validated.
        existing, process = registry_state(registry, validate=False)
        additions = []
        for index, item in enumerate(parsed):
            assign_path(existing + parsed[:index], item)
            old = next((row for row in existing if row["id"] == item["id"]), None)
            additions.append(registry_record(item, old))
            item.update(additions[-1])
        replacement_ids = {item["id"] for item in additions}
        untouched = [item for item in existing if item["id"] not in replacement_ids]
        stale = stale_rows(untouched)
        candidate = untouched + additions
        validate_records(candidate, stale)
        child: subprocess.Popen[str] | None = None
        try:
            live = process and process_owns_registry(process["pid"], registry)
            # A registry without bind is private. An explicit different visibility restarts the live server,
            # planned first so a bad bind or proof host leaves it running.
            bind, plan = (process or {}).get("bind") or LOOPBACK, None
            if live and (args.public or args.private) and select_bind(args, None) != bind:
                plan = start_plan(args, process)
                if not plan[2]:
                    # No recorded port this bind may take: pick one while the old service still runs.
                    plan = (*plan[:2], select_port(bind_ports(plan[0]), plan[0]))
                stop_process(process["pid"], registry)
                live = False
            if live:
                url = process.get("url") or running_url(log_path)
                process = {**process, "url": url}
                write_registry(registry, candidate, process, stale)
                for item in parsed:
                    prove_resource(url, item)
                print_access(url, bind)
                print_urls(url, additions, state)
                return 0

            bind, host, recorded_port = plan or start_plan(args, process)
            port = select_start_port(bind, recorded_port)
            write_registry(registry, candidate, stale_ids=stale)
            command = server_command(registry, bind, port, host)
            with log_path.open("w", encoding="utf-8") as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, text=True)
            deadline = time.monotonic() + 8
            pattern = re.compile(r"spec-chat review-serve on (https?://\S+)")
            url = None
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise LauncherError("review server exited before printing its URL")
                text = log_path.read_text(encoding="utf-8", errors="replace")
                matches = pattern.findall(text)
                if matches:
                    url = matches[-1]
                    break
                time.sleep(0.05)
            if not url:
                raise LauncherError("review server did not print a URL")
            for item in parsed:
                prove_resource(url, item)
            process = {"pid": child.pid, "port": urllib.parse.urlsplit(url).port or port, "bind": bind,
                       "host": host, "url": url}
            write_registry(registry, candidate, process, stale)
            print_access(url, bind)
            print_urls(url, additions, state)
            return 0
        except BaseException:
            if child is not None and child.poll() is None:
                with contextlib.suppress(OSError):
                    os.kill(child.pid, signal.SIGTERM)
                with contextlib.suppress(Exception):
                    child.wait(timeout=3)
            if old_bytes is None:
                with contextlib.suppress(FileNotFoundError):
                    registry.unlink()
            else:
                atomic_write(registry, old_bytes.decode("utf-8"))
            raise


def remove(args: argparse.Namespace) -> int:
    state = state_dir(args)
    registry, _, _ = paths(state)
    with state_lock(state):
        records, process = registry_state(registry, validate=False)
        remaining = [record for record in records if record["id"] != args.id]
        if len(remaining) == len(records):
            raise LauncherError(f"unknown resource id: {args.id}")
        write_registry(registry, remaining, process, stale_rows(remaining))
        print(f"{args.id}: removed")
        return 0


def reviewed(args: argparse.Namespace) -> int:
    """Human spec review: commit the spec if dirty, then set the row base to HEAD and accepted."""
    state = state_dir(args)
    registry, _, _ = paths(state)
    with state_lock(state):
        records, process = registry_state(registry, validate=False)
        record = next((item for item in records if item["id"] == args.id), None)
        if record is None:
            raise LauncherError(f"unknown resource id: {args.id}")
        root, spec = Path(record["root"]), record["spec"]
        if run_git(root, "status", "--porcelain", "--", spec):
            run_git(root, "add", "--", spec)
            run_git(root, "commit", "-q", "-m", f"docs: human spec review of {spec}", "--", spec)
        record["base"] = run_git(root, "rev-parse", "HEAD")
        record["accepted"] = args.accepted
        record["updated_at"] = now()
        write_registry(registry, records, process, stale_rows([item for item in records if item is not record]))
        print(f"{args.id}: base {record['base']}")
        return 0


def stop(args: argparse.Namespace) -> int:
    state = state_dir(args)
    registry, _, _ = paths(state)
    with state_lock(state):
        # Stop reads only [process]; resource rows may point at specs gone from their checkout.
        _, process = registry_state(registry, validate=False)
        if not process:
            raise LauncherError("registry has no running process")
        stop_process(process["pid"], registry)
        print("review host stopped")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("register")
    resource_flags(sub)
    sub.add_argument("--state-dir", required=False)
    visibility = sub.add_mutually_exclusive_group()
    visibility.add_argument("--public", metavar="HOST", help="listen on HOST instead of loopback; the page has no login")
    visibility.add_argument("--private", action="store_true", help="listen on loopback even if a public bind is recorded")
    sub.add_argument("--proof-host", help="public proof host when --public binds a wildcard address")
    sub.add_argument("--test", action="store_true", default=False,
                     help="register as a test slug; records go to a separate test store")
    sub = commands.add_parser("remove")
    sub.add_argument("--id", required=True)
    sub.add_argument("--state-dir", required=False)
    sub = commands.add_parser("reviewed")
    sub.add_argument("--id", required=True)
    sub.add_argument("--accepted", action="store_true", help="the review was Accept spec")
    sub.add_argument("--state-dir", required=False)
    sub = commands.add_parser("stop")
    sub.add_argument("--state-dir", required=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "register":
            return register(args)
        if args.command == "remove":
            return remove(args)
        if args.command == "reviewed":
            return reviewed(args)
        return stop(args)
    except LauncherError as exc:
        print(f"review-host: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
