import importlib.util
import json
import os
import socket
import subprocess
import tempfile
import time
import tomllib
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "skill/review-spec/scripts/review-host.py"
SERVER_PATH = ROOT / "skill/review-spec/assets/review-serve.py"

_spec = importlib.util.spec_from_file_location("review_host", LAUNCHER_PATH)
review_host = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(review_host)


_wake_spec = importlib.util.spec_from_file_location("review_host_wake", ROOT / "tests/review-host-wake.py")
review_host_wake = importlib.util.module_from_spec(_wake_spec)
assert _wake_spec.loader is not None
_wake_spec.loader.exec_module(review_host_wake)
FakeHerdr = review_host_wake.FakeHerdr
path_without_herdr = review_host_wake.path_without_herdr


def request(url, *, method="GET", body=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, method=method), timeout=4) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class ReviewHostTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spec-chat-review-host-")
        self.work = Path(self.temp.name)
        self.repo = self.work / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Spec Chat tests"], check=True)
        self.spec = self.repo / "docs/specs/review.spec.html"
        self.second_spec = self.repo / "docs/specs/second.spec.html"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text("<!doctype html><title>review</title><p>current</p>\n", encoding="utf-8")
        self.second_spec.write_text("<!doctype html><title>second</title><p>current</p>\n", encoding="utf-8")
        self.review = Path(str(self.spec) + ".review")
        (self.review / "human").mkdir(parents=True)
        (self.review / "agent").mkdir()
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "seed"], check=True)
        self.base = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()
        self.states = []

    def tearDown(self):
        for state in self.states:
            registry = Path(state) / "registry.toml"
            try:
                document = tomllib.loads(registry.read_text(encoding="utf-8"))
                process = document.get("process", {})
                pid = process.get("pid")
                if review_host.process_owns_registry(pid, registry):
                    review_host.stop_process(pid, registry)
            except (FileNotFoundError, OSError, ValueError, review_host.LauncherError):
                pass
        self.temp.cleanup()

    def port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def resource(self, project="review", spec="review"):
        return f"{project}={self.repo}:docs/specs/{spec}.spec.html@{self.base}"

    def run_cli(self, *args, state=None, ports=None, herdr=None):
        state = Path(state or self.work / "state")
        if state not in self.states:
            self.states.append(state)
        env = os.environ.copy()
        env.update(herdr.env() if herdr else {"PATH": path_without_herdr()})
        env["SPEC_CHAT_APPROVED_INGRESS_PORTS"] = str(ports or self.port())
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            ["python3", str(LAUNCHER_PATH), *args],
            cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
        )

    def register_args(self, state, project="review", slug="ann45", spec="review", **extra):
        args = [
            "register", "--state-dir", str(state), "--bind", "127.0.0.1",
            "--proof-host", "127.0.0.1", "--test-loopback", "--resource", self.resource(project, spec),
            "--owner", "owner", "--checker", "checker", "--cursor-name", ".cursor-test",
            "--slug", slug,
        ]
        for key, value in extra.items():
            args.extend([f"--{key.replace('_', '-')}", value])
        return args

    def registry(self, state):
        with (Path(state) / "registry.toml").open("rb") as stream:
            return tomllib.load(stream)

    def test_register_remove_stop_and_registry_shape(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        document = self.registry(state)
        self.assertEqual(set(document["process"]), {"pid", "port"})
        self.assertEqual(len(document["resource"]), 1)
        resource = document["resource"][0]
        self.assertEqual(resource["slug"], "ann45")
        for field in ("lifecycle", "cursor_snapshot", "finish_event"):
            self.assertNotIn(field, resource)
        url = next(line.split("review URL: ", 1)[1] for line in started.stdout.splitlines() if line.startswith("review URL: "))
        status, body = request(url + "/ann45/docs/specs/review.spec.html")
        self.assertEqual((status, body), (200, self.spec.read_bytes()))

        rid = resource["id"]
        removed = self.run_cli("remove", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(self.registry(state).get("resource", []), [])
        self.assertEqual(request(url + "/ann45/docs/specs/review.spec.html")[0], 404)

        stopped = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse(review_host.process_owns_registry(document["process"]["pid"], Path(state) / "registry.toml"))

    def wake_lines(self, result):
        return [line.split(" URL: ", 1)[1].split(" ", 1)[1] for line in result.stdout.splitlines()
                if " URL: " in line and not line.startswith("review URL: ")]

    def test_register_prints_wake_verified_only_when_herdr_resolves_owner(self):
        herdr = FakeHerdr(self.work)
        herdr.agent("owner")
        state = self.work / "state"
        verified = self.run_cli(*self.register_args(state), state=state, herdr=herdr)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(self.wake_lines(verified), ["wake=verified owner=owner"])
        herdr.agent("owner", None)
        unresolved = self.run_cli(*self.register_args(state, spec="second"), state=state, herdr=herdr)
        self.assertEqual(unresolved.returncode, 0, unresolved.stderr)
        self.assertEqual(self.wake_lines(unresolved), ["wake=unavailable owner=owner"])
        self.assertEqual(herdr.says(), [], "registration never prompts the owner")

    def test_register_without_herdr_prints_wake_unavailable(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(self.wake_lines(started), ["wake=unavailable owner=owner"])

    def test_register_adds_to_existing_server(self):
        state = self.work / "state"
        first = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        process = self.registry(state)["process"]
        second = self.run_cli(
            *self.register_args(state, project="second", slug="ann45", spec="second"), state=state, ports=process["port"]
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(self.registry(state)["resource"]), 2)

    def test_different_slugs_can_register_and_serve_the_same_spec(self):
        state = self.work / "state"
        first = self.run_cli(*self.register_args(state, slug="lane-one"), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli(*self.register_args(state, slug="lane-two"), state=state)
        self.assertEqual(second.returncode, 0, second.stderr)

        resources = self.registry(state)["resource"]
        self.assertEqual({resource["id"] for resource in resources}, {
            "spec:lane-one::docs/specs/review.spec.html",
            "spec:lane-two::docs/specs/review.spec.html",
        })
        for slug in ("lane-one", "lane-two"):
            url = next(line.split("review URL: ", 1)[1] for line in second.stdout.splitlines() if line.startswith("review URL: "))
            self.assertEqual(request(url + f"/{slug}/docs/specs/review.spec.html"), (200, self.spec.read_bytes()))

    def test_register_proves_committed_and_uncommitted_specs_absent_at_base(self):
        state = self.work / "state"
        committed = self.repo / "docs/specs/committed.spec.html"
        committed.write_text("<!doctype html><title>committed</title><p>new</p>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", str(committed)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "add committed spec"], check=True)

        started = self.run_cli(*self.register_args(state, project="committed", slug="committed", spec="committed"), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)

        working = self.repo / "docs/specs/working.spec.html"
        working.write_text("<!doctype html><title>working</title><p>new</p>\n", encoding="utf-8")
        restarted = self.run_cli(*self.register_args(state, project="working", slug="working", spec="working"), state=state)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        url = next(line.split("review URL: ", 1)[1] for line in restarted.stdout.splitlines() if line.startswith("review URL: "))
        for slug, spec in (("committed", "committed"), ("working", "working")):
            query = urllib.parse.urlencode({"path": f"/{slug}/docs/specs/{spec}.spec.html", "base": self.base})
            status, body = request(url + "/api/baseline?" + query)
            self.assertEqual(status, 200)
            baseline = json.loads(body)
            self.assertIsNone(baseline["htmlBase"])
            self.assertIsNone(baseline["html"])

    def test_stop_then_register_replaces_resource_and_restarts_server(self):
        state = self.work / "state"
        first = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        stopped = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)

        restarted = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        self.assertEqual(len(self.registry(state)["resource"]), 1)
        url = next(line.split("review URL: ", 1)[1] for line in restarted.stdout.splitlines() if line.startswith("review URL: "))
        self.assertEqual(request(url + "/ann45/docs/specs/review.spec.html"), (200, self.spec.read_bytes()))

    def test_legacy_registry_loads_without_lifecycle_state(self):
        legacy = self.work / "legacy.toml"
        legacy.write_text(
            f"""[[resource]]
id = "spec:review::docs/specs/review.spec.html"
slug = "legacy"
root = "{self.repo}"
narrow_root = "{self.repo / 'docs'}"
spec = "docs/specs/review.spec.html"
base = "{self.base}"
owner = "owner"
checker = "checker"
lifecycle = "finished"
cursor_name = ".cursor-test"
cursor_snapshot = {{ lines = 0, last = "", sha256 = "" }}
finish_event = ""
""",
            encoding="utf-8",
        )
        process = subprocess.Popen(
            ["python3", str(SERVER_PATH), "--registry", str(legacy), "--bind", "127.0.0.1", "--port", "0", "--host", "127.0.0.1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        def cleanup_server():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=2)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        self.addCleanup(cleanup_server)
        line = ""
        for _ in range(100):
            if process.stdout:
                line = process.stdout.readline()
            if "http://" in line:
                break
            time.sleep(0.02)
        self.assertIn("http://", line)
        port = int(line.split("http://127.0.0.1:", 1)[1].split()[0])
        self.assertEqual(request(f"http://127.0.0.1:{port}/legacy/docs/specs/review.spec.html")[0], 200)

    def test_invalid_port_does_not_start_server(self):
        state = self.work / "invalid"
        result = self.run_cli(*self.register_args(state), state=state, ports="not-a-port")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid approved ingress port", result.stderr)
        self.assertFalse((state / "registry.toml").exists())

    def test_process_stop_requires_registry_path_in_cmdline(self):
        registry = self.work / "registry.toml"
        with mock.patch.object(review_host, "process_cmdline", return_value=["python", "other.py"]):
            self.assertFalse(review_host.process_owns_registry(os.getpid(), registry))

    def test_proof_rejects_wrong_bytes(self):
        resource = review_host.parse_resource_spec(
            self.resource(), "owner", "checker", ".cursor-test", "ann45", None
        )
        with mock.patch.object(review_host._verify_module, "verify", side_effect=ValueError("served spec bytes differ")):
            with self.assertRaises(review_host.ProofError):
                review_host.prove_resource("http://127.0.0.1:9999", resource)

    def test_only_register_remove_stop_commands_exist(self):
        commands = set(review_host.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual(commands, {"register", "remove", "stop"})


if __name__ == "__main__":
    unittest.main()
