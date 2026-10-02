"""Tier 3 of the Design History: golden sets staged for ML training.

A golden set is an immutable Parquet snapshot of (sequence, metrics) rows that
passed curation, plus a JSON manifest recording how it was selected. Parquet so
a training job can read it with any dataframe library and nothing needs this app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


@dataclass
class CurationRules:
    """Which designs are worth training on."""

    metric: str
    direction: str = "max"
    min_value: float | None = None
    max_value: float | None = None
    top_k: int | None = None
    require_structure: bool = False
    min_sequence_length: int = 1
    # Drop exact duplicate sequences, keeping the best-scoring one.
    dedupe_sequences: bool = True

    def passes(self, row: dict) -> bool:
        value = row.get("metrics", {}).get(self.metric)
        if value is None:
            return False
        if self.min_value is not None and value < self.min_value:
            return False
        if self.max_value is not None and value > self.max_value:
            return False
        if self.require_structure and not row.get("structure_path"):
            return False
        if len(row.get("sequence", "")) < self.min_sequence_length:
            return False
        return True

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "direction": self.direction,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "top_k": self.top_k,
            "require_structure": self.require_structure,
            "min_sequence_length": self.min_sequence_length,
            "dedupe_sequences": self.dedupe_sequences,
        }


class GoldenStore:
    """Tier 3 golden-set staging area."""

    def __init__(self, root: Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def curate(self, designs: Iterable[dict], rules: CurationRules) -> list[dict]:
        """Apply the rules, returning the rows that would be staged."""
        kept = [dict(d) for d in designs if rules.passes(d)]
        reverse = rules.direction == "max"
        kept.sort(key=lambda d: d["metrics"][rules.metric], reverse=reverse)

        if rules.dedupe_sequences:
            seen: set[str] = set()
            deduped = []
            for row in kept:  # already best-first, so the first wins
                seq = row.get("sequence", "")
                if seq and seq in seen:
                    continue
                seen.add(seq)
                deduped.append(row)
            kept = deduped

        if rules.top_k is not None:
            kept = kept[: rules.top_k]
        return kept

    def stage(
        self,
        name: str,
        designs: Iterable[dict],
        rules: CurationRules,
        *,
        campaign_id: str = "",
        notes: str = "",
    ) -> dict:
        """Write a golden set to Parquet and return its manifest."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = self.curate(designs, rules)
        created = datetime.now(timezone.utc).isoformat()
        stamp = created.replace(":", "").replace("-", "")[:15]
        set_id = f"{name}-{stamp}"
        out_dir = self._root / set_id
        out_dir.mkdir(parents=True, exist_ok=True)
        parquet_path = out_dir / "designs.parquet"

        # One column per metric that appears, so trainers can select directly.
        metric_names = sorted({m for r in rows for m in r.get("metrics", {})})
        table_dict: dict[str, list[Any]] = {
            "design_id": [r.get("design_id", "") for r in rows],
            "sequence": [r.get("sequence", "") for r in rows],
            "campaign_id": [r.get("campaign_id", campaign_id) for r in rows],
            "round": [int(r.get("round", 0) or 0) for r in rows],
            "mutations": [json.dumps(r.get("mutations", [])) for r in rows],
            "structure_path": [r.get("structure_path", "") or "" for r in rows],
        }
        for metric in metric_names:
            table_dict[f"metric_{metric}"] = [
                _as_float(r.get("metrics", {}).get(metric)) for r in rows
            ]

        pq.write_table(pa.table(table_dict), parquet_path)

        manifest = {
            "id": set_id,
            "name": name,
            "created_at": created,
            "campaign_id": campaign_id,
            "n_rows": len(rows),
            "metrics": metric_names,
            "rules": rules.as_dict(),
            "notes": notes,
            "parquet": str(parquet_path),
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        return manifest

    def list_sets(self) -> list[dict]:
        out = []
        for manifest_path in sorted(self._root.glob("*/manifest.json")):
            try:
                out.append(json.loads(manifest_path.read_text()))
            except json.JSONDecodeError:
                continue
        return sorted(out, key=lambda m: m.get("created_at", ""), reverse=True)

    def read_set(self, set_id: str) -> list[dict]:
        import pyarrow.parquet as pq

        path = self._root / set_id / "designs.parquet"
        if not path.exists():
            return []
        return pq.read_table(path).to_pylist()


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
