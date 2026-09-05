"""Parse Secrets Manager SecretString JSON objects."""

from __future__ import annotations

import json
from typing import Any

from jdparser.config import JDParserError


def load_secret_object(client: Any, arn: str, *, label: str) -> dict[str, Any]:
    """Return the secret JSON object, or raise JDParserError(SCHEDULE_SECRET_MISSING)."""
    try:
        payload = client.get_secret_value(SecretId=arn)
        raw = payload.get("SecretString") or ""
        parsed: object = json.loads(raw) if raw else {}
    except JDParserError:
        raise
    except Exception as exc:
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message=f"{label} secret unavailable",
        ) from exc
    if not isinstance(parsed, dict):
        raise JDParserError(
            code="SCHEDULE_SECRET_MISSING",
            message=f"{label} secret JSON is not an object",
        )
    return parsed
