import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ArtifactPane from "./artifacts/ArtifactPane";
import ChatPane from "./chat/ChatPane";
import {
  UNAUTHORIZED,
  fetchHealth,
  fetchMe,
  fetchSession,
  logout,
  newSessionId,
  streamChat,
} from "./lib/api";
import type {
  AgentState,
  ChatMessage,
  Frame,
  StatusLine,
  TaskChip,
  Me,
  TraceEntry,
} from "./lib/types";
import CredentialsPanel from "./settings/CredentialsPanel";
import LoginPage from "./settings/LoginPage";
import SettingsPanel from "./settings/SettingsPanel";

const SESSION_KEY = "designagent.session";

function initialSession(): string {
  const existing = localStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const fresh = `s-${Math.random().toString(36).slice(2, 10)}`;
  localStorage.setItem(SESSION_KEY, fresh);
  return fresh;
}

/**
 * The sign-in gate. With logins off (`auth: false`, also what any error reads
 * as) it renders the app straight away, exactly as before logins existed.
 */
export default function App() {
  const [me, setMe] = useState<Me | null>(null);

  const refresh = useCallback(async () => setMe(await fetchMe()), []);

  useEffect(() => {
    void refresh();
    // Any 401 — an expired cookie, a sign-out in another tab — comes back here.
    const onUnauthorized = () => setMe({ auth: true });
    window.addEventListener(UNAUTHORIZED, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED, onUnauthorized);
  }, [refresh]);

  if (me === null) return null;
  if (me.auth && !me.user) return <LoginPage onSignedIn={() => void refresh()} />;
  return (
    <Workspace
      me={me}
      onMeChanged={() => void refresh()}
      onSignOut={async () => {
        await logout();
        setMe({ auth: true });
      }}
    />
  );
}

function Workspace({
  me,
  onMeChanged,
  onSignOut,
}: {
  me: Me;
  onMeChanged: () => void;
  onSignOut: () => void;
}) {
  const [sessionId, setSessionId] = useState(initialSession);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [state, setState] = useState<AgentState>({});
  const [statuses, setStatuses] = useState<StatusLine[]>([]);
  const [tasks, setTasks] = useState<Record<string, TaskChip>>({});
  const [busy, setBusy] = useState(false);
  const [paneOpen, setPaneOpen] = useState(true);
  const [health, setHealth] = useState<Record<string, any>>({});
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const isAdmin = !me.auth || me.user?.role === "admin";

  // Under logins the badges describe *this user's* key and endpoint, which is
  // what /api/me reports; /api/health is the server's.
  const llm = me.auth ? me.llm : (health.llm as boolean | undefined);
  const hpc = me.auth ? me.hpc : (health.hpc as boolean | undefined);
  const openCredentials = () => (me.auth ? setCredentialsOpen(true) : setSettingsOpen(true));

  // Restore the session on load, so a refresh keeps the conversation.
  useEffect(() => {
    if (!me.auth) void fetchHealth().then(setHealth);
    void fetchSession(sessionId).then(async (body) => {
      if (body.missing && me.auth) {
        // A stored id the server does not know, or another account's: start a
        // fresh one rather than show someone a 404 as an empty conversation.
        const fresh = await newSessionId(true);
        localStorage.setItem(SESSION_KEY, fresh);
        setSessionId(fresh);
        return;
      }
      if (body.messages?.length) {
        setMessages(
          body.messages
            .filter((m) => m.content)
            .map((m) => ({ role: m.role as ChatMessage["role"], content: m.content })),
        );
      }
      if (body.state) setState(body.state);
      if (body.tasks?.length) {
        setTasks(Object.fromEntries(body.tasks.map((t) => [t.id, t])));
      }
    });
  }, [sessionId, me.auth]);

  const send = useCallback(
    async (text: string) => {
      if (busy) return;
      setBusy(true);
      setStatuses([]);
      setMessages((prev) => [...prev, { role: "user", content: text }]);

      const controller = new AbortController();
      abort.current = controller;

      // Mirrors of what the turn emitted, kept outside React state so the
      // `finally` below can attach them without racing a re-render.
      const turnStatuses: StatusLine[] = [];
      let turnTrace: TraceEntry[] = [];

      // Tokens accumulate into a single streaming assistant bubble.
      let streamingIndex: number | null = null;
      const appendToken = (chunk: string, node?: string) => {
        setMessages((prev) => {
          const next = [...prev];
          if (streamingIndex === null || !next[streamingIndex]?.streaming) {
            next.push({ role: "assistant", content: chunk, streaming: true, node });
            streamingIndex = next.length - 1;
          } else {
            next[streamingIndex] = {
              ...next[streamingIndex],
              content: next[streamingIndex].content + chunk,
            };
          }
          return next;
        });
      };

      try {
        for await (const frame of streamChat(text, sessionId, controller.signal)) {
          if (frame.type === "status") {
            turnStatuses.push({ text: frame.text, node: frame.node });
          } else if (frame.type === "state" && frame.state.trace) {
            turnTrace = frame.state.trace;
          }
          handleFrame(frame, {
            appendToken,
            setMessages,
            setState,
            setStatuses,
            setTasks,
            openPane: () => setPaneOpen(true),
          });
          if ((frame as any).type === "done") break;
        }
      } catch (error: any) {
        if (error?.name !== "AbortError") {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: `Request failed: ${error?.message ?? error}` },
          ]);
        }
      } finally {
        // Settle the streaming bubble, and hand this turn's trail and node path
        // to the reply it explains. They used to be cleared here, which is why a
        // finished turn could not say how it got there.
        setMessages((prev) => {
          const next = prev.map((m) =>
            m.streaming ? { ...m, streaming: false } : m,
          );
          for (let i = next.length - 1; i >= 0; i -= 1) {
            if (next[i].role === "assistant") {
              next[i] = { ...next[i], statuses: turnStatuses, trace: turnTrace };
              break;
            }
          }
          return next;
        });
        setBusy(false);
        setStatuses([]);
        abort.current = null;
      }
    },
    [busy, sessionId],
  );

  const stop = useCallback(() => abort.current?.abort(), []);

  const newSession = useCallback(async () => {
    const fresh = await newSessionId(me.auth);
    localStorage.setItem(SESSION_KEY, fresh);
    setMessages([]);
    setState({});
    setTasks({});
    setStatuses([]);
    setSessionId(fresh);
  }, [me.auth]);

  const taskList = useMemo(() => Object.values(tasks), [tasks]);
  const hasArtifacts = Boolean(
    state.artifacts?.length || state.molecular_visualization?.structures?.length,
  );

  return (
    <div className={hasArtifacts && paneOpen ? "app split" : "app"}>
      <header className="topbar">
        <span className="brand">Protein Design Agent</span>
        <span className="badges">
          {state.reference_design?.pdb_id && (
            <span className="badge">{state.reference_design.pdb_id}</span>
          )}
          {state.key_metric?.name && (
            <span className="badge">
              {state.key_metric.direction === "min" ? "min" : "max"}{" "}
              {state.key_metric.name}
            </span>
          )}
          {typeof state.round === "number" && state.round > 0 && (
            <span className="badge">round {state.round}</span>
          )}
          {llm === false && (
            <button
              className="badge warn badge-button"
              title="No API key: every node is using its rule-based path. Click to add one."
              onClick={openCredentials}
            >
              rule-based mode
            </button>
          )}
          {hpc ? (
            <span className="badge ok" title={me.endpoint ? `endpoint ${me.endpoint}` : undefined}>
              HPC
            </span>
          ) : (
            <button
              className="badge badge-button"
              title={
                me.hpc_error ||
                "No HPC endpoint: tasks marked hpc run their local equivalents. Click to configure one."
              }
              onClick={openCredentials}
            >
              local only
            </button>
          )}
        </span>
        <span className="actions">
          {hasArtifacts && !paneOpen && (
            <button className="link" onClick={() => setPaneOpen(true)}>
              Show panel
            </button>
          )}
          {me.auth && (
            <button className="link" onClick={() => setCredentialsOpen(true)}>
              My credentials
            </button>
          )}
          {isAdmin && (
            <button className="link" onClick={() => setSettingsOpen(true)}>
              Settings
            </button>
          )}
          <button className="link" onClick={() => void newSession()}>
            New session
          </button>
          {me.auth && (
            <button className="link" onClick={onSignOut} title={`signed in as ${me.user?.username}`}>
              Sign out
            </button>
          )}
        </span>
      </header>

      {settingsOpen && (
        <SettingsPanel
          auth={me.auth}
          onClose={() => setSettingsOpen(false)}
          onChanged={() => {
            void fetchHealth().then(setHealth);
            onMeChanged();
          }}
        />
      )}
      {credentialsOpen && (
        <CredentialsPanel onClose={() => setCredentialsOpen(false)} onChanged={onMeChanged} />
      )}

      <main className="panes">
        <ChatPane
          messages={messages}
          statuses={statuses}
          tasks={taskList}
          busy={busy}
          onSend={send}
          onStop={stop}
        />
        {hasArtifacts && paneOpen && (
          <ArtifactPane state={state} onClose={() => setPaneOpen(false)} />
        )}
      </main>
    </div>
  );
}

/**
 * Apply one SSE frame to the UI.
 *
 * Exported for `frames.test.ts`: this switch is the whole translation from the
 * wire to what the user sees, and it is the part of the browser path most able
 * to break without anything failing to compile.
 */
export function handleFrame(
  frame: Frame,
  sinks: {
    appendToken: (chunk: string, node?: string) => void;
    setMessages: React.Dispatch<React.SetStateAction<ChatMessage[]>>;
    setState: React.Dispatch<React.SetStateAction<AgentState>>;
    setStatuses: React.Dispatch<React.SetStateAction<StatusLine[]>>;
    setTasks: React.Dispatch<React.SetStateAction<Record<string, TaskChip>>>;
    openPane: () => void;
  },
) {
  switch (frame.type) {
    case "token":
      sinks.appendToken(frame.text, frame.node);
      break;

    case "message":
      // Without an LLM the reply arrives whole rather than as tokens. `node` and
      // `source` say which code wrote it; they are what the disclosure shows.
      sinks.setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.content === frame.text) {
          const next = [...prev];
          next[next.length - 1] = { ...last, node: frame.node, source: frame.source };
          return next;
        }
        return [
          ...prev,
          {
            role: "assistant",
            content: frame.text,
            node: frame.node,
            source: frame.source,
          },
        ];
      });
      break;

    case "status":
      // `node` arrives on this frame and used to be dropped here, which left
      // the progress trail unattributed and then discarded it at end of turn.
      sinks.setStatuses((prev) =>
        prev[prev.length - 1]?.text === frame.text
          ? prev
          : [...prev, { text: frame.text, node: frame.node }],
      );
      break;

    case "task": {
      const id = frame.id;
      if (!id) break;
      sinks.setTasks((prev) => {
        const existing = prev[id];
        const next: TaskChip = {
          id,
          task: frame.task ?? existing?.task ?? "task",
          label: frame.label ?? existing?.label,
          interface: frame.interface ?? existing?.interface ?? "local",
          state: frame.state ?? existing?.state ?? "RUNNING",
          elapsed: frame.elapsed ?? existing?.elapsed ?? 0,
          error: frame.error ?? existing?.error,
          log_tail:
            frame.event === "log"
              ? `${existing?.log_tail ?? ""}${frame.text ?? ""}`
              : frame.log_tail ?? existing?.log_tail,
        };
        return { ...prev, [id]: next };
      });
      break;
    }

    case "state":
      sinks.setState((prev) => {
        const merged = { ...prev, ...frame.state };
        // Artifacts accumulate across a turn; everything else is replaced.
        if (frame.state.artifacts) {
          const byId = new Map(
            [...(prev.artifacts ?? []), ...frame.state.artifacts].map((a) => [a.id, a]),
          );
          merged.artifacts = [...byId.values()];
        }
        return merged;
      });
      if (frame.state.artifacts?.length || frame.state.molecular_visualization) {
        sinks.openPane();
      }
      break;

    case "error":
      sinks.setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Something went wrong: ${frame.message}` },
      ]);
      break;

    default:
      break;
  }
}
