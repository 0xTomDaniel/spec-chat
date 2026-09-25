# Review control states

The spool protocol is portable.
Wake ownership is host-specific and must be explicit.
Every open review ends in exactly one terminal control state.

Terminal control selects the checker owner, not the hosting lifetime. While a
remote review is parked, the same public server, captured public URL, and
checker remain alive. The public URL is not a secret in any security sense. It
is not an authentication boundary. Keep it out of Linear, pull requests, and other public durable records. Empty spool, timeout, no draft, a final assistant
response, and `manual-resume` are non-terminal for hosting. The only hosting
terminal is the processed empty **spec acceptance** hand-off: the human selected
Accept spec, the agent consumed that hand-off, and the exact cursor advance
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

The review-control wrapper holds one nonblocking local kernel lock for the canonical collection root and cursor name.
A user-owned absolute runtime namespace remains identical across Herdr panes and ordinary shells even when their environment variables differ.
A second yielded owner fails visibly; process exit releases the lock automatically.
This is not a lease, heartbeat, fencing protocol, or persistent coordinator.

## Host-wake

Use for a finished-looking idle experience when the long-lived review host can prompt the owner pane.
Registering the resource with the lane record owner pane id is the whole wake setup:

```sh
scripts/review-host.py register --slug <lane-key> \
  --resource <project>=<root>:<spec-path>@<base> \
  --owner <owner-pane-id> --checker <checker> --cursor-name .cursor-<cli-or-session>
```

For an existing row, `<base>` is that row's current `base` in `registry.toml`, never the lane start base: an upsert takes the base it is given, so passing the start base would discard the last reviewed version.
Only the first registration of a new row uses the lane start base.

Registration prints `wake=verified owner=<pane>` only when `herdr agent get` resolves that pane, and `wake=unavailable owner=<pane>` otherwise; registration never fails on wake.
`wake=verified` selects `host-wake` and allows a final response.
`wake=unavailable` selects `manual-resume`.
The owner is the pane id, never an agent or tab name.

The host polls every registered spool every 3 seconds and runs `herdr-say` to the row's owner pane once per unchanged completed hand-off batch.
It defers while the owner is working or `herdr-say` exits 75, and retries each poll after a failed resolve or delivery, so a re-registered owner is woken.
It never reads comments, edits the spec, writes the spool, advances cursors, or starts another processor.
The woken owner performs the zero-wait scan, batch transaction, cursor advance, and parks.

## Manual-resume

Use when neither a verified same-turn yield nor verified host wake exists.

```sh
scripts/review-control.sh manual
```

Return a final response that says automatic wake is not active and a new human chat message is required. Keep the existing public server, captured URL, and checker alive. On that message, resume the checker against the same URL; do not start a second server or treat the empty spool as review completion.
On that message, discard any old watcher or tool session, run the zero-wait collection scan, and drain every complete batch before new work.

The browser independently changes an unacknowledged handoff to: automatic wake did not occur; send a new chat message to resume.
When the review host reports a failed wake, it shows: wake failed; send a new chat message to resume.
Durable spools and unchanged cursors make this lossless.

## Forbidden detached processing

Do not invoke `codex exec`, Claude headless mode, or another agent process from a watcher while an interactive owner may exist.
That creates a second processor and can race the authoring thread.

## Recovery invariant

Files are the contract and session continuation is an optimization.
Every resumed turn begins with a zero-wait scan.
Every successful batch externalizes durable agreements before advancing exactly the reported filenames in its cursor.
