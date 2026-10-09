"""Signal-peptide stripping and the offset shift that follows.

The highest-value tests in the protocol. A wrong offset is silent: ProteinMPNN
gets a fixed-position set protecting the wrong residues, redesigns the real
active site, and the run looks entirely ordinary. So the refusals matter more
than the happy path, and the happy path is checked against residue *types*
rather than against positions alone.
"""

from __future__ import annotations

import pytest
from designagent.protocol.inputs import DomainSegment, InvalidInput, ProtocolInputs
from designagent.protocol.trim import (
    ResidueShift,
    renumber_pdb,
    residues_in_chain,
    shift_segments,
    trim_signal_peptide,
    verify_residues,
)
from designagent.tools.scoring import THREE_TO_ONE

ONE_TO_THREE = {one: three for three, one in THREE_TO_ONE.items()}

# The SM0524 case the skill records: offset 31, Tyr353 -> Tyr322.
SM_DOMAINS = "1-SP-31-32-L-50-51-CD-400"
SM_LENGTH = 400
SM_OFFSET = 31
SM_CATALYTIC_UNTRIMMED = 353


def pdb_from_sequence(sequence: str, chain: str = "A", plddt: float = 85.0) -> str:
    """A CA-only PDB in the real fixed-column format, numbered from 1.

    Columns, not fields: `trim.py` works on the record positions directly, so a
    fixture that only looks right under `split()` would prove nothing.
    """
    lines = []
    for index, letter in enumerate(sequence, start=1):
        name = ONE_TO_THREE.get(letter, "ALA")
        lines.append(
            f"ATOM  {index:5d}  CA  {name} {chain}{index:4d}    "
            f"{float(index):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{plddt:6.2f}           C"
        )
    return "\n".join(lines) + "\nEND\n"


def sm_sequence() -> str:
    """400 alanines with a tyrosine at the catalytic position."""
    chars = ["A"] * SM_LENGTH
    chars[SM_CATALYTIC_UNTRIMMED - 1] = "Y"
    return "".join(chars)


def sm_inputs(**overrides) -> ProtocolInputs:
    base = {
        "name": "SM0524",
        "uniprot": "A0A173MSR7",
        "domains": SM_DOMAINS,
        "netid": "abc123",
        "method": "cpos",
        "cat_res": "",
    }
    return ProtocolInputs.parse(**{**base, **overrides})


# --- the reference case ----------------------------------------------------


def test_the_skills_own_offset_example():
    # "SM0524: offset 31, Tyr353 -> Tyr322", verbatim from the skill.
    got = trim_signal_peptide(
        pdb_from_sequence(sm_sequence()),
        sm_inputs(),
        cat_res_untrimmed=[SM_CATALYTIC_UNTRIMMED],
    )
    assert got.offset == SM_OFFSET
    assert got.mapping == (ResidueShift(353, 322, "Y"),)
    assert str(got.mapping[0]) == "Y353 -> Y322"
    assert got.inputs.cat_res == (322,)


def test_the_trimmed_chain_is_renumbered_from_one_and_is_shorter_by_the_offset():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    residues = residues_in_chain(got.pdb)
    assert min(residues) == 1
    assert max(residues) == SM_LENGTH - SM_OFFSET
    assert got.n_residues == SM_LENGTH - SM_OFFSET == 369


def test_the_trimmed_sequence_is_the_tail_of_the_original():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    assert got.sequence == sm_sequence()[SM_OFFSET:]
    assert got.sequence[322 - 1] == "Y"


def test_the_domain_string_loses_the_sp_and_shifts():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    assert got.inputs.segments == (
        DomainSegment(1, "L", 19),
        DomainSegment(20, "CD", 369),
    )
    assert got.inputs.domains == "1-L-19-20-CD-369"
    assert got.inputs.chain_length == 369


def test_the_offset_is_carried_on_the_inputs_so_later_numbers_are_unambiguous():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    assert got.inputs.offset == SM_OFFSET


def test_the_trimmed_files_get_their_own_stem():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    assert got.trimmed is True
    assert got.stem == "A0A173MSR7_noSP"


def test_plddt_comes_from_the_b_factor_column():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence(), plddt=91.5), sm_inputs())
    assert got.plddt is not None
    assert got.plddt["plddt"] == 91.5
    assert got.plddt["n_residues"] == 369


def test_atom_serials_are_renumbered_contiguously():
    got = trim_signal_peptide(pdb_from_sequence(sm_sequence()), sm_inputs())
    serials = [
        int(line[6:11]) for line in got.pdb.splitlines() if line.startswith("ATOM")
    ]
    assert serials == list(range(1, 370))


# --- no signal peptide -----------------------------------------------------


def test_a_chain_with_no_signal_peptide_is_left_alone():
    inputs = ProtocolInputs.parse(
        name="AlyFRB",
        uniprot="A0A173MSR7",
        domains="1-IDR-10-11-CD-100",
        netid="abc123",
        method="cpos",
        cat_res="50",
    )
    text = pdb_from_sequence("A" * 100)
    got = trim_signal_peptide(text, inputs)
    assert got.offset == 0
    assert got.trimmed is False
    assert got.pdb == text
    assert got.inputs.cat_res == (50,)
    # Renaming the file would make the notebook claim a trim that never happened.
    assert got.stem == "AF-A0A173MSR7-F1-model_v6"


# --- the refusals ----------------------------------------------------------


def test_a_structure_whose_length_disagrees_with_the_domain_string_is_refused():
    # One of the two is for a different construct, and guessing which would
    # shift every position downstream.
    with pytest.raises(InvalidInput, match="different construct"):
        trim_signal_peptide(pdb_from_sequence("A" * 399), sm_inputs())


def test_a_chain_not_numbered_from_one_is_refused():
    text = "\n".join(
        f"ATOM  {i:5d}  CA  ALA A{i + 10:4d}    "
        f"{float(i):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{85.0:6.2f}           C"
        for i in range(1, 401)
    )
    with pytest.raises(InvalidInput, match="1-based"):
        trim_signal_peptide(text, sm_inputs())


def test_a_chain_with_a_gap_is_refused():
    lines = pdb_from_sequence(sm_sequence()).splitlines()
    missing = [ln for ln in lines if not ln.startswith("ATOM") or int(ln[22:26]) != 200]
    with pytest.raises(InvalidInput, match="not contiguous"):
        trim_signal_peptide("\n".join(missing), sm_inputs())


def test_insertion_codes_are_refused_rather_than_collapsed():
    # Renumbering 52 and 52A to consecutive integers is a choice this module
    # has no basis to make.
    line = (
        f"ATOM  {1:5d}  CA  ALA A{52:4d}A   "
        f"{1.0:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{85.0:6.2f}           C"
    )
    with pytest.raises(InvalidInput, match="insertion codes"):
        residues_in_chain(pdb_from_sequence("AAA") + line + "\n")


def test_a_structure_with_no_records_for_the_chain_is_refused():
    with pytest.raises(InvalidInput, match="no ATOM records"):
        residues_in_chain(pdb_from_sequence("AAA", chain="A"), chain="B")


def test_a_signal_peptide_away_from_the_n_terminus_is_refused():
    inputs = sm_inputs(domains="1-L-10-11-SP-40-41-CD-400")
    with pytest.raises(InvalidInput, match="N-terminus"):
        trim_signal_peptide(pdb_from_sequence(sm_sequence()), inputs)


def test_two_signal_peptides_are_refused():
    inputs = sm_inputs(domains="1-SP-10-11-SP-40-41-CD-400")
    with pytest.raises(InvalidInput, match="at most one signal peptide"):
        trim_signal_peptide(pdb_from_sequence(sm_sequence()), inputs)


def test_a_catalytic_residue_inside_the_signal_peptide_is_refused():
    # 20 - 31 is negative, so this cannot be a position in the mature chain.
    with pytest.raises(InvalidInput):
        trim_signal_peptide(
            pdb_from_sequence(sm_sequence()), sm_inputs(), cat_res_untrimmed=[20]
        )


# --- the residue-type guard ------------------------------------------------


def test_expected_residues_that_match_are_accepted():
    got = trim_signal_peptide(
        pdb_from_sequence(sm_sequence()),
        sm_inputs(),
        cat_res_untrimmed=[SM_CATALYTIC_UNTRIMMED],
        expect={322: "Y"},
    )
    assert got.inputs.cat_res == (322,)


def test_an_expected_residue_of_the_wrong_type_stops_the_campaign():
    # Either the offset is wrong or the paper used a different numbering scheme.
    # Both are reasons to stop rather than to redesign the active site.
    with pytest.raises(InvalidInput, match="paper's numbering scheme"):
        trim_signal_peptide(
            pdb_from_sequence(sm_sequence()),
            sm_inputs(),
            cat_res_untrimmed=[SM_CATALYTIC_UNTRIMMED],
            expect={322: "H"},
        )


def test_verify_residues_names_every_problem_rather_than_the_first():
    residues = {1: "A", 2: "Y", 3: "H"}
    assert verify_residues(residues, {2: "Y"}) == []
    assert verify_residues(residues, {2: "H", 3: "Y", 9: "W"}) == [
        "H2: the structure has Y2",
        "Y3: the structure has H3",
        "W9: no such residue in the trimmed chain",
    ]


# --- the pure helpers ------------------------------------------------------


def test_shift_segments_drops_what_was_cut_and_clips_a_straddling_segment():
    segments = (
        DomainSegment(1, "SP", 31),
        DomainSegment(32, "L", 50),
        DomainSegment(51, "CD", 400),
    )
    assert shift_segments(segments, 31) == (
        DomainSegment(1, "L", 19),
        DomainSegment(20, "CD", 369),
    )
    # A segment straddling the offset starts at 1 rather than at zero or below.
    straddling = (DomainSegment(1, "SP", 31), DomainSegment(20, "CD", 400))
    assert shift_segments(straddling, 31)[0] == DomainSegment(1, "CD", 369)


def test_an_offset_that_consumes_every_segment_is_refused():
    with pytest.raises(InvalidInput, match="no domain segments"):
        shift_segments((DomainSegment(1, "SP", 31), DomainSegment(32, "CD", 40)), 40)


def test_renumber_pdb_is_a_no_op_without_an_offset():
    text = pdb_from_sequence("AYH")
    assert renumber_pdb(text, 0) == text


def test_renumber_pdb_leaves_other_chains_untouched():
    # Shifting a second chain into the first one's numbering space would be a
    # silent corruption of a structure the far end then reads.
    text = pdb_from_sequence("AAAAA", chain="A") + pdb_from_sequence("YYY", chain="B")
    out = renumber_pdb(text, 2, chain="A")
    assert residues_in_chain(out, "A") == {1: "A", 2: "A", 3: "A"}
    assert residues_in_chain(out, "B") == {1: "Y", 2: "Y", 3: "Y"}
