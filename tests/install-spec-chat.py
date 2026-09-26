"""Fresh-install test of Spec Chat's onboarding (acceptance-onboarding)."""

import os
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
        self.assertFalse((self.specs / ".viz").exists())
        self.assertTrue(foreign.is_symlink())
        self.assertTrue(self.spec.is_file())
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual(registry.get("resource", []), [])


if __name__ == "__main__":
    unittest.main()
