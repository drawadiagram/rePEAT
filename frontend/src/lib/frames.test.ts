/**
 * The wire, and what it becomes.
 *
 * Two things live here, both of which could break without failing to compile:
 * `streamChat`, which reassembles SSE frames out of arbitrary byte chunks, and
 * `handleFrame`, which is the whole translation from a frame to what the user
 * sees. Neither had a test before; the only automated check of the browser path
 * was `tsc`, and a blank page type-checks fine.
 */

import { describe, expect, it, vi } from "vitest";
import { handleFrame } from "../App";
import { streamChat } from "./api";
import type { AgentState, ChatMessage, Frame, StatusLine, TaskChip } from "./types";

/** A fetch whose body yields exactly these byte chunks, in order. */
function fetchYielding(chunks: string[]) {
  const encoder = new TextEncoder();
  let index = 0;
  return vi.fn().mockResolvedValue({
    ok: true,
    body: {
      getReader: () => ({
        read: async () =>
          index < chunks.length
            ? { done: false, value: encoder.encode(chunks[index++]) }
            : { done: true, value: undefined },
      }),
    },
  });
}

async function collect(chunks: string[]): Promise<Frame[]> {
  vi.stubGlobal("fetch", fetchYielding(chunks));
  const out: Frame[] = [];
  for await (const frame of streamChat("hi", "s1")) out.push(frame);
  vi.unstubAllGlobals();
  return out;
}

const FRAME_A = 'data: {"type": "status", "text": "Reading your request…", "node": "coordinator"}\n\n';
const FRAME_B = 'data: {"type": "message", "text": "Loaded 1OIL.", "node": "initializer"}\n\n';

describe("streamChat reassembles frames from arbitrary chunks", () => {
  it("reads whole frames", async () => {
    const frames = await collect([FRAME_A, FRAME_B]);
    expect(frames.map((f) => f.type)).toEqual(["status", "message"]);
  });

  it("holds a frame split mid-JSON", async () => {
    // The split lands inside the JSON object, which is what the buffer is for.
    const cut = 40;
    const frames = await collect([FRAME_A.slice(0, cut), FRAME_A.slice(cut)]);
    expect(frames).toHaveLength(1);
    expect((frames[0] as { text: string }).text).toBe("Reading your request…");
  });

  it("holds a frame split between the two newlines of its terminator", async () => {
    const body = FRAME_A.slice(0, -1); // everything but the final \n
    const frames = await collect([body, "\n", FRAME_B]);
    expect(frames.map((f) => f.type)).toEqual(["status", "message"]);
  });

  it("splits several frames arriving in one chunk", async () => {
    const frames = await collect([FRAME_A + FRAME_B]);
    expect(frames.map((f) => f.type)).toEqual(["status", "message"]);
  });

  it("drops a malformed frame without losing the ones around it", async () => {
    const frames = await collect([FRAME_A, "data: {not json\n\n", FRAME_B]);
    expect(frames.map((f) => f.type)).toEqual(["status", "message"]);
  });

  it("reports a failed request as an error frame rather than throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    const out: Frame[] = [];
    for await (const frame of streamChat("hi", "s1")) out.push(frame);
    vi.unstubAllGlobals();
    expect(out).toEqual([{ type: "error", message: "server returned 503" }]);
  });
});

// --- the reducers ---------------------------------------------------------

/** A stand-in for the pieces of App state that `handleFrame` writes. */
function harness() {
  const state = {
    messages: [] as ChatMessage[],
    agent: {} as AgentState,
    statuses: [] as StatusLine[],
    tasks: {} as Record<string, TaskChip>,
    paneOpened: false,
  };
  let streamingIndex: number | null = null;

  const sinks = {
    // The real one, copied from App.send, because the token path is the bit
    // that has to collapse many frames into one bubble.
    appendToken: (chunk: string, node?: string) => {
      const next = [...state.messages];
      if (streamingIndex === null || !next[streamingIndex]?.streaming) {
        next.push({ role: "assistant", content: chunk, streaming: true, node });
        streamingIndex = next.length - 1;
      } else {
        next[streamingIndex] = {
          ...next[streamingIndex],
          content: next[streamingIndex].content + chunk,
        };
      }
      state.messages = next;
    },
    setMessages: (fn: (prev: ChatMessage[]) => ChatMessage[]) => {
      state.messages = fn(state.messages);
    },
    setState: (fn: (prev: AgentState) => AgentState) => {
      state.agent = fn(state.agent);
    },
    setStatuses: (fn: (prev: StatusLine[]) => StatusLine[]) => {
      state.statuses = fn(state.statuses);
    },
    setTasks: (fn: (prev: Record<string, TaskChip>) => Record<string, TaskChip>) => {
      state.tasks = fn(state.tasks);
    },
    openPane: () => {
      state.paneOpened = true;
    },
  } as unknown as Parameters<typeof handleFrame>[1];

  return { state, sinks };
}

describe("handleFrame", () => {
  it("accumulates tokens into one bubble", () => {
    const { state, sinks } = harness();
    handleFrame({ type: "token", text: "Hel", node: "interpreter" }, sinks);
    handleFrame({ type: "token", text: "lo." }, sinks);
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].content).toBe("Hello.");
    expect(state.messages[0].node).toBe("interpreter");
  });

  it("attributes a matching reply instead of duplicating it", () => {
    // Without an LLM the text arrives once as a message frame; with one it has
    // already streamed, and the message frame only adds the authorship.
    const { state, sinks } = harness();
    handleFrame({ type: "token", text: "Loaded 1OIL." }, sinks);
    handleFrame(
      {
        type: "message",
        text: "Loaded 1OIL.",
        node: "initializer",
        source: "initializer:summary_line",
      },
      sinks,
    );
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].source).toBe("initializer:summary_line");
  });

  it("appends a reply that did not stream", () => {
    const { state, sinks } = harness();
    handleFrame(
      { type: "message", text: "Loaded 1OIL.", node: "initializer", source: "x" },
      sinks,
    );
    expect(state.messages).toEqual([
      { role: "assistant", content: "Loaded 1OIL.", node: "initializer", source: "x" },
    ]);
  });

  it("keeps the node on a status line and drops a repeat", () => {
    const { state, sinks } = harness();
    handleFrame({ type: "status", text: "Folding…", node: "orchestrator" }, sinks);
    handleFrame({ type: "status", text: "Folding…", node: "orchestrator" }, sinks);
    expect(state.statuses).toEqual([{ text: "Folding…", node: "orchestrator" }]);
  });

  it("merges artifacts by id and replaces everything else", () => {
    const { state, sinks } = harness();
    handleFrame(
      {
        type: "state",
        node: "analyst",
        state: { round: 1, artifacts: [{ id: "a", kind: "markdown", title: "A", url: "/a" }] },
      },
      sinks,
    );
    handleFrame(
      {
        type: "state",
        node: "interpreter",
        state: { round: 2, artifacts: [{ id: "b", kind: "molstar", title: "B", url: "/b" }] },
      },
      sinks,
    );
    expect(state.agent.round).toBe(2);
    expect(state.agent.artifacts?.map((a) => a.id)).toEqual(["a", "b"]);
    expect(state.paneOpened).toBe(true);
  });

  it("upserts a task chip and appends streamed log lines", () => {
    const { state, sinks } = harness();
    handleFrame(
      { type: "task", event: "submitted", id: "t1", task: "fold_sequence", state: "RUNNING" },
      sinks,
    );
    handleFrame({ type: "task", event: "log", id: "t1", text: "line-1\n" }, sinks);
    handleFrame({ type: "task", event: "log", id: "t1", text: "line-2\n" }, sinks);
    handleFrame({ type: "task", event: "finished", id: "t1", state: "DONE", elapsed: 4.2 }, sinks);

    expect(Object.keys(state.tasks)).toEqual(["t1"]);
    expect(state.tasks.t1.state).toBe("DONE");
    expect(state.tasks.t1.elapsed).toBe(4.2);
    expect(state.tasks.t1.task).toBe("fold_sequence"); // kept across updates
    expect(state.tasks.t1.log_tail).toBe("line-1\nline-2\n");
  });

  it("shows an error frame to the user", () => {
    const { state, sinks } = harness();
    handleFrame({ type: "error", message: "runtime is not ready" }, sinks);
    expect(state.messages[0].content).toContain("runtime is not ready");
  });
});
