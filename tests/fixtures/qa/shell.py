#!/usr/bin/env python3
"""Test shell (shell #shell, #proof-held): a shell that frames one of a review service's pages and the service's
comment UI page, says hello with comments to the page it holds, and relays the shell messages between them by window,
as a workbench does. It writes no comment code and keeps no thread. Served at an origin the fixture box names with
--shell (reset.sh, QA_URL_SHELL), its page is held; the same shell at another origin (qa.toml shell-other) is not.

usage: shell.py <service-url> <port>

  /<path>[?<query>]       the shell: its bar, the service's page at <path> held in one frame, and the comment UI page
                          framed beside it, or as a sheet over the lower part of a phone's screen
  /bare/<path>[?<query>]  the same, its frames sandboxed with no origin, so with no storage; the comment UI page gets the
                          shell's own browser id, kept in the shell's storage, as #browser=<id> (#shell-phone-id)
  /late/<path>[?<query>]  the same, its hello sent only when the bar's Hold is pressed, so it reaches a page that drew its
                          own comment UI (#shell-roles-late)
  /native/<collection>    the same, holding the shell's own canvas page at /canvas/<collection> in place of a service page
  /canvas/<collection>    a factory floor drawn on a canvas, a page that speaks the page side of the shell messages itself,
                          with no Spec Chat script (#shell-native): its lane cards are things with no page element, each
                          in the collection's scope, naming the lane spools.py STAGES names at QA; it draws their comment
                          controls and the pins the marks name, picks a card, a pin, or after point a card or a box
                          dragged across the floor, with a picture of the boxed spot, the box drawn on it (threads
                          #box-picture), and draws a box a show names on its view; it sends no marks answer, holding no
                          quoted text (#shell-fields-marks-back)

Each load of the held frame gets a hello, once the comment UI page has loaded, so no publish relayed to it is lost
(shell #shell-msg-hello); a pick shows the comment UI, its close control puts it away, and the bar's
Comments shows it again. The bar's Messages lists every shell message the shell relayed, and the page's last publish
whole, so what the shell receives shows. Loopback only: a test fixture, never a product shell.
"""

import html
import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Frames with no origin: everything a review page needs, never allow-same-origin.
SANDBOX = "allow-scripts allow-forms allow-popups"
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test shell</title><style>
html,body{margin:0;height:100%%}
body{font:500 13px/1.4 system-ui,sans-serif;background:#f5f5f5;color:#333}
.bar{position:fixed;left:0;right:0;top:0;height:40px;box-sizing:border-box;display:flex;align-items:center;gap:12px;
  padding:0 16px;background:#f5f5f5;border-bottom:1px solid #dfdfdf;z-index:3}
.tab{padding:4px 12px;border-radius:6px;background:#fff;box-shadow:0 1px 2px rgba(51,51,51,.14);white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;max-width:40vw}
.bar button{margin-left:auto;font:600 12px system-ui,sans-serif;min-height:32px;padding:0 12px;border:1px solid #dfdfdf;
  border-radius:6px;background:#fff;cursor:pointer}
.bar details{position:relative}
.bar details pre{position:absolute;right:0;top:28px;width:min(520px,90vw);max-height:60vh;overflow:auto;margin:0;padding:8px;
  background:#fff;border:1px solid #dfdfdf;border-radius:8px;font:11px/1.4 ui-monospace,monospace;white-space:pre-wrap}
iframe{position:fixed;display:block;box-sizing:border-box;border:0;background:#fff}
#held{left:0;top:40px;width:calc(100%% - 380px);height:calc(100%% - 40px)}
#comments{right:0;top:40px;width:380px;height:calc(100%% - 40px);border-left:1px solid #dfdfdf;z-index:2}
body.closed #held{width:100%%}
body.closed #comments{display:none}
@media (max-width:640px){
  .bar{gap:8px;padding:0 8px}
  #held{width:100%%}
  #comments{left:0;right:auto;top:auto;bottom:0;width:100%%;height:calc(100%% - 430px);border-left:0;
    border-radius:16px 16px 0 0;box-shadow:0 -12px 32px rgba(51,51,51,.14)}
}
</style></head>
<body>
<div class="bar">Test shell <span class="tab" id="tab">%(tab)s</span>%(hold)s<button type="button" id="open">Comments</button>
<details><summary>Messages</summary><pre id="log"></pre></details></div>
<iframe id="held" title="Held page" %(sandbox)s></iframe>
<iframe id="comments" title="Comments" %(sandbox)s></iframe>
<script>
var SERVICE = %(service)s, PAGE = %(page)s, BARE = %(bare)s, LATE = %(late)s, FRAME = 'page-1';
var held = document.getElementById('held'), comments = document.getElementById('comments');
// The frames' origin, or any while they have none (a sandboxed frame's origin is null): the comment UI page's is the
// service's, the held page's its own, the service's or, for the native page, the shell's.
var TARGET = BARE ? '*' : SERVICE, HELD = BARE ? '*' : new URL(PAGE, location.href).origin;
// A phone shell's frames have no storage: the comment UI page takes the shell's browser id from its address.
var id = '';
if (BARE) {
  try { id = localStorage.getItem('test-shell:browser') || ''; } catch (e) {}
  if (!id) {
    id = Array.from(crypto.getRandomValues(new Uint8Array(12)), function (n) { return (n %% 36).toString(36); }).join('');
    try { localStorage.setItem('test-shell:browser', id); } catch (e) {}
  }
}
// Its frames' loads: the comment UI page's, once; the held page's, each time.
var loaded = { comments: false, held: false };
comments.addEventListener('load', function () { loaded.comments = true; if (!LATE) hello(); }, { once: true });
comments.src = SERVICE + '/comments' + (id ? '#browser=' + id : '');
window.shellLog = [];
function log(direction, data) {
  window.shellLog.push({ direction: direction, data: data });
  var pre = document.getElementById('log');
  var lines = window.shellLog.map(function (m) { return m.direction + ' ' + m.data.type; });
  var last = window.shellLog.filter(function (m) { return m.data.type === 'spec-chat-publish'; }).pop();
  pre.textContent = lines.join('\\n') + (last ? '\\n\\nLast publish:\\n' + JSON.stringify(last.data, null, 2) : '');
  if (last && typeof last.data.page === 'string') document.getElementById('tab').textContent = last.data.page.split('/').pop();
}
function show(open) { document.body.classList.toggle('closed', !open); }
document.getElementById('open').addEventListener('click', function () { show(true); });
// Every load of the held page: the hello, with comments, only once the comment UI page has loaded too, so the publish
// it answers with reaches a page that listens (shell #shell-msg-hello); a late shell's on Hold.
function hello() {
  if (loaded.comments && loaded.held) held.contentWindow.postMessage({ type: 'spec-chat-host', opens: ['comments'] }, HELD);
}
held.addEventListener('load', function () { loaded.held = true; if (!LATE) hello(); });
if (LATE) document.getElementById('hold').addEventListener('click', hello);
held.src = PAGE; // once its load is heard
addEventListener('message', function (event) {
  var data = event.data;
  if (!data || typeof data !== 'object' || typeof data.type !== 'string') return;
  if (event.source === held.contentWindow && /^spec-chat-(publish|pick|marks)$/.test(data.type)) {
    log('page', data);
    if (data.type === 'spec-chat-pick') show(true);
    comments.contentWindow.postMessage(Object.assign({}, data, { frame: FRAME }), TARGET);
  } else if (event.source === comments.contentWindow) {
    log('comments', data);
    if (data.type === 'spec-chat-close') return show(false);
    if (/^spec-chat-(point|show|marks)$/.test(data.type) && data.frame === FRAME) held.contentWindow.postMessage(data, HELD);
  }
});
</script></body></html>
"""

# The native page (shell #shell-native): a factory floor drawn on a canvas, no Spec Chat script. Its tokens are
# the design set's light values by value, as a product drawing its own floor would carry them.
CANVAS = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Factory floor</title><style>
html,body{margin:0;height:100%%;overflow:hidden;background:#f5f5f5}
canvas{display:block;width:100vw;height:100vh;touch-action:none;cursor:default}
</style></head>
<body><canvas id="floor" role="img" aria-label="Factory floor: lane cards by station"></canvas>
<script>
var SCOPE = %(scope)s, LANE = 'qa-20';
var INK = '#333333', MUTED = '#525252', BORDER = '#dfdfdf', PAGE = '#ffffff', SOFT = '#f5f5f5', ACTION = '#1d4ed8',
  ATTENTION = '#b0540e', PASS = '#005c32', PASS_SOFT = '#e6efea', FAIL = '#a5000f', ATTENTION_SOFT = '#faf4ef',
  FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
var STATIONS = [
  { name: 'Build', cards: [{ id: '1518', title: 'Spec Chat visual shaping' }, { id: '1516', title: 'Test reliability' }] },
  { name: 'Review', cards: [{ id: '1514', title: 'Projects panel', badge: 'QA failed', fail: true },
    { id: '1515', title: 'QA batch', badge: 'QA passed' }] }];
var canvas = document.getElementById('floor'), ctx = canvas.getContext('2d');
var parentOrigin = null, pointing = false, threads = [], shown = null, hover = null, drag = null, layout = [];
// Each lane card a thing with no page element, as its publish names it (#shell-fields-publish).
var items = [];
STATIONS.forEach(function (station) { station.cards.forEach(function (card) {
  items.push({ subject: 'lane-card:qa:' + card.id, scope: SCOPE, kind: 'Lane card', label: 'QA-' + card.id + ' card on the floor',
    from: location.href, starters: [], lane: LANE });
}); });
function size() {
  var ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(innerWidth * ratio);
  canvas.height = Math.round(innerHeight * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  var gap = innerWidth < 640 ? 12 : 24, width = Math.min(230, (innerWidth - gap * (STATIONS.length + 1)) / STATIONS.length);
  layout = [];
  STATIONS.forEach(function (station, i) {
    var x = gap + i * (width + gap), y = 24;
    layout.push({ station: station, rect: { x: x, y: y, w: width, h: Math.min(innerHeight - 48, 340) } });
    station.cards.forEach(function (card, j) {
      layout.push({ subject: 'lane-card:qa:' + card.id, card: card, rect: { x: x + 12, y: y + 44 + j * 84, w: width - 24, h: 72 } });
    });
  });
  draw();
}
function cardAt(x, y) {
  return layout.filter(function (l) { var r = l.rect; return l.subject && x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h; })[0] || null;
}
function cardOf(subject) { return layout.filter(function (l) { return l.subject === subject; })[0] || null; }
// Its comment control at the card's top right, and its threads' pins to the control's left, 24 px each.
function controlRect(l) { return { x: l.rect.x + l.rect.w - 28, y: l.rect.y + 4, w: 24, h: 24 }; }
function pinsOf(l) {
  return threads.filter(function (t) { return t.subject === l.subject; }).map(function (t, i) {
    return { thread: t, rect: { x: l.rect.x + l.rect.w - 56 - i * 28, y: l.rect.y + 4, w: 24, h: 24 } };
  });
}
function inside(r, x, y) { return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h; }
function rounded(r, radius) { ctx.beginPath(); ctx.roundRect(r.x, r.y, r.w, r.h, radius); }
function viewBox(box) { return { x: box.left * innerWidth, y: box.top * innerHeight, w: box.width * innerWidth, h: box.height * innerHeight }; }
function draw() {
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  ctx.fillStyle = SOFT;
  ctx.fillRect(0, 0, innerWidth, innerHeight);
  layout.forEach(function (l) {
    var r = l.rect;
    if (l.station) {
      rounded(r, 8); ctx.fillStyle = PAGE; ctx.fill(); ctx.strokeStyle = BORDER; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = MUTED; ctx.font = '600 14px ' + FONT; ctx.fillText(l.station.name, r.x + 12, r.y + 26);
      return;
    }
    rounded(r, 6); ctx.fillStyle = PAGE; ctx.fill(); ctx.strokeStyle = BORDER; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = INK; ctx.font = '600 13px ' + FONT; ctx.fillText('QA-' + l.card.id, r.x + 12, r.y + 22);
    ctx.font = '12px ' + FONT; ctx.fillText(l.card.title, r.x + 12, r.y + 40, r.w - 24);
    if (l.card.badge) {
      var w = ctx.measureText(l.card.badge).width + 16;
      rounded({ x: r.x + 12, y: r.y + 48, w: w, h: 18 }, 9); ctx.fillStyle = l.card.fail ? ATTENTION_SOFT : PASS_SOFT; ctx.fill();
      ctx.fillStyle = l.card.fail ? FAIL : PASS; ctx.font = '600 12px ' + FONT; ctx.fillText(l.card.badge, r.x + 20, r.y + 61);
    }
    var c = controlRect(l);
    rounded(c, 6); ctx.fillStyle = PAGE; ctx.fill(); ctx.strokeStyle = BORDER; ctx.stroke();
    ctx.strokeStyle = MUTED; ctx.lineWidth = 1.5; ctx.beginPath();
    ctx.moveTo(c.x + 6, c.y + 7); ctx.lineTo(c.x + 18, c.y + 7); ctx.lineTo(c.x + 18, c.y + 15); ctx.lineTo(c.x + 10, c.y + 15);
    ctx.lineTo(c.x + 6, c.y + 18); ctx.closePath(); ctx.stroke();
    // The pins the marks name (#shell-native): numbered, in its review status's color, a dot while it waits.
    pinsOf(l).forEach(function (pin) {
      var p = pin.rect, t = pin.thread, done = t.status === 'resolved';
      ctx.beginPath(); ctx.arc(p.x + 12, p.y + 12, 12, 0, Math.PI * 2);
      ctx.fillStyle = done ? PAGE : t.status === 'acknowledged' ? ACTION : ATTENTION; ctx.fill();
      if (done) { ctx.strokeStyle = PASS; ctx.lineWidth = 2; ctx.stroke(); }
      ctx.fillStyle = done ? PASS : PAGE; ctx.font = '600 12px ' + FONT; ctx.textAlign = 'center';
      ctx.fillText(String(t.number), p.x + 12, p.y + 16); ctx.textAlign = 'start';
      if (t.state === 'waiting') { ctx.beginPath(); ctx.arc(p.x + 21, p.y + 3, 4, 0, Math.PI * 2); ctx.fillStyle = ACTION; ctx.fill(); }
    });
  });
  var ring = shown ? cardOf(shown.subject) : hover;
  if (ring) { var g = ring.rect; ctx.strokeStyle = ATTENTION; ctx.lineWidth = 2; rounded({ x: g.x - 4, y: g.y - 4, w: g.w + 8, h: g.h + 8 }, 8); ctx.stroke(); }
  var box = drag && drag.box ? drag.box : shown && shown.box ? viewBox(shown.box) : null;
  if (box) { ctx.strokeStyle = ATTENTION; ctx.lineWidth = 2; rounded(box, 6); ctx.stroke(); }
  window.floor = { pointing: pointing, shown: shown, threads: threads,
    pins: layout.filter(function (l) { return l.subject; }).reduce(function (all, l) {
      return all.concat(pinsOf(l).map(function (pin) { return { subject: l.subject, id: pin.thread.id, number: pin.thread.number,
        status: pin.thread.status, state: pin.thread.state, rect: pin.rect }; })); }, []),
    rect: function (subject) { var l = cardOf(subject); return l && l.rect; },
    control: function (subject) { var l = cardOf(subject); return l && controlRect(l); } };
}
function send(message) { if (parentOrigin) parent.postMessage(message, parentOrigin === 'null' ? '*' : parentOrigin); }
function pick(spot) { pointing = false; hover = null; send(Object.assign({ type: 'spec-chat-pick' }, spot)); draw(); }
// A picture of the boxed spot as the human saw it, the box drawn on it, with a margin around it, at the screen's pixels.
function picture(r) {
  var ratio = window.devicePixelRatio || 1, m = 12;
  var x = Math.max(0, r.x - m), y = Math.max(0, r.y - m);
  var w = Math.min(innerWidth, r.x + r.w + m) - x, h = Math.min(innerHeight, r.y + r.h + m) - y;
  var out = document.createElement('canvas');
  out.width = Math.round(w * ratio); out.height = Math.round(h * ratio);
  var o = out.getContext('2d');
  o.drawImage(canvas, x * ratio, y * ratio, w * ratio, h * ratio, 0, 0, out.width, out.height);
  o.strokeStyle = ATTENTION; o.lineWidth = 2 * ratio;
  o.beginPath(); o.roundRect((r.x - x) * ratio, (r.y - y) * ratio, r.w * ratio, r.h * ratio, 6 * ratio); o.stroke();
  return { type: 'image/png', data: out.toDataURL('image/png').split(',')[1] };
}
canvas.addEventListener('pointerdown', function (e) {
  drag = { x: e.clientX, y: e.clientY, box: null };
  try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
});
canvas.addEventListener('pointermove', function (e) {
  if (drag && pointing) {
    var x = Math.min(drag.x, e.clientX), y = Math.min(drag.y, e.clientY), w = Math.abs(e.clientX - drag.x), h = Math.abs(e.clientY - drag.y);
    drag.box = w >= 6 && h >= 6 ? { x: x, y: y, w: w, h: h } : null;
    return draw();
  }
  var over = pointing ? cardAt(e.clientX, e.clientY) : null;
  if (over !== hover) { hover = over; draw(); }
});
canvas.addEventListener('pointerup', function (e) {
  var d = drag; drag = null;
  if (!d) return;
  if (pointing && d.box) {
    var r = d.box, under = cardAt(r.x + r.w / 2, r.y + r.h / 2);
    if (!under) return draw();
    var f = function (v) { return Math.round(v * 10000) / 10000; };
    var shot = picture(r); // before the pick draws the floor again
    return pick({ subject: under.subject, quote: null, box: { left: f(r.x / innerWidth), top: f(r.y / innerHeight),
      width: f(r.w / innerWidth), height: f(r.h / innerHeight) }, picture: shot });
  }
  var x = e.clientX, y = e.clientY;
  for (var i = 0; i < layout.length; i++) {
    var l = layout[i];
    if (!l.subject) continue;
    var pins = pinsOf(l);
    for (var j = 0; j < pins.length; j++) if (inside(pins[j].rect, x, y)) return pick({ subject: l.subject, thread: pins[j].thread.id, quote: null });
    if (inside(controlRect(l), x, y)) return pick({ subject: l.subject, quote: null });
  }
  var card = pointing ? cardAt(x, y) : null;
  if (card) pick({ subject: card.subject, quote: null });
});
// Keys in the frame stay the page's: its own C starts pointing, Escape ends it (#shell-protocol-keys).
addEventListener('keydown', function (e) {
  if (e.key === 'Escape' && pointing) { pointing = false; hover = null; draw(); }
  if ((e.key === 'c' || e.key === 'C') && !e.metaKey && !e.ctrlKey && !e.altKey && parentOrigin) { pointing = true; draw(); }
});
addEventListener('message', function (event) {
  var data = event.data;
  if (event.source !== parent || !data || typeof data !== 'object') return;
  if (data.type === 'spec-chat-host' && Array.isArray(data.opens) && data.opens.indexOf('comments') >= 0) {
    parentOrigin = event.origin;
    return send({ type: 'spec-chat-publish', page: location.href, items: items });
  }
  if (event.origin !== parentOrigin) return;
  if (data.type === 'spec-chat-point') { pointing = data.off !== true; hover = null; return draw(); }
  if (data.type === 'spec-chat-show') {
    shown = typeof data.subject === 'string' ? { subject: data.subject, box: data.box && typeof data.box === 'object' ? data.box : null } : null;
    return draw();
  }
  if (data.type === 'spec-chat-marks') {
    threads = (Array.isArray(data.threads) ? data.threads : []).filter(function (t) { return t && typeof t.subject === 'string'; });
    return draw();
  }
});
addEventListener('resize', size);
size();
</script></body></html>
"""
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


class Handler(BaseHTTPRequestHandler):
    service = ""  # the review service's origin, scheme://host:port

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        variant = parsed.path.split("/", 2)[1] if parsed.path.count("/") > 1 else ""
        bare, late, native = variant == "bare", variant == "late", variant == "native"
        path = parsed.path[len(variant) + 1:] if bare or late or native or variant == "canvas" else parsed.path
        if variant == "canvas" or native:
            if not SLUG.fullmatch(path[1:]):
                return self._send(404, b"no such collection\n", "text/plain")
            if variant == "canvas":
                return self._send(200, (CANVAS % {"scope": json.dumps(path[1:])}).encode("utf-8"), "text/html; charset=utf-8")
        if path in ("", "/"):
            return self._send(200, b"test shell: open /<path of a page the review service serves>\n", "text/plain")
        page = "/canvas" + path if native else self.service + path + ("?" + parsed.query if parsed.query else "")
        body = PAGE % {
            "tab": html.escape(path.rsplit("/", 1)[-1]), "page": json.dumps(page).replace("<", "\\u003c"),
            "sandbox": 'sandbox="%s"' % SANDBOX if bare else "", "service": json.dumps(self.service),
            "bare": "true" if bare else "false", "late": "true" if late else "false",
            "hold": '<button type="button" id="hold">Hold</button>' if late else "",
        }
        return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, status, body, kind):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: shell.py <service-url> <port>")
    service, port = argv
    url = urllib.parse.urlsplit(service)
    Handler.service = "%s://%s" % (url.scheme, url.netloc)
    ThreadingHTTPServer(("127.0.0.1", int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
