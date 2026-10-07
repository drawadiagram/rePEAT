import { useCallback, useEffect, useState } from "react";
import {
  clearSettings,
  fetchSettings,
  saveSettings,
  testSettings,
} from "../lib/api";
import type { ProbeResult, SettingsView } from "../lib/types";
import { isSecret } from "../lib/types";

/**
 * Credentials for the running process.
 *
 * Two rules shape this component. A secret is never rendered: the server returns
 * a masked hint, which goes in the placeholder, and an untouched field is left
 * out of the request entirely rather than sent back as its own mask. And saving
 * can recycle the worker pool, because a task body reads its key from the
 * settings its process was given at fork — so the cost is stated before the
 * click, not after.
 *
 * `FIELDS` and `GROUPS` are hand-maintained against `CREDENTIAL_FIELDS` in
 * `backend/designagent/config.py`, and nothing enforces that they agree: a group
 * the backend reports but this file omits is dropped silently by the
 * `GROUPS.map` below, which is how the `mpnn` group and three `orbit` fields
 * stayed invisible after being added server-side. A field added here must also
 * be declared on `SettingsUpdate` (`app.py`), which is `extra="forbid"` — or
 * marked `readonly`, which is how a value the server deliberately refuses is
 * shown without offering an edit that would 422. `globus` is still reported and
 * still not rendered.
 */

type Field = {
  name: string;
  label: string;
  group: string;
  secret?: boolean;
  kind?: "text" | "bool" | "number";
  hint?: string;
  /**
   * Shown, but not editable here, because the server will not accept it:
   * `SettingsUpdate` is `extra="forbid"`, so sending one would 422. Reserved
   * for values that name shell to run — the settings routes are
   * unauthenticated on loopback, so accepting those over HTTP would be
   * arbitrary code execution. Set them in the environment or `.env`.
   */
  readonly?: boolean;
};

const FIELDS: Field[] = [
  { name: "anthropic_api_key", label: "Anthropic API key", group: "llm", secret: true },
  { name: "model", label: "Model", group: "llm" },
  {
    name: "fold_backend",
    label: "Fold backend",
    group: "fold",
    hint: "esmatlas, local or hpc",
  },

  { name: "orbit_enabled", label: "Use remote HPC (Orbit)", group: "orbit", kind: "bool" },
  { name: "orbit_broker_url", label: "Broker URL", group: "orbit" },
  { name: "orbit_broker_token", label: "Broker token", group: "orbit", secret: true },
  {
    name: "orbit_broker_cert",
    label: "Broker certificate",
    group: "orbit",
    hint: "path on the server, required for https/wss",
  },
  { name: "orbit_endpoint", label: "Endpoint", group: "orbit", hint: "name or substring" },
  {
    name: "orbit_rhapsody_backends",
    label: "Rhapsody backends",
    group: "orbit",
    hint: "comma separated; empty uses the endpoint default",
  },
  {
    name: "orbit_psij_executor",
    label: "Scheduler",
    group: "orbit",
    hint: "local, slurm, pbspro…",
  },
  { name: "orbit_account", label: "Account", group: "orbit" },
  { name: "orbit_queue", label: "Queue", group: "orbit" },
  {
    name: "orbit_job_duration_sec",
    label: "Walltime (s)",
    group: "orbit",
    kind: "number",
  },
  {
    name: "orbit_local_stack",
    label: "Development broker on this machine",
    group: "orbit",
    kind: "bool",
  },
  {
    name: "orbit_job_gpus",
    label: "GPUs per job",
    group: "orbit",
    kind: "number",
    hint: "0 omits the request entirely, for a CPU-only endpoint",
  },
  {
    name: "orbit_artifact_max_bytes",
    label: "Max bytes per staged file",
    group: "orbit",
    kind: "number",
  },
  {
    name: "orbit_job_output_max_bytes",
    label: "Max bytes per job output",
    group: "orbit",
    kind: "number",
  },

  {
    name: "mpnn_command",
    label: "ProteinMPNN command",
    group: "mpnn",
    readonly: true,
    hint: "environment only — ./scripts/setup_mpnn.sh --check prints one",
  },
  {
    name: "mpnn_prologue",
    label: "Prologue",
    group: "mpnn",
    readonly: true,
    hint: "environment only — shell run first at the far end",
  },
  {
    name: "mpnn_sampling_temp",
    label: "Sampling temperature",
    group: "mpnn",
    kind: "number",
  },

  // Every one of these is read-only for the same reason the two above are: it
  // names a path the server will cd into, a module to load, or a conda
  // environment whose bin goes on PATH, all of it inside a `bash -lc` script
  // that runs on the endpoint under the site's allocation.
  {
    name: "protocol_proj_root",
    label: "Project root",
    group: "protocol",
    readonly: true,
    hint: "environment only — absolute path on the cluster; campaigns go in <root>/<name>",
  },
  {
    name: "protocol_scratch_root",
    label: "Scratch root",
    group: "protocol",
    readonly: true,
    hint: "environment only — AlphaFold3 writes to <root>/<netid>/af3/<name>",
  },
  {
    name: "protocol_conda_aifold",
    label: "Conservation / MPNN environment",
    group: "protocol",
    readonly: true,
    hint: "environment only — its bin goes on PATH",
  },
  {
    name: "protocol_conda_analysis",
    label: "Analysis environment",
    group: "protocol",
    readonly: true,
    hint: "environment only — runs the scoring notebook",
  },
  {
    name: "protocol_mpnn_path",
    label: "ProteinMPNN checkout",
    group: "protocol",
    readonly: true,
    hint: "environment only",
  },
  {
    name: "protocol_mpnn_weights",
    label: "HaloMPNN weights",
    group: "protocol",
    readonly: true,
    hint: "environment only — needed for the HaloMPNN half of each round",
  },
  {
    name: "protocol_uniref_db",
    label: "UniRef30 database",
    group: "protocol",
    readonly: true,
    hint: "environment only — what HHblits searches",
  },
  {
    name: "protocol_af3_modules",
    label: "AlphaFold3 modules",
    group: "protocol",
    readonly: true,
    hint: "environment only — comma separated module load lines",
  },
  {
    name: "protocol_af3_image",
    label: "AlphaFold3 image",
    group: "protocol",
    readonly: true,
    hint: "environment only — found under $CONTAINERDIR",
  },
  {
    name: "protocol_gpu_queue",
    label: "GPU queue",
    group: "protocol",
    readonly: true,
    hint: "environment only",
  },
  {
    name: "protocol_gpu_constraint",
    label: "GPU constraint",
    group: "protocol",
    readonly: true,
    hint: "environment only — a Slurm --constraint word, e.g. ampere|adalovelace",
  },
  {
    name: "protocol_scripts_dir",
    label: "Skill scripts checkout",
    group: "protocol",
    readonly: true,
    hint: "environment only — the enzyme-redesign-protocol repo's scripts/ directory",
  },
];

const GROUPS: { id: string; title: string; note: string }[] = [
  {
    id: "llm",
    title: "Language model",
    note: "Without a key every node uses its rule-based path. Changing this restarts the worker pool.",
  },
  {
    id: "orbit",
    title: "Remote HPC",
    note: "Applied without a restart: these are read only by the server process.",
  },
  {
    id: "mpnn",
    title: "ProteinMPNN",
    note: "Used only when an endpoint is attached; without one the round falls back to the heuristic proposer and says so. The command and prologue name shell to run, so they are environment-only and shown here read-only.",
  },
  {
    id: "protocol",
    title: "Enzyme redesign protocol",
    note: "Where the campaign's files and tools live on the cluster. All of it names paths or shell the server runs on the endpoint, so it is environment-only and shown here read-only. See plans/AMAREL_ENDPOINT.md.",
  },
  { id: "fold", title: "Structure prediction", note: "" },
];

const PROBE_LABEL: Record<ProbeResult["state"], string> = {
  ok: "works",
  absent: "not configured",
  rejected: "refused",
  error: "unreachable",
  skipped: "not checked here",
};

export default function SettingsPanel({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const [view, setView] = useState<SettingsView | null>(null);
  const [draft, setDraft] = useState<Record<string, string | boolean | number>>({});
  const [probes, setProbes] = useState<Record<string, ProbeResult>>({});
  const [admin, setAdmin] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"" | "saving" | "testing">("");
  const [needsForce, setNeedsForce] = useState(false);

  const reload = useCallback(async () => {
    const body = await fetchSettings();
    setView(body);
    setDraft({});
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const dirty = Object.keys(draft).length > 0;

  function set(name: string, value: string | boolean | number) {
    setDraft((previous) => ({ ...previous, [name]: value }));
    setNeedsForce(false);
  }

  function currentValue(field: Field): string | boolean | number {
    if (field.name in draft) return draft[field.name];
    const entry = view?.credentials[field.group]?.[field.name];
    if (!entry || isSecret(entry)) return field.kind === "bool" ? false : "";
    return entry.value;
  }

  async function apply(force = false) {
    setBusy("saving");
    setMessage("");
    const result = await saveSettings(draft, { force, admin: admin || undefined });
    setBusy("");
    const body = result.body;
    // Narrowed rather than cast: the three response shapes share almost nothing,
    // so a cast here would have been a guess about which one arrived.
    if ("running" in body) {
      setNeedsForce(true);
      setMessage(
        `${body.running.length} task(s) are still running. Applying now abandons them.`,
      );
      return;
    }
    if (!result.ok || !("applied" in body)) {
      const detail = "detail" in body ? body.detail : undefined;
      setMessage(detail ?? `the server refused the change (${result.status})`);
      return;
    }
    const restarted = body.restarted.length ? body.restarted.join(", ") : "nothing";
    const sessions = body.sessions_preserved
      ? ""
      : " Conversations were in memory and are gone.";
    setMessage(`Applied. Restarted: ${restarted}.${sessions}`);
    setNeedsForce(false);
    await reload();
    onChanged();
  }

  async function probe() {
    setBusy("testing");
    setMessage("");
    setProbes(await testSettings(draft, admin || undefined));
    setBusy("");
  }

  async function reset() {
    setBusy("saving");
    const ok = await clearSettings(admin || undefined);
    setBusy("");
    setMessage(ok ? "Back to the environment and .env." : "the server refused the reset");
    await reload();
    onChanged();
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div
        className="sheet"
        role="dialog"
        aria-label="Settings"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="sheet-head">
          <strong>Credentials</strong>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </header>

        <p className="muted sheet-intro">
          Values here live in this server process only. They are not written to disk —
          put them in <code>.env</code> to survive a restart.
        </p>

        {GROUPS.map((group) => (
          <section key={group.id} className="sheet-group">
            <h3>
              {group.title}
              {probes[group.id] && (
                <span className={`badge probe-${probes[group.id].state}`}>
                  {PROBE_LABEL[probes[group.id].state]}
                </span>
              )}
            </h3>
            {group.note && <p className="muted">{group.note}</p>}
            {probes[group.id]?.detail && (
              <p className="muted probe-detail">{probes[group.id].detail}</p>
            )}

            {FIELDS.filter((field) => field.group === group.id).map((field) => {
              const entry = view?.credentials[field.group]?.[field.name];
              const source = entry?.source ?? "default";
              const lastError = entry?.last_error;
              return (
                <label key={field.name} className="sheet-row">
                  <span className="sheet-label">
                    {field.label}
                    {source === "override" && <em className="tag">set here</em>}
                    {source === "env" && <em className="tag">from .env</em>}
                    {field.readonly && <em className="tag">read only</em>}
                  </span>
                  {field.kind === "bool" ? (
                    <input
                      type="checkbox"
                      checked={Boolean(currentValue(field))}
                      onChange={(event) => set(field.name, event.target.checked)}
                    />
                  ) : (
                    <input
                      type={field.secret ? "password" : "text"}
                      disabled={field.readonly}
                      inputMode={field.kind === "number" ? "numeric" : undefined}
                      value={
                        field.name in draft ? String(draft[field.name]) : field.secret ? "" : String(currentValue(field))
                      }
                      placeholder={
                        entry && isSecret(entry) && entry.present
                          ? entry.hint
                          : field.hint ?? ""
                      }
                      onChange={(event) =>
                        set(
                          field.name,
                          field.kind === "number"
                            ? Number(event.target.value || 0)
                            : event.target.value,
                        )
                      }
                    />
                  )}
                  {lastError && <span className="sheet-error">{lastError}</span>}
                </label>
              );
            })}
          </section>
        ))}

        <section className="sheet-group">
          <label className="sheet-row">
            <span className="sheet-label">Admin token</span>
            <input
              type="password"
              value={admin}
              placeholder="only when the server is not on loopback"
              onChange={(event) => setAdmin(event.target.value)}
            />
          </label>
        </section>

        {message && <p className="sheet-message">{message}</p>}

        <footer className="sheet-foot">
          <button className="link" onClick={reset} disabled={busy !== ""}>
            Reset to .env
          </button>
          <span className="sheet-spacer" />
          <button className="link" onClick={probe} disabled={busy !== ""}>
            {busy === "testing" ? "Testing…" : "Test"}
          </button>
          <button
            className="primary"
            onClick={() => apply(needsForce)}
            disabled={busy !== "" || !dirty}
          >
            {busy === "saving"
              ? "Applying…"
              : needsForce
                ? "Apply anyway"
                : "Apply"}
          </button>
        </footer>
      </div>
    </div>
  );
}
