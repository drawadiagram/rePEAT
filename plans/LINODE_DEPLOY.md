# Linode deployment: broker and agent on this VM, UI by IP, then per-user logins

## Context

`plans/AMAREL_ENDPOINT.md` describes the layout: the Orbit endpoint runs on `amarel3`, and the broker
and the rePEAT agent run on one Linode. **This machine is that Linode** (97.107.137.219, Ubuntu 24.04).
Its reverse name `97-107-137-219.ip.linodeusercontent.com` resolves forward to the same address.

The endpoint passed its selftest on Amarel (rung 0-pre) but has never talked to a real broker.
`refcodes/` has arrived (2026-10-09). Nothing else is installed here yet: no `.venv`, no node, no
Caddy, no `orbit` user, and UFW is inactive.

The request changes one thing about the documented design. Section 2.1 of that document keeps the
backend on loopback and reaches it over `ssh -L`. Here the UI must instead be reachable by IP, and
the end state is **user-specific logins that unlock user-specific secrets** (LLM key, Orbit
broker/endpoint/token, Slurm account).

Facts from the survey that shape the plan:

- **The host is a Linode 4 GB, and that is final** (resized 2026-10-08 from a 1 GB Nanode): 2 vCPU
  (AMD EPYC 7642), 3.9 GB RAM, 79 GB disk, 496 MB swap partition. That is AMAREL_ENDPOINT.md's stated
  *floor*, not its recommended 8 GB, so memory is the budget this plan is written against (see
  "Resource budget" below). The resize kept 97.107.137.219, so the cert SAN and the endpoint's
  `--url` are unaffected. The host also has a global IPv6 (`2600:3c03::…`); Caddy listens on it, the
  broker does not need to.
- **`refcodes/` is in `/root/rePEAT/refcodes`**, copied over by the user. Every checkout is a clean
  git tree, so for the first time the revisions are on record (backlog B1):

  | Checkout | Commit | Date | Branch | Role here |
  | --- | --- | --- | --- | --- |
  | `radical.asyncflow` | `038d52a` | 2026-08-20 | main (v0.5.1) | `setup.sh` installs it |
  | `rhapsody` | `71536ac` | 2026-09-08 | main | `setup.sh` installs it |
  | `flowgentic` | `dd27bd8` | 2026-08-19 | **`demo/radical`** | `setup.sh` installs it, `--no-deps` |
  | `radical.orbit` | **`c7ede0c`** | 2026-09-29 | devel | the broker, and the agent's Orbit client |
  | `ProteinMPNN` | `8907e66` | 2023-06-27 | detached | `setup_mpnn.sh`'s pin; **not used on this VM** |
  | `ChemGraph` | `d7a34ca` | 2026-10-01 | main | only with `.[chem]`; not needed to deploy |
  | `langgraph` | `b36b1d5` | 2026-10-01 | main (1.2.12) | reference reading only |
  | `hpc-bridge`, `rcsb-molstar` | `46f63bf`, `7153df7` | — | main / master | reference reading only |

  - **`radical.orbit` is already at `c7ede0c`**, the revision AMAREL_ENDPOINT.md and the Amarel
    endpoint were checked against. So the broker and the endpoint run the same Orbit, and the
    separate `~/radical.orbit` clone in §3.3 is unnecessary. `local_orbit._script` already looks in
    `refcodes/radical.orbit/bin`.
  - **flowgentic is on a non-default branch**, `demo/radical`. Record that, because "main" would
    rebuild something different.
  - The files are owned by uid 1000, which has no passwd entry. Ownership is fixed during the copy
    in Phase 0 step 4, not by adding a global `safe.directory`.
- **There is no identity concept anywhere.** The only gate is one shared `admin_token`, checked by
  `_authorize_write` (`app.py:470`) on just the three settings-write routes.
  - Chat, sessions, artifacts, tasks and cancel are all open.
  - Session ids are minted by the client in `localStorage` (`App.tsx:15-21`).
  - `/api/tasks` with no `session_id` returns every task.
- **A reverse proxy on this host turns the dev default into an open door.** `bound_to_loopback` reads
  the *bind address*, not the caller (`config.py:188`). With uvicorn on 127.0.0.1 behind a proxy,
  every internet request looks local, so with no admin token settings writes are open to the world.
- **Settings are one process-global object** (`config._current`, `config.py:254-299`).
  - Pool workers receive settings once, through the initializer (`runtime.py:185-194`), and cache
    them.
  - `chemgraph_agent.py:87` writes `os.environ["ANTHROPIC_API_KEY"]`.
  - A key change rebuilds the whole runtime (`needs_rebuild`, `runtime.py:64`).
  - There is one `OrbitInterface` per runtime (`runtime.py:222-245`, `manager.py:95-105`).
- **The Orbit broker is not a tenant boundary.** Orbit 0.8.0 has one ingress token per broker, and
  every endpoint needs it. Anyone holding that token can submit to *any* endpoint registered on the
  broker. That means arbitrary commands under another user's netid and allocation (upstream
  `security_token_mitigation.md` treats psij submit as command execution). So per-user HPC
  isolation means **one broker per user**, which the app cannot enforce.

The work is split into phases so the UI is reachable early without ever being open.

---

## Phase 0 — Host and broker (follows AMAREL_ENDPOINT.md §3; no code changes)

1. **Resize — done.** Linode 4 GB, confirmed with `nproc` (2) and `free -m` (3915 MB). Record it in
   AMAREL_ENDPOINT.md's Linode table, whose "Linode 8 GB" row is now wrong. Then fit the host to it:
   - Add a 2 GB swapfile next to the 496 MB partition. It absorbs the frontend build and a burst
     of turns rather than inviting the OOM killer, and costs nothing with 72 GB free.
   - **`DESIGNAGENT_POOL_WORKERS=2`**, not the default 4: one per vCPU. Measure each worker's RSS
     after first start and record it.
   - **Do not run `setup_mpnn.sh` here**, and keep `fold_backend` at `esmatlas` or `hpc`. Real
     ProteinMPNN and folding run on Amarel; torch has no room on this host.
2. **Cloud Firewall:** inbound default Drop.
   - TCP 22 from the admin's addresses.
   - TCP 8443 from `128.6.0.0/16`.
   - **TCP 80 and 443 from anywhere.** These are new; 80 is needed for the ACME challenge and to
     redirect.
   - Apply each rule to IPv4 *and* IPv6. Caddy binds both.
   - Mirror the same rules in UFW, allowing ssh first.
3. **User and packages.**
   - Create an `orbit` user, as in §3.3.
   - Install `git`, `build-essential`, `uv` (as the `orbit` user), Node 20 LTS (for the frontend
     build) and Caddy (from its apt repository).
4. **Code.**
   - `git clone` into `/home/orbit/rePEAT` as `orbit`, then check out this branch.
   - **Copy the four checkouts the deployment needs, owned by `orbit`:**
     `rsync -a --chown=orbit:orbit /root/rePEAT/refcodes/{radical.asyncflow,rhapsody,flowgentic,radical.orbit} /home/orbit/rePEAT/refcodes/`.
     Leave out `langgraph` (533 MB) and `ProteinMPNN` (216 MB), which are only read. Leave out
     `ChemGraph` unless `.[chem]` is wanted. Then confirm each `git rev-parse HEAD` matches the
     table above.
   - **Record the revisions in the repo** (B1's "middle option"):
     - Add the table above to `plans/BACKLOG.md` B1, since `refcodes/` itself is gitignored.
     - Add a small `REFCODES` pin list to `scripts/setup.sh`. `--check` then warns when a checkout's
       HEAD differs, the same pattern `setup_mpnn.sh` uses for `MPNN_REV`.
     - Fix the stale counts in `setup.sh`'s closing message (165 and 11 tests, against 398 and 12).
   - Run `./scripts/setup.sh`.
   - **Install Orbit from `refcodes/`.** Use `uv pip install --python .venv --no-deps -e
     refcodes/radical.orbit`, then its `requirements.txt` by name **minus `rhapsody-py`**: `httpx
     msgpack cloudpickle requests websockets websocket-client fastapi uvicorn psutil rich
     psij-python globus-sdk authlib`.
     - `rhapsody-py` from PyPI would displace the editable `refcodes/rhapsody`. Confirm afterwards
       that `uv pip show rhapsody-py` points at `refcodes/`.
     - Then run `./scripts/setup.sh --check` and `.venv/bin/python -c "import radical.orbit"` (rung
       0a).
     - Folding this install into `setup.sh` behind a flag closes B1's "fourth package" note. It is
       optional, but cheap now that the checkout is in place.
5. **Broker credentials** (§3.4): generate a self-signed cert with
   `SAN=IP:97.107.137.219,IP:127.0.0.1`, plus a token. Both go in `~orbit/.radical/orbit/` at 0600.
6. **Broker unit:** create `/etc/systemd/system/orbit-broker.service` exactly as in §3.5
   (`--host 0.0.0.0 --port 8443 -p sysinfo`, never `--no-auth`).
7. **Skill checkout** for `DESIGNAGENT_PROTOCOL_SCRIPTS_DIR` (§5).
8. **Amarel side, done by the user:**
   - scp the cert and token to `amarel3`.
   - `export ORBIT_BROKER_URL=https://97.107.137.219:8443`.
   - Run `./scripts/amarel_endpoint.sh check && … start`.

**Acceptance ladder rungs to run and record:** 0a, 0b, 0c, 1 (`pytest -m live`), 2 (`-m remote`
with `ORBIT_LOCAL`) and 3 (`--check-config --probe`). Fill in the survey's Linode table as well.

## Phase 1 — Expose the UI by IP, single operator, gated at the proxy

This phase gets the app on the IP safely before any app code changes. It is an interim gate; Phase 2
replaces it.

- **Frontend:** `cd frontend && npm ci && npm run build`, then copy the build to `/srv/repeat/www`.
  The backend serves no static files today, so Caddy does. **Caddy cannot read `/home/orbit`**:
  Ubuntu 24.04 creates home directories `0750` (`HOME_MODE` in `/etc/login.defs`) and Caddy runs as
  its own user, so serving `frontend/dist` in place returns 403 on every file. Copying keeps the home
  directory private.
- **Kuzu's buffer pool — one small code change.** `lake/graph.py:62` opens `kuzu.Database(path)` with
  no `buffer_pool_size`. Kuzu's default is a fraction of physical RAM (about 80% in the versions
  checked; confirm against the installed one), which is most of this host. Add an environment-only
  setting, e.g. `DESIGNAGENT_KUZU_BUFFER_POOL_MB=256`, pass it through, and report it in
  `--check-config`.
- **`/etc/caddy/Caddyfile`:**
  - Site `97-107-137-219.ip.linodeusercontent.com`, which gets an automatic Let's Encrypt cert.
    `http://97.107.137.219` redirects there.
  - `basic_auth` with a bcrypt hash made by `caddy hash-password`.
  - `handle /api/*` → `reverse_proxy 127.0.0.1:8000 { flush_interval -1 }` (SSE must not buffer).
  - Everything else: `root * /srv/repeat/www`, `try_files {path} /index.html`,
    `file_server`.
  - Security headers: HSTS, `X-Content-Type-Options`, `frame-ancestors 'none'`. Add **no CORS
    headers**: the rule in `_authorize_write` still holds.
- **Backend unit `/etc/systemd/system/repeat-backend.service`:**
  - `User=orbit`, `WorkingDirectory=/home/orbit/rePEAT`, because `config.yml` and `.env` are read
    from the cwd.
  - `MemoryMax=2.5G`, so a runaway turn is killed by the unit's own limit before the broker or sshd
    feel it.
  - `EnvironmentFile=/etc/repeat/backend.env` (0600, owned by orbit) holding the §5 values,
    `DESIGNAGENT_POOL_WORKERS=2`,
    `ANTHROPIC_API_KEY`, and **`DESIGNAGENT_ADMIN_TOKEN` (mandatory, because of the proxy-loopback
    hole above)**.
  - `ExecStart=.venv/bin/python -m designagent --host 127.0.0.1 --port 8000`.
  - `After=orbit-broker.service`, `Restart=on-failure`.
  - `KillMode=mixed` and `TimeoutStopSec=15`, so systemd escalates to SIGKILL (backlog C8: SIGTERM
    does not stop it).
  - Restart is single-instance, which respects the Kuzu lock.
- **Ordering:** start the Amarel endpoint before the backend, then check that `/api/health` reports
  `hpc: true`. A round planned earlier silently takes the heuristic branch.
- **Docs:**
  - Amend AMAREL_ENDPOINT.md §2 and §2.1 with the new topology row (browser → Caddy :443 → backend
    loopback) and the proxy-loopback finding.
  - Add a backlog entry, "A17 · behind a same-host proxy `bound_to_loopback` is true for every
    caller", repro included.

**The deploy checkout is not the development checkout.** Development happens as root in
`/root/rePEAT`; the service runs from `/home/orbit/rePEAT`. A deploy is `git pull` in the second,
from GitHub after a push. Pulling straight from `/root/rePEAT` would need the orbit user to read
root's home. Then rebuild and copy the frontend, and `systemctl restart repeat-backend`.

**Check:** `curl -I https://97-107-137-219.ip.linodeusercontent.com` returns 401 without
credentials. In a browser with credentials, one chat turn streams. `PUT /api/settings` without
`X-Designagent-Admin` returns 403.

## Resource budget (Linode 4 GB: 2 vCPU, 3.9 GB RAM)

Estimates to replace with measured RSS at the end of Phase 1. These numbers are planning figures,
not observations.

| Process | Expected | Lever |
| --- | --- | --- |
| backend main (uvicorn, LangGraph, langchain, Kuzu) | 0.5–0.8 GB | Kuzu buffer pool setting |
| pool workers ×2 | 0.2–0.4 GB each | `DESIGNAGENT_POOL_WORKERS` |
| broker | < 0.1 GB per broker | Phase 3 adds one per user |
| Caddy | < 0.05 GB | — |
| `npm run build` (transient) | ~1 GB | the swapfile; or stop the backend while building |
| `pytest -m live` (transient, Phase 0) | a second broker, endpoint and pool | run before the service is up |

That leaves about 1.5 GB of headroom with one user, enough for a small lab under Phase 2. **Treat
an 8 GB resize as the answer if measured RSS says otherwise; do not trim workers below 2.**
Phase 2's per-user `OrbitInterface`s add one listener thread and connection each, which is small.
The idle close in 2c is what keeps them bounded.

## Phase 2 — Per-user logins and per-user secrets (code)

### 2a. Identity and access control
- **New module `backend/designagent/auth/`** backed by `data/auth.sqlite`, which is separate from
  the lake so secrets never share a file with provenance.
  - Tables: `users(id, username, pw_hash, role ∈ {admin,user}, created)`,
    `login_sessions(token_hash, user_id, expires)` and `chat_sessions(session_id, owner_id, created)`.
  - Hash passwords with stdlib `hashlib.scrypt`, per-user salt, compared with `compare_digest`.
  - There is no self-registration. Accounts are created with
    `python -m designagent --add-user NAME [--admin]`, which prompts for the password.
- **Routes:**
  - `POST /api/login` sets an opaque cookie: `HttpOnly; Secure; SameSite=Strict; Path=/`. Only the
    token's hash is stored.
  - `POST /api/logout` and `GET /api/me`.
  - Login is rate-limited per IP and username, in memory.
- **A `current_user` FastAPI dependency on every route except `/api/login` and a minimal
  `/api/health`:**
  - Mutating routes also check that `Origin` matches the host. This is defence in depth on top of
    SameSite, and the app still has no CORS policy.
- **Ownership:**
  - **The server mints session ids.** `POST /api/sessions` creates one, and the `localStorage` id
    becomes a hint that the server validates.
  - The chat, session, artifact, task and cancel routes check that the session belongs to the
    caller.
  - `/api/tasks` with no `session_id` is limited to the caller's own sessions, or all of them for an
    admin.
  - Ownership is checked at the route boundary. The lake, checkpointer and Kuzu stay shared, because
    the Kuzu lock rules out per-user data dirs.
- **Replace the admin token:**
  - `_authorize_write` becomes a role check.
  - `DESIGNAGENT_ADMIN_TOKEN` and the loopback-open default are retired, which closes A9's
    shared-host case and A17.
  - Keep `test_a_non_loopback_bind_requires_a_token`'s intent as "no route mutates without a
    user".

### 2b. Splitting settings into operator and user layers
- **Operator settings** stay process-global and environment-only, as today: data_dir, pool_workers,
  `mpnn_*`, `protocol_*` paths, executor, timeouts and the fold backend. Only an admin may change the
  writable subset.
- **`UserCredentials`** is a new pydantic model in `config.py`. Its fields:
  - `anthropic_api_key` and `model`;
  - `orbit_broker_url`, `orbit_broker_cert` (PEM text), `orbit_broker_token` and `orbit_endpoint`;
  - `orbit_account` and `orbit_queue`;
  - and the protocol netid.

  Secrets are `SecretStr`.
- **Storage:** the `user_secrets` table encrypts each value with Fernet. The new `cryptography`
  dependency goes in `pyproject.toml` (check first whether it is already transitive). The key comes
  from `DESIGNAGENT_SECRETS_KEY` in `backend.env`. Losing that key means users re-enter their
  secrets; it never means plaintext. This revises B9's "memory only" stance for user secrets only,
  so record the reason in B9.
- **`PUT /api/me/credentials`** validates the URL and endpoint fields, and the broker URL must be in
  the operator's allow-list `DESIGNAGENT_ORBIT_ALLOWED_BROKERS`. That keeps A9's SSRF and redirect
  concern closed per user.
  - `GET` returns `describe`-style `{present, hint}` and never a value.
  - `POST /api/me/credentials/test` reuses `preflight.probe_all`.

### 2c. Threading a user's credentials through a turn without globals
- **Graph nodes:**
  - `app.py` puts **only `user_id`** in `config["configurable"]`, never a credential.
    - **Confirmed in the refcodes checkout** (langgraph 1.2.12, checkpoint 4.2.0):
      `get_checkpoint_metadata` (`libs/checkpoint/langgraph/checkpoint/base/__init__.py:758`) copies
      every `str`/`int`/`float`/`bool` value in `configurable` into checkpoint metadata. The only
      exceptions are `__`-prefixed keys and `EXCLUDED_METADATA_KEYS`.
    - So an API key passed as a string would be written to the checkpoint store on every turn.
      `user_id` will be copied too, which is harmless and useful for ownership.
    - Re-check against the version `.venv` actually resolves (`langgraph>=1.2,<2`).
    - The same rule as `test_structures_are_not_carried_in_state` applies: nothing that must not
      persist rides through the graph's own plumbing. Phase 2e asserts it.
  - `Deps` gains `settings_for(config)`. It looks up `user_id` in an in-process credentials cache,
    decrypted on login and dropped on logout or credential change, and returns
    `operator_settings.model_copy(update=user_creds)`.
  - Use it wherever nodes read `deps.settings` today: coordinator, interpreter, initializer,
    orchestrator and protocol.
  - `llm.complete`/`build_llm` already take a `settings` argument (`llm.py:38-70`), so the change is
    to pass the effective settings through. The `deps.llm_caveat` path is unchanged.
- **Pool workers:**
  - Task bodies that need the key (`molviz_agent`, `chemgraph_agent`) receive an explicit
    `credentials` parameter in place of calling `get_settings()` for it.
  - Delete the `os.environ` write in `chemgraph_agent.py:87`, which would leak one user's key into
    the next task on that worker.
  - `TaskManager` must strip `credentials` from params before snapshot, Kuzu Task rows and `_slim`.
  - The API key leaves `POOL_VISIBLE_FIELDS` and `REBUILD_FIELDS`, so changing it no longer rebuilds
    the runtime.
- **Orbit:**
  - `TaskManager.interfaces["hpc"]` becomes an `OrbitRegistry` keyed by
    `(user_id, broker_url, endpoint)`. Each entry is an `OrbitInterface` built by the existing
    `_make_orbit`, created lazily on first `hpc` submit, closed after it has been idle and has no
    live tasks, and dropped when those credentials change.
  - `interface_for("hpc", user=…)` and `_job_params` take the account and queue from the user's
    credentials.
  - `hpc_available` in `/api/health` becomes per user, through `/api/me`.
  - Delete `Runtime.reconfigure`'s in-place Orbit restart for user fields; keep it for operator
    fields.
- **Log scrubbing:** `config.secret_values()` also returns the secrets currently decrypted in the
  registry and the per-turn cache, so the `app.lifespan` filter covers every user's values.

### 2d. Frontend
- A `LoginPage` gate in `App.tsx` driven by `GET /api/me`; a 401 anywhere returns the user to it.
  `lib/api.ts` sends `credentials: "same-origin"`, and the admin header goes away.
- **`SettingsPanel.tsx` splits in two:**
  - "My credentials", for every user;
  - "Server", for admins only, read-only except the writable operator subset.

  Update the hardcoded `FIELDS`/`GROUPS` and keep
  `test_every_reported_credential_group_reaches_the_settings_panel` passing.
- The session id comes from `POST /api/sessions`. Add a small "my sessions" list (optional, after
  the core work).

### 2e. Tests (offline, in the existing style)
- Auth:
  - login, logout and expiry;
  - every route returns 401 without a cookie;
  - user B gets 404 on user A's session, artifact, task and cancel;
  - a mutating route without a matching `Origin` returns 403.
- Secrets:
  - **no user secret appears in serialized graph state, Kuzu rows, task snapshots or logs**, modelled
    on `test_structures_are_not_carried_in_state`;
  - the ciphertext round-trips, and a wrong master key fails closed;
  - two users' turns at the same time use their own keys, checked with a fake LLM recording the key.
- Orbit:
  - the registry isolates users;
  - a broker URL outside the allow-list returns 422.
- Rework the `NOT_REMOTELY_WRITABLE` tests for the two layers.
- vitest: the login gate, and the credentials panel never rendering a value.

Afterwards update CLAUDE.md (architecture rules, routes, test counts) and the README, run
`slides/check_anchors.py`, and run ruff.

### Phase 2 rollout
Create the accounts with `--add-user`, set `DESIGNAGENT_SECRETS_KEY`, deploy, and have each user
enter their credentials. **Then remove `basic_auth` from the Caddyfile.** Keep the operator's own
`ANTHROPIC_API_KEY` out of `backend.env` unless the plan is to fall back to it, and decide that
explicitly; the default here is no fallback.

## Phase 3 — HPC tenancy (decide before inviting a second user)

Every endpoint holds the broker token, and that token can submit to every endpoint. **On a shared
broker, any user can run commands as any other user.** Two options:

- **One broker per user (recommended once there are mutually untrusted users).** Use a templated
  unit, `orbit-broker@<user>.service`, with its own port, token and cert dir. Each port is allowed
  from `128.6.0.0/16`, and each user's credentials point at their own broker. The 2b allow-list
  enforces that.
- **A shared broker**, acceptable only for a single user or a lab that trusts one another. Record it
  as a backlog entry.

## Verification (end to end)
1. Phase 0:
   - `systemctl status orbit-broker`.
   - `GET /endpoints` lists `amarel3` with `connected: true` and psij.
   - Rungs 1–3 pass. Record the results in AMAREL_ENDPOINT.md §6.
2. Phase 1:
   - From outside, without credentials, the site returns 401.
   - With credentials, a chat turn streams and the task chip for an `hpc` task reads `hpc`.
   - `PUT /api/settings` without the admin header returns 403.
   - `systemctl restart repeat-backend` completes inside the timeout.
3. Phase 2:
   - `.venv/bin/python -m pytest -q`, `ruff check backend tests`, `cd frontend && npm test &&
     npm run test:e2e`, then `slides/check_anchors.py`.
   - On the live host, with two accounts: each sees only their own sessions. A's key is used for A's
     turn (check `/api/health`'s llm caveat with B's key unset). `grep` the lake and logs for both
     keys and find nothing.
   - One rung 4 run (`pytest -m remote`) with the user's own credentials.
