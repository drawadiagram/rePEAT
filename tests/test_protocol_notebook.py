"""The generated Configuration cell, and the AlphaFold3 inputs that follow it.

The cell is checked against the real notebook from the skill when it is
available, because the contract is "this notebook runs with this cell" and the
cell's variable names are the whole of it. The patch is checked for finding the
cell by marker, which is what stops it rewriting the wrong one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from designagent.protocol.af3 import (
    SUMMARY_SUFFIX,
    collect_summaries,
    design_name,
    design_names,
    input_files,
    monomer_json,
    output_globs,
    read_summary,
)
from designagent.protocol.inputs import InvalidInput, ProtocolInputs
from designagent.protocol.notebook import (
    MARKER,
    analysis_outputs,
    configuration_cell,
    find_configuration_cell,
    input_sets,
    patch_notebook,
    read_fasta,
    results_dir,
    selected_fasta_name,
)

ALYFRB = dict(
    name="AlyFRB",
    uniprot="A0A173MSR7",
    domains="1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785",
    netid="all239",
    method="cpos",
    cat_res="310,364",
)
STEM = "AF-A0A173MSR7-F1-model_v6"
PROJ = "/projects/f_sdk94_1/Stabilization/Targets/JGI_Fall_2026/AlyFRB"

#: The skill's own notebook, if the checkout is beside this repo. Several tests
#: assert against the real thing and skip rather than lie when it is absent.
REAL_NOTEBOOK = (
    Path(__file__).resolve().parents[2]
    / "enzyme-redesign-protocol/scripts/amarel/analysis/analyze_stabilization.ipynb"
)


@pytest.fixture
def inputs() -> ProtocolInputs:
    return ProtocolInputs.parse(**ALYFRB)


def cell(inputs: ProtocolInputs, **kw) -> str:
    args = dict(proj=PROJ, model_stem=STEM, levels=(50,), analysis_level=50)
    return configuration_cell(inputs, **{**args, **kw})


# --- the cell -------------------------------------------------------------


def test_the_cell_defines_every_name_the_notebook_reads(inputs):
    text = cell(inputs)
    for name in (
        "TARGET",
        "BASE",
        "INPUTS",
        "PDB",
        "FIXED_POSITIONS_JSONL",
        "FIXED_POSITIONS_1IDX",
        "CATALYTIC_POSITIONS_1IDX",
        "N_SELECT",
        "KMEDOIDS_FIXED_MODE",
        "KMEDOIDS_USE_SCORES",
        "KMEDOIDS_SCORE_RADIUS",
        "KMEDOIDS_SEED",
        "TSNE_SEED",
        "PYMOL_DESIGN_POSITIONS",
        "OUTDIR",
    ):
        assert f"{name} " in text or f"{name}=" in text, name


def test_the_cell_is_valid_python_and_assigns_what_it_should(inputs):
    namespace: dict = {"Path": Path}
    exec(compile(cell(inputs), "<cell>", "exec"), namespace)  # noqa: S102
    assert namespace["TARGET"] == "AlyFRB"
    assert namespace["BASE"] == Path(PROJ)
    assert namespace["CATALYTIC_POSITIONS_1IDX"] == [310, 364]
    assert namespace["N_SELECT"] == 10
    assert namespace["PDB"] == Path(PROJ) / f"mpnn/pdb/{STEM}.pdb"
    assert namespace["FIXED_POSITIONS_JSONL"] == Path(PROJ) / "mpnn/cpos_50_CD.jsonl"


def test_the_default_is_the_cpos50_pair(inputs):
    namespace: dict = {"Path": Path}
    exec(compile(cell(inputs), "<cell>", "exec"), namespace)  # noqa: S102
    labels = [label for label, _ in namespace["INPUTS"]]
    # HaloMPNN first: the figures put the two side by side and the order sets
    # the legend. These exact labels pick the HaloMPNN paper's colours.
    assert labels == ["HaloMPNN_cpos50", "SolubleMPNN_cpos50"]
    assert namespace["INPUTS"][0][1] == Path(PROJ) / f"mpnn/cpos50_halo/seqs/{STEM}.fa"
    assert namespace["INPUTS"][1][1] == Path(PROJ) / f"mpnn/cpos50/seqs/{STEM}.fa"


def test_widening_to_three_levels_lists_six_sets(inputs):
    namespace: dict = {"Path": Path}
    source = cell(inputs, levels=(30, 50, 70), analysis_level=50)
    exec(compile(source, "<cell>", "exec"), namespace)  # noqa: S102
    # Each added set is N_SELECT more AlphaFold3 jobs, an hour of GPU each,
    # which is why the default stays at one level.
    assert len(namespace["INPUTS"]) == 6


def test_the_conservation_liu_paths_carry_the_tag():
    liu = ProtocolInputs.parse(**{**ALYFRB, "method": "conservation_liu"})
    namespace: dict = {"Path": Path}
    exec(compile(cell(liu), "<cell>", "exec"), namespace)  # noqa: S102
    assert namespace["INPUTS"][0][0] == "HaloMPNN_cposliu_50"
    assert namespace["INPUTS"][0][1] == Path(PROJ) / f"mpnn/cposliu_50_halo/seqs/{STEM}.fa"
    assert namespace["FIXED_POSITIONS_JSONL"] == Path(PROJ) / "mpnn/cpos_liu_50_CD.jsonl"


def test_the_fixed_positions_level_must_be_one_of_the_analysed_levels(inputs):
    with pytest.raises(InvalidInput, match="not among the levels"):
        cell(inputs, levels=(30,), analysis_level=50)


def test_the_cell_needs_the_confirmed_catalytic_residues():
    staged = ProtocolInputs.parse(**{**ALYFRB, "cat_res": ""})
    with pytest.raises(InvalidInput, match="catalytic residues"):
        cell(staged)


def test_asking_for_neither_model_is_refused(inputs):
    with pytest.raises(InvalidInput, match="at least one redesign set"):
        input_sets(inputs, (50,), halo=False, soluble=False)


# --- patching by marker ---------------------------------------------------


def notebook_with(*sources: str) -> str:
    return json.dumps(
        {
            "cells": [
                {"cell_type": "code", "source": [s], "outputs": [{"x": 1}], "execution_count": 3}
                for s in sources
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )


def test_the_configuration_cell_is_found_by_marker_not_by_index(inputs):
    # An index-only patch rewrites the wrong cell the first time anyone inserts
    # one above it, and the notebook then runs someone else's configuration.
    book = notebook_with("import x", "# unrelated", f"{MARKER}\nTARGET = 'old'")
    assert find_configuration_cell(json.loads(book)) == 2


def test_a_missing_marker_fails_loudly(inputs):
    with pytest.raises(InvalidInput, match="no Configuration cell"):
        find_configuration_cell(json.loads(notebook_with("import x")))


def test_an_ambiguous_marker_fails_loudly(inputs):
    with pytest.raises(InvalidInput, match="ambiguous"):
        find_configuration_cell(json.loads(notebook_with(MARKER, MARKER)))


def test_patching_replaces_the_cell_and_clears_its_stale_output(inputs):
    book = notebook_with("import x", f"{MARKER}\nTARGET = 'old'")
    patched = json.loads(patch_notebook(book, cell(inputs)))
    assert patched["cells"][0]["source"] == ["import x"]
    assert "AlyFRB" in "".join(patched["cells"][1]["source"])
    assert "old" not in "".join(patched["cells"][1]["source"])
    # A pushed notebook must not carry a previous campaign's printed
    # configuration next to this one's code.
    assert patched["cells"][1]["outputs"] == []
    assert patched["cells"][1]["execution_count"] is None


def test_patching_keeps_the_notebook_loadable_by_nbformat(inputs):
    book = notebook_with("import x", f"{MARKER}\nTARGET = 'old'")
    patched = json.loads(patch_notebook(book, cell(inputs)))
    # nbformat wants every source line to keep its newline but the last.
    lines = patched["cells"][1]["source"]
    assert all(line.endswith("\n") for line in lines[:-1])
    assert "".join(lines) == cell(inputs)


def test_a_notebook_that_is_not_json_is_refused(inputs):
    with pytest.raises(InvalidInput, match="not valid JSON"):
        patch_notebook("{nope", cell(inputs))


@pytest.mark.skipif(not REAL_NOTEBOOK.exists(), reason="the skill checkout is not beside this repo")
def test_the_marker_matches_the_real_notebook(inputs):
    """The contract: the real notebook has exactly one cell this patch targets."""
    book = REAL_NOTEBOOK.read_text()
    index = find_configuration_cell(json.loads(book))
    original = "".join(json.loads(book)["cells"][index]["source"])
    # Every name the real cell assigns must still be assigned by ours, or the
    # notebook raises NameError halfway through a two-minute batch job.
    assigned = {
        line.split("=")[0].strip()
        for line in original.splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    generated = cell(inputs)
    missing = {name for name in assigned if name and f"{name} " not in generated
               and f"{name}=" not in generated}
    assert not missing, f"the real notebook reads these and the cell omits them: {missing}"


@pytest.mark.skipif(not REAL_NOTEBOOK.exists(), reason="the skill checkout is not beside this repo")
def test_patching_the_real_notebook_leaves_every_other_cell_alone(inputs):
    book = REAL_NOTEBOOK.read_text()
    before = json.loads(book)
    after = json.loads(patch_notebook(book, cell(inputs)))
    assert len(after["cells"]) == len(before["cells"])
    index = find_configuration_cell(before)
    for position, (was, now) in enumerate(zip(before["cells"], after["cells"])):
        if position != index:
            assert was["source"] == now["source"], position


# --- the notebook's outputs ----------------------------------------------


def test_the_output_names_are_the_ones_later_steps_read(inputs):
    assert selected_fasta_name(inputs) == "AlyFRB_selected.fa"
    assert results_dir(inputs) == "results_AlyFRB"
    assert analysis_outputs(inputs) == (
        "AlyFRB_selected.fa",
        "AlyFRB_tsne_coords.csv",
        "AlyFRB_pymol.pml",
    )
    # The plots are deliberately absent: one PNG is close to the 1 MiB
    # per-file ceiling and several would exhaust the 4 MiB job budget.
    assert not any(name.endswith(".png") for name in analysis_outputs(inputs))


def test_reading_the_selected_fasta():
    text = ">n1|HaloMPNN_cpos50_d_9|src=x\nMKVL\nAAAA\n>n2|other\nMKVV\n"
    assert read_fasta(text) == [
        ("n1|HaloMPNN_cpos50_d_9|src=x", "MKVLAAAA"),
        ("n2|other", "MKVV"),
    ]


def test_an_empty_selected_fasta_is_an_error():
    with pytest.raises(InvalidInput, match="no sequences"):
        read_fasta(">header with no sequence\n")


# --- AlphaFold3 ----------------------------------------------------------


def test_a_design_name_survives_both_header_styles():
    # The raw ProteinMPNN header and the notebook's curated one.
    assert design_name("T=0.1, sample=3, score=1.2", STEM) == f"{STEM}_T_0.1_sample_3_score_1.2_af3"
    assert (
        design_name("n1|HaloMPNN_cpos50_d_9|src=HaloMPNN_cpos50", STEM)
        == f"{STEM}_n1_HaloMPNN_cpos50_d_9_src_HaloMPNN_cpos50_af3"
    )


def test_a_design_name_keeps_nothing_that_is_not_filename_safe():
    name = design_name("a b;c|d$(e)/f", STEM)
    assert set(name) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def test_a_header_with_nothing_usable_is_refused():
    with pytest.raises(InvalidInput, match="nothing usable"):
        design_name("$$$", STEM)


def test_two_headers_cleaning_to_one_name_are_refused():
    # The name is a filename: the second would silently replace the first, and
    # the campaign would fold nine designs while reporting ten.
    with pytest.raises(InvalidInput, match="already uses"):
        design_names(["a b", "a;b"], STEM)


def test_the_monomer_json_matches_the_wrappers_shape():
    payload = json.loads(monomer_json("d1", "mkv"))
    assert payload == {
        "name": "d1",
        "sequences": [{"protein": {"id": "A", "sequence": "MKV"}}],
        "modelSeeds": [1],
        "dialect": "alphafold3",
        "version": 1,
    }


def test_a_sequence_that_is_not_residues_is_refused():
    for bad in ["", "MKV*", "MK V", "MKV1"]:
        with pytest.raises(InvalidInput, match="no usable sequence"):
            monomer_json("d1", bad)


def test_one_input_file_per_selected_design():
    records = read_fasta(">n1|a\nMKV\n>n2|b\nMKA\n")
    files = input_files(records, STEM)
    assert set(files) == {f"{STEM}_n1_a_af3.json", f"{STEM}_n2_b_af3.json"}
    assert json.loads(files[f"{STEM}_n1_a_af3.json"])["sequences"][0]["protein"][
        "sequence"
    ] == "MKV"


def test_reading_a_summary_keeps_the_scalars_and_drops_the_matrices():
    text = json.dumps(
        {
            "ptm": 0.87,
            "iptm": None,
            "ranking_score": 0.91,
            "has_clash": False,
            "fraction_disordered": 0.12,
            "chain_pair_pae_min": [[0.1, 0.2], [0.2, 0.1]],
        }
    )
    assert read_summary(text) == {
        "ptm": 0.87,
        "ranking_score": 0.91,
        "has_clash": 0.0,
        "fraction_disordered": 0.12,
    }


def test_a_file_that_is_not_a_summary_is_refused():
    with pytest.raises(InvalidInput, match="not a summary_confidences file"):
        read_summary(json.dumps({"something": "else"}))
    with pytest.raises(InvalidInput, match="not valid JSON"):
        read_summary("{nope")


def test_collecting_summaries_counts_progress_and_picks_the_best():
    files = {
        f"af3/d1{SUMMARY_SUFFIX}": json.dumps({"ptm": 0.7, "ranking_score": 0.70}),
        f"af3/d2{SUMMARY_SUFFIX}": json.dumps({"ptm": 0.9, "ranking_score": 0.95}),
        "af3/notes.txt": "ignored",
    }
    got = collect_summaries(files)
    assert got["n_done"] == 2
    assert got["best"] == "d2"
    assert got["designs"]["d1"]["ptm"] == 0.7
    assert got["problems"] == []


def test_a_summary_that_cannot_be_read_is_named_not_dropped():
    # A design whose scores cannot be read is not the same as a design that has
    # not finished, and the count must not conflate them.
    got = collect_summaries({f"af3/d1{SUMMARY_SUFFIX}": "{nope"})
    assert got["n_done"] == 0
    assert got["problems"] and got["problems"][0].startswith("d1:")


def test_the_output_glob_is_also_the_progress_poll():
    # Which design directories exist is the report; no handle is involved.
    assert output_globs("/scratch/all239/af3/AlyFRB") == [
        ("af3", f"/scratch/all239/af3/AlyFRB/af_output/*/*{SUMMARY_SUFFIX}")
    ]
