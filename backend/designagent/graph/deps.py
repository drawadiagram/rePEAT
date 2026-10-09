"""Runtime dependencies handed to nodes.

Nodes are closures over a Deps instance rather than reaching for globals, so a
test can build a graph against a temp data dir and fake interfaces.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..artifacts.store import ArtifactStore
from ..config import Settings
from ..context import current_turn
from ..lake.store import DesignHistory
from ..tasks.manager import TaskManager

log = logging.getLogger(__name__)


class Deps:
    """What a node may reach. `settings` is per turn when someone is signed in.

    Nodes read `deps.settings` on every turn, so making it resolve the turn's
    effective settings (`context.current_turn`) is what carries a user's own key
    and allocation to every node without one of them knowing users exist. With
    no turn context — tests, auth off — it is the process's settings, as before.
    Assigning to it sets that process-wide baseline (`Runtime.reconfigure`).
    """

    def __init__(
        self,
        settings: Settings,
        tasks: TaskManager,
        history: DesignHistory,
        artifacts: ArtifactStore,
    ):
        self.base_settings = settings
        self.tasks = tasks
        self.history = history
        self.artifacts = artifacts
        # Why the last LLM call degraded to rules, for /api/health and /api/me,
        # keyed by user id ("" when nobody is signed in). Per user, because one
        # user's rejected key says nothing about another's.
        self._llm_errors: dict[str, str] = {}

    @property
    def settings(self) -> Settings:
        turn = current_turn.get()
        return turn.settings if turn is not None else self.base_settings

    @settings.setter
    def settings(self, value: Settings) -> None:
        self.base_settings = value

    @staticmethod
    def _who() -> str:
        turn = current_turn.get()
        return turn.user_id if turn is not None else ""

    @property
    def last_llm_error(self) -> str:
        return self._llm_errors.get(self._who(), "")

    @last_llm_error.setter
    def last_llm_error(self, value: str) -> None:
        self._llm_errors[self._who()] = value

    def llm_error_for(self, user_id: str) -> str:
        return self._llm_errors.get(user_id, "")

    def forget_llm_error(self, user_id: str) -> None:
        self._llm_errors.pop(user_id, None)

    def clear_llm_errors(self) -> None:
        self._llm_errors.clear()

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
