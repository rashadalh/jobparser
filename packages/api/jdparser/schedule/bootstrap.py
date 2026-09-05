"""Lambda process entry: runtime secrets into environ, then exec RIC (SPEC §4.1a)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_RUNTIME_KEYS = ("OPENROUTER_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY")


def load_runtime_secrets(client: Any | None = None) -> None:
    """Fetch RUNTIME_SECRET_ARN JSON and export the three API keys into os.environ.

    Does not exec RIC. Missing keys → JDParserError(SCHEDULE_SECRET_MISSING).
    """
    from jdparser.config import JDParserError

    arn = os.environ.get("RUNTIME_SECRET_ARN") or ""
    if not arn:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="RUNTIME_SECRET_ARN unset",
        )
    if client is None:
        import boto3  # lazy: tests inject a fake client

        client = boto3.client("secretsmanager")
    try:
        payload = client.get_secret_value(SecretId=arn)
        raw = payload.get("SecretString") or ""
        parsed: object = json.loads(raw) if raw else {}
    except JDParserError:
        raise
    except Exception as exc:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="runtime secret unavailable",
        ) from exc
    if not isinstance(parsed, dict):
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="runtime secret JSON is not an object",
        )
    missing: list[str] = []
    for key in _RUNTIME_KEYS:
        val = parsed.get(key)
        if not isinstance(val, str) or not val:
            missing.append(key)
            continue
        os.environ[key] = val
    if missing:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message="runtime secret missing " + ", ".join(missing),
        )


def ric_python() -> str:
    """Venv interpreter, not sys.executable.

    uv/venv ``bin/python`` is often a symlink to /usr/local/bin/python. execv of
    that path drops site-packages, so ``-m awslambdaric`` fails on the system
    interpreter. ``sys.prefix/bin/python`` keeps the venv.
    """
    return str(Path(sys.prefix) / "bin" / "python")


def main() -> None:
    load_runtime_secrets()
    python = ric_python()
    os.execv(python, [python, "-m", "awslambdaric", "jdparser.schedule.handler.handler"])


if __name__ == "__main__":
    main()
