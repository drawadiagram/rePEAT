"""The ProteinMPNN fixed-position set.

This set decides which residues survive a redesign, so the tests pin the
semantics of the skill's own `add_fixed_positions.py` rather than just the shape
of the output: the union of the conservation run's positions, everything outside
a `CD` segment, and two residues at each terminus.
"""

from __future__ import annotations

import json

import pytest
from designagent.protocol.fixed_positions import (
    add_fixed_positions,
    designable_positions,
    fixed_positions,
    non_catalytic_residues,
    parse_fixed_positions_jsonl,
    render_fixed_positions_jsonl,
    terminal_residues,
)
from designagent.protocol.inputs import InvalidInput, validate_domains

ALYFRB_DOMAINS = (
    "1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785"
)
ALYFRB = validate_domains(ALYFRB_DOMAINS)
MODEL = "AF-A0A173MSR7-F1-model_v6"

# A short string with one catalytic domain, for cases where 785 residues only
# make the assertion harder to read: CD is 6..10 of a 12-residue chain.
SHORT = validate_domains("1-L-5-6-CD-10-11-IDR-12")


# --- the three contributions -----------------------------------------------


def test_non_catalytic_residues_are_the_complement_of_the_cd_segments():
    assert non_catalytic_residues(SHORT) == {1, 2, 3, 4, 5, 11, 12}


def test_the_catalytic_domains_are_what_is_left_designable():
    # AlyFRB: CD 144-479 and 494-773, so 169 residues are fixed by domain alone.
    assert len(non_catalytic_residues(ALYFRB)) == 169
    assert sorted(non_catalytic_residues(ALYFRB))[:3] == [1, 2, 3]
    assert 144 not in non_catalytic_residues(ALYFRB)
    assert 480 in non_catalytic_residues(ALYFRB)


def test_the_termini_match_the_skills_own_choice():
    # The script does `add_residues.update([1, 2, length - 1, length])`.
    assert terminal_residues(785) == {1, 2, 784, 785}
    assert terminal_residues(12) == {1, 2, 11, 12}


def test_a_chain_shorter_than_the_terminus_width_does_not_run_off_either_end():
    assert terminal_residues(3) == {1, 2, 3}
    assert terminal_residues(1) == {1}
    with pytest.raises(InvalidInput, match="no termini"):
        terminal_residues(0)


def test_the_fixed_set_is_the_union_of_all_three():
    # 7 outside the CD, plus conserved 7 and 8 inside it.
    assert fixed_positions(SHORT, [7, 8]) == (1, 2, 3, 4, 5, 7, 8, 11, 12)


def test_a_conserved_position_already_outside_the_cd_changes_nothing():
    assert fixed_positions(SHORT, [1, 2]) == fixed_positions(SHORT)


def test_the_designable_set_is_the_complement_and_lies_inside_the_cd():
    assert designable_positions(SHORT, [7, 8]) == (6, 9, 10)
    # The figure that makes a conservation level mean something before a round.
    assert len(designable_positions(ALYFRB)) == 785 - 169


def test_a_conserved_position_outside_the_chain_is_refused():
    # The conservation run and the domain string disagree about the construct,
    # which means every position in the jsonl is suspect.
    with pytest.raises(InvalidInput, match="outside the trimmed chain"):
        fixed_positions(SHORT, [99])
    with pytest.raises(InvalidInput, match="outside the trimmed chain"):
        fixed_positions(SHORT, [0])


# --- reading what the conservation step wrote ------------------------------


def test_a_protein_id_written_as_a_path_is_reduced_to_its_stem():
    # ProteinMPNN derives the id from --pdb_path, so a path key would not match.
    text = json.dumps({f"pdb/{MODEL}.pdb": {"A": [3, 1, 2]}}) + "\n"
    assert parse_fixed_positions_jsonl(text) == {f"{MODEL}.pdb": [3, 1, 2]}


def test_the_nothing_conserved_sentinel_is_read_as_an_empty_set():
    # hhblits_search.py writes {"A": "-"} rather than an empty list.
    text = json.dumps({MODEL: {"A": "-"}}) + "\n"
    assert parse_fixed_positions_jsonl(text) == {MODEL: []}


def test_a_chain_that_is_absent_is_read_as_an_empty_set():
    text = json.dumps({MODEL: {"B": [5]}}) + "\n"
    assert parse_fixed_positions_jsonl(text, chain="A") == {MODEL: []}


def test_blank_lines_are_skipped():
    text = f"\n{json.dumps({MODEL: {'A': [1]}})}\n\n"
    assert parse_fixed_positions_jsonl(text) == {MODEL: [1]}


def test_a_malformed_jsonl_is_refused_with_its_line_number():
    with pytest.raises(InvalidInput, match="line 2 is not JSON"):
        parse_fixed_positions_jsonl(json.dumps({MODEL: {"A": [1]}}) + "\n{nope\n")


def test_a_jsonl_of_the_wrong_shape_is_refused():
    with pytest.raises(InvalidInput, match="not an object"):
        parse_fixed_positions_jsonl("[1, 2, 3]\n")
    with pytest.raises(InvalidInput, match="chain table"):
        parse_fixed_positions_jsonl(json.dumps({MODEL: [1, 2]}) + "\n")
    with pytest.raises(InvalidInput, match="not a list of positions"):
        parse_fixed_positions_jsonl(json.dumps({MODEL: {"A": 5}}) + "\n")


def test_an_empty_jsonl_is_an_error_not_an_empty_result():
    # A conservation run that produced nothing must not quietly become a round
    # in which every position is designable.
    with pytest.raises(InvalidInput, match="empty"):
        parse_fixed_positions_jsonl("\n\n")


# --- writing what ProteinMPNN reads ---------------------------------------


def test_the_rendered_jsonl_is_one_sorted_deduped_line_per_protein():
    text = render_fixed_positions_jsonl({f"pdb/{MODEL}.pdb": [3, 1, 1, 2]})
    assert text == json.dumps({f"{MODEL}.pdb": {"A": [1, 2, 3]}}) + "\n"


def test_rendering_round_trips_through_parsing():
    positions = {f"{MODEL}.pdb": [1, 2, 3, 785]}
    assert parse_fixed_positions_jsonl(render_fixed_positions_jsonl(positions)) == positions


def test_rendering_nothing_is_refused():
    with pytest.raises(InvalidInput, match="no proteins"):
        render_fixed_positions_jsonl({})


# --- end to end, as the skill's script is used ----------------------------


def test_add_fixed_positions_unions_the_conservation_output_with_the_domains():
    conserved = json.dumps({f"pdb/{MODEL}.pdb": {"A": [7, 8]}}) + "\n"
    out = json.loads(add_fixed_positions(conserved, SHORT))
    assert out == {f"{MODEL}.pdb": {"A": [1, 2, 3, 4, 5, 7, 8, 11, 12]}}


def test_add_fixed_positions_handles_the_alyfrb_scale():
    conserved = json.dumps({MODEL: {"A": [200, 300, 500]}}) + "\n"
    out = json.loads(add_fixed_positions(conserved, ALYFRB))
    positions = out[MODEL]["A"]
    # 169 by domain, plus the three conserved ones inside the CD segments.
    assert len(positions) == 172
    assert {200, 300, 500}.issubset(positions)
    assert positions == sorted(positions)


def test_add_fixed_positions_keeps_every_protein_in_a_multi_record_jsonl():
    text = (
        json.dumps({f"pdb/{MODEL}.pdb": {"A": [7]}})
        + "\n"
        + json.dumps({"other.pdb": {"A": [8]}})
        + "\n"
    )
    out = add_fixed_positions(text, SHORT)
    assert len(out.strip().splitlines()) == 2
    assert parse_fixed_positions_jsonl(out).keys() == {f"{MODEL}.pdb", "other.pdb"}
