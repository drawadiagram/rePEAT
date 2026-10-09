# designagent walkthrough — speaking script

Companion to [`DECK_OUTLINE.md`](DECK_OUTLINE.md) (slide structure) and
[`CODE_FOR_DECK.md`](CODE_FOR_DECK.md) (the staged code blocks). Slide numbers and snippet IDs match
across all three. Derived against `main` @ `7e72b74`.

**This file is generated from the `addNotes` blocks in `build_deck.js`.** The deck is the single
source of the spoken prose, so a presenter reading from the notes pane and a presenter reading from
this file never diverge. Regenerate after editing the deck — the command is at the bottom.

**How to read it.** Plain prose is meant to be *said*. Anything in `[brackets]` is a stage direction
or a cumulative timestamp, and is excluded from the word counts.

**Pacing — measured, not estimated.** Counts are the actual spoken prose at 155 words/minute, a
realistic rate for technical material delivered with pauses.

| Slide | Spoken | | Slide | Spoken |
|---|---|---|---|---|
| 1. Title | 0.8 min | | 13. The seam: contract | 1.3 |
| 2. What it does | 0.9 | | 14. The seam: substrate | 1.5 |
| 3. The three dimensions | 1.2 | | 15. Orbit | 1.6 |
| 4. The constraint | 1.0 | | 16. What we have and have not measured (F5) | 2.2 |
| 5. Architecture (F2) | 1.1 | | 17. Degradation | 1.2 |
| 6. FUNCTIONALITY — divider | 0.4 | | 18. USABILITY — divider | 0.4 |
| 7. The loop (F1) | 1.4 | | 19. Frontend | 1.4 |
| 8. State | 1.2 | | 20. Deployed | 1.5 |
| 9. The lake (F4) | 1.3 | | 21. Running it | 1.8 |
| 10. The protocol node | 1.5 | | 22. Status | 1.4 |
| 11. PERFORMANCE — divider | 0.4 | | 23. Asks | 2.3 |
| 12. One campaign, measured (F3) | 1.6 | | | |

**Main path (slides 1–23): 4550 words = 29.4 minutes of speech.** Backups add 5.3 min if used.

| Order | What's in | Speech | Fits |
|---|---|---|---|
| **A · full** | slides 1–23, all three acts | **29.4** | a 30-minute slot with questions at the end |
| **B · questions inline** | drop 9 (the lake), 19 (frontend) and 20 (deployment) | **25.2** | the 30-minute slot as briefed, or a 25 with questions after |
| **C · twenty** | B, and drop 5 (the layer map) and 8 (state) | **22.9** | a hard 20 with questions after |

**Protect 3 (the three dimensions), 13, 14 and 15 (the seam), 16 (measured and not measured), 22
(status) and 23 (asks).** The seam and the asks are what this room came for; 3 and 16 are what make
the performance claims honest rather than decorative. The three dividers are 20 seconds each and
cheap to keep — if the clock goes, cut a content slide, not the frame.

Do **not** compress 16 or 22. An audience that catches you overclaiming stops believing the rest,
and those two slides are where this deck does its volunteering.

**Three things to say out loud even if nothing prompts them:** no stage of the protocol has run on a
cluster, so every figure in `specs.py` is an estimate (slides 6 and 22); the concurrency figure is a
mean in-flight depth and not a speedup, because there is no serial baseline (slides 12 and 16); and
the variants in the worked example are heuristic proposals, not ProteinMPNN samples (slide 2).

---

## 1. Title — *0.8 min*

[0:25] A code walk, not a results talk. What I built is a chatbot for protein redesign: you type a prompt, and behind it a LangGraph loop runs an actual campaign — structure lookups, folds, scoring, a provenance lake, artifacts you can open.

What is worth your time is the seam in the middle, where long work leaves this process: onto a rhapsody pool through flowgentic and asyncflow, or off the box entirely through ORBIT.

Every number on these slides comes from a real campaign still on disk, and the deck is in three acts — the three things I would want to know about anyone else's software: what new tasks it makes possible, what it costs to run, and what it is usable as.

## 2. What it does — *0.9 min*

[0:50] Here is the shape of one turn, and then what actually came out of it.

Left card, all measured: the prompt named 1OIL and nothing else. The agent resolved it to UniProt P22088, a Burkholderia cepacia lipase, 320 residues, pulled the crystal structure and the literature, proposed variants, folded them, scored them against the real structure, and wrote 114 score rows.

Right card, because you will ask and I would rather say it first. The variants are heuristic proposals — glycine to alanine, that kind of thing — not ProteinMPNN samples, because no HPC endpoint was attached to this run. The pLDDT improvement is inside the noise. And there was no API key, so every node ran its rule-based path.

So treat the science as a demo and the plumbing as the deliverable. The plumbing is what the rest of the talk is about.

## 3. The three dimensions — *1.2 min*

[0:50] The frame, in three questions, because they are the ones you would ask about any piece of scientific software and a deck organised by module answers none of them directly.

Functionality: what new tasks does it make possible. Performance: runtime efficiency and effectiveness — usually scalability and occupancy, but also scientific performance, and the interesting cases are where scale and scientific yield are not linear in each other. This system has one of those. Usability, and I do not mean the user interface: the use cases. What it lends itself to as an application, as a platform other people's credentials plug into, or as a component in someone else's workflow.

And the honest part up front: the middle column is the weak one. One host, a pool of four, one campaign, no GPU of our own. So that act ends with a slide about the measurements that do not exist, and the concurrency number I am about to quote is a mean depth, not a speedup. You can tell the difference, which is why I am saying it now rather than when someone asks.

## 4. The constraint — *1.0 min*

[1:05] If you remember one slide, this is the one, because everything after it is a consequence rather than a preference.

A chat interface has to answer in milliseconds. The work it triggers takes tens of seconds at best and hours at worst, and it might sit in a queue before it even starts. That gap is the entire design problem.

Five things fall out. Tasks return futures, not results — submit places work and comes back immediately. Duration stops being the interface's concern, so a forty-millisecond REST call and a queued batch job wear the same handle and differ only in capability flags. Status becomes a stream rather than a return value. State holds references rather than payloads, because LangGraph serializes every checkpoint. And every layer has a floor it degrades to.

The honest flip side is at the bottom: if the agent were allowed to block, you would not build most of this. You would call the tools inline and go home.

## 5. Architecture (F2) — *1.1 min*

[1:15] Eight bands. Read it top to bottom and the orange is yours.

Browser, then FastAPI with a single SSE chat endpoint. Then the LangGraph graph, checkpointed to SQLite. Then a small Deps object, which is the only thing a node closes over — settings, the task manager, the history lake, the artifact store.

Then the three task interfaces, then the substrate — flowgentic over asyncflow over a rhapsody concurrent backend on a four-worker pool — then remote: ORBIT solid, because since the ninth of October it has run jobs on a real cluster; Globus dashed, designed for and never run against anything live. Then the three lake tiers.

Two things in the rail. The rule that nodes reach out only through Deps is what makes 417 of the 441 tests run with no network and no pool. And exactly one band crosses into asyncflow: the local task interface. Everything above it is ordinary async Python, deliberately, so the middleware dependency stays in a layer I can stub.

## 6. FUNCTIONALITY — divider — *0.4 min*

[0:20] Act one, functionality: the new tasks this makes possible. Three claims — a prompt that runs a campaign, a multi-day cluster protocol turned into a conversation, and a campaign that becomes a queryable record. The weakest of the three is the second one: the protocol has two hundred and three tests and has never run on a cluster.

## 7. The loop (F1) — *1.4 min*

[1:55] Five nodes in the campaign loop, matching the spec: coordinator, design initializer, redesign orchestrator, analyst, interpreter. There is a sixth in the graph, the protocol node, and it is its own slide later — it answers the user and goes straight to END, so drawing it here would add a box and no information.

The coordinator is the only entry and the only re-entry point. It classifies the prompt and, where it can, answers straight from state without waking anything up — "what is the lead design?" costs one node visit.

The interesting edge is the red one. The analyst decides whether to go round again, and the test is whether the key metric actually improved, bounded by a round budget. In the measured campaign that fired once: round one improved on nothing, round two improved on round one, and then the budget and the interpreter took over.

Note how little static wiring there is. One static edge, START to coordinator. Everything else is a Command with a goto, which means the routing decision and the state write are the same atomic return — a node cannot update state and then fail to say where it went. The destinations tuple is a declaration so LangGraph can validate and draw the graph; it is not control flow.

## 8. State — *1.2 min*

[1:20] State is a TypedDict, not a pydantic model, because LangGraph checkpoints it and partial dict updates are the natural write unit from a node.

The eight keys from the spec are all there, each with an explicit reducer. Most are "replace" — stated explicitly rather than left to default, so the intent is readable. Three are not: artifacts append and dedupe by id, the worklist upserts so status can change in place, and warnings accumulate because they have to outlive the single turn that status lives for.

The right-hand side is a finding worth your time. I originally passed fold results through state. Six PDB files is about 320 kilobytes of checkpoint per turn, and it grows with the ensemble, so turn ten is carrying turn one's coordinates. The fix is that the orchestrator writes coordinates to a content-addressed blob and state carries a path.

What makes that a real lesson rather than a tidy-up is that nothing breaks when you get it wrong. It just gets slower every turn, forever. So it is pinned by a test that serializes the state and asserts the string "ATOM" never appears in it.

## 9. The lake (F4) — *1.3 min*

[1:35] Three tiers, exactly as the brief specified, and these are the real contents of the measured campaign.

Tier one is a Kuzu graph: six node tables, eight relationship tables, the whole provenance chain from campaign to reference to design to task to output to structure. One design decision there worth flagging — the properties columns hold JSON, so adding a task type needs no migration. That is a deliberate trade: queryability for evolvability, and I would make it again at this stage.

Tier two is SQLite: 114 score rows over ten metrics, seventeen rankings, two round analyses. The method worth pointing at is best_designs, which can exclude the current campaign — that is how the interpreter finds comparable prior work without rediscovering its own designs, and it is the new task this tier makes possible.

Tier three is Parquet plus a manifest: eleven rows from twelve designs, because the curation rules deduplicate sequences, and the manifest records the rules so the set is reproducible rather than merely present.

And only tier one needs a running process to read, which is why the script that mined these numbers could read the other two while the server held the Kuzu lock.

## 10. The protocol node — *1.5 min*

[1:15] This is the newest part, and the reason the rest of the machinery exists.

The enzyme redesign protocol is a multi-day cluster pipeline — model the target, trim it, search conservation, redesign with ProteinMPNN, select, fold with AlphaFold3, collect, report — written for a human at a terminal over several days.

It runs here one stage per turn, because the protocol has three points where it must stop for a person and there is no interrupt() anywhere in this repository. The turn boundary is the checkpoint instead: a stage sets awaiting and returns, and the next message answers it.

The code block is the bug I would otherwise have shipped. The route to the protocol has to come before classification, because "liu", "310,364" and "go" all classify as chat — which answers from session state and leaves the campaign waiting forever.

Bottom left is the decision I would flag to anyone building this. A transfer step declares inputs and outputs and gets rewritten into a temp-directory script on the local executor, no queue slot. A compute step declares neither and sets a working directory under the project, so its files persist for the next stage.

And the honest line: two hundred and three of the four hundred and seventeen offline tests cover this node, and no stage of it has run on a cluster, so every number in specs.py is an estimate.

## 11. PERFORMANCE — divider — *0.4 min*

[0:20] Act two, performance, and this is the act where I have the least to offer. Three claims: the loop never blocks, the seam is what buys that, and one figure in this deck is an actual curve I measured for this talk. The caveat is the whole act's caveat — there is no scaling study here, and the concurrency number is a mean depth, not a speedup.

## 12. One campaign, measured (F3) — *1.6 min*

[1:45] This is the real task ledger of the measured campaign, straight out of tier one of the lake, with submit and finish times as recorded.

Look at the orange fold rows. Six go out in the same instant and come back at 13.3, 13.3, 14.3, 17.9, 26.0 and 31.7 seconds — out of order, reaped as they land. That is the whole point of the futures design, and it is the one thing that would be invisible in a sequence diagram.

It also settles an argument against a default. Round two ran from 13.2 to 42.4 seconds, and flowgentic's RetryConfig defaults to a 30-second per-attempt timeout, so with the defaults roughly half of these folds would have been cancelled and silently retried. I'll come back to that.

The band underneath is the only aggregate I will quote: thirty-seven tasks, two hundred and ninety-eight seconds of handle lifetime inside a hundred and forty-six of wall clock, so mean concurrency of two, peaking at six. The deck computes it from these rows at build time, so it cannot go stale. But notice what it is not — a lifetime includes time queued inside a pool of four, and I have no serial baseline, so that two is a depth, not a speedup. The critical path is a single forty-two second fold.

And the bottom right is not contrived. ESM Atlas genuinely dropped one of six requests: that design came through as sequence-only with four metrics instead of eleven, the other five scored normally, and the user saw a warning.

## 13. The seam: contract — *1.3 min*

[2:00] Here is the whole vocabulary. Five verbs and a capability record.

Submit is the only abstract method, and its docstring is the contract: place the task and return immediately. Status, logs, result, cancel and close all have defaults that work for an in-process future, so a trivial interface is about ten lines.

The capability flags are where I tried to be honest rather than uniform. Globus Compute cannot tail a running task's logs — that is a real property of the service, not a gap in my adapter — so the flag says false and the manager simply does not start a log drain. No caller branches on which backend it got.

The bottom-left code is the piece I would defend hardest. When submission itself fails — no endpoint, unknown task name — you do not get an exception. You get a handle whose future is already failed. That single decision is why the gather path never raises and why a round survives a dead endpoint: there is exactly one shape to handle, and the failure arrives through the same channel as a task that failed after starting.

The state enum normalizes across vocabularies, because ORBIT and PSI/J each have their own and I did not want those leaking upward.

## 14. The seam: substrate — *1.5 min*

[2:10] This is the band that crosses out of my process, and it is five hops: a module-level task body, the flowgentic wrapper, asyncflow's engine, a rhapsody concurrent backend, a four-worker process pool.

Top left is the wrapper. The comment in it is doing real work: flowgentic's RetryConfig defaults to a 30-second per-attempt timeout with three attempts, which is right for a service call and wrong for a fold. We saw on the last slide that half the round-two folds exceed it. So we pass timeout_sec None and max_attempts one, and own retries ourselves.

Bottom left: submission has to return immediately, and flowgentic's FUNCTION_TASK wrapper is a coroutine function rather than a future factory, so we wrap it in ensure_future — that comment exists because I got it wrong first and blocked the loop. Top right, the pool's three constraints: bodies at module level, clients built inside the body, a main guard on the entry point.

And the red block is the first of the findings. That is verbatim flowgentic. The comment says "try to include aiohttp timeouts if present" and the handler says raise. So aiohttp becomes a hard requirement, and so does httpx twelve lines up. It fires whenever retryable_exceptions is left at its default, which is the empty tuple — the common case. I think that except clause wants to be a pass, and I would like to know if you agree.

## 15. Orbit — *1.6 min*

[2:20] The remote interface uses the raw clients rather than anything higher, on purpose: I wanted to see what the substrate actually offers.

Two clients, two different jobs. RhapsodyClient handles function and executable tasks and pushes status events at us. PSIJClient handles batch jobs, and it is the one place in either backend where you can tail a running job's output — get_job_status takes stdout and stderr byte offsets. That is what the whole log-streaming story is built on, and it works.

Two threading problems, neither a complaint: every client method is synchronous, so all 23 call sites go through to_thread, and push callbacks arrive on Orbit's own listener thread, so _dispatch hops them onto our loop with call_soon_threadsafe.

The red half is findings four and five. A completed task's terminal event carries state and exit code but not stdout, so my first version resolved futures with empty results — the fix re-fetches with get_task, and lets the event win on state while the fetch fills in output. And a failed job reports only a non-zero exit code; no reason reaches the client at all. So FAILED always synthesises an explanation from the exit code, then stderr, then the log tail. I would rather Orbit told me.

Status, plainly: twelve tests against a real localhost broker and endpoint, including one real ProteinMPNN run — and since the ninth of October, this same client code reaching a cluster login node through a public broker and coming back with Slurm job ids.

## 16. What we have and have not measured (F5) — *2.2 min*

[1:25] The slide I would want to see if I were you, so it is here rather than in an appendix.

The figure is the only curve in this deck, and I measured it for this talk. It is about the one data path this system has to a cluster: because the broker forwards neither outputs nor stdin_text — finding five, later — a job returns a file by printing it on stdout, gzipped, base64'd and framed. So I measured what that costs, driving the real wrap and collect functions locally.

Orange is incompressible data, about one and a third bytes of stdout per byte of payload, which is base64 doing what base64 does. Green is structure text, a third of a byte per byte, because a PDB gzips about four to one. Cost depends entirely on entropy.

The ceilings do not, and that is the finding. Outbound the cap is checked on the raw file size, so a two megabyte PDB is refused even though it would compress to two thirds of a megabyte, while a one megabyte random file sails through and costs one point four. Inbound there is no declared cap at all: exec fails with E2BIG at about one and a half megabytes of incompressible payload. Both caps are in the wrong units — that is going in our backlog, not on your list.

On the right, the ledger. Measured: in-flight depth, the critical path, checkpoint growth, this curve, the suite. Not measured and therefore not claimed: no scaling curve, no GPU occupancy, no multi-node run, no serial baseline.

At the bottom is where this stops being a systems question. When the protocol selects designs it says AlphaFold3 is about an hour of GPU each, and waits. Cost is linear in how many you fold; yield is not, because they are diversity-selected — the tenth is less like the other nine than the second was. That is the nonlinearity, and it is the piece of this story I would most like to measure and cannot.

## 17. Degradation — *1.2 min*

[1:15] Six failure modes, what each does, and the evidence that it does it.

The fifth row is the one to dwell on. During a live run I hit "attempt to write a readonly database" — my own fault, I deleted a data directory under a running server — but it exposed something real: the analyst wrote to the lake unguarded, so a storage problem could lose a round the user had waited two minutes for.

The fix is at the bottom. Each tier write is guarded independently, a failure falls back to ranking in memory, and the error goes onto a warnings channel that survives the turn, so the interpreter appends a "Caveats from this run" section rather than silently handing back a thinner answer. A test kills the lake mid-round and asserts the designs still come back.

And the fourth row is the one I did not have to arrange. ESM Atlas dropped a request during the measured campaign, the design came through sequence-only, the round scored the other five, and the warning surfaced. That is the whole mechanism working on a failure I did not choose.

## 18. USABILITY — divider — *0.4 min*

[0:20] Act three, usability — use cases, not user interface. Three modes: as an application, as a platform that holds other people's credentials and endpoints, and as a component something else drives. All three are built. The caveat is that nothing outside this repo has driven it as a component yet, so that third claim is the shape of the code rather than evidence.

## 19. Frontend — *1.4 min*

[1:10] Briefly, because the backend is what you came for.

The chat endpoint is a POST that returns an event stream — not EventSource, because the prompt goes in the body. Seven frame kinds. The thing I would point at is that a single asyncio queue merges LangGraph's own stream with TaskManager events, so node status and task progress arrive in one ordered stream rather than two the client has to interleave.

The client code is there for a bug class people hit constantly: a network chunk boundary lands mid-frame, so partial frames are held in a buffer until a blank line arrives, and a malformed frame is skipped rather than killing the stream.

The viewer is rcsb-molstar from the CDN, the loader cached on window, one viewer per mount. The usability point rather than the implementation one: what the agent sends the browser is a sanitized JSON view spec, never generated JavaScript, because the brief asked for the latter and that would put a prompt's output into the page as code. There is a backup slide on it.

And this was built without a browser available, so for a while the canvas was the one thing nobody had looked at — everything around it checked out, which is exactly when you convince yourself it is fine. Confirmed rendering on the first of October.

## 20. Deployed — *1.5 min*

[1:10] One slide on where this actually runs, because "it works on my laptop" is not an architecture claim.

Left to right: a browser, Caddy terminating TLS, the agent on loopback and the Orbit broker under systemd — all on one small VM — then the endpoint, a cluster login node registered to that broker across the internet. The two-way arrow is the only one, because push events come back over the same websocket.

Logins are off by default, and off they change nothing: with no auth object the user lookup returns None and every ownership check passes, which is why the rest of the suite needed no edit when they arrived.

The red card is the part I would want reviewed. A user's key must not end up written down, and there were two places it would have gone by default: LangGraph copies every string in configurable into checkpoint metadata, and task params are written to the lake. So the key travels in a ContextVar for the turn, a pool task is handed it as a call argument, and a test scans the checkpointer, the task snapshots and the data directory for it.

What this does not give you is a tenant boundary. The broker is shared — one ingress token, and anyone holding it can submit to every endpoint on it — so per-user isolation needs a broker per user, which is the next phase.

## 21. Running it — *1.8 min*

[1:00] How you actually use this, in all three senses. Four commands to run it, three to test it, and no configuration step that has to succeed first — with no API key every layer notes on the health endpoint what it could not do and keeps going.

The card on the right is the component claim made concrete. Nothing here needs the browser: twenty-two HTTP routes, of which the chat is the SSE stream and the rest are ordinary REST, plus a command line for config, credentials and accounts. And the record outlives the process — two of the three lake tiers are a SQLite file and a Parquet file, which is exactly how the script that mined the numbers for this deck read them while the server held the Kuzu lock.

The test split is the part I would defend. 417 of the 441 tests need no network, no process pool and no endpoint, which is a direct consequence of the Deps rule from the architecture slide: the suite hands a node an in-process task manager and a temp-directory lake, and the whole graph runs in seconds.

The twenty-four that need a substrate are split three ways and deselected separately: live brings up its own broker and endpoint as subprocesses, remote wants a real scheduler and an allocation, and llm spends money. Keeping them apart means the cheap tier can never drag in an expensive one. The live ones are not mocks of ORBIT; they are ORBIT, on localhost.

The top row is the protocol node, half the suite — deliberate, because it is the part with no endpoint to try it against, so the tests are the only thing holding it.

## 22. Status — *1.4 min*

[1:25] Same slide as before, cut three ways instead of two, so each dimension has to answer for itself. Green runs end to end against something real; red is built and has never run for real.

Functionality is the strongest column: the loop runs, all three tiers are written, the artifacts open. What has never run is the protocol on a cluster — two hundred and three tests, zero real runs — and nothing re-attaches to an in-flight job after a restart.

Performance: work genuinely leaves this process, ORBIT has run against a real cluster, ProteinMPNN is the real model. What is missing is every form of scaling evidence — one host, a pool of four I never varied, no multi-node run, no occupancy, no GPU of my own, which is why the AlphaFold3 figures are estimates.

Usability: deployed, holding other people's keys, drivable over HTTP and from a CLI. What is not there is a broker per user, so HPC is not yet a tenant boundary, and no second consumer has driven it as a component.

I made the red columns the same length as the green ones deliberately. If one of them were short you should be suspicious of it, and the one I would attack if I were in your seat is the middle one.

## 23. Asks — *2.3 min*

[1:50] Eight things I had to work around in two weeks on your stack. Each names a file and a line and CODE_FOR_DECK.md has the reproduction, so none of it needs taking on my word. They are grouped by what they cost: the left column produces a wrong answer or burns real time, the right column is paid by whoever adopts the stack after me.

Left column first. Three, four and five are ORBIT, and they are the ones I would most like fixed, because all three produce a silent wrong answer rather than an error: an empty result, a failure with no reason, and declared outputs that never arrive. Five is the expensive one — stdout becomes a job's only channel for a file, so this repo carries a staging protocol whose cost you saw two slides ago, and I would delete all of it the day the field is forwarded. Two is the retry default, which is a judgement call rather than a bug, but the wrong one for a workload where some tasks are models.

The right column is cheaper. One is, I think, a one-character fix: raise wants to be pass. Six is asyncflow's SIGTERM handler, which reports a completed shutdown and leaves the process running, so every stop script escalates to SIGKILL on a process holding a database lock. Seven and eight are documentation — I lost an afternoon to --no-auth, because no auth meaning no TLS is a reasonable reading.

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

## B3. Backup: Globus — *1.4 min*

[1:15] The brief said design for both Globus hpc-bridge and ORBIT, implement ORBIT first. So: one ABC, two implementations, and one of them still has never touched a live endpoint.

What the abstraction cost is worth saying, because "we abstracted over two backends" is usually a boast hiding a mess. It cost almost nothing, because the shared surface is tiny — connect, track, settle, and a log drain, 102 lines in total. The interesting work is irreducibly backend-specific: Orbit's push callbacks have no Globus analogue, and pretending otherwise would have meant inventing a polling shim nobody wanted.

So the base class's real job is not hiding differences. It is making them declarable, which is what the capability record on the right does. Globus Compute genuinely cannot tail a running task and genuinely cannot cancel after start; each flag carries the reason inline.

And it is testable without Globus at all, because hpc-bridge's runner takes an injectable executor factory. The suite hands it a plain executor. One detail there: argv is a list run with no shell on the test path, while the real ShellFunction API takes a string, so that path uses shlex.join — with a test proving a shell metacharacter stays data.

The claim I will defend is that the flags earned their keep and the base class merely did not get in the way.

## B4. Backup: local task agents — *1.4 min*

[1:10] The brief asked for Local Task Agents, and named two: a molecular visualization generator and ChemGraph.

The visualization one is where I deviated, and I want to be explicit about it. The brief says the agent "codes a browser-based visualization". Shipping LLM-written JavaScript into the viewer means a prompt can get code into the page, so I did not do that. The agent emits a constrained JSON view spec instead — structures, highlights, representation, colours — and a single React component is the only thing that ever touches Mol*. sanitize_spec repairs or drops every field, so a bad generation degrades to a plain cartoon rather than a broken pane.

I think that keeps the full expressive range of what a reviewer actually asks for — "colour the mutated residue, focus on it, show the rest as cartoon" — without executing anything.

The table is the honest status of all four. The visualizer runs and drew both leads in the measured campaign. ChemGraph is wired behind the same interface but has only ever been exercised by tests. ESMFold has twelve real predictions on disk. ProteinMPNN has a job spec and a FASTA parser and no endpoint, so what actually ran was the heuristic proposer — which is why the variants in this campaign are single-point substitutions.

---

## Regenerating this file

The prose lives in `build_deck.js`. After editing a slide's `addNotes`:

```sh
python3 slides/make_script.py        # rewrites DECK_SCRIPT.md from build_deck.js
```
