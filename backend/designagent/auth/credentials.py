"""A user's own credentials: validated, encrypted at rest, applied per turn.

The split this module encodes (plans/LINODE_DEPLOY.md, Phase 2b):

* **Operator settings** stay process-wide and environment-only: paths, shell,
  pool size, the protocol's cluster layout. Nothing here can change them.
* **User credentials** are the few values that say *whose* key and *whose*
  allocation: the Anthropic key and model, and an Orbit broker, token, cert,
  endpoint, account and queue. Each user's replace the operator's for that
  user's turns only.

A plain user starts from the operator's settings with every credential
*removed*, so the environment's key and HPC allocation are never lent to an
account that did not bring its own. An admin starts from the operator's
settings unchanged: in this deployment the admin is the operator, and the
environment's credentials are theirs.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any

from pydantic import SecretStr

from ..config import Settings, forget_secret, mask, register_secret
from .store import AuthStore, User

# name -> is_secret. The whole surface a user can write; anything else is the
# operator's. Order is the order the panel shows them in.
USER_FIELDS: dict[str, bool] = {
    "anthropic_api_key": True,
    "model": False,
    "orbit_broker_url": False,
    "orbit_broker_token": True,
    "orbit_broker_cert": False,
    "orbit_endpoint": False,
    "orbit_account": False,
    "orbit_queue": False,
}

# What a plain user's baseline loses: every value that would let them spend the
# operator's key or allocation.
_STRIPPED: dict[str, Any] = {
    "anthropic_api_key": SecretStr(""),
    "orbit_enabled": False,
    "orbit_local_stack": False,
    "orbit_broker_url": "",
    "orbit_broker_token": SecretStr(""),
    "orbit_broker_cert": "",
    "orbit_endpoint": "",
    "orbit_account": "",
    "orbit_queue": "",
    "globus_enabled": False,
    "globus_endpoint_id": "",
}

_WORD = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")
MAX_TOKEN = 512
MAX_CERT = 16 * 1024


class CredentialError(ValueError):
    """A user-supplied credential that is refused, with a sentence saying why."""


class NoSecretsKey(RuntimeError):
    """The server has no DESIGNAGENT_SECRETS_KEY, so it will not store secrets."""


def validate(values: dict[str, str], settings: Settings) -> dict[str, str]:
    """Check each supplied field. Reject, never repair: these reach a shell's
    environment and a socket, so a guess about what was meant is worse than no.

    `""` means "clear this field" and is always accepted.
    """
    out: dict[str, str] = {}
    for name, raw in values.items():
        if name not in USER_FIELDS:
            raise CredentialError(f"{name} is not a user credential")
        value = (raw or "").strip()
        if not value:
            out[name] = ""
            continue
        if name == "orbit_broker_url":
            allowed = settings.allowed_brokers
            if value.rstrip("/") not in allowed:
                raise CredentialError(
                    "that broker is not on this server's allow-list"
                    if allowed
                    else "this server does not let users choose a broker "
                    "(DESIGNAGENT_ORBIT_ALLOWED_BROKERS is empty)"
                )
            value = value.rstrip("/")
        elif name in ("orbit_endpoint", "orbit_account", "orbit_queue"):
            if not _WORD.match(value):
                raise CredentialError(f"{name} must be 1-64 letters, digits, '.', '_' or '-'")
        elif name == "model":
            if not _MODEL.match(value):
                raise CredentialError("model must be a model id, e.g. claude-sonnet-5-5")
        elif name == "orbit_broker_cert":
            if len(value) > MAX_CERT or "-----BEGIN CERTIFICATE-----" not in value:
                raise CredentialError("the broker certificate must be a PEM certificate")
            if "PRIVATE KEY" in value:
                raise CredentialError(
                    "that is a private key: the broker's key never leaves the broker"
                )
        elif name in ("anthropic_api_key", "orbit_broker_token"):
            if len(value) > MAX_TOKEN or any(c.isspace() for c in value):
                raise CredentialError(f"{name} does not look like a key")
        out[name] = value
    return out


class CredentialBox:
    """Fernet, with each value bound to its user and field.

    Fernet has no associated data, so the binding goes in the plaintext and is
    checked on the way out: a ciphertext copied to another row or another user
    fails to decrypt rather than silently becoming someone else's key.
    """

    def __init__(self, key: str):
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key.encode())

    @staticmethod
    def generate_key() -> str:
        from cryptography.fernet import Fernet

        return Fernet.generate_key().decode()

    def seal(self, user_id: str, name: str, value: str) -> bytes:
        return self._fernet.encrypt(f"{user_id}\0{name}\0{value}".encode())

    def open(self, user_id: str, name: str, token: bytes) -> str:
        from cryptography.fernet import InvalidToken

        try:
            plain = self._fernet.decrypt(token).decode()
        except InvalidToken as exc:
            raise CredentialError(
                "a stored credential could not be decrypted (wrong DESIGNAGENT_SECRETS_KEY?)"
            ) from exc
        owner, field_name, value = plain.split("\0", 2)
        if owner != user_id or field_name != name:
            raise CredentialError("a stored credential is bound to a different user or field")
        return value


@dataclass
class Resolved:
    """One user's effective settings for a turn, and how `hpc` should route."""

    settings: Settings
    # True when the user brought their own broker and endpoint, so their turns
    # need an interface of their own (runtime.OrbitRegistry).
    own_orbit: bool
    # True for an admin with no Orbit credentials of their own: their turns use
    # the process-wide interface, built from the environment.
    shared_orbit: bool
    errors: list[str] = field(default_factory=list)


class CredentialService:
    """Decrypts on demand, caches per user, and forgets on change."""

    def __init__(self, store: AuthStore, settings: Settings):
        self.store = store
        self.cert_dir = settings.data_dir / "user-certs"
        self._key = settings.secrets_key_value
        self._box = CredentialBox(self._key) if self._key else None
        self._cache: dict[str, dict[str, str]] = {}
        self._lock = threading.Lock()

    @property
    def can_store(self) -> bool:
        return self._box is not None

    def load(self, user: User) -> tuple[dict[str, str], list[str]]:
        """Decrypted values for `user`, and any field that failed to decrypt."""
        with self._lock:
            cached = self._cache.get(user.id)
        if cached is not None:
            return dict(cached), []
        rows = self.store.credential_rows(user.id)
        values: dict[str, str] = {}
        errors: list[str] = []
        if rows and self._box is None:
            errors.append("credentials are stored but this server has no secrets key")
            return {}, errors
        for name, token in rows.items():
            if name not in USER_FIELDS:
                continue
            try:
                values[name] = self._box.open(user.id, name, token)  # type: ignore[union-attr]
            except CredentialError as exc:
                errors.append(f"{name}: {exc}")
        for name, is_secret in USER_FIELDS.items():
            if is_secret and values.get(name):
                register_secret(values[name])
        with self._lock:
            self._cache[user.id] = dict(values)
        return values, errors

    def save(self, user: User, values: dict[str, str]) -> None:
        if self._box is None:
            raise NoSecretsKey(
                "this server has no DESIGNAGENT_SECRETS_KEY; it will not store credentials"
            )
        sealed: dict[str, bytes | None] = {
            name: (self._box.seal(user.id, name, value) if value else None)
            for name, value in values.items()
        }
        self.store.put_credentials(user.id, sealed)
        self.forget(user)

    def clear(self, user: User) -> None:
        self.store.clear_credentials(user.id)
        self.forget(user)

    def forget(self, user: User) -> None:
        with self._lock:
            old = self._cache.pop(user.id, None) or {}
        for name, is_secret in USER_FIELDS.items():
            if is_secret and old.get(name):
                forget_secret(old[name])

    def describe(self, user: User) -> dict[str, dict[str, Any]]:
        """What the panel may see: values for plain fields, hints for secrets."""
        values, _ = self.load(user)
        out: dict[str, dict[str, Any]] = {}
        for name, is_secret in USER_FIELDS.items():
            value = values.get(name, "")
            if is_secret:
                out[name] = {"present": bool(value), "hint": mask(value), "source": "user"}
            elif name == "orbit_broker_cert":
                # A certificate is public, but a page of PEM is not a useful value
                # to show; say whether there is one.
                out[name] = {"value": "(certificate set)" if value else "", "source": "user"}
            else:
                out[name] = {"value": value, "source": "user"}
        return out

    def resolve(self, user: User, operator: Settings) -> Resolved:
        values, errors = self.load(user)
        base = operator if user.is_admin else operator.model_copy(update=_STRIPPED)
        update: dict[str, Any] = {}
        if values.get("anthropic_api_key"):
            update["anthropic_api_key"] = SecretStr(values["anthropic_api_key"])
        if values.get("model"):
            update["model"] = values["model"]
        own_orbit = bool(values.get("orbit_broker_url") and values.get("orbit_endpoint"))
        if own_orbit:
            update.update(
                orbit_enabled=True,
                orbit_local_stack=False,
                orbit_broker_url=values["orbit_broker_url"],
                orbit_broker_token=SecretStr(values.get("orbit_broker_token", "")),
                orbit_broker_cert=self._cert_path(user, values.get("orbit_broker_cert", "")),
                orbit_endpoint=values["orbit_endpoint"],
            )
        for name in ("orbit_account", "orbit_queue"):
            if values.get(name):
                update[name] = values[name]
        settings = base.model_copy(update=update) if update else base
        shared = user.is_admin and not own_orbit
        return Resolved(settings=settings, own_orbit=own_orbit, shared_orbit=shared, errors=errors)

    def _cert_path(self, user: User, pem: str) -> str:
        """Orbit pins a cert *file*, so the PEM is written where only we can read it."""
        if not pem:
            return ""
        path = self.cert_dir / user.id / "broker_cert.pem"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        if not path.exists() or path.read_text() != pem:
            path.write_text(pem)
            path.chmod(0o600)
        return str(path.resolve())


def fingerprint(settings: Settings) -> tuple[str, str, str, str]:
    """What identifies one user's Orbit connection, for the registry's key.

    The token is hashed rather than kept, so the key can be logged.
    """
    import hashlib

    token = hashlib.sha256(settings.orbit_token.encode()).hexdigest()[:16]
    return (settings.orbit_broker_url, settings.orbit_endpoint, settings.orbit_broker_cert, token)
