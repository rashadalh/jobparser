"""JSON-LD JobPosting extraction — SPEC §4.6, §10 item 3, IMPLEMENTATION_EXTRACT.

The *preferred* JD source: most ATSs emit a schema.org ``JobPosting`` block in a
``<script type="application/ld+json">``. We return its ``description`` stripped
to plain text.
"""

import json
import re
from typing import Any

from bs4 import BeautifulSoup

_WS = re.compile(r"\s+")


def _collapse(text: str) -> str:
    """Collapse all whitespace runs to single spaces and strip the ends."""
    return _WS.sub(" ", text).strip()


def _strip_html(value: str) -> str:
    """A JSON-LD ``description`` often embeds HTML markup — strip it to text."""
    return _collapse(BeautifulSoup(value, "lxml").get_text(" "))


def _find_jobposting(node: Any) -> dict[str, Any] | None:
    """Depth-first search for a ``JobPosting`` node.

    A JSON-LD node can be a dict or a list, and the JobPosting may be nested
    inside ``@graph`` (or any container). ``@type`` may be a string or a list.
    """
    # reason: parsed JSON-LD is arbitrary JSON
    if isinstance(node, dict):
        node_type = node.get("@type")
        if node_type == "JobPosting" or (
            isinstance(node_type, list) and "JobPosting" in node_type
        ):
            return node
        for value in node.values():
            found = _find_jobposting(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_jobposting(item)
            if found is not None:
                return found
    return None


def jobposting_jsonld(html: str) -> str | None:
    """Return the ``JobPosting.description`` (HTML-stripped) or ``None``.

    Parses every ``application/ld+json`` block defensively (malformed JSON is
    skipped) and returns the first JobPosting's ``description`` collapsed to text.
    """
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            # reason: parsed JSON-LD is arbitrary JSON
            data: Any = json.loads(raw)
        except (ValueError, TypeError):
            continue  # skip malformed blocks
        node = _find_jobposting(data)
        if node is None:
            continue
        description = node.get("description")
        if isinstance(description, str) and description.strip():
            return _strip_html(description)
    return None
