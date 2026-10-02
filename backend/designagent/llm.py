"""LLM access with a deterministic fallback.

Every node must work with no API key, so callers use `complete()` / `classify()`
and handle `None` by falling back to rules. Clients are built per call rather
than cached on a module global, because task bodies may run in a pool worker.

`None` alone cannot say *why* the rules were used, and "no key" and "the key you
just typed in was rejected" need different answers from the UI. Pass
`on_fallback=` to receive a short reason; nodes hand it a collector that appends
to the `warnings` state channel, which the interpreter already renders under
"Caveats from this run".
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger(__name__)

# Reasons handed to `on_fallback`. NO_KEY is the ordinary offline mode and is not
# worth a caveat in the reply; the rest mean something is misconfigured.
NO_KEY = "no-key"

Fallback = Callable[[str], None]


def build_llm(settings: Settings | None = None, **kwargs: Any):
    """Return a ChatAnthropic, or None when no key is configured."""
    settings = settings or get_settings()
    if not settings.llm_available:
        return None
    from langchain_anthropic import ChatAnthropic

    options: dict[str, Any] = {
        "model": settings.model,
        "api_key": settings.llm_key,
        "max_tokens": kwargs.pop("max_tokens", settings.max_tokens),
    }
    # Only sent when a caller asks for it. This used to default to 0.0, which the
    # current models reject outright — "`temperature` is not supported for
    # claude-sonnet-5-5 at non-default values" — so *every* LLM call failed and
    # fell back to rules. Invisible until a real key was configured, because with
    # no key the call never happens.
    temperature = kwargs.pop("temperature", None)
    if temperature is not None:
        options["temperature"] = temperature
    return ChatAnthropic(**options, **kwargs)


async def complete(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    on_fallback: Fallback | None = None,
    **kwargs: Any,
) -> str | None:
    """One-shot completion. Returns None if no LLM is available or it errors."""
    llm = build_llm(settings, **kwargs)
    if llm is None:
        if on_fallback:
            on_fallback(NO_KEY)
        return None
    try:
        resp = await llm.ainvoke(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )
    except Exception as exc:  # the agent must degrade, not crash
        log.warning("LLM call failed, falling back to rules: %s", exc)
        if on_fallback:
            on_fallback(describe_error(exc))
        return None
    return _text_of(resp)


async def complete_json(
    system: str,
    user: str,
    *,
    settings: Settings | None = None,
    on_fallback: Fallback | None = None,
    **kwargs: Any,
) -> dict | None:
    """Completion parsed as a JSON object, or None."""
    raw = await complete(
        system + "\n\nRespond with a single JSON object and nothing else.",
        user,
        settings=settings,
        on_fallback=on_fallback,
        **kwargs,
    )
    if raw is None:
        return None
    parsed = extract_json(raw)
    if parsed is None and on_fallback:
        on_fallback("the model's reply was not JSON")
    return parsed


def describe_error(exc: BaseException) -> str:
    """A short reason, classified enough to be actionable.

    The provider's own message is included but truncated: it is not our text, it
    can be long, and it is the one place an echoed request body could surface.
    """
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "authentication" in lowered or "401" in lowered or "invalid x-api-key" in lowered:
        kind = "the API key was rejected"
    elif "rate" in lowered and "limit" in lowered or "429" in lowered:
        kind = "rate limited"
    elif "not_found" in lowered or "404" in lowered:
        kind = "the model name was rejected"
    elif isinstance(exc, TimeoutError) or "timeout" in lowered:
        kind = "the request timed out"
    else:
        kind = "the LLM call failed"
    return f"{kind}: {text[:200]}"


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
