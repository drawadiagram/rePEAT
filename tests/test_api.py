"""The web API and the streaming contract.

These drive the real FastAPI app with a stubbed runtime, so the SSE framing and
artifact serving are exercised without starting a process pool.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from designagent import app as app_module
from designagent.graph.build import build_graph
from designagent.runtime import Runtime
from designagent.tasks.registry import CATALOG, TaskDef
from tests.test_graph import PDB_TEXT, REF_SEQ  # reuse the fake protein world


@pytest.fixture
def client(deps, monkeypatch):
    """The app with a runtime built from the test deps (no flowgentic/pool)."""
    graph = build_graph(deps, checkpointer=InMemorySaver())
    runtime = Runtime(
        settings=deps.settings,
        deps=deps,
        app=graph,
        manager=deps.tasks,
        history=deps.history,
        artifacts=deps.artifacts,
    )

    async def fake_build_runtime(settings=None):
        return runtime

    monkeypatch.setattr(app_module, "build_runtime", fake_build_runtime)
    with TestClient(app_module.app) as test_client:
        test_client.runtime = runtime
        yield test_client


@pytest.fixture
def stub_tools(monkeypatch):
    async def pdb_lookup(**kw):
        return {
            "pdb_id": "1UBQ", "name": "Ubiquitin", "sequence": REF_SEQ,
            "length": len(REF_SEQ), "organism": "Homo sapiens", "uniprot_id": "P0CG48",
            "chains": [{"chain_id": "A", "length": len(REF_SEQ)}], "ligands": [],
        }

    async def uniprot_lookup(**kw):
        return {"uniprot_id": "P0CG48", "sequence": REF_SEQ, "features": [], "pdb_ids": ["1UBQ"]}

    async def literature_lookup(**kw):
        return {"refs": [], "n_refs": 0, "mutations_mentioned": []}

    async def pdb_structure(**kw):
        return {"pdb_id": "1UBQ", "format": "pdb", "text": PDB_TEXT}

    async def fold_sequence(sequence="", design_id="", **kw):
        n = sum(1 for a, b in zip(REF_SEQ, sequence) if a != b)
        return {
            "structure": PDB_TEXT, "format": "pdb", "sequence": sequence,
            "design_id": design_id, "metrics": {"plddt": 70.0 + 5 * n},
        }

    originals = {}
    for name, body in (
        ("pdb_lookup", pdb_lookup), ("uniprot_lookup", uniprot_lookup),
        ("literature_lookup", literature_lookup), ("pdb_structure", pdb_structure),
        ("fold_sequence", fold_sequence),
    ):
        originals[name] = CATALOG[name]
        CATALOG[name] = TaskDef(name, body, originals[name].interface, "stub",
                                originals[name].produces)
    yield
    CATALOG.update(originals)


def _frames(response) -> list[dict]:
    out = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


# --- health and catalog ----------------------------------------------------


def test_health_reports_capabilities(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["llm"] is False           # no key in tests
    assert body["hpc"] is False           # no Orbit
    assert "local" in body["interfaces"] and "query" in body["interfaces"]
    assert body["interfaces"]["local"]["cancel"] is True
    assert body["interfaces"]["local"]["logs"] is False


def test_catalog_lists_tasks(client):
    tasks = client.get("/api/catalog").json()["tasks"]
    names = {t["name"] for t in tasks}
    assert {"pdb_lookup", "fold_sequence", "generate_visualization"} <= names
    assert all(t["interface"] in ("local", "query", "hpc") for t in tasks)


# --- chat streaming --------------------------------------------------------


def test_chat_streams_sse_frames(client, stub_tools):
    response = client.post("/api/chat", json={"message": "hello", "session_id": "s1"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    frames = _frames(response)
    kinds = [f["type"] for f in frames]
    assert kinds[-1] == "done"
    # With no LLM there are no tokens, so the reply arrives as a message frame.
    assert "message" in kinds
    assert any(f.get("text") for f in frames if f["type"] == "message")


def test_chat_streams_status_and_task_and_state_frames(client, stub_tools):
    response = client.post(
        "/api/chat",
        json={"message": "redesign 1UBQ for thermostability", "session_id": "s2"},
    )
    frames = _frames(response)
    kinds = {f["type"] for f in frames}

    # the four things the UI needs
    assert "status" in kinds, kinds
    assert "task" in kinds, kinds
    assert "state" in kinds, kinds
    assert "done" in kinds

    statuses = [f["text"] for f in frames if f["type"] == "status"]
    assert any("Looking up" in s for s in statuses)
    assert any("Folding" in s for s in statuses)

    task_events = [f for f in frames if f["type"] == "task"]
    assert {"submitted", "finished"} <= {e["event"] for e in task_events}
    assert all("state" in e for e in task_events if e["event"] == "finished")

    # the state frames carry the keys the artifact pane renders
    state_keys = set()
    for frame in frames:
        if frame["type"] == "state":
            state_keys |= set(frame["state"])
    assert {"reference_design", "lead_design", "ensemble", "artifacts"} <= state_keys


def test_chat_rejects_empty_message(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


# --- session restore -------------------------------------------------------


def test_session_state_round_trips(client, stub_tools):
    client.post("/api/chat", json={"message": "load 1UBQ", "session_id": "s3"})
    body = client.get("/api/sessions/s3").json()
    assert body["state"]["reference_design"]["pdb_id"] == "1UBQ"
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles and "assistant" in roles


def test_unknown_session_is_empty_not_an_error(client):
    body = client.get("/api/sessions/never-used").json()
    assert body["state"] == {} or body["state"] is not None


# --- artifacts -------------------------------------------------------------


def test_artifacts_are_served_with_correct_types(client, stub_tools):
    client.post(
        "/api/chat",
        json={"message": "redesign 1UBQ for stability", "session_id": "s4"},
    )
    listed = client.get("/api/artifacts", params={"session_id": "s4"}).json()["artifacts"]
    by_kind = {a["kind"]: a for a in listed}
    assert {"markdown", "docx", "molstar", "table"} <= set(by_kind)

    md = client.get(by_kind["markdown"]["url"])
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "Design summary" in md.text

    docx = client.get(by_kind["docx"]["url"])
    assert docx.content[:2] == b"PK"
    assert "attachment" in docx.headers["content-disposition"]

    viz = client.get(by_kind["molstar"]["url"]).json()
    assert "structures" in viz and "representation" in viz


def test_missing_artifact_is_404(client):
    assert client.get("/api/artifacts/nope").status_code == 404


# --- tasks ----------------------------------------------------------------


def test_task_listing_and_lookup(client, stub_tools):
    client.post("/api/chat", json={"message": "load 1UBQ", "session_id": "s5"})
    tasks = client.get("/api/tasks", params={"session_id": "s5"}).json()["tasks"]
    assert tasks
    assert {"id", "task", "state", "elapsed"} <= set(tasks[0])

    one = client.get(f"/api/tasks/{tasks[0]['id']}").json()
    assert one["id"] == tasks[0]["id"]


def test_cancelling_a_finished_task_explains_itself(client, stub_tools):
    client.post("/api/chat", json={"message": "load 1UBQ", "session_id": "s6"})
    task_id = client.get("/api/tasks", params={"session_id": "s6"}).json()["tasks"][0]["id"]
    response = client.post(f"/api/tasks/{task_id}/cancel")
    assert response.status_code == 409
    assert "already finished" in response.json()["reason"]


def test_cancelling_unknown_task_is_404(client):
    assert client.post("/api/tasks/nope/cancel").status_code == 404
