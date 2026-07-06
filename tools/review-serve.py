#!/usr/bin/env python3
"""spec-chat review-serve — remote-dev transport.

FSA requires the browser and the spool files to share a machine; over SSH they
don't. This serves the repo statically plus a tiny spool API on localhost, to
be reached through an SSH port forward. Stdlib only.

usage: review-serve.py [ROOT] [PORT]

Tunnel with an EXPLICIT IPv4 destination — `ssh -L PORT:127.0.0.1:PORT host`.
Using `localhost` as the destination makes sshd try ::1 first and every
forwarded channel fails, because this server binds IPv4 loopback only.
  GET  /api/events?dir=<review-dir-rel-path>            -> ordered event list
  POST /api/events?dir=<...>&actor=human|agent  (JSON)  -> writes one event file
"""
import json
import os
import sys
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 7160


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        # dev loop: never let a stale runtime.js survive a reload
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def log_message(self, fmt, *a):
        line = fmt % a
        if 'hxdebug' in line:
            print('DEBUG-BEACON:', line, flush=True)

    def _review_dir(self, q):
        rel = q.get('dir', [''])[0]
        d = os.path.abspath(os.path.join(ROOT, rel))
        if not d.startswith(ROOT + os.sep) or not d.endswith('.review'):
            return None
        return d

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path != '/api/events':
            return super().do_GET()
        d = self._review_dir(parse_qs(u.query))
        if not d:
            return self._json({'error': 'bad dir'}, 400)
        events = []
        for actor in ('human', 'agent'):
            p = os.path.join(d, actor)
            if not os.path.isdir(p):
                continue
            for name in os.listdir(p):
                try:
                    with open(os.path.join(p, name)) as f:
                        events.append({'actor': actor, 'name': name, 'body': json.load(f)})
                except (OSError, ValueError):
                    pass
        events.sort(key=lambda e: e['name'])
        self._json(events)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != '/api/events':
            return self._json({'error': 'not found'}, 404)
        q = parse_qs(u.query)
        d = self._review_dir(q)
        actor = q.get('actor', ['human'])[0]
        if not d or actor not in ('human', 'agent'):
            return self._json({'error': 'bad dir or actor'}, 400)
        try:
            ev = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        except ValueError:
            return self._json({'error': 'bad json'}, 400)
        os.makedirs(os.path.join(d, actor), exist_ok=True)
        name = '%d-%s-%s.json' % (time.time_ns(), ev.get('event', 'event'), ev.get('id', 'x'))
        with open(os.path.join(d, actor, name), 'w') as f:
            json.dump(ev, f)
        self._json({'ok': True, 'name': name})


if __name__ == '__main__':
    print('spec-chat review-serve on http://127.0.0.1:%d  root=%s' % (PORT, ROOT))
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
