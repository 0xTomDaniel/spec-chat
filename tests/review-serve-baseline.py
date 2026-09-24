import importlib.util
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "skill" / "review-spec" / "assets" / "review-serve.py"
VERIFIER = ROOT / "skill" / "review-spec" / "scripts" / "verify-review.py"
module = importlib.util.spec_from_file_location("verify_review", VERIFIER)
verifier = importlib.util.module_from_spec(module)
module.loader.exec_module(verifier)


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

    def test_service_root_serves_a_responsive_index_with_detail_links(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as response:
            body = response.read().decode()

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get_content_type(), "text/html")
        self.assertIn('<meta name="viewport"', body)
        self.assertIn('<title>Spec Chat index</title>', body)
        self.assertIn('href="specs/focus.spec.html"', body)
        self.assertIn('focus.spec', body)

    def verify(self, path="specs/focus.spec.html", base="main", local_spec=None):
        link_base = subprocess.check_output(("git", "rev-parse", base), cwd=self.repo, text=True).strip()
        return subprocess.run(
            (sys.executable, str(VERIFIER), str(self.repo), str(local_spec or self.docs / path),
             f"http://127.0.0.1:{self.port}/{path}?focus=changes&base={link_base}", base),
            text=True, capture_output=True,
        )

    def test_verifier_checks_served_bytes_and_exact_git_source(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        facts = json.loads(result.stdout)
        self.assertEqual(facts["base"], self.base)
        self.assertEqual(facts["htmlBase"], self.base)
        self.assertEqual(facts["source"], "base")

    def test_verifier_accepts_a_new_file_without_unchanged_blocks(self):
        result = self.verify("specs/new.spec.html")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["source"], "new")

    def test_verifier_rejects_a_different_served_spec(self):
        result = self.verify(local_spec=self.specs / "new.spec.html")
        self.assertEqual(result.returncode, 2)
        self.assertIn("served spec bytes differ", result.stderr)

    def test_verifier_rejects_a_link_with_a_different_baseline(self):
        url = f"http://127.0.0.1:{self.port}/specs/focus.spec.html?focus=changes&base=stack-base"
        with self.assertRaisesRegex(ValueError, "review URL selects a different baseline"):
            verifier.verify(self.repo, self.spec, url, "main")

    def test_verifier_requires_a_pinned_base_in_focused_links(self):
        url = f"http://127.0.0.1:{self.port}/specs/focus.spec.html"
        for query in ("?focus=changes", "?focus=changes&base=main"):
            with self.subTest(query=query):
                with self.assertRaisesRegex(ValueError, "focused review URL"):
                    verifier.verify(self.repo, self.spec, url + query, "main")
        # A normal view can still verify its source and an explicit comparison.
        self.assertEqual(verifier.verify(self.repo, self.spec, url, "main")["source"], "base")

    def test_verifier_rejects_incorrect_baseline_response(self):
        expected = self.baseline("specs/focus.spec.html", "main")
        url = f"http://127.0.0.1:{self.port}/specs/focus.spec.html"
        for override in ({"base": self.stack_base}, {"htmlBase": self.stack_base}, {"html": "wrong snapshot"}):
            with self.subTest(override=override):
                with patch.object(verifier, "read_url", side_effect=[self.spec.read_bytes(), json.dumps(expected | override).encode()]):
                    with self.assertRaises(ValueError):
                        verifier.verify(self.repo, self.spec, url, "main")

    def test_returns_baseline_html_from_local_merge_base(self):
        result = self.baseline("specs/focus.spec.html")
        self.assertEqual(result["base"], self.base)
        self.assertIn("baseline rule", result["html"])
        self.assertNotIn("changed rule", result["html"])

    def test_new_file_has_no_baseline_html(self):
        result = self.baseline("specs/new.spec.html")
        self.assertEqual(result["base"], self.base)
        self.assertIsNone(result["html"])
        self.assertIsNone(result["htmlBase"])

    def test_committed_new_file_uses_seed_when_absent_from_base(self):
        seeded = self.specs / "seeded.spec.html"
        seeded.write_text('<p data-anchor="seed">seeded baseline</p>\n')
        run("git", "add", str(seeded), cwd=self.repo)
        run("git", "commit", "-m", "seed new spec", cwd=self.repo)
        seed = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()
        result = self.baseline("specs/seeded.spec.html", "main")

        self.assertEqual(result["base"], self.base)
        self.assertIn("seeded baseline", result["html"])
        self.assertEqual(result["htmlBase"], seed)

    def test_committed_new_file_baseline_stays_at_seed_after_later_edit(self):
        seeded = self.specs / "stable-seed.spec.html"
        seeded.write_text('<p data-anchor="seed">seed version</p>\n')
        run("git", "add", str(seeded), cwd=self.repo)
        run("git", "commit", "-m", "seed stable spec", cwd=self.repo)
        seed = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()
        seeded.write_text('<p data-anchor="seed">later version</p>\n')
        run("git", "add", str(seeded), cwd=self.repo)
        run("git", "commit", "-m", "edit stable spec", cwd=self.repo)

        result = self.baseline("specs/stable-seed.spec.html", "main")

        self.assertEqual(result["base"], self.base)
        self.assertIn("seed version", result["html"])
        self.assertNotIn("later version", result["html"])
        self.assertEqual(result["htmlBase"], seed)
        checked = self.verify("specs/stable-seed.spec.html")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["source"], "seed")
        self.assertEqual(json.loads(checked.stdout)["htmlBase"], seed)

    def test_explicit_sibling_snapshot_is_not_replaced_by_a_shared_ancestor(self):
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-m", "current feature", cwd=self.repo)
        run("git", "switch", "-c", "reviewed-sibling", "main", cwd=self.repo)
        expected = '<p data-anchor="rule">previously reviewed sibling</p>\n'
        self.spec.write_text(expected)
        run("git", "add", ".", cwd=self.repo)
        run("git", "commit", "-m", "reviewed snapshot", cwd=self.repo)
        sibling = subprocess.check_output(("git", "rev-parse", "HEAD"), cwd=self.repo, text=True).strip()
        run("git", "switch", "feature", cwd=self.repo)

        for ref in (sibling, "reviewed-sibling"):
            with self.subTest(ref=ref):
                result = self.baseline("specs/focus.spec.html", ref)
                self.assertEqual(result["base"], sibling)
                self.assertEqual(result["htmlBase"], sibling)
                self.assertEqual(result["html"], expected)

    def test_explicit_base_supports_stacked_change_requests(self):
        result = self.baseline("specs/focus.spec.html", "stack-base")
        self.assertEqual(result["base"], self.stack_base)
        self.assertIn("stacked base rule", result["html"])
        self.assertEqual(result["htmlBase"], self.stack_base)

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
