"""The stage machine, driven turn by turn.

No endpoint, no network, no pool: a fake `hpc` interface returns canned job
results, so the whole campaign can be walked offline. What these tests are
really about is the turn boundary -- a stage that needs an answer must stop, and
the answer must come back to this node rather than to the chat path.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from designagent.artifacts.store import ArtifactStore
from designagent.config import Settings
from designagent.graph.deps import Deps
from designagent.graph.nodes.coordinator import make_coordinator
from designagent.graph.nodes.protocol import ANSWERS, STAGES, make_protocol
from designagent.lake.store import DesignHistory
from designagent.protocol.specs import MpnnParams
from designagent.tasks.base import Capabilities, TaskInterface, TaskSpec
from designagent.tasks.manager import TaskManager
from designagent.tasks.registry import CATALOG, TaskDef

ALYFRB_DOMAINS = "1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785"
STEM = "AF-A0A173MSR7-F1-model_v6"
CHAIN = 785


def pdb_text(n: int = CHAIN, chain: str = "A") -> str:
    return (
        "\n".join(
            f"ATOM  {i:5d}  CA  ALA {chain}{i:4d}    "
            f"{float(i):8.3f}{0.0:8.3f}{0.0:8.3f}  1.00{88.0:6.2f}           C"
            for i in range(1, n + 1)
        )
        + "\nEND\n"
    )


def mpnn_fasta(n: int = 48) -> str:
    """ProteinMPNN output with the input record first, as the real thing does.

    Every sample has to be a *distinct* sequence: `variants_from_mpnn_fasta`
    dedupes by sequence, so identical records collapse to one design -- which is
    the right behaviour and makes a lazy fixture look like a short yield.
    """
    native = "MKV" * 20  # longer than any n used here, so each sample gets its own site
    lines = [f">{STEM}, score=1.0, model_name=v_48_020, seed=256", native]
    for i in range(n):
        chars = list(native)
        # A distinct position per sample, and a residue guaranteed to differ
        # from the native one, so no two samples and none of them collapse.
        chars[i] = "A" if chars[i] != "A" else "C"
        lines += [f">T=0.1, sample={i}, score=0.9", "".join(chars)]
    return "\n".join(lines) + "\n"


class FakeHpc(TaskInterface):
    """An endpoint that answers whatever the test told it to."""

    name = "hpc"
    capabilities = Capabilities(
        supports_cancel=True,
        supports_log_stream=False,  # keeps the log drain out of these tests
        supports_push_events=False,
        supports_staging=True,
    )
    connected = True

    def __init__(self) -> None:
        self.submitted: list[TaskSpec] = []
        self.artifacts: dict[str, dict[str, bytes]] = {}
        self.fail: set[str] = set()

    async def submit(self, spec: TaskSpec):
        self.submitted.append(spec)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        if spec.name in self.fail:
            future.set_exception(RuntimeError(f"{spec.name} exploded"))
        else:
            future.set_result(
                {
                    "job_id": f"job-{len(self.submitted)}",
                    "state": "DONE",
                    "exit_code": 0,
                    "stdout": "",
                    "artifacts": self.artifacts.get(spec.name, {}),
                }
            )
        return self._handle(spec, future, job_id="j", native_id=f"123{len(self.submitted)}")

    def labels(self) -> list[str]:
        return [spec.name for spec in self.submitted]

    def spec_for(self, label: str) -> dict[str, Any]:
        for spec in self.submitted:
            if spec.name == label:
                return spec.params["job_spec"]
        raise AssertionError(f"{label} was never submitted; got {self.labels()}")


@pytest.fixture
def site_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        anthropic_api_key="",
        task_timeout_sec=30.0,
        protocol_proj_root="/projects/test/targets",
        protocol_scratch_root="/scratch",
        protocol_conda_aifold="/envs/aifold",
        protocol_conda_analysis="/envs/analysis",
        protocol_mpnn_path="/tools/ProteinMPNN",
        protocol_mpnn_weights="/tools/weights",
        protocol_uniref_db="/db/uniref30",
        protocol_af3_modules="module load alphafold",
    )


@pytest.fixture
def hpc() -> FakeHpc:
    return FakeHpc()


@pytest.fixture
def pdeps(site_settings, hpc):
    history = DesignHistory(site_settings)
    artifacts = ArtifactStore(site_settings.artifacts_dir)
    manager = TaskManager(history=history, hpc=hpc)
    yield Deps(
        settings=site_settings, tasks=manager, history=history, artifacts=artifacts
    )
    history.close()


@pytest.fixture
def afdb_stub():
    """A deterministic AFDB download."""
    original = CATALOG["afdb_structure"]

    async def body(**kw):
        return {
            "stem": STEM,
            "text": pdb_text(),
            "format": "pdb",
            "version": 6,
            "source": "alphafold-db",
            "url": f"https://alphafold.ebi.ac.uk/files/{STEM}.pdb",
        }

    CATALOG["afdb_structure"] = TaskDef(
        "afdb_structure", body, "query", original.description, original.produces, False
    )
    yield
    CATALOG["afdb_structure"] = original


async def turn(pdeps, state: dict, text: str) -> dict:
    """One user message through the protocol node, returning the state update."""
    node = make_protocol(pdeps)
    payload = {**state, "messages": [{"role": "user", "content": text}]}
    command = await node(payload)
    return command.update


def reply_of(update: dict) -> str:
    return update["messages"][0]["content"]


# --- the tables -----------------------------------------------------------


def test_every_stage_an_answer_names_exists():
    # The dispatch tables are data, so they can be checked against each other.
    assert set(ANSWERS) == {"inputs", "method", "cat_res", "designs"}
    assert "intake" in STAGES and "done" in STAGES


# --- intake ---------------------------------------------------------------


async def test_intake_asks_for_the_inputs_it_does_not_have(pdeps):
    update = await turn(pdeps, {}, "run the enzyme redesign protocol")
    assert update["protocol"]["awaiting"] == "inputs"
    assert "Missing: name, uniprot, domains, netid" in reply_of(update)
    assert update["reply_source"] == "protocol:stage_intake:ask_inputs"


async def test_intake_then_asks_the_one_question_with_no_default(pdeps):
    update = await turn(
        pdeps,
        {},
        f"name=AlyFRB uniprot=A0A173MSR7 domains={ALYFRB_DOMAINS} netid=all239",
    )
    assert update["protocol"]["awaiting"] == "method"
    assert update["protocol"]["name"] == "AlyFRB"
    # Both methods offered, neither preselected: the skill forbids a default.
    reply = reply_of(update)
    assert "cpos" in reply and "conservation_liu" in reply
    assert "no default" in reply


async def test_a_bad_domain_string_is_refused_before_a_campaign_exists(pdeps):
    update = await turn(
        pdeps, {}, "name=X uniprot=A0A173MSR7 netid=all239 domains=1-IDR-10-20-CD-99"
    )
    assert "gap" in reply_of(update)
    assert update["reply_source"] == "protocol:invalid_input"


async def test_a_near_miss_on_the_method_re_asks_rather_than_guessing(pdeps):
    state = {"protocol": {"awaiting": "method", "name": "A", "uniprot": "A0A173MSR7"}}
    update = await turn(pdeps, state, "liu")
    # "liu" is not "conservation_liu". Guessing would decide which residues the
    # redesign may change.
    assert update["reply_source"] == "protocol:invalid_input"
    assert "no default" in reply_of(update)
    assert update["protocol"]["awaiting"] == "method"


# --- the refusals ---------------------------------------------------------


async def test_an_unconfigured_site_refuses_by_naming_what_is_unset(tmp_path, hpc):
    history = DesignHistory(Settings(data_dir=tmp_path / "d"))
    bare = Deps(
        settings=Settings(data_dir=tmp_path / "d", protocol_proj_root=""),
        tasks=TaskManager(history=history, hpc=hpc),
        history=history,
        artifacts=ArtifactStore(tmp_path / "a"),
    )
    update = await turn(bare, {"protocol": {"stage": "structure"}}, "go")
    assert update["reply_source"] == "protocol:unavailable:site_unconfigured"
    assert "proj_root" in reply_of(update)
    assert "AMAREL_ENDPOINT.md" in reply_of(update)
    history.close()


async def test_no_endpoint_refuses_rather_than_running_a_local_fallback(
    site_settings, tmp_path
):
    # With no `hpc` key, `interface_for` would route a job spec to the local
    # pool, which has no task by that name -- so the failure would be unrelated
    # to the real problem. And unlike a design round, there is no local
    # equivalent of this protocol to fall back to.
    history = DesignHistory(site_settings)
    deps = Deps(
        settings=site_settings,
        tasks=TaskManager(history=history),
        history=history,
        artifacts=ArtifactStore(site_settings.artifacts_dir),
    )
    update = await turn(deps, {"protocol": {"stage": "structure"}}, "go")
    assert update["reply_source"] == "protocol:unavailable:no_endpoint"
    assert "no job was submitted" in reply_of(update)
    history.close()


# --- structure ------------------------------------------------------------


async def test_the_structure_stage_installs_the_project_and_asks_for_residues(
    pdeps, hpc, afdb_stub
):
    state = {
        "protocol": {
            "stage": "structure",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
        }
    }
    update = await turn(pdeps, state, "go")
    assert "protocol-install" in hpc.labels()
    assert update["protocol"]["awaiting"] == "cat_res"
    assert update["protocol"]["model_stem"] == STEM
    assert update["protocol"]["installed"] is True
    reply = reply_of(update)
    assert "785 residues" in reply
    assert "no signal peptide" in reply
    assert "pLDDT mean 88.0" in reply
    # The FoldSeek upload stays the user's job, by the skill's own instruction.
    assert "search.foldseek.com" in reply
    # The install carries the backbone to both places that read it.
    pushed = hpc.spec_for("protocol-install")["inputs"]
    assert f"conservation/{STEM}.pdb" in pushed
    assert f"mpnn/pdb/{STEM}.pdb" in pushed


async def test_a_structure_that_disagrees_with_the_domain_string_stops_the_campaign(
    pdeps, hpc
):
    original = CATALOG["afdb_structure"]

    async def short(**kw):
        return {"stem": STEM, "text": pdb_text(400), "format": "pdb", "version": 6, "url": "u"}

    CATALOG["afdb_structure"] = TaskDef(
        "afdb_structure", short, "query", "d", "structure", False
    )
    try:
        state = {
            "protocol": {
                "stage": "structure",
                "name": "AlyFRB",
                "uniprot": "A0A173MSR7",
                "domains": ALYFRB_DOMAINS,
                "netid": "all239",
                "method": "cpos",
            }
        }
        update = await turn(pdeps, state, "go")
        assert update["reply_source"] == "protocol:invalid_input"
        assert "different construct" in reply_of(update)
        # Nothing was created on the cluster on the strength of a bad premise.
        assert "protocol-install" not in hpc.labels()
    finally:
        CATALOG["afdb_structure"] = original


# --- the catalytic-residue checkpoint ------------------------------------


async def test_catalytic_residues_are_checked_against_the_trimmed_chain(pdeps, hpc):
    state = {
        "protocol": {
            "awaiting": "cat_res",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "model_stem": STEM,
        }
    }
    update = await turn(pdeps, state, "the paper says 310 and 900")
    # 900 is past the end: the list is probably still in untrimmed numbering,
    # and accepting it would protect the wrong residues.
    assert update["reply_source"] == "protocol:invalid_input"
    assert "untrimmed numbering" in reply_of(update)
    assert update["protocol"]["awaiting"] == "cat_res"


# --- conservation ---------------------------------------------------------


def conservation_artifacts(levels=(30, 50, 70), tag="") -> dict[str, bytes]:
    out = {}
    for level in levels:
        name = f"{STEM}_cpos_{tag}{level}.jsonl"
        out[f"conservation/{name}"] = (
            json.dumps({f"{STEM}.pdb": {"A": [200, 300, 500]}}) + "\n"
        ).encode()
    return out


async def test_the_conservation_stage_builds_each_levels_fixed_positions(pdeps, hpc):
    hpc.artifacts["conservation-fetch"] = conservation_artifacts()
    state = {
        "protocol": {
            "stage": "conservation",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "cat_res": [310, 364],
            "model_stem": STEM,
        }
    }
    update = await turn(pdeps, state, "")
    assert "hhblits" in hpc.labels()
    assert update["protocol"]["stage"] == "mpnn"
    # The fixed-position files are built here, from the conservation output
    # unioned with everything outside a CD segment.
    pushed = hpc.spec_for("fixed-positions-push")["inputs"]
    assert set(pushed) == {"cpos_30_CD.jsonl", "cpos_50_CD.jsonl", "cpos_70_CD.jsonl"}
    record = json.loads(pushed["cpos_50_CD.jsonl"])
    positions = record[f"{STEM}.pdb"]["A"]
    assert {200, 300, 500}.issubset(positions)
    assert 1 in positions and 785 in positions  # termini
    assert "designable" in reply_of(update)
    # The scheduler ids are kept: they are the only route to sacct.
    assert update["protocol"]["native_ids"]


async def test_the_hhblits_job_asks_for_twelve_hours_and_no_gpu(pdeps, hpc):
    hpc.artifacts["conservation-fetch"] = conservation_artifacts()
    state = {
        "protocol": {
            "stage": "conservation",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "cat_res": [310, 364],
            "model_stem": STEM,
        }
    }
    await turn(pdeps, state, "")
    spec = hpc.spec_for("hhblits")
    assert spec["duration_sec"] == 43200
    # `job_params` turns a declared zero into an omitted key, so the site's
    # default of one GPU does not leak onto a CPU search.
    assert "gpus" not in spec["resources"]
    assert spec["directory"] == "/projects/test/targets/AlyFRB/conservation"


async def test_conservation_output_for_the_wrong_method_is_named_not_ignored(pdeps, hpc):
    # The user chose conservation_liu; the files are the cpos ones.
    hpc.artifacts["conservation-fetch"] = conservation_artifacts(tag="")
    state = {
        "protocol": {
            "stage": "conservation",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "conservation_liu",
            "cat_res": [310, 364],
            "model_stem": STEM,
        }
    }
    update = await turn(pdeps, state, "")
    assert update["reply_source"] == "protocol:stage_conservation:wrong_sets"
    assert "cpos_liu_30" in reply_of(update)


async def test_a_failed_conservation_search_keeps_its_job_ids(pdeps, hpc):
    hpc.fail.add("hhblits")
    state = {
        "protocol": {
            "stage": "conservation",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "cat_res": [310, 364],
            "model_stem": STEM,
        }
    }
    update = await turn(pdeps, state, "")
    assert update["reply_source"] == "protocol:stage_conservation:failed"
    assert "exploded" in reply_of(update)
    assert update["protocol"]["stage"] != "mpnn"


# --- the redesign ---------------------------------------------------------


def mpnn_state(levels=(50,)) -> dict:
    return {
        "protocol": {
            "stage": "mpnn",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "cat_res": [310, 364],
            "model_stem": STEM,
            "levels": list(levels),
        }
    }


async def test_the_redesign_runs_both_models_at_each_level(pdeps, hpc):
    hpc.artifacts["mpnn-fetch"] = {
        f"cpos50_halo/{STEM}.fa": mpnn_fasta().encode(),
        f"cpos50/{STEM}.fa": mpnn_fasta().encode(),
    }
    update = await turn(pdeps, mpnn_state(), "")
    assert "proteinmpnn-cpos50_halo" in hpc.labels()
    assert "proteinmpnn-cpos50" in hpc.labels()
    assert update["protocol"]["stage"] == "score"
    assert "cpos50_halo: 48 designs" in reply_of(update)
    # HaloMPNN's own weights, SolubleMPNN's soluble model.
    halo = hpc.spec_for("proteinmpnn-cpos50_halo")["arguments"][1]
    soluble = hpc.spec_for("proteinmpnn-cpos50")["arguments"][1]
    assert "halompnn_v1" in halo and "/tools/weights" in halo
    assert "--use_soluble_model" in soluble


async def test_a_short_yield_is_reported_rather_than_delivered_quietly(pdeps, hpc):
    # The skill's check: num_seq_per_target x n_temps. A short count means the
    # run was cut off, so the designs that landed are not a complete sample.
    hpc.artifacts["mpnn-fetch"] = {
        f"cpos50_halo/{STEM}.fa": mpnn_fasta(10).encode(),
        f"cpos50/{STEM}.fa": mpnn_fasta().encode(),
    }
    update = await turn(pdeps, mpnn_state(), "")
    assert f"expected {MpnnParams().expected_sequences - 1}" in reply_of(update)
    assert any("not 48" in w for w in update["warnings"])


async def test_no_redesign_output_at_all_is_an_error(pdeps, hpc):
    update = await turn(pdeps, mpnn_state(), "")
    assert update["reply_source"] == "protocol:stage_mpnn:no_designs"
    assert update["protocol"]["stage"] != "score"


# --- AlphaFold3 -----------------------------------------------------------


def af3_state(designs: list[str]) -> dict:
    return {
        "protocol": {
            "stage": "af3_collect",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
            "cat_res": [310, 364],
            "model_stem": STEM,
            "designs": designs,
        }
    }


async def test_collecting_reports_partial_progress_and_stays_put(pdeps, hpc):
    hpc.artifacts["af3-collect"] = {
        "af3/d1_summary_confidences.json": json.dumps({"ptm": 0.8, "ranking_score": 0.8}).encode()
    }
    update = await turn(pdeps, af3_state(["d1", "d2"]), "how is it going")
    assert update["reply_source"] == "protocol:stage_af3_collect:in_progress"
    assert "1 of 2" in reply_of(update)
    # Still collecting, so asking again re-reads the directory.
    assert update["protocol"]["stage"] == "af3_collect"


async def test_collecting_needs_no_handle_so_it_survives_a_restart(pdeps, hpc):
    # The state carries design names and nothing else: no handle, no task id.
    # A backend restart loses every in-flight handle while the jobs keep
    # running, so the output tree is the only honest progress report.
    hpc.artifacts["af3-collect"] = {
        "af3/d1_summary_confidences.json": json.dumps({"ptm": 0.7, "ranking_score": 0.7}).encode(),
        "af3/d2_summary_confidences.json": json.dumps({"ptm": 0.9, "ranking_score": 0.95}).encode(),
    }
    update = await turn(pdeps, af3_state(["d1", "d2"]), "status")
    assert update["reply_source"] == "protocol:stage_af3_collect:complete"
    assert "Best: `d2`" in reply_of(update)
    assert update["protocol"]["stage"] == "report"


async def test_the_af3_jobs_ask_for_a_gpu_and_carry_the_constraint(pdeps, hpc):
    state = {
        **af3_state(["d1"]),
    }
    state["protocol"]["stage"] = "af3_submit"
    pdeps.settings.__dict__["protocol_gpu_constraint"] = "ampere|adalovelace"
    update = await turn(pdeps, state, "go")
    spec = hpc.spec_for("alphafold3-d1")
    assert spec["resources"]["gpus"] == 1
    assert spec["queue"] == "gpu"
    assert spec["custom_attributes"]["slurm.gres"] == "gpu:1"
    assert update["protocol"]["stage"] == "af3_collect"
    # It does not wait: an hour per design cannot sit inside a turn.
    assert "Ask me again" in reply_of(update)


async def test_the_design_checkpoint_accepts_go_or_named_designs(pdeps, hpc):
    state = af3_state(["d1", "d2"])
    state["protocol"]["awaiting"] = "designs"
    state["protocol"]["stage"] = "score"
    update = await turn(pdeps, state, "just d2 please")
    assert update["protocol"]["designs"] == ["d2"]


async def test_an_unrecognised_design_name_re_asks(pdeps, hpc):
    state = af3_state(["d1", "d2"])
    state["protocol"]["awaiting"] = "designs"
    update = await turn(pdeps, state, "the third one")
    assert update["reply_source"] == "protocol:invalid_input"
    assert update["protocol"]["awaiting"] == "designs"


# --- the lab notebook ----------------------------------------------------


async def test_the_notebook_accumulates_across_stages(pdeps, hpc, afdb_stub):
    state: dict = {
        "session_id": "s1",
        "protocol": {
            "stage": "structure",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
        },
    }
    first = await turn(pdeps, state, "go")
    assert first["artifacts"]
    artifact_id = first["artifacts"][0]["id"]

    hpc.artifacts["conservation-fetch"] = conservation_artifacts()
    state = {"session_id": "s1", "protocol": {**first["protocol"], "awaiting": ""}}
    state["protocol"]["stage"] = "conservation"
    state["protocol"]["cat_res"] = [310, 364]
    second = await turn(pdeps, state, "")

    # One artifact id throughout, so the pane shows one notebook and not a dozen.
    assert second["artifacts"][0]["id"] == artifact_id
    text = pdeps.artifacts.read_bytes(artifact_id).decode()
    assert "# AlyFRB redesign notebook" in text
    assert "Step 1-2: Structure and domains" in text
    assert "Step 6-7: Conservation" in text
    assert text.index("Step 1-2") < text.index("Step 6-7")


# --- the coordinator's routing -------------------------------------------


async def coordinate(pdeps, state: dict, text: str):
    node = make_coordinator(pdeps)
    return await node({**state, "messages": [{"role": "user", "content": text}]})


async def test_an_answer_to_a_checkpoint_reaches_the_protocol_not_the_chat_path(pdeps):
    # The bug this exists to prevent: "310,364" classifies as `chat`, which
    # would answer from session state and leave the campaign waiting forever.
    for answer in ("310,364", "liu", "go", "cpos", "yes"):
        command = await coordinate(pdeps, {"protocol": {"awaiting": "cat_res"}}, answer)
        assert command.goto == "protocol", answer
        assert command.update["intent"] == "protocol"


async def test_asking_for_the_protocol_by_name_starts_it(pdeps):
    for phrase in (
        "run the enzyme redesign protocol on AlyFRB",
        "do the full protocol",
        "use hhblits conservation then halompnn",
    ):
        command = await coordinate(pdeps, {}, phrase)
        assert command.goto == "protocol", phrase


async def test_an_ordinary_redesign_request_is_still_a_design_round(pdeps):
    # Nothing in PROTOCOL_WORDS overlaps the ordinary action words, so a normal
    # request keeps going through the orchestrator.
    command = await coordinate(pdeps, {}, "redesign this lipase for thermostability")
    assert command.goto != "protocol"


async def test_the_escape_phrase_leaves_a_waiting_campaign(pdeps):
    # Otherwise a user could not ask anything else until the protocol finished.
    command = await coordinate(
        pdeps, {"protocol": {"awaiting": "cat_res"}}, "cancel the protocol"
    )
    assert command.goto != "protocol"


async def test_continuing_resumes_a_campaign_that_is_not_waiting_on_anyone(pdeps):
    command = await coordinate(pdeps, {"protocol": {"stage": "af3_collect"}}, "continue")
    assert command.goto == "protocol"


async def test_a_finished_campaign_does_not_capture_later_messages(pdeps):
    command = await coordinate(pdeps, {"protocol": {"stage": "done"}}, "continue")
    assert command.goto != "protocol"


# --- the state contract ---------------------------------------------------


async def test_the_protocol_state_carries_no_bulky_payload(pdeps, hpc, afdb_stub):
    state = {
        "protocol": {
            "stage": "structure",
            "name": "AlyFRB",
            "uniprot": "A0A173MSR7",
            "domains": ALYFRB_DOMAINS,
            "netid": "all239",
            "method": "cpos",
        }
    }
    update = await turn(pdeps, state, "go")
    # The same rule as the rest of the graph: coordinates go to a blob and
    # travel as a path. LangGraph serialises a checkpoint every turn.
    serialized = json.dumps(update["protocol"])
    assert "ATOM  " not in serialized
    assert update["protocol"]["structure_path"].endswith(".pdb")


async def test_a_stage_always_names_who_wrote_its_reply(pdeps, hpc):
    update = await turn(pdeps, {}, "run the redesign protocol")
    assert update["reply_source"].startswith("protocol:")
    # Grep-able straight to the function, like every other node's.
    assert update["reply_source"].count(":") >= 1


def test_the_state_key_is_not_in_the_turn_payload_by_accident():
    """`protocol` must come from the checkpoint, never from a fresh turn.

    Most of `DesignState` is reduced with `replace`, so any key present in a
    turn's input *overwrites* the saved value. `app.py` sends only the message,
    the session id and the two turn-scoped resets; if `protocol` were ever added
    there, every second message would wipe the campaign.
    """
    from pathlib import Path

    app = Path(__file__).resolve().parents[1] / "backend/designagent/app.py"
    text = app.read_text()
    # The per-turn payload resets exactly these.
    assert '"pending_results"' in text
    assert '"protocol"' not in text
