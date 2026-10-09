"""The protocol's job specs, as dicts and as shell that actually runs.

Two kinds of test here, and the second kind is the point. Asserting on the dict
catches a wrong flag; running the generated script with `bash` catches the
things that make a job fail at the far end an hour later -- a quoting mistake, a
`set -e` interaction, a glob that resolves in the wrong directory, a file that
does not come back because its mtime was preserved.

The mode of each spec is checked mechanically rather than by eye:
`wrap(spec) is spec` is true exactly for a compute spec, which is what makes its
`directory` take effect and its files persist.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from designagent.protocol.inputs import InvalidInput, ProtocolInputs
from designagent.protocol.site import PROJECT_SUBDIRS, SiteLayout
from designagent.protocol.specs import (
    AF3_DURATION_SEC,
    HHBLITS_DURATION_SEC,
    MpnnParams,
    af3_job_spec,
    analysis_job_spec,
    conservation_jsonl_name,
    fetch_job_spec,
    fixed_positions_name,
    gib,
    hhblits_job_spec,
    install_job_spec,
    job_stats_spec,
    mpnn_job_spec,
    mpnn_out_dir,
    push_job_spec,
)
from designagent.tasks.hpc.artifacts import collect, wrap

ALYFRB = dict(
    name="AlyFRB",
    uniprot="A0A173MSR7",
    domains="1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785",
    netid="abc123",
    method="cpos",
    cat_res="310,364",
)
STEM = "AF-A0A173MSR7-F1-model_v6"


@pytest.fixture
def inputs() -> ProtocolInputs:
    return ProtocolInputs.parse(**ALYFRB)


@pytest.fixture
def site() -> SiteLayout:
    return SiteLayout(
        proj_root="/projects/f_proj00_1/Stabilization/Targets/JGI_Fall_2026",
        scratch_root="/scratch",
        conda_aifold="/projects/f_proj00_1/conda/envs/aifold",
        conda_analysis="/projects/f_proj00_1/conda/envs/shared_als515",
        mpnn_path="/projects/f_proj00_1/Tools/ProteinMPNN",
        mpnn_weights="/projects/f_proj00_1/abc123/MPNN_weights",
        uniref_db="/projects/f_proj00_1/Uniref30/UniRef30_2023_02",
        af3_modules=("module load apptainer/1.2.5", "module load alphafold/vs3.0.0-pgarias"),
        gpu_constraint="ampere|adalovelace",
    )


def script_of(spec: dict) -> str:
    assert spec["executable"] == "bash"
    assert spec["arguments"][0] == "-lc"
    return spec["arguments"][1]


def is_compute(spec: dict) -> bool:
    """A compute spec is the one `wrap` leaves alone."""
    return wrap(spec) is spec


def run_transfer(spec: dict, **kw):
    """Run a transfer spec the way the far end does, and read it back."""
    wrapped = wrap(spec)
    proc = subprocess.run(
        ["bash", *wrapped["arguments"]], capture_output=True, text=True, **kw
    )
    return proc, collect(proc.stdout)


def run_compute(spec: dict, *, cwd: Path, bin_dir: Path | None = None):
    """Run a compute spec's script, with `cwd` standing in for `directory`."""
    assert is_compute(spec), "this spec would be wrapped; cwd would not apply"
    env = dict(os.environ)
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["bash", *spec["arguments"]],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def stub(bin_dir: Path, name: str, body: str = "") -> Path:
    """An executable stub that records how it was called."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(f'#!/bin/bash\necho "{name} $*" >> "$STUBLOG"\n{body}\n')
    path.chmod(0o755)
    return path


# --- modes ----------------------------------------------------------------


def test_compute_specs_are_the_ones_wrap_leaves_alone(site, inputs):
    # The property the whole design rests on: no temp directory, so `directory`
    # applies and the files are still there for the next stage.
    for spec in (
        hhblits_job_spec(site, inputs, model_stem=STEM),
        mpnn_job_spec(site, inputs, model_stem=STEM, level=50),
        analysis_job_spec(site, inputs),
        af3_job_spec(site, inputs, design="d1"),
    ):
        assert is_compute(spec), spec["name"]
        assert spec["directory"].startswith("/projects/")


def test_transfer_specs_are_wrapped_and_run_on_the_endpoint_host(site, inputs):
    for spec in (
        push_job_spec("/projects/x", {"a.txt": "a"}),
        fetch_job_spec([("out", "/projects/x/*.fa")]),
        job_stats_spec(["123"], "echo hi"),
        install_job_spec(site, inputs, {"conservation/x.py": "x"}),
    ):
        assert not is_compute(spec), spec["name"]
        # The `local` PSI/J executor: on the endpoint host, no queue slot.
        assert spec["executor"] == "local"


# --- resources: the skill's #SBATCH headers, re-expressed -----------------


def test_memory_is_expressed_in_bytes(site, inputs):
    # PSI/J's ResourceSpecV1.memory is bytes. A site rendering
    # `--mem={{memory_kb}}K` from `memory // 1024` turns 32 into `--mem=0K`,
    # which Slurm reads as the whole node.
    assert gib(32) == 34359738368
    assert hhblits_job_spec(site, inputs, model_stem=STEM)["resources"]["memory"] == gib(32)
    assert mpnn_job_spec(site, inputs, model_stem=STEM, level=50)["resources"][
        "memory"
    ] == gib(16)
    assert af3_job_spec(site, inputs, design="d")["resources"]["memory"] == gib(64)


def test_hhblits_asks_for_twelve_hours_four_processes_and_no_gpu(site, inputs):
    spec = hhblits_job_spec(site, inputs, model_stem=STEM)
    assert spec["duration_sec"] == HHBLITS_DURATION_SEC == 43200
    assert spec["resources"]["processes"] == 4
    # Zero, not absent: `job_params` turns zero into an omitted key, which is
    # how a 0-GPU job survives a site whose default is one.
    assert spec["resources"]["gpus"] == 0


def test_the_cpu_stages_ask_for_no_gpu(site, inputs):
    for spec in (
        mpnn_job_spec(site, inputs, model_stem=STEM, level=50),
        analysis_job_spec(site, inputs),
    ):
        assert spec["resources"]["gpus"] == 0


def test_af3_asks_for_a_gpu_the_gpu_queue_and_its_constraint(site, inputs):
    spec = af3_job_spec(site, inputs, design="d1")
    assert spec["duration_sec"] == AF3_DURATION_SEC
    assert spec["resources"] == {
        "node_count": 1,
        "processes": 1,
        "cpus": 8,
        "memory": gib(64),
        "gpus": 1,
    }
    assert spec["queue"] == "gpu"
    # Top level, because the far end reads only four named keys out of
    # `attributes` and drops the rest without complaining.
    assert spec["custom_attributes"] == {
        "slurm.gres": "gpu:1",
        "slurm.constraint": "ampere|adalovelace",
    }
    assert "attributes" not in spec


def test_a_site_with_no_gpu_constraint_sends_no_constraint(site, inputs):
    from dataclasses import replace

    spec = af3_job_spec(replace(site, gpu_constraint=""), inputs, design="d")
    assert spec["custom_attributes"] == {"slurm.gres": "gpu:1"}


# --- hhblits --------------------------------------------------------------


def test_hhblits_passes_the_catalytic_residues_and_the_ten_angstrom_shell(site, inputs):
    text = script_of(hhblits_job_spec(site, inputs, model_stem=STEM))
    assert "--cat_residues 310,364" in text
    # 10.0 is the current default; AlyFRB's and SM0524's first passes used 6.0
    # and the two are not comparable, which is why it is explicit and logged.
    assert "--cat_cutoff 10.0" in text
    assert "/projects/f_proj00_1/Uniref30/UniRef30_2023_02" in text
    assert f"-i {STEM}.pdb" in text
    assert spec_dir(site, inputs, "conservation") == hhblits_job_spec(
        site, inputs, model_stem=STEM
    )["directory"]


def spec_dir(site, inputs, *parts) -> str:
    return site.sub(inputs, *parts)


def test_hhblits_records_which_hhblits_ran(site, inputs):
    # Provenance, on the job's own stdout, so it is in the chip's log tail.
    assert "HHBLITS version" in script_of(hhblits_job_spec(site, inputs, model_stem=STEM))


def test_hhblits_without_the_catalytic_residues_is_refused(site):
    staged = ProtocolInputs.parse(**{**ALYFRB, "cat_res": ""})
    with pytest.raises(InvalidInput, match="Step 5 checkpoint"):
        hhblits_job_spec(site, staged, model_stem=STEM)


# --- ProteinMPNN ----------------------------------------------------------


def test_the_output_directory_names_match_the_skills(site, inputs):
    assert mpnn_out_dir(inputs, 50) == "cpos50"
    assert mpnn_out_dir(inputs, 50, halo=True) == "cpos50_halo"
    liu = ProtocolInputs.parse(**{**ALYFRB, "method": "conservation_liu"})
    assert mpnn_out_dir(liu, 50) == "cposliu_50"
    assert fixed_positions_name(inputs, 50) == "cpos_50_CD.jsonl"
    assert fixed_positions_name(liu, 50) == "cpos_liu_50_CD.jsonl"
    assert conservation_jsonl_name(inputs, 70, STEM) == f"{STEM}_cpos_70.jsonl"
    assert conservation_jsonl_name(liu, 70, STEM) == f"{STEM}_cpos_liu_70.jsonl"


def test_soluble_and_halo_select_different_weights(site, inputs):
    soluble = script_of(mpnn_job_spec(site, inputs, model_stem=STEM, level=50))
    halo = script_of(mpnn_job_spec(site, inputs, model_stem=STEM, level=50, halo=True))
    assert "--use_soluble_model --model_name v_48_020" in soluble
    assert "--path_to_model_weights" not in soluble
    assert "--model_name halompnn_v1" in halo
    assert "/projects/f_proj00_1/abc123/MPNN_weights" in halo
    assert "--use_soluble_model" not in halo


def test_halo_without_a_weights_directory_is_refused(site, inputs):
    bare = SiteLayout(proj_root="/p", conda_aifold="/e", mpnn_path="/m", uniref_db="/u")
    with pytest.raises(InvalidInput, match="weights"):
        mpnn_job_spec(bare, inputs, model_stem=STEM, level=50, halo=True)


def test_the_three_sampling_temperatures_stay_one_argv_element(site, inputs):
    # The reason parameters cannot ride in `environment`: the Slurm template
    # renders `export name=value` unquoted.
    assert MpnnParams().temps == "0.1 0.2 0.3"
    assert "--sampling_temp '0.1 0.2 0.3'" in script_of(
        mpnn_job_spec(site, inputs, model_stem=STEM, level=50)
    )


def test_mpnn_runs_the_skills_three_script_sequence(site, inputs):
    text = script_of(mpnn_job_spec(site, inputs, model_stem=STEM, level=50))
    assert text.index("parse_multiple_chains.py") < text.index("assign_fixed_chains.py")
    assert text.index("assign_fixed_chains.py") < text.index("protein_mpnn_run.py")
    # The flag that backlog A5 removed for being wrong, back with a file.
    assert "--fixed_positions_jsonl cpos_50_CD.jsonl" in text


def test_mpnn_fails_rather_than_leave_the_analysis_stage_an_absent_fasta(site, inputs):
    assert f'test -s "$out/seqs/{STEM}.fa"' in script_of(
        mpnn_job_spec(site, inputs, model_stem=STEM, level=50)
    )


def test_the_expected_sequence_count_is_the_skills_formula():
    # num_seq_per_target x n_temps + 1; the +1 is the input sequence.
    assert MpnnParams().expected_sequences == 16 * 3 + 1 == 49
    assert MpnnParams(num_seq_per_target=4, sampling_temps=(0.1,)).expected_sequences == 5


# --- analysis and AF3 -----------------------------------------------------


def test_the_analysis_stage_runs_the_notebook_with_the_analysis_environment(site, inputs):
    text = script_of(analysis_job_spec(site, inputs))
    assert "/projects/f_proj00_1/conda/envs/shared_als515/bin/jupyter" in text
    # There is no kernelspec named after the environment; the env is chosen by
    # which jupyter runs.
    assert "--ExecutePreprocessor.kernel_name=python3" in text
    assert "--inplace" in text


def test_af3_binds_the_module_provided_paths_into_the_container(site, inputs):
    text = script_of(af3_job_spec(site, inputs, design="n1_HaloMPNN_cpos50_d_9_af3"))
    assert "module load alphafold/vs3.0.0-pgarias" in text
    assert '-B "$ALPHAFOLD_MODELWEIGHTS":/root/models' in text
    assert '-B "$ALPHAFOLD_DATA_PATH":/root/public_databases' in text
    assert '"$CONTAINERDIR"/alphafold3.sif' in text
    assert "/scratch/abc123/af3/AlyFRB/af_input" in text
    assert "/scratch/abc123/af3/AlyFRB/af_output" in text


def test_af3_refuses_a_design_name_that_is_not_a_json_stem(site, inputs):
    for bad in ["", "a/b", ".hidden"]:
        with pytest.raises(InvalidInput, match="json stem"):
            af3_job_spec(site, inputs, design=bad)


def test_af3_without_the_sites_module_lines_is_refused(site, inputs):
    bare = SiteLayout(proj_root="/p")
    with pytest.raises(InvalidInput, match="module"):
        af3_job_spec(bare, inputs, design="d")


# --- transfer specs: refusals --------------------------------------------


def test_a_push_needs_an_absolute_destination_and_relative_keys():
    with pytest.raises(InvalidInput, match="absolute"):
        push_job_spec("relative/dir", {"a": "a"})
    with pytest.raises(InvalidInput, match="relative path"):
        push_job_spec("/p", {"/etc/passwd": "x"})
    with pytest.raises(InvalidInput, match="relative path"):
        push_job_spec("/p", {"../../escape": "x"})
    with pytest.raises(InvalidInput, match="at least one file"):
        push_job_spec("/p", {})


def test_a_fetch_needs_absolute_sources():
    # The command runs in a temp directory, so a relative source resolves there.
    with pytest.raises(InvalidInput, match="absolute"):
        fetch_job_spec([("out", "relative/*.fa")])
    with pytest.raises(InvalidInput, match="relative path"):
        fetch_job_spec([("/abs", "/p/*.fa")])
    with pytest.raises(InvalidInput, match="at least one source"):
        fetch_job_spec([])


def test_job_stats_refuses_anything_that_is_not_a_scheduler_id():
    with pytest.raises(InvalidInput, match="scheduler id"):
        job_stats_spec(["123; rm -rf /"], "echo")
    with pytest.raises(InvalidInput, match="no job ids"):
        job_stats_spec([], "echo")
    assert job_stats_spec(["123", "456_7", "89.batch"], "echo")["inputs"]


# --- transfer specs: actually run them -----------------------------------


def test_a_push_lands_the_files_and_reports_their_sizes(tmp_path):
    dest = tmp_path / "proj"
    spec = push_job_spec(
        str(dest),
        {"conservation/hhblits_search.py": "print(1)\n", "mpnn/pdb/m.pdb": "ATOM\n"},
        subdirs=PROJECT_SUBDIRS,
    )
    proc, got = run_transfer(spec)
    assert proc.returncode == 0, proc.stderr
    assert got.ok, got.error
    # The tree every compute stage's `--chdir` depends on existing.
    for sub in PROJECT_SUBDIRS:
        assert (dest / sub).is_dir(), sub
    assert (dest / "conservation/hhblits_search.py").read_text() == "print(1)\n"
    assert (dest / "mpnn/pdb/m.pdb").read_text() == "ATOM\n"
    manifest = got.text("manifest.txt")
    assert "9 conservation/hhblits_search.py" in manifest
    assert "5 mpnn/pdb/m.pdb" in manifest


def test_a_push_is_idempotent(tmp_path):
    dest = tmp_path / "proj"
    spec = push_job_spec(str(dest), {"a.txt": "one"}, subdirs=("x",))
    assert run_transfer(spec)[0].returncode == 0
    proc, got = run_transfer(spec)
    assert proc.returncode == 0, proc.stderr
    assert got.ok
    assert (dest / "a.txt").read_text() == "one"


def test_a_fetch_brings_files_back_under_prefixes_that_keep_them_distinct(tmp_path):
    # The real collision: every MPNN output FASTA is named after the same model,
    # so six of them flattened into one directory would be one file.
    proj = tmp_path / "proj" / "mpnn"
    for variant in ("cpos50", "cpos50_halo"):
        seqs = proj / variant / "seqs"
        seqs.mkdir(parents=True)
        (seqs / f"{STEM}.fa").write_text(f">{variant}\nMKV\n")

    spec = fetch_job_spec(
        [
            ("soluble_cpos50", f"{proj}/cpos50/seqs/*.fa"),
            ("halo_cpos50", f"{proj}/cpos50_halo/seqs/*.fa"),
        ]
    )
    proc, got = run_transfer(spec)
    assert proc.returncode == 0, proc.stderr
    assert got.ok, got.error
    assert set(got.files) == {
        f"soluble_cpos50/{STEM}.fa",
        f"halo_cpos50/{STEM}.fa",
    }
    assert "cpos50_halo" in got.text(f"halo_cpos50/{STEM}.fa")
    assert ">cpos50\n" in got.text(f"soluble_cpos50/{STEM}.fa")


def test_a_fetch_returns_an_old_file_despite_the_mtime_marker(tmp_path):
    # `outputs: ["**"]` finds files newer than a marker written before the
    # command ran. A conservation output made hours earlier must still come
    # back, which is why the script copies without -p and then touches.
    source = tmp_path / "proj" / "output"
    source.mkdir(parents=True)
    old = source / "x_cpos_50.jsonl"
    old.write_text('{"m": {"A": [1]}}\n')
    os.utime(old, (1, 1))  # 1970

    proc, got = run_transfer(fetch_job_spec([("conservation", f"{source}/*.jsonl")]))
    assert proc.returncode == 0, proc.stderr
    assert got.ok, got.error
    assert got.text("conservation/x_cpos_50.jsonl").startswith('{"m"')


def test_a_fetch_that_matches_nothing_succeeds_with_nothing(tmp_path):
    # Not an error: the caller asked for specific names and can see what came
    # back, and failing would throw away whatever else did match -- `set -e`
    # means a non-zero command never reaches the staging epilogue.
    proc, got = run_transfer(fetch_job_spec([("out", f"{tmp_path}/absent/*.fa")]))
    assert proc.returncode == 0, proc.stderr
    assert got.ok
    assert got.files == {}


def test_a_fetch_brings_back_the_matches_it_does_have(tmp_path):
    (tmp_path / "there.fa").write_text(">a\nMK\n")
    proc, got = run_transfer(
        fetch_job_spec(
            [("got", f"{tmp_path}/there.fa"), ("missing", f"{tmp_path}/absent/*.fa")]
        )
    )
    assert proc.returncode == 0, proc.stderr
    assert set(got.files) == {"got/there.fa"}


def test_job_stats_runs_the_staged_script(tmp_path):
    spec = job_stats_spec(
        ["12345"], '#!/bin/bash\nfor j in "$@"; do echo "| $j | done |"; done\n'
    )
    proc, got = run_transfer(spec)
    assert proc.returncode == 0, proc.stderr
    assert got.text("job_stats.md").strip() == "| 12345 | done |"


# --- compute specs: actually run their shell ------------------------------


def test_the_hhblits_script_calls_through_with_the_right_argv(tmp_path, site, inputs):
    bin_dir = tmp_path / "bin"
    log = tmp_path / "stub.log"
    stub(bin_dir, "hhblits")
    stub(bin_dir, "python")
    work = tmp_path / "conservation"
    work.mkdir()

    spec = hhblits_job_spec(site, inputs, model_stem=STEM)
    env_proc = subprocess.run(
        ["bash", *spec["arguments"]],
        cwd=str(work),
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUBLOG": str(log)},
        capture_output=True,
        text=True,
    )
    assert env_proc.returncode == 0, env_proc.stderr
    calls = log.read_text()
    assert "hhblits -h" in calls
    assert f"python hhblits_search.py -i {STEM}.pdb -o output" in calls
    assert "--cat_residues 310,364 --cat_cutoff 10.0" in calls


def test_the_mpnn_script_survives_a_host_with_no_module_command(tmp_path, site, inputs):
    # `module purge || true`: these runs ask for zero GPUs, so a site without
    # the CUDA module must not fail the job over it. Written as `|| true`
    # rather than `[ -r ... ] && .` because under `set -e` the `&&` form makes a
    # missing file the script's exit status.
    bin_dir = tmp_path / "bin"
    log = tmp_path / "stub.log"
    stub(bin_dir, "python", body='mkdir -p "$out/seqs" 2>/dev/null; true')
    work = tmp_path / "mpnn"
    (work / "pdb").mkdir(parents=True)

    spec = mpnn_job_spec(site, inputs, model_stem=STEM, level=50)
    proc = subprocess.run(
        ["bash", *spec["arguments"]],
        cwd=str(work),
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUBLOG": str(log)},
        capture_output=True,
        text=True,
    )
    # It gets as far as the final `test -s`, which fails because the stub wrote
    # no FASTA -- proving the module guard did not abort it first.
    assert "parse_multiple_chains.py" in log.read_text()
    assert "protein_mpnn_run.py" in log.read_text()
    assert proc.returncode != 0


def test_a_site_path_containing_a_space_survives_quoting(tmp_path, inputs):
    # Site paths come from settings, which `protocol/inputs.py` never sees, so
    # they are the one unvalidated string in a generated script.
    spaced = SiteLayout(
        proj_root="/projects/with space/targets",
        conda_aifold="/opt/env with space",
        mpnn_path="/opt/mpnn dir",
        uniref_db="/db/uniref 30",
    )
    spec = hhblits_job_spec(spaced, inputs, model_stem=STEM)
    assert spec["directory"] == "/projects/with space/targets/AlyFRB/conservation"
    # `bash -n` parses without running: a quoting error is a syntax error here.
    check = subprocess.run(
        ["bash", "-n", "-c", script_of(spec)], capture_output=True, text=True
    )
    assert check.returncode == 0, check.stderr
    assert "'/db/uniref 30'" in script_of(spec)


def test_every_generated_script_parses(tmp_path, site, inputs):
    specs = [
        hhblits_job_spec(site, inputs, model_stem=STEM),
        mpnn_job_spec(site, inputs, model_stem=STEM, level=50),
        mpnn_job_spec(site, inputs, model_stem=STEM, level=70, halo=True),
        analysis_job_spec(site, inputs),
        af3_job_spec(site, inputs, design="d1"),
        push_job_spec("/p", {"a/b.py": "x"}),
        fetch_job_spec([("o", "/p/*.fa")]),
        job_stats_spec(["1"], "echo"),
    ]
    for spec in specs:
        check = subprocess.run(
            ["bash", "-n", "-c", script_of(spec)], capture_output=True, text=True
        )
        assert check.returncode == 0, f"{spec['name']}: {check.stderr}"


def test_an_absolute_project_root_is_required():
    with pytest.raises(InvalidInput, match="absolute path"):
        SiteLayout(proj_root="projects/relative")


def test_the_site_names_what_is_still_unset():
    assert SiteLayout(proj_root="/p").missing() == (
        "conda_aifold",
        "conda_analysis",
        "mpnn_path",
        "uniref_db",
    )
