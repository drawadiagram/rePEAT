"""ChemGraph as a Local Task Agent (cheminformatics / quantum chemistry).

ChemGraph is a LangGraph agent in its own right, so it is invoked as a nested
agent rather than imported as a library of functions: we hand it a natural
language task and collect its answer plus whatever files it wrote.

It is an optional extra (`pip install -e '.[chem]'`): it pins langgraph and
langchain exactly and pulls torch via mace-torch. `chemgraph_available()` lets
the orchestrator route around it when it is not installed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..config import get_settings

log = logging.getLogger(__name__)


def chemgraph_available() -> bool:
    from importlib.util import find_spec

    try:
        return find_spec("chemgraph") is not None
    except (ImportError, ValueError):
        return False


def _model_name(settings) -> str:
    """Map our configured model onto something ChemGraph's loader accepts.

    ChemGraph picks a provider by matching the model name against its own
    supported-model lists, so a newer Claude id it has never heard of would not
    resolve. Prefix with `anthropic:` when available, else fall back to a name
    its table definitely contains.
    """
    model = settings.model
    try:
        from chemgraph.models.supported_models import supported_anthropic_models

        if model in supported_anthropic_models:
            return model
        log.info(
            "ChemGraph does not list %s; using %s instead",
            model,
            supported_anthropic_models[0],
        )
        return supported_anthropic_models[0]
    except Exception:
        return model


# --- task body -------------------------------------------------------------


async def run_chemgraph(
    task: str = "",
    workflow: str = "single_agent",
    **_: Any,
) -> dict[str, Any]:
    """Run a cheminformatics task through ChemGraph.

    Typical uses here: resolve a ligand name to SMILES/structure, optimize a
    small molecule, or compute a binding-relevant property for a cofactor.
    """
    if not task.strip():
        return {"error": "no task given to ChemGraph"}
    if not chemgraph_available():
        return {
            "error": "ChemGraph is not installed; install the 'chem' extra to enable it"
        }

    settings = get_settings()
    if not settings.llm_available:
        return {"error": "ChemGraph needs an LLM; set ANTHROPIC_API_KEY"}

    # ChemGraph writes tool output (XYZ/JSON/HTML) to this directory.
    log_dir = settings.data_dir / "chemgraph"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("CHEMGRAPH_LOG_DIR", str(log_dir))
    os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)

    before = set(log_dir.glob("*"))
    try:
        from chemgraph.agent.llm_agent import ChemGraph

        agent = ChemGraph(
            model_name=_model_name(settings),
            workflow_type=workflow,
            return_option="last_message",
        )
        result = await agent.run(task)
    except Exception as exc:
        log.warning("ChemGraph failed: %s", exc)
        return {"error": f"ChemGraph failed: {exc}", "task": task}

    answer = getattr(result, "content", None) or str(result)
    new_files = sorted(str(p) for p in set(log_dir.glob("*")) - before)
    return {
        "task": task,
        "workflow": workflow,
        "answer": answer,
        "files": new_files,
    }
