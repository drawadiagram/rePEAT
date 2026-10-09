"""The Design History facade.

Nodes talk to this, not to the individual tiers. It also owns the blob
directory, because tier 1 stores paths and someone has to write the bytes.

Writes are sync (SQLite/Kuzu are local and fast); `record_task_result` is the
one call a node makes per completed task and it writes tiers 1 and 2 together
so they cannot drift.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..config import Settings, get_settings
from .golden import CurationRules, GoldenStore
from .graph import GraphStore, jsonable
from .scores import ScoreStore

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DesignHistory:
    """Tiered data lake: graph (raw) -> scores (derived) -> golden (curated)."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.graph = GraphStore(
            self.settings.graph_db_path, buffer_pool_mb=self.settings.kuzu_buffer_pool_mb
        )
        self.scores = ScoreStore(self.settings.scores_db_path)
        self.golden = GoldenStore(self.settings.golden_dir)
        self._blobs = self.settings.blobs_dir

    def close(self) -> None:
        self.graph.close()
        self.scores.close()

    # --- blobs --------------------------------------------------------
    def write_blob(self, data: str | bytes, *, suffix: str, prefix: str = "blob") -> str:
        """Store raw bytes content-addressed; returns a path string for tier 1."""
        payload = data.encode() if isinstance(data, str) else data
        digest = hashlib.sha256(payload).hexdigest()[:16]
        path = self._blobs / f"{prefix}-{digest}{suffix}"
        if not path.exists():
            path.write_bytes(payload)
        return str(path)

    def read_blob(self, path: str | Path) -> bytes | None:
        p = Path(path)
        return p.read_bytes() if p.exists() else None

    # --- campaign lifecycle -------------------------------------------
    def start_campaign(self, campaign_id: str, goal: str) -> None:
        self.graph.upsert_campaign(campaign_id, goal, _now())

    def record_reference(self, campaign_id: str, reference: dict) -> str:
        ref_id = (
            reference.get("pdb_id")
            or reference.get("uniprot_id")
            or f"ref:{campaign_id}"
        )
        ref_id = f"ref:{ref_id}"
        self.graph.upsert_reference(ref_id, reference, campaign_id)
        return ref_id

    def record_task_submitted(
        self, campaign_id: str, task_id: str, name: str, interface: str, params: dict
    ) -> None:
        self.graph.upsert_task(
            task_id,
            name=name,
            interface=interface,
            state="RUNNING",
            params=params,
            submitted_at=_now(),
            campaign_id=campaign_id,
        )

    def record_task_result(
        self,
        campaign_id: str,
        task_id: str,
        *,
        name: str,
        interface: str,
        state: str,
        result: Any = None,
        error: str = "",
        designs: Iterable[dict] | None = None,
        round_no: int = 0,
        reference_id: str | None = None,
        blob: tuple[str | bytes, str] | None = None,
    ) -> dict:
        """Write one task's outcome across tiers 1 and 2.

        `blob` is an optional (data, suffix) pair for bulky raw output.
        Returns {"output_id", "blob_path"} for the caller to reference.
        """
        ts = _now()
        self.graph.upsert_task(
            task_id,
            name=name,
            interface=interface,
            state=state,
            params={},
            finished_at=ts,
            error=error,
            campaign_id=campaign_id,
        )

        blob_path = ""
        if blob is not None:
            data, suffix = blob
            blob_path = self.write_blob(data, suffix=suffix, prefix=name)

        output_id = f"{task_id}:out"
        summary = _summarize(result)
        self.graph.add_output(
            output_id,
            task_id=task_id,
            kind=name,
            blob_path=blob_path,
            summary=summary,
            properties=jsonable(result if isinstance(result, dict) else {"value": result}),
            created_at=ts,
        )

        for design in designs or []:
            self.record_design(
                campaign_id,
                design,
                round_no=round_no,
                reference_id=reference_id,
                task_id=task_id,
            )

        return {"output_id": output_id, "blob_path": blob_path}

    def record_design(
        self,
        campaign_id: str,
        design: dict,
        *,
        round_no: int = 0,
        reference_id: str | None = None,
        task_id: str = "",
    ) -> None:
        """Tier 1 node + edges, and tier 2 scores, for a single design."""
        self.graph.upsert_design(
            design,
            campaign_id=campaign_id,
            reference_id=reference_id,
            round_no=round_no,
            created_at=_now(),
        )
        metrics = design.get("metrics") or {}
        if metrics:
            self.scores.record_scores(
                campaign_id,
                design["design_id"],
                metrics,
                round_no=round_no,
                task_id=task_id,
                created_at=_now(),
            )

    def rank_round(
        self, campaign_id: str, round_no: int, metric: str, direction: str, designs: list[dict]
    ) -> list[dict]:
        """Order designs by the key metric and persist the ranking (tier 2)."""
        scored = [d for d in designs if isinstance(d.get("metrics", {}).get(metric), (int, float))]
        scored.sort(key=lambda d: d["metrics"][metric], reverse=(direction == "max"))
        if scored:
            self.scores.record_ranking(
                campaign_id,
                round_no,
                metric,
                [d["design_id"] for d in scored],
                {d["design_id"]: d["metrics"][metric] for d in scored},
            )
        # Designs without the metric keep their relative order at the back.
        unscored = [d for d in designs if d not in scored]
        return scored + unscored

    def record_analysis(
        self, campaign_id: str, kind: str, payload: dict, *, round_no: int = 0
    ) -> None:
        self.scores.record_analysis(
            f"{campaign_id}:{kind}:{round_no}",
            campaign_id,
            kind,
            payload,
            round_no=round_no,
            created_at=_now(),
        )

    # --- reads used by the interpreter ---------------------------------
    def related_past_designs(
        self, campaign_id: str, metric: str, direction: str = "max", limit: int = 5
    ) -> list[dict]:
        """Best designs on this metric from *other* campaigns, with context."""
        rows = self.scores.best_designs(
            metric, direction=direction, limit=limit, exclude_campaign=campaign_id
        )
        out = []
        for row in rows:
            lineage = self.graph.lineage(row["design_id"], depth=1)
            out.append(
                {
                    "design_id": row["design_id"],
                    "campaign_id": row["campaign_id"],
                    "metric": metric,
                    "value": row["value"],
                    "parent": lineage[0]["design_id"] if lineage else None,
                }
            )
        return out

    def campaign_report(self, campaign_id: str) -> dict:
        """Everything the interpreter needs in one call."""
        return {
            "designs": self.graph.designs_in_campaign(campaign_id),
            "tasks": self.graph.tasks_for_campaign(campaign_id),
            "scores": self.scores.campaign_scores(campaign_id),
            "analyses": self.scores.analyses_for_campaign(campaign_id),
        }

    def stage_golden_set(
        self,
        campaign_id: str,
        name: str,
        rules: CurationRules,
        *,
        notes: str = "",
        designs: Iterable[dict] | None = None,
    ) -> dict:
        """Promote campaign designs into a tier 3 golden set."""
        if designs is None:
            designs = self.graph.designs_in_campaign(campaign_id)
            # graph properties carry metrics; make sure tier 2 wins where present
            designs = [
                {
                    **d,
                    "metrics": {
                        **(d.get("metrics") or {}),
                        **self.scores.scores_for_design(d["design_id"]),
                    },
                    "campaign_id": campaign_id,
                }
                for d in designs
            ]
        return self.golden.stage(
            name, designs, rules, campaign_id=campaign_id, notes=notes
        )


def _summarize(result: Any, limit: int = 2000) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:limit]
    if isinstance(result, dict):
        keys = ", ".join(sorted(result)[:12])
        return f"dict({keys})"[:limit]
    if isinstance(result, (list, tuple)):
        return f"{type(result).__name__}[{len(result)}]"
    return str(result)[:limit]
