import { useState } from "react";
import type { ChatMessage, StatusLine, TraceEntry } from "../lib/types";

/**
 * "How this answer was made" — the node path behind one reply.
 *
 * The agent answers the same way whether an LLM or a rule wrote the sentence,
 * which makes a surprising reply hard to account for: the progress lines were
 * emitted with their node and then discarded, and the reply itself carried no
 * author at all. Everything here comes from the turn that produced this
 * message, so it stays true after the next turn and after a reload.
 *
 * Collapsed by default. It is for the moment you ask "why did it do that?", not
 * something to read on every turn.
 */
export default function TurnTrace({ message }: { message: ChatMessage }) {
  const [open, setOpen] = useState(false);
  const trace = message.trace ?? [];
  const statuses = message.statuses ?? [];
  const source = message.source ?? lastSource(trace);

  if (trace.length === 0 && !source) return null;

  const total = trace.reduce((sum, entry) => sum + (entry.ms ?? 0), 0);
  const llm = source?.endsWith(":llm");

  return (
    <div className="trace">
      <button className="trace-toggle" onClick={() => setOpen(!open)}>
        <span className="trace-caret">{open ? "▾" : "▸"}</span>
        how this answer was made
        {total > 0 && <span className="trace-total">{seconds(total)}</span>}
      </button>

      {open && (
        <div className="trace-body">
          {trace.map((entry, index) => (
            <div key={`${entry.node}-${index}`} className="trace-row">
              <span className="trace-node">{entry.node}</span>
              <span className="trace-ms">{entry.ms}ms</span>
              <span className="trace-detail">
                {describe(entry)}
                {entry.goto && <span className="trace-goto"> → {entry.goto}</span>}
              </span>
            </div>
          ))}

          {source && (
            <p className="trace-source">
              reply written by <code>{source}</code>
              {llm ? " (the model)" : " (a rule, no model involved)"}
            </p>
          )}

          {statuses.length > 0 && (
            <details className="trace-statuses">
              <summary>{statuses.length} progress line(s)</summary>
              {statuses.map((status: StatusLine, index) => (
                <div key={index} className="trace-status">
                  {status.node && <span className="trace-node">{status.node}</span>}
                  {status.text}
                </div>
              ))}
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/** The counters a node reported, as a short phrase. */
function describe(entry: TraceEntry): string {
  const parts: string[] = [];
  if (entry.intent) parts.push(`intent=${entry.intent}`);
  if (entry.round) parts.push(`round ${entry.round}`);
  if (entry.n_worklist) parts.push(`${entry.n_worklist} task(s)`);
  if (entry.n_ensemble) parts.push(`${entry.n_ensemble} design(s)`);
  if (entry.n_artifacts) parts.push(`${entry.n_artifacts} artifact(s)`);
  if (entry.n_warnings) parts.push(`${entry.n_warnings} caveat(s)`);
  if (entry.reply_source) parts.push(entry.reply_source.split(":").slice(1).join(":"));
  return parts.join(" · ");
}

function lastSource(trace: TraceEntry[]): string | undefined {
  for (let i = trace.length - 1; i >= 0; i -= 1) {
    if (trace[i].reply_source) return trace[i].reply_source;
  }
  return undefined;
}

function seconds(ms: number): string {
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(2)}s`;
}
