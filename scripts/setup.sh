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

for mod in radical.asyncflow rhapsody flowgentic designagent; do
  $PY -c "import importlib; importlib.import_module('$mod')" 2>/dev/null \
    || fail "cannot import $mod — the environment is incomplete. Re-run ./scripts/setup.sh"
  ok "import $mod"
done

$PY -c "from designagent.graph.build import build_graph" 2>/dev/null \
  || fail "designagent imports, but the graph does not build"
ok "graph builds"

printf '\n  Environment is good.\n'
printf '    .venv/bin/python -m designagent --reload   # :8000\n'
printf '    .venv/bin/python -m pytest -q              # 93 offline tests\n'
printf '    .venv/bin/python -m pytest -q -m live      # 6 tests, starts a real broker\n\n'
