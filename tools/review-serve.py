#!/usr/bin/env python3
# spec-chat-capabilities: exact-baseline git-baseline narrow-review-root
"""spec-chat review-serve - HTTP transport for a narrow review collection.

FSA requires the browser and the spool files to share a machine; over SSH they
don't. This serves a narrow review collection plus tiny spool and Git-baseline
routes. The URL is public and is not a secret in any security sense or an authentication boundary.
Stdlib only.

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
import stat
import subprocess
import sys
import time
import argparse
import fcntl
import mimetypes
import tomllib
import contextlib
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs, quote



def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.')
    parser.add_argument('port', nargs='?', type=int, default=None)
    parser.add_argument('--public', action='store_true', help='bind to all host interfaces')
    parser.add_argument('--bind', default=None, help='bind address (default: loopback)')
    parser.add_argument('--host', default=None, help='host name or address printed in the review URL')
    parser.add_argument('--registry', default=None, help='serve the resources in a registry TOML file')
    parser.add_argument('--port', dest='option_port', type=int, default=None,
                        help='multi-resource listening port')
    return parser.parse_args()


ARGS = parse_args()
ROOT = os.path.realpath(ARGS.root)
PORT = ARGS.option_port if ARGS.option_port is not None else (ARGS.port if ARGS.port is not None else (0 if ARGS.public else 7160))
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

if not ARGS.registry and (not REPO_ROOT or os.path.samefile(ROOT, REPO_ROOT)):
    raise SystemExit('refusing broad root; serve a narrow review collection strictly inside its Git repository')


class Handler(SimpleHTTPRequestHandler):
    timeout = 2.0

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def setup(self):
        super().setup()
        self.connection.settimeout(self.timeout)

    def end_headers(self):
        # dev loop: never let a stale runtime.js survive a reload
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def log_message(self, fmt, *a):
        line = fmt % a
        if 'hxdebug' in line:
            print('DEBUG-BEACON:', line, flush=True)

    def translate_path(self, path):
        handled, target = _own_viz_asset(path)
        if handled:
            return target or os.path.join(ROOT, '.spec-chat-path-denied')
        translated = super().translate_path(path)
        resolved = os.path.realpath(translated)
        if resolved != ROOT and not resolved.startswith(ROOT + os.sep):
            return os.path.join(ROOT, '.spec-chat-path-denied')
        return translated

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
        events = _read_spool_events(d, ROOT)
        if events is None:
            return self._json({'error': 'unsafe spool path'}, 400)
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
        if actor == 'agent':
            return self._json({'error': 'agent spool writes are disk-only'}, 403)
        try:
            ev = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        except ValueError:
            return self._json({'error': 'bad json'}, 400)
        event = ev.get('event')
        event_id = ev.get('id')
        safe = re.compile(r'[A-Za-z0-9._-]{1,128}')
        if not isinstance(event, str) or not isinstance(event_id, str) or not safe.fullmatch(event) or not safe.fullmatch(event_id):
            return self._json({'error': 'bad event name'}, 400)
        name = '%d-%s-%s.json' % (time.time_ns(), event, event_id)
        try:
            _write_event(d, ROOT, actor, name, ev)
        except OSError:
            return self._json({'error': 'unsafe spool path'}, 400)
        self._json({'ok': True, 'name': name})


class RegistryError(ValueError):
    pass


def _inside(path, parent, strict=False):
    """Return whether path is inside parent after symlink resolution."""
    try:
        common = os.path.commonpath((os.path.realpath(path), os.path.realpath(parent)))
    except ValueError:
        return False
    return common == os.path.realpath(parent) and (not strict or os.path.realpath(path) != os.path.realpath(parent))


@contextlib.contextmanager
def _actor_directory(review, root, actor, create=False):
    """Anchor spool access to directory descriptors; never follow symlinks."""
    relative = os.path.relpath(review, root)
    parts = relative.split(os.sep) + [actor]
    if any(part in ('', '.', '..') for part in parts) or not _inside(review, root, strict=True):
        raise OSError('review path escapes collection')
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(root, flags)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_spool_events(review, root):
    events = []
    for actor in ('human', 'agent'):
        try:
            with _actor_directory(review, root, actor) as directory:
                for name in os.listdir(directory):
                    if not name.endswith('.json'):
                        continue
                    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
                    descriptor = os.open(name, flags, dir_fd=directory)
                    with os.fdopen(descriptor, encoding='utf-8') as stream:
                        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                            return None
                        try:
                            body = json.load(stream)
                        except ValueError:
                            continue
                    events.append({'actor': actor, 'name': name, 'body': body})
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return events


def _write_event(review, root, actor, name, event):
    with _actor_directory(review, root, actor, create=True) as directory:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=directory)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(event, stream)


def _state_lock_path(registry):
    return os.path.join(os.path.dirname(os.path.realpath(registry)), '.state.lock')


@contextlib.contextmanager
def _state_lock(registry):
    descriptor = os.open(_state_lock_path(registry), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _own_viz_asset(path):
    """Resolve .viz assets to the server's vendored runtime, never the collection."""
    decoded = urllib_unquote(urlparse(path).path)
    parts = decoded.split('/')
    try:
        marker = parts.index('.viz')
    except ValueError:
        return False, None
    suffix = parts[marker + 1:]
    if not suffix or any(part in ('', '.', '..') for part in suffix):
        return True, None
    here = os.path.realpath(os.path.dirname(__file__))
    candidates = [os.path.join(here, 'viz'), os.path.join(here, '..', 'skill', 'review-spec', 'assets', 'viz')]
    asset_root = next((os.path.realpath(candidate) for candidate in candidates if os.path.isdir(candidate)), None)
    if not asset_root:
        return True, None
    target = os.path.realpath(os.path.join(asset_root, *suffix))
    if not _inside(target, asset_root, strict=True) or not os.path.isfile(target):
        return True, None
    return True, target


def _read_resource_records(path):
    try:
        with open(path, 'rb') as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RegistryError('cannot read registry: %s' % exc) from exc
    records = document.get('resource', [])
    if not isinstance(records, list):
        raise RegistryError('registry resource entries must be an array')
    result = []
    seen_ids = set()
    slug_roots = {}
    seen_stable = set()
    seen_sources = set()
    slug_re = re.compile(r'[a-z0-9][a-z0-9-]{0,62}\Z')
    for raw in records:
        if not isinstance(raw, dict):
            raise RegistryError('registry resource entry must be a table')
        required = ('id', 'slug', 'root', 'narrow_root', 'spec', 'base', 'owner', 'checker', 'lifecycle', 'cursor_name')
        missing = [name for name in required if not isinstance(raw.get(name), str) or not raw[name].strip()]
        if missing:
            raise RegistryError('resource missing required field: %s' % ', '.join(missing))
        resource = dict(raw)
        rid = resource['id']
        slug = resource['slug']
        if rid in seen_ids:
            raise RegistryError('duplicate resource identity: %s' % rid)
        if not slug_re.fullmatch(slug) or slug in {'api', 'static'}:
            raise RegistryError('invalid or reserved resource slug: %s' % slug)
        root = os.path.realpath(resource['root'])
        narrow_root = os.path.realpath(os.path.join(root, resource['narrow_root']))
        if not os.path.isabs(resource['root']):
            raise RegistryError('resource root must be absolute: %s' % rid)
        try:
            toplevel = subprocess.check_output(
                ('git', '-C', root, 'rev-parse', '--show-toplevel'),
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RegistryError('resource root is not a Git worktree: %s' % rid) from exc
        if root != os.path.realpath(toplevel):
            raise RegistryError('resource root must be the Git worktree toplevel: %s' % rid)
        if slug in slug_roots and slug_roots[slug] != root:
            raise RegistryError('ambiguous resource slug: %s' % slug)
        if not os.path.isdir(root) or not os.path.isdir(narrow_root) or not _inside(narrow_root, root, strict=True):
            raise RegistryError('resource roots are invalid: %s' % rid)
        spec = resource['spec'].replace('\\', '/')
        if spec.startswith('/') or spec.endswith('/') or not spec.endswith('.spec.html'):
            raise RegistryError('resource spec path is invalid: %s' % spec)
        spec_parts = spec.split('/')
        if any(part in ('', '.', '..') for part in spec_parts):
            raise RegistryError('resource spec path escapes its repository: %s' % spec)
        spec_file = os.path.realpath(os.path.join(root, *spec_parts))
        if not _inside(spec_file, narrow_root, strict=True) or not os.path.isfile(spec_file):
            raise RegistryError('resource spec is missing or outside its collection: %s' % spec)
        stable = slug + '/' + spec
        source = spec_file
        if stable in seen_stable:
            raise RegistryError('duplicate stable resource path: %s' % stable)
        if source in seen_sources:
            raise RegistryError('ambiguous resource source: %s' % source)
        base = resource.get('base', '')
        if not isinstance(base, str):
            raise RegistryError('resource base must be text: %s' % rid)
        if base:
            checked = subprocess.run(
                ('git', '-C', root, 'rev-parse', '--verify', base + '^{commit}'),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if checked.returncode != 0:
                raise RegistryError('resource base is unresolved: %s' % base)
        resource.update({
            'root': root,
            'narrow_root': narrow_root,
            'spec': spec,
            'spec_file': spec_file,
            'lifecycle': resource.get('lifecycle', 'serving'),
            'base': base,
        })
        if resource['lifecycle'] not in {'serving', 'parked', 'finished', 'removed'}:
            raise RegistryError('invalid resource lifecycle: %s' % resource['lifecycle'])
        seen_ids.add(rid)
        slug_roots[slug] = root
        seen_stable.add(stable)
        seen_sources.add(source)
        result.append(resource)
    return result


class RegistryState:
    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.signature = None
        self.records = []
        self.reload(initial=True)

    def _signature(self):
        stat = os.stat(self.path)
        return (stat.st_mtime_ns, stat.st_size)

    def reload(self, initial=False):
        try:
            signature = self._signature()
            records = _read_resource_records(self.path)
        except (OSError, RegistryError):
            if initial:
                raise
            return False
        if not initial and signature == self.signature:
            return False
        self.records = records
        self.signature = signature
        return True

    def snapshot(self):
        try:
            if self._signature() != self.signature:
                self.reload()
        except OSError:
            pass
        return tuple(self.records)


class MultiHandler(SimpleHTTPRequestHandler):
    server_version = 'SpecChatMulti/1'
    timeout = 2.0

    def setup(self):
        super().setup()
        self.connection.settimeout(self.timeout)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def log_message(self, fmt, *args):
        return

    @property
    def resources(self):
        return self.server.registry_state.snapshot()

    def _json(self, value, code=200):
        body = json.dumps(value).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _resource_by_slug(self, slug):
        return next((item for item in self.resources if item['slug'] == slug and item['lifecycle'] != 'removed'), None)

    @staticmethod
    def _decoded_path(path):
        value = urllib_unquote(path)
        if '\x00' in value:
            return None
        return value

    def _static_target(self, path):
        decoded = self._decoded_path(path)
        if not decoded or not decoded.startswith('/'):
            return None, None
        parts = decoded.split('/')
        if len(parts) < 3 or any(part in ('', '.', '..') for part in parts[1:]):
            return None, None
        relative = '/'.join(parts[2:])
        if any(part.endswith('.review') for part in parts[2:]):
            return None, None
        for resource in self.resources:
            if resource['slug'] != parts[1] or resource['lifecycle'] == 'removed':
                continue
            if relative.endswith('.spec.html') and relative != resource['spec']:
                continue
            target = os.path.realpath(os.path.join(resource['root'], *relative.split('/')))
            target_parts = os.path.normpath(target).split(os.sep)
            if any(part.endswith('.review') for part in target_parts):
                continue
            if _inside(target, resource['narrow_root']) and os.path.isfile(target):
                return resource, target
        return None, None

    def _send_file(self, path):
        decoded = self._decoded_path(path) or ''
        route_parts = decoded.split('/')
        if len(route_parts) > 1 and not any(
            resource['slug'] == route_parts[1] and resource['lifecycle'] != 'removed'
            for resource in self.resources
        ):
            self.send_error(404)
            return
        handled, bundled = _own_viz_asset(path)
        if handled:
            if not bundled:
                self.send_error(404)
                return
            target = bundled
            resource = True
        else:
            resource, target = self._static_target(path)
            if not resource:
                self.send_error(404)
                return
        try:
            with open(target, 'rb') as stream:
                body = stream.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        content_type = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _index(self):
        entries = []
        for resource in self.resources:
            if resource['lifecycle'] == 'removed':
                continue
            href = '/' + quote(resource['slug'] + '/' + resource['spec'], safe='/')
            title = Handler._page_title(resource['spec_file'])
            entries.append('<li><a href="%s">%s</a><span>%s</span></li>' % (
                html.escape(href, quote=True), html.escape(title), html.escape(resource['id'])))
        listing = '\n'.join(entries) or '<li class="empty">No Spec Chat resources are available.</li>'
        body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Spec Chat index</title></head><body><main><h1>Review index</h1>'
                '<ul>%s</ul></main></body></html>' % listing).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _route_resource_dir(self, query):
        raw = query.get('dir', [''])[0]
        decoded = self._decoded_path(raw)
        if decoded is None:
            return None, None
        decoded = decoded.lstrip('/')
        for resource in self.resources:
            expected = resource['slug'] + '/' + resource['spec'] + '.review'
            if decoded == expected and resource['lifecycle'] != 'removed':
                review = resource['spec_file'] + '.review'
                if _inside(review, resource['narrow_root'], strict=True):
                    return resource, review
            if decoded == expected and resource['lifecycle'] == 'removed':
                return resource, None
        return None, None

    def _events(self, query):
        resource, directory = self._route_resource_dir(query)
        if resource and resource['lifecycle'] == 'removed':
            return self._json({'error': 'resource removed'}, 404)
        if not resource or not directory:
            return self._json({'error': 'bad dir'}, 400)
        events = _read_spool_events(directory, resource['narrow_root'])
        if events is None:
            return self._json({'error': 'unsafe spool path'}, 400)
        events.sort(key=lambda event: event['name'])
        return self._json(events)

    def _baseline_multi(self, query):
        raw = query.get('path', [''])[0]
        decoded = self._decoded_path(raw)
        if decoded is None:
            return self._json({'error': 'bad path'}, 400)
        decoded = decoded.lstrip('/')
        resource = next((item for item in self.resources
                         if decoded == item['slug'] + '/' + item['spec']), None)
        if not resource:
            return self._json({'error': 'bad path'}, 400)
        if resource['lifecycle'] == 'removed':
            return self._json({'error': 'resource removed'}, 404)
        rel = resource['spec']
        requested = query.get('base', [resource.get('base', '')])[0]
        try:
            candidates = [requested] if requested else []
            if not candidates:
                candidates.extend(('main', 'master'))
            base_ref = next((candidate for candidate in candidates if candidate and subprocess.run(
                ('git', '-C', resource['root'], 'rev-parse', '--verify', candidate + '^{commit}'),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0), None)
            if not base_ref:
                return self._json({'error': 'no local base ref'}, 409)
            base = subprocess.check_output(
                ('git', '-C', resource['root'], 'rev-parse', '--verify', base_ref + '^{commit}'),
                text=True, stderr=subprocess.DEVNULL).strip()
            prior = subprocess.run(('git', '-C', resource['root'], 'show', base + ':' + rel),
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            html_base = base if prior.returncode == 0 else None
            html = prior.stdout.decode('utf-8') if prior.returncode == 0 else None
            return self._json({'base': base, 'htmlBase': html_base, 'html': html})
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
            return self._json({'error': 'git baseline unavailable'}, 409)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == '/':
            return self._index()
        if parsed.path == '/api/events':
            return self._events(query)
        if parsed.path == '/api/baseline':
            return self._baseline_multi(query)
        return self._send_file(parsed.path)

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/api/events':
            return self._json({'error': 'not found'}, 404)
        query = parse_qs(parsed.query)
        resource, directory = self._route_resource_dir(query)
        actor = query.get('actor', ['human'])[0]
        if resource and resource['lifecycle'] == 'removed':
            return self._json({'error': 'resource removed'}, 404)
        if not resource or not directory or actor not in ('human', 'agent'):
            return self._json({'error': 'bad dir or actor'}, 400)
        if actor == 'agent':
            return self._json({'error': 'agent spool writes are disk-only'}, 403)
        try:
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            event = json.loads(body)
        except (ValueError, TypeError):
            return self._json({'error': 'bad json'}, 400)
        event_name = event.get('event') if isinstance(event, dict) else None
        event_id = event.get('id') if isinstance(event, dict) else None
        safe = re.compile(r'[A-Za-z0-9._-]{1,128}\Z')
        if not isinstance(event_name, str) or not isinstance(event_id, str) or not safe.fullmatch(event_name) or not safe.fullmatch(event_id):
            return self._json({'error': 'bad event name'}, 400)
        name = '%d-%s-%s.json' % (time.time_ns(), event_name, event_id)
        with _state_lock(self.server.registry_state.path):
            try:
                fresh = _read_resource_records(self.server.registry_state.path)
            except (OSError, RegistryError):
                return self._json({'error': 'registry unavailable'}, 409)
            resource = next((item for item in fresh if item['id'] == resource['id']), None)
            if not resource or resource['lifecycle'] == 'removed':
                return self._json({'error': 'resource removed'}, 404)
            if resource['lifecycle'] == 'finished':
                return self._json({'error': 'resource finished'}, 409)
            directory = resource['spec_file'] + '.review'
            try:
                _write_event(directory, resource['narrow_root'], actor, name, event)
            except OSError:
                return self._json({'error': 'unsafe spool path'}, 400)
        return self._json({'ok': True, 'name': name})


def urllib_unquote(value):
    from urllib.parse import unquote
    return unquote(value)


class ReviewThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == '__main__':
    if ARGS.registry:
        registry_state = RegistryState(ARGS.registry)
        server = ReviewThreadingHTTPServer((BIND, PORT), MultiHandler)
        server.registry_state = registry_state
    else:
        server = ReviewThreadingHTTPServer((BIND, PORT), Handler)
    PORT = server.server_port
    host = advertised_host()
    print('spec-chat review-serve on http://%s:%d  root=%s' % (host, PORT, ROOT), flush=True)
    print('review URL is public and is not a secret in any security sense or an authentication boundary; stop this process when review ends', flush=True)
    server.serve_forever()
