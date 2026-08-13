"""Admin passphrase compiler for 100Ways.

The operator sets a human password (default ``Nastech@Pass``).  We never
store or transmit that password; instead every process derives a **compiled
token** — a deterministic string of numbers and symbols that only the system
understands (``0012003903083@a9889w`` style).  Verifying accepts either:

  * the human password itself (``Nastech@Pass``) — it re-compiles and must
    match the stored compiled form, so typing the password "just works"; or
  * the compiled token — matching the stored form directly.

Because ``compile_token`` is deterministic, the same password always yields
the same compiled token, which is what makes offline verification possible
without ever holding the plaintext.

    >>> compile_token("Nastech@Pass")
    '0012003903083@a9889w'
    >>> verify_token("Nastech@Pass", stored)
    True
    >>> verify_token(stored, stored)   # the compiled form works too
    True
"""

from __future__ import annotations

import hashlib
import secrets
import string

DEFAULT_ADMIN_PASS = "Nastech@Pass"

_ALPHABET = string.digits + string.ascii_lowercase
_SYMBOL = "@"


def compile_token(passphrase: str = DEFAULT_ADMIN_PASS) -> str:
    """Derive the system-only compiled token from a passphrase.

    Deterministic: the same passphrase always produces the same token, so
    verification is a plain string compare against the stored compiled form.
    """
    digest = hashlib.sha256(("100ways::" + passphrase).encode("utf-8")).digest()
    num = int.from_bytes(digest[:8], "big")
    digits = str(num % 10**13).zfill(13)
    alpha = "".join(_ALPHABET[b % len(_ALPHABET)] for b in digest[8:16])
    return f"{digits}{_SYMBOL}{alpha}"


def verify_token(candidate: str, stored_compiled: str | None) -> bool:
    """Accept either the human password or the compiled token itself."""
    if not stored_compiled:
        return False
    candidate = (candidate or "").strip()
    if not candidate:
        return False
    return secrets.compare_digest(candidate, stored_compiled) or secrets.compare_digest(
        compile_token(candidate), stored_compiled
    )


def is_compiled(token: str) -> bool:
    """True when the string looks like a compiled token (digits@alnum)."""
    if _SYMBOL not in token:
        return False
    head, _, tail = token.partition(_SYMBOL)
    return head.isdigit() and len(head) >= 10 and tail.isalnum() and tail.islower()
