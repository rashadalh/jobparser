#!/bin/sh
# Starts a virtual display before the app, so Playwright's headed-retry escalation
# (extract/fetch.py — some anti-bot WAFs block headless Chromium but allow identical
# headed automation from the same IP) has a display to render into.
#
# DISPLAY comes from the image ENV (see Dockerfile), not from this script: `docker exec`
# inherits the image's environment but never this script's. Serving the same display the
# ENV advertises is what makes an exec'd process — a debug shell, or a CLI run — able to
# use the headed retry too.
#
# The app is exec'd directly (rather than wrapped in xvfb-run) so it stays PID 1 and
# receives SIGTERM from `docker stop` without an intermediary.
set -e

: "${DISPLAY:=:99}"
export DISPLAY

Xvfb "$DISPLAY" -screen 0 1280x1024x24 -nolisten tcp &

# Wait for the socket instead of sleeping and hoping. `set -e` cannot see a backgrounded
# Xvfb die, so a failed start used to go unnoticed here and resurface much later as a
# confusing FETCH_FAILED ("Missing X server or $DISPLAY") from the headed retry.
socket="/tmp/.X11-unix/X${DISPLAY#:}"
i=0
while [ ! -e "$socket" ]; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then   # ~5s
        echo "entrypoint: Xvfb did not come up on $DISPLAY ($socket missing)." >&2
        echo "  Continuing without a display: the headed-retry escalation will be" >&2
        echo "  skipped (fetch degrades to the headless render). Nothing else is affected." >&2
        break
    fi
    sleep 0.1
done

exec "$@"
