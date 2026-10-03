export type ArtifactKind = "markdown" | "docx" | "molstar" | "table" | "json";

export interface ArtifactRef {
  id: string;
  kind: ArtifactKind;
  title: string;
  url: string;
  created_at?: string;
}

export interface Highlight {
  chain: string;
  residues: number[];
  color: string;
  label: string;
  representation: string;
}

export interface StructureSource {
  source: "pdb" | "artifact";
  value: string;
  format: string;
  label?: string;
  path?: string;
}

export interface MolSpec {
  title: string;
  structures: StructureSource[];
  highlights: Highlight[];
  representation: string;
  focus?: { chain: string; residues: number[] } | null;
  caption?: string;
  artifact_id?: string;
}

export interface Design {
  design_id: string;
  sequence?: string;
  mutations?: string[];
  metrics?: Record<string, number>;
  provenance?: { rationale?: string; source?: string; round?: number };
}

export interface KeyMetric {
  name?: string;
  direction?: "max" | "min";
  target?: number | null;
  description?: string;
}

export interface ReferenceDesign {
  name?: string;
  pdb_id?: string;
  uniprot_id?: string;
  organism?: string;
  length?: number;
  function?: string;
  literature?: { title?: string; year?: string; doi?: string; relevance?: string }[];
}

/** One node's contribution to a turn: counters and names, never payloads. */
export interface TraceEntry {
  node: string;
  ms: number;
  goto?: string;
  intent?: string;
  round?: number;
  reply_source?: string;
  status?: string;
  n_messages?: number;
  n_warnings?: number;
  n_artifacts?: number;
  n_ensemble?: number;
  n_worklist?: number;
}

export interface AgentState {
  reference_design?: ReferenceDesign;
  key_metric?: KeyMetric;
  lead_design?: Design;
  ensemble?: Design[];
  molecular_visualization?: MolSpec;
  artifacts?: ArtifactRef[];
  design_summary?: string;
  round?: number;
  intent?: string;
  status?: string;
  /** Which node and function authored the last reply, e.g. "interpreter:llm". */
  reply_source?: string;
  /** This turn's node path. Reset every turn, server-side. */
  trace?: TraceEntry[];
}

export interface TaskChip {
  id: string;
  task: string;
  label?: string;
  interface: string;
  state: string;
  elapsed: number;
  error?: string;
  log_tail?: string;
}

/** A secret, as the server is willing to describe it: never the value. */
export interface SecretField {
  present: boolean;
  hint: string;
  source: "default" | "env" | "override";
  last_error?: string;
}

export interface PlainField {
  value: string | number | boolean;
  source: "default" | "env" | "override";
  last_error?: string;
}

export type CredentialGroup = Record<string, SecretField | PlainField>;

export interface SettingsView {
  credentials: Record<string, CredentialGroup>;
  overrides: string[];
  hpc_available: boolean;
  llm_available: boolean;
  persistent_sessions: boolean;
}

/** `PUT`/`DELETE /api/settings` applied the change and reports what restarted. */
export interface SettingsApplied extends SettingsView {
  applied: true;
  restarted: string[];
  sessions_preserved: boolean;
}

/** 409: tasks are still in flight, so the change was not applied. */
export interface SettingsRefused {
  applied: false;
  reason: string;
  running: string[];
}

/**
 * One of three shapes, and they share almost nothing — which is why this is a
 * union rather than one interface with everything optional. The refusal carries
 * none of `SettingsView`'s fields, so a single interface made the panel cast its
 * way through every branch and type-checked a stub that could never arrive.
 */
export type SettingsResponse =
  | SettingsApplied
  | SettingsRefused
  | { detail?: string };

export interface ProbeResult {
  state: "ok" | "absent" | "rejected" | "error" | "skipped";
  detail: string;
}

export function isSecret(field: SecretField | PlainField): field is SecretField {
  return "present" in field;
}

/** A progress line with the node that emitted it. */
export interface StatusLine {
  text: string;
  node?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
  /** The node that authored it, and the function inside that node. */
  node?: string;
  source?: string;
  /** Kept with the message so a finished turn can still explain itself: the
   *  progress lines it emitted and the path it took. */
  statuses?: StatusLine[];
  trace?: TraceEntry[];
}

/** One SSE frame from POST /api/chat. */
export type Frame =
  | { type: "token"; text: string; node?: string }
  | { type: "message"; text: string; node?: string; source?: string }
  | { type: "status"; text: string; node?: string }
  | ({ type: "task"; event: string } & Partial<TaskChip> & { text?: string })
  | { type: "state"; node: string; state: AgentState }
  | { type: "error"; message: string }
  | { type: "done" };
