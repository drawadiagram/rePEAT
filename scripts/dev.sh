#!/usr/bin/env bash
# Bring the agent up locally for testing, and take it down again cleanly.
#
#   ./scripts/dev.sh up            backend + frontend, ProteinMPNN if installed
#   ./scripts/dev.sh up --no-mpnn  no endpoint: the heuristic path
#   ./scripts/dev.sh up --backend  backend only, no Vite
#   ./scripts/dev.sh down          stop both, and wait for the lock to clear
#   ./scripts/dev.sh status        what is running, and what it reports
#   ./scripts/dev.sh restart       down, then up, with the same flags
#   ./scripts/dev.sh logs [-f]     tail the backend log
#
#   --port N        backend port (default 8000; Vite's proxy is fixed at 8000,
#                   so anything else is backend-only testing)
#   --scratch       an isolated data dir under data/dev/scratch, so a test run
#                   cannot touch a real campaign
#
# Everything here is a trap that cost time when done by hand:
#
#   * Kuzu takes an exclusive lock on the graph store. A second backend on the
#     same data dir does not degrade, it dies in `lifespan` — so `up` refuses
#     when the lock is held and names the holder, and `down` waits for the lock
#     to actually clear rather than assuming SIGTERM was instant.
#   * SIGTERM does not stop the backend at all: asyncflow installs its own
#     handler, logs "Shutdown completed for all components", and the process
#     stays alive — so uvicorn's lifespan shutdown never runs. `down` therefore
#     always ends up escalating, and says so. Nothing is lost; the lake and the
#     checkpoints are already on disk. See plans/BACKLOG.md C8.
#   * `pkill -f "port 8000"` matches the shell running it and kills itself.
#     Processes are found by reading /proc and skipping this script's own tree.
#   * The local Orbit broker needs a few seconds after the port answers before
#     an endpoint registers, so `up` waits for `hpc: true` rather than HTTP 200
#     when an endpoint is expected. A premature round silently takes the
#     heuristic branch.
#   * `npm run test:e2e` declares its own backend on :8000 with
#     reuseExistingServer, so it adopts whatever `up` started and leaves it
#     alone — but if nothing is up it starts one and kills it on exit. Run `up`
#     first if you want the server to survive the test.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
PY=.venv/bin/python
DEV=data/dev
PIDS=$DEV/pids

mkdir -p "$PIDS"

fail() { printf '\n  FAIL  %s\n\n' "$1" >&2; exit 1; }
ok()   { printf '  ok    %s\n' "$1"; }
note() { printf '  --    %s\n' "$1"; }

# --- arguments -------------------------------------------------------------

CMD=${1:-status}
shift || true

PORT=8000
WANT_MPNN=1
WANT_FRONTEND=1
SCRATCH=0
FOLLOW=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-mpnn)  WANT_MPNN=0 ;;
    --backend)  WANT_FRONTEND=0 ;;
    --scratch)  SCRATCH=1 ;;
    --port)     PORT=${2:?--port needs a number}; shift ;;
    -f|--follow) FOLLOW=1 ;;
    *) fail "unknown option: $1" ;;
  esac
  shift
done

DATA_DIR=""
if [[ $SCRATCH -eq 1 ]]; then
  # Absolute, because the Orbit broker runs with its work dir as cwd and a
  # relative path would resolve against itself.
  DATA_DIR="$ROOT/$DEV/scratch"
fi
LOCK="${DATA_DIR:-$ROOT/data}/lake/graph"
BACKEND_LOG=$DEV/backend.log
FRONTEND_LOG=$DEV/frontend.log

# --- process helpers -------------------------------------------------------

#: Every designagent process, excluding this script and its children. `pgrep -f`
#: would match the pattern inside our own command line.
designagent_pids() {
  local pid
  for pid in /proc/[0-9]*; do
    pid=${pid#/proc/}
    [[ $pid == "$$" || $pid == "$PPID" ]] && continue
    grep -qa 'designagent' "/proc/$pid/cmdline" 2>/dev/null || continue
    # A python process, not a grep or an editor that happens to mention it.
    grep -qa 'python' "/proc/$pid/cmdline" 2>/dev/null || continue
    echo "$pid"
  done
}

#: This repo's Vite dev server, however it was started — by pidfile or by hand.
#: Matched on cwd as well as cmdline so another project's Vite is never touched.
frontend_pids() {
  local pid cwd
  for pid in /proc/[0-9]*; do
    pid=${pid#/proc/}
    [[ $pid == "$$" || $pid == "$PPID" ]] && continue
    grep -qa 'vite' "/proc/$pid/cmdline" 2>/dev/null || continue
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null) || continue
    [[ $cwd == "$ROOT/frontend" || $cwd == "$ROOT" ]] || continue
    echo "$pid"
  done
}

lock_held() { command -v fuser >/dev/null && fuser "$LOCK" >/dev/null 2>&1; }

lock_holder() {
  command -v fuser >/dev/null || { echo "unknown (no fuser)"; return; }
  fuser "$LOCK" 2>/dev/null | tr -s ' ' || echo none
}

health() { curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null; }

#: One field out of /api/health, without a jq dependency.
health_field() {
  health | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('$1'))" 2>/dev/null || echo ""
}

# --- up --------------------------------------------------------------------

mpnn_command() {
  local py="$ROOT/.venv-mpnn/bin/python"
  local script="$ROOT/refcodes/ProteinMPNN/protein_mpnn_run.py"
  [[ -x $py && -f $script ]] || return 1
  echo "$py $script"
}

do_up() {
  [[ -x $PY ]] || fail "no .venv — run ./scripts/setup.sh"

  if health >/dev/null 2>&1; then
    note "something already answers on :$PORT — 'status' to see what, 'restart' to replace it"
    do_status
    return
  fi
  if lock_held; then
    fail "the graph store is locked by: $(lock_holder)
        Kuzu is exclusive, so a second backend on this data dir would die at
        startup. Use './scripts/dev.sh down' first, or 'up --scratch'."
  fi

  local env=()
  if [[ -n $DATA_DIR ]]; then
    mkdir -p "$DATA_DIR"
    env+=("DESIGNAGENT_DATA_DIR=$DATA_DIR")
    note "scratch data dir: $DATA_DIR"
  fi

  local expect_hpc=0
  if [[ $WANT_MPNN -eq 1 ]]; then
    local cmd
    if cmd=$(mpnn_command); then
      env+=(
        "DESIGNAGENT_ORBIT_LOCAL=true"
        "DESIGNAGENT_ORBIT_JOB_GPUS=0"
        "DESIGNAGENT_MPNN_COMMAND=$cmd"
      )
      expect_hpc=1
      ok "ProteinMPNN found; attaching a local endpoint (CPU, no GPU requested)"
    else
      note "ProteinMPNN is not installed (./scripts/setup_mpnn.sh), so no endpoint"
      note "rounds will take the heuristic path — see BROWSER_TESTS.md T10"
    fi
  else
    note "--no-mpnn: no endpoint, so rounds take the heuristic path"
  fi

  mkdir -p "$DEV"
  printf '==> backend on :%s\n' "$PORT"
  env "${env[@]}" nohup "$PY" -m designagent --port "$PORT" \
    > "$BACKEND_LOG" 2>&1 &
  echo $! > "$PIDS/backend.pid"

  local i
  for i in $(seq 1 90); do
    health >/dev/null 2>&1 && break
    # Died during startup? Say why instead of timing out.
    if ! kill -0 "$(cat "$PIDS/backend.pid")" 2>/dev/null; then
      printf '\n'; tail -25 "$BACKEND_LOG" >&2
      fail "the backend exited during startup (full log: $BACKEND_LOG)"
    fi
    sleep 1
  done
  health >/dev/null 2>&1 || fail "no health response after 90s (log: $BACKEND_LOG)"
  ok "backend answering on :$PORT"

  if [[ $expect_hpc -eq 1 ]]; then
    # The broker's endpoint registers a little after the port opens, and a round
    # planned before then silently uses the heuristic proposer.
    for i in $(seq 1 45); do
      [[ $(health_field hpc) == "True" ]] && break
      sleep 1
    done
    if [[ $(health_field hpc) == "True" ]]; then
      ok "endpoint attached (hpc: true)"
    else
      note "no endpoint after 45s — rounds will use the heuristic path"
      note "grep -i orbit $BACKEND_LOG"
    fi
  fi

  if [[ $WANT_FRONTEND -eq 1 ]]; then
    [[ -d frontend/node_modules ]] || fail "frontend/node_modules is missing — cd frontend && npm install"
    if [[ $PORT -ne 8000 ]]; then
      note "Vite's proxy target is fixed at :8000, so skipping it for port $PORT"
    else
      printf '==> frontend on :5173\n'
      (cd frontend && nohup npm run dev > "$ROOT/$FRONTEND_LOG" 2>&1 & echo $! > "$ROOT/$PIDS/frontend.pid")
      for i in $(seq 1 45); do
        curl -fsS --max-time 2 http://localhost:5173/ >/dev/null 2>&1 && break
        sleep 1
      done
      curl -fsS --max-time 2 http://localhost:5173/ >/dev/null 2>&1 \
        && ok "frontend answering on :5173" \
        || note "frontend did not come up (log: $FRONTEND_LOG)"
    fi
  fi

  printf '\n'
  do_status
  printf '\n'
  if curl -fsS --max-time 2 http://localhost:5173/api/health >/dev/null 2>&1; then
    # Only when Vite is actually in front of *this* backend: its proxy target is
    # fixed at :8000, so a run on any other port is not what the page talks to.
    printf '  Open http://localhost:5173 — and use "New session" before testing,\n'
    printf '  because a reload restores the previous campaign rather than clearing it.\n'
    printf '  Tests to run by hand: frontend/e2e/BROWSER_TESTS.md\n\n'
  else
    printf '  Backend only. The API is at http://127.0.0.1:%s/api/health\n\n' "$PORT"
  fi
}

# --- down ------------------------------------------------------------------

do_down() {
  local pids
  pids=$(designagent_pids || true)
  if [[ -z $pids ]]; then
    note "no backend running"
  else
    printf '==> stopping backend (%s)\n' "$(echo "$pids" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null || true

    local i cleared=0
    for i in $(seq 1 30); do
      if ! lock_held && [[ -z $(designagent_pids || true) ]]; then cleared=1; break; fi
      sleep 1
    done
    if [[ $cleared -eq 0 ]]; then
      # Expected, not exceptional: asyncflow swallows SIGTERM (BACKLOG C8).
      note "SIGTERM did not stop it (asyncflow swallows it — BACKLOG C8); escalating"
      pids=$(designagent_pids || true)
      # shellcheck disable=SC2086
      [[ -n $pids ]] && kill -9 $pids 2>/dev/null || true
      sleep 2
    fi
    lock_held && fail "the graph store is still locked by: $(lock_holder)" || ok "graph store unlocked"
  fi

  local fe
  fe=$(frontend_pids || true)
  if [[ -z $fe ]]; then
    note "no frontend running"
  else
    printf '==> stopping frontend (%s)\n' "$(echo "$fe" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $fe 2>/dev/null || true
    local i
    for i in $(seq 1 10); do
      [[ -z $(frontend_pids || true) ]] && break
      sleep 1
    done
    fe=$(frontend_pids || true)
    # shellcheck disable=SC2086
    [[ -n $fe ]] && kill -9 $fe 2>/dev/null || true
    ok "frontend stopped"
  fi
  rm -f "$PIDS/frontend.pid" "$PIDS/backend.pid"
}

# --- status ----------------------------------------------------------------

do_status() {
  local pids
  pids=$(designagent_pids || true)
  if [[ -z $pids ]]; then
    note "backend:  not running"
  else
    printf '  --    backend:  pid(s) %s\n' "$(echo "$pids" | tr '\n' ' ')"
  fi

  if health >/dev/null 2>&1; then
    local hpc llm fold
    hpc=$(health_field hpc); llm=$(health_field llm); fold=$(health_field fold_backend)
    printf '  --    :%-5s   hpc: %-5s  llm: %-5s  fold: %s\n' "$PORT" "$hpc" "$llm" "$fold"
    [[ $hpc == "True" ]] \
      && ok "remote execution attached — a redesign round will use ProteinMPNN" \
      || note "no endpoint — a redesign round will use the heuristic proposer"
  else
    note ":$PORT      not answering"
  fi

  if curl -fsS --max-time 2 http://localhost:5173/ >/dev/null 2>&1; then
    printf '  --    :5173    frontend up (pid %s)\n' "$(frontend_pids | tr '\n' ' ')"
  else
    printf '  --    :5173    frontend down\n'
  fi

  lock_held && printf '  --    lock:     held by %s\n' "$(lock_holder)" \
            || printf '  --    lock:     free\n'
}

# --- dispatch --------------------------------------------------------------

case "$CMD" in
  up)      do_up ;;
  down)    do_down ;;
  restart) do_down; printf '\n'; do_up ;;
  status)  do_status ;;
  logs)
    [[ -f $BACKEND_LOG ]] || fail "no log yet at $BACKEND_LOG"
    if [[ $FOLLOW -eq 1 ]]; then tail -f "$BACKEND_LOG"; else tail -40 "$BACKEND_LOG"; fi ;;
  *) fail "unknown command: $CMD (up | down | restart | status | logs)" ;;
esac
