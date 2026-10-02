# Backlog

Open issues, with the evidence for each. An entry earns its place by naming where the problem is and
how to see it — "needs refactoring" is not an entry. When something is fixed, delete the entry rather
than marking it done; git history is the record.

Convention: **A** = correctness or honesty of a claim · **B** = developer experience ·
**C** = upstream, in the middleware rather than here · **D** = known-unknown, needs investigation
before it can be sized.

Status as of `a9ce74b` (2026-10-01).

---

## A — correctness and honesty

### A1 · No HPC endpoint has ever executed a task
The remote path is proven against a localhost broker only (`tests/test_orbit_local.py`, 6 tests).
That exercises the client — websocket, plugin sessions, push events, PSI/J offset tailing,
cancellation — and says nothing about a scheduler, a queue, staging, or an allocation.

**Next step:** one real submission to a real endpoint, even a trivial one. Everything claimed about
`hpc` upgrades from "the client works" to "the path works" the moment that lands, and several entries
below collapse into it.

### A2 · The Globus adapter has never met a live endpoint
`tasks/hpc/globus.py` implements the ABC and is tested with an injected executor
(`tests/test_tasks.py::test_globus_interface_runs_with_injected_executor`), which proves the shape
and the argv handling, not the integration. Capability flags are set from the documented behaviour of
Globus Compute, not from observation.

**Risk if ignored:** the flags may be wrong in a direction that matters —
`supports_cancel=False` is conservative, but `supports_staging=False` may be too pessimistic.

### A3 · `proteinmpnn` is a heuristic proposer unless an endpoint is attached
With no `hpc` interface, `interface_for` falls back to `propose_variants`, which produces
single-point substitutions by rule. It labels itself
(`"note": "heuristic proposals, not ProteinMPNN samples"`), and the summary carries the label
through. **Keep that honest** — the whole reference campaign in `slides/run.json` is heuristic
output, and a reader who misses the label will over-read a 0.13 pLDDT difference.

### A4 · The Mol* canvas has never been looked at by a human
Verified: the dev server, the `/api` proxy, a real artifact fetched through it, and that the
rcsb-molstar CDN assets return 200 with CORS. Not verified: that the viewer draws. No browser was
available in the session that built it.

**Next step:** open `http://localhost:5173`, ask for a redesign, confirm the pane renders and the
highlighted residue is the mutated one.

### A5 · `wrap_nodes` ships off, against the original design
See `config.py:37` and the long comment in `graph/build.py`. Blocked on **C2** — if
`EXECUTION_BLOCK` is supposed to preserve the caller's context, this becomes a one-line default flip
plus a re-verification that status lines still stream. If it is not, the flag should probably be
deleted rather than left as a trap.

---

## B — developer experience

### B1 · A fresh clone cannot be built
`refcodes/` is gitignored and is where `radical.asyncflow`, `rhapsody` and `flowgentic` come from, so
the install sequence in the README cannot run against a clone. There is no vendoring, submodule or
pinned-revision strategy — the working tree is the only record of which revision of each was used.

**Why it matters:** three of this project's findings (C1, C2, C3) are claims about specific revisions
of those packages, and right now nothing records which.

**Options:** git submodules pinned to a SHA; or a `refcodes/VERSIONS.md` recording each package's
commit; or vendoring the three into the repo. The middle one is cheap and would do.

### B2 · `uv sync` silently produces a broken environment
`radical-asyncflow`, `rhapsody-py` and `flowgentic` appear in `[tool.uv.sources]` but **not** in
`[project] dependencies`, so `uv sync` resolves and installs without them and the failure only shows
up at `import flowgentic`. The deck shipped a slide with `uv sync --extra dev` on it for exactly this
reason; corrected, but the trap remains for the next person.

**Fix:** either add the three to `dependencies` so the sources entries bind, or drop
`[tool.uv.sources]` entirely so nothing implies `uv sync` is the supported path.

### B3 · A bare `pytest -q` is not offline
`pytest -q` collects and runs all 89 tests, including the 6 `live` Orbit tests, which start a real
broker and endpoint as subprocesses (89 s, versus 64 s for `-m "not live"`). They self-skip only when
the Orbit CLI scripts cannot be found — which is false in any working tree. The README claimed the
bare command was offline.

**Fix:** `addopts = -m "not live"` in `[tool.pytest.ini_options]`, so the default is the fast offline
path and the live tier is opt-in by `-m live`.

### B4 · The initializer re-runs its lookups within a single campaign
Measured in the reference campaign: the full lookup set ran at `19:17:53` and again at `19:18:58`,
both under campaign `s-tutp87hw` — `pdb_lookup`, `uniprot_lookup`, `pdb_structure`,
`literature_lookup`, plus the cross-reference follow-up. That is 5 redundant live API calls and about
6 seconds on the second prompt of an already-initialized session.

**Reproduce:** `MATCH (t:Task)-[:RAN_FOR]->(c:Campaign) RETURN t.name, t.submitted_at, c.id ORDER BY
t.submitted_at` against `data/lake/graph`.

**Unclear:** the classifier should route a second prompt to `design`, not `initialize`, once
`reference_design.sequence` is set (`coordinator.py:122`). Why it did not is **D1**. Fixing the
routing probably fixes this; a lookup cache in `tools/` would fix it regardless and is worth having
anyway, since RCSB and UniProt answers are stable.

### B5 · `asyncflow.session.*` directories accumulate in the repo root
Eight of them at the time of writing. Gitignored, so harmless to the repo, but they make `ls` useless
and they are never cleaned up. They come from `WorkflowEngine` and are created per run.

**Fix:** point asyncflow at `data/flow/` the way `config.yml` already points its other outputs, if
the engine supports it; otherwise clean them in `Runtime.aclose()`.

### B6 · Nothing prunes `data/`
Blobs are content-addressed, so duplicates are free, but nothing ever removes them — the reference
campaign alone is 5.1 MB across 24 files. Checkpoints, artifacts and the Kuzu WAL grow the same way.
Fine for development, wrong for anything long-lived.

### B7 · Tier 1 cannot be read while the server is running
Kuzu takes an exclusive file lock. Any out-of-process reader has to copy the database aside first
(`slides/run_model.py` does). Tiers 2 and 3 are a plain SQLite file and Parquet and read fine in
place.

**Not necessarily a bug** — it is how embedded Kuzu works — but it means no read-only analytics, no
dashboard and no second process can touch provenance while the app is up. If that becomes a
requirement, tier 1 needs either a read replica or a different store.

---

## C — upstream

These were found while building on the middleware and are the asks on slide 18 of the code walk.
`slides/CODE_FOR_DECK.md` carries the verbatim code and the reproduction for each.

### C1 · flowgentic hard-imports `aiohttp` and `httpx`
`fault_tolerance.py:71–72` and `:83–84` do `except Exception: raise` around imports the comment
describes as optional ("Try to include aiohttp timeouts **if present**"). Both become hard
requirements. Fires whenever `RetryConfig.retryable_exceptions` is left at its default, which is `()`
— the common case.

**Worked around** by installing `aiohttp` (see the comment in `pyproject.toml`) and passing
`retryable_exceptions` explicitly. Looks like a one-word fix upstream: `raise` → `pass`.

### C2 · `EXECUTION_BLOCK` loses the caller's runnable context
A node wrapped as one runs on asyncflow's loop, so LangGraph's `get_stream_writer()` raises
`Called get_config outside of a runnable context` and every custom event is dropped **silently** —
the graph completes and the chat goes quiet. Isolated both ways: a plain node streams its status
lines, a wrapped one streams none.

**The open question:** is `EXECUTION_BLOCK` *meant* to propagate context? If yes, bug. If no,
flowgentic's node wrapping and LangGraph's streaming are mutually exclusive and that belongs in the
README. Blocks **A5**.

### C3 · `RetryConfig` defaults cancel long work silently
`timeout_sec=30.0`, `max_attempts=3` (`fault_tolerance.py:23–37`). Measured against this repo's own
workload, folds ran 13.3–42.4 s, so the default would have cancelled and re-run roughly half of one
round. Reasonable for a service call, wrong for the agent tasks flowgentic exists to wrap.

### C4 · Orbit's terminal task event omits stdout
A completed rhapsody task resolves with an empty result until `get_task` is called a second time.
Worked around in `orbit.py:290` `_finish_task_enriched`. This and C5 both produce a *silent wrong
answer* rather than an error, which is what makes them the two worth fixing first.

### C5 · A FAILED Orbit job carries no reason
Only a non-zero exit code reaches the client. `orbit.py:353–357` synthesises an explanation from the
exit code, then stderr, then the log tail, so the user sees something — but the real reason never
left the endpoint.

### C6 · Broker documentation gaps
Two afternoon-sized traps: there is no HTTP topology route (`/topology` is read as a plugin name and
404s after a 307, so readiness must come from the client's `rt.topology()`, which propagates
asynchronously); and `--no-auth` disables *ingress auth only* — the broker still serves TLS and
refuses to start without a cert and key. Both are documentation fixes, not code.

---

## D — needs investigation

### D1 · Why did the initializer run twice in one campaign?
`classify_rules` (`coordinator.py:114–126`) should return `design` rather than `initialize` once
`reference_design.sequence` is set, which would route the second prompt to the orchestrator. The
reference campaign shows it running the full initializer twice anyway (see **B4**).

**Hypotheses, untested:** the second prompt named `1OIL` again and something upstream of the
`has_reference` check won; or the turn arrived on a different `session_id` and loaded an empty
checkpoint while reusing the campaign; or the orchestrator itself re-requested the reference.

**How to settle it:** run two prompts in one session with the backend's log at DEBUG and watch the
`intent` the coordinator emits on the second. Cheap, and it either closes B4 or redirects it.

### D2 · ESM Atlas drops requests under concurrent load
One of six round-2 folds came back with no structure (`s-tutp87hw-r2-5`). The campaign absorbed it
correctly — sequence-only scoring, a warning to the user — so this is not a bug report against us.
But the fold path has **no retry**, and a 1-in-6 drop rate at a fan-out of 6 is high enough that it
is probably load-related rather than random.

**Worth knowing:** whether it is rate limiting (then back off and retry), a size limit interacting
with the 400 aa threshold, or genuinely random. One scripted run of 20 folds at varying concurrency
would answer it.

---

## Maintenance

### M1 · The deck cites 31 source line numbers
Any backend refactor can drift them. `slides/check_anchors.py` re-derives every one and reports where
a moved line actually is; run it before presenting and after any significant edit. `run.json` and
`DECK_SCRIPT.md` are generated — see `CLAUDE.md`.
