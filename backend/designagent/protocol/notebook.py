"""The scoring notebook's Configuration cell, generated.

Step 10 runs `analyze_stabilization.ipynb` headless. The notebook stays the
executable and only this one cell is written, which is what the skill itself
says to do ("edit only the Configuration cell").

The alternative -- a command-line driver around `mpnn_analysis.py` -- was
considered and rejected. That module is import-only, so a driver would mean
reimplementing fourteen notebook sections (k-medoids, t-SNE, positional entropy,
the consensus, the `.pml`, `<name>_selected.fa`, `<name>_tsne_coords.csv`), and
then owning a second implementation of them. Steps 10b and 12 read the
notebook's outputs *by filename*, so the two would have to agree forever.
Generating 40 lines of assignments is the smaller commitment.

`sed` could not do this job either: the cell's `INPUTS` list carries the
unselected conservation levels as **commented-out lines**, and "uncomment the
cpos30 pair" is not a substitution.

The cell is replaced **by marker, not by index**. `patch_notebook` finds the
cell containing `MARKER` and fails loudly if it is absent or ambiguous. An
index-only patch would silently rewrite the wrong cell the first time anyone
inserts a cell above it, and the notebook would then run with someone else's
configuration.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from .inputs import InvalidInput, ProtocolInputs
from .specs import fixed_positions_name, mpnn_out_dir

#: The first line of the Configuration cell, and how it is found.
MARKER = "# ── Target ─"

#: The label each input set gets. `mpnn_analysis` colours `HaloMPNN` and
#: `SolubleMPNN` with the HaloMPNN paper's colours and anything else from
#: viridis, so these names are load-bearing for the figures.
HALO_LABEL = "HaloMPNN"
SOLUBLE_LABEL = "SolubleMPNN"


def _rule(title: str, width: int = 78) -> str:
    """A section comment in the notebook's own style."""
    head = f"# ── {title} "
    return head + "─" * max(0, width - len(head))


def input_sets(
    inputs: ProtocolInputs, levels: Sequence[int], *, halo: bool = True, soluble: bool = True
) -> list[tuple[str, str]]:
    """`(label, relative path)` for each redesign FASTA, in the notebook's order.

    HaloMPNN first at each level, matching the skill's own cell, because the
    figures put the two side by side and the order sets the legend.
    """
    sets: list[tuple[str, str]] = []
    for level in levels:
        if halo:
            sets.append(
                (
                    f"{HALO_LABEL}_cpos{inputs.tag}{level}",
                    f"mpnn/{mpnn_out_dir(inputs, level, halo=True)}/seqs",
                )
            )
        if soluble:
            sets.append(
                (
                    f"{SOLUBLE_LABEL}_cpos{inputs.tag}{level}",
                    f"mpnn/{mpnn_out_dir(inputs, level)}/seqs",
                )
            )
    if not sets:
        raise InvalidInput("the analysis needs at least one redesign set")
    return sets


def configuration_cell(
    inputs: ProtocolInputs,
    *,
    proj: str,
    model_stem: str,
    levels: Sequence[int],
    analysis_level: int,
    n_select: int = 10,
    halo: bool = True,
    soluble: bool = True,
    kmedoids_seed: int = 42,
    tsne_seed: int = 42,
) -> str:
    """Render the cell.

    `levels` are the sets to analyse together; `analysis_level` picks which
    level's fixed-position JSONL describes them. The skill's default is the
    cpos50 pair, and it warns that each extra set adds `n_select` AlphaFold3
    jobs -- an hour of GPU each -- so widening this is the user's call.
    """
    if analysis_level not in levels:
        raise InvalidInput(
            f"the fixed positions are read from level {analysis_level}, which is "
            f"not among the levels being analysed ({', '.join(map(str, levels))})"
        )
    if not inputs.cat_res:
        raise InvalidInput("the analysis cell needs the confirmed catalytic residues")

    sets = input_sets(inputs, levels, halo=halo, soluble=soluble)
    rows = "\n".join(
        f'    ({label!r}, BASE / "{path}/{model_stem}.fa"),' for label, path in sets
    )
    catalytic = ", ".join(str(position) for position in inputs.cat_res)

    return "\n".join(
        [
            _rule("Target"),
            f"TARGET = {inputs.name!r}",
            f"BASE   = Path({proj!r})",
            "",
            _rule("Redesign FASTAs: (label, path), one per model being compared"),
            "INPUTS = [",
            rows,
            "]",
            "",
            _rule("The backbone the designs were made on"),
            f'PDB = BASE / "mpnn/pdb/{model_stem}.pdb"',
            "",
            _rule("Fixed (non-redesigned) positions, 1-based"),
            f'FIXED_POSITIONS_JSONL = BASE / "mpnn/{fixed_positions_name(inputs, analysis_level)}"',
            "FIXED_POSITIONS_1IDX  = None",
            "",
            _rule("Catalytic / active-site residues, 1-based"),
            f"CATALYTIC_POSITIONS_1IDX = [{catalytic}]",
            "",
            _rule("k-medoids"),
            f"N_SELECT = {int(n_select)}",
            'KMEDOIDS_FIXED_MODE = "wt_closest"',
            "KMEDOIDS_USE_SCORES   = False",
            "KMEDOIDS_SCORE_RADIUS = 0.05",
            f"KMEDOIDS_SEED = {int(kmedoids_seed)}",
            f"TSNE_SEED     = {int(tsne_seed)}",
            "",
            _rule("PyMOL: which positions to paint orange"),
            'PYMOL_DESIGN_POSITIONS = "allowed"',
            "",
            _rule("Output"),
            'OUTDIR = Path(f"results_{TARGET}")',
            "OUTDIR.mkdir(parents=True, exist_ok=True)",
            'print(f"{TARGET}: {len(INPUTS)} input set(s) -> {OUTDIR.resolve()}")',
            "",
        ]
    )


def find_configuration_cell(notebook: dict[str, Any], marker: str = MARKER) -> int:
    """The index of the cell holding `marker`, or a loud failure.

    Ambiguity is an error too: two matching cells means the notebook changed
    shape, and picking either one would be a guess about which configuration
    runs.
    """
    hits = [
        index
        for index, cell in enumerate(notebook.get("cells") or [])
        if cell.get("cell_type") == "code" and marker in "".join(cell.get("source") or [])
    ]
    if not hits:
        raise InvalidInput(
            f"the notebook has no Configuration cell: no code cell contains "
            f"{marker!r}. It was renamed or removed, and patching by position "
            f"would configure the wrong cell"
        )
    if len(hits) > 1:
        raise InvalidInput(
            f"the notebook has {len(hits)} cells containing {marker!r} "
            f"(indices {hits}); which one configures the run is ambiguous"
        )
    return hits[0]


def patch_notebook(notebook_json: str, cell_source: str, *, marker: str = MARKER) -> str:
    """Return the notebook with its Configuration cell replaced.

    Also clears that cell's outputs and execution count, so a notebook pushed
    to the cluster never carries a previous campaign's printed configuration
    next to this one's code.
    """
    try:
        notebook = json.loads(notebook_json)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"the notebook is not valid JSON: {exc.msg}") from None

    index = find_configuration_cell(notebook, marker)
    cell = notebook["cells"][index]
    cell["source"] = _as_source_lines(cell_source)
    cell["outputs"] = []
    cell["execution_count"] = None
    return json.dumps(notebook, indent=1) + "\n"


def _as_source_lines(text: str) -> list[str]:
    """Split as nbformat wants it: every line keeps its newline but the last."""
    lines = text.splitlines(keepends=True)
    return lines or [""]


def selected_fasta_name(inputs: ProtocolInputs) -> str:
    """`<name>_selected.fa`, the notebook output AlphaFold3 consumes."""
    return f"{inputs.name}_selected.fa"


def results_dir(inputs: ProtocolInputs) -> str:
    """`results_<name>/`, where the notebook puts everything."""
    return f"results_{inputs.name}"


def analysis_outputs(inputs: ProtocolInputs) -> tuple[str, ...]:
    """The small files worth fetching after the notebook runs.

    Deliberately not the plots: a PNG is close to the 1 MiB per-file ceiling and
    several would exhaust the 4 MiB job budget, so they stay on the cluster and
    the user gets a path. The skill's "pull summary plots locally" is the one
    instruction this cannot honour.
    """
    return (
        selected_fasta_name(inputs),
        f"{inputs.name}_tsne_coords.csv",
        f"{inputs.name}_pymol.pml",
    )


def read_fasta(text: str) -> list[tuple[str, str]]:
    """`(header, sequence)` pairs, headers without the `>`.

    The notebook's `<name>_selected.fa` is what AlphaFold3 is run on, so this is
    the join between Step 10's output and Step 11's input.
    """
    records: list[tuple[str, str]] = []
    header: str | None = None
    chunks: list[str] = []

    def flush() -> None:
        if header is None:
            return
        sequence = "".join(chunks).strip().replace(" ", "")
        if sequence:
            records.append((header, sequence))

    for line in text.splitlines():
        if line.startswith(">"):
            flush()
            header, chunks = line[1:].strip(), []
        elif header is not None:
            chunks.append(line.strip())
    flush()
    if not records:
        raise InvalidInput("the selected-designs FASTA holds no sequences")
    return records
