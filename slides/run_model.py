#!/usr/bin/env python3
"""Mine the on-disk Design History into slides/run.json.

The deck's two data-driven figures (F3, one round measured; F4, the three tiers)
are drawn from a real campaign rather than a mock-up. `data/` is gitignored, so
this file is what makes those figures reproducible: run it to regenerate
run.json, then build the deck.

    python3 slides/run_model.py
    NODE_PATH=<dir with pptxgenjs> node slides/build_deck.js

Two notes on reading the lake from outside the app:

  * Kuzu takes an exclusive file lock. A running backend holds it, so this
    copies the database aside and opens the copy. Never point this at the live
    directory -- it would either fail or fight the server for the lock.
  * Tier 2 is plain SQLite and tier 3 is plain Parquet + JSON, so both are read
    in place. That asymmetry is itself a deck point: only tier 1 needs a server.

**Re-run this deliberately, not reflexively.** It aggregates the *whole* lake and
takes whichever campaign Kuzu lists first, so on a data dir that has run anything
since, every figure in the deck changes: as of 2026-10-09 the same command turns
the committed 12 designs and 114 scores into 222 and 2244, and the pLDDT
progression the deck narrates into a different campaign's. The committed
run.json's tiers are the 2026-10-01 campaign on purpose; only its `code` block
was refreshed in place. Pinning the campaign id is backlog M2.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAKE = ROOT / "data" / "lake"
OUT = Path(__file__).resolve().parent / "run.json"


# --------------------------------------------------------------- tier 1 (Kuzu)


def read_graph() -> dict:
    """Node/rel counts and the task ledger, from a lock-free copy."""
    try:
        import kuzu
    except ImportError:
        return {"available": False, "reason": "kuzu is not importable"}

    source = LAKE / "graph"
    if not source.exists():
        return {"available": False, "reason": f"{source} does not exist"}

    tmp = Path(tempfile.mkdtemp(prefix="deck-graph-"))
    try:
        shutil.copy2(source, tmp / "graph")
        wal = LAKE / "graph.wal"
        if wal.exists():
            shutil.copy2(wal, tmp / "graph.wal")

        conn = kuzu.Connection(kuzu.Database(str(tmp / "graph")))

        def rows(cypher: str) -> list:
            result = conn.execute(cypher)
            out = []
            while result.has_next():
                out.append(result.get_next())
            return out

        nodes = {
            table: rows(f"MATCH (n:{table}) RETURN count(n)")[0][0]
            for table in ("Campaign", "Reference", "Design", "Task", "Output", "Structure")
        }
        rels = {}
        for rel, pattern in (
            ("IN_CAMPAIGN", "(:Design)-[r:IN_CAMPAIGN]->(:Campaign)"),
            ("REF_OF", "(:Reference)-[r:REF_OF]->(:Campaign)"),
            ("DERIVED_FROM", "(:Design)-[r:DERIVED_FROM]->(:Design)"),
            ("BASED_ON", "(:Design)-[r:BASED_ON]->(:Reference)"),
            ("PRODUCED", "(:Task)-[r:PRODUCED]->(:Output)"),
            ("RAN_FOR", "(:Task)-[r:RAN_FOR]->(:Campaign)"),
            ("OUTPUT_FOR", "(:Output)-[r:OUTPUT_FOR]->(:Design)"),
            ("HAS_STRUCTURE", "(:Design)-[r:HAS_STRUCTURE]->(:Structure)"),
        ):
            rels[rel] = rows(f"MATCH {pattern} RETURN count(r)")[0][0]

        tasks = [
            {
                "id": r[0],
                "name": r[1],
                "interface": r[2],
                "state": r[3],
                "submitted_at": r[4],
                "finished_at": r[5],
            }
            for r in rows(
                "MATCH (t:Task) RETURN t.id, t.name, t.interface, t.state, "
                "t.submitted_at, t.finished_at ORDER BY t.submitted_at, t.id"
            )
        ]
        campaign = rows("MATCH (c:Campaign) RETURN c.id, c.goal, c.created_at")
        reference = rows(
            "MATCH (r:Reference) RETURN r.id, r.pdb_id, r.uniprot_id, size(r.sequence)"
        )
        return {
            "available": True,
            "nodes": nodes,
            "rels": rels,
            "n_node_tables": len(nodes),
            "n_rel_tables": len(rels),
            "tasks": tasks,
            "campaign": (
                {"id": campaign[0][0], "goal": campaign[0][1], "created_at": campaign[0][2]}
                if campaign
                else None
            ),
            "reference": (
                {
                    "id": reference[0][0],
                    "pdb_id": reference[0][1],
                    "uniprot_id": reference[0][2],
                    "length": reference[0][3],
                }
                if reference
                else None
            ),
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------- tier 2 (SQLite)


def read_scores() -> dict:
    path = LAKE / "scores.sqlite"
    if not path.exists():
        return {"available": False, "reason": f"{path} does not exist"}

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        counts = {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("scores", "rankings", "analyses")
        }
        metrics = [
            {"metric": m, "n": n, "min": lo, "max": hi}
            for m, n, lo, hi in conn.execute(
                "SELECT metric, count(*), min(value), max(value) FROM scores "
                "GROUP BY metric ORDER BY metric"
            )
        ]
        by_metric: dict[str, list] = {}
        for metric in ("plddt", "rmsd_to_reference", "sequence_identity"):
            by_metric[metric] = [
                {"design_id": d, "value": v}
                for d, v in conn.execute(
                    "SELECT design_id, value FROM scores WHERE metric = ? "
                    "ORDER BY design_id",
                    (metric,),
                )
            ]
        analyses = [
            json.loads(payload)
            for (payload,) in conn.execute(
                "SELECT payload FROM analyses WHERE kind = 'round_summary' ORDER BY round"
            )
        ]
        return {
            "available": True,
            "counts": counts,
            "n_metrics": len(metrics),
            "metrics": metrics,
            "by_metric": by_metric,
            "rounds": [
                {
                    "round": a.get("round"),
                    "metric": a.get("metric"),
                    "n_designs": a.get("n_designs"),
                    "improved": a.get("improved"),
                    "best_design": (a.get("best") or {}).get("design_id"),
                    "best_value": ((a.get("best") or {}).get("metrics") or {}).get(
                        a.get("metric", "")
                    ),
                }
                for a in analyses
            ],
        }
    finally:
        conn.close()


# ------------------------------------------------------------ tier 3 (Parquet)


def read_golden() -> dict:
    root = LAKE / "golden"
    if not root.exists():
        return {"available": False, "reason": f"{root} does not exist"}
    sets = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text())
        parquet = directory / "designs.parquet"
        sets.append(
            {
                "id": manifest.get("id"),
                "campaign_id": manifest.get("campaign_id"),
                "n_rows": manifest.get("n_rows"),
                "metrics": manifest.get("metrics", []),
                "rules": manifest.get("rules", {}),
                "parquet_bytes": parquet.stat().st_size if parquet.exists() else 0,
            }
        )
    return {"available": bool(sets), "sets": sets}


# ------------------------------------------------------------------- the rest


def read_blobs() -> dict:
    root = ROOT / "data" / "blobs"
    if not root.exists():
        return {"available": False, "n": 0}
    files = sorted(p for p in root.iterdir() if p.is_file())
    total = sum(p.stat().st_size for p in files)
    kinds: dict[str, int] = {}
    for p in files:
        kinds[p.suffix.lstrip(".") or "none"] = kinds.get(p.suffix.lstrip(".") or "none", 0) + 1
    return {"available": True, "n": len(files), "bytes": total, "by_suffix": kinds}


def read_artifacts() -> dict:
    index = ROOT / "data" / "artifacts" / "index.json"
    if not index.exists():
        return {"available": False, "n": 0}
    records = json.loads(index.read_text())
    items = records.values() if isinstance(records, dict) else records
    kinds: dict[str, int] = {}
    for record in items:
        kind = record.get("kind", "?")
        kinds[kind] = kinds.get(kind, 0) + 1
    return {"available": True, "n": len(list(items)), "by_kind": kinds}


def code_sizes() -> dict:
    """Line counts for the status slide, measured rather than remembered."""

    def count(paths) -> int:
        return sum(
            sum(1 for _ in p.open(errors="replace")) for p in paths if p.is_file()
        )

    backend = ROOT / "backend" / "designagent"
    areas = {
        "graph": sorted((backend / "graph").glob("*.py"))
        + sorted((backend / "graph" / "nodes").glob("*.py")),
        "tasks": sorted((backend / "tasks").glob("*.py"))
        + sorted((backend / "tasks" / "hpc").glob("*.py")),
        "tools": sorted((backend / "tools").glob("*.py")),
        "lake": sorted((backend / "lake").glob("*.py")),
        "artifacts": sorted((backend / "artifacts").glob("*.py")),
        "app": [backend / f for f in ("app.py", "runtime.py", "config.py", "llm.py", "__main__.py")],
    }
    out = {name: count(paths) for name, paths in areas.items()}
    out["backend_total"] = sum(out.values())
    out["tests"] = count(sorted((ROOT / "tests").glob("*.py")))
    out["frontend"] = count(
        p for p in (ROOT / "frontend" / "src").rglob("*") if p.suffix in {".tsx", ".ts", ".css"}
    )
    return out


def main() -> int:
    model = {
        # Repo-relative: an absolute path names the operator's home directory, and this
        # file is committed to a public repo (backlog A24).
        "generated_from": str(LAKE.relative_to(ROOT) if LAKE.is_relative_to(ROOT) else LAKE),
        "tier1": read_graph(),
        "tier2": read_scores(),
        "tier3": read_golden(),
        "blobs": read_blobs(),
        "artifacts": read_artifacts(),
        "code": code_sizes(),
    }
    OUT.write_text(json.dumps(model, indent=2, sort_keys=False) + "\n")

    t1, t2, t3 = model["tier1"], model["tier2"], model["tier3"]
    print(f"wrote {OUT}")
    if t1.get("available"):
        print(
            f"  tier 1: {t1['nodes']} over {t1['n_node_tables']} node tables / "
            f"{t1['n_rel_tables']} rel tables; {len(t1['tasks'])} tasks"
        )
    else:
        print(f"  tier 1: UNAVAILABLE -- {t1.get('reason')}")
    if t2.get("available"):
        print(f"  tier 2: {t2['counts']} over {t2['n_metrics']} metrics")
        for r in t2["rounds"]:
            print(
                f"          round {r['round']}: {r['n_designs']} designs, best "
                f"{r['metric']} {r['best_value']} ({r['best_design']}), "
                f"improved={r['improved']}"
            )
    else:
        print(f"  tier 2: UNAVAILABLE -- {t2.get('reason')}")
    if t3.get("available"):
        for s in t3["sets"]:
            print(f"  tier 3: {s['id']} -- {s['n_rows']} rows, {len(s['metrics'])} metrics")
    else:
        print(f"  tier 3: UNAVAILABLE -- {t3.get('reason')}")
    print(f"  blobs: {model['blobs'].get('n')} · artifacts: {model['artifacts'].get('n')}")
    print(f"  code: {model['code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
