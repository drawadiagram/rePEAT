"""Shared fixtures.

Tests run offline: `no_network` blocks real HTTP so a missing stub fails loudly
instead of silently hitting the internet. Tests that want live services ask for
the `live` marker and are deselected by default.
"""

from __future__ import annotations

import pytest
from designagent.artifacts.store import ArtifactStore
from designagent.config import Settings
from designagent.graph.deps import Deps
from designagent.lake.store import DesignHistory
from designagent.tasks.manager import TaskManager


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real network services")


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
def no_network(monkeypatch):
    """Fail any unstubbed outbound HTTP."""
    import httpx

    async def blocked(*args, **kwargs):
        raise AssertionError("unstubbed network call in a test")

    monkeypatch.setattr(httpx.AsyncClient, "get", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "post", blocked)
