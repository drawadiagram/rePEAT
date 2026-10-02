"""Redesign Orchestrator: turns the goal into a key metric and a worklist.

It chooses the metric that defines success, then picks tasks from the available
Task Interfaces and dispatches them. Routing to HPC happens here, because only
this node knows both the task and whether an endpoint is connected.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from langgraph.types import Command

from ...llm import complete_json
from ...tasks.base import TaskSpec
from ...tasks.registry import describe
from ...tools.esmfold import ESMATLAS_MAX_LEN, fold_job_spec
from ...tools.proteinmpnn import mpnn_job_spec
from ..deps import Deps, llm_caveat, status
from ..state import DesignState

log = logging.getLogger(__name__)

# Metrics the loop knows how to compute, with the direction that means "better".
METRICS = {
    "plddt": ("max", "Predicted structure confidence (ESMFold pLDDT, 0-100)."),
    "rmsd_to_reference": ("min", "Backbone deviation from the reference structure."),
    "mpnn_score": ("min", "ProteinMPNN negative log-likelihood of the sequence."),
    "sequence_identity": ("max", "Fraction of positions identical to the reference."),
    "mean_hydropathy": ("min", "Mean Kyte-Doolittle hydropathy (lower is more soluble)."),
    "net_charge": ("max", "Net charge at neutral pH."),
}

GOAL_KEYWORDS = {
    "plddt": ("stable", "stability", "thermostab", "fold", "confidence", "structure", "rigid"),
    "mean_hydropathy": ("soluble", "solubility", "aggregat", "express"),
    "rmsd_to_reference": ("conserve", "preserve", "keep the fold", "same shape", "scaffold"),
    "sequence_identity": ("conservative", "minimal change", "few mutations"),
    "net_charge": ("charge", "electrostatic", "ph "),
}

PLAN_SYSTEM = """You plan one round of protein redesign.

Pick the single metric that best measures the user's goal, from this list:
""" + "\n".join(f"  {name} ({d}): {desc}" for name, (d, desc) in METRICS.items()) + """

Then choose which tasks to run this round from the catalog given below. Prefer:
  - propose_variants when new candidate sequences are needed
  - apply_mutations when the user named specific mutations
  - fold_sequence to get a structure and pLDDT for each candidate
  - run_chemgraph only for small-molecule/ligand chemistry questions

Return JSON:
{"key_metric": {"name": ..., "direction": "max"|"min", "target": number or null,
                "description": "..."},
 "tasks": [{"task": "<catalog name>", "why": "..."}],
 "n_variants": integer 2-12,
 "notes": "one sentence on the plan"}"""


def choose_metric_rules(goal: str, state: DesignState) -> dict[str, Any]:
    """Keyword-based metric choice, used when no LLM is configured."""
    low = (goal or "").lower()
    for metric, words in GOAL_KEYWORDS.items():
        if any(word in low for word in words):
            direction, description = METRICS[metric]
            return {
                "name": metric,
                "direction": direction,
                "target": None,
                "description": description,
            }
    direction, description = METRICS["plddt"]
    return {
        "name": "plddt",
        "direction": direction,
        "target": None,
        "description": description,
    }


def make_orchestrator(deps: Deps):
    async def orchestrator(state: DesignState) -> Command:
        session_id = state.get("session_id", "")
        campaign_id = deps.campaign_id(state)
        reference = state.get("reference_design") or {}
        goal = state.get("goal", "")
        round_no = int(state.get("round", 0)) + 1

        parent = state.get("lead_design") or {}
        parent_sequence = parent.get("sequence") or reference.get("sequence") or ""
        if not parent_sequence:
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "I have no sequence to redesign yet.",
                        }
                    ],
                    "reply_source": "orchestrator:no_sequence",
                },
            )

        status(f"Planning round {round_no}…", node="orchestrator")

        # --- decide the metric and the plan ---
        plan: dict[str, Any] | None = None
        caveats: list[str] = []
        if deps.settings.llm_available:
            plan = await complete_json(
                PLAN_SYSTEM + "\n\nCatalog:\n" + describe({"local", "query", "hpc"}),
                _plan_context(state, round_no, parent_sequence),
                settings=deps.settings,
                on_fallback=llm_caveat(deps, caveats),
                max_tokens=900,
            )

        key_metric = state.get("key_metric") or {}
        if plan and isinstance(plan.get("key_metric"), dict):
            proposed = plan["key_metric"]
            name = str(proposed.get("name", "")).strip()
            if name in METRICS:
                key_metric = {
                    "name": name,
                    "direction": proposed.get("direction") or METRICS[name][0],
                    "target": _number(proposed.get("target")),
                    "description": proposed.get("description") or METRICS[name][1],
                }
        if not key_metric.get("name"):
            key_metric = choose_metric_rules(goal, state)

        n_variants = _int(plan.get("n_variants") if plan else None, default=6, lo=2, hi=12)
        requested = state.get("requested_mutations") or []

        # --- build the worklist ---
        work: list[dict[str, Any]] = []
        specs: list[TaskSpec] = []

        if requested:
            work.append(
                _item("apply_mutations", "local", {
                    "sequence": parent_sequence,
                    "mutations": requested,
                })
            )
        else:
            hints = reference.get("literature_mutations") or []
            use_hpc = deps.tasks.hpc_available
            task_name = "proteinmpnn" if use_hpc else "propose_variants"
            params = {
                "sequence": parent_sequence,
                "n": n_variants,
                "suggested_mutations": hints,
                "seed": round_no,
                "structure_path": reference.get("structure_path", ""),
            }
            if use_hpc:
                # Without this the remote interface submits `to_psij_spec({})`,
                # which is `/bin/true` — a job that succeeds and does nothing.
                params.update(
                    _job_params(
                        deps,
                        mpnn_job_spec(
                            reference.get("structure_path", ""),
                            num_sequences=n_variants,
                        ),
                    )
                )
            work.append(_item(task_name, "hpc" if use_hpc else "local", params))

        # ChemGraph only when the goal is actually about chemistry.
        if plan and any(
            t.get("task") == "run_chemgraph" for t in (plan.get("tasks") or [])
        ):
            ligands = [lig.get("comp_id") for lig in (reference.get("ligands") or [])]
            work.append(
                _item("run_chemgraph", "local", {
                    "task": (
                        f"For the protein {reference.get('name', '')} with ligand(s) "
                        f"{', '.join(filter(None, ligands)) or 'none'}: {goal}"
                    ),
                })
            )

        for item in work:
            specs.append(
                TaskSpec(
                    name=item["task"],
                    params={**item["params"], "_interface": item["interface"]},
                    label=item["task"].replace("_", " "),
                    kind="job" if item["interface"] == "hpc" else "function",
                )
            )

        status(f"Generating candidates ({n_variants} requested)…", node="orchestrator")
        records = await deps.tasks.run_many(
            specs,
            session_id=session_id,
            campaign_id=campaign_id,
            timeout=_batch_timeout(deps, specs),
        )

        # --- fold every candidate produced ---
        variants = _collect_variants(records)
        if not variants:
            note = _failure_note(records)
            return Command(
                goto="interpreter",
                update={
                    "key_metric": key_metric,
                    "round": round_no,
                    "worklist": [
                        {**item, "status": "failed", "error": note} for item in work
                    ],
                    "status": note,
                },
            )

        fold_specs: list[TaskSpec] = []
        fold_backend = _fold_backend(deps, variants)
        for i, variant in enumerate(variants):
            design_id = f"{campaign_id}-r{round_no}-{i + 1}"
            variant["design_id"] = design_id
            params = {
                "sequence": variant["sequence"],
                "design_id": design_id,
                "backend": fold_backend,
                "_interface": "hpc" if fold_backend == "hpc" else "local",
            }
            if fold_backend == "hpc":
                params.update(
                    _job_params(deps, fold_job_spec(variant["sequence"], name=design_id))
                )
            fold_specs.append(
                TaskSpec(
                    name="fold_sequence",
                    params=params,
                    label=f"fold {design_id}",
                    kind="job" if fold_backend == "hpc" else "function",
                )
            )

        status(f"Folding {len(fold_specs)} candidate(s)…", node="orchestrator")
        fold_records = await deps.tasks.run_many(
            fold_specs,
            session_id=session_id,
            campaign_id=campaign_id,
            timeout=_batch_timeout(deps, fold_specs),
        )

        # Coordinates go to a blob here, not into state: a checkpoint carrying
        # six PDB files is hundreds of KB per turn and grows with the ensemble.
        fold_summaries: list[dict[str, Any]] = []
        for record in fold_records:
            result = record.get("result") if isinstance(record.get("result"), dict) else {}
            summary: dict[str, Any] = {
                "design_id": (
                    result.get("design_id")
                    or (record.get("params") or {}).get("design_id")
                ),
                "ok": bool(record.get("ok")) and not result.get("error"),
                "error": record.get("error") or result.get("error", ""),
                "metrics": result.get("metrics") or {},
                "backend": result.get("backend", ""),
            }
            structure = result.get("structure")
            if summary["ok"] and isinstance(structure, str) and structure:
                summary["structure_path"] = deps.history.write_blob(
                    structure,
                    suffix=".pdb" if result.get("format", "pdb") == "pdb" else ".cif",
                    prefix=summary["design_id"] or "fold",
                )
                summary["structure_format"] = result.get("format", "pdb")
            fold_summaries.append(summary)

        worklist = [{**item, "status": "done"} for item in work]
        worklist += [
            {
                "id": f"fold-{r['id']}",
                "interface": r["interface"],
                "task": r["task"],
                "params": {"design_id": r["params"].get("design_id")},
                "status": "done" if r["ok"] else "failed",
                "handle_id": r["id"],
                "error": r.get("error") or "",
            }
            for r in fold_records
        ]

        return Command(
            goto="analyst",
            update={
                "key_metric": key_metric,
                "round": round_no,
                "worklist": worklist,
                # Handed to the analyst as the round's raw material. Slim by
                # construction: structures are referenced by blob path.
                "pending_results": {
                    "variants": variants,
                    "folds": fold_summaries,
                    "parent_sequence": parent_sequence,
                    "parent_id": parent.get("design_id"),
                },
                "status": f"Round {round_no}: scoring {len(variants)} candidate(s)",
                **({"warnings": caveats} if caveats else {}),
            },
        )

    return orchestrator


# --- helpers ---------------------------------------------------------------


def _job_params(deps: Deps, job_spec: dict[str, Any]) -> dict[str, Any]:
    """Carry a job spec and the site's scheduler details to a remote interface.

    `OrbitInterface._submit_job` reads `job_spec` and `executor` out of the spec's
    params; everything in `job_spec` that the site decides — the allocation, the
    queue, the walltime — comes from settings rather than from the tool module,
    which cannot know them.
    """
    settings = deps.settings
    spec = {**job_spec, "duration_sec": settings.orbit_job_duration_sec}
    if settings.orbit_account:
        spec["account"] = settings.orbit_account
    if settings.orbit_queue:
        spec["queue"] = settings.orbit_queue
    return {"job_spec": spec, "executor": settings.orbit_psij_executor}


def _batch_timeout(deps: Deps, specs: list[TaskSpec]) -> float | None:
    """The manager's ceiling for a batch, widened when it contains a real job.

    `task_timeout_sec` defaults to 900 s, which is shorter than a job's own
    walltime: a queued job would be failed by us before the scheduler had started
    it. A queued job is not a late job.
    """
    base = deps.settings.task_timeout_sec
    if base is None or not any(spec.kind == "job" for spec in specs):
        return base
    return max(base, deps.settings.orbit_job_duration_sec + 300)


def _fold_backend(deps: Deps, variants: list[dict]) -> str:
    """Pick a folding backend that can actually handle these sequences."""
    configured = deps.settings.fold_backend
    longest = max((len(v.get("sequence", "")) for v in variants), default=0)
    if configured == "esmatlas" and longest > ESMATLAS_MAX_LEN:
        if deps.tasks.hpc_available:
            log.info("sequences are %d aa; routing folding to HPC", longest)
            return "hpc"
        log.info("sequences are %d aa; the public fold API will reject them", longest)
    return configured


def _item(task: str, interface: str, params: dict) -> dict[str, Any]:
    return {
        "id": f"{task}-{uuid.uuid4().hex[:8]}",
        "task": task,
        "interface": interface,
        "params": params,
        "status": "pending",
        "handle_id": None,
        "depends_on": [],
    }


def _collect_variants(records: list[dict]) -> list[dict]:
    """Pull candidate sequences out of whatever the generators returned."""
    out: list[dict] = []
    seen: set[str] = set()
    for record in records:
        if not record.get("ok"):
            continue
        result = record.get("result")
        if not isinstance(result, dict):
            continue
        for variant in result.get("variants") or []:
            sequence = (variant or {}).get("sequence")
            if not sequence or sequence in seen:
                continue
            seen.add(sequence)
            out.append(dict(variant))
    return out


def _failure_note(records: list[dict]) -> str:
    errors = []
    for record in records:
        if not record.get("ok"):
            errors.append(f"{record['task']}: {record.get('error', 'failed')}")
        else:
            result = record.get("result")
            if isinstance(result, dict) and result.get("error"):
                errors.append(f"{record['task']}: {result['error']}")
    return (
        "No candidate designs were produced. " + "; ".join(errors[:3])
        if errors
        else "No candidate designs were produced."
    )


def _plan_context(state: DesignState, round_no: int, sequence: str) -> str:
    import json

    reference = state.get("reference_design") or {}
    return json.dumps(
        {
            "goal": state.get("goal", ""),
            "round": round_no,
            "reference": {
                "pdb_id": reference.get("pdb_id"),
                "name": reference.get("name"),
                "length": reference.get("length"),
                "organism": reference.get("organism"),
                "ligands": [lig.get("comp_id") for lig in (reference.get("ligands") or [])],
                "functional_features": [
                    {"type": f.get("type"), "start": f.get("start")}
                    for f in (reference.get("features") or [])[:15]
                ],
                "literature_mutations": (reference.get("literature_mutations") or [])[:15],
            },
            "parent_sequence_length": len(sequence),
            "current_key_metric": state.get("key_metric") or {},
            "best_so_far": [
                {"design_id": d.get("design_id"), "metrics": d.get("metrics")}
                for d in (state.get("ensemble") or [])[:5]
            ],
            "requested_mutations": state.get("requested_mutations") or [],
        },
        default=str,
    )


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, *, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default
