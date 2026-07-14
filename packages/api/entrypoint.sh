#!/bin/sh
# Starts a virtual display before the app, so Playwright's headed-retry escalation
# (extract/fetch.py — some anti-bot WAFs block headless Chromium but allow identical
# headed automation from the same IP) has a display to render into.
set -e
Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp &
export DISPLAY=:99
sleep 0.5
exec "$@"
