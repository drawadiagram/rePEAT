"""Render session summaries as Markdown and .docx.

The Markdown is the source of truth; the .docx is built from the same structured
summary rather than by parsing the Markdown back.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any


def summary_markdown(
    *,
    goal: str,
    reference: dict,
    key_metric: dict,
    lead: dict,
    ensemble: list[dict],
    summary_text: str,
    related: list[dict] | None = None,
    tasks: list[dict] | None = None,
) -> str:
    """A session summary suitable for the artifact pane and for download."""
    metric = key_metric.get("name", "")
    direction = key_metric.get("direction", "max")
    lines: list[str] = []

    title = reference.get("name") or reference.get("pdb_id") or "Design session"
    lines.append(f"# Design summary: {title}")
    lines.append("")
    lines.append(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")

    if goal:
        lines += ["## Goal", "", goal, ""]

    if summary_text:
        lines += ["## Interpretation", "", summary_text, ""]

    # --- reference ---
    if reference:
        lines += ["## Reference design", ""]
        for label, key in (
            ("PDB", "pdb_id"),
            ("UniProt", "uniprot_id"),
            ("Organism", "organism"),
            ("Length", "length"),
            ("Function", "function"),
        ):
            value = reference.get(key)
            if value:
                lines.append(f"- **{label}:** {value}")
        seq = reference.get("sequence") or ""
        if seq:
            lines.append(f"- **Sequence ({len(seq)} aa):** `{_elide(seq, 80)}`")
        lines.append("")

    # --- metric ---
    if metric:
        target = key_metric.get("target")
        goal_text = f"{'maximize' if direction == 'max' else 'minimize'} {metric}"
        if target is not None:
            goal_text += f" (target {target})"
        lines += ["## Key metric", "", f"- {goal_text}"]
        if key_metric.get("description"):
            lines.append(f"- {key_metric['description']}")
        lines.append("")

    # --- lead ---
    if lead:
        lines += ["## Lead design", ""]
        lines.append(f"- **ID:** `{lead.get('design_id', '')}`")
        if lead.get("mutations"):
            lines.append(f"- **Mutations:** {', '.join(lead['mutations'])}")
        for name, value in (lead.get("metrics") or {}).items():
            lines.append(f"- **{name}:** {_fmt(value)}")
        if lead.get("sequence"):
            lines.append(f"- **Sequence:** `{_elide(lead['sequence'], 80)}`")
        lines.append("")

    # --- ensemble ---
    if ensemble:
        metric_names = _metric_columns(ensemble, metric)
        lines += ["## Ensemble", ""]
        header = ["Rank", "Design", "Mutations", *metric_names]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for i, design in enumerate(ensemble, start=1):
            row = [
                str(i),
                f"`{design.get('design_id', '')}`",
                ", ".join(design.get("mutations", [])) or "—",
                *[_fmt((design.get("metrics") or {}).get(m)) for m in metric_names],
            ]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    # --- prior art ---
    if related:
        lines += ["## Related past designs", ""]
        for item in related:
            lines.append(
                f"- `{item['design_id']}` from campaign `{item['campaign_id']}`: "
                f"{item['metric']} = {_fmt(item['value'])}"
            )
        lines.append("")

    # --- provenance ---
    if tasks:
        lines += ["## Tasks run", ""]
        for task in tasks:
            state = task.get("state", "")
            note = f" — {task['error']}" if task.get("error") else ""
            lines.append(f"- `{task.get('name', '')}` ({task.get('interface', '')}): {state}{note}")
        lines.append("")

    if reference.get("literature"):
        lines += ["## Literature", ""]
        for ref in reference["literature"]:
            cite = ref.get("title", "untitled")
            year = f" ({ref['year']})" if ref.get("year") else ""
            ident = ref.get("doi") or ref.get("id", "")
            lines.append(f"- {cite}{year} — {ident}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def summary_docx(
    *,
    goal: str,
    reference: dict,
    key_metric: dict,
    lead: dict,
    ensemble: list[dict],
    summary_text: str,
    related: list[dict] | None = None,
    tasks: list[dict] | None = None,
) -> bytes:
    """The same summary as a Word document."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    title = reference.get("name") or reference.get("pdb_id") or "Design session"
    doc.add_heading(f"Design summary: {title}", level=0)
    stamp = doc.add_paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    stamp.runs[0].italic = True

    if goal:
        doc.add_heading("Goal", level=1)
        doc.add_paragraph(goal)

    if summary_text:
        doc.add_heading("Interpretation", level=1)
        for block in summary_text.split("\n\n"):
            if block.strip():
                doc.add_paragraph(block.strip())

    if reference:
        doc.add_heading("Reference design", level=1)
        for label, key in (
            ("PDB", "pdb_id"),
            ("UniProt", "uniprot_id"),
            ("Organism", "organism"),
            ("Length", "length"),
            ("Function", "function"),
        ):
            if reference.get(key):
                doc.add_paragraph(f"{label}: {reference[key]}", style="List Bullet")
        if reference.get("sequence"):
            seq = reference["sequence"]
            doc.add_paragraph(f"Sequence ({len(seq)} aa):", style="List Bullet")
            mono = doc.add_paragraph()
            run = mono.add_run(_wrap(seq, 60))
            run.font.name = "Courier New"
            run.font.size = Pt(8)

    if key_metric.get("name"):
        doc.add_heading("Key metric", level=1)
        direction = "maximize" if key_metric.get("direction", "max") == "max" else "minimize"
        doc.add_paragraph(f"{direction} {key_metric['name']}", style="List Bullet")
        if key_metric.get("description"):
            doc.add_paragraph(key_metric["description"], style="List Bullet")

    if lead:
        doc.add_heading("Lead design", level=1)
        doc.add_paragraph(f"ID: {lead.get('design_id', '')}", style="List Bullet")
        if lead.get("mutations"):
            doc.add_paragraph(
                f"Mutations: {', '.join(lead['mutations'])}", style="List Bullet"
            )
        for name, value in (lead.get("metrics") or {}).items():
            doc.add_paragraph(f"{name}: {_fmt(value)}", style="List Bullet")

    if ensemble:
        doc.add_heading("Ensemble", level=1)
        metric_names = _metric_columns(ensemble, key_metric.get("name", ""))
        table = doc.add_table(rows=1, cols=3 + len(metric_names))
        table.style = "Light Grid Accent 1"
        headers = ["Rank", "Design", "Mutations", *metric_names]
        for cell, text in zip(table.rows[0].cells, headers):
            cell.text = text
        for i, design in enumerate(ensemble, start=1):
            cells = table.add_row().cells
            values = [
                str(i),
                design.get("design_id", ""),
                ", ".join(design.get("mutations", [])) or "-",
                *[_fmt((design.get("metrics") or {}).get(m)) for m in metric_names],
            ]
            for cell, text in zip(cells, values):
                cell.text = text

    if related:
        doc.add_heading("Related past designs", level=1)
        for item in related:
            doc.add_paragraph(
                f"{item['design_id']} (campaign {item['campaign_id']}): "
                f"{item['metric']} = {_fmt(item['value'])}",
                style="List Bullet",
            )

    if tasks:
        doc.add_heading("Tasks run", level=1)
        for task in tasks:
            note = f" - {task['error']}" if task.get("error") else ""
            doc.add_paragraph(
                f"{task.get('name', '')} ({task.get('interface', '')}): "
                f"{task.get('state', '')}{note}",
                style="List Bullet",
            )

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# --- helpers ---------------------------------------------------------------


def _metric_columns(designs: list[dict], primary: str) -> list[str]:
    """Metric columns with the key metric first, then the rest alphabetically."""
    names = {m for d in designs for m in (d.get("metrics") or {})}
    ordered = [primary] if primary in names else []
    return ordered + sorted(names - set(ordered))


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.3g}"
    return str(value)


def _elide(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… (+{len(text) - limit})"


def _wrap(text: str, width: int) -> str:
    return "\n".join(text[i : i + width] for i in range(0, len(text), width))
