"""Where a tool's job spec meets the site's scheduler.

A tool module builds a job spec describing *what to run*: the executable, the
arguments, the files it needs and produces. It cannot know *where* — the
allocation, the queue, how many GPUs a site will give a job, how long it will
let one run. Those come from settings, and this is the one place the two meet.

Settings are **defaults, not overrides.** A spec that declares its own
`duration_sec`, `resources.gpus` or `executor` means it: hhblits needs twelve
hours and no GPU, AlphaFold3 needs one GPU and four, and a job whose only work
is to copy three files out of a project directory should run on the `local`
executor rather than wait in a queue. A spec that declares none gets the site's
answer for all three, which is what every caller did before the protocol
existed.

This used to live in `graph/nodes/orchestrator.py`. It moved so that a node is
not importing another node to reach it -- nodes reach the outside through `Deps`
(see `CLAUDE.md`), and a shared helper between two of them belongs below both.
It takes `Settings` rather than `Deps` for the same reason: nothing here needs a
task manager, a lake or a history.
"""

from __future__ import annotations

from typing import Any, Iterable

from ..config import Settings
from .base import TaskSpec

# Headroom over a job's own walltime before the manager stops waiting for it.
# Not a guess at queue time -- `gather` shields the future, so this only decides
# when we stop watching, never when the scheduler starts or cancels anything.
TIMEOUT_MARGIN_SEC = 300


def job_params(settings: Settings, job_spec: dict[str, Any]) -> dict[str, Any]:
    """Build the `params` dict `OrbitInterface._submit_job` reads.

    It wants `job_spec` and `executor`. Everything the site decides is filled in
    here unless the spec already said, per the module docstring.
    """
    spec = dict(job_spec)

    spec.setdefault("duration_sec", settings.orbit_job_duration_sec)
    if settings.orbit_account:
        spec.setdefault("account", settings.orbit_account)
    if settings.orbit_queue:
        spec.setdefault("queue", settings.orbit_queue)

    # Omitting the GPU key is not the same as sending zero. `to_psij_spec` is
    # presence-based, and a site that is handed `gpu_cores_per_process: 0` may
    # render `--gres=gpu:0` rather than no request at all -- so zero, from
    # either source, means "do not ask", and only a positive number is sent.
    resources = dict(spec.get("resources") or {})
    requested = resources.pop("gpus", None)
    if requested is None:
        requested = settings.orbit_job_gpus
    if int(requested) > 0:
        resources["gpus"] = int(requested)
    spec["resources"] = resources

    executor = spec.pop("executor", "") or settings.orbit_psij_executor
    return {"job_spec": spec, "executor": executor}


def batch_timeout(settings: Settings, specs: Iterable[TaskSpec]) -> float | None:
    """The manager's ceiling for a batch, widened when it contains a real job.

    `task_timeout_sec` defaults to 900 s, which is shorter than a job's own
    walltime: a queued job would be failed by us before the scheduler had
    started it. A queued job is not a late job.

    The widening keys off the *submitted specs'* own durations, not the global
    setting, because the protocol's jobs differ by more than an order of
    magnitude -- a twelve-hour hhblits run against a 1800 s default would be
    abandoned after 35 minutes, and the task chip would say FAILED about a job
    that was still queued.
    """
    base = settings.task_timeout_sec
    specs = list(specs)
    jobs = [spec for spec in specs if spec.kind == "job"]
    if base is None or not jobs:
        return base
    longest = max(
        (
            int((spec.params.get("job_spec") or {}).get("duration_sec", 0))
            for spec in jobs
        ),
        default=0,
    )
    return max(base, max(longest, settings.orbit_job_duration_sec) + TIMEOUT_MARGIN_SEC)
