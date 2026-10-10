"""Credential shapes the 100Ways engine detects, redacts, and refuses to ship.

Two sets, on purpose:

* :data:`SECRET_PATTERNS` is the conservative gating set.  Each pattern matches a
  provider's documented token format, so a hit is evidence rather than a guess,
  and a false positive never silently blocks publication.
* :data:`EXTENDED_SECRET_PATTERNS` widens the net with more provider shapes
  (OpenAI, Slack, Google, PEM private keys).  It is used by the read-only
  ``100ways audit`` command to *report* additional exposure without changing any
  gate.

Keeping both here means one place to review when a provider changes its token
format, and no opportunity for the gate and the audit to drift apart.
"""

from __future__ import annotations

import re

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),          # GitHub PAT/OAuth/app/user/server
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),        # GitHub fine-grained PAT
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),     # Telegram bot token
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                # AWS access key id
)

EXTENDED_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = SECRET_PATTERNS + (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),                                   # OpenAI-style
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),                            # Slack
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),                                  # Google API key
    re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"),  # PEM key
)


def find_secrets(
    text: str, patterns: tuple[re.Pattern[str], ...] = EXTENDED_SECRET_PATTERNS
) -> list[str]:
    """Return every credential-shaped substring in ``text`` in match order."""
    found: list[str] = []
    for pattern in patterns:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return found


def contains_secret(
    text: str, patterns: tuple[re.Pattern[str], ...] = EXTENDED_SECRET_PATTERNS
) -> bool:
    """True when ``text`` contains any credential-shaped substring."""
    return any(pattern.search(text) for pattern in patterns)


def redact(
    text: str,
    patterns: tuple[re.Pattern[str], ...] = EXTENDED_SECRET_PATTERNS,
    mask: str = "[REDACTED]",
) -> str:
    """Replace every credential-shaped substring in ``text`` with ``mask``."""
    redacted = text
    for pattern in patterns:
        redacted = pattern.sub(mask, redacted)
    return redacted
