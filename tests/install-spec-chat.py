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
FAKE_SYSTEMD = ROOT / "tests/fixtures/fake-systemd"
UNIT = "spec-chat-review.service"


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
        subprocess.run(["python3", str(HOST), "stop", "--state-dir", str(self.state / "spec-chat/hosting/default")],
                       env=self.env, capture_output=True)
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

    def test_onboarding_holds_status_doc_and_installed_only(self):
        """review-service #published-onboarding: no other table survives a rerun."""
        self.onboarding.parent.mkdir(parents=True)
        self.onboarding.write_text('status = "pending"\n\n[project.example]\nstate = "done"\nspecs_to_reconcile = 2\n')
        self.run_install()
        self.assertEqual(set(self.status()), {"status", "doc", "installed"})
        self.assertEqual(self.status()["status"], "pending")

    def state_files(self):
        return sorted(p.name for p in self.onboarding.parent.iterdir() if p.is_file())

    def test_one_onboarding_file_records_install_rerun_and_undo(self):
        """review-service #acceptance-onboarding-one-file."""
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertEqual(self.state_files(), ["onboarding.toml"])
        installed = self.status()["installed"]
        self.assertEqual(len(installed["links"]), 6)
        self.assertEqual(sorted(Path(c).name for c in installed["copies"]), [".style", ".viz"])
        self.assertEqual(len(installed["resources"]), 1)

        # The rerun reads what was added from the file: it still owns the links it made.
        for item in installed["links"]:
            Path(item).unlink()
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertEqual(self.state_files(), ["onboarding.toml"])
        self.assertEqual(self.status()["installed"], installed)

        self.run_install("--undo")
        for item in installed["links"] + installed["copies"]:
            self.assertFalse(os.path.lexists(item), item)
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual(registry.get("resource", []), [])
        self.assertEqual(self.state_files(), [])

    def test_legacy_install_record_is_folded_in_then_deleted(self):
        legacy = self.state / "spec-chat/install.toml"
        stale = self.home / ".claude/skills/spec-chat-old"
        stale.parent.mkdir(parents=True)
        stale.symlink_to(ROOT / "skill/review-spec")
        legacy.parent.mkdir(parents=True)
        legacy.write_text(f'links = ["{stale}"]\ncopies = []\nresources = []\n')
        self.run_install()
        self.assertFalse(legacy.exists())
        self.assertIn(str(stale), self.status()["installed"]["links"])
        self.run_install("--undo")
        self.assertFalse(stale.is_symlink())
        self.assertEqual(self.state_files(), [])

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


    def test_service_box_setup_runs_a_boot_user_service_on_the_public_base_and_undo_removes_it(self):
        """review-service #service-unit and #acceptance-public-base, through onboarding's box setup."""
        fake = Path(self.temp.name) / "fake-systemd"
        self.env.update({"PATH": f"{FAKE_SYSTEMD}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(self.home / ".config"),
                         "FAKE_SYSTEMD_DIR": str(fake), "USER": "boxuser"})
        unit = self.home / ".config/systemd/user" / UNIT
        base = "https://spec.203-0-113-9.sslip.io"
        result = self.run_install("--specs", str(self.specs), "--review", str(self.spec), "--service",
                                  "--public-base", base)
        self.assertIn(f"review URL: {base}\n", result.stdout)
        text = unit.read_text()
        state = self.state / "spec-chat/hosting/default"
        self.assertRegex(text, rf'\nExecStart="/[^"]*python[^"]*" "{HOST}" "serve" "--state-dir" "{state}"\n')
        for line in ("Restart=always", "WantedBy=default.target", f"WorkingDirectory={ROOT}"):
            self.assertIn(line + "\n", text)
        calls = (fake / "calls").read_text().splitlines()
        for call in ("loginctl enable-linger boxuser", "systemctl --user daemon-reload",
                     f"systemctl --user enable {UNIT}", f"systemctl --user restart {UNIT}"):
            self.assertIn(call, calls)
        process = tomllib.loads((state / "registry.toml").read_text())["process"]
        self.assertEqual((process["unit"], process["url"]), (UNIT, base))
        self.assertEqual(process["pid"], int((fake / f"{UNIT}.pid").read_text()))
        self.assertEqual(self.status()["installed"]["units"], [str(unit)])

        before = snapshot(self.home, self.state, self.repo)
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertEqual(snapshot(self.home, self.state, self.repo), before)

        self.run_install("--undo")
        self.assertIn(f"systemctl --user disable --now {UNIT}", (fake / "calls").read_text().splitlines())
        self.assertFalse(unit.exists())
        self.assertFalse(self.onboarding.exists())
        with self.assertRaises(OSError):
            os.kill(process["pid"], 0)

    def onboard_in_named_session(self, *flags):
        """Onboard a first spec from a pane of a named Herdr session, then undo."""
        self.env.update({"HERDR_SESSION": "lane-a", "HERDR_PANE_ID": "w1:p1",
                         "HERDR_SOCKET_PATH": str(Path(self.temp.name) / "herdr-lane-a.sock")})
        hosting = self.state / "spec-chat/hosting"
        self.run_install("--specs", str(self.specs), "--review", str(self.spec), *flags)
        self.assertEqual(sorted(p.name for p in hosting.iterdir()), ["default"])
        document = tomllib.loads((hosting / "default/registry.toml").read_text())
        self.assertEqual([r["spec"] for r in document["resource"]], ["docs/specs/first.spec.html"])
        os.kill(document["process"]["pid"], 0)
        environ = Path(f"/proc/{document['process']['pid']}/environ").read_bytes().split(b"\0")
        self.run_install("--undo")
        self.assertEqual(sorted(p.name for p in hosting.iterdir()), ["default"])
        self.assertEqual(tomllib.loads((hosting / "default/registry.toml").read_text()).get("resource", []), [])
        with self.assertRaises(OSError):
            os.kill(document["process"]["pid"], 0)
        return document["process"], environ

    def test_onboarding_in_a_named_session_sets_up_the_default_sessions_box_user_service(self):
        """review-service #acceptance-session-onboarding: the box user service runs the default session's service
        and the first spec is registered there; undo removes it there."""
        fake = Path(self.temp.name) / "fake-systemd"
        self.env.update({"PATH": f"{FAKE_SYSTEMD}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(self.home / ".config"),
                         "FAKE_SYSTEMD_DIR": str(fake), "USER": "boxuser"})
        process, _ = self.onboard_in_named_session("--service")
        self.assertEqual(process["unit"], UNIT)
        self.assertIn(f"systemctl --user restart {UNIT}", (fake / "calls").read_text().splitlines())

    def test_onboarding_in_a_named_session_registers_and_stops_the_default_sessions_service(self):
        """review-service #onboarding-entry: whatever session it runs in, onboarding registers on, and undo stops
        and removes from, the default session's service, which runs in the default session (#service-session-ids):
        as a process outside any pane, no HERDR_ variable of the caller reaches it."""
        process, environ = self.onboard_in_named_session()
        self.assertNotIn("unit", process)
        self.assertIn(b"XDG_STATE_HOME=" + str(self.state).encode(), environ)
        self.assertEqual([v for v in environ if v.startswith(b"HERDR_")], [])

    def test_service_flags_are_box_setup(self):
        for flags in (["--service"], ["--public-base", "https://spec.example.invalid"]):
            result = subprocess.run([str(INSTALL), *flags], env=self.env, text=True, capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 1)
            self.assertIn("need --review", result.stderr)

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


    # --- ANN-396: adopt/skip for occupied paths --------------------------------

    def test_adopt_symlink_from_different_clone_with_same_origin(self):
        """link() adopts a symlink resolving into another clone with matching origin."""
        origin = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            text=True, capture_output=True,
        )
        if origin.returncode != 0 or not origin.stdout.strip():
            self.skipTest("ROOT has no origin remote")
        origin_url = origin.stdout.strip()

        # Build a fake second clone: git repo with same origin, skill dirs present
        other = Path(self.temp.name) / "other-clone"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "remote", "add", "origin", origin_url], check=True)
        for d in ("skill/shape-spec", "skill/review-spec", "skill/repair-specs"):
            (other / d).mkdir(parents=True)

        skill_map = {
            "spec-chat-shape": other / "skill/shape-spec",
            "spec-chat-review": other / "skill/review-spec",
            "repair-specs": other / "skill/repair-specs",
        }
        for base in (".claude/skills", ".codex/skills"):
            for name, tgt in skill_map.items():
                p = self.home / base / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(tgt)

        result = self.run_install()
        self.assertIn("adopted:", result.stdout)
        # Symlinks still point to the other clone, not replaced
        for base in (".claude/skills", ".codex/skills"):
            for name, tgt in skill_map.items():
                p = self.home / base / name
                self.assertTrue(p.is_symlink())
                self.assertEqual(p.resolve(), tgt.resolve())
        # The install record has no links (all adopted, none created)
        self.assertEqual(self.status()["installed"]["links"], [])

    def test_skip_occupied_path_and_continue(self):
        """link() skips occupied non-clone paths and the installer continues."""
        for base in (".claude/skills", ".codex/skills"):
            for name in ("spec-chat-shape", "spec-chat-review", "repair-specs"):
                p = self.home / base / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.mkdir()  # a directory, not a symlink

        result = self.run_install()
        self.assertEqual(result.stdout.count("skipped:"), 6)
        self.assertIn("(occupied by directory)", result.stdout)
        # Onboarding still wrote its status (installer continued)
        self.assertEqual(self.status()["status"], "pending")

    def test_skip_symlink_to_unrelated_target(self):
        """link() skips a symlink whose target has a different origin."""
        unrelated = Path(self.temp.name) / "unrelated"
        unrelated.mkdir()
        subprocess.run(["git", "init", "-q", str(unrelated)], check=True)
        subprocess.run(["git", "-C", str(unrelated), "remote", "add", "origin",
                         "https://example.invalid/other.git"], check=True)

        for base in (".claude/skills", ".codex/skills"):
            p = self.home / base / "spec-chat-shape"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.symlink_to(unrelated)

        result = self.run_install()
        self.assertIn("skipped:", result.stdout)
        # The other skills should still be linked normally
        for base in (".claude/skills", ".codex/skills"):
            self.assertEqual(
                (self.home / base / "spec-chat-review").resolve(),
                ROOT / "skill/review-spec",
            )

    def test_onboarding_doc_says_how_to_write_a_project_rule(self):
        """project-rules #acceptance-onboarding-doc: one onboarding section says how to declare a project rule: mark
        its criterion in its home spec with a short name, and say in its text that it applies to every feature."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count("\n## Writing a project rule\n"), 1)
        section = readme.split("\n## Writing a project rule\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("mark its criterion in its home spec with a short name", section)
        self.assertIn("say in its text that it applies to every feature", section)
        self.assertIn('data-project-rule="', section)


class CiTest(unittest.TestCase):
    def test_ci_runs_the_full_suite_alone_with_one_contract_test_per_provider(self):
        """review-service #acceptance-ci-alone: every push and pull request runs every test, this fresh-install test
        among them, under a fresh HOME with no XDG state or config, so no other box tool and no provider file; each
        provider has its file present / absent contract test."""
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn("on: [push, pull_request]", workflow)
        self.assertIn("for f in tests/*.py tests/*.sh tests/*.mjs; do", workflow)
        self.assertIn('export HOME="$RUNNER_TEMP/home"', workflow)
        self.assertIn("unset XDG_STATE_HOME XDG_CONFIG_HOME", workflow)
        for test, name in (("review-serve-evidence.py", "def test_provider_plugs_and_unplugs_live("),
                           ("review-serve-evidence.py", "def test_jev_key_is_read_at_each_use_and_only_from_a_private_file("),
                           ("review-host-wake.py", "def test_without_provider_nothing_is_sent_and_plugging_needs_no_restart(")):
            self.assertIn(name, (ROOT / "tests" / test).read_text(encoding="utf-8"), test)


if __name__ == "__main__":
    unittest.main()
