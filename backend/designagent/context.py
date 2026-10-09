"""Who a turn is running for, without putting it in graph state.

`/api/chat` sets `current_turn` before it starts the graph, and everything the
turn runs reads it: `Deps.settings` resolves to that user's effective settings,
and `TaskManager` routes `hpc` work to that user's interface. asyncio copies the
context into every task it creates, so concurrent turns cannot see each other's.

Why a ContextVar rather than `config["configurable"]`: LangGraph copies every
string, int, float and bool found in `configurable` into checkpoint metadata
(`get_checkpoint_metadata`, langgraph-checkpoint 4.2.0), so a key passed there
would be written to the checkpoint store on every turn. Nothing here is
serialized; the same rule `test_structures_are_not_carried_in_state` pins for
coordinates.

Unset outside an authenticated turn — the tests, the development server with
auth off — and then every reader falls back to the process-wide settings and the
single shared `hpc` interface, which is exactly the pre-login behaviour.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnContext:
    user_id: str
    username: str
    role: str
    # The effective `Settings`: the operator's, with this user's credentials over
    # them. Typed loosely so this module imports nothing from the package.
    settings: Any
    # The user's own task interface for `hpc` work, or None for "none". When
    # `use_shared_hpc` is true the process-wide interface is used instead: that
    # is the admin's case, whose credentials are the environment's.
    hpc: Any = None
    use_shared_hpc: bool = False
    hpc_error: str = ""
    extras: dict[str, Any] = field(default_factory=dict)


current_turn: ContextVar[TurnContext | None] = ContextVar("current_turn", default=None)
