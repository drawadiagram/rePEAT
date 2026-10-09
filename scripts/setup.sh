#!/usr/bin/env bash
# The only supported way to build this environment.
#
# `uv sync` cannot work here: radical.asyncflow, rhapsody and flowgentic are
# local editable installs from refcodes/, and flowgentic pins radical-asyncflow
# and academy-py to git URLs that fight the local checkouts. So it is installed
# --no-deps, and this script is what encodes that. See pyproject.toml and
# "A fresh clone cannot be built" in plans/BACKLOG.md.
#
#   ./scripts/setup.sh            build .venv and verify it
#   ./scripts/setup.sh --check    verify an existing .venv, install nothing
#
# --check is the useful one in CI and after a pull: it fails loudly with the
# missing piece rather than letting the app die deep inside an import.
set -euo pipefail

cd "$(dirname "$0")/.."
PY=.venv/bin/python
CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

fail() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }
ok()   { printf '  ok    %s\n' "$1"; }
warn() { printf '  warn  %s\n' "$1"; }

# The revisions this project was last built and checked against (plans/BACKLOG.md
# B1). refcodes/ is gitignored, so this list is the only record in the repo. A
# mismatch warns rather than fails: a newer checkout may be fine, but the
# backlog's claims about these packages were made against these commits.
REFCODES_PINS=(
  "radical.asyncflow 038d52a446404a474ea3ea4388ec9413676aad90"
  "rhapsody          71536accb8cba26c2b1d8ab954a8910668407401"
  "flowgentic        dd27bd8b7e6edb2995038c9c1cc9b2fec3b7c7e9"   # branch demo/radical
  "radical.orbit     c7ede0c4d3d204ffdbe0d6a074fb4cd1eae432d2"   # branch devel, 0.8.0
)

if [[ $CHECK_ONLY -eq 0 ]]; then
  command -v uv >/dev/null || fail "uv is not installed: https://docs.astral.sh/uv/"
  [[ -d refcodes/flowgentic ]] || fail \
    "refcodes/ is missing. It is gitignored, so a fresh clone does not have it;
        you need local checkouts of radical.asyncflow, rhapsody and flowgentic.
        See plans/BACKLOG.md B1 — nothing in this repo records which revisions."

  echo "==> creating .venv"
  uv venv --python 3.12 .venv

  echo "==> installing the local middleware"
  uv pip install -e refcodes/radical.asyncflow -e refcodes/rhapsody
  # --no-deps: flowgentic's pins pull radical-asyncflow and academy-py from git.
  # The deps it then lacks (academy, langchain-community, langchain-mcp-adapters)
  # are not on any path this project uses.
  uv pip install --no-deps -e refcodes/flowgentic

  # Orbit is what the `hpc` interface and `pytest -m live` need. --no-deps
  # because its requirements name rhapsody-py, which would displace the editable
  # refcodes/rhapsody; the rest of its requirements.txt is installed by name.
  if [[ -d refcodes/radical.orbit ]]; then
    echo "==> installing radical.orbit"
    uv pip install --no-deps -e refcodes/radical.orbit
    uv pip install httpx msgpack cloudpickle requests websockets websocket-client \
      fastapi uvicorn psutil rich psij-python globus-sdk authlib
    # rhapsody's `telemetry` extra, by name for the same reason. Orbit's rhapsody
    # plugin calls Session.start_telemetry whenever the method exists, and that
    # imports opentelemetry.sdk: without it every rhapsody session fails to
    # open, and -m live and -m remote skip rather than fail.
    uv pip install 'opentelemetry-sdk>=1.20.0' nvidia-ml-py
  else
    warn "refcodes/radical.orbit is absent: the hpc interface and -m live will not work"
  fi

  echo "==> installing designagent"
  uv pip install -e '.[dev]'    # '.[chem]' adds ChemGraph; see the README
fi

echo "==> verifying"
[[ -x $PY ]] || fail ".venv is missing. Run ./scripts/setup.sh with no arguments."

# config.yml is read from the CWD at `import flowgentic` time, and flowgentic
# does APP_SETTINGS["logger"]["level"] with no fallback once it finds a file.
[[ -f config.yml ]] || fail "config.yml is missing from the repo root; flowgentic reads it from the CWD."
$PY - <<'EOF' || fail "config.yml is missing a key flowgentic requires"
import sys, yaml
cfg = yaml.safe_load(open("config.yml")) or {}
missing = [k for k in ("agent_execution", "logger") if k not in cfg]
if missing:
    print(f"config.yml lacks: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)
EOF
ok "config.yml"

for pin in "${REFCODES_PINS[@]}"; do
  read -r name want _ <<<"$pin"
  dir=refcodes/$name
  [[ -d $dir/.git ]] || continue
  # safe.directory for this one call: refcodes/ is often copied in from another
  # machine, owned by a uid git distrusts, and this must not need a global.
  have=$(git -c safe.directory="$PWD/$dir" -C "$dir" rev-parse HEAD 2>/dev/null || echo unknown)
  if [[ $have == "$want" ]]; then ok "$dir @ ${want:0:7}"
  else warn "$dir is at ${have:0:12}, not the pinned ${want:0:12}"; fi
done

for mod in radical.asyncflow rhapsody flowgentic designagent; do
  $PY -c "import importlib; importlib.import_module('$mod')" 2>/dev/null \
    || fail "cannot import $mod — the environment is incomplete. Re-run ./scripts/setup.sh"
  ok "import $mod"
done

if [[ -d refcodes/radical.orbit ]]; then
  $PY -c "import radical.orbit" 2>/dev/null \
    || fail "refcodes/radical.orbit is present but does not import. Re-run ./scripts/setup.sh"
  ok "import radical.orbit"
  $PY -c "import opentelemetry.sdk" 2>/dev/null \
    || fail "opentelemetry-sdk is missing: Orbit's rhapsody sessions cannot open. Re-run ./scripts/setup.sh"
  ok "import opentelemetry.sdk"
  # rhapsody-py from PyPI must not have displaced the editable checkout.
  $PY -c "import pathlib, sys, rhapsody
sys.exit(0 if 'refcodes' in pathlib.Path(rhapsody.__file__).resolve().parts else 1)" \
    || fail "rhapsody no longer resolves to refcodes/rhapsody: rhapsody-py displaced it"
  ok "rhapsody is the refcodes checkout"
fi

$PY -c "from designagent.graph.build import build_graph" 2>/dev/null \
  || fail "designagent imports, but the graph does not build"
ok "graph builds"

printf '\n  Environment is good.\n'
printf '    .venv/bin/python -m designagent --reload   # :8000\n'
printf '    .venv/bin/python -m pytest -q              # 399 offline tests\n'
printf '    .venv/bin/python -m pytest -q -m live      # 12 tests, starts a real broker\n\n'
