#!/bin/sh
# Own the terminal review control state. Raw watchers are detection-only.
# usage:
#   review-control.sh yielded REVIEW_ROOT CURSOR_NAME [TIMEOUT_S] [POLL_S]
#   review-control.sh external REVIEW_ROOT CURSOR_NAME OWNER_ID OWNER_SESSION ADAPTER [ARG...]
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
  CONTROL_LOCK_DIR=${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}
  CONTROL_LOCK="$CONTROL_LOCK_DIR/spec-chat-control-$CONTROL_KEY.lock"
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
  external)
    [ "$#" -ge 6 ] || {
      echo "usage: review-control.sh external REVIEW_ROOT CURSOR_NAME OWNER_ID OWNER_SESSION ADAPTER [ARG...]" >&2
      exit 2
    }
    ROOT=$(CDPATH= cd "$2" && pwd)
    CURSOR=$3
    OWNER_ID=$4
    OWNER_SESSION=$5
    shift 5
    [ -x "$1" ] || {
      echo "review-control: wake adapter is not executable: $1" >&2
      exit 2
    }
    claim_control "$ROOT" "$CURSOR"
    echo "control=external-wake final=allowed owner=$OWNER_ID session=$OWNER_SESSION"
    LAST_BATCH=
    while :; do
      set +e
      READY=$(SPEC_CHAT_WATCH_OWNER=external-wake "$WATCH" "$ROOT" "$CURSOR" 3600 3)
      WATCH_RC=$?
      set -e
      if [ "$WATCH_RC" -eq 3 ]; then
        continue
      fi
      [ "$WATCH_RC" -eq 0 ] || exit "$WATCH_RC"

      BATCH_ID=$(printf '%s\n' "$READY" | cksum | awk '{ print $1 "-" $2 }')
      [ "$BATCH_ID" = "$LAST_BATCH" ] && continue
      READY_SPEC=$(printf '%s\n' "$READY" | sed -n '1s/\t.*//p')
      READY_EVENTS=$(printf '%s\n' "$READY" | cut -f2-)

      set +e
      SPEC_CHAT_OWNER_ID=$OWNER_ID \
      SPEC_CHAT_OWNER_SESSION=$OWNER_SESSION \
      SPEC_CHAT_BATCH_ID=$BATCH_ID \
      SPEC_CHAT_READY_SPEC=$READY_SPEC \
      SPEC_CHAT_READY_EVENTS=$READY_EVENTS \
      SPEC_CHAT_CURSOR_NAME=$CURSOR \
        "$@"
      WAKE_RC=$?
      set -e
      if [ "$WAKE_RC" -eq 75 ]; then
        echo "wake=deferred owner=working batch=$BATCH_ID"
        sleep 3
        continue
      fi
      if [ "$WAKE_RC" -ne 0 ]; then
        echo "control=manual-resume reason=wake-adapter-failed new human chat message required" >&2
        exit 4
      fi
      echo "wake=sent batch=$BATCH_ID spec=$READY_SPEC"
      LAST_BATCH=$BATCH_ID
      [ "${SPEC_CHAT_MONITOR_ONCE:-0}" = 1 ] && exit 0

      while :; do
        sleep 3
        set +e
        CURRENT=$("$WATCH" "$ROOT" "$CURSOR" 0 3)
        CURRENT_RC=$?
        set -e
        if [ "$CURRENT_RC" -eq 3 ]; then
          LAST_BATCH=
          break
        fi
        [ "$CURRENT_RC" -eq 0 ] || exit "$CURRENT_RC"
        CURRENT_ID=$(printf '%s\n' "$CURRENT" | cksum | awk '{ print $1 "-" $2 }')
        if [ "$CURRENT_ID" != "$LAST_BATCH" ]; then
          LAST_BATCH=
          break
        fi
      done
    done
    ;;
  *)
    echo "usage: review-control.sh yielded|external|manual ..." >&2
    exit 2
    ;;
esac
