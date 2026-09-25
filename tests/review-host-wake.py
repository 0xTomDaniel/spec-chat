"""ANN-132: the review host wakes each registry row's owner pane once per batch.

Herdr is faked on PATH; no real pane is ever prompted.
"""
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "skill/review-spec/assets/review-serve.py"
WATCH = ROOT / "skill/review-spec/scripts/watch-specs.sh"

_spec = importlib.util.spec_from_file_location("review_serve", SERVER_PATH)
serve = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(serve)

FAKE_HERDR = """#!/bin/sh
[ "$1 $2" = "agent get" ] || exit 2
f="$FAKE_HERDR_DIR/agents/$3"
[ -f "$f" ] || { echo "pane not found: $3" >&2; exit 1; }
printf '{"result":{"agent":{"pane_id":"%s","terminal_id":"term-1","agent_status":"%s"}}}\\n' "$3" "$(cat "$f")"
"""
FAKE_SAY = """#!/bin/sh
{ for arg in "$@"; do printf '%s\\037' "$arg"; done; printf '\\n'; } >> "$FAKE_HERDR_DIR/say.log"
[ -f "$FAKE_HERDR_DIR/say.err" ] && cat "$FAKE_HERDR_DIR/say.err" >&2
[ -f "$FAKE_HERDR_DIR/say.sleep" ] && sleep "$(cat "$FAKE_HERDR_DIR/say.sleep")"
exit "$(cat "$FAKE_HERDR_DIR/say.rc" 2>/dev/null || echo 0)"
"""


def path_without_herdr():
    keep = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and not any(os.path.exists(os.path.join(entry, tool)) for tool in ("herdr", "herdr-say")):
            keep.append(entry)
    return os.pathsep.join(keep)


class FakeHerdr:
    """Fake herdr and herdr-say on PATH, controlled by files."""

    def __init__(self, work):
        self.dir = Path(work) / "herdr"
        self.bin = self.dir / "bin"
        (self.dir / "agents").mkdir(parents=True)
        self.bin.mkdir()
        for name, body in (("herdr", FAKE_HERDR), ("herdr-say", FAKE_SAY)):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)
        self.log = self.dir / "say.log"

    def env(self, installed=True):
        base = path_without_herdr()
        return {
            "PATH": (str(self.bin) + os.pathsep + base) if installed else base,
            "FAKE_HERDR_DIR": str(self.dir),
        }

    def agent(self, pane, status="idle"):
        path = self.dir / "agents" / pane
        if status is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(status)

    def say_rc(self, code):
        (self.dir / "say.rc").write_text(str(code))

    def say_err(self, text):
        (self.dir / "say.err").write_text(text)

    def say_sleep(self, seconds):
        (self.dir / "say.sleep").write_text(str(seconds))

    def says(self):
        if not self.log.exists():
            return []
        return [line.split("\x1f")[:-1] for line in self.log.read_text().splitlines()]


class WakeControllerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-host-wake-")
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.herdr = FakeHerdr(self.work)
        self.use_env(installed=True)
        self.collection = self.work / "repo/docs"
        self.spec = self.collection / "specs/wake.spec.html"
        self.spec.parent.mkdir(parents=True)
        self.spec.write_text("<title>wake</title>")
        self.human = Path(str(self.spec) + ".review/human")
        self.human.mkdir(parents=True)
        self.cursor = Path(str(self.spec) + ".review/.cursor-owner")
        self.record = {
            "id": "spec:wake::docs/specs/wake.spec.html", "slug": "wake",
            "root": str(self.work / "repo"), "narrow_root": str(self.collection),
            "spec": "docs/specs/wake.spec.html", "spec_file": str(self.spec),
            "base": "HEAD", "owner": "w1:pOwner", "checker": "checker",
            "cursor_name": ".cursor-owner",
        }
        self.herdr.agent("w1:pOwner")
        self.controller = self.new_controller()

    def use_env(self, installed):
        saved = {key: os.environ.get(key) for key in ("PATH", "FAKE_HERDR_DIR")}

        def restore():
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        os.environ.update(self.herdr.env(installed))

    def new_controller(self):
        server = types.SimpleNamespace(mount_state=serve.MountState([self.record]))
        return serve.WakeController(server)

    def event(self, name):
        (self.human / name).write_text("{}")

    def advance(self, *names):
        with self.cursor.open("a") as stream:
            for name in names:
                stream.write(name + "\n")

    def status(self):
        return self.controller.status(self.record["id"])

    def test_idle_owner_is_woken_exactly_once_per_unchanged_batch(self):
        self.event("100-comment-a.json")
        self.event("110-handoff-a.json")
        self.controller.poll()
        says = self.herdr.says()
        self.assertEqual(len(says), 1)
        argv = says[0]
        self.assertEqual(argv[:5], ["--kind", "command", "--artifact", str(self.spec), "w1:pOwner"])
        message = argv[5]
        for part in (str(self.spec), str(self.collection), ".cursor-owner", "2 events", "zero-wait scan", "park"):
            self.assertIn(part, message)
        self.assertEqual(self.status(), "sent")
        for _ in range(3):
            self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 1)
        self.assertFalse(self.cursor.exists(), "the host never advances the cursor")

    def test_no_wake_without_completed_handoff(self):
        self.event("100-comment-a.json")
        self.controller.poll()
        self.assertEqual(self.herdr.says(), [])
        self.assertIsNone(self.status())

    def test_batch_matches_zero_wait_scan(self):
        for name in ("090-comment-x.json", "100-handoff-x.json", "110-reply-x.json",
                     "120-handoff-x.json", "130-comment-after.json"):
            self.event(name)
        self.advance("090-comment-x.json")
        scan = subprocess.run(
            ["sh", str(WATCH), str(self.collection), ".cursor-owner", "0", "1"],
            text=True, stdout=subprocess.PIPE, check=True,
        ).stdout
        expected = tuple(line.split("\t")[1] for line in scan.splitlines())
        self.assertEqual(serve._wake_batch(self.record), expected)

    def test_later_handoff_wakes_once_before_and_after_cursor_advance(self):
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.event("200-handoff-b.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 2)
        self.assertIn("2 events", self.herdr.says()[1][5])
        self.advance("100-handoff-a.json", "200-handoff-b.json")
        self.controller.poll()
        self.assertIsNone(self.status())
        self.event("300-handoff-c.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 3)
        self.assertIn("1 events", self.herdr.says()[2][5])

    def test_working_owner_is_deferred_and_retried_until_delivered(self):
        self.herdr.agent("w1:pOwner", "working")
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(self.herdr.says(), [])
        self.assertEqual(self.status(), "deferred")
        self.herdr.agent("w1:pOwner", "idle")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 1)
        self.assertEqual(self.status(), "sent")

    def test_blocked_delivery_exit_75_is_deferred_and_retried_each_poll(self):
        self.herdr.say_rc(75)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 2)
        self.assertEqual(self.status(), "deferred")
        self.herdr.say_rc(0)
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 3)
        self.assertEqual(self.status(), "sent")

    def test_unresolved_owner_fails_and_reregistered_owner_is_woken(self):
        self.herdr.agent("w1:pOwner", None)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(self.herdr.says(), [])
        self.assertEqual(self.status(), "failed")
        self.record["owner"] = "w2:pNew"
        self.herdr.agent("w2:pNew")
        self.controller.poll()
        self.assertEqual([argv[4] for argv in self.herdr.says()], ["w2:pNew"])
        self.assertEqual(self.status(), "sent")

    def test_other_delivery_failure_fails_and_retries(self):
        self.herdr.say_rc(3)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 2)
        self.assertEqual(self.status(), "failed")
        self.herdr.say_rc(0)
        self.controller.poll()
        self.assertEqual(self.status(), "sent")
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 3)

    def test_stalled_prompt_counts_as_sent_and_is_never_retyped(self):
        self.herdr.say_rc(1)
        self.herdr.say_err('{"error":{"code":"agent_prompt_stalled","message":"no activity observed"}}\n')
        self.event("100-handoff-a.json")
        for _ in range(3):
            self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 1)
        self.assertEqual(self.status(), "sent")

    def test_refused_prompt_error_stays_failed(self):
        self.herdr.say_rc(1)
        self.herdr.say_err('{"error":{"code":"agent_prompt_failed","message":"refused"}}\n')
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 2)
        self.assertEqual(self.status(), "failed")

    def test_say_timeout_after_sending_counts_as_sent_and_is_never_retyped(self):
        saved = serve.WAKE_SAY_TIMEOUT_SECONDS
        serve.WAKE_SAY_TIMEOUT_SECONDS = 0.5
        self.addCleanup(setattr, serve, "WAKE_SAY_TIMEOUT_SECONDS", saved)
        self.herdr.say_sleep(5)
        self.event("100-handoff-a.json")
        started = time.monotonic()
        self.controller.poll()
        self.assertLess(time.monotonic() - started, 3, "timeout must kill the whole herdr-say process group")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 1)
        self.assertEqual(self.status(), "sent")

    def test_bad_row_never_stops_wake_for_other_rows(self):
        bad_cursor = dict(self.record, id="spec:bad::cursor", cursor_name=".cursor-bad")
        Path(str(self.spec) + ".review/.cursor-bad").write_bytes(b"\xff\xfe\x00bad\n")
        broken = dict(self.record, id="spec:bad::broken", spec_file=None)
        self.controller.server.mount_state = serve.MountState([bad_cursor, broken, self.record])
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.assertEqual([argv[4] for argv in self.herdr.says()], ["w1:pOwner"])
        self.assertEqual(self.status(), "sent")
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 1)

    def test_without_herdr_nothing_is_sent_and_state_is_unavailable(self):
        self.use_env(installed=False)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.assertEqual(self.herdr.says(), [])
        self.assertEqual(self.status(), "unavailable")

    def test_restart_wakes_unprocessed_batch_once_more(self):
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller = self.new_controller()
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.herdr.says()), 2)

    def test_removed_resource_forgets_state(self):
        self.herdr.agent("w1:pOwner", None)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.server.mount_state = serve.MountState([])
        self.controller.poll()
        self.assertIsNone(self.status())


class RegistryReloadTest(unittest.TestCase):
    def test_same_size_same_mtime_rewrite_is_reloaded(self):
        with tempfile.TemporaryDirectory(prefix="spec-chat-host-wake-reload-") as temp:
            registry = Path(temp) / "registry.toml"

            def body(owner):
                return (
                    "[[resource]]\n"
                    f'id = "spec:wake::docs/wake.spec.html"\nslug = "wake"\nroot = "{temp}"\n'
                    f'narrow_root = "{temp}/docs"\nspec = "docs/wake.spec.html"\nbase = "HEAD"\n'
                    f'owner = "{owner}"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n'
                )
            registry.write_text(body("w1:pAAAA"))
            state = serve.MountState(serve._read_registry(str(registry), trust=True), str(registry))
            self.assertEqual(state.snapshot()[0]["owner"], "w1:pAAAA")
            before = os.stat(registry)
            fresh = Path(temp) / "registry.toml.new"
            fresh.write_text(body("w1:pBBBB"))
            os.utime(fresh, ns=(before.st_atime_ns, before.st_mtime_ns))
            os.replace(fresh, registry)
            after = os.stat(registry)
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
            self.assertEqual(state.snapshot()[0]["owner"], "w1:pBBBB")


class WakeHeaderHttpTest(unittest.TestCase):
    """The running server reports the wake state on /api/events."""

    def test_events_carry_wake_state_header(self):
        with tempfile.TemporaryDirectory(prefix="spec-chat-host-wake-http-") as temp:
            work = Path(temp)
            herdr = FakeHerdr(work)
            repo = work / "repo"
            spec = repo / "docs/wake.spec.html"
            spec.parent.mkdir(parents=True)
            spec.write_text("<title>wake</title>")
            git = ("git", "-C", str(repo))
            subprocess.run(("git", "init", "-q", "-b", "main", str(repo)), check=True)
            subprocess.run(git + ("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "seed"), check=True)
            base = subprocess.check_output(git + ("rev-parse", "HEAD"), text=True).strip()
            human = Path(str(spec) + ".review/human")
            human.mkdir(parents=True)
            registry = work / "registry.toml"
            registry.write_text(
                "[[resource]]\n"
                f'id = "spec:wake::docs/wake.spec.html"\nslug = "wake"\nroot = "{repo}"\n'
                f'narrow_root = "{repo}/docs"\nspec = "docs/wake.spec.html"\nbase = "{base}"\n'
                'owner = "w1:pGone"\nchecker = "checker"\ncursor_name = ".cursor-owner"\n'
            )
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **herdr.env(True))
            process = subprocess.Popen(
                (sys.executable, str(SERVER_PATH), "--registry", str(registry), "--bind", "127.0.0.1", "--port", str(port)),
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            self.addCleanup(process.wait, 5)
            self.addCleanup(process.terminate)
            url = f"http://127.0.0.1:{port}/api/events?dir=wake%2Fdocs%2Fwake.spec.html.review"

            def header():
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(url, timeout=2) as response:
                            return response.headers.get("X-Spec-Chat-Wake")
                    except OSError:
                        time.sleep(0.1)
                raise AssertionError("server did not answer")

            def wait_for(expected, seconds=8):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    value = header()
                    if value == expected:
                        return value
                    time.sleep(0.2)
                return header()

            self.assertIsNone(header())
            (human / "100-handoff-a.json").write_text('{"event":"handoff","id":"h1","createdAt":"2026-09-25T00:00:00Z"}')
            self.assertEqual(wait_for("failed"), "failed")
            self.assertEqual(herdr.says(), [])
            herdr.agent("w1:pGone")
            started = time.monotonic()
            self.assertEqual(wait_for("sent"), "sent")
            self.assertLess(time.monotonic() - started, 8)
            time.sleep(3.5)
            self.assertEqual(len(herdr.says()), 1)


if __name__ == "__main__":
    unittest.main()
