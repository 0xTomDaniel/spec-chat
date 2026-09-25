#!/bin/sh
# Own the terminal review control state. Raw watchers are detection-only.
# usage:
#   review-control.sh yielded REVIEW_ROOT CURSOR_NAME [TIMEOUT_S] [POLL_S]
#   review-control.sh manual
set -eu

MODE=${1:-}
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
WATCH="$SCRIPT_DIR/watch-specs.sh"

claim_control() {
  CONTROL_ROOT=$1
  CONTROL_CURSOR=$2
  command -v flock >/dev/null 2>&1 || {
    echo "control=manual-resume reason=flock-unavailable; new human chat message required" >&2
    exit 4
  }
  CONTROL_KEY=$(printf '%s\n%s\n' "$CONTROL_ROOT" "$CONTROL_CURSOR" | cksum | awk '{ print $1 "-" $2 }')
  CONTROL_UID=$(id -u)
  if [ -d "/run/user/$CONTROL_UID" ] && [ -w "/run/user/$CONTROL_UID" ]; then
    CONTROL_LOCK_DIR="/run/user/$CONTROL_UID/spec-chat-review-control"
  else
    CONTROL_LOCK_DIR="/tmp/spec-chat-review-control-$CONTROL_UID"
  fi
  [ ! -L "$CONTROL_LOCK_DIR" ] || {
    echo "control=manual-resume reason=unsafe-lock-directory; new human chat message required" >&2
    exit 4
  }
  umask 077
  mkdir -p "$CONTROL_LOCK_DIR"
  [ "$(stat -c '%u' "$CONTROL_LOCK_DIR")" = "$CONTROL_UID" ] || {
    echo "control=manual-resume reason=foreign-lock-directory; new human chat message required" >&2
    exit 4
  }
  chmod 700 "$CONTROL_LOCK_DIR"
  CONTROL_LOCK="$CONTROL_LOCK_DIR/control-$CONTROL_KEY.lock"
  exec 9>"$CONTROL_LOCK"
  flock -n 9 || {
    echo "review-control: control owner already exists for $CONTROL_ROOT and $CONTROL_CURSOR" >&2
    exit 5
  }
}

case "$MODE" in
  manual)
    [ "$#" -eq 1 ] || {
      echo "review-control: manual takes no additional arguments" >&2
      exit 2
    }
    echo "control=manual-resume final=allowed; new human chat message required"
    exit 0
    ;;
  yielded)
    [ "$#" -ge 3 ] && [ "$#" -le 5 ] || {
      echo "usage: review-control.sh yielded REVIEW_ROOT CURSOR_NAME [TIMEOUT_S] [POLL_S]" >&2
      exit 2
    }
    ROOT=$(CDPATH= cd "$2" && pwd)
    CURSOR=$3
    TIMEOUT=${4:-3600}
    POLL=${5:-3}
    claim_control "$ROOT" "$CURSOR"
    echo "control=turn-yielded final=forbidden wake-owner=same-turn"
    SPEC_CHAT_WATCH_OWNER=turn-yielded exec "$WATCH" "$ROOT" "$CURSOR" "$TIMEOUT" "$POLL"
    ;;
  *)
    echo "usage: review-control.sh yielded|manual ..." >&2
    exit 2
    ;;
esac
