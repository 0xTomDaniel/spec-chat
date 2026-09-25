import contextlib
import fcntl
import importlib.util
import io
import json
import os
import socket
import subprocess
import tempfile
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
        review_host.assign_path([], resource)
        with mock.patch.object(review_host._verify_module, "verify", side_effect=ValueError("served spec bytes differ")):
            with self.assertRaises(review_host.ProofError):
                review_host.prove_resource("http://127.0.0.1:9999", resource)

    def make_repo(self, name, spec="docs/specs/x.spec.html"):
        repo = self.work / name
        path = repo / spec
        path.parent.mkdir(parents=True)
        path.write_text(f"<!doctype html><title>{name}</title><p data-anchor=\"a\">{name}</p>\n", encoding="utf-8")
        (repo / "docs/specs/.style").mkdir(parents=True)
        (repo / "docs/specs/.style/spec.css").write_text(f"/* {name} */", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "tests@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Spec Chat tests"], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
        base = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        return repo, base

    def register_from(self, state, project, repo, base, spec="docs/specs/x.spec.html", slug="ann45", ports=None):
        return self.run_cli(
            "register", "--state-dir", str(state), "--bind", "127.0.0.1", "--proof-host", "127.0.0.1",
            "--test-loopback", "--resource", f"{project}={repo}:{spec}@{base}", "--owner", "owner",
            "--checker", "checker", "--cursor-name", ".cursor-test", "--slug", slug, state=state, ports=ports,
        )

    @staticmethod
    def served_url(result):
        return next(line.split("review URL: ", 1)[1] for line in result.stdout.splitlines() if line.startswith("review URL: "))

    def baseline(self, url, path, base=None):
        params = {"path": path} | ({"base": base} if base else {})
        status, body = request(url + "/api/baseline?" + urllib.parse.urlencode(params))
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def git_head(self, repo):
        return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    def test_reviewed_commits_a_dirty_spec_and_moves_the_row_base(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        url, page = self.served_url(started), "ann45/docs/specs/review.spec.html"
        rid = self.registry(state)["resource"][0]["id"]
        self.assertEqual(self.baseline(url, page)["base"], self.base, "before any review, the registry base is compared")

        self.spec.write_text("<!doctype html><title>review</title><p>edited in review</p>\n", encoding="utf-8")
        (self.repo / "unrelated.txt").write_text("staged elsewhere\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "unrelated.txt"], check=True)
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        head = self.git_head(self.repo)
        self.assertNotEqual(head, self.base)
        subject = subprocess.check_output(["git", "-C", str(self.repo), "log", "-1", "--format=%s"], text=True).strip()
        self.assertIn("docs/specs/review.spec.html", subject)
        self.assertIn("human spec review", subject)
        changed = subprocess.check_output(["git", "-C", str(self.repo), "show", "--name-only", "--format=", "HEAD"], text=True).split()
        self.assertEqual(changed, ["docs/specs/review.spec.html"], "only the spec is committed")
        status = subprocess.check_output(["git", "-C", str(self.repo), "status", "--porcelain"], text=True)
        self.assertIn("A  unrelated.txt", status)
        self.assertEqual(self.registry(state)["resource"][0]["base"], head)
        default = self.baseline(url, page)
        self.assertEqual((default["base"], default["htmlBase"]), (head, head))
        self.assertEqual(default["html"].encode(), self.spec.read_bytes())
        self.assertNotIn("reviewed", default)

        # Text merged in by others after the review shows as new against the row base.
        self.spec.write_text("<!doctype html><title>review</title><p>edited in review</p><p>merged</p>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qam", "merge from others"], check=True)
        self.assertNotIn("merged", self.baseline(url, page)["html"])

    def test_reviewed_with_a_clean_spec_sets_the_row_base_to_head(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        (self.repo / "other.txt").write_text("later\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "other.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "later"], check=True)
        head = self.git_head(self.repo)
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.git_head(self.repo), head, "a clean spec makes no commit")
        self.assertEqual(self.registry(state)["resource"][0]["base"], head)
        unknown = self.run_cli("reviewed", "--state-dir", str(state), "--id", "spec:nope::x", state=state)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("unknown resource id", unknown.stderr)

    def test_legacy_row_has_no_project_and_does_not_clash_with_a_slug_named_project(self):
        legacy = {"id": "spec:ann45::docs/specs/x.spec.html", "slug": "ann45", "spec": "docs/specs/x.spec.html"}
        self.assertIsNone(review_host.row_project(legacy))
        other = {"id": "spec:ann45::ann45/docs/specs/x.spec.html", "slug": "ann45", "project": "ann45",
                 "spec": "docs/specs/x.spec.html", "path": "ann45/ann45/docs/specs/x.spec.html"}
        self.assertIsNone(review_host.path_rule_violation([legacy, other]))
        self.assertIn("invalid", review_host.path_rule_violation([legacy | {"path": "ann45/ann45/docs/specs/x.spec.html"}]))
        mixed = other | {"id": "b", "spec": "docs/specs/y.spec.html", "path": "ann45/docs/specs/y.spec.html"}
        self.assertIn("mixed", review_host.path_rule_violation([other, mixed]))
        clash = other | {"project": "docs", "path": "ann45/docs/docs/specs/x.spec.html"}
        self.assertIn("collides", review_host.path_rule_violation([legacy, clash]))

    def test_one_slug_holds_several_roots_with_fixed_paths(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        sc, sc_base = self.make_repo("sc")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        second = self.register_from(state, "sc", sc, sc_base, ports=port)
        self.assertEqual(second.returncode, 0, second.stderr)
        url = self.served_url(second)
        self.assertIn("spec:ann45::sc/docs/specs/x.spec.html URL: " + url + "/ann45/sc/docs/specs/x.spec.html", second.stdout)

        rows = {row["project"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(rows["aa"]["path"], "ann45/docs/specs/x.spec.html")
        self.assertEqual(rows["sc"]["path"], "ann45/sc/docs/specs/x.spec.html")
        for name, repo in (("aa", aa), ("sc", sc)):
            path = rows[name]["path"]
            self.assertEqual(request(url + "/" + path), (200, (repo / "docs/specs/x.spec.html").read_bytes()))
            style = path.rsplit("/", 1)[0] + "/.style/spec.css"
            self.assertEqual(request(url + "/" + style), (200, f"/* {name} */".encode()))
            self.assertEqual(request(url + "/" + path.rsplit("/", 1)[0] + "/.viz/runtime.js")[0], 200)

        # Review state belongs to each root: a reviewed commit lands in that root and its row only.
        for name, repo in (("aa", aa), ("sc", sc)):
            (repo / "docs/specs/x.spec.html").write_text(f"<title>{name}</title><p>reviewed</p>\n", encoding="utf-8")
            done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rows[name]["id"], state=state)
            self.assertEqual(done.returncode, 0, done.stderr)
            head = self.git_head(repo)
            self.assertEqual(self.baseline(url, rows[name]["path"])["base"], head)
        after = {row["project"]: row for row in self.registry(state)["resource"]}
        for name, repo in (("aa", aa), ("sc", sc)):
            self.assertEqual(after[name]["base"], self.git_head(repo))
        rows = after

        # Registering the same row id from a recreated root is an upsert: only that row changes.
        (aa / "docs/specs/y.spec.html").write_text("<title>aa y</title>\n", encoding="utf-8")
        added = self.register_from(state, "aa", aa, aa_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        before = {row["id"]: row for row in self.registry(state)["resource"]}
        other, other_base = self.make_repo("aa-recreated")
        moved = self.register_from(state, "aa", other, other_base, ports=port)
        self.assertEqual(moved.returncode, 0, moved.stderr)
        now_rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(set(now_rows), set(before), "no row added or removed")
        x_id = rows["aa"]["id"]
        self.assertEqual((now_rows[x_id]["root"], now_rows[x_id]["base"]), (str(other.resolve()), other_base))
        self.assertEqual(now_rows[x_id]["path"], "ann45/docs/specs/x.spec.html")
        for rid, row in before.items():
            if rid != x_id:
                self.assertEqual((now_rows[rid]["root"], now_rows[rid]["base"], now_rows[rid]["path"]),
                                 (row["root"], row["base"], row["path"]), rid)
        self.assertEqual(request(url + "/ann45/docs/specs/x.spec.html"), (200, (other / "docs/specs/x.spec.html").read_bytes()))
        self.assertEqual(request(url + "/ann45/docs/specs/y.spec.html"), (200, (aa / "docs/specs/y.spec.html").read_bytes()))
        rows = {row["project"]: row for row in self.registry(state)["resource"] if row["spec"] == "docs/specs/x.spec.html"}

        # A project named like the primary collection's top-level directory is refused.
        docs, docs_base = self.make_repo("docs-project")
        clash = self.register_from(state, "docs", docs, docs_base, ports=port)
        self.assertNotEqual(clash.returncode, 0)
        self.assertIn("collides", clash.stderr)

        # Paths are fixed: removing the primary never moves the others.
        removed = self.run_cli("remove", "--state-dir", str(state), "--id", rows["aa"]["id"], state=state)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        added = self.register_from(state, "sc", sc, sc_base, spec="docs/specs/.style/../x.spec.html", ports=port)
        self.assertNotEqual(added.returncode, 0)
        (sc / "docs/specs/y.spec.html").write_text("<title>y</title>\n", encoding="utf-8")
        added = self.register_from(state, "sc", sc, sc_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        paths = sorted(row["path"] for row in self.registry(state)["resource"])
        self.assertEqual(paths, ["ann45/docs/specs/y.spec.html", "ann45/sc/docs/specs/x.spec.html", "ann45/sc/docs/specs/y.spec.html"])
        self.assertEqual(request(url + "/ann45/sc/docs/specs/x.spec.html")[0], 200)
        self.assertEqual(request(url + "/ann45/docs/specs/x.spec.html")[0], 404)

    def write_legacy_registry(self, state, repo, base, spec="docs/specs/x.spec.html", slug="ann45"):
        state.mkdir(parents=True, exist_ok=True)
        (state / "registry.toml").write_text(
            f"""[[resource]]
id = "spec:{slug}::{spec}"
slug = "{slug}"
root = "{repo.resolve()}"
narrow_root = "{(repo / 'docs').resolve()}"
spec = "{spec}"
base = "{base}"
owner = "owner"
checker = "checker"
cursor_name = ".cursor-test"
""",
            encoding="utf-8",
        )

    def test_legacy_row_is_not_taken_over_by_a_second_repo_with_the_same_spec_path(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        sc, sc_base = self.make_repo("sc")
        self.write_legacy_registry(state, aa, aa_base)
        added = self.register_from(state, "sc", sc, sc_base)
        self.assertEqual(added.returncode, 0, added.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        legacy = rows["spec:ann45::docs/specs/x.spec.html"]
        self.assertEqual((legacy["root"], legacy.get("project")), (str(aa.resolve()), None))
        self.assertEqual(rows["spec:ann45::sc/docs/specs/x.spec.html"]["path"], "ann45/sc/docs/specs/x.spec.html")
        url = self.served_url(added)
        self.assertEqual(request(url + "/ann45/docs/specs/x.spec.html"), (200, (aa / "docs/specs/x.spec.html").read_bytes()))

        # The same root re-registering the legacy spec is its upsert: one row, path kept.
        again = self.register_from(state, "aa", aa, aa_base, ports=self.registry(state)["process"]["port"])
        self.assertEqual(again.returncode, 0, again.stderr)
        rows = {row["id"]: row for row in self.registry(state)["resource"]}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["spec:ann45::docs/specs/x.spec.html"]["project"], "aa")

    def test_prefixed_row_whose_id_matches_the_primary_cannot_delete_it(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa", spec="sc/docs/x.spec.html")
        sc, sc_base = self.make_repo("sc", spec="docs/x.spec.html")
        first = self.register_from(state, "aa", aa, aa_base, spec="sc/docs/x.spec.html")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = (state / "registry.toml").read_bytes()
        port = self.registry(state)["process"]["port"]
        clash = self.register_from(state, "sc", sc, sc_base, spec="docs/x.spec.html", ports=port)
        self.assertNotEqual(clash.returncode, 0)
        self.assertEqual((state / "registry.toml").read_bytes(), before, "the primary row survives unchanged")
        self.assertEqual([row["project"] for row in self.registry(state)["resource"]], ["aa"])

    def test_a_second_project_from_the_primary_root_gets_the_prefixed_form(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        (aa / "docs/specs/y.spec.html").write_text("<title>y</title>\n", encoding="utf-8")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        other = self.register_from(state, "bb", aa, aa_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(other.returncode, 0, other.stderr)
        rows = {row["project"]: row["path"] for row in self.registry(state)["resource"]}
        self.assertEqual(rows, {"aa": "ann45/docs/specs/x.spec.html", "bb": "ann45/bb/docs/specs/y.spec.html"})

    def test_reviewed_restores_the_owners_index_when_the_commit_fails(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\necho refused by hook >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        spec = "docs/specs/review.spec.html"

        def index_entry():
            return subprocess.check_output(["git", "-C", str(self.repo), "ls-files", "-s", "--", spec], text=True)

        # Owner staged one version and kept editing: the staged entry is put back as it was.
        self.spec.write_text("<title>review</title><p>staged by owner</p>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", spec], check=True)
        staged = index_entry()
        self.spec.write_text("<title>review</title><p>edited in review</p>\n", encoding="utf-8")
        failed = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("refused by hook", failed.stderr)
        self.assertEqual(index_entry(), staged)
        self.assertEqual(self.git_head(self.repo), self.base)
        self.assertEqual(self.registry(state)["resource"][0]["base"], self.base, "base unmoved")

        # Nothing staged before: nothing staged after.
        subprocess.run(["git", "-C", str(self.repo), "reset", "-q", "--", spec], check=True)
        failed = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertNotEqual(failed.returncode, 0)
        cached = subprocess.check_output(["git", "-C", str(self.repo), "diff", "--cached", "--name-only"], text=True)
        self.assertEqual(cached, "")
        self.assertEqual(self.registry(state)["resource"][0]["base"], self.base)

    def test_re_register_at_the_row_base_keeps_the_last_reviewed_version(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        row = self.registry(state)["resource"][0]
        (aa / "docs/specs/x.spec.html").write_text("<title>aa</title><p>reviewed</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", row["id"], state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        reviewed_base = self.registry(state)["resource"][0]["base"]
        self.assertEqual(reviewed_base, self.git_head(aa))
        stopped = self.run_cli("stop", "--state-dir", str(state), state=state)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        again = self.register_from(state, "aa", aa, reviewed_base)
        self.assertEqual(again.returncode, 0, again.stderr)
        rows = self.registry(state)["resource"]
        self.assertEqual([(r["id"], r["base"]) for r in rows], [(row["id"], reviewed_base)])
        self.assertEqual(self.baseline(self.served_url(again), row["path"])["base"], reviewed_base)

    def test_relative_assets_follow_the_referring_rows_root_after_an_upsert(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        (aa / "docs/specs/y.spec.html").write_text("<title>aa y</title>\n", encoding="utf-8")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        added = self.register_from(state, "aa", aa, aa_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        other, other_base = self.make_repo("aa-recreated")
        moved = self.register_from(state, "aa", other, other_base, ports=port)
        self.assertEqual(moved.returncode, 0, moved.stderr)
        url = self.served_url(moved)
        style = url + "/ann45/docs/specs/.style/spec.css"
        for page, name in (("x", "aa-recreated"), ("y", "aa")):
            referer = f"{url}/ann45/docs/specs/{page}.spec.html?focus=changes"
            with urllib.request.urlopen(urllib.request.Request(style, headers={"Referer": referer}), timeout=4) as response:
                self.assertEqual(response.read(), f"/* {name} */".encode(), page)

    def test_reviewed_ignores_byte_differences_git_filters_away(self):
        state = self.work / "state"
        (self.repo / ".gitattributes").write_text("*.html text eol=crlf\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", ".gitattributes"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "eol"], check=True)
        self.spec.unlink()
        subprocess.run(["git", "-C", str(self.repo), "checkout", "--", "docs/specs/review.spec.html"], check=True)
        self.assertIn(b"\r\n", self.spec.read_bytes())
        head = self.git_head(self.repo)
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("committed", done.stdout)
        self.assertEqual(self.git_head(self.repo), head)
        self.assertEqual(self.registry(state)["resource"][0]["base"], head)

    def test_a_project_adopts_its_legacy_row_at_the_same_root(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        other, other_base = self.make_repo("aa-other")
        self.write_legacy_registry(state, aa, aa_base)
        prefixed = self.register_from(state, "aa", other, other_base)
        self.assertEqual(prefixed.returncode, 0, prefixed.stderr)
        port = self.registry(state)["process"]["port"]
        again = self.register_from(state, "aa", aa, aa_base, ports=port)
        self.assertEqual(again.returncode, 0, again.stderr)
        rows = self.registry(state)["resource"]
        # One row for this root and spec: the legacy row takes the project and keeps its path.
        self.assertEqual([(r["id"], r["root"], r.get("project"), r["path"]) for r in rows],
                         [("spec:ann45::docs/specs/x.spec.html", str(aa.resolve()), "aa", "ann45/docs/specs/x.spec.html")])
        (aa / "docs/specs/x.spec.html").write_text("<title>aa</title><p>reviewed</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rows[0]["id"], state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([r["base"] for r in self.registry(state)["resource"]], [self.git_head(aa)])

    def test_host_wake_re_register_at_the_registry_base_keeps_it(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        (aa / "docs/specs/x.spec.html").write_text("<title>aa</title><p>reviewed</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        port = self.registry(state)["process"]["port"]
        # Each turn's host-wake register reads the row's current base, never the lane start base.
        for _ in range(2):
            row_base = next(r["base"] for r in self.registry(state)["resource"] if r["id"] == rid)
            wake = self.register_from(state, "aa", aa, row_base, ports=port)
            self.assertEqual(wake.returncode, 0, wake.stderr)
            self.assertIn("wake=", wake.stdout)
        rows = self.registry(state)["resource"]
        self.assertEqual([(r["id"], r["base"]) for r in rows], [(rid, self.git_head(aa))])
        self.assertNotEqual(rows[0]["base"], aa_base)
        self.assertEqual(self.baseline(self.served_url(wake), rows[0]["path"])["base"], self.git_head(aa))

    def test_concurrent_reviewed_in_one_root_both_commit(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        (aa / "docs/specs/y.spec.html").write_text("<title>y</title>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(aa), "add", "."], check=True)
        subprocess.run(["git", "-C", str(aa), "commit", "-qm", "y"], check=True)
        head = self.git_head(aa)
        first = self.register_from(state, "aa", aa, head)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        second = self.register_from(state, "aa", aa, head, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(second.returncode, 0, second.stderr)
        ids = [r["id"] for r in self.registry(state)["resource"]]
        for name in ("x", "y"):
            (aa / f"docs/specs/{name}.spec.html").write_text(f"<title>{name}</title><p>reviewed</p>\n", encoding="utf-8")
        env = os.environ.copy()
        env.update({"PATH": path_without_herdr(), "PYTHONDONTWRITEBYTECODE": "1"})
        for _ in range(3):
            procs = [subprocess.Popen(["python3", str(LAUNCHER_PATH), "reviewed", "--state-dir", str(state), "--id", rid],
                                      env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for rid in ids]
            results = [(proc.returncode, proc.communicate(timeout=30)[1]) for proc in procs]
            results = [(proc.returncode, err) for proc, (_, err) in zip(procs, results)]
            for code, err in results:
                self.assertEqual(code, 0, err)
        log = subprocess.check_output(["git", "-C", str(aa), "log", "--format=%s", f"{head}..HEAD"], text=True).split("\n")
        self.assertEqual(sorted(line for line in log if line),
                         ["docs: human spec review of docs/specs/x.spec.html", "docs: human spec review of docs/specs/y.spec.html"])
        status = subprocess.check_output(["git", "-C", str(aa), "status", "--porcelain"], text=True)
        self.assertEqual(status, "")
        self.assertEqual({r["base"] for r in self.registry(state)["resource"]}, {self.git_head(aa)})

        # A held per-root lock makes reviewed wait; a foreign index.lock is retried, not fatal.
        (aa / "docs/specs/x.spec.html").write_text("<title>x</title><p>again</p>\n", encoding="utf-8")
        index_lock = aa / ".git/index.lock"
        with review_host.root_lock(aa):
            index_lock.write_text("", encoding="utf-8")
            proc = subprocess.Popen(["python3", str(LAUNCHER_PATH), "reviewed", "--state-dir", str(state), "--id", ids[0]],
                                    env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1)
            self.assertIsNone(proc.poll(), "reviewed waits for the root lock")
        time.sleep(0.5)
        self.assertIsNone(proc.poll(), "reviewed retries while index.lock is held")
        index_lock.unlink()
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        self.assertIn("x.spec.html", subprocess.check_output(["git", "-C", str(aa), "log", "-1", "--format=%s"], text=True))

    def test_reviewed_staged_only_change_sets_head_and_ignored_spec_is_refused(self):
        state = self.work / "state"
        started = self.run_cli(*self.register_args(state), state=state)
        self.assertEqual(started.returncode, 0, started.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        spec = "docs/specs/review.spec.html"
        original = self.spec.read_bytes()
        self.spec.write_text("<title>review</title><p>staged</p>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", spec], check=True)
        self.spec.write_bytes(original)
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("committed", done.stdout)
        self.assertEqual(self.git_head(self.repo), self.base)
        self.assertEqual(self.registry(state)["resource"][0]["base"], self.base)

        # An ignored, untracked spec cannot be committed: a clear refusal, base unmoved.
        ignored = self.repo / "docs/specs/ignored.spec.html"
        ignored.write_text("<title>ignored</title>\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text("ignored.spec.html\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "ignore", "--", ".gitignore"], check=True)
        port = self.registry(state)["process"]["port"]
        head = self.git_head(self.repo)
        added = self.run_cli(*self.register_args(state, spec="ignored"), state=state, ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        ignored_id = next(r["id"] for r in self.registry(state)["resource"] if r["spec"].endswith("ignored.spec.html"))
        refused = self.run_cli("reviewed", "--state-dir", str(state), "--id", ignored_id, state=state)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("ignored by Git", refused.stderr)
        self.assertEqual(self.git_head(self.repo), head)
        self.assertEqual(next(r["base"] for r in self.registry(state)["resource"] if r["id"] == ignored_id), self.base)

    def test_reviewed_treats_spec_names_as_literal_paths(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa", spec="docs/specs/a[1].spec.html")
        (aa / "docs/specs/a1.spec.html").write_text("<title>a1</title>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(aa), "add", "."], check=True)
        subprocess.run(["git", "-C", str(aa), "commit", "-qm", "a1"], check=True)
        head = self.git_head(aa)
        first = self.register_from(state, "aa", aa, head, spec="docs/specs/a[1].spec.html")
        self.assertEqual(first.returncode, 0, first.stderr)
        rid = self.registry(state)["resource"][0]["id"]
        # Only the glob-matching sibling is dirty: the literal spec is unchanged, so no commit.
        (aa / "docs/specs/a1.spec.html").write_text("<title>a1</title><p>other work</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.git_head(aa), head)
        (aa / "docs/specs/a[1].spec.html").write_text("<title>a</title><p>reviewed</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rid, state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        changed = subprocess.check_output(["git", "-C", str(aa), "show", "--name-only", "--format=", "HEAD"], text=True).split("\n")
        self.assertEqual([line for line in changed if line], ["docs/specs/a[1].spec.html"])
        self.assertIn(" M docs/specs/a1.spec.html", subprocess.check_output(["git", "-C", str(aa), "status", "--porcelain"], text=True))

    def test_register_ignores_a_torn_down_sibling_root(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        sc, sc_base = self.make_repo("sc")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        second = self.register_from(state, "sc", sc, sc_base, ports=port)
        self.assertEqual(second.returncode, 0, second.stderr)
        subprocess.run(["rm", "-rf", str(sc)], check=True)
        again = self.register_from(state, "aa", aa, aa_base, ports=port)
        self.assertEqual(again.returncode, 0, again.stderr)
        (aa / "docs/specs/y.spec.html").write_text("<title>y</title>\n", encoding="utf-8")
        added = self.register_from(state, "aa", aa, aa_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(sorted(r["project"] for r in self.registry(state)["resource"]), ["aa", "aa", "sc"])

    def test_css_referenced_assets_come_from_a_root_that_holds_them_after_an_upsert(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        (aa / "docs/specs/y.spec.html").write_text("<title>aa y</title>\n", encoding="utf-8")
        (aa / "docs/specs/.style/font.woff2").write_bytes(b"aa font")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        port = self.registry(state)["process"]["port"]
        added = self.register_from(state, "aa", aa, aa_base, spec="docs/specs/y.spec.html", ports=port)
        self.assertEqual(added.returncode, 0, added.stderr)
        other, other_base = self.make_repo("aa-recreated")
        (other / "docs/specs/.style/font.woff2").write_bytes(b"recreated font")
        (other / "docs/specs/.style/x-only.css").write_text("/* x */", encoding="utf-8")
        (other / "docs/specs/.style/x.png").write_bytes(b"x image")
        moved = self.register_from(state, "aa", other, other_base, ports=port)
        self.assertEqual(moved.returncode, 0, moved.stderr)
        url = self.served_url(moved) + "/ann45/docs/specs/.style/"

        def get(name, referer):
            req = urllib.request.Request(url + name, headers={"Referer": url + referer})
            with urllib.request.urlopen(req, timeout=4) as response:
                return response.read()

        # Only the moved row's root holds the image: served whatever the stylesheet Referer.
        self.assertEqual(get("x.png", "spec.css"), b"x image")
        # Both roots hold the font: the root that holds the referring stylesheet wins.
        self.assertEqual(get("font.woff2", "x-only.css"), b"recreated font")

    def test_host_and_server_share_one_spec_path_rule(self):
        server = review_host._serve_module
        for value in ("docs\\specs\\x.spec.html", "docs/specs/x.spec.html"):
            self.assertEqual(review_host.normalise_spec(value), server.normalise_spec(value))
        for value in ("/abs/x.spec.html", "docs/../x.spec.html", "docs//x.spec.html", "docs/x.html"):
            with self.assertRaises(ValueError):
                server.normalise_spec(value)
            with self.assertRaises(review_host.LauncherError):
                review_host.normalise_spec(value)

    def test_register_reports_wake_after_releasing_the_state_lock(self):
        state = self.work / "state"
        held = []

        def probe(owner):
            descriptor = os.open(state / ".state.lock", os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
            finally:
                os.close(descriptor)
            return "unavailable"

        env = {"SPEC_CHAT_APPROVED_INGRESS_PORTS": str(self.port()), "PATH": path_without_herdr()}
        self.states.append(state)
        with mock.patch.dict(os.environ, env), mock.patch.object(review_host, "wake_status", probe), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(review_host.main(self.register_args(state)), 0)
        self.assertEqual(held, [False])

    def test_reviewed_and_remove_ignore_a_torn_down_sibling_root(self):
        state = self.work / "state"
        aa, aa_base = self.make_repo("aa")
        sc, sc_base = self.make_repo("sc")
        first = self.register_from(state, "aa", aa, aa_base)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.register_from(state, "sc", sc, sc_base, ports=self.registry(state)["process"]["port"])
        self.assertEqual(second.returncode, 0, second.stderr)
        rows = {row["project"]: row for row in self.registry(state)["resource"]}
        subprocess.run(["rm", "-rf", str(sc)], check=True)
        (aa / "docs/specs/x.spec.html").write_text("<title>aa</title><p>reviewed</p>\n", encoding="utf-8")
        done = self.run_cli("reviewed", "--state-dir", str(state), "--id", rows["aa"]["id"], state=state)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual({r["project"]: r["base"] for r in self.registry(state)["resource"]},
                         {"aa": self.git_head(aa), "sc": sc_base})
        gone = self.run_cli("reviewed", "--state-dir", str(state), "--id", rows["sc"]["id"], state=state)
        self.assertNotEqual(gone.returncode, 0)
        removed = self.run_cli("remove", "--state-dir", str(state), "--id", rows["sc"]["id"], state=state)
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual([r["project"] for r in self.registry(state)["resource"]], ["aa"])

    def test_registry_read_normalises_spec_separators_once(self):
        state = self.work / "state"
        self.write_legacy_registry(state, self.repo, self.base, spec="docs\\\\specs\\\\review.spec.html")
        records, _ = review_host.registry_state(state / "registry.toml")
        self.assertEqual(records[0]["spec"], "docs/specs/review.spec.html")
        self.assertEqual(review_host.row_path(records[0]), "ann45/docs/specs/review.spec.html")

    def test_project_id_must_be_one_path_segment(self):
        for project in ("a/b", "..", "a b"):
            with self.subTest(project=project):
                with self.assertRaisesRegex(review_host.LauncherError, "one path segment"):
                    review_host.parse_resource_spec(
                        self.resource(project), "owner", "checker", ".cursor-test", "ann45", None
                    )
        parsed = review_host.parse_resource_spec(self.resource(), "owner", "checker", ".cursor-test", "ann45", None)
        self.assertNotIn("id", parsed)
        self.assertNotIn("path", parsed)

    def test_only_register_remove_reviewed_stop_commands_exist(self):
        commands = set(review_host.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual(commands, {"register", "remove", "reviewed", "stop"})


if __name__ == "__main__":
    unittest.main()
