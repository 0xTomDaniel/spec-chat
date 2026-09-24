#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root
"""spec-chat review-serve - HTTP transport for a narrow review collection.

FSA requires the browser and the spool files to share a machine; over SSH they
don't. This serves a narrow review collection plus tiny spool and Git-baseline
routes. For remote review, bind directly to the host interface and treat the
printed URL as the secret. Stdlib only.

usage: review-serve.py [ROOT] [PORT] [--public] [--bind HOST] [--host HOST]

  GET  /                                             -> Spec Chat collection index
  GET  /api/events?dir=<review-dir-rel-path>            -> ordered event list
  POST /api/events?dir=<...>&actor=human|agent  (JSON)  -> writes one event file
  GET  /api/baseline?path=<spec-rel-path>[&base=<ref>]  -> local Git baseline
"""
import html
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import argparse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, quote



def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('port', nargs='?', type=int, default=None)
    parser.add_argument('--public', action='store_true', help='bind to all host interfaces')
    parser.add_argument('--bind', default=None, help='bind address (default: loopback)')
    parser.add_argument('--host', default=None, help='host name or address printed in the review URL')
    return parser.parse_args()


ARGS = parse_args()
ROOT = os.path.realpath(ARGS.root)
PORT = ARGS.port if ARGS.port is not None else (0 if ARGS.public else 7160)
BIND = ARGS.bind or ('0.0.0.0' if ARGS.public else '127.0.0.1')


def advertised_host():
    if ARGS.host:
        return ARGS.host
    if not ARGS.public:
        return '127.0.0.1'
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(('8.8.8.8', 80))
        address = probe.getsockname()[0]
        probe.close()
        if address and not address.startswith('127.'):
            return address
    except OSError:
        pass
    return socket.gethostname()

try:
    REPO_ROOT = subprocess.check_output(
        ('git', '-C', ROOT, 'rev-parse', '--show-toplevel'), text=True, stderr=subprocess.DEVNULL
    ).strip()
except (OSError, subprocess.CalledProcessError):
    REPO_ROOT = None

if not REPO_ROOT or os.path.samefile(ROOT, REPO_ROOT):
    raise SystemExit('refusing broad root; serve a narrow review collection strictly inside its Git repository')


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

    def translate_path(self, path):
        translated = super().translate_path(path)
        resolved = os.path.realpath(translated)
        if resolved != ROOT and not resolved.startswith(ROOT + os.sep):
            return os.path.join(ROOT, '.spec-chat-path-denied')
        return translated

    def _review_dir(self, q):
        rel = q.get('dir', [''])[0]
        d = os.path.realpath(os.path.join(ROOT, rel))
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

    def send_head(self):
        if urlparse(self.path).path == '/':
            body = self._index().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            return io.BytesIO(body)
        return super().send_head()

    def _detail_pages(self):
        pages = []
        support_dirs = {'evidence', 'evidence-bundle', 'evidence-bundles', 'fixture', 'fixtures', 'support', 'supports'}
        for directory, directories, names in os.walk(ROOT, followlinks=False):
            directories[:] = sorted(name for name in directories
                                    if (not name.startswith('.') and not name.endswith('.review')
                                        and name.lower() not in support_dirs))
            for name in sorted(names):
                if not name.endswith('.spec.html') or name.startswith('.'):
                    continue
                path = os.path.join(directory, name)
                resolved = os.path.realpath(path)
                if not resolved.startswith(ROOT + os.sep) or not os.path.isfile(path):
                    continue
                relative = os.path.relpath(path, ROOT).replace(os.sep, '/')
                pages.append((relative, self._page_title(path)))
        return pages

    @staticmethod
    def _page_title(path):
        class TitleParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.in_title = False
                self.parts = []

            def handle_starttag(self, tag, attrs):
                if tag.lower() == 'title':
                    self.in_title = True

            def handle_endtag(self, tag):
                if tag.lower() == 'title':
                    self.in_title = False

            def handle_data(self, data):
                if self.in_title:
                    self.parts.append(data)

        parser = TitleParser()
        try:
            with open(path, encoding='utf-8', errors='replace') as page:
                parser.feed(page.read())
        except OSError:
            pass
        title = ' '.join(''.join(parser.parts).split())
        return title or os.path.splitext(os.path.basename(path))[0]

    def _index(self):
        entries = []
        query = urlparse(self.path).query
        for relative, title in self._detail_pages():
            href = quote(relative, safe='/') + ('?' + query if query else '')
            entries.append(
                '<li><a href="%s">%s</a><span>%s</span></li>' % (
                    html.escape(href, quote=True),
                    html.escape(title),
                    html.escape(relative),
                )
            )
        listing = '\n'.join(entries) or '<li class="empty">No Spec Chat spec pages are available.</li>'
        return '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spec Chat index</title>
<style>
:root { color-scheme: light; font-family: system-ui, sans-serif; background: #faf9f6; color: #22242a; }
body { margin: 0; }
main { box-sizing: border-box; max-width: 60rem; margin: 0 auto; padding: clamp(1.25rem, 4vw, 3rem); }
header { margin-bottom: 2rem; }
.eyebrow { color: #595e68; font-size: .8rem; font-weight: 700; letter-spacing: .08em; margin: 0 0 .5rem; text-transform: uppercase; }
h1 { font-size: clamp(1.8rem, 5vw, 2.8rem); line-height: 1.1; margin: 0; }
header p:last-child { color: #595e68; line-height: 1.5; margin-bottom: 0; }
ul { display: grid; gap: .75rem; list-style: none; margin: 0; padding: 0; }
li { min-width: 0; background: #fff; border: 1px solid #e2e0d8; border-radius: .75rem; padding: .25rem 1rem 1rem; }
li a { color: #087f73; display: flex; align-items: center; min-height: 44px; padding: .25rem 0; font-weight: 700; font-size: 1.05rem; line-height: 1.35; overflow-wrap: anywhere; }
li span { color: #595e68; display: block; font-size: .9rem; overflow-wrap: anywhere; }
a:focus-visible { border-radius: .25rem; outline: 3px solid #087f73; outline-offset: 3px; }
.empty { color: #595e68; padding: 1rem; }
</style>
</head>
<body>
<main>
<header>
<p class="eyebrow">Spec Chat</p>
<h1>Review index</h1>
<p>Choose a visual spec to open its detail page.</p>
</header>
<nav aria-label="Spec Chat detail pages">
<ul>
%s
</ul>
</nav>
</main>
</body>
</html>
''' % listing

    def _baseline(self, q):
        rel = q.get('path', [''])[0]
        target = os.path.realpath(os.path.join(ROOT, rel))
        if not rel or not target.startswith(ROOT + os.sep) or not os.path.isfile(target):
            return self._json({'error': 'bad path'}, 400)
        try:
            repo = subprocess.check_output(
                ('git', '-C', ROOT, 'rev-parse', '--show-toplevel'), text=True, stderr=subprocess.DEVNULL
            ).strip()
            repo_rel = os.path.relpath(target, repo)
            if repo_rel.startswith('..' + os.sep) or repo_rel == '..':
                return self._json({'error': 'path outside repository'}, 400)
            requested = q.get('base', [''])[0]
            candidates = [requested] if requested else []
            if not candidates:
                try:
                    candidates.append(subprocess.check_output(
                        ('git', '-C', repo, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'),
                        text=True,
                        stderr=subprocess.DEVNULL,
                    ).strip())
                except subprocess.CalledProcessError:
                    candidates.extend(('main', 'master'))
            base_ref = next((candidate for candidate in candidates if candidate and subprocess.run(
                ('git', '-C', repo, 'rev-parse', '--verify', '--quiet', candidate + '^{commit}'),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0), None)
            if not base_ref:
                return self._json({'error': 'no local base ref'}, 409)
            # Explicit review snapshots may be on sibling branches. Only
            # automatic discovery asks for the common ancestor with HEAD.
            command = ('rev-parse', '--verify', base_ref + '^{commit}') if requested else ('merge-base', 'HEAD', base_ref)
            base = subprocess.check_output(
                ('git', '-C', repo, *command), text=True, stderr=subprocess.DEVNULL
            ).strip()
            prior = subprocess.run(
                ('git', '-C', repo, 'show', base + ':' + repo_rel),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            html_base = base if prior.returncode == 0 else None
            # A newly seeded spec is absent from the change-request base. Use
            # the first committed snapshot that introduced it. This seed must
            # stay stable across later spec commits, or refresh would move the
            # baseline to HEAD and erase the review diff.
            if prior.returncode != 0:
                seed = subprocess.run(
                    ('git', '-C', repo, 'rev-list', '--reverse', 'HEAD', '--', repo_rel),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                seed = seed.stdout.strip().splitlines()[0] if seed.returncode == 0 and seed.stdout.strip() else None
                if seed:
                    seed_prior = subprocess.run(
                        ('git', '-C', repo, 'show', seed + ':' + repo_rel),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    if seed_prior.returncode == 0:
                        prior = seed_prior
                        html_base = seed
            html = prior.stdout.decode('utf-8') if prior.returncode == 0 else None
            return self._json({'base': base, 'htmlBase': html_base, 'html': html})
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
            return self._json({'error': 'git baseline unavailable'}, 409)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/api/baseline':
            return self._baseline(parse_qs(u.query))
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
        event = ev.get('event')
        event_id = ev.get('id')
        safe = re.compile(r'[A-Za-z0-9._-]{1,128}')
        if not isinstance(event, str) or not isinstance(event_id, str) or not safe.fullmatch(event) or not safe.fullmatch(event_id):
            return self._json({'error': 'bad event name'}, 400)
        os.makedirs(os.path.join(d, actor), exist_ok=True)
        name = '%d-%s-%s.json' % (time.time_ns(), event, event_id)
        with open(os.path.join(d, actor, name), 'w') as f:
            json.dump(ev, f)
        self._json({'ok': True, 'name': name})


if __name__ == '__main__':
    server = HTTPServer((BIND, PORT), Handler)
    PORT = server.server_port
    host = advertised_host()
    print('spec-chat review-serve on http://%s:%d  root=%s' % (host, PORT, ROOT), flush=True)
    print('review URL is the secret; stop this process when review ends', flush=True)
    server.serve_forever()
