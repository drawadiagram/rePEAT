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
The remote path is proven against a localhost broker only (`tests/test_orbit_local.py`, 8 tests).
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

A third, found the same way and worse than either: **a job's `stdout` came back duplicated.**
`drain_logs` (`tasks/hpc/base.py`) and `_poll_job` (`tasks/hpc/orbit.py`) tailed the same file from
independent cursors — `handle.log_offset` and `meta["stdout_offset"]` — and both appended to
`handle.log_tail`, which `_finish_job` then returned as the job's output, appending the terminal
event's copy on top. Three writers, so every byte landed up to three times in arbitrary order, then
got clipped to the last 8000 chars. Reproduce by printing 20 lines at 0.3 s intervals with a drain
running, which is the production arrangement since `manager.submit` starts one for every interface
advertising `supports_log_stream`: the result held 60 lines,
`['line-1', 'line-2', 'line-3', 'line-1', ...]`. Every assertion on that channel was a substring
check, which cannot see duplication, so it passed throughout. Fixed by making `_finish_job_enriched`
re-read the whole file once from offset 0 (the broker serves it from any offset and reports its
size) and by leaving `log_tail` to the drain alone. Pinned by
`test_job_stdout_is_not_duplicated_by_the_log_drain` and
`test_a_large_stdout_payload_survives_intact`.

This matters beyond logging: stdout is the **only** channel a job has for returning a file, because
`outputs` is dropped in transit (**C6**). Anything built on job output had to land on top of this.

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

### A3 · `proteinmpnn` is a heuristic proposer unless an endpoint with ProteinMPNN is attached
With no `hpc` interface, `interface_for` falls back to the heuristic, which produces single-point
substitutions from a fixed table. The catalog entry now points at
`proteinmpnn_local_fallback` rather than `propose_variants` itself, so the result relabels at the
point of substitution — *"no HPC endpoint was attached, so the job was never submitted"* — because
the old wiring made a heuristic round indistinguishable from a real one in the output while the
ledger recorded that ProteinMPNN was asked for.

**The real path now works and has been run.** Weights `v_48_020` on CPU, through a real localhost
broker: 3 sequences of a 320-residue target in 4.6 s, and a three-round 1UBQ campaign whose designs
carry `source=proteinmpnn`, `mpnn_score`, and 30–36 substitutions from the reference. See
`scripts/setup_mpnn.sh`.

Honesty no longer rests on a hand-written note. `variants_from_mpnn_fasta` lifts `model_name` and
`git_hash` out of ProteinMPNN's own FASTA header, so provenance is *read* rather than asserted, and a
run that declares neither earns a caveat saying its samples cannot be attributed to specific weights.
The cheapest discriminator in a finished round is the mutation count: the heuristic table never emits
more than one substitution per variant.

**Still keep it honest** — the whole reference campaign in `slides/run.json` is heuristic output until
it is regenerated, and a reader who misses the label will over-read a 0.13 pLDDT difference.

### A4 · Every ProteinMPNN round redesigns the *reference* backbone
`_mpnn_job` (`graph/nodes/orchestrator.py`) reads `reference["structure_path"]`, so round 2 and round
3 re-sample the same original backbone with a different seed rather than building on the round's
lead. Meanwhile `parent_sequence` *is* the evolving lead, so `mutations` is the delta from the lead
while ProteinMPNN's own `seq_recovery` is measured against the reference sequence. Both numbers are
correct and they are not comparable, which is why a finished round can read
"5 mutations ... 51% identity to the input backbone's sequence".

Measured on a real 3-round 1UBQ run (`model_name=v_48_020`): round 1 designs sat 30-36 substitutions
from the reference; round 2 designs were 11-15 from their parent but still 33-36 from the reference.
So the rounds are near-independent samples of one fold, not refinement.

The heuristic path does not have this shape -- `propose_variants` mutates `parent_sequence`, so it
iterates. The asymmetry was invisible until ProteinMPNN actually ran.

**The choice:** feed each round the lead's own predicted structure
(`lead_design["structure_path"]`, already written as a blob by the fold step) and a campaign
iterates; keep the reference and a campaign is a wider sample of one backbone with
`rmsd_to_reference` still meaningful. Either is defensible, but it should be deliberate and the
reply should say which. Reproduce with `DESIGNAGENT_ORBIT_LOCAL=true` plus a real
`DESIGNAGENT_MPNN_COMMAND` and three rounds on one target.

### A5 · `--fixed_positions` was not a ProteinMPNN flag
`mpnn_job_spec` carried `--fixed_positions <space-separated ints>`, which ProteinMPNN does not
accept: the real flag is `--fixed_positions_jsonl` and it takes a file. No caller ever passed it, so
nothing broke, but a real run would have exited non-zero on the first use of the feature. Removed
rather than left in place, with a comment at the site.

**To bring it back correctly:** write the JSONL into the job's `inputs` (the staging mechanism added
for C6 can carry it) and pass `--fixed_positions_jsonl` pointing at that name. The caller-facing
argument can stay a list of residue positions. Worth having — it is how a campaign protects an active
site from redesign, which is precisely what the `functional_features` already in `reference` describe.

### A6 · An inlined structure has a hard size ceiling
Stage-in rides in argv (C6), so `mpnn_job_spec` refuses a structure whose gzipped, base64'd backbone
exceeds `MAX_INLINE_B64` = 1 MiB, half of this host's `ARG_MAX` of 2 MiB. The round then falls back to
the heuristic with a caveat naming the measured size.

Backbone-only stripping buys most of the headroom: 1UBQ's 76 residues go from ~80 KB to ~25 KB before
gzip, and a real 320-residue chain inlined at ~120 KB of backbone without trouble. The ceiling bites
on a very large single chain or a multi-chain design, and `ARG_MAX` is not guaranteed to be 2 MiB
everywhere — a site with a smaller limit will fail at submission rather than at the guard.

**The real fix is upstream staging (C6)**, or a site-side input cache the job reads by path. Until
then the guard is the honest behaviour: a named refusal beats a scheduler error.

### A7 · A design's provenance is not visible in the UI
`provenance.source` is set by the generator (`"proteinmpnn"` / `"heuristic"` / `"literature"` /
`"user"`), carried into each design by `nodes/analyst.py`, and typed in the frontend
(`lib/types.ts`) — and rendered by nothing. The Ensemble table rows (`nodes/interpreter.py`) and the
Markdown/.docx summary (`artifacts/render.py`) both omit it, so the honest discriminator a reader
needs is only inferable: a `mpnn score` column, or counting substitutions in `mutations`.

Adding a `source` column to the Ensemble rows is a two-line change at `interpreter.py` and a
`TableView` pin; the reason to pause is that a mixed round (ProteinMPNN plus a heuristic fallback)
should probably say so in the reply too, not just in a table a user has to open. Related: the task
chip now names a non-local `interface`, and the summary's `## Tasks run` prints it, so *where* a
round ran is visible while *what proposed each design* is not.

### A8 · `state.warnings` has no UI element of its own
The channel is streamed (`"warnings"` is in `UI_STATE_KEYS`) and merged into the frontend's state,
and then nothing renders it. Caveats reach the user only because `nodes/interpreter.py` appends a
`**Caveats from this run:**` block to the reply text, capped at the first 5, plus a count in the
`TurnTrace` disclosure. So a degraded turn whose reply the user skims looks normal, and caveats 6+
are dropped silently.

### A9 · The settings write surface is a security boundary
`PUT /api/settings` is open on loopback with no `DESIGNAGENT_ADMIN_TOKEN`, which is the development
default, so every field added to `SettingsUpdate` is reachable by any local process. That was fine
while the surface held credentials and config values. It stopped being fine the moment
`mpnn_command` and `mpnn_prologue` were added: the prologue is appended to the generated job script
verbatim (`tasks/hpc/artifacts.py`), so a `PUT` was arbitrary shell, run as the server user locally
and on the HPC endpoint under the site's allocation remotely. Caught in review before it shipped
anywhere; demonstrated against the pre-fix process, which accepted
`{"mpnn_prologue": "touch /tmp/pwned"}` with HTTP 200 and held it as an active override awaiting the
next design round.

Both are now refused, listed in `NOT_REMOTELY_WRITABLE` with the reason, and shown `readonly` in the
panel. The general lesson is recorded because the next field to name a program will look just as
harmless: **adding a field to `SettingsUpdate` widens an unauthenticated surface**, and a value that
is interpreted rather than stored needs a reason before it goes there.

**Worth considering separately:** whether loopback-without-token should stay open at all now that the
surface is wider, or whether `orbit_psij_executor` and `orbit_broker_url` — a scheduler name and a
URL the client will trust — deserve the same scrutiny.

### A10 · A heuristic round never says so in its reply
Measured in a browser with the endpoint turned off: the round completes, produces ranked designs, and
the reply reads exactly like a real one — *"It was proposed because glutamine to glutamate avoids
deamidation"*, no caveats block, no mention that these are rule-based proposals. The generator does
label its own output (`"note": "heuristic proposals, not ProteinMPNN samples"`), but the note stays in
the task record and `nodes/interpreter.py` never surfaces it, because nothing put it on the
`warnings` channel: from the orchestrator's point of view nothing failed, so there was nothing to
caveat.

What a user is left with, all requiring prior knowledge: the chip is named `propose variants`
rather than `proteinmpnn`, each design carries exactly one substitution, the rationale is a chemistry
phrase instead of a sample and score, and there is no `mpnn score` column.

This is the other half of **A3**. The label is honest where it is written and absent where it is
read. The cheap fix is for the orchestrator to push a line onto `warnings` when it takes the
heuristic branch *by configuration* rather than by failure — the fallback-after-failure path already
does exactly that. The reason to think first: a user with no endpoint would then see the same caveat
on every single round, which is noise that trains people to ignore the block. Possibly it belongs on
the first round of a session only, or in the ensemble table as a provenance column (**A7**) instead
of in prose.

Reproduce from the browser: turn off the development-broker toggle in the credentials panel, start a
new session, run a redesign. `frontend/e2e/BROWSER_TESTS.md` T10 is this, written up as a test with
the gap stated.

### A11 · The relabelling fallback body is unreachable in practice
`CATALOG["proteinmpnn"]` points at `proteinmpnn_local_fallback`, which exists to relabel a heuristic
round as *"no HPC endpoint was attached, so the job was never submitted"*. The orchestrator never
reaches it: it guards on `hpc_available` **before** enqueuing, so it names `propose_variants` on the
local interface rather than letting `interface_for` downgrade a `proteinmpnn` spec. The body therefore
runs only if an endpoint detaches between planning and submission, or if something calls the catalog
entry directly.

Not harmful — the label is correct whenever it does appear, and the guard it duplicates is the right
one — but it means the clearest wording of the heuristic-substitution message is the one users almost
never see, which is worth knowing before anyone counts it as covering **A10**.

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

### B5 · A branch switch under the running dev server kills the browser tab
Cost a session. Vite was serving `wire-user-config-and-secrets`, a `git checkout main` deleted
`src/settings/SettingsPanel.tsx` and `src/chat/TurnTrace.tsx` underneath it, and the browser applied
an HMR update for modules whose dependencies had vanished — a dead tab with no error in the server
log. The vite log is the tell: HMR updates naming files that no longer exist on disk.

**Recovery:** check out the branch the tab was loaded from (or restart the server on the branch you
want), then **hard-reload**; a soft reload can keep the broken module graph.

**Possible guard:** a `postcheckout` git hook that touches a sentinel, or just a line in the README.
Not obviously worth automating, but worth knowing, because the symptom points at the browser and the
cause is in git.

### B6 · The browser tests stop at the Mol* canvas
`frontend/e2e` asserts the artifact pane opens and the viewer mounts; nothing checks that a structure
actually renders, that highlights land on the right residues, or that a focus call moves the camera.
Those are the parts of the visualization path a user would notice first and the tests would not.

**How it could be done:** a Playwright screenshot comparison on a fixed PDB with a fixed view spec,
or reading back Mol*'s own state through `page.evaluate`. Both are more machinery than the rest of
this tier, which is why neither is here yet.

### B7 · `asyncflow.session.*` directories accumulate in the repo root
Eight of them at the time of writing. Gitignored, so harmless to the repo, but they make `ls` useless
and they are never cleaned up. They come from `WorkflowEngine` and are created per run.

**Fix:** point asyncflow at `data/flow/` the way `config.yml` already points its other outputs, if
the engine supports it; otherwise clean them in `Runtime.aclose()`.

### B8 · Nothing prunes `data/`
Blobs are content-addressed, so duplicates are free, but nothing ever removes them — the reference
campaign alone is 5.1 MB across 24 files. Checkpoints, artifacts and the Kuzu WAL grow the same way.
Fine for development, wrong for anything long-lived.

### B9 · Runtime settings are not persisted
`PUT /api/settings` and the Settings panel hold values in memory only: a restart returns to `.env`,
and `GET /api/settings` reports the source so nothing is hidden. That was deliberate — a secrets file
is a second source of truth and a new thing to leak — but it means a credential typed into the UI has
to be typed again after a restart, or copied into `.env` by hand.

**If it becomes annoying:** write the override layer to `data/overrides.json` at 0600 and load it
below the environment, or offer an "append to .env" action. Not until someone actually wants it.

### B10 · Tier 1 cannot be read while the server is running
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

**Still open upstream, and worked around in one place.** `refcodes/` is gitignored and an editable
install with no recorded revision (**B1**), so a patch to `plugin_psij.py` is neither shippable nor
verifiable by anyone else. The client therefore honours both fields in band, over the one channel the
broker does carry whole: stdout. `tasks/hpc/artifacts.py` rewrites any spec declaring `inputs` or
`outputs` into a `bash -lc` script that reassembles its inputs from argv, redirects the real
command's own output to stderr, and prints each declared output as a framed block carrying its size
and sha256. `OrbitInterface._submit_job` applies it to every job spec, so `fold_job_spec` gets the
same treatment; `TaskManager._materialize_artifacts` turns the returned bytes into blob paths before
anything persists them. A spec declaring neither field passes through unwrapped.

Two ceilings that workaround buys, both measured:

* **Inputs**: `MAX_ARG_STRLEN` caps a *single* argv element at ~128 KiB, so the payload is gzipped,
  base64'd and split into 64 KiB chunks; the limit on all of them together is `ARG_MAX`, megabytes.
  A 533 KB incompressible input round-trips in 9 chunks.
* **Outputs**: bounded by `orbit_artifact_max_bytes` per file and `orbit_job_output_max_bytes` in
  total. An over-budget file is *named* in the manifest rather than dropped.

The protocol detects rather than infers a short read: a missing closing frame, a missing manifest, a
file count below the manifest's, and a digest mismatch are all distinguishable, which matters because
a clipped base64 payload is still valid base64. Pinned by `tests/test_artifacts.py` (15 offline tests
that run the generated script with bash) and three `-m live` tests through a real broker.

**What would close this:** the broker forwarding `outputs` and `stdin_text`, at which point the
wrapper becomes a fallback for older endpoints rather than the only path.

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
