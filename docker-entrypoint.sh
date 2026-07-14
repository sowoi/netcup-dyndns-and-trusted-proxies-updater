#!/bin/sh
# Runs the DynDNS updater once, or in a loop, depending on RUN_ONCE.
# Any arguments passed to this script (e.g. via `docker run <image> --ip-mode
# ipv4`) are forwarded as CLI arguments to the updater and override
# .settings.json, exactly as they would when running the script directly.
#
# Env vars:
#   RUN_ONCE                 - if "true", run a single update and exit (default: false)
#   UPDATE_INTERVAL_SECONDS  - seconds to sleep between runs when looping (default: 300)
set -eu

INTERVAL="${UPDATE_INTERVAL_SECONDS:-300}"

# -h/--help (or any other single-shot argument) should always just run once
# and exit, even in the default looping mode - otherwise --help would only
# print usage once per loop iteration and never return control.
for arg in "$@"; do
    case "$arg" in
        -h|--help)
            exec python -m src.updateDynDns "$@"
            ;;
    esac
done

if [ "${RUN_ONCE:-false}" = "true" ]; then
    exec python -m src.updateDynDns "$@"
fi

while true; do
    python -m src.updateDynDns "$@" || echo "Update run failed; will retry in ${INTERVAL}s." >&2
    sleep "$INTERVAL"
done
