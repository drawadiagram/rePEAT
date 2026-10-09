# Code for the deck — staging file

Blocks to put on slides for the designagent code walk. Companion to `DECK_OUTLINE.md` (not yet
written) and `build_deck.js` (not yet written); slide numbers and snippet IDs are meant to match
across all three.

**Derived against `main` @ `e8467e6`** (2026-10-01). Every anchor below was verified by reading the
cited lines in this session, not from memory.

**Fidelity marking, on every block:**

| Mark | Meaning |
|---|---|
| **VERBATIM** | byte-identical to the source at the cited lines — safe to present as "this is the code" |
| **TRIMMED** | verbatim lines with whole lines removed (elisions marked `...`); nothing rewritten |
| **EDITED** | restructured for legibility — **say so on the slide**, and show the real file in the terminal |
| **ILLUSTRATIVE** | **not from the repo** — label it on the slide, never let it be quoted as shipped code |

Priority: ★ must show · ○ show if time. ~20 lines is the legible maximum at presentation size.

| ID | Slide | Source | Lines | Fidelity | Pri |
|---|---|---|---|---|---|
| S4-A | 4 | `backend/designagent/graph/build.py:147–164` | 16 | VERBATIM | ★ |
| S4-B | 4 | `backend/designagent/graph/nodes/coordinator.py:145–177` | 20 | TRIMMED | ★ |
| S6-A | 6 | `backend/designagent/graph/state.py:211–240` | 20 | TRIMMED | ★ |
| S6-B | 6 | `backend/designagent/graph/state.py:159–204` (reducers) | 16 | TRIMMED | ○ |
| S6-C | 6 | `tests/test_graph.py::test_structures_are_not_carried_in_state` | 12 | TRIMMED | ★ |
| S7-A | 7 | `backend/designagent/graph/nodes/orchestrator.py:273–297` | 19 | TRIMMED | ★ |
| S7-B | 7 | `backend/designagent/graph/nodes/orchestrator.py:314–330` | 17 | VERBATIM | ○ |
| S8-A | 8 | `backend/designagent/tasks/base.py:22–34, 62–67, 70–83` | 20 | TRIMMED | ★ |
| S8-B | 8 | `backend/designagent/tasks/base.py:92–120` (`TaskHandle`) | 18 | TRIMMED | ★ |
| S8-C | 8 | `backend/designagent/tasks/base.py:148–186` (the ABC) | 20 | TRIMMED | ★★ |
| S8-D | 8 | `backend/designagent/tasks/manager.py:111–128` | 18 | VERBATIM | ★ |
| S8-E | 8 | `backend/designagent/tasks/manager.py:76–88` (`interface_for`) | 13 | VERBATIM | ○ |
| S9-A | 9 | `backend/designagent/runtime.py:179–219` | 20 | TRIMMED | ★★ |
| S9-B | 9 | `backend/designagent/tasks/local.py:58–63` | 6 | VERBATIM | ★ |
| S9-C | 9 | `refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py:73–84` | 12 | **VERBATIM — the bug** | ★★ |
| S9-D | 9 | `refcodes/flowgentic/.../fault_tolerance.py:23–37` (the defaults) | 15 | TRIMMED | ★ |
| S10-A | 10 | `backend/designagent/tasks/hpc/orbit.py:328–343` | 16 | VERBATIM | ★★ |
| S10-B | 10 | `backend/designagent/tasks/hpc/orbit.py:345–369` | 20 | TRIMMED | ★★ |
| S10-C | 10 | `backend/designagent/tasks/hpc/base.py:80–103` (`drain_logs`) | 20 | TRIMMED | ★ |
| S10-D | 10 | `backend/designagent/tasks/hpc/orbit.py:400–426` (`_finish_job`) | 20 | TRIMMED | ★ |
| S11-A | 11 | `backend/designagent/tasks/hpc/globus.py:31–36` (capabilities) | 6 | VERBATIM | ★ |
| S11-B | 11 | `backend/designagent/tasks/hpc/globus.py:90–104` | 15 | VERBATIM | ★ |
| S12-A | 12 | `backend/designagent/tools/molviz_agent.py:143–154` | 12 | TRIMMED | ○ |
| S13-A | 13 | `backend/designagent/lake/graph.py:24–51` (the schema) | 20 | TRIMMED | ★ |
| S13-B | 13 | `backend/designagent/lake/store.py:89–120` | 18 | TRIMMED | ★ |
| S14-A | 14 | `backend/designagent/graph/nodes/analyst.py:166–195` | 20 | TRIMMED | ★★ |
| S14-B | 14 | `backend/designagent/graph/nodes/analyst.py:405–411` (`_rank_in_memory`) | 7 | VERBATIM | ★ |
| S15-A | 15 | `frontend/src/lib/api.ts:36–58` | 20 | TRIMMED | ○ |
| S16-A | 16 | — (shell) | 6 | VERBATIM | ★ |
| S17-A | 17 | `backend/designagent/config.py:69–72` | 4 | **VERBATIM** | ★★ |

**Re-deriving anchors.** Do this before presenting; drift hides here. Every anchor above is checked
by matching the snippet's first code line against the current source, which is automated:

```sh
python3 slides/check_anchors.py      # 36/36
```

It reports, for any anchor that moved, where the line actually is — so this file and `build_deck.js`
get corrected rather than presented wrong. Add an entry to `ANCHORS` whenever a snippet is added
here. Do not present a drifted anchor: a slide citing `tasks/base.py:135` while showing code from
somewhere else is worse than a slide with no citation at all.

---

## The three blocks that carry the asks

These are the ones the middleware authors will read closely. They are quoted here in full because
the slide text must not paraphrase them.

### S9-C — flowgentic hard-imports `aiohttp` · **VERBATIM**

`refcodes/flowgentic/src/flowgentic/langGraph/fault_tolerance.py:73–84`

```python
		# Try to include aiohttp timeouts if present
		try:
			import aiohttp  # type: ignore

			ex.extend(
				[
					aiohttp.ClientConnectionError,
					aiohttp.ServerTimeoutError,
				]
			)
		except Exception:
			raise
```

The comment says *if present*; the code says *or die*. The same shape appears for `httpx` at
`:60–72`. Both become hard requirements of flowgentic. `except Exception: pass` is clearly what was
meant.

Reached whenever `RetryConfig.retryable_exceptions` is left at its default — which is `()`
(`:38–40`), i.e. the common case. Passing an explicit tuple is what sidesteps it, and that is what
`runtime.py:208` does.

Observed as: `ModuleNotFoundError: No module named 'aiohttp'` raised from `fault_tolerance.py:75` on
the first wrapped task, in an env with `httpx` but not `aiohttp`.

### S9-D — the retry defaults · **TRIMMED** from `fault_tolerance.py:23–37`

```python
	max_attempts: int = Field(
		default=3, description="Total attempts including the first try"
	)
	...
	timeout_sec: Optional[float] = Field(
		default=30.0, description="Per-attempt timeout (None disables timeout)"
	)
	retryable_exceptions: Tuple[type, ...] = Field(
		default=(), description="Tuple of exception classes considered transient"
	)
```

30 s with 3 attempts is right for a service call and wrong for a protein fold. Measured against this
repo's own workload: the six round-1 folds in the reference campaign took **13.3 s to 31.7 s** each,
and round 2 ran to **42.4 s** — so the default would have cancelled and silently re-run roughly half
of them. Our wrapper passes `timeout_sec=None, max_attempts=1` (`runtime.py:204–209`).

### S17-A — the deviation, recorded in the code · **VERBATIM**

`backend/designagent/config.py:69–72`

```python
    # Route node bodies through flowgentic's EXECUTION_BLOCK as well as tasks.
    # Off by default: it runs nodes outside LangGraph's runnable context, which
    # silently disables status and token streaming. See graph/build.py.
    wrap_nodes: bool = False
```

The approved design had every node wrapped as an `EXECUTION_BLOCK`. It ships off. A node body run on
asyncflow's loop is outside LangGraph's runnable context, so `get_stream_writer()` raises
`RuntimeError: Called get_config outside of a runnable context` and every custom event is dropped
**silently** — the graph still completes, the chat just goes quiet. Isolated both ways: a plain node
emits its status lines, a wrapped one emits none.

The ask for the room: **is `EXECUTION_BLOCK` meant to preserve the caller's context?** If yes, this
is a bug worth fixing and `wrap_nodes=True` becomes the default. If no, flowgentic's node wrapping
and LangGraph's streaming are mutually exclusive, and that belongs in its README.

---

## Orbit findings, with the lines that work around them

| Finding | Evidence | Workaround |
|---|---|---|
| The terminal rhapsody event carries state and `exit_code` but **not `stdout`** | a completed `/bin/echo` task resolved with an empty result | `orbit.py:323–335` `_finish_task_enriched` re-fetches with `get_task` before settling the future |
| A FAILED job reports **only a non-zero exit code** — no reason reaches the client | `handle.error` was empty for a job that exited 3 | `orbit.py:428–433` synthesises one from exit code, then stderr, then the log tail |
| The broker has **no HTTP topology route** | `/topology` → 307 → 404, `Endpoint 'topology' unknown` (it reads as a plugin name) | readiness comes from the client's own `rt.topology()`, polled in `connect()`; the local stack greps the endpoint's log for `registered as '<name>'` (`local_orbit.py:201–220`) |
| `--no-auth` disables **only ingress auth** — the broker still refuses to start without cert and key | broker exited at startup with `--no-auth` alone | `local_orbit.py:60–83` generates a throwaway self-signed pair, SAN `IP:127.0.0.1,DNS:localhost`, key `0600` |
| All Orbit clients are **synchronous/blocking** | `RhapsodyClient` / `PSIJClient` methods block | every call goes through `asyncio.to_thread` (23 sites in `orbit.py`) |
| Push callbacks arrive on a **listener thread** | — | `orbit.py:328–332` `_dispatch` hops to the loop with `call_soon_threadsafe` |

Not a complaint, worth saying out loud: offset-based log tailing via `PSIJClient.get_job_status(job_id,
stdout_offset, stderr_offset)` is the **only** streaming mechanism either backend offers, and it
works. `drain_logs` (`hpc/base.py:80–101`) is built on it and `tests/test_orbit_local.py:92` proves
incremental tailing against a real local endpoint.

---

## S16-A — running it · **VERBATIM** (shell)

```sh
./scripts/setup.sh                      # the only supported install path
./scripts/setup.sh --check              # verify an existing .venv, install nothing

python -m designagent --reload          # :8000, needs the __main__ guard (ProcessPoolExecutor)
cd frontend && npm install && npm run dev   # :5173, proxies /api
pytest -q                               # 93 tests, offline
pytest -q -m live                       # 12 tests against a real localhost broker + endpoint
```

`uv sync` cannot work: the three middleware packages are local editable installs from `refcodes/`
(gitignored), and flowgentic pins `radical-asyncflow` and `academy-py` to git URLs that fight the
local checkouts, so it is installed `--no-deps`. The live tier is deselected by `addopts`, so naming
`tests/test_orbit_local.py` alone collects nothing — `-m live` is what selects it.

No `ANTHROPIC_API_KEY` is required: every node has a deterministic rule-based path, and the
reference campaign in `run.json` was produced with `llm: false`.
