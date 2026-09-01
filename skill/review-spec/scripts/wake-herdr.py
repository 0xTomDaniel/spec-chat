#!/usr/bin/env python3
"""Wake one exact Herdr owner pane for a ready Spec Chat batch."""

import json
import os
import shutil
import subprocess
import sys


def fail(message):
    print(f"wake-herdr: {message}", file=sys.stderr)
    return 2


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"missing {name}")
    return value


def main():
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "--verify-only"):
        return fail("usage: wake-herdr.py [--verify-only]")
    verify_only = len(sys.argv) == 2
    if not shutil.which("herdr") or not shutil.which("herdr-say"):
        return fail("Herdr commands are unavailable; select manual-resume or another adapter")
    try:
        owner = required("SPEC_CHAT_OWNER_ID")
        session = required("SPEC_CHAT_OWNER_SESSION")
        batch = required("SPEC_CHAT_BATCH_ID")
        spec = required("SPEC_CHAT_READY_SPEC")
        cursor = required("SPEC_CHAT_CURSOR_NAME")
    except ValueError as error:
        return fail(str(error))

    result = subprocess.run(
        ["herdr", "agent", "get", owner], text=True, capture_output=True
    )
    if result.returncode != 0:
        return fail(f"owner pane is unavailable: {owner}")
    try:
        agent = json.loads(result.stdout)["result"]["agent"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return fail("Herdr returned unreadable owner identity")
    if agent.get("pane_id") != owner or agent.get("terminal_id") != session:
        return fail("owner pane or terminal identity changed; refusing to wake a different session")
    if verify_only:
        print(f"herdr-owner=verified pane={owner} terminal={session}")
        return 0
    if agent.get("agent_status") == "working":
        print("wake-herdr: owner is still working; retry after it becomes idle", file=sys.stderr)
        return 75

    message = (
        f"Spec Chat handoff ready for {spec}, batch {batch}. "
        f"Run the mandatory zero-wait scan with cursor {cursor}, process the durable batch, "
        "then establish turn-yielded, external-wake, or manual-resume explicitly. "
        "Do not use detached Codex processing."
    )
    wake = subprocess.run(
        [
            "herdr-say",
            "--kind",
            "command",
            "--artifact",
            spec,
            owner,
            message,
        ],
        text=True,
    )
    if wake.returncode in (0, 75):
        return wake.returncode
    return fail(f"Herdr wake transport failed with exit {wake.returncode}")


if __name__ == "__main__":
    raise SystemExit(main())
