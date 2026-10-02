"""Structure prediction.

Three backends behind one task body:
  esmatlas - ESM Atlas public fold API, no install, length-limited
  local    - a locally installed ESMFold (torch), if present
  hpc      - emit a job spec for the Orbit/PSI-J interface to run

`fold_sequence` never raises for an unavailable backend; it returns an `error`
key so the analyst can record a FAILED task and the chat can explain why.
"""

from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from .http import post_text

log = logging.getLogger(__name__)

ESMATLAS_URL = "https://api.esmatlas.com/foldSequence/v1/pdb/"
# The public endpoint rejects long sequences.
ESMATLAS_MAX_LEN = 400

VALID_AA = set("ACDEFGHIKLMNPQRSTVWY")


def clean_sequence(sequence: str) -> str:
    """Uppercase, strip whitespace/FASTA header, drop non-standard residues."""
    lines = [ln.strip() for ln in sequence.splitlines() if not ln.startswith(">")]
    seq = "".join(lines).upper().replace(" ", "")
    return "".join(c for c in seq if c in VALID_AA)


async def fold_esmatlas(sequence: str) -> dict[str, Any]:
    if len(sequence) > ESMATLAS_MAX_LEN:
        return {
            "error": (
                f"sequence is {len(sequence)} aa; the public ESM Atlas endpoint "
                f"accepts up to {ESMATLAS_MAX_LEN}. Use the local or hpc backend."
            )
        }
    text = await post_text(ESMATLAS_URL, sequence)
    if not text or "ATOM" not in text:
        return {"error": "ESM Atlas returned no structure", "detail": (text or "")[:200]}
    return {"structure": text, "format": "pdb", "backend": "esmatlas"}


async def fold_local(sequence: str) -> dict[str, Any]:
    """Fold with a locally installed ESMFold. Heavy; only if torch+esm present."""
    try:
        import esm  # noqa: F401
        import torch  # noqa: F401
    except ImportError as exc:
        return {"error": f"local ESMFold unavailable: {exc}"}

    import asyncio

    def _run() -> str:
        import esm
        import torch

        model = esm.pretrained.esmfold_v1()
        model = model.eval()
        if torch.cuda.is_available():
            model = model.cuda()
        with torch.no_grad():
            return model.infer_pdb(sequence)

    try:
        text = await asyncio.to_thread(_run)
    except Exception as exc:  # OOM, missing weights, ...
        return {"error": f"local ESMFold failed: {exc}"}
    return {"structure": text, "format": "pdb", "backend": "local"}


def fold_job_spec(sequence: str, *, name: str = "fold") -> dict[str, Any]:
    """A PSI/J-style job spec for folding on HPC.

    Returned rather than executed: the HPC interface owns submission, and this
    keeps the command in one place for both Orbit and Globus.
    """
    return {
        "name": name,
        "executable": "bash",
        "arguments": [
            "-lc",
            # The site provides `fold-sequence`; writing the sequence to stdin
            # avoids argv length limits for large proteins.
            "cat > seq.fasta && fold-sequence seq.fasta --out model.pdb && cat model.pdb",
        ],
        "stdin_text": f">query\n{sequence}\n",
        "outputs": ["model.pdb"],
        "resources": {"node_count": 1, "processes": 1, "gpus": 1},
        "duration_sec": 1800,
    }


# --- task body -------------------------------------------------------------


async def fold_sequence(
    sequence: str = "",
    backend: str = "",
    design_id: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Predict a structure for one sequence."""
    seq = clean_sequence(sequence)
    if not seq:
        return {"error": "no valid amino-acid sequence given"}

    backend = backend or get_settings().fold_backend
    if backend == "local":
        out = await fold_local(seq)
    elif backend == "hpc":
        # Caller (orchestrator) routes this through the HPC interface instead.
        return {"job_spec": fold_job_spec(seq), "backend": "hpc", "sequence": seq}
    else:
        out = await fold_esmatlas(seq)

    if "error" in out:
        return {**out, "sequence": seq, "design_id": design_id}

    # Score the model here so one task yields a complete, rankable design.
    from .scoring import plddt_from_pdb

    metrics = plddt_from_pdb(out["structure"]) or {}
    return {
        **out,
        "sequence": seq,
        "design_id": design_id,
        "metrics": metrics,
        "length": len(seq),
    }
