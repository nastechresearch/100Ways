"""Authentication helpers for operator-supplied dashboard secrets."""

from __future__ import annotations

import hashlib
import secrets


def verify_token(candidate: str | None, configured_token: str | None) -> bool:
    """Return whether *candidate* matches the configured bearer token.

    Missing configuration always denies access. Both values are hashed before
    comparison so ``compare_digest`` receives fixed-length inputs.
    """
    candidate = (candidate or "").strip()
    configured_token = (configured_token or "").strip()
    if not candidate or not configured_token:
        return False
    return secrets.compare_digest(
        hashlib.sha256(candidate.encode("utf-8")).digest(),
        hashlib.sha256(configured_token.encode("utf-8")).digest(),
    )


def is_configured(token: str | None) -> bool:
    """Return whether a non-empty operator secret is configured."""
    return bool((token or "").strip())


__all__ = ["is_configured", "verify_token"]
