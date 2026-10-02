"""Interpreter: summarizes the campaign and flags relevant past designs.

Queries the data lake for comparable work from other campaigns, writes
`design_summary`, and registers the Markdown and .docx artifacts. It also stages
a tier 3 golden set when the round produced anything worth training on.
"""

from __future__ import annotations

import logging

from langgraph.types import Command

from ...artifacts.render import summary_docx, summary_markdown
from ...lake.golden import CurationRules
from ...llm import complete
from ..deps import Deps, llm_caveat, status
from ..state import DesignState

log = logging.getLogger(__name__)

SUMMARY_SYSTEM = """You write the interpretation section of a protein design report.

Explain, in 2-4 short paragraphs:
  - what was attempted and how success was measured
  - what the numbers actually show, naming design ids and values
  - how the lead compares to the reference and to any related past designs
  - the most useful next experiment

Be precise and sober. Use only the data given. Do not invent metrics, residues
or structures. If the evidence is weak, say so."""


def make_interpreter(deps: Deps):
    async def interpreter(state: DesignState) -> Command:
        session_id = state.get("session_id", "")
        campaign_id = deps.campaign_id(state)
        reference = state.get("reference_design") or {}
        key_metric = state.get("key_metric") or {}
        metric_name = key_metric.get("name", "")
        direction = key_metric.get("direction", "max")
        lead = state.get("lead_design") or {}
        ensemble = list(state.get("ensemble") or [])
        goal = state.get("goal", "")

        status("Summarizing the session…", node="interpreter")

        # --- prior art from the lake ---
        related: list[dict] = []
        if metric_name:
            try:
                related = deps.history.related_past_designs(
                    campaign_id, metric_name, direction, limit=5
                )
            except Exception as exc:
                log.warning("prior-art query failed: %s", exc)

        try:
            report = deps.history.campaign_report(campaign_id)
        except Exception as exc:
            log.warning("campaign report failed: %s", exc)
            report = {"tasks": [], "designs": [], "scores": [], "analyses": []}
        tasks = report.get("tasks", [])

        # --- the narrative ---
        summary_text = ""
        caveats: list[str] = []
        if deps.settings.llm_available:
            summary_text = (
                await complete(
                    SUMMARY_SYSTEM,
                    _context(state, related, report),
                    settings=deps.settings,
                    on_fallback=llm_caveat(deps, caveats),
                    stream=True,   # the session summary is the reply itself
                    max_tokens=1200,
                )
                or ""
            )
        reply_source = (
            "interpreter:llm" if summary_text else "interpreter:_rule_based_summary"
        )
        if not summary_text:
            summary_text = _rule_based_summary(
                goal, reference, key_metric, lead, ensemble, related, tasks
            )

        # --- artifacts ---
        payload = {
            "goal": goal,
            "reference": reference,
            "key_metric": key_metric,
            "lead": lead,
            "ensemble": ensemble,
            "summary_text": summary_text,
            "related": related,
            "tasks": tasks,
        }
        title = (
            f"{reference.get('pdb_id') or reference.get('name') or 'Design'} session summary"
        )

        artifacts = []
        try:
            markdown = summary_markdown(**payload)
            artifacts.append(
                deps.artifacts.add(
                    "markdown", title, content=markdown, session_id=session_id
                )
            )
        except Exception as exc:
            log.warning("markdown render failed: %s", exc)

        try:
            docx_bytes = summary_docx(**payload)
            artifacts.append(
                deps.artifacts.add(
                    "docx", f"{title} (.docx)", content=docx_bytes, session_id=session_id
                )
            )
        except Exception as exc:
            log.warning("docx render failed: %s", exc)

        # A ranked table is cheap and makes the ensemble sortable in the UI.
        if ensemble:
            artifacts.append(
                deps.artifacts.add(
                    "table",
                    "Ensemble",
                    content={
                        "metric": metric_name,
                        "direction": direction,
                        "rows": [
                            {
                                "rank": i + 1,
                                "design_id": d.get("design_id"),
                                "mutations": d.get("mutations", []),
                                **(d.get("metrics") or {}),
                            }
                            for i, d in enumerate(ensemble)
                        ],
                    },
                    session_id=session_id,
                )
            )

        # --- tier 3: stage a golden set when there is anything to train on ---
        if metric_name and ensemble:
            try:
                staged = deps.history.stage_golden_set(
                    campaign_id,
                    name=_slug(reference.get("pdb_id") or campaign_id),
                    rules=CurationRules(
                        metric=metric_name,
                        direction=direction,
                        top_k=max(5, len(ensemble)),
                        require_structure=False,
                        dedupe_sequences=True,
                    ),
                    notes=f"goal: {goal}",
                )
                if staged.get("n_rows"):
                    log.info(
                        "staged golden set %s with %d rows", staged["id"], staged["n_rows"]
                    )
            except Exception as exc:
                log.warning("staging a golden set failed: %s", exc)

        # Non-fatal problems are told to the user, not just logged. `caveats` is
        # this node's own: a summary that silently came from rules because the key
        # was rejected would otherwise look like a normal answer.
        warnings = list(state.get("warnings") or []) + caveats
        reply = summary_text
        if warnings:
            reply += "\n\n**Caveats from this run:**\n" + "\n".join(
                f"- {w}" for w in warnings[:5]
            )

        return Command(
            goto="__end__",
            update={
                "design_summary": summary_text,
                "reply_source": reply_source,
                "artifacts": artifacts,
                "messages": [{"role": "assistant", "content": reply}],
                "status": "",
                **({"warnings": caveats} if caveats else {}),
            },
        )

    return interpreter


# --- fallbacks and helpers -------------------------------------------------


def _rule_based_summary(
    goal: str,
    reference: dict,
    key_metric: dict,
    lead: dict,
    ensemble: list[dict],
    related: list[dict],
    tasks: list[dict],
) -> str:
    """A factual summary with no LLM: numbers and provenance only."""
    metric = key_metric.get("name", "")
    direction = key_metric.get("direction", "max")
    lines: list[str] = []

    ident = reference.get("pdb_id") or reference.get("uniprot_id") or "the target"
    if goal:
        lines.append(f"Goal: {goal}")
    lines.append(
        f"Reference: {ident}"
        + (f" ({reference['name']})" if reference.get("name") else "")
        + (f", {reference['length']} residues" if reference.get("length") else "")
        + "."
    )

    if metric:
        lines.append(
            f"Success was measured by {metric} "
            f"({'higher' if direction == 'max' else 'lower'} is better)."
        )

    if lead.get("design_id"):
        value = (lead.get("metrics") or {}).get(metric)
        shown = f"{value:.3g}" if isinstance(value, (int, float)) else "not measured"
        muts = ", ".join(lead.get("mutations") or []) or "no substitutions"
        lines.append(
            f"The lead design is {lead['design_id']} ({muts}), with {metric} = {shown}."
        )
        rationale = (lead.get("provenance") or {}).get("rationale")
        if rationale:
            lines.append(f"It was proposed because {rationale}.")

    if ensemble:
        measured = [
            d for d in ensemble if isinstance((d.get("metrics") or {}).get(metric), (int, float))
        ]
        if len(measured) > 1:
            values = [d["metrics"][metric] for d in measured]
            lines.append(
                f"Across {len(measured)} scored designs, {metric} ranged from "
                f"{min(values):.3g} to {max(values):.3g}."
            )

    if related:
        best = related[0]
        lines.append(
            f"For comparison, campaign {best['campaign_id']} reached "
            f"{best['metric']} = {best['value']:.3g} with {best['design_id']}."
        )

    failed = [t for t in tasks if t.get("state") == "FAILED"]
    if failed:
        lines.append(
            f"{len(failed)} task(s) failed this session: "
            + "; ".join(f"{t['name']} ({t.get('error', '')})" for t in failed[:3])
            + "."
        )

    lines.append(
        "Next, fold the top candidates with a higher-accuracy predictor, or "
        "combine the best substitutions into a single multi-mutant."
        if lead.get("design_id")
        else "No design was scored, so the next step is to generate and fold candidates."
    )
    return "\n\n".join(lines)


def _context(state: DesignState, related: list[dict], report: dict) -> str:
    import json

    reference = state.get("reference_design") or {}
    return json.dumps(
        {
            "goal": state.get("goal", ""),
            "rounds_run": state.get("round", 0),
            "reference": {
                "pdb_id": reference.get("pdb_id"),
                "uniprot_id": reference.get("uniprot_id"),
                "name": reference.get("name"),
                "organism": reference.get("organism"),
                "length": reference.get("length"),
                "function": (reference.get("function") or "")[:500],
                "literature": [
                    {
                        "title": r.get("title"),
                        "year": r.get("year"),
                        "relevance": r.get("relevance"),
                    }
                    for r in (reference.get("literature") or [])[:5]
                ],
            },
            "key_metric": state.get("key_metric") or {},
            "lead_design": state.get("lead_design") or {},
            "ensemble": [
                {
                    "design_id": d.get("design_id"),
                    "mutations": d.get("mutations", []),
                    "metrics": d.get("metrics", {}),
                    "rationale": (d.get("provenance") or {}).get("rationale", ""),
                }
                for d in (state.get("ensemble") or [])[:12]
            ],
            "related_past_designs": related,
            "tasks": [
                {"name": t.get("name"), "state": t.get("state"), "error": t.get("error")}
                for t in report.get("tasks", [])
            ],
            "round_analyses": [
                a.get("payload") for a in report.get("analyses", [])
            ],
        },
        default=str,
    )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-") or "set"
