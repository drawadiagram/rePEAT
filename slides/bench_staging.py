#!/usr/bin/env python3
"""Measure the in-band staging channel, and write slides/staging.json.

A job returns files through its stdout, because the broker forwards neither
`outputs` nor `stdin_text` (backlog C6). `tasks/hpc/artifacts.py` therefore
gzips and base64s an input across argv elements and prints each output back as
a framed block. That is the only data path this system has to a cluster, so the
deck should say what it costs -- and the honest way to say it is to measure it.

This drives the real functions. `wrap()` and `collect()` are pure, so the only
thing standing in for Orbit here is a local `bash -lc`:

    payload -> wrap() -> bash -lc -> stdout -> collect() -> payload

What it records, per size and per payload shape:

  * `argv_bytes`   what the inbound half costs (gzip + base64, chunked)
  * `stdout_bytes` what the outbound half costs, which is the number that has
                   to cross the broker
  * `ratio`        stdout bytes per payload byte
  * wall time for each of the three stages
  * whether the returned bytes are identical to what went in

The point the curve makes is the asymmetry between the cap and the cost. The
cap is checked on the *raw* file size in `_stage_out` (`wc -c`), while what
actually travels is compressed: an incompressible 1 MiB file is accepted and
costs ~1.4 MB of stdout, and a 2 MiB file that would compress to a few KB is
refused anyway. Entropy decides the cost; it does not decide the refusal.

Local measurement, no broker in the path, so these are a floor on what a real
job pays. `source` in the output says so, per the deck's rule that a number the
slide cannot re-derive names where it came from.

    python3 slides/bench_staging.py [--repeats N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "staging.json"
sys.path.insert(0, str(ROOT / "backend"))

from designagent.tasks.hpc import artifacts as A  # noqa: E402

KIB = 1024
SIZES = [1 * KIB, 16 * KIB, 64 * KIB, 256 * KIB, 1024 * KIB, 2048 * KIB]


def payload(shape: str, size: int) -> tuple[bytes, bool]:
    """`size` bytes of the requested shape, and whether it had to be tiled."""
    if shape == "random":
        return os.urandom(size), False
    pdbs = sorted((ROOT / "data" / "blobs").glob("*.pdb"))
    if not pdbs:
        raise SystemExit("no PDB blobs in data/blobs -- run a campaign first")
    raw = max(pdbs, key=lambda p: p.stat().st_size).read_bytes()
    if len(raw) >= size:
        return raw[:size], False
    # Tiling exaggerates compressibility: gzip's window is 32 KiB, so a repeat
    # at any larger period is nearly free. Flagged, and said on the slide.
    return (raw * (size // len(raw) + 1))[:size], True


def run_once(data: bytes, work: Path) -> dict:
    """One full round trip through wrap -> bash -> collect."""
    spec = {
        "executable": "cp",
        "arguments": ["payload.bin", "out.bin"],
        "inputs": {"payload.bin": data},
        "outputs": ["out.bin"],
    }
    t0 = time.perf_counter()
    wrapped = A.wrap(spec)
    t1 = time.perf_counter()
    argv = [str(a) for a in wrapped["arguments"]]
    argv_bytes = sum(len(a) for a in argv)
    try:
        proc = subprocess.run(
            [wrapped["executable"], *argv], capture_output=True, cwd=work, check=False
        )
    except OSError as exc:
        # E2BIG. Chunking defeats MAX_ARG_STRLEN, the per-element cap, but all
        # the chunks together still have to fit ARG_MAX -- so the inbound half
        # has a ceiling of its own, and it is reached before any declared limit
        # in artifacts.py is consulted.
        return {
            "exit_code": None,
            "argv_bytes": argv_bytes,
            "stdout_bytes": 0,
            "returned_bytes": 0,
            "identical": False,
            "skipped": [],
            "error": f"{type(exc).__name__}: {exc.strerror or exc}",
            "wrap_sec": t1 - t0,
            "run_sec": None,   # JSON has no Infinity, and the deck reads this file
            "collect_sec": 0.0,
        }
    t2 = time.perf_counter()
    got = A.collect(proc.stdout.decode("utf-8", errors="replace"))
    t3 = time.perf_counter()

    returned = next(iter(got.files.values()), b"")
    skipped = [s.get("reason") for s in got.skipped]
    return {
        "exit_code": proc.returncode,
        "argv_bytes": argv_bytes,
        "stdout_bytes": len(proc.stdout),
        "returned_bytes": len(returned),
        "identical": hashlib.sha256(returned).digest() == hashlib.sha256(data).digest(),
        "skipped": skipped,
        "error": got.error,
        "wrap_sec": t1 - t0,
        "run_sec": t2 - t1,
        "collect_sec": t3 - t2,
    }


def argv_ceiling(work: Path, lo: int, hi: int) -> int:
    """Largest incompressible payload whose argv still execs, to ~16 KiB.

    Bisection rather than `getconf ARG_MAX`, because what the kernel enforces is
    the argv *and* the environment, and the script body is in there too.
    """
    while hi - lo > 16 * KIB:
        mid = (lo + hi) // 2
        got = run_once(os.urandom(mid), work)
        if got["error"].startswith("OSError"):
            hi = mid
        else:
            lo = mid
    return lo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3, help="runs per point; best is kept")
    args = ap.parse_args()

    rows: list[dict] = []
    with TemporaryDirectory(prefix="bench-staging-") as tmp:
        work = Path(tmp)
        for shape in ("random", "structure"):
            for size in SIZES:
                data, tiled = payload(shape, size)
                best: dict | None = None
                for _ in range(max(1, args.repeats)):
                    got = run_once(data, work)
                    if best is None or (got["run_sec"] or 1e9) < (best["run_sec"] or 1e9):
                        best = got
                assert best is not None
                best.update(shape=shape, payload_bytes=size, tiled=tiled)
                best["ratio"] = best["stdout_bytes"] / size
                rows.append(best)
                mark = "·".join(best["skipped"]) or ("ok" if best["identical"] else "MISMATCH")
                print(
                    f"  {shape:10s} {size // KIB:5d} KiB  argv {best['argv_bytes']:9d}"
                    f"  stdout {best['stdout_bytes']:9d}  x{best['ratio']:5.2f}"
                    f"  {(best['run_sec'] or 0) * 1000:7.1f} ms  {mark}"
                )

        ceiling = argv_ceiling(work, 1024 * KIB, 2048 * KIB)
        print(f"\n  argv ceiling: {ceiling / KIB:.0f} KiB of incompressible payload "
              f"(~{ceiling * 4 / 3 / 1e6:.1f} MB of argv) before E2BIG")

    model = {
        "rows": rows,
        "argv_ceiling_bytes": ceiling,
        "limits": {
            "artifact_max_bytes": A.ARTIFACT_MAX_BYTES,
            "total_max_bytes": A.TOTAL_MAX_BYTES,
            "argv_chunk_bytes": A.CHUNK,
            "getconf_arg_max": int(subprocess.run(
                ["getconf", "ARG_MAX"], capture_output=True, text=True).stdout.strip() or 0),
        },
        "source": {
            "measured": "locally, with no broker in the path -- a floor on what a real job pays",
            "host": platform.node(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "date": datetime.now(timezone.utc).date().isoformat(),
            "code": "backend/designagent/tasks/hpc/artifacts.py (wrap, collect)",
            "repeats": args.repeats,
            "note": (
                "The cap is checked on raw file size; what travels is gzip+base64. "
                "The structure series is a real 462 KB PDB, tiled above that size, "
                "so its ratio above 462 KB is optimistic -- gzip's window is 32 KiB."
            ),
        },
    }
    OUT.write_text(json.dumps(model, indent=2) + "\n")
    print(f"\nwrote {OUT.relative_to(ROOT)} — {len(rows)} points")
    return 0


if __name__ == "__main__":
    sys.exit(main())
