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

WORKDIR /app

# 1) Dependency layer — cached unless uv.lock / pyproject.toml change. Installs the
#    pinned runtime deps (incl. playwright) but NOT the project or the dev group.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) Browser + OS deps for the JS-render fallback. `--with-deps` apt-installs the
#    chromium system libraries; the browser build matches the pinned playwright 1.60.0.
#    Call the venv binary directly (not `uv run`, which would re-sync the dev group).
RUN /app/.venv/bin/playwright install --with-deps chromium

# 3) Application source + editable project install.
COPY jdparser ./jdparser
RUN uv sync --frozen --no-dev

# data/ (resume cache, run records, uploads) — also created at import by config.py;
# mounted as a volume in compose so it persists across restarts.
RUN mkdir -p data/profiles data/runs data/uploads

EXPOSE 8000
# Secrets (OPENROUTER/ADZUNA keys) and WEB_ORIGIN are injected at runtime via compose.
CMD ["uv", "run", "uvicorn", "jdparser.server:app", "--host", "0.0.0.0", "--port", "8000"]
