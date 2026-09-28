"""Fresh-install test of Spec Chat's onboarding (acceptance-onboarding)."""

import os
import socket
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install-spec-chat"
HOST = ROOT / "skill/review-spec/scripts/review-host.py"


def snapshot(*roots):
    """Every path, link target, and file byte under the given roots."""
    result = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_symlink():
                result[str(path)] = "->" + os.readlink(path)
            elif path.is_file() and not path.name.endswith((".log", ".lock")):
                result[str(path)] = path.read_bytes()
    return result


class InstallSpecChatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spec-chat-install-")
        work = Path(self.temp.name)
        self.home, self.state = work / "home", work / "state"
        self.home.mkdir()
        self.repo = work / "example"
        self.specs = self.repo / "docs/specs"
        self.specs.mkdir(parents=True)
        self.spec = self.specs / "first.spec.html"
        self.spec.write_text('<!doctype html><title>first</title><script defer src="./.viz/runtime.js"></script>\n')
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "seed"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True)
        self.env = {"HOME": str(self.home), "XDG_STATE_HOME": str(self.state), "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1"}
        self.onboarding = self.state / "spec-chat/onboarding.toml"

    def tearDown(self):
        subprocess.run(["python3", str(HOST), "stop"], env=self.env, capture_output=True)
        self.temp.cleanup()

    def run_install(self, *args):
        result = subprocess.run([str(INSTALL), *args], env=self.env, text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def status(self):
        return tomllib.loads(self.onboarding.read_text())

    def test_fresh_install_onboards_end_to_end_idempotently_and_undoes_only_its_own(self):
        foreign = self.home / ".claude/skills/other"
        foreign.parent.mkdir(parents=True)
        foreign.symlink_to(self.repo)

        self.run_install()
        for base in (".claude/skills", ".codex/skills"):
            self.assertEqual((self.home / base / "spec-chat-review").resolve(), ROOT / "skill/review-spec")
            self.assertEqual((self.home / base / "spec-chat-shape").resolve(), ROOT / "skill/shape-spec")
            self.assertEqual((self.home / base / "repair-specs").resolve(), ROOT / "skill/repair-specs")
        self.assertEqual(self.status()["status"], "pending")
        self.assertTrue(self.status()["doc"].endswith("README.md#install-and-onboarding"))

        first = self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertIn("review URL: http://127.0.0.1:", first.stdout)
        self.assertTrue((self.specs / ".viz/runtime.js").is_file())
        self.assertTrue((self.specs / ".style/spec.css").is_file())
        self.assertEqual(self.status()["status"], "done")
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual([r["spec"] for r in registry["resource"]], ["docs/specs/first.spec.html"])
        self.assertEqual(registry["resource"][0]["project"], "example")

        before = snapshot(self.home, self.state, self.repo)
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertEqual(snapshot(self.home, self.state, self.repo), before)

        self.run_install("--undo")
        self.assertFalse(self.onboarding.exists())
        for base in (".claude/skills", ".codex/skills"):
            self.assertFalse((self.home / base / "spec-chat-review").is_symlink())
            self.assertFalse((self.home / base / "repair-specs").is_symlink())
        self.assertFalse((self.specs / ".viz").exists())
        self.assertTrue(foreign.is_symlink())
        self.assertTrue(self.spec.is_file())
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual(registry.get("resource", []), [])

    def test_rerun_keeps_the_review_service_warm_up_tables(self):
        self.onboarding.parent.mkdir(parents=True)
        self.onboarding.write_text('status = "pending"\n\n[project.example]\nstate = "done"\nspecs_to_reconcile = 2\n')
        self.run_install()
        self.assertEqual(self.status()["status"], "pending")
        self.assertEqual(self.status()["project"], {"example": {"state": "done", "specs_to_reconcile": 2}})

    def test_box_setup_moves_a_live_private_service_to_public(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.connect(("192.0.2.1", 80))
                proof = probe.getsockname()[0]
            except OSError:
                proof = ""
        if not proof or proof.startswith("127."):
            self.skipTest("no non-loopback address for a public bind")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.env["SPEC_CHAT_APPROVED_INGRESS_PORTS"] = str(port)
        registry = self.state / "spec-chat/hosting/default/registry.toml"
        private = self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertIn("ssh -L", private.stdout)
        before = tomllib.loads(registry.read_text())
        self.assertEqual((before["process"]["bind"], before["process"]["port"]), ("127.0.0.1", port))
        public = self.run_install("--specs", str(self.specs), "--review", str(self.spec),
                                  "--public", "0.0.0.0", "--proof-host", proof)
        self.assertIn(f"review URL: http://{proof}:{port}", public.stdout)
        self.assertIn("has no login", public.stderr)
        after = tomllib.loads(registry.read_text())
        self.assertEqual((after["process"]["bind"], after["process"]["port"]), ("0.0.0.0", port))
        self.assertNotEqual(after["process"]["pid"], before["process"]["pid"])
        stamps = ("registered_at", "updated_at")
        rows = [[{k: v for k, v in r.items() if k not in stamps} for r in d["resource"]] for d in (before, after)]
        self.assertEqual(rows[1], rows[0])


    # --- F-S1: undo stops the review server -----------------------------------

    def test_undo_stops_the_review_server(self):
        """F-S1: --undo stops the review server it started."""
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        registry = tomllib.loads(
            (self.state / "spec-chat/hosting/default/registry.toml").read_text()
        )
        pid = registry["process"]["pid"]
        # server should be alive before undo
        os.kill(pid, 0)
        result = self.run_install("--undo")
        self.assertIn("stopped review server", result.stdout)
        with self.assertRaises(OSError):
            os.kill(pid, 0)

    # --- F-S2: undo leaves git-tracked assets --------------------------------

    def test_undo_leaves_git_tracked_assets(self):
        """F-S2: --undo does not delete .viz/.style when tracked by git."""
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertTrue((self.specs / ".viz").is_dir())
        self.assertTrue((self.specs / ".style").is_dir())
        # Commit the assets so they become git-tracked
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "add runtime"], check=True)
        result = self.run_install("--undo")
        self.assertTrue((self.specs / ".viz").is_dir(), ".viz was deleted despite being git-tracked")
        self.assertTrue((self.specs / ".style").is_dir(), ".style was deleted despite being git-tracked")
        self.assertIn("left git-tracked", result.stdout)


if __name__ == "__main__":
    unittest.main()
