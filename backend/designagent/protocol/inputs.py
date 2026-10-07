"""Validation for every protocol input that reaches a job spec.

This is a security boundary, and the only one on this path. `NAME`, `UNIPROT`,
`DOMAINS`, `CAT_RES` and the netid arrive in a **chat message** and end up
inside a `bash -lc` script body, in argv, and in `$PROJ` paths the endpoint
writes to under the site's allocation. `config.py` refuses `mpnn_command` and
`mpnn_prologue` over the settings API for exactly that reason -- they name shell
the server runs -- and the chat route has no auth story at all, so guarding the
settings and not the inputs would be the wrong half of the job.

**Reject, do not sanitize.** Stripping dangerous characters silently turns a
typo into a different target and a hostile string into a plausible one; a named
refusal is cheaper to read and impossible to get wrong. Case folding and
whitespace trimming are the only normalisations here, and both happen inside
charsets that are already constrained.

Nothing in this module quotes anything. Quoting is the spec builders' job
(`protocol/specs.py`), and it is belt-and-braces: these validators are the
reason there is nothing to quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, NamedTuple

# Every pattern is anchored and bounded. An unbounded one is a denial-of-service
# waiting for a pathological input, and a length the far end will reject is
# better refused here with a reason.
NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,32}\Z")
UNIPROT_RE = re.compile(r"\A[A-Z0-9]{6,10}\Z")
NETID_RE = re.compile(r"\A[A-Za-z0-9]{2,16}\Z")
LABEL_RE = re.compile(r"\A[A-Za-z0-9]{1,8}\Z")
PDB_ID_RE = re.compile(r"\A[0-9][A-Za-z0-9]{3}\Z")

# The conservation method the user picks at intake, and the filename infix it
# selects. `hhblits_search.py` writes both sets; the choice only decides which
# one feeds MPNN. The skill forbids a default, so there is no entry for "unset".
METHOD_TAGS = {"cpos": "", "conservation_liu": "liu_"}

# The three conservation levels the protocol runs. Not a range: these are the
# filenames `hhblits_search.py` emits.
LEVELS = (30, 50, 70)

# The domain-string label marking a catalytic domain -- the segments MPNN is
# allowed to redesign -- and the one marking a signal peptide, which is always
# stripped (see `protocol/trim.py`).
CATALYTIC_LABEL = "CD"
SIGNAL_PEPTIDE_LABEL = "SP"


class InvalidInput(ValueError):
    """A protocol input was refused.

    Its own type so the stage machine can turn it into a named refusal for the
    user rather than a traceback, and so a bare `except ValueError` around
    unrelated parsing cannot swallow it.
    """


class DomainSegment(NamedTuple):
    """One `start-label-end` triple of a domain string, inclusive at both ends."""

    start: int
    label: str
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def residues(self) -> range:
        return range(self.start, self.end + 1)


def validate_name(value: str) -> str:
    """The campaign name. It becomes a directory component and a filename stem."""
    text = (value or "").strip()
    if not NAME_RE.match(text):
        raise InvalidInput(
            f"name {value!r} is not usable: it becomes a directory name on the "
            f"cluster, so it must be 1-32 characters of letters, digits, "
            f"underscore or hyphen"
        )
    return text


def validate_uniprot(value: str) -> str:
    """A UniProt accession. Canonically upper case, so fold rather than refuse."""
    text = (value or "").strip().upper()
    if not UNIPROT_RE.match(text):
        raise InvalidInput(
            f"UniProt accession {value!r} is not usable: expected 6-10 letters "
            f"and digits, e.g. A0A173MSR7"
        )
    return text


def validate_netid(value: str) -> str:
    """The cluster account name. It becomes a `/scratch/<netid>` path component."""
    text = (value or "").strip()
    if not NETID_RE.match(text):
        raise InvalidInput(
            f"netid {value!r} is not usable: it becomes a path component under "
            f"/scratch, so it must be 2-16 letters and digits"
        )
    return text


def validate_pdb_id(value: str) -> str:
    """A four-character PDB entry id, as the optional `STRUCTURE` input."""
    text = (value or "").strip().upper()
    if not PDB_ID_RE.match(text):
        raise InvalidInput(
            f"PDB id {value!r} is not usable: expected four characters starting "
            f"with a digit, e.g. 4Q8L"
        )
    return text


def validate_domains(value: str) -> tuple[DomainSegment, ...]:
    """Parse and check a domain string.

    The format is dash-separated `start-label-end` triples which the skill
    requires to be contiguous and to cover the whole chain, e.g.
    `1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785`.

    Contiguity is checked, not assumed. `add_fixed_positions.py` derives the
    fixed set as "every residue outside a CD segment", so a gap in the string is
    a run of residues that is neither fixed nor designed -- which does not fail,
    it just quietly lets MPNN rewrite a region nobody chose. The same string
    also carries the chain length, which Step 1 checks the structure against.
    """
    text = (value or "").strip()
    if not text:
        raise InvalidInput("no domain string was given")

    tokens = text.split("-")
    if len(tokens) % 3 != 0:
        raise InvalidInput(
            f"domain string does not split into start-label-end triples "
            f"({len(tokens)} dash-separated tokens): {text!r}"
        )

    segments: list[DomainSegment] = []
    for index in range(0, len(tokens), 3):
        raw_start, label, raw_end = tokens[index : index + 3]
        if not LABEL_RE.match(label):
            raise InvalidInput(
                f"domain label {label!r} is not usable: expected 1-8 letters or "
                f"digits, e.g. CD, IDR, FN3"
            )
        try:
            start, end = int(raw_start), int(raw_end)
        except ValueError:
            raise InvalidInput(
                f"domain segment {raw_start!r}-{label}-{raw_end!r} does not have "
                f"integer bounds"
            ) from None
        if start < 1:
            raise InvalidInput(
                f"domain segment {start}-{label}-{end} starts below residue 1; "
                f"the chain is numbered from 1"
            )
        if end < start:
            raise InvalidInput(f"domain segment {start}-{label}-{end} ends before it starts")
        segments.append(DomainSegment(start, label, end))

    if segments[0].start != 1:
        raise InvalidInput(
            f"domain string starts at residue {segments[0].start}, not 1; it has "
            f"to cover the whole chain"
        )
    for previous, current in zip(segments, segments[1:]):
        if current.start != previous.end + 1:
            gap = "overlaps" if current.start <= previous.end else "leaves a gap after"
            raise InvalidInput(
                f"domain string {gap} residue {previous.end}: "
                f"{previous.start}-{previous.label}-{previous.end} is followed by "
                f"{current.start}-{current.label}-{current.end}, and the segments "
                f"must be contiguous"
            )

    if not any(segment.label == CATALYTIC_LABEL for segment in segments):
        raise InvalidInput(
            f"domain string names no {CATALYTIC_LABEL} segment, so there is "
            f"nothing for ProteinMPNN to redesign -- every position would be fixed"
        )
    return tuple(segments)


def format_domains(segments: Iterable[DomainSegment]) -> str:
    """Re-serialise segments to the string form the skill's scripts take."""
    return "-".join(f"{s.start}-{s.label}-{s.end}" for s in segments)


def chain_length(segments: Iterable[DomainSegment]) -> int:
    """The last residue the domain string covers."""
    return max(segment.end for segment in segments)


def validate_cat_res(value: str | Iterable[int], *, length: int | None = None) -> tuple[int, ...]:
    """Catalytic residue positions, in the trimmed chain's own numbering.

    `length` is the chain length from the domain string; when given, a position
    outside it is refused. That is the check that catches a residue list still
    in precursor numbering after the signal peptide was stripped -- the single
    most consequential mistake in the protocol, because MPNN would then be told
    to protect the wrong positions and redesign the real active site.
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise InvalidInput("no catalytic residues were given")
        parts = [part.strip() for part in text.split(",")]
        if any(not part for part in parts):
            raise InvalidInput(f"catalytic residue list {value!r} has an empty entry")
        try:
            positions = [int(part) for part in parts]
        except ValueError:
            raise InvalidInput(
                f"catalytic residue list {value!r} is not a comma-separated list "
                f"of residue numbers, e.g. 310,364"
            ) from None
    else:
        # Coerced, not assumed: the caller may be handing over a regex's
        # findall, which yields strings, and comparing those to 1 raises a
        # TypeError far from the mistake.
        try:
            positions = [int(part) for part in value]
        except (TypeError, ValueError):
            raise InvalidInput(
                f"catalytic residues {value!r} are not all residue numbers"
            ) from None

    if not positions:
        raise InvalidInput("no catalytic residues were given")
    for position in positions:
        if position < 1:
            raise InvalidInput(f"catalytic residue {position} is below residue 1")
        if length is not None and position > length:
            raise InvalidInput(
                f"catalytic residue {position} is past the end of the chain "
                f"({length} residues) -- is the list still in untrimmed numbering?"
            )
    return tuple(sorted(set(positions)))


def tag_for_method(value: str) -> str:
    """The filename infix for a conservation method: `""` or `"liu_"`.

    The skill requires the user to pick and forbids a default, so an unrecognised
    answer is refused rather than guessed.
    """
    text = (value or "").strip().lower()
    if text not in METHOD_TAGS:
        raise InvalidInput(
            f"conservation method {value!r} is not one of "
            f"{', '.join(sorted(METHOD_TAGS))}; the protocol has no default here"
        )
    return METHOD_TAGS[text]


def validate_levels(values: Iterable[int | str]) -> tuple[int, ...]:
    """Conservation levels to run, a subset of 30/50/70 in ascending order."""
    chosen: list[int] = []
    for value in values:
        try:
            level = int(value)
        except (TypeError, ValueError):
            raise InvalidInput(f"conservation level {value!r} is not a number") from None
        if level not in LEVELS:
            raise InvalidInput(
                f"conservation level {level} is not one of "
                f"{', '.join(str(lv) for lv in LEVELS)}"
            )
        chosen.append(level)
    if not chosen:
        raise InvalidInput("no conservation levels were chosen")
    return tuple(sorted(set(chosen)))


@dataclass(frozen=True)
class ProtocolInputs:
    """Every validated value the protocol's job specs are built from.

    Frozen, and the only thing the builders accept: a builder that took raw
    strings would make the validation optional, and optional validation on this
    path is no validation.
    """

    name: str
    uniprot: str
    netid: str
    segments: tuple[DomainSegment, ...]
    cat_res: tuple[int, ...]
    tag: str
    levels: tuple[int, ...] = LEVELS
    structure_pdb_id: str = ""
    # Set by `protocol/trim.py` once the signal peptide is known; 0 means the
    # chain was already mature. Kept here so every downstream number is
    # unambiguous about which numbering it is in.
    offset: int = 0
    papers: tuple[str, ...] = field(default_factory=tuple)

    @property
    def domains(self) -> str:
        """The domain string, as the skill's scripts take it."""
        return format_domains(self.segments)

    @property
    def chain_length(self) -> int:
        return chain_length(self.segments)

    @property
    def catalytic_segments(self) -> tuple[DomainSegment, ...]:
        return tuple(s for s in self.segments if s.label == CATALYTIC_LABEL)

    @property
    def model(self) -> str:
        """The AFDB model stem, which names almost every file in the campaign."""
        return f"AF-{self.uniprot}-F1-model_v6"

    @classmethod
    def parse(
        cls,
        *,
        name: str,
        uniprot: str,
        domains: str,
        netid: str,
        method: str,
        cat_res: str | Iterable[int] = (),
        levels: Iterable[int | str] = LEVELS,
        structure_pdb_id: str = "",
        papers: Iterable[str] = (),
    ) -> "ProtocolInputs":
        """Validate everything at once, raising `InvalidInput` on the first problem.

        `cat_res` is optional because it is not known until the Step 5
        checkpoint; everything else is required at intake.
        """
        segments = validate_domains(domains)
        return cls(
            name=validate_name(name),
            uniprot=validate_uniprot(uniprot),
            netid=validate_netid(netid),
            segments=segments,
            cat_res=(
                validate_cat_res(cat_res, length=chain_length(segments)) if cat_res else ()
            ),
            tag=tag_for_method(method),
            levels=validate_levels(levels),
            structure_pdb_id=validate_pdb_id(structure_pdb_id) if structure_pdb_id else "",
            papers=tuple(papers),
        )

    def with_cat_res(self, value: str | Iterable[int]) -> "ProtocolInputs":
        """A copy carrying the residues confirmed at the Step 5 checkpoint."""
        from dataclasses import replace

        return replace(self, cat_res=validate_cat_res(value, length=self.chain_length))
