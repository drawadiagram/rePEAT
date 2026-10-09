# PEAT — code walkthrough

**Format:** 30 minutes, code walk, questions inline · **Audience:** the lab, including the authors of
`radical.asyncflow`, `rhapsody`, ORBIT and flowgentic.

**The deck is in three acts**, because those are the three questions an audience actually arrives
with and a deck ordered by module answers none of them directly:

| Act | The question | Slides |
|---|---|---|
| **Functionality** | what specific new tasks does this make possible? | 6–10 |
| **Performance** | what does it cost to run — efficiency, effectiveness, and where scale and scientific yield stop being linear in each other? | 11–17 |
| **Usability** | not the interface: the use cases. What is it usable *as* — an application, a platform, a component? | 18–21 |

Each act opens with a divider that states the dimension, makes three claims, and names **the weakest
of them in red before the act rather than after it**. Slide 3 lays the frame out and says which act
is the weak one (performance), and slide 16 is where that act pays up.

Because the middleware authors are in the room the deck is still weighted toward the **seam** —
slides 13, 14 and 15, the Task Interface contract and the two substrates below it — which now sit
inside the performance act, since concurrency is what the seam is *for*. It **ends with asks** rather
than a summary: two weeks on this stack produced eight specific, reproducible findings against their
code, and slide 23 states them with file and line, grouped by what each one costs.

**Derived against `main` @ `7e72b74`** (2026-10-09; the first pass was `e8467e6`, 2026-10-01).
Companion files:

| File | Role |
|---|---|
| `CODE_FOR_DECK.md` | every on-slide code block, keyed `S<slide>-<letter>`, anchored and fidelity-marked |
| `DECK_SCRIPT.md` | the spoken script — **generated** from the deck's own `addNotes`, so it cannot drift |
| `build_deck.js` | the builder; every diagram is native editable shapes, never an image |
| `run_model.py` → `run.json` | mines the real campaign on disk; F3 and F4 are drawn from it |
| `bench_staging.py` → `staging.json` | measures the in-band staging channel; F5 is drawn from it |
| `make_script.py` | regenerates `DECK_SCRIPT.md` |

**Measured: 29.4 minutes of speech** for slides 1–23 at 155 wpm. That fits a 30-minute slot only
with questions held to the end; the order to walk in with for questions inline drops 9, 19 and 20
and measures **25.2**, and a hard twenty drops 5 and 8 as well for **22.9**. `DECK_SCRIPT.md` carries
all three orders and the per-slide counts. Pick one before you walk in.

---

## The one thing to be straight about

**No stage of the enzyme-redesign protocol has run on a cluster.** Nine stages, 203 of the 417
offline tests, zero real runs — so every walltime, core count and memory figure in `protocol/specs.py`
is an estimate. Say this on slides 6 and 22 without being asked, and do not let slide 10 imply
otherwise.

**And there is no scaling study of any kind.** One host, a process pool of four that has never been
varied, no multi-node run, no occupancy measurement, no GPU of our own. The concurrency figure the
deck quotes — mean 2.04 tasks in flight, peak 6 — is a *depth*, not a speedup: a handle's lifetime
includes time queued inside the pool, and there is no serial baseline to divide by. Slide 3 names
performance as the weak act up front, and slide 16 lists the missing measurements rather than letting
anyone find them missing. The one performance number that is a real measurement, taken for this talk,
is F5.

What *has* run is the path under it. On 2026-10-09 the `remote` tier went through a broker on a
public VM to an endpoint registered from a cluster login node, ran Slurm jobs, read their logs,
cancelled a queued one and brought the Slurm id back (`plans/AMAREL_ENDPOINT.md` rung 4). The deck's
previous headline caveat — *no HPC endpoint has ever run a task for this agent* — is retired, and the
narrower one above replaces it. The Globus adapter still has never met a live endpoint.

Second: the variants in the worked example are **heuristic single-point proposals**, not ProteinMPNN
samples, because no endpoint was attached when it ran. The code labels them
`"note": "heuristic proposals, not ProteinMPNN samples"`, and slide 2 says so out loud. ProteinMPNN
itself now runs for real — slide 19's left column — but not in *this* campaign, and the cheapest
discriminator is the mutation count: the heuristic never emits more than one substitution.

Third: the worked campaign ran with **no `ANTHROPIC_API_KEY`** — `/api/health` reports `llm: false`.
Every node took its deterministic path. That is a feature of the design, and it is also why the
classifier needed 18 parametrized test cases.

---

## Visual vocabulary

Carried over from the IMPRESS-A deck so the two read as siblings:

```
line style = status      solid   built, runs end to end against something real
                         dotted  built and tested, never exercised for real
                         dashed  designed for, not implemented

orange                   a RADICAL component, wherever it appears
```

Colour roles: `agent` indigo (the LangGraph loop) · `task` teal (interfaces) · `lake` green (Design
History) · `ui` violet (the web app) · `radical` orange · `fail` red (findings and caveats).

---

## The worked example

One campaign, still on disk, is the deck's only source of numbers. Mined by `run_model.py`:

| | |
|---|---|
| campaign | `s-tutp87hw`, goal recorded as *"initialize 1OIL as the reference design"* |
| reference | **1OIL** → UniProt **P22088**, *Burkholderia cepacia* lipase, **320 aa**, crystal structure pulled |
| rounds | 2 × 6 folds, routed by the analyst's improvement test |
| designs | 12 recorded, 11 after sequence dedupe |
| lead | **G147A**, pLDDT **81.79**, RMSD **3.75 Å**, sequence identity 0.994 |
| progression | round 1 best `r1-5` S136A at **81.66** → round 2 best `r2-2` G147A at **81.79** |
| tier 1 | 6 node tables, 8 rel tables; Campaign 1 · Reference 1 · Design 12 · Task 37 · Output 37 · Structure 11 |
| tier 2 | **114** score rows over **10** metrics · 17 rankings (round 1: 6, round 2: 11 cumulative) · 2 analyses |
| tier 3 | 11-row `designs.parquet` + manifest carrying the curation rules |
| blobs | 24 files, 5.1 MB, content-addressed |
| artifacts | Markdown, `.docx`, a sortable ensemble table, 2 Mol* specs |
| wall clock | ~146 s for the whole session, ~75 s for the redesign turn |

**A real failure in it, which is better than a contrived one:** ESM Atlas dropped one of round 2's
six folds — `s-tutp87hw-r2-5: ESM Atlas returned no structure`. That design survived as a
sequence-only entry with 4 metrics instead of 11, the round scored the other five, and the failure
reached the user through the `warnings` channel. Slide 14 is built on it.

**A pLDDT move of 0.13 is inside the noise.** The loop is the deliverable; the science is a demo.

---

## Coverage map

Two readings: the acts, and the topics a code walk is expected to cover regardless.

| Act | Slides | The claims it makes | What it concedes on its own divider |
|---|---|---|---|
| **Functionality** | 6–10 | a prompt runs a campaign end to end · a multi-day cluster protocol becomes a conversation · a campaign becomes a queryable record | the protocol's nine stages have never run on a cluster |
| **Performance** | 11–17 | the chat answers while the work runs · the seam is what buys that · one figure is a measured curve | no scaling study: one host, one pool of four, no serial baseline |
| **Usability** | 18–21 | as an application · as a platform · as a component | no second consumer — the component claim is the code's shape, not evidence |

| Topic | Slide |
|---|---|
| Purpose | 2 |
| The three dimensions, and which one is weak | **3** |
| Why the design looks like this | **4** |
| Architecture + diagram | 5 |
| The loop, and state | 7, 8 |
| The record, and what it makes askable | 9 |
| **The protocol node, one stage per turn** | **10** |
| Data / control flow, measured | **12** |
| **The middleware seam** | **13–15** |
| Key dependencies | 5, 14, 15 |
| **What is measured, and what is not** | **16** |
| Failure behaviour, as effectiveness | 17 |
| The web app | 19 |
| Where it is deployed, and per-user keys | 20 |
| How to run it, drive it, and the test tiers | 21 |
| Status and open issues | **22** |
| Asks | **23** |

---

## Figures — specification

### F1 — the loop (slide 7)

```
  ┌──────────────┐          ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
  │              │          │   Design     │      │  Redesign    │      │              │      │              │
  │              │   ────►  │ Initializer  │ ───► │ Orchestrator │ ───► │   Analyst    │ ───► │ Interpreter  │
  │ Coordinator  │          │ PDB·UniProt  │      │  key_metric  │      │ score·persist│      │  summarize   │
  │  classify    │          │  literature  │      │   worklist   │      │ rank·visualize      │  prior art   │
  │  answer from │          └──────────────┘      └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
  │  state       │                                       │                     │                     │
  └───┬──────────┘                                       │  ◄──────────────────┘                     ▼
      │   a reference already loaded skips the initializer│   improved AND round < max_rounds        END
      ├──────────────────────────────────────────────────►│
      │
      └─────────────────── plain chat, or a question state can already answer ──────────────────────► END
```

The back edge is **red** — it is the only cycle, and the thing an audience will ask about. It is the
only labelled edge: per-edge `goto=` labels were drawn first and removed, because the gaps between
boxes are too narrow to hold them legibly and the `destinations` block below the figure already names
every target. Say the goto names aloud; do not put them on the arrows.

Accuracy notes: `coordinator` can also route straight to `analyst` (a bare "show me X" with a
reference already loaded) and to `interpreter` (a summarize intent). Both are in `destinations` and
both are drawn only as part of the coordinator's fan-out, not as separate arrows, to keep the figure
readable. Say it if asked; do not draw it.

### F2 — layer map (slide 5)

Eight bands, each with its entry symbol. Orange is RADICAL.

```
 BROWSER      React + Vite      ChatPane · Composer · TaskChips · ArtifactPane (Mol* · md · docx · table)
 API          FastAPI + SSE     app.py — /api/chat streams 7 frame kinds · artifacts · tasks · health · catalog
 GRAPH        LangGraph         coordinator · initializer · orchestrator · analyst · interpreter
                                AsyncSqliteSaver checkpoints
 DEPS         Deps(settings, tasks, history, artifacts)        ← the only thing a node closes over
 INTERFACES   LocalTaskInterface │ QueryTaskInterface │ RemoteWorkflowInterface (ABC)
 SUBSTRATE    flowgentic → radical.asyncflow → rhapsody        ChemGraph ╌ optional
              ConcurrentExecutionBackend(ProcessPoolExecutor(4), name="compute")
 REMOTE       RADICAL Orbit — localhost and a real cluster  │  Globus Compute ╌╌ never run
 HISTORY      Tier 1 Kuzu  │  Tier 2 SQLite  │  Tier 3 Parquet
```

**Band → entry symbol**, for anyone who wants to read along:

| Band | Function | Entry symbol |
|---|---|---|
| BROWSER | chat + artifact pane | `frontend/src/App.tsx` |
| API | one SSE chat endpoint; artifacts and tasks over REST | `backend/designagent/app.py:86` |
| GRAPH | the loop | `graph/build.py::build_graph:69` |
| DEPS | the only seam a node sees | `graph/deps.py::Deps` |
| INTERFACES | submit/status/logs/result/cancel | `tasks/base.py::TaskInterface:135` |
| SUBSTRATE | the flowgentic wrapper and the pool | `runtime.py::_task_wrapper:71` |
| REMOTE | the two remote adapters | `tasks/hpc/base.py::RemoteWorkflowInterface` |
| HISTORY | the three tiers behind one facade | `lake/store.py::DesignHistory` |

**Draw the rule that is not an arrow:** a node never reaches an interface or a store directly, only
through `Deps`. That is what makes 417 of 441 tests run with no network, no pool and no endpoint. State
it on the slide; this audience will check whether the claim is enforced or merely intended (it is
convention here, not an `ast` check — say "convention" if asked).

### F3 — one campaign, measured (slide 12)

A real Gantt from `run.json`'s Task nodes, with recorded submit and finish times. **Not illustrative.**
Rows are coloured by task family: orange folds, indigo scoring, teal lookups, green proposals, violet
visualization. Round brackets above, a seconds axis below.

The three things to point at:

1. **Six folds submitted in the same instant**, finishing 13.3 / 13.3 / 14.3 / 17.9 / 26.0 / 31.7 s —
   out of order. This is the futures design visible, and it is what a sequence diagram would hide.
2. **Round 2 ran 13.2–42.4 s**, so flowgentic's 30 s default timeout would have cancelled and
   silently re-run roughly half of them. This is finding 2 with the project's own numbers behind it.
3. **The initializer's concurrency**: `pdb_lookup` + `uniprot_lookup` together, a cross-reference
   follow-up, then structure download + literature together.

### F4 — the lake (slide 9)

Three tier cards with real counts, plus `record_task_result`'s signature to show that tiers 1 and 2
are written together or not at all. Closing observation: **only tier 1 needs a running process to
read** — tiers 2 and 3 are a SQLite file and a Parquet file, which is why `run_model.py` could read
them while the server held the Kuzu lock.

### F5 — what one file costs to cross the broker (slide 16)

The deck's only measured curve, and the only figure not drawn from the campaign. Produced by
`slides/bench_staging.py`, which drives the real `wrap()` and `collect()` from
`tasks/hpc/artifacts.py` through a local `bash -lc` — no broker in the path, so every number is a
**floor** on what a real job pays. Two series against payload size (1 KiB … 1 MiB, equally spaced as
categories because the range is a thousandfold), y axis in stdout bytes per payload byte, with a
dashed break-even line at ×1.0:

- **incompressible** (`os.urandom`) settles at **×1.33** — base64 over gzip that cannot compress.
- **structure text** (a real 1OIL PDB, tiled above 462 KB) settles at **×0.33**. Tiling flatters it:
  gzip's window is 32 KiB, so the slide and the footer both say the series is tiled.

The figure exists for the asymmetry underneath it, which is the finding: **the cap is checked on the
raw file size** and the cost is paid in compressed bytes, so a 2 MiB PDB is refused as `too_large`
while costing 0.67 MB, and a 1 MiB random file is accepted and costs 1.40 MB. Inbound has no declared
cap at all — the argv chunks must fit `ARG_MAX`, and `exec` fails with E2BIG at ~1.5 MiB of
incompressible payload on this host. Both ceilings are in the wrong units. That is **our** bug, so it
goes to `plans/BACKLOG.md` and not onto the asks slide.

Re-run it before presenting if the host has changed; the `source` block in `staging.json` records
which host, which date and which constants it measured against.

---

## Slides

Timings are cumulative, from `DECK_SCRIPT.md`'s measured counts. `✂` is in `make_script.py`'s `CUT`.

### 1 — Title (0:00–0:48)
Motif: a chat bubble feeding a five-node ring feeding an orange worker pool. The thesis subtitle —
*a LangGraph loop whose long work leaves the process, and a chat that keeps talking while it does* —
the campaign's headline numbers, and the three dimensions listed as the deck's spine so the structure
is announced before it is used.

### 2 — One prompt, a whole campaign (0:48–1:42)
Six-step strip, then two cards: what came back (left, measured) and **what that is not** (right, red).
The right card is load-bearing. Put the heuristic-proposer caveat and the no-API-key fact here, at
minute one, rather than letting someone find them at minute twenty.

### 3 — ★ Three questions this deck is organised around (1:42–2:54)
The frame. Three columns — functionality, performance, usability — each with the definition being
used and what this system answers. The dark band at the bottom is the point of the slide: **the
middle column is the weak one**, said here rather than extracted later, together with the reason the
concurrency number that is coming is a depth and not a speedup.

### 4 — ★ One constraint sets the whole design (2:54–3:54)
The slide to keep if you keep one, and it is a *performance* requirement: milliseconds versus hours.
Five consequences, each with the file that implements it. Closes by naming the condition under which
the whole Task Interface layer stops paying for itself: *if the agent may block, you would call the
tools directly*. Saying that earns the rest of the deck.

### 5 — ✂ Architecture (3:54–5:00) · **F2**
Eight bands, band→symbol table above. Two rail cards: the `Deps` rule, and the fact that exactly one
band crosses into asyncflow — deliberate, because it confines the middleware dependency to a layer
that can be stubbed, which is what the offline test tier rests on.

---

### 6 — **FUNCTIONALITY** — divider (5:00–5:24)
Three claims: a prompt runs a campaign, a cluster protocol becomes a conversation, a campaign becomes
a queryable record. Red strip: the protocol has never run on a cluster.

### 7 — Six nodes, and the nodes do their own routing (5:24–6:48) · **F1** · `S7-A`
`destinations` beside the figure, now including `protocol`. The figure draws the five campaign nodes
and a line of text says the sixth answers in place — drawing it would add a box and no information.
The point to land: one static edge, `START → coordinator`; everything else is
`Command(goto=…, update=…)`, so the routing decision and the state write are one atomic return.

### 8 — ✂ State (6:48–8:00) · `S8-A`…`S8-E`
The eight spec'd keys with their reducers, then the checkpoint-bloat finding: coordinates in
`pending_results` cost ~320 KB of checkpoint per turn and grew with the ensemble. Fixed by writing
blobs in the orchestrator. **Pinned by a test** that serializes state and asserts `"ATOM  "` never
appears, because the regression is invisible — nothing breaks, it just gets slower forever. The
number reappears on slide 16 as one of the few things actually measured.

### 9 — ✂ Design History (8:00–9:18) · **F4** · `S9-A`, `S9-B`
Three tiers with real counts, framed as functionality: the campaign becomes a record the *next*
campaign can query, and `best_designs(exclude_campaign=…)` is the new task that makes possible. Flag
the deliberate trade in tier 1: `properties` columns hold JSON, so a new task type needs no migration
— queryability traded for evolvability, stage-appropriate rather than ideal.

### 10 — The protocol node (9:18–10:48) · `S10-A`, `S10-B`
The nine stages as a strip, indigo where a stage asks the user something and stops. The mechanism is
the point: there is no `interrupt()` in this repo, so **the turn boundary is the checkpoint**, and the
coordinator therefore routes on `awaiting` *before* it classifies intent — "liu", "310,364" and "go"
all classify as `chat`. Then the two job modes, and the honest close: 203 tests, no cluster run.

---

### 11 — **PERFORMANCE** — divider (10:48–11:12)
Three claims: the chat answers while the work runs, the seam is what buys that, and one figure is a
measured curve. Red strip: no scaling study, and a mean in-flight depth is not a speedup.

### 12 — ★ One campaign, measured (11:12–12:48) · **F3**
See F3 above. The dark band under the Gantt carries the derived aggregate — 37 tasks, 297.9 s of
handle lifetime in 146.1 s of wall clock, mean 2.04 in flight, peak 6 — computed in `build_deck.js`
from the same rows the figure is drawn from, so it cannot go stale. Say what it is not. End on the
ESM Atlas failure, which sets up slide 17.

### 13 — ★ The Task Interface contract (12:48–14:06) · `S13-A`…`S13-E`
Five verbs, a capability record, a capability table across all four interfaces. The piece to defend:
a failed *submission* becomes a **settled FAILED handle**, not an exception, so there is exactly one
shape for a caller to handle and `gather()` never raises.

### 14 — ★ Through flowgentic to rhapsody (14:06–15:36) · `S14-A`…`S14-D`
The five-hop path, the wrapper with its `RetryConfig`, the `ensure_future` comment, the three
picklability constraints, and **finding 1 verbatim**: flowgentic's `except Exception: raise` under a
comment that says "if present". Pose it as a question about intent, not an accusation.

### 15 — ★ RADICAL Orbit (15:36–17:12) · `S15-A`…`S15-D`
Two clients and what each is for; two threading bridges. Then findings 3 and 4 side by side with the
code that works around them — both produce a *silent wrong answer* rather than an error, which is why
they matter most. Close with the status: twelve tests against a real localhost broker, and since
2026-10-09 a real cluster through a real one.

### 16 — ★ What we have measured, and what we have not (17:12–19:24) · **F5**
The act's reckoning, and the slide this room will test you on. F5 and its two ceilings on the left,
a two-card ledger on the right — measured above, *not measured and therefore not claimed* below — and
the nonlinearity in the band at the foot: `stage_score` quotes the GPU-hours and waits, cost is
linear in designs folded and yield is not. Do not compress this slide and do not soften the right
card; its whole value is that it was volunteered.

### 17 — Degradation is a feature (19:24–20:36) · `S17-A`, `S17-B`
Six failure modes with evidence, framed as *effectiveness*: the run still produced a scored round
with a fold missing. Dwell on the guarded lake writes. Row 4 is the failure that happened on its own.

---

### 18 — **USABILITY** — divider (20:36–21:00)
Three claims: as an application, as a platform, as a component. Red strip: no second consumer has
ever driven it, so the component claim is the shape of the code rather than evidence.

### 19 — ✂ The web app (21:00–22:24) · `S19-A`
Seven frame kinds, one merged queue so node status and task progress arrive in one ordered stream.
The SSE buffer, because chunk boundaries land mid-frame. The usability point rather than the
implementation one: what the agent sends the browser is a **sanitized JSON view spec, never generated
JavaScript** — B4 has the full argument. The canvas was confirmed rendering on 2026-10-01; say so,
because some of the room saw the version where it was an open item.

### 20 — ✂ Deployed, with per-user keys (22:24–23:54) · `S20-A`
Five hops from browser to Slurm, three of them on one VM. Logins off by default and off they change
nothing; on, the cookie and the admin role. The red card is the one to dwell on if anyone asks about
multi-user: a key must not reach `configurable` (checkpoint metadata) or `spec.params` (the lake), so
it travels in a `ContextVar` and is handed to a pool task as an argument. Close with A21 — the broker
is shared, so HPC is not yet a tenant boundary.

### 21 — Running it, driving it (23:54–25:42) · `S21-A`
Eight commands, and the component claim made concrete: 22 HTTP routes, a CLI, and a record whose
tiers 2 and 3 read with nothing running. Then the test-tier table and the split that matters: 417
offline, and 24 split three ways so the cheap tier cannot drag in one that needs an allocation or
spends money.

---

### 22 — ★ What is real, and what is not — in all three (25:42–27:06)
The status slide, cut three ways instead of two: a green block and a red block *per dimension*.
**Do not compress it.** The three red columns are deliberately as long as the three green ones — an
equal-length red column reads as candour and a short one reads as spin — and the closing line names
the performance column as the one to attack.

### 23 — ★ Findings and asks (27:06–29:24)
Dark slide. The eight reproducibles in two columns, **grouped by what each costs**: left, the ones
that produce a wrong answer or burn real time; right, the ones paid by whoever adopts the stack next.
Each carries a dimension tag and names file and line. Then the question in an orange-bordered box:
**is `EXECUTION_BLOCK` meant to preserve the caller's context?** End there. No summary slide — the
asks are the ending.

### B1 — The streaming experiment, in full *(backup)* · `B1-A`
What was tried, the symptom, the isolation, and the mechanism: `EXECUTION_BLOCK` runs the body on
asyncflow's loop, LangGraph's writer lives in a contextvar its executor sets around the node call,
crossing loops loses it, and LangGraph's helper degrades to a no-op rather than raising — which is
exactly why the failure is silent. Includes the fairness point: this is only a bug if
`EXECUTION_BLOCK` intends to propagate context.

### B2 — Standing up a local Orbit stack *(backup)*
The TLS trap (`--no-auth` disables ingress auth only), the SAN and key-mode requirements, the absent
HTTP topology route, and why subprocesses rather than `EmbeddedBroker` (it expects operator-placed
credentials in `~/.radical/orbit`, which a test must not create).

### B3 — Designed for two, implemented for one *(backup)* · `B3-A`, `B3-B`
Demoted from the main path when the deck went to three acts: it is an argument about the *shape* of
the extension point, which the usability divider now makes in one line. Keep it to hand, because it
is the honest version — the shared surface is 102 lines and cost almost nothing, the interesting work
is irreducibly backend-specific, and **the capability flags are more valuable than the base class**.

### B4 — Local Task Agents *(backup)* · `B4-A`
Also demoted. Where the brief was deliberately **not** followed: it says the agent "codes a
browser-based visualization", and shipping LLM-written JS into the viewer is an injection surface for
a prompt, so the agent emits a sanitized JSON view spec instead. Slide 19 states the decision in one
sentence; this slide has the four-agent status table behind it.

---

## Delivery notes

- **Open two terminals.** One in the repo root for `pytest` and `grep`; one for `python -m
  designagent`. Every anchor on a slide is greppable live, and this audience may ask you to.
- **Have `run.json` and `staging.json` open.** Every number in the deck is in one of them, and "let
  me show you where that came from" is a stronger answer than repeating the number.
- **If someone challenges the concurrency figure, agree with them.** It is a mean in-flight depth
  over one session on one host, not a speedup and not a scaling result. Slide 16 is the answer, and
  conceding fast is cheaper than defending a number the deck already disclaims.
- **If the clock goes, cut a content slide, not a divider.** The three dividers are 20 seconds each
  and they are what makes the deck legible; `CUT` is 9, 19 and 20 for that reason.
- **If the room goes deep on EXECUTION_BLOCK**, go to B1 and stay there. It is the most interesting
  finding and the one most likely to change their code.
- **If the room goes deep on ORBIT**, go to B2. Findings 3, 4 and 5 are the ones worth their time;
  7 and 8 are documentation and should not eat the clock.
- **If someone asks why not `langgraph-api` / LangGraph Platform**: the brief specified flowgentic as
  the deployment substrate, and the interesting question — what happens to streaming when node bodies
  leave the runnable context — only exists because of that choice. Do not relitigate it on the clock.
- **If someone asks about ChemGraph's pins**: it pins `langgraph==1.2.11`, `langchain-core==1.6.1`
  and `pydantic==2.13.4`, and it pulls torch via mace-torch. That is why it is an optional extra
  rather than a base dependency.

## Before presenting

```sh
python3 slides/check_anchors.py      # 46/46 — do this first, it is the cheapest check
python3 slides/bench_staging.py      # re-measure F5 if the host changed
python3 slides/make_script.py        # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
pytest -q                            # 417 pass; the deck must not have touched the code
```

**Do not re-run `run_model.py` casually.** It aggregates the whole lake and takes whichever campaign
Kuzu lists first, so on today's data dir it replaces the deck's worked example wholesale and exits 0
(`plans/BACKLOG.md` M2). The committed `run.json` is the 2026-10-01 campaign with only its `code`
block refreshed. `staging.json` is deliberately a separate file for the same reason: re-measuring F5
must not be able to touch the campaign.

`CODE_FOR_DECK.md` carries the per-snippet first-line matcher, and that file is exactly where drift
hides.
