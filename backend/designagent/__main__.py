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
    args = parser.parse_args()

    if args.check_config:
        raise SystemExit(_check_config(probe=args.probe))

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
