#!/bin/sh
# Launch review-serve on a free approved ingress port and prove reachability
# before handing the capability URL to a remote reviewer.
set -eu

usage() {
  echo "usage: launch-review-serve.sh NARROW_ROOT SPEC_PATH EXACT_BASE --external-probe EXECUTABLE" >&2
  exit 2
}

[ "$#" -ge 5 ] || usage
ROOT=$1
SPEC_PATH=$2
EXACT_BASE=$3
shift 3
[ "$1" = "--external-probe" ] || usage
[ "$#" -eq 2 ] || usage
PROBE=$2
[ -x "$PROBE" ] || {
  echo "launch-review-serve: external probe is not executable: $PROBE" >&2
  exit 2
}
[ -d "$ROOT" ] || {
  echo "launch-review-serve: narrow root is not a directory: $ROOT" >&2
  exit 2
}
[ -n "${SPEC_CHAT_EXTERNAL_VANTAGE:-}" ] || {
  echo "launch-review-serve: set SPEC_CHAT_EXTERNAL_VANTAGE to the non-loopback probe vantage" >&2
  exit 2
}

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ROOT=$(CDPATH= cd "$ROOT" && pwd)
REPO=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null) || {
  echo "launch-review-serve: narrow root is not inside a Git repository" >&2
  exit 2
}
SPEC=$(realpath "$ROOT/$SPEC_PATH")
case "$SPEC" in
  "$ROOT"/*) ;;
  *) echo "launch-review-serve: spec escapes narrow root" >&2; exit 2 ;;
esac
[ -f "$SPEC" ] || {
  echo "launch-review-serve: spec does not exist: $SPEC" >&2
  exit 2
}

# The host supplies this list. If it is absent, discover TCP rules from UFW.
# A failure to discover rules is safer than guessing a port.
APPROVED=${SPEC_CHAT_APPROVED_INGRESS_PORTS:-}
if [ -z "$APPROVED" ] && command -v ufw >/dev/null 2>&1; then
  APPROVED=$(ufw status 2>/dev/null | awk '
    /^[[:space:]]*[0-9]+([,:[0-9]]*)?\/tcp([[:space:]]|$)/ {
      split($1, p, "/"); print p[1]
    }
  ' | tr '\n' ' ')
fi
[ -n "$APPROVED" ] || {
  echo "launch-review-serve: approved ingress ports unavailable; configure SPEC_CHAT_APPROVED_INGRESS_PORTS" >&2
  exit 2
}

PORTS=$(printf '%s\n' "$APPROVED" | tr ',' ' ' | awk '{ for (i = 1; i <= NF; i++) print $i }' | awk '!seen[$0]++')
SELECTED=
for PORT in $PORTS; do
  case "$PORT" in
    ''|*[!0-9]*) continue ;;
  esac
  [ "$PORT" -ge 1 ] 2>/dev/null && [ "$PORT" -le 65535 ] 2>/dev/null || continue
  if python3 - "$PORT" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
  then
    SELECTED=$PORT
    break
  fi
done
[ -n "$SELECTED" ] || {
  echo "launch-review-serve: no approved ingress port is free" >&2
  exit 2
}

LOG=$(mktemp "${TMPDIR:-/tmp}/spec-chat-review-serve.XXXXXX")
SERVER_PID=
cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -f "$LOG"
}
trap cleanup EXIT HUP INT TERM

python3 "$SCRIPT_DIR/../assets/review-serve.py" "$ROOT" "$SELECTED" --public >"$LOG" 2>&1 &
SERVER_PID=$!
BASE_URL=
LINE=
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  LINE=$(grep -m1 '^spec-chat review-serve on http' "$LOG" 2>/dev/null || true)
  if [ -n "$LINE" ]; then
    BASE_URL=$(printf '%s\n' "$LINE" | sed -n 's/^spec-chat review-serve on \(http:\/\/[^ ]*\).*/\1/p')
    break
  fi
  kill -0 "$SERVER_PID" 2>/dev/null || break
  sleep 1
done
[ -n "$BASE_URL" ] || {
  echo "launch-review-serve: review-serve did not print a URL" >&2
  exit 2
}

REVIEW_URL="$BASE_URL/${SPEC_PATH#./}?focus=changes&base=$EXACT_BASE"
python3 "$SCRIPT_DIR/verify-review.py" "$REPO" "$SPEC" "$REVIEW_URL" "$EXACT_BASE" >/dev/null
SPEC_SHA256=$(sha256sum "$SPEC" | awk '{print $1}')
BASELINE_URL="$BASE_URL/api/baseline?path=${SPEC_PATH#./}&base=$EXACT_BASE"
PROBE_RESULT=$("$PROBE" "$REVIEW_URL" "$BASELINE_URL" "$EXACT_BASE" "$SPEC_SHA256" "$SPEC_PATH")
printf '%s\n' "$PROBE_RESULT"
printf '%s\n' "$PROBE_RESULT" | grep -F 'external-spec-http=200' >/dev/null || {
  echo "launch-review-serve: external probe did not verify spec HTTP 200" >&2
  exit 2
}
printf '%s\n' "$PROBE_RESULT" | grep -F 'external-baseline-http=200' >/dev/null || {
  echo "launch-review-serve: external probe did not verify baseline HTTP 200" >&2
  exit 2
}
printf '%s\n' "$PROBE_RESULT" | grep -F "external-spec-sha256=$SPEC_SHA256" >/dev/null || {
  echo "launch-review-serve: external probe did not verify exact spec bytes" >&2
  exit 2
}
printf '%s\n' "$PROBE_RESULT" | grep -F "external-baseline-base=$EXACT_BASE" >/dev/null || {
  echo "launch-review-serve: external probe did not verify exact baseline" >&2
  exit 2
}
printf '%s\n' "$LINE"
printf 'review-hosting=verified approved-port=%s external-vantage=%s\n' "$SELECTED" "$SPEC_CHAT_EXTERNAL_VANTAGE"
printf '%s\n' 'review-handoff=allowed only after this URL, spec path, exact baseline, and external proof are delivered'
wait "$SERVER_PID"
