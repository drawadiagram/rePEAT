"""RCSB PDB lookups: search, entry metadata, sequence, coordinates."""

from __future__ import annotations

import logging
import re
from typing import Any

from .http import get_json, get_text

log = logging.getLogger(__name__)

DATA_API = "https://data.rcsb.org/rest/v1/core"
SEARCH_API = "https://search.rcsb.org/rcsbsearch/v2/query"
FILES = "https://files.rcsb.org/download"

PDB_ID_RE = re.compile(r"\b([1-9][A-Za-z0-9]{3})\b")


def find_pdb_ids(text: str) -> list[str]:
    """Pull candidate 4-character PDB ids out of free text.

    Deliberately conservative: the first character must be 1-9, which is true of
    every real PDB id and rules out most English words.
    """
    out, seen = [], set()
    for match in PDB_ID_RE.finditer(text):
        pid = match.group(1).upper()
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


async def search_pdb(query: str, limit: int = 5) -> list[str]:
    """Full-text search, returning PDB ids best-match first."""
    payload = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": query},
        },
        "return_type": "entry",
        "request_options": {"paginate": {"start": 0, "rows": int(limit)}},
    }
    data = await get_json(SEARCH_API, params={"json": _compact(payload)})
    if not data:
        return []
    return [item["identifier"] for item in data.get("result_set", [])]


async def fetch_entry(pdb_id: str) -> dict[str, Any]:
    """Entry-level metadata plus per-entity sequences and ligands."""
    pdb_id = pdb_id.upper()
    entry = await get_json(f"{DATA_API}/entry/{pdb_id}")
    if not entry:
        return {}

    struct = entry.get("struct") or {}
    out: dict[str, Any] = {
        "pdb_id": pdb_id,
        "name": struct.get("title", ""),
        "method": _first(
            [m.get("method") for m in entry.get("exptl", []) if m.get("method")]
        ),
        "resolution": _first(
            (entry.get("rcsb_entry_info") or {}).get("resolution_combined") or []
        ),
        "deposited": (entry.get("rcsb_accession_info") or {}).get("deposit_date", ""),
    }

    entity_ids = (entry.get("rcsb_entry_container_identifiers") or {}).get(
        "polymer_entity_ids", []
    )
    chains: list[dict[str, Any]] = []
    sequence = ""
    uniprot_id = ""
    organism = ""
    function = ""

    for entity_id in entity_ids:
        ent = await get_json(f"{DATA_API}/polymer_entity/{pdb_id}/{entity_id}")
        if not ent:
            continue
        poly = ent.get("entity_poly") or {}
        seq = poly.get("pdbx_seq_one_letter_code_can") or ""
        seq = re.sub(r"\s+", "", seq)
        ids = ent.get("rcsb_polymer_entity_container_identifiers") or {}
        asym_ids = ids.get("auth_asym_ids") or ids.get("asym_ids") or []
        desc = (ent.get("rcsb_polymer_entity") or {}).get("pdbx_description", "")

        chains.append(
            {
                "chain_id": asym_ids[0] if asym_ids else entity_id,
                "all_chain_ids": asym_ids,
                "entity_id": entity_id,
                "description": desc,
                "length": len(seq),
                "sequence": seq,
            }
        )
        # The first (usually largest) protein entity is the design target.
        if seq and not sequence:
            sequence = seq
            function = desc
            for ref in ent.get("rcsb_polymer_entity_align", []) or []:
                if (ref.get("reference_database_name") or "").upper() == "UNIPROT":
                    uniprot_id = ref.get("reference_database_accession", "")
                    break
            if not uniprot_id:
                uniprot_id = _first(ids.get("uniprot_ids") or []) or ""
            src = ent.get("rcsb_entity_source_organism") or []
            if src:
                organism = src[0].get("scientific_name", "")

    ligands = []
    for comp_id in (entry.get("rcsb_entry_container_identifiers") or {}).get(
        "non_polymer_entity_ids", []
    ) or []:
        nonpoly = await get_json(f"{DATA_API}/nonpolymer_entity/{pdb_id}/{comp_id}")
        if not nonpoly:
            continue
        comp = (nonpoly.get("pdbx_entity_nonpoly") or {})
        ligands.append({"comp_id": comp.get("comp_id", ""), "name": comp.get("name", "")})

    out.update(
        {
            "chains": chains,
            "sequence": sequence,
            "length": len(sequence),
            "uniprot_id": uniprot_id,
            "organism": organism,
            "function": function,
            "ligands": ligands,
        }
    )
    return out


async def fetch_structure(pdb_id: str, fmt: str = "pdb") -> tuple[str, str] | None:
    """Download coordinates. Returns (text, format) or None."""
    pdb_id = pdb_id.upper()
    suffix = "cif" if fmt in ("mmcif", "cif") else "pdb"
    text = await get_text(f"{FILES}/{pdb_id}.{suffix}")
    if not text:
        # Very large entries are mmCIF-only.
        if suffix == "pdb":
            text = await get_text(f"{FILES}/{pdb_id}.cif")
            if text:
                return text, "mmcif"
        return None
    return text, "mmcif" if suffix == "cif" else "pdb"


# --- task bodies (module level so a pool worker can import them) -----------


async def pdb_lookup(query: str = "", pdb_id: str = "", **_: Any) -> dict[str, Any]:
    """Resolve a prompt or explicit id to reference-design fields."""
    target = (pdb_id or "").upper()
    if not target and query:
        ids = find_pdb_ids(query)
        target = ids[0] if ids else _first(await search_pdb(query, limit=1)) or ""
    if not target:
        return {"error": "no PDB entry matched", "query": query}

    entry = await fetch_entry(target)
    if not entry:
        return {"error": f"PDB entry {target} not found", "pdb_id": target}
    return entry


async def pdb_structure(pdb_id: str, fmt: str = "pdb", **_: Any) -> dict[str, Any]:
    """Fetch coordinates; the caller writes them to a blob."""
    got = await fetch_structure(pdb_id, fmt)
    if not got:
        return {"error": f"could not download structure for {pdb_id}"}
    text, actual = got
    return {"pdb_id": pdb_id.upper(), "format": actual, "text": text, "bytes": len(text)}


def _first(seq: list[Any]) -> Any:
    return seq[0] if seq else None


def _compact(payload: dict) -> str:
    import json

    return json.dumps(payload, separators=(",", ":"))
