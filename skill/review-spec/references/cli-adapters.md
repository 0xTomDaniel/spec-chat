# Host wake adapters

The spool protocol is portable.
Wake ownership is host-specific and must be explicit.
Every open review ends in exactly one terminal control state.

Terminal control selects the checker owner, not the hosting lifetime. While a
remote review is parked, the same public server, captured public URL, and
checker remain alive. The public URL is not a secret in any security sense. It
is not an authentication boundary. Keep it out of Linear, pull requests, and other public durable records. Empty spool, timeout, no draft, a final assistant
response, and `manual-resume` are non-terminal for hosting. The only hosting
terminal is the processed empty **Finish review** hand-off: the human selected
Finish review, the agent consumed that hand-off, and the exact cursor advance
succeeded. A manual-resume owner must preserve the live URL and resume against
it when the next human message arrives.

## Turn-yielded

Use when the host proves that completion of the yielded tool call re-enters the same open authoring turn.

```sh
scripts/review-control.sh yielded <spec-root> .cursor-<cli-or-session> 3600 3
```

Keep the turn open and silent while parked.
Do not send a final response.
A background shell, watcher PID, unified exec session, or returned tool-session id is not proof of same-turn reactivation.

Claude Code may use this state only when its harness callback demonstrably re-invokes the open session.
Codex may use it only through a yielded tool wait that keeps the current turn active.
If Stop, cancellation, timeout ownership, or final response closes the turn, this state ends immediately.

## External-wake

Use for a finished-looking idle experience only when a foreground host-owned monitor can prompt the exact owning conversation.

```sh
scripts/review-control.sh external \
  <spec-root> .cursor-<cli-or-session> \
  <owner-id> <owner-session> <wake-adapter> [args...]
```

The monitor is host-neutral.
It reads complete handoff readiness without advancing the processing cursor, computes one in-memory batch identity, and invokes the adapter once while that batch remains unchanged.
The review-control wrapper holds one nonblocking local kernel lock for the canonical collection root and cursor name.
A user-owned absolute runtime namespace remains identical across Herdr panes and ordinary shells even when their environment variables differ.
A second yielded or external owner fails visibly; process exit releases the lock automatically.
This is not a lease, heartbeat, fencing protocol, or persistent coordinator.
The adapter owns only wake.
The reactivated owner performs the mandatory zero-wait scan, processing transaction, cursor advance, and next control-state selection.

### Herdr

Herdr is the preferred adapter when the authoring conversation is already in a Herdr pane.
Run the monitor in a separate visible Herdr shell pane, not in the owner pane and not as a detached agent processor.
Pass the exact owner `pane_id` and current `terminal_id` from `herdr agent get <pane-id>`.
Verify that identity before starting the monitor; no synthetic handoff or batch values are needed:

```sh
set -e
SPEC_CHAT_OWNER_ID=wC:pOwner SPEC_CHAT_OWNER_SESSION=term_exact \
  scripts/wake-herdr.py --verify-only
scripts/review-control.sh external \
  docs .cursor-codex-session \
  wC:pOwner term_exact \
  scripts/wake-herdr.py
```

`wake-herdr.py` refuses a changed pane or terminal identity and uses `herdr-say` for modal-safe delivery.
If the owner is busy, the adapter defers until it is idle; no second Codex process is started.
If identity validation or wake transport fails, the monitor exits with an explicit manual-resume downgrade.
Reuse the live verified monitor for the same collection, cursor, pane, and terminal; a resumed authoring turn still performs its zero-wait scan without launching another monitor.

Other hosts may provide any executable adapter through the same environment contract:

- `SPEC_CHAT_OWNER_ID`
- `SPEC_CHAT_OWNER_SESSION`
- `SPEC_CHAT_BATCH_ID`
- `SPEC_CHAT_READY_SPEC`
- `SPEC_CHAT_READY_EVENTS`
- `SPEC_CHAT_CURSOR_NAME`

## Manual-resume

Use when neither a verified same-turn yield nor a verified host adapter exists.

```sh
scripts/review-control.sh manual
```

Return a final response that says automatic wake is not active and a new human chat message is required. Keep the existing public server, captured URL, and checker alive. On that message, resume the checker against the same URL; do not start a second server or treat the empty spool as review completion.
On that message, discard any old watcher or tool session, run the zero-wait collection scan, and drain every complete batch before new work.

The browser independently changes an unacknowledged handoff to: automatic wake did not occur; send a new chat message to resume.
Durable spools and unchanged cursors make this lossless.

## Forbidden detached processing

Do not invoke `codex exec`, Claude headless mode, or another agent process from a watcher while an interactive owner may exist.
That creates a second processor and can race the authoring thread.
`scripts/codex-review.sh` is retained only as a compatibility tombstone that exits with guidance.

## Recovery invariant

Files are the contract and session continuation is an optimization.
Every resumed turn begins with a zero-wait scan.
Every successful batch externalizes durable agreements before advancing exactly the reported filenames in its cursor.
