import http.server
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "skill/review-spec/scripts/review-host.py"
SERVER_PATH = ROOT / "skill/review-spec/assets/review-serve.py"
SPEC_ROOT = ROOT / "docs/specs"

_spec = importlib.util.spec_from_file_location("review_host", LAUNCHER_PATH)
review_host = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(review_host)


def http_request(url, *, method="GET", body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, data=body, method=method)
    try:
        with opener.open(request, timeout=4) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class ForeignHTTP:
    """A test-owned listener used to prove launcher collision safety."""

    def __init__(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while not self.stop.is_set():
            try:
                client, _ = self.socket.accept()
            except (socket.timeout, OSError):
                continue
            with client:
                try:
                    client.recv(4096)
                    client.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n"
                        b"Connection: close\r\n\r\nforeign"
                    )
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        self.socket.close()
        self.thread.join(timeout=1)


class ReviewHostT2Test(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ann62-review-host-")
        self.work = Path(self.temp.name)
        self.states = []
        self.repos = {}
        self.repo_a = self.make_repo("alpha", {"alpha": "alpha-current"})
        self.repo_b = self.make_repo("beta", {"beta": "beta-current"})

    def tearDown(self):
        for state in self.states:
            self.cleanup_state(state)
        self.temp.cleanup()

    def make_repo(self, name, specs):
        root = self.work / name
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "ANN-62 tests"], check=True)
        for spec_name, text in specs.items():
            path = root / "docs/specs" / f"{spec_name}.spec.html"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"<!doctype html><title>{spec_name}</title><p>{text}</p>\n", encoding="utf-8")
            review = Path(str(path) + ".review")
            (review / "human").mkdir(parents=True)
            (review / "agent").mkdir()
        subprocess.run(["git", "-C", str(root), "add", "docs"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed specs"], check=True)
        self.repos[name] = root
        return root

    def base(self, root):
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()

    def resource(self, project, root, spec_name, *, owner="owner", checker="checker", cursor=".cursor-test", slug=None):
        spec = f"docs/specs/{spec_name}.spec.html"
        value = f"{project}={root}:{spec}@{self.base(root)}"
        return {
            "value": value,
            "owner": owner,
            "checker": checker,
            "cursor": cursor,
            "slug": slug or project,
            "root": root,
            "spec_file": root / spec,
            "id": f"spec:{project}::{spec}",
        }

    def port(self):
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            value = sock.getsockname()[1]
            sock.close()
            if value > 20000 and value not in {45581, 45583}:
                return value

    def run_cli(self, *args, state=None, ports=None, extra_env=None):
        if state is None:
            state = self.work / "state"
        state = Path(state)
        if state not in self.states:
            self.states.append(state)
        env = os.environ.copy()
        env.pop("REVIEW_APPROVED_INGRESS_PORTS", None)
        env["SPEC_CHAT_APPROVED_INGRESS_PORTS"] = str(ports or self.port())
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(LAUNCHER_PATH), *args], cwd=ROOT, env=env,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
        )

    def start_args(self, resource, state=None, *extra):
        state = Path(state or (self.work / "state"))
        args = [
            "start", "--state-dir", str(state), "--bind", "127.0.0.1",
            "--proof-host", "127.0.0.1", "--resource", resource["value"],
            "--owner", resource["owner"], "--checker", resource["checker"],
            "--cursor-name", resource["cursor"], "--slug", resource["slug"],
        ]
        return args + list(extra)

    def add_args(self, state, resource, *extra):
        args = [
            "add", "--state-dir", str(state), "--resource", resource["value"],
            "--owner", resource["owner"], "--checker", resource["checker"],
            "--cursor-name", resource["cursor"], "--slug", resource["slug"],
        ]
        return args + list(extra)

    def receipt(self, state):
        with (Path(state) / "receipt.toml").open("rb") as stream:
            return tomllib.load(stream)

    def registry(self, state):
        with (Path(state) / "registry.toml").open("rb") as stream:
            return tomllib.load(stream)

    def cleanup_state(self, state):
        receipt_path = Path(state) / "receipt.toml"
        if not receipt_path.exists():
            return
        try:
            receipt = self.receipt(state)
            registry = Path(state) / "registry.toml"
            pid = receipt.get("pid")
            if receipt.get("state") == "running" and review_host.process_owned(pid, registry):
                review_host.kill_owned(pid, registry)
        except (OSError, ValueError, review_host.LauncherError):
            pass

    def clear_review(self, resource):
        review = Path(str(resource["spec_file"]) + ".review")
        shutil.rmtree(review, ignore_errors=True)
        (review / "human").mkdir(parents=True)
        (review / "agent").mkdir()
        return review

    def event(self, review, actor, name, body):
        path = review / actor / name
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def finish(self, state, resource):
        return self.run_cli("finish", "--state-dir", str(state), "--id", resource["id"])

    def lifecycle(self, command, state, resource):
        return self.run_cli(command, "--state-dir", str(state), "--id", resource["id"])

    def stop_cleanly(self, state, resources):
        for resource in resources:
            if self.registry(state)["resource"]:
                current = next(item for item in self.registry(state)["resource"] if item["id"] == resource["id"])
                if current["lifecycle"] != "removed":
                    result = self.lifecycle("remove", state, resource)
                    self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_cli("stop", "--state-dir", str(state))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ports_invalid_config_occupied_candidate_and_none_free(self):
        resource = self.resource("alpha", self.repo_a, "alpha")
        invalid_state = self.work / "invalid-state"
        invalid = self.run_cli(*self.start_args(resource, invalid_state), state=invalid_state, ports="not-a-port")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("invalid approved ingress port", invalid.stderr)
        self.assertNotIn("http://", invalid.stdout)
        self.assertEqual(self.receipt(invalid_state)["state"], "failed")
        self.assertFalse((invalid_state / "registry.toml").exists())

        occupied = ForeignHTTP()
        free = self.port()
        try:
            state = self.work / "occupied-state"
            result = self.run_cli(*self.start_args(resource, state), state=state, ports=f"{occupied.port},{free}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.receipt(state)["port"], free)
            self.assertEqual(http_request(f"http://127.0.0.1:{occupied.port}/")[0], 200)
            self.stop_cleanly(state, [resource])

            first = ForeignHTTP()
            second = ForeignHTTP()
            none_state = self.work / "none-state"
            none = self.run_cli(*self.start_args(resource, none_state), state=none_state, ports=f"{first.port},{second.port}")
            self.assertNotEqual(none.returncode, 0)
            self.assertIn("no approved ingress port is free", none.stderr)
            self.assertNotIn("http://", none.stdout)
            self.assertEqual(self.receipt(none_state)["state"], "failed")
            self.assertEqual(http_request(f"http://127.0.0.1:{first.port}/")[0], 200)
            self.assertEqual(http_request(f"http://127.0.0.1:{second.port}/")[0], 200)
            first.close()
            second.close()
        finally:
            occupied.close()

    def test_proof_rejects_wrong_bytes_and_substituted_baseline(self):
        resource = review_host.parse_resource_spec(
            self.resource("alpha", self.repo_a, "alpha")["value"], "owner", "checker", ".cursor-test", "alpha", None
        )
        expected = Path(resource["spec_file"]).read_bytes()
        with mock.patch.object(review_host, "direct_request", return_value=(200, expected + b"tampered")):
            with self.assertRaises(review_host.ProofError):
                review_host.prove_resource("http://127.0.0.1:9999", resource)
        baseline = {
            "base": resource["resolved_base_commit"],
            "htmlBase": resource["resolved_base_commit"],
            "html": "substituted baseline",
        }
        with mock.patch.object(
            review_host, "direct_request", side_effect=[(200, expected), (200, json.dumps(baseline).encode())]
        ):
            with self.assertRaises(review_host.ProofError):
                review_host.prove_resource("http://127.0.0.1:9999", resource)

    def test_receipt_owner_checker_url_and_add_preserves_process(self):
        first = self.resource("alpha", self.repo_a, "alpha", owner="owner-a", checker="checker-a", cursor=".cursor-a")
        second = self.resource("beta", self.repo_b, "beta", owner="owner-b", checker="checker-b", cursor=".cursor-b")
        state = self.work / "receipt-state"
        result = self.run_cli(*self.start_args(first, state), state=state)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.receipt(state)
        before_resource = before["resource"][0]
        for key in ("service_kind", "state", "pid", "port", "bind", "public_url", "source_revision"):
            self.assertIn(key, before)
        for key in ("id", "stable_path", "root", "spec", "base", "resolved_base_commit", "spec_sha256", "owner", "checker", "lifecycle", "cursor_name", "cursor_snapshot", "finish_event", "proof"):
            self.assertIn(key, before_resource)
        self.assertEqual(before_resource["owner"], "owner-a")
        self.assertEqual(before_resource["checker"], "checker-a")
        pid, port, url = before["pid"], before["port"], before["public_url"]

        added = self.run_cli(*self.add_args(state, second), state=state, ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        after = self.receipt(state)
        self.assertEqual((after["pid"], after["port"], after["public_url"]), (pid, port, url))
        added_resource = next(item for item in after["resource"] if item["id"] == second["id"])
        self.assertEqual((added_resource["owner"], added_resource["checker"]), ("owner-b", "checker-b"))
        self.assertEqual(http_request(url + "/beta/docs/specs/beta.spec.html")[0], 200)

        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
        for name in tracked:
            if name:
                self.assertNotIn(url.encode(), (ROOT / os.fsdecode(name)).read_bytes())

        missing_owner = self.run_cli(
            "start", "--state-dir", str(self.work / "missing-owner"), "--bind", "127.0.0.1",
            "--proof-host", "127.0.0.1", "--resource", first["value"], "--checker", "checker",
            "--cursor-name", ".cursor", state=self.work / "missing-owner"
        )
        self.assertNotEqual(missing_owner.returncode, 0)
        self.assertIn("--owner", missing_owner.stderr)

    def test_duplicate_and_ambiguous_start_add_are_rejected(self):
        first = self.resource("alpha", self.repo_a, "alpha", slug="shared")
        second = self.resource("beta", self.repo_b, "beta", slug="shared")
        state = self.work / "duplicate-state"
        duplicate_start = self.run_cli(
            "start", "--state-dir", str(state), "--bind", "127.0.0.1", "--proof-host", "127.0.0.1",
            "--resource", first["value"], "--resource", first["value"], "--owner", "owner", "--checker", "checker",
            "--cursor-name", ".cursor", "--slug", "shared", state=state
        )
        self.assertNotEqual(duplicate_start.returncode, 0)
        self.assertIn("duplicate resource identity", duplicate_start.stderr)
        started = self.run_cli(*self.start_args(first, state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        before = (self.registry(state)["resource"], self.receipt(state)["resource"])
        duplicate_add = self.run_cli(*self.add_args(state, first), state=state)
        self.assertNotEqual(duplicate_add.returncode, 0)
        self.assertIn("duplicate resource identity", duplicate_add.stderr)
        self.assertEqual(self.registry(state)["resource"], before[0])
        ambiguous = self.run_cli(*self.add_args(state, second), state=state)
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertIn("ambiguous resource slug", ambiguous.stderr)
        self.assertEqual(self.registry(state)["resource"], before[0])

    def test_lifecycle_finish_predicates_siblings_and_stop(self):
        first = self.resource("alpha", self.repo_a, "alpha", owner="owner-a", checker="checker-a", cursor=".cursor-a", slug="alpha")
        second = self.resource("beta", self.repo_b, "beta", owner="owner-b", checker="checker-b", cursor=".cursor-b", slug="beta")
        state = self.work / "lifecycle-state"
        result = self.run_cli(
            "start", "--state-dir", str(state), "--bind", "127.0.0.1", "--proof-host", "127.0.0.1",
            "--resource", first["value"], "--resource", second["value"],
            "--owner", "owner-a", "--owner", "owner-b", "--checker", "checker-a", "--checker", "checker-b",
            "--cursor-name", ".cursor-a", "--cursor-name", ".cursor-b", "--slug", "alpha", "--slug", "beta",
            state=state,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.receipt(state)
        pid, port, url = before["pid"], before["port"], before["public_url"]
        parked = self.lifecycle("park", state, first)
        self.assertEqual(parked.returncode, 0, parked.stderr)
        parked_receipt = self.receipt(state)
        parked_item = next(item for item in parked_receipt["resource"] if item["id"] == first["id"])
        self.assertEqual((parked_receipt["pid"], parked_receipt["port"], parked_receipt["public_url"]), (pid, port, url))
        self.assertEqual((parked_item["owner"], parked_item["checker"], parked_item["cursor_name"]), ("owner-a", "checker-a", ".cursor-a"))
        events_url = url + "/api/events?" + urllib.parse.urlencode({"dir": "alpha/docs/specs/alpha.spec.html.review", "actor": "human"})
        event_body = json.dumps({"event": "comment", "id": "parked"}).encode()
        status, _ = http_request(events_url, method="POST", body=event_body)
        self.assertEqual(status, 200)
        self.assertTrue(list((Path(str(first["spec_file"]) + ".review") / "human").glob("*.json")))
        resumed = self.lifecycle("resume", state, first)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        resumed_receipt = self.receipt(state)
        self.assertEqual((resumed_receipt["pid"], resumed_receipt["port"], resumed_receipt["public_url"]), (pid, port, url))

        review = self.clear_review(first)
        self.assertNotEqual(self.finish(state, first).returncode, 0)  # no hand-off

        def make_batch(kind):
            self.clear_review(first)
            self.event(review, "human", "010-comment.json", {"event": "comment", "id": "900", "body": "x"})
            self.event(review, "human", "020-handoff-old.json", {"event": "handoff", "id": "100"})
            if kind == "comment":
                self.event(review, "human", "030-comment.json", {"event": "comment", "id": "800"})
            elif kind == "reply":
                self.event(review, "human", "030-reply.json", {"event": "reply", "id": "700", "respondsTo": "900"})
            elif kind == "edit":
                self.event(review, "human", "030-edit.json", {"event": "edit", "id": "600", "supersedes": "900"})
            self.event(review, "human", "040-handoff-final.json", {"event": "handoff", "id": "001"})
            (review / ".cursor-a").write_text("020-handoff-old.json\n040-handoff-final.json\n", encoding="utf-8")
            self.assertNotEqual(self.finish(state, first).returncode, 0, kind)

        for kind in ("comment", "reply", "edit"):
            make_batch(kind)

        self.clear_review(first)
        self.event(review, "human", "010-comment.json", {"event": "comment", "id": "900"})
        self.event(review, "human", "020-handoff-old.json", {"event": "handoff", "id": "100"})
        self.event(review, "human", "030-handoff-final.json", {"event": "handoff", "id": "001"})
        (review / ".cursor-a").write_text("020-handoff-old.json\n030-handoff-final.json\n", encoding="utf-8")
        self.assertNotEqual(self.finish(state, first).returncode, 0)  # unresolved folded thread

        original_spec = first["spec_file"].read_text(encoding="utf-8")
        first["spec_file"].write_text(original_spec.replace("</title>", ' data-spec-tbd="true"></title>'), encoding="utf-8")
        try:
            self.assertNotEqual(self.finish(state, first).returncode, 0)
        finally:
            first["spec_file"].write_text(original_spec, encoding="utf-8")

        self.clear_review(first)
        self.event(review, "human", "010-handoff-old.json", {"event": "handoff", "id": "100"})
        self.event(review, "human", "020-comment-after.json", {"event": "comment", "id": "900"})
        (review / ".cursor-a").write_text("010-handoff-old.json\n", encoding="utf-8")
        self.assertNotEqual(self.finish(state, first).returncode, 0)  # human event after hand-off

        self.clear_review(first)
        self.event(review, "human", "010-handoff-final.json", {"event": "handoff", "id": "001"})
        self.assertNotEqual(self.finish(state, first).returncode, 0)  # hand-off absent from cursor

        self.clear_review(first)
        self.event(review, "human", "010-comment.json", {"event": "comment", "id": "900"})
        self.event(review, "human", "020-handoff-old.json", {"event": "handoff", "id": "100"})
        self.event(review, "agent", "030-reply.json", {"event": "reply", "id": "700", "respondsTo": "900"})
        self.event(review, "human", "040-status-resolved.json", {"event": "status", "id": "050", "respondsTo": "900", "status": "resolved"})
        self.event(review, "human", "050-handoff-final.json", {"event": "handoff", "id": "001"})
        (review / ".cursor-a").write_text("020-handoff-old.json\n050-handoff-final.json\n", encoding="utf-8")
        self.assertEqual(self.finish(state, first).returncode, 0)
        after_finish = self.receipt(state)
        self.assertEqual(next(item for item in after_finish["resource"] if item["id"] == first["id"])["lifecycle"], "finished")
        sibling_bytes = http_request(url + "/beta/docs/specs/beta.spec.html")[1]
        before_post = list((Path(str(first["spec_file"]) + ".review") / "human").glob("*.json"))
        status, _ = http_request(events_url, method="POST", body=event_body)
        self.assertEqual(status, 409)
        self.assertEqual(before_post, list((Path(str(first["spec_file"]) + ".review") / "human").glob("*.json")))
        self.assertEqual(sibling_bytes, http_request(url + "/beta/docs/specs/beta.spec.html")[1])

        removed = self.lifecycle("remove", state, first)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(http_request(url + "/alpha/docs/specs/alpha.spec.html")[0], 404)
        self.assertEqual(http_request(events_url)[0], 404)
        self.assertTrue((review / "human" / "050-handoff-final.json").exists())
        readd = self.run_cli(*self.add_args(state, first), state=state, ports=port)
        self.assertNotEqual(readd.returncode, 0)
        self.assertIn("duplicate resource identity", readd.stderr)
        parked_sibling = self.lifecycle("park", state, second)
        self.assertEqual(parked_sibling.returncode, 0, parked_sibling.stderr)
        refused_stop = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertNotEqual(refused_stop.returncode, 0)
        self.assertIn("serving or parked", refused_stop.stderr)
        self.assertEqual(self.lifecycle("remove", state, second).returncode, 0)
        stopped = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse(review_host.process_owned(pid, Path(state) / "registry.toml"))

    def test_independent_startup_keeps_foreign_listener_on_launcher_failure(self):
        foreign = ForeignHTTP()
        try:
            resource = self.resource("alpha", self.repo_a, "alpha")
            state = self.work / "independent-failure"
            result = self.run_cli(*self.start_args(resource, state), state=state, ports=foreign.port)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no approved ingress port is free", result.stderr)
            self.assertEqual(http_request(f"http://127.0.0.1:{foreign.port}/")[1], b"foreign")
            source = LAUNCHER_PATH.read_text(encoding="utf-8")
            self.assertNotIn("annotateanything", source.lower())
            self.assertNotIn("evidence launcher", source.lower())
        finally:
            foreign.close()

    def test_shared_helpers_and_box_only_contract(self):
        source = (LAUNCHER_PATH.read_text(encoding="utf-8") + "\n" + SERVER_PATH.read_text(encoding="utf-8")).lower()
        for token in ("annotateanything", "resource_type", "npm install", "pip install", "bb", "plugin install", "laptop component"):
            self.assertNotIn(token, source)
        self.assertNotIn("subprocess.run((\"npm", source)
        self.assertNotIn("subprocess.run((\"pip", source)
        self.assertIn("service_kind", LAUNCHER_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
