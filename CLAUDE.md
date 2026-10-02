# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

The three middleware packages are **local editable installs from `refcodes/`, which is gitignored
and not in a fresh clone**. They are listed in `[tool.uv.sources]` but *not* in `dependencies`, so
`uv sync` will not install them — use the explicit sequence:

```bash
uv venv --python 3.12 .venv
uv pip install -e refcodes/radical.asyncflow -e refcodes/rhapsody
uv pip install --no-deps -e refcodes/flowgentic   # --no-deps: its pins conflict
uv pip install -e '.[dev]'
```

`config.yml` in the **current working directory** is read at `import flowgentic` time. Both
`agent_execution` and `logger` keys must be present — flowgentic does `APP_SETTINGS["logger"]["level"]`
with no fallback once it finds a file. Run everything from the repo root.

## Commands

```bash
.venv/bin/python -m designagent --reload        # backend on :8000
cd frontend && npm run dev                      # Vite on :5173, proxies /api
cd frontend && npm run build                    # tsc -b && vite build

.venv/bin/python -m pytest -q                   # 83 offline tests, no network
.venv/bin/python -m pytest -q -m "not live"     # same, explicit
.venv/bin/python -m pytest tests/test_graph.py::test_full_redesign_loop -q   # one test
.venv/bin/python -m pytest tests/test_orbit_local.py -q   # 6 live tests; starts a real broker
.venv/bin/ruff check backend tests              # line-length 100
```

The `live` marker gates the 6 tests that need a substrate, but **nothing deselects them by default**:
a bare `pytest -q` runs all 89 and starts a real broker (89 s, versus 64 s for `-m "not live"`). They
self-skip only when the Orbit CLI scripts cannot be found, which is false in any working tree. Use
`-m "not live"` for the fast loop. See `plans/BACKLOG.md` B3.

## Architecture

A LangGraph loop behind a FastAPI/SSE chat, with long work pushed off the event loop through a Task
Interface layer. The whole design follows from one constraint: **a chat interface must answer in
milliseconds while a fold takes tens of seconds and an HPC job takes hours.**

```
browser ─SSE─ app.py ── graph/build.py ── nodes ── Deps ── TaskManager ── interfaces
                                                    │                        │
                                            lake/store.py            local │ query │ hpc
                                         (Kuzu/SQLite/Parquet)                       │
                                                              flowgentic → asyncflow → rhapsody pool
                                                              Orbit (EndpointRuntime) │ Globus Compute
```

### The rules that are not obvious from one file

**Nodes reach the outside only through `Deps`** (`graph/deps.py`: settings, tasks, history,
artifacts). Never import a store or an interface into a node. This is convention, not an enforced
check, and it is the only reason 83 tests run with no network, no process pool and no endpoint — the
suite hands nodes an in-process `TaskManager` and a `tmp_path` lake.

**Every interface's `submit()` returns immediately with a handle whose `future` resolves later.**
Nothing in the graph awaits at submission time. A *failed submission* becomes a settled FAILED handle
rather than an exception (`tasks/manager.py`), so callers have exactly one shape to handle and
`gather()` never raises.

**State must not carry bulky payloads.** LangGraph serializes a checkpoint every turn, so coordinates
go to a content-addressed blob (`history.write_blob`) and travel as `structure_path`. Pinned by
`tests/test_graph.py::test_structures_are_not_carried_in_state`, which asserts `"ATOM  "` never
appears in serialized state. The regression is invisible — nothing breaks, it just grows every turn.

**Routing is `Command(goto=..., update=...)` from the node body**, so the routing decision and the
state write are one atomic return. `build.py` declares `destinations` per node for validation; the
only static edge is `START → coordinator`.

**Process-pool constraints** (`runtime.py`, `tools/`): task bodies live at module level with no
closures over clients, HTTP/LLM clients are constructed *inside* the body, and the entry point needs
its `__main__` guard or uvicorn re-imports under fork.

**Lake writes are individually guarded.** A storage failure must not lose a round the user waited two
minutes for: the analyst catches per-tier, falls back to `_rank_in_memory`, and pushes a message onto
the `warnings` state channel, which the interpreter appends to its reply as "Caveats from this run".

**Kuzu takes an exclusive file lock.** A running backend holds it, so read `data/lake/graph` by
copying it aside (see `slides/run_model.py`). Tiers 2 and 3 are a plain SQLite file and Parquet, and
can be read in place.

### Deliberate deviations

`DESIGNAGENT_WRAP_NODES` ships **false**, against the original design, which had every node wrapped as
a flowgentic `EXECUTION_BLOCK`. That runs the node body on asyncflow's loop, outside LangGraph's
runnable context, so `get_stream_writer()` raises and every status line and token is dropped
*silently* — the graph still completes. Tasks are still wrapped (`FUNCTION_TASK`), which is where the
parallelism matters. Do not flip this default without re-verifying that status lines still stream.

The visualization agent emits a **sanitized JSON view spec**, not generated JavaScript, even though
the brief said "codes a browser-based visualization". Shipping LLM-written JS into the viewer would
let a prompt get code into the page. `tools/molviz_agent.py::sanitize_spec` repairs or drops every
field; a bad generation degrades to a plain cartoon.

With no HPC endpoint attached, `proteinmpnn` falls back to a heuristic proposer that labels its own
output `"note": "heuristic proposals, not ProteinMPNN samples"`. Keep that label honest.

### Working around the middleware

Three flowgentic behaviours are worked around at the call site, each commented there:
`ConcurrentExecutionBackend` lives in `rhapsody.backends` (not `radical.asyncflow`, as its examples
claim); `_default_retryable_exceptions()` does `except Exception: raise` around optional
`httpx`/`aiohttp` imports, making both hard requirements; and `RetryConfig` defaults to a 30 s timeout
with 3 attempts, which would cancel and silently re-run a fold, so tasks pass
`timeout_sec=None, max_attempts=1`.

Orbit's clients are all synchronous (wrapped in `asyncio.to_thread`) and its push callbacks arrive on
a listener thread (hopped with `call_soon_threadsafe`). Its terminal task event omits stdout, so
`_finish_task_enriched` re-fetches; a FAILED job carries only an exit code, so the reason is
synthesized. `tasks/hpc/local_orbit.py` stands up a real broker + endpoint for tests — note that
`--no-auth` disables ingress auth only, and the broker still requires a cert and key.

## Backlog

Open issues and their evidence live in `plans/BACKLOG.md`. Add to it when you find something worth
fixing later rather than leaving it in a commit message; each entry names the file and how to
reproduce.

## Slides

`slides/` holds a code-walk deck built from real data. If you change backend code that a slide cites:

```bash
.venv/bin/python slides/check_anchors.py        # 31 cited line numbers, re-derived
.venv/bin/python slides/run_model.py            # regenerate run.json from data/lake
.venv/bin/python slides/make_script.py          # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
```

`DECK_SCRIPT.md` is generated — edit the `addNotes` blocks in `build_deck.js`, not the Markdown.
