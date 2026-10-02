"""Runtime dependencies handed to nodes.

Nodes are closures over a Deps instance rather than reaching for globals, so a
test can build a graph against a temp data dir and fake interfaces.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..artifacts.store import ArtifactStore
from ..config import Settings
from ..lake.store import DesignHistory
from ..tasks.manager import TaskManager

log = logging.getLogger(__name__)


@dataclass
class Deps:
    settings: Settings
    tasks: TaskManager
    history: DesignHistory
    artifacts: ArtifactStore
    # Why the last LLM call degraded to rules, for /api/health. Lives here rather
    # than on the Runtime because nodes only ever see Deps.
    last_llm_error: str = ""

    def campaign_id(self, state: dict) -> str:
        """One campaign per chat session, so history groups naturally."""
        return state.get("session_id") or "default"

    def note_llm_fallback(self, reason: str) -> None:
        """Record a fallback reason. Having no key at all is not an error."""
        from ..llm import NO_KEY

        self.last_llm_error = "" if reason == NO_KEY else reason


def llm_caveat(deps: Deps, sink: list[str]) -> Callable[[str], None]:
    """Collector for `llm.complete(on_fallback=...)`.

    Records the reason for `/api/health` and, when something is actually
    misconfigured, pushes a line onto `sink` for the node's `warnings` update —
    which the interpreter renders as "Caveats from this run". Running with no key
    is the documented offline mode and says nothing.
    """

    def record(reason: str) -> None:
        from ..llm import NO_KEY

        deps.note_llm_fallback(reason)
        if reason != NO_KEY:
            sink.append(f"Fell back to the rule-based path: {reason}")

    return record


def emit(event: dict) -> None:
    """Push a custom event to the chat stream, if one is listening.

    Safe to call outside a graph run (tests), where there is no stream writer.
    """
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
    except Exception:
        return
    if writer is None:
        return
    try:
        writer(event)
    except Exception as exc:
        log.debug("stream writer rejected event: %s", exc)


def status(text: str, **extra: Any) -> None:
    """A short progress line for the UI."""
    emit({"type": "status", "text": text, **extra})
