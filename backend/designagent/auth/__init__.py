"""Logins, chat-session ownership and per-user credentials.

Off unless `DESIGNAGENT_AUTH_ENABLED=true`; see `plans/LINODE_DEPLOY.md`, Phase 2.
"""

from .store import ROLES, AuthStore, User

__all__ = ["AuthStore", "User", "ROLES"]
