"""RADICAL Orbit remote-workflow interface.

Two Orbit plugins are used for different shapes of work:

  rhapsody - function and short executable tasks. Status arrives as pushed
             `task_status` events, so completion needs no polling.
  psij     - batch jobs. `get_job_status` takes byte offsets, which is the only
             way either backend can tail logs while a job runs.

Every Orbit client call is synchronous and blocking (they use `threading.Event`
and HTTP-over-websocket), so all of them are wrapped in `asyncio.to_thread`.
Push callbacks arrive on Orbit's listener thread and are marshalled back onto
the event loop with `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..base import Capabilities, LogChunk, TaskHandle, TaskSpec, TaskState, normalize_state
from .base import RemoteWorkflowInterface

log = logging.getLogger(__name__)

TERMINAL = {"DONE", "FAILED", "CANCELED", "CANCELLED", "COMPLETED"}


def to_psij_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Translate our generic job spec into a PSI/J job spec.

    PSI/J puts scheduler concerns under `attributes` and wants `duration` as a
    string, which our tool modules should not have to know.
    """
    resources = spec.get("resources") or {}
    attributes: dict[str, Any] = {
        "duration": str(spec.get("duration_sec", 1800)),
        # None means "auto-discover at the endpoint".
        "account": spec.get("account"),
        "queue_name": spec.get("queue"),
    }
    attributes.update(spec.get("attributes") or {})

    out: dict[str, Any] = {
        "executable": spec.get("executable", "/bin/true"),
        "arguments": [str(a) for a in spec.get("arguments", [])],
        "attributes": attributes,
    }
    for key in ("node_count", "processes", "processes_per_node", "gpus"):
        if key in resources:
            out.setdefault("resources", {})[key] = resources[key]
    if spec.get("environment"):
        out["environment"] = spec["environment"]
    if spec.get("directory"):
        out["directory"] = spec["directory"]
    return out


class OrbitInterface(RemoteWorkflowInterface):
    """Remote execution through an Orbit broker and endpoint."""

    name = "hpc"
    capabilities = Capabilities(
        supports_cancel=True,
        supports_log_stream=True,   # via the psij plugin's offset reads
        supports_push_events=True,  # via rhapsody/psij notifications
        supports_staging=True,      # the staging/globus plugins
    )

    def __init__(
        self,
        *,
        broker_url: str = "",
        endpoint: str = "",
        token: str = "",
        cert: str = "",
        rhapsody_backends: list[str] | None = None,
        poll_interval: float = 2.0,
        connect_timeout: float = 30.0,
        name: str = "designagent",
    ):
        super().__init__(poll_interval=poll_interval)
        self._connect_timeout = connect_timeout
        self._broker_url = broker_url
        self._endpoint_hint = endpoint
        self._token = token
        self._cert = cert
        self._rhapsody_backends = rhapsody_backends
        self._client_name = name

        self._rt: Any = None
        self._rhapsody: Any = None
        self._psij: Any = None
        self._endpoint: str = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pollers: dict[str, asyncio.Task] = {}

    # --- lifecycle ----------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._rt is not None and bool(self._endpoint)

    async def connect(self) -> None:
        """Start the runtime, find an endpoint, and open plugin sessions."""
        from radical.orbit import EndpointRuntime

        self._loop = asyncio.get_running_loop()

        kwargs: dict[str, Any] = {"name": self._client_name}
        if self._broker_url:
            kwargs["broker_url"] = self._broker_url
        if self._token:
            kwargs["token"] = self._token
        if self._cert:
            kwargs["cert"] = self._cert

        rt = EndpointRuntime(**kwargs)
        await asyncio.to_thread(rt.start, wait=True)
        self._rt = rt

        # The broker's topology propagates asynchronously, so an endpoint that
        # has just registered may not be visible on the first look.
        topology: dict = {}
        deadline = asyncio.get_running_loop().time() + self._connect_timeout
        while True:
            topology = await asyncio.to_thread(rt.topology)
            self._endpoint = self._pick_endpoint(topology)
            if self._endpoint:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(
                    "no Orbit endpoint with a rhapsody or psij plugin found "
                    f"(topology: {sorted(topology or {})})"
                )
            await asyncio.sleep(1.0)

        plugins = (topology.get(self._endpoint) or {}).get("plugins") or {}

        if "rhapsody" in plugins:
            self._rhapsody = await asyncio.to_thread(
                rt.get_plugin, self._endpoint, "rhapsody"
            )
            await asyncio.to_thread(
                self._rhapsody.register_session, self._rhapsody_backends
            )
            await asyncio.to_thread(
                self._rhapsody.register_notification_callback, self._on_rhapsody_event
            )
        if "psij" in plugins:
            self._psij = await asyncio.to_thread(rt.get_plugin, self._endpoint, "psij")
            await asyncio.to_thread(self._psij.register_session)
            await asyncio.to_thread(
                self._psij.register_notification_callback, self._on_psij_event
            )
        log.info(
            "Orbit connected: endpoint=%s plugins=%s",
            self._endpoint,
            sorted(set(plugins) & {"rhapsody", "psij"}),
        )

    def _pick_endpoint(self, topology: dict) -> str:
        candidates = [
            name
            for name, info in (topology or {}).items()
            if info.get("role") == "endpoint"
            and {"rhapsody", "psij"} & set((info.get("plugins") or {}))
        ]
        if self._endpoint_hint:
            for name in candidates:
                if self._endpoint_hint in name:
                    return name
            log.warning(
                "endpoint hint %r matched nothing; candidates=%s",
                self._endpoint_hint,
                candidates,
            )
        return candidates[0] if candidates else ""

    async def close(self) -> None:
        for poller in self._pollers.values():
            poller.cancel()
        self._pollers.clear()
        for client in (self._rhapsody, self._psij):
            if client is not None:
                try:
                    await asyncio.to_thread(client.close)
                except Exception as exc:
                    log.debug("closing Orbit plugin client: %s", exc)
        if self._rt is not None:
            try:
                await asyncio.to_thread(self._rt.stop)
            except Exception as exc:
                log.debug("stopping Orbit runtime: %s", exc)
        self._rt = self._rhapsody = self._psij = None
        self._endpoint = ""

    # --- submission ---------------------------------------------------
    async def submit(self, spec: TaskSpec) -> TaskHandle:
        if not self.connected:
            raise RuntimeError("Orbit interface is not connected")

        if spec.kind == "job":
            return await self._submit_job(spec)
        return await self._submit_task(spec)

    async def _submit_task(self, spec: TaskSpec) -> TaskHandle:
        """A rhapsody function/executable task; completion arrives by event."""
        if self._rhapsody is None:
            raise RuntimeError("endpoint has no rhapsody plugin")

        payload = dict(spec.params.get("task_dict") or {})
        if not payload:
            payload = {
                "executable": spec.params.get("executable", "/bin/true"),
                "arguments": [str(a) for a in spec.params.get("arguments", [])],
            }
        acks = await asyncio.to_thread(self._rhapsody.submit_tasks, [payload])
        if not acks:
            raise RuntimeError("Orbit accepted no tasks")

        uid = str(acks[0]["uid"])
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        handle = self._handle(spec, future, task_id=uid, uid=uid, plugin="rhapsody")
        handle.state = normalize_state(acks[0].get("state")) or TaskState.QUEUED
        self._track(handle)
        # Events are the primary completion signal; poll as a safety net in case
        # the notification is missed while the listener reconnects.
        self._pollers[uid] = asyncio.ensure_future(self._poll_task(handle))
        return handle

    async def _submit_job(self, spec: TaskSpec) -> TaskHandle:
        """A PSI/J batch job; status and logs come from polling by offset."""
        if self._psij is None:
            raise RuntimeError("endpoint has no psij plugin")

        job_spec = spec.params.get("job_spec") or {}
        executor = spec.params.get("executor", "local")
        resp = await asyncio.to_thread(
            self._psij.submit_job, to_psij_spec(job_spec), executor
        )
        job_id = str(resp["job_id"])

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        handle = self._handle(
            spec,
            future,
            task_id=job_id,
            job_id=job_id,
            plugin="psij",
            native_id=resp.get("native_id"),
            stdout_offset=0,
            stderr_offset=0,
        )
        handle.state = TaskState.QUEUED
        self._track(handle)
        self._pollers[job_id] = asyncio.ensure_future(self._poll_job(handle))
        return handle

    # --- push events --------------------------------------------------
    def _dispatch(self, fn, *args) -> None:
        """Hop from Orbit's listener thread onto our event loop."""
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(fn, *args)

    def _on_rhapsody_event(self, endpoint, plugin, topic, data) -> None:
        if topic == "task_status_batch":
            for item in (data or {}).get("tasks", []):
                self._dispatch(self._apply_task_status, item)
        elif topic == "task_status":
            self._dispatch(self._apply_task_status, data or {})

    def _on_psij_event(self, endpoint, plugin, topic, data) -> None:
        if topic == "job_status":
            self._dispatch(self._apply_job_status, data or {})

    def _apply_task_status(self, data: dict) -> None:
        handle = self._handles.get(str(data.get("uid", "")))
        if handle is None:
            return
        state = normalize_state(data.get("state"))
        if not state.terminal:
            handle.state = state
            return
        # The terminal event carries state and exit_code but not stdout, so the
        # full record is fetched before the future is resolved.
        asyncio.ensure_future(self._finish_task_enriched(handle, data, state))

    async def _finish_task_enriched(
        self, handle: TaskHandle, data: dict, state: TaskState
    ) -> None:
        merged = dict(data)
        if not data.get("stdout") and self._rhapsody is not None:
            try:
                info = await asyncio.to_thread(self._rhapsody.get_task, handle.id)
                if isinstance(info, dict):
                    # The event wins on state; the fetch fills in the output.
                    merged = {**info, **{k: v for k, v in data.items() if v not in (None, "")}}
            except Exception as exc:
                log.debug("fetching final task record for %s failed: %s", handle.id, exc)
        self._finish_task(handle, merged, state)

    def _apply_job_status(self, data: dict) -> None:
        handle = self._handles.get(str(data.get("job_id", "")))
        if handle is None:
            return
        state = normalize_state(data.get("state"))
        if not state.terminal:
            handle.state = state
            return
        self._finish_job(handle, data, state)

    # --- completion ---------------------------------------------------
    def _finish_task(self, handle: TaskHandle, data: dict, state: TaskState) -> None:
        exit_code = data.get("exit_code")
        error = data.get("error") or data.get("exception") or ""
        result = {
            "uid": handle.id,
            "state": state.value,
            "exit_code": exit_code,
            "return_value": data.get("return_value"),
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
        }
        if state is TaskState.DONE and exit_code not in (None, 0):
            state = TaskState.FAILED
        if state is TaskState.FAILED and not error:
            # Orbit often reports only a non-zero exit code, so say that rather
            # than surfacing an empty error to the user.
            error = (
                f"exit code {exit_code}" if exit_code not in (None, 0)
                else (result.get("stderr") or "task failed without a message")
            )
        self._settle(handle, state, result, error)
        self._stop_poller(handle.id)

    def _finish_job(self, handle: TaskHandle, data: dict, state: TaskState) -> None:
        exit_code = data.get("exit_code")
        error = data.get("error") or ""
        stdout = data.get("stdout", "")
        if stdout:
            handle.log_tail = (handle.log_tail + stdout)[-8000:]
        result = {
            "job_id": handle.id,
            "state": state.value,
            "exit_code": exit_code,
            "stdout": handle.log_tail,
            "stderr": data.get("stderr", ""),
        }
        if state is TaskState.DONE and exit_code not in (None, 0):
            state = TaskState.FAILED
        if state is TaskState.FAILED and not error:
            error = (
                f"exit code {exit_code}" if exit_code not in (None, 0)
                else (data.get("stderr") or handle.log_tail[-500:] or "job failed")
            )
        self._settle(handle, state, result, error)
        self._stop_poller(handle.id)

    def _stop_poller(self, task_id: str) -> None:
        poller = self._pollers.pop(task_id, None)
        if poller is not None:
            poller.cancel()

    # --- polling fallbacks --------------------------------------------
    async def _poll_task(self, handle: TaskHandle) -> None:
        try:
            while not handle.future.done():
                await asyncio.sleep(self.poll_interval)
                if handle.future.done():
                    return
                try:
                    info = await asyncio.to_thread(self._rhapsody.get_task, handle.id)
                except Exception as exc:
                    log.debug("poll task %s: %s", handle.id, exc)
                    continue
                state = normalize_state(info.get("state"))
                if state.terminal:
                    self._finish_task(handle, info, state)
                    return
                handle.state = state
        except asyncio.CancelledError:
            raise

    async def _poll_job(self, handle: TaskHandle) -> None:
        try:
            while not handle.future.done():
                await asyncio.sleep(self.poll_interval)
                if handle.future.done():
                    return
                try:
                    info = await asyncio.to_thread(
                        self._psij.get_job_status,
                        handle.id,
                        handle.meta.get("stdout_offset", 0),
                        handle.meta.get("stderr_offset", 0),
                    )
                except Exception as exc:
                    log.debug("poll job %s: %s", handle.id, exc)
                    continue
                chunk = info.get("stdout") or ""
                if chunk:
                    handle.meta["stdout_offset"] = (
                        handle.meta.get("stdout_offset", 0) + len(chunk.encode())
                    )
                    handle.log_tail = (handle.log_tail + chunk)[-8000:]
                state = normalize_state(info.get("state"))
                if state.terminal:
                    self._finish_job(handle, info, state)
                    return
                handle.state = state
        except asyncio.CancelledError:
            raise

    # --- introspection ------------------------------------------------
    async def status(self, handle: TaskHandle) -> TaskState:
        if handle.future.done():
            return await super().status(handle)
        return handle.state

    async def logs(self, handle: TaskHandle, offset: int = 0) -> LogChunk:
        """Incremental stdout. Only PSI/J jobs can be tailed mid-run."""
        if handle.meta.get("plugin") != "psij" or self._psij is None:
            return LogChunk(offset=offset)
        try:
            info = await asyncio.to_thread(
                self._psij.get_job_status, handle.id, offset, 0
            )
        except Exception as exc:
            log.debug("log read for %s failed: %s", handle.id, exc)
            return LogChunk(offset=offset)
        text = info.get("stdout") or ""
        return LogChunk(text=text, offset=offset + len(text.encode()))

    async def cancel(self, handle: TaskHandle) -> bool:
        plugin = handle.meta.get("plugin")
        try:
            if plugin == "psij" and self._psij is not None:
                await asyncio.to_thread(self._psij.cancel_job, handle.id)
            elif plugin == "rhapsody" and self._rhapsody is not None:
                await asyncio.to_thread(self._rhapsody.cancel_task, handle.id)
            else:
                return False
        except Exception as exc:
            log.warning("cancel %s failed: %s", handle.id, exc)
            return False
        self._settle(handle, TaskState.CANCELED, error="canceled by user")
        self._stop_poller(handle.id)
        return True
