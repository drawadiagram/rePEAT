import { useEffect, useRef, useState } from "react";
import { fetchArtifactJson } from "../lib/api";
import type { MolSpec } from "../lib/types";

/**
 * Mol* viewer driven by a declarative spec.
 *
 * The agent never ships JavaScript: it produces a spec (structures, residue
 * highlights, representation) and this component translates it into
 * rcsb-molstar calls. The UMD bundle is loaded once from a CDN and cached on
 * window so remounts are cheap.
 */

const VERSION = "2.14.7";
const BASE = `https://cdn.jsdelivr.net/npm/@rcsb/rcsb-molstar@${VERSION}/build/dist/viewer`;

declare global {
  interface Window {
    rcsbMolstar?: any;
    __molstarLoader?: Promise<any>;
  }
}

function loadMolstar(): Promise<any> {
  if (window.rcsbMolstar) return Promise.resolve(window.rcsbMolstar);
  if (window.__molstarLoader) return window.__molstarLoader;

  window.__molstarLoader = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${BASE}/rcsb-molstar.css`;
    document.head.appendChild(css);

    const script = document.createElement("script");
    script.src = `${BASE}/rcsb-molstar.js`;
    script.async = true;
    script.onload = () =>
      window.rcsbMolstar
        ? resolve(window.rcsbMolstar)
        : reject(new Error("rcsb-molstar loaded but exposed no global"));
    script.onerror = () => reject(new Error("could not load the Mol* viewer bundle"));
    document.head.appendChild(script);
  });
  return window.__molstarLoader;
}

/** "#rrggbb" -> the 0xRRGGBB integer Mol* colour themes expect. */
function hexToInt(hex: string): number {
  return parseInt(hex.replace("#", ""), 16) || 0x4477aa;
}

export default function MolstarView({ spec }: { spec: MolSpec }) {
  const host = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  // One viewer per mount.
  useEffect(() => {
    let disposed = false;

    loadMolstar()
      .then((molstar) => {
        if (disposed || !host.current) return;
        viewerRef.current = new molstar.Viewer(host.current, {
          layoutShowControls: false,
          layoutShowSequence: true,
          showWelcomeToast: false,
          // Required when running without RCSB's sierra backend; otherwise the
          // viewer tries to reach their validation/annotation services.
          detachedFromSierra: true,
        });
        setReady(true);
      })
      .catch((err) => {
        if (!disposed) {
          setError(String(err?.message ?? err));
          setBusy(false);
        }
      });

    return () => {
      disposed = true;
      try {
        viewerRef.current?.clear?.();
      } catch {
        /* already torn down */
      }
      viewerRef.current = null;
    };
  }, []);

  // Apply the spec once the viewer exists, and again whenever it changes.
  useEffect(() => {
    if (!ready) return;
    const viewer = viewerRef.current;
    if (!viewer) return;

    let cancelled = false;

    (async () => {
      setBusy(true);
      setError("");
      try {
        await viewer.clear();

        for (const structure of spec.structures ?? []) {
          if (cancelled) return;
          if (structure.source === "pdb" && structure.value) {
            await viewer.loadPdbId(structure.value, {
              props: { kind: "standard", assemblyId: "1" },
            });
          } else if (structure.source === "artifact" && structure.value) {
            // The backend inlined the coordinates as a JSON artifact.
            const payload = await fetchArtifactJson<{ format: string; data: string }>(
              `/api/artifacts/${structure.value}`,
            );
            if (!payload?.data) continue;
            await viewer.loadStructureFromData(
              payload.data,
              payload.format === "mmcif" ? "mmcif" : "pdb",
              false,
            );
          }
        }
        if (cancelled) return;

        for (const highlight of spec.highlights ?? []) {
          const targets = (highlight.residues ?? []).map((residue) => ({
            labelAsymId: highlight.chain || "A",
            labelSeqId: residue,
          }));
          if (!targets.length) continue;
          try {
            await viewer.createComponent(
              highlight.label || "highlight",
              targets,
              highlight.representation || "ball-and-stick",
            );
            // createComponent takes no colour, so the theme is set afterwards
            // through the raw plugin. Cosmetic: ignore if the API shifts.
            viewer.pluginCall?.((plugin: any) => {
              const components =
                plugin.managers.structure.hierarchy.current.structures
                  .flatMap((s: any) => s.components)
                  .filter((c: any) => c.cell?.obj?.label === highlight.label);
              if (!components.length) return;
              plugin.managers.structure.component.updateRepresentationsTheme(
                components,
                { color: "uniform", colorParams: { value: hexToInt(highlight.color) } },
              );
            });
          } catch {
            // A residue outside the loaded model is not fatal.
          }
        }

        const focus = spec.focus;
        if (focus?.residues?.length) {
          try {
            viewer.focusOnResidue({
              labelAsymId: focus.chain || "A",
              labelSeqId: focus.residues[0],
            });
          } catch {
            /* focus is cosmetic */
          }
        }
        viewer.resetCamera?.(0);
      } catch (err: any) {
        if (!cancelled) setError(String(err?.message ?? err));
      } finally {
        if (!cancelled) setBusy(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, spec]);

  // Mol* needs an explicit nudge when its container resizes.
  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver(() => viewerRef.current?.handleResize?.());
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="viz">
      <div className="viz-canvas" ref={host} />
      {busy && !error && <div className="viz-overlay">Loading structure…</div>}
      {error && <div className="viz-overlay viz-error">{error}</div>}
      {(spec.highlights ?? []).length > 0 && (
        <div className="viz-legend">
          {spec.highlights.map((highlight) => (
            <span className="legend-item" key={highlight.label}>
              <i style={{ background: highlight.color }} />
              {highlight.label} ({highlight.residues?.length ?? 0})
            </span>
          ))}
        </div>
      )}
      {spec.caption && <p className="viz-caption">{spec.caption}</p>}
    </div>
  );
}
