"""Analyze GitHub Actions failures without weakening publication gates.

The analyzer is intentionally separate from synchronization and publication logic. It
classifies known infrastructure failures, redacts credentials and sensitive identifiers,
and returns an actionable report. A report never authorizes a retry, merge, release, or
deployment; callers must still fail closed when the originating job failed.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?:bot|token|secret|password|api[_-]?key)\s*[:=]\s*[^\s,;]+", re.I),
    re.compile(r"https://[^\s/@]+:[^\s/@]+@", re.I),
)


@dataclass(frozen=True)
class FailureFinding:
    category: str
    severity: str
    title: str
    evidence: str
    recommendation: str
    retryable: bool


@dataclass(frozen=True)
class FailureReport:
    status: str
    findings: tuple[FailureFinding, ...]
    safe_to_retry: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "safe_to_retry": self.safe_to_retry,
            "findings": [asdict(finding) for finding in self.findings],
        }


def redact(text: str) -> str:
    """Remove credentials and credential-like values before public reporting."""
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def analyze_decision(decision: dict[str, object]) -> FailureReport:
    """Explain a structured weekly-gate failure without converting it to approval."""
    findings: list[FailureFinding] = []
    fields = (
        "brand_issues",
        "lock_issues",
        "asset_issues",
        "security_issues",
        "ci_issues",
        "skill_issues",
    )
    for field in fields:
        issues = decision.get(field, [])
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity", "block"))
            if field in {"ci_issues", "skill_issues"} and severity != "block":
                continue
            code = str(issue.get("code", "unknown"))
            path = str(issue.get("path", "unknown"))
            detail = redact(str(issue.get("detail", "")))
            findings.append(
                FailureFinding(
                    category=f"weekly_gate_{field}",
                    severity="error" if severity == "block" else severity,
                    title=f"Weekly gate blocked by {code}",
                    evidence=f"{path}: {detail}"[:300],
                    recommendation=(
                        "Resolve the reported gate issue and rerun; "
                        "publication remains disabled."
                    ),
                    retryable=False,
                )
            )
    if decision.get("freshness_ok") is False:
        findings.append(
            FailureFinding(
                category="weekly_gate_freshness",
                severity="error",
                title="Weekly gate freshness lock failed",
                evidence="The direct upstream ref changed or could not be confirmed stable.",
                recommendation=(
                    "Refetch the direct upstream source and rerun; "
                    "do not use cached evidence."
                ),
                retryable=False,
            )
        )
    if not findings:
        findings.append(
            FailureFinding(
                category="weekly_gate_unknown",
                severity="error",
                title="Weekly gate failed without a structured blocking issue",
                evidence="The decision payload contained no recognized blocking issue.",
                recommendation=(
                    "Inspect the original gate report manually; "
                    "publication remains disabled."
                ),
                retryable=False,
            )
        )
    return FailureReport(status="gate_failure", findings=tuple(findings), safe_to_retry=False)


def analyze_failure(log: str, *, step: str = "unknown") -> FailureReport:
    """Classify an Actions log; failure reports remain fail-closed by default."""
    clean = redact(log)
    lowered = clean.lower()
    findings: list[FailureFinding] = []

    if "http 429" in lowered or "error: rpc failed" in lowered and "429" in lowered:
        findings.append(
            FailureFinding(
                category="upstream_rate_limit",
                severity="warning",
                title="Upstream Git transport was rate-limited",
                evidence=_evidence(clean, "429"),
                recommendation=(
                    "Use a shallow, blob-filtered clone with bounded exponential backoff; "
                    "do not substitute cached or stale upstream data."
                ),
                retryable=True,
            )
        )
    elif "could not resolve host" in lowered or "connection timed out" in lowered:
        findings.append(
            FailureFinding(
                category="network_transport",
                severity="warning",
                title="Public source transport was unavailable",
                evidence=_first_line(clean, ("could not resolve host", "timed out")),
                recommendation=(
                    "Retry the direct public fetch with bounded backoff; fail closed "
                    "if it remains unavailable."
                ),
                retryable=True,
            )
        )
    elif "permission denied" in lowered or "resource not accessible" in lowered:
        findings.append(
            FailureFinding(
                category="permissions",
                severity="critical",
                title="GitHub permissions prevented the requested operation",
                evidence=_first_line(clean, ("permission denied", "resource not accessible")),
                recommendation=(
                    "Correct repository or environment permissions; never bypass "
                    "the protected gate."
                ),
                retryable=False,
            )
        )
    elif "expected flush after ref listing" in lowered or "fatal:" in lowered:
        findings.append(
            FailureFinding(
                category="git_fetch",
                severity="error",
                title="Git source fetch failed",
                evidence=_first_line(clean, ("fatal:", "expected flush after ref listing")),
                recommendation=(
                    "Retry a bounded direct fetch and preserve fail-closed behavior "
                    "if it fails again."
                ),
                retryable=True,
            )
        )
    else:
        findings.append(
            FailureFinding(
                category="unknown",
                severity="error",
                title="Actions failure requires manual review",
                evidence=_first_line(clean, ()),
                recommendation=(
                    "Inspect the original job log; no automated publication action "
                    "is authorized."
                ),
                retryable=False,
            )
        )

    return FailureReport(status="failure", findings=tuple(findings), safe_to_retry=False)


def _first_line(text: str, needles: tuple[str, ...]) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if needles:
        for line in lines:
            if any(needle in line.lower() for needle in needles):
                return line[:300]
    return (lines[-1] if lines else "No diagnostic output was captured")[:300]


def _evidence(text: str, needle: str) -> str:
    lines = [line.strip() for line in text.splitlines() if needle.lower() in line.lower()]
    return (lines[0] if lines else _first_line(text, ()))[:300]
