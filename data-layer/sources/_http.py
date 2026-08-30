"""Shared HTTP plumbing for providers.

Centralises the two things every provider must get right: counting the call
against its daily budget, and turning transport/status failures into
SourceError so the fallback chain can move on instead of crashing.
"""
from __future__ import annotations

from typing import Any

import httpx

import quota
from sources import SourceError

DEFAULT_TIMEOUT = 30.0


def get_json(
    provider: str,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """GET returning parsed JSON, counted against the provider's quota."""
    # Counted before the response is seen: a call that errors still consumed
    # the allowance, and undercounting is what gets an account rate-limited.
    quota.record(provider)

    try:
        response = httpx.get(
            url, params=params, headers=headers, timeout=timeout, follow_redirects=True
        )
    except httpx.HTTPError as exc:
        raise SourceError(f"{provider}: request failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise SourceError(
            f"{provider}: rejected the credentials (HTTP {response.status_code})"
        )
    if response.status_code == 404:
        raise SourceError(f"{provider}: not found (HTTP 404)")
    if response.status_code == 429:
        raise SourceError(f"{provider}: rate limited (HTTP 429)")
    if response.status_code >= 400:
        raise SourceError(
            f"{provider}: HTTP {response.status_code}: {response.text[:200]}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(
            f"{provider}: non-JSON response: {response.text[:200]}"
        ) from exc
