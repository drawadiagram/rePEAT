"""Sequence redesign.

ProteinMPNN proper runs remotely (it needs weights and a GPU), so this module
provides the job spec and the output parser for the HPC interface.

`propose_variants` is the app-local alternative: an explicit, rule-based variant
generator used when no HPC endpoint is configured. It is a heuristic proposer,
not a learned model, and labels itself as such so nothing downstream mistakes
its output for ProteinMPNN samples.
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

log = logging.getLogger(__name__)

# Substitutions commonly used to raise thermostability / rigidity, and their
# rationale. Used by the local proposer, which must be able to explain itself.
STABILIZING = {
    "G": ("A", "glycine to alanine reduces backbone entropy"),
    "A": ("P", "alanine to proline rigidifies the backbone where tolerated"),
    "S": ("A", "serine to alanine removes an unsatisfied hydroxyl"),
    "N": ("D", "asparagine to aspartate avoids deamidation"),
    "Q": ("E", "glutamine to glutamate avoids deamidation"),
    "M": ("L", "methionine to leucine removes an oxidation-prone residue"),
    "C": ("S", "free cysteine to serine avoids mis-pairing"),
    "K": ("R", "lysine to arginine gives a better-solvated charge"),
    "V": ("I", "valine to isoleucine improves core packing"),
    "T": ("V", "threonine to valine strengthens beta propensity"),
}

MUTATION_RE = re.compile(r"^([ACDEFGHIKLMNPQRSTVWY])(\d{1,5})([ACDEFGHIKLMNPQRSTVWY])$")

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V",
}
ONE_TO_THREE = {v: k.title() for k, v in THREE_TO_ONE.items()}


def parse_mutation(token: str) -> tuple[str, int, str] | None:
    """Parse "A42V" or "Ala42Val" into (wt, 1-indexed position, mutant)."""
    token = token.strip()
    match = MUTATION_RE.match(token.upper())
    if match:
        return match.group(1), int(match.group(2)), match.group(3)

    long_form = re.match(r"^([A-Za-z]{3})(\d{1,5})([A-Za-z]{3})$", token)
    if long_form:
        wt = THREE_TO_ONE.get(long_form.group(1).upper())
        mut = THREE_TO_ONE.get(long_form.group(3).upper())
        if wt and mut:
            return wt, int(long_form.group(2)), mut
    return None


def apply_mutations(sequence: str, mutations: list[str]) -> tuple[str, list[str], list[str]]:
    """Apply mutation tokens to a sequence.

    Returns (new_sequence, applied, rejected). A mutation is rejected when the
    position is out of range or the wild-type residue does not match, which is
    how literature-mined mutations from a different numbering get filtered out.
    """
    chars = list(sequence)
    applied, rejected = [], []
    for token in mutations:
        parsed = parse_mutation(token)
        if not parsed:
            rejected.append(f"{token} (unparseable)")
            continue
        wt, pos, mut = parsed
        if pos < 1 or pos > len(chars):
            rejected.append(f"{token} (position outside 1-{len(chars)})")
            continue
        if chars[pos - 1] != wt:
            rejected.append(f"{token} (reference has {chars[pos - 1]}{pos})")
            continue
        chars[pos - 1] = mut
        applied.append(f"{wt}{pos}{mut}")
    return "".join(chars), applied, rejected


# --- HPC job -------------------------------------------------------------


def mpnn_job_spec(
    structure_path: str,
    *,
    num_sequences: int = 8,
    sampling_temp: float = 0.1,
    fixed_positions: list[int] | None = None,
    chain: str = "A",
    name: str = "proteinmpnn",
) -> dict[str, Any]:
    """A PSI/J-style job spec running ProteinMPNN on a remote endpoint."""
    args = [
        "--pdb_path", structure_path,
        "--pdb_path_chains", chain,
        "--num_seq_per_target", str(int(num_sequences)),
        "--sampling_temp", str(sampling_temp),
        "--out_folder", ".",
    ]
    if fixed_positions:
        args += ["--fixed_positions", " ".join(str(p) for p in fixed_positions)]
    return {
        "name": name,
        "executable": "protein_mpnn_run.py",
        "arguments": args,
        "outputs": ["seqs/*.fa"],
        "resources": {"node_count": 1, "processes": 1, "gpus": 1},
        "duration_sec": 1800,
    }


def parse_mpnn_fasta(text: str) -> list[dict[str, Any]]:
    """Parse ProteinMPNN output FASTA.

    Headers look like:
      >name, score=1.2345, global_score=..., seq_recovery=0.43
    The first record is the input sequence, which we mark rather than drop.
    """
    records: list[dict[str, Any]] = []
    header, chunks = None, []

    def flush() -> None:
        if header is None:
            return
        seq = "".join(chunks).strip().replace(" ", "")
        if not seq:
            return
        fields: dict[str, Any] = {}
        for part in header.split(","):
            if "=" in part:
                key, _, value = part.partition("=")
                try:
                    fields[key.strip()] = float(value)
                except ValueError:
                    fields[key.strip()] = value.strip()
        records.append(
            {
                "sequence": seq,
                "score": fields.get("score"),
                "global_score": fields.get("global_score"),
                "seq_recovery": fields.get("seq_recovery"),
                "sample": fields.get("sample"),
                "is_input": "sample" not in fields and "score" in fields and not records,
                "header": header,
            }
        )

    for line in text.splitlines():
        if line.startswith(">"):
            flush()
            header, chunks = line[1:].strip(), []
        elif header is not None:
            chunks.append(line.strip())
    flush()
    return records


# --- task bodies -----------------------------------------------------------


async def propose_variants(
    sequence: str = "",
    n: int = 6,
    suggested_mutations: list[str] | None = None,
    avoid_positions: list[int] | None = None,
    seed: int = 0,
    **_: Any,
) -> dict[str, Any]:
    """Rule-based variant proposals (the local stand-in for ProteinMPNN).

    Literature-mined mutations are tried first, then single substitutions from
    the stabilizing table. Every variant carries the reason it was proposed.
    """
    if not sequence:
        return {"error": "no sequence to redesign"}

    protected = set(avoid_positions or [])
    rng = random.Random(seed or 1234)
    variants: list[dict[str, Any]] = []
    seen_sequences = {sequence}

    # 1. Mutations named in the literature, applied individually.
    for token in suggested_mutations or []:
        if len(variants) >= n:
            break
        parsed = parse_mutation(token)
        if not parsed or parsed[1] in protected:
            continue
        new_seq, applied, _ = apply_mutations(sequence, [token])
        if applied and new_seq not in seen_sequences:
            seen_sequences.add(new_seq)
            variants.append(
                {
                    "sequence": new_seq,
                    "mutations": applied,
                    "rationale": "reported in the retrieved literature",
                    "source": "literature",
                }
            )

    # 2. Single stabilizing substitutions at eligible positions.
    positions = [
        i
        for i, aa in enumerate(sequence, start=1)
        if aa in STABILIZING and i not in protected
    ]
    rng.shuffle(positions)
    for pos in positions:
        if len(variants) >= n:
            break
        wt = sequence[pos - 1]
        mut, reason = STABILIZING[wt]
        new_seq, applied, _ = apply_mutations(sequence, [f"{wt}{pos}{mut}"])
        if applied and new_seq not in seen_sequences:
            seen_sequences.add(new_seq)
            variants.append(
                {
                    "sequence": new_seq,
                    "mutations": applied,
                    "rationale": reason,
                    "source": "heuristic",
                }
            )

    return {
        "method": "rule_based_proposer",
        "note": "heuristic proposals, not ProteinMPNN samples",
        "variants": variants[:n],
        "n": len(variants[:n]),
        "parent_sequence": sequence,
    }


async def apply_mutation_set(
    sequence: str = "", mutations: list[str] | None = None, **_: Any
) -> dict[str, Any]:
    """Build one variant from an explicit mutation list (a user request)."""
    if not sequence:
        return {"error": "no sequence given"}
    new_seq, applied, rejected = apply_mutations(sequence, mutations or [])
    if not applied:
        return {
            "error": "no mutations could be applied",
            "rejected": rejected,
            "parent_sequence": sequence,
        }
    return {
        "variants": [
            {
                "sequence": new_seq,
                "mutations": applied,
                "rationale": "requested",
                "source": "user",
            }
        ],
        "applied": applied,
        "rejected": rejected,
        "n": 1,
        "parent_sequence": sequence,
    }
