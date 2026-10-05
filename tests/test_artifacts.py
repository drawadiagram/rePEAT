"""The in-band staging protocol, without a broker.

`wrap` emits a bash script, so these tests run it with bash directly: the thing
worth pinning is that the generated script and the client-side reader agree, and
that a clipped or corrupted channel is *detected* rather than quietly short.
"""

from __future__ import annotations

import base64
import hashlib
import os
import subprocess

import pytest
from designagent.tasks.hpc.artifacts import CHUNK, collect, wrap


def run(spec: dict, **kw):
    """Execute a wrapped spec and read its artifacts back."""
    wrapped = wrap(spec, **kw)
    proc = subprocess.run(
        ["bash", *wrapped["arguments"]], capture_output=True, text=True
    )
    return proc, collect(proc.stdout)


def job(script: str, **extra) -> dict:
    return {"executable": "bash", "arguments": ["-c", script], **extra}


def _raw_stdout(script: str, **extra) -> str:
    wrapped = wrap(job(script, **extra))
    return subprocess.run(
        ["bash", *wrapped["arguments"]], capture_output=True, text=True
    ).stdout


@pytest.fixture
def one_file_stdout() -> str:
    return _raw_stdout('echo hello > a.txt', outputs=["*.txt"])


@pytest.fixture
def two_file_stdout() -> str:
    return _raw_stdout('echo hello > a.txt; echo world > b.txt', outputs=["*.txt"])


def test_a_spec_declaring_nothing_is_not_wrapped():
    """No shell is inserted between the scheduler and a plain command."""
    plain = {"executable": "/bin/echo", "arguments": ["routed"]}
    assert wrap(plain) == plain


def test_declared_outputs_come_back_with_their_contents():
    proc, got = run(
        job('mkdir -p seqs && echo one > seqs/a.fa && echo two > seqs/b.fa',
            outputs=["seqs/*.fa"])
    )
    assert proc.returncode == 0
    assert got.ok, got.error
    assert got.text("seqs/a.fa") == "one\n"
    assert got.text("seqs/b.fa") == "two\n"
    assert got.declared_files == 2


def test_the_jobs_own_output_does_not_pollute_the_channel():
    """stdout is the data channel, so the command's chatter goes to stderr."""
    proc, got = run(job('echo chatter; echo ">not a header" ; echo x > a.txt',
                        outputs=["*.txt"]))
    assert "chatter" in proc.stderr
    assert got.ok, got.error
    assert sorted(got.files) == ["a.txt"]
    assert got.log == ""


def test_a_login_banner_before_the_first_frame_is_kept_as_log(one_file_stdout):
    """`bash -lc` sources the profile, and a stray `>` line is valid FASTA."""
    got = collect("Welcome to the cluster\n>sneaky header\n" + one_file_stdout)
    assert sorted(got.files) == ["a.txt"]
    assert got.text("a.txt") == "hello\n"
    assert "Welcome to the cluster" in got.log


def test_an_input_is_staged_in_and_readable_by_the_job():
    text = "ATOM      1  CA  ALA A   1\nATOM      2  CA  GLY A   2\n"
    _, got = run(job('wc -l < in.pdb > n.txt', inputs={"in.pdb": text},
                     outputs=["n.txt"]))
    assert got.text("n.txt").strip() == "2"


def test_a_large_incompressible_input_is_split_across_argv_elements():
    """MAX_ARG_STRLEN caps one element, not all of them together."""
    blob = base64.b64encode(os.urandom(400_000)).decode()
    wrapped = wrap(job('sha256sum in.bin | cut -d" " -f1 > d.txt',
                       inputs={"in.bin": blob}, outputs=["d.txt"]))
    assert len(wrapped["arguments"]) > 4, "payload should need several chunks"
    assert max(len(a) for a in wrapped["arguments"]) <= CHUNK
    proc = subprocess.run(["bash", *wrapped["arguments"]],
                          capture_output=True, text=True)
    got = collect(proc.stdout)
    assert got.text("d.txt").strip() == hashlib.sha256(blob.encode()).hexdigest()


def test_outputs_everything_covers_a_job_that_decides_at_runtime():
    """The indeterminate case: nothing declared the names in advance."""
    _, got = run(job(
        'mkdir -p deep/nested && echo a > deep/nested/x.json '
        '&& echo b > y.txt && echo c > deep/z.log',
        outputs=["**"]))
    assert got.ok, got.error
    assert sorted(got.files) == ["deep/nested/x.json", "deep/z.log", "y.txt"]


def test_an_oversized_file_is_named_rather_than_dropped():
    _, got = run(
        job('head -c 5000 /dev/zero | tr "\\0" "a" > big.txt; echo ok > ok.txt',
            outputs=["*.txt"]),
        artifact_max_bytes=1000,
    )
    assert sorted(got.files) == ["ok.txt"]
    assert got.skipped == [{"name": "big.txt", "bytes": 5000, "reason": "too_large"}]


def test_the_total_budget_stops_collecting_and_says_so():
    _, got = run(
        job('for i in 1 2 3 4; do head -c 400 /dev/zero | tr "\\0" "x" > f$i.txt; done',
            outputs=["*.txt"]),
        artifact_max_bytes=10_000,
        total_max_bytes=900,
    )
    assert len(got.files) == 2
    assert [s["reason"] for s in got.skipped] == ["budget", "budget"]


def test_a_failing_job_never_reaches_the_epilogue():
    """`set -e` means a failure takes the ordinary FAILED path, not a short one."""
    proc, got = run(job('echo made > a.txt; exit 7', outputs=["*.txt"]))
    assert proc.returncode == 7
    assert got.files == {}


# --- detection, not inference ----------------------------------------------


def test_output_clipped_mid_artifact_is_reported(two_file_stdout):
    cut = two_file_stdout[: two_file_stdout.index("<<<ORBIT_ARTIFACT_END>>>") + 10]
    got = collect(cut)
    assert got.truncated
    assert "ended inside the artifact" in got.error


def test_a_lost_manifest_is_reported(two_file_stdout):
    without = "\n".join(
        ln for ln in two_file_stdout.splitlines()
        if not ln.startswith("<<<ORBIT_MANIFEST")
    )
    got = collect(without)
    assert got.truncated
    assert len(got.files) == 2  # the files are fine; the tail was lost


def test_a_missing_file_is_caught_by_the_manifest_count(two_file_stdout):
    lines = two_file_stdout.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("<<<ORBIT_ARTIFACT name"))
    end = next(i for i, ln in enumerate(lines) if ln == "<<<ORBIT_ARTIFACT_END>>>")
    got = collect("\n".join(lines[:start] + lines[end + 1:]))
    assert got.truncated
    assert "declared 2 file(s), 1 arrived" in got.error


def test_a_corrupted_payload_fails_its_digest(two_file_stdout):
    got = collect(two_file_stdout.replace("sha256=", "sha256=0"))
    assert "sha256 mismatch" in got.error
    assert not got.ok


def test_empty_stdout_is_not_an_error():
    got = collect("")
    assert got.files == {} and not got.error and not got.truncated
