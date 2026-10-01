"""Graph assembly.

Nodes run as plain LangGraph nodes by default, and *tasks* are what get routed
through flowgentic to the rhapsody process pool (see runtime.py).

Why not wrap nodes as flowgentic EXECUTION_BLOCKs: that runs the node body on
asyncflow's own event loop, outside LangGraph's runnable context, so
`get_stream_writer()` raises "Called get_config outside of a runnable context"
and every status update and token is silently dropped. Streaming progress to the
chat matters more than having asyncflow schedule the nodes, and the wrapping
otherwise buys nothing here: its retry/timeout has to be disabled for nodes that
legitimately wait on an HPC job.

`wrap_nodes=True` restores the block wrapping for anyone who wants asyncflow to
own node execution and can live without custom-event streaming.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from .deps import Deps
from .nodes.analyst import make_analyst
from .nodes.coordinator import make_coordinator
from .nodes.initializer import make_initializer
from .nodes.interpreter import make_interpreter
from .nodes.orchestrator import make_orchestrator
from .state import DesignState

log = logging.getLogger(__name__)

NODES = ("coordinator", "initializer", "orchestrator", "analyst", "interpreter")


def node_retry_config():
    """A RetryConfig suited to long-running agent nodes, or None."""
    try:
        from flowgentic.langGraph.fault_tolerance import RetryConfig
    except ImportError:
        return None
    return RetryConfig(
        timeout_sec=None,   # a node may legitimately wait on an HPC job
        max_attempts=1,     # nodes write to the lake; re-running duplicates work
        retryable_exceptions=(ConnectionError, OSError),
    )


def wrap_node(integration: Any, fn: Callable) -> Callable:
    """Wrap a node as a flowgentic execution block, if possible."""
    if integration is None:
        return fn
    try:
        from flowgentic.langGraph.execution_wrappers import AsyncFlowType

        return integration.execution_wrappers.asyncflow(
            fn,
            flow_type=AsyncFlowType.EXECUTION_BLOCK,
            retry=node_retry_config(),
        )
    except Exception as exc:
        # A flowgentic change should degrade to plain LangGraph, not break boot.
        log.warning("flowgentic node wrapping unavailable (%s); running unwrapped", exc)
        return fn


def build_graph(
    deps: Deps,
    *,
    integration: Any = None,
    checkpointer: Any = None,
    wrap_nodes: bool = False,
):
    """Compile the agent graph.

    Routing is expressed with `Command(goto=...)` from inside the nodes, so the
    only static edge is START -> coordinator; `add_node` declares the possible
    destinations for each node so LangGraph can validate them.
    """
    if wrap_nodes and integration is not None:
        log.warning(
            "wrap_nodes=True: nodes run on asyncflow's loop, so status/token "
            "streaming to the chat will be dropped"
        )
    else:
        integration = None  # nodes stay plain; tasks still use flowgentic

    builder = StateGraph(DesignState)

    factories = {
        "coordinator": make_coordinator,
        "initializer": make_initializer,
        "orchestrator": make_orchestrator,
        "analyst": make_analyst,
        "interpreter": make_interpreter,
    }
    destinations = {
        "coordinator": ("initializer", "orchestrator", "analyst", "interpreter", END),
        "initializer": ("orchestrator", END),
        "orchestrator": ("analyst", "interpreter", END),
        "analyst": ("orchestrator", "interpreter", END),
        "interpreter": (END,),
    }

    for name, factory in factories.items():
        builder.add_node(
            name,
            wrap_node(integration, factory(deps)),
            destinations=destinations[name],
        )

    builder.add_edge(START, "coordinator")
    return builder.compile(checkpointer=checkpointer)
