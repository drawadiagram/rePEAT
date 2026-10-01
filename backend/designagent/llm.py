"""LLM access with a deterministic fallback.

Every node must work with no API key, so callers use `complete()` / `classify()`
and handle `None` by falling back to rules. Clients are built per call rather
than cached on a module global, because task bodies may run in a pool worker.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

from .config import Settings, get_settings

log = logging.getLogger(__name__)


def build_llm(settings: Settings | None = None, **kwargs: Any):
    """Return a ChatAnthropic, or None when no key is configured."""
    settings = settings or get_settings()
    if not settings.llm_available:
        return None
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.model,
        api_key=settings.anthropic_api_key,
        max_tokens=kwargs.pop("max_tokens", settings.max_tokens),
        temperature=kwargs.pop("temperature", 0.0),
        **kwargs,
    )


async def complete(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    **kwargs: Any,
) -> str | None:
    """One-shot completion. Returns None if no LLM is available or it errors."""
    llm = build_llm(settings, **kwargs)
    if llm is None:
        return None
    try:
        resp = await llm.ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
    except Exception as exc:  # the agent must degrade, not crash
        log.warning("LLM call failed, falling back to rules: %s", exc)
        return None
    return _text_of(resp)


async def complete_json(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    **kwargs: Any,
) -> dict | None:
    """Completion parsed as a JSON object, or None."""
    raw = await complete(
        system + "\n\nRespond with a single JSON object and nothing else.",
        user,
        settings=settings,
        **kwargs,
    )
    if raw is None:
        return None
    return extract_json(raw)


def extract_json(raw: str) -> dict | None:
    """Pull the first JSON object out of a model response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _text_of(resp: Any) -> str:
    """Flatten a LangChain message's content to plain text."""
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content)
