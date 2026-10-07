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

> `refcodes/` is gitignored and holds no recorded revision (backlog **B1**), so the flags below are
> read off whatever `radical.orbit` is checked out here. Re-read `--help` before trusting them.

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
| `--plugins`, `-p` | **must be `rhapsody,psij`** — `psij` is what submits batch jobs, and `tasks/hpc/orbit.py` opens sessions on both |
| `--tunnel` | `none` \| `forward` \| `reverse` — see §2 |
| `--tunnel-via HOST` | login host for `forward`; falls back to `$SLURM_SUBMIT_HOST` |
| `--log-level`, `-l` | or `RADICAL_ORBIT_LOG_LVL`; `DEBUG` before a first run is worth it |

Note the asymmetry that makes a clean deployment possible: cert and token both fall back to files
under `~/.radical/orbit/`, so once those are placed, neither process needs a flag for them. Only
`--url`, `--name` and `--plugins` have to be passed.

**Every path must be absolute.** `LocalOrbitStack` resolves its work dir in `__init__` for exactly
this reason: `_spawn` runs the children with `cwd=work_dir`, so a relative `--cert` resolved against
the work dir twice, and the broker exited "TLS cert not found" (the trap recorded in `CLAUDE.md`).

---

## 2 · Topology: who dials whom

Both the endpoint and this agent must reach the broker. Amarel login nodes do not generally accept
inbound connections, which rules out the naive arrangement. Three options, best first:

**(a) Broker beside the endpoint on `amarel3`, agent reaches it over an ssh local forward.**
The broker binds loopback on the login node; the workstation runs
`ssh -N -L 8443:127.0.0.1:8443 <netid>@amarel3.hpc.rutgers.edu` and sets
`RADICAL_ORBIT_BROKER_URL=https://127.0.0.1:8443`. Nothing is exposed to the campus network, the
endpoint's connection never leaves the host, and the only moving part is an ssh session the user
already knows how to open. **Start here.**

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
arrangement (a) that is the workstation only, since the broker and endpoint share a home directory.
Auth is **on by default** and the token is never generated by the software; `~/.radical/orbit` is
treated as operator-owned configuration. Do not pass `--no-auth`: the dev stack passes it, and it
disables the ingress gate only — the broker still needs a cert and key either way.

The cert expires in 365 days. Note the date here when it is generated.

---

## 4 · Starting the pair

On `amarel3`, in the venv that has `radical.orbit` installed:

```bash
# broker: loopback only, for arrangement (a)
radical-orbit-broker.py --host 127.0.0.1 --port 8443 &

# endpoint: both plugins, or no batch jobs
radical-orbit-endpoint.py \
    --name amarel3 -p rhapsody,psij \
    --url https://127.0.0.1:8443 &
```

`radical-orbit-endpoint-wrapper.sh` is the better entry point when the endpoint is launched from
somewhere that may scrub the interpreter (PSI/J, IRI, a batch script): it resolves the venv from its
own location and prepends both `purelib` and `platlib` to `PYTHONPATH`.

**Readiness is not an HTTP check.** The broker reads `/topology` as a plugin name and 404s after a
307 (backlog **C7**), so there is no readiness route. Two honest checks:

- the endpoint's own log line `registered as '<name>'`, in `~/.radical/orbit/logs/<name>.log` —
  which is what `LocalOrbitStack._wait_for_endpoint` greps for;
- the client's `rt.topology()`, which is what `OrbitInterface.connect()` polls.

And the ordering trap from `CLAUDE.md`: **the endpoint registers a few seconds after the broker's
port opens.** A round planned before then silently takes the heuristic branch. `dev.sh up` waits for
`hpc: true` for this reason.

**Keeping it alive.** The endpoint must outlive the login session — `tmux`, `nohup`, or a user
systemd unit — and sites do reap long-lived login-node processes. Record here which was used, and
how to tell it died: the absence of a fresh `registered as` line after a restart, or
`--check-config --probe` failing to find the endpoint.

---

## 5 · The settings this produces

An env block for the workstation, to paste or put in a direnv file. These are environment-only
settings by design: each names a path or shell the server will run on the endpoint under the site's
allocation, so none is remotely writable (`tests/test_settings.py::NOT_REMOTELY_WRITABLE`).

```bash
export DESIGNAGENT_ORBIT_ENABLED=true
export RADICAL_ORBIT_BROKER_URL=https://127.0.0.1:8443   # the local end of the ssh -L
export RADICAL_ORBIT_BROKER_CERT=$HOME/.radical/orbit/broker_cert.pem
export RADICAL_ORBIT_BROKER_TOKEN=...                    # or leave it to the token file
export DESIGNAGENT_ORBIT_ENDPOINT=amarel3                # must equal the endpoint's --name
export DESIGNAGENT_ORBIT_PSIJ_EXECUTOR=slurm
export DESIGNAGENT_ORBIT_ACCOUNT=...
export DESIGNAGENT_ORBIT_QUEUE=main
export DESIGNAGENT_ORBIT_JOB_GPUS=0                      # the protocol's specs ask per job
```

`DESIGNAGENT_ORBIT_JOB_GPUS=0` matters: `_job_params` otherwise forces one GPU onto *every* spec,
and hhblits and the MPNN jobs need none. Check the lot with
`.venv/bin/python -m designagent --check-config --probe` before spending a queue slot — it masks
secrets and tries each credential.

---

## 6 · Acceptance ladder

Each rung is a command, and each has a result to record. Do not skip to the bottom: the whole point
of the first two is that they cost nothing.

| # | Command | Proves | Result |
| --- | --- | --- | --- |
| 1 | `.venv/bin/python -m pytest -q -m live` | the client path against a localhost broker we start ourselves | |
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
  Python error;
- **whether `directory` is honoured.** It is forwarded by `to_psij_spec` and set on the PSI/J spec by
  the broker's plugin, and the Slurm template emits `#SBATCH --chdir=`, but no caller in this repo
  sets it and no test exercises it. The protocol's compute jobs depend on it entirely — and Slurm
  fails a job whose `--chdir` does not exist before anything runs, so `$PROJ` must be created first.
