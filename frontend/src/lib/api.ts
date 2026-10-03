import type {
  AgentState,
  ArtifactRef,
  Frame,
  ProbeResult,
  SettingsResponse,
  SettingsView,
  TaskChip,
} from "./types";

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

/**
 * Settings. Values go out, never come back: a GET returns presence, source and a
 * masked hint, so a field left untouched in the panel must be sent as `undefined`
 * rather than as the hint it displayed.
 *
 * `admin` is the shared secret the server demands when it is not on loopback.
 */
export async function fetchSettings(): Promise<SettingsView | null> {
  const response = await fetch("/api/settings");
  return response.ok ? response.json() : null;
}

function adminHeaders(admin?: string): Record<string, string> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (admin) headers["X-Designagent-Admin"] = admin;
  return headers;
}

export async function saveSettings(
  values: Record<string, unknown>,
  options: { force?: boolean; admin?: string } = {},
): Promise<{ ok: boolean; status: number; body: SettingsResponse }> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: adminHeaders(options.admin),
    body: JSON.stringify({ ...values, force: options.force ?? false }),
  });
  return { ok: response.ok, status: response.status, body: await response.json() };
}

export async function testSettings(
  values: Record<string, unknown>,
  admin?: string,
): Promise<Record<string, ProbeResult>> {
  const response = await fetch("/api/settings/test", {
    method: "POST",
    headers: adminHeaders(admin),
    body: JSON.stringify(values),
  });
  if (!response.ok) return {};
  return (await response.json()).probes ?? {};
}

export async function clearSettings(admin?: string): Promise<boolean> {
  const response = await fetch("/api/settings", {
    method: "DELETE",
    headers: adminHeaders(admin),
  });
  return response.ok;
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
