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
SERVER = ROOT / "skill" / "review-spec" / "assets" / "review-serve.py"


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class BaselineRouteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.docs = self.repo / "docs"
        self.specs = self.docs / "specs"
        self.specs.mkdir(parents=True)
        run("git", "init", "-b", "main", cwd=self.repo)
        run("git", "config", "user.name", "Spec Chat Test", cwd=self.repo)
        run("git", "config", "user.email", "spec-chat@example.test", cwd=self.repo)
        self.spec = self.specs / "focus.spec.html"
        self.spec.write_text('<p data-anchor="rule">baseline rule</p>\n')
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-m", "baseline", cwd=self.repo)
        self.base = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()
        run("git", "switch", "-c", "stack-base", cwd=self.repo)
        self.spec.write_text('<p data-anchor="rule">stacked base rule</p>\n')
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-m", "stack base", cwd=self.repo)
        self.stack_base = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()
        run("git", "switch", "-c", "feature", cwd=self.repo)
        self.spec.write_text('<p data-anchor="rule">changed rule</p>\n<p data-anchor="new">new rule</p>\n')
        (self.specs / "new.spec.html").write_text('<p data-anchor="new-file">new file</p>\n')

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.server = subprocess.Popen(
            (sys.executable, str(SERVER), str(self.docs), str(self.port)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=0.1).close()
                break
            except Exception:
                time.sleep(0.02)
        else:
            self.fail("review server did not start")

    def tearDown(self):
        self.server.terminate()
        self.server.wait(timeout=2)
        self.temp.cleanup()

    def baseline(self, path, base=None):
        params = {"path": path}
        if base is not None:
            params["base"] = base
        query = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/baseline?{query}") as response:
            return json.load(response)

    def test_returns_baseline_html_from_local_merge_base(self):
        result = self.baseline("specs/focus.spec.html")
        self.assertEqual(result["base"], self.base)
        self.assertIn("baseline rule", result["html"])
        self.assertNotIn("changed rule", result["html"])

    def test_new_file_has_no_baseline_html(self):
        result = self.baseline("specs/new.spec.html")
        self.assertEqual(result["base"], self.base)
        self.assertIsNone(result["html"])

    def test_explicit_base_supports_stacked_change_requests(self):
        result = self.baseline("specs/focus.spec.html", "stack-base")
        self.assertEqual(result["base"], self.stack_base)
        self.assertIn("stacked base rule", result["html"])

    def test_missing_explicit_base_fails_visibly(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.baseline("specs/focus.spec.html", "missing-base")
        self.assertEqual(error.exception.code, 409)

    def test_rejects_paths_outside_the_served_collection(self):
        query = urllib.parse.urlencode({"path": "../secret"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/baseline?{query}")
        self.assertEqual(error.exception.code, 400)

    def test_refuses_to_serve_the_repository_root(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(
            (sys.executable, str(SERVER), str(self.repo), str(port)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            output, error = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=2)
            self.fail("review server accepted the repository root")
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("review collection", output + error)

    def test_static_serving_does_not_follow_symlinks_outside_collection(self):
        secret = self.repo / "secret.txt"
        secret.write_text("not public")
        (self.docs / "leak.txt").symlink_to(secret)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/leak.txt")
        self.assertEqual(error.exception.code, 404)

    def test_refuses_to_serve_an_ancestor_of_a_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            ancestor = Path(directory)
            repo = ancestor / "repo"
            repo.mkdir()
            run("git", "init", "-b", "main", cwd=repo)
            (ancestor / "private-notes.txt").write_text("not public")
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = subprocess.Popen(
                (sys.executable, str(SERVER), str(ancestor), str(port)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                output, error = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=2)
                self.fail("review server accepted a repository ancestor")
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("review collection", output + error)

    def test_rejects_malformed_public_event_names(self):
        query = urllib.parse.urlencode({"dir": "specs/focus.spec.html.review", "actor": "human"})
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/events?{query}",
            data=json.dumps({"event": "../escape", "id": "bad/id"}).encode(),
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
