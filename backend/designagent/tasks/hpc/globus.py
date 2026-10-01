"""Globus Compute remote-workflow interface (the hpc-bridge path).

hpc-bridge is an MCP server wrapping `globus_compute_sdk.Executor`, so the
reusable piece is its `GlobusRunner`, not its tools. This adapter targets that
API shape and keeps the same contract as the Orbit interface, with honest
capability flags:

  - no log streaming: only final stdout is returned, so `logs()` stays empty
  - cancel only before a task starts, which is all ComputeFuture allows

`executor_factory` is injectable so tests can drive this with a thread pool and
no Globus account; that is the same seam hpc-bridge's own tests use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..base import Capabilities, LogChunk, TaskHandle, TaskSpec, TaskState
from .base import RemoteWorkflowInterface

log = logging.getLogger(__name__)


class GlobusComputeInterface(RemoteWorkflowInterface):
    """Remote execution through Globus Compute."""

    name = "hpc"
    capabilities = Capabilities(
        supports_cancel=False,      # only before start; see cancel()
        supports_log_stream=False,  # final stdout only
        supports_push_events=False, # the future resolves; no intermediate states
        supports_staging=False,     # shared FS or a separate Globus Transfer
    )

    def __init__(
        self,
        *,
        endpoint_id: str = "",
        executor_factory: Callable[[], Any] | None = None,
        walltime: int | None = 3600,
        poll_interval: float = 2.0,
    ):
        super().__init__(poll_interval=poll_interval)
        self._endpoint_id = endpoint_id
        self._executor_factory = executor_factory
        self._walltime = walltime
        self._executor: Any = None

    @property
    def connected(self) -> bool:
        return self._executor is not None

    async def connect(self) -> None:
        if self._executor_factory is not None:
            self._executor = self._executor_factory()
            return
        if not self._endpoint_id:
            raise RuntimeError("no Globus Compute endpoint id configured")
        from globus_compute_sdk import Executor

        self._executor = await asyncio.to_thread(
            Executor, endpoint_id=self._endpoint_id
        )

    async def close(self) -> None:
        if self._executor is not None:
            shutdown = getattr(self._executor, "shutdown", None)
            if shutdown:
                await asyncio.to_thread(shutdown)
            self._executor = None

    async def submit(self, spec: TaskSpec) -> TaskHandle:
        if not self.connected:
            raise RuntimeError("Globus Compute interface is not connected")

        job_spec = spec.params.get("job_spec") or {}
        argv = _argv_of(spec, job_spec)

        compute_future = await asyncio.to_thread(self._submit_shell, argv)
        # Bridge the concurrent.futures.Future onto the event loop.
        future = asyncio.wrap_future(compute_future)
        handle = self._handle(spec, future, argv=argv)
        handle.state = TaskState.RUNNING
        handle.meta["compute_future"] = compute_future
        return self._track(handle)

    def _submit_shell(self, argv: list[str]):
        """Wrap the work in a ShellFunction, as hpc-bridge does."""
        try:
            from globus_compute_sdk import ShellFunction
        except ImportError:
            # Test path: a plain executor taking a callable. argv is passed as a
            # list and run without a shell, so no quoting is involved.
            return self._executor.submit(_run_argv, argv)

        # ShellFunction takes a command *string*, so each element is quoted:
        # argv may contain sequences or filenames derived from a user prompt.
        import shlex

        payload = ShellFunction(shlex.join(argv), walltime=self._walltime)
        return self._executor.submit(payload)

    async def logs(self, handle: TaskHandle, offset: int = 0) -> LogChunk:
        """Unsupported: Globus Compute returns output only at the end.

        The documented workaround is to redirect to a file and read it with a
        second task; that belongs in the job spec, not here.
        """
        return LogChunk(offset=offset)

    async def cancel(self, handle: TaskHandle) -> bool:
        compute_future = handle.meta.get("compute_future")
        if compute_future is None:
            return False
        # Succeeds only while the task is still queued.
        cancelled = compute_future.cancel()
        if cancelled:
            self._settle(handle, TaskState.CANCELED, error="canceled before start")
        return bool(cancelled)


def _argv_of(spec: TaskSpec, job_spec: dict[str, Any]) -> list[str]:
    """Build an argv list for the job.

    A list rather than a string: it is the only representation that cannot be
    reinterpreted by a shell, and the Globus path quotes it back into a string
    at the last moment.
    """
    argv = spec.params.get("argv")
    if isinstance(argv, (list, tuple)) and argv:
        return [str(a) for a in argv]
    return [
        str(job_spec.get("executable", "/bin/true")),
        *[str(a) for a in job_spec.get("arguments", [])],
    ]


def _run_argv(argv: list[str]) -> dict[str, Any]:
    """Module-level so it is picklable; used by the injectable test executor."""
    import subprocess

    proc = subprocess.run(argv, capture_output=True, text=True)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
