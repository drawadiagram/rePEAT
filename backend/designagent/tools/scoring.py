"""Scoring and comparison of designs.

Pure CPU work, no network: these are the bodies that run in the process pool.
Structure parsing uses Biopython, which is already a dependency.
"""

from __future__ import annotations

import logging
import math
from io import StringIO
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "MSE": "M", "SEC": "U", "PYL": "O",
}

# Kyte-Doolittle hydropathy, for a cheap surface-hydrophobicity proxy.
HYDROPATHY = {
    "A": 1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C": 2.5, "Q": -3.5, "E": -3.5,
    "G": -0.4, "H": -3.2, "I": 4.5, "L": 3.8, "K": -3.9, "M": 1.9, "F": 2.8,
    "P": -1.6, "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V": 4.2,
}


# --- sequence-level --------------------------------------------------------


def mutations_between(reference: str, variant: str) -> list[str]:
    """Point mutations as 1-indexed strings, e.g. "A42V".

    Only meaningful for equal-length sequences; returns [] otherwise, which is
    the honest answer for an indel-containing design.
    """
    if not reference or not variant or len(reference) != len(variant):
        return []
    return [
        f"{r}{i}{v}"
        for i, (r, v) in enumerate(zip(reference, variant), start=1)
        if r != v
    ]


def sequence_identity(a: str, b: str) -> float:
    """Fraction of identical positions over the shorter sequence."""
    if not a or not b:
        return 0.0
    pairs = list(zip(a, b))
    return sum(1 for x, y in pairs if x == y) / max(len(pairs), 1)


def mean_hydropathy(sequence: str) -> float:
    values = [HYDROPATHY[c] for c in sequence.upper() if c in HYDROPATHY]
    return sum(values) / len(values) if values else 0.0


def net_charge(sequence: str, ph: float = 7.0) -> float:
    """Net charge at neutral pH, counting only full charges (good enough to rank)."""
    seq = sequence.upper()
    positive = seq.count("K") + seq.count("R") + 0.1 * seq.count("H")
    negative = seq.count("D") + seq.count("E")
    return positive - negative


# --- structure-level -------------------------------------------------------


def _parse_structure(text: str, fmt: str = "pdb"):
    from Bio.PDB import MMCIFParser, PDBParser

    parser = (
        MMCIFParser(QUIET=True) if fmt in ("mmcif", "cif") else PDBParser(QUIET=True)
    )
    return parser.get_structure("s", StringIO(text))


def bfactor_stats(text: str) -> dict[str, float] | None:
    """Per-CA B-factor column statistics, raw.

    Predicted models (ESMFold, AlphaFold) store per-residue pLDDT here, but an
    experimental structure stores a real temperature factor, so the caller
    decides how to label these. See `plddt_from_pdb`.
    """
    values: list[float] = []
    for line in text.splitlines():
        if not line.startswith("ATOM"):
            continue
        if line[12:16].strip() != "CA":
            continue
        try:
            values.append(float(line[60:66]))
        except ValueError:
            continue
    if not values:
        return None
    # Some tools emit pLDDT on 0-1 rather than 0-100.
    if max(values) <= 1.0:
        values = [v * 100.0 for v in values]
    return {
        "mean": round(sum(values) / len(values), 2),
        "min": round(min(values), 2),
        "frac_ge_70": round(sum(1 for v in values if v >= 70.0) / len(values), 4),
        "n_residues": len(values),
    }


def plddt_from_pdb(text: str) -> dict[str, float] | None:
    """pLDDT metrics, for *predicted* structures only."""
    stats = bfactor_stats(text)
    if not stats:
        return None
    return {
        "plddt": stats["mean"],
        "plddt_min": stats["min"],
        "plddt_frac_confident": stats["frac_ge_70"],
        "n_residues": stats["n_residues"],
    }


def ca_coords(text: str, fmt: str = "pdb", chain: str | None = None) -> list[tuple]:
    """CA coordinates with residue ids, in file order."""
    structure = _parse_structure(text, fmt)
    out = []
    for model in structure:
        for ch in model:
            if chain and ch.id != chain:
                continue
            for residue in ch:
                if "CA" in residue:
                    out.append((ch.id, residue.id[1], tuple(residue["CA"].coord)))
        break  # first model only
    return out


def rmsd_between(
    text_a: str, text_b: str, fmt_a: str = "pdb", fmt_b: str = "pdb"
) -> dict[str, float] | None:
    """Superimpose on shared CA positions and return RMSD in angstroms."""
    import numpy as np
    from Bio.PDB import Superimposer
    from Bio.PDB.Atom import Atom

    try:
        a = ca_coords(text_a, fmt_a)
        b = ca_coords(text_b, fmt_b)
    except Exception as exc:
        log.warning("structure parse failed: %s", exc)
        return None
    if not a or not b:
        return None

    # Match on residue number within the same chain where possible, else by order.
    index_b = {(c, r): xyz for c, r, xyz in b}
    pairs = [((c, r), xyz, index_b[(c, r)]) for c, r, xyz in a if (c, r) in index_b]
    if len(pairs) < 3:
        n = min(len(a), len(b))
        pairs = [(("-", i), a[i][2], b[i][2]) for i in range(n)]
    if len(pairs) < 3:
        return None

    fixed = [_atom(xyz, i) for i, (_, xyz, _) in enumerate(pairs)]
    moving = [_atom(xyz, i) for i, (_, _, xyz) in enumerate(pairs)]
    sup = Superimposer()
    sup.set_atoms(fixed, moving)
    return {"rmsd": round(float(sup.rms), 3), "n_aligned": len(pairs)}


def _atom(xyz, serial: int):
    import numpy as np
    from Bio.PDB.Atom import Atom

    return Atom("CA", np.array(xyz, dtype=float), 0.0, 1.0, " ", "CA", serial, "C")


def sequence_from_structure(text: str, fmt: str = "pdb", chain: str | None = None) -> str:
    structure = _parse_structure(text, fmt)
    letters = []
    for model in structure:
        for ch in model:
            if chain and ch.id != chain:
                continue
            for residue in ch:
                name = residue.get_resname().upper()
                if name in THREE_TO_ONE:
                    letters.append(THREE_TO_ONE[name])
        break
    return "".join(letters)


# --- task bodies -----------------------------------------------------------


async def score_structure(
    structure_path: str = "",
    structure_text: str = "",
    fmt: str = "pdb",
    reference_path: str = "",
    reference_fmt: str = "pdb",
    reference_sequence: str = "",
    predicted: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """Score one structure, optionally against a reference.

    `predicted` says whether the B-factor column holds pLDDT (a model) or a real
    temperature factor (an experimental structure); mislabelling it would invent
    a confidence score, so experimental input is reported as b_factor_* instead.
    """
    text = structure_text or _read(structure_path)
    if not text:
        return {"error": "no structure to score"}

    metrics: dict[str, float] = {}
    stats = bfactor_stats(text)
    if stats:
        if predicted:
            metrics.update(
                {
                    "plddt": stats["mean"],
                    "plddt_min": stats["min"],
                    "plddt_frac_confident": stats["frac_ge_70"],
                }
            )
        else:
            metrics["b_factor_mean"] = stats["mean"]
        metrics["n_residues"] = float(stats["n_residues"])

    try:
        seq = sequence_from_structure(text, fmt)
    except Exception as exc:
        log.warning("could not extract sequence: %s", exc)
        seq = ""

    if seq:
        metrics["length"] = float(len(seq))
        metrics["mean_hydropathy"] = round(mean_hydropathy(seq), 3)
        metrics["net_charge"] = round(net_charge(seq), 2)

    ref_text = _read(reference_path) if reference_path else ""
    if ref_text:
        rms = rmsd_between(text, ref_text, fmt, reference_fmt)
        if rms:
            metrics["rmsd_to_reference"] = rms["rmsd"]
            metrics["n_aligned"] = float(rms["n_aligned"])

    out: dict[str, Any] = {"metrics": metrics, "sequence": seq}
    if reference_sequence and seq:
        out["mutations"] = mutations_between(reference_sequence, seq)
        metrics["sequence_identity"] = round(
            sequence_identity(reference_sequence, seq), 4
        )
    return out


async def score_sequences(
    sequences: list[dict] | None = None,
    reference_sequence: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Cheap sequence-only metrics for a batch of candidate designs."""
    results = []
    for item in sequences or []:
        seq = item.get("sequence", "") if isinstance(item, dict) else str(item)
        if not seq:
            continue
        metrics = {
            "length": float(len(seq)),
            "mean_hydropathy": round(mean_hydropathy(seq), 3),
            "net_charge": round(net_charge(seq), 2),
        }
        entry: dict[str, Any] = {"sequence": seq, "metrics": metrics}
        if isinstance(item, dict):
            for key in ("design_id", "score", "name"):
                if key in item:
                    entry[key] = item[key]
            if "score" in item:
                # ProteinMPNN reports negative log-likelihood; lower is better.
                metrics["mpnn_score"] = float(item["score"])
        if reference_sequence:
            metrics["sequence_identity"] = round(
                sequence_identity(reference_sequence, seq), 4
            )
            entry["mutations"] = mutations_between(reference_sequence, seq)
        results.append(entry)
    return {"scored": results, "n": len(results)}


def _read(path: str | Path) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        log.warning("structure path missing: %s", p)
        return ""
    return p.read_text(errors="replace")
