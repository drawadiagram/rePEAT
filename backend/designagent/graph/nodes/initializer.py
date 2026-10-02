"""Design Initializer: bootstraps the reference design.

Runs PDB, UniProt and literature retrieval concurrently through the query task
interface, then reconciles them into one `reference_design`. Reconciliation
matters: PDB gives the crystallized construct, UniProt the canonical full-length
sequence, and they frequently disagree. We keep the PDB sequence as the design
substrate (it is what a structure-based tool will see) and record the UniProt
sequence alongside rather than silently preferring one.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Command

from ...tasks.base import TaskSpec
from ..deps import Deps, status
from ..state import DesignState

log = logging.getLogger(__name__)


def _goal_terms(goal: str) -> str:
    """Reduce a goal sentence to literature search terms."""
    stop = {
        "the", "a", "an", "of", "for", "to", "and", "with", "make", "more",
        "improve", "increase", "decrease", "design", "redesign", "please",
        "this", "that", "its", "it", "protein", "me", "my", "can", "you",
    }
    words = [w.strip(".,;:!?()").lower() for w in goal.split()]
    kept = [w for w in words if w and w not in stop and len(w) > 2]
    return " ".join(kept[:6])


def make_initializer(deps: Deps):
    async def initializer(state: DesignState) -> Command:
        session_id = state.get("session_id", "")
        campaign_id = deps.campaign_id(state)
        goal = state.get("goal", "")
        hints = state.get("target_hints") or {}
        prompt = goal or ""

        deps.history.start_campaign(campaign_id, goal)
        status("Looking up the reference design…", node="initializer")

        # Round 1: identify the target. PDB and UniProt are asked in parallel;
        # either may be the one the user actually named.
        lookups = [
            TaskSpec(
                name="pdb_lookup",
                params={"query": prompt, "pdb_id": hints.get("pdb_id", "")},
                label="PDB lookup",
            ),
            TaskSpec(
                name="uniprot_lookup",
                params={"query": prompt, "uniprot_id": hints.get("uniprot_id", "")},
                label="UniProt lookup",
            ),
        ]
        records = await deps.tasks.run_many(
            lookups, session_id=session_id, campaign_id=campaign_id
        )
        by_task = {r["task"]: r for r in records}
        pdb_data = _ok(by_task.get("pdb_lookup"))
        uniprot_data = _ok(by_task.get("uniprot_lookup"))

        # If PDB found nothing but UniProt did, follow its PDB cross-references.
        if not pdb_data and uniprot_data.get("pdb_ids"):
            follow = await deps.tasks.run_many(
                [
                    TaskSpec(
                        name="pdb_lookup",
                        params={"pdb_id": uniprot_data["pdb_ids"][0]},
                        label="PDB lookup (via UniProt)",
                    )
                ],
                session_id=session_id,
                campaign_id=campaign_id,
            )
            pdb_data = _ok(follow[0])

        # Likewise, resolve UniProt from the PDB entry's cross-reference.
        if not uniprot_data and pdb_data.get("uniprot_id"):
            follow = await deps.tasks.run_many(
                [
                    TaskSpec(
                        name="uniprot_lookup",
                        params={"uniprot_id": pdb_data["uniprot_id"]},
                        label="UniProt lookup (via PDB)",
                    )
                ],
                session_id=session_id,
                campaign_id=campaign_id,
            )
            uniprot_data = _ok(follow[0])

        if not pdb_data and not uniprot_data:
            message = (
                "I could not identify a protein target from that. Name a PDB "
                "entry (e.g. 1UBQ), a UniProt accession (e.g. P0CG48), or a "
                "protein and organism."
            )
            return Command(
                goto="__end__",
                update={
                    "messages": [{"role": "assistant", "content": message}],
                    "reply_source": "initializer:no_target",
                    "status": "",
                },
            )

        reference: dict[str, Any] = {
            "name": pdb_data.get("name") or uniprot_data.get("name", ""),
            "pdb_id": pdb_data.get("pdb_id", ""),
            "uniprot_id": uniprot_data.get("uniprot_id") or pdb_data.get("uniprot_id", ""),
            "organism": pdb_data.get("organism") or uniprot_data.get("organism", ""),
            "function": uniprot_data.get("function") or pdb_data.get("function", ""),
            "chains": pdb_data.get("chains", []),
            "ligands": pdb_data.get("ligands", []),
            "features": uniprot_data.get("features", []),
            "method": pdb_data.get("method", ""),
            "resolution": pdb_data.get("resolution"),
        }

        # The design substrate: prefer the crystallized sequence.
        pdb_seq = pdb_data.get("sequence") or ""
        uniprot_seq = uniprot_data.get("sequence") or ""
        reference["sequence"] = pdb_seq or uniprot_seq
        reference["length"] = len(reference["sequence"])
        if uniprot_seq and pdb_seq and uniprot_seq != pdb_seq:
            reference["canonical_sequence"] = uniprot_seq
            reference["notes"] = (
                f"PDB construct is {len(pdb_seq)} aa; UniProt canonical is "
                f"{len(uniprot_seq)} aa. Residue numbering may differ."
            )

        # Round 2: coordinates and literature, in parallel.
        followups: list[TaskSpec] = []
        if reference["pdb_id"]:
            followups.append(
                TaskSpec(
                    name="pdb_structure",
                    params={"pdb_id": reference["pdb_id"], "fmt": "pdb"},
                    label="Download structure",
                )
            )
        lit_protein = reference.get("name") or reference.get("uniprot_id") or ""
        followups.append(
            TaskSpec(
                name="literature_lookup",
                params={
                    "protein": lit_protein,
                    "goal": _goal_terms(goal),
                    "limit": 5,
                },
                label="Literature search",
            )
        )
        status("Fetching coordinates and literature…", node="initializer")
        records = await deps.tasks.run_many(
            followups, session_id=session_id, campaign_id=campaign_id
        )
        by_task = {r["task"]: r for r in records}

        structure = _ok(by_task.get("pdb_structure"))
        if structure.get("text"):
            path = deps.history.write_blob(
                structure["text"],
                suffix=".pdb" if structure.get("format") == "pdb" else ".cif",
                prefix=f"ref-{reference['pdb_id']}",
            )
            reference["structure_path"] = path
            reference["structure_format"] = structure.get("format", "pdb")

        literature = _ok(by_task.get("literature_lookup"))
        reference["literature"] = literature.get("refs", [])
        if literature.get("mutations_mentioned"):
            reference["literature_mutations"] = literature["mutations_mentioned"]

        deps.history.record_reference(campaign_id, reference)

        ident = reference["pdb_id"] or reference["uniprot_id"]
        summary_line = (
            f"Loaded {ident}"
            + (f" — {reference['name']}" if reference.get("name") else "")
            + f" ({reference['length']} aa"
            + (f", {reference['organism']}" if reference.get("organism") else "")
            + ")."
        )
        if reference.get("literature"):
            summary_line += f" Found {len(reference['literature'])} relevant paper(s)."
        status(summary_line, node="initializer")

        update = {
            "reference_design": reference,
            "status": summary_line,
            "messages": [{"role": "assistant", "content": summary_line}],
            "reply_source": "initializer:summary_line",
        }

        # A bare "load this protein" ends here; a design request continues.
        if state.get("intent") == "initialize" and not _wants_design(state):
            return Command(goto="__end__", update=update)
        return Command(goto="orchestrator", update=update)

    return initializer


def _wants_design(state: DesignState) -> bool:
    """Whether the user's request implies work beyond loading the target."""
    if state.get("requested_mutations"):
        return True
    goal = (state.get("goal") or "").lower()
    return any(
        word in goal
        for word in (
            "redesign", "design", "optimize", "optimise", "improve", "mutate",
            "variant", "stabilize", "stabilise", "fold", "predict", "engineer",
            "increase", "decrease", "raise", "lower", "more", "better",
        )
    )


def _ok(record: dict | None) -> dict:
    """The result of a task record, or {} when it failed or errored."""
    if not record or not record.get("ok"):
        return {}
    result = record.get("result")
    if not isinstance(result, dict) or "error" in result:
        return {}
    return result
