"""Entry point.

The `__main__` guard matters: the rhapsody backend uses a ProcessPoolExecutor,
whose workers re-import this module.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="designagent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="print what is configured, with secrets masked, and exit",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="with --check-config, also try each credential against its service",
    )
    accounts = parser.add_argument_group(
        "accounts", "for DESIGNAGENT_AUTH_ENABLED=true; run with the server's environment"
    )
    accounts.add_argument("--add-user", metavar="NAME", help="create an account")
    accounts.add_argument("--admin", action="store_true", help="with --add-user: an admin")
    accounts.add_argument("--passwd", metavar="NAME", help="set an account's password")
    accounts.add_argument("--list-users", action="store_true")
    accounts.add_argument(
        "--gen-secrets-key",
        action="store_true",
        help="print a new DESIGNAGENT_SECRETS_KEY (credentials at rest)",
    )
    args = parser.parse_args()

    if args.check_config:
        raise SystemExit(_check_config(probe=args.probe))
    if args.gen_secrets_key:
        from .auth.credentials import CredentialBox

        print(CredentialBox.generate_key())
        return
    if args.add_user or args.passwd or args.list_users:
        raise SystemExit(_accounts(args))

    # uvicorn does not tell the app what it bound to, and the settings-write
    # routes need to know whether that was loopback.
    os.environ.setdefault("DESIGNAGENT_BIND_HOST", args.host)

    import uvicorn

    uvicorn.run(
        "designagent.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


def _read_password(prompt: str) -> str:
    """From the terminal when there is one, else one line of stdin (for scripts)."""
    import getpass
    import sys

    if sys.stdin.isatty():
        first = getpass.getpass(prompt)
        if getpass.getpass("again: ") != first:
            raise SystemExit("the two passwords differ")
        return first
    return sys.stdin.readline().rstrip("\n")


def _accounts(args) -> int:
    """Account management. Writes `data/auth.sqlite` directly; the server need not run.

    SQLite takes its own locks, so this is safe beside a running server, and a
    new account can sign in at once.
    """
    from .auth.store import AuthStore
    from .config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    store = AuthStore(settings.auth_db_path, session_hours=settings.auth_session_hours)
    try:
        if args.list_users:
            for user in store.users():
                print(f"{user.username:<24} {user.role:<6} {user.id}")
            return 0
        if args.add_user:
            user = store.create_user(
                args.add_user,
                _read_password(f"password for {args.add_user}: "),
                "admin" if args.admin else "user",
            )
            print(f"created {user.username} ({user.role})")
            return 0
        store.set_password(args.passwd, _read_password(f"new password for {args.passwd}: "))
        print(f"password changed for {args.passwd}; their sessions were ended")
        return 0
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    finally:
        store.close()


def _check_config(*, probe: bool) -> int:
    """Report the effective configuration. Exit status is 0 unless a probe failed."""
    import asyncio

    from .config import get_settings
    from .preflight import probe_all, report

    settings = get_settings()
    probes = asyncio.run(probe_all(settings)) if probe else None
    print(report(settings, probes))
    if not probes:
        return 0
    bad = [name for name, result in probes.items() if result["state"] in ("rejected", "error")]
    if bad:
        print(f"\n  {len(bad)} credential(s) configured but not usable: {', '.join(bad)}")
        return 1
    return 0


if __name__ == "__main__":
    main()
