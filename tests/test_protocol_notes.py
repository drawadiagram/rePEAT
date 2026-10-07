"""The lab notebook.

Its one hard rule is that history is appended, never rewritten: a campaign runs
over days, stages fail and are retried, and a notebook that loses the failed
attempt makes the run look cleaner than it was. Most of these tests are about
that.
"""

from __future__ import annotations

from datetime import datetime, timezone

from designagent.protocol.inputs import ProtocolInputs
from designagent.protocol.notes import (
    JOB_COLUMNS,
    append_entry,
    entry,
    failure_note,
    header,
    jobs_from_stats,
)
from designagent.protocol.site import SiteLayout

WHEN = datetime(2026, 10, 6, 14, 30, tzinfo=timezone.utc)
ALYFRB = dict(
    name="AlyFRB",
    uniprot="A0A173MSR7",
    domains="1-IDR-10-11-FN3-117-118-L-143-144-CD-479-480-L-493-494-CD-773-774-IDR-785",
    netid="all239",
    method="cpos",
    cat_res="310,364",
)

# A real `job_stats.sh` run, verbatim in the format the script emits.
STATS = """\
| 1234567 | hhblits | COMPLETED | 00:18:42 | 01:09:51 | 7.21 GB | gpuc001 |
| 1234568 | mpnn | COMPLETED | 00:01:35 | 00:01:30 | 1.02 GB | slepner045 |
"""


def inputs() -> ProtocolInputs:
    return ProtocolInputs.parse(**ALYFRB)


def site() -> SiteLayout:
    return SiteLayout(proj_root="/projects/f_sdk94_1/x", scratch_root="/scratch")


# --- the header ----------------------------------------------------------


def test_the_header_records_what_the_files_cannot_say_later():
    text = header(inputs(), site(), method="cpos", when=WHEN)
    assert "# AlyFRB redesign notebook" in text
    assert "2026-10-06 14:30 UTC" in text
    # The two irrecoverable facts: which method, and what the numbers are
    # relative to.
    assert "`cpos` (TAG=``)" in text
    assert "10.0 A (`--cat_cutoff`)" in text
    assert "310, 364" in text
    assert "/projects/f_sdk94_1/x/AlyFRB" in text
    assert "/scratch/all239/af3/AlyFRB" in text


def test_the_header_marks_catalytic_residues_as_pending_before_the_checkpoint():
    staged = ProtocolInputs.parse(**{**ALYFRB, "cat_res": ""})
    assert "_pending_" in header(staged, site(), method="cpos", when=WHEN)


def test_the_header_carries_the_signal_peptide_mapping():
    # The skill's own example. Every residue number in the rest of the document
    # is relative to this.
    note = "Offset 31 (SP 1-31). Tyr353 -> Tyr322."
    text = header(inputs(), site(), method="cpos", offset_note=note, when=WHEN)
    assert "## Signal peptide" in text
    assert "Tyr353 -> Tyr322" in text


def test_the_liu_method_is_recorded_with_its_tag():
    liu = ProtocolInputs.parse(**{**ALYFRB, "method": "conservation_liu"})
    assert "`conservation_liu` (TAG=`liu_`)" in header(
        liu, site(), method="conservation_liu", when=WHEN
    )


# --- an entry ------------------------------------------------------------


def test_an_entry_uses_the_skills_format():
    text = entry(
        "6",
        "Conservation (HHblits)",
        goal="Find conserved positions at 30/50/70%.",
        commands=["python hhblits_search.py -i m.pdb -o output"],
        result="Three jsonl files, 1,284 conserved positions at cpos50.",
        files=["conservation/output/m_cpos_50.jsonl"],
        notes="cat_cutoff 10.0, not the 6.0 of the first AlyFRB pass.",
        when=WHEN,
    )
    assert text.startswith("## 2026-10-06 14:30 — Step 6: Conservation (HHblits)")
    for label in ("**Goal:**", "**Commands:**", "**Result:**", "**Files:**"):
        assert label in text
    assert "**Notes/decisions:**" in text
    # Commands are fenced so they can be copied and run.
    assert "```bash" in text and "```" in text


def test_an_entry_omits_the_sections_it_has_nothing_for():
    text = entry("2", "Domains", when=WHEN)
    assert "**Goal:**" not in text
    assert "### Jobs" not in text
    assert text.startswith("## 2026-10-06 14:30 — Step 2: Domains")


def test_an_entry_renders_the_jobs_table_in_the_skills_column_order():
    text = entry("6", "Conservation", jobs=jobs_from_stats(STATS), when=WHEN)
    assert "### Jobs" in text
    assert "| " + " | ".join(JOB_COLUMNS) + " |" in text
    assert "| 1234567 | hhblits | COMPLETED | 00:18:42 | 01:09:51 | 7.21 GB | gpuc001 |" in text


# --- reading job_stats.sh ------------------------------------------------


def test_job_stats_rows_are_read_back_from_the_scripts_own_format():
    jobs = jobs_from_stats(STATS)
    assert len(jobs) == 2
    assert jobs[0]["JobID"] == "1234567"
    assert jobs[0]["wall"] == "00:18:42"
    assert jobs[0]["CPU time"] == "01:09:51"
    assert jobs[0]["MaxRSS"] == "7.21 GB"
    assert jobs[1]["name"] == "mpnn"


def test_a_header_row_or_prose_is_not_mistaken_for_a_job():
    text = "Some prose\n" + "| " + " | ".join(JOB_COLUMNS) + " |\n| --- |\n" + STATS
    assert len(jobs_from_stats(text)) == 2


def test_a_row_of_the_wrong_width_is_skipped_rather_than_mangled():
    assert jobs_from_stats("| only | three | cells |\n") == []


def test_no_output_yields_no_jobs():
    # `sacct` returning nothing yet is normal right after a job ends.
    assert jobs_from_stats("") == []


# --- failures are recorded too ------------------------------------------


def test_a_failure_note_names_the_first_few_errors():
    assert failure_note("mpnn", []) == ""
    note = failure_note("mpnn", ["exit code 1", "timed out"])
    assert "mpnn did not complete cleanly" in note
    assert "exit code 1; timed out" in note


def test_a_failure_note_counts_the_rest_rather_than_listing_them():
    note = failure_note("af3", [f"e{i}" for i in range(7)])
    assert "e0; e1; e2 (and 4 more)" in note


# --- appending -----------------------------------------------------------


def test_appending_to_an_empty_document_starts_it():
    assert append_entry("", "## one\n") == "## one\n"


def test_appending_keeps_everything_that_was_there():
    doc = append_entry(header(inputs(), site(), method="cpos", when=WHEN), entry("1", "Structure"))
    doc = append_entry(doc, entry("2", "Domains"))
    doc = append_entry(doc, entry("6", "Conservation"))
    assert doc.count("## ") >= 3
    assert "Step 1: Structure" in doc
    assert "Step 2: Domains" in doc
    assert "Step 6: Conservation" in doc
    assert "# AlyFRB redesign notebook" in doc
    # In order, so the document reads as a timeline.
    assert doc.index("Step 1") < doc.index("Step 2") < doc.index("Step 6")


def test_a_retried_stage_appears_twice_rather_than_replacing_itself():
    # The rule the whole module exists for: a notebook that drops the failed
    # attempt makes the run look cleaner than it was.
    doc = append_entry("", entry("9", "MPNN", result="FAILED: exit code 1", when=WHEN))
    doc = append_entry(doc, entry("9", "MPNN (retry)", result="six FASTAs", when=WHEN))
    assert doc.count("Step 9") == 2
    assert "FAILED: exit code 1" in doc
    assert "six FASTAs" in doc


def test_appending_leaves_exactly_one_blank_line_between_blocks():
    doc = append_entry("## one\n\n\n", "## two\n")
    assert doc == "## one\n\n## two\n"
    assert "\n\n\n" not in doc
