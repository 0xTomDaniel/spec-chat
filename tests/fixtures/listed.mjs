import assert from 'node:assert/strict';

// What `read` returns once it lists the events a page action wrote. A page draws a save, reply, edit, hand-off,
// answer, or resolve from its outbox before its POST to /api/events lands (runtime.js save), so a read of the review
// service, or of the spool on disk, right after the page shows the action may not hold it yet. `wants` is how many
// events the read lists, or a test of them; `read` may answer at once or as a promise.
export const listed = async (read, wants, what, ms = 10000) => {
  const holds = typeof wants === 'number' ? events => events.length >= wants : wants;
  for (const end = Date.now() + ms; ; await new Promise(r => setTimeout(r, 100))) {
    const events = await read();
    if (holds(events)) return events;
    if (Date.now() >= end) assert.fail('timed out: ' + what + ' stored');
  }
};
