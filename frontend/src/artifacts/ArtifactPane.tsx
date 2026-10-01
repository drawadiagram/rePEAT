import { useEffect, useMemo, useState } from "react";
import { fetchArtifactJson, fetchArtifactText } from "../lib/api";
import type { AgentState, ArtifactRef } from "../lib/types";
import MarkdownView from "./MarkdownView";
import MolstarView from "./MolstarView";
import TableView from "./TableView";

/**
 * The right-hand pane: one tab per artifact.
 *
 * The live molecular visualization comes from state rather than from the stored
 * artifact, so the view updates as soon as the analyst recomputes it.
 */
export default function ArtifactPane({
  state,
  onClose,
}: {
  state: AgentState;
  onClose: () => void;
}) {
  const artifacts = state.artifacts ?? [];
  const viz = state.molecular_visualization;

  // Tabs: the live viewer first (if any), then every non-molstar artifact.
  const tabs = useMemo(() => {
    const out: { id: string; title: string; kind: string; artifact?: ArtifactRef }[] = [];
    if (viz?.structures?.length) {
      out.push({ id: "live-viz", title: viz.title || "Structure", kind: "molstar" });
    }
    for (const artifact of artifacts) {
      if (artifact.kind === "molstar") continue; // superseded by the live viewer
      if (artifact.kind === "json") continue; // inlined coordinate payloads
      out.push({
        id: artifact.id,
        title: artifact.title,
        kind: artifact.kind,
        artifact,
      });
    }
    return out;
  }, [artifacts, viz]);

  const [activeId, setActiveId] = useState<string | null>(null);

  // Follow new artifacts, but never steal a tab the user deliberately chose.
  useEffect(() => {
    if (!tabs.length) {
      setActiveId(null);
      return;
    }
    if (!activeId || !tabs.some((t) => t.id === activeId)) {
      setActiveId(tabs[0].id);
    }
  }, [tabs, activeId]);

  const active = tabs.find((t) => t.id === activeId);

  return (
    <aside className="artifacts">
      <header className="artifacts-head">
        <div className="tabs" role="tablist">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={tab.id === activeId}
              className={tab.id === activeId ? "tab active" : "tab"}
              onClick={() => setActiveId(tab.id)}
              title={tab.title}
            >
              <span className="tab-kind">{kindLabel(tab.kind)}</span>
              {tab.title}
            </button>
          ))}
        </div>
        <button className="icon-btn" onClick={onClose} title="Hide panel">
          ✕
        </button>
      </header>

      <div className="artifact-body">
        {active?.kind === "molstar" && viz && <MolstarView spec={viz} />}
        {active?.artifact && <ArtifactContent artifact={active.artifact} />}
        {!active && <p className="muted">Nothing to display yet.</p>}
      </div>
    </aside>
  );
}

function kindLabel(kind: string): string {
  return (
    { molstar: "3D", markdown: "MD", docx: "DOCX", table: "TBL", json: "JSON" }[kind] ??
    kind.toUpperCase()
  );
}

function ArtifactContent({ artifact }: { artifact: ArtifactRef }) {
  const [text, setText] = useState("");
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setText("");
    setData(null);

    if (artifact.kind === "markdown") {
      fetchArtifactText(artifact.url).then((value) => {
        if (!cancelled) {
          setText(value);
          setLoading(false);
        }
      });
    } else if (artifact.kind === "table") {
      fetchArtifactJson(artifact.url).then((value) => {
        if (!cancelled) {
          setData(value);
          setLoading(false);
        }
      });
    } else {
      setLoading(false);
    }
    return () => {
      cancelled = true;
    };
  }, [artifact.id, artifact.kind, artifact.url]);

  if (artifact.kind === "docx") {
    return (
      <div className="download-card">
        <p>{artifact.title}</p>
        <p className="muted">Word document, generated from this session.</p>
        <a className="btn" href={artifact.url} download>
          Download .docx
        </a>
      </div>
    );
  }

  if (loading) return <p className="muted">Loading…</p>;
  if (artifact.kind === "markdown") return <MarkdownView text={text} />;
  if (artifact.kind === "table") return <TableView data={data} />;
  return (
    <a className="btn" href={artifact.url} download>
      Download {artifact.title}
    </a>
  );
}
