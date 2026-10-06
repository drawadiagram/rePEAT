import { useState } from "react";
import { cancelTask } from "../lib/api";
import type { TaskChip } from "../lib/types";

/**
 * Live task indicators.
 *
 * Task duration is indeterminate, so each in-flight task shows its state and
 * elapsed time, plus a log tail where the interface can stream one (only the
 * Orbit PSI/J path can).
 *
 * The interface is shown only when it is not `local`. Most work is local, so
 * labelling every chip would say nothing; the case worth seeing is a task that
 * actually left this machine, which is otherwise visible only in the session
 * summary artifact's "Tasks run" section after the turn has finished.
 */
export default function TaskChips({ tasks }: { tasks: TaskChip[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!tasks.length) return null;

  const running = tasks.filter((t) => !isTerminal(t.state));
  const finished = tasks.filter((t) => isTerminal(t.state));
  const shown = [...running, ...finished.slice(-6)];

  return (
    <div className="chips">
      {shown.map((task) => (
        <div key={task.id} className={`chip chip-${task.state.toLowerCase()}`}>
          <button
            className="chip-main"
            onClick={() => setExpanded(expanded === task.id ? null : task.id)}
            title={task.error || task.task}
          >
            <span className="chip-dot" />
            <span className="chip-name">{task.label || task.task}</span>
            {task.interface && task.interface !== "local" && (
              <span className="chip-iface">{task.interface}</span>
            )}
            <span className="chip-meta">
              {task.state.toLowerCase()} · {task.elapsed?.toFixed(1)}s
            </span>
          </button>
          {!isTerminal(task.state) && (
            <button
              className="chip-cancel"
              title="Cancel this task"
              onClick={() => void cancelTask(task.id)}
            >
              ✕
            </button>
          )}
          {expanded === task.id && (task.log_tail || task.error) && (
            <pre className="chip-log">{task.error || task.log_tail}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

function isTerminal(state: string): boolean {
  return ["DONE", "FAILED", "CANCELED"].includes(state?.toUpperCase());
}
