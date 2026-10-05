"""ProteinMPNN: the job spec, the FASTA parser, and the adapter between them.

None of this needed a test before because none of it was reachable: `hpc` was
never attached, so `mpnn_job_spec` was never submitted and `parse_mpnn_fasta`
had no callers anywhere in the repo.
"""

from __future__ import annotations

import base64
import gzip

import pytest
from designagent.tools.proteinmpnn import (
    MAX_INLINE_B64,
    backbone_pdb,
    mpnn_job_spec,
    parse_mpnn_fasta,
    propose_variants,
    proteinmpnn_local_fallback,
    variants_from_mpnn_fasta,
)

from tests.conftest import REF_SEQ

# A real ProteinMPNN header, verbatim. The commas inside `designed_chains`
# matter: the parser splits the header on commas.
REAL_FASTA = """\
>1ubq, score=1.0383, global_score=1.0383, fixed_chains=[], designed_chains=['A'], \
model_name=v_48_020, git_hash=8907e6671bfbfc92303b5f79c4b5e6ce47cdef57, seed=37
MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG
>T=0.1, sample=1, score=0.8521, global_score=0.9011, seq_recovery=0.6447
MQIFVKTLTGKTITLEVEASDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG
>T=0.1, sample=2, score=0.9014, global_score=0.9330, seq_recovery=0.6184
MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIWKESTLHLVLRLRGG
"""


def pdb_text(n: int = 40, chain: str = "A") -> str:
    """A minimal multi-atom PDB: backbone plus a side-chain atom to be dropped."""
    lines = []
    for i in range(1, n + 1):
        for atom in ("N", "CA", "C", "O", "CB"):
            lines.append(
                f"ATOM  {i * 5:5d}  {atom:<3s} ALA {chain}{i:4d}    "
                f"{i:8.3f}{i:8.3f}{i:8.3f}  1.00 50.00"
            )
    lines.append("END")
    return "\n".join(lines) + "\n"


# --- the structure that travels with the job --------------------------------


def test_backbone_pdb_keeps_only_backbone_atoms():
    out = backbone_pdb(pdb_text(3))
    assert " CB " not in out
    for atom in ("N", "CA", "C", "O"):
        assert f" {atom} " in out or f" {atom}  " in out
    assert out.rstrip().endswith("END")


def test_backbone_pdb_keeps_only_the_requested_chain():
    mixed = pdb_text(2, chain="A") + pdb_text(2, chain="B")
    out = backbone_pdb(mixed, chain="B")
    assert out
    atoms = [ln for ln in out.splitlines() if ln.startswith("ATOM")]
    assert atoms and all(ln[21] == "B" for ln in atoms)


def test_backbone_pdb_is_empty_when_the_chain_is_absent():
    assert backbone_pdb(pdb_text(2, chain="A"), chain="Z") == ""


def test_the_job_spec_carries_the_structure_as_an_input():
    spec = mpnn_job_spec(pdb_text(20), num_sequences=4)
    assert spec["outputs"] == ["seqs/*.fa"]
    assert "--pdb_path" in spec["arguments"]
    # A path on this machine means nothing at the far end, so the file travels.
    assert spec["arguments"][spec["arguments"].index("--pdb_path") + 1] == "in.pdb"
    assert "in.pdb" in spec["inputs"]
    assert "ATOM" in spec["inputs"]["in.pdb"]
    assert " CB " not in spec["inputs"]["in.pdb"]
    assert spec["arguments"][spec["arguments"].index("--num_seq_per_target") + 1] == "4"


def test_the_job_spec_does_not_request_gpus_itself():
    """`_job_params` owns that, because only the site knows what it will give."""
    assert "gpus" not in mpnn_job_spec(pdb_text(5))["resources"]


def test_a_command_string_becomes_executable_plus_arguments():
    spec = mpnn_job_spec(pdb_text(5), command="/venv/bin/python /sw/protein_mpnn_run.py")
    assert spec["executable"] == "/venv/bin/python"
    assert spec["arguments"][0] == "/sw/protein_mpnn_run.py"


def test_the_job_spec_omits_fixed_positions():
    """`--fixed_positions` is not a ProteinMPNN flag; passing it fails a real run."""
    assert "--fixed_positions" not in mpnn_job_spec(pdb_text(5))["arguments"]


def test_a_structure_with_no_backbone_is_refused():
    with pytest.raises(ValueError, match="no backbone atoms"):
        mpnn_job_spec("REMARK nothing here\nEND\n")


def test_a_structure_too_large_to_inline_is_refused_with_its_size():
    """Staging is in-band, so there is a real ceiling and it must be named."""
    # Incompressible junk in the B-factor columns defeats gzip.
    rows = []
    for i in range(1, 220_000):
        rows.append(
            f"ATOM  {i:5d}  CA  ALA A{i:4d}    "
            f"{i % 997:8.3f}{(i * 31) % 991:8.3f}{(i * 17) % 983:8.3f}  1.00 50.00"
        )
    huge = "\n".join(rows) + "\nEND\n"
    payload = len(base64.b64encode(gzip.compress(backbone_pdb(huge).encode())))
    if payload <= MAX_INLINE_B64:
        pytest.skip(f"test structure compressed to {payload}; under the ceiling")
    with pytest.raises(ValueError, match="too large to inline"):
        mpnn_job_spec(huge)


# --- reading the output -----------------------------------------------------


def test_parse_mpnn_fasta_marks_the_input_record():
    records = parse_mpnn_fasta(REAL_FASTA)
    assert len(records) == 3
    assert records[0]["is_input"] is True
    assert records[0]["sequence"] == REF_SEQ
    # The header splits on commas, and designed_chains=['A'] contains one.
    assert records[0]["score"] == pytest.approx(1.0383)
    assert [r.get("sample") for r in records[1:]] == [1.0, 2.0]


def test_the_adapter_drops_the_input_and_names_mutations():
    out = variants_from_mpnn_fasta(REAL_FASTA, parent_sequence=REF_SEQ, requested=2)
    assert out["n"] == 2
    assert REF_SEQ not in [v["sequence"] for v in out["variants"]]
    first = out["variants"][0]
    assert first["source"] == "proteinmpnn"
    assert first["mutations"] == ["P19A"]
    assert first["metrics"]["mpnn_score"] == pytest.approx(0.8521)
    assert "score 0.8521" in first["rationale"]


def test_the_adapter_reads_provenance_out_of_the_models_own_output():
    out = variants_from_mpnn_fasta(REAL_FASTA, parent_sequence=REF_SEQ)
    assert out["provenance"]["model_name"] == "v_48_020"
    assert out["provenance"]["git_hash"].startswith("8907e667")


def test_provenance_is_empty_when_the_output_does_not_say():
    """Nothing invents a model name: a run that cannot say so is detectable."""
    anonymous = REAL_FASTA.replace(
        "model_name=v_48_020, git_hash=8907e6671bfbfc92303b5f79c4b5e6ce47cdef57, ", ""
    )
    out = variants_from_mpnn_fasta(anonymous, parent_sequence=REF_SEQ)
    assert out["n"] == 2
    assert "model_name" not in out["provenance"]


def test_the_adapter_reports_a_short_yield_rather_than_delivering_quietly():
    out = variants_from_mpnn_fasta(REAL_FASTA, parent_sequence=REF_SEQ, requested=8)
    assert "2 of 8" in out["short"]


def test_empty_output_is_an_error_not_an_empty_success():
    out = variants_from_mpnn_fasta("", parent_sequence=REF_SEQ)
    assert out["n"] == 0 and out["error"]


def test_output_holding_only_the_input_sequence_is_an_error():
    only_input = "\n".join(REAL_FASTA.splitlines()[:2]) + "\n"
    out = variants_from_mpnn_fasta(only_input, parent_sequence=REF_SEQ)
    assert out["n"] == 0
    assert "no sequences other than the input" in out["error"]


def test_duplicate_samples_are_deduped():
    doubled = REAL_FASTA + "\n".join(REAL_FASTA.splitlines()[2:4]) + "\n"
    out = variants_from_mpnn_fasta(doubled, parent_sequence=REF_SEQ)
    assert out["n"] == 2


# --- the fallback -----------------------------------------------------------


async def test_the_fallback_says_the_job_was_never_submitted():
    out = await proteinmpnn_local_fallback(sequence=REF_SEQ, n=3)
    assert out["requested"] == "proteinmpnn"
    assert "not ProteinMPNN samples" in out["note"]
    assert "never submitted" in out["note"]
    assert out["variants"] and all(v["source"] != "proteinmpnn" for v in out["variants"])


async def test_the_plain_proposer_keeps_its_own_label():
    out = await propose_variants(sequence=REF_SEQ, n=2)
    assert "requested" not in out
    assert out["note"] == "heuristic proposals, not ProteinMPNN samples"
