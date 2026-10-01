"""Literature retrieval and preprocessing (Europe PMC).

Preprocessing matters as much as retrieval: raw abstracts are long and the
orchestrator only needs the sentences that mention the design target, a mutation
or a measurable property. `preprocess_abstract` extracts those, and mutation
strings are parsed out so the orchestrator can seed a worklist from prior art.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .http import get_json

log = logging.getLogger(__name__)

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# Point mutations as written in papers: A42V, Trp61Ala, p.Arg120Gly
MUTATION_RE = re.compile(
    r"\b(?:p\.)?("
    r"[ACDEFGHIKLMNPQRSTVWY]\d{1,4}[ACDEFGHIKLMNPQRSTVWY]"
    r"|(?:Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|Trp|Tyr|Val)"
    r"\d{1,4}"
    r"(?:Ala|Arg|Asn|Asp|Cys|Gln|Glu|Gly|His|Ile|Leu|Lys|Met|Phe|Pro|Ser|Thr|Trp|Tyr|Val)"
    r")\b"
)

# Phrases that signal a measured property worth optimizing.
PROPERTY_TERMS = (
    "thermostability", "thermal stability", "melting temperature", "tm ",
    "activity", "catalytic", "kcat", "km", "affinity", "kd ", "binding",
    "solubility", "aggregation", "expression", "yield", "half-life",
    "stability", "selectivity", "specificity", "folding",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")


def find_mutations(text: str) -> list[str]:
    out, seen = [], set()
    for match in MUTATION_RE.finditer(text):
        mut = match.group(1)
        if mut not in seen:
            seen.add(mut)
            out.append(mut)
    return out


def preprocess_abstract(
    abstract: str, *, target_terms: list[str] | None = None, max_sentences: int = 4
) -> dict[str, Any]:
    """Reduce an abstract to the sentences a designer would actually read."""
    if not abstract:
        return {"excerpt": "", "mutations": [], "properties": [], "n_sentences": 0}

    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(abstract.strip()) if s.strip()]
    terms = [t.lower() for t in (target_terms or []) if t]

    scored: list[tuple[int, int, str]] = []
    for i, sentence in enumerate(sentences):
        low = sentence.lower()
        score = 0
        if MUTATION_RE.search(sentence):
            score += 3
        score += sum(2 for term in PROPERTY_TERMS if term in low)
        score += sum(3 for term in terms if term in low)
        if re.search(r"\d+(\.\d+)?\s*(°c|kcal|fold|%|µm|nm|mm)", low):
            score += 2
        if score:
            scored.append((score, i, sentence))

    scored.sort(key=lambda t: (-t[0], t[1]))
    picked = sorted(scored[:max_sentences], key=lambda t: t[1])
    excerpt = " ".join(s for _, _, s in picked) or " ".join(sentences[:2])

    properties = sorted({t.strip() for t in PROPERTY_TERMS if t in abstract.lower()})
    return {
        "excerpt": excerpt,
        "mutations": find_mutations(abstract),
        "properties": properties,
        "n_sentences": len(sentences),
    }


async def search_literature(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Europe PMC search with abstracts included."""
    data = await get_json(
        SEARCH,
        params={
            "query": query,
            "format": "json",
            "resultType": "core",
            "pageSize": int(limit),
            # Default (relevance) ordering. Sorting by citation count surfaces
            # famous but off-topic papers for narrow design queries.
        },
    )
    if not data:
        return []

    out = []
    for item in (data.get("resultList") or {}).get("result", []):
        out.append(
            {
                "id": item.get("pmcid") or item.get("pmid") or item.get("id", ""),
                "source": item.get("source", ""),
                "title": item.get("title", "").strip().rstrip("."),
                "year": str(item.get("pubYear", "")),
                "doi": item.get("doi", ""),
                "journal": (item.get("journalInfo") or {}).get(
                    "journal", {}
                ).get("title", ""),
                "citations": item.get("citedByCount", 0),
                "abstract": item.get("abstractText", "") or "",
            }
        )
    return out


# --- task body -------------------------------------------------------------


async def literature_lookup(
    query: str = "",
    protein: str = "",
    goal: str = "",
    limit: int = 5,
    **_: Any,
) -> dict[str, Any]:
    """Retrieve and preprocess literature for a design target."""
    terms = [t for t in (protein, goal) if t]
    search_query = query or " AND ".join(f'"{t}"' for t in terms) or protein
    if not search_query:
        return {"error": "no literature query", "refs": []}

    hits = await search_literature(search_query, limit=limit)
    if not hits and terms:
        # Fall back to the protein alone: the combined query is often too narrow.
        hits = await search_literature(protein or terms[0], limit=limit)

    refs = []
    all_mutations: list[str] = []
    for hit in hits:
        pre = preprocess_abstract(hit.pop("abstract", ""), target_terms=terms)
        all_mutations.extend(pre["mutations"])
        refs.append(
            {
                **hit,
                "abstract": pre["excerpt"],
                "relevance": ", ".join(pre["properties"][:4]),
                "mutations": pre["mutations"],
            }
        )

    # Mutations cited by more than one paper are the interesting ones.
    counts: dict[str, int] = {}
    for mut in all_mutations:
        counts[mut] = counts.get(mut, 0) + 1
    recurring = sorted(counts, key=lambda m: -counts[m])

    return {
        "query": search_query,
        "refs": refs,
        "n_refs": len(refs),
        "mutations_mentioned": recurring[:20],
    }
