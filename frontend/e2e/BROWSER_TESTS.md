# Browser user tests

Manual tests a person runs in a real browser, against a real backend with a real
endpoint attached. They exist because the automated tiers each stop short of the
thing a user actually relies on:

- `npm test` (jsdom) proves a component renders what it is handed.
- `npm run test:e2e` (Chromium) proves the page survives a canned stream.
- `pytest -m live` proves the transport carries a job and its files.

None of them answers *"can someone sitting in front of this tell that a real
model ran, and would they notice if it hadn't?"* That is a question about
attribution and honesty, and it is answered by reading the screen.

Every test below names the **function it verifies** — the user-facing capability,
not the code that implements it. Where a component is referenced it is named by
what it does, so these stay valid across refactors.

## Before starting

A backend with a remote endpoint attached, and the dev server in front of it:

```bash
./scripts/setup_mpnn.sh --check     # prints the command to export
DESIGNAGENT_ORBIT_LOCAL=true \
DESIGNAGENT_ORBIT_JOB_GPUS=0 \
DESIGNAGENT_MPNN_COMMAND="…" \
  .venv/bin/python -m designagent --port 8000
cd frontend && npm run dev          # http://localhost:5173
```

The dev server's proxy target is fixed, so the backend has to be on `:8000`.

**Standing assertion, every test:** the browser console shows no uncaught error
and the page never blanks. A dead tab is the failure the automated browser tier
was built around, and it is just as fatal here.

**With no model API key** the replies come from the rule-based summary path. That
is the normal configuration for these tests and changes nothing they assert.

---

## T1 · The configuration surface reports what is actually in force

**Verifies:** that a user can confirm which model command and execution settings
the running process holds, and where each came from, without reading the
environment or restarting.

1. Open the **credentials panel** (gear icon).
2. Find the **ProteinMPNN** group.

**Pass:** the command is shown with its real value and a `from .env` marker. The
sampling temperature is editable. The command and prologue are marked
`read only`.

**Fail:** the group is missing entirely — the panel's field list has drifted from
what the server reports, which has happened before and fails silently rather than
erroring.

## T2 · The write surface refuses settings that name a program to run

**Verifies:** that configuration which would be *executed* cannot be changed
through the browser, because the settings routes are reachable without
authentication from the local machine.

1. In the **ProteinMPNN** group, try to type in the command field, then the
   prologue field.
2. Change the sampling temperature and **Apply**.

**Pass:** both text fields are disabled and cannot be edited. The temperature
change applies and the panel reports it as set here afterwards.

**Fail:** either field accepts input. A value typed there is shell the server
runs on its next design round — locally as the server's user, and on a real
endpoint under the site's allocation.

## T3 · Remote execution availability is visible before it is relied on

**Verifies:** that the interface states whether remote work is possible, so a
user is never guessing about where their next round will run.

1. Look at the **header**.

**Pass:** a readiness indicator shows remote execution is attached.

**Fail:** it shows local-only. Every later test in this file will then silently
measure the fallback path instead of the real one. Stop and fix the endpoint
before continuing — this indicator is the precondition for T5 through T9.

## T4 · Starting a new session discards the previous campaign

**Verifies:** that session reset really resets, rather than reloading the prior
run's reference structure and lead design from the saved conversation.

1. Note any design results on screen.
2. Use the **new-session control** in the header.

**Pass:** the transcript, the activity chips and the artifact tabs all clear.

**Fail:** previous results reappear, or reappear after a page reload. A reload
restores the saved session on purpose, so *reloading is not a reset* — only the
control is. Skipping this is the most common way these tests produce a
misleading result, because a heuristic lead design from an earlier run carries
forward and later rounds then build on it.

## T5 · A redesign round routes to the endpoint, and says so while it runs

**Verifies:** remote job submission, and live attribution of where each unit of
work is running.

1. Send `load PDB 1UBQ`.
2. Send `redesign 1UBQ to improve thermostability`.
3. Watch the **activity chips** below the transcript as the round proceeds.

**Pass:** a chip appears named for the remote sequence-design task, carrying a
badge naming the remote interface. The many folding and scoring chips alongside
it carry no badge, because they ran locally and labelling them would say nothing.

**Fail:** the chip is named for the local proposer instead. That means the
endpoint was not attached when the round was planned, and what follows is the
heuristic path — see T10 for what that looks like.

## T6 · A remote task's output can be followed while it runs

**Verifies:** log streaming from the endpoint, which is the one capability no
local execution path has.

1. During or after the round, click the remote task's chip.

**Pass:** the expanded chip shows output from the job.

**Fail:** nothing appears. Harmless on its own — a very fast job can finish
before any output is polled — but a populated log is positive proof the work left
this machine, so an empty one proves nothing either way.

## T7 · The finished round records where each task ran

**Verifies:** durable attribution — that the interface each task used is written
into the run's record, not just shown transiently in the UI.

1. Open the **session summary** artifact tab.
2. Find the section listing the tasks the round ran.

**Pass:** the sequence-design task is listed with the remote interface named
beside it, and a completed state.

**Fail:** it is absent, or named with a local interface. This section is read back
from the stored run record rather than from the page's own state, so it is the
one place that survives a reload and disagreement with T5 is meaningful.

## T8 · The model's own scores become a ranking metric

**Verifies:** that the sequence designer's confidence scores are parsed out of its
output and carried through scoring, rather than discarded.

1. Open the **ensemble** artifact tab.

**Pass:** a column of per-design model scores is present, alongside the structural
confidence and the other sequence metrics. Column headers sort.

**Fail:** no such column. The score exists nowhere else in the system, so its
absence means the output was never parsed — which previously looked like success
because the round still produced designs.

## T9 · A real round is distinguishable from a heuristic one by reading it

**Verifies:** provenance honesty — that a user can tell a learned model's output
from a rule table's without being told.

1. In the **ensemble** tab, read the mutations column.
2. In the reply, read the sentence explaining why the lead design was chosen.

**Pass:** designs carry **many** substitutions each, and the explanation names a
sample number and a model score.

**Fail:** one substitution per design, and an explanation that gives a chemical
reason instead — see T10.

**Expect fewer mutations in later rounds** and do not read it as a regression.
The count is measured against the round's own parent design, while each round
re-samples the original reference structure. A round-one design sits 30-ish
substitutions from the reference; a round-two design can sit a third of that from
its parent while still being just as far from the reference. The two numbers
answer different questions and the interface does not currently distinguish them.

## T10 · With no endpoint, the round still completes — and this is what that looks like

**Verifies:** graceful degradation, and documents the **honesty gap** a user
should know about.

1. In the credentials panel, turn off the **development broker** toggle and
   **Apply**. The readiness indicator should drop to local-only.
2. Start a new session and run the same two prompts.

**Pass:** the round completes and produces ranked designs. The chip is named for
the local proposer. Each design carries exactly **one** substitution, the
explanation gives a chemical reason such as *"glutamine to glutamate avoids
deamidation"*, and there is **no** model-score column.

**Known gap — not a test failure.** The reply does not state that these are
rule-based proposals. The generator labels its own output, but that label stays
in the task record and never reaches the text a user reads. So the only signals
are the four above, all of which require knowing to look. Treat a round with one
substitution per design and no model-score column as heuristic regardless of what
the prose implies.

3. Turn the toggle back on, or reset the panel to the environment, and confirm the
   readiness indicator returns.

## T11 · A failed remote round does not lose the round

**Verifies:** that a remote failure degrades to a working answer and explains
itself, rather than ending the turn empty-handed.

Needs a broken endpoint rather than an absent one, so it is awkward to stage from
the browser alone: point the model command at something that exits non-zero and
restart the backend.

**Pass:** the round still produces designs, and the reply carries a
**caveats** block naming what went wrong with the remote step.

**Fail:** the reply says no candidate designs were produced. That was the original
behaviour of the remote path and is the regression this guards.

## T12 · The turn can account for itself

**Verifies:** per-turn attribution — that a user can see which stages ran and
which component wrote the reply.

1. Expand the **how this answer was made** disclosure under the reply.

**Pass:** each stage is listed with its duration, and a line names the component
that authored the reply text.

**Fail:** nothing expands, or the authoring line is absent. Note this reports who
wrote the *reply*, never which generator proposed each design — that is the gap
T9 and T10 work around.

---

## Not currently checkable in the browser

Recorded so nobody spends time looking:

- **Which generator proposed an individual design.** It is carried end to end and
  displayed nowhere; the ensemble table and the summary document both omit it.
  T9's mutation count is the available proxy.
- **Caveats as anything other than reply text.** There is no banner or toast, and
  only the first few are included in the reply, so a degraded turn can look
  ordinary to a skimming reader.
- **Which stage submitted a given task.**
- **A task list.** Only the transient chips and whatever the saved session
  restores; there is no table to sort or filter.
