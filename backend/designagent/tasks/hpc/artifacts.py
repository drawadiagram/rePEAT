"""In-band file staging for remote jobs.

A PSI/J job has no way to return a file. `to_psij_spec` forwards neither
`outputs` nor `stdin_text`, and neither does the broker's `plugin_psij.py`
(backlog C6), so a job spec that declares `outputs` is declaring nothing. The
one channel that does carry data is stdout, which the broker serves whole from
any byte offset.

So staging is expressed over stdout. `wrap` rewrites a job spec to carry its
inputs inlined in argv and to print its outputs, framed, after the real command
runs; `collect` reads the frames back. Both are pure functions: the transport
calls them and knows nothing about what is being staged, which is what lets this
serve ProteinMPNN's `seqs/*.fa`, ESMFold's single PDB, and a job whose output
shape is not known until it has run.

Three details are load-bearing:

  * **Frames, not a bare `cat`.** `bash -lc` sources the login profile and may
    print a banner, and a job's own chatter interleaves with its data. The real
    command's stdout is redirected to stderr and only framed blocks are printed
    on stdout.
  * **A declared size and digest per file.** Truncation and corruption are
    detected rather than inferred. If an opening frame is itself clipped, its
    absence is the signal.
  * **Inputs split across argv elements.** `MAX_ARG_STRLEN` caps a *single*
    argument at ~128 KiB; the limit on all of them together is `ARG_MAX`, which
    is megabytes. Chunking buys an order of magnitude.

`outputs` entries are globs resolved relative to the job's work directory.
The single entry `"**"` means "every file this job created", resolved against a
marker written before the command runs -- the indeterminate case, for a job that
decides at runtime what to write.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import logging
import re
import shlex
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

ARTIFACT_BEGIN = "<<<ORBIT_ARTIFACT"
ARTIFACT_END = "<<<ORBIT_ARTIFACT_END>>>"
SKIPPED = "<<<ORBIT_SKIPPED"
MANIFEST = "<<<ORBIT_MANIFEST"

# One argv element must stay under MAX_ARG_STRLEN (128 KiB on Linux). 64 KiB
# leaves room for the quoting the shell adds.
CHUNK = 64 * 1024

# Default ceilings; the interface passes the configured ones.
ARTIFACT_MAX_BYTES = 1024 * 1024
TOTAL_MAX_BYTES = 4 * 1024 * 1024

_BEGIN_RE = re.compile(
    r"^<<<ORBIT_ARTIFACT name=(\S+) bytes=(\d+) sha256=(\S*)>>>$"
)
_SKIPPED_RE = re.compile(r"^<<<ORBIT_SKIPPED name=(\S+) bytes=(\d+) reason=(\S+)>>>$")
_MANIFEST_RE = re.compile(r"^<<<ORBIT_MANIFEST files=(\d+) bytes=(\d+) skipped=(\d+)>>>$")


@dataclass
class ArtifactSet:
    """What came back from a job's stdout."""

    files: dict[str, bytes] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    log: str = ""
    declared_files: int = -1
    declared_bytes: int = -1
    truncated: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.truncated

    def text(self, name: str, encoding: str = "utf-8") -> str:
        return self.files[name].decode(encoding, errors="replace")


def _inflate(payload: str, limit: int) -> bytes:
    """Decompress a framed payload, refusing to expand past `limit`.

    The bytes come from a job's stdout, so the compression ratio is not ours to
    trust: a few KB of base64 can describe gigabytes. Reading `limit + 1` and
    rejecting a full buffer bounds the allocation without having to believe the
    declared size first.
    """
    raw = base64.b64decode(payload, validate=False)
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        out = fh.read(limit + 1)
    if len(out) > limit:
        raise ValueError(f"expands past {limit} bytes")
    return out


def _b64name(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode()).decode()


def _unb64name(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad).decode(errors="replace")


# --- building ---------------------------------------------------------------


def _stage_in(inputs: dict[str, str]) -> tuple[list[str], list[str]]:
    """Lines that rebuild each input in $work, plus the argv chunks they read.

    The payload rides in argv rather than the script body so that one oversized
    file cannot push the script itself past MAX_ARG_STRLEN.
    """
    lines: list[str] = []
    chunks: list[str] = []
    for name, text in inputs.items():
        payload = base64.b64encode(
            gzip.compress(text.encode() if isinstance(text, str) else text)
        ).decode()
        parts = [payload[i : i + CHUNK] for i in range(0, len(payload), CHUNK)] or [""]
        refs = "".join(f'"${{{len(chunks) + i + 1}}}"' for i in range(len(parts)))
        chunks.extend(parts)
        lines.append(f'mkdir -p "$(dirname "$work/{name}")"')
        lines.append(f'printf %s {refs} | base64 -d | gunzip > "$work/{name}"')
    return lines, chunks


def _stage_out(outputs: list[str], artifact_max: int, total_max: int) -> list[str]:
    """Lines that print each declared output as a framed block."""
    emit = [
        "__total=0",
        "__files=0",
        "__skipped=0",
        "__emit() {",
        '  __rel="$1"',
        '  [ -f "$__rel" ] || return 0',
        '  __sz=$(wc -c < "$__rel")',
        '  __nm=$(printf %s "$__rel" | base64 -w0 | tr "+/" "-_" | tr -d "=")',
        f'  if [ "$__sz" -gt {artifact_max} ]; then',
        '    printf "%s name=%s bytes=%s reason=too_large>>>\\n"'
        f' "{SKIPPED}" "$__nm" "$__sz"',
        "    __skipped=$((__skipped + 1)); return 0",
        "  fi",
        f'  if [ $((__total + __sz)) -gt {total_max} ]; then',
        '    printf "%s name=%s bytes=%s reason=budget>>>\\n"'
        f' "{SKIPPED}" "$__nm" "$__sz"',
        "    __skipped=$((__skipped + 1)); return 0",
        "  fi",
        '  __dg=$(sha256sum "$__rel" 2>/dev/null | cut -d" " -f1)',
        '  printf "%s name=%s bytes=%s sha256=%s>>>\\n"'
        f' "{ARTIFACT_BEGIN}" "$__nm" "$__sz" "$__dg"',
        '  gzip -c "$__rel" | base64 -w0',
        f'  printf "\\n%s\\n" "{ARTIFACT_END}"',
        "  __total=$((__total + __sz)); __files=$((__files + 1))",
        "}",
        'cd "$work" || exit 0',
        "shopt -s nullglob dotglob globstar 2>/dev/null || true",
    ]
    if outputs == ["**"]:
        # Indeterminate shape: everything this job created, by mtime against the
        # marker, so a job that decides at runtime what to write is covered.
        emit += [
            'while IFS= read -r __f; do __emit "${__f#./}"; done < <(',
            '  find . -type f -newer .orbit-start ! -name .orbit-start | sort)',
        ]
    else:
        for pattern in outputs:
            emit.append(f"for __f in {pattern}; do __emit \"$__f\"; done")
    emit.append(
        '  printf "%s files=%s bytes=%s skipped=%s>>>\\n"'
        f' "{MANIFEST}" "$__files" "$__total" "$__skipped"'
    )
    return emit


def wrap(
    spec: dict[str, Any],
    *,
    artifact_max_bytes: int = ARTIFACT_MAX_BYTES,
    total_max_bytes: int = TOTAL_MAX_BYTES,
) -> dict[str, Any]:
    """Rewrite a job spec so its `inputs` and `outputs` actually travel.

    A spec declaring neither is returned unchanged -- wrapping it would put a
    shell between the scheduler and a command that does not need one.
    """
    inputs = spec.get("inputs") or {}
    outputs = [p for p in (spec.get("outputs") or []) if p]
    prologue = (spec.get("prologue") or "").strip()
    if not inputs and not outputs:
        return spec

    executable = spec.get("executable") or "/bin/true"
    argv = [str(a) for a in (spec.get("arguments") or [])]
    command = " ".join(shlex.quote(part) for part in [executable, *argv])

    body = [
        "set -euo pipefail",
        'work=$(mktemp -d)',
        'trap \'rm -rf "$work"\' EXIT',
        'touch "$work/.orbit-start"',
    ]
    stage_in, chunks = _stage_in(inputs)
    body += stage_in
    if prologue:
        body.append(prologue)
    # The real command's own output goes to stderr: stdout is the data channel,
    # and `set -e` means a failure never reaches the epilogue below.
    body.append(f'(cd "$work" && {command}) >&2')
    body += _stage_out(outputs, artifact_max_bytes, total_max_bytes)

    wrapped = dict(spec)
    wrapped["executable"] = "bash"
    # "_" occupies $0 so the payload chunks start at $1.
    wrapped["arguments"] = ["-lc", "\n".join(body), "_", *chunks]
    return wrapped


# --- reading ----------------------------------------------------------------


def collect(stdout: str, *, max_artifact_bytes: int = ARTIFACT_MAX_BYTES) -> ArtifactSet:
    """Read framed artifacts back out of a job's stdout. Never raises.

    `max_artifact_bytes` bounds what a single frame may expand to. The job wrote
    these frames, so neither the declared size nor the compression ratio is
    trustworthy on its own.
    """
    out = ArtifactSet()
    if not stdout:
        return out

    noise: list[str] = []
    pending: tuple[str, int, str] | None = None
    payload: list[str] = []
    skipping = False

    for line in stdout.splitlines():
        if skipping:
            # Swallow a refused frame's payload rather than reading it as log.
            if line == ARTIFACT_END:
                skipping = False
            continue
        if pending is None:
            begin = _BEGIN_RE.match(line)
            if begin:
                name = _unb64name(begin.group(1))
                size = int(begin.group(2))
                if size > max_artifact_bytes:
                    # Refuse before reading the payload at all.
                    out.skipped.append({
                        "name": name,
                        "bytes": size,
                        "reason": "declared_over_limit",
                    })
                    pending = None
                    skipping = True
                    continue
                pending = (name, size, begin.group(3))
                payload = []
                continue
            skipped = _SKIPPED_RE.match(line)
            if skipped:
                out.skipped.append({
                    "name": _unb64name(skipped.group(1)),
                    "bytes": int(skipped.group(2)),
                    "reason": skipped.group(3),
                })
                continue
            manifest = _MANIFEST_RE.match(line)
            if manifest:
                out.declared_files = int(manifest.group(1))
                out.declared_bytes = int(manifest.group(2))
                continue
            noise.append(line)
            continue

        if line == ARTIFACT_END:
            name, size, digest = pending
            pending = None
            try:
                raw = _inflate("".join(payload), max_artifact_bytes)
            except Exception as exc:
                out.error = f"{name}: could not decode ({exc})"
                continue
            if len(raw) != size:
                out.error = f"{name}: declared {size} bytes, decoded {len(raw)}"
                continue
            if digest and hashlib.sha256(raw).hexdigest() != digest:
                out.error = f"{name}: sha256 mismatch"
                continue
            out.files[name] = raw
            continue
        payload.append(line.strip())

    out.log = "\n".join(noise).strip()
    if pending is not None:
        # An opening frame with no close: the output was cut mid-artifact.
        out.truncated = True
        if not out.error:
            out.error = f"{pending[0]}: output ended inside the artifact"
    elif out.declared_files < 0 and (out.files or out.skipped):
        # Frames but no manifest: the tail was lost.
        out.truncated = True
    elif out.declared_files >= 0 and out.declared_files != len(out.files):
        out.truncated = True
        if not out.error:
            out.error = (
                f"manifest declared {out.declared_files} file(s), "
                f"{len(out.files)} arrived"
            )
    return out
