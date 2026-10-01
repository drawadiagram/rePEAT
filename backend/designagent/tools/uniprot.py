"""UniProt lookups: entry, sequence, functional features, PDB cross-references."""

from __future__ import annotations

import logging
import re
from typing import Any

from .http import get_json

log = logging.getLogger(__name__)

REST = "https://rest.uniprot.org/uniprotkb"

# Accessions look like P0CG48 / A0A023GPI8. Require word boundaries to avoid
# matching inside longer tokens.
ACCESSION_RE = re.compile(
    r"\b([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]([A-Z][A-Z0-9]{2}[0-9]){1,2})\b"
)

# Feature types worth showing a designer.
INTERESTING_FEATURES = {
    "Active site",
    "Binding site",
    "Site",
    "Disulfide bond",
    "Metal binding",
    "Modified residue",
    "Mutagenesis",
    "Domain",
    "Region",
    "Motif",
}


def find_accessions(text: str) -> list[str]:
    out, seen = [], set()
    for match in ACCESSION_RE.finditer(text.upper()):
        acc = match.group(1)
        if acc not in seen:
            seen.add(acc)
            out.append(acc)
    return out


async def search_uniprot(query: str, limit: int = 3) -> list[str]:
    data = await get_json(
        f"{REST}/search",
        params={"query": query, "format": "json", "size": int(limit), "fields": "accession"},
    )
    if not data:
        return []
    return [r["primaryAccession"] for r in data.get("results", []) if r.get("primaryAccession")]


async def fetch_entry(accession: str) -> dict[str, Any]:
    data = await get_json(f"{REST}/{accession.upper()}.json")
    if not data:
        return {}

    desc = data.get("proteinDescription") or {}
    rec = desc.get("recommendedName") or {}
    name = (rec.get("fullName") or {}).get("value", "")
    if not name:
        submitted = desc.get("submittedName") or []
        if submitted:
            name = (submitted[0].get("fullName") or {}).get("value", "")

    sequence = (data.get("sequence") or {}).get("value", "")
    organism = (data.get("organism") or {}).get("scientificName", "")
    genes = [
        (g.get("geneName") or {}).get("value", "")
        for g in data.get("genes", []) or []
        if (g.get("geneName") or {}).get("value")
    ]

    features = []
    for feat in data.get("features", []) or []:
        ftype = feat.get("type", "")
        if ftype not in INTERESTING_FEATURES:
            continue
        loc = feat.get("location") or {}
        start = ((loc.get("start") or {}).get("value"))
        end = ((loc.get("end") or {}).get("value"))
        features.append(
            {
                "type": ftype,
                "start": start,
                "end": end,
                "description": feat.get("description", ""),
            }
        )

    pdb_ids = [
        xref.get("id", "")
        for xref in data.get("uniProtKBCrossReferences", []) or []
        if xref.get("database") == "PDB" and xref.get("id")
    ]

    function_text = ""
    for comment in data.get("comments", []) or []:
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts") or []
            if texts:
                function_text = texts[0].get("value", "")
                break

    return {
        "uniprot_id": data.get("primaryAccession", accession.upper()),
        "name": name,
        "genes": genes,
        "sequence": sequence,
        "length": len(sequence),
        "organism": organism,
        "function": function_text,
        "features": features,
        "pdb_ids": pdb_ids,
    }


# --- task body -------------------------------------------------------------


async def uniprot_lookup(
    query: str = "", uniprot_id: str = "", sequence: str = "", **_: Any
) -> dict[str, Any]:
    """Resolve a prompt, accession, or gene name to UniProt fields."""
    target = (uniprot_id or "").upper()
    if not target and query:
        found = find_accessions(query)
        target = found[0] if found else ""
    if not target and query:
        hits = await search_uniprot(query, limit=1)
        target = hits[0] if hits else ""
    if not target:
        return {"error": "no UniProt entry matched", "query": query}

    entry = await fetch_entry(target)
    if not entry:
        return {"error": f"UniProt entry {target} not found", "uniprot_id": target}
    return entry
