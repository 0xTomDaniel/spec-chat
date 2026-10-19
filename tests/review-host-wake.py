"""The review host wakes each registry row's owner once per batch, only through the wake provider.

The wake provider is a fake registered in providers/wake.toml; no real pane is ever prompted.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock
import urllib.parse
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
printf '%s\\n' "$1" >> "$0.d/check.log"
[ -f "$0.d/owners/$1" ] && exit 0
[ -f "$0.d/check.err" ] && cat "$0.d/check.err" >&2
exit 1
"""
FAKE_SEND = """#!/bin/sh
{ for arg in "$@"; do printf '%s\\037' "$arg"; done; printf '\\n'; } >> "$0.d/send.log"
printf '%s\\n' "${HERDR_SESSION:-default}" >> "$0.d/session.log"
[ -f "$0.d/send.sleep" ] && sleep "$(cat "$0.d/send.sleep")"
[ -f "$0.d/send.err" ] && cat "$0.d/send.err" >&2
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

    def send_error(self, text):
        (self.data / "send.err").write_text(text)

    def check_error(self, text):
        """What a check that does not find the owner writes to its error output."""
        (self.dir / "check.d/check.err").write_text(text)

    def checks(self):
        """How many checks ran: one per wake attempt."""
        log = self.dir / "check.d/check.log"
        return len(log.read_text().splitlines()) if log.exists() else 0

    def sessions(self):
        """The session each send ran in: a pane id names a pane of that session alone."""
        log = self.data / "session.log"
        return log.read_text().splitlines() if log.exists() else []

    def says(self):
        log = self.data / "send.log"
        if not log.exists():
            return []
        return [line.split("\x1f")[:-1] for line in log.read_text().splitlines()]


class WakeControllerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="spec-chat-host-wake-")
        self.addCleanup(temp.cleanup)
        self.enterContext(contextlib.redirect_stderr(io.StringIO()))  # the wake log lines; polled_log reads them
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
        self.now = 1000.0  # the controller's clock: polls in a row run at one instant unless a test moves it
        self.controller = self.new_controller()

    def new_controller(self):
        server = types.SimpleNamespace(mount_state=serve.MountState([self.record]), state_dir=str(self.state))
        controller = serve.WakeController(server)
        controller.clock = lambda: self.now
        return controller

    def backoff_passes(self):
        """The longest wait between failed attempts passes (host-wake #wake-rule-retry: at most every few minutes)."""
        self.now += serve.WAKE_POLL_SECONDS * 2 ** serve.WAKE_BACKOFF_DOUBLINGS

    def polls_every_3s(self, seconds, until=lambda: False):
        """Poll as the host does, every 3 seconds, for the given time: the poll indexes that ran a wake attempt."""
        attempts = []
        for index in range(int(seconds // serve.WAKE_POLL_SECONDS)):
            self.now += serve.WAKE_POLL_SECONDS
            before = self.waker.checks()
            self.controller.poll()
            if self.waker.checks() > before:
                attempts.append(index)
            if until():
                break
        return attempts

    def event(self, name):
        (self.human / name).write_text("{}")

    def advance(self, *names):
        with self.cursor.open("a") as stream:
            for name in names:
                stream.write(name + "\n")

    def status(self):
        return self.controller.status(self.record["id"])

    def test_owner_is_woken_exactly_once_per_unchanged_batch(self):
        """host-wake #acceptance-wake-once; prompt-first-shaping #acceptance-agent-wake (the host only runs the provider's
        check and send, so no second processor); review-service #acceptance-wake (wake half)."""
        self.event("100-comment-a.json")
        self.event("110-handoff-a.json")
        launched = []
        popen = subprocess.Popen

        def record(argv, *args, **kwargs):
            launched.append(argv[0])
            return popen(argv, *args, **kwargs)

        with mock.patch.object(serve.subprocess, "Popen", side_effect=record):
            self.controller.poll()
            for _ in range(3):
                self.controller.poll()
        self.assertEqual(launched, [str(self.waker.check), str(self.waker.send)])
        says = self.waker.says()
        self.assertEqual(len(says), 1)
        owner, artifact, message = says[0]
        self.assertEqual((owner, artifact), ("w1:pOwner", str(self.spec)))
        for part in (str(self.spec), str(self.collection), ".cursor-owner", "2 events", "zero-wait scan", "park"):
            self.assertIn(part, message)
        self.assertEqual(self.status(), "sent")
        self.assertFalse(self.cursor.exists(), "the host never advances the cursor")

    def test_reproof_request_hand_off_wakes_the_row_owner(self):
        """criterion-evidence #acceptance-reproof-owner, #acceptance-reproof-owner-main: a re-proof request is a comment, so
        its hand-off wakes the row's owner, the lane owner's pane, or the operator's pane for a spec on main."""
        for owner in ("w1:pLane", "w1:pOperator"):
            for path in self.human.iterdir():
                path.unlink()
            self.record["owner"] = owner
            self.waker.agent(owner)
            self.write("100-comment-r.json", {"event": "comment", "id": "r",
                                              "text": "acceptance-x changed since its evidence: please recapture it."})
            self.write("110-handoff-r.json", {"event": "handoff", "id": "h", "events": ["r"]})
            self.controller = self.new_controller()
            self.controller.poll()
            self.assertEqual(self.waker.says()[-1][:2], [owner, str(self.spec)])

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

    def scan(self, *, check=True):
        result = subprocess.run(
            ["sh", str(WATCH), str(self.collection), ".cursor-owner", "0", "1"],
            text=True, stdout=subprocess.PIPE, check=False,
        )
        if check:
            self.assertEqual(result.returncode, 0)
        return result.returncode, tuple(line.split("\t")[1] for line in result.stdout.splitlines())

    def write(self, name, body):
        (self.human / name).write_text(json.dumps(body))

    def test_handoff_batch_is_the_handoff_and_the_events_it_lists(self):
        # review-state #live-handoff, host-wake #wake-rule-batch: two reviewers' drafts, one hands off.
        self.write("100-comment-a1.json", {"event": "comment", "id": "a1", "browser": "A"})
        self.write("110-comment-b1.json", {"event": "comment", "id": "b1", "browser": "B"})
        self.write("120-edit-a2.json", {"event": "edit", "id": "a2", "supersedes": "a1", "browser": "A"})
        self.write("130-handoff-ha.json", {"event": "handoff", "id": "ha", "events": ["a1", "a2"], "browser": "A"})
        expected = ("100-comment-a1.json", "120-edit-a2.json", "130-handoff-ha.json")
        self.assertEqual(self.scan(), (0, expected))
        self.assertEqual(serve._wake_batch(self.record), expected)

    def test_two_handoffs_then_cursor_advance_leaves_nothing(self):
        # review-state #acceptance-agent-cursor, #acceptance-agent-cursor-scan (server side).
        self.write("100-comment-a1.json", {"event": "comment", "id": "a1"})
        self.write("110-comment-b1.json", {"event": "comment", "id": "b1"})
        self.write("120-handoff-ha.json", {"event": "handoff", "id": "ha", "events": ["a1"]})
        self.write("130-handoff-hb.json", {"event": "handoff", "id": "hb", "events": ["b1"]})
        self.write("140-comment-a3.json", {"event": "comment", "id": "a3"})
        rc, names = self.scan()
        self.assertEqual(names, ("100-comment-a1.json", "110-comment-b1.json",
                                 "120-handoff-ha.json", "130-handoff-hb.json"))
        self.assertEqual(serve._wake_batch(self.record), names)
        self.advance(*names)
        self.assertEqual(self.scan(check=False), (3, ()))
        self.assertEqual(serve._wake_batch(self.record), ())

    def test_handoff_without_events_hands_off_every_draft_before_it(self):
        self.write("100-comment-a1.json", {"event": "comment", "id": "a1"})
        self.write("110-comment-b1.json", {"event": "comment", "id": "b1"})
        self.write("120-handoff-old.json", {"event": "handoff", "id": "old"})
        self.write("130-comment-late.json", {"event": "comment", "id": "late"})
        expected = ("100-comment-a1.json", "110-comment-b1.json", "120-handoff-old.json")
        self.assertEqual(self.scan(), (0, expected))
        self.assertEqual(serve._wake_batch(self.record), expected)

    def test_listed_event_already_in_cursor_is_not_repeated(self):
        self.write("100-comment-a1.json", {"event": "comment", "id": "a1"})
        self.write("110-handoff-h1.json", {"event": "handoff", "id": "h1", "events": ["a1"]})
        self.advance("100-comment-a1.json", "110-handoff-h1.json")
        self.write("120-handoff-h2.json", {"event": "handoff", "id": "h2", "events": ["a1"]})
        self.assertEqual(self.scan(), (0, ("120-handoff-h2.json",)))
        self.assertEqual(serve._wake_batch(self.record), ("120-handoff-h2.json",))

    def test_later_handoff_wakes_once_before_and_after_cursor_advance(self):
        """host-wake #acceptance-next-batch."""
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
        """host-wake #acceptance-defer: exit 75 is busy, nothing delivered; one prompt once delivery succeeds."""
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

    def poll_until_failed(self):
        """host-wake #wake-rule-retry: retrying for each failed attempt before a few in a row, then failed."""
        for _ in range(serve.WAKE_FAILED_ATTEMPTS - 1):
            self.controller.poll()
            self.assertEqual(self.status(), "retrying")
        self.controller.poll()

    def polled_log(self, polls=1):
        """The service log lines the given polls write (the service's stderr)."""
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            for _ in range(polls):
                self.controller.poll()
        return stderr.getvalue().splitlines()

    def test_failed_check_fails_and_reregistered_owner_is_woken(self):
        """host-wake #acceptance-failed-reregister."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        self.assertEqual(self.waker.says(), [])
        self.assertEqual(self.status(), "failed; owner pane not found (exit 1)")
        self.record["owner"] = "w2:pNew"
        self.waker.agent("w2:pNew")
        self.controller.poll()
        self.assertEqual([argv[0] for argv in self.waker.says()], ["w2:pNew"])
        self.assertEqual(self.status(), "sent")

    def test_a_new_batch_or_owner_restarts_the_attempts_in_a_row(self):
        """host-wake #wake-rule-retry: failed needs a few failed attempts in a row for one batch and owner."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        for change, apply in (("new batch", lambda: self.event("200-handoff-b.json")),
                              ("re-registered owner", lambda: self.record.update(owner="w2:pGone"))):
            with self.subTest(change):
                self.controller = self.new_controller()
                for _ in range(serve.WAKE_FAILED_ATTEMPTS - 1):
                    self.controller.poll()
                self.assertEqual(self.status(), "retrying")
                apply()
                self.poll_until_failed()
                self.assertTrue(self.status().startswith("failed; "), self.status())

    def test_other_send_exit_fails_and_retries(self):
        self.waker.send_rc(3)
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        self.assertEqual(len(self.waker.says()), serve.WAKE_FAILED_ATTEMPTS)
        self.assertEqual(self.status(), "failed; send failed (exit 3)")
        self.waker.send_rc(0)
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), serve.WAKE_FAILED_ATTEMPTS, "failed backs off: no attempt next poll")
        self.assertEqual(self.status(), "failed; send failed (exit 3)")
        self.backoff_passes()
        self.controller.poll()
        self.assertEqual(self.status(), "sent")
        self.controller.poll()
        self.assertEqual(len(self.waker.says()), serve.WAKE_FAILED_ATTEMPTS + 1)

    def test_first_failed_attempt_retries_and_a_later_attempt_delivers(self):
        """host-wake #acceptance-retry-delivers; #acceptance-retry-page (host half: retrying, not failed)."""
        for fail in (lambda: self.waker.agent("w1:pOwner", False), lambda: self.waker.send_rc(3)):
            with self.subTest(fail=fail):
                self.waker.agent("w1:pOwner")
                self.waker.send_rc(0)
                self.controller = self.new_controller()
                for path in self.human.iterdir():
                    path.unlink()
                (self.waker.data / "send.log").unlink(missing_ok=True)
                self.event("100-handoff-a.json")
                fail()
                self.controller.poll()
                self.assertEqual(self.status(), "retrying")
                self.waker.agent("w1:pOwner")
                self.waker.send_rc(0)
                before = len(self.waker.says())
                self.controller.poll()
                self.controller.poll()
                self.assertEqual(len(self.waker.says()), before + 1, "one prompt for the batch from the later attempt")
                self.assertEqual(self.status(), "sent")

    def test_failed_wake_recovers_with_one_prompt(self):
        """host-wake #acceptance-failed-recovers: failing past the retries, then recovering, wakes once within a few
        minutes of the recovery."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        self.polls_every_3s(20 * 60)
        self.assertEqual(self.status(), "failed; owner pane not found (exit 1)")
        self.assertEqual(self.waker.says(), [])
        self.waker.agent("w1:pOwner")
        recovered = self.now
        self.polls_every_3s(10 * 60, until=lambda: self.waker.says())
        self.assertLessEqual(self.now - recovered, 4 * 60, "woken within a few minutes")
        self.polls_every_3s(10 * 60)
        self.assertEqual([said[:2] for said in self.waker.says()], [["w1:pOwner", str(self.spec)]])
        self.assertEqual(self.status(), "sent")

    def test_failed_wake_backs_off_to_every_few_minutes(self):
        """host-wake #acceptance-failed-backoff: an hour's wait with the owner pane gone runs that batch's attempts at
        each poll until failed, then less and less often, at most every few minutes, never once per poll."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        attempts = self.polls_every_3s(60 * 60)
        self.assertEqual(self.status(), "failed; owner pane not found (exit 1)")
        self.assertEqual(attempts[:serve.WAKE_FAILED_ATTEMPTS], list(range(serve.WAKE_FAILED_ATTEMPTS)), "each poll")
        gaps = [(later - earlier) * serve.WAKE_POLL_SECONDS
                for earlier, later in zip(attempts[serve.WAKE_FAILED_ATTEMPTS - 1:], attempts[serve.WAKE_FAILED_ATTEMPTS:])]
        self.assertGreater(gaps[0], serve.WAKE_POLL_SECONDS, "never once per poll after failed")
        self.assertEqual(gaps, sorted(gaps), "less and less often")
        self.assertGreaterEqual(gaps[-1], 2 * 60, "every few minutes")
        self.assertLessEqual(max(gaps), 4 * 60, "at most every few minutes")
        self.assertLess(len(attempts), 40, "a gone owner costs a few attempts an hour, not one per poll (1200)")
        self.assertEqual(self.waker.says(), [])

    def test_failed_reason_is_the_last_line_of_the_providers_error_output(self):
        """host-wake #acceptance-failed (host half), #wake-rule-reason: the check's or send's last error line."""
        self.waker.agent("w1:pOwner", False)
        self.waker.check_error("looking up w1:pOwner\npane w1:pOwner not found in session\n\n")
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        self.assertEqual(self.status(), "failed; pane w1:pOwner not found in session")
        self.waker.agent("w1:pOwner")
        self.waker.send_rc(4)
        self.waker.send_error("pane closed while writing\n")
        self.backoff_passes()
        self.controller.poll()  # still failed: the attempts in a row keep failing, now at the send
        self.assertEqual(self.status(), "failed; pane closed while writing")

    def test_silent_check_failure_reason_is_owner_pane_not_found_with_its_exit_code(self):
        """host-wake #acceptance-failed-silent (host half): the check exits 1 and writes nothing."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        self.assertEqual(self.status(), "failed; owner pane not found (exit 1)")

    def test_failed_reason_names_a_command_that_could_not_start_or_timed_out(self):
        """host-wake #wake-rule-reason: no exit code, so the reason says the command could not start or timed out."""
        self.event("100-handoff-a.json")
        self.waker.check.unlink()
        self.poll_until_failed()
        self.assertRegex(self.status(), r"^failed; check could not start: \S")
        self.assertIn(str(self.waker.check), urllib.parse.unquote(self.status()), "the reason names the command")
        self.waker.check.write_text("#!/bin/sh\nsleep 5\n")
        self.waker.check.chmod(0o755)
        saved = serve.WAKE_CHECK_TIMEOUT_SECONDS
        serve.WAKE_CHECK_TIMEOUT_SECONDS = 0.3
        self.addCleanup(setattr, serve, "WAKE_CHECK_TIMEOUT_SECONDS", saved)
        self.backoff_passes()
        self.controller.poll()
        self.assertEqual(self.status(), "failed; check timed out")

    def test_reason_is_header_safe_and_decodes_to_the_providers_words(self):
        """The reason travels in the X-Spec-Chat-Wake header: header-safe, and the page decodes the provider's words."""
        words = "pane «w1:pOwner» 100% gone; see log"
        self.waker.agent("w1:pOwner", False)
        self.waker.check_error(words + "\n")
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        value = self.status()
        value.encode("ascii")
        state, reason = value.split("; ", 1)
        self.assertEqual((state, urllib.parse.unquote(reason)), ("failed", words))

    def test_a_long_reason_is_cut_short_for_the_header_and_page(self):
        """host-wake #wake-rule-reason travels in the X-Spec-Chat-Wake header: a provider's long line is cut short so
        the percent-encoded header stays small for proxies with small header buffers."""
        words = "pane gone: " + "«é»" * 500  # within the error output tail kept, past the reason's length
        self.waker.agent("w1:pOwner", False)
        self.waker.check_error(words + "\n")
        self.event("100-handoff-a.json")
        self.poll_until_failed()
        state, reason = self.status().split("; ", 1)
        reason = urllib.parse.unquote(reason)
        self.assertEqual(state, "failed")
        self.assertLessEqual(len(reason), 200)
        self.assertTrue(reason.startswith("pane gone: «é»"), reason)
        self.assertLess(len(self.status()), 2048)

    def test_each_send_writes_one_log_line(self):
        """host-wake #acceptance-log-send: one line for the send naming the spec, owner, step, and exit code; a check
        that exits 0 writes nothing."""
        self.event("100-handoff-a.json")
        lines = self.polled_log(3)
        self.assertEqual(lines, ['review-serve: wake send spec=%s owner=w1:pOwner exit=0 error=""' % self.spec])

    def test_each_failed_send_writes_a_log_line_with_its_error_output(self):
        """host-wake #acceptance-log-send-failed."""
        self.waker.send_rc(3)
        self.waker.send_error("cannot write to pane\nretry later\n")
        self.event("100-handoff-a.json")
        lines = self.polled_log(serve.WAKE_FAILED_ATTEMPTS + 2)
        self.assertEqual(lines, ['review-serve: wake send spec=%s owner=w1:pOwner exit=3 '
                                 'error="cannot write to pane\\nretry later"' % self.spec],
                         "host-wake #wake-rule-log: a stuck failing send logs once")

    def test_each_failed_check_writes_a_log_line_with_any_error_output(self):
        """host-wake #acceptance-log-check-failed: with error output and without it."""
        self.waker.agent("w1:pOwner", False)
        self.event("100-handoff-a.json")
        silent = 'review-serve: wake check spec=%s owner=w1:pOwner exit=1 error=""' % self.spec
        self.assertEqual(self.polled_log(serve.WAKE_FAILED_ATTEMPTS + 2), [silent], "a stuck failing check logs once")
        self.waker.check_error("no pane w1:pOwner\n")
        self.backoff_passes()
        self.assertEqual(self.polled_log(2), [silent[:-len('""')] + '"no pane w1:pOwner"'], "a new reason logs once")

    def test_a_send_deferred_again_for_the_same_batch_writes_no_second_line(self):
        """host-wake #wake-rule-log."""
        self.waker.send_rc(75)
        self.event("100-handoff-a.json")
        deferred = 'review-serve: wake send spec=%s owner=w1:pOwner exit=75 error=""' % self.spec
        self.assertEqual(self.polled_log(3), [deferred])
        self.event("200-handoff-b.json")
        self.assertEqual(self.polled_log(2), [deferred], "a new batch's first deferral is logged")
        self.waker.send_rc(0)
        self.assertEqual(self.polled_log(2), [deferred[:-len('75 error=""')] + '0 error=""'])

    def test_only_an_attempt_repeating_the_previous_one_writes_no_line(self):
        """host-wake #wake-rule-log: step, exit code, and reason against the previous attempt for the batch, so send exits
        75, 3, 75 log three lines; a new batch logs its first attempt again."""
        self.event("100-handoff-a.json")
        line = 'review-serve: wake send spec=%s owner=w1:pOwner exit=%s error=""'
        logged = []
        for code in (75, 3, 3, 75, 75):
            self.waker.send_rc(code)
            logged += self.polled_log()
        self.assertEqual(logged, [line % (self.spec, code) for code in (75, 3, 75)])
        self.event("200-handoff-b.json")
        self.assertEqual(self.polled_log(2), [line % (self.spec, 75)])

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
        """host-wake #acceptance-no-provider-report (no pane prompted; review-host.py proves the report)."""
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

    def test_registering_with_the_pane_is_the_whole_wake_setup(self):
        """host-wake #acceptance-deleted: the skill names registering with the pane as the whole wake setup, beside
        turn-yielded and manual-resume, and no external wake mode, wake adapter, adapter environment, or monitor."""
        adapters = (ROOT / "skill/review-spec/references/cli-adapters.md").read_text(encoding="utf-8")
        self.assertIn("Registering the spec with the lane command and the owner pane id is the whole wake setup", adapters)
        for mode in ("## Turn-yielded", "## Host-wake", "## Manual-resume"):
            self.assertIn(mode, adapters)
        for path in (ROOT / "skill/review-spec").rglob("*.md"):
            text = path.read_text(encoding="utf-8").lower()
            for word in ("external wake", "external-wake", "wake adapter", "wake-adapter", "wake_"):
                self.assertNotIn(word, text, str(path))
            self.assertNotRegex(text, r"\bmonitor\b", str(path))  # the word, not monitoring or monitored

    def test_no_pane_tool_is_named(self):
        """review-service #acceptance-wake-only: Herdr appears only as the session that scopes the service, by its name
        Herdr and its key HERDR_SESSION; every other spelling, in any case (a command such as herdr or herdr-..., or
        another HERDR_ variable), names the pane tool. The scan reads the tracked sources only, never generated files."""
        roots = ["skill", "DESIGN.md", "README.md", ":(glob)docs/specs/*.spec.html"]
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", *roots],
                                 capture_output=True, text=True, check=True).stdout.split("\0")
        paths = [ROOT / name for name in tracked if name and "vendor" not in Path(name).parts]
        self.assertIn(ROOT / "skill/review-spec/assets/review-serve.py", paths)
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            named = {m.group() for m in re.finditer(r"herdr[\w-]*", text, re.IGNORECASE)}
            self.assertLessEqual(named, {"Herdr", "HERDR_SESSION"}, str(path))


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
            state = serve.MountState(serve._resource_records(serve._registry_document(str(registry)), trust=True),
                                     str(registry))
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
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XDG_STATE_HOME=str(work / "xdg"))
            env.pop("HERDR_SESSION", None)
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
            failed = "failed; owner pane not found (exit 1)"  # host-wake #acceptance-failed-silent: the reason travels
            self.assertEqual(wait_for(failed, serve.WAKE_POLL_SECONDS * (serve.WAKE_FAILED_ATTEMPTS + 2)), failed)
            self.assertEqual(waker.says(), [])
            waker.agent("w1:pGone")
            started = time.monotonic()
            recover = serve.WAKE_POLL_SECONDS * 3 + 2  # failed's first wait, two polls, then the poll that sends
            self.assertEqual(wait_for("sent", recover), "sent")
            self.assertLess(time.monotonic() - started, recover)
            time.sleep(3.5)
            self.assertEqual(len(waker.says()), 1)


if __name__ == "__main__":
    unittest.main()
