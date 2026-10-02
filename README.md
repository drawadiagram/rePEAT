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

```bash
./scripts/setup.sh              # builds .venv and verifies it
cp .env.example .env            # optional: add ANTHROPIC_API_KEY
.venv/bin/python -m designagent # http://127.0.0.1:8000

# frontend (separate shell)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

`scripts/setup.sh` is the only supported install path, and `--check` verifies an
existing environment without installing anything. **`uv sync` cannot work here**:
`radical.asyncflow`, `rhapsody` and `flowgentic` are local editable installs from
`refcodes/`, and flowgentic pins two of its own dependencies to git URLs that
fight the local checkouts, so it is installed `--no-deps`. `refcodes/` is
gitignored, so a fresh clone cannot build at all — see `plans/BACKLOG.md` B1.

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

Orbit is implemented first. Against a real broker:

```bash
DESIGNAGENT_ORBIT_ENABLED=true \
RADICAL_ORBIT_BROKER_URL=https://broker.site:8443 \
RADICAL_ORBIT_BROKER_CERT=/path/broker_cert.pem \
RADICAL_ORBIT_BROKER_TOKEN=... \
DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm DESIGNAGENT_ORBIT_ACCOUNT=... \
.venv/bin/python -m designagent
```

For development, `DESIGNAGENT_ORBIT_LOCAL=true` has the server bring up a
localhost broker plus an endpoint itself (`tasks/hpc/local_orbit.py`: rhapsody on
`concurrent`, PSI/J on `local`), so the real client path is exercised with no
allocation. That broker runs `--no-auth`, which disables *ingress auth only* — it
still serves TLS and will not start without a cert and key, which the stack
generates.

The HPC path carries a real job spec: `_job_params` (`graph/nodes/orchestrator.py`)
fills in the executable from the tool module and the allocation from settings, and
`to_psij_spec` renames our resource vocabulary to PSI/J's. Without that, a
submission is `/bin/true` — or, with the wrong field names, an HTTP 500.

The Globus adapter implements the same ABC and takes an injectable
`executor_factory`, so it is testable offline. It has never met a live endpoint,
and `globus-compute-sdk` is not installed.

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

Settings come from env vars (prefix `DESIGNAGENT_`, see `.env.example`), and can
also be supplied **to a running server** — from the Settings panel in the UI, or
`PUT /api/settings`. Those are held in memory and are not written anywhere, so a
restart returns to `.env`; `GET /api/settings` says which source each value came
from. Precedence is: values set in the running app, then the environment, then
`.env` (resolved against the CWD), then the defaults in `config.py`.

```bash
python -m designagent --check-config            # what is in effect, secrets masked
python -m designagent --check-config --probe    # ...and does each credential work
```

The ones that change behaviour most:

- `ANTHROPIC_API_KEY` — enables LLM reasoning
- `DESIGNAGENT_FOLD_BACKEND` — `esmatlas` (public API, ≤400 aa), `local`, or `hpc`
- `DESIGNAGENT_POOL_WORKERS` — process-pool size
- `DESIGNAGENT_MAX_ROUNDS` — redesign rounds before summarizing
- `DESIGNAGENT_ORBIT_ENABLED` + `RADICAL_ORBIT_*` — remote HPC, with
  `DESIGNAGENT_ORBIT_PSIJ_EXECUTOR` / `_ACCOUNT` / `_QUEUE` for the site
- `DESIGNAGENT_ORBIT_LOCAL` — start a localhost broker + endpoint at startup
- `DESIGNAGENT_ADMIN_TOKEN` — required to change settings over HTTP when the
  server is not bound to loopback
- `DESIGNAGENT_WRAP_NODES` — see the note below

A credential is never rendered in full: responses carry presence, source and a
masked hint, and a log filter scrubs live secret values from records this code
did not write. Nothing refuses to start — a missing or rejected credential shows
up in `/api/health`, is badged in the UI, and makes the turn say in its reply
that it fell back.

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

## Observability

A turn says how it was made. Each node that authors text declares which function wrote it, so a reply
carries its author rather than just its content:

```
turn s-7fa2: coordinator(0ms) → initializer(2871ms) → orchestrator(967ms) → analyst(540ms)
             → orchestrator(932ms) → analyst(309ms) → interpreter(177ms)
             = 5796ms · reply by interpreter:_rule_based_summary
```

The same record reaches the browser on the `state` frames and is kept with the message, so the
collapsible **how this answer was made** under a reply still works after the next turn and after a
reload. `message` and `token` frames carry the node that produced them; `GET /api/sessions/{id}`
returns `reply_source` and `trace` for the last turn. `reply_source` values are grep-able —
`interpreter:_rule_based_summary` is a function name — and `CLAUDE.md` tabulates which function writes
which sentence.

The trace is counters and node names only, never payloads, and is reset every turn so the checkpoint
holds one turn's worth. Tasks record the node that submitted them, and a finished task's `elapsed`
is its duration rather than its age.

## Tests

```bash
.venv/bin/python -m pytest -q            # 124 tests, offline; stubs replace every tool
.venv/bin/python -m pytest -q -m live    # 6 tests; starts a real Orbit broker + endpoint
.venv/bin/python -m pytest -q -m remote  # 5 tests; submits to a real HPC endpoint
.venv/bin/python -m pytest -q -m llm     # 4 tests; calls Anthropic with a real key
```

The offline tier covers the lake tiers, task routing and capabilities,
shell-injection safety on the Globus path, the classifier, the full design loop,
artifact rendering, the SSE framing contract, and resilience when storage or a
fold service fails. It needs no network, no process pool and no endpoint, because
nodes reach the outside only through `Deps`.

The 6 tests marked `live` bring up a localhost Orbit broker and endpoint as
subprocesses and exercise the real client path — push states, incremental log
tailing by byte offset, a failing job, cancelling a running one.

`remote` and `llm` are the two validation tiers, each excluded separately so that
`-m live` cannot drag in one that needs an allocation or spends money. `remote`
reads its broker and scheduler from the environment and submits trivial jobs, so
the same assertions rehearse against `DESIGNAGENT_ORBIT_LOCAL=true` before a real
endpoint; `llm` replays the classifier table through the LLM path and reports
where it disagrees with the rules, and checks that the session summary really came
from the model. All three tiers are deselected by default (`addopts` in
`pyproject.toml`), and a marker is the only thing that selects them — naming the
file alone collects nothing.

## Slides

`slides/` holds a code-walk deck for a technical audience — architecture, the
Task Interface seam, and the findings against the middleware. Its figures are
drawn from a real campaign rather than a mock-up: `run_model.py` mines
`data/lake` into `run.json`, which the builder reads.

```bash
.venv/bin/python slides/check_anchors.py   # re-derive the 39 cited line numbers
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
endpoint is attached. The Mol* artifact pane was confirmed rendering in a
browser on 2026-10-01.
