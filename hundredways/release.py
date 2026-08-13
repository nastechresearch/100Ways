"""Release verification for the 100Ways code table.

`release` is the gatekeeper: it validates both directions of the code
contract before anything ships.

  1. **Incoming codes** — any value arriving from a scan, a report, a
     dashboard payload, or a webhook must be a registered code.  Unknown
     codes are rejected loudly instead of silently mislabeled.
  2. **The table itself** — before a release, the CODE_DETAILS table is
     checked for internal consistency (unique values, unique names,
     monotone severity order, both presence and content contracts).  A
     table that contradicts itself is a release blocker.

Typical use::

    from hundredways.codes import release_verify_incoming
    from hundredways.codes import release_check_table

    errors = release_check_table()          # run in CI before release
    bad = release_verify_incoming(payload)  # validate every incoming code
"""

from __future__ import annotations

from .codes import (
    CODE_DETAILS,
    PASS,
    _SEVERITY,
    aggregate,
    code_for,
    code_name,
)


def release_check_table() -> list[str]:
    """Validate the code table's internal consistency.  Returns error strings.

    Empty list == the table is sound and a release is permitted.
    """
    errors: list[str] = []
    values = list(CODE_DETAILS)
    names = [c.name for c in CODE_DETAILS.values()]

    # 1. every value must map to exactly one detail
    if len(values) != len(set(values)):
        errors.append("duplicate code values in CODE_DETAILS")
    # 2. every name must be unique and match its value's role
    if len(names) != len(set(names)):
        errors.append("duplicate code names in CODE_DETAILS")
    # 3. every code must have a severity, and severities must form a total
    #    order (the ranking is explicit: MISSING beats VIOLATION beats DRIFT
    #    beats EXTRA beats PASS; GATE is the hardest) - no two codes tie.
    missing = [v for v in values if v not in _SEVERITY]
    if missing:
        errors.append(f"codes missing severity: {missing}")
    severities = [_SEVERITY[v] for v in values]
    if len(severities) != len(set(severities)):
        errors.append("two codes share the same severity - ranking is ambiguous")
    # 4. the aggregate of a single code must return that code (identity)
    for v in values:
        if aggregate([v]) != v:
            errors.append(f"aggregate([{v}]) != {v}")
    # 4b. aggregate must respect the severity ranking: the highest-severity
    #     member of any mix is the one that wins.
    ranked = sorted(_SEVERITY.items(), key=lambda kv: -kv[1])
    for i, (winner, _s) in enumerate(ranked):
        for loser, _ls in ranked[i + 1:]:
            if aggregate([winner, loser]) != winner:
                errors.append(f"aggregate ranking violated: {winner} should beat {loser}")
    # 5. PASS must be severity-0 and aggregate() of [] must be PASS
    if _SEVERITY.get(PASS, -1) != 0:
        errors.append("PASS must be severity 0")
    if aggregate([]) != PASS:
        errors.append("aggregate([]) must be PASS")
    return errors


def release_verify_incoming(payload: object, where: str = "payload") -> list[str]:
    """Validate every code inside a scan/report payload.  Returns error strings.

    Accepts either a list of ints or a dict with a ``codes`` key (which may
    itself be a list of ints or of per-file records carrying a ``code``).
    Non-registered codes are reported with their location in the payload.
    """
    errors: list[str] = []
    if isinstance(payload, dict):
        raw = payload.get("codes", payload.get("entries", []))
    else:
        raw = payload
    if not isinstance(raw, list):
        return [f"{where}: expected a list, got {type(raw).__name__}"]
    for i, item in enumerate(raw):
        if isinstance(item, dict):
            code = item.get("code")
            loc = f"{where}[{i}].code"
        elif isinstance(item, int):
            code = item
            loc = f"{where}[{i}]"
        else:
            errors.append(f"{where}[{i}]: not a code (type {type(item).__name__})")
            continue
        if code not in CODE_DETAILS:
            errors.append(f"{loc}: unknown code {code!r}")
        else:
            meta = code_for(code)
            if meta.name != code_name(code):
                errors.append(f"{loc}: name mismatch {meta.name!r} vs {code_name(code)!r}")
    return errors


def release_summary(payload: object, where: str = "payload") -> str:
    """Human summary of a payload: worst code + per-name counts."""
    if isinstance(payload, dict):
        raw = payload.get("codes", payload.get("entries", []))
    else:
        raw = payload
    codes = [c if isinstance(c, int) else c.get("code") for c in raw if c is not None]
    codes = [c for c in codes if c in CODE_DETAILS]
    counts = {}
    for c in codes:
        counts[code_name(c)] = counts.get(code_name(c), 0) + 1
    if not counts:
        return "clean"
    worst = aggregate(codes)
    parts = "  ".join(f"{name}={n}" for name, n in sorted(counts.items()))
    return f"{code_name(worst)} ({worst}) - {parts}"
