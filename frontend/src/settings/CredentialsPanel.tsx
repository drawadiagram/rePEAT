import { useCallback, useEffect, useState } from "react";
import {
  clearCredentials,
  fetchCredentials,
  saveCredentials,
  testCredentials,
} from "../lib/api";
import type { CredentialsView, ProbeResult, UserCredential } from "../lib/types";

/**
 * The signed-in user's own credentials: their key, their endpoint, their
 * allocation. They apply to this user's turns only, from the next one.
 *
 * The same two rules as `SettingsPanel`. A secret is never rendered — the server
 * sends a masked hint, which goes in the placeholder. And an untouched field is
 * left out of the request, so the hint is never written back as the value.
 *
 * `FIELDS` mirrors `USER_FIELDS` in `backend/designagent/auth/credentials.py`;
 * the server is `extra="forbid"`, so a field named here that it does not know
 * is a 422 rather than a silent drop.
 */

type Field = {
  name: string;
  label: string;
  secret?: boolean;
  multiline?: boolean;
  hint?: string;
};

const FIELDS: { group: string; title: string; note: string; fields: Field[] }[] = [
  {
    group: "llm",
    title: "Language model",
    note: "Your own Anthropic key. Without one, every node uses its rule-based path.",
    fields: [
      { name: "anthropic_api_key", label: "Anthropic API key", secret: true },
      { name: "model", label: "Model", hint: "e.g. claude-sonnet-5-5; empty uses the server's" },
    ],
  },
  {
    group: "orbit",
    title: "Your HPC endpoint",
    note: "Jobs you start run under your account and allocation, through your own endpoint. The broker must be one this server allows.",
    fields: [
      { name: "orbit_broker_url", label: "Broker URL" },
      { name: "orbit_broker_token", label: "Broker token", secret: true },
      {
        name: "orbit_broker_cert",
        label: "Broker certificate",
        multiline: true,
        hint: "the broker's PEM certificate (never its key)",
      },
      { name: "orbit_endpoint", label: "Endpoint name", hint: "the endpoint's --name" },
      { name: "orbit_account", label: "Slurm account" },
      { name: "orbit_queue", label: "Queue" },
    ],
  },
];

const PROBE_LABEL: Record<ProbeResult["state"], string> = {
  ok: "works",
  absent: "not configured",
  rejected: "refused",
  error: "unreachable",
  skipped: "not checked here",
};

function isSecretEntry(
  entry: UserCredential | undefined,
): entry is { present: boolean; hint: string; source: "user" } {
  return Boolean(entry && "present" in entry);
}

export default function CredentialsPanel({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [view, setView] = useState<CredentialsView | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [probes, setProbes] = useState<Record<string, ProbeResult>>({});
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"" | "saving" | "testing">("");

  const reload = useCallback(async () => {
    setView(await fetchCredentials());
    setDraft({});
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const dirty = Object.keys(draft).length > 0;

  function shown(field: Field): string {
    if (field.name in draft) return draft[field.name];
    const entry = view?.credentials[field.name];
    if (!entry || isSecretEntry(entry) || field.multiline) return "";
    return entry.value;
  }

  function placeholder(field: Field): string {
    const entry = view?.credentials[field.name];
    if (isSecretEntry(entry) && entry.present) return entry.hint;
    if (field.multiline && entry && !isSecretEntry(entry) && entry.value) {
      return "a certificate is set; paste a new one to replace it";
    }
    if (field.name === "orbit_broker_url" && view?.allowed_brokers.length) {
      return view.allowed_brokers.join(" or ");
    }
    return field.hint ?? "";
  }

  async function save() {
    setBusy("saving");
    setMessage("");
    const result = await saveCredentials(draft);
    setBusy("");
    if (!result.ok) {
      setMessage(result.detail ?? `the server refused the change (${result.status})`);
      return;
    }
    setMessage("Saved. Your next turn uses them.");
    await reload();
    onChanged();
  }

  async function probe() {
    setBusy("testing");
    setMessage("");
    setProbes(await testCredentials(draft));
    setBusy("");
  }

  async function clear() {
    setBusy("saving");
    const ok = await clearCredentials();
    setBusy("");
    setMessage(ok ? "Your credentials were removed." : "the server refused the reset");
    await reload();
    onChanged();
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-label="My credentials"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-head">
          <strong>My credentials</strong>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </header>

        <p className="muted sheet-intro">
          Stored encrypted on the server and used only for your own turns. Nobody, you
          included, can read a key back once it is saved.
        </p>
        {view && !view.can_store && (
          <p className="sheet-error">
            This server has no secrets key configured, so it cannot store credentials yet.
          </p>
        )}

        {FIELDS.map((group) => (
          <section key={group.group} className="sheet-group">
            <h3>
              {group.title}
              {probes[group.group] && (
                <span className={`badge probe-${probes[group.group].state}`}>
                  {PROBE_LABEL[probes[group.group].state]}
                </span>
              )}
            </h3>
            <p className="muted">{group.note}</p>
            {probes[group.group]?.detail && (
              <p className="muted probe-detail">{probes[group.group].detail}</p>
            )}
            {group.fields.map((field) => (
              <label key={field.name} className="sheet-row">
                <span className="sheet-label">{field.label}</span>
                {field.multiline ? (
                  <textarea
                    rows={4}
                    value={shown(field)}
                    placeholder={placeholder(field)}
                    onChange={(event) =>
                      setDraft((d) => ({ ...d, [field.name]: event.target.value }))
                    }
                  />
                ) : (
                  <input
                    type={field.secret ? "password" : "text"}
                    autoComplete="off"
                    value={shown(field)}
                    placeholder={placeholder(field)}
                    onChange={(event) =>
                      setDraft((d) => ({ ...d, [field.name]: event.target.value }))
                    }
                  />
                )}
              </label>
            ))}
          </section>
        ))}

        {message && <p className="sheet-message">{message}</p>}

        <footer className="sheet-foot">
          <button className="link" onClick={clear} disabled={busy !== ""}>
            Remove all
          </button>
          <span className="sheet-spacer" />
          <button className="link" onClick={probe} disabled={busy !== ""}>
            {busy === "testing" ? "Testing…" : "Test"}
          </button>
          <button className="primary" onClick={save} disabled={busy !== "" || !dirty}>
            {busy === "saving" ? "Saving…" : "Save"}
          </button>
        </footer>
      </div>
    </div>
  );
}
