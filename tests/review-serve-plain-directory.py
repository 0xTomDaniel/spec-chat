import json
import os
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
SERVER = ROOT / "skill" / "review-spec" / "assets" / "review-serve.py"
PAGE = '<!doctype html><title>Plain page</title><script src="./.viz/runtime.js"></script><p data-anchor="a">Plain page</p>\n'


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Served:
    """One review-serve process on a free loopback port."""

    def __init__(self, root, env=None):
        self.port = free_port()
        self.process = subprocess.Popen(
            (sys.executable, str(SERVER), str(root), str(self.port)),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=env,
        )
        for _ in range(250):
            if self.process.poll() is not None:
                raise AssertionError("review server exited: " + self.process.stderr.read())
            try:
                urllib.request.urlopen(self.url("/"), timeout=0.1).close()
                return
            except Exception:
                time.sleep(0.02)
        self.stop()
        raise AssertionError("review server did not start")

    def url(self, route, /, **params):
        return "http://127.0.0.1:%d%s%s" % (self.port, route, ("?" + urllib.parse.urlencode(params)) if params else "")

    def get(self, route, /, **params):
        try:
            with urllib.request.urlopen(self.url(route, **params)) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def post(self, route, body, /, **params):
        request = urllib.request.Request(self.url(route, **params), data=json.dumps(body).encode(), method="POST")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=2)
        self.process.stderr.close()


def refusal(root, env=None):
    process = subprocess.run(
        (sys.executable, str(SERVER), str(root), str(free_port())),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=10,
    )
    return process.returncode, process.stdout + process.stderr


class PlainDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(os.path.realpath(self.temp.name))
        self.home = self.base / "home"
        self.collection = self.home / "notes" / "resume"
        self.collection.mkdir(parents=True)
        (self.collection / "page.html").write_text(PAGE)
        (self.collection / "assets").mkdir()
        (self.collection / "assets" / "style.css").write_text("p { color: black; }\n")
        agent = self.collection / "page.html.review" / "agent"
        agent.mkdir(parents=True)
        (agent / "1-reply-r1.json").write_text(json.dumps({"event": "reply", "id": "r1"}))
        self.env = dict(os.environ, HOME=str(self.home))

    def tearDown(self):
        self.temp.cleanup()

    def serve(self, root=None, env=None):
        server = Served(root or self.collection, env or self.env)
        self.addCleanup(server.stop)
        return server

    def test_plain_directory_serves_pages_and_event_spools(self):
        self.assertNotEqual(subprocess.run(
            ("git", "-C", str(self.collection), "rev-parse", "--show-toplevel"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode, 0)
        server = self.serve()
        status, body = server.get("/page.html")
        self.assertEqual((status, body.decode()), (200, PAGE))
        self.assertEqual(server.get("/assets/style.css")[0], 200)
        self.assertEqual(server.get("/.viz/runtime.js")[0], 200)

        status, body = server.get("/api/events", dir="page.html.review")
        self.assertEqual(status, 200)
        self.assertEqual([event["name"] for event in json.loads(body)], ["1-reply-r1.json"])

        status, posted = server.post("/api/events", {"event": "comment", "id": "c1", "text": "hi"},
                                     dir="page.html.review", actor="human")
        self.assertEqual(status, 200, posted)
        self.assertTrue((self.collection / "page.html.review" / "human" / posted["name"]).is_file())
        status, body = server.get("/api/events", dir="/page.html.review")
        self.assertEqual(sorted(event["actor"] for event in json.loads(body)), ["agent", "human"])

    def test_plain_directory_keeps_spools_and_outside_files_private(self):
        (self.collection / "page.html.review" / "human").mkdir()
        (self.collection.parent / "private.txt").write_text("not public")
        (self.collection / "leak.txt").symlink_to(self.collection.parent / "private.txt")
        server = self.serve()
        self.assertEqual(server.get("/page.html.review/agent/1-reply-r1.json")[0], 404)
        self.assertEqual(server.get("/leak.txt")[0], 404)
        self.assertEqual(server.get("/../private.txt")[0], 404)
        for directory in ("missing.html.review", "../resume/page.html.review", "assets.review", ".viz/runtime.js.review"):
            self.assertEqual(server.get("/api/events", dir=directory)[0], 400, directory)
        status, _ = server.post("/api/events", {"event": "comment", "id": "c1"}, dir="page.html.review", actor="agent")
        self.assertEqual(status, 403)

    def test_plain_directory_reports_git_features_unavailable(self):
        (self.collection / "plain.spec.html").write_text('<p data-anchor="rule">rule</p>\n')
        server = self.serve()
        status, body = server.get("/api/baseline", path="plain.spec.html")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "git baseline unavailable", "reason": "not a Git repository"})
        status, body = server.get("/api/jev", path="plain.spec.html", base="main")
        self.assertEqual((status, json.loads(body)["error"]), (409, "jev unavailable"))
        status, body = server.get("/api/evidence", path="plain.spec.html")
        self.assertEqual((status, json.loads(body)["criteria"]), (200, None))
        status, body = server.get("/")
        self.assertEqual(status, 200)
        self.assertIn("plain.spec.html", body.decode())

    def test_broad_roots_are_refused_without_git(self):
        documents = self.home / "Documents"
        documents.mkdir()
        nested = self.base / "outside"
        (nested / "repo").mkdir(parents=True)
        run("git", "init", "-b", "main", cwd=nested / "repo")
        cases = {
            "filesystem root": Path("/"),
            "home": self.home,
            "home ancestor": self.base,
            "home Documents": documents,
            "git metadata": nested / "repo" / ".git",
            "repository ancestor": nested,
        }
        for label, root in cases.items():
            with self.subTest(label):
                code, output = refusal(root, self.env)
                self.assertNotEqual(code, 0)
                self.assertIn("review collection", output)

    def test_missing_git_binary_serves_plain_and_refuses_repository_root(self):
        repo = self.base / "repo"
        docs = repo / "docs"
        docs.mkdir(parents=True)
        (docs / "page.html").write_text(PAGE)
        run("git", "init", "-b", "main", cwd=repo)
        env = dict(self.env, PATH=str(self.base / "no-bin"))
        code, output = refusal(repo, env)
        self.assertNotEqual(code, 0)
        self.assertIn("review collection", output)
        server = self.serve(docs, env)
        self.assertEqual(server.get("/page.html")[0], 200)
        self.assertEqual(server.post("/api/events", {"event": "comment", "id": "c1"}, dir="page.html.review")[0], 200)


class GitDirectoryTest(unittest.TestCase):
    def test_git_collection_keeps_baseline_and_accepts_page_spools(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(os.path.realpath(directory))
            docs = repo / "docs"
            docs.mkdir()
            (docs / "focus.spec.html").write_text('<p data-anchor="rule">rule</p>\n')
            (docs / "page.html").write_text(PAGE)
            run("git", "init", "-b", "main", cwd=repo)
            run("git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "--allow-empty", "-m", "root", cwd=repo)
            run("git", "add", ".", cwd=repo)
            run("git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-m", "docs", cwd=repo)
            code, output = refusal(repo)
            self.assertNotEqual(code, 0)
            self.assertIn("strictly inside its Git repository", output)
            server = Served(docs)
            try:
                status, body = server.get("/api/baseline", path="focus.spec.html", base="HEAD")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["html"], '<p data-anchor="rule">rule</p>\n')
                for review in ("focus.spec.html.review", "page.html.review"):
                    status, _ = server.post("/api/events", {"event": "comment", "id": "c1"}, dir=review)
                    self.assertEqual(status, 200, review)
                    self.assertEqual(len(json.loads(server.get("/api/events", dir=review)[1])), 1)
                self.assertEqual(server.get("/.git/config")[0], 404)
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
