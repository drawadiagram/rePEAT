# Protein Design Agent

A chatbot for protein redesign: a LangGraph agent loop with a web chat interface
and an artifact pane that renders session summaries and interactive molecular
visualizations.

```
┌───────────────── frontend (Vite/React) ─────────────────┐
│  chat pane  ◄── SSE ──┐            │  artifact pane      │
│  (prompts, status,    │            │  Mol* · Markdown    │
│   task chips)         │            │  .docx · tables     │
└───────────────────────┼────────────┴─────────────────────┘
                        │ /api/chat, /api/artifacts
┌───────────────────────┴───────────── backend (FastAPI) ──┐
│  LangGraph loop                                          │
│    Coordinator → Initializer → Orchestrator              │
│                      ↑              ↓                    │
│                 Interpreter ←   Analyst                  │
│                                                          │
│  Task Interfaces          Design History (tiered lake)   │
│   local  → flowgentic/asyncflow → rhapsody process pool  │
│   query  → REST APIs (RCSB, UniProt, Europe PMC)         │
│   hpc    → RADICAL Orbit  (or Globus Compute)            │
└──────────────────────────────────────────────────────────┘
```

## Quick start

`refcodes/` holds `radical.asyncflow`, `rhapsody` and `flowgentic` as local
editable installs. **It is gitignored, so a fresh clone cannot build** — see
`plans/BACKLOG.md` B1. They are listed in `[tool.uv.sources]` but not in
`dependencies`, so `uv sync` alone silently leaves them out (B2); use the
sequence below.

```bash
# backend
uv venv --python 3.12 .venv
uv pip install -e refcodes/radical.asyncflow -e refcodes/rhapsody
uv pip install --no-deps -e refcodes/flowgentic
uv pip install -e '.[dev]'
cp .env.example .env            # optional: add ANTHROPIC_API_KEY
.venv/bin/python -m designagent # http://127.0.0.1:8000

# frontend (separate shell)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Then ask for something, e.g. *"redesign 1UBQ to improve thermostability"*.

**It runs without an API key.** Every node has a deterministic rule-based path,
so the loop works end to end with no LLM; the key only improves intent
classification, planning and the written summary. `/api/health` reports which
mode you are in.

## The agent loop

| Node | Does | Writes to state |
| --- | --- | --- |
| **Coordinator** | Classifies the prompt, streams updates, answers from state | `intent`, `goal` |
| **Design Initializer** | PDB + UniProt + literature lookups, concurrently | `reference_design` |
| **Redesign Orchestrator** | Picks the success metric, builds and dispatches the worklist | `key_metric`, `worklist` |
| **Analyst** | Scores returns, writes the lake, ranks, rebuilds the view | `lead_design`, `ensemble`, `molecular_visualization` |
| **Interpreter** | Summarizes, flags related past designs, renders artifacts | `design_summary`, `artifacts` |

Routing is dynamic (`Command(goto=...)`): chat ends immediately, a design
request loops Orchestrator → Analyst until the key metric stops improving or
`max_rounds` is reached, then summarizes.

## Task Interfaces

Task duration is indeterminate, so **every** interface submits asynchronously and
returns a handle carrying a future. Nothing in the graph blocks on submission.

| Interface | Backing | Cancel | Log stream | Push events |
| --- | --- | --- | --- | --- |
| `local` | flowgentic → asyncflow → rhapsody `ConcurrentExecutionBackend` (process pool) | yes | no | no |
| `query` | `httpx` on the main loop (REST retrieval) | yes | no | no |
| `hpc` (Orbit) | `EndpointRuntime` + rhapsody & PSI/J plugins | yes | yes (PSI/J, by byte offset) | yes |
| `hpc` (Globus) | `globus_compute_sdk.Executor` | only before start | no | no |

Capabilities are declared rather than assumed (`tasks/base.py: Capabilities`),
because the two HPC backends genuinely differ. With no HPC endpoint configured,
`hpc` tasks fall back to their app-local equivalents.

### Remote HPC

Orbit is implemented first. For development, `tasks/hpc/local_orbit.py` brings up
a localhost `--no-auth` broker plus an endpoint (rhapsody on `concurrent`, PSI/J
on `local`), so the real client path is exercised with no allocation:

```bash
DESIGNAGENT_ORBIT_ENABLED=true RADICAL_ORBIT_BROKER_URL=http://127.0.0.1:8000 ...
```

The Globus adapter implements the same ABC and takes an injectable
`executor_factory`, so it is testable offline.

## Design History (tiered data lake)

| Tier | Store | Holds |
| --- | --- | --- |
| 1 | Kuzu graph (embedded) | raw task outputs and provenance: `Design`, `Task`, `Output`, `Structure`, `DERIVED_FROM`, `PRODUCED` |
| 2 | SQLite | scores, per-round rankings, analyses |
| 3 | Parquet + manifest | curated golden sets staged for ML training |

Bulky payloads (coordinates, FASTA) go to a content-addressed blob directory and
are referenced by path from tier 1. A lake write failure is non-fatal: the round
survives in state, ranking falls back to in-memory, and the user is told in the
summary's caveats.

## Configuration

Everything is an env var (prefix `DESIGNAGENT_`, see `.env.example`). The ones
that change behaviour most:

- `ANTHROPIC_API_KEY` — enables LLM reasoning
- `DESIGNAGENT_FOLD_BACKEND` — `esmatlas` (public API, ≤400 aa), `local`, or `hpc`
- `DESIGNAGENT_POOL_WORKERS` — process-pool size
- `DESIGNAGENT_MAX_ROUNDS` — redesign rounds before summarizing
- `DESIGNAGENT_ORBIT_ENABLED` + `RADICAL_ORBIT_*` — remote HPC
- `DESIGNAGENT_WRAP_NODES` — see the note below

### Optional extras

```bash
uv pip install -e '.[chem]'   # ChemGraph: cheminformatics / quantum chemistry
```

ChemGraph pins `langgraph`/`langchain`/`pydantic` exactly and pulls torch, so
install it deliberately and re-check the resolved set.

## Notes on the dependencies

Three things in the reference stack needed working around, all commented at the
call site:

1. **flowgentic's examples import `ConcurrentExecutionBackend` from
   `radical.asyncflow`**, where it no longer exists. It lives in
   `rhapsody.backends` now.
2. **flowgentic's `_default_retryable_exceptions()` does `except Exception:
   raise`** around its optional `httpx`/`aiohttp` imports, making both hard
   requirements. We install aiohttp *and* pass `retryable_exceptions` explicitly.
3. **flowgentic wraps everything with a 30 s timeout and 3 retries by default.**
   Fatal for a node awaiting an HPC job, so tasks are wrapped with
   `timeout_sec=None, max_attempts=1`.

**Why nodes are not flowgentic `EXECUTION_BLOCK`s by default:** that runs the
node body on asyncflow's own event loop, outside LangGraph's runnable context,
so `get_stream_writer()` raises and every status update and token is silently
dropped. Tasks still go through flowgentic to the process pool, which is where
the parallelism matters. Set `DESIGNAGENT_WRAP_NODES=true` to opt back in and
lose custom-event streaming.

## Tests

```bash
.venv/bin/python -m pytest -q -m "not live"   # 83 tests, offline; stubs replace every tool
.venv/bin/python -m pytest -q                 # all 89 — the live tier starts a real broker
```

The offline tier covers the lake tiers, task routing and capabilities,
shell-injection safety on the Globus path, the classifier, the full design loop,
artifact rendering, the SSE framing contract, and resilience when storage or a
fold service fails. It needs no network, no process pool and no endpoint, because
nodes reach the outside only through `Deps`.

The 6 tests marked `live` bring up a localhost Orbit broker and endpoint as
subprocesses and exercise the real client path — push states, incremental log
tailing by byte offset, a failing job, cancelling a running one. **They are not
excluded by default**, so a bare `pytest -q` runs them (~25 s longer); they
self-skip only when the Orbit CLI scripts cannot be found.

```bash
.venv/bin/python -m pytest tests/test_orbit_local.py -q
```

## Slides

`slides/` holds a code-walk deck for a technical audience — architecture, the
Task Interface seam, and the findings against the middleware. Its figures are
drawn from a real campaign rather than a mock-up: `run_model.py` mines
`data/lake` into `run.json`, which the builder reads.

```bash
.venv/bin/python slides/check_anchors.py   # re-derive the 31 cited line numbers
.venv/bin/python slides/run_model.py       # regenerate run.json from data/lake
.venv/bin/python slides/make_script.py     # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
```

Run `check_anchors.py` after changing backend code: a slide that cites
`tasks/base.py:135` while showing something else is worse than one with no
citation.

## Backlog

Open issues live in [`plans/BACKLOG.md`](plans/BACKLOG.md), each with the
evidence and how to reproduce it. The ones that most change how you should read
this README: no HPC endpoint has ever executed a task for this agent (Orbit is
proven against localhost only), the Globus adapter has never met a live
endpoint, and `proteinmpnn` falls back to a labelled heuristic proposer when no
endpoint is attached.
