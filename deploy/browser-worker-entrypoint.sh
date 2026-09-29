#!/bin/sh
# Entrypoint for the browser worker (docs/agents/BROWSER-ORDERING.md 10, tier 2).
#
# Some suppliers' sites (Tesco, behind Akamai) serve "Access Denied" to a headless
# Chromium however well the profile is signed in. For those the adapter sets
# `prefers_headed`, and the worker launches a VISIBLE window -- which needs a display.
# In a container there is none, so when the operator asks for headed browsing
# (CAFEOPS_BROWSER_HEADLESS=false) and no DISPLAY is set, run the worker under Xvfb,
# a virtual X server the Dockerfile's browser stage installs. Everything else (the
# default, headless) runs the worker as is.
#
# Any arguments are passed through to `cafeops browser-worker` (e.g. --once).
set -eu

headless="${CAFEOPS_BROWSER_HEADLESS:-true}"
case "$(printf '%s' "$headless" | tr '[:upper:]' '[:lower:]')" in
  false|0|no|off)
    if [ -z "${DISPLAY:-}" ]; then
      if command -v xvfb-run >/dev/null 2>&1; then
        echo "browser-worker: CAFEOPS_BROWSER_HEADLESS=$headless and no DISPLAY; starting under xvfb-run" >&2
        exec xvfb-run -a --server-args="-screen 0 1280x900x24" cafeops browser-worker "$@"
      fi
      echo "browser-worker: CAFEOPS_BROWSER_HEADLESS=$headless, no DISPLAY and no xvfb-run; headed portals will end NEEDS_HUMAN" >&2
    fi
    ;;
esac
exec cafeops browser-worker "$@"
