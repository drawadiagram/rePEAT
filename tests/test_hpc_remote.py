"""One real HPC submission, against whatever endpoint the environment names.

This is the tier that answers backlog A1: *no HPC endpoint has ever executed a
task for this agent*. `test_orbit_local.py` proves the client against a localhost
broker we start ourselves; this proves the path against an endpoint we do not
own — a scheduler, a queue, an allocation.

Deliberately trivial jobs. The question is whether submit → poll → logs → cancel
work across a real broker, not whether ProteinMPNN is installed at the far end.

Run it:

    DESIGNAGENT_ORBIT_ENABLED=true \\
    RADICAL_ORBIT_BROKER_URL=https://broker.site:8443 \\
    RADICAL_ORBIT_BROKER_CERT=/path/broker_cert.pem \\
    RADICAL_ORBIT_BROKER_TOKEN=... \\
    DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm \\
    DESIGNAGENT_ORBIT_ACCOUNT=... DESIGNAGENT_ORBIT_QUEUE=... \\
    .venv/bin/python -m pytest -m remote -q

Nothing is hardcoded to localhost, so `DESIGNAGENT_ORBIT_LOCAL=true` runs the
same assertions against the development stack first. Check the credentials with
`python -m designagent --check-config --probe` before spending a queue slot.
"""

from __future__ import annotations

import asyncio

import pytest
from designagent.config import Settings
from designagent.tasks.base import TaskSpec, TaskState
from designagent.tasks.hpc.base import drain_logs

pytestmark = pytest.mark.remote

# A real queue may make us wait. These are ceilings, not expectations.
SUBMIT_TIMEOUT = 300.0
JOB_TIMEOUT = 900.0


@pytest.fixture(scope="module")
async def remote_settings(tmp_path_factory):
    """Where to submit: a real broker, or the development stack as a rehearsal.

    With `DESIGNAGENT_ORBIT_LOCAL=true` this starts the same localhost stack the
    server would, so the assertions below can be proven before a queue slot is
    spent. Everything else about the tier is identical — nothing here knows
    whether the endpoint is on this machine.
    """
    from designagent.runtime import _start_local_stack, _stop_local_stack, _with_local_stack

    settings = Settings()
    if settings.orbit_local_stack:
        settings = settings.model_copy(
            update={"data_dir": tmp_path_factory.mktemp("orbit")}
        )
        stack, error = await _start_local_stack(settings)
        if stack is None:
            pytest.skip(f"the development Orbit stack did not start: {error}")
        try:
            yield _with_local_stack(settings, stack)
        finally:
            await _stop_local_stack(stack)
        return

    if not settings.orbit_enabled:
        pytest.skip(
            "no endpoint configured: set DESIGNAGENT_ORBIT_ENABLED with the "
            "RADICAL_ORBIT_* values, or DESIGNAGENT_ORBIT_LOCAL=true to rehearse"
        )
    if not settings.orbit_broker_url:
        pytest.skip("RADICAL_ORBIT_BROKER_URL is not set")
    yield settings


@pytest.fixture(scope="module")
async def orbit(remote_settings):
    """The real interface, built the way the app builds it."""
    from designagent.runtime import _make_orbit

    interface, error = await _make_orbit(remote_settings)
    if interface is None:
        pytest.skip(f"could not connect to the Orbit broker: {error}")
    yield interface
    await interface.close()


async def test_the_endpoint_is_real_and_names_itself(orbit, remote_settings):
    assert orbit.connected
    assert orbit.endpoint_name
    # A hint is a substring, not an id: say which endpoint actually answered.
    print(f"\nendpoint: {orbit.endpoint_name} via {remote_settings.orbit_broker_url}")


async def test_a_rhapsody_task_runs_at_the_far_end(orbit):
    """The simplest possible remote execution: did our bytes run over there?"""
    spec = TaskSpec(
        name="hostname",
        kind="function",
        params={"task_dict": {"executable": "/bin/hostname", "arguments": []}},
    )
    handle = await orbit.submit(spec)
    assert not handle.done  # submission returns immediately, by contract

    result = await asyncio.wait_for(handle.future, timeout=SUBMIT_TIMEOUT)
    assert result["state"] == "DONE", result
    remote_host = (result.get("stdout") or "").strip()
    assert remote_host
    print(f"\nran on: {remote_host}")
    assert await orbit.status(handle) is TaskState.DONE


async def test_a_psij_job_goes_through_the_scheduler(orbit, remote_settings):
    """A batch job with the site's executor, account and queue, and its logs."""
    job = {
        "name": "designagent-preflight",
        "executable": "/bin/bash",
        "arguments": ["-c", "echo start; sleep 5; echo done"],
        "duration_sec": min(600, remote_settings.orbit_job_duration_sec),
        "resources": {"node_count": 1, "processes": 1},
    }
    if remote_settings.orbit_account:
        job["account"] = remote_settings.orbit_account
    if remote_settings.orbit_queue:
        job["queue"] = remote_settings.orbit_queue

    spec = TaskSpec(
        name="preflight",
        kind="job",
        params={"job_spec": job, "executor": remote_settings.orbit_psij_executor},
    )
    handle = await orbit.submit(spec)

    chunks: list[str] = []

    async def collect(_handle, text):
        chunks.append(text)

    drain = asyncio.ensure_future(drain_logs(orbit, handle, collect, interval=2.0))
    try:
        result = await asyncio.wait_for(handle.future, timeout=JOB_TIMEOUT)
    finally:
        drain.cancel()

    assert result["state"] == "DONE", result
    combined = "".join(chunks) + (result.get("stdout") or "")
    assert "start" in combined and "done" in combined


async def test_a_queued_job_can_be_cancelled(orbit, remote_settings):
    """Cancellation has to reach the scheduler, not just drop our future."""
    job = {
        "name": "designagent-cancel",
        "executable": "/bin/bash",
        "arguments": ["-c", "sleep 600"],
        "duration_sec": min(900, remote_settings.orbit_job_duration_sec),
    }
    if remote_settings.orbit_account:
        job["account"] = remote_settings.orbit_account
    if remote_settings.orbit_queue:
        job["queue"] = remote_settings.orbit_queue

    spec = TaskSpec(
        name="cancel-me",
        kind="job",
        params={"job_spec": job, "executor": remote_settings.orbit_psij_executor},
    )
    handle = await orbit.submit(spec)
    await asyncio.sleep(3)  # let the scheduler actually place it

    assert await orbit.cancel(handle) is True
    # The contract is the handle's state, not a settled result: cancelling
    # cancels the future itself, so awaiting it raises rather than returning.
    assert handle.state is TaskState.CANCELED
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handle.future, timeout=SUBMIT_TIMEOUT)


async def test_the_manager_routes_hpc_work_to_this_endpoint(orbit, remote_settings):
    """The graph's view: `interface_for` must pick hpc, with a real job spec."""
    from designagent.graph.deps import Deps
    from designagent.tasks.manager import TaskManager

    manager = TaskManager(hpc=orbit)
    assert manager.hpc_available

    # _job_params is what turns `to_psij_spec({})` — /bin/true — into a command.
    from designagent.graph.nodes.orchestrator import _batch_timeout, _job_params

    deps = Deps(settings=remote_settings, tasks=manager, history=None, artifacts=None)
    params = _job_params(deps, {"executable": "/bin/echo", "arguments": ["routed"]})
    assert params["executor"] == remote_settings.orbit_psij_executor
    assert params["job_spec"]["duration_sec"] == remote_settings.orbit_job_duration_sec

    spec = TaskSpec(name="proteinmpnn", kind="job", params={**params, "_interface": "hpc"})
    # The timeout must outlast the job's own walltime, or we fail a queued job.
    assert (_batch_timeout(deps, [spec]) or 0) > remote_settings.orbit_job_duration_sec

    name, interface = manager.interface_for(spec)
    assert name == "hpc"
    handle = await interface.submit(spec)
    result = await asyncio.wait_for(handle.future, timeout=JOB_TIMEOUT)
    assert result["state"] == "DONE", result
    assert "routed" in (result.get("stdout") or "")
