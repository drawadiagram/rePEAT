# designagent walkthrough — speaking script

Companion to [`DECK_OUTLINE.md`](DECK_OUTLINE.md) (slide structure) and
[`CODE_FOR_DECK.md`](CODE_FOR_DECK.md) (the staged code blocks). Slide numbers and snippet IDs match
across all three. Derived against `main` @ `e8467e6`.

**This file is generated from the `addNotes` blocks in `build_deck.js`.** The deck is the single
source of the spoken prose, so a presenter reading from the notes pane and a presenter reading from
this file never diverge. Regenerate after editing the deck — the command is at the bottom.

**How to read it.** Plain prose is meant to be *said*. Anything in `[brackets]` is a stage direction
or a cumulative timestamp, and is excluded from the word counts.

**Pacing — measured, not estimated.** Counts are the actual spoken prose at 155 words/minute, a
realistic rate for technical material delivered with pauses.

| Slide | Spoken | | Slide | Spoken |
|---|---|---|---|---|
| 1. Title | 0.8 min | | 10. Orbit | 1.6 |
| 2. What it does | 0.9 | | 11. Globus | 1.4 |
| 3. The constraint | 1.0 | | 12. Local task agents | 1.4 |
| 4. The loop (F1) | 1.1 | | 13. The lake (F4) | 1.3 |
| 5. Architecture (F2) | 1.1 | | 14. Degradation | 1.4 |
| 6. State | 1.2 | | 15. Frontend | 1.7 |
| 7. One campaign, measured (F3) | 1.3 | | 16. Running it | 1.2 |
| 8. The seam: contract | 1.3 | | 17. Status | 1.3 |
| 9. The seam: substrate | 1.5 | | 18. Asks | 1.8 |

**Main path (slides 1–18): 3641 words = 23.5 minutes of speech.** Backups add 2.5 min if used.

| Order | What's in | Speech | Fits |
|---|---|---|---|
| **A · full** | slides 1–18 | **23.5** | a 30-minute slot, questions inline |
| **B · twenty-five** | drop 12 (task agents) and 15 (frontend) | **20.5** | a 25-minute slot with real Q&A |
| **C · twenty** | B, and fold 11 (Globus) into 10 as one sentence | **~19.0** | a hard 20 with questions after |

**Protect 8, 9, 10 and 18.** Those are the seam and the asks, and they are what this room came for.
Slides 12 and 15 are the designated cuts: the visualization-agent decision and the SSE details are
both recoverable in one sentence elsewhere. Do **not** compress 17 (status) — an audience that
catches you overclaiming stops believing the rest, and this deck's whole bet is that the honesty is
the credibility.

**Two things to say out loud even if nothing prompts them:** no HPC endpoint has ever run a task for
this agent (slide 17), and the variants in the worked example are heuristic proposals rather than
ProteinMPNN samples (slide 2).

---

## 1. Title — *0.8 min*

[0:25] This is a code walk, not a results talk. The thing I built is a chatbot for protein redesign: you type a prompt, and behind it a LangGraph loop goes and runs an actual campaign — structure lookups, folds, scoring, a provenance lake, and artifacts you can open.

The reason it's worth your time is the seam in the middle. Long work leaves this process: it goes onto a rhapsody process pool through flowgentic and asyncflow, or off the box entirely through ORBIT. Everything about the design follows from one constraint, which is that a chat interface cannot block on a protein fold.

Every number on these slides comes from a real campaign that is still on disk. I'll be explicit about what has never run.

## 2. What it does — *0.9 min*

[0:50] Here is the shape of one turn, and then what actually came out of it.

Left card, all measured: the prompt named 1OIL and nothing else. The agent resolved it to UniProt P22088, a Burkholderia cepacia lipase, 320 residues, pulled the crystal structure and the literature, proposed variants, folded them, scored them against the real structure, and wrote 114 score rows.

Right card, because you will ask and I would rather say it first. The variants are heuristic proposals — glycine to alanine, that kind of thing — not ProteinMPNN samples, because no HPC endpoint was attached to this run. The pLDDT improvement is inside the noise. And there was no API key, so every node ran its rule-based path.

So treat the science as a demo and the plumbing as the deliverable. The plumbing is what the rest of the talk is about.

## 3. The constraint — *1.0 min*

[1:05] If you remember one slide, this is the one, because everything after it is a consequence rather than a preference.

A chat interface has to answer in milliseconds. The work it triggers takes tens of seconds at best and hours at worst, and it might sit in a queue before it even starts. That gap is the entire design problem.

Five things fall out. Tasks return futures, not results — submit places work and comes back immediately. Duration stops being the interface's concern, so a forty-millisecond REST call and a queued batch job wear the same handle and differ only in capability flags. Status becomes a stream rather than a return value. State holds references rather than payloads, because LangGraph serializes every checkpoint. And every layer has a floor it degrades to.

The honest flip side is at the bottom: if the agent were allowed to block, you would not build most of this. You would call the tools inline and go home.

## 4. The loop (F1) — *1.1 min*

[2:05] Five nodes, matching the spec: coordinator, design initializer, redesign orchestrator, analyst, interpreter.

The coordinator is the only entry and the only re-entry point. It classifies the prompt and, where it can, answers straight from state without waking anything up — "what is the lead design?" costs one node visit.

The interesting edge is the red one. The analyst decides whether to go round again, and the test is whether the key metric actually improved, bounded by a round budget. In the measured campaign that fired once: round one improved on nothing, round two improved on round one, and then the budget and the interpreter took over.

Note how little static wiring there is. One static edge, START to coordinator. Everything else is a Command with a goto, which means the routing decision and the state write are the same atomic return — a node cannot update state and then fail to say where it went. The destinations tuple is a declaration so LangGraph can validate and draw the graph; it is not control flow.

## 5. Architecture (F2) — *1.1 min*

[1:15] Eight bands. Read it top to bottom and the orange is yours.

Browser, then FastAPI with a single SSE chat endpoint. Then the LangGraph graph, checkpointed to SQLite. Then a small Deps object, which is the only thing a node closes over — settings, the task manager, the history lake, the artifact store.

Then the three task interfaces. Then the substrate, which is flowgentic over asyncflow over a rhapsody concurrent backend on a four-worker process pool. Then remote: ORBIT solid-but-dotted, meaning it genuinely works and has only ever met a localhost broker; Globus dashed, meaning designed for and not implemented against anything live. Then the three lake tiers.

Two things in the rail. The rule that nodes only ever reach through Deps is what makes 83 of the 89 tests run with no network and no pool. And the band that crosses into asyncflow is exactly one: the local task interface. Everything above it is ordinary async Python, which is deliberate — I wanted the middleware dependency confined to a layer I could swap or stub.

## 6. State — *1.2 min*

[1:20] State is a TypedDict, not a pydantic model, because LangGraph checkpoints it and partial dict updates are the natural write unit from a node.

The eight keys from the spec are all there, each with an explicit reducer. Most are "replace" — stated explicitly rather than left to default, so the intent is readable. Three are not: artifacts append and dedupe by id, the worklist upserts so status can change in place, and warnings accumulate because they have to outlive the single turn that status lives for.

The right-hand side is a finding worth your time. I originally passed fold results through state. Six PDB files is about 320 kilobytes of checkpoint per turn, and it grows with the ensemble, so turn ten is carrying turn one's coordinates. The fix is that the orchestrator writes coordinates to a content-addressed blob and state carries a path.

What makes that a real lesson rather than a tidy-up is that nothing breaks when you get it wrong. It just gets slower every turn, forever. So it is pinned by a test that serializes the state and asserts the string "ATOM" never appears in it.

## 7. One campaign, measured (F3) — *1.3 min*

[1:45] This is the real task ledger of the measured campaign, straight out of tier one of the lake, with submit and finish times as recorded.

Look at the orange fold rows. Six go out in the same instant and come back at 13.3, 13.3, 14.3, 17.9, 26.0 and 31.7 seconds — out of order, reaped as they land. That is the whole point of the futures design, and it is the one thing that would be invisible in a sequence diagram.

It also settles an argument in your favour and against a default. Round two ran from 13.2 to 42.4 seconds. flowgentic's RetryConfig defaults to a 30-second per-attempt timeout with three attempts, so with the defaults roughly half of these folds would have been cancelled and silently retried. I'll come back to that.

The blue lookups show the initializer's concurrency: PDB and UniProt together, a cross-reference follow-up, then structure and literature together.

And the bottom right is not a contrived example. ESM Atlas genuinely dropped one of six requests. That design came through as sequence-only, got four metrics instead of eleven, the other five scored normally, and the user saw a warning. I did not have to construct a failure to talk about degradation.

## 8. The seam: contract — *1.3 min*

[2:00] Here is the whole vocabulary. Five verbs and a capability record.

Submit is the only abstract method, and its docstring is the contract: place the task and return immediately. Status, logs, result, cancel and close all have defaults that work for an in-process future, so a trivial interface is about ten lines.

The capability flags are where I tried to be honest rather than uniform. Globus Compute cannot tail a running task's logs — that is a real property of the service, not a gap in my adapter — so the flag says false and the manager simply does not start a log drain. No caller branches on which backend it got.

The bottom-left code is the piece I would defend hardest. When submission itself fails — no endpoint, unknown task name — you do not get an exception. You get a handle whose future is already failed. That single decision is why the gather path never raises and why a round survives a dead endpoint: there is exactly one shape to handle, and the failure arrives through the same channel as a task that failed after starting.

The state enum normalizes across vocabularies, because ORBIT and PSI/J each have their own and I did not want those leaking upward.

## 9. The seam: substrate — *1.5 min*

[2:10] This is the band that crosses out of my process, and it is five hops: a module-level task body, the flowgentic wrapper, asyncflow's engine, a rhapsody concurrent backend, a four-worker process pool.

Top left is the wrapper. The comment in it is doing real work: flowgentic's RetryConfig defaults to a 30-second per-attempt timeout with three attempts, which is right for a service call and wrong for a fold. We saw on the last slide that half the round-two folds exceed it. So we pass timeout_sec None and max_attempts one, and own retries ourselves.

Bottom left: submission has to return immediately, and the flowgentic wrapper for FUNCTION_TASK is a coroutine function rather than a future factory, so we wrap it in ensure_future. That comment exists because I got it wrong first and blocked the loop.

Top right: the pool imposes three real constraints. Bodies at module level, clients built inside the body, and a main guard on the entry point.

And the red block is the first of the findings. That is verbatim flowgentic. The comment says "try to include aiohttp timeouts if present" and the handler says raise. So aiohttp becomes a hard requirement, and so does httpx twelve lines up. It fires whenever retryable_exceptions is left at its default, which is the empty tuple — the common case. I think that except clause wants to be a pass, and I would like to know if you agree.

## 10. Orbit — *1.6 min*

[2:20] The remote interface uses the raw clients rather than anything higher, on purpose: I wanted to see what the substrate actually offers.

Two clients, two different jobs. RhapsodyClient handles function and executable tasks and pushes status events at us. PSIJClient handles batch jobs, and it is the one place in either backend where you can tail a running job's output — get_job_status takes stdout and stderr byte offsets. That is what the whole log-streaming story is built on, and it works.

Two threading problems. Every client method is synchronous and blocking, so all 23 call sites go through to_thread. And push callbacks arrive on Orbit's own listener thread, so _dispatch hops them onto our loop with call_soon_threadsafe. Neither is a complaint; they are just facts you need to know before you build on this.

The red half is findings four and five. A completed task's terminal event carries state and exit code but not stdout, so my first version resolved futures with empty results — the fix re-fetches with get_task, and lets the event win on state while the fetch fills in output. And a failed job reports only a non-zero exit code; no reason reaches the client at all. So FAILED always synthesises an explanation from the exit code, then stderr, then the log tail. I would rather Orbit told me.

Status, plainly: six tests against a real localhost broker and endpoint, covering push states, incremental tailing, a failing job and cancelling a running one. It has never met a scheduler.

## 11. Globus — *1.4 min*

[1:15] The brief said design for both Globus hpc-bridge and ORBIT, implement ORBIT first. So: one ABC, two implementations, and one of them has never touched a live endpoint.

What the abstraction cost is worth saying, because "we abstracted over two backends" is usually a boast hiding a mess. It cost almost nothing, because the shared surface is tiny — connect, track, settle, and a log drain, 102 lines in total. The interesting work is irreducibly backend-specific: Orbit's push callbacks have no Globus analogue, and pretending otherwise would have meant inventing a polling shim nobody wanted.

So the base class's real job is not hiding differences. It is making them declarable, which is what the capability record on the right does. Globus Compute genuinely cannot tail a running task and genuinely cannot cancel after start; each flag carries the reason inline.

And it is testable without Globus at all, because hpc-bridge's runner takes an injectable executor factory. The suite hands it a plain executor. One detail there: argv is a list run with no shell on the test path, while the real ShellFunction API takes a string, so that path uses shlex.join — with a test proving a shell metacharacter stays data.

The claim I will defend is that the flags earned their keep and the base class merely did not get in the way.

## 12. Local task agents — *1.4 min*

[1:10] The brief asked for Local Task Agents, and named two: a molecular visualization generator and ChemGraph.

The visualization one is where I deviated, and I want to be explicit about it. The brief says the agent "codes a browser-based visualization". Shipping LLM-written JavaScript into the viewer means a prompt can get code into the page, so I did not do that. The agent emits a constrained JSON view spec instead — structures, highlights, representation, colours — and a single React component is the only thing that ever touches Mol*. sanitize_spec repairs or drops every field, so a bad generation degrades to a plain cartoon rather than a broken pane.

I think that keeps the full expressive range of what a reviewer actually asks for — "colour the mutated residue, focus on it, show the rest as cartoon" — without executing anything.

The table is the honest status of all four. The visualizer runs and drew both leads in the measured campaign. ChemGraph is wired behind the same interface but has only ever been exercised by tests. ESMFold has twelve real predictions on disk. ProteinMPNN has a job spec and a FASTA parser and no endpoint, so what actually ran was the heuristic proposer — which is why the variants in this campaign are single-point substitutions.

## 13. The lake (F4) — *1.3 min*

[1:35] Three tiers, exactly as the brief specified, and these are the real contents of the measured campaign.

Tier one is a Kuzu graph: six node tables, eight relationship tables, the whole provenance chain from campaign to reference to design to task to output to structure. One design decision there worth flagging — the properties columns hold JSON, so adding a task type needs no migration. That is a deliberate trade: queryability for evolvability, and I would make it again at this stage.

Tier two is SQLite: 114 score rows over ten metrics, seventeen ranking rows, two round analyses. The method I would point at is best_designs, which can exclude the current campaign — that is how the interpreter finds comparable prior work without rediscovering its own designs.

Tier three is Parquet plus a manifest. Eleven rows from twelve designs, because the curation rules deduplicate sequences, and the manifest records the rules so the set is reproducible rather than just present.

The bottom line is a small thing I only noticed when building this deck: only tier one needs a running process to read. Tiers two and three are just files, which is why the script that produced these numbers could read them while the server held the Kuzu lock.

## 14. Degradation — *1.4 min*

[1:15] Six failure modes, what each does, and the evidence that it does it.

The one I want to dwell on is the fifth row. During a live run I hit "attempt to write a readonly database" from SQLite. The cause was mine — I deleted a data directory under a running server — so it was not a product bug. But it exposed something real: the analyst was writing to the lake unguarded, which meant a storage problem could lose a round of work the user had already waited two minutes for.

The fix is the code at the bottom. Each tier write is guarded independently, a failure falls back to ranking in memory, and the error goes into a warnings channel that survives the turn. The interpreter then appends a "Caveats from this run" section to its reply, so the user is told rather than silently given a thinner answer. There is a test that kills the lake mid-round and asserts the designs still come back.

And the fourth row is the one I did not have to arrange. ESM Atlas dropped a request during the measured campaign, the design came through sequence-only, the round scored the other five, and the warning surfaced. That is the whole mechanism working on a failure I did not choose.

## 15. Frontend — *1.7 min*

[1:10] Briefly, because the backend is what you came for.

The chat endpoint is a POST that returns an event stream — not EventSource, because the prompt goes in the body. Seven frame kinds. The thing I would point at is that a single asyncio queue merges LangGraph's own stream with TaskManager events, so node status and task progress arrive in one ordered stream rather than two the client has to interleave.

The client code is there because of a bug class people hit constantly: a network chunk boundary lands in the middle of an SSE frame, so you hold partial frames in a buffer until you see a blank line. And a malformed frame is skipped rather than killing the stream.

The viewer: rcsb-molstar from the CDN, loader cached on window so one fetch serves every mount, one viewer per mount, resize observed. Two things bit me — createComponent takes no colour, so colours have to go through a plugin call to update the representation theme; and the analyst inlines coordinates as a JSON artifact so the viewer does not need a second authenticated fetch.

Last line: this was built without a browser available, so for a while the canvas was the one thing nobody had actually looked at — everything around it checked out, which is exactly the situation where you convince yourself it is fine. It was confirmed rendering on the first of October. I am mentioning it because it was on the status slide as an open item until then, and some of you may have seen that version.

## 16. Running it — *1.2 min*

[1:00] Three commands to run it, two to test it, and no configuration step that has to succeed first.

The test split is the part I would defend. 83 of the 89 tests need no network, no process pool and no endpoint. That is a direct consequence of the rule from the architecture slide — nodes only reach the outside through Deps — so the suite hands them an in-process task manager and a temp-directory lake and the whole graph runs in under a second.

The six that genuinely need a substrate are marked live and bring up their own broker and endpoint as subprocesses. They are not mocks of ORBIT; they are ORBIT, on localhost.

What I would call out in the middle column is that test_graph covers the classifier with eighteen parametrized cases. That is there because two real bugs hid in it: "what is the lead design?" classified as a design request because it contains the word design, and "make it more stable" classified as chat because it matched nothing at all. Both are the kind of bug an LLM path would have masked and the rule path makes visible.

## 17. Status — *1.3 min*

[1:20] Two columns. Left is what runs end to end against something real, right is what is built and has never run for real.

Left, briefly: the loop, two rounds, routed by the improvement test. The query interface against live RCSB, UniProt and Europe PMC. Twelve real folds on the rhapsody pool, six at a time, reaped out of order. All three lake tiers with the counts you saw. Four artifacts on disk. Streaming working.

Right is the column that matters. ProteinMPNN is a job spec and a FASTA parser; with no endpoint, the orchestrator falls back to a heuristic proposer, and the output says so in a note field rather than quietly implying ProteinMPNN ran. The Globus adapter has never met a live endpoint. ORBIT works, against localhost only — which proves the client path and proves nothing about a queue. ChemGraph has been exercised by tests and never by a campaign.

And the bottom line is the one I would put on a slide even if nobody asked: no HPC endpoint has ever run a task for this agent. Everything I have said about the remote path is a statement about the client, not about HPC.

## 18. Asks — *1.8 min*

[1:50] I built on your stack for two weeks and these are the six things I had to work around. Each one names a file and a line, and the deck's CODE_FOR_DECK.md has the reproduction, so none of this needs to be taken on my word.

One and two are flowgentic. The aiohttp import is, I think, a one-character fix: raise wants to be pass. The retry defaults are a judgement call rather than a bug, but I would argue the default is wrong for the workload flowgentic is most likely to be used for — if you are wrapping agent tasks, some of them are models.

Three through six are ORBIT, and three and four are the ones I would most like fixed, because both of them produce a silent wrong answer rather than an error: an empty result, and a failure with no reason.

Five and six are documentation. I lost an afternoon to --no-auth, because it is a reasonable reading that no auth means no TLS.

And then the question, which is the actual ask. The approved design for this project had every graph node wrapped as an EXECUTION_BLOCK. It ships disabled, because a wrapped node runs on asyncflow's loop, outside LangGraph's runnable context, so the stream writer raises and every status event is dropped — silently. The graph still completes. The chat just goes quiet.

So: is EXECUTION_BLOCK meant to preserve the caller's context? If it is, that is a bug worth fixing and I flip my default back. If it is not, then flowgentic's node wrapping and LangGraph's streaming are mutually exclusive, and I think that sentence belongs in the README, because I would have liked to read it.

## B1. Backup: streaming — *1.3 min*

Backup, for the EXECUTION_BLOCK discussion if it goes deep.

The approved plan said wrap every node. I did, and the graph worked perfectly while the chat went completely silent — no error, no warning, just no status.

Isolating it took one plain node and one wrapped node, each asking for a stream writer and emitting one event. The plain one works. The wrapped one raises "Called get_config outside of a runnable context" and emits nothing.

The mechanism: EXECUTION_BLOCK hands the body to asyncflow's engine, which runs it on its own loop. LangGraph's stream writer lives in a contextvar that its executor sets around the node call. Cross loops and you lose the contextvar. And LangGraph's helper degrades to a no-op rather than raising, which is exactly why the failure is invisible.

Important fairness point: this is only a flowgentic bug if EXECUTION_BLOCK intends to propagate caller context. I do not know that it does, which is why slide 18 asks rather than asserts.

What ships: wrap_nodes false, the path kept and documented. Tasks are still wrapped, so the process pool is genuinely in use — task bodies never touch the stream writer, so nothing is lost there.

## B2. Backup: local Orbit — *1.3 min*

Backup, for anyone who wants to reproduce the ORBIT work.

The thing that cost me most of an afternoon is the first comment. --no-auth disables the ingress token check only. The broker always serves TLS and refuses to start without a cert and key, so with --no-auth alone it exits at startup. The fixture generates a throwaway self-signed pair, with a SAN covering 127.0.0.1 because that is what the client connects to, and chmods the key to 600 because the broker rejects anything looser.

Readiness was the other trap. I went looking for an HTTP topology endpoint and got a 307 then a 404 saying endpoint 'topology' unknown — because the gateway reads it as a plugin name. There is no such route. So the fixture waits on the endpoint's own log line, and the authoritative check moved to where it belongs: the client polls rt.topology() until the endpoint appears, bounded by a timeout, because topology propagates asynchronously.

Subprocesses rather than the embedded broker, because the embedded one expects operator-placed credentials in the user's home directory and a test has no business creating those.

What it buys is the four assertions on the right, all against real processes.

---

## Regenerating this file

The prose lives in `build_deck.js`. After editing a slide's `addNotes`:

```sh
python3 slides/make_script.py        # rewrites DECK_SCRIPT.md from build_deck.js
```
