#!/bin/sh
# Starts a virtual display before the app, so Playwright's headed-retry escalation
# (extract/fetch.py — some anti-bot WAFs block headless Chromium but allow identical
# headed automation from the same IP) has a display to render into.
#
# The app is exec'd directly (rather than wrapped in xvfb-run) so it stays PID 1 and
# receives SIGTERM from `docker stop` without an intermediary.
set -e

Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
export DISPLAY=:99

# Wait for the socket instead of sleeping and hoping. `set -e` cannot see a backgrounded
# Xvfb die, so a failed start used to go unnoticed here and resurface much later as a
# confusing FETCH_FAILED ("Missing X server or $DISPLAY") from the headed retry.
i=0
while [ ! -e /tmp/.X11-unix/X99 ]; do
    i=$((i + 1))
    if [ "$i" -gt 50 ]; then   # ~5s
        echo "entrypoint: Xvfb did not come up; continuing without a display." >&2
        echo "  The headed-retry escalation will be skipped (fetch degrades to the" >&2
        echo "  headless render); everything else works normally." >&2
        break
    fi
    sleep 0.1
done

exec "$@"
