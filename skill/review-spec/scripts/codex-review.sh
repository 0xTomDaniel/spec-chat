#!/bin/sh
# Compatibility tombstone. Detached processing can race the interactive owner.
echo "codex-review: detached processing is disabled; use review-control.sh external with a verified host wake adapter, or manual" >&2
exit 2
