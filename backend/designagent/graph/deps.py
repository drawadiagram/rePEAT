"""Runtime dependencies handed to nodes.

Nodes are closures over a Deps instance rather than reaching for globals, so a
test can build a graph against a temp data dir and fake interfaces.
"""

from __future__ import annotations

import logging
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

    def campaign_id(self, state: dict) -> str:
        """One campaign per chat session, so history groups naturally."""
        return state.get("session_id") or "default"


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
