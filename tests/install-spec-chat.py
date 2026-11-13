"""Fresh-install test of Spec Chat's onboarding (acceptance-onboarding)."""

import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts/install-spec-chat"
HOST = ROOT / "skill/review-spec/scripts/review-host.py"
FAKE_SYSTEMD = ROOT / "tests/fixtures/fake-systemd"
UNIT = "spec-chat-review.service"
SKILL_NAMES = ("spec-chat-shape", "spec-chat-review", "repair-specs")
BASES = (".agents/skills", ".claude/skills", ".codex/skills")


SERVER = "skill/review-spec/assets/review-serve.py"
# A pull that changes what the status route answers, so an answer shows which code the service runs.
STATUS_LINE = '"uptime_seconds": int(time.monotonic() - self.server.started),'
PULLED = (STATUS_LINE, STATUS_LINE + ' "pulled": True,')
# A pull whose server cannot start: the service does not come back after its restart.
MAIN_LINE = "def main(argv=None):\n"
BROKEN = (MAIN_LINE, MAIN_LINE + '    raise SystemExit("pulled code cannot start")\n')
# A pull whose server cannot start as the default session's service alone.
DEFAULT_BROKEN = (MAIN_LINE, MAIN_LINE + '    if "/hosting/default/" in " ".join(sys.argv):\n'
                             '        raise SystemExit("pulled code cannot start here")\n')


def status(process):
    """The service's status route on its own listener, or None when nothing answers there."""
    try:
        with urllib.request.urlopen(f"http://{process['host']}:{process['port']}/api/status", timeout=4) as response:
            return json.loads(response.read())
    except OSError:
        return None


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
        for state in sorted((self.state / "spec-chat/hosting").glob("*")) or [self.state / "spec-chat/hosting/default"]:
            subprocess.run(["python3", str(HOST), "stop", "--state-dir", str(state)], env=self.env, capture_output=True)
        self.temp.cleanup()

    def run_install(self, *args, cwd=None, code=0):
        result = subprocess.run([str(INSTALL), *args], env=self.env, cwd=cwd or self.repo, text=True,
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def status(self):
        return tomllib.loads(self.onboarding.read_text())

    def test_fresh_install_onboards_end_to_end_idempotently_and_undoes_only_its_own(self):
        foreign = self.repo / ".claude/skills/other"
        foreign.parent.mkdir(parents=True)
        foreign.symlink_to(self.repo)

        self.run_install()
        for base in BASES:
            self.assertEqual((self.repo / base / "spec-chat-review").resolve(), ROOT / "skill/review-spec")
            self.assertEqual((self.repo / base / "spec-chat-shape").resolve(), ROOT / "skill/shape-spec")
            self.assertEqual((self.repo / base / "repair-specs").resolve(), ROOT / "skill/repair-specs")
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
        for base in BASES:
            self.assertFalse((self.repo / base / "spec-chat-review").is_symlink())
            self.assertFalse((self.repo / base / "repair-specs").is_symlink())
        self.assertFalse((self.specs / ".viz").exists())
        self.assertTrue(foreign.is_symlink())
        self.assertTrue(self.spec.is_file())
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual(registry.get("resource", []), [])

    def test_onboarding_holds_status_doc_after_pull_check_commit_and_installed_only(self):
        """review-service #published-onboarding, #acceptance-commit-published: no other table survives a rerun;
        after_pull and check are this clone's steps, and commit the clone's commit as its installer ran; none from a
        clone whose commit cannot be read."""
        self.onboarding.parent.mkdir(parents=True)
        self.onboarding.write_text('status = "pending"\n\n[project.example]\nstate = "done"\nspecs_to_reconcile = 2\n')
        self.run_install()
        self.assertEqual(set(self.status()), {"status", "doc", "after_pull", "check", "commit", "installed"})
        self.assertEqual(self.status()["status"], "pending")
        self.assertEqual(self.status()["after_pull"], [str(INSTALL), "--after-pull"])
        self.assertEqual(self.status()["check"], [str(INSTALL), "--check"])
        self.assertEqual(self.status()["commit"], self.head(ROOT))

        self.run_install("--undo")
        outside = self.copy_clone(Path(self.temp.name) / "outside-git")
        self.assertNotEqual(subprocess.run(["git", "-C", str(outside), "rev-parse", "HEAD"], capture_output=True)
                            .returncode, 0)
        result = subprocess.run([str(outside / "scripts/install-spec-chat")], env=self.env, cwd=self.repo, text=True,
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(set(self.status()), {"status", "doc", "after_pull", "check", "installed"})

    def without_git(self):
        """PATH holding python3 alone, so no git can run."""
        bare = Path(self.temp.name) / "python-only"
        bare.mkdir()
        (bare / "python3").symlink_to(Path(sys.executable).resolve())
        self.env["PATH"] = str(bare)

    def test_no_git_publishes_no_commit_and_every_step_still_runs(self):
        """review-service #published-onboarding: no commit when the clone's commit cannot be read, here with no git on
        PATH; the install still records what it added, so its after-pull step, check, and undo run on that record."""
        self.without_git()
        self.run_install("--prefix", str(self.repo))
        self.assertEqual(set(self.status()), {"status", "doc", "after_pull", "check", "installed"})
        self.assertEqual(len(self.status()["installed"]["links"]), len(SKILL_NAMES) * len(BASES))
        self.run_after_pull()
        self.assertEqual(set(self.status()), {"status", "doc", "after_pull", "check", "installed"})
        self.assertEqual(self.run_check(0), [])
        self.run_install("--undo")
        self.assertFalse(self.onboarding.exists())
        for base in BASES:
            for name in SKILL_NAMES:
                self.assertFalse((self.repo / base / name).is_symlink())

    def test_no_git_installs_into_the_directory_and_check_names_a_link_leading_elsewhere_with_its_fix(self):
        """review-service #onboarding-entry, #service-check: with no git to run, an install with no --prefix falls back
        to the directory it runs from, skips an occupied place, and writes its onboarding file; the check names a skill
        link leading elsewhere and the fix that links it again, never a traceback."""
        self.without_git()
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.mkdir()
        occupied = self.repo / ".claude/skills/spec-chat-shape"
        occupied.parent.mkdir(parents=True)
        occupied.symlink_to(elsewhere)
        result = self.run_install()
        self.assertIn(f"skipped: {occupied} (occupied by {elsewhere})", result.stdout)
        self.assertEqual(self.status()["status"], "pending")
        self.assertEqual(len(self.status()["installed"]["links"]), len(SKILL_NAMES) * len(BASES) - 1)
        for name, skill in (("spec-chat-shape", "shape-spec"), ("spec-chat-review", "review-spec"),
                            ("repair-specs", "repair-specs")):
            self.assertEqual((self.repo / ".agents/skills" / name).resolve(), ROOT / "skill" / skill)
        self.assertEqual(self.run_check(0), [])

        review = self.repo / ".codex/skills/spec-chat-review"
        review.unlink()
        review.symlink_to(ROOT / "skill/shape-spec")
        lines = self.run_check(1)
        self.assertEqual(lines, [
            f"Spec Chat skill spec-chat-review is not linked in {self.repo} (.codex/skills); fix: remove what holds its "
            f"place, {review}, then run {INSTALL} --prefix {self.repo}"])
        self.apply_fix(lines[0])
        self.assertEqual(self.run_check(0), [])
        self.assertEqual(review.resolve(), ROOT / "skill/review-spec")

    # --- review-service #service-after-pull ---------------------------------------

    @staticmethod
    def copy_clone(dest):
        """This clone's installer and skills copied to dest, outside Git."""
        for part in ("scripts", "skill"):
            shutil.copytree(ROOT / part, dest / part, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.review"))
        return dest

    @staticmethod
    def head(clone):
        return subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True, capture_output=True,
                              check=True).stdout.strip()

    def clone(self):
        """A Spec Chat clone of an upstream it can fast-forward from: (upstream, clone)."""
        work = Path(self.temp.name)
        upstream, clone = self.copy_clone(work / "upstream"), work / "clone"
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
                     ["config", "user.name", "t"], ["add", "."], ["commit", "-qm", "seed"]):
            subprocess.run(["git", "-C", str(upstream), *args], check=True)
        subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
        return upstream, clone

    def pull(self, upstream, clone, edit):
        """Commit an edit of the server upstream, then fast-forward the clone onto it."""
        server = upstream / SERVER
        text = server.read_text()
        self.assertEqual(text.count(edit[0]), 1)
        server.write_text(text.replace(edit[0], edit[1]))
        subprocess.run(["git", "-C", str(upstream), "commit", "-qam", "pull"], check=True)
        subprocess.run(["git", "-C", str(clone), "pull", "-q", "--ff-only"], check=True)

    def use_fake_systemd(self):
        fake = Path(self.temp.name) / "fake-systemd"
        self.env.update({"PATH": f"{FAKE_SYSTEMD}:/usr/bin:/bin", "XDG_CONFIG_HOME": str(self.home / ".config"),
                         "FAKE_SYSTEMD_DIR": str(fake), "USER": "boxuser"})
        return fake

    def install_from(self, clone, *flags):
        """Onboard the first spec from the clone's installer; return the running service's [process]."""
        result = subprocess.run([str(clone / "scripts/install-spec-chat"), "--specs", str(self.specs), "--review",
                                 str(self.spec), *flags], env=self.env, cwd=self.repo, text=True,
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status()["after_pull"], [str(clone / "scripts/install-spec-chat"), "--after-pull"])
        self.assertEqual(self.status()["check"], [str(clone / "scripts/install-spec-chat"), "--check"])
        return self.process()

    def process(self, session="default"):
        return tomllib.loads((self.state / "spec-chat/hosting" / session / "registry.toml").read_text()).get("process")

    def register_in_session(self, clone, session, *flags):
        """Register the spec with the clone's review-host from a pane of a named Herdr session, which starts that
        session's own service; return its [process]."""
        env = {**self.env, "HERDR_SESSION": session, "HERDR_PANE_ID": "w1:p1",
               "HERDR_SOCKET_PATH": str(Path(self.temp.name) / f"herdr-{session}.sock")}
        result = subprocess.run(["python3", str(clone / "skill/review-spec/scripts/review-host.py"), "register",
                                 "--slug", session, "--owner", "w1:p1", *flags, str(self.spec)], env=env,
                                cwd=self.repo, text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.process(session)

    def run_after_pull(self, code=0):
        """Run the published after_pull step as a host does: its argv with no shell, from outside any repo."""
        began = time.monotonic()
        result = subprocess.run(self.status()["after_pull"], env=self.env, cwd=self.home, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result, began, time.monotonic() - began

    def assert_runs_pulled_code(self, before, result, began, ran, session="default"):
        """The session's service restarted onto the pulled code: a new process on the recorded setup whose status route
        answers the pulled code with an uptime shorter than the step has run."""
        after = self.process(session)
        self.assertNotEqual(after["pid"], before["pid"])
        self.assertEqual({k: after.get(k) for k in ("port", "bind", "host", "url", "unit")},
                         {k: before.get(k) for k in ("port", "bind", "host", "url", "unit")})
        with self.assertRaises(OSError):
            os.kill(before["pid"], 0)
        reported = int(self.said(result, session).rsplit("status route answers, up ", 1)[1].split("s", 1)[0])
        self.assertLess(reported, ran)
        answer = status(after)
        self.assertIs(answer.get("pulled"), True)
        self.assertLess(answer["uptime_seconds"], time.monotonic() - began)

    @staticmethod
    def said(result, session):
        """What the step printed for a session's service: its lines under that session's heading."""
        return result.stdout.split(f"session {session}:\n", 1)[1].split("\nsession ", 1)[0]

    def test_after_pull_restarts_the_unit_service_onto_the_pulled_code(self):
        """review-service #acceptance-after-pull."""
        fake = self.use_fake_systemd()
        upstream, clone = self.clone()
        before = self.install_from(clone, "--service")
        self.assertEqual(before["unit"], UNIT)
        self.assertNotIn("pulled", status(before))
        self.pull(upstream, clone, PULLED)
        calls = len((fake / "calls").read_text().splitlines())
        result, began, ran = self.run_after_pull()
        self.assertEqual((fake / "calls").read_text().splitlines()[calls:], [f"systemctl --user cat {UNIT}",
                                                                             f"systemctl --user restart {UNIT}"])
        self.assertEqual(self.process()["pid"], int((fake / f"{UNIT}.pid").read_text()))
        self.assert_runs_pulled_code(before, result, began, ran)

    def test_after_pull_restarts_an_own_service_by_a_clean_stop_and_a_start_on_its_recorded_setup(self):
        """review-service #service-after-pull: with no unit recorded."""
        upstream, clone = self.clone()
        before = self.install_from(clone)
        self.assertNotIn("unit", before)
        self.pull(upstream, clone, PULLED)
        result, began, ran = self.run_after_pull()
        self.assert_runs_pulled_code(before, result, began, ran)
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual([r["spec"] for r in registry["resource"]], ["docs/specs/first.spec.html"])

    LINGER = f"loginctl show-user {os.getuid()} --property=Linger --value"

    def after_pull_calls(self, lingers):
        """With no unit recorded and the box's user service manager lingering or not, the after-pull step restarts the
        service onto the pulled code: the clone and the service manager calls the step made."""
        fake = self.use_fake_systemd()
        self.env["FAKE_SYSTEMD_LINGER"] = lingers
        upstream, clone = self.clone()
        before = self.install_from(clone)
        self.assertNotIn("unit", before)
        self.pull(upstream, clone, PULLED)
        calls = len((fake / "calls").read_text().splitlines()) if (fake / "calls").exists() else 0
        result, began, ran = self.run_after_pull()
        self.assert_runs_pulled_code(before, result, began, ran)
        return clone, (fake / "calls").read_text().splitlines()[calls:]

    def test_after_pull_starts_an_own_service_in_a_transient_user_unit_where_the_box_manager_lingers(self):
        """review-service #service-after-pull: with no unit recorded, the restarted service outlives the step and
        whatever ran it: where the box's user service manager lingers, it starts in a transient unit of that manager,
        outside the caller's cgroup."""
        clone, made = self.after_pull_calls("yes")
        self.assertEqual(made[:2], [self.LINGER, "systemctl --user show-environment"])
        self.assertTrue(made[2].startswith("systemd-run --user --scope --quiet --collect "), made)
        self.assertIn(str((clone / SERVER).resolve()), made[2])

    def test_after_pull_starts_an_own_service_as_before_where_the_box_manager_does_not_linger(self):
        """review-service #service-after-pull: without linger, logind ends the user service manager, and any unit of
        it, at the user's last logout, so the restarted service starts as before, in the caller's session."""
        _, made = self.after_pull_calls("no")
        self.assertEqual(made, [self.LINGER])

    def test_after_pull_restarts_a_named_sessions_service_onto_the_pulled_code_in_its_own_session(self):
        """review-service #acceptance-after-pull-session: beside the default session's service, a named session's live
        service restarts onto the pulled code, each in its own session (#service-session-ids): the restarted server's
        HERDR_SESSION names its session and no other HERDR_ variable of the host's reaches it, so its wakes stay
        there; a named session's stopped service stays stopped (#acceptance-after-pull-stopped)."""
        upstream, clone = self.clone()
        default = self.install_from(clone)
        lane = self.register_in_session(clone, "lane-a")
        stopped = self.register_in_session(clone, "lane-b")
        lane_b = self.state / "spec-chat/hosting/lane-b"
        stop = subprocess.run(["python3", str(HOST), "stop", "--state-dir", str(lane_b)], env=self.env,
                              capture_output=True, text=True)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(len({default["port"], lane["port"], stopped["port"]}), 3)
        self.pull(upstream, clone, PULLED)
        # The host runs the step from a pane of yet another session.
        self.env.update({"HERDR_SESSION": "host", "HERDR_PANE_ID": "w9:p9",
                         "HERDR_SOCKET_PATH": str(Path(self.temp.name) / "herdr-host.sock")})
        result, began, ran = self.run_after_pull()
        self.assert_runs_pulled_code(lane, result, began, ran, "lane-a")
        self.assert_runs_pulled_code(default, result, began, ran)
        for session, key in (("default", None), ("lane-a", b"lane-a")):
            environ = Path(f"/proc/{self.process(session)['pid']}/environ").read_bytes().split(b"\0")
            self.assertEqual([v for v in environ if v.startswith(b"HERDR_")],
                             [b"HERDR_SESSION=" + key] if key else [], session)
        self.assertIn("stays stopped", self.said(result, "lane-b"))
        self.assertEqual(self.process("lane-b"), stopped)
        self.assertIsNone(status(stopped))
        self.assertEqual(sorted(p.name for p in (self.state / "spec-chat/hosting").iterdir()),
                         ["default", "lane-a", "lane-b"])

    def test_after_pull_restarts_every_session_and_ends_naming_why_one_did_not_answer(self):
        """review-service #service-after-pull: a session whose service does not come back leaves no other on older
        code, and the step's last error line names that session and why, after a later session's own error output."""
        upstream, clone = self.clone()
        default = self.install_from(clone)
        # A later session fronted by an HTTPS public base, whose every start warns on the error output.
        lane = self.register_in_session(clone, "lane-a", "--public-base", "https://spec.example.invalid")
        self.pull(upstream, clone, PULLED)
        self.pull(upstream, clone, DEFAULT_BROKEN)
        result, began, ran = self.run_after_pull(code=1)
        self.assert_runs_pulled_code(lane, result, began, ran, "lane-a")
        self.assertIsNone(status(default))
        lines = result.stderr.strip().splitlines()
        self.assertTrue(lines[-1].startswith("session default: review-host: error: the review service did not answer "
                                             "on this clone's code: "), lines[-1])
        self.assertIn("review server exited before printing its URL", lines[-1])
        self.assertTrue(any(line.startswith("warning: public review page at https://spec.example.invalid")
                            for line in lines[:-1]), result.stderr)
        self.assertEqual(result.stderr.count("review-host: error: "), 1, result.stderr)

    def test_after_pull_publishes_the_setup_check_keeping_status_and_installed(self):
        """review-service #acceptance-after-pull-publishes: an onboarding file an earlier installer wrote, without the
        setup check, holds it after the step, its status and [installed] record reading as they did; pending, and done
        with a service the step restarts."""
        for flags in ((), ("--specs", str(self.specs), "--review", str(self.spec))):
            self.run_install(*flags)
            written = self.onboarding.read_text()
            older = written.replace(f'check = ["{INSTALL}", "--check"]\n', "")
            self.assertNotEqual(older, written)
            self.onboarding.write_text(older)
            before = self.status()
            self.run_after_pull()
            after = self.status()
            self.assertEqual(after["check"], [str(INSTALL), "--check"])
            self.assertEqual((after["status"], after["installed"]), (before["status"], before["installed"]))
            self.assertEqual(self.onboarding.read_text(), written)
        self.assertEqual(after["status"], "done")

    def pulled_since_install(self):
        """A light install from a clone, then the clone fast-forwarded with no after-pull step run: (clone, the commit
        it was installed at)."""
        upstream, clone = self.clone()
        result = subprocess.run([str(clone / "scripts/install-spec-chat")], env=self.env, cwd=self.repo, text=True,
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = self.head(clone)
        self.assertEqual(self.status()["commit"], installed)
        self.pull(upstream, clone, PULLED)
        self.assertNotEqual(self.head(clone), installed)
        return clone, installed

    def test_after_pull_publishes_the_pulled_commit_keeping_status_and_installed(self):
        """review-service #acceptance-after-pull-commit."""
        clone, _ = self.pulled_since_install()
        before = self.status()
        self.run_after_pull()
        after = self.status()
        self.assertEqual(after["commit"], self.head(clone))
        self.assertEqual((after["status"], after["installed"]), (before["status"], before["installed"]))

    def test_after_pull_ends_naming_an_onboarding_file_it_cannot_write_and_restarts_every_session(self):
        """review-service #service-after-pull: the last error line names why; the file stays as it was. A record that
        does not parse, and an onboarding file that parses with its [installed] record of another shape."""
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        written = self.onboarding.read_text()
        legacy = self.state / "spec-chat/install.toml"
        wrong = written.split("\n[installed]\n", 1)[0] + "\ninstalled = []\n"
        for broken, text in ((legacy, "links = [\n"), (self.onboarding, wrong)):
            before = self.process()
            broken.write_text(text)
            result, began, _ = self.run_after_pull(code=1)
            self.assertNotIn("Traceback", result.stderr)
            last = result.stderr.strip().splitlines()[-1]
            self.assertTrue(last.startswith(f"onboarding file not written: {broken} cannot be read ("), last)
            if broken == self.onboarding:
                self.assertIn("install record", last)
            self.assertEqual(broken.read_text(), text)
            self.assertNotEqual(self.process()["pid"], before["pid"])
            self.assertLess(status(self.process())["uptime_seconds"], time.monotonic() - began)
            if broken == legacy:
                legacy.unlink()
                self.assertEqual(self.onboarding.read_text(), written)

    def test_after_pull_and_check_take_no_other_flag(self):
        """review-service #service-after-pull, #service-check: each step names no host and asks nothing, so a flag
        beside it is refused, never accepted and ignored."""
        self.run_install()
        before = snapshot(self.home, self.state, self.repo)
        for step in ("--after-pull", "--check"):
            for flags in (["--specs", str(self.specs)], ["--review", str(self.spec)], ["--service"], ["--private"],
                          ["--public-base", ""], ["--prefix", str(self.repo)], ["--agents-only"], ["--undo"]):
                result = self.run_install(step, *flags, code=2)
                self.assertIn(f"{step} takes no other flag", result.stderr)
        self.assertIn("takes no other flag", self.run_install("--check", "--after-pull", code=2).stderr)
        self.assertEqual(snapshot(self.home, self.state, self.repo), before)

    def test_after_pull_leaves_a_stopped_service_stopped(self):
        """review-service #acceptance-after-pull-stopped: stopped under its unit, and never started at all."""
        fake = self.use_fake_systemd()
        self.run_install()
        result, _, _ = self.run_after_pull()
        self.assertIn("stays stopped", result.stdout)
        self.assertFalse((fake / "calls").exists())

        before = self.install_from(ROOT, "--service")
        stopped = subprocess.run([str(HOST), "stop", "--state-dir", str(self.state / "spec-chat/hosting/default")],
                                 env=self.env, capture_output=True, text=True)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        calls = (fake / "calls").read_text().splitlines()
        self.assertEqual(calls[-1], f"systemctl --user stop {UNIT}")
        result, _, _ = self.run_after_pull()
        self.assertIn("stays stopped", result.stdout)
        self.assertEqual((fake / "calls").read_text().splitlines(), calls)
        self.assertFalse((fake / f"{UNIT}.pid").exists())
        self.assertEqual(self.process(), before)
        self.assertIsNone(status(before))

    def assert_fails_naming_why(self, *flags):
        """review-service #acceptance-after-pull-no-answer: the service runs, and the pulled code cannot start."""
        self.use_fake_systemd()
        upstream, clone = self.clone()
        before = self.install_from(clone, *flags)
        self.pull(upstream, clone, BROKEN)
        result, _, _ = self.run_after_pull(code=1)
        last = result.stderr.strip().splitlines()[-1]
        self.assertTrue(last.startswith("session default: review-host: error: the review service did not answer on "
                                        "this clone's code: "), last)
        self.assertEqual(result.stderr.count("review-host: error: "), 1, result.stderr)
        self.assertIsNone(status(before))
        with self.assertRaises(OSError):
            os.kill(before["pid"], 0)
        return last

    def test_after_pull_fails_naming_why_when_the_unit_service_does_not_come_back(self):
        last = self.assert_fails_naming_why("--service")
        self.assertIn(f"{UNIT} did not start", last)

    def test_after_pull_fails_naming_systemctls_own_error_when_the_unit_does_not_restart(self):
        """review-service #service-after-pull: the last error line names why, in systemctl's own words."""
        fake = self.use_fake_systemd()
        before = self.install_from(ROOT, "--service")
        (fake / f"{UNIT}.failing").touch()
        result, _, _ = self.run_after_pull(code=1)
        self.assertEqual(result.stderr.strip().splitlines()[-1],
                         f"session default: review-host: error: the review service did not answer on this clone's "
                         f"code: systemctl --user restart {UNIT} failed: Job for {UNIT} failed because the control "
                         f'process exited with error code. See "systemctl --user status {UNIT}" and '
                         f'"journalctl --user -xeu {UNIT}" for details.')
        (fake / f"{UNIT}.failing").unlink()
        self.assertEqual(self.process()["pid"], before["pid"])

    def test_after_pull_fails_naming_why_when_an_own_service_does_not_come_back(self):
        last = self.assert_fails_naming_why()
        self.assertIn("review server exited before printing its URL", last)

    # --- review-service #service-check ---------------------------------------------

    def run_check(self, code, fake=None, argv=None):
        """Run the published check as a host does: its argv with no shell and no input, from outside any repo, twice,
        each reporting the same and changing nothing on the box; its error lines."""
        calls = fake / "calls" if fake else None
        made = len(calls.read_text().splitlines()) if calls and calls.exists() else 0
        before = snapshot(self.home, self.state, self.repo)
        paths = sorted(self.state.rglob("*"))  # no lock file either
        results = [subprocess.run(argv or self.status()["check"], env=self.env, cwd=self.home, stdin=subprocess.DEVNULL,
                                  text=True, capture_output=True, timeout=30) for _ in range(2)]
        self.assertEqual(snapshot(self.home, self.state, self.repo), before)
        self.assertEqual(sorted(self.state.rglob("*")), paths)
        self.assertEqual(results[0].stderr, results[1].stderr)
        for result in results:
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        if calls and calls.exists():  # it only asks the service manager, starting, stopping, and enabling nothing
            self.assertLessEqual(set(calls.read_text().splitlines()[made:]),
                                 {f"systemctl --user is-enabled {UNIT}", "systemctl --user show-environment"})
        return results[0].stderr.splitlines()

    def test_check_finds_no_problem_on_a_box_set_up_in_full(self):
        """review-service #acceptance-check-clean: the skills linked, the onboarding file this clone's installer
        writes, the box user service enabled, and the review service running its clone's code; also on the light path,
        with no service."""
        self.run_install()
        self.assertEqual(self.run_check(0), [])
        self.run_install("--undo")
        fake = self.use_fake_systemd()
        _, clone = self.clone()
        process = self.install_from(clone, "--service")
        self.assertEqual(process["unit"], UNIT)
        head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True, capture_output=True)
        self.assertEqual(process["commit"], head.stdout.strip())
        self.assertEqual(self.run_check(0, fake), [])
        self.assertEqual(self.process(), process)
        os.kill(process["pid"], 0)

    def test_check_reports_a_lost_skill_link_naming_the_installer_run_that_links_it_again(self):
        """review-service #acceptance-check-skill: the link stays missing. A link adopted into another clone of this
        repository is no problem; one leading elsewhere is."""
        self.run_install("--agents-only")
        link = self.repo / ".agents/skills/spec-chat-review"
        link.unlink()
        self.assertEqual(self.run_check(1), [
            f"Spec Chat skill spec-chat-review is not linked in {self.repo} (.agents/skills); fix: run {INSTALL} "
            f"--prefix {self.repo} --agents-only"])
        self.assertFalse(os.path.lexists(link))

        self.run_install()
        (self.repo / ".claude/skills/spec-chat-shape").unlink()
        (self.repo / ".claude/skills/spec-chat-shape").symlink_to(ROOT / "skill/review-spec")
        (self.repo / ".agents/skills/repair-specs").unlink()
        self.assertEqual(self.run_check(1), [
            f"Spec Chat skill spec-chat-shape is not linked in {self.repo} (.claude/skills); fix: remove what holds its "
            f"place, {self.repo}/.claude/skills/spec-chat-shape, then run {INSTALL} --prefix {self.repo}",
            f"Spec Chat skill repair-specs is not linked in {self.repo} (.agents/skills, .claude/skills, "
            f".codex/skills); fix: run {INSTALL} --prefix {self.repo}"])

        origin = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], text=True, capture_output=True)
        if origin.returncode or not origin.stdout.strip():
            self.skipTest("ROOT has no origin remote")
        other = Path(self.temp.name) / "other-clone"
        (other / "skill/repair-specs").mkdir(parents=True)
        (other / "skill/shape-spec").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "remote", "add", "origin", origin.stdout.strip()], check=True)
        (self.repo / ".agents/skills/repair-specs").symlink_to(other / "skill/repair-specs")
        (self.repo / ".claude/skills/spec-chat-shape").unlink()
        (self.repo / ".claude/skills/spec-chat-shape").symlink_to(other / "skill/shape-spec")
        self.assertEqual(self.run_check(0), [])

    def apply_fix(self, line):
        """Do what a check line names as its fix: remove each path it names first, then run its installer run."""
        fix = line.split("; fix: ", 1)[1]
        if fix.startswith("remove what holds its place, "):
            held, fix = fix.removeprefix("remove what holds its place, ").split(", then ", 1)
            for place in (Path(shlex.split(part)[0]) for part in held.split(", ")):
                shutil.rmtree(place) if place.is_dir() and not place.is_symlink() else place.unlink()
        command = shlex.split(fix.removeprefix("run "))
        result = subprocess.run(command, env=self.env, cwd=self.home, text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_names_removing_what_holds_a_skill_links_place_and_that_fix_links_it_again(self):
        """review-service #service-check: the installer adopts a link into a clone of this repository, even to another
        skill, and skips any other occupant, so the fix removes what holds the link's place first; a link through the
        repository's own .agents/skills entry is held by what holds that entry."""
        self.run_install()
        shape = self.repo / ".claude/skills/spec-chat-shape"
        shape.unlink()
        shape.symlink_to(ROOT / "skill/review-spec")
        review = self.repo / ".codex/skills/spec-chat-review"
        review.unlink()
        review.mkdir()
        repair = self.repo / ".agents/skills/repair-specs"
        repair.unlink()
        repair.mkdir()
        (repair / "SKILL.md").write_text("another skill\n")
        lines = self.run_check(1)
        self.assertEqual(lines, [
            f"Spec Chat skill spec-chat-shape is not linked in {self.repo} (.claude/skills); fix: remove what holds its "
            f"place, {shape}, then run {INSTALL} --prefix {self.repo}",
            f"Spec Chat skill spec-chat-review is not linked in {self.repo} (.codex/skills); fix: remove what holds its "
            f"place, {review}, then run {INSTALL} --prefix {self.repo}",
            f"Spec Chat skill repair-specs is not linked in {self.repo} (.agents/skills, .claude/skills, "
            f".codex/skills); fix: remove what holds its place, {repair}, then run {INSTALL} --prefix {self.repo}"])
        for line in lines:
            self.apply_fix(line)
        self.assertEqual(self.run_check(0), [])
        for base in BASES:
            for name, skill in (("spec-chat-shape", "shape-spec"), ("spec-chat-review", "review-spec"),
                                ("repair-specs", "repair-specs")):
                self.assertEqual((self.repo / base / name).resolve(), ROOT / "skill" / skill)

    def test_check_names_the_unreadable_file_with_a_fix_that_works_for_it(self):
        """review-service #service-check: the onboarding file, or an older installer's install.toml the installer still
        folds in, that does not parse or whose install record is of another shape; once corrected, the named installer
        run clears it."""
        self.run_install()
        legacy = self.state / "spec-chat/install.toml"
        for broken, text in ((legacy, "links = [\n"), (self.onboarding, "links = [\n"), (legacy, 'links = "x"\n'),
                             (self.onboarding, "installed = []\n")):
            kept = broken.read_text() if broken.exists() else 'links = []\n'
            broken.write_text(text)
            lines = self.run_check(1, argv=[str(INSTALL), "--check"])
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith(f"Spec Chat file {broken} cannot be read ("), lines[0])
            self.assertTrue(lines[0].endswith(f"); fix: correct or remove it, then run {INSTALL} from the repository "
                                              "to install Spec Chat into"), lines[0])
            broken.write_text(kept)
            self.run_install()
            self.assertFalse(legacy.exists())
            self.assertEqual(self.run_check(0), [])

    def test_check_reports_an_onboarding_file_out_of_date_naming_the_installer_run_that_writes_it_again(self):
        """review-service #acceptance-check-onboarding: the file stays as it was; with none, the check names the
        installer run that writes one."""
        self.run_install()
        older = self.onboarding.read_text().replace(f'check = ["{INSTALL}", "--check"]\n', "")
        self.onboarding.write_text(older)
        check = [str(INSTALL), "--check"]  # as an older installer published none
        self.assertEqual(self.run_check(1, argv=check), [
            f"Spec Chat onboarding file {self.onboarding} is out of date (check missing); fix: run {INSTALL} --prefix "
            f"{self.repo}"])
        self.assertEqual(self.onboarding.read_text(), older)
        self.onboarding.write_text(older.replace('status = "pending"\n', 'status = "pending"\nproject = "x"\n')
                                   .replace(f'"{INSTALL}", "--after-pull"', '"/elsewhere/install-spec-chat", "--after-pull"'))
        self.assertEqual(self.run_check(1, argv=check), [
            f"Spec Chat onboarding file {self.onboarding} is out of date (after_pull differs, check missing, extra "
            f"project); fix: run {INSTALL} --prefix {self.repo}"])

        self.onboarding.unlink()
        self.assertEqual(self.run_check(1, argv=check), [
            f"no Spec Chat onboarding file at {self.onboarding}; fix: run {INSTALL} from the repository to install Spec "
            "Chat into"])
        self.assertFalse(self.onboarding.exists())

    def test_check_leaves_an_install_older_than_its_clone_to_readers(self):
        """review-service #acceptance-check-commit: no review service running and nothing else the installer publishes
        changed; the onboarding file keeps the commit it was installed at."""
        _, installed = self.pulled_since_install()
        self.assertEqual(self.run_check(0), [])
        self.assertEqual(self.status()["commit"], installed)

    def older_code(self, problem, session="default"):
        return (f"Spec Chat review service of session {session} is not on this clone's commit: it started on commit "
                f"{problem[0][:12]}, the clone is at {problem[1][:12]}; fix: run the after-pull step: "
                f"{self.status()['after_pull'][0]} --after-pull")

    def test_check_reports_a_service_on_older_code_naming_the_after_pull_step(self):
        """review-service #acceptance-check-older-code: the clone fast-forwarded with no after-pull step run; the
        service keeps running as it was, a join keeps the commit it started on, and the after-pull step clears it."""
        self.use_fake_systemd()
        upstream, clone = self.clone()
        before = self.install_from(clone)
        self.pull(upstream, clone, PULLED)
        head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True, capture_output=True)
        problem = [self.older_code((before["commit"], head.stdout.strip()))]
        self.assertEqual(self.run_check(1), problem)
        self.assertEqual(self.process(), before)
        self.assertNotIn("pulled", status(before))

        second = self.specs / "second.spec.html"
        second.write_text("<!doctype html><title>second</title>\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "second"], check=True)
        joined = subprocess.run([str(clone / HOST.relative_to(ROOT)), "register", "--state-dir",
                                 str(self.state / "spec-chat/hosting/default"), "--slug", "second", "--owner", "o",
                                 str(second)], env=self.env, text=True, capture_output=True, timeout=40)
        self.assertEqual(joined.returncode, 0, joined.stderr)
        self.assertEqual(self.process(), before)
        self.assertEqual(self.run_check(1), problem)

        self.run_after_pull()
        self.assertEqual(self.process()["commit"], head.stdout.strip())
        self.assertEqual(self.run_check(0), [])

    def test_check_reads_every_sessions_service_and_a_stopped_one_is_no_problem(self):
        """review-service #service-check: each session's running service under hosting/ is read; one stopped runs this
        clone's code at its next start."""
        upstream, clone = self.clone()
        before = self.install_from(clone)
        lane = self.state / "spec-chat/hosting/lane-a"
        host = [str(clone / HOST.relative_to(ROOT))]
        started = subprocess.run([*host, "register", "--state-dir", str(lane), "--slug", "lane", "--owner", "o",
                                  str(self.spec)], env=self.env, text=True, capture_output=True, timeout=40)
        self.assertEqual(started.returncode, 0, started.stderr)
        self.pull(upstream, clone, PULLED)
        head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True, capture_output=True)
        commits = (before["commit"], head.stdout.strip())
        self.assertEqual(self.run_check(1), [self.older_code(commits), self.older_code(commits, "lane-a")])
        stopped = subprocess.run([*host, "stop", "--state-dir", str(lane)], env=self.env, text=True, capture_output=True)
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(self.run_check(1), [self.older_code(commits)])

    def test_check_reports_a_box_user_service_disabled_or_gone_naming_box_setups_service_step(self):
        """review-service #acceptance-check-unit: the service stays disabled, and a unit gone stays gone."""
        fake = self.use_fake_systemd()
        process = self.install_from(ROOT, "--service")
        subprocess.run([str(FAKE_SYSTEMD / "systemctl"), "--user", "disable", UNIT], env=self.env, check=True)
        fix = (f"fix: run box setup's --service: {INSTALL} --prefix {self.repo} --review <first .spec.html> "
               "--service")
        self.assertEqual(self.run_check(1, fake), [
            f"Spec Chat box user service {UNIT} is not enabled (disabled), so the review service no longer starts with "
            "the box; " + fix])
        self.assertFalse((fake / f"{UNIT}.enabled").exists())
        (fake / f"{UNIT}.masked").touch()
        self.assertEqual(self.run_check(1, fake), [
            f"Spec Chat box user service {UNIT} is not enabled (masked), so the review service no longer starts with "
            f"the box; fix: run systemctl --user unmask {UNIT}, then " + fix.removeprefix("fix: ")])
        (fake / f"{UNIT}.masked").unlink()
        # Older systemd names a unit it does not know on its error output alone: the manager answers, so not enabled.
        (fake / f"{UNIT}.unknown").touch()
        self.assertEqual(self.run_check(1, fake), [
            f"Spec Chat box user service {UNIT} is not enabled (Failed to get unit file state for {UNIT}: No such file "
            "or directory), so the review service no longer starts with the box; " + fix])
        (fake / f"{UNIT}.unknown").unlink()
        # With no user bus the manager cannot be asked: no problem is claimed, and the check says it did not check.
        self.env["FAKE_SYSTEMD_NO_BUS"] = "1"
        self.assertEqual(self.run_check(0, fake), [])
        said = subprocess.run(self.status()["check"], env=self.env, cwd=self.home, stdin=subprocess.DEVNULL, text=True,
                              capture_output=True, timeout=30).stdout
        self.assertEqual(said, f"Spec Chat box user service {UNIT} not checked: the box's user service manager cannot "
                               "be asked (Failed to connect to bus: No medium found)\n")
        del self.env["FAKE_SYSTEMD_NO_BUS"]
        self.assertEqual(self.process(), process)
        unit = self.home / ".config/systemd/user" / UNIT
        unit.unlink()
        self.assertEqual(self.run_check(1, fake), [
            f"Spec Chat box user service {UNIT} is gone, so the review service no longer starts with the box; " + fix])
        self.assertFalse(unit.exists())

    def test_check_reports_every_problem_each_on_its_own_line_with_its_fix(self):
        """review-service #acceptance-check-every: a lost skill link and a service on older code than its clone."""
        upstream, clone = self.clone()
        before = self.install_from(clone)
        (self.repo / ".codex/skills/spec-chat-shape").unlink()
        self.pull(upstream, clone, PULLED)
        head = subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True, capture_output=True)
        installer = clone / "scripts/install-spec-chat"
        self.assertEqual(self.run_check(1), [
            f"Spec Chat skill spec-chat-shape is not linked in {self.repo} (.codex/skills); fix: run {installer} "
            f"--prefix {self.repo}",
            self.older_code((before["commit"], head.stdout.strip()))])

    def state_files(self):
        return sorted(p.name for p in self.onboarding.parent.iterdir() if p.is_file())

    def test_one_onboarding_file_records_install_rerun_and_undo(self):
        """review-service #acceptance-onboarding-one-file."""
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        self.assertEqual(self.state_files(), ["onboarding.toml"])
        installed = self.status()["installed"]
        self.assertEqual(len(installed["links"]), 9)
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

    def test_shell_box_setup_names_each_origin_and_an_empty_one_clears_them(self):
        """review-service #box-setup, shell #shell-setting: --shell, given again for more, reaches the service as
        box setup's other flags do; the service names those origins, and '' clears them."""
        def named():
            process = self.process()
            request = urllib.request.Request(f"http://{process['host']}:{process['port']}/comments", method="HEAD")
            with urllib.request.urlopen(request, timeout=4) as response:
                return process.get("shells"), response.headers.get("X-Spec-Chat-Shells")

        shells = ["https://workbench.example.invalid", "http://127.0.0.1:8123"]
        self.run_install("--specs", str(self.specs), "--review", str(self.spec), "--shell", shells[0], "--shell", shells[1])
        self.assertEqual(named(), (shells, " ".join(shells)))
        self.run_install("--specs", str(self.specs), "--review", str(self.spec), "--shell", "")
        self.assertEqual(named(), (None, ""))

    def test_service_flags_are_box_setup(self):
        for flags in (["--service"], ["--public-base", "https://spec.example.invalid"],
                      ["--shell", "https://workbench.example.invalid"]):
            result = subprocess.run([str(INSTALL), *flags], env=self.env, cwd=self.repo, text=True,
                                    capture_output=True, timeout=40)
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

    def test_install_delivers_the_vendored_bundles_license_and_notice_set_beside_it(self):
        """Every vendored file reaches the target with its upstream license and notice texts beside it."""
        self.use_fake_systemd()
        self.run_install("--prefix", str(self.repo), "--specs", str(self.specs))
        vendor, bundled = self.specs / ".viz/vendor", ROOT / "skill/review-spec/assets/viz/vendor"
        names = sorted(path.relative_to(vendor).as_posix() for path in vendor.rglob("*") if path.is_file())
        self.assertEqual(names, ["echarts-5.5.1.min.js", "echarts-5.5.1/LICENSE", "echarts-5.5.1/NOTICE",
                                 "echarts-5.5.1/ORIGIN.json", "echarts-5.5.1/licenses/LICENSE-d3",
                                 "echarts-5.5.1/licenses/LICENSE-zrender"])
        for name in names:
            self.assertEqual((vendor / name).read_bytes(), (bundled / name).read_bytes(), name)

    def assert_undo_keeps_tracked_assets(self, git_cannot_answer, why):
        """review-service #onboarding-entry: --undo deletes unmodified assets only when git answers it tracks none
        there, or they lie outside every repository; a git-tracked asset folder git cannot answer for stays, and the
        output names it and why."""
        self.run_install("--prefix", str(self.repo), "--specs", str(self.specs))
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "add runtime"], check=True)
        assets = [self.specs / ".viz", self.specs / ".style"]
        before = snapshot(*assets)
        git_cannot_answer()
        result = self.run_install("--undo")
        self.assertTrue(all(path.is_dir() for path in assets), result.stdout)
        self.assertEqual(snapshot(*assets), before)
        for path in assets:
            self.assertIn(f"left {path}: {why}", result.stdout)
        self.assertFalse(self.onboarding.exists())

    def test_undo_with_no_git_keeps_unmodified_assets_git_may_track(self):
        self.assert_undo_keeps_tracked_assets(self.without_git, "git ls-files")

    def test_undo_with_a_git_refusing_the_repo_keeps_unmodified_assets_git_may_track(self):
        def refusing_git():
            """A git that refuses every repository, as git does one owned by another user."""
            self.without_git()
            git = Path(self.env["PATH"]) / "git"
            git.write_text("#!/bin/sh\necho \"fatal: detected dubious ownership in repository\" >&2\nexit 128\n")
            git.chmod(0o755)
        self.assert_undo_keeps_tracked_assets(refusing_git, "fatal: detected dubious ownership in repository")

    def test_undo_with_no_git_deletes_unmodified_assets_outside_every_repository(self):
        """review-service #onboarding-entry: assets outside every repository are tracked by none, so --undo deletes
        them unmodified with no git to ask."""
        outside = Path(self.temp.name) / "outside-specs"
        outside.mkdir()
        self.without_git()
        self.run_install("--prefix", str(self.repo), "--specs", str(outside))
        self.assertEqual(sorted(path.name for path in outside.iterdir()), [".style", ".viz"])
        self.run_install("--undo")
        self.assertEqual(list(outside.iterdir()), [])


    # --- adopt/skip for occupied paths --------------------------------

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
        for base in BASES:
            for name, tgt in skill_map.items():
                p = self.repo / base / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.symlink_to(tgt)

        result = self.run_install()
        self.assertIn("adopted:", result.stdout)
        # Symlinks still point to the other clone, not replaced
        for base in BASES:
            for name, tgt in skill_map.items():
                p = self.repo / base / name
                self.assertTrue(p.is_symlink())
                self.assertEqual(p.resolve(), tgt.resolve())
        # The install record has no links (all adopted, none created)
        self.assertEqual(self.status()["installed"]["links"], [])

    def test_adopt_and_check_links_into_a_clone_whose_origin_names_the_same_repository_another_way(self):
        """review-service #onboarding-entry, #service-check: a clone whose origin is this clone's repository as an
        ssh:// URL, with or without a port, an scp-like git@host:path, or an https URL, with or without a .git suffix,
        however this clone's origin is written, is one with the same origin: the installer adopts its links and the
        check reports them linked."""
        origin = subprocess.run(["git", "-C", str(ROOT), "remote", "get-url", "origin"], text=True, capture_output=True)
        found = re.fullmatch(r"(?:(?:https|ssh)://(?:[^@/]+@)?|git@)([^/:@]+)(?::\d+)?[/:](.+)", origin.stdout.strip())
        if not found:
            self.skipTest("ROOT has no origin of a host")
        host, repo = found[1], found[2].strip("/").removesuffix(".git")  # owner/repo, however ROOT's origin is written
        forms = (f"ssh://git@{host}/{repo}.git", f"ssh://git@{host}:22/{repo}", f"git@{host}:{repo}.git",
                 f"https://{host}/{repo}")
        for n, url in enumerate(forms):
            with self.subTest(origin=url):
                other = Path(self.temp.name) / f"clone-{n}"
                subprocess.run(["git", "init", "-q", str(other)], check=True)
                subprocess.run(["git", "-C", str(other), "remote", "add", "origin", url], check=True)
                for name, source in zip(SKILL_NAMES, ("shape-spec", "review-spec", "repair-specs")):
                    (other / "skill" / source).mkdir(parents=True)
                    entry = self.repo / ".agents/skills" / name
                    entry.parent.mkdir(parents=True, exist_ok=True)
                    entry.unlink(missing_ok=True)
                    entry.symlink_to(other / "skill" / source)
                self.assertEqual(self.run_install().stdout.count("adopted:"), 3)
                for name, source in zip(SKILL_NAMES, ("shape-spec", "review-spec", "repair-specs")):
                    self.assertEqual((self.repo / ".agents/skills" / name).resolve(), other / "skill" / source)
                self.assertEqual(self.run_check(0), [])

    def test_review_slug_is_the_repository_name_its_origin_names(self):
        """The first spec's review slug is the repository's name from its origin, an ssh:// URL with a port too."""
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "ssh://git@example.invalid:2222/team/Weekly.Report.git/"], check=True)
        self.run_install("--specs", str(self.specs), "--review", str(self.spec))
        registry = tomllib.loads((self.state / "spec-chat/hosting/default/registry.toml").read_text())
        self.assertEqual([r["slug"] for r in registry["resource"]], ["weekly-report"])

    def test_skip_occupied_path_and_continue(self):
        """link() skips occupied non-clone paths and the installer continues."""
        for base in BASES:
            for name in SKILL_NAMES:
                p = self.repo / base / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.mkdir()  # a directory, not a symlink

        result = self.run_install()
        self.assertEqual(result.stdout.count("skipped:"), 9)
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

        for base in BASES:
            p = self.repo / base / "spec-chat-shape"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.symlink_to(unrelated)

        result = self.run_install()
        self.assertIn("skipped:", result.stdout)
        # The other skills should still be linked normally
        for base in BASES:
            self.assertEqual(
                (self.repo / base / "spec-chat-review").resolve(),
                ROOT / "skill/review-spec",
            )

    def test_skills_live_in_the_repo_agents_folder_and_never_under_home(self):
        """Skills live in the target repo's .agents/skills; .claude/skills and .codex/skills hold relative links to
        them; nothing is written under $HOME, from the repo root or a folder below it."""
        below = self.specs
        self.run_install(cwd=below)
        for name, source in zip(SKILL_NAMES, ("shape-spec", "review-spec", "repair-specs")):
            entry = self.repo / ".agents/skills" / name
            self.assertTrue(entry.is_symlink())
            self.assertEqual(entry.resolve(), ROOT / "skill" / source)
            for base in (".claude/skills", ".codex/skills"):
                self.assertEqual(os.readlink(self.repo / base / name), "../../.agents/skills/" + name)
        self.assertEqual(list(self.home.iterdir()), [])
        self.assertFalse((self.specs / ".agents").exists())

    def test_agents_only_puts_skills_only_in_the_repo_agents_folder(self):
        """--agents-only links the skills into .agents/skills and writes no .claude or .codex folder; a rerun
        changes nothing and --undo removes the links."""
        self.run_install("--agents-only")
        for name, source in zip(SKILL_NAMES, ("shape-spec", "review-spec", "repair-specs")):
            self.assertEqual((self.repo / ".agents/skills" / name).resolve(), ROOT / "skill" / source)
        self.assertFalse((self.repo / ".claude").exists())
        self.assertFalse((self.repo / ".codex").exists())
        self.assertEqual(sorted(self.status()["installed"]["links"]),
                         sorted(str(self.repo.resolve() / ".agents/skills" / n) for n in SKILL_NAMES))
        before = snapshot(self.home, self.state, self.repo)
        self.run_install("--agents-only")
        self.assertEqual(snapshot(self.home, self.state, self.repo), before)
        self.run_install("--undo")
        self.assertFalse(any((self.repo / ".agents/skills" / n).is_symlink() for n in SKILL_NAMES))

    def test_home_is_never_the_install_target(self):
        """Neither --prefix $HOME nor running from $HOME puts skills under ~/.agents/skills."""
        for args, cwd in ((("--prefix", str(self.home)), None), ((), self.home)):
            with self.subTest(args=args, cwd=cwd):
                result = self.run_install(*args, cwd=cwd, code=1)
                self.assertIn("never the home directory", result.stderr)
                self.assertEqual(list(self.home.iterdir()), [])
                self.assertFalse(self.onboarding.exists())

    def test_an_earlier_absolute_link_of_ours_becomes_relative(self):
        self.run_install()
        link = self.repo / ".claude/skills/spec-chat-review"
        link.unlink()
        link.symlink_to(self.repo / ".agents/skills/spec-chat-review")  # as an earlier run might have written it
        self.run_install()
        self.assertEqual(os.readlink(link), "../../.agents/skills/spec-chat-review")

    def test_onboarding_doc_says_how_to_write_a_project_rule(self):
        """project-rules #acceptance-onboarding-doc: one onboarding section says how to declare a project rule: mark
        its criterion in its home spec with a short name, and say in its text that it applies to every feature."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertEqual(readme.count("\n## Writing a project rule\n"), 1)
        section = readme.split("\n## Writing a project rule\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("mark its criterion in its home spec with a short name", section)
        self.assertIn("say in its text that it applies to every feature", section)
        self.assertIn('data-project-rule="', section)

    def test_onboarding_doc_says_a_project_rule_is_a_promise(self):
        """project-rules #acceptance-promise-doc: the project rule section says a project rule is a promise of what
        the product does or how the project builds and ships it, that a design-system convention is never marked and
        lives in the design system with a code check, and that a project keeps at most one umbrella rule for its
        design system."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = " ".join(readme.split("\n## Writing a project rule\n", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn("A project rule is a promise a later feature could break: what the product does for its users",
                      section)
        self.assertIn("or how the project builds and ships it", section)
        self.assertIn("A design-system convention", section)
        self.assertIn("is never marked: it lives in the design system", section)
        self.assertIn("where a code check rejects a hand-rolled version", section)
        self.assertIn("A project keeps at most one project rule for its design system, the umbrella", section)


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

    def test_a_test_file_that_leaves_a_process_running_fails_the_run(self):
        """review-service #acceptance-ci-alone: the suite runs in one temp directory, and a test file that leaves a
        process naming it running fails, and the process is stopped."""
        workflow = (ROOT / ".github/workflows/test.yml").read_text(encoding="utf-8")
        self.assertIn('export TMPDIR="$SPEC_CHAT_TEST_TMP"', workflow)
        self.assertIn("sh tests/leftover-processes || ", workflow)
        check = ROOT / "tests/leftover-processes"
        with tempfile.TemporaryDirectory(prefix="spec-chat-leftover-") as temp:
            env = {**os.environ, "SPEC_CHAT_TEST_TMP": str(Path(temp).resolve())}
            clean = subprocess.run(["sh", str(check)], env=env, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            # A detached stand-in server that names the run directory and outlives its starter.
            server = subprocess.Popen(["python3", "-c", "import sys, time; time.sleep(60)", env["SPEC_CHAT_TEST_TMP"]],
                                      start_new_session=True)
            self.addCleanup(server.wait, 5)
            self.addCleanup(server.kill)
            left = subprocess.run(["sh", str(check)], env=env, capture_output=True, text=True)
            self.assertEqual(left.returncode, 1)
            self.assertIn(str(server.pid), left.stderr)
            self.assertIsNotNone(server.wait(timeout=5), "the check stops what it finds")
            self.assertEqual(subprocess.run(["sh", str(check)], env=env).returncode, 0)


if __name__ == "__main__":
    unittest.main()
