"""Per-turn trace: which node ran, for how long, and who wrote the reply.

A finished turn used to leave almost nothing behind. Progress lines carried their
node and were dropped by the browser; state writes carried their node and were
never persisted; the reply carried nothing at all, so answering "which node
produced this sentence?" meant grepping for the string. This module records the
node path as the turn runs.

Two constraints shape it.

**The trace rides the state channel, not a module global.** Several sessions share
one process, so an accumulator keyed by nothing would interleave their turns.
Returning the entry in the node's own state update makes LangGraph keep it per
thread, for free. It is turn-scoped: `app.py` resets `trace` in the per-turn
payload, so a checkpoint holds one turn's worth and never grows — and note that
the channel is `replace` for exactly that reason. An accumulating reducer cannot
be reset, because `[*saved, *[]]` is `saved`; the wrapper appends to what it read
instead.

**An entry holds counters and names, never payloads.** `DesignState` is
serialized every turn (see `test_structures_are_not_carried_in_state`), and a
trace that quoted the text it describes would double the size of every reply.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

log = logging.getLogger(__name__)

# The node currently executing, for code that is called *by* a node and cannot be
# passed the name: `TaskManager.submit` reads it so a task records who asked,
# rather than threading `node=` through every `run_many` call site. A ContextVar
# is copied into each asyncio task, so a node's submissions inherit it.
current_node: ContextVar[str] = ContextVar("designagent_current_node", default="")

#: Update keys worth keeping in a trace entry, and the shape each becomes. Fixed
#: so an entry cannot grow a payload by accident when a node starts writing a new
#: channel.
_SCALARS = ("intent", "round", "reply_source", "status")
_COUNTED = ("messages", "warnings", "artifacts", "ensemble", "worklist")


def entry(node: str, ms: int, update: dict[str, Any] | None, goto: Any) -> dict[str, Any]:
    """One node's contribution to the turn, as counters and names."""
    update = update or {}
    record: dict[str, Any] = {"node": node, "ms": ms, "goto": _goto_name(goto)}
    for key in _SCALARS:
        value = update.get(key)
        if value not in (None, "", 0):
            record[key] = value
    for key in _COUNTED:
        value = update.get(key)
        if isinstance(value, list) and value:
            record[f"n_{key}"] = len(value)
    return record


#: Ceiling on one turn. The analyst↔orchestrator loop runs a few rounds at most,
#: and a trace is only useful while it cannot become the largest thing in the
#: checkpoint.
MAX_ENTRIES = 40


def extend(trace: list[dict[str, Any]] | None, record: dict[str, Any]) -> list[dict[str, Any]]:
    """Append one record to the turn's trace, oldest dropped past the cap."""
    return [*(trace or []), record][-MAX_ENTRIES:]


def _goto_name(goto: Any) -> str:
    """`Command.goto` is a node name, END, or a list of either."""
    if isinstance(goto, str):
        return "end" if goto == "__end__" else goto
    if isinstance(goto, (list, tuple)):
        return ",".join(_goto_name(g) for g in goto)
    return ""


def summarize(trace: list[dict[str, Any]]) -> str:
    """One log line for a whole turn: the path, with timings."""
    if not trace:
        return "no nodes ran"
    path = " → ".join(f"{e['node']}({e.get('ms', 0)}ms)" for e in trace)
    total = sum(int(e.get("ms", 0)) for e in trace)
    author = next(
        (e["reply_source"] for e in reversed(trace) if e.get("reply_source")), "none"
    )
    return f"{path} = {total}ms · reply by {author}"
