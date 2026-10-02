"""Shared fixtures.

Tests run offline: `no_network` blocks real HTTP so a missing stub fails loudly
instead of silently hitting the internet. Tests that want a real substrate, a real
endpoint or a real API key carry the `live`, `remote` or `llm` marker and are
deselected by default.

The fake protein world and `stub_tools` live here because three modules now use
them: the graph tests, the API tests and the `llm` tier.
"""

from __future__ import annotations

import pytest
from designagent import config as config_module
from designagent.artifacts.store import ArtifactStore
from designagent.config import Settings, install_settings
from designagent.graph.deps import Deps
from designagent.lake.store import DesignHistory
from designagent.tasks.manager import TaskManager
from designagent.tasks.registry import CATALOG, TaskDef


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real network services")
    config.addinivalue_line("markers", "remote: submits to a real HPC endpoint")
    config.addinivalue_line("markers", "llm: calls a real Anthropic API key")


# --- a tiny fake protein world --------------------------------------------

REF_SEQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
PDB_TEXT = "\n".join(
    f"ATOM  {i:5d}  CA  ALA A{i:4d}    "
    f"{i:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{85.0:6.2f}           C"
    for i in range(1, 21)
)


@pytest.fixture
def stub_tools(monkeypatch):
    """Replace every external tool body with a deterministic stub."""
    calls: list[str] = []

    async def pdb_lookup(**kw):
        calls.append("pdb_lookup")
        if not (kw.get("pdb_id") or kw.get("query")):
            return {"error": "no match"}
        return {
            "pdb_id": "1UBQ",
            "name": "Ubiquitin",
            "sequence": REF_SEQ,
            "length": len(REF_SEQ),
            "organism": "Homo sapiens",
            "function": "UBIQUITIN",
            "uniprot_id": "P0CG48",
            "chains": [{"chain_id": "A", "length": len(REF_SEQ)}],
            "ligands": [],
        }

    async def uniprot_lookup(**kw):
        calls.append("uniprot_lookup")
        return {
            "uniprot_id": "P0CG48",
            "name": "Polyubiquitin-B",
            "sequence": REF_SEQ,
            "length": len(REF_SEQ),
            "organism": "Homo sapiens",
            "function": "Covalent attachment to substrates.",
            "features": [{"type": "Active site", "start": 48, "end": 48, "description": ""}],
            "pdb_ids": ["1UBQ"],
        }

    async def literature_lookup(**kw):
        calls.append("literature_lookup")
        return {
            "query": "stub",
            "refs": [
                {
                    "id": "PMC1",
                    "title": "Stabilizing ubiquitin",
                    "year": "2024",
                    "doi": "10.1/x",
                    "abstract": "T55V raised Tm.",
                    "relevance": "thermostability",
                    "mutations": ["T55V"],
                }
            ],
            "n_refs": 1,
            "mutations_mentioned": ["T55V"],
        }

    async def pdb_structure(**kw):
        calls.append("pdb_structure")
        return {"pdb_id": "1UBQ", "format": "pdb", "text": PDB_TEXT, "bytes": len(PDB_TEXT)}

    async def fold_sequence(sequence="", design_id="", **kw):
        calls.append("fold_sequence")
        # pLDDT rises with the number of mutations, so round 1 improves.
        score = 70.0 + 5.0 * sum(1 for a, b in zip(REF_SEQ, sequence) if a != b)
        return {
            "structure": PDB_TEXT,
            "format": "pdb",
            "backend": "stub",
            "sequence": sequence,
            "design_id": design_id,
            "metrics": {"plddt": min(score, 99.0)},
            "length": len(sequence),
        }

    originals = {}
    for name, body in (
        ("pdb_lookup", pdb_lookup),
        ("uniprot_lookup", uniprot_lookup),
        ("literature_lookup", literature_lookup),
        ("pdb_structure", pdb_structure),
        ("fold_sequence", fold_sequence),
    ):
        originals[name] = CATALOG[name]
        CATALOG[name] = TaskDef(
            name, body, originals[name].interface, originals[name].description,
            originals[name].produces,
        )
    yield calls
    CATALOG.update(originals)


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Keep the process-wide settings instance from leaking between tests.

    `config` holds the instance a process is using, and `apply_overrides` mutates
    it. Without this, a test that applies an override would change what every
    later test's task bodies see.
    """
    monkeypatch.setattr(config_module, "_base", None)
    monkeypatch.setattr(config_module, "_current", None)
    monkeypatch.setattr(config_module, "_overrides", {})


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key="",  # force the deterministic fallbacks
        fold_backend="esmatlas",
        max_rounds=2,
        task_timeout_sec=60.0,
    )


@pytest.fixture
def deps(settings):
    history = DesignHistory(settings)
    artifacts = ArtifactStore(settings.artifacts_dir)
    manager = TaskManager(history=history)
    yield Deps(settings=settings, tasks=manager, history=history, artifacts=artifacts)
    history.close()


@pytest.fixture
def client(deps, monkeypatch):
    """The real FastAPI app with a runtime built from the test deps.

    No process pool and no flowgentic, so the routes and the SSE framing are
    exercised without a substrate. Shared by test_api and test_settings.
    """
    from designagent import app as app_module
    from designagent.graph.build import build_graph
    from designagent.runtime import Runtime
    from fastapi.testclient import TestClient
    from langgraph.checkpoint.memory import InMemorySaver

    # Production builds the runtime from `get_settings()`, so the process's
    # settings and the runtime's are the same object. Say so here too: without
    # it an override would be layered onto a different baseline and every
    # settings change would look like a rebuild.
    install_settings(deps.settings)
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
def no_network(monkeypatch):
    """Fail any unstubbed outbound HTTP."""
    import httpx

    async def blocked(*args, **kwargs):
        raise AssertionError("unstubbed network call in a test")

    monkeypatch.setattr(httpx.AsyncClient, "get", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "post", blocked)
