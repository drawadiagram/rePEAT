"""AlphaFold3 inputs and results, as pure functions.

The skill reaches Step 11 through three shell scripts, and all three carry
hardcoded values it tells you to `sed`-edit on the copies:
`01_af3_json_monomer.sh` has one netid's scratch path, `02_af3_job.sh` has the
netid again plus its own `sbatch` loop, and `01_af3_json_wrapper.py` is a
general-purpose JSON builder being used for the monomer case. For a monomer that
JSON is five keys, so it is built here instead -- which removes the sed step, the
hardcoded paths, and a nested `sbatch` whose children PSI/J could not see.

What is *not* reimplemented is the container invocation in
`02_af3_batch_input.sh`; that is re-expressed as a job spec in
`protocol/specs.py::af3_job_spec`, resources and all.

Reading results is deliberately filesystem-shaped rather than handle-shaped. See
`graph/nodes/protocol.py` for why: a backend restart loses every in-flight
handle, but the output directory is still there, so recovery is a re-read rather
than a re-attach.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

from .inputs import InvalidInput

#: Everything outside this set collapses to a single underscore. The result is a
#: filename, a JSON `name` and an argv element, so nothing else may survive.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: What AlphaFold3 writes per design, and the only file worth bringing back: a
#: few hundred bytes of scores next to gigabytes of structures.
SUMMARY_SUFFIX = "_summary_confidences.json"


def design_name(header: str, model_stem: str) -> str:
    """A filesystem-safe job name for one design.

    `clean_name` from `01_af3_json_monomer.sh`, as a function. It has to cope
    with the raw ProteinMPNN header (`T=0.1, sample=3, score=...`) and with the
    notebook's curated one (`n1|HaloMPNN_cpos50_d_9|src=...`) alike.
    """
    cleaned = _UNSAFE.sub("_", header).strip("_")
    if not cleaned:
        raise InvalidInput(f"design header {header!r} has nothing usable in it")
    return f"{model_stem}_{cleaned}_af3"


def design_names(headers: Iterable[str], model_stem: str) -> list[str]:
    """One name per design, refusing a collision rather than overwriting.

    Two headers that differ only in punctuation clean to the same name, and the
    name is a filename: the second would silently replace the first, and the
    campaign would fold nine designs while reporting ten.
    """
    names: list[str] = []
    for header in headers:
        name = design_name(header, model_stem)
        if name in names:
            raise InvalidInput(
                f"design header {header!r} cleans to {name!r}, which another "
                f"design already uses; two designs would share one output file"
            )
        names.append(name)
    return names


def monomer_json(name: str, sequence: str, *, seeds: Sequence[int] = (1,)) -> str:
    """The AlphaFold3 input JSON for a single protein chain.

    The shape `01_af3_json_wrapper.py` emits for
    `-s 1 -d alphafold3 -v 1 -seqs "protein,A,<seq>"`.
    """
    if not sequence or not sequence.isalpha():
        raise InvalidInput(
            f"design {name!r} has no usable sequence: AlphaFold3 wants one-letter "
            f"residues and nothing else"
        )
    if not seeds:
        raise InvalidInput("AlphaFold3 needs at least one model seed")
    payload = {
        "name": name,
        "sequences": [{"protein": {"id": "A", "sequence": sequence.upper()}}],
        "modelSeeds": [int(seed) for seed in seeds],
        "dialect": "alphafold3",
        "version": 1,
    }
    return json.dumps(payload, indent=4) + "\n"


def input_files(
    records: Sequence[tuple[str, str]], model_stem: str, *, seeds: Sequence[int] = (1,)
) -> dict[str, str]:
    """`{<design>.json: content}` for every selected design.

    The mapping a `push_job_spec` carries into `<scratch>/af_input`.
    """
    names = design_names([header for header, _ in records], model_stem)
    return {
        f"{name}.json": monomer_json(name, sequence, seeds=seeds)
        for name, (_, sequence) in zip(names, records)
    }


def read_summary(text: str) -> dict[str, float]:
    """The scores worth reporting out of one `*_summary_confidences.json`.

    Only the scalars. AlphaFold3 also writes per-chain and per-pair matrices,
    which are the bulk of the file and say nothing extra about a monomer.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"AlphaFold3 summary is not valid JSON: {exc.msg}") from None
    if not isinstance(payload, Mapping):
        raise InvalidInput("AlphaFold3 summary is not an object")

    out: dict[str, float] = {}
    for key in ("ptm", "iptm", "ranking_score", "fraction_disordered", "has_clash"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
        elif isinstance(value, bool):
            out[key] = float(value)
    if "ptm" not in out and "ranking_score" not in out:
        raise InvalidInput(
            "AlphaFold3 summary carries neither ptm nor ranking_score; it is "
            "not a summary_confidences file"
        )
    return out


def collect_summaries(files: Mapping[str, bytes | str]) -> dict[str, Any]:
    """Turn fetched summary files into a per-design table and a completion count.

    Takes what a fetch returned, keyed by the path it came back under, so a file
    that failed to parse is *named* rather than dropped -- a design whose scores
    cannot be read is not the same as a design that has not finished.
    """
    designs: dict[str, dict[str, float]] = {}
    problems: list[str] = []
    for path, blob in sorted(files.items()):
        if not path.endswith(SUMMARY_SUFFIX):
            continue
        text = blob.decode("utf-8", errors="replace") if isinstance(blob, bytes) else blob
        name = path.rsplit("/", 1)[-1][: -len(SUMMARY_SUFFIX)]
        try:
            designs[name] = read_summary(text)
        except InvalidInput as exc:
            problems.append(f"{name}: {exc}")
    return {
        "designs": designs,
        "n_done": len(designs),
        "problems": problems,
        "best": max(
            designs,
            key=lambda n: designs[n].get("ranking_score", designs[n].get("ptm", 0.0)),
            default="",
        ),
    }


def output_globs(scratch: str) -> list[tuple[str, str]]:
    """Fetch items for the summaries of every finished design.

    One glob over the output tree. AlphaFold3 writes a directory per design, and
    which of them exist *is* the progress report -- so this is also the poll.
    """
    return [("af3", f"{scratch.rstrip('/')}/af_output/*/*{SUMMARY_SUFFIX}")]
