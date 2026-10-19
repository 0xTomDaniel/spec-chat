#!/usr/bin/env python3
"""Test host (review-service #served-rule, #proof-served-seam): serves the review service's pages at its own address,
as any host may. It fetches a page from the service and returns it with a base address naming the address it fetched,
into a frame sandboxed without same-origin access, so the page runs with no origin.

usage: host.py <service-url> <port>

  /frame?page=<address>  the service's page at that address, its base address and the link script first in its head,
                         answered under the same sandbox, so it has no origin even opened alone
  /page?page=<address>   the same page unframed and unsandboxed: another site's page at this host's own origin, as a
                         host page that loads the comment layer from the service is (comment-layer #load)
  /<path>[?<query>]      the host page: one sandboxed frame showing the service's page at that path and query

The frame's location never names the page (/frame), so the page reads only its base address. The link script loads a
tap on any link to the service through /frame, in the same frame, as a host's link handling does (#served-move); a link
to the page's own address and a fragment moves the frame's location fragment (#served-anchor). Loopback only: a test
fixture, never a product host.
"""

import html
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# A frame with no origin: everything a review page needs, never allow-same-origin.
SANDBOX = "allow-scripts allow-forms allow-popups"
HEAD = re.compile(rb"<head\b[^>]*>", re.I)
LINKS = """<script>
addEventListener('click', function (event) {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  var link = event.target instanceof Element && event.target.closest('a[href]');
  if (!link || (link.getAttribute('target') || '_self') !== '_self' || link.hasAttribute('download')) return;
  var own = new URL(document.baseURI), url = new URL(link.getAttribute('href'), own);
  if (url.origin !== own.origin) return;
  event.preventDefault();
  var hash = url.hash;
  url.hash = own.hash = '';
  if (url.href === own.href && hash) location.hash = hash;
  else location.href = new URL('/frame?page=' + encodeURIComponent(url.href) + hash, location.href).href;
});
</script>"""
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test host</title><style>
html,body{margin:0;height:100%%}
body{display:flex;flex-direction:column;font:13px/1.4 system-ui,sans-serif}
header{padding:6px 12px;background:#1f2937;color:#f9fafb}
iframe{flex:1;width:100%%;border:0}
</style></head>
<body><header>Test host: a review page served at another address, in a frame with no origin</header>
<iframe title="Served review page" sandbox="%s" src="%s"></iframe></body></html>
"""


def served(body, address):
    """The fetched page with its base address and the link script first in its head (before any address it holds)."""
    found = HEAD.search(body)
    at = found.end() if found else 0
    first = ('<base href="%s">%s' % (html.escape(address, quote=True), LINKS)).encode("utf-8")
    return body[:at] + first + body[at:]


class Handler(BaseHTTPRequestHandler):
    service = None  # the service's origin, scheme://host:port

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path in ("/frame", "/page"):
            return self._frame(urllib.parse.parse_qs(parsed.query).get("page", [""])[0], sandbox=parsed.path == "/frame")
        page = self.service + parsed.path + ("?" + parsed.query if parsed.query else "")
        src = "/frame?" + urllib.parse.urlencode({"page": page})
        return self._send(200, "text/html; charset=utf-8", (PAGE % (SANDBOX, html.escape(src, quote=True))).encode("utf-8"))

    def _frame(self, page, sandbox=True):
        address = urllib.parse.urlsplit(page)
        if "%s://%s" % (address.scheme, address.netloc) != self.service or not address.path.startswith("/"):
            return self._send(404, "text/plain; charset=utf-8", b"not a page of the service\n")
        try:
            with urllib.request.urlopen(page, timeout=10) as response:
                status, kind, body, page = response.status, response.headers.get("Content-Type", ""), response.read(), response.geturl()
        except urllib.error.HTTPError as error:
            status, kind, body = error.code, error.headers.get("Content-Type", ""), error.read()
        except OSError as error:
            return self._send(502, "text/plain; charset=utf-8", ("service unreachable: %s\n" % error).encode("utf-8"))
        if kind.startswith("text/html"):
            body = served(body, page)
        return self._send(status, kind or "application/octet-stream", body, sandbox=sandbox)

    def _send(self, status, kind, body, sandbox=False):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if sandbox:
            self.send_header("Content-Security-Policy", "sandbox " + SANDBOX)
        self.end_headers()
        self.wfile.write(body)


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: host.py <service-url> <port>")
    service = urllib.parse.urlsplit(argv[0])
    Handler.service = "%s://%s" % (service.scheme, service.netloc)
    server = ThreadingHTTPServer(("127.0.0.1", int(argv[1])), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
