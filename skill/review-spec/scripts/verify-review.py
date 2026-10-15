#!/usr/bin/env python3
"""Check hosted spec bytes and exact Git baseline before browser inspection."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen


# The served page is the file plus the meta the service adds (review-service #published-spec-page): the proof applies
# the server's own transform, never a copy of it.
_serve_spec = importlib.util.spec_from_file_location(
    "spec_chat_review_serve", Path(__file__).resolve().parents[1] / "assets" / "review-serve.py"
)
_serve = importlib.util.module_from_spec(_serve_spec)
_serve_spec.loader.exec_module(_serve)


def git(repository, *args, optional=False):
    result = subprocess.run(
        ("git", "-C", str(repository), *args), capture_output=True
    )
    if result.returncode:
        if optional:
            return None
        raise ValueError("local Git lookup failed: " + " ".join(args))
    return result.stdout


def read_url(url):
    with urlopen(url, timeout=10) as response:
        return response.read()


def verify(repository, spec, review_url, requested_base):
    repository = Path(git(repository, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    spec = Path(spec).resolve()
    relative = spec.relative_to(repository).as_posix()
    current = spec.read_bytes()
    base = git(repository, "rev-parse", "--verify", requested_base + "^{commit}").decode().strip()
    url = urlsplit(review_url)
    if url.scheme not in ("http", "https") or not url.netloc:
        raise ValueError("review URL must use HTTP or HTTPS")
    query_params = parse_qs(url.query)
    focused = query_params.get("focus", [""])[0] == "changes"
    link_base = query_params.get("base", [""])[0]
    if focused and not link_base:
        raise ValueError("focused review URL must include base=<resolved-commit>")
    if link_base and git(repository, "rev-parse", "--verify", link_base + "^{commit}").decode().strip() != base:
        raise ValueError("review URL selects a different baseline")
    if focused and link_base != base:
        raise ValueError("focused review URL must pin the resolved baseline commit")
    if read_url(urlunsplit(url._replace(fragment=""))) != _serve._share_meta(current, str(spec)):
        raise ValueError("served spec bytes differ from the local spec plus the meta the service adds")

    query = urlencode({"path": unquote(url.path).lstrip("/"), "base": base})
    result = json.loads(read_url(urlunsplit((url.scheme, url.netloc, "/api/baseline", query, ""))))
    if not isinstance(result, dict):
        raise ValueError("baseline response must be a JSON object")
    if result.get("base") != base:
        raise ValueError("server replaced the requested exact baseline")

    expected = git(repository, "show", base + ":" + relative, optional=True)
    html_base = base if expected is not None else None
    if "htmlBase" not in result or result["htmlBase"] != html_base:
        raise ValueError("baseline HTML source differs from local Git; verify the server's exact-baseline capability")
    if "html" not in result or result["html"] != (expected.decode("utf-8") if expected is not None else None):
        raise ValueError("baseline HTML bytes differ from local Git")

    return {
        "spec": relative,
        "specSha256": hashlib.sha256(current).hexdigest(),
        "base": base,
        "htmlBase": html_base,
        "source": "new" if html_base is None else "base",
        "result": "source-and-baseline-verified",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("spec_html")
    parser.add_argument("review_url")
    parser.add_argument("exact_base")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.repository, args.spec_html, args.review_url, args.exact_base)))
        return 0
    except (OSError, ValueError) as error:
        print(f"verify-review: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
