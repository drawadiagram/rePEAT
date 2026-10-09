"""Application settings.

Everything is overridable by environment variable so the same image runs on a
laptop (local process pool, public REST APIs) and against real HPC (Orbit).

**Precedence**, highest first:

1. the runtime override layer (`apply_overrides`, fed by `PUT /api/settings`),
   which lives in memory only and dies with the process;
2. the process environment;
3. `.env`, resolved against the **current working directory** — which is why
   everything is run from the repo root;
4. the field defaults below.

`get_settings()` returns the instance the process is currently using, so the
override layer is invisible to the ~12 call sites that read it. A worker process
does not derive its own: `runtime.py` hands it one through the pool initializer
(`install_settings`), because a task body that called `Settings()` itself would
pick up the environment as of fork and cache it for the worker's lifetime.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fields that must never be rendered in full — not in a response body, not in a
# log line. `mask()` is the only permitted form. Keep this in step with the
# SecretStr annotations below; `secret_values()` reads it to build the log filter.
SECRET_FIELDS = ("anthropic_api_key", "orbit_broker_token", "admin_token", "secrets_key")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DESIGNAGENT_",
        extra="ignore",
        # Load-bearing. Without it an aliased field can only be set by its alias,
        # and `extra="ignore"` swallows the field-name spelling without a word —
        # `Settings(orbit_broker_url=...)` would silently keep the default, and so
        # would every override applied through `apply_overrides`.
        populate_by_name=True,
    )

    # --- LLM ---
    # Read without the DESIGNAGENT_ prefix: it is the conventional name.
    anthropic_api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    model: str = "claude-sonnet-5-5"
    max_tokens: int = 2048

    # --- storage ---
    data_dir: Path = Path("./data")
    # Kuzu's buffer pool, in MiB. 0 keeps Kuzu's default, which is ~80% of the
    # host's RAM: fine on a workstation, most of a small VM. Environment-only,
    # like data_dir.
    kuzu_buffer_pool_mb: int = 0

    # --- compute ---
    pool_workers: int = 4
    # Wall-clock ceiling for a single local task. None disables it.
    task_timeout_sec: float | None = 900.0
    # Rounds of redesign the orchestrator may run before it must summarize.
    max_rounds: int = 3
    # Route node bodies through flowgentic's EXECUTION_BLOCK as well as tasks.
    # Off by default: it runs nodes outside LangGraph's runnable context, which
    # silently disables status and token streaming. See graph/build.py.
    wrap_nodes: bool = False

    # --- Orbit ---
    orbit_enabled: bool = False
    orbit_endpoint: str = ""
    orbit_broker_url: str = Field(default="", alias="RADICAL_ORBIT_BROKER_URL")
    orbit_broker_token: SecretStr = Field(
        default=SecretStr(""), alias="RADICAL_ORBIT_BROKER_TOKEN"
    )
    orbit_broker_cert: str = Field(default="", alias="RADICAL_ORBIT_BROKER_CERT")
    # Bring up a localhost broker + endpoint at startup (tasks/hpc/local_orbit.py).
    # Development only: the broker runs with ingress auth off.
    orbit_local_stack: bool = Field(default=False, alias="DESIGNAGENT_ORBIT_LOCAL")
    # Comma-separated rather than a list field: pydantic-settings parses a list
    # from the environment as JSON, so `=concurrent` would be a validation error
    # and the honest spelling would be `=["concurrent"]`. Empty means the
    # endpoint's own default, which is ['dragon_v3'].
    orbit_rhapsody_backends: str = ""
    # PSI/J executor at the far end: local, slurm, pbspro, lsf, cobalt…
    orbit_psij_executor: str = "local"
    orbit_account: str = ""
    orbit_queue: str = ""
    # Walltime requested for a submitted job, and the client's own identity,
    # which the broker uses to re-attach a session after a reconnect.
    orbit_job_duration_sec: int = 1800
    orbit_client_name: str = "designagent"
    orbit_poll_interval: float = 2.0
    orbit_connect_timeout: float = 30.0
    # Ceiling on the stdout a finished job may return. The broker serves the
    # whole file from a byte offset, so this is our limit rather than the
    # scheduler's; it has to be generous because stdout is the only channel a
    # job has for returning a file (see tasks/hpc/artifacts.py).
    orbit_job_output_max_bytes: int = 4_194_304
    # Ceiling on any single file a job stages back through that channel.
    orbit_artifact_max_bytes: int = 1_048_576
    # GPUs to request per job. 0 omits the key entirely rather than sending a
    # zero: `to_psij_spec` is presence-based, and the endpoint answers HTTP 500
    # for a resource field it does not expect, so a guess is expensive.
    orbit_job_gpus: int = 1

    # --- ProteinMPNN ---
    # A command string, not a path, so one field spells both a local CPU install
    # (".venv-mpnn/bin/python refcodes/ProteinMPNN/protein_mpnn_run.py") and a
    # site's own ("python /sw/ProteinMPNN/protein_mpnn_run.py").
    mpnn_command: str = "protein_mpnn_run.py"
    # Shell run before the command at the far end, e.g. "module load conda &&
    # conda activate proteinmpnn".
    mpnn_prologue: str = ""
    mpnn_sampling_temp: float = 0.1

    # --- the enzyme-redesign protocol's far end ---
    # Where the campaign's files and tools live on the cluster. Every one of
    # these names a path or a shell word the server will run on the endpoint,
    # under the site's allocation, which is the same reason `mpnn_command` and
    # `mpnn_prologue` are refused over the settings API: all of them are
    # reported and none is remotely writable. See `protocol/site.py`, and
    # `plans/AMAREL_ENDPOINT.md` for how they are obtained.
    protocol_proj_root: str = ""
    protocol_scratch_root: str = "/scratch"
    # Conda environments, used by putting their `bin` on PATH rather than by
    # `conda activate`: a login shell reads `.bash_profile`, not `.bashrc`, so
    # `conda` may not be a shell function at the far end at all.
    protocol_conda_aifold: str = ""
    protocol_conda_analysis: str = ""
    protocol_mpnn_path: str = ""
    protocol_mpnn_weights: str = ""
    protocol_uniref_db: str = ""
    # Comma-separated `module load` lines for AlphaFold3, not a list: the same
    # reason `orbit_rhapsody_backends` is a string -- pydantic-settings would
    # demand JSON in the environment variable.
    protocol_af3_modules: str = ""
    protocol_af3_image: str = "alphafold3.sif"
    protocol_gpu_queue: str = "gpu"
    # A Slurm `--constraint` word. It has no PSI/J resource field, so it travels
    # in `custom_attributes`; see `tasks/hpc/orbit.py::to_psij_spec`.
    protocol_gpu_constraint: str = ""
    # Where the skill's own scripts are checked out, so the install stage can
    # carry them to the cluster. They are deliberately not vendored here:
    # copying them in would fork them, and `plans/BACKLOG.md` already records
    # one place where this repository's port and the skill's original disagree.
    protocol_scripts_dir: str = ""

    # --- Globus Compute ---
    # Implemented and unit-tested with an injected executor; never run against a
    # live endpoint. Needs `globus-compute-sdk`, which is not a dependency.
    globus_enabled: bool = False
    globus_endpoint_id: str = ""

    # --- serving ---
    # What the server was started on. `__main__` exports it before uvicorn takes
    # over, because uvicorn does not tell the app. The settings-write routes use
    # it to decide whether a shared secret is required.
    bind_host: str = "127.0.0.1"
    admin_token: SecretStr = SecretStr("")

    # --- logins (plans/LINODE_DEPLOY.md, Phase 2) ---
    # Off by default, so the development server, dev.sh and the offline suite
    # behave exactly as before: one implicit user, the loopback rules above. On,
    # every route but /api/login and a minimal /api/health needs a session
    # cookie, the settings routes need the admin role, and each user's own
    # credentials replace the environment's. Environment-only, like data_dir.
    auth_enabled: bool = False
    # Fernet key for user credentials at rest (`python -m designagent
    # --gen-secrets-key`). Losing it means users re-enter their credentials; it
    # never means plaintext. Without one, credential writes are refused.
    secrets_key: SecretStr = SecretStr("")
    auth_session_hours: float = 168.0
    # Browsers treat http://localhost as secure, so Secure cookies work in
    # development too; turn this off only for a plain-HTTP host that is not
    # loopback, which should not exist.
    auth_cookie_secure: bool = True
    # Comma-separated broker URLs a user may point their own credentials at.
    # Empty means users cannot set a broker at all, only the operator can (the
    # environment). A user-supplied URL is a host this server will dial and
    # hand a token to, so it is allow-listed rather than validated (backlog A9).
    orbit_allowed_brokers: str = ""

    # --- external services ---
    fold_backend: Literal["esmatlas", "local", "hpc"] = "esmatlas"
    http_timeout_sec: float = 60.0
    user_agent: str = "designagent/0.1 (protein redesign agent)"

    # --- secrets ---------------------------------------------------------
    # Accessors rather than `.get_secret_value()` at the call site: one place to
    # change, and `.strip()` happens once.
    @property
    def llm_key(self) -> str:
        return self.anthropic_api_key.get_secret_value().strip()

    @property
    def orbit_token(self) -> str:
        return self.orbit_broker_token.get_secret_value().strip()

    @property
    def admin_secret(self) -> str:
        return self.admin_token.get_secret_value().strip()

    @property
    def llm_available(self) -> bool:
        return bool(self.llm_key)

    @property
    def bound_to_loopback(self) -> bool:
        return self.bind_host in ("127.0.0.1", "::1", "localhost")

    @property
    def secrets_key_value(self) -> str:
        return self.secrets_key.get_secret_value().strip()

    @property
    def allowed_brokers(self) -> tuple[str, ...]:
        return tuple(
            u.strip().rstrip("/") for u in self.orbit_allowed_brokers.split(",") if u.strip()
        )

    @property
    def rhapsody_backends(self) -> list[str] | None:
        """None, not [], so Orbit falls through to the endpoint's own default."""
        names = [p.strip() for p in self.orbit_rhapsody_backends.split(",") if p.strip()]
        return names or None

    @property
    def af3_modules(self) -> tuple[str, ...]:
        """The AlphaFold3 `module load` lines, split out of the one setting."""
        return tuple(
            line.strip() for line in self.protocol_af3_modules.split(",") if line.strip()
        )

    # --- derived paths -------------------------------------------------
    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def blobs_dir(self) -> Path:
        """Raw task outputs (structures, FASTA, logs) referenced by tier 1."""
        return self.data_dir / "blobs"

    @property
    def graph_db_path(self) -> Path:
        return self.data_dir / "lake" / "graph"

    @property
    def scores_db_path(self) -> Path:
        return self.data_dir / "lake" / "scores.sqlite"

    @property
    def golden_dir(self) -> Path:
        return self.data_dir / "lake" / "golden"

    @property
    def auth_db_path(self) -> Path:
        """Users, logins and encrypted credentials; apart from the lake on purpose."""
        return self.data_dir / "auth.sqlite"

    @property
    def checkpoint_db_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def flow_work_dir(self) -> Path:
        """Keep asyncflow session dirs out of the repo root."""
        return self.data_dir / "flow"

    @property
    def orbit_work_dir(self) -> Path:
        """Cert, key and child logs for the development broker."""
        return self.data_dir / "orbit-local"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.artifacts_dir,
            self.blobs_dir,
            self.graph_db_path.parent,
            self.golden_dir,
            self.flow_work_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


# --- the process's current settings ----------------------------------------

_lock = threading.Lock()
# The env/.env-derived instance, kept so `sources()` can tell "came from the
# environment" from "a user typed it into the running app".
_base: Settings | None = None
_current: Settings | None = None
_overrides: dict[str, Any] = {}


def get_settings() -> Settings:
    """The settings this process is using. Built from the environment once."""
    global _base, _current
    if _current is None:
        with _lock:
            if _current is None:
                _base = Settings()
                _current = _base
    return _current


def install_settings(settings: Settings) -> None:
    """Adopt `settings` wholesale, discarding any override layer.

    Also the pool worker initializer (`runtime.py`): a worker is handed its
    settings rather than deriving them, so it does not depend on its own CWD and
    so a key supplied at runtime reaches it when the pool is rebuilt.
    """
    global _base, _current, _overrides
    with _lock:
        _base = settings
        _current = settings
        _overrides = {}


def adopt(settings: Settings) -> None:
    """Use `settings` from now on, leaving the override bookkeeping alone.

    `install_settings` is for a fresh process (a pool worker) and resets the
    override layer; this is for a process that is already running one, so
    `sources()` keeps reporting "override" for the fields a user supplied.
    """
    global _current
    with _lock:
        _current = settings


def apply_overrides(values: Mapping[str, Any]) -> Settings:
    """Layer `values` over the environment-derived settings and install them.

    Validated before installation, so a bad value raises `ValidationError` and
    leaves the process on its previous settings.
    """
    global _current, _overrides
    base = get_settings() if _base is None else _base
    probe = Settings(**dict(values))  # validates and coerces; never installed
    cleaned = {name: getattr(probe, name) for name in values}
    with _lock:
        _overrides = {**_overrides, **cleaned}
        _current = base.model_copy(update=dict(_overrides))
    return _current


def clear_overrides() -> Settings:
    """Drop every override, returning to what the environment says."""
    global _current, _overrides
    base = get_settings() if _base is None else _base
    with _lock:
        _overrides = {}
        _current = base
    return _current


def overrides() -> dict[str, Any]:
    return dict(_overrides)


def sources() -> dict[str, Literal["default", "env", "override"]]:
    """Where each field's current value came from."""
    settings = get_settings()
    base = _base or settings
    defaults = Settings.model_fields
    out: dict[str, Literal["default", "env", "override"]] = {}
    for name in defaults:
        if name in _overrides:
            out[name] = "override"
        elif getattr(base, name) != _field_default(name):
            out[name] = "env"
        else:
            out[name] = "default"
    return out


def _field_default(name: str) -> Any:
    field = Settings.model_fields[name]
    return field.get_default(call_default_factory=True)


def mask(value: str) -> str:
    """The only permitted rendering of a secret.

    Shows enough to tell two keys apart and not enough to use one. Short values
    are not partially revealed: a 6-character token would be mostly given away.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) < 12:
        return "…" * 3
    head = value[:7] if value.startswith(("sk-", "pk-")) else value[:3]
    return f"{head}…{value[-4:]}"


# Secrets that are live in this process but belong to no `Settings` field: a
# signed-in user's decrypted credentials. `auth.credentials` registers them as
# it decrypts and forgets them when they are replaced, so the log filter covers
# every user's key and not only the environment's.
_live_secrets: set[str] = set()


def register_secret(value: str) -> None:
    value = (value or "").strip()
    if len(value) >= 8:
        with _lock:
            _live_secrets.add(value)


def forget_secret(value: str) -> None:
    with _lock:
        _live_secrets.discard((value or "").strip())


def secret_values(settings: Settings | None = None) -> list[str]:
    """Live secret values, for the log filter to scrub. Never for display."""
    settings = settings or get_settings()
    with _lock:
        out = sorted(_live_secrets)
    for name in SECRET_FIELDS:
        raw = getattr(settings, name)
        value = raw.get_secret_value().strip() if isinstance(raw, SecretStr) else str(raw)
        if len(value) >= 8:  # too short to scrub without mangling ordinary text
            out.append(value)
    return out


# Which credential each reportable field belongs to, and whether it is secret.
# Drives both `GET /api/settings` and `--check-config`, so the two cannot drift.
CREDENTIAL_FIELDS: dict[str, tuple[tuple[str, bool], ...]] = {
    "llm": (
        ("anthropic_api_key", True),
        ("model", False),
        ("max_tokens", False),
    ),
    "orbit": (
        ("orbit_enabled", False),
        ("orbit_broker_url", False),
        ("orbit_broker_token", True),
        ("orbit_broker_cert", False),
        ("orbit_endpoint", False),
        ("orbit_local_stack", False),
        ("orbit_rhapsody_backends", False),
        ("orbit_psij_executor", False),
        ("orbit_account", False),
        ("orbit_queue", False),
        ("orbit_job_duration_sec", False),
        ("orbit_job_output_max_bytes", False),
        ("orbit_artifact_max_bytes", False),
        ("orbit_job_gpus", False),
    ),
    "mpnn": (
        ("mpnn_command", False),
        ("mpnn_prologue", False),
        ("mpnn_sampling_temp", False),
    ),
    "protocol": (
        ("protocol_proj_root", False),
        ("protocol_scratch_root", False),
        ("protocol_conda_aifold", False),
        ("protocol_conda_analysis", False),
        ("protocol_mpnn_path", False),
        ("protocol_mpnn_weights", False),
        ("protocol_uniref_db", False),
        ("protocol_af3_modules", False),
        ("protocol_af3_image", False),
        ("protocol_gpu_queue", False),
        ("protocol_gpu_constraint", False),
        ("protocol_scripts_dir", False),
    ),
    "globus": (
        ("globus_enabled", False),
        ("globus_endpoint_id", False),
    ),
    "fold": (("fold_backend", False),),
}


def describe(settings: Settings | None = None) -> dict[str, dict[str, Any]]:
    """Reportable configuration: values for the plain fields, hints for secrets.

    A secret appears only as `{"present": bool, "hint": mask(...)}`. There is no
    code path that puts the value itself in a response.
    """
    settings = settings or get_settings()
    where = sources()
    out: dict[str, dict[str, Any]] = {}
    for group, fields in CREDENTIAL_FIELDS.items():
        entry: dict[str, Any] = {}
        for name, is_secret in fields:
            raw = getattr(settings, name)
            if is_secret:
                value = raw.get_secret_value().strip() if isinstance(raw, SecretStr) else ""
                entry[name] = {
                    "present": bool(value),
                    "hint": mask(value),
                    "source": where[name],
                }
            else:
                entry[name] = {"value": raw, "source": where[name]}
        out[group] = entry
    return out
