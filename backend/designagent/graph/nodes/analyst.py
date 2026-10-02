"""Analyst: evaluates returned task data and stores it in the Design History.

Takes the round's raw task records, scores each candidate, writes all three lake
tiers, re-ranks the ensemble, and refreshes the molecular visualization. Decides
whether another round is worth running or the campaign should be summarized.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from ...tasks.base import TaskSpec
from ...tools.scoring import mutations_between
from ..deps import Deps, status
from ..state import DesignState, last_user_text

log = logging.getLogger(__name__)


def make_analyst(deps: Deps):
    async def analyst(state: DesignState) -> Command:
        session_id = state.get("session_id", "")
        campaign_id = deps.campaign_id(state)
        reference = state.get("reference_design") or {}
        key_metric = state.get("key_metric") or {}
        metric_name = key_metric.get("name", "plddt")
        direction = key_metric.get("direction", "max")
        round_no = int(state.get("round", 0))
        pending = state.get("pending_results") or {}

        # A bare "show me X" reaches the analyst with nothing new to analyze.
        if not pending:
            return await _visualize_only(deps, state, session_id, campaign_id)

        status("Analyzing results…", node="analyst")

        reference_sequence = reference.get("sequence", "")
        parent_sequence = pending.get("parent_sequence") or reference_sequence
        parent_id = pending.get("parent_id")

        # --- assemble designs from the generators + folds ---
        by_design: dict[str, dict[str, Any]] = {}
        for variant in pending.get("variants") or []:
            design_id = variant.get("design_id")
            if not design_id:
                continue
            by_design[design_id] = {
                "design_id": design_id,
                "sequence": variant.get("sequence", ""),
                "parent_id": parent_id,
                "mutations": variant.get("mutations")
                or mutations_between(parent_sequence, variant.get("sequence", "")),
                "metrics": {},
                "provenance": {
                    "round": round_no,
                    "rationale": variant.get("rationale", ""),
                    "source": variant.get("source", ""),
                },
            }

        # Fold summaries arrive slim: metrics inline, coordinates by blob path.
        failures: list[str] = []
        for summary in pending.get("folds") or []:
            design_id = summary.get("design_id")
            if not summary.get("ok"):
                failures.append(f"{design_id or 'fold'}: {summary.get('error') or 'failed'}")
                continue
            design = by_design.get(design_id)
            if design is None:
                continue
            design["metrics"].update(summary.get("metrics") or {})
            if summary.get("structure_path"):
                design["structure_path"] = summary["structure_path"]
                design["structure_format"] = summary.get("structure_format", "pdb")
                design["structure_source"] = "predicted"

        designs = [d for d in by_design.values() if d.get("sequence")]
        if not designs:
            note = "No designs survived this round. " + "; ".join(failures[:3])
            return Command(
                goto="interpreter",
                update={"status": note, "pending_results": {}},
            )

        # --- score structures against the reference ---
        score_specs = [
            TaskSpec(
                name="score_structure",
                params={
                    "structure_path": design["structure_path"],
                    "reference_path": reference.get("structure_path", ""),
                    "reference_fmt": reference.get("structure_format", "pdb"),
                    "reference_sequence": reference_sequence,
                    "predicted": True,
                    "_interface": "local",
                },
                label=f"score {design['design_id']}",
            )
            for design in designs
            if design.get("structure_path")
        ]
        if score_specs:
            status(f"Scoring {len(score_specs)} structure(s)…", node="analyst")
            score_records = await deps.tasks.run_many(
                score_specs,
                session_id=session_id,
                campaign_id=campaign_id,
                timeout=deps.settings.task_timeout_sec,
            )
            # Specs were built in `designs` order, so results line up with it.
            scored = [d for d in designs if d.get("structure_path")]
            for design, record in zip(scored, score_records):
                if not record.get("ok"):
                    continue
                result = record.get("result") or {}
                design["metrics"].update(result.get("metrics") or {})

        # Sequence-only metrics for anything that never got a structure.
        unfolded = [d for d in designs if not d.get("structure_path")]
        if unfolded:
            record = (
                await deps.tasks.run_many(
                    [
                        TaskSpec(
                            name="score_sequences",
                            params={
                                "sequences": [
                                    {"sequence": d["sequence"], "design_id": d["design_id"]}
                                    for d in unfolded
                                ],
                                "reference_sequence": reference_sequence,
                                "_interface": "local",
                            },
                            label="score sequences",
                        )
                    ],
                    session_id=session_id,
                    campaign_id=campaign_id,
                )
            )[0]
            if record.get("ok"):
                by_id = {
                    entry.get("design_id"): entry
                    for entry in (record["result"].get("scored") or [])
                }
                for design in unfolded:
                    entry = by_id.get(design["design_id"])
                    if entry:
                        design["metrics"].update(entry.get("metrics") or {})

        # --- persist to the lake (tiers 1 and 2) ---
        # A storage failure must not lose the user's round: the designs are
        # already in state, so we log, warn, and carry on.
        reference_key = (
            reference.get("pdb_id") or reference.get("uniprot_id") or campaign_id
        )
        reference_id = f"ref:{reference_key}"
        storage_error = ""
        for design in designs:
            try:
                deps.history.record_design(
                    campaign_id,
                    design,
                    round_no=round_no,
                    reference_id=reference_id,
                )
            except Exception as exc:
                storage_error = str(exc)
                log.warning("recording design %s failed: %s", design["design_id"], exc)

        # --- rank this round together with everything before it ---
        previous = list(state.get("ensemble") or [])
        combined = _merge_by_id(previous, designs)
        try:
            ranked = deps.history.rank_round(
                campaign_id, round_no, metric_name, direction, combined
            )
        except Exception as exc:
            storage_error = storage_error or str(exc)
            log.warning("persisting the ranking failed: %s", exc)
            ranked = _rank_in_memory(combined, metric_name, direction)

        best = _best(ranked, metric_name)
        lead = best or (state.get("lead_design") or {})

        improved = _improved(state.get("lead_design") or {}, lead, metric_name, direction)
        try:
            deps.history.record_analysis(
                campaign_id,
                "round_summary",
                {
                    "round": round_no,
                    "metric": metric_name,
                    "direction": direction,
                    "n_designs": len(designs),
                    "best": {"design_id": lead.get("design_id"), "metrics": lead.get("metrics")},
                    "improved": improved,
                    "failures": failures,
                },
                round_no=round_no,
            )
        except Exception as exc:
            storage_error = storage_error or str(exc)
            log.warning("recording the round analysis failed: %s", exc)

        # --- refresh the visualization for the new lead ---
        visualization = await _make_visualization(
            deps, state, lead, session_id, campaign_id
        )

        update: dict[str, Any] = {
            "lead_design": lead,
            "ensemble": ranked,
            "worklist": [],
            "pending_results": {},
            "status": _round_line(round_no, len(designs), metric_name, lead, improved),
        }
        if storage_error:
            update["warnings"] = [f"Design History write failed: {storage_error}"]
        if failures:
            update["warnings"] = [
                *update.get("warnings", []),
                *(f"Task failed: {f}" for f in failures[:3]),
            ]
        if visualization:
            update["molecular_visualization"] = visualization["spec"]
            update["artifacts"] = [visualization["artifact"]]

        status(update["status"], node="analyst")

        # Another round only if it is still improving and the budget allows.
        if improved and round_no < deps.settings.max_rounds:
            return Command(goto="orchestrator", update=update)
        return Command(goto="interpreter", update=update)

    return analyst


# --- visualization ---------------------------------------------------------


async def _make_visualization(
    deps: Deps,
    state: DesignState,
    lead: dict,
    session_id: str,
    campaign_id: str,
    prompt: str = "",
) -> dict[str, Any] | None:
    """Run the visualization generator and register the spec as an artifact."""
    reference = state.get("reference_design") or {}
    record = (
        await deps.tasks.run_many(
            [
                TaskSpec(
                    name="generate_visualization",
                    params={
                        # The turn's own words before the campaign goal: on a
                        # "label the active site" turn the goal is still the
                        # design objective, which says nothing about the view.
                        "prompt": prompt or last_user_text(state) or state.get("goal", ""),
                        "reference": reference,
                        "lead": lead,
                        "key_metric": state.get("key_metric") or {},
                        "_interface": "local",
                    },
                    label="build visualization",
                )
            ],
            session_id=session_id,
            campaign_id=campaign_id,
        )
    )[0]
    if not record.get("ok"):
        return None
    result = record.get("result") or {}
    spec = result.get("spec")
    if not spec:
        return None

    # Inline structure data so the viewer needs no second authenticated fetch.
    for structure in spec.get("structures") or []:
        if structure.get("source") == "artifact" and structure.get("path"):
            data = deps.history.read_blob(structure["path"])
            if data:
                artifact = deps.artifacts.add(
                    "json",
                    f"{spec.get('title', 'structure')} coordinates",
                    content={
                        "format": structure.get("format", "pdb"),
                        "data": data.decode(errors="replace"),
                    },
                    session_id=session_id,
                )
                structure["value"] = artifact["id"]

    artifact = deps.artifacts.add(
        "molstar",
        spec.get("title") or "Structure view",
        content=spec,
        session_id=session_id,
        meta={"caption": spec.get("caption", "")},
    )
    spec = {**spec, "artifact_id": artifact["id"]}
    return {"spec": spec, "artifact": artifact}


async def _visualize_only(
    deps: Deps, state: DesignState, session_id: str, campaign_id: str
) -> Command:
    """Handle a pure "show me this" request with no new computation."""
    reference = state.get("reference_design") or {}
    lead = state.get("lead_design") or {}
    if not (reference or lead):
        return Command(
            goto="__end__",
            update={
                "messages": [
                    {"role": "assistant", "content": "There is nothing loaded to display yet."}
                ]
            },
        )

    status("Building the visualization…", node="analyst")
    # No explicit prompt: _make_visualization reads the turn's own words.
    visualization = await _make_visualization(
        deps, state, lead, session_id, campaign_id
    )
    if not visualization:
        return Command(
            goto="__end__",
            update={
                "messages": [
                    {"role": "assistant", "content": "I could not build that view."}
                ]
            },
        )

    caption = visualization["spec"].get("caption") or "Structure view ready."
    return Command(
        goto="__end__",
        update={
            "molecular_visualization": visualization["spec"],
            "artifacts": [visualization["artifact"]],
            "messages": [{"role": "assistant", "content": caption}],
            "status": "",
        },
    )


# --- helpers ---------------------------------------------------------------


def _merge_by_id(previous: list[dict], new: list[dict]) -> list[dict]:
    merged = {d.get("design_id"): d for d in previous if d.get("design_id")}
    for design in new:
        did = design.get("design_id")
        if not did:
            continue
        if did in merged:
            merged[did] = {
                **merged[did],
                **design,
                "metrics": {**(merged[did].get("metrics") or {}), **(design.get("metrics") or {})},
            }
        else:
            merged[did] = design
    return list(merged.values())


def _rank_in_memory(designs: list[dict], metric: str, direction: str) -> list[dict]:
    """Rank without touching the lake, for when tier 2 is unavailable."""
    scored = [
        d for d in designs if isinstance((d.get("metrics") or {}).get(metric), (int, float))
    ]
    scored.sort(key=lambda d: d["metrics"][metric], reverse=(direction == "max"))
    return scored + [d for d in designs if d not in scored]


def _best(designs: list[dict], metric: str) -> dict | None:
    """The first design that actually has the key metric (list is pre-ranked)."""
    for design in designs:
        if isinstance((design.get("metrics") or {}).get(metric), (int, float)):
            return design
    return designs[0] if designs else None


def _improved(old: dict, new: dict, metric: str, direction: str) -> bool:
    old_value = (old.get("metrics") or {}).get(metric)
    new_value = (new.get("metrics") or {}).get(metric)
    if not isinstance(new_value, (int, float)):
        return False
    if not isinstance(old_value, (int, float)):
        return True  # first measurement counts as progress
    return new_value > old_value if direction == "max" else new_value < old_value


def _round_line(
    round_no: int, n: int, metric: str, lead: dict, improved: bool
) -> str:
    value = (lead.get("metrics") or {}).get(metric)
    shown = f"{value:.3g}" if isinstance(value, (int, float)) else "n/a"
    verb = "improved to" if improved else "did not improve past"
    return (
        f"Round {round_no}: scored {n} design(s). Best {metric} {verb} {shown} "
        f"({lead.get('design_id', 'n/a')})."
    )
