"""Tier 1 of the Design History: raw task outputs as a provenance graph.

Kuzu is embedded, so there is no server to run. Everything a task produced is
recorded here verbatim (large payloads live in the blob dir and are referenced
by path), and tiers 2 and 3 are derived from it.

Kuzu allows a single writer, so all access goes through one connection guarded
by a lock. Calls are sync and short; callers in async code wrap them with
`asyncio.to_thread` where it matters.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Node and relationship tables. `properties` columns hold JSON blobs so a task
# can record arbitrary output without a migration.
_SCHEMA = [
    """CREATE NODE TABLE IF NOT EXISTS Campaign(
         id STRING, goal STRING, created_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Reference(
         id STRING, pdb_id STRING, uniprot_id STRING, sequence STRING,
         properties STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Design(
         id STRING, sequence STRING, mutations STRING, round INT64,
         created_at STRING, properties STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Task(
         id STRING, name STRING, interface STRING, state STRING,
         params STRING, submitted_at STRING, finished_at STRING,
         error STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Output(
         id STRING, kind STRING, blob_path STRING, summary STRING,
         properties STRING, created_at STRING, PRIMARY KEY(id))""",
    """CREATE NODE TABLE IF NOT EXISTS Structure(
         id STRING, format STRING, blob_path STRING, source STRING,
         properties STRING, PRIMARY KEY(id))""",
    "CREATE REL TABLE IF NOT EXISTS IN_CAMPAIGN(FROM Design TO Campaign)",
    "CREATE REL TABLE IF NOT EXISTS REF_OF(FROM Reference TO Campaign)",
    "CREATE REL TABLE IF NOT EXISTS DERIVED_FROM(FROM Design TO Design, how STRING)",
    "CREATE REL TABLE IF NOT EXISTS BASED_ON(FROM Design TO Reference)",
    "CREATE REL TABLE IF NOT EXISTS PRODUCED(FROM Task TO Output)",
    "CREATE REL TABLE IF NOT EXISTS RAN_FOR(FROM Task TO Campaign)",
    "CREATE REL TABLE IF NOT EXISTS OUTPUT_FOR(FROM Output TO Design)",
    "CREATE REL TABLE IF NOT EXISTS HAS_STRUCTURE(FROM Design TO Structure)",
]


class GraphStore:
    """Tier 1 provenance graph."""

    def __init__(self, path: Path):
        import kuzu

        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self._path))
        self._conn = kuzu.Connection(self._db)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            for stmt in _SCHEMA:
                self._conn.execute(stmt)

    # --- low level ----------------------------------------------------
    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Run Cypher and return rows as dicts keyed by the RETURN expressions."""
        with self._lock:
            result = self._conn.execute(cypher, params or {})
            cols = result.get_column_names()
            rows = []
            while result.has_next():
                rows.append(dict(zip(cols, result.get_next())))
            return rows

    def execute(self, cypher: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(cypher, params or {})

    def close(self) -> None:
        with self._lock:
            # Dropping references lets Kuzu release the lock file.
            self._conn = None
            self._db = None

    # --- writes -------------------------------------------------------
    def upsert_campaign(self, campaign_id: str, goal: str, created_at: str) -> None:
        self.execute(
            """MERGE (c:Campaign {id: $id})
               ON CREATE SET c.goal = $goal, c.created_at = $ts
               ON MATCH  SET c.goal = $goal""",
            {"id": campaign_id, "goal": goal, "ts": created_at},
        )

    def upsert_reference(self, ref_id: str, reference: dict, campaign_id: str | None) -> None:
        self.execute(
            """MERGE (r:Reference {id: $id})
               ON CREATE SET r.pdb_id = $pdb, r.uniprot_id = $uni,
                             r.sequence = $seq, r.properties = $props
               ON MATCH  SET r.pdb_id = $pdb, r.uniprot_id = $uni,
                             r.sequence = $seq, r.properties = $props""",
            {
                "id": ref_id,
                "pdb": reference.get("pdb_id", ""),
                "uni": reference.get("uniprot_id", ""),
                "seq": reference.get("sequence", ""),
                "props": json.dumps(_jsonable(reference)),
            },
        )
        if campaign_id:
            self.execute(
                """MATCH (r:Reference {id: $rid}), (c:Campaign {id: $cid})
                   MERGE (r)-[:REF_OF]->(c)""",
                {"rid": ref_id, "cid": campaign_id},
            )

    def upsert_task(
        self,
        task_id: str,
        *,
        name: str,
        interface: str,
        state: str,
        params: dict | None = None,
        submitted_at: str = "",
        finished_at: str = "",
        error: str = "",
        campaign_id: str | None = None,
    ) -> None:
        self.execute(
            """MERGE (t:Task {id: $id})
               ON CREATE SET t.name = $name, t.interface = $iface, t.state = $state,
                             t.params = $params, t.submitted_at = $sub,
                             t.finished_at = $fin, t.error = $err
               ON MATCH  SET t.state = $state, t.finished_at = $fin, t.error = $err""",
            {
                "id": task_id,
                "name": name,
                "iface": interface,
                "state": state,
                "params": json.dumps(_jsonable(params or {})),
                "sub": submitted_at,
                "fin": finished_at,
                "err": error or "",
            },
        )
        if campaign_id:
            self.execute(
                """MATCH (t:Task {id: $tid}), (c:Campaign {id: $cid})
                   MERGE (t)-[:RAN_FOR]->(c)""",
                {"tid": task_id, "cid": campaign_id},
            )

    def add_output(
        self,
        output_id: str,
        *,
        task_id: str,
        kind: str,
        blob_path: str = "",
        summary: str = "",
        properties: dict | None = None,
        created_at: str = "",
        design_id: str | None = None,
    ) -> None:
        self.execute(
            """MERGE (o:Output {id: $id})
               ON CREATE SET o.kind = $kind, o.blob_path = $blob, o.summary = $sum,
                             o.properties = $props, o.created_at = $ts
               ON MATCH  SET o.kind = $kind, o.blob_path = $blob, o.summary = $sum,
                             o.properties = $props""",
            {
                "id": output_id,
                "kind": kind,
                "blob": blob_path,
                "sum": summary[:4000],
                "props": json.dumps(_jsonable(properties or {})),
                "ts": created_at,
            },
        )
        self.execute(
            """MATCH (t:Task {id: $tid}), (o:Output {id: $oid})
               MERGE (t)-[:PRODUCED]->(o)""",
            {"tid": task_id, "oid": output_id},
        )
        if design_id:
            self.execute(
                """MATCH (o:Output {id: $oid}), (d:Design {id: $did})
                   MERGE (o)-[:OUTPUT_FOR]->(d)""",
                {"oid": output_id, "did": design_id},
            )

    def upsert_design(
        self,
        design: dict,
        *,
        campaign_id: str | None = None,
        reference_id: str | None = None,
        round_no: int = 0,
        created_at: str = "",
    ) -> None:
        did = design["design_id"]
        self.execute(
            """MERGE (d:Design {id: $id})
               ON CREATE SET d.sequence = $seq, d.mutations = $muts, d.round = $rnd,
                             d.created_at = $ts, d.properties = $props
               ON MATCH  SET d.sequence = $seq, d.mutations = $muts,
                             d.properties = $props""",
            {
                "id": did,
                "seq": design.get("sequence", ""),
                "muts": json.dumps(design.get("mutations", [])),
                "rnd": int(round_no),
                "ts": created_at,
                "props": json.dumps(_jsonable(design)),
            },
        )
        if campaign_id:
            self.execute(
                """MATCH (d:Design {id: $did}), (c:Campaign {id: $cid})
                   MERGE (d)-[:IN_CAMPAIGN]->(c)""",
                {"did": did, "cid": campaign_id},
            )
        if reference_id:
            self.execute(
                """MATCH (d:Design {id: $did}), (r:Reference {id: $rid})
                   MERGE (d)-[:BASED_ON]->(r)""",
                {"did": did, "rid": reference_id},
            )
        parent = design.get("parent_id")
        if parent and parent != did:
            self.execute(
                """MATCH (d:Design {id: $did}), (p:Design {id: $pid})
                   MERGE (d)-[:DERIVED_FROM {how: $how}]->(p)""",
                {"did": did, "pid": parent, "how": design.get("how", "redesign")},
            )
        if design.get("structure_path"):
            sid = f"{did}:structure"
            self.execute(
                """MERGE (s:Structure {id: $id})
                   ON CREATE SET s.format = $fmt, s.blob_path = $blob,
                                 s.source = $src, s.properties = '{}'
                   ON MATCH  SET s.blob_path = $blob""",
                {
                    "id": sid,
                    "fmt": design.get("structure_format", "pdb"),
                    "blob": design["structure_path"],
                    "src": design.get("structure_source", "predicted"),
                },
            )
            self.execute(
                """MATCH (d:Design {id: $did}), (s:Structure {id: $sid})
                   MERGE (d)-[:HAS_STRUCTURE]->(s)""",
                {"did": did, "sid": sid},
            )

    # --- reads --------------------------------------------------------
    def designs_in_campaign(self, campaign_id: str) -> list[dict]:
        rows = self.query(
            """MATCH (d:Design)-[:IN_CAMPAIGN]->(c:Campaign {id: $cid})
               RETURN d.id, d.sequence, d.round, d.properties
               ORDER BY d.round""",
            {"cid": campaign_id},
        )
        return [
            {
                "design_id": r["d.id"],
                "sequence": r["d.sequence"],
                "round": r["d.round"],
                **_loads(r["d.properties"]),
            }
            for r in rows
        ]

    def lineage(self, design_id: str, depth: int = 5) -> list[dict]:
        """Ancestors of a design, nearest first."""
        rows = self.query(
            f"""MATCH (d:Design {{id: $did}})-[:DERIVED_FROM*1..{int(depth)}]->(a:Design)
                RETURN a.id, a.round, a.properties""",
            {"did": design_id},
        )
        return [
            {"design_id": r["a.id"], "round": r["a.round"], **_loads(r["a.properties"])}
            for r in rows
        ]

    def tasks_for_campaign(self, campaign_id: str) -> list[dict]:
        rows = self.query(
            """MATCH (t:Task)-[:RAN_FOR]->(c:Campaign {id: $cid})
               RETURN t.id, t.name, t.interface, t.state, t.error""",
            {"cid": campaign_id},
        )
        return [
            {
                "id": r["t.id"],
                "name": r["t.name"],
                "interface": r["t.interface"],
                "state": r["t.state"],
                "error": r["t.error"],
            }
            for r in rows
        ]

    def outputs_for_task(self, task_id: str) -> list[dict]:
        rows = self.query(
            """MATCH (t:Task {id: $tid})-[:PRODUCED]->(o:Output)
               RETURN o.id, o.kind, o.blob_path, o.summary, o.properties""",
            {"tid": task_id},
        )
        return [
            {
                "id": r["o.id"],
                "kind": r["o.kind"],
                "blob_path": r["o.blob_path"],
                "summary": r["o.summary"],
                "properties": _loads(r["o.properties"]),
            }
            for r in rows
        ]

    def find_designs_by_sequence(self, sequence: str) -> list[dict]:
        """Exact sequence matches across all campaigns (dedupe / prior-art check)."""
        rows = self.query(
            """MATCH (d:Design) WHERE d.sequence = $seq
               RETURN d.id, d.round, d.properties""",
            {"seq": sequence},
        )
        return [
            {"design_id": r["d.id"], "round": r["d.round"], **_loads(r["d.properties"])}
            for r in rows
        ]

    def campaigns(self) -> list[dict]:
        rows = self.query(
            "MATCH (c:Campaign) RETURN c.id, c.goal, c.created_at ORDER BY c.created_at DESC"
        )
        return [
            {"id": r["c.id"], "goal": r["c.goal"], "created_at": r["c.created_at"]}
            for r in rows
        ]


def _loads(raw: Any) -> dict:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _jsonable(obj: Any) -> Any:
    """Make task payloads safe for json.dumps (Paths, sets, numpy scalars)."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (set, frozenset)):
        return sorted(str(v) for v in obj)
    item = getattr(obj, "item", None)  # numpy scalar
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return str(obj)


def jsonable(obj: Any) -> Any:
    """Public alias; other modules sanitize payloads the same way."""
    return _jsonable(obj)
