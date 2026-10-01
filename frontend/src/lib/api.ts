import type { AgentState, ArtifactRef, Frame, TaskChip } from "./types";

/**
 * POST a prompt and yield SSE frames as they arrive.
 *
 * EventSource cannot POST, so this reads the body with fetch + a stream reader
 * and splits on the blank line that terminates an SSE event. A partial frame is
 * held in `buffer` until its terminator arrives, otherwise a chunk boundary
 * mid-JSON would corrupt it.
 */
export async function* streamChat(
  message: string,
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<Frame> {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });

  if (!response.ok || !response.body) {
    yield { type: "error", message: `server returned ${response.status}` };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let split: number;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      for (const line of raw.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          yield JSON.parse(line.slice(6)) as Frame;
        } catch {
          // A malformed frame should not kill the stream.
        }
      }
    }
  }
}

export async function fetchSession(
  sessionId: string,
): Promise<{ state: AgentState; messages: { role: string; content: string }[]; tasks: TaskChip[] }> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (!response.ok) return { state: {}, messages: [], tasks: [] };
  return response.json();
}

export async function fetchHealth(): Promise<Record<string, unknown>> {
  const response = await fetch("/api/health");
  return response.ok ? response.json() : {};
}

export async function fetchArtifactText(url: string): Promise<string> {
  const response = await fetch(url);
  return response.ok ? response.text() : "";
}

export async function fetchArtifactJson<T = unknown>(url: string): Promise<T | null> {
  const response = await fetch(url);
  return response.ok ? ((await response.json()) as T) : null;
}

export async function cancelTask(id: string): Promise<boolean> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
  return response.ok;
}

export function artifactsOf(state: AgentState): ArtifactRef[] {
  return state.artifacts ?? [];
}
