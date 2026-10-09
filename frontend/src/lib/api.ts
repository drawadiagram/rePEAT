import type {
  AgentState,
  ArtifactRef,
  CredentialsView,
  Frame,
  Me,
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
  const response = notice401(
    await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
      signal,
    }),
  );

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

export async function fetchSession(sessionId: string): Promise<{
  state: AgentState;
  messages: { role: string; content: string }[];
  tasks: TaskChip[];
  /** The server does not know this id, or it is someone else's (both are 404). */
  missing?: boolean;
}> {
  const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
  if (response.status === 404) return { state: {}, messages: [], tasks: [], missing: true };
  if (!response.ok) return { state: {}, messages: [], tasks: [] };
  return response.json();
}

// --- logins -----------------------------------------------------------------
//
// Inert unless the server says `auth: true`. Anything else — logins off, an old
// server without /api/me, a stubbed route in a test — reads as "no logins", so
// the app behaves exactly as it did before they existed.

/** Fired on any 401, so the app can return to the sign-in page from anywhere. */
export const UNAUTHORIZED = "designagent:unauthorized";

function notice401(response: Response): Response {
  if (response.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
  return response;
}

export async function fetchMe(): Promise<Me> {
  try {
    const response = await fetch("/api/me");
    if (!response.ok) return { auth: response.status === 401 };
    const body = await response.json();
    return body && body.auth === true ? (body as Me) : { auth: false };
  } catch {
    return { auth: false };
  }
}

export async function login(
  username: string,
  password: string,
): Promise<{ ok: boolean; status: number; detail?: string }> {
  const response = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (response.ok) return { ok: true, status: response.status };
  const body = await response.json().catch(() => ({}));
  return { ok: false, status: response.status, detail: body?.detail };
}

export async function logout(): Promise<void> {
  await fetch("/api/logout", { method: "POST" });
}

/** A new session id. Minted by the server under logins, locally otherwise. */
export async function newSessionId(auth: boolean): Promise<string> {
  if (auth) {
    const response = notice401(await fetch("/api/sessions", { method: "POST" }));
    if (response.ok) return (await response.json()).session_id;
  }
  return `s-${Math.random().toString(36).slice(2, 10)}`;
}

export async function fetchCredentials(): Promise<CredentialsView | null> {
  const response = notice401(await fetch("/api/me/credentials"));
  return response.ok ? response.json() : null;
}

export async function saveCredentials(
  values: Record<string, string>,
): Promise<{ ok: boolean; status: number; detail?: string; view?: CredentialsView }> {
  const response = notice401(
    await fetch("/api/me/credentials", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
  );
  const body = await response.json().catch(() => ({}));
  return response.ok
    ? { ok: true, status: response.status, view: body }
    : { ok: false, status: response.status, detail: body?.detail };
}

export async function clearCredentials(): Promise<boolean> {
  const response = notice401(await fetch("/api/me/credentials", { method: "DELETE" }));
  return response.ok;
}

export async function testCredentials(
  values: Record<string, string>,
): Promise<Record<string, ProbeResult>> {
  const response = notice401(
    await fetch("/api/me/credentials/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    }),
  );
  if (!response.ok) return {};
  return (await response.json()).probes ?? {};
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
