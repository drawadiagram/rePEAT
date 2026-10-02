# Backlog

Open issues, with the evidence for each. An entry earns its place by naming where the problem is and
how to see it — "needs refactoring" is not an entry. When something is fixed, delete the entry rather
than marking it done; git history is the record.

Convention: **A** = correctness or honesty of a claim · **B** = developer experience ·
**C** = upstream, in the middleware rather than here · **D** = known-unknown, needs investigation
before it can be sized.

Status as of 2026-10-02. Numbering is not stable across commits — entries are deleted
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

**Unblocked.** `tests/test_hpc_remote.py` (`-m remote`) is that submission: it reads the broker,
cert, token, endpoint and scheduler from settings, so pointing it at a real endpoint is configuration
rather than code. All 5 pass against `DESIGNAGENT_ORBIT_LOCAL=true` as a rehearsal. Two things the
rehearsal already found and fixed, both of which would have burned a queue slot to discover:
PSI/J names its resource fields differently from us and answers **HTTP 500** rather than ignoring an
unexpected one (`PSIJ_RESOURCE_KEYS` in `orbit.py` now maps them), and the manager's 900 s
`task_timeout_sec` is shorter than a job's walltime, so a queued job was failed before the scheduler
started it (`_batch_timeout`).

### A2 · The Globus adapter has never met a live endpoint
`tasks/hpc/globus.py` implements the ABC and is tested with an injected executor
(`tests/test_tasks.py::test_globus_interface_runs_with_injected_executor`), which proves the shape
and the argv handling, not the integration. Capability flags are set from the documented behaviour of
Globus Compute, not from observation.

**Risk if ignored:** the flags may be wrong in a direction that matters —
`supports_cancel=False` is conservative, but `supports_staging=False` may be too pessimistic.

Now reachable from configuration (`DESIGNAGENT_GLOBUS_ENABLED` + `_ENDPOINT_ID`, constructed in
`runtime._make_globus`), but still unproven, and `globus-compute-sdk` is **not installed** — so
enabling it reports why it could not start rather than connecting. Credentials come from the SDK's own
login, not from our settings; if that turns out to need plumbing, it is a new entry.

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

### B2 · Is `E,F,I` the right lint baseline?
`ruff check backend tests` is clean at `E,F,I` and that is what `pyproject.toml` pins. The open
question is whether to widen it. Measured now that the baseline is clean, `E,F,I,B,UP` reports **29**:

| Rule | n | What it is |
|---|---|---|
| UP035 | 11 | `typing.Iterable`/`Dict` etc. — deprecated, belongs in `collections.abc` |
| B905 | 8 | `zip()` without `strict=` |
| UP017 | 5 | `datetime.timezone.utc` → `datetime.UTC` |
| UP034/37/41/42 | 4 | one each: extra parens, quoted annotation, `TimeoutError` alias, `NamedTuple` → class |
| B017 | 1 | a test asserting on bare `Exception` |

All 29 are mechanical and most are auto-fixable. **Correcting an earlier claim in this file:** the
old version of this entry said the extra findings were "mostly `B` (blind `except Exception:`)" and
would need per-site `noqa`. That was wrong — blind `except` is `BLE001`, in the `BLE` set, which is
not enabled by `B`. There is no conflict with the deliberate `except Exception:` in the degradation
paths, so the argument against widening was based on a misreading.

**What it costs:** `UP035` and `UP017` touch imports and `datetime` calls across many files, which
moves line numbers the deck's 39 anchors cite — so, as with the cleanup itself, the widening and an
anchor pass have to land together (see **M1**).

### B3 · The classifier's keyword lists miss `-ing` forms
`_has_word` (`coordinator.py`) matches a leading word boundary only, so each entry acts as a prefix.
That works for `fold` → "folding" but not for any verb whose gerund drops the final `e`:
`optimize` misses "optimizing", `generate` misses "generating", `iterate` misses "iterating". So
"keep iterating" and "optimizing solubility" classify as **chat** and silently do nothing.

Affects the no-LLM path only. Noted in a comment at the list, which is the honest minimum, but not
fixed: stripping a trailing `e` before matching would fix the class at the cost of real false
positives (`create` → `creat` fires on "creature"). A stem list, or two entries for the verbs that
matter, is the safe version.

### B4 · A question containing a past participle runs a redesign round
Same root cause as B3, opposite direction: `_has_word` is a prefix match, so **"what is loaded?"**
matches `load` in `ACTION_WORDS`, which sets `wants_action` and disables the
`is_question → chat` branch (`coordinator.py`). With a reference in state the turn classifies as
**design** and runs a full round — folds, scoring, a summary — in answer to a question.

```python
>>> classify_rules("what is loaded?", state_with_reference)["intent"]
'design'          # also "which PDB is loaded?", "what did you load?"
```

Found by a trace test that used that phrasing as a throwaway question
(`tests/test_graph.py::test_the_trace_is_turn_scoped` now avoids it and says why).

**Why it is not a one-liner.** `load`/`fetch` cannot simply leave `ACTION_WORDS`: they are what makes
"load a thermostable lipase" (no identifier) bootstrap, and moving the identifier branch above the
question branch would then swallow "can you load 1OIL?". The shape that works is probably a separate
`LOAD_WORDS` list consulted by the identifier branch, plus a participle guard on `wants_action`, and
it needs the classifier table in `tests/test_graph.py` extended with the interrogative forms first.

### B5 · `asyncflow.session.*` directories accumulate in the repo root
Eight of them at the time of writing. Gitignored, so harmless to the repo, but they make `ls` useless
and they are never cleaned up. They come from `WorkflowEngine` and are created per run.

**Fix:** point asyncflow at `data/flow/` the way `config.yml` already points its other outputs, if
the engine supports it; otherwise clean them in `Runtime.aclose()`.

### B6 · Nothing prunes `data/`
Blobs are content-addressed, so duplicates are free, but nothing ever removes them — the reference
campaign alone is 5.1 MB across 24 files. Checkpoints, artifacts and the Kuzu WAL grow the same way.
Fine for development, wrong for anything long-lived.

### B7 · Runtime settings are not persisted
`PUT /api/settings` and the Settings panel hold values in memory only: a restart returns to `.env`,
and `GET /api/settings` reports the source so nothing is hidden. That was deliberate — a secrets file
is a second source of truth and a new thing to leak — but it means a credential typed into the UI has
to be typed again after a restart, or copied into `.env` by hand.

**If it becomes annoying:** write the override layer to `data/overrides.json` at 0600 and load it
below the environment, or offer an "append to .env" action. Not until someone actually wants it.

### B8 · Tier 1 cannot be read while the server is running
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
Worked around in `orbit.py:315` `_finish_task_enriched`. This and C5 both produce a *silent wrong
answer* rather than an error, which is what makes them the two worth fixing first.

### C5 · A FAILED Orbit job carries no reason
Only a non-zero exit code reaches the client. `orbit.py:376–382` synthesises an explanation from the
exit code, then stderr, then the log tail, so the user sees something — but the real reason never
left the endpoint.

### C6 · PSI/J spec fields are dropped in transit
`to_psij_spec` (`orbit.py`) does not forward `stdin_text` or `outputs`, and neither does the broker's
`plugin_psij.py`. `fold_job_spec` (`esmfold.py`) relies on `stdin_text` to feed a sequence without
hitting argv limits, and both job specs declare `outputs` for staging. Reproduce by submitting
`fold_job_spec(...)` through `hpc` and watching the remote command read an empty stdin.

**Consequence now:** a job spec must carry its inputs on the command line, which is why
`mpnn_job_spec` passes a path and `fold_job_spec` is not yet used on a real endpoint. Either the
client should inline stdin into the command (`printf … | cmd`) or the plugin should carry the field.

### C7 · Broker documentation gaps
Two afternoon-sized traps: there is no HTTP topology route (`/topology` is read as a plugin name and
404s after a 307, so readiness must come from the client's `rt.topology()`, which propagates
asynchronously); and `--no-auth` disables *ingress auth only* — the broker still serves TLS and
refuses to start without a cert and key. Both are documentation fixes, not code.

---

## D — needs investigation

### D1 · A turn is still not recorded anywhere durable
The per-turn trace lives in state and is reset each turn, so the checkpoint holds the *current* turn
only: once the next prompt arrives, the previous turn's path is gone. Good enough to explain the
answer on screen, not enough to compare turns across a session or a restart.

**What blocks the obvious fix.** `record_analysis` keys its row
`f"{campaign_id}:{kind}:{round_no}"` with `ON CONFLICT DO UPDATE` (`lake/scores.py`), so two turns in
the same round overwrite each other — a turn is not a concept the lake models. Persisting traces means
either a turn id in that key or a `Turn` entity in tier 1, and then a route to read them back.

**Also not recoverable:** attribution *within* one message. The interpreter's reply is the summary plus
an appended caveats block, transmitted as one string with one `reply_source`; and task handles live in
an in-memory dict, so `/api/tasks` empties on restart while the lake's Task rows persist.

### D2 · The LLM classifier path is unmeasured
Everything known about routing comes from `classify_rules`: the reported session, the whole reference
campaign and all 124 offline tests run with `llm: false`. When a key is present, `CLASSIFY_SYSTEM`
(`coordinator.py`) decides instead and the rules are never consulted — so the phrasings fixed in the
rule path ("label the active site residues", "run another round") are unverified there.

**Unblocked, not answered.** `tests/test_llm_live.py::test_llm_routing_matches_the_rule_table`
(`-m llm`) replays the two tables in `test_graph.py` — `CASES_WITHOUT_REFERENCE` and
`CASES_WITH_REFERENCE`, 26 phrasings — through `CLASSIFY_SYSTEM` and prints every divergence. It
reports rather than fails, because the fix for a divergence is a prompt change and a red test would
make the tier useless for finding them. **Still needs a key and one run.**

Already found while wiring that tier, with a key present: `build_llm` passed `temperature=0.0`, which
`claude-sonnet-5-5` rejects outright — so *every* LLM call failed and fell back to rules. Invisible
with no key, because the call never happened. Fixed in `llm.py`; the lesson is that an unmeasured path
is not a working path.

### D3 · ESM Atlas drops requests under concurrent load
One of six round-2 folds came back with no structure (`s-tutp87hw-r2-5`). The campaign absorbed it
correctly — sequence-only scoring, a warning to the user — so this is not a bug report against us.
But the fold path has **no retry**, and a 1-in-6 drop rate at a fan-out of 6 is high enough that it
is probably load-related rather than random.

**Worth knowing:** whether it is rate limiting (then back off and retry), a size limit interacting
with the 400 aa threshold, or genuinely random. One scripted run of 20 folds at varying concurrency
would answer it.

---

## Maintenance

### M1 · The deck cites 39 source line numbers
Any backend refactor can drift them. `slides/check_anchors.py` re-derives every one and reports where
a moved line actually is; run it before presenting and after any significant edit. The table is not
self-maintaining: three citations in `CODE_FOR_DECK.md` had no anchor and drifted unnoticed until the
deck was scanned for every `file:line` it prints. When adding a citation, add the anchor. `run.json` and
`DECK_SCRIPT.md` are generated — see `CLAUDE.md`.
