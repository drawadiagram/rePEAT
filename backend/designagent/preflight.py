"""Credential checks, shared by `POST /api/settings/test` and `--check-config`.

A probe answers one question: *would this credential work?* It is deliberately
separate from startup, which never refuses to come up — the agent's whole
degradation story depends on a missing key being an ordinary state. So this
module reports, and nothing here changes what the process is using.

Each probe returns `{"state": ..., "detail": ...}` where state is one of:

  ok        the credential was used successfully
  absent    nothing is configured, which is a valid way to run
  rejected  something is configured and the far end refused it
  error     configured, but we could not get an answer (unreachable, timeout)
  skipped   not applicable here (a dev stack that only exists while serving)
"""

from __future__ import annotations

import logging
from typing import Any

from .config import CREDENTIAL_FIELDS, Settings, describe, get_settings

log = logging.getLogger(__name__)


async def probe_llm(settings: Settings) -> dict[str, str]:
    """One cheap completion. Distinguishes "no key" from "key rejected"."""
    if not settings.llm_available:
        return {"state": "absent", "detail": "no ANTHROPIC_API_KEY configured"}
    from .llm import complete

    reasons: list[str] = []
    text = await complete(
        "Reply with the single word: ok",
        "ping",
        settings=settings,
        on_fallback=reasons.append,
        max_tokens=4,
    )
    if text:
        return {"state": "ok", "detail": f"{settings.model} answered"}
    detail = reasons[0] if reasons else "no answer and no error"
    state = "rejected" if "rejected" in detail else "error"
    return {"state": state, "detail": detail}


async def probe_orbit(settings: Settings) -> dict[str, str]:
    """Connect, resolve an endpoint, disconnect. Nothing is submitted."""
    if settings.orbit_local_stack:
        return {
            "state": "skipped",
            "detail": "the development stack is started with the server",
        }
    if not settings.orbit_enabled:
        return {"state": "absent", "detail": "DESIGNAGENT_ORBIT_ENABLED is false"}
    if not settings.orbit_broker_url:
        return {"state": "error", "detail": "no RADICAL_ORBIT_BROKER_URL configured"}
    from .runtime import _make_orbit

    interface, error = await _make_orbit(settings)
    if interface is None:
        lowered = error.lower()
        rejected = "auth" in lowered or "token" in lowered or "certificate" in lowered
        return {"state": "rejected" if rejected else "error", "detail": error}
    detail = f"endpoint {getattr(interface, 'endpoint_name', '') or 'resolved'}"
    try:
        await interface.close()
    except Exception as exc:
        log.warning("closing the probe interface failed: %s", exc)
    return {"state": "ok", "detail": detail}


async def probe_globus(settings: Settings) -> dict[str, str]:
    if not settings.globus_enabled:
        return {"state": "absent", "detail": "DESIGNAGENT_GLOBUS_ENABLED is false"}
    from .runtime import _make_globus

    interface, error = await _make_globus(settings)
    if interface is None:
        return {"state": "error", "detail": error}
    try:
        await interface.close()
    except Exception as exc:
        log.warning("closing the probe interface failed: %s", exc)
    return {"state": "ok", "detail": f"endpoint {settings.globus_endpoint_id}"}


async def probe_all(settings: Settings | None = None) -> dict[str, dict[str, str]]:
    settings = settings or get_settings()
    return {
        "llm": await probe_llm(settings),
        "orbit": await probe_orbit(settings),
        "globus": await probe_globus(settings),
    }


# --- the text report -------------------------------------------------------


def report_lines(
    settings: Settings | None = None,
    probes: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """The `--check-config` table. Secrets appear only as `mask()` hints."""
    settings = settings or get_settings()
    shown = describe(settings)
    lines: list[str] = []
    for group, fields in CREDENTIAL_FIELDS.items():
        probe = (probes or {}).get(group)
        header = f"  {group}"
        if probe:
            header += f"  [{probe['state']}] {probe['detail']}"
        lines.append(header)
        for name, is_secret in fields:
            entry = shown[group][name]
            if is_secret:
                value = entry["hint"] or "-"
                if not entry["present"]:
                    value = "(not set)"
            else:
                value = "(empty)" if entry["value"] == "" else str(entry["value"])
            lines.append(f"      {name:<28} {value:<34} {entry['source']}")
        lines.append("")
    pool = f"{settings.kuzu_buffer_pool_mb} MiB" if settings.kuzu_buffer_pool_mb else "kuzu default"
    lines.append(
        f"  data_dir {settings.data_dir}   bind {settings.bind_host}   kuzu buffer pool {pool}"
    )
    if settings.auth_enabled:
        key = "set" if settings.secrets_key_value else "MISSING: credentials cannot be stored"
        brokers = ", ".join(settings.allowed_brokers) or "none (users cannot choose one)"
        lines.append(f"  logins: on   secrets key: {key}   user brokers: {brokers}")
    else:
        lines.append("  logins: off")
    lines.append(
        "  hpc: "
        + (
            "configured"
            if (settings.orbit_enabled or settings.orbit_local_stack)
            else "not configured — tasks marked hpc run their local equivalents"
        )
    )
    return lines


def report(
    settings: Settings | None = None, probes: dict[str, Any] | None = None
) -> str:
    return "\n".join(report_lines(settings, probes))
