#!/usr/bin/env python3
"""Verify every code anchor cited in the deck still points where it claims.

Anchors are load-bearing: a slide that says `tasks/base.py:135` and shows code
from somewhere else is worse than no citation, and this audience will check. Run
before presenting, and after any edit to the backend:

    python3 slides/check_anchors.py

Each entry is (path, line, expected first line of the snippet). The check is a
prefix match on the stripped line, so indentation changes are tolerated and real
movement is not. When an anchor has drifted, the script reports where the line
actually is, so `CODE_FOR_DECK.md` and `build_deck.js` can be corrected rather
than papered over.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (path, line, expected first line) — keep in sync with CODE_FOR_DECK.md.
ANCHORS: list[tuple[str, int, str]] = [
    # S4 — the loop
    ("backend/designagent/graph/build.py", 99, "destinations = {"),
    ("backend/designagent/graph/nodes/coordinator.py", 93, "def classify_rules"),
    # S6 — state
    ("backend/designagent/graph/state.py", 168, "class DesignState"),
    ("backend/designagent/graph/state.py", 117, "def merge_artifacts"),
    # S7 — the orchestrator's blob write
    ("backend/designagent/graph/nodes/orchestrator.py", 252,
     'summary["structure_path"] = deps.history.write_blob('),
    ("backend/designagent/graph/nodes/orchestrator.py", 274, "return Command("),
    # S8 — the task contract
    ("backend/designagent/tasks/base.py", 22, "class TaskState"),
    ("backend/designagent/tasks/base.py", 62, "@dataclass(frozen=True)"),
    ("backend/designagent/tasks/base.py", 92, "@dataclass"),
    ("backend/designagent/tasks/base.py", 135, "class TaskInterface(ABC):"),
    ("backend/designagent/tasks/manager.py", 75, "def interface_for"),
    ("backend/designagent/tasks/manager.py", 99, "try:"),
    # S9 — the substrate
    ("backend/designagent/runtime.py", 76, "retry = RetryConfig("),
    ("backend/designagent/tasks/local.py", 58, "async def submit"),
    ("refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py", 73,
     "# Try to include aiohttp timeouts if present"),
    ("refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py", 23,
     "max_attempts: int = Field("),
    # S10 — Orbit
    ("backend/designagent/tasks/hpc/orbit.py", 261, "def _dispatch"),
    ("backend/designagent/tasks/hpc/orbit.py", 286,
     "# The terminal event carries state and exit_code but not"),
    ("backend/designagent/tasks/hpc/orbit.py", 353,
     "if state is TaskState.FAILED and not error:"),
    ("backend/designagent/tasks/hpc/base.py", 78, "async def drain_logs"),
    # S11 — Globus
    ("backend/designagent/tasks/hpc/globus.py", 31, "capabilities = Capabilities("),
    ("backend/designagent/tasks/hpc/globus.py", 90, "def _submit_shell"),
    # S12 — the visualization agent
    ("backend/designagent/tools/molviz_agent.py", 143, "def sanitize_spec"),
    # S13 — the lake
    ("backend/designagent/lake/graph.py", 24, "_SCHEMA = ["),
    ("backend/designagent/lake/store.py", 87, "def record_task_result"),
    # S14 — degradation
    ("backend/designagent/graph/nodes/analyst.py", 155,
     "# A storage failure must not lose"),
    ("backend/designagent/graph/nodes/analyst.py", 374, "def _rank_in_memory"),
    # S15 — the frontend
    ("frontend/src/lib/api.ts", 30, 'let buffer = "";'),
    # S17 / B1 — the wrap_nodes deviation
    ("backend/designagent/config.py", 37,
     "# Route node bodies through flowgentic's EXECUTION_BLOCK"),
    ("backend/designagent/graph/build.py", 51, "def wrap_node"),
    # B2 — the local Orbit stack
    ("backend/designagent/tasks/hpc/local_orbit.py", 193, "async def _wait_for_endpoint"),
    # Cited in slide body text rather than in a code block's anchor label. These
    # were untracked until an edit to app.py shifted one of them by a line, which
    # nothing caught — a citation is a citation wherever it appears on the slide.
    ("backend/designagent/app.py", 77, "def _frame"),
    ("backend/designagent/tasks/base.py", 146, "async def submit"),
    ("backend/designagent/runtime.py", 172, 'notes.append(f"Using in-memory checkpoints'),
    ("refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py", 71, "except Exception:"),
    ("refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py", 75,
     "import aiohttp"),
]


def main() -> int:
    drifted = 0
    for rel, line_no, expected in ANCHORS:
        path = ROOT / rel
        if not path.exists():
            print(f"MISSING  {rel}")
            drifted += 1
            continue
        lines = path.read_text(errors="replace").splitlines()
        actual = lines[line_no - 1].strip() if line_no - 1 < len(lines) else "<past EOF>"
        if actual.startswith(expected.strip()):
            continue
        real = next(
            (i + 1 for i, l in enumerate(lines) if l.strip().startswith(expected.strip())),
            None,
        )
        print(f"DRIFT    {rel}:{line_no}")
        print(f"         expected {expected!r}")
        print(f"         found    {actual!r}")
        print(f"         now at   {real if real else 'NOT FOUND — snippet may be gone'}")
        drifted += 1

    ok = len(ANCHORS) - drifted
    print(f"\n{ok}/{len(ANCHORS)} anchors verified" + ("" if drifted else " — all good"))
    if drifted:
        print("Fix CODE_FOR_DECK.md and build_deck.js; do not present drifted anchors.")
    return 1 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
