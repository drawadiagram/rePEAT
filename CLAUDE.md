# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup

```bash
./scripts/setup.sh           # build .venv and verify it
./scripts/setup.sh --check   # verify an existing one, install nothing
```

That script is the only supported path and **`uv sync` is not an alternative**. The three middleware
packages are local editable installs from `refcodes/`, which is gitignored and absent from a fresh
clone; flowgentic pins `radical-asyncflow` and `academy-py` to git URLs that fight the local
checkouts, so it goes in `--no-deps` and the three deps it then lacks are unused here. `pyproject.toml`
carries the full reasoning where a `[tool.uv.sources]` block used to be.

Run `--check` after a pull: it fails with the missing piece named, rather than letting the app die
deep inside an import.

`config.yml` in the **current working directory** is read at `import flowgentic` time. Both
`agent_execution` and `logger` keys must be present — flowgentic does `APP_SETTINGS["logger"]["level"]`
with no fallback once it finds a file. Run everything from the repo root.

## Commands

```bash
.venv/bin/python -m designagent --reload        # backend on :8000
cd frontend && npm run dev                      # Vite on :5173, proxies /api
cd frontend && npm run build                    # tsc -b && vite build

.venv/bin/python -m pytest -q                   # 93 offline tests, no network
.venv/bin/python -m pytest -q -m live           # 6 live tests; starts a real broker
.venv/bin/python -m pytest tests/test_graph.py::test_design_loop_produces_lead_ensemble_and_artifacts -q
.venv/bin/ruff check backend tests              # E/F/I, line-length 100
```

`ruff` is in the `dev` extra but was absent from the working venv for a while, so the lint command
had never been run: it currently reports **34 findings** (16 unused imports, 8 long lines, 7 unsorted
import blocks, 3 ambiguous names). All trivial, none fixed yet, because fixing them shifts line
numbers that the deck's 31 anchors cite. See `plans/BACKLOG.md`.

`addopts = "-m 'not live'"` in `pyproject.toml` deselects the 6 live tests by default, because they
start a real broker. `-m live` on the command line overrides it; naming the file alone does not, and
collects nothing.

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
check, and it is the only reason 93 tests run with no network, no process pool and no endpoint — the
suite hands nodes an in-process `TaskManager` and a `tmp_path` lake.

**Every interface's `submit()` returns immediately with a handle whose `future` resolves later.**
Nothing in the graph awaits at submission time. A *failed submission* becomes a settled FAILED handle
rather than an exception (`tasks/manager.py`), so callers have exactly one shape to handle and
`gather()` never raises.

**State must not carry bulky payloads.** LangGraph serializes a checkpoint every turn, so coordinates
go to a content-addressed blob (`history.write_blob`) and travel as `structure_path`. Pinned by
`tests/test_graph.py::test_structures_are_not_carried_in_state`, which asserts `"ATOM  "` never
appears in serialized state. The regression is invisible — nothing breaks, it just grows every turn.

**A turn's graph input carries only what that turn contributes** — the user message, `session_id`,
and the two turn-scoped resets (`pending_results`, `status`). Everything else comes from the
checkpoint. Most of `DesignState` is reduced with `replace` (`state.py`), so any key present in the
input **overwrites** the saved value: passing a fresh `new_state()` wipes the reference design,
metric, lead, ensemble and view, and commits the blank. That was a real bug — the second prompt of
every session answered "No reference design is loaded yet" and re-ran the initializer. Pinned by
`tests/test_graph.py::test_state_survives_into_the_next_turn`, and `_send` in that file deliberately
mirrors `app.py` rather than threading state by hand, because threading it is what hid the bug.

**Routing is `Command(goto=..., update=...)` from the node body**, so the routing decision and the
state write are one atomic return. `build.py` declares `destinations` per node for validation; the
only static edge is `START → coordinator`.

**A view request's own words reach the visualization generator**, not the campaign goal: the
coordinator writes `goal` only for `design`/`initialize`, so on a "label the active site" turn `goal`
is still the design objective. `_make_visualization` falls back to `last_user_text(state)`
(`graph/state.py`) for this reason.

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
