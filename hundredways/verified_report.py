"""One markdown document that says what the engine measured -- and what it did not.

Every answer the engine produces today leaves as stdout, so "what is the state of
this fork" has no single honest reply.  This module assembles the reply from the
objects the engine already built (:class:`hundredways.verify.VerifyReport`,
:class:`hundredways.weekly_sync.WeeklyFullSyncReport`, ``pubconfig`` findings,
``urlcheck`` findings, live ``coverage`` rows, ``forklint`` summary,
``adapters`` orphans, receipt files).  It measures nothing itself: an assembly
point that recomputes is a second implementation that can disagree with the first.

Three properties, each load-bearing:

* **Absence is stated.**  A section with no measurement prints
  ``not verified: <the measurement that is missing>`` and names what to run.
  An empty list is *not* the same as ``None`` -- "the scan ran and found nothing"
  is a result, "nobody looked" is not, and only ``None`` renders the absence.
* **Nothing secret ships.**  Every line passes ``secrets.redact`` before it is
  written or hashed, so a token inside a receipt's JSON, a workflow note, or a
  credential finding's evidence cannot reach the document.
* **Identical inputs give identical bytes.**  No clock, no ``dict`` order: the
  document is a function of its inputs alone, so two runs of the same commit are
  comparable byte for byte and any difference is a real change.  Each section
  carries a ``sha256`` of its own rendered lines, so a reader can tell which
  measurement moved without diffing the prose.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import urlcheck
from .receipts import sha256_file
from .secrets import redact
from .verify import gate_passes

__all__ = ["SECTIONS", "VerifiedReport", "build"]

SCHEMA = "100ways.verified-report/v1"

SECTIONS: tuple[str, ...] = (
    "Summary",
    "Delta",
    "Gates",
    "Credentials",
    "URLs",
    "Lint",
    "Ways",
    "Orphans",
    "Receipts",
)

# What to run when a section has nothing to say: the sentence names the missing
# measurement, not a generic "unknown".
_ABSENT = {
    "Summary": "no measurement was supplied, so there is nothing to summarize",
    "Delta": "no weekly sync, gap report or surface delta was run, so nothing here "
    "says what upstream changed",
    "Gates": "no parity VerifyReport and no sync gate, so no gate was measured",
    "Credentials": "pubconfig did not run, so credential presence is unjudged",
    "URLs": "no url scan was run, so every link in the tree is unjudged",
    "Lint": "fork-lint did not run, so the fork's own lint verdict is unknown",
    "Ways": "no ways coverage rows, so the registry was not resolved against the engine",
    "Orphans": "no orphan pass ran against a fork/upstream pair, so fork-only files "
    "are unjudged",
    "Receipts": "no receipt files were handed to the report, so nothing proves a gate ran",
}

_MISSING_PREFIX = "not verified: "


@dataclass(frozen=True)
class _Measurement:
    """One section's rendered lines plus the single line the summary repeats."""

    headline: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedReport:
    """The document, the per-section digests, and which sections could not measure."""

    text: str
    digests: dict[str, str]
    measured: tuple[str, ...]
    missing: tuple[str, ...]

    def section(self, name: str) -> str:
        """The body of one section, headings excluded -- for callers that quote it."""
        if name not in SECTIONS:
            raise KeyError(f"no such section: {name}")
        marker = f"\n## {name}\n"
        if marker not in self.text:
            raise KeyError(f"section {name} is not in the document")
        tail = self.text.split(marker, 1)[1]
        return tail.split("\n## ", 1)[0].strip()


def _digest(name: str, lines: Sequence[str]) -> str:
    """A hash of this section's own rendered lines, name included.

    Reproducible from the document alone: a reader who re-hashes the lines under a
    heading gets the same value the report printed, which is what makes the
    digest evidence rather than decoration.
    """
    payload = "\n".join((name, *lines))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parity_headline(parity: Any) -> str:
    return f"parity {parity.summary()}, gate {'PASS' if gate_passes(parity) else 'FAIL'}"


def _gates(parity: Any, sync: Any, threshold: float) -> _Measurement | None:
    sources: list[str] = []
    lines: list[str] = []
    headline: list[str] = []
    if parity is not None:
        sources.append("verify.gate_passes")
        lines.append(f"- parity: {parity.summary()}")
        lines.append(f"- parity gate: {'PASS' if gate_passes(parity, threshold) else 'FAIL'}")
        headline.append(_parity_headline(parity))
    if sync is not None:
        sources.append("weekly_sync.gate_blockers")
        blockers = sorted(
            sync.gate_blockers,
            key=lambda item: (item["category"], item["code"], item["path"], item["detail"]),
        )
        lines.append(f"- sync gate: {'PASS' if not blockers else 'FAIL'}")
        for blocker in blockers:
            lines.append(
                f"- blocker {blocker['category']}: {blocker['code']} "
                f"{blocker['path'] or '-'} {blocker['detail']}"
            )
        if sync.review_required:
            lines.append("- review required: non-blocking findings are listed for a human")
        headline.append(
            f"sync gate {'PASS' if not blockers else 'FAIL'} ({len(blockers)} blocker(s))"
        )
    if not lines:
        return None
    lines.insert(0, f"- measured: {' + '.join(sources)} (threshold {threshold})")
    return _Measurement("; ".join(headline), tuple(lines))


def _delta(sync: Any, gaps: Any, surface: Sequence[Any] | None) -> _Measurement | None:
    sources: list[str] = []
    lines: list[str] = []
    headline: list[str] = []
    if sync is not None:
        sources.append("weekly_sync")
        lines.append(
            f"- sync: {sync.commits} commits, {sync.files_changed} files, "
            f"+{sync.added_lines}/-{sync.deleted_lines} lines"
        )
        lines.append(f"- refs: {sync.previous_sha[:12]} -> {sync.upstream_sha[:12]}")
        areas = getattr(sync, "changed_areas", None) or {}
        if areas:
            lines.append(
                "- areas: " + ", ".join(f"{name}={areas[name]}" for name in sorted(areas))
            )
        headline.append(f"sync {sync.commits} commits / {sync.files_changed} files")
    if gaps is not None:
        sources.append("analyzer")
        lines.append(f"- gaps: {gaps.summary}")
        lines.append(
            f"- tree: nastech {gaps.nastech_commit[:12]} vs upstream {gaps.upstream_commit[:12]}"
        )
        headline.append(f"gaps {gaps.summary}")
    if surface:
        sources.append("adapters.diff_surface")
        ordered = sorted(
            surface,
            key=lambda item: (item.field, item.kind, item.before, item.after),
        )
        lines.append(f"- surface: {len(ordered)} movement(s) on upstream's own surface")
        for item in ordered:
            moved = (
                f"{item.before} -> {item.after}"
                if item.kind == "changed"
                else (item.after or item.before)
            )
            lines.append(f"  {item.field} {item.kind}: {moved} [{item.classification}]")
        headline.append(f"surface {len(ordered)} delta(s)")
    if not lines:
        return None
    lines.insert(0, f"- measured: {' + '.join(sources)}")
    return _Measurement("; ".join(headline), tuple(lines))


def _credentials(findings: Sequence[Any] | None) -> _Measurement | None:
    if findings is None:
        return None
    actionable = [item for item in findings if getattr(item, "actionable", False)]
    lines = [
        f"- measured: pubconfig findings over {len(findings)} credential name(s); "
        "names only, never values",
        f"- actionable: {len(actionable)} of {len(findings)}",
    ]
    for item in sorted(findings, key=lambda finding: finding.name):
        scopes = ", ".join(item.scopes) or "unscoped"
        gate = f" gate={item.gate}" if item.gate else ""
        lines.append(f"- {item.name}: {item.kind} {item.verdict} ({scopes}){gate}")
        for evidence in item.evidence():
            lines.append(f"    {evidence}")
    return _Measurement(
        f"credentials {len(findings)} name(s), {len(actionable)} actionable",
        tuple(lines),
    )


def _urls(findings: Sequence[Any] | None) -> _Measurement | None:
    if findings is None:
        return None
    tally = urlcheck.counts(findings)
    probed, unprobed = urlcheck.probe_tally(findings)
    lines = [
        f"- measured: urlcheck over {len(findings)} link(s); "
        f"{probed} answered by the network, {unprobed} not looked at",
    ]
    if probed == 0 and findings:
        lines.append(
            "- no network probe was made, so nothing here is claimed reachable: "
            "the verdicts are syntax and policy only"
        )
    lines.append(
        "- counts: " + ", ".join(f"{name}={tally[name]}" for name in urlcheck.SEVERITY_ORDER)
    )
    ranked = sorted(
        findings,
        key=lambda item: (
            urlcheck.SEVERITY_ORDER.index(item.status),
            item.link.path,
            item.link.line,
            item.link.url,
        ),
    )
    for item in ranked:
        lines.append(f"  {item.status} {item.link.url}  ({item.link.path}:{item.link.line})")
        lines.append(f"      {item.detail}")
        if item.suggestion:
            lines.append(f"      -> {item.suggestion}")
    verdicts = [item for item in findings if item.status in urlcheck.DECIDED]
    return _Measurement(
        f"urls {len(findings)} link(s): {len(verdicts)} verdict(s), "
        f"{probed} answered by the network, {unprobed} not looked at",
        tuple(lines),
    )


def _lint(summary: Any) -> _Measurement | None:
    if summary is None:
        return None
    lines = [
        f"- measured: fork-lint simulation over {len(summary.issues)} issue(s)",
        f"- {summary.line()}",
    ]
    for issue in sorted(summary.issues, key=lambda item: (item.script, item.path, item.detail)):
        stance = "blocking" if issue.blocking else "advisory"
        lines.append(f"  {issue.script}: {issue.path or '-'} {issue.detail} [{stance}]")
    return _Measurement(summary.line(), tuple(lines))


def _ways(rows: Sequence[Any] | None) -> _Measurement | None:
    if rows is None:
        return None
    counts = {"built": 0, "dangling": 0, "catalog-only": 0}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    lines = [
        f"- measured: {len(rows)} registry row(s) resolved against the live engine",
        f"- total={len(rows)} built={counts['built']} dangling={counts['dangling']} "
        f"catalog-only={counts['catalog-only']}",
    ]
    for row in sorted(rows, key=lambda item: item.way.way_id):
        if row.status != "dangling":
            continue
        detail = row.resolution.detail if row.resolution is not None else ""
        lines.append(f"  dangling {row.way.way_id}: uses {row.way.uses} -- {detail}")
    return _Measurement(
        f"ways total={len(rows)} built={counts['built']} dangling={counts['dangling']} "
        f"catalog-only={counts['catalog-only']}",
        tuple(lines),
    )


def _orphans(orphans: Sequence[Any] | None) -> _Measurement | None:
    if orphans is None:
        return None
    kinds: dict[str, int] = {}
    for item in orphans:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    lines = [
        f"- measured: adapters.find_orphans over {len(orphans)} fork-only path(s)",
        "- kinds: " + (", ".join(f"{name}={kinds[name]}" for name in sorted(kinds)) or "none"),
    ]
    for item in sorted(orphans, key=lambda found: found.path):
        lines.append(f"  {item.kind} {item.path}")
        lines.append(f"      evidence: {item.evidence}")
        lines.append(f"      next: {item.suggestion}")
    return _Measurement(
        f"orphans {len(orphans)} fork-only path(s)",
        tuple(lines),
    )


def _receipts(paths: Sequence[Any] | None) -> _Measurement | None:
    if paths is None:
        return None
    lines = [f"- measured: sha256 over {len(paths)} receipt file(s)"]
    unreadable: list[str] = []
    digested = 0
    for path in sorted(str(item) for item in paths):
        try:
            digest = sha256_file(path)
        except (OSError, ValueError) as error:
            unreadable.append(f"{path} [{type(error).__name__}: {error}]")
            continue
        digested += 1
        lines.append(f"  {Path(path).name} sha256 {digest}")
    for problem in unreadable:
        lines.append(f"- {_MISSING_PREFIX}this receipt could not be read: {problem}")
    return _Measurement(
        f"receipts {digested} of {len(list(paths))} file(s) digested",
        tuple(lines),
    )


def build(
    *,
    parity: Any = None,
    sync: Any = None,
    gaps: Any = None,
    surface: Sequence[Any] | None = None,
    credentials: Sequence[Any] | None = None,
    urls: Sequence[Any] | None = None,
    lint: Any = None,
    ways: Sequence[Any] | None = None,
    orphans: Sequence[Any] | None = None,
    receipts: Sequence[Any] | None = None,
    threshold: float = 0.99,
) -> VerifiedReport:
    """Assemble the verified document from whatever the engine has already measured.

    Each argument is one measurement; ``None`` means it did not run and the
    section says so.  The output is deterministic in its inputs alone, so a caller
    can keep two reports and diff them as evidence rather than as prose.
    """
    measured: dict[str, _Measurement | None] = {
        # Summary is a section like any other: with nothing measured it prints its
        # own absence rather than disappearing from the document.
        "Summary": None,
        "Delta": _delta(sync, gaps, surface),
        "Gates": _gates(parity, sync, threshold),
        "Credentials": _credentials(credentials),
        "URLs": _urls(urls),
        "Lint": _lint(lint),
        "Ways": _ways(ways),
        "Orphans": _orphans(orphans),
        "Receipts": _receipts(receipts),
    }
    ran = [name for name in SECTIONS if name != "Summary" and measured[name] is not None]
    if ran:
        summary_lines = [f"- measured: {', '.join(ran)}"]
        summary_lines += [f"- {measured[name].headline}" for name in ran]
        measured["Summary"] = _Measurement("", tuple(summary_lines))

    digests: dict[str, str] = {}
    parts: list[str] = [
        "# 100Ways verified report",
        "",
        f"schema: `{SCHEMA}`",
        "- determinism: identical inputs give identical bytes -- no clock, no dict order",
        "- redaction: every line passes ``secrets.redact`` before it is written or hashed",
        "- sections: " + ", ".join(SECTIONS),
    ]
    for name in SECTIONS:
        entry = measured[name]
        if entry is None:
            lines: list[str] = [f"- {_MISSING_PREFIX}{_ABSENT[name]}"]
        else:
            lines = [redact(line) for line in entry.lines]
        digest = _digest(name, lines)
        digests[name] = digest
        parts.append("")
        parts.append(f"## {name}")
        parts.append("")
        parts.extend(lines)
        parts.append(f"- digest: sha256 {digest}")
    parts.append("")
    parts.append("---")
    parts.append(
        f"document: sha256 {_digest('document', [digests[name] for name in SECTIONS])}"
    )
    missing = tuple(name for name in SECTIONS if measured[name] is None)
    return VerifiedReport(
        text="\n".join(parts) + "\n",
        digests=digests,
        measured=tuple(name for name in SECTIONS if name not in missing),
        missing=missing,
    )
