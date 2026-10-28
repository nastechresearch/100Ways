"""Safe, deterministic helpers for Telegram-only 100Ways operations."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .commit_stream import TELEGRAM_SAFETY_FOOTER

TELEGRAM_MAX_CHARS = 4096
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{12,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def redact_telegram_text(text: str) -> str:
    """Redact credential-shaped text before any Telegram delivery attempt."""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def bound_telegram_text(text: str, *, limit: int = TELEGRAM_MAX_CHARS) -> str:
    """Return a redacted message bounded to Telegram's documented size limit."""
    if limit < len(TELEGRAM_SAFETY_FOOTER) + 32:
        raise ValueError("Telegram limit is too small to preserve the safety footer")
    safe = redact_telegram_text(text).strip()
    if TELEGRAM_SAFETY_FOOTER not in safe:
        safe = f"{safe}\n{TELEGRAM_SAFETY_FOOTER}".strip()
    if len(safe) <= limit:
        return safe
    prefix_limit = limit - len(TELEGRAM_SAFETY_FOOTER) - len("\n… [truncated]\n")
    return f"{safe[:prefix_limit].rstrip()}\n… [truncated]\n{TELEGRAM_SAFETY_FOOTER}"


def notification_fingerprint(payload: dict[str, Any]) -> str:
    """Hash delivery-significant, non-secret status fields for deduplication."""
    keys = (
        "status",
        "pending_commits",
        "threshold",
        "baseline_sha",
        "upstream_sha",
        "release_gate",
        "release_tag",
    )
    canonical = "\n".join(f"{key}={payload.get(key, '')}" for key in keys)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def should_send_notification(*, previous_fingerprint: str, payload: dict[str, Any]) -> bool:
    """Send only when a meaningful operational state has changed."""
    return notification_fingerprint(payload) != previous_fingerprint


def format_release_readiness_status(readiness: dict[str, Any]) -> str:
    """Format a Telegram-safe manual release readiness notice; never an instruction to release."""
    tag = str(readiness.get("upstream_tag", "unknown"))
    gate = str(readiness.get("gate", "BLOCKED"))
    if gate == "READY":
        body = (
            f"NasTech release readiness: {tag}\n"
            "A verified branded merge is eligible for human release review.\n"
            "Next action: a human may inspect the receipt and manually start the guarded promotion flow."
        )
    else:
        count = len(readiness.get("issues", [])) if isinstance(readiness.get("issues"), list) else 0
        body = (
            f"NasTech release readiness: {tag}\n"
            f"Blocked: {count} release-readiness issue(s) require review.\n"
            "Next action: resolve evidence only; no tag or release is authorized."
        )
    return bound_telegram_text(body)
