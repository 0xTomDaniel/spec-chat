# Holding pages in a shell

For a shell author: a host, such as a workbench, that frames several pages in one window and wants one comment UI for
all of them. Spec: `docs/specs/shell.spec.html#shell`.

A page that runs Spec Chat's runtime (a spec page, a mockup page, or a host page loading `<url>/static/comment-layer.js`)
draws its own comment UI until a shell holds it. Held, it draws only its pins, waiting dots, question marks, and quote
marks, and turns a pointed click, a pin, an answer button, or a host item's control into a pick; a held spec page keeps
its top line, Jev marks, evidence notes, and focus on changes, and a held mockup page its step bar and Before and After.
A hello that comes after the page drew its own comment UI saves any text in its composer as a draft, then removes that
UI. Spec Chat's comment UI
page, `<url>/comments` on the review service's own address, draws the conversation, the composer, Hand off, Next TBD,
and Accept spec, and alone reads and writes threads. The shell writes no comment code and keeps no thread.

Only a shell at an origin the box owner names in box setup holds a page; under any other parent a page stays alone. The
comment UI page takes messages only from its parent at such an origin and shows nothing until the first one. The review
service's own origin is the one other shell: a mockup page holds the prototype routes it frames, which the service
serves with the comment layer's script added.

## What the shell does

1. Frame `<url>/comments` once per window. Place it beside a page, under another pane, or as a sheet on a phone;
   moving it never reloads it, so its drafts, outbox, and fruit name last as long as the window.
2. Frame each page. On every load of a page's frame, post the hello to that frame's window:
   `{type: "spec-chat-host", opens: ["comments"]}`, with the page's origin as target origin (`"*"` for a frame with no
   origin).
3. Relay by source window, never by content:
   - a page's `spec-chat-publish`, `spec-chat-pick`, and `spec-chat-marks` (its answer) to the comment UI page, adding
     `frame`, a string the shell picks for that page's frame;
   - the comment UI page's `spec-chat-point`, `spec-chat-show`, and `spec-chat-marks` to the frame their `frame` names.
   The comment UI page shows the conversation of the page whose publish the shell relayed last; a pick from another
   frame shows that frame's page.
4. On the comment UI page's `spec-chat-close`, put the comment UI away as for the shell's own close, Escape, or a tap
   outside it. Showing it again, as on a pick, is the shell's choice.

A page that sends nothing after the hello runs no Spec Chat script; what the shell does for it is the shell's.

## The messages

Every message is plain data: none carries markup to draw or an address the receiver loads. A `from` address shows only
as a link the human may follow. A receiver ignores a field it does not know, and an absent field means none, so a page
and a shell of different versions still speak. Each message has its `type`; every relayed one also carries `frame`.

A spot, in pick, show, and marks: `subject` on a host page's thing, else `anchor` and, for a part of its block,
`target`; `quote`; on a mockup `step`; on a video `moment`, in seconds; and `box`, `{left, top, width, height}` as
fractions of the video's frame or, with no video, of the page's view as the human saw it.

| Message | From, to | Fields |
| --- | --- | --- |
| `spec-chat-host` | shell to each page it frames, on every load | `opens`, an array holding `"comments"` |
| `spec-chat-publish` | page to shell, on hello and whenever it changes; relayed to the comment UI page | `page`, the page's own address; `version`, on a spec or mockup page; `items`, each `{subject, scope, kind, label, from, starters, lane}` as its anchor gives them, `starters` an array; `questions`, each `{anchor, text, answers, about, step}`, `answers` its answer buttons' words and `about` its parts' anchors; on a mockup `steps`, each `{name, caption}`, and `step`, the one showing |
| `spec-chat-pick` | page to shell, on a pointed click, a pin pressed, an answer button pressed, or C then a click; relayed to the comment UI page | a spot; for a box, `picture`, `{type, data}`, its image type and its bytes in base64; for an answer button, `answer`, its words; for a pin, `thread`, its thread's id |
| `spec-chat-point` | comment UI page to shell, relayed to the page `frame` names | none to start: the page highlights what is under the pointer, and a pick or Escape ends it; `off`, `true` to end pointing, as when the shell's own Comment turns off |
| `spec-chat-show` | comment UI page to shell, relayed to the page `frame` names | a spot, and `thread` when it shows one |
| `spec-chat-marks` | comment UI page to shell, relayed to the page `frame` names | `threads`, each a spot with `id`, `number`, `state` (`open`, `waiting`, or `resolved`), and `status` (`draft`, `pending`, `acknowledged`, or `resolved`); `answers`, each `{anchor, answer}`, a question's chosen answer; a mockup page sending marks to the prototype frames it holds adds `questions`, `active`, `asked`, `zoom`, and `size`, which a shell never sends |
| `spec-chat-marks`, the page's answer | page to shell, relayed to the comment UI page | `missing`, the ids of the threads whose quote its text no longer holds; a page that holds no quoted text may send none, and no answer means none is missing |
| `spec-chat-close` | comment UI page to shell | none |

No message names a spot in words. To name each thread's spot, and to answer a question as its page's own button does,
the comment UI page reads the spec or mockup page `page` names from its own review service, and only from it; a host
page's items name themselves by their `label`.

Held, a host page shows every anchored item's control: only the comment UI page reads which scopes have a row, and it
opens no conversation for an item in a scope with none.

Keys and clicks in a frame stay the page's: it points itself, and its own C starts pointing as its toolbar's Comment
does alone.

## A page speaking the messages natively

Any page may speak the page side of these messages itself, without Spec Chat's script, such as a page that draws on a
canvas (`#shell-native`). On the hello, it publishes `page`, its own address, and `items`: each thing it shows as
`{subject, scope, kind, label, from, starters, lane}`, the fields an anchored element's attributes give, though the
thing has no page element. The comment UI page treats it as a held host page: it reads the scopes' rows on its own
service, and opens no conversation for a thing in a scope with none.

- Picks name a thing by its `subject`: its comment control, a pin (with `thread`), or, after `spec-chat-point`, what is
  under a click. A drag after `spec-chat-point` is a box: `box` as fractions of the page's view, and, when the page makes
  one, `picture`, `{type, data}`, a PNG, JPEG, or WebP image of the boxed spot as the human saw it, the box drawn on it,
  its bytes in base64, at most 1 MB. The comment keeps the picture and its focused thread shows it; a larger one, or one
  in another format, is left out and the comment saved without it.
- The page draws its own marks from `spec-chat-marks`: each thread's pin on its thing by `subject`, numbered, colored by
  `status`, with a dot while `state` is `waiting`.
- `spec-chat-show` names a thing and, for a box, `box`: the page shows the thing and draws the box on its view.
- With no quoted text, it may send no marks answer.

## A phone shell's frames

A shell whose frames have no origin (sandboxed without `allow-same-origin`) gives the comment UI page no storage, so its
fruit name and drafts last only until a reload. To keep them, keep one browser id in the shell's own storage and pass it
in the comment UI page's address fragment: `<url>/comments#browser=<id>`, the id up to 64 letters, digits, hyphens, and
underscores. With no storage the page takes that id as its browser and the fruit name its earlier events carry, so the
name lasts across reloads.

A frame with no origin reads the review service as another origin, so the service must be public (`### Box setup`).

## Test shell

`tests/fixtures/qa/shell.py` is a working shell: one held frame, the comment UI page beside it or as a phone sheet, a
`/bare/` variant with frames with no origin, a `/native/` variant holding its own canvas page that speaks the messages
natively, and a log of every message it relays.
