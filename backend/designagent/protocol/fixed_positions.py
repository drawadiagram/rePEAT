"""The ProteinMPNN fixed-position set: what a redesign is not allowed to touch.

This is `scripts/amarel/conservation/add_fixed_positions.py` as a function. It
runs here rather than on the cluster for three reasons: it is pure python with
no site dependencies, it is the one piece of the protocol whose output decides
which residues survive a redesign, and as a function it can be unit-tested --
which as a script invoked over ssh it never was.

The set is a union of three things:

  * **the conservation step's own output**, the positions HHblits found conserved
    at a given level, read from `*_cpos_{30,50,70}.jsonl`. The 10 A catalytic
    shell is already unioned in there by `hhblits_search.py`;
  * **everything outside a `CD` segment**, because a stabilisation campaign
    redesigns the catalytic domains and leaves the rest of the chain alone;
  * **the two residues at each chain terminus**, which MPNN otherwise frays.

Writing it here also closes backlog **A5**: `--fixed_positions_jsonl` takes a
file, and the in-band staging added for C6 can carry one, so the flag that was
removed for being wrong can come back correct.

Every number is in the **trimmed** chain's numbering. See `protocol/trim.py` for
why nothing downstream should ever see the untrimmed ones.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from .inputs import CATALYTIC_LABEL, DomainSegment, InvalidInput, chain_length

# How many residues at each end of the chain are pinned. The skill's script
# fixes the first two and the last two.
TERMINUS_WIDTH = 2


def non_catalytic_residues(
    segments: Iterable[DomainSegment], *, catalytic_label: str = CATALYTIC_LABEL
) -> set[int]:
    """Every residue a stabilisation campaign leaves alone.

    The complement of the `CD` segments, which is why `validate_domains` refuses
    a domain string with a gap: a gap would be in neither set, so MPNN would
    rewrite it and nothing would say which residues those were.
    """
    fixed: set[int] = set()
    for segment in segments:
        if segment.label != catalytic_label:
            fixed.update(segment.residues())
    return fixed


def terminal_residues(length: int, width: int = TERMINUS_WIDTH) -> set[int]:
    """The first and last `width` residues of a chain of `length`."""
    if length <= 0:
        raise InvalidInput(f"a chain length of {length} has no termini")
    head = range(1, min(width, length) + 1)
    tail = range(max(1, length - width + 1), length + 1)
    return set(head) | set(tail)


def fixed_positions(
    segments: Iterable[DomainSegment],
    conserved: Iterable[int] = (),
    *,
    catalytic_label: str = CATALYTIC_LABEL,
    terminus_width: int = TERMINUS_WIDTH,
) -> tuple[int, ...]:
    """The whole fixed set for one conservation level, sorted and deduped."""
    segments = tuple(segments)
    length = chain_length(segments)
    positions = non_catalytic_residues(segments, catalytic_label=catalytic_label)
    positions |= terminal_residues(length, terminus_width)
    for position in conserved:
        if position < 1 or position > length:
            raise InvalidInput(
                f"conserved position {position} is outside the trimmed chain "
                f"(1-{length}); the conservation run and the domain string "
                f"disagree about which construct this is"
            )
        positions.add(int(position))
    return tuple(sorted(positions))


def designable_positions(
    segments: Iterable[DomainSegment],
    conserved: Iterable[int] = (),
    **kwargs: Any,
) -> tuple[int, ...]:
    """The complement: what MPNN may actually change.

    Not used to build the job, but the number the user is told -- "N of M
    positions are designable" is the one figure that makes a conservation level
    mean something before the round runs.
    """
    segments = tuple(segments)
    fixed = set(fixed_positions(segments, conserved, **kwargs))
    return tuple(p for p in range(1, chain_length(segments) + 1) if p not in fixed)


def parse_fixed_positions_jsonl(text: str, chain: str = "A") -> dict[str, list[int]]:
    """Read a ProteinMPNN fixed-positions JSONL into `{protein_id: positions}`.

    The format is one JSON object per line, `{protein_id: {chain: [positions]}}`.
    `hhblits_search.py` writes the protein id as a path, so the stem is taken the
    way the skill's script does it -- otherwise the id would not match the PDB
    name ProteinMPNN derives from `--pdb_path`.

    A record whose chain entry is the string `"-"` means "nothing conserved";
    `hhblits_search.py` emits that rather than an empty list.
    """
    out: dict[str, list[int]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InvalidInput(
                f"fixed-positions JSONL line {number} is not JSON: {exc.msg}"
            ) from None
        if not isinstance(record, Mapping):
            raise InvalidInput(
                f"fixed-positions JSONL line {number} is not an object"
            )
        for protein_id, chains in record.items():
            if not isinstance(chains, Mapping):
                raise InvalidInput(
                    f"fixed-positions JSONL line {number} maps {protein_id!r} to "
                    f"something other than a chain table"
                )
            positions = chains.get(chain)
            if positions is None or positions == "-":
                positions = []
            if not isinstance(positions, list):
                raise InvalidInput(
                    f"fixed-positions JSONL line {number}: chain {chain} of "
                    f"{protein_id!r} is not a list of positions"
                )
            out[str(protein_id).rsplit("/", 1)[-1]] = [int(p) for p in positions]
    if not out:
        raise InvalidInput("the fixed-positions JSONL is empty")
    return out


def render_fixed_positions_jsonl(
    positions_by_protein: Mapping[str, Iterable[int]], chain: str = "A"
) -> str:
    """Write the JSONL ProteinMPNN's `--fixed_positions_jsonl` expects.

    One line per protein, keys stripped of any path, which is what
    `add_fixed_positions.py` does and what makes the id match the name MPNN
    derives from the PDB file.
    """
    lines = []
    for protein_id, positions in positions_by_protein.items():
        stem = str(protein_id).rsplit("/", 1)[-1]
        lines.append(json.dumps({stem: {chain: sorted(set(int(p) for p in positions))}}))
    if not lines:
        raise InvalidInput("no proteins to write fixed positions for")
    return "\n".join(lines) + "\n"


def add_fixed_positions(
    jsonl_text: str,
    segments: Iterable[DomainSegment],
    *,
    chain: str = "A",
    catalytic_label: str = CATALYTIC_LABEL,
    terminus_width: int = TERMINUS_WIDTH,
) -> str:
    """`add_fixed_positions.py`, end to end: conservation JSONL in, MPNN JSONL out.

    The conservation step's own positions are unioned with everything outside a
    `CD` segment and with the chain termini, exactly as the skill's script does,
    so the output can be dropped in where the script's was.
    """
    segments = tuple(segments)
    conserved_by_protein = parse_fixed_positions_jsonl(jsonl_text, chain)
    return render_fixed_positions_jsonl(
        {
            protein_id: fixed_positions(
                segments,
                conserved,
                catalytic_label=catalytic_label,
                terminus_width=terminus_width,
            )
            for protein_id, conserved in conserved_by_protein.items()
        },
        chain,
    )
