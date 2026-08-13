"""100Ways error/exit codes.

The engine speaks in codes so every surface (CLI exit status, dashboard,
GitHub Actions, notifications) reports the same language:

    0    PASS   - file identical / parity gate passed
    404  MISSING - an upstream file (or its branded twin) is missing on the
                   Nastech side: "error 404" - follow the article and port it.
    82   VIOLATION - a brand rule was violated inside an article/file.
    83   DRIFT   - file present on both sides but differs after branding.
    84   EXTRA   - a Nastech-only file with no upstream twin (must be explained).
    1    GATE    - the parity threshold was not met (hard failure).

A report or scan aggregates the worst code it saw: 404 beats 82 beats 83
beats 84, and any of them beats 0.
"""

from __future__ import annotations

from dataclasses import dataclass

PASS = 0
GATE_FAIL = 1
VIOLATION = 82
DRIFT = 83
EXTRA = 84
MISSING = 404

_CODES: dict[int, str] = {
    PASS: "PASS",
    GATE_FAIL: "GATE",
    VIOLATION: "VIOLATION",
    DRIFT: "DRIFT",
    EXTRA: "EXTRA",
    MISSING: "MISSING",
}

# severity order: higher wins when aggregating
_SEVERITY = {
    PASS: 0,
    EXTRA: 1,
    DRIFT: 2,
    VIOLATION: 3,
    MISSING: 4,
    GATE_FAIL: 5,
}


@dataclass(frozen=True)
class Code:
    value: int
    name: str
    meaning: str


CODE_DETAILS: dict[int, Code] = {
    PASS: Code(PASS, "PASS", "file identical; parity gate passed"),
    GATE_FAIL: Code(GATE_FAIL, "GATE", "parity threshold not met - port blocked"),
    VIOLATION: Code(VIOLATION, "VIOLATION", "brand rule violated inside an article"),
    DRIFT: Code(DRIFT, "DRIFT", "present on both sides but differs after branding"),
    EXTRA: Code(EXTRA, "EXTRA", "Nastech-only file with no upstream twin"),
    MISSING: Code(MISSING, "MISSING", "upstream file missing on the Nastech side"),
}


def code_for(value: int) -> Code:
    return CODE_DETAILS.get(value, Code(value, str(value), "unknown"))


def code_name(value: int) -> str:
    """Short uppercase name for a code, e.g. ``MISSING`` for 404."""
    return code_for(value).name


def file_code(upstream_has: bool, nastech_has: bool, identical: bool | None = None, violations: list | None = None) -> int:
    """Map a file's state to its error code.

    ``upstream_has`` / ``nastech_has`` tell presence; ``identical`` (when both
    exist) tells parity after branding; ``violations`` lists brand-rule hits.
    """
    if not upstream_has and nastech_has:
        return EXTRA
    if not upstream_has:
        return PASS
    if not nastech_has:
        return MISSING
    if violations:
        return VIOLATION
    if identical is True:
        return PASS
    return DRIFT


def aggregate(codes: list[int]) -> int:
    """Worst code in the list (highest severity wins)."""
    if not codes:
        return PASS
    worst = PASS
    for c in codes:
        if _SEVERITY.get(c, 0) > _SEVERITY.get(worst, 0):
            worst = c
    return worst


def exit_code_for(codes: list[int]) -> int:
    """Exit status for a scan/report: the aggregate error code (0 on pass)."""
    return aggregate(codes)


def summarize(codes: list[int]) -> str:
    worst = aggregate(codes)
    counts = {c: codes.count(c) for c in set(codes) if c != PASS}
    parts = [f"{code_for(c).name}={n}" for c, n in sorted(counts.items(), key=lambda kv: -_SEVERITY[kv[0]])]
    body = "  ".join(parts) if parts else "all clean"
    return f"{code_for(worst).name} ({worst}) - {body}"
