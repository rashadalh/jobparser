# syntax=docker/dockerfile:1
# --- jdparser API (FastAPI + LangGraph + Playwright), uv-managed, Python 3.11 ---
FROM python:3.11-slim-bookworm

# uv from the pinned release image (matches the host toolchain; no version drift).
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_FROZEN=1 \
    PYTHONUNBUFFERED=1

# The display the entrypoint's Xvfb serves, and that Playwright's headed retry renders
# into. It lives HERE rather than only in entrypoint.sh because a shell `export` reaches
# the app the entrypoint execs and nothing else — `docker exec` gets the image's ENV, not
# the entrypoint's environment. Without this, an exec'd shell (or a CLI run:
# `docker exec jdparser-api-1 uv run python -m jdparser resume.pdf`) had no DISPLAY and
# every headed retry failed with "Missing X server or $DISPLAY", even though Xvfb was
# running perfectly well in that same container. entrypoint.sh reads this value rather
# than hardcoding its own, so there is one definition.
ENV DISPLAY=:99

WORKDIR /app

# 1) Dependency layer — cached unless uv.lock / pyproject.toml change. Installs the
#    pinned runtime deps (incl. playwright) but NOT the project or the dev group.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) Browser + OS deps for the JS-render fallback. `--with-deps` apt-installs the
#    chromium system libraries; the browser build matches the pinned playwright 1.60.0.
#    Call the venv binary directly (not `uv run`, which would re-sync the dev group).
RUN /app/.venv/bin/playwright install --with-deps chromium
# Xvfb: some anti-bot WAFs block Playwright's headless Chromium outright but allow
# identical headed automation from the SAME IP (confirmed empirically, not theoretical
# — see extract/fetch.py's headed-retry escalation). Headed mode needs a real display,
# which a container doesn't have without one; explicit install (not relying on it being
# a transitive dep of --with-deps, which could change).
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

# 3) Application source + editable project install.
COPY jdparser ./jdparser
RUN uv sync --frozen --no-dev
# RIC for Lambda dual-mode (Linux wheel). Do not add to pyproject.toml — breaks macOS uv sync.
RUN uv pip install --python /app/.venv/bin/python awslambdaric==4.0.2
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# data/ (resume cache, run records, uploads) — also created at import by config.py;
# mounted as a volume in compose so it persists across restarts.
RUN mkdir -p data/profiles data/runs data/uploads

EXPOSE 8000
# Secrets (OPENROUTER/ADZUNA keys) and WEB_ORIGIN are injected at runtime via compose.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uv", "run", "uvicorn", "jdparser.server:app", "--host", "0.0.0.0", "--port", "8000"]
