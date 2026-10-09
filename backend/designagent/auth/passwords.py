"""Password hashing, on the standard library's scrypt.

No new dependency: `hashlib.scrypt` is memory-hard and in every CPython this
project supports. The stored form names its parameters, so they can be raised
later without invalidating existing hashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# ~16 MiB and a few tens of milliseconds per check: enough to make an offline
# guess expensive, small enough for a 2-vCPU VM to log someone in promptly.
N, R, P = 2**14, 8, 1
MIN_LENGTH = 10


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"a password needs at least {MIN_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# Spent on an unknown username, so a miss costs the same as a wrong password and
# the response time does not say which usernames exist.
DUMMY_HASH = hash_password("not-a-real-password-" + secrets.token_hex(8))
