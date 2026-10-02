"""The agent loop: routing, state updates, and the full redesign path.

All of these run with no LLM key and no network: tool bodies are stubbed in the
registry, which is also how the task layer is meant to be substituted.
"""

from __future__ import annotations

import pytest
from designagent.graph.build import build_graph
from designagent.graph.nodes.coordinator import classify_rules, describe_state
from designagent.graph.state import merge_artifacts, merge_worklist, new_state
from designagent.tasks.registry import CATALOG, TaskDef
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from tests.conftest import REF_SEQ


@pytest.fixture
def app(deps):
    return build_graph(deps, checkpointer=InMemorySaver())


def _config(thread="t1"):
    return {"configurable": {"thread_id": thread}}


async def _send(app, text, *, session="s1", thread="t1"):
    """One turn, built exactly as `app.py` builds it.

    This must not thread the previous turn's state back in: doing so hid a bug
    where the real payload blanked every `replace`-reduced channel, so a session
    lost its reference design on turn 2. The `app` fixture has a checkpointer —
    continuity is its job, not the caller's.
    """
    payload = {
        "messages": [{"role": "user", "content": text}],
        "session_id": session,
        "pending_results": {},
        "status": "",
    }
    return await app.ainvoke(payload, config=_config(thread))


# --- reducers --------------------------------------------------------------


def test_merge_artifacts_dedupes_by_id_keeping_last():
    left = [{"id": "a", "title": "one"}, {"id": "b", "title": "two"}]
    right = [{"id": "a", "title": "one-updated"}, {"id": "c", "title": "three"}]
    out = merge_artifacts(left, right)
    assert [a["id"] for a in out] == ["a", "b", "c"]
    assert out[0]["title"] == "one-updated"


def test_merge_worklist_upserts_status():
    left = [{"id": "w1", "status": "pending"}]
    right = [{"id": "w1", "status": "done"}, {"id": "w2", "status": "pending"}]
    out = merge_worklist(left, right)
    assert out[0]["status"] == "done"
    assert len(out) == 2


# --- coordinator classification -------------------------------------------


#: Phrasings and the intent they must produce with nothing loaded yet. Named so
#: the `llm` tier can replay the same table through CLASSIFY_SYSTEM and report
#: where the two classifiers disagree — see tests/test_llm_live.py.
CASES_WITHOUT_REFERENCE = [
    ("hello there", "chat"),
    ("what is the lead design?", "chat"),
    ("load 1UBQ", "initialize"),
    ("redesign 1UBQ for thermostability", "initialize"),
    ("write me a report", "summarize"),
    ("which design scored best?", "chat"),
    ("how does pLDDT work?", "chat"),
    ("tell me about the ensemble", "chat"),
    ("summarize the session", "summarize"),
]

#: The same, with a reference design in state.
CASES_WITH_REFERENCE = [
    ("make it more stable", "design"),
    ("improve solubility", "design"),
    ("propose some variants", "design"),
    ("what mutations did you try?", "chat"),
    ("is the lead design better?", "chat"),
    ("can you redesign it for stability", "design"),
    ("show me the structure", "visualize"),
    ("highlight the active site", "visualize"),
    # Phrasings that used to fall through to chat.
    ("label the active site residues in the visualization", "visualize"),
    ("add labels to the catalytic residues", "visualize"),
    ("zoom in on residue 147", "visualize"),
    ("rotate it and show the surface", "visualize"),
    ("run another round", "design"),
    ("iterate on the lead", "design"),
    # ...without swallowing the design request that mentions a residue.
    ("mutate residue 42 to valine", "design"),
    ("what happened in that run?", "chat"),
]


@pytest.mark.parametrize("text,expected", CASES_WITHOUT_REFERENCE)
def test_classify_rules_without_reference(text, expected):
    assert classify_rules(text, new_state("s"))["intent"] == expected


@pytest.mark.parametrize("text,expected", CASES_WITH_REFERENCE)
def test_classify_rules_questions_versus_requests(text, expected):
    state = {**new_state("s"), "reference_design": {"sequence": REF_SEQ}}
    assert classify_rules(text, state)["intent"] == expected


def test_classify_rules_with_reference_loaded():
    state = {**new_state("s"), "reference_design": {"sequence": REF_SEQ}}
    assert classify_rules("make it more stable", state)["intent"] == "design"
    assert classify_rules("show me the structure", state)["intent"] == "visualize"
    assert classify_rules("apply A42V", state)["mutations"] == ["A42V"]


def test_describe_state_without_reference_asks_for_a_target():
    text = describe_state(new_state("s"))
    assert "No reference design" in text


# --- chat path ------------------------------------------------------------


async def test_plain_chat_ends_without_running_tasks(app, stub_tools):
    out = await _send(app, "hello")
    assert out["intent"] == "chat"
    assert stub_tools == []  # nothing was looked up
    assert out["messages"][-1].content


# --- initialization path --------------------------------------------------


async def test_initialize_builds_reference_design(app, stub_tools, deps):
    out = await _send(app, "load PDB 1UBQ")
    ref = out["reference_design"]
    assert ref["pdb_id"] == "1UBQ"
    assert ref["sequence"] == REF_SEQ
    assert ref["uniprot_id"] == "P0CG48"
    assert ref["structure_path"].endswith(".pdb")
    assert ref["literature"][0]["title"] == "Stabilizing ubiquitin"
    assert "pdb_lookup" in stub_tools and "literature_lookup" in stub_tools
    # tier 1 recorded the campaign and its reference
    assert deps.history.graph.campaigns()[0]["id"] == "s1"


async def test_unidentifiable_target_reports_instead_of_crashing(app, stub_tools, monkeypatch):
    async def nothing(**kw):
        return {"error": "no match"}

    for name in ("pdb_lookup", "uniprot_lookup"):
        CATALOG[name] = TaskDef(name, nothing, "query", "stub")

    out = await _send(app, "redesign the flux capacitor protein")
    assert "could not identify" in out["messages"][-1].content.lower()
    assert not out.get("reference_design", {}).get("sequence")


async def test_state_survives_into_the_next_turn(app, stub_tools):
    """app.py used to spread a fresh new_state() into every turn, blanking this.

    Every `replace`-reduced channel was overwritten with its empty default, so
    the second prompt of a session saw no reference design: it answered "No
    reference design is loaded yet" and re-ran the whole initializer.
    """
    await _send(app, "load PDB 1UBQ")
    out = await _send(app, "what is loaded?")

    assert out["reference_design"]["pdb_id"] == "1UBQ"
    assert "No reference design" not in out["messages"][-1].content
    assert stub_tools.count("pdb_lookup") == 1  # the initializer did not re-run


# --- full design loop -----------------------------------------------------


async def test_design_loop_produces_lead_ensemble_and_artifacts(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ to improve thermostability")

    assert out["key_metric"]["name"] == "plddt"
    assert out["key_metric"]["direction"] == "max"

    ensemble = out["ensemble"]
    assert len(ensemble) >= 2
    # ranked best-first on the key metric
    values = [d["metrics"]["plddt"] for d in ensemble if "plddt" in d.get("metrics", {})]
    assert values == sorted(values, reverse=True)

    lead = out["lead_design"]
    assert lead["design_id"] == ensemble[0]["design_id"]
    assert lead["mutations"]  # it differs from the reference

    # the visualization and the summary artifacts were registered
    kinds = {a["kind"] for a in out["artifacts"]}
    assert {"molstar", "markdown", "docx", "table"} <= kinds
    assert out["design_summary"]

    # every design reached tier 1 and tier 2
    designs = deps.history.graph.designs_in_campaign("s1")
    assert len(designs) == len(ensemble)
    stored = deps.history.scores.scores_for_design(lead["design_id"])
    assert stored["plddt"] == lead["metrics"]["plddt"]

    # tier 3 golden set was staged
    assert deps.history.golden.list_sets()[0]["n_rows"] >= 1


async def test_explicit_mutations_are_applied(app, stub_tools):
    out = await _send(app, "redesign 1UBQ applying T55V")
    assert out["lead_design"]["mutations"] == ["T55V"]


async def test_round_budget_is_respected(app, stub_tools, deps):
    """max_rounds=2, and the stub always improves, so it must stop at 2."""
    out = await _send(app, "redesign 1UBQ for stability")
    assert out["round"] <= deps.settings.max_rounds


async def test_summary_artifact_contains_the_numbers(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ for stability")
    markdown = next(a for a in out["artifacts"] if a["kind"] == "markdown")
    text = deps.artifacts.read_bytes(markdown["id"]).decode()
    assert "Lead design" in text
    assert out["lead_design"]["design_id"] in text
    assert "plddt" in text.lower()


async def test_docx_artifact_is_a_real_docx(app, stub_tools, deps):
    out = await _send(app, "redesign 1UBQ for stability")
    docx = next(a for a in out["artifacts"] if a["kind"] == "docx")
    data = deps.artifacts.read_bytes(docx["id"])
    assert data[:2] == b"PK"  # a zip container
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(data))
    assert any("Design summary" in p.text for p in doc.paragraphs)


# --- visualization path ---------------------------------------------------


async def test_visualize_request_builds_a_spec(app, stub_tools, deps):
    await _send(app, "load 1UBQ")
    out = await _send(app, "show me the structure")
    spec = out["molecular_visualization"]
    assert spec["structures"]
    assert spec["representation"] in ("cartoon", "ball-and-stick", "gaussian-surface")
    assert spec["artifact_id"]
    stored = deps.artifacts.read_json(spec["artifact_id"])
    assert stored["title"]


async def test_visualization_highlights_functional_residues(app, stub_tools):
    await _send(app, "load 1UBQ")
    out = await _send(app, "show the active site")
    labels = {h["label"] for h in out["molecular_visualization"]["highlights"]}
    assert "Functional residues" in labels


async def test_a_label_request_changes_the_view(app, stub_tools):
    """The phrasing from the bug report: it used to classify as chat."""
    await _send(app, "load PDB 1UBQ")
    out = await _send(app, "label the active site residues in the visualization")
    assert out["intent"] == "visualize"
    assert out["molecular_visualization"]["highlights"]


# --- failure handling -----------------------------------------------------


async def test_fold_failure_does_not_abort_the_loop(app, stub_tools):
    async def failing_fold(**kw):
        return {"error": "fold service unavailable"}

    CATALOG["fold_sequence"] = TaskDef(
        "fold_sequence", failing_fold, "local", "stub", "structure"
    )
    out = await _send(app, "redesign 1UBQ for stability")
    # The loop still finishes and explains itself.
    assert out["messages"][-1].content
    assert out["design_summary"]


async def test_task_exception_is_recorded_as_failed(app, stub_tools, deps):
    async def exploding(**kw):
        raise RuntimeError("boom")

    CATALOG["fold_sequence"] = TaskDef(
        "fold_sequence", exploding, "local", "stub", "structure"
    )
    await _send(app, "redesign 1UBQ for stability")
    states = {t["state"] for t in deps.history.graph.tasks_for_campaign("s1")}
    assert "FAILED" in states


async def test_lake_write_failure_does_not_lose_the_round(app, stub_tools, deps, monkeypatch):
    """Storage is not allowed to destroy work the user already paid for."""

    def boom(*args, **kwargs):
        raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr(deps.history, "record_design", boom)
    monkeypatch.setattr(deps.history, "rank_round", boom)
    monkeypatch.setattr(deps.history, "record_analysis", boom)

    out = await _send(app, "redesign 1UBQ for stability")

    # The designs survived, ranked in memory, and the failure is surfaced.
    assert out["ensemble"]
    assert out["lead_design"]["design_id"]
    values = [d["metrics"]["plddt"] for d in out["ensemble"] if "plddt" in d.get("metrics", {})]
    assert values == sorted(values, reverse=True)
    assert any("Design History write failed" in w for w in out["warnings"])
    # and the user is actually told, not just the log
    assert "Caveats from this run" in out["messages"][-1].content
    assert out["design_summary"]


async def test_structures_are_not_carried_in_state(app, stub_tools, deps):
    """Coordinates belong in blobs; checkpoints must not balloon with PDB text."""
    import json

    out = await _send(app, "redesign 1UBQ for stability")

    # Designs reference a blob path, and the file really holds the coordinates.
    lead = out["lead_design"]
    assert lead["structure_path"]
    assert deps.history.read_blob(lead["structure_path"]).startswith(b"ATOM")

    # No PDB text anywhere in the serialized state.
    blob = json.dumps(
        {k: v for k, v in out.items() if k != "messages"}, default=str
    )
    assert "ATOM  " not in blob
    assert out.get("pending_results") in (None, {})


async def test_a_rejected_key_says_so_instead_of_silently_using_rules(
    app, stub_tools, deps, monkeypatch
):
    """A configured key that fails must not look like ordinary offline mode.

    Without this, the only difference between "no key" and "the key you just
    pasted is wrong" is a line in the server log.
    """
    from designagent import llm as llm_module

    deps.settings = deps.settings.model_copy(
        update={"anthropic_api_key": SecretStr("sk-ant-wrong-0123456789")}
    )

    async def rejected(*args, **kwargs):
        raise RuntimeError("authentication_error: invalid x-api-key")

    monkeypatch.setattr(llm_module, "build_llm", lambda *a, **k: _Unusable(rejected))

    out = await _send(app, "redesign 1UBQ for stability")

    assert any("rule-based path" in w for w in out["warnings"])
    assert "Caveats from this run" in out["messages"][-1].content
    assert "the API key was rejected" in deps.last_llm_error
    # The run still produced its work: degradation, not failure.
    assert out["design_summary"]
    assert out["summary_source"] == "rules"


class _Unusable:
    """A chat model stand-in whose every call raises."""

    def __init__(self, ainvoke):
        self.ainvoke = ainvoke
