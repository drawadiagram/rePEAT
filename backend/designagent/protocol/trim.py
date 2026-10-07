"""Signal-peptide stripping, renumbering, and the offset shift that follows.

The protocol's first hard rule: **never include a signal peptide.** Strip the SP
residues, renumber the remaining chain from 1, and shift *everything* by
`offset = SP end` -- the domain string, the catalytic residues from the paper or
from UniProt, and every fixed position derived from them.

This is the most consequential arithmetic in the campaign and its failure mode
is silent. Get the offset wrong and ProteinMPNN is handed a fixed-position set
that protects the wrong residues, so it redesigns the real active site; the run
succeeds, the scores look ordinary, and nothing in the output says the catalytic
machinery was rewritten. So every number here is checked against the structure's
own residue types rather than trusted: `verify_residues` is the guard, and
`TrimResult.mapping` is what the lab notebook records (the skill's example:
"SM0524: offset 31, Tyr353 -> Tyr322").

Deliberately free of Bio.PDB. `tools/scoring.py` parses structures properly and
is the right place for geometry, but renumbering is column arithmetic on ATOM
records, and a round trip through a parser would reformat every line of a file
the far end then reads.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, NamedTuple

from ..tools.scoring import THREE_TO_ONE, plddt_from_pdb
from .inputs import (
    SIGNAL_PEPTIDE_LABEL,
    DomainSegment,
    InvalidInput,
    ProtocolInputs,
    chain_length,
    validate_cat_res,
)

# PDB fixed-column positions, since this module works on the columns directly.
# A PDB line is a record format, not a delimited one: `line.split()` fails on a
# four-character residue name touching the chain id, which is why the ranges are
# spelled out rather than inferred.
_RESNAME = slice(17, 20)
_CHAIN = 21
_RESSEQ = slice(22, 26)
_ICODE = 26
_SERIAL = slice(6, 11)


class ResidueShift(NamedTuple):
    """One residue's position before and after trimming, with its identity."""

    untrimmed: int
    trimmed: int
    residue: str  # one-letter code read from the structure

    def __str__(self) -> str:
        return f"{self.residue}{self.untrimmed} -> {self.residue}{self.trimmed}"


@dataclass(frozen=True)
class TrimResult:
    """A trimmed structure and everything that had to move with it."""

    pdb: str
    sequence: str
    offset: int
    inputs: ProtocolInputs
    mapping: tuple[ResidueShift, ...]
    plddt: dict[str, float] | None
    chain: str
    n_residues: int

    @property
    def trimmed(self) -> bool:
        return self.offset > 0

    @property
    def stem(self) -> str:
        """The filename stem to use from here on.

        The skill names the trimmed files `<UNIPROT>_noSP`, and says to use that
        as `MODEL` from then on; with no signal peptide the AFDB model stem is
        still the right name, and renaming it would make the notebook claim a
        trim that never happened.
        """
        return f"{self.inputs.uniprot}_noSP" if self.trimmed else self.inputs.model


def residues_in_chain(pdb_text: str, chain: str = "A") -> dict[int, str]:
    """Map residue number to one-letter code for one chain, from ATOM records.

    Keyed on the residue sequence number, so a structure carrying insertion
    codes is refused rather than silently collapsed -- renumbering `52` and
    `52A` to consecutive integers is a choice this module has no basis to make.
    """
    residues: dict[int, str] = {}
    seen_icodes: set[str] = set()
    for line in pdb_text.splitlines():
        if not line.startswith("ATOM") or len(line) < 27:
            continue
        if chain and line[_CHAIN] != chain:
            continue
        icode = line[_ICODE].strip()
        if icode:
            seen_icodes.add(icode)
            continue
        try:
            number = int(line[_RESSEQ])
        except ValueError:
            continue
        name = line[_RESNAME].strip().upper()
        residues.setdefault(number, THREE_TO_ONE.get(name, "X"))
    if seen_icodes:
        raise InvalidInput(
            f"chain {chain} carries insertion codes "
            f"({', '.join(sorted(seen_icodes))}); renumbering from 1 would be "
            f"ambiguous, so trim this structure by hand"
        )
    if not residues:
        raise InvalidInput(f"no ATOM records for chain {chain!r} in the structure")
    return residues


def sequence_of(residues: Mapping[int, str]) -> str:
    """The one-letter sequence, in residue-number order."""
    return "".join(residues[number] for number in sorted(residues))


def shift_segments(
    segments: Iterable[DomainSegment], offset: int
) -> tuple[DomainSegment, ...]:
    """Drop the SP segment and subtract the offset from every bound.

    A segment that ends at or before the offset is dropped entirely: it is part
    of what was cut. A segment straddling the offset is clipped to start at 1.
    """
    shifted: list[DomainSegment] = []
    for segment in segments:
        if segment.label == SIGNAL_PEPTIDE_LABEL:
            continue
        if segment.end <= offset:
            continue
        shifted.append(
            DomainSegment(max(1, segment.start - offset), segment.label, segment.end - offset)
        )
    if not shifted:
        raise InvalidInput(
            f"an offset of {offset} leaves no domain segments; the signal "
            f"peptide cannot be the whole chain"
        )
    return tuple(shifted)


def renumber_pdb(pdb_text: str, offset: int, chain: str = "A") -> str:
    """Drop residues 1..offset of `chain` and renumber the rest from 1.

    Only the chain being trimmed is touched; anything else is passed through, so
    a heteroatom or a second chain keeps whatever numbering it had rather than
    being silently shifted into the first chain's space.
    """
    if offset <= 0:
        return pdb_text

    out: list[str] = []
    serial = 0
    for line in pdb_text.splitlines():
        if line.startswith(("ATOM", "HETATM")) and len(line) >= 27:
            if chain and line[_CHAIN] != chain:
                out.append(line)
                continue
            try:
                number = int(line[_RESSEQ])
            except ValueError:
                out.append(line)
                continue
            if number <= offset:
                continue
            serial += 1
            line = (
                f"{line[:_SERIAL.start]}{serial:>5}{line[_SERIAL.stop:_RESSEQ.start]}"
                f"{number - offset:>4}{line[_RESSEQ.stop:]}"
            )
            out.append(line)
        elif line.startswith(("TER", "END")):
            out.append(line)
    if not out or not out[-1].startswith("END"):
        out.append("END")
    return "\n".join(out) + "\n"


def verify_residues(residues: Mapping[int, str], expected: Mapping[int, str]) -> list[str]:
    """Positions whose residue type is not what the caller expected.

    `expected` is one-letter codes keyed by position in the *trimmed* numbering,
    typically read off a paper ("His310, Tyr364"). A mismatch means the offset is
    wrong or the paper used a different numbering scheme, and either way the
    campaign must stop: see the module docstring.
    """
    problems: list[str] = []
    for position, want in sorted(expected.items()):
        got = residues.get(position)
        if got is None:
            problems.append(f"{want}{position}: no such residue in the trimmed chain")
        elif got != want.upper():
            problems.append(f"{want}{position}: the structure has {got}{position}")
    return problems


def trim_signal_peptide(
    pdb_text: str,
    inputs: ProtocolInputs,
    *,
    chain: str = "A",
    cat_res_untrimmed: Iterable[int] = (),
    expect: Mapping[int, str] | None = None,
) -> TrimResult:
    """Strip the signal peptide and shift everything that depended on it.

    `inputs` carries the **untrimmed** domain string, as the user gave it.
    `cat_res_untrimmed` is the catalytic residue list in the paper's or
    UniProt's numbering; it is shifted here, and `inputs.cat_res` is replaced
    with the result. `expect` is an optional one-letter check keyed by *trimmed*
    position, for the residues a paper names.

    Returns a `TrimResult` whose `inputs` are the ones every later stage uses.
    Nothing downstream should ever see the untrimmed numbers again -- that is
    the whole point of doing this once, here.
    """
    signal = [s for s in inputs.segments if s.label == SIGNAL_PEPTIDE_LABEL]
    if len(signal) > 1:
        raise InvalidInput(
            f"the domain string names {len(signal)} {SIGNAL_PEPTIDE_LABEL} "
            f"segments; there can be at most one signal peptide"
        )
    offset = signal[0].end if signal else 0
    if signal and signal[0].start != 1:
        raise InvalidInput(
            f"the {SIGNAL_PEPTIDE_LABEL} segment starts at residue "
            f"{signal[0].start}, not 1; a signal peptide is the chain's N-terminus"
        )

    before = residues_in_chain(pdb_text, chain)
    numbers = sorted(before)
    if numbers[0] != 1:
        raise InvalidInput(
            f"chain {chain} starts at residue {numbers[0]}, not 1; the protocol "
            f"needs 1-based ATOM records before it can shift anything"
        )
    if numbers[-1] != len(numbers):
        raise InvalidInput(
            f"chain {chain} is not contiguous: {len(numbers)} residues ending at "
            f"{numbers[-1]}. A gap would make every shifted position wrong"
        )
    if numbers[-1] != inputs.chain_length:
        raise InvalidInput(
            f"the structure has {numbers[-1]} residues but the domain string "
            f"covers {inputs.chain_length}; one of the two is for a different "
            f"construct"
        )

    trimmed_pdb = renumber_pdb(pdb_text, offset, chain)
    after = residues_in_chain(trimmed_pdb, chain) if offset else before

    segments = shift_segments(inputs.segments, offset)
    expected_length = chain_length(segments)
    if len(after) != expected_length:
        raise InvalidInput(
            f"trimming left {len(after)} residues but the shifted domain string "
            f"covers {expected_length}"
        )

    shifted = validate_cat_res(
        [position - offset for position in cat_res_untrimmed] if cat_res_untrimmed
        else inputs.cat_res,
        length=expected_length,
    ) if (cat_res_untrimmed or inputs.cat_res) else ()

    mapping: list[ResidueShift] = []
    for position in shifted:
        residue = after.get(position, "X")
        mapping.append(ResidueShift(position + offset, position, residue))

    # The skill's own check: the residue type at each shifted position must be
    # what it was before the shift. True by construction, which is exactly why
    # it is worth asserting -- it is an off-by-one detector for `renumber_pdb`.
    for shift in mapping:
        was = before.get(shift.untrimmed)
        if was is not None and was != shift.residue:
            raise InvalidInput(
                f"residue {shift.untrimmed} was {was} before trimming and "
                f"{shift.residue} at {shift.trimmed} after; the renumbering is "
                f"off by at least one"
            )

    if expect:
        problems = verify_residues(after, expect)
        if problems:
            raise InvalidInput(
                "the trimmed structure does not match the expected residues, so "
                "the offset or the paper's numbering scheme is wrong: "
                + "; ".join(problems)
            )

    return TrimResult(
        pdb=trimmed_pdb,
        sequence=sequence_of(after),
        offset=offset,
        inputs=replace(inputs, segments=segments, cat_res=shifted, offset=offset),
        mapping=tuple(mapping),
        plddt=plddt_from_pdb(trimmed_pdb),
        chain=chain,
        n_residues=len(after),
    )
