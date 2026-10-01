import { useRef, useState } from "react";

const SUGGESTIONS = [
  "Redesign 1UBQ to improve thermostability",
  "Load P00698 and summarize what is known",
  "Apply T55V and fold it",
  "Show the active site",
];

export default function Composer({
  onSend,
  busy,
  onStop,
  showSuggestions,
}: {
  onSend: (text: string) => void;
  busy: boolean;
  onStop: () => void;
  showSuggestions: boolean;
}) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  function send() {
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setText("");
    area.current?.focus();
  }

  return (
    <div className="composer-wrap">
      {showSuggestions && (
        <div className="suggestions">
          {SUGGESTIONS.map((suggestion) => (
            <button key={suggestion} onClick={() => onSend(suggestion)} disabled={busy}>
              {suggestion}
            </button>
          ))}
        </div>
      )}
      <div className="composer">
        <textarea
          ref={area}
          value={text}
          rows={1}
          placeholder="Name a protein, or ask for a redesign…"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter is a newline.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              send();
            }
          }}
        />
        {busy ? (
          <button className="btn stop" onClick={onStop}>
            Stop
          </button>
        ) : (
          <button className="btn" onClick={send} disabled={!text.trim()}>
            Send
          </button>
        )}
      </div>
    </div>
  );
}
