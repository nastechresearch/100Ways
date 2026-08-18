"""Bounded remediation policy for 100Ways workflow failures.

Ollama may explain a decision, but it never selects or executes a recovery.
This module is the deterministic authority: only a shallow direct-source history
completion and the existing bounded direct-fetch retry are low-risk actions.
Every branding, integrity, provenance, security, runtime-test, or unknown failure
is skipped fail-closed for human review.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:sk|ollama|tg)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?:token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+", re.IGNORECASE),
)
_MAX_EVIDENCE_CHARS = 1_000


@dataclass(frozen=True)
class RemediationDecision:
    """A deterministic remediation classification with no execution authority."""

    category: str
    disposition: str
    action: str
    hard: bool
    evidence: str
    rationale: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def redact_failure_evidence(value: str, *, limit: int = _MAX_EVIDENCE_CHARS) -> str:
    """Redact credential-shaped values before logs leave a runner or reach Ollama."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted[:limit]


def classify_failure(log: str, *, source_is_shallow: bool = False) -> RemediationDecision:
    """Classify a failure under the strict allowlist-based recovery policy.

    ``auto_recover`` is not permission to publish or bypass a gate.  The caller
    may only perform the exact named low-risk action, then must rerun every
    deterministic gate.  All hard and unknown classes return ``action=none``.
    """
    evidence = redact_failure_evidence(log)
    lowered = evidence.lower()

    if "not an ancestor of upstream" in lowered or "manual reconciliation is required" in lowered:
        if source_is_shallow or "shallow" in lowered:
            return RemediationDecision(
                category="source_history",
                disposition="auto_recover",
                action="complete_shallow_history",
                hard=False,
                evidence=evidence,
                rationale=(
                    "The direct source clone lacks ancestry evidence. Complete only its "
                    "existing origin history, then recheck the unchanged baseline."
                ),
            )
        return _hard(
            "source_history",
            evidence,
            (
                "The recorded baseline is not proven to be an upstream ancestor after full "
                "history; manual reconciliation is required."
            ),
        )

    if (
        "http 429" in lowered
        or "could not resolve host" in lowered
        or "connection timed out" in lowered
    ):
        return RemediationDecision(
            category="transport",
            disposition="auto_recover",
            action="bounded_direct_fetch_retry",
            hard=False,
            evidence=evidence,
            rationale=(
                "The direct public transport is transient. Use the existing bounded retry only; "
                "never substitute cached or stale source data."
            ),
        )

    if any(
        marker in lowered
        for marker in (
            "candidate integrity checks failed",
            "archive-case-collision",
            "case-collision",
            "brand violation",
            "reference audit",
            "source-provenance",
            "source-sha",
        )
    ):
        return _hard(
            "candidate_integrity",
            evidence,
            (
                "Candidate branding, archive integrity, or provenance evidence failed and "
                "must be reviewed without automatic changes."
            ),
        )

    if any(
        marker in lowered
        for marker in ("failed tests/", "assertionerror", "pytest", "typeerror")
    ):
        return _hard(
            "fork_runtime",
            evidence,
            (
                "The branded runtime does not match the verified test contract; preserve "
                "evidence and do not patch automatically."
            ),
        )

    if any(
        marker in lowered
        for marker in ("permission denied", "resource not accessible", "security", "secret")
    ):
        return _hard(
            "security_or_permission",
            evidence,
            "Security or permission evidence cannot be repaired automatically.",
        )

    return RemediationDecision(
        category="unknown",
        disposition="manual_skip",
        action="none",
        hard=True,
        evidence=evidence,
        rationale=(
            "The failure is not on the narrow recovery allowlist; skip automatic action "
            "and require review."
        ),
    )


def _hard(category: str, evidence: str, rationale: str) -> RemediationDecision:
    return RemediationDecision(
        category=category,
        disposition="hard_skip",
        action="none",
        hard=True,
        evidence=evidence,
        rationale=rationale,
    )
