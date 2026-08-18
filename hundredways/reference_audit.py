"""Strict whole-tree upstream-brand accounting for a branded candidate.

Every path and text occurrence of ``hermes`` or ``nous`` is counted before
candidate publication.  The scanner is intentionally fail-closed: only exact
approved dependency tokens, contributor-name identity records, and the exact
summary attribution phrase are accepted.  Natural-language words that merely
contain the letter sequence (for example ``synchronous``) are reported as
non-brand lexical forms, not upstream-brand references.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

from .scanner import is_text

EXACT_ATTRIBUTION = "Powered by NousResearch"
_ALLOWED_ROOT_DEPENDENCIES = ("hermes-parser", "hermes-estree")
_ALLOWED_WEBSITE_DEPENDENCY = "@nous-research/image-size"
_CONTRIBUTOR_NAME_PREFIX = "contributors/names/"
_NONBRAND_NOUS_WORDS = {
    "anonymous",
    "anonymously",
    "asynchronous",
    "asynchronously",
    "autonomous",
    "autonomously",
    "luminous",
    "venous",
    "luminousix",
    "monotonous",
    "ominous",
    "pythonnousersite",
    "synchronous",
    "synchronously",
    "testsynchronousfallbackcacheplans",
    "testasynchronousfallbackcacheplans",
    "testoneshotclirunissynchronous",
}
_TOKEN_PATTERN = re.compile(r"hermes|nous", re.IGNORECASE)
_WORD_PATTERN = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class ReferenceFinding:
    token: str
    path: str
    line: int
    column: int
    kind: str
    detail: str


@dataclass
class ReferenceAuditReport:
    root: str
    raw_occurrences: dict[str, int] = field(default_factory=lambda: {"hermes": 0, "nous": 0})
    approved_occurrences: dict[str, int] = field(default_factory=lambda: {"hermes": 0, "nous": 0})
    lexical_occurrences: dict[str, int] = field(default_factory=lambda: {"hermes": 0, "nous": 0})
    findings: list[ReferenceFinding] = field(default_factory=list)

    @property
    def blocking_occurrences(self) -> dict[str, int]:
        values = {"hermes": 0, "nous": 0}
        for finding in self.findings:
            values[finding.token] += 1
        return values

    @property
    def passes(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": "100ways.strict-reference-audit/v1",
            "root": self.root,
            "gate": "PASS" if self.passes else "FAIL",
            "raw_occurrences": self.raw_occurrences,
            "approved_occurrences": self.approved_occurrences,
            "lexical_occurrences": self.lexical_occurrences,
            "blocking_occurrences": self.blocking_occurrences,
            "allowed_dependency_groups": {
                "root_package_lock": list(_ALLOWED_ROOT_DEPENDENCIES),
                "website_package_metadata": [_ALLOWED_WEBSITE_DEPENDENCY],
            },
            "allowed_attribution": EXACT_ATTRIBUTION,
            "findings": [asdict(finding) for finding in self.findings],
        }


def _word_at(text: str, position: int) -> str:
    for match in _WORD_PATTERN.finditer(text):
        if match.start() <= position < match.end():
            return match.group(0).lower()
    return ""


def _is_lexical_nonbrand(token: str, text: str, position: int) -> bool:
    return token == "nous" and _word_at(text, position) in _NONBRAND_NOUS_WORDS


def _is_approved_dependency(token: str, path: str, line: str) -> bool:
    if path == "package-lock.json" and token == "hermes":
        return any(dependency in line.lower() for dependency in _ALLOWED_ROOT_DEPENDENCIES)
    if path in {"website/package-lock.json", "website/.npmrc"} and token == "nous":
        return _ALLOWED_WEBSITE_DEPENDENCY in line.lower()
    return False


def _is_approved_attribution(token: str, path: str, line: str) -> bool:
    return (
        token == "nous"
        and path.upper().endswith("SUMMARY.MD")
        and EXACT_ATTRIBUTION in line
    )


def _scan_text(report: ReferenceAuditReport, path: str, text: str) -> None:
    for line_number, line in enumerate(text.splitlines() or [text], start=1):
        for match in _TOKEN_PATTERN.finditer(line):
            token = match.group(0).lower()
            report.raw_occurrences[token] += 1
            if _is_lexical_nonbrand(token, line, match.start()):
                report.lexical_occurrences[token] += 1
                continue
            if path.startswith(_CONTRIBUTOR_NAME_PREFIX):
                report.approved_occurrences[token] += 1
                continue
            if _is_approved_dependency(token, path, line):
                report.approved_occurrences[token] += 1
                continue
            if _is_approved_attribution(token, path, line):
                report.approved_occurrences[token] += 1
                continue
            report.findings.append(
                ReferenceFinding(
                    token=token,
                    path=path,
                    line=line_number,
                    column=match.start() + 1,
                    kind="unapproved-reference",
                    detail="upstream brand reference is outside the strict allowlist",
                )
            )


def _scan_path(report: ReferenceAuditReport, path: str) -> None:
    for match in _TOKEN_PATTERN.finditer(path):
        token = match.group(0).lower()
        report.raw_occurrences[token] += 1
        if _is_lexical_nonbrand(token, path, match.start()):
            report.lexical_occurrences[token] += 1
            continue
        if path.startswith(_CONTRIBUTOR_NAME_PREFIX):
            report.approved_occurrences[token] += 1
            continue
        report.findings.append(
            ReferenceFinding(
                token=token,
                path=path,
                line=0,
                column=match.start() + 1,
                kind="unapproved-path-reference",
                detail="upstream brand reference appears in a candidate path",
            )
        )


def audit_references(root: str | Path) -> ReferenceAuditReport:
    """Count every relevant upstream-brand reference and return blocking findings."""
    base = Path(root)
    report = ReferenceAuditReport(root=str(base))
    if not base.is_dir():
        report.findings.append(
            ReferenceFinding(
                token="hermes",
                path=str(base),
                line=0,
                column=0,
                kind="candidate-root",
                detail="candidate root is unavailable",
            )
        )
        return report
    for file_path in sorted(base.rglob("*"), key=lambda value: value.as_posix()):
        if not file_path.is_file() or ".git" in file_path.parts:
            continue
        relative = file_path.relative_to(base).as_posix()
        _scan_path(report, relative)
        try:
            data = file_path.read_bytes()
        except OSError:
            continue
        if not is_text(data):
            continue
        _scan_text(report, relative, data.decode("utf-8", errors="replace"))
    return report


def write_audit(path: str | Path, report: ReferenceAuditReport) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    target.write_text(rendered, encoding="utf-8")
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count and gate upstream brand references")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = audit_references(args.candidate)
    write_audit(args.output, report)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.passes else 1


if __name__ == "__main__":
    raise SystemExit(main())
