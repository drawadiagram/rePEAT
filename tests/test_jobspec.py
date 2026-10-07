"""Where a tool's job spec meets the site's scheduler.

Two things to prove. The first is that nothing changed for the callers that
existed before the protocol did: `mpnn_job_spec` and `fold_job_spec` declare
neither a walltime nor a GPU count, so both still come from settings. The second
is that a spec which *does* declare them is taken at its word -- which is the
whole point of the change, because hhblits needs twelve hours and no GPU where
AlphaFold3 needs four and one.
"""

from __future__ import annotations

from designagent.config import Settings
from designagent.tasks.base import TaskSpec
from designagent.tasks.jobspec import batch_timeout, job_params
from designagent.tools.esmfold import fold_job_spec
from designagent.tools.proteinmpnn import mpnn_job_spec

from tests.conftest import PDB_TEXT


def site(**overrides) -> Settings:
    """A site that answers every question, so a default is visible when used."""
    base = {
        "orbit_job_duration_sec": 1800,
        "orbit_job_gpus": 1,
        "orbit_psij_executor": "slurm",
        "orbit_account": "acct",
        "orbit_queue": "main",
        "task_timeout_sec": 900.0,
    }
    return Settings(**{**base, **overrides})


def job(**params) -> TaskSpec:
    return TaskSpec(name="t", params=params, kind="job")


# --- the site fills in what a spec leaves out ------------------------------


def test_the_site_supplies_the_walltime_queue_account_and_gpus():
    got = job_params(site(), {"executable": "/bin/true"})
    assert got["executor"] == "slurm"
    assert got["job_spec"]["duration_sec"] == 1800
    assert got["job_spec"]["account"] == "acct"
    assert got["job_spec"]["queue"] == "main"
    assert got["job_spec"]["resources"] == {"gpus": 1}


def test_an_unset_account_or_queue_is_left_for_the_endpoint_to_discover():
    got = job_params(site(orbit_account="", orbit_queue=""), {"executable": "/bin/true"})
    assert "account" not in got["job_spec"]
    assert "queue" not in got["job_spec"]


def test_a_site_that_gives_no_gpus_omits_the_key_rather_than_asking_for_zero():
    # `to_psij_spec` is presence-based; a site handed zero may render
    # `--gres=gpu:0` rather than no request at all.
    got = job_params(site(orbit_job_gpus=0), {"executable": "/bin/true"})
    assert "gpus" not in got["job_spec"]["resources"]


# --- the existing callers are unaffected ----------------------------------


def test_the_mpnn_spec_still_takes_its_walltime_and_gpus_from_the_site():
    got = job_params(site(orbit_job_duration_sec=7200, orbit_job_gpus=2), mpnn_job_spec(PDB_TEXT))
    assert got["job_spec"]["duration_sec"] == 7200
    assert got["job_spec"]["resources"] == {"node_count": 1, "processes": 1, "gpus": 2}


def test_the_fold_spec_still_takes_its_walltime_and_gpus_from_the_site():
    got = job_params(site(orbit_job_duration_sec=7200, orbit_job_gpus=2), fold_job_spec("MKV"))
    assert got["job_spec"]["duration_sec"] == 7200
    assert got["job_spec"]["resources"] == {"node_count": 1, "processes": 1, "gpus": 2}


def test_neither_existing_spec_declares_a_walltime_or_a_gpu_count():
    # The reason the two tests above hold. If either spec starts declaring one,
    # it will be honoured, and this is where that decision surfaces.
    for spec in (mpnn_job_spec(PDB_TEXT), fold_job_spec("MKV")):
        assert "duration_sec" not in spec
        assert "gpus" not in (spec.get("resources") or {})


# --- a spec that knows better wins ----------------------------------------


def test_a_spec_declaring_a_longer_walltime_keeps_it():
    # hhblits: twelve hours against a 1800 s site default.
    got = job_params(site(), {"executable": "/bin/true", "duration_sec": 43200})
    assert got["job_spec"]["duration_sec"] == 43200


def test_a_spec_declaring_no_gpus_overrides_a_site_that_gives_one():
    # hhblits and the MPNN CPU runs want none, whatever the site's default is.
    got = job_params(
        site(orbit_job_gpus=1),
        {"executable": "/bin/true", "resources": {"processes": 4, "gpus": 0}},
    )
    assert got["job_spec"]["resources"] == {"processes": 4}


def test_a_spec_declaring_a_gpu_overrides_a_site_that_gives_none():
    # AlphaFold3 needs one even when the site default is zero.
    got = job_params(
        site(orbit_job_gpus=0), {"executable": "/bin/true", "resources": {"gpus": 1}}
    )
    assert got["job_spec"]["resources"] == {"gpus": 1}


def test_a_spec_declaring_its_own_queue_and_account_keeps_them():
    got = job_params(
        site(), {"executable": "/bin/true", "queue": "gpu", "account": "other"}
    )
    assert got["job_spec"]["queue"] == "gpu"
    assert got["job_spec"]["account"] == "other"


def test_a_spec_can_choose_the_executor_and_the_key_does_not_reach_the_job():
    # A job whose only work is to copy three files out of a project directory
    # runs on the endpoint host rather than waiting in a queue.
    got = job_params(site(), {"executable": "/bin/true", "executor": "local"})
    assert got["executor"] == "local"
    # `to_psij_spec` would drop it anyway, but leaving it on the job spec would
    # imply the far end reads it.
    assert "executor" not in got["job_spec"]


def test_job_params_does_not_mutate_the_spec_it_is_given():
    # The specs come from pure builders that callers may reuse or assert on.
    spec = {"executable": "/bin/true", "resources": {"gpus": 0}}
    job_params(site(), spec)
    assert spec == {"executable": "/bin/true", "resources": {"gpus": 0}}


# --- the batch ceiling ----------------------------------------------------


def test_a_batch_with_no_job_uses_the_plain_task_timeout():
    specs = [TaskSpec(name="t", params={}, kind="function")]
    assert batch_timeout(site(), specs) == 900.0


def test_a_batch_with_a_job_is_widened_past_the_sites_walltime():
    assert batch_timeout(site(), [job(job_spec={})]) == 1800 + 300


def test_the_ceiling_follows_the_longest_job_in_the_batch():
    # A twelve-hour hhblits run against a 1800 s default would otherwise be
    # abandoned after 35 minutes, and the chip would say FAILED about a job that
    # was still queued.
    specs = [job(job_spec={"duration_sec": 43200}), job(job_spec={"duration_sec": 900})]
    assert batch_timeout(site(), specs) == 43200 + 300


def test_the_site_walltime_is_still_a_floor():
    # A spec asking for less than the site's default does not shorten the wait:
    # the site's number is what the scheduler was told in the absence of one.
    assert batch_timeout(site(), [job(job_spec={"duration_sec": 60})]) == 1800 + 300


def test_a_disabled_task_timeout_stays_disabled():
    assert batch_timeout(site(task_timeout_sec=None), [job(job_spec={})]) is None


def test_a_large_task_timeout_is_not_lowered():
    assert batch_timeout(site(task_timeout_sec=99999.0), [job(job_spec={})]) == 99999.0
