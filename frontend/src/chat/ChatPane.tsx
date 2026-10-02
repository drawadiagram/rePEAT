import { useEffect, useRef } from "react";
import MarkdownView from "../artifacts/MarkdownView";
import type { ChatMessage, StatusLine, TaskChip } from "../lib/types";
import Composer from "./Composer";
import TaskChips from "./TaskChips";
import TurnTrace from "./TurnTrace";

export default function ChatPane({
  messages,
  statuses,
  tasks,
  busy,
  onSend,
  onStop,
}: {
  messages: ChatMessage[];
  statuses: StatusLine[];
  tasks: TaskChip[];
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const bottom = useRef<HTMLDivElement>(null);

  // Keep the newest turn in view as content streams in.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, statuses, tasks]);

  return (
    <section className="chat">
      <div className="messages">
        {messages.length === 0 && (
          <div className="empty">
            <h1>Protein Design Agent</h1>
            <p className="muted">
              Name a target by PDB id, UniProt accession, or protein name, and say
              what you want to improve. Structures and summaries appear in the
              panel beside this conversation.
            </p>
          </div>
        )}

        {messages.map((message, index) => (
          <article key={index} className={`msg msg-${message.role}`}>
            <div className="msg-role">{message.role === "user" ? "You" : "Agent"}</div>
            <div className="msg-body">
              {message.role === "assistant" ? (
                <MarkdownView text={message.content} />
              ) : (
                <p>{message.content}</p>
              )}
              {message.streaming && <span className="caret" />}
            </div>
            {message.role === "assistant" && !message.streaming && (
              <TurnTrace message={message} />
            )}
          </article>
        ))}

        {busy && statuses.length > 0 && (
          <div className="status-trail">
            {statuses.slice(-4).map((status, index) => (
              <div
                key={`${status.text}-${index}`}
                className={index === statuses.slice(-4).length - 1 ? "status live" : "status"}
              >
                {status.node && <span className="status-node">{status.node}</span>}
                {status.text}
              </div>
            ))}
          </div>
        )}

        <TaskChips tasks={tasks} />
        <div ref={bottom} />
      </div>

      <Composer
        onSend={onSend}
        busy={busy}
        onStop={onStop}
        showSuggestions={messages.length === 0}
      />
    </section>
  );
}
