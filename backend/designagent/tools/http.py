"""Shared HTTP helper.

Clients are created per call: these bodies may run inside a process-pool worker,
where a cached client from the parent would be useless or unpicklable.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)


def client(**kwargs: Any) -> httpx.AsyncClient:
    settings = get_settings()
    kwargs.setdefault("timeout", settings.http_timeout_sec)
    kwargs.setdefault("follow_redirects", True)
    headers = {"User-Agent": settings.user_agent, **kwargs.pop("headers", {})}
    return httpx.AsyncClient(headers=headers, **kwargs)


async def get_json(url: str, **kwargs: Any) -> Any | None:
    """GET JSON, returning None on any 4xx/5xx or transport error."""
    async with client() as http:
        try:
            resp = await http.get(url, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("GET %s failed: %s", url, exc)
            return None
    if resp.status_code >= 400:
        log.info("GET %s -> %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("GET %s returned non-JSON", url)
        return None


async def get_text(url: str, **kwargs: Any) -> str | None:
    async with client() as http:
        try:
            resp = await http.get(url, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("GET %s failed: %s", url, exc)
            return None
    return resp.text if resp.status_code < 400 else None


async def post_json(url: str, payload: Any, **kwargs: Any) -> Any | None:
    async with client() as http:
        try:
            resp = await http.post(url, json=payload, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("POST %s failed: %s", url, exc)
            return None
    if resp.status_code >= 400:
        log.info("POST %s -> %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def post_text(url: str, content: str, **kwargs: Any) -> str | None:
    async with client() as http:
        try:
            resp = await http.post(url, content=content, **kwargs)
        except httpx.HTTPError as exc:
            log.warning("POST %s failed: %s", url, exc)
            return None
    return resp.text if resp.status_code < 400 else None
