"""Design History tiers 1-3."""

from __future__ import annotations

import pytest
from designagent.config import Settings
from designagent.lake.golden import CurationRules
from designagent.lake.store import DesignHistory


@pytest.fixture
def lake(tmp_path):
    history = DesignHistory(Settings(data_dir=tmp_path, anthropic_api_key=""))
    yield history
    history.close()


def _design(did, seq, plddt, parent=None):
    return {
        "design_id": did,
        "sequence": seq,
        "parent_id": parent,
        "mutations": ["A1V"] if parent else [],
        "metrics": {"plddt": plddt},
    }


def test_campaign_reference_and_designs(lake):
    lake.start_campaign("c1", "raise thermostability")
    ref_id = lake.record_reference("c1", {"pdb_id": "1ABC", "sequence": "MKV", "chains": []})
    assert ref_id == "ref:1ABC"

    lake.record_design("c1", _design("d1", "MKVAA", 80.0), reference_id=ref_id, round_no=1)
    lake.record_design("c1", _design("d2", "MKVAV", 91.5, parent="d1"), round_no=2)

    designs = lake.graph.designs_in_campaign("c1")
    assert {d["design_id"] for d in designs} == {"d1", "d2"}

    # tier 2 got the metrics
    assert lake.scores.scores_for_design("d2") == {"plddt": 91.5}

    # tier 1 kept the lineage edge
    lineage = lake.graph.lineage("d2")
    assert [a["design_id"] for a in lineage] == ["d1"]


def test_task_result_writes_both_tiers_and_blob(lake):
    lake.start_campaign("c1", "goal")
    out = lake.record_task_result(
        "c1",
        "task-1",
        name="fold_sequence",
        interface="local",
        state="DONE",
        result={"plddt": 88.0},
        designs=[_design("d9", "MKVAA", 88.0)],
        round_no=1,
        blob=("ATOM      1  N   MET A   1\n", ".pdb"),
    )
    assert out["blob_path"].endswith(".pdb")
    assert lake.read_blob(out["blob_path"]).startswith(b"ATOM")

    tasks = lake.graph.tasks_for_campaign("c1")
    assert tasks[0]["state"] == "DONE"

    outputs = lake.graph.outputs_for_task("task-1")
    assert outputs[0]["properties"]["plddt"] == 88.0
    assert lake.scores.scores_for_design("d9") == {"plddt": 88.0}


def test_failed_task_records_error(lake):
    lake.start_campaign("c1", "goal")
    lake.record_task_result(
        "c1", "t-bad", name="fold_sequence", interface="hpc",
        state="FAILED", error="endpoint unreachable",
    )
    assert lake.graph.tasks_for_campaign("c1")[0]["error"] == "endpoint unreachable"


def test_ranking_orders_by_direction(lake):
    lake.start_campaign("c1", "goal")
    designs = [_design("a", "AA", 70.0), _design("b", "BB", 95.0), _design("c", "CC", 85.0)]
    ranked = lake.rank_round("c1", 1, "plddt", "max", designs)
    assert [d["design_id"] for d in ranked] == ["b", "c", "a"]

    ranked_min = lake.rank_round("c1", 1, "rmsd", "min", designs)
    # no design has rmsd, so order is preserved rather than dropped
    assert len(ranked_min) == 3


def test_ranking_keeps_unscored_designs_at_the_back(lake):
    lake.start_campaign("c1", "goal")
    designs = [_design("a", "AA", 70.0), {"design_id": "x", "sequence": "XX", "metrics": {}}]
    ranked = lake.rank_round("c1", 1, "plddt", "max", designs)
    assert [d["design_id"] for d in ranked] == ["a", "x"]


def test_related_past_designs_excludes_current_campaign(lake):
    lake.start_campaign("old", "previous work")
    lake.record_design("old", _design("old1", "QQ", 99.0))
    lake.start_campaign("new", "current")
    lake.record_design("new", _design("new1", "RR", 50.0))

    related = lake.related_past_designs("new", "plddt", "max")
    assert [r["design_id"] for r in related] == ["old1"]


def test_golden_set_curation_and_staging(lake):
    lake.start_campaign("c1", "goal")
    for i, score in enumerate([95.0, 60.0, 88.0, 72.0]):
        d = _design(f"d{i}", "SEQ" + ("A" * i), score)
        lake.record_design("c1", d, round_no=1)

    rules = CurationRules(metric="plddt", direction="max", min_value=80.0, top_k=2)
    manifest = lake.stage_golden_set("c1", "thermo", rules, notes="round 1")

    assert manifest["n_rows"] == 2
    assert manifest["rules"]["min_value"] == 80.0
    rows = lake.golden.read_set(manifest["id"])
    assert [r["metric_plddt"] for r in rows] == [95.0, 88.0]
    assert lake.golden.list_sets()[0]["id"] == manifest["id"]


def test_golden_dedupe_keeps_best_scoring_duplicate(lake):
    rules = CurationRules(metric="plddt", direction="max", dedupe_sequences=True)
    designs = [
        {"design_id": "a", "sequence": "SAME", "metrics": {"plddt": 70.0}},
        {"design_id": "b", "sequence": "SAME", "metrics": {"plddt": 90.0}},
        {"design_id": "c", "sequence": "OTHER", "metrics": {"plddt": 80.0}},
    ]
    kept = lake.golden.curate(designs, rules)
    assert [d["design_id"] for d in kept] == ["b", "c"]


def test_golden_require_structure_filters(lake):
    rules = CurationRules(metric="plddt", require_structure=True)
    designs = [
        {"design_id": "a", "sequence": "AA", "metrics": {"plddt": 90.0}},
        {
            "design_id": "b",
            "sequence": "BB",
            "metrics": {"plddt": 80.0},
            "structure_path": "/x.pdb",
        },
    ]
    assert [d["design_id"] for d in lake.golden.curate(designs, rules)] == ["b"]


def test_blob_is_content_addressed(lake):
    p1 = lake.write_blob("same bytes", suffix=".txt")
    p2 = lake.write_blob("same bytes", suffix=".txt")
    p3 = lake.write_blob("other bytes", suffix=".txt")
    assert p1 == p2 and p1 != p3


def test_campaign_report_shape(lake):
    lake.start_campaign("c1", "goal")
    lake.record_design("c1", _design("d1", "AA", 90.0), round_no=1)
    lake.record_analysis("c1", "round_summary", {"note": "ok"}, round_no=1)
    report = lake.campaign_report("c1")
    assert set(report) == {"designs", "tasks", "scores", "analyses"}
    assert report["analyses"][0]["payload"]["note"] == "ok"
