"""Job specs for every step of the protocol that runs on the cluster.

Pure functions returning the dict shape `OrbitInterface._submit_job` reads, the
same shape as `tools/proteinmpnn.py::mpnn_job_spec`. Nothing here submits, so
all of it is testable offline -- both as dicts and by running the generated
shell with `bash` against a `tmp_path`, the way `tests/test_artifacts.py` proves
the staging protocol.

## The two modes

`artifacts.wrap` rewrites any spec declaring `inputs` or `outputs` into a
`bash -lc` script that works in `work=$(mktemp -d)` under a `trap ... EXIT` that
deletes it, and globs the declared outputs *in that temp directory*. A spec
declaring neither is passed through untouched. So:

**Compute specs declare neither.** They set `directory` to a `$PROJ` subdirectory
instead, so their files persist for the next step and their real stdout is the
job's stdout, which `drain_logs` tails into the task chip. Unwrapped means
*`wrap` is identity*, not "no shell": these are still `bash -lc`, because a
login shell is where `module` is defined and where a conda environment's `bin`
can be put on `PATH`.

**Transfer specs declare both.** `push_job_spec` carries files out to `$PROJ`,
`fetch_job_spec` brings results back. They run on the `local` PSI/J executor --
on the endpoint host, no queue slot -- which is sound only because `$PROJ` and
`/scratch` are visible there. That is a property of running the endpoint on a
login node, and it is written down in `plans/AMAREL_ENDPOINT.md` as a
precondition rather than assumed here.

## Why the skill's `#SBATCH` lines are gone

PSI/J generates its own submit script, so the moment one of the skill's scripts
becomes a *command*, its `#SBATCH` header is a block of comments. The resources
below are those headers re-expressed, and **these builders are now the source of
truth for them.** Two things are lost in the move and worth knowing: `--requeue`,
so a preempted job is simply a failure; and the per-job `slurm.%N.%j.out` names,
so the lab notebook's job table comes from `handle.meta["native_id"]` and `sacct`
rather than from parsing filenames.

`ResourceSpecV1.memory` is in **bytes**. A site rendering `--mem={{memory_kb}}K`
from `memory // 1024` turns `{"memory": 32}` into `--mem=0K`, which Slurm reads
as the whole node, so every memory figure here goes through `gib()`.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .inputs import InvalidInput, ProtocolInputs
from .site import PROJECT_SUBDIRS, SiteLayout

# Walltimes, from the skill's own #SBATCH --time values.
HHBLITS_DURATION_SEC = 12 * 60 * 60
MPNN_DURATION_SEC = 4 * 60 * 60
ANALYSIS_DURATION_SEC = 4 * 60 * 60
AF3_DURATION_SEC = 4 * 60 * 60
TRANSFER_DURATION_SEC = 10 * 60


def gib(count: float) -> int:
    """Gibibytes as the bytes PSI/J's `ResourceSpecV1.memory` wants."""
    return int(count * 1024**3)


def _bash(lines: Sequence[str]) -> dict[str, Any]:
    """A login shell running a script, as both modes need."""
    return {"executable": "bash", "arguments": ["-lc", "\n".join(lines)]}


def _head(*lines: str) -> list[str]:
    """Script preamble. `set -e` is ours to add: only `wrap` adds its own."""
    return ["set -euo pipefail", *lines]


def _modules(*loads: str) -> list[str]:
    """Load environment modules, sourcing the module function first.

    Written as an `if` rather than `[ -r ... ] && . ...` on purpose: under
    `set -e` the `&&` form makes a *missing* file the script's exit status, so
    the job dies on the one host where the guard was supposed to help.
    """
    lines = [
        "if [ -r /etc/profile.d/modules.sh ]; then . /etc/profile.d/modules.sh; fi",
    ]
    lines.extend(loads)
    return lines


def _on_path(env: str) -> str:
    """Put a conda environment's `bin` first, instead of `conda activate`.

    The skill does `source ~/.bashrc; conda activate <env>`, which works
    interactively and is a coin flip under `bash -lc`: a login shell reads
    `.bash_profile`, not `.bashrc`, so `conda` may not be a function at all.
    Prepending `bin` needs no shell integration and is what the skill already
    does for the analysis environment, by absolute path.
    """
    return f'export PATH={shlex.quote(env.rstrip("/") + "/bin")}:"$PATH"'


def _q(value: Any) -> str:
    return shlex.quote(str(value))


@dataclass(frozen=True)
class MpnnParams:
    """The ProteinMPNN sampling parameters, defaulting to the skill's values."""

    num_seq_per_target: int = 16
    sampling_temps: tuple[float, ...] = (0.1, 0.2, 0.3)
    seed: int = 256
    batch_size: int = 8
    omit_aas: str = "XC"
    soluble_model: str = "v_48_020"
    halo_model: str = "halompnn_v1"

    @property
    def expected_sequences(self) -> int:
        """How many records the output FASTA should hold.

        `num_seq_per_target x n_temps + 1`: the skill's own check, and the one
        the `mpnn` stage applies before believing a round. The `+ 1` is the input
        sequence, which ProteinMPNN writes first.
        """
        return self.num_seq_per_target * len(self.sampling_temps) + 1

    @property
    def temps(self) -> str:
        """The `--sampling_temp` value, a single space-separated argv element.

        This is why parameters cannot ride in `environment`: the Slurm template
        renders `export {{name}}={{value}}` unquoted, so a value with a space
        in it breaks the script.
        """
        return " ".join(str(t) for t in self.sampling_temps)


# --- transfer specs (mode B: declare inputs/outputs) -----------------------


def push_job_spec(
    dest: str,
    files: Mapping[str, str],
    *,
    name: str = "protocol-push",
    subdirs: Iterable[str] = (),
) -> dict[str, Any]:
    """Carry files from here to `dest` on the cluster, and report what landed.

    `files` maps a path relative to `dest` to its content. The keys are ours --
    repo-relative script paths, never anything from a chat message -- which
    matters because `_stage_in` interpolates each name into the generated shell
    inside double quotes.
    """
    if not files:
        raise InvalidInput("a push job needs at least one file")
    if not dest.startswith("/"):
        raise InvalidInput(f"push destination must be absolute, not {dest!r}")
    for key in files:
        if key.startswith("/") or ".." in key.split("/"):
            raise InvalidInput(
                f"push target {key!r} must be a relative path inside {dest}"
            )

    lines = _head(f"mkdir -p {_q(dest)}")
    for sub in subdirs:
        lines.append(f"mkdir -p {_q(f'{dest}/{sub}')}")
    lines.append(": > manifest.txt")
    for key in files:
        target = f"{dest}/{key}"
        lines.append(f'mkdir -p "$(dirname {_q(target)})"')
        # Plain `cp`, never `cp -p`: see `fetch_job_spec` for why a preserved
        # mtime is a silent failure on the way back.
        lines.append(f"cp {_q(key)} {_q(target)}")
        lines.append(
            f'printf "%s %s\\n" "$(wc -c < {_q(target)})" {_q(key)} >> manifest.txt'
        )

    return {
        "name": name,
        **_bash(lines),
        "inputs": dict(files),
        "outputs": ["manifest.txt"],
        "executor": "local",
        "duration_sec": TRANSFER_DURATION_SEC,
        "resources": {"node_count": 1, "processes": 1, "gpus": 0},
    }


def fetch_job_spec(
    items: Sequence[tuple[str, str]],
    *,
    name: str = "protocol-fetch",
) -> dict[str, Any]:
    """Bring results back, each under a prefix that keeps its name unique.

    `items` pairs a destination prefix with an absolute source glob, so
    `("soluble_cpos50", ".../cpos50/seqs/*.fa")` returns
    `soluble_cpos50/<file>`. The prefix is required because the interesting
    collisions are real: every MPNN output FASTA is named after the same model,
    so six of them flattened into one directory would be one file.

    Declares `outputs: ["**"]` -- everything the job created, found by mtime
    against a marker. Hence plain `cp` and an explicit `touch`: `cp -a` or
    `cp -p` would carry the source's mtime, the file would not be "newer" than
    the marker, and the fetch would come back empty with nothing to say why.

    A pattern that matches nothing is not an error here. The caller asked for
    specific names and can see which came back, and failing the whole job would
    throw away the files that did -- `set -euo pipefail` means a non-zero
    command never reaches the staging epilogue.
    """
    if not items:
        raise InvalidInput("a fetch job needs at least one source")

    lines = _head("shopt -s nullglob")
    for prefix, pattern in items:
        if not pattern.startswith("/"):
            raise InvalidInput(
                f"fetch source must be absolute, not {pattern!r}: the command "
                f"runs in a temp directory, so a relative path resolves there"
            )
        if prefix.startswith("/") or ".." in prefix.split("/"):
            raise InvalidInput(f"fetch prefix {prefix!r} must be a relative path")
        lines.append(f"mkdir -p {_q(prefix)}")
        # The glob is deliberately unquoted -- it has to expand at the far end.
        # `touch` the copy, not the tree: a blanket `find . -exec touch` also
        # touches `wrap`'s own `.orbit-start` marker, which makes it the newest
        # file and leaves nothing for `outputs: ["**"]` to find. That failure is
        # silent -- an empty fetch with a successful exit code.
        lines.append(
            f"for __f in {pattern}; do "
            f'__d={_q(prefix)}/"$(basename "$__f")"; cp "$__f" "$__d"; touch "$__d"; '
            f"done"
        )

    return {
        "name": name,
        **_bash(lines),
        "outputs": ["**"],
        "executor": "local",
        "duration_sec": TRANSFER_DURATION_SEC,
        "resources": {"node_count": 1, "processes": 1, "gpus": 0},
    }


def install_job_spec(
    site: SiteLayout,
    inputs: ProtocolInputs,
    files: Mapping[str, str],
) -> dict[str, Any]:
    """Create the project tree and populate it with the protocol's scripts.

    Every compute stage depends on this one: Slurm fails a job whose `--chdir`
    does not exist *before* it runs anything, so `$PROJ` and its subdirectories
    have to be there first. Idempotent, so re-running a campaign is free.
    """
    return push_job_spec(
        site.proj(inputs),
        files,
        name=f"install-{inputs.name}",
        subdirs=PROJECT_SUBDIRS,
    )


def job_stats_spec(
    native_ids: Sequence[str], script: str, *, name: str = "job-stats"
) -> dict[str, Any]:
    """Run the skill's `job_stats.sh` for the lab notebook's job table.

    `sacct` is the only source of wall time, CPU time and MaxRSS -- a PSI/J
    status carries a state and an exit code and nothing else. The ids are the
    Slurm ones from `handle.meta["native_id"]`, which is also the only link
    between a submission here and a row there now that the skill's
    `slurm.%N.%j.out` filenames are gone.
    """
    if not native_ids:
        raise InvalidInput("no job ids to collect statistics for")
    for job_id in native_ids:
        if not str(job_id).replace("_", "").replace(".", "").isalnum():
            raise InvalidInput(f"job id {job_id!r} is not a scheduler id")

    argv = " ".join(_q(job_id) for job_id in native_ids)
    return {
        "name": name,
        **_bash(_head(f"bash job_stats.sh {argv} > job_stats.md")),
        "inputs": {"job_stats.sh": script},
        "outputs": ["job_stats.md"],
        "executor": "local",
        "duration_sec": TRANSFER_DURATION_SEC,
        "resources": {"node_count": 1, "processes": 1, "gpus": 0},
    }


# --- compute specs (mode A: declare neither, set `directory`) --------------


def hhblits_job_spec(
    site: SiteLayout,
    inputs: ProtocolInputs,
    *,
    model_stem: str,
    cat_cutoff: float = 10.0,
    processes: int = 4,
    memory_gib: int = 32,
    duration_sec: int = HHBLITS_DURATION_SEC,
) -> dict[str, Any]:
    """Step 6: the HHblits conservation search.

    `--cat_cutoff` is the catalytic-site shell: every residue with any atom
    within that many angstroms of any atom of a catalytic residue joins the
    conserved set at every level. The skill's default is 10.0 and the value is
    recorded in the notebook, because the first AlyFRB and SM0524 passes used
    6.0 and the two are not comparable.

    No GPU, twelve hours, and four processes with 32 GiB -- the skill's own
    `#SBATCH` header, which PSI/J would otherwise ignore.
    """
    if not inputs.cat_res:
        raise InvalidInput(
            "the conservation search needs the catalytic residues, which are "
            "confirmed at the Step 5 checkpoint"
        )
    cat_res = ",".join(str(position) for position in inputs.cat_res)
    lines = _head(
        _on_path(site.conda_aifold),
        # Provenance: which hhblits actually ran. Goes to the job's stdout, so
        # it is visible in the task chip's log tail while the search runs.
        'echo "HHBLITS version: $(hhblits -h 2>&1 | head -n 1)"',
        " ".join(
            [
                "python hhblits_search.py",
                "-i",
                _q(f"{model_stem}.pdb"),
                "-o output",
                "--cat_residues",
                _q(cat_res),
                "--cat_cutoff",
                _q(cat_cutoff),
                "-d",
                _q(site.uniref_db),
                "-c",
                _q(processes),
                "--cpu_per_job",
                _q(processes),
                "-m",
                _q(memory_gib),
            ]
        ),
    )
    return {
        "name": f"hhblits-{inputs.name}",
        **_bash(lines),
        "directory": site.sub(inputs, "conservation"),
        "resources": {
            "node_count": 1,
            "processes": processes,
            "memory": gib(memory_gib),
            "gpus": 0,
        },
        "duration_sec": duration_sec,
    }


def mpnn_job_spec(
    site: SiteLayout,
    inputs: ProtocolInputs,
    *,
    model_stem: str,
    level: int,
    halo: bool = False,
    params: MpnnParams | None = None,
    memory_gib: int = 16,
    duration_sec: int = MPNN_DURATION_SEC,
) -> dict[str, Any]:
    """Steps 8-9: one ProteinMPNN run, SolubleMPNN or HaloMPNN, at one level.

    Not `tools/proteinmpnn.py::mpnn_job_spec`, which inlines a single backbone
    and runs `protein_mpnn_run.py` directly. The protocol needs the skill's
    three-script sequence over a *folder* of PDBs with `--fixed_positions_jsonl`,
    and three sampling temperatures where that builder takes one float.

    The fixed-positions JSONL is referenced by name, not carried: it was pushed
    into `$PROJ/mpnn` by the preceding stage. Carrying it here would make this
    spec a transfer spec, which would run the whole thing in a temp directory
    and leave the analysis notebook nothing to read.
    """
    params = params or MpnnParams()
    out_dir = mpnn_out_dir(inputs, level, halo=halo)
    fixed = fixed_positions_name(inputs, level)

    model_args = (
        ["--path_to_model_weights", _q(site.mpnn_weights), "--model_name", _q(params.halo_model)]
        if halo
        else ["--use_soluble_model", "--model_name", _q(params.soluble_model)]
    )
    if halo and not site.mpnn_weights:
        raise InvalidInput(
            "HaloMPNN needs the weights directory; set the mpnn weights path"
        )

    helpers = f"{site.mpnn_path.rstrip('/')}/helper_scripts"
    lines = _head(
        _on_path(site.conda_aifold),
        *_modules(
            # Advisory, not required: these runs ask for zero GPUs, so a site
            # without the CUDA module should not fail the job over it. The
            # skill's scripts load it unguarded and get away with it because
            # `sbatch --export=ALL` carries a working module environment in.
            "module purge || true",
            "module use /projects/community/modulefiles || true",
            "module load cuda/11.7.1 || true",
        ),
        f"out={_q(out_dir)}",
        'mkdir -p "$out"',
        " ".join(
            [
                "python",
                _q(f"{helpers}/parse_multiple_chains.py"),
                "--input_path=pdb",
                '--output_path="$out/parsed_pdbs.jsonl"',
            ]
        ),
        " ".join(
            [
                "python",
                _q(f"{helpers}/assign_fixed_chains.py"),
                '--input_path="$out/parsed_pdbs.jsonl"',
                '--output_path="$out/assigned_pdbs.jsonl"',
                "--chain_list A",
            ]
        ),
        " ".join(
            [
                "python",
                _q(f"{site.mpnn_path.rstrip('/')}/protein_mpnn_run.py"),
                '--jsonl_path "$out/parsed_pdbs.jsonl"',
                '--chain_id_jsonl "$out/assigned_pdbs.jsonl"',
                "--fixed_positions_jsonl",
                _q(fixed),
                '--out_folder "$out"',
                "--num_seq_per_target",
                _q(params.num_seq_per_target),
                "--sampling_temp",
                _q(params.temps),
                "--seed",
                _q(params.seed),
                "--batch_size",
                _q(params.batch_size),
                f"--omit_AAs={_q(params.omit_aas)}",
                *model_args,
            ]
        ),
        # Fail here rather than let the analysis stage read an absent FASTA.
        f'test -s "$out/seqs/{model_stem}.fa"',
    )
    return {
        "name": f"mpnn-{out_dir}",
        **_bash(lines),
        "directory": site.sub(inputs, "mpnn"),
        "resources": {
            "node_count": 1,
            "processes": 1,
            "cpus": 1,
            "memory": gib(memory_gib),
            "gpus": 0,
        },
        "duration_sec": duration_sec,
    }


def analysis_job_spec(
    site: SiteLayout,
    inputs: ProtocolInputs,
    *,
    notebook: str = "analyze_stabilization.ipynb",
    processes: int = 1,
    cpus: int = 4,
    memory_gib: int = 16,
    duration_sec: int = ANALYSIS_DURATION_SEC,
) -> dict[str, Any]:
    """Step 10: run the scoring notebook headless.

    The notebook stays the executable. `mpnn_analysis.py` beside it is
    import-only -- no CLI -- and Steps 10b and 12 both read the notebook's
    outputs *by filename*, so a hand-written driver would mean reimplementing
    fourteen sections and then owning a second implementation that could drift
    from the one those steps expect. Only the Configuration cell is generated;
    see `protocol/notebook.py`.

    `--inplace` means the copy in `$PROJ/analysis` is mutated as it runs, which
    is why the install stage pushes a fresh copy rather than reusing one.
    """
    jupyter = f"{site.conda_analysis.rstrip('/')}/bin/jupyter"
    lines = _head(
        " ".join(
            [
                _q(jupyter),
                "nbconvert --to notebook --execute --inplace",
                # There is no kernelspec named after the environment; the skill
                # records this. The env is chosen by which jupyter runs.
                "--ExecutePreprocessor.kernel_name=python3",
                "--ExecutePreprocessor.timeout=-1",
                _q(notebook),
            ]
        )
    )
    return {
        "name": f"analysis-{inputs.name}",
        **_bash(lines),
        "directory": site.sub(inputs, "analysis"),
        "resources": {
            "node_count": 1,
            "processes": processes,
            "cpus": cpus,
            "memory": gib(memory_gib),
            "gpus": 0,
        },
        "duration_sec": duration_sec,
    }


def af3_job_spec(
    site: SiteLayout,
    inputs: ProtocolInputs,
    *,
    design: str,
    cpus: int = 8,
    memory_gib: int = 64,
    gpus: int = 1,
    duration_sec: int = AF3_DURATION_SEC,
) -> dict[str, Any]:
    """Step 11: AlphaFold3 on one selected design.

    One job per design rather than one job running the skill's `02_af3_job.sh`
    loop. That script `sbatch`es from inside itself, and its children would be
    invisible to PSI/J -- no task chip, no log tail, no `native_id`, nothing to
    give `sacct`. The loop's "at most 100 queued" intent belongs in the stage
    machine, where it can see the whole batch.

    `--constraint` and `--gres` have no PSI/J resource field, so they ride in
    `custom_attributes`, keyed `slurm.<flag>`. That key has to sit at the spec's
    top level: the far end reads only `duration`, `queue_name`, `account` and
    `reservation_id` out of `attributes` and silently drops the rest.
    """
    if not design or "/" in design or design.startswith("."):
        raise InvalidInput(f"design name {design!r} is not a usable json stem")
    if not site.af3_modules:
        raise InvalidInput(
            "AlphaFold3 needs the site's module lines; set the af3 modules"
        )

    scratch = site.af3_scratch(inputs)
    lines = _head(
        *_modules("module purge", "module use /projects/community/modulefiles", *site.af3_modules),
        f"IN={_q(f'{scratch}/af_input')}",
        f"OUT={_q(f'{scratch}/af_output')}",
        'mkdir -p "$OUT"',
        f'test -s "$IN"/{_q(design + ".json")}',
        " ".join(
            [
                "apptainer exec",
                '-B "$IN":/root/af_input',
                '-B "$OUT":/root/af_output',
                '-B "$ALPHAFOLD_MODELWEIGHTS":/root/models',
                '-B "$ALPHAFOLD_DATA_PATH":/root/public_databases',
                "--pwd /app/alphafold --nv",
                f'"$CONTAINERDIR"/{_q(site.af3_image)}',
                "python run_alphafold.py",
                f"--json_path=/root/af_input/{design}.json",
                "--db_dir=/root/public_databases",
                "--model_dir=/root/models",
                "--output_dir=/root/af_output",
            ]
        ),
    )
    spec: dict[str, Any] = {
        "name": f"af3-{design}",
        **_bash(lines),
        "directory": site.sub(inputs, "af3"),
        "resources": {
            "node_count": 1,
            "processes": 1,
            "cpus": cpus,
            "memory": gib(memory_gib),
            "gpus": gpus,
        },
        "duration_sec": duration_sec,
    }
    if site.gpu_queue:
        spec["queue"] = site.gpu_queue
    custom = {}
    if gpus > 0:
        custom["slurm.gres"] = f"gpu:{gpus}"
    if site.gpu_constraint:
        custom["slurm.constraint"] = site.gpu_constraint
    if custom:
        spec["custom_attributes"] = custom
    return spec


# --- the names every stage agrees on --------------------------------------


def mpnn_out_dir(inputs: ProtocolInputs, level: int, *, halo: bool = False) -> str:
    """`cpos50`, `cpos50_halo`, `cposliu_50`... as the skill names them.

    One function because four things have to agree on these strings: the MPNN
    job that writes the directory, the fetch that reads it, the notebook's
    `INPUTS` list, and the alignment page's `targets.json`.
    """
    return f"cpos{inputs.tag}{level}{'_halo' if halo else ''}"


def fixed_positions_name(inputs: ProtocolInputs, level: int) -> str:
    """`cpos_50_CD.jsonl` / `cpos_liu_50_CD.jsonl`, the MPNN input."""
    return f"cpos_{inputs.tag}{level}_CD.jsonl"


def conservation_jsonl_name(inputs: ProtocolInputs, level: int, model_stem: str) -> str:
    """What `hhblits_search.py` writes, which embeds the model name."""
    return f"{model_stem}_cpos_{inputs.tag}{level}.jsonl"
