import email.utils
import importlib.util
import json
import os
import shutil
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
            "register", "--state-dir", str(state), "--resource", self.resource(project, spec),
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
        self.assertEqual(document["process"]["bind"], "127.0.0.1")
        self.assertEqual(set(document["process"]), {"pid", "port", "bind"})
        self.assertIn(f"ssh -L {document['process']['port']}:127.0.0.1:", started.stdout)
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

    def test_register_records_a_ref_base_as_its_resolved_commit(self):
        """ANN-192 compared-range #reviewed-default: a branch base never moves the row."""
        state = self.work / "state"
        args = self.register_args(state)
        args[args.index("--resource") + 1] = f"review={self.repo}:docs/specs/review.spec.html@main"
        started = self.run_cli(*args, state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(self.registry(state)["resource"][0]["base"], self.base)

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
            "spec:lane-one:review::docs/specs/review.spec.html",
            "spec:lane-two:review::docs/specs/review.spec.html",
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

    def test_stop_succeeds_when_a_registered_spec_is_gone(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        pid = self.registry(state)["process"]["pid"]
        self.spec.unlink()
        stopped = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertFalse(review_host.process_owns_registry(pid, Path(state) / "registry.toml"))

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

    def test_head_on_a_served_spec_returns_last_modified_at_file_mtime(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        head = urllib.request.Request(self.url(started) + "/ann45/docs/specs/review.spec.html", method="HEAD")
        with urllib.request.urlopen(head, timeout=4) as response:
            modified = response.headers.get("Last-Modified")
        self.assertIsNotNone(modified)
        self.assertEqual(email.utils.parsedate_to_datetime(modified).timestamp(), int(self.spec.stat().st_mtime))

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

    def test_private_default_needs_no_approved_ports(self):
        state = self.work / "state"
        self.states.append(state)
        env = {**os.environ, "PATH": path_without_herdr(), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("SPEC_CHAT_APPROVED_INGRESS_PORTS", None)
        env.pop("REVIEW_APPROVED_INGRESS_PORTS", None)
        started = subprocess.run(["python3", str(LAUNCHER_PATH), *self.register_args(state)], cwd=ROOT, env=env,
                                 text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
        self.assertEqual(started.returncode, 0, started.stderr)
        process = self.registry(state)["process"]
        self.assertGreater(process["port"], 0)
        self.assertTrue(self.url(started).endswith(f":{process['port']}"))

    def test_public_loopback_is_rejected(self):
        state = self.work / "state"
        result = self.run_cli(*self.register_args(state), "--public", "127.0.0.1", state=state)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be loopback", result.stderr)
        self.assertFalse((state / "registry.toml").exists())

    def test_bind_choice_is_explicit_then_recorded(self):
        def args(public=None, private=False):
            return review_host.build_parser().parse_args(
                ["register", "--resource", "x", "--owner", "o", "--checker", "c", "--cursor-name", ".c",
                 *(["--public", public] if public else []), *(["--private"] if private else [])])
        self.assertEqual(review_host.select_bind(args(), None), "127.0.0.1")
        self.assertEqual(review_host.select_bind(args(), {"pid": 1, "port": 2}), "127.0.0.1")
        self.assertEqual(review_host.select_bind(args(public="0.0.0.0"), None), "0.0.0.0")
        recorded = {"pid": 1, "port": 2, "bind": "0.0.0.0"}
        self.assertEqual(review_host.select_bind(args(), recorded), "0.0.0.0")
        self.assertEqual(review_host.select_bind(args(private=True), recorded), "127.0.0.1")

    def test_public_bind_warns_no_login(self):
        with mock.patch("sys.stderr") as err, mock.patch("sys.stdout"):
            review_host.print_access("http://203.0.113.9:45583", "0.0.0.0")
        written = "".join(call.args[0] for call in err.write.call_args_list)
        self.assertIn("has no login", written)

    def test_live_host_keeps_its_bind_and_refuses_a_different_one(self):
        state = self.work / "state"
        first = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        registry = state / "registry.toml"
        process = self.registry(state)["process"]
        registry.write_text(registry.read_text(encoding="utf-8").replace('bind = "127.0.0.1"\n', ""), encoding="utf-8")
        self.assertNotIn("bind", self.registry(state)["process"])
        before = registry.read_bytes()
        public = self.run_cli(*self.register_args(state, spec="second"), "--public", "0.0.0.0", state=state,
                              ports=process["port"])
        self.assertNotEqual(public.returncode, 0)
        self.assertIn("already running on 127.0.0.1", public.stderr)
        self.assertIn("run stop", public.stderr)
        self.assertEqual(registry.read_bytes(), before)
        private = self.run_cli(*self.register_args(state, spec="second"), "--private", state=state,
                               ports=process["port"])
        self.assertEqual(private.returncode, 0, private.stderr)
        self.assertEqual(self.registry(state)["process"], {"pid": process["pid"], "port": process["port"]})
        self.assertIn("ssh -L", private.stdout)

    def test_running_url_never_guesses_without_log(self):
        with self.assertRaises(review_host.LauncherError):
            review_host.running_url(self.work / "missing.log")

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

    def url(self, result):
        return next(line.split("review URL: ", 1)[1] for line in result.stdout.splitlines() if line.startswith("review URL: "))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def test_reregistering_a_row_id_replaces_only_that_row(self):
        state = self.work / "state"
        self.assertEqual(self.run_cli(*self.register_args(state), state=state).returncode, 0)
        self.assertEqual(self.run_cli(*self.register_args(state, spec="second"), state=state).returncode, 0)
        self.spec.write_text("<!doctype html><title>review</title><p>next</p>\n", encoding="utf-8")
        self.git("commit", "-qam", "next")
        args = self.register_args(state)
        args[args.index("--resource") + 1] = f"review={self.repo}:docs/specs/review.spec.html@HEAD"
        again = self.run_cli(*args, state=state)
        self.assertEqual(again.returncode, 0, again.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(set(rows), {"spec:ann45:review::docs/specs/review.spec.html",
                                     "spec:ann45:review::docs/specs/second.spec.html"})
        self.assertEqual(rows["spec:ann45:review::docs/specs/review.spec.html"]["base"], self.git("rev-parse", "HEAD"))
        self.assertEqual(rows["spec:ann45:review::docs/specs/second.spec.html"]["base"], self.base)

    def test_second_project_under_a_slug_serves_at_slug_project_spec(self):
        state = self.work / "state"
        other = self.work / "other"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(other)], check=True)
        first = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        args = self.register_args(state, project="sc")
        args[args.index("--resource") + 1] = f"sc={other}:docs/specs/second.spec.html@{self.base}"
        second = self.run_cli(*args, state=state)
        self.assertEqual(second.returncode, 0, second.stderr)
        rows = {row["project"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(rows["review"]["path"], "ann45/docs/specs/review.spec.html")
        self.assertEqual(rows["sc"]["path"], "ann45/sc/docs/specs/second.spec.html")
        url = self.url(second)
        self.assertEqual(request(url + "/ann45/docs/specs/review.spec.html"), (200, self.spec.read_bytes()))
        self.assertEqual(request(url + "/ann45/sc/docs/specs/second.spec.html"), (200, self.second_spec.read_bytes()))

    def legacy_rows(self, state, root, specs, project=None):
        state.mkdir(parents=True, exist_ok=True)
        extra = "" if project is None else f'project = "{project}"\n'
        (state / "registry.toml").write_text("".join(
            f"""[[resource]]
id = "spec:ann45::docs/specs/{spec}.spec.html"
slug = "ann45"
{extra}root = "{root}"
narrow_root = "{root / 'docs'}"
spec = "docs/specs/{spec}.spec.html"
base = "{self.base}"
owner = "owner"
checker = "checker"
cursor_name = ".cursor-test"
""" for spec in specs), encoding="utf-8")

    def test_register_adopts_a_legacy_row_at_the_same_slug_root_and_spec(self):
        state = self.work / "state"
        self.legacy_rows(state, self.repo, ("review", "second"))
        result = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(set(rows), {"spec:ann45::docs/specs/review.spec.html",
                                     "spec:ann45::docs/specs/second.spec.html"})
        adopted = rows["spec:ann45::docs/specs/review.spec.html"]
        self.assertEqual((adopted["project"], adopted["path"]), ("review", "ann45/docs/specs/review.spec.html"))
        self.assertNotIn("project", rows["spec:ann45::docs/specs/second.spec.html"])
        self.assertEqual(request(self.url(result) + "/ann45/docs/specs/review.spec.html"), (200, self.spec.read_bytes()))

    def test_register_after_adoption_is_idempotent(self):
        state = self.work / "state"
        self.legacy_rows(state, self.repo, ("review", "second"))
        for _ in range(2):
            result = self.run_cli(*self.register_args(state), state=state)
            self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(set(rows), {"spec:ann45::docs/specs/review.spec.html",
                                     "spec:ann45::docs/specs/second.spec.html"})
        adopted = rows["spec:ann45::docs/specs/review.spec.html"]
        self.assertEqual((adopted["project"], adopted["path"]), ("review", "ann45/docs/specs/review.spec.html"))

    def test_adopted_row_reregistered_from_a_new_root_keeps_id_and_path(self):
        state = self.work / "state"
        self.legacy_rows(state, self.repo, ("review",))
        first = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(first.returncode, 0, first.stderr)
        rid = "spec:ann45::docs/specs/review.spec.html"
        for name in ("clone-a", "clone-b"):
            other = self.work / name
            subprocess.run(["git", "clone", "-q", str(self.repo), str(other)], check=True)
            args = self.register_args(state)
            args[args.index("--resource") + 1] = f"review={other}:docs/specs/review.spec.html@{self.base}"
            again = self.run_cli(*args, state=state)
            self.assertEqual(again.returncode, 0, again.stderr)
            rows = self.registry(state)["resource"]
            self.assertEqual([row["id"] for row in rows], [rid])
            self.assertEqual((rows[0]["project"], rows[0]["path"], rows[0]["root"]),
                             ("review", "ann45/docs/specs/review.spec.html", str(other.resolve())))
            if name == "clone-a":
                shutil.rmtree(other)

    def test_empty_string_project_row_is_treated_as_legacy(self):
        state = self.work / "state"
        self.legacy_rows(state, self.repo, ("review",), project="")
        result = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = self.registry(state)["resource"]
        self.assertEqual([row["id"] for row in rows], ["spec:ann45::docs/specs/review.spec.html"])
        self.assertEqual((rows[0]["project"], rows[0]["path"]), ("review", "ann45/docs/specs/review.spec.html"))

    def test_empty_string_project_row_at_a_different_root_is_untouched(self):
        state = self.work / "state"
        other = self.work / "other"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(other)], check=True)
        self.legacy_rows(state, other, ("review",), project="")
        result = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(result.returncode, 0, result.stderr)
        ids = {row["id"] for row in self.registry(state)["resource"]}
        self.assertEqual(ids, {"spec:ann45::docs/specs/review.spec.html",
                               "spec:ann45:review::docs/specs/review.spec.html"})

    def test_register_leaves_a_legacy_row_at_a_different_root_untouched(self):
        state = self.work / "state"
        other = self.work / "other"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(other)], check=True)
        self.legacy_rows(state, other, ("review",))
        legacy = self.registry(state)["resource"][0]
        result = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(rows[legacy["id"]], legacy)
        added = rows["spec:ann45:review::docs/specs/review.spec.html"]
        self.assertEqual((added["root"], added["path"]),
                         (str(self.repo.resolve()), "ann45/review/docs/specs/review.spec.html"))

    def test_reviewed_commits_a_dirty_spec_and_page_defaults_to_the_row_base(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        self.spec.write_text("<!doctype html><title>review</title><p>edited</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, self.base)
        self.assertEqual(self.git("status", "--porcelain", "--", str(self.spec)), "")
        self.assertEqual(self.registry(state)["resource"][0]["base"], head)
        query = urllib.parse.urlencode({"path": "/ann45/docs/specs/review.spec.html"})
        deadline = time.monotonic() + 3
        while True:
            status, body = request(self.url(started) + "/api/baseline?" + query)
            if json.loads(body).get("base") == head or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual((status, json.loads(body)["base"]), (200, head))
        self.git("commit", "-q", "--allow-empty", "-m", "clean")
        clean = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(self.registry(state)["resource"][0]["base"], self.git("rev-parse", "HEAD"))

    def test_reviewed_accepted_records_spec_acceptance_on_the_row(self):
        """ANN-239 compared-range #acceptance-reviewed-accepted."""
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        before = self.registry(state)["resource"][0]
        rid = before["id"]
        self.assertNotIn("accepted", before)
        self.spec.write_text("<!doctype html><title>review</title><p>accepted</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, "--accepted", state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        row = self.registry(state)["resource"][0]
        self.assertEqual((row["accepted"], row["base"]), (True, self.git("rev-parse", "HEAD")))
        untouched = {key: value for key, value in before.items() if key not in {"base", "updated_at"}}
        self.assertEqual({key: row[key] for key in untouched}, untouched)
        self.spec.write_text("<!doctype html><title>review</title><p>batch</p>\n", encoding="utf-8")
        plain = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        row = self.registry(state)["resource"][0]
        self.assertEqual((row["accepted"], row["base"]), (False, self.git("rev-parse", "HEAD")))
        self.assertEqual({key: row[key] for key in untouched}, untouched)

    def test_proof_rejects_wrong_bytes(self):
        resource = review_host.parse_resource_spec(
            self.resource(), "owner", "checker", ".cursor-test", "ann45", None
        )
        with mock.patch.object(review_host._verify_module, "verify", side_effect=ValueError("served spec bytes differ")):
            with self.assertRaises(review_host.ProofError):
                review_host.prove_resource("http://127.0.0.1:9999", resource)

    def test_only_register_remove_stop_commands_exist(self):
        commands = set(review_host.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual(commands, {"register", "remove", "reviewed", "stop"})


if __name__ == "__main__":
    unittest.main()
