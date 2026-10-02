"""The LLM paths, measured against a real key.

Everything else in this suite runs with `llm: false`, so two code paths have never
been observed: `CLASSIFY_SYSTEM` deciding intent instead of `classify_rules`
(backlog D1), and the interpreter writing its narrative instead of
`_rule_based_summary`. Both are reached only when a key is present, and both are
silent when they go wrong — a misrouted turn just does something else, and a
rule-based summary reads like a normal answer.

    ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest -m llm -q -s

This tier costs money. It is small on purpose: one classification per phrasing in
the table `test_graph.py` already holds, and one full session.

A routing divergence is *reported, not failed*: the rule table is this project's
intent, but the fix for a divergence is a prompt change in `CLASSIFY_SYSTEM`, and
turning every disagreement into a red test would mean the tier could not be used
to find them. The two assertions that do fail are the ones that mean the path is
broken rather than differently-opinionated: nothing was reached at all, or the
summary silently came from the rules.
"""

from __future__ import annotations

import pytest
from designagent.config import Settings
from designagent.graph.build import build_graph
from designagent.graph.nodes.coordinator import CLASSIFY_SYSTEM, classify_rules, state_digest
from designagent.graph.state import new_state
from designagent.llm import complete_json
from langgraph.checkpoint.memory import InMemorySaver

from tests.conftest import REF_SEQ
from tests.test_graph import CASES_WITH_REFERENCE, CASES_WITHOUT_REFERENCE

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def llm_settings() -> Settings:
    settings = Settings()
    if not settings.llm_available:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    return settings


async def _classify(settings: Settings, text: str, state: dict) -> str:
    decision = await complete_json(
        CLASSIFY_SYSTEM,
        f"Session state:\n{state_digest(state)}\n\nUser message:\n{text}",
        settings=settings,
        max_tokens=600,
    )
    assert decision is not None, f"the classifier returned nothing for {text!r}"
    return str(decision.get("intent", "")).lower()


async def test_the_llm_classifier_answers_at_all(llm_settings):
    """Before comparing opinions: does this path produce a usable intent?"""
    intent = await _classify(llm_settings, "redesign 1UBQ for thermostability", new_state("s"))
    assert intent in ("initialize", "design", "visualize", "summarize", "chat")


async def test_llm_routing_matches_the_rule_table(llm_settings):
    """The D1 measurement: every divergence, named, in one report."""
    empty = new_state("s")
    loaded = {**new_state("s"), "reference_design": {"sequence": REF_SEQ}}

    divergences: list[str] = []
    for cases, state, label in (
        (CASES_WITHOUT_REFERENCE, empty, "no reference"),
        (CASES_WITH_REFERENCE, loaded, "reference loaded"),
    ):
        for text, expected in cases:
            llm_intent = await _classify(llm_settings, text, state)
            rule_intent = classify_rules(text, state)["intent"]
            assert rule_intent == expected  # the offline tier should have caught this
            if llm_intent != expected:
                divergences.append(
                    f"  [{label}] {text!r}: rules={expected} llm={llm_intent}"
                )

    total = len(CASES_WITHOUT_REFERENCE) + len(CASES_WITH_REFERENCE)
    print(f"\nLLM routing: {total - len(divergences)}/{total} agree with the rule table")
    if divergences:
        print("divergences (each is a CLASSIFY_SYSTEM prompt fix, not a code change):")
        print("\n".join(divergences))


async def test_the_interpreter_writes_the_summary_itself(llm_settings, deps, stub_tools):
    """Goal 3: the narrative came from the model, not from `_rule_based_summary`."""
    deps.settings = llm_settings.model_copy(
        update={"data_dir": deps.settings.data_dir, "max_rounds": 1}
    )
    app = build_graph(deps, checkpointer=InMemorySaver())

    out = await app.ainvoke(
        {
            "messages": [{"role": "user", "content": "redesign 1UBQ for thermostability"}],
            "session_id": "llm-tier",
            "pending_results": {},
            "status": "",
        },
        config={"configurable": {"thread_id": "llm-tier"}},
    )

    assert out["summary_source"] == "llm", (
        "the key is configured but the summary came from the rules; "
        f"last error: {deps.last_llm_error!r}"
    )
    summary = out["design_summary"]
    print(f"\n--- interpreter output ({len(summary)} chars) ---\n{summary}\n")

    # It is about *this* run: the metric and the lead design it actually chose.
    assert out["key_metric"]["name"] in summary
    assert out["lead_design"]["design_id"] in summary
    assert not deps.last_llm_error


async def test_a_view_request_reaches_the_visualization_model(llm_settings, deps, stub_tools):
    """The generator runs in-process here, but the same prompt path as a worker."""
    from designagent.tools.molviz_agent import generate_visualization

    result = await generate_visualization(
        prompt="colour the first ten residues and nothing else",
        reference={"pdb_id": "1UBQ", "sequence": REF_SEQ, "chains": [{"chain_id": "A"}]},
    )
    assert not result.get("llm_error"), result["llm_error"]
    assert result["llm_used"] is True
    spec = result["spec"]
    assert spec["structures"]  # never taken from the model
    assert spec["highlights"], "the model was asked to highlight and did not"
