# Backlog

Open issues, with the evidence for each. An entry earns its place by naming where the problem is and
how to see it — "needs refactoring" is not an entry. When something is fixed, delete the entry rather
than marking it done; git history is the record.

Convention: **A** = correctness or honesty of a claim · **B** = developer experience ·
**C** = upstream, in the middleware rather than here · **D** = known-unknown, needs investigation
before it can be sized.

Status as of 2026-10-01. Numbering is not stable across commits — entries are deleted
when fixed and the rest close up, so refer to issues by their title in commit messages.

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

---

## B — developer experience

### B1 · A fresh clone cannot be built
`refcodes/` is gitignored and is where `radical.asyncflow`, `rhapsody` and `flowgentic` come from, so
`scripts/setup.sh` cannot run against a clone — it detects this and says so, which is the most it can
do. There is no vendoring, submodule or pinned-revision strategy, and the working tree is the only
record of which revision of each was used.

**Why it matters:** three of this project's findings (C1, C2, C3) are claims about specific revisions
of those packages, and right now nothing records which.

**Options:** git submodules pinned to a SHA; or a `refcodes/VERSIONS.md` recording each package's
commit; or vendoring the three into the repo. The middle one is cheap and would do.

### B2 · The lint command had never been run
`ruff` is declared in the `dev` extra but was missing from the working venv, so `ruff check` had
never been run against this code. With the rule set now pinned to `E,F,I` it reports **34 findings**:
16 unused imports (F401), 8 lines over 100 (E501), 7 unsorted import blocks (I001), 3 ambiguous
variable names (E741, all `l`). Every one is trivial and 23 are auto-fixable.

Not fixed yet for a specific reason: removing an import or wrapping a line shifts every line below
it, and the deck cites **31 source line numbers** across 14 of these files. The cleanup and the
anchor update have to land together.

**How to do it:** `ruff check backend tests --fix`, hand-fix the 11 remaining E501/E741, then
`pytest -q`, then `slides/check_anchors.py` and correct every anchor it reports moved — in both
`slides/CODE_FOR_DECK.md` and the `anchor:` labels in `slides/build_deck.js`.

**Also worth deciding:** whether `E,F,I` is the right baseline. `E,F,I,B,UP` reports 67, and the
extra 31 are mostly `B` (blind `except Exception:`) which this codebase uses deliberately in the
degradation paths — so that set would need per-site `noqa`, which is probably not worth it.

### B3 · The classifier's keyword lists miss `-ing` forms
`_has_word` (`coordinator.py`) matches a leading word boundary only, so each entry acts as a prefix.
That works for `fold` → "folding" but not for any verb whose gerund drops the final `e`:
`optimize` misses "optimizing", `generate` misses "generating", `iterate` misses "iterating". So
"keep iterating" and "optimizing solubility" classify as **chat** and silently do nothing.

Affects the no-LLM path only. Noted in a comment at the list, which is the honest minimum, but not
fixed: stripping a trailing `e` before matching would fix the class at the cost of real false
positives (`create` → `creat` fires on "creature"). A stem list, or two entries for the verbs that
matter, is the safe version.

### B4 · `asyncflow.session.*` directories accumulate in the repo root
Eight of them at the time of writing. Gitignored, so harmless to the repo, but they make `ls` useless
and they are never cleaned up. They come from `WorkflowEngine` and are created per run.

**Fix:** point asyncflow at `data/flow/` the way `config.yml` already points its other outputs, if
the engine supports it; otherwise clean them in `Runtime.aclose()`.

### B5 · Nothing prunes `data/`
Blobs are content-addressed, so duplicates are free, but nothing ever removes them — the reference
campaign alone is 5.1 MB across 24 files. Checkpoints, artifacts and the Kuzu WAL grow the same way.
Fine for development, wrong for anything long-lived.

### B6 · Tier 1 cannot be read while the server is running
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
README. Until it is answered, `DESIGNAGENT_WRAP_NODES` has to ship `false` against the approved
design (`config.py:37–40`), which is the deviation slide 17 of the code walk leads with.

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

### D1 · The LLM classifier path is unmeasured
Everything known about routing comes from `classify_rules`: the reported session, the whole reference
campaign and all 93 tests run with `llm: false`. When a key is present, `CLASSIFY_SYSTEM`
(`coordinator.py`) decides instead and the rules are never consulted — so the phrasings just fixed in
the rule path ("label the active site residues", "run another round") are unverified there.

**How to settle it:** with `ANTHROPIC_API_KEY` set, send the same three turns used to verify the
state fix and compare the `intent` on each against the rule path's answer. Any divergence is a prompt
fix in `CLASSIFY_SYSTEM`, not a code change.

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
