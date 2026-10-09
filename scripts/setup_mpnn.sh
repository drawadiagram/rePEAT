#!/usr/bin/env bash
# Install real ProteinMPNN for a local, CPU-only run.
#
# Deliberately not part of scripts/setup.sh, which CLAUDE.md names as the only
# supported way to build .venv. This is optional and large: torch is a ~200 MB
# download and none of the offline tests need it. A fresh clone should not pay
# for a GPU-less optional dependency it may never use.
#
#   ./scripts/setup_mpnn.sh           clone ProteinMPNN and build .venv-mpnn
#   ./scripts/setup_mpnn.sh --check   verify an existing install, install nothing
#
# --check prints the DESIGNAGENT_MPNN_COMMAND to export, and fails with the
# missing piece named — the same contract scripts/setup.sh --check has.
#
# The result is the real model with real weights, which is what makes a round
# attributable to `model_name=v_48_020` rather than to a stub. It proves nothing
# about a scheduler, a queue, an allocation or a GPU: the local PSI/J executor
# forks a process. The real thing is plans/AMAREL_ENDPOINT.md, rung 4.
set -euo pipefail

cd "$(dirname "$0")/.."

# Pinned, because "whatever main is today" is how refcodes/ ended up with no
# recorded revisions (plans/BACKLOG.md B1).
MPNN_REPO=https://github.com/dauparas/ProteinMPNN.git
MPNN_REV=8907e6671bfbfc92303b5f79c4b5e6ce47cdef57
MPNN_DIR=refcodes/ProteinMPNN
VENV=.venv-mpnn

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

fail() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }
ok()   { printf '  ok    %s\n' "$1"; }

if [[ $CHECK_ONLY -eq 0 ]]; then
  command -v uv >/dev/null || fail "uv is not installed: https://docs.astral.sh/uv/"
  command -v git >/dev/null || fail "git is not installed"

  if [[ -d "$MPNN_DIR/.git" ]]; then
    echo "==> $MPNN_DIR exists; fetching $MPNN_REV"
    git -C "$MPNN_DIR" fetch --quiet origin "$MPNN_REV" 2>/dev/null \
      || git -C "$MPNN_DIR" fetch --quiet origin
  else
    echo "==> cloning ProteinMPNN into $MPNN_DIR"
    mkdir -p refcodes
    git clone --quiet "$MPNN_REPO" "$MPNN_DIR"
  fi
  git -C "$MPNN_DIR" checkout --quiet "$MPNN_REV" \
    || fail "could not check out $MPNN_REV — the pin may need updating"

  echo "==> creating $VENV with CPU-only torch"
  uv venv --python 3.12 "$VENV"
  # The CPU index keeps this to ~200 MB instead of pulling the CUDA wheels,
  # which are gigabytes and useless on a host with no driver.
  VIRTUAL_ENV="$VENV" uv pip install --quiet \
    --index-url https://download.pytorch.org/whl/cpu torch
  VIRTUAL_ENV="$VENV" uv pip install --quiet numpy
fi

# --- verification ---------------------------------------------------------

[[ -d $MPNN_DIR ]] || fail "$MPNN_DIR is missing. Run ./scripts/setup_mpnn.sh"
ok "$MPNN_DIR"

REV=$(git -C "$MPNN_DIR" rev-parse HEAD 2>/dev/null || echo unknown)
[[ "$REV" == "$MPNN_REV" ]] \
  || printf '  warn  %s is at %s, not the pinned %s\n' "$MPNN_DIR" "${REV:0:12}" "${MPNN_REV:0:12}"

[[ -f $MPNN_DIR/protein_mpnn_run.py ]] \
  || fail "$MPNN_DIR/protein_mpnn_run.py is missing — is this really a ProteinMPNN checkout?"
ok "protein_mpnn_run.py"

WEIGHTS=0
for dir in vanilla_model_weights ca_model_weights soluble_model_weights; do
  if compgen -G "$MPNN_DIR/$dir/*.pt" >/dev/null; then
    names=$(cd "$MPNN_DIR/$dir" && ls *.pt | sed 's/\.pt$//' | tr '\n' ' ')
    ok "$dir: $names"
    WEIGHTS=1
  fi
done
[[ $WEIGHTS -eq 1 ]] || fail \
  "no .pt weights found under $MPNN_DIR. ProteinMPNN vendors them in the repo;
        a partial clone or an LFS-less checkout is the usual cause."

[[ -x $VENV/bin/python ]] || fail "$VENV is missing. Run ./scripts/setup_mpnn.sh"
"$VENV/bin/python" -c "import torch, numpy" 2>/dev/null \
  || fail "$VENV cannot import torch and numpy. Re-run ./scripts/setup_mpnn.sh"
ok "$(cd . && "$VENV/bin/python" -c 'import torch; print("torch " + torch.__version__ + (" (CUDA)" if torch.cuda.is_available() else " (CPU)"))')"

CMD="$VENV/bin/python $MPNN_DIR/protein_mpnn_run.py"

printf '\n  ProteinMPNN is installed. Export this and the hpc path will use it:\n\n'
printf '    export DESIGNAGENT_MPNN_COMMAND="%s"\n' "$CMD"
printf '    export DESIGNAGENT_ORBIT_JOB_GPUS=0        # this host has no GPU\n'
printf '    export DESIGNAGENT_ORBIT_LOCAL=true        # a real broker on localhost\n\n'
printf '  Then:\n'
printf '    .venv/bin/python -m pytest -q -m live tests/test_orbit_local.py\n'
printf '    .venv/bin/python -m designagent\n\n'
