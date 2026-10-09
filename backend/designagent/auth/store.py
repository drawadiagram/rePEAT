"""Users, login sessions, chat-session ownership and encrypted credentials.

One SQLite file, `data/auth.sqlite`, kept apart from the lake so that secrets
never share a file with provenance, and so the lake can be copied for analysis
(`slides/run_model.py`) without carrying anyone's credentials along.

A login token is returned once and stored only as its SHA-256: a copy of this
file does not let anyone sign in. Credentials are stored only as Fernet
ciphertext (`auth/credentials.py`).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .passwords import DUMMY_HASH, hash_password, verify_password

ROLES = ("admin", "user")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        pw_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
        created REAL NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS login_sessions (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created REAL NOT NULL,
        expires REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_credentials (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        field TEXT NOT NULL,
        value BLOB NOT NULL,
        updated REAL NOT NULL,
        PRIMARY KEY (user_id, field)
    )""",
)


@dataclass(frozen=True)
class User:
    id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_username(name: str) -> bool:
    return 1 <= len(name) <= 64 and all(c.isalnum() or c in "._-" for c in name)


class AuthStore:
    def __init__(self, path: Path, *, session_hours: float = 168.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_seconds = session_hours * 3600
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute("PRAGMA foreign_keys = ON")
        with self._lock, self._db:
            for stmt in _SCHEMA:
                self._db.execute(stmt)
        # Owner-only: password hashes and ciphertext, but no reason to share.
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- users ----------------------------------------------------------
    def create_user(self, username: str, password: str, role: str = "user") -> User:
        if not valid_username(username):
            raise ValueError("a username is 1-64 letters, digits, '.', '_' or '-'")
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        user = User(id=f"u-{secrets.token_hex(8)}", username=username, role=role)
        pw_hash = hash_password(password)
        try:
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO users (id, username, pw_hash, role, created) VALUES (?,?,?,?,?)",
                    (user.id, username, pw_hash, role, time.time()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"user {username!r} already exists") from exc
        return user

    def set_password(self, username: str, password: str) -> None:
        pw_hash = hash_password(password)
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE users SET pw_hash = ? WHERE username = ?", (pw_hash, username)
            )
            if cur.rowcount == 0:
                raise ValueError(f"no user {username!r}")
            # A new password ends every existing login.
            self._db.execute(
                "DELETE FROM login_sessions WHERE user_id = "
                "(SELECT id FROM users WHERE username = ?)",
                (username,),
            )

    def users(self) -> list[User]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, username, role FROM users WHERE disabled = 0 ORDER BY username"
            ).fetchall()
        return [User(*row) for row in rows]

    def user_count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, username, role FROM users WHERE id = ? AND disabled = 0", (user_id,)
            ).fetchone()
        return User(*row) if row else None

    # --- logins ---------------------------------------------------------
    def authenticate(self, username: str, password: str) -> User | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, username, role, pw_hash FROM users "
                "WHERE username = ? AND disabled = 0",
                (username,),
            ).fetchone()
        if row is None:
            verify_password(password, DUMMY_HASH)  # same cost as a real miss
            return None
        if not verify_password(password, row[3]):
            return None
        return User(row[0], row[1], row[2])

    def start_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._db:
            self._db.execute("DELETE FROM login_sessions WHERE expires < ?", (now,))
            self._db.execute(
                "INSERT INTO login_sessions (token_hash, user_id, created, expires) "
                "VALUES (?,?,?,?)",
                (_token_hash(token), user.id, now, now + self.session_seconds),
            )
        return token

    def user_for_token(self, token: str) -> User | None:
        if not token:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT u.id, u.username, u.role FROM login_sessions s "
                "JOIN users u ON u.id = s.user_id "
                "WHERE s.token_hash = ? AND s.expires > ? AND u.disabled = 0",
                (_token_hash(token), time.time()),
            ).fetchone()
        return User(*row) if row else None

    def end_session(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM login_sessions WHERE token_hash = ?", (_token_hash(token),)
            )

    # --- chat sessions --------------------------------------------------
    def create_chat_session(self, user: User) -> str:
        session_id = f"s-{secrets.token_urlsafe(12)}"
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO chat_sessions (session_id, owner_id, created) VALUES (?,?,?)",
                (session_id, user.id, time.time()),
            )
        return session_id

    def session_owner(self, session_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT owner_id FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row[0] if row else None

    def sessions_of(self, user: User) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT session_id, created FROM chat_sessions WHERE owner_id = ? "
                "ORDER BY created DESC",
                (user.id,),
            ).fetchall()
        return [{"session_id": sid, "created": created} for sid, created in rows]

    def assign_session(self, session_id: str, user: User) -> None:
        """Give an existing, unowned session id to a user (the CLI's adoption path)."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO chat_sessions (session_id, owner_id, created) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO NOTHING",
                (session_id, user.id, time.time()),
            )

    # --- credentials (ciphertext only; see auth/credentials.py) ----------
    def credential_rows(self, user_id: str) -> dict[str, bytes]:
        with self._lock:
            rows = self._db.execute(
                "SELECT field, value FROM user_credentials WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {field: value for field, value in rows}

    def put_credentials(self, user_id: str, values: dict[str, bytes | None]) -> None:
        now = time.time()
        with self._lock, self._db:
            for field_name, value in values.items():
                if value is None:
                    self._db.execute(
                        "DELETE FROM user_credentials WHERE user_id = ? AND field = ?",
                        (user_id, field_name),
                    )
                else:
                    self._db.execute(
                        "INSERT INTO user_credentials (user_id, field, value, updated) "
                        "VALUES (?,?,?,?) ON CONFLICT(user_id, field) "
                        "DO UPDATE SET value = excluded.value, updated = excluded.updated",
                        (user_id, field_name, value, now),
                    )

    def clear_credentials(self, user_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM user_credentials WHERE user_id = ?", (user_id,))


__all__ = ["AuthStore", "User", "ROLES", "valid_username"]
