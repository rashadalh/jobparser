"""OpenRouter client (OpenAI SDK + instructor) + the shared call helpers.

SPEC §6.3 / IMPLEMENTATION_LLM "client.py" + "Canonical call shape". The four LLM
agents import `_call` from here, so the canonical call shape lives in exactly one
place. Reasoning depth is steered only via OpenRouter's `reasoning` control passed
through `extra_body` (no provider-specific `thinking`/`effort` params).
"""

from typing import Any, TypeVar

import instructor
from instructor.core import InstructorRetryException  # moved from instructor.exceptions in 1.15.3
from openai import OpenAI
from pydantic import BaseModel

from jdparser.config import (
    JDParserError,
    LLM_MAX_RETRIES,
    NodeCfg,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_APP_URL,
    OPENROUTER_BASE_URL,
)

T = TypeVar("T", bound=BaseModel)

_oai = OpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=OPENROUTER_API_KEY,
    default_headers={  # optional OpenRouter attribution
        "HTTP-Referer": OPENROUTER_APP_URL,
        "X-Title": OPENROUTER_APP_TITLE,
    },
)
# instructor patches the client: chat.completions gains `response_model` (Pydantic)
# + `max_retries` (re-ask on validation failure). Mode.JSON is the most portable
# across heterogeneous OpenRouter models.
_client = instructor.from_openai(_oai, mode=instructor.Mode.JSON)


def _reasoning_body(setting: str) -> dict[str, Any]:  # reason: heterogeneous extra_body JSON (SPEC §6.3)
    # OpenRouter normalizes `reasoning` across providers (GLM, Gemini, ...).
    if setting == "off":
        return {"reasoning": {"enabled": False}}
    return {"reasoning": {"effort": setting}}  # "low" | "medium" | "high"


def _hit_length(exc: InstructorRetryException) -> bool:
    # best-effort: did the failed retry loop end on a length-truncation?
    comp = getattr(exc, "last_completion", None)
    try:
        return bool(comp and comp.choices and comp.choices[0].finish_reason == "length")
    except Exception:  # reason: defensive — never mask the real error
        return False


def _call(cfg: NodeCfg, system: str, user: str, schema: type[T], err_code: str) -> T:
    try:
        obj, completion = _client.chat.completions.create_with_completion(
            model=cfg["model"],
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],  # generous ceiling; billed only as generated
            response_model=schema,  # instructor -> validated Pydantic instance
            max_retries=LLM_MAX_RETRIES,  # re-ask the model on a validation failure
            extra_body=_reasoning_body(cfg["reasoning"]),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except InstructorRetryException as e:
        if _hit_length(e):  # truncation, not a real schema problem
            raise JDParserError(
                code="LLM_TRUNCATED",
                message=f"{err_code}: hit max_tokens; raise LLM_MAX_TOKENS_<NODE>",
            )
        raise JDParserError(code=err_code, message=f"invalid structured output: {e}")
    except Exception as e:  # openai.APIError / RateLimitError / provider error
        raise JDParserError(code="LLM_API_ERROR", message=str(e))
    # validated, but the provider still flagged truncation -> surface it, don't return partial
    if completion.choices and completion.choices[0].finish_reason == "length":
        raise JDParserError(
            code="LLM_TRUNCATED",
            message=f"{err_code}: finish_reason=length; raise LLM_MAX_TOKENS_<NODE>",
        )
    return obj
