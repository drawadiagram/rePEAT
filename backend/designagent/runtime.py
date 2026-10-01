"""Application runtime: builds the whole stack and tears it down cleanly.

Startup order matters:
  1. rhapsody ConcurrentExecutionBackend over a ProcessPoolExecutor
  2. flowgentic LangraphIntegration, which creates the asyncflow WorkflowEngine
  3. task interfaces (local tasks routed through flowgentic to the pool)
  4. optional Orbit interface for remote HPC workflows
  5. the checkpointer, then the compiled graph

Everything is optional downwards: if flowgentic or rhapsody cannot start, the
agent still runs with plain asyncio task execution, because a broken deployment
substrate should not make the chatbot unusable.
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from .artifacts.store import ArtifactStore
from .config import Settings, get_settings
from .graph.build import build_graph
from .graph.deps import Deps
from .lake.store import DesignHistory
from .tasks.local import LocalTaskInterface, QueryTaskInterface
from .tasks.manager import TaskManager

log = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    deps: Deps
    app: Any                      # the compiled LangGraph
    manager: TaskManager
    history: DesignHistory
    artifacts: ArtifactStore
    integration: Any = None       # flowgentic LangraphIntegration
    backend: Any = None           # rhapsody backend
    orbit: Any = None
    _stack: AsyncExitStack | None = field(default=None, repr=False)
    _notes: list[str] = field(default_factory=list)

    @property
    def notes(self) -> list[str]:
        """Human-readable facts about how this process came up."""
        return list(self._notes)

    async def close(self) -> None:
        await self.manager.close()
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self.history.close()


async def _make_backend(settings: Settings):
    """The rhapsody process-pool backend, or None if unavailable."""
    from concurrent.futures import ProcessPoolExecutor

    from rhapsody.backends import ConcurrentExecutionBackend

    executor = ProcessPoolExecutor(max_workers=settings.pool_workers)
    # Backends initialise asynchronously: `await Backend(...)` is the API.
    return await ConcurrentExecutionBackend(executor, name="compute")


def _task_wrapper(integration: Any):
    """Build the callable that turns a task body into a flowgentic task."""
    from flowgentic.langGraph.execution_wrappers import AsyncFlowType
    from flowgentic.langGraph.fault_tolerance import RetryConfig

    retry = RetryConfig(
        # Folding or an HPC job can take far longer than flowgentic's 30s default.
        timeout_sec=None,
        max_attempts=1,
        retryable_exceptions=(ConnectionError, OSError),
    )

    def wrap(body):
        return integration.execution_wrappers.asyncflow(
            body,
            flow_type=AsyncFlowType.FUNCTION_TASK,
            backend="compute",
            retry=retry,
        )

    return wrap


async def _make_orbit(settings: Settings, notes: list[str]):
    """Connect the Orbit interface, or return None with a note explaining why not."""
    if not settings.orbit_enabled:
        return None
    from .tasks.hpc.orbit import OrbitInterface

    interface = OrbitInterface(
        broker_url=settings.orbit_broker_url,
        endpoint=settings.orbit_endpoint,
        token=settings.orbit_broker_token,
        cert=settings.orbit_broker_cert,
    )
    try:
        await interface.connect()
    except Exception as exc:
        notes.append(f"Orbit unavailable ({exc}); HPC tasks will run locally.")
        log.warning("Orbit connect failed: %s", exc)
        return None
    notes.append("Orbit connected for remote HPC workflows.")
    return interface


async def build_runtime(settings: Settings | None = None) -> Runtime:
    """Bring up the full stack."""
    settings = settings or get_settings()
    settings.ensure_dirs()

    notes: list[str] = []
    stack = AsyncExitStack()

    history = DesignHistory(settings)
    artifacts = ArtifactStore(settings.artifacts_dir)

    # --- compute substrate ---
    backend = None
    integration = None
    wrap = None
    try:
        backend = await _make_backend(settings)
        from flowgentic.langGraph.main import LangraphIntegration

        integration = await stack.enter_async_context(
            LangraphIntegration(backend=backend)
        )
        wrap = _task_wrapper(integration)
        notes.append(
            f"flowgentic + asyncflow on a rhapsody process pool "
            f"({settings.pool_workers} workers)."
        )
    except Exception as exc:
        log.warning("flowgentic/rhapsody unavailable: %s", exc)
        notes.append(
            f"Running without flowgentic ({exc}); local tasks execute in-process."
        )

    # --- task interfaces ---
    orbit = await _make_orbit(settings, notes)
    manager = TaskManager(
        local=LocalTaskInterface(wrap=wrap),
        query=QueryTaskInterface(),
        hpc=orbit,
        history=history,
    )

    # --- checkpointer ---
    checkpointer = None
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        checkpointer = await stack.enter_async_context(
            AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_db_path))
        )
        notes.append(f"Conversations persist in {settings.checkpoint_db_path}.")
    except Exception as exc:
        log.warning("sqlite checkpointer unavailable: %s", exc)
        from langgraph.checkpoint.memory import InMemorySaver

        checkpointer = InMemorySaver()
        notes.append(f"Using in-memory checkpoints ({exc}); history is not persisted.")

    if not settings.llm_available:
        notes.append(
            "No ANTHROPIC_API_KEY: nodes use their deterministic rule-based paths."
        )

    deps = Deps(settings=settings, tasks=manager, history=history, artifacts=artifacts)
    graph = build_graph(
        deps,
        integration=integration,
        checkpointer=checkpointer,
        wrap_nodes=settings.wrap_nodes,
    )

    for note in notes:
        log.info("runtime: %s", note)

    return Runtime(
        settings=settings,
        deps=deps,
        app=graph,
        manager=manager,
        history=history,
        artifacts=artifacts,
        integration=integration,
        backend=backend,
        orbit=orbit,
        _stack=stack,
        _notes=notes,
    )
