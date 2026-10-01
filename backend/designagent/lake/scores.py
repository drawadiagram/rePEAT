"""Tier 2 of the Design History: scores, rankings and analyses.

SQLite because this tier is queried relationally ("best pLDDT across all
campaigns", "rank within round 2") and feeds the golden-set curation in tier 3.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    campaign_id TEXT NOT NULL,
    design_id   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    round       INTEGER DEFAULT 0,
    task_id     TEXT,
    created_at  TEXT,
    PRIMARY KEY (design_id, metric)
);
CREATE INDEX IF NOT EXISTS scores_metric ON scores(metric, value);
CREATE INDEX IF NOT EXISTS scores_campaign ON scores(campaign_id, round);

CREATE TABLE IF NOT EXISTS rankings (
    campaign_id TEXT NOT NULL,
    round       INTEGER NOT NULL,
    metric      TEXT NOT NULL,
    design_id   TEXT NOT NULL,
    rank        INTEGER NOT NULL,
    value       REAL,
    PRIMARY KEY (campaign_id, round, metric, design_id)
);

CREATE TABLE IF NOT EXISTS analyses (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    round       INTEGER,
    kind        TEXT,
    payload     TEXT,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS analyses_campaign ON analyses(campaign_id, round);
"""


class ScoreStore:
    """Tier 2 relational store."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- writes -------------------------------------------------------
    def record_scores(
        self,
        campaign_id: str,
        design_id: str,
        metrics: dict[str, float],
        *,
        round_no: int = 0,
        task_id: str = "",
        created_at: str = "",
    ) -> None:
        rows = [
            (campaign_id, design_id, name, float(value), round_no, task_id, created_at)
            for name, value in metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT INTO scores
                     (campaign_id, design_id, metric, value, round, task_id, created_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(design_id, metric) DO UPDATE SET
                     value=excluded.value, round=excluded.round,
                     task_id=excluded.task_id, created_at=excluded.created_at""",
                rows,
            )
            self._conn.commit()

    def record_ranking(
        self,
        campaign_id: str,
        round_no: int,
        metric: str,
        ordered_design_ids: Iterable[str],
        values: dict[str, float] | None = None,
    ) -> None:
        values = values or {}
        rows = [
            (campaign_id, round_no, metric, did, i + 1, values.get(did))
            for i, did in enumerate(ordered_design_ids)
        ]
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                """INSERT INTO rankings
                     (campaign_id, round, metric, design_id, rank, value)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(campaign_id, round, metric, design_id)
                   DO UPDATE SET rank=excluded.rank, value=excluded.value""",
                rows,
            )
            self._conn.commit()

    def record_analysis(
        self,
        analysis_id: str,
        campaign_id: str,
        kind: str,
        payload: dict,
        *,
        round_no: int = 0,
        created_at: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO analyses (id, campaign_id, round, kind, payload, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                (
                    analysis_id,
                    campaign_id,
                    round_no,
                    kind,
                    json.dumps(payload, default=str),
                    created_at,
                ),
            )
            self._conn.commit()

    # --- reads --------------------------------------------------------
    def scores_for_design(self, design_id: str) -> dict[str, float]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT metric, value FROM scores WHERE design_id = ?", (design_id,)
            )
            return {r["metric"]: r["value"] for r in cur.fetchall()}

    def best_designs(
        self,
        metric: str,
        *,
        direction: str = "max",
        limit: int = 10,
        campaign_id: str | None = None,
        exclude_campaign: str | None = None,
    ) -> list[dict]:
        """Top designs by a metric; used to flag relevant past work."""
        order = "DESC" if direction == "max" else "ASC"
        sql = "SELECT campaign_id, design_id, metric, value, round FROM scores WHERE metric = ?"
        params: list[Any] = [metric]
        if campaign_id:
            sql += " AND campaign_id = ?"
            params.append(campaign_id)
        if exclude_campaign:
            sql += " AND campaign_id != ?"
            params.append(exclude_campaign)
        sql += f" ORDER BY value {order} LIMIT ?"
        params.append(int(limit))
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def campaign_scores(self, campaign_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                """SELECT design_id, metric, value, round FROM scores
                   WHERE campaign_id = ? ORDER BY round, design_id""",
                (campaign_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    def metric_stats(self, metric: str) -> dict[str, float] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT COUNT(*) n, AVG(value) mean, MIN(value) lo, MAX(value) hi
                   FROM scores WHERE metric = ?""",
                (metric,),
            ).fetchone()
        if not row or not row["n"]:
            return None
        return {"n": row["n"], "mean": row["mean"], "min": row["lo"], "max": row["hi"]}

    def analyses_for_campaign(self, campaign_id: str) -> list[dict]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM analyses WHERE campaign_id = ? ORDER BY round",
                (campaign_id,),
            )
            out = []
            for r in cur.fetchall():
                row = dict(r)
                try:
                    row["payload"] = json.loads(row["payload"] or "{}")
                except json.JSONDecodeError:
                    row["payload"] = {}
                out.append(row)
            return out
