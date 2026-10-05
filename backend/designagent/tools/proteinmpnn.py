"""Sequence redesign.

ProteinMPNN proper runs through the HPC interface, because it needs weights and
(for anything large) a GPU. This module owns the three pure pieces that takes:
the job spec, the output parser, and the adapter that turns a finished job's
FASTA into the variant shape the graph reads.

The adapter lives here rather than in a task body because on the `hpc` path the
body never runs -- `OrbitInterface._submit_job` reads `params["job_spec"]` -- and
not in the transport, which must not know what a FASTA is. That asymmetry is why
`parse_mpnn_fasta` sat with no callers for so long.

`propose_variants` is the app-local alternative: an explicit, rule-based variant
generator used when no HPC endpoint is configured. It is a heuristic proposer,
not a learned model, and labels itself as such so nothing downstream mistakes
its output for ProteinMPNN samples.
"""

from __future__ import annotations

import base64
import gzip
import logging
import random
import re
from typing import Any

from .scoring import mutations_between

log = logging.getLogger(__name__)

# Backbone atoms are all ProteinMPNN reads, and dropping the rest is the
# difference between fitting in argv and not: 1UBQ goes from ~80 KB to ~25 KB
# before compression.
BACKBONE_ATOMS = frozenset({"N", "CA", "C", "O"})

# Ceiling on the gzipped, base64'd structure a job may carry. `artifacts.wrap`
# chunks it across argv elements, so the binding limit is ARG_MAX (2 MiB on
# Linux) rather than MAX_ARG_STRLEN (128 KiB per element). Half of ARG_MAX
# leaves room for the script, the environment, and the command's own flags. Past
# this the caller hears a named error instead of a scheduler failure; the real
# fix is upstream staging (backlog C6).
MAX_INLINE_B64 = 1_048_576

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


def backbone_pdb(text: str, chain: str = "A") -> str:
    """Keep only the backbone of one chain.

    ProteinMPNN reads N/CA/C/O and ignores the rest, so sending side chains
    wastes the argv budget the structure has to fit into.
    """
    kept: list[str] = []
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            if line[12:16].strip() not in BACKBONE_ATOMS:
                continue
            if chain and len(line) > 21 and line[21] != chain:
                continue
            kept.append(line)
        elif line.startswith(("TER", "END")):
            kept.append(line)
    if not any(ln.startswith(("ATOM", "HETATM")) for ln in kept):
        return ""
    if not kept[-1].startswith("END"):
        kept.append("END")
    return "\n".join(kept) + "\n"


def mpnn_job_spec(
    structure_text: str,
    *,
    num_sequences: int = 8,
    sampling_temp: float = 0.1,
    chain: str = "A",
    name: str = "proteinmpnn",
    command: str = "protein_mpnn_run.py",
    prologue: str = "",
) -> dict[str, Any]:
    """A PSI/J-style job spec running ProteinMPNN on a remote endpoint.

    The structure travels *with* the job, as a declared input, because a blob
    path on this machine means nothing at the far end and the broker stages
    nothing (see `tasks/hpc/artifacts.py`). `command` is a command string rather
    than a path so a site can spell `python /sw/ProteinMPNN/protein_mpnn_run.py`
    and a local CPU install can spell its own venv.
    """
    backbone = backbone_pdb(structure_text, chain)
    if not backbone:
        raise ValueError(f"no backbone atoms for chain {chain!r} in the structure")
    payload = len(base64.b64encode(gzip.compress(backbone.encode())))
    if payload > MAX_INLINE_B64:
        raise ValueError(
            f"structure is too large to inline: {payload} bytes of payload "
            f"exceeds {MAX_INLINE_B64}; staging is in-band (backlog C6)"
        )

    argv = command.split()
    return {
        "name": name,
        "executable": argv[0],
        "arguments": [
            *argv[1:],
            "--pdb_path", "in.pdb",
            "--pdb_path_chains", chain,
            "--num_seq_per_target", str(int(num_sequences)),
            "--sampling_temp", str(sampling_temp),
            "--out_folder", ".",
        ],
        # `--fixed_positions` is deliberately absent: it is not a ProteinMPNN
        # flag. The real one is `--fixed_positions_jsonl` and takes a file,
        # which `inputs` can now carry. See plans/BACKLOG.md.
        "inputs": {"in.pdb": backbone},
        "outputs": ["seqs/*.fa"],
        "prologue": prologue,
        "resources": {"node_count": 1, "processes": 1},
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


def variants_from_mpnn_fasta(
    fasta: str, *, parent_sequence: str = "", requested: int = 0
) -> dict[str, Any]:
    """Turn a finished ProteinMPNN run's FASTA into graph-shaped variants.

    `parse_mpnn_fasta`'s first caller. It drops the input record rather than
    trusting its position, computes mutations against the parent, and lifts the
    model's own `model_name`/`git_hash` into provenance -- read from the output,
    never asserted by us, so a run that cannot say which weights it used is
    distinguishable from one that can.
    """
    records = parse_mpnn_fasta(fasta)
    if not records:
        return {
            "error": "no sequences in the ProteinMPNN output",
            "variants": [],
            "n": 0,
        }

    provenance: dict[str, Any] = {}
    for record in records:
        if record.get("is_input"):
            header = record.get("header") or ""
            for field in ("model_name", "git_hash", "seed"):
                match = re.search(rf"{field}=([^\s,]+)", header)
                if match:
                    provenance[field] = match.group(1)
            break

    variants: list[dict[str, Any]] = []
    seen = {parent_sequence} if parent_sequence else set()
    for record in records:
        if record.get("is_input"):
            continue
        sequence = record.get("sequence") or ""
        if not sequence or sequence in seen:
            continue
        seen.add(sequence)
        score = record.get("score")
        entry: dict[str, Any] = {
            "sequence": sequence,
            "mutations": mutations_between(parent_sequence, sequence),
            "rationale": _mpnn_rationale(record),
            "source": "proteinmpnn",
        }
        if isinstance(score, (int, float)):
            entry["score"] = float(score)
            # The orchestrator registers mpnn_score as a rankable metric; it
            # only arrives if it is attached here.
            entry["metrics"] = {"mpnn_score": float(score)}
        variants.append(entry)

    out: dict[str, Any] = {
        "method": "proteinmpnn",
        "variants": variants,
        "n": len(variants),
        "parent_sequence": parent_sequence,
        "provenance": provenance,
    }
    if not variants:
        out["error"] = "ProteinMPNN returned no sequences other than the input"
    elif requested and len(variants) < requested:
        out["short"] = f"{len(variants)} of {requested} requested sequences"
    return out


def _mpnn_rationale(record: dict[str, Any]) -> str:
    bits = []
    sample = record.get("sample")
    if sample is not None:
        bits.append(f"sample {int(sample) if isinstance(sample, float) else sample}")
    score = record.get("score")
    if isinstance(score, (int, float)):
        bits.append(f"score {score:.4f}")
    recovery = record.get("seq_recovery")
    if isinstance(recovery, (int, float)):
        bits.append(f"{recovery:.0%} identity to the input")
    return "ProteinMPNN " + ", ".join(bits) if bits else "ProteinMPNN sample"


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


async def proteinmpnn_local_fallback(**kwargs: Any) -> dict[str, Any]:
    """What `CATALOG["proteinmpnn"]` runs when no endpoint is attached.

    The catalog entry stays, so the ledger still records that ProteinMPNN was
    what the round asked for, but the result says at the point of substitution
    that a job was never submitted. Pointing the entry straight at
    `propose_variants` made a heuristic round indistinguishable from a real one
    in the output.
    """
    out = await propose_variants(**kwargs)
    if "error" in out:
        return out
    out["requested"] = "proteinmpnn"
    out["note"] = (
        "heuristic proposals, not ProteinMPNN samples "
        "(no HPC endpoint was attached, so the job was never submitted)"
    )
    return out


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
