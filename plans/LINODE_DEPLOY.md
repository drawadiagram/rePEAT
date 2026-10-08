# Linode deployment: broker and agent on this VM, UI by IP, then per-user logins

## Context

`plans/AMAREL_ENDPOINT.md` describes the layout: the Orbit endpoint runs on `amarel3`, and the broker
and the rePEAT agent run on one Linode. **This machine is that Linode** (97.107.137.219, Ubuntu 24.04).
Its reverse name `97-107-137-219.ip.linodeusercontent.com` resolves forward to the same address.

The endpoint passed its selftest on Amarel (rung 0-pre) but has never talked to a real broker. Right
now nothing is installed here: no `.venv`, no `refcodes/`, no Orbit, no node, no `orbit` user, and
UFW is inactive.

The request changes one thing about the documented design. Section 2.1 of that document keeps the
backend on loopback and reaches it over `ssh -L`. Here the UI must instead be reachable by IP, and
the end state is **user-specific logins that unlock user-specific secrets** (LLM key, Orbit
broker/endpoint/token, Slurm account).

Facts from the survey that shape the plan:

- **The host is undersized.** It has 1 vCPU and 961 MB of RAM, while the document puts the floor at
  4 GB. The user chose to **resize first**. A Linode resize keeps the IPv4, so the cert SAN and the
  endpoint's `--url` stay valid.
- **`refcodes/` will be copied over by the user** with scp (backlog B1).
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

1. **Resize** to Linode 4 GB or 8 GB in Cloud Manager, then confirm with `free -m` and `nproc`.
2. **Cloud Firewall:** inbound default Drop.
   - TCP 22 from the admin's addresses.
   - TCP 8443 from `128.6.0.0/16`.
   - **TCP 80 and 443 from anywhere.** These are new; 80 is needed for the ACME challenge and to
     redirect.
   - Mirror the same rules in UFW, allowing ssh first.
3. **User and packages.**
   - Create an `orbit` user, as in §3.3.
   - Install `git`, `build-essential`, `uv` (as the `orbit` user), Node 20 LTS (for the frontend
     build) and Caddy (from its apt repository).
4. **Code.**
   - `git clone` into `/home/orbit/rePEAT`.
   - **The user scp's `refcodes/`.** Record each package's `git rev-parse HEAD` in
     `refcodes/VERSIONS.md` and commit that note to `plans/BACKLOG.md` B1.
   - Run `./scripts/setup.sh`.
   - Install `radical.orbit`: clone it at `c7ede0c` with `--no-deps -e`, plus the named deps from
     §3.3. Then confirm that `uv pip show rhapsody-py` still points at `refcodes/`, and run
     `./scripts/setup.sh --check`.
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

- **Frontend:** `cd frontend && npm ci && npm run build`. Caddy serves `frontend/dist`; the backend
  serves no static files today.
- **`/etc/caddy/Caddyfile`:**
  - Site `97-107-137-219.ip.linodeusercontent.com`, which gets an automatic Let's Encrypt cert.
    `http://97.107.137.219` redirects there.
  - `basic_auth` with a bcrypt hash made by `caddy hash-password`.
  - `handle /api/*` → `reverse_proxy 127.0.0.1:8000 { flush_interval -1 }` (SSE must not buffer).
  - Everything else: `root * /home/orbit/rePEAT/frontend/dist`, `try_files {path} /index.html`,
    `file_server`.
  - Security headers: HSTS, `X-Content-Type-Options`, `frame-ancestors 'none'`. Add **no CORS
    headers**: the rule in `_authorize_write` still holds.
- **Backend unit `/etc/systemd/system/repeat-backend.service`:**
  - `User=orbit`, `WorkingDirectory=/home/orbit/rePEAT`, because `config.yml` and `.env` are read
    from the cwd.
  - `EnvironmentFile=/etc/repeat/backend.env` (0600, owned by orbit) holding the §5 values,
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

**Check:** `curl -I https://97-107-137-219.ip.linodeusercontent.com` returns 401 without
credentials. In a browser with credentials, one chat turn streams. `PUT /api/settings` without
`X-Designagent-Admin` returns 403.

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
  - `app.py` resolves `effective = operator_settings.model_copy(update=user_creds)` per turn and
    passes it as `config["configurable"]["settings"]`.
  - `Deps` gains `settings_for(config)`, used where nodes read `deps.settings` today (coordinator,
    interpreter, initializer, orchestrator, protocol).
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
