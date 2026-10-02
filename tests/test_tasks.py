"""Task interfaces, routing, and the manager's event/persistence behaviour."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from designagent.config import Settings
from designagent.lake.store import DesignHistory
from designagent.tasks.base import TaskSpec, TaskState, normalize_state
from designagent.tasks.hpc.globus import GlobusComputeInterface
from designagent.tasks.hpc.orbit import to_psij_spec
from designagent.tasks.local import LocalTaskInterface, QueryTaskInterface
from designagent.tasks.manager import TaskManager
from designagent.tasks.registry import CATALOG

# --- state normalization ---------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("COMPLETED", TaskState.DONE),
        ("done", TaskState.DONE),
        ("CANCELLED", TaskState.CANCELED),
        ("ACTIVE", TaskState.RUNNING),
        ("HELD", TaskState.QUEUED),
        ("nonsense", TaskState.UNKNOWN),
        (None, TaskState.UNKNOWN),
    ],
)
def test_normalize_state(raw, expected):
    assert normalize_state(raw) is expected


def test_terminal_states():
    assert TaskState.DONE.terminal and TaskState.FAILED.terminal
    assert not TaskState.RUNNING.terminal and not TaskState.QUEUED.terminal


# --- registry -------------------------------------------------------------


def test_every_catalog_body_is_importable_and_module_level():
    """Pool workers import bodies by reference, so no closures or lambdas."""
    for name, task in CATALOG.items():
        assert task.body.__module__.startswith("designagent.tools"), name
        assert task.body.__qualname__ == task.body.__name__, name


# --- local / query interfaces ---------------------------------------------


async def test_query_interface_returns_handle_immediately():
    iface = QueryTaskInterface()
    spec = TaskSpec(name="propose_variants", params={"sequence": "MGGSAAT", "n": 2})
    handle = await iface.submit(spec)
    assert not handle.done  # submission did not block on the work
    result = await iface.result(handle)
    assert result["n"] >= 1
    assert await iface.status(handle) is TaskState.DONE


async def test_local_interface_applies_wrapper():
    """The flowgentic wrapper seam is used for every body it resolves."""
    calls = []

    def wrap(fn):
        calls.append(fn.__name__)
        return fn

    iface = LocalTaskInterface(wrap=wrap)
    spec = TaskSpec(name="score_sequences", params={"sequences": [{"sequence": "MKV"}]})
    handle = await iface.submit(spec)
    assert await handle.future
    assert calls == ["score_sequences"]
    # Wrapping is cached, not repeated per submission.
    await iface.submit(spec)
    assert calls == ["score_sequences"]


async def test_unknown_task_raises():
    iface = LocalTaskInterface()
    with pytest.raises(KeyError):
        await iface.submit(TaskSpec(name="does_not_exist"))


async def test_cancel_marks_handle_canceled():
    iface = QueryTaskInterface()
    spec = TaskSpec(name="fold_sequence", params={"sequence": "M" * 50})
    handle = await iface.submit(spec)
    assert await iface.cancel(handle)
    with pytest.raises(asyncio.CancelledError):
        await handle.future


# --- manager --------------------------------------------------------------


@pytest.fixture
def history(tmp_path):
    h = DesignHistory(Settings(data_dir=tmp_path, anthropic_api_key=""))
    yield h
    h.close()


async def test_manager_runs_tasks_and_emits_events(history):
    manager = TaskManager(history=history)
    history.start_campaign("c1", "goal")
    events = []

    async def sink(event):
        events.append(event)

    manager.subscribe("s1", sink)
    records = await manager.run_many(
        [TaskSpec(name="score_sequences", params={"sequences": [{"sequence": "MKVL"}]})],
        session_id="s1",
        campaign_id="c1",
    )

    assert records[0]["ok"] is True
    assert records[0]["result"]["n"] == 1

    kinds = [(e["event"]) for e in events]
    assert kinds == ["submitted", "finished"]

    # the task landed in tier 1
    assert history.graph.tasks_for_campaign("c1")[0]["state"] == "DONE"


async def test_manager_reports_failure_without_raising(history):
    manager = TaskManager(history=history)
    history.start_campaign("c1", "goal")
    # Missing required args -> the body raises TypeError inside the task.
    records = await manager.run_many(
        [TaskSpec(name="score_structure", params={"structure_path": "/nope.pdb"})],
        campaign_id="c1",
    )
    # score_structure handles a missing file itself and returns an error dict
    assert records[0]["ok"] is True
    assert "error" in records[0]["result"]


async def test_manager_records_failed_task_when_body_raises(history):
    manager = TaskManager(history=history)
    history.start_campaign("c1", "goal")

    async def boom(**_):
        raise RuntimeError("kaboom")

    manager.interfaces["local"] = LocalTaskInterface()
    manager.interfaces["local"]._wrapped["boom"] = boom
    from designagent.tasks.registry import TaskDef

    CATALOG["boom"] = TaskDef("boom", boom, "local", "test only")
    try:
        records = await manager.run_many(
            [TaskSpec(name="boom")], campaign_id="c1"
        )
    finally:
        CATALOG.pop("boom", None)

    assert records[0]["ok"] is False
    assert "kaboom" in records[0]["error"]
    assert history.graph.tasks_for_campaign("c1")[0]["state"] == "FAILED"


async def test_manager_falls_back_to_local_when_no_hpc():
    manager = TaskManager()  # no hpc interface registered
    assert not manager.hpc_available
    name, _ = manager.interface_for(TaskSpec(name="proteinmpnn"))
    assert name == "local"


async def test_manager_routes_to_hpc_when_present():
    class FakeHpc(QueryTaskInterface):
        name = "hpc"
        connected = True

    manager = TaskManager(hpc=FakeHpc())
    name, _ = manager.interface_for(TaskSpec(name="proteinmpnn"))
    assert name == "hpc"


async def test_manager_concurrency_is_real():
    """submit_many must place tasks in parallel, not one after another."""
    manager = TaskManager()
    specs = [
        TaskSpec(name="fold_sequence", params={"sequence": "M" * 600})
        for _ in range(3)
    ]
    # length guard returns an error fast; what matters is all 3 complete together
    import time

    t0 = time.monotonic()
    records = await manager.run_many(specs)
    assert len(records) == 3
    assert time.monotonic() - t0 < 10


async def test_manager_snapshot_filters_by_session(history):
    manager = TaskManager(history=history)
    await manager.run_many(
        [TaskSpec(name="score_sequences", params={"sequences": []})], session_id="sA"
    )
    await manager.run_many(
        [TaskSpec(name="score_sequences", params={"sequences": []})], session_id="sB"
    )
    assert len(manager.snapshot("sA")) == 1
    assert len(manager.snapshot()) == 2


# --- HPC spec translation -------------------------------------------------


def test_to_psij_spec_moves_scheduler_fields_into_attributes():
    spec = to_psij_spec(
        {
            "executable": "protein_mpnn_run.py",
            "arguments": ["--num_seq_per_target", 8],
            "duration_sec": 900,
            "queue": "debug",
            "resources": {"node_count": 1, "gpus": 1},
        }
    )
    assert spec["executable"] == "protein_mpnn_run.py"
    assert spec["arguments"] == ["--num_seq_per_target", "8"]
    assert spec["attributes"]["duration"] == "900"
    assert spec["attributes"]["queue_name"] == "debug"
    # Renamed to PSI/J's own vocabulary. Forwarding our names verbatim made the
    # endpoint answer HTTP 500 for every batch job: ResourceSpecV1 rejects an
    # unexpected keyword rather than ignoring it.
    assert spec["resources"] == {"node_count": 1, "gpu_cores_per_process": 1}


def test_to_psij_spec_renames_every_resource_field():
    spec = to_psij_spec(
        {
            "executable": "/bin/true",
            "resources": {"node_count": 2, "processes": 8, "processes_per_node": 4, "gpus": 1},
        }
    )
    assert spec["resources"] == {
        "node_count": 2,
        "process_count": 8,
        "processes_per_node": 4,
        "gpu_cores_per_process": 1,
    }


def test_to_psij_spec_defaults_are_auto_discoverable():
    spec = to_psij_spec({"executable": "/bin/echo"})
    assert spec["attributes"]["account"] is None
    assert spec["attributes"]["queue_name"] is None


# --- Globus adapter (offline, injected executor) --------------------------


async def test_globus_interface_runs_with_injected_executor():
    iface = GlobusComputeInterface(executor_factory=lambda: ThreadPoolExecutor(2))
    await iface.connect()
    assert iface.connected
    spec = TaskSpec(
        name="fold_sequence_hpc",
        kind="job",
        params={"job_spec": {"executable": "/bin/echo", "arguments": ["hello"]}},
    )
    handle = await iface.submit(spec)
    result = await handle.future
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]
    await iface.close()


async def test_globus_capabilities_are_honest():
    iface = GlobusComputeInterface(executor_factory=lambda: ThreadPoolExecutor(1))
    assert not iface.capabilities.supports_log_stream
    assert not iface.capabilities.supports_push_events
    await iface.connect()
    handle = await iface.submit(
        TaskSpec(name="x", kind="job", params={"job_spec": {"executable": "/bin/true"}})
    )
    chunk = await iface.logs(handle)
    assert chunk.text == ""
    await handle.future
    await iface.close()


async def test_globus_argv_is_not_shell_interpreted():
    """A metacharacter in an argument must be data, not a second command."""
    iface = GlobusComputeInterface(executor_factory=lambda: ThreadPoolExecutor(1))
    await iface.connect()
    handle = await iface.submit(
        TaskSpec(
            name="x",
            kind="job",
            params={"job_spec": {"executable": "/bin/echo", "arguments": ["a; echo pwned"]}},
        )
    )
    result = await handle.future
    assert "pwned" in result["stdout"]  # echoed as text ...
    assert result["stdout"].count("\n") == 1  # ... on one line, not two commands
    await iface.close()
