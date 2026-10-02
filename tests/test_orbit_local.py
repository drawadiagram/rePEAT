"""Orbit interface against a real localhost broker + endpoint.

Exercises the actual client path -- websocket, plugin sessions, pushed status
events, PSI/J log tailing by byte offset, and cancellation -- with no HPC
allocation. Skipped unless the Orbit CLI scripts are importable and runnable.

Run explicitly:
    pytest tests/test_orbit_local.py -q
"""

from __future__ import annotations

import asyncio

import pytest
from designagent.tasks.base import TaskSpec, TaskState
from designagent.tasks.hpc.base import drain_logs

pytestmark = pytest.mark.live


def _orbit_available() -> bool:
    try:
        import radical.orbit  # noqa: F401
    except ImportError:
        return False
    from designagent.tasks.hpc.local_orbit import _script

    return bool(_script("radical-orbit-broker.py") and _script("radical-orbit-endpoint.py"))


pytest.importorskip("radical.orbit", reason="radical.orbit is not installed")
if not _orbit_available():
    pytest.skip("Orbit CLI scripts not found", allow_module_level=True)


@pytest.fixture
async def orbit(tmp_path):
    """A connected OrbitInterface over a throwaway local stack."""
    from designagent.tasks.hpc.local_orbit import LocalOrbitStack
    from designagent.tasks.hpc.orbit import OrbitInterface

    stack = LocalOrbitStack(work_dir=tmp_path / "orbit")
    try:
        await stack.start(timeout=90)
    except Exception as exc:
        pytest.skip(f"could not start a local Orbit stack: {exc}")

    interface = OrbitInterface(
        broker_url=stack.broker_url,
        endpoint="local",
        # The broker serves TLS with a throwaway self-signed cert, so pin it.
        cert=str(stack.cert),
        rhapsody_backends=["concurrent"],
        poll_interval=1.0,
        connect_timeout=45.0,
    )
    try:
        await interface.connect()
    except Exception as exc:
        await stack.stop()
        pytest.skip(f"could not connect to the local Orbit stack: {exc}")

    yield interface
    await interface.close()
    await stack.stop()


async def test_connects_and_finds_the_endpoint(orbit):
    assert orbit.connected
    assert orbit.capabilities.supports_push_events
    assert orbit.capabilities.supports_log_stream


async def test_executable_task_runs_and_returns_output(orbit):
    spec = TaskSpec(
        name="echo",
        kind="function",
        params={"task_dict": {"executable": "/bin/echo", "arguments": ["hello-orbit"]}},
    )
    handle = await orbit.submit(spec)
    assert not handle.done  # submission returned immediately

    result = await asyncio.wait_for(handle.future, timeout=120)
    assert result["state"] == "DONE"
    assert result["exit_code"] in (0, None)
    assert "hello-orbit" in (result.get("stdout") or "")
    assert await orbit.status(handle) is TaskState.DONE


async def test_psij_job_streams_logs_by_offset(orbit):
    """The one place either backend can tail a running job."""
    job = {
        "executable": "/bin/bash",
        "arguments": [
            "-c",
            "for i in 1 2 3; do echo line-$i; sleep 1; done",
        ],
        "duration_sec": 120,
    }
    spec = TaskSpec(name="tail", kind="job", params={"job_spec": job, "executor": "local"})
    handle = await orbit.submit(spec)

    chunks: list[str] = []

    async def collect(_handle, text):
        chunks.append(text)

    drain = asyncio.ensure_future(drain_logs(orbit, handle, collect, interval=0.5))
    result = await asyncio.wait_for(handle.future, timeout=180)
    drain.cancel()

    assert result["state"] == "DONE"
    combined = "".join(chunks) + (result.get("stdout") or "")
    assert "line-1" in combined and "line-3" in combined
    # Offsets advanced, so the tail was incremental rather than refetched whole.
    assert handle.log_offset > 0


async def test_failing_job_is_reported_as_failed(orbit):
    spec = TaskSpec(
        name="fail",
        kind="job",
        params={
            "job_spec": {"executable": "/bin/bash", "arguments": ["-c", "exit 3"]},
            "executor": "local",
        },
    )
    handle = await orbit.submit(spec)
    with pytest.raises(Exception):
        await asyncio.wait_for(handle.future, timeout=120)
    assert handle.state is TaskState.FAILED
    assert handle.error


async def test_cancel_stops_a_running_job(orbit):
    spec = TaskSpec(
        name="sleeper",
        kind="job",
        params={
            "job_spec": {
                "executable": "/bin/sleep",
                "arguments": ["120"],
                "duration_sec": 300,
            },
            "executor": "local",
        },
    )
    handle = await orbit.submit(spec)
    await asyncio.sleep(3)  # let it actually start

    assert await orbit.cancel(handle) is True
    assert handle.state is TaskState.CANCELED


async def test_manager_routes_through_orbit(orbit, tmp_path):
    """The manager prefers the hpc interface when one is connected."""
    from designagent.config import Settings
    from designagent.lake.store import DesignHistory
    from designagent.tasks.manager import TaskManager

    history = DesignHistory(Settings(data_dir=tmp_path / "lake", anthropic_api_key=""))
    manager = TaskManager(hpc=orbit, history=history)
    try:
        assert manager.hpc_available
        name, _ = manager.interface_for(TaskSpec(name="proteinmpnn"))
        assert name == "hpc"
    finally:
        history.close()
