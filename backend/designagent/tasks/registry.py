"""The task catalog.

Every entry names a module-level async function so a process-pool worker can
import it by reference instead of receiving a closure. `interface` is the
default routing choice; the orchestrator may override it (e.g. send folding to
HPC when an endpoint is available).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..tools import (
    chemgraph_agent,
    esmfold,
    literature,
    molviz_agent,
    pdb,
    proteinmpnn,
    scoring,
    uniprot,
)


@dataclass(frozen=True)
class TaskDef:
    name: str
    body: Callable
    interface: str  # local | query | hpc
    description: str
    # Where the result belongs, for the analyst's dispatch.
    produces: str = "data"  # data | designs | structure | visualization


CATALOG: dict[str, TaskDef] = {
    # --- Query via API ---
    "pdb_lookup": TaskDef(
        "pdb_lookup", pdb.pdb_lookup, "query",
        "Resolve a PDB entry and read its metadata, sequence and ligands.",
    ),
    "pdb_structure": TaskDef(
        "pdb_structure", pdb.pdb_structure, "query",
        "Download experimental coordinates for a PDB entry.",
        produces="structure",
    ),
    "uniprot_lookup": TaskDef(
        "uniprot_lookup", uniprot.uniprot_lookup, "query",
        "Read a UniProt entry: sequence, organism, functional features, PDB xrefs.",
    ),
    "literature_lookup": TaskDef(
        "literature_lookup", literature.literature_lookup, "query",
        "Retrieve and preprocess papers about the target and the design goal.",
    ),
    # --- Local task agents ---
    "fold_sequence": TaskDef(
        "fold_sequence", esmfold.fold_sequence, "local",
        "Predict a structure for a sequence and score its confidence.",
        produces="structure",
    ),
    "propose_variants": TaskDef(
        "propose_variants", proteinmpnn.propose_variants, "local",
        "Propose sequence variants from literature hints and stabilizing rules.",
        produces="designs",
    ),
    "apply_mutations": TaskDef(
        "apply_mutations", proteinmpnn.apply_mutation_set, "local",
        "Build a variant from an explicit list of mutations.",
        produces="designs",
    ),
    "score_structure": TaskDef(
        "score_structure", scoring.score_structure, "local",
        "Score a structure: confidence, RMSD to reference, sequence metrics.",
    ),
    "score_sequences": TaskDef(
        "score_sequences", scoring.score_sequences, "local",
        "Cheap sequence-only metrics for a batch of candidates.",
        produces="designs",
    ),
    "generate_visualization": TaskDef(
        "generate_visualization", molviz_agent.generate_visualization, "local",
        "Compose a Mol* view spec from the design state and the user's request.",
        produces="visualization",
    ),
    "run_chemgraph": TaskDef(
        "run_chemgraph", chemgraph_agent.run_chemgraph, "local",
        "Run a cheminformatics or quantum-chemistry task through ChemGraph.",
    ),
    # --- Remote HPC workflows ---
    "fold_sequence_hpc": TaskDef(
        "fold_sequence_hpc", esmfold.fold_sequence, "hpc",
        "Predict a structure on an HPC endpoint (large proteins, batches).",
        produces="structure",
    ),
    "proteinmpnn": TaskDef(
        "proteinmpnn", proteinmpnn.propose_variants, "hpc",
        "Run ProteinMPNN on an HPC endpoint to sample redesigned sequences.",
        produces="designs",
    ),
}


def get(name: str) -> TaskDef | None:
    return CATALOG.get(name)


def describe(interfaces: set[str] | None = None) -> str:
    """A catalog listing for the orchestrator's prompt."""
    lines = []
    for task in CATALOG.values():
        if interfaces and task.interface not in interfaces:
            continue
        lines.append(f"- {task.name} ({task.interface}): {task.description}")
    return "\n".join(lines)
