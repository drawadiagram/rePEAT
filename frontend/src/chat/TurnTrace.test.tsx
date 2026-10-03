/** The disclosure that answers "which node wrote this?". */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { ChatMessage } from "../lib/types";
import TurnTrace from "./TurnTrace";

afterEach(cleanup);

const TRACE: ChatMessage = {
  role: "assistant",
  content: "…",
  source: "interpreter:_rule_based_summary",
  statuses: [{ text: "Summarizing the session…", node: "interpreter" }],
  trace: [
    { node: "coordinator", ms: 4, goto: "orchestrator", intent: "design" },
    { node: "orchestrator", ms: 967, goto: "analyst", round: 1, n_worklist: 7 },
    {
      node: "interpreter",
      ms: 177,
      goto: "end",
      reply_source: "interpreter:_rule_based_summary",
      n_messages: 1,
    },
  ],
};

describe("TurnTrace", () => {
  it("renders nothing when the turn left no record", () => {
    const { container } = render(
      <TurnTrace message={{ role: "assistant", content: "hi" }} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("is collapsed until asked", async () => {
    render(<TurnTrace message={TRACE} />);
    expect(screen.queryByText("orchestrator")).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: /how this answer was made/ }));
    expect(screen.getByText("orchestrator")).toBeTruthy();
  });

  it("shows the path, the timings and the total", async () => {
    render(<TurnTrace message={TRACE} />);
    const toggle = screen.getByRole("button", { name: /how this answer was made/ });
    // 4 + 967 + 177 = 1148ms, shown in seconds once past 1s.
    expect(toggle.textContent).toContain("1.15s");

    await userEvent.click(toggle);
    // getAllByText: a node name also appears beside its progress line, so the
    // query is deliberately not asserting uniqueness.
    for (const node of ["coordinator", "orchestrator", "interpreter"]) {
      expect(screen.getAllByText(node).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("967ms")).toBeTruthy();
    expect(screen.getByText(/intent=design/)).toBeTruthy();
    expect(screen.getByText(/7 task\(s\)/)).toBeTruthy();
  });

  it("says plainly that a rule wrote the reply", async () => {
    render(<TurnTrace message={TRACE} />);
    await userEvent.click(screen.getByRole("button", { name: /how this answer was made/ }));
    expect(screen.getByText("interpreter:_rule_based_summary")).toBeTruthy();
    expect(screen.getByText(/a rule, no model involved/)).toBeTruthy();
  });

  it("…and that the model wrote it when it did", async () => {
    render(
      <TurnTrace
        message={{
          ...TRACE,
          source: "interpreter:llm",
          trace: [{ node: "interpreter", ms: 900, goto: "end", reply_source: "interpreter:llm" }],
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /how this answer was made/ }));
    expect(screen.getByText(/\(the model\)/)).toBeTruthy();
  });

  it("falls back to the trace when the message carries no source", async () => {
    const { source, ...withoutSource } = TRACE;
    void source;
    render(<TurnTrace message={withoutSource} />);
    await userEvent.click(screen.getByRole("button", { name: /how this answer was made/ }));
    expect(screen.getByText("interpreter:_rule_based_summary")).toBeTruthy();
  });

  it("keeps the turn's progress lines, with their nodes", async () => {
    render(<TurnTrace message={TRACE} />);
    await userEvent.click(screen.getByRole("button", { name: /how this answer was made/ }));
    expect(screen.getByText(/1 progress line/)).toBeTruthy();
    expect(screen.getByText(/Summarizing the session…/)).toBeTruthy();
  });
});
