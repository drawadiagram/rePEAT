# Standing up an Orbit endpoint on Amarel

What the enzyme-redesign protocol needs that is not code. Every heavy step of that protocol is
site-bound — the UniRef30 database, the ProteinMPNN and HaloMPNN weights, the `aifold` /
`shared_als515` / `pyr2` conda environments, IPC2, PyRosetta, the AlphaFold3 apptainer image — so it
runs through the `hpc` task interface, which means through an Orbit broker and an endpoint on
Amarel. Until that pair is up, `TaskManager.interface_for` has no `"hpc"` key and degrades to
`local` with only a `log.info` (`tasks/manager.py:76-88`).

This is a deployment record as much as a procedure: fill in what each rung of the ladder at the
bottom actually returned, so the next person reads results rather than intentions.

Status as of 2026-10-06: **nothing here has been run.** Every command below is derived from the
Orbit CLIs and from `tasks/hpc/local_orbit.py`, which is the only working example of starting the
pair, against a localhost broker. Treat the whole document as unverified until the ladder says
otherwise.

### Environment survey, 2026-10-07

Taken read-only from a session **on `amarel3` itself** (`amarel3.amarel.rutgers.edu`, a login
node, no `SLURM_JOB_ID`), with the repo at `/cache/home/mh1314/rePEAT`. Nothing was started. It
changed the plan in three places: the agent can live on the login node too (§2, arrangement 0),
loopback there is shared with every other user (§2.1), and port 8000 is already taken (§4).

| Check | Result | Consequence |
| --- | --- | --- |
| host | `amarel3`, 64 cores, ~86 users logged in | agent, broker and endpoint can share one host — §2 (0) |
| `.venv/`, `refcodes/` | **both absent** — a fresh clone | rung 0 of the ladder; `setup.sh` fails until `refcodes/` is placed (backlog **B1**) |
| system python, `uv` | 3.9.21; `uv` in `~/.local/bin` | fine: `setup.sh` runs `uv venv --python 3.12` |
| `~/.radical/orbit` | absent | §3 not yet done |
| Slurm | `sbatch`/`sacct`/`squeue` on `PATH`; `sacctmgr` association `general`, qos `normal` | §5 values; the group `g_sdk94_1` and partition `p_sdk94_1` suggest a lab allocation may be the one to charge |
| partitions | `main`* `gpu` `mem` `nonpre` `cmain` `cgpu` `cmem` `p_sdk94_1`, all 3-day limits but the last | `protocol_gpu_queue=gpu` matches a real partition |
| `127.0.0.1:8000` | **LISTENing, held by another user** | the backend's default port *and* the broker's are taken — §4 |
| `apptainer`, `module` | present; `/projects/community/modulefiles` offers `alphafold/vs3.0.0-pgarias` | a candidate for `protocol_af3_modules`, unverified |
| keep-alive | `tmux`, `screen` present; `systemd --user` runs but `Linger=no` | §4 |
| `$HOME`, `/scratch/<netid>` | both `0700` | cert, key and token files stay private |
| Orbit source | reference checkout at `/home/mh1314/radical.orbit`: 0.8.0, branch `devel`, commit `c7ede0c` (2026-09-29), clean | §1's flags verified against it; four corrections below (§1, §4, §6) |

> `refcodes/` is gitignored and holds no recorded revision (backlog **B1**). On 2026-10-07 the flags
> below were checked against the reference checkout at `/home/mh1314/radical.orbit` (0.8.0, `devel`,
> `c7ede0c`) and every one matched. Re-read `--help` against any other revision. Upstream's own
> `DEPLOYMENT.md` in that checkout is the companion to this document.

---

## 1 · The two processes

Orbit is a broker plus one or more endpoints. The broker is a server; **the endpoint is a client of
the broker**, and so is this agent. That single fact decides the topology (§2).

`radical-orbit-broker.py`:

| Flag | Notes |
| --- | --- |
| `--cert PATH` | CLI > `$RADICAL_ORBIT_BROKER_CERT` > `~/.radical/orbit/broker_cert.pem` |
| `--key PATH` | CLI > `$RADICAL_ORBIT_BROKER_KEY` > `~/.radical/orbit/broker_key.pem`. **Refuses to start if the file is more permissive than 0600.** |
| `--host` / `--port` | default `0.0.0.0` / `8000` |
| `--plugins`, `-p` | default `default` (the broker role's set); `all`, `""`, and wildcards like `iri*` are accepted |
| `--token T` | CLI > `$RADICAL_ORBIT_BROKER_TOKEN` > `~/.radical/orbit/broker.token`. **Required unless `--no-auth`, and never auto-generated.** |
| `--no-auth` | local dev only; disables the ingress gate |
| `--no-gateway` | headless: only the token-gated WebSocket `/register`, no HTTP/SSE tier |

`radical-orbit-endpoint.py`:

| Flag | Notes |
| --- | --- |
| `--name`, `-n` | **Set it explicitly.** Defaults to `socket.gethostname()`; the source comments that a serving endpoint wants a stable, recoverable name. This is what `DESIGNAGENT_ORBIT_ENDPOINT` must match. |
| `--url`, `-u` | CLI > `$RADICAL_ORBIT_BROKER_URL`. **No file fallback** — unlike the cert and token. |
| `--cert`, `-c` | CLI > `$RADICAL_ORBIT_BROKER_CERT` > `~/.radical/orbit/broker_cert.pem` |
| `--token`, `-t` | same resolution as the broker's |
| `--plugins`, `-p` | **must include `psij`**, which submits every batch job. Use **`psij,sysinfo`** (below). Default `default` expands by host role (`plugin_host_base.py`) |
| `--tunnel` | `none` \| `forward` \| `reverse` — see §2 |
| `--tunnel-via HOST` | login host for `forward`; falls back to `$SLURM_SUBMIT_HOST` |
| `--log-level`, `-l` | or `RADICAL_ORBIT_LOG_LVL`, falling back to `RADICAL_LOG_LVL`; `DEBUG` before a first run is worth it |
| `$RADICAL_ORBIT_PSIJ_DIR` | where PSI/J keeps job stdout (`output/`) and generated submit scripts (`work/`). Default `~/.radical/orbit/psij` — **set it to `/scratch/<netid>/orbit-psij`**; upstream moved it after a full home quota failed every submit on Perlmutter |
| `$RADICAL_ORBIT_PSIJ_KEEP_FILES=1` | keep PSI/J's generated sbatch scripts — the direct way to see the `--chdir` and `--mem` it rendered (§6, rung 4) |

**Rhapsody does not load on a login node.** `tasks/hpc/orbit.py` opens a session on each of `rhapsody`
and `psij` *if the endpoint has it*, but Orbit decides per host: `PluginRhapsody.is_enabled` is true
only when `utils.host_role` says `compute` (inside an allocation) or `standalone` (no batch system).
On `amarel3` Slurm is detected and there is no allocation, so the role is `login` and rhapsody is
skipped with one INFO line (`[PluginHost] Skipping plugin (not applicable here)`). PSI/J loads on
every host. That costs this path nothing: every `hpc` submission here is `kind="job"`
(`nodes/orchestrator.py`, `nodes/protocol.py`), which goes to psij. It does cost ladder rung 1 (§6).

**Why `psij,sysinfo` and not the default.** On a login node `default` is
`psij,staging,sysinfo,queue_info`. Upstream's `plans/security_token_mitigation.md` describes psij
submit as arbitrary command execution and staging as able to create files under `$HOME`; both sit
behind the token, but nothing here uses `staging`, so it need not be exposed. `sysinfo` adds the
`host_role` route, which reports the role and batch system the endpoint actually detected.

Note the asymmetry that makes a clean deployment possible: cert and token both fall back to files
under `~/.radical/orbit/`, so once those are placed, neither process needs a flag for them. Only
`--url`, `--name` and `--plugins` have to be passed.

**Every path must be absolute.** `LocalOrbitStack` resolves its work dir in `__init__` for exactly
this reason: `_spawn` runs the children with `cwd=work_dir`, so a relative `--cert` resolved against
the work dir twice, and the broker exited "TLS cert not found" (the trap recorded in `CLAUDE.md`).

---

## 2 · Topology: who dials whom

Both the endpoint and this agent must reach the broker. Amarel login nodes do not generally accept
inbound connections, which rules out the naive arrangement. Four options, best first:

**(0) Everything on `amarel3`: agent, broker and endpoint.** When the agent itself runs on the login
node — as it does when this repo is checked out there — no process needs to cross the network to
reach the broker. The broker binds loopback, the endpoint and the backend both dial
`https://127.0.0.1:<port>`, and `~/.radical/orbit` is already where both look, so nothing is copied.
The ssh forward moves to the **browser**: the laptop runs
`ssh -N -L 8080:127.0.0.1:<backend port> <netid>@amarel3.hpc.rutgers.edu` and opens
`http://127.0.0.1:8080`. The price is that the agent's own process pool now runs on a shared login
node, so keep `DESIGNAGENT_POOL_WORKERS` small; nothing heavy runs in it on this path, since every
protocol step is a batch job. **Start here when the agent runs on Amarel**, and read §2.1 first.

**(a) Broker beside the endpoint on `amarel3`, agent reaches it over an ssh local forward.**
The broker binds loopback on the login node; the workstation runs
`ssh -N -L 8443:127.0.0.1:8443 <netid>@amarel3.hpc.rutgers.edu` and sets
`RADICAL_ORBIT_BROKER_URL=https://127.0.0.1:8443`. Nothing is exposed to the campus network, the
endpoint's connection never leaves the host, and the only moving part is an ssh session the user
already knows how to open. **Start here when the agent runs on a workstation.**

**(b) Orbit's own tunnel modes.** The endpoint supports `--tunnel forward`, which opens `ssh -L`
from a compute node to the login host (`--tunnel-via`, defaulting to `$SLURM_SUBMIT_HOST`), and
`--tunnel reverse`, which waits for a parent-side `ssh -R` and reads
`~/.radical/orbit/tunnels/<name>.port` off the shared filesystem. These exist for the case where the
endpoint runs *inside an allocation* rather than on a login node. Note what that costs us: the
protocol's transfer jobs submit with `executor: "local"` so they run on the endpoint host without a
queue slot, which requires `$PROJ` and `/scratch/<netid>` to be visible there. A login node
satisfies that; a compute node inside a finite allocation does not, and the endpoint dies with the
allocation. **Prefer a login node, and reach for the tunnel modes only if site policy forbids it.**

**(c) Broker on a third host both can reach.** Correct, and the most work: it needs a host with a
stable address, a real certificate story, and the ingress token distributed to two more places.
Only worth it if several people share one endpoint.

### 2.1 · Loopback is not private on a shared login node

This repo treats `127.0.0.1` as "only me" in three places, and on `amarel3` it is every logged-in
user (about 86 at the survey). None of this is new code; it is the same code on a different kind of
host.

- **The development broker runs `--no-auth`.** `LocalOrbitStack` (`tasks/hpc/local_orbit.py`) starts
  its broker with `--no-auth --host 127.0.0.1` on a random port, and an endpoint with the `psij`
  plugin behind it. That is what `pytest -m live`, `DESIGNAGENT_ORBIT_LOCAL=true` and
  `./scripts/dev.sh up` with an endpoint all start. On a shared host, any user who finds the port
  can submit through it — as you, on your allocation — for as long as it runs. Rungs 1 and 2 of the
  ladder start exactly this. Run them short-lived and attended, or inside an `srun` allocation where
  the node's loopback is shared with far fewer people (backlog **A16**).
- **Settings writes are open on loopback.** `_authorize_write` (`app.py`) admits any loopback caller
  when `DESIGNAGENT_ADMIN_TOKEN` is unset, and the read routes are open regardless. On `amarel3`,
  **always set `DESIGNAGENT_ADMIN_TOKEN`** (backlog **A9**).
- **The real broker of §4 is safe only because auth is on.** Never pass `--no-auth` to it here.

---

## 3 · TLS and the token

A self-signed pair is the supported shape, and **hostname matching is disabled for pinned certs** —
the CN does not need to match the broker host, which removes the usual self-signed pain. Endpoints
and clients pin the *cert*; the key never leaves the broker host.

On the broker host:

```bash
mkdir -p ~/.radical/orbit
openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout ~/.radical/orbit/broker_key.pem \
    -out    ~/.radical/orbit/broker_cert.pem \
    -days 365 -subj "/CN=$(hostname -f)"
chmod 600 ~/.radical/orbit/broker_key.pem      # the broker refuses to start otherwise

python3 -c "import secrets; print(secrets.token_urlsafe(32))" \
    > ~/.radical/orbit/broker.token
chmod 600 ~/.radical/orbit/broker.token
```

Then copy `broker_cert.pem` and `broker.token` to `~/.radical/orbit/` on every connecting host — in
arrangement (a) that is the workstation only, since the broker and endpoint share a home directory;
in arrangement (0), nothing. Upstream `DEPLOYMENT.md` ("Credential staging") has push-style commands,
and the broker prints pull-style one-liners in its startup banner.
Auth is **on by default** and the token is never generated by the software; `~/.radical/orbit` is
treated as operator-owned configuration. Do not pass `--no-auth`: the dev stack passes it, and it
disables the ingress gate only — the broker still needs a cert and key either way.

The cert expires in 365 days. Note the date here when it is generated.

---

## 4 · Starting the pair

**Pick ports first.** The survey found `127.0.0.1:8000` already held by another user, and nothing
reserves `8443` either. Check, then record what was chosen:

```bash
ss -ltnH | awk '{print $4}' | grep -E ':(8443|8001)$'   # empty means free, for now
```

The backend's default `--port 8000` is taken too, so run it on another port. `dev.sh up --port N`
then skips the frontend, because Vite's proxy target is fixed at `:8000` (`scripts/dev.sh`); on
`amarel3` either run the backend alone and use the API, or run Vite by hand with its proxy target
changed. Built frontend assets served some other way are not set up in this repo.

On `amarel3`, in the venv that has `radical.orbit` installed:

```bash
# broker: loopback only, for arrangement (a)
radical-orbit-broker.py --host 127.0.0.1 --port 8443 &

# endpoint: psij submits the jobs; rhapsody would be skipped here anyway (§1)
export RADICAL_ORBIT_PSIJ_DIR=/scratch/$USER/orbit-psij
radical-orbit-endpoint.py \
    --name amarel3 -p psij,sysinfo \
    --url https://127.0.0.1:8443 &
```

`radical-orbit-endpoint-wrapper.sh` is the better entry point when the endpoint is launched from
somewhere that may scrub the interpreter (PSI/J, IRI, a batch script): it resolves the venv from its
own location and prepends both `purelib` and `platlib` to `PYTHONPATH`.

**Readiness.** `/topology` is still not a route (it reads as a plugin name, backlog **C7**), but at
`c7ede0c` the gateway serves `GET /endpoints`, token-gated like every capability route. Three checks,
any of which will do:

```bash
curl -s --cacert ~/.radical/orbit/broker_cert.pem \
     -H "Authorization: Bearer $(cat ~/.radical/orbit/broker.token)" \
     https://127.0.0.1:8443/endpoints        # expect amarel3, "connected": true, plugins incl. psij
```

- the endpoint's own log line `registered as '<name>'`, in `~/.radical/orbit/logs/<name>.log` —
  which is what `LocalOrbitStack._wait_for_endpoint` greps for (and which also lists the plugins
  that actually loaded);
- the client's `rt.topology()`, which is what `OrbitInterface.connect()` polls.

`GET /endpoints` does not exist with `--no-gateway`; don't pass it here.

And the ordering trap from `CLAUDE.md`: **the endpoint registers a few seconds after the broker's
port opens.** A round planned before then silently takes the heuristic branch. `dev.sh up` waits for
`hpc: true` for this reason.

**Keeping it alive.** The endpoint must outlive the login session, and sites do reap long-lived
login-node processes. On `amarel3` use **`tmux`** (installed). A user systemd unit is not an option
as things stand: `systemd --user` runs, but lingering is off (`loginctl show-user` reports
`Linger=no`), so the unit stops at logout, and enabling it needs an administrator. Note also that
`amarel3` is one of several login nodes: a tmux session lives only on the node it was started on, so
reconnect to `amarel3` by name, not through a round-robin alias. Record here which was used, and
how to tell it died: the absence of a fresh `registered as` line after a restart, or
`--check-config --probe` failing to find the endpoint.

---

## 5 · The settings this produces

An env block to paste or put in a direnv file. These are environment-only
settings by design: each names a path or shell the server will run on the endpoint under the site's
allocation, so none is remotely writable (`tests/test_settings.py::NOT_REMOTELY_WRITABLE`).

```bash
export DESIGNAGENT_ORBIT_ENABLED=true
export RADICAL_ORBIT_BROKER_URL=https://127.0.0.1:8443   # (0): the broker itself; (a): the ssh -L end
export RADICAL_ORBIT_BROKER_CERT=$HOME/.radical/orbit/broker_cert.pem
export RADICAL_ORBIT_BROKER_TOKEN=...                    # or leave it to the token file
export DESIGNAGENT_ORBIT_ENDPOINT=amarel3                # must equal the endpoint's --name
export DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm
export DESIGNAGENT_ORBIT_ACCOUNT=general                 # from sacctmgr; confirm it is the one to charge
export DESIGNAGENT_ORBIT_QUEUE=main
export DESIGNAGENT_ORBIT_JOB_GPUS=0                      # the protocol's specs ask per job
```

In arrangement (0), add the token that closes §2.1's write route, and keep the pool small:

```bash
export DESIGNAGENT_ADMIN_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
export DESIGNAGENT_POOL_WORKERS=2
```

`ACCOUNT=general` is what `sacctmgr show assoc user=$USER` returned at the survey; the
`g_sdk94_1` group membership suggests a lab account may be intended instead.

`DESIGNAGENT_ORBIT_JOB_GPUS=0` matters: `_job_params` otherwise forces one GPU onto *every* spec,
and hhblits and the MPNN jobs need none. Check the lot with
`.venv/bin/python -m designagent --check-config --probe` before spending a queue slot — it masks
secrets and tries each credential.

---

## 6 · Acceptance ladder

Each rung is a command, and each has a result to record. **Rung 0's Orbit install is not in `setup.sh`.** That script installs asyncflow, rhapsody and
flowgentic and never `radical.orbit`; `pyproject.toml` does not list it, and `tasks/hpc/orbit.py`
imports it lazily, so the gap stays invisible until `hpc` is switched on (backlog **B1**). `--no-deps`
because Orbit requires `rhapsody-py` from PyPI, which would displace the editable `refcodes/rhapsody`;
install what else `requirements.txt` in the checkout names (`psij-python`, `websockets`,
`websocket-client`, …) by name, then confirm `uv pip show rhapsody-py` still points at `refcodes/`.
Symlinking the checkout as `refcodes/radical.orbit` also works for the CLI scripts —
`local_orbit._script` searches there — but not for the import.

**Rung 1 on a login node.** `LocalOrbitStack` asks for `rhapsody,psij`, and
`test_executable_task_runs_and_returns_output` submits a `kind="function"` task, which needs
rhapsody. Read from the code, not run: on `amarel3` itself rhapsody is skipped (§1) and that test
should fail with "endpoint has no rhapsody plugin". Inside `srun` the role is `compute`, both plugins
load, and §2.1's shared-loopback exposure shrinks to one node — so run rungs 1 and 2 there
(backlog **D4**).

Do not skip to the bottom: the whole point
of the first two is that they cost nothing — in queue time. On a shared login node they are not free
of risk: both start a `--no-auth` broker (§2.1).

| # | Command | Proves | Result |
| --- | --- | --- | --- |
| 0 | place `refcodes/`, then `./scripts/setup.sh`; then `uv pip install --python .venv --no-deps -e /home/mh1314/radical.orbit` and its deps that are missing; then `./scripts/setup.sh --check` | the venv exists and can import `radical.orbit`; nothing below runs without it | 2026-10-07: not started — `refcodes/` and `.venv/` absent on `amarel3` |
| 1 | `.venv/bin/python -m pytest -q -m live` — **inside `srun`** | the client path against a localhost broker we start ourselves | |
| 2 | `DESIGNAGENT_ORBIT_LOCAL=true .venv/bin/python -m pytest -q -m remote` | the remote tier's assertions, rehearsed with no allocation | |
| 3 | `.venv/bin/python -m designagent --check-config --probe` | the credentials reach the real broker and the endpoint is visible | |
| 4 | `.venv/bin/python -m pytest -q -m remote` | submit → poll → logs → cancel across a real scheduler. **This is backlog A1's question.** | |
| 5 | one real hhblits run | a 12-hour walltime, 0 GPUs, 32 GiB, and a `directory` that persists | |
| 6 | the full protocol spine | everything else | |

Three things to write down from rung 4, because each is the first evidence we will have of it:

- **the Slurm `native_id`** reported back in `handle.meta` — the only link between a PSI/J handle and
  `sacct`, and therefore the only route to the lab notebook's `### Jobs` table now that the skill's
  `slurm.%N.%j.out` filenames are gone;
- **the endpoint's detected batch system**, since
  `batch_system.detect_batch_system().default_custom_attributes()` merges site defaults underneath
  our own `custom_attributes`, and a surprise there surfaces as a scheduler rejection rather than a
  Python error. At `c7ede0c` the Slurm backend does not override it and the base returns `{}`
  (`batch_system.py`), so on Amarel nothing should be merged — confirm with `sysinfo`'s `host_role`
  (expect `scheduler: slurm`, `psij_executor: slurm`) and with a kept submit script;
- **whether `directory` is honoured.** It is forwarded by `to_psij_spec` and set on the PSI/J spec by
  the broker's plugin, and the Slurm template emits `#SBATCH --chdir=`, but no caller in this repo
  sets it and no test exercises it. The protocol's compute jobs depend on it entirely — and Slurm
  fails a job whose `--chdir` does not exist before anything runs, so `$PROJ` must be created first.
