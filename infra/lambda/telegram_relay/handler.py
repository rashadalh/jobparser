"""SNS → Telegram alarm relay. Stdlib + Lambda-managed boto3. Zip root: handler.py."""

from __future__ import annotations

import json
import os
import urllib.request

import boto3

_creds: tuple[str, str] | None = None
# Keep names and values in lockstep with jdparser.config (this zip cannot import it).
TELEGRAM_CHUNK_CHARS = 3500
TELEGRAM_SEND_TIMEOUT_S = 10


def _load_credentials() -> tuple[str, str]:
    global _creds
    if _creds is not None:
        return _creds
    arn = os.environ["TELEGRAM_SECRET_ARN"]
    raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
    parsed = json.loads(raw)
    token = parsed.get("TELEGRAM_BOT_TOKEN") or ""
    chat_id = parsed.get("TELEGRAM_CHAT_ID") or ""
    if not token or not chat_id:
        raise RuntimeError("telegram secret missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
    _creds = (token, chat_id)
    return _creds


def _format_alarm(message: str) -> str:
    try:
        alarm = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        body = message or "unparseable alarm"
    else:
        if isinstance(alarm, dict):
            name = alarm.get("AlarmName") or "unknown"
            state = alarm.get("NewStateValue") or ""
            reason = alarm.get("NewStateReason") or ""
            body = " ".join(part for part in (name, state, reason) if part)
        else:
            body = message
    if len(body) > TELEGRAM_CHUNK_CHARS:
        body = body[: TELEGRAM_CHUNK_CHARS - 3] + "..."
    return f"jdparser alarm: {body}"


def _send(text: str, *, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TELEGRAM_SEND_TIMEOUT_S) as resp:
        status = int(getattr(resp, "status", 200))
        if not (200 <= status < 300):
            raise RuntimeError(f"telegram HTTP {status}")


def handler(event, context):
    token, chat_id = _load_credentials()
    for record in event.get("Records") or []:
        sns = record.get("Sns") or {}
        message = sns.get("Message") or ""
        _send(_format_alarm(message), token=token, chat_id=chat_id)
    return {"ok": True}
