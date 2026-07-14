#!/bin/sh
# Runs the DynDNS updater once, or in a loop, depending on RUN_ONCE.
#
# Env vars:
#   RUN_ONCE                 - if "true", run a single update and exit (default: false)
#   UPDATE_INTERVAL_SECONDS  - seconds to sleep between runs when looping (default: 300)
set -eu

INTERVAL="${UPDATE_INTERVAL_SECONDS:-300}"

if [ "${RUN_ONCE:-false}" = "true" ]; then
    exec python -m src.updateDynDns
fi

while true; do
    python -m src.updateDynDns || echo "Update run failed; will retry in ${INTERVAL}s." >&2
    sleep "$INTERVAL"
done
