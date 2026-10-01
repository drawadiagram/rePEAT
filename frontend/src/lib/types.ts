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

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}

/** One SSE frame from POST /api/chat. */
export type Frame =
  | { type: "token"; text: string }
  | { type: "message"; text: string }
  | { type: "status"; text: string; node?: string }
  | ({ type: "task"; event: string } & Partial<TaskChip> & { text?: string })
  | { type: "state"; node: string; state: AgentState }
  | { type: "error"; message: string }
  | { type: "done" };
