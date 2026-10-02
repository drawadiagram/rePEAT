# designagent — code walkthrough

**Format:** 30 minutes, code walk, questions inline · **Audience:** the lab, including the authors of
`radical.asyncflow`, `rhapsody`, ORBIT and flowgentic.

Because the middleware authors are in the room, this deck is weighted toward the **seam** — slides
8, 9 and 10, the Task Interface contract and the two substrates below it — and it **ends with asks**
rather than a summary. Two weeks on this stack produced six specific, reproducible findings against
their code, and slide 18 states them with file and line.

**Derived against `main` @ `e8467e6`** (2026-10-01). Companion files:

| File | Role |
|---|---|
| `CODE_FOR_DECK.md` | every on-slide code block, keyed `S<slide>-<letter>`, anchored and fidelity-marked |
| `DECK_SCRIPT.md` | the spoken script — **generated** from the deck's own `addNotes`, so it cannot drift |
| `build_deck.js` | the builder; every diagram is native editable shapes, never an image |
| `run_model.py` → `run.json` | mines the real campaign on disk; F3 and F4 are drawn from it |
| `make_script.py` | regenerates `DECK_SCRIPT.md` |

**Measured: 23.4 minutes of speech** for slides 1–18 at 155 wpm, which fits a 30-minute slot with
questions taken inline. `DECK_SCRIPT.md` carries three running orders (23.4 / 20.5 / 19.1) and the
per-slide counts. Pick one before you walk in.

---

## The one thing to be straight about

**No HPC endpoint has ever run a task for this agent.** ORBIT works, against a localhost broker; that
proves the client path and nothing about a scheduler. The Globus adapter has never met a live
endpoint. Say this on slide 17 without being asked, and do not let slides 9–11 imply otherwise.

Second: the variants in the worked example are **heuristic single-point proposals**, not ProteinMPNN
samples, because no endpoint was attached. The code labels them
`"note": "heuristic proposals, not ProteinMPNN samples"`, and slide 2 says so out loud.

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

| Topic | Slide |
|---|---|
| Purpose | 2 |
| Why the design looks like this | **3** |
| Architecture + diagram | 4–5 |
| State and its reducers | 6 |
| Data / control flow, measured | **7** |
| Major components and how they interact | 5, 8–15 |
| **The middleware seam** | **8–10** |
| Key dependencies | 5, 9, 10 |
| Failure behaviour | 14 |
| How to run it, and the test tiers | 16 |
| Status and open issues | **17** |
| Asks | **18** |

---

## Figures — specification

### F1 — the loop (slide 4)

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
 REMOTE       RADICAL Orbit ╌ localhost only  │  Globus Compute ╌╌ never run
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
through `Deps`. That is what makes 93 of 99 tests run with no network, no pool and no endpoint. State
it on the slide; this audience will check whether the claim is enforced or merely intended (it is
convention here, not an `ast` check — say "convention" if asked).

### F3 — one campaign, measured (slide 7)

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

### F4 — the lake (slide 13)

Three tier cards with real counts, plus `record_task_result`'s signature to show that tiers 1 and 2
are written together or not at all. Closing observation: **only tier 1 needs a running process to
read** — tiers 2 and 3 are a SQLite file and a Parquet file, which is why `run_model.py` could read
them while the server held the Kuzu lock.

---

## Slides

Timings are cumulative, from `DECK_SCRIPT.md`'s measured counts.

### 1 — Title (0:00–0:50)
Motif: a chat bubble feeding a five-node ring feeding an orange worker pool. Carries the subtitle that
states the thesis — *a LangGraph loop whose long work leaves the process, and a chat that keeps
talking while it does* — plus the campaign's headline numbers and `main @ e8467e6`.

### 2 — One prompt, a whole campaign (0:50–1:45)
Six-step strip, then two cards: what came back (left, measured) and **what that is not** (right, red).
The right card is load-bearing. Put the heuristic-proposer caveat and the no-API-key fact here, at
minute one, rather than letting someone find them at minute twenty.

### 3 — One constraint sets the whole design (1:45–2:50)
The slide to keep if you keep one. A black band states the constraint — milliseconds versus hours —
then five consequences, each with the file that implements it. Closes by naming the condition under
which the whole Task Interface layer stops paying for itself: *if the agent may block, you would call
the tools directly*. Saying that earns the rest of the deck.

### 4 — Five nodes, one entry (2:50–4:00) · **F1** · `S4-A`
`destinations` verbatim beside the figure. The point to land: one static edge, `START → coordinator`.
Everything else is `Command(goto=…, update=…)`, so the routing decision and the state write are the
same atomic return — a node cannot update state and then fail to say where it went.

### 5 — Architecture (4:00–5:10) · **F2**
Eight bands, band→symbol table above. Two rail cards: the `Deps` rule, and the fact that exactly one
band crosses into asyncflow. The second is deliberate and worth saying: the middleware dependency is
confined to a layer that can be stubbed, which is what the offline test tier rests on.

### 6 — State (5:10–6:25) · `S6-A`, `S6-B`, `S6-C`
The eight spec'd keys with their reducers, then the checkpoint-bloat finding: coordinates in
`pending_results` cost ~320 KB of checkpoint per turn and grew with the ensemble. Fixed by writing
blobs in the orchestrator. **Pinned by a test** that serializes state and asserts `"ATOM  "` never
appears, because the regression is invisible — nothing breaks, it just gets slower forever.

### 7 — One campaign, measured (6:25–7:45) · **F3**
See F3 above. End on the ESM Atlas failure, which sets up slide 14.

### 8 — ★ The Task Interface contract (7:45–9:05) · `S8-A`…`S8-E`
Five verbs, a capability record, a capability table across all four interfaces. The piece to defend:
a failed *submission* becomes a **settled FAILED handle**, not an exception, so there is exactly one
shape for a caller to handle and `gather()` never raises. State the invariant explicitly — *a caller
holds a handle whose future resolves* — because everything downstream depends on it.

### 9 — ★ Through flowgentic to rhapsody (9:05–10:35) · `S9-A`…`S9C`
The five-hop path, the wrapper with its `RetryConfig`, the `ensure_future` comment, the three
picklability constraints, and **finding 1 verbatim**: flowgentic's `except Exception: raise` under a
comment that says "if present". Pose it as a question about intent, not an accusation.

### 10 — ★ RADICAL Orbit (10:35–12:10) · `S10-A`…`S10-D`
Two clients and what each is for; two threading bridges (`to_thread` ×23, `call_soon_threadsafe`).
Then findings 4 and 5 side by side with the code that works around them — both produce a *silent
wrong answer* rather than an error, which is why they matter most. Close with the status: six tests
against a real localhost broker, never a scheduler.

### 11 — Designed for two, implemented for one (12:10–13:35) · `S11-A`, `S11-B`
The ABC with two children, one dotted and one dashed. Make the honest argument: the shared surface is
102 lines and cost almost nothing, the interesting work is irreducibly backend-specific, and **the
capability flags are more valuable than the base class**. Mention the `shlex.join` vs. argv-list
split and the test that proves a metacharacter stays data.

### 12 — Local Task Agents *(cuttable)* (13:35–14:55) · `S12-A`
Where the brief was deliberately **not** followed: it says the agent "codes a browser-based
visualization", and shipping LLM-written JS into the viewer is an injection surface for a prompt. The
agent emits a sanitized JSON view spec instead. Then the four-agent status table — including that
ProteinMPNN's absence is why the campaign's variants are single-point substitutions.

### 13 — Design History (14:55–16:15) · **F4** · `S13-A`, `S13-B`
Three tiers with real counts. Flag the deliberate trade in tier 1: `properties` columns hold JSON, so
a new task type needs no migration — queryability traded for evolvability, and defend it as
stage-appropriate rather than ideal. Point at `best_designs(exclude_campaign=…)` as how the
interpreter finds prior art without rediscovering its own designs.

### 14 — Degradation is a feature (16:15–17:35) · `S14-A`, `S14-B`
Six failure modes with evidence. Dwell on the guarded lake writes: the trigger was a readonly-SQLite
error of my own making, but it exposed writes that could lose a round the user had waited two minutes
for. Now each tier write is guarded, ranking falls back in memory, and the interpreter appends a
*"Caveats from this run"* section. Row 4 is the failure that happened on its own.

### 15 — The web app *(cuttable)* (17:35–19:05) · `S15-A`
Seven frame kinds, one merged queue so node status and task progress arrive in one ordered stream.
The SSE buffer, because chunk boundaries land mid-frame. The Mol* lifecycle and the two things that
bit. The canvas was confirmed rendering in a browser on 2026-10-01; it was an open item until then,
because the deck was built without one. Say so — some of the room may have seen the earlier version.

### 16 — Running it (19:05–20:20) · `S16-A`
Five commands. The test-tier table and the split that matters: 93 offline, 6 live. Mention the two
real classifier bugs the 18 parametrized cases caught — *"what is the lead design?"* classified as a
design request, *"make it more stable"* classified as chat — because they argue for the rule-based
path being visible rather than masked by an LLM.

### 17 — What is real, and what is not (20:20–21:40)
Two columns, green and red, six rows each. **Do not compress this slide.** An audience that catches
you overclaiming stops believing everything else, and this deck's credibility is built on the caveats
being volunteered rather than extracted.

### 18 — Findings and asks (21:40–23:30)
Dark slide. Six numbered reproducibles in two columns, each naming file and line, then the question
in an orange-bordered box: **is `EXECUTION_BLOCK` meant to preserve the caller's context?** If yes,
it is a bug and `wrap_nodes=True` becomes the default. If no, flowgentic's node wrapping and
LangGraph's streaming are mutually exclusive and that sentence belongs in the README.

End there. No summary slide — the asks are the ending.

### B1 — The streaming experiment, in full *(backup)* · `S17-A`
What was tried, the symptom, the isolation, and the mechanism: `EXECUTION_BLOCK` runs the body on
asyncflow's loop, LangGraph's writer lives in a contextvar its executor sets around the node call,
crossing loops loses it, and LangGraph's helper degrades to a no-op rather than raising — which is
exactly why the failure is silent. Includes the fairness point: this is only a bug if
`EXECUTION_BLOCK` intends to propagate context.

### B2 — Standing up a local Orbit stack *(backup)*
The TLS trap (`--no-auth` disables ingress auth only), the SAN and key-mode requirements, the absent
HTTP topology route, and why subprocesses rather than `EmbeddedBroker` (it expects operator-placed
credentials in `~/.radical/orbit`, which a test must not create).

---

## Delivery notes

- **Open two terminals.** One in the repo root for `pytest` and `grep`; one for `python -m
  designagent`. Every anchor on a slide is greppable live, and this audience may ask you to.
- **Have `run.json` open.** Every number in the deck is in it, and "let me show you where that came
  from" is a stronger answer than repeating the number.
- **If the room goes deep on EXECUTION_BLOCK**, go to B1 and stay there. It is the most interesting
  finding and the one most likely to change their code.
- **If the room goes deep on ORBIT**, go to B2. Findings 3 and 4 are the ones worth their time;
  5 and 6 are documentation and should not eat the clock.
- **If someone asks why not `langgraph-api` / LangGraph Platform**: the brief specified flowgentic as
  the deployment substrate, and the interesting question — what happens to streaming when node bodies
  leave the runnable context — only exists because of that choice. Do not relitigate it on the clock.
- **If someone asks about ChemGraph's pins**: it pins `langgraph==1.2.11`, `langchain-core==1.6.1`
  and `pydantic==2.13.4`, and it pulls torch via mace-torch. That is why it is an optional extra
  rather than a base dependency.

## Before presenting

```sh
python3 slides/run_model.py          # regenerate run.json from data/lake
python3 slides/make_script.py        # regenerate DECK_SCRIPT.md from the deck's notes
NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js
pytest -q                            # 93 pass; the deck must not have touched the code
```

Then re-derive the code anchors. `CODE_FOR_DECK.md` carries the per-snippet first-line matcher, and
that file is exactly where drift hides.
