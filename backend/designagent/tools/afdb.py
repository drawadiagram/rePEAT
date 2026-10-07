"""AlphaFold DB: the protocol's structure source of first resort.

Separate from `tools/pdb.py`, which reads RCSB. The protocol's Step 1 says AFDB
first, a user-named crystal structure only if it covers the whole mature chain,
and an AlphaFold3 prediction only if there is no AFDB entry at all -- so a
redesign campaign starts from a predicted model far more often than from an
experimental one.

The version in the filename is not assumed. `AF-<acc>-F1-model_v6` is the
current naming and the skill's derived `MODEL`, but a stale guess 404s, so a miss
falls through to the API, which names the files it actually has.
"""

from __future__ import annotations

import logging
from typing import Any

from .http import get_json, get_text

log = logging.getLogger(__name__)

FILES_URL = "https://alphafold.ebi.ac.uk/files"
PREDICTION_URL = "https://alphafold.ebi.ac.uk/api/prediction"

#: What the skill derives `MODEL` as. Tried first, so the common case is one
#: request rather than two.
DEFAULT_VERSION = 6


def model_stem(uniprot: str, version: int = DEFAULT_VERSION) -> str:
    return f"AF-{uniprot.upper()}-F1-model_v{version}"


async def afdb_structure(
    uniprot_id: str = "",
    version: int = DEFAULT_VERSION,
    fmt: str = "pdb",
    **_: Any,
) -> dict[str, Any]:
    """Download one AFDB model, resolving the file version if the guess misses.

    Returns `{stem, text, format, version, source, url}`, or `{error}`. The stem
    is what every later file in the campaign is named after, so it is reported
    rather than recomputed by the caller.
    """
    accession = (uniprot_id or "").strip().upper()
    if not accession:
        return {"error": "no UniProt accession to look up"}
    suffix = "cif" if fmt in ("cif", "mmcif") else "pdb"

    stem = model_stem(accession, version)
    url = f"{FILES_URL}/{stem}.{suffix}"
    text = await get_text(url)
    if text and text.lstrip().startswith(("ATOM", "HEADER", "data_", "#")):
        return {
            "stem": stem,
            "text": text,
            "format": suffix,
            "version": version,
            "source": "alphafold-db",
            "url": url,
        }

    # The guessed version is wrong, or there is no entry. Ask what exists rather
    # than walking version numbers downwards.
    payload = await get_json(f"{PREDICTION_URL}/{accession}")
    entry = payload[0] if isinstance(payload, list) and payload else None
    if not isinstance(entry, dict):
        return {
            "error": (
                f"no AlphaFold DB entry for {accession}; the protocol's fallback "
                f"is an AlphaFold3 prediction of its own"
            )
        }

    key = "cifUrl" if suffix == "cif" else "pdbUrl"
    resolved = entry.get(key) or ""
    if not resolved:
        return {"error": f"AlphaFold DB has no {suffix} file for {accession}"}
    text = await get_text(resolved)
    if not text:
        return {"error": f"AlphaFold DB file could not be downloaded: {resolved}"}

    name = resolved.rsplit("/", 1)[-1]
    return {
        "stem": name.rsplit(".", 1)[0],
        "text": text,
        "format": suffix,
        # Read off the filename the API gave, never asserted: a campaign's
        # notebook has to record which model version it actually used.
        "version": _version_of(name),
        "source": "alphafold-db",
        "url": resolved,
        "entry_version": entry.get("latestVersion") or entry.get("modelCreatedDate", ""),
    }


def _version_of(filename: str) -> int | str:
    marker = "model_v"
    if marker in filename:
        tail = filename.split(marker, 1)[1].split(".", 1)[0]
        if tail.isdigit():
            return int(tail)
    return ""
