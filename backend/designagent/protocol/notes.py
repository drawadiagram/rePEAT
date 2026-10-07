"""The lab notebook: `NOTEBOOK.md`, appended to after every stage.

The skill's format, and its one hard rule: **append, never rewrite history.** A
campaign runs over days, a stage can fail and be retried, and a notebook that
quietly loses the failed attempt is worse than no notebook -- it makes a run look
cleaner than it was. So every function here produces a *block* to add, and
`append_entry` is the only way the document grows.

Stored as a `markdown` artifact under a fixed id, so re-adding it replaces the
file in place while `merge_artifacts` keeps one reference in state. That is what
makes "append across turns" work without the document appearing a dozen times in
the artifact pane.

Nothing here invents a number. Wall time, CPU time and MaxRSS come from `sacct`
via the skill's own `job_stats.sh` (`protocol/specs.py::job_stats_spec`), because
a PSI/J status carries a state and an exit code and nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .inputs import ProtocolInputs
from .site import SiteLayout

#: The `### Jobs` table's columns, in the skill's order.
JOB_COLUMNS = ("JobID", "name", "state", "wall", "CPU time", "MaxRSS", "node")


def _stamp(when: datetime | None = None) -> str:
    """`YYYY-MM-DD HH:MM`, as the skill's entry heading wants it."""
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M")


def _table(rows: Iterable[Sequence[str]], header: Sequence[str]) -> list[str]:
    out = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return out


def header(
    inputs: ProtocolInputs,
    site: SiteLayout,
    *,
    method: str,
    cat_cutoff: float = 10.0,
    offset_note: str = "",
    when: datetime | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """The notebook's opening block: the inputs and the confirmed parameters.

    Written once, at intake. It records the two things a reader cannot recover
    from the files afterwards: which conservation method was chosen, and the
    signal-peptide offset that every residue number in the rest of the document
    is relative to.
    """
    rows = [
        ("NAME", inputs.name),
        ("UNIPROT", inputs.uniprot),
        ("DOMAINS (trimmed)", f"`{inputs.domains}`"),
        ("chain length", str(inputs.chain_length)),
        ("CAT_RES (trimmed)", ", ".join(str(p) for p in inputs.cat_res) or "_pending_"),
        ("conservation method", f"`{method}` (TAG=`{inputs.tag}`)"),
        ("conservation levels", ", ".join(str(level) for level in inputs.levels)),
        ("catalytic shell", f"{cat_cutoff} A (`--cat_cutoff`)"),
        ("netid", inputs.netid),
        ("PROJ", f"`{site.proj(inputs)}`"),
        ("AF3 scratch", f"`{site.af3_scratch(inputs)}`"),
    ]
    for key, value in (extra or {}).items():
        rows.append((key, str(value)))

    lines = [
        f"# {inputs.name} redesign notebook",
        "",
        f"Started {_stamp(when)} UTC.",
        "",
        *_table(rows, ("input", "value")),
    ]
    if offset_note:
        lines += [
            "",
            "## Signal peptide",
            "",
            offset_note,
        ]
    return "\n".join(lines) + "\n"


def entry(
    step: str,
    title: str,
    *,
    goal: str = "",
    commands: Sequence[str] = (),
    result: str = "",
    files: Sequence[str] = (),
    notes: str = "",
    jobs: Sequence[Mapping[str, str]] = (),
    when: datetime | None = None,
) -> str:
    """One stage's entry, in the skill's format.

    `commands` are recorded *as run*. For this agent that means the generated
    script, not a hand-typed line: the whole point of the record is that someone
    can see what the far end was actually asked to do, and the two differ (see
    `protocol/specs.py` on the skill's dead `#SBATCH` headers).
    """
    lines = [f"## {_stamp(when)} — Step {step}: {title}", ""]
    if goal:
        lines += [f"**Goal:** {goal}", ""]
    if commands:
        lines += ["**Commands:**", "", "```bash"]
        lines += list(commands)
        lines += ["```", ""]
    if result:
        lines += [f"**Result:** {result}", ""]
    if files:
        lines += ["**Files:**", ""]
        lines += [f"- `{name}`" for name in files]
        lines += [""]
    if notes:
        lines += [f"**Notes/decisions:** {notes}", ""]
    if jobs:
        lines += ["### Jobs", ""]
        lines += _table(
            [[job.get(column, "") for column in JOB_COLUMNS] for job in jobs],
            JOB_COLUMNS,
        )
        lines += [""]
    return "\n".join(lines)


def jobs_from_stats(markdown: str) -> list[dict[str, str]]:
    """Parse `job_stats.sh`'s output rows back into dicts.

    The script already emits markdown table rows, so this reads its own format
    rather than re-deriving anything: `| id | name | state | wall | cpu | rss |
    node |`.
    """
    jobs: list[dict[str, str]] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != len(JOB_COLUMNS):
            continue
        if cells[0] in ("JobID", "---"):
            continue
        jobs.append(dict(zip(JOB_COLUMNS, cells)))
    return jobs


def failure_note(stage: str, errors: Sequence[str]) -> str:
    """What to record when a stage's jobs did not all succeed.

    Failures and retries go in the notebook too; the skill says so explicitly.
    """
    if not errors:
        return ""
    shown = "; ".join(errors[:3])
    more = f" (and {len(errors) - 3} more)" if len(errors) > 3 else ""
    return f"{stage} did not complete cleanly: {shown}{more}"


def append_entry(existing: str, block: str) -> str:
    """Add a block to the document, separated by a blank line.

    The only mutation this module offers, because it is the only one the skill
    permits.
    """
    base = existing.rstrip("\n")
    if not base:
        return block if block.endswith("\n") else block + "\n"
    return f"{base}\n\n{block.rstrip(chr(10))}\n"
