"""ANN-61: HTTP/registry seam from the ANN-31 brief, T1 and H4/C1/H6."""
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tools/review-serve.py"
VIZ = ROOT / "skill/review-spec/assets/viz"


def git(repo, *args):
    return subprocess.check_output(
        ("git", "-C", str(repo), *args), text=True, stderr=subprocess.DEVNULL,
    ).strip()


class MultiReviewServeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.registry = self.work / "registry.toml"

    @staticmethod
    def _free_port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def make_resource(self, name, spec="docs/specs/domains/x.spec.html"):
        repo = self.work / name
        path = repo / spec
        path.parent.mkdir(parents=True)
        path.write_text(f"<title>{name}</title>baseline {name}\n")
        runtime = repo / "docs/specs/.viz/runtime.js"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.write_text("stale collection runtime")
        (repo / "outside.txt").write_text("outside collection")
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Spec Chat Test")
        git(repo, "config", "user.email", "spec-chat@example.test")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "baseline")
        path.write_text(f"<title>{name}</title>current {name}\n")
        review = Path(str(path) + ".review")
        (review / "human").mkdir(parents=True)
        (review / "agent").mkdir()
        (review / ".cursor-test").write_text("001-handoff-existing.json\n")
        return {
            "id": f"spec:{name}::{spec}", "slug": name,
            "root": str(repo), "narrow_root": str(repo / "docs"),
            "spec": spec, "base": "main", "owner": "test-owner", "checker": "test-checker",
            "lifecycle": "serving", "cursor_name": ".cursor-test",
            "cursor_snapshot": {"lines": 0, "last": "", "sha256": ""},
            "finish_event": "", "registered_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }

    def write_registry(self, resources):
        def toml(value):
            if isinstance(value, dict):
                return "{ " + ", ".join(f"{key} = {toml(item)}" for key, item in value.items()) + " }"
            return json.dumps(value)
        lines = []
        for resource in resources:
            lines.append("[[resource]]")
            lines.extend(f"{key} = {toml(value)}" for key, value in resource.items())
        staged = self.registry.with_suffix(".tmp")
        staged.write_text("\n".join(lines) + "\n")
        staged.replace(self.registry)

    @staticmethod
    def stop(server):
        server.terminate()
        server.communicate(timeout=3)

    def start(self, resources):
        self.write_registry(resources)
        self.server = subprocess.Popen(
            (sys.executable, str(SERVER), "--registry", str(self.registry), "--bind", "127.0.0.1", "--port", "0"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop, self.server)
        line = self.server.stdout.readline()
        self.assertIn("http://127.0.0.1:", line, self.server.stderr.read() if not line else line)
        self.url = line.split(" on ", 1)[1].split()[0]
        self.port = urllib.parse.urlparse(self.url).port

    def request(self, path, method="GET", event=None):
        data = json.dumps(event).encode() if event is not None else None
        request = urllib.request.Request(self.url + path, method=method, data=data)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            with error:
                return error.code, error.read()

    @staticmethod
    def stable(resource):
        return "/" + resource["slug"] + "/" + resource["spec"]

    def api(self, resource, route="events", **params):
        key = "dir" if route == "events" else "path"
        value = self.stable(resource) + (".review" if route == "events" else "")
        return f"/api/{route}?" + urllib.parse.urlencode({key: value, **params})

    def test_independent_bytes_baselines_spools_and_read_only_cursors(self):
        resources = [self.make_resource(name) for name in ("first", "second", "third")]
        self.start(resources)
        for resource in resources:
            with self.subTest(resource=resource["slug"]):
                source = Path(resource["root"]) / resource["spec"]
                self.assertEqual(self.request(self.stable(resource)), (200, source.read_bytes()))
                self.assertEqual(self.request(self.stable(resource), "HEAD"), (200, b""))
                status, body = self.request(self.api(resource, "baseline", base="main"))
                self.assertEqual(status, 200)
                baseline = json.loads(body)
                self.assertEqual(baseline["base"], git(resource["root"], "rev-parse", "main"))
                self.assertEqual(baseline["htmlBase"], baseline["base"])
                self.assertEqual(baseline["html"], f'<title>{resource["slug"]}</title>baseline {resource["slug"]}\n')
                self.assertEqual(baseline["head"], git(resource["root"], "rev-parse", "HEAD"))
                self.assertTrue(baseline["dirty"])
                self.assertRegex(baseline["headDate"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertRegex(baseline["baseDate"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertLessEqual(len(baseline["commits"]), 20)
                self.assertEqual(baseline["commits"][0]["id"], baseline["head"])
                event = {"event": "comment", "id": resource["slug"], "text": resource["slug"]}
                self.assertEqual(self.request(self.api(resource), "POST", event)[0], 200)
        for resource in resources:
            events = json.loads(self.request(self.api(resource))[1])
            self.assertEqual([event["body"]["id"] for event in events], [resource["slug"]])
            review = Path(resource["root"]) / (resource["spec"] + ".review")
            self.assertEqual((review / ".cursor-test").read_text(), "001-handoff-existing.json\n")
        self.assertIsNone(self.server.poll())

    def test_registry_baseline_returns_new_spec_for_spec_created_after_base(self):
        resource = self.make_resource("first")
        repo = Path(resource["root"])
        subprocess.run(("git", "-C", str(repo), "switch", "-c", "feature"), check=True)
        spec = repo / "docs/specs/new.spec.html"
        spec.write_text("<title>new</title>new spec\n")
        git(repo, "add", str(spec))
        subprocess.run(("git", "-C", str(repo), "commit", "-m", "new spec"), check=True)
        resource = resource | {
            "id": "spec:first::docs/specs/new.spec.html",
            "spec": "docs/specs/new.spec.html",
        }
        self.start([resource])
        status, body = self.request(self.api(resource, "baseline", base="main"))
        self.assertEqual(status, 200)
        baseline = json.loads(body)
        self.assertIsNone(baseline["htmlBase"])
        self.assertIsNone(baseline["html"])

    def test_registry_reload_ignores_legacy_finish_field_and_keeps_post_open(self):
        first, second = (self.make_resource(name) for name in ("first", "second"))
        self.start([first, second])
        with socket.create_connection(("127.0.0.1", self.port), timeout=2):
            start = time.monotonic()
            self.assertEqual(self.request(self.stable(second))[0], 200)
            self.assertLess(time.monotonic() - start, 2)
        first["lifecycle"] = "finished"
        self.write_registry([first, second])
        self.assertEqual(self.request(self.api(first), "POST", {"event": "comment", "id": "late"})[0], 200)
        self.assertEqual(self.request(self.stable(first))[0], 200)
        self.assertEqual([event["body"]["id"] for event in json.loads(self.request(self.api(first))[1])], ["late"])
        self.assertEqual(self.request(self.stable(second))[0], 200)

    def test_agent_post_is_forbidden_without_writing_a_file(self):
        resource = self.make_resource("first")
        self.start([resource])
        review = Path(resource["root"]) / (resource["spec"] + ".review")
        before = sorted(str(path.relative_to(review)) for path in review.rglob("*"))
        self.assertEqual(self.request(self.api(resource, actor="agent"), "POST", {"event": "reply", "id": "agent"})[0], 403)
        self.assertEqual(sorted(str(path.relative_to(review)) for path in review.rglob("*")), before)
        self.assertEqual(self.request(self.api(resource, actor="human"), "POST", {"event": "comment", "id": "human"})[0], 200)

    def test_legacy_mode_uses_vendored_viz_and_collection_style(self):
        repo = self.work / "legacy"
        docs = repo / "docs"
        (docs / "specs/.viz").mkdir(parents=True)
        (docs / "specs/.style").mkdir(parents=True)
        spec = docs / "specs/legacy.spec.html"
        spec.write_text("legacy")
        (docs / "specs/.viz/runtime.js").write_text("stale")
        (docs / "specs/.style/site.css").write_text("collection style")
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Spec Chat Test")
        git(repo, "config", "user.email", "spec-chat@example.test")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "legacy")
        port = self._free_port()
        process = subprocess.Popen((sys.executable, str(SERVER), str(docs), str(port)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.1):
                    break
            except Exception:
                time.sleep(0.01)
        def fetch(path):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as error:
                return error.code, error.read()
        self.assertEqual(fetch("/specs/.viz/runtime.js"), (200, (VIZ / "runtime.js").read_bytes()))
        self.assertEqual(fetch("/specs/.style/site.css"), (200, b"collection style"))
        self.assertEqual(fetch("/specs/.viz/missing.js")[0], 404)

    def test_legacy_event_spool_rejects_actor_and_event_symlinks(self):
        repo = self.work / "legacy-spool"
        docs = repo / "docs"
        spec = docs / "specs/legacy.spec.html"
        spec.parent.mkdir(parents=True)
        spec.write_text("legacy")
        review = Path(str(spec) + ".review")
        review.mkdir()
        outside = self.work / "legacy-outside"
        (outside / "human").mkdir(parents=True)
        secret = outside / "secret.json"
        secret.write_text('{"outside": true}')
        (outside / "human" / "leak.json").symlink_to(secret)
        (review / "human").symlink_to(outside / "human", target_is_directory=True)
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "Spec Chat Test")
        git(repo, "config", "user.email", "spec-chat@example.test")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "legacy spool")
        port = self._free_port()
        process = subprocess.Popen((sys.executable, str(SERVER), str(docs), str(port)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.1).close()
                break
            except Exception:
                time.sleep(0.01)
        query = urllib.parse.urlencode({"dir": "specs/legacy.spec.html.review", "actor": "human"})
        url = f"http://127.0.0.1:{port}/api/events?{query}"
        self.assertEqual(self.request_url(url)[0], 400)
        body = json.dumps({"event": "comment", "id": "escape"}).encode()
        self.assertEqual(self.request_url(url, "POST", body)[0], 400)
        (review / "human").unlink()
        (review / "human").mkdir()
        (review / "human/leak.json").symlink_to(secret)
        self.assertEqual(self.request_url(url)[0], 400)

    def request_url(self, url, method="GET", body=None):
        request = urllib.request.Request(url, method=method, data=body)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_registry_allows_multiple_specs_with_one_slug_in_one_repository(self):
        resource = self.make_resource("first")
        other = resource | {"id": "spec:first::docs/adr/y.spec.html", "spec": "docs/adr/y.spec.html"}
        path = Path(resource["root"]) / other["spec"]
        path.parent.mkdir(parents=True)
        path.write_text("other registered spec")
        self.start([resource, other])
        self.assertEqual(self.request(self.stable(resource))[0], 200)
        self.assertEqual(self.request(self.stable(other)), (200, b"other registered spec"))

    def test_legacy_removed_field_does_not_hide_same_slug_sibling_or_assets(self):
        resource = self.make_resource("first")
        other = resource | {"id": "spec:first::docs/adr/y.spec.html", "spec": "docs/adr/y.spec.html"}
        path = Path(resource["root"]) / other["spec"]
        path.parent.mkdir(parents=True)
        path.write_text("other registered spec")
        self.start([resource, other])
        resource["lifecycle"] = "removed"
        self.write_registry([resource, other])
        time.sleep(0.2)

        self.assertEqual(self.request(self.stable(resource))[0], 200)
        self.assertEqual(self.request(self.api(resource))[0], 200)
        self.assertEqual(self.request(self.api(resource, "baseline", base="main"))[0], 200)
        self.assertEqual(self.request(self.stable(other)), (200, b"other registered spec"))
        self.assertEqual(self.request(self.api(other))[0], 200)
        self.assertEqual(self.request("/first/docs/specs/.viz/runtime.js")[0], 200)

    def test_narrow_collection_denies_unregistered_specs_spools_and_escapes(self):
        resource = self.make_resource("first")
        repo = Path(resource["root"])
        (repo / "docs/unregistered.spec.html").write_text("unregistered")
        (repo / "docs/leak.txt").symlink_to(repo / "outside.txt")
        (repo / "docs/alias.html").symlink_to(repo / "docs/unregistered.spec.html")
        review = repo / (resource["spec"] + ".review")
        (repo / "docs/spool-alias").symlink_to(review, target_is_directory=True)
        (review / "human/001-comment.json").write_text('{"event":"comment"}')
        outside_spool = self.work / "outside-spool"
        (outside_spool / "human").mkdir(parents=True)
        (outside_spool / "human/secret.json").write_text('{"outside": true}')
        (review / "human").rename(review / "human-real")
        (review / "human").symlink_to(outside_spool / "human", target_is_directory=True)
        self.start([resource])
        for path in (
            "/first/docs/unregistered.spec.html", "/first/outside.txt", "/first/docs/leak.txt",
            "/first/docs/spool-alias/human/001-comment.json",
            self.stable(resource) + ".review/human/001-comment.json",
            "/first/docs/../outside.txt", "/first/docs/%2e%2e/outside.txt", "/first/docs/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 404)
                self.assertEqual(self.request(path, "HEAD")[0], 404)
        self.assertEqual(self.request("/api/events?dir=first/docs/unregistered.spec.html.review")[0], 400)
        self.assertEqual(self.request("/api/baseline?path=first/docs/unregistered.spec.html")[0], 400)
        events = self.api(resource)
        self.assertEqual(self.request(events)[0], 400)
        self.assertEqual(self.request(events, "POST", {"event": "comment", "id": "escape"})[0], 400)
        (review / "human").unlink()
        (review / "human").mkdir()
        (review / "human/leak.json").symlink_to(outside_spool / "human/secret.json")
        self.assertEqual(self.request(events)[0], 400)

    def test_reload_add_remove_and_invalid_updates_preserve_siblings(self):
        first, second, third = (self.make_resource(name) for name in ("first", "second", "third"))
        self.start([first, second])
        second_bytes = self.request(self.stable(second))
        second_base = self.request(self.api(second, "baseline", base="main"))
        self.assertNotIn(self.stable(third).encode(), self.request("/")[1])
        self.write_registry([first, second, third])
        self.assertIn(self.stable(third).encode(), self.request("/")[1])
        self.assertEqual(self.request(self.stable(third))[0], 200)
        first["lifecycle"] = "parked"
        self.write_registry([first, second, third])
        event = {"event": "comment", "id": "parked"}
        self.assertEqual(self.request(self.api(first), "POST", event)[0], 200)
        spool = self.request(self.api(first))[1]
        first["lifecycle"] = "removed"
        self.write_registry([first, second, third])
        time.sleep(0.2)
        self.assertIn(self.stable(first).encode(), self.request("/")[1])
        for path in (self.stable(first), "/first/docs/specs/.viz/runtime.js", self.api(first), self.api(first, "baseline", base="main")):
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request(self.api(first), "POST", event)[0], 200)
        review = Path(first["root"]) / (first["spec"] + ".review/human")
        self.assertGreaterEqual(len(list(review.glob("*.json"))), len(json.loads(spool)))
        self.write_registry([first, second, third, second | {"slug": "duplicate"}])
        self.assertEqual(self.request(self.stable(second)), second_bytes)
        self.assertEqual(self.request(self.api(second, "baseline", base="main")), second_base)
        self.registry.write_text("not valid TOML [")
        self.assertEqual(self.request(self.stable(second)), second_bytes)
        self.assertIsNone(self.server.poll())

    def test_invalid_registries_exit_before_binding_or_printing_url(self):
        first, second = (self.make_resource(name) for name in ("first", "second"))
        invalid = [
            [first, second | {"id": first["id"]}],
            [first, first | {"id": "spec:other::" + first["spec"]}],
            [first, second | {"slug": "first"}],
            [first | {"spec": "docs/missing.spec.html"}],
            [first | {"base": "not-a-ref"}],
            [first | {"base": ""}],
            [first | {"owner": ""}],
            [first | {"cursor_name": ""}],
            [first | {"slug": "api"}],
            [first | {"path": "first/other/" + first["spec"]}],
            [first | {"path": "elsewhere/" + first["spec"]}],
            [first | {"project": "first"}, second | {"slug": "first", "project": "first", "path": "first/first/" + second["spec"]}],
            [first | {"project": "a"}, second | {"slug": "first", "project": "docs", "path": "first/docs/" + second["spec"]}],
            [first | {"narrow_root": first["root"]}],
            [first | {"root": str(Path(first["root"]) / "docs"), "spec": "specs/domains/x.spec.html"}],
        ]
        for resources in invalid:
            with self.subTest(resources=resources):
                self.write_registry(resources)
                process = subprocess.Popen(
                    (sys.executable, str(SERVER), "--registry", str(self.registry), "--bind", "127.0.0.1", "--port", "0"),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                try:
                    stdout, stderr = process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    self.stop(process)
                    self.fail("invalid registry started a listener")
                self.assertNotEqual(process.returncode, 0, stderr)
                self.assertNotIn("http://", stdout)


if __name__ == "__main__":
    unittest.main()
