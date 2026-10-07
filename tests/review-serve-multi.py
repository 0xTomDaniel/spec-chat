"""ANN-61: HTTP/registry seam from the ANN-31 brief, T1 and H4/C1/H6."""
import hashlib
import http.client
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
SERVER = ROOT / "skill/review-spec/assets/review-serve.py"
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

    def test_page_named_event_resend_stores_it_once(self):
        # review-state #live-save, #acceptance-lost-response (server side), #trust-immutable.
        resource = self.make_resource("named")
        self.start([resource])
        human = Path(resource["root"]) / (resource["spec"] + ".review") / "human"
        name = "1759300000000000000-comment-c1.json"
        event = {"event": "comment", "id": "c1", "text": "x", "browser": "b", "author": "Mango"}
        path = self.api(resource, actor="human", name=name)
        self.assertEqual(self.request(path, "POST", event), (200, json.dumps({"ok": True, "name": name}).encode()))
        stored = (human / name).read_bytes()
        self.assertEqual(self.request(path, "POST", event)[0], 200)
        self.assertEqual(sorted(p.name for p in human.iterdir()), [name])
        self.assertEqual((human / name).read_bytes(), stored)
        self.assertEqual(self.request(path, "POST", {**event, "text": "other"})[0], 409)
        self.assertEqual((human / name).read_bytes(), stored)
        events = json.loads(self.request(self.api(resource))[1])
        self.assertEqual([e["name"] for e in events], [name])
        for bad in ("1-comment-c2.json", "1-reply-c1.json", "x-comment-c1.json", "1-comment-c1.txt", ".1-comment-c1.json"):
            self.assertEqual(self.request(self.api(resource, actor="human", name=bad), "POST", event)[0], 400, bad)
        self.assertEqual(self.request(self.api(resource, actor="human", name="2-comment-c3.json"), "POST",
                                      {"event": "comment", "id": "c3", "actor": "agent"})[0], 403)
        self.assertEqual(sorted(p.name for p in human.iterdir()), [name])

    def test_event_write_io_failure_is_a_server_error_the_page_retries(self):
        # review-state #offline-outbox: an I/O failure is never a refusal, so the page keeps the event.
        resource = self.make_resource("io")
        self.start([resource])
        human = Path(resource["root"]) / (resource["spec"] + ".review") / "human"
        human.mkdir(parents=True, exist_ok=True)
        human.chmod(0o500)
        self.addCleanup(human.chmod, 0o700)
        path = self.api(resource, actor="human", name="1759300000000000000-comment-io.json")
        event = {"event": "comment", "id": "io", "text": "x"}
        self.assertEqual(self.request(path, "POST", event)[0], 503)
        human.chmod(0o700)
        self.assertEqual(self.request(path, "POST", event)[0], 200)

    def test_served_spec_text_is_stored_as_a_version_by_its_etag(self):
        # review-state #model-versions, #model-fields: version hash is the page ETag.
        resource = self.make_resource("versions")
        self.start([resource])
        source = Path(resource["root"]) / resource["spec"]
        versions = Path(str(source) + ".review") / "versions"
        seen = []
        for text in ("<title>v</title>one\n", "<title>v</title>two\n"):
            source.write_text(text)
            with urllib.request.urlopen(self.url + self.stable(resource), timeout=2) as response:
                etag = response.headers["ETag"].strip('"')
                body = response.read()
            self.assertEqual(etag, hashlib.sha256(body).hexdigest())
            self.assertEqual((versions / (etag + ".html")).read_bytes(), body)
            seen.append(etag + ".html")
        self.assertEqual(sorted(p.name for p in versions.iterdir()), sorted(seen))
        events = json.loads(self.request(self.api(resource))[1])
        self.assertEqual(events, [])

    def test_pages_revalidate_by_etag_and_vendor_assets_are_immutable(self):
        resource = self.make_resource("cache")
        self.start([resource])
        def fetch(path, etag=None):
            request = urllib.request.Request(self.url + path, headers={"If-None-Match": etag} if etag else {})
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    return response.status, response.headers, response.read()
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, error.headers, error.read()
        for path in ("/", self.stable(resource), "/cache/docs/specs/.viz/runtime.js"):
            status, headers, body = fetch(path)
            self.assertEqual((status, headers["Cache-Control"]), (200, "no-cache"), path)
            etag = headers["ETag"]
            self.assertEqual(etag, '"%s"' % hashlib.sha256(body).hexdigest())
            status, headers, body = fetch(path, etag)
            self.assertEqual((status, headers["ETag"], headers["Cache-Control"], body), (304, etag, "no-cache", b""), path)
            self.assertEqual(fetch(path, '"stale"')[0], 200, path)
        status, headers, body = fetch("/cache/docs/specs/.viz/vendor/echarts-5.5.1.min.js")
        self.assertEqual((status, headers["Cache-Control"], headers["ETag"]), (200, "public, max-age=31536000, immutable", None))
        self.assertEqual(body, (VIZ / "vendor/echarts-5.5.1.min.js").read_bytes())
        status, headers, _ = fetch("/cache/docs/specs/.viz/missing.js")
        self.assertEqual((status, headers["Cache-Control"], headers["ETag"]), (404, "no-cache", None))

    def test_every_path_is_http11_keep_alive_on_one_connection(self):
        resource = self.make_resource("alive")
        self.start([resource])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        self.addCleanup(connection.close)
        page = self.stable(resource)
        etag = None
        socket_in_use = None
        steps = (
            ("GET", "/", None, {}, 200),
            ("GET", page, None, {}, 200),
            ("GET", page, None, "etag", 304),
            ("HEAD", page, None, {}, 200),
            ("GET", self.api(resource, "baseline"), None, {}, 200),
            ("GET", self.api(resource), None, {}, 200),
            ("POST", self.api(resource, actor="agent"), b'{"event": "reply", "id": "agent"}', {}, 403),
            ("POST", "/nowhere", b'{"event": "comment", "id": "lost"}', {}, 404),
            ("POST", self.api(resource), b'not json', {}, 400),
            ("POST", self.api(resource, actor="human"), b'{"event": "comment", "id": "human"}', {}, 200),
            ("GET", "/alive/docs/specs/.viz/vendor/echarts-5.5.1.min.js", None, {}, 200),
            ("GET", page, None, {}, 200),
        )
        for method, path, body, headers, expected in steps:
            if headers == "etag":
                headers = {"If-None-Match": etag}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            self.assertEqual((response.status, response.version), (expected, 11), (method, path))
            self.assertFalse(response.will_close, (method, path))
            if method == "HEAD" or expected == 304:
                self.assertEqual(data, b"", (method, path))
            else:
                self.assertEqual(int(response.headers["Content-Length"]), len(data), (method, path))
            if path == page and expected == 200:
                etag = response.headers["ETag"]
            socket_in_use = socket_in_use or connection.sock
            self.assertIs(connection.sock, socket_in_use, (method, path))
        connection.request("GET", "/alive/docs/specs/missing.spec.html")
        response = connection.getresponse()
        response.read()
        self.assertEqual((response.status, response.version, response.will_close), (404, 11, True))

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

    def test_index_groups_lanes_projects_and_status_in_order(self):
        """ANN-136 lane-hosting #acceptance-review-index."""
        import html as html_lib
        import re

        board = self.make_resource("ann134") | {"project": "aa"}
        repo = Path(board["root"])
        Path(repo / board["spec"]).write_text("<title>Zeta board</title>changed\n")
        fresh = board | {"id": "spec:ann134:aa::docs/specs/new.spec.html", "spec": "docs/specs/new.spec.html"}
        (repo / fresh["spec"]).write_text("<title>Alpha fresh</title>\n")
        tool = board | {"id": "spec:ann134:sc::docs/specs/domains/x.spec.html", "project": "sc"}
        tool["path"] = "ann134/sc/" + tool["spec"]
        steady = self.make_resource("ann119") | {"project": "sc"}
        git(steady["root"], "checkout", "--", steady["spec"])
        broken = self.make_resource("ann7") | {"project": "aa"}
        git(broken["root"], "branch", "gone")
        broken["base"] = "gone"
        lost = self.make_resource("ann134b") | {"slug": "ann134", "project": "zz"}
        lost["path"] = "ann134/zz/" + lost["spec"]
        git(lost["root"], "branch", "gone")
        lost["base"] = "gone"
        # #acceptance-index-sections: rows under a slug that is not an issue key settle.
        main = self.make_resource("aa") | {"project": "aa"}
        other = self.make_resource("trunk") | {"slug": "aa", "project": "sc"}
        other["path"] = "aa/sc/" + other["spec"]
        git(other["root"], "checkout", "--", other["spec"])
        self.start([steady, broken, main, board, other, fresh, tool, lost])
        git(broken["root"], "branch", "-D", "gone")
        git(lost["root"], "branch", "-D", "gone")

        status, raw = self.request("/?focus=changes")
        self.assertEqual(status, 200)
        body = raw.decode()
        self.assertIn('"GIT_OPTIONAL_LOCKS": "0"', SERVER.read_text())
        # #index-entry-title: the Changed pill sits on the right of its row.
        self.assertRegex(body, r"\.pill \{[^}]*margin-left: auto;")
        text = html_lib.unescape(re.sub(r"<style>.*?</style>|<[^>]+>", "\n", body, flags=re.S))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertEqual(lines[:2], ["Spec Chat index", "Review index"])
        # #acceptance-index-sections: lane cards sit under In progress; aa rows in one closed Settled.
        self.assertEqual(len(re.findall(r"<details\b", body)), 1)
        self.assertRegex(body, r"<details(?![^>]*\bopen\b)[^>]*>\s*<summary>Settled \(2\)</summary>")
        self.assertEqual(lines[2:], [
            "In progress",
            "ANN-134", "3 of 3 changed",
            "aa", "Alpha fresh", "Changed", "Zeta board", "Changed",
            "sc", "Zeta board", "Changed",
            "zz", "ann134b",
            "ANN-7", "No status", "aa", "ann7",
            "ANN-119", "Up to date", "sc", "ann119",
            "Settled (2)",
            "aa", "aa", "Changed",
            "sc", "trunk",
        ])
        # #index-entry-lane: the stripe is attention when any spec changed, else border.
        cards = re.findall(r'<section class="lane( changed)?"', body)
        self.assertEqual(cards, [" changed", "", ""])
        for leaked in ("docs/specs", "spec:", "main", "gone", "ann134/"):
            self.assertNotIn(leaked, "\n".join(lines))
        self.assertIn('href="/ann134/sc/docs/specs/domains/x.spec.html?focus=changes"', body)
        self.assertIn('href="/ann134/docs/specs/new.spec.html?focus=changes"', body)

    INDEX_TOKENS = {
        "--ui-font": '"Inter Variable", Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
        "--ui-mono": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        "--ui-text-xs": "12px", "--ui-text-sm": "14px", "--ui-text-md": "16px", "--ui-text-lg": "20px",
        "--ui-page": "#ffffff", "--ui-surface": "#ffffff", "--ui-ink": "#333333",
        "--ui-muted": "#525252", "--ui-border": "#dfdfdf",
        "--ui-link": "#333333", "--ui-focus": "#262626", "--ui-action": "#262626",
        "--ui-pass": "#005c32", "--ui-pass-soft": "#e6efea",
        "--ui-fail": "#a5000f", "--ui-fail-soft": "#f6e6e7",
        "--ui-attention": "#b0540e", "--ui-attention-soft": "#faf4ef",
        "--ui-muted-soft": "#eeeeee",
        "--ui-draft": "#b0540e", "--ui-draft-soft": "#faf4ef",
        "--ui-resolved": "#005c32", "--ui-resolved-soft": "#e6efea",
        "--ui-space-1": "4px", "--ui-space-2": "8px", "--ui-space-3": "12px",
        "--ui-space-4": "16px", "--ui-space-5": "24px", "--ui-space-6": "32px",
        "--ui-radius": "8px", "--ui-radius-sm": "6px", "--ui-radius-pill": "999px",
    }

    # lane-hosting #index-token-dark: the dark block overrides these, the spec page chrome's dark values.
    INDEX_DARK = {
        "--ui-page": "#1a1a1a", "--ui-surface": "#242424", "--ui-ink": "#e0e0e0", "--ui-muted": "#999999",
        "--ui-border": "#3a3a3a", "--ui-link": "#e0e0e0", "--ui-focus": "#cccccc", "--ui-action": "#cccccc",
        "--ui-attention": "#e5873a", "--ui-attention-soft": "#2e2218",
        "--ui-draft": "#e5873a", "--ui-draft-soft": "#2e2218",
        "--ui-pass": "#3daa6e", "--ui-pass-soft": "#1a2e22",
        "--ui-resolved": "#3daa6e", "--ui-resolved-soft": "#1a2e22",
        "--ui-fail": "#ffb4ab", "--ui-fail-soft": "#2e1a1c",
    }

    def test_index_css_is_one_ui_tokens_block_and_rules_use_only_its_variables(self):
        """ANN-301 lane-hosting #acceptance-index-design, #acceptance-index-dark, #index-design, #index-tokens."""
        import re

        board = self.make_resource("ann134")
        Path(board["root"], board["spec"]).write_text("<title>Board</title>changed\n")
        self.start([board])
        body = self.request("/")[1].decode()
        css = re.search(r"<style>(.*?)</style>", body, re.S).group(1)
        block = re.search(r"/\* ui tokens \*/\s*:root\s*\{([^}]*)\}", css)
        self.assertIsNotNone(block)
        declared = dict(
            (name, value.strip()) for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block.group(1)))
        self.assertEqual(declared, self.INDEX_TOKENS)
        # The one other :root is the system-appearance override, dark values only (#index-token-dark).
        dark = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root\s*\{([^}]*)\}\s*\}", css)
        self.assertIsNotNone(dark)
        self.assertEqual(len(re.findall(r":root\s*\{", css)), 2)
        self.assertEqual(dict(
            (name, value.strip()) for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", dark.group(1))),
            self.INDEX_DARK)
        self.assertIn("color-scheme: light dark", css)
        rules = css.replace(block.group(0), "").replace(dark.group(0), "")
        self.assertNotRegex(rules, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
        self.assertNotRegex(rules, r"font-family\s*:(?!\s*var\()")
        self.assertNotRegex(rules, r"font-size\s*:(?!\s*var\()")
        for prop, value in re.findall(r"((?:padding|margin|gap|border-radius)[\w-]*)\s*:([^;}]*)", rules):
            self.assertNotRegex(re.sub(r"var\(--[\w-]+\)", "", value), r"\b(?!0\b)\d", prop)
        # Only hairline and ring widths, the 640px breakpoint, and the 72rem width are literal lengths.
        self.assertLessEqual(set(re.findall(r"\b\d+(?:\.\d+)?(?:px|rem)\b", rules)),
                             {"1px", "2px", "640px", "72rem"})
        self.assertLessEqual(set(re.findall(r"var\((--[\w-]+)\)", rules)), set(self.INDEX_TOKENS))
        # One heading; the rest are section labels.
        self.assertEqual(re.findall(r"<h\d\b[^>]*>(.*?)</h\d>", body), ["Review index"])
        self.assertIn("max-width: 72rem", rules)

        def luminance(color):
            channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

        # Text at 4.5:1 and stripes at 3:1, in light and in dark appearance.
        stripes = re.findall(r"([^{}]*)\{[^}]*border-left(?:-color)?\s*:[^;}]*var\((--[\w-]+)\)", rules)
        self.assertEqual([(sel.strip(), token) for sel, token in stripes], [(".lane.changed", "--ui-attention")])
        for scheme, tokens in (("light", self.INDEX_TOKENS), ("dark", {**self.INDEX_TOKENS, **self.INDEX_DARK})):
            def contrast(one, two):
                high, low = sorted((luminance(tokens[one]), luminance(tokens[two])), reverse=True)
                return (high + 0.05) / (low + 0.05)

            for text, ground in (("--ui-ink", "--ui-page"), ("--ui-ink", "--ui-surface"),
                                 ("--ui-muted", "--ui-surface"), ("--ui-link", "--ui-surface"),
                                 ("--ui-attention", "--ui-attention-soft"), ("--ui-draft", "--ui-draft-soft"),
                                 ("--ui-resolved", "--ui-resolved-soft")):
                self.assertGreaterEqual(contrast(text, ground), 4.5, (scheme, text, ground))
            for _, token in stripes:
                self.assertGreaterEqual(contrast(token, "--ui-surface"), 3, (scheme, token))
        # Count pills sit on their status soft color (#index-threads-table).
        self.assertRegex(rules, r"\.tally\.open \{ background: var\(--ui-draft-soft\); color: var\(--ui-draft\); \}")
        self.assertRegex(rules, r"\.tally\.resolved \{ background: var\(--ui-resolved-soft\); color: var\(--ui-resolved\); \}")

    @staticmethod
    def spool_event(resource, actor, stamp, **body):
        review = Path(resource["root"], resource["spec"] + ".review", actor)
        review.mkdir(parents=True, exist_ok=True)
        body = {"actor": actor, "schemaVersion": 1, "createdAt": "2026-01-01T00:00:00Z", **body}
        (review / f"{stamp}-{body['event']}-{body['id']}.json").write_text(json.dumps(body))

    def seed_every_status(self, resource):
        """Draft, handed-off, replied, and resolved threads (event-schema lifecycle)."""
        human = {"browser": "b1", "author": "Mango", "anchorId": "x", "target": None}
        for stamp, ident in ((100, "d"), (101, "p"), (102, "a"), (103, "r")):
            self.spool_event(resource, "human", stamp, id=ident, event="comment", text=ident, **human)
        self.spool_event(resource, "human", 110, id="h", event="handoff", events=["p", "a", "r"], **human)
        self.spool_event(resource, "agent", 120, id="ra", event="reply", respondsTo="a", status="acknowledged", text="ok")
        self.spool_event(resource, "agent", 121, id="rr", event="reply", respondsTo="r", status="acknowledged", text="ok")
        self.spool_event(resource, "human", 130, id="s", event="status", respondsTo="r", threadId="r", status="resolved")

    def test_index_rows_show_open_then_resolved_count_pills(self):
        """ANN-610 lane-hosting #acceptance-index-threads, #acceptance-index-threads-none,
        #acceptance-index-threads-refresh, #index-threads, #index-threads-refresh."""
        import html as html_lib
        import re

        busy = self.make_resource("ann610") | {"project": "sc"}
        self.seed_every_status(busy)
        quiet = busy | {"id": "spec:ann610::docs/specs/quiet.spec.html", "spec": "docs/specs/quiet.spec.html"}
        Path(busy["root"], quiet["spec"]).write_text("<title>Quiet</title>\n")
        one = self.make_resource("ann611") | {"project": "sc"}
        self.spool_event(one, "human", 100, id="o", event="comment", anchorId="x", text="o", browser="b", author="Kiwi")
        self.start([busy, quiet, one])

        def rows():
            response = urllib.request.urlopen(self.url + "/", timeout=2)
            body = response.read().decode()
            self.assertNotIn("<script", body)
            items = re.findall(r"<li>(.*?)</li>", body, re.S)
            text = {}
            for item in items:
                parts = [p.strip() for p in html_lib.unescape(re.sub(r"<[^>]+>", "\n", item)).splitlines() if p.strip()]
                text[parts[0]] = parts[1:]
            return body, response.headers["ETag"], text

        body, etag, text = rows()
        self.assertEqual(text["ann610"], ["Changed", "3", "Open", "1", "Resolved"])
        self.assertEqual(text["Quiet"], ["Changed"])
        self.assertEqual(text["ann611"], ["Changed", "1", "Open"])
        self.assertIn('<div class="tallies"><span class="tally open"><b>3</b> Open</span>'
                      '<span class="tally resolved"><b>1</b> Resolved</span></div>', body)
        self.assertNotRegex(body, r"<b>0</b>")
        # Resolve the one open thread, then reload: one Resolved, no Open, a new ETag.
        self.spool_event(one, "human", 200, id="so", event="status", respondsTo="o", threadId="o", status="resolved")
        _, fresh, text = rows()
        self.assertEqual(text["ann611"], ["Changed", "1", "Resolved"])
        self.assertNotEqual(fresh, etag)

    def test_thread_counts_refold_only_when_a_spool_directory_changes(self):
        """ANN-610 lane-hosting #index-entry-cost: counts kept per spool, keyed by human/ and agent/ mtimes."""
        import importlib.util
        import os
        from unittest import mock

        spec = importlib.util.spec_from_file_location("review_serve_threads", SERVER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mount = self.make_resource("ann612")
        path = str(Path(mount["root"]) / mount["spec"])
        folds = []
        fold = module.spool.fold_threads
        with mock.patch.object(module.spool, "fold_threads", lambda events: folds.append(1) or fold(events)):
            self.assertEqual(module._thread_counts(mount, path), (0, 0))
            self.seed_every_status(mount)
            self.assertEqual(module._thread_counts(mount, path), (3, 1))
            self.assertEqual(module._thread_counts(mount, path), (3, 1))
            self.assertEqual(len(folds), 2)
            agent = Path(path + ".review", "agent")
            info = os.stat(agent)
            os.utime(agent, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            self.assertEqual(module._thread_counts(mount, path), (3, 1))
            self.assertEqual(len(folds), 3)
        # No spool at all: nothing to read, no threads.
        bare = self.make_resource("ann613")
        bare_path = str(Path(bare["root"]) / bare["spec"])
        for actor in ("human", "agent"):
            os.rmdir(bare_path + ".review/" + actor)
        self.assertEqual(module._thread_counts(bare, bare_path), (0, 0))

    def test_index_settles_specs_served_without_a_row_in_a_closed_disclosure(self):
        """ANN-230 lane-hosting #acceptance-index-sections: row-less specs go in one closed Settled (n)."""
        import html as html_lib
        import re

        repo = self.work / "settled"
        specs = repo / "docs/specs"
        (specs / "sub").mkdir(parents=True)
        (specs / "b.spec.html").write_text("<title>Beta</title>")
        (specs / "sub/a.spec.html").write_text("<title>Alpha</title>")
        git(repo, "init", "-b", "main")
        port = self._free_port()
        process = subprocess.Popen((sys.executable, str(SERVER), str(repo / "docs"), str(port)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        for _ in range(200):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.1) as response:
                    body = response.read().decode()
                break
            except Exception:
                time.sleep(0.01)
        else:
            self.fail("review server did not start")
        self.assertEqual(len(re.findall(r"<details\b", body)), 1)
        self.assertRegex(body, r"<details(?![^>]*\bopen\b)[^>]*>\s*<summary>Settled \(2\)</summary>")
        self.assertTrue(body.rstrip().endswith("</details></main></body></html>"))
        text = html_lib.unescape(re.sub(r"<style>.*?</style>|<[^>]+>", "\n", body, flags=re.S))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertEqual(lines, ["Spec Chat index", "Review index", "Settled (2)", "settled", "Alpha", "Beta"])
        for gone in ("In progress", "Other specs", "No status", "Up to date"):
            self.assertNotIn(gone, body)

    def test_status_is_none_when_any_git_call_fails(self):
        """ANN-136 lane-hosting #index-entry-status: no status when the compare fails."""
        import importlib.util
        from unittest import mock

        spec = importlib.util.spec_from_file_location("review_serve_under_test", SERVER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mount = self.make_resource("ann9")
        real = subprocess.run
        self.assertTrue(module._review_status(mount))
        for step in ("^{commit}", "hash-object", ":" + mount["spec"]):
            for error in (subprocess.TimeoutExpired("git", 5), OSError("no git")):
                with self.subTest(step=step, error=type(error).__name__):
                    def run(args, *rest, **options):
                        if any(step in arg for arg in args):
                            raise error
                        return real(args, *rest, **options)
                    with mock.patch.object(module.subprocess, "run", run):
                        self.assertIsNone(module._review_status(mount))
        git(mount["root"], "rm", "-q", "--cached", mount["spec"])
        git(mount["root"], "commit", "-q", "-m", "drop spec")
        self.assertTrue(module._review_status(mount))

    def test_index_row_rereads_only_on_new_file_identity_or_base(self):
        """ANN-181 lane-hosting #index-entry-cost."""
        import importlib.util
        import os
        from unittest import mock

        spec = importlib.util.spec_from_file_location("review_serve_cache", SERVER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mount = self.make_resource("ann181")
        mount["base"] = git(mount["root"], "rev-parse", "HEAD")
        path = str(Path(mount["root"]) / mount["spec"])
        calls = []
        status, title = module._review_status, module._page_title
        with mock.patch.object(module, "_review_status", lambda m: calls.append("git") or status(m)), \
                mock.patch.object(module, "_page_title", lambda p: calls.append("title") or title(p)):
            row = module._index_row(mount, path, True)
            self.assertEqual(row, (True, "ann181"))
            self.assertEqual(module._index_row(mount, path, True), row)
            self.assertEqual(calls, ["git", "title"])
            Path(path).write_text("<title>Renamed</title>x\n")
            self.assertEqual(module._index_row(mount, path, True), (True, "Renamed"))
            self.assertEqual(len(calls), 4)
            head = git(mount["root"], "commit", "-qam", "reviewed") or git(mount["root"], "rev-parse", "HEAD")
            self.assertEqual(module._index_row(mount | {"base": head}, path, True), (False, "Renamed"))
            self.assertEqual(len(calls), 6)
            info = os.stat(path)
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            module._index_row(mount | {"base": head}, path, True)
            self.assertEqual(len(calls), 8)

    def test_index_row_with_ref_base_matches_uncached_after_the_ref_moves(self):
        """ANN-192 lane-hosting #index-entry-cost: a legacy ref row base is compared uncached."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("review_serve_ref", SERVER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        mount = self.make_resource("ann192")
        self.assertEqual(mount["base"], "main")
        path = str(Path(mount["root"]) / mount["spec"])
        self.assertEqual(module._index_row(mount, path, True), (True, "ann192"))
        git(mount["root"], "commit", "-qam", "reviewed on main")
        self.assertIs(module._review_status(mount), False)
        self.assertEqual(module._index_row(mount, path, True), (False, "ann192"))

    def test_invalid_registries_exit_before_binding_or_printing_url(self):
        first, second = (self.make_resource(name) for name in ("first", "second"))
        invalid = [
            [first, second | {"id": first["id"]}],
            [first, first | {"id": "spec:other::" + first["spec"]}],
            [first | {"spec": "docs/missing.spec.html"}],
            [first | {"base": "not-a-ref"}],
            [first | {"base": ""}],
            [first | {"owner": ""}],
            [first | {"cursor_name": ""}],
            [first | {"slug": "api"}],
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
