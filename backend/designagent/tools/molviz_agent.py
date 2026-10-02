"""Molecular Visualization Generator (a Local Task Agent).

Produces a declarative view spec keyed from design state and the user's prompt.
The frontend turns the spec into Mol* calls, which keeps untrusted model output
out of the browser as code: the LLM chooses *what* to show (residues, colors,
representation), never executable JavaScript.

With no LLM the spec is still built, from the mutated positions and any
functional residues the reference carries.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..llm import complete_json

log = logging.getLogger(__name__)

REPRESENTATIONS = {
    "cartoon",
    "ball-and-stick",
    "gaussian-surface",
    "molecular-surface",
    "spacefill",
    "putty",
    "backbone",
}

# Colorblind-safe accents, used in order.
PALETTE = ["#d1495b", "#2e86ab", "#e8a33d", "#5d8a52", "#8e6cab", "#49a3a3"]

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _positions_from_mutations(mutations: list[str]) -> list[int]:
    out = []
    for token in mutations or []:
        match = re.match(r"^[A-Z](\d{1,5})[A-Z]$", str(token).strip().upper())
        if match:
            out.append(int(match.group(1)))
    return out


def default_spec(
    *,
    reference: dict,
    lead: dict | None = None,
    prompt: str = "",
    chain: str = "",
) -> dict[str, Any]:
    """A sensible view with no LLM involved."""
    lead = lead or {}
    chain = chain or _primary_chain(reference)
    highlights: list[dict[str, Any]] = []

    mutated = _positions_from_mutations(lead.get("mutations", []))
    if mutated:
        highlights.append(
            {
                "chain": chain,
                "residues": mutated,
                "color": PALETTE[0],
                "label": "Mutated positions",
                "representation": "ball-and-stick",
            }
        )

    # Functional residues from UniProt features are worth seeing alongside.
    functional: list[int] = []
    for feature in reference.get("features", []) or []:
        if feature.get("type") in ("Active site", "Binding site", "Metal binding"):
            start, end = feature.get("start"), feature.get("end")
            if isinstance(start, int):
                functional.extend(range(start, (end if isinstance(end, int) else start) + 1))
    if functional:
        highlights.append(
            {
                "chain": chain,
                "residues": sorted(set(functional))[:40],
                "color": PALETTE[1],
                "label": "Functional residues",
                "representation": "ball-and-stick",
            }
        )

    structures = []
    if lead.get("structure_path"):
        structures.append(
            {
                "source": "artifact",
                "value": lead.get("structure_artifact_id", ""),
                "path": lead["structure_path"],
                "format": lead.get("structure_format", "pdb"),
                "label": lead.get("design_id", "design"),
            }
        )
    elif reference.get("pdb_id"):
        structures.append(
            {
                "source": "pdb",
                "value": reference["pdb_id"],
                "format": "mmcif",
                "label": reference["pdb_id"],
            }
        )

    title = lead.get("design_id") or reference.get("pdb_id") or "Structure"
    return {
        "title": f"{title}",
        "structures": structures,
        "highlights": highlights,
        "representation": "cartoon",
        "focus": (
            {"chain": chain, "residues": mutated[:1]} if mutated else None
        ),
        "caption": _caption(reference, lead, mutated, functional),
    }


def _caption(reference: dict, lead: dict, mutated: list[int], functional: list[int]) -> str:
    parts = []
    if lead.get("design_id"):
        parts.append(f"Design {lead['design_id']}")
    elif reference.get("pdb_id"):
        parts.append(f"PDB {reference['pdb_id']}")
    if mutated:
        parts.append(f"{len(mutated)} mutated position(s) in red")
    if functional:
        parts.append("functional residues in blue")
    return "; ".join(parts)


def _primary_chain(reference: dict) -> str:
    chains = reference.get("chains") or []
    if chains:
        return str(chains[0].get("chain_id") or "A")
    return "A"


def sanitize_spec(spec: Any, *, fallback: dict) -> dict[str, Any]:
    """Validate an LLM-proposed spec, repairing or dropping bad fields.

    Anything unexpected falls back rather than reaching the viewer, so a bad
    generation degrades to a plain cartoon instead of a broken pane.
    """
    if not isinstance(spec, dict):
        return fallback

    out = dict(fallback)
    fallback_highlights = fallback.get("highlights") or []
    default_chain = str(fallback_highlights[0].get("chain", "A")) if fallback_highlights else "A"

    title = spec.get("title")
    if isinstance(title, str) and title.strip():
        out["title"] = title.strip()[:120]

    caption = spec.get("caption")
    if isinstance(caption, str) and caption.strip():
        out["caption"] = caption.strip()[:400]

    rep = spec.get("representation")
    if isinstance(rep, str) and rep in REPRESENTATIONS:
        out["representation"] = rep

    highlights = []
    for raw in spec.get("highlights") or []:
        if not isinstance(raw, dict):
            continue
        residues = raw.get("residues")
        if isinstance(residues, int):
            residues = [residues]
        if not isinstance(residues, list):
            continue
        clean_residues = []
        for value in residues[:200]:
            try:
                clean_residues.append(int(value))
            except (TypeError, ValueError):
                continue
        if not clean_residues:
            continue
        color = raw.get("color")
        if not (isinstance(color, str) and HEX_RE.match(color)):
            color = PALETTE[len(highlights) % len(PALETTE)]
        hrep = raw.get("representation")
        if hrep not in REPRESENTATIONS:
            hrep = "ball-and-stick"
        chain = raw.get("chain")
        highlights.append(
            {
                "chain": str(chain)[:4] if chain else default_chain,
                "residues": clean_residues,
                "color": color,
                "label": str(raw.get("label", ""))[:80],
                "representation": hrep,
            }
        )
    if highlights:
        out["highlights"] = highlights

    focus = spec.get("focus")
    if isinstance(focus, dict):
        residues = focus.get("residues")
        if isinstance(residues, list) and residues:
            try:
                out["focus"] = {
                    "chain": str(focus.get("chain") or default_chain)[:4],
                    "residues": [int(residues[0])],
                }
            except (TypeError, ValueError):
                pass

    # Structures are never taken from the model: they decide what loads.
    out["structures"] = fallback.get("structures", [])
    return out


SYSTEM = """You design molecular visualizations for a protein engineering tool.
Given a reference protein, an optional redesigned variant, and the user's
request, choose what the viewer should emphasize.

Return JSON with these keys:
  title: short string
  representation: one of cartoon, ball-and-stick, gaussian-surface,
    molecular-surface, spacefill, putty, backbone
  highlights: array of {chain, residues (array of integers), color (#rrggbb), label, representation}
  focus: {chain, residues: [one integer]} or null
  caption: one sentence explaining what is shown

Rules: use only residue numbers supported by the data given to you. Prefer at
most three highlight groups. Use distinct, colorblind-safe colors."""


# --- task body -------------------------------------------------------------


async def generate_visualization(
    prompt: str = "",
    reference: dict | None = None,
    lead: dict | None = None,
    key_metric: dict | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Build a Mol* view spec for the current design state."""
    reference = reference or {}
    lead = lead or {}
    fallback = default_spec(reference=reference, lead=lead, prompt=prompt)

    if not (reference or lead):
        return {"error": "nothing to visualize yet", "spec": fallback}

    context = {
        "user_request": prompt,
        "reference": {
            "pdb_id": reference.get("pdb_id"),
            "chains": [
                {"chain_id": c.get("chain_id"), "length": c.get("length")}
                for c in (reference.get("chains") or [])[:6]
            ],
            "length": reference.get("length"),
            "functional_features": [
                {"type": f.get("type"), "start": f.get("start"), "end": f.get("end")}
                for f in (reference.get("features") or [])[:20]
            ],
            "ligands": [lig.get("comp_id") for lig in (reference.get("ligands") or [])[:8]],
        },
        "lead_design": {
            "design_id": lead.get("design_id"),
            "mutations": lead.get("mutations", []),
            "metrics": lead.get("metrics", {}),
        },
        "key_metric": (key_metric or {}).get("name"),
    }

    import json

    # Runs in a pool worker, so `complete_json` resolves the key from the settings
    # the worker was handed at fork (runtime.py). `llm_error` travels back with the
    # spec because the worker cannot reach the node's warnings channel itself.
    reasons: list[str] = []
    proposed = await complete_json(
        SYSTEM, json.dumps(context, default=str), on_fallback=reasons.append
    )
    spec = sanitize_spec(proposed, fallback=fallback)
    from ..llm import NO_KEY

    error = next((r for r in reasons if r != NO_KEY), "")
    return {
        "spec": spec,
        "llm_used": proposed is not None,
        "llm_error": error,
        # Which function wrote the caption the user will read. `sanitize_spec`
        # keeps the fallback's caption unless the model proposed its own, so a
        # repaired generation is still the rule-based sentence.
        "caption_source": (
            "molviz:llm"
            if proposed is not None and spec.get("caption") != fallback.get("caption")
            else "molviz:_caption"
        ),
    }
