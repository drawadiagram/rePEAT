/** The chips that answer "what is running, and did it leave this machine?". */

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TaskChip } from "../lib/types";
import TaskChips from "./TaskChips";

// The cancel button posts; nothing here is asserting on the network.
vi.mock("../lib/api", () => ({ cancelTask: vi.fn() }));

afterEach(cleanup);

function chip(over: Partial<TaskChip> = {}): TaskChip {
  return {
    id: "t1",
    task: "fold_sequence",
    interface: "local",
    state: "DONE",
    elapsed: 2.5,
    ...over,
  };
}

describe("TaskChips", () => {
  it("renders nothing when there is nothing to show", () => {
    const { container } = render(<TaskChips tasks={[]} />);
    expect(container.innerHTML).toBe("");
  });

  it("names a remote task's interface", () => {
    // The one browser-visible signal that a job went through the endpoint
    // rather than running in the local pool.
    render(
      <TaskChips tasks={[chip({ task: "proteinmpnn", interface: "hpc" })]} />,
    );
    expect(screen.getByText("proteinmpnn")).toBeTruthy();
    expect(screen.getByText("hpc")).toBeTruthy();
  });

  it("stays quiet about the local interface", () => {
    // Most work is local, so saying so on every chip would carry no signal.
    render(<TaskChips tasks={[chip()]} />);
    expect(screen.getByText("fold_sequence")).toBeTruthy();
    expect(screen.queryByText("local")).toBeNull();
  });

  it("prefers a label over the raw task name", () => {
    render(<TaskChips tasks={[chip({ label: "fold r1-1" })]} />);
    expect(screen.getByText("fold r1-1")).toBeTruthy();
  });

  it("offers cancel only while the task can still be cancelled", () => {
    const { rerender } = render(
      <TaskChips tasks={[chip({ state: "RUNNING" })]} />,
    );
    expect(screen.getByTitle("Cancel this task")).toBeTruthy();
    rerender(<TaskChips tasks={[chip({ state: "DONE" })]} />);
    expect(screen.queryByTitle("Cancel this task")).toBeNull();
  });

  it("reveals a remote log tail on click", async () => {
    // Only the Orbit PSI/J interface can stream one, so a populated tail is
    // itself evidence of where the task ran.
    render(
      <TaskChips
        tasks={[
          chip({
            task: "proteinmpnn",
            interface: "hpc",
            state: "RUNNING",
            log_tail: "Generating sequences for: in",
          }),
        ]}
      />,
    );
    expect(screen.queryByText(/Generating sequences/)).toBeNull();
    await userEvent.click(screen.getByText("proteinmpnn"));
    expect(screen.getByText(/Generating sequences/)).toBeTruthy();
  });

  it("shows an error in place of the log tail", async () => {
    render(
      <TaskChips
        tasks={[chip({ state: "FAILED", error: "exit code 3", log_tail: "x" })]}
      />,
    );
    await userEvent.click(screen.getByText("fold_sequence"));
    expect(screen.getByText("exit code 3")).toBeTruthy();
  });
});
