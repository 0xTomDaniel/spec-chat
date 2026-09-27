"""The review host wakes each registry row's owner once per batch, only through the wake provider.

The wake provider is a fake registered in providers/wake.toml; no real pane is ever prompted.
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

FAKE_CHECK = """#!/bin/sh
[ -f "$0.d/owners/$1" ]
"""
FAKE_SEND = """#!/bin/sh
{ for arg in "$@"; do printf '%s\\037' "$arg"; done; printf '\\n'; } >> "$0.d/send.log"
[ -f "$0.d/send.sleep" ] && sleep "$(cat "$0.d/send.sleep")"
exit "$(cat "$0.d/send.rc" 2>/dev/null || echo 0)"
"""


class FakeWaker:
    """A fake wake provider plugged into a service state dir, controlled by files."""

    def __init__(self, work, state):
        self.dir = Path(work) / "waker"
        self.state = Path(state)
        self.check = self.dir / "check"
        self.send = self.dir / "send"
        self.data = self.dir / "send.d"
        (self.dir / "check.d/owners").mkdir(parents=True)
        self.data.mkdir()
        for path, body in ((self.check, FAKE_CHECK), (self.send, FAKE_SEND)):
            path.write_text(body)
            path.chmod(0o755)
        self.plug()

    def plug(self):
        providers = self.state / "providers"
        providers.mkdir(parents=True, exist_ok=True)
        (providers / "wake.toml").write_text(
            f'check = ["{self.check}", "{{owner}}"]\n'
            f'send = ["{self.send}", "{{owner}}", "{{artifact}}", "{{message}}"]\n'
        )

    def unplug(self):
        (self.state / "providers/wake.toml").unlink(missing_ok=True)

    def agent(self, owner, present=True):
        path = self.dir / "check.d/owners" / owner
        if present:
            path.write_text("")
        else:
            path.unlink(missing_ok=True)

    def send_rc(self, code):
        (self.data / "send.rc").write_text(str(code))

    def send_sleep(self, seconds):
        (self.data / "send.sleep").write_text(str(seconds))

    def says(self):
        log = self.data / "send.log"
        if not log.exists():
            return []
        return [line.split("\x1f")[:-1] for line in log.read_text().splitlines()]


class WakeControllerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-host-wake-")
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.state = self.work / "state"
        self.waker = FakeWaker(self.work, self.state)
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
        self.waker.agent("w1:pOwner")
        self.controller = self.new_controller()

    def new_controller(self):
        server = types.SimpleNamespace(mount_state=serve.MountState([self.record]), state_dir=str(self.state))
        return serve.WakeController(server)

    def event(self, name):
        (self.human / name).write_text("{}")

    def advance(self, *names):
        with self.cursor.open("a") as stream:
            for name in names:
                stream.write(name + "\n")

    def status(self):
        return self.controller.status(self.record["id"])

    def test_owner_is_woken_exactly_once_per_unchanged_batch(self):
        self.event("100-comment-a.json")
        self.event("110-handoff-a.json")
        self.controller.poll()
        says = self.waker.says()
        self.assertEqual(len(says), 1)
        owner, artifact, message = says[0]
        self.assertEqual((owner, artifact), ("w1:pOwner", str(self.spec)))
        for part in (str(self.spec), str(self.collection), ".cursor-owner", "2 events", "zero-wait scan", "park"):
            self.assertIn(part, message)
        self.assertEqual(self.status(), "sent")
        for _ in range(3):
            self.controller.poll()
        self.assertEqual(len(self.waker.says()), 1)
        self.assertFalse(self.cursor.exists(), "the host never advances the cursor")

    def test_no_wake_without_completed_handoff(self):
        self.event("100-comment-a.json")
        self.controller.poll()
        self.assertEqual(self.waker.says(), [])
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
        self.assertEqual(len(self.waker.says()), 2)
        self.assertIn("2 events", self.waker.says()[1][2])
        self.advance("100-handoff-a.json", "200-handoff-b.json")
        self.controller.poll()
        self.assertIsNone(self.status())
        self.event("300-handoff-c.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 3)
        self.assertIn("1 events", self.waker.says()[2][2])

    def test_send_exit_75_is_deferred_and_retried_each_poll(self):
        self.waker.send_rc(75)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 2)
        self.assertEqual(self.status(), "deferred")
        self.waker.send_rc(0)
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 3)
        self.assertEqual(self.status(), "sent")

    def test_failed_check_fails_and_reregistered_owner_is_woken(self):
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(self.waker.says(), [])
        self.assertEqual(self.status(), "failed")
        self.record["owner"] = "w2:pNew"
        self.waker.agent("w2:pNew")
        self.controller.poll()
        self.assertEqual([argv[0] for argv in self.waker.says()], ["w2:pNew"])
        self.assertEqual(self.status(), "sent")

    def test_other_send_exit_fails_and_retries(self):
        self.waker.send_rc(3)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 2)
        self.assertEqual(self.status(), "failed")
        self.waker.send_rc(0)
        self.controller.poll()
        self.assertEqual(self.status(), "sent")
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 3)

    def test_send_timeout_counts_as_sent_and_is_never_resent(self):
        saved = serve.WAKE_SEND_TIMEOUT_SECONDS
        serve.WAKE_SEND_TIMEOUT_SECONDS = 0.5
        self.addCleanup(setattr, serve, "WAKE_SEND_TIMEOUT_SECONDS", saved)
        self.waker.send_sleep(5)
        self.event("100-handoff-a.json")
        started = time.monotonic()
        self.controller.poll()
        self.assertLess(time.monotonic() - started, 3, "timeout must kill the whole send process group")
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 1)
        self.assertEqual(self.status(), "sent")

    def test_bad_row_never_stops_wake_for_other_rows(self):
        bad_cursor = dict(self.record, id="spec:bad::cursor", cursor_name=".cursor-bad")
        Path(str(self.spec) + ".review/.cursor-bad").write_bytes(b"\xff\xfe\x00bad\n")
        broken = dict(self.record, id="spec:bad::broken", spec_file=None)
        self.controller.server.mount_state = serve.MountState([bad_cursor, broken, self.record])
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.assertEqual([argv[0] for argv in self.waker.says()], ["w1:pOwner"])
        self.assertEqual(self.status(), "sent")
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 1)

    def test_without_provider_nothing_is_sent_and_plugging_needs_no_restart(self):
        self.waker.unplug()
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.assertEqual(self.waker.says(), [])
        self.assertEqual(self.status(), "unavailable")
        (self.state / "providers/wake.toml").write_text('check = "not a list"\n')
        self.controller.poll()
        self.assertEqual(self.status(), "unavailable")
        self.waker.plug()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 1)
        self.assertEqual(self.status(), "sent")

    def test_restart_wakes_unprocessed_batch_once_more(self):
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller = self.new_controller()
        self.controller.poll()
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), 2)

    def test_removed_resource_forgets_state(self):
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        self.controller.poll()
        self.controller.server.mount_state = serve.MountState([])
        self.controller.poll()
        self.assertIsNone(self.status())

    def test_no_pane_tool_is_named(self):
        roots = [ROOT / "skill", ROOT / "tools", ROOT / "DESIGN.md", ROOT / "README.md"]
        roots += [path for path in (ROOT / "docs/specs").glob("*.spec.html")]
        for root in roots:
            for path in ([root] if root.is_file() else root.rglob("*")):
                if path.is_file() and "vendor" not in path.parts:
                    text = path.read_text(encoding="utf-8", errors="replace").lower()
                    self.assertNotIn("herdr", text, str(path))


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
            waker = FakeWaker(work, work)
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
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
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
            self.assertEqual(waker.says(), [])
            waker.agent("w1:pGone")
            started = time.monotonic()
            self.assertEqual(wait_for("sent"), "sent")
            self.assertLess(time.monotonic() - started, 8)
            time.sleep(3.5)
            self.assertEqual(len(waker.says()), 1)


if __name__ == "__main__":
    unittest.main()
