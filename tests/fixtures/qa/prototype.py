#!/usr/bin/env python3
"""QA fixture prototype (qa.toml services.prototype; mockups #prototype): a tiny product on loopback, the running
prototype a prototype mockup frames. It serves the weekly report screen built from the repo's fixture data
(base/fixtures/weekly-totals.json) at two routes: reports/weekly, the teams' totals as cards, a team's last three weeks
opening on a press with the product's own motion, or at load for the team its address names (?team=), and an export
dialog; and reports/weekly-table, another direction of that screen (mockups #directions), every team's last three weeks
in one table. Product code: it carries nothing of Spec Chat's, and serves each route only at its root, the cards page
loading its script and style there by absolute paths, with helmet's default Content-Security-Policy and
X-Frame-Options, as an Express product ships; the review service serves it at the lane's prototype origin
(review-service #prototype-proxy).

usage: prototype.py <port>
"""

import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).resolve().parent / "base" / "fixtures" / "weekly-totals.json"
ROUTE = "/reports/weekly"
TABLE_ROUTE = "/reports/weekly-table"
# helmet's defaults, which each route's page is served with
POLICY = ("default-src 'self';base-uri 'self';font-src 'self' https: data:;form-action 'self';frame-ancestors 'self';"
          "img-src 'self' data:;object-src 'none';script-src 'self';script-src-attr 'none';"
          "style-src 'self' https: 'unsafe-inline';upgrade-insecure-requests")
STYLE_PATH, SCRIPT_PATH = "/assets/report.css", "/assets/report.js"
STYLE = """
:root{--ui-page:#ffffff;--ui-surface:#ffffff;--ui-surface-soft:#f5f5f5;--ui-ink:#333333;--ui-muted:#525252;
--ui-border:#dfdfdf;--ui-action:#1d4ed8;--ui-focus:#1d4ed8;--ui-font:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--ui-space-1:4px;--ui-space-2:8px;--ui-space-3:12px;--ui-space-4:16px;--ui-space-5:24px;--ui-radius:8px;--ui-motion-duration:150ms;
--ui-motion-ease:ease-out}
*{box-sizing:border-box}
body{margin:0;background:var(--ui-page);color:var(--ui-ink);font:16px/1.5 var(--ui-font)}
.app-bar{display:flex;justify-content:space-between;align-items:center;padding:var(--ui-space-4) var(--ui-space-5);
border-bottom:1px solid var(--ui-border);font-weight:600}
.app-bar .week{color:var(--ui-muted);font-weight:500}
main{display:flex;gap:var(--ui-space-5);align-items:flex-start;padding:var(--ui-space-5)}
.cards{display:flex;flex-direction:column;gap:var(--ui-space-3);flex:0 0 320px}
.team-card{display:flex;justify-content:space-between;width:100%;padding:var(--ui-space-4);border:1px solid var(--ui-border);
border-radius:var(--ui-radius);background:var(--ui-surface);color:inherit;font:inherit;cursor:pointer;
transition:background var(--ui-motion-duration) var(--ui-motion-ease)}
.team-card:hover,.team-card[aria-pressed=true]{background:var(--ui-surface-soft)}
.team-card:focus-visible,button:focus-visible{outline:2px solid var(--ui-focus);outline-offset:2px}
.total{font-weight:600}
.team-panel{flex:1 1 auto;padding:var(--ui-space-5);border:1px solid var(--ui-border);border-radius:var(--ui-radius);
opacity:0;transition:opacity var(--ui-motion-duration) var(--ui-motion-ease)}
.team-panel.open{opacity:1}
.team-panel h2{margin:0 0 var(--ui-space-3);font-size:20px}
.weeks{list-style:none;margin:0 0 var(--ui-space-4);padding:0}
.weeks li{display:flex;justify-content:space-between;padding:var(--ui-space-2) 0;border-bottom:1px solid var(--ui-border)}
.export{padding:var(--ui-space-2) var(--ui-space-4);border:0;border-radius:var(--ui-radius);background:var(--ui-action);
color:var(--ui-page);font:inherit;font-weight:600;cursor:pointer}
.export:disabled{opacity:.5;cursor:not-allowed}
.dialog{margin-top:var(--ui-space-4);padding:var(--ui-space-4);border:1px solid var(--ui-border);border-radius:var(--ui-radius)}
[hidden]{display:none!important}
.teams{width:100%;max-width:760px;border-collapse:collapse;font:inherit;font-variant-numeric:tabular-nums}
.teams th,.teams td{padding:var(--ui-space-3) var(--ui-space-4);border-bottom:1px solid var(--ui-border);text-align:right}
.teams th:first-child,.teams td:first-child{text-align:left}
.teams th{color:var(--ui-muted);font-weight:500}
.teams .now{font-weight:600}
@media (max-width:640px){main{flex-direction:column}.cards{flex:none;width:100%}.team-panel{width:100%}
.teams{font-size:13px}.teams th,.teams td{padding:var(--ui-space-2) var(--ui-space-1)}}
@media (prefers-reduced-motion:reduce){.team-card,.team-panel{transition:none}}
"""
SCRIPT = """
const data = JSON.parse(document.getElementById('totals').textContent);
const panel = document.getElementById('team-panel'), dialog = document.getElementById('export-dialog');
const cards = [...document.querySelectorAll('.team-card')];
function openTeam(card) {
  const team = data.teams.find(t => t.team === card.dataset.team);
  document.querySelectorAll('.team-card').forEach(c => c.setAttribute('aria-pressed', String(c === card)));
  panel.querySelector('h2').textContent = team.team;
  panel.querySelector('.weeks').replaceChildren(...data.weeks.map((week, i) => {
    const row = document.createElement('li');
    row.innerHTML = '<span></span><span></span>';
    row.firstChild.textContent = week;
    row.lastChild.textContent = team.totals[i].toLocaleString('en-US');
    return row;
  }));
  dialog.hidden = true;
  panel.hidden = false;
  requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add('open')));
}
for (const card of cards) card.addEventListener('click', () => openTeam(card));
const named = cards.find(card => card.dataset.team === new URLSearchParams(location.search).get('team'));
if (named) openTeam(named);
document.getElementById('export').addEventListener('click', () => {
  dialog.querySelector('code').textContent = panel.querySelector('h2').textContent.toLowerCase() + '-' + data.weeks.at(-1) + '.csv';
  dialog.hidden = false;
});
"""


def page(route):
    """The weekly report at `route`: its team cards, or its team table."""
    data = json.loads(DATA.read_text(encoding="utf-8"))
    week = data["weeks"][-1]
    if route == TABLE_ROUTE:
        head = "".join('<th scope="col">%s</th>' % html.escape(w) for w in data["weeks"])
        rows = "".join("<tr><td>%s</td>%s</tr>" % (html.escape(team["team"]), "".join(
            '<td%s>%s</td>' % (' class="now"' if i == len(team["totals"]) - 1 else "", "{:,}".format(total))
            for i, total in enumerate(team["totals"]))) for team in data["teams"])
        main = '<main><table class="teams"><thead><tr><th scope="col">Team</th>%s</tr></thead><tbody>%s</tbody></table></main>' % (head, rows)
        return ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly report</title><style>%s</style></head>
<body><header class="app-bar"><span>Weekly report</span><span class="week">%s</span></header>
%s</body></html>
""" % (STYLE, html.escape(week), main)).encode("utf-8")
    cards = "".join(
        '<button type="button" class="team-card" data-team="%s"><span class="team">%s</span><span class="total">%s</span></button>'
        % (html.escape(team["team"]), html.escape(team["team"]), "{:,}".format(team["totals"][-1])) for team in data["teams"])
    return ("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly report</title><link rel="stylesheet" href="%s"></head>
<body><header class="app-bar"><span>Weekly report</span><span class="week">%s</span></header>
<main><section class="cards" aria-label="Teams">%s</section>
<section class="team-panel" id="team-panel" hidden><h2></h2><ol class="weeks"></ol>
<button type="button" class="export" id="export">Export CSV</button>
<div class="dialog" id="export-dialog" role="dialog" aria-label="Export" hidden><p><code></code> downloads.</p></div></section></main>
<script type="application/json" id="totals">%s</script><script src="%s"></script></body></html>
""" % (STYLE_PATH, html.escape(week), cards, json.dumps(data).replace("</", "<\\/"), SCRIPT_PATH)).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self.answer(200, b"prototype\n", "text/plain; charset=utf-8")
        if path in (ROUTE, TABLE_ROUTE):
            return self.answer(200, page(path), "text/html; charset=utf-8",
                               [("Content-Security-Policy", POLICY), ("X-Frame-Options", "SAMEORIGIN")])
        if path == STYLE_PATH:
            return self.answer(200, STYLE.encode("utf-8"), "text/css; charset=utf-8")
        if path == SCRIPT_PATH:
            return self.answer(200, SCRIPT.encode("utf-8"), "text/javascript; charset=utf-8")
        return self.answer(404, b"not found\n", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def answer(self, status, body, kind, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
