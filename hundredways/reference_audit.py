"""Strict public-branding reference audit for branded candidate trees.

Only two dependency groups are allowed to retain upstream names:

* the JavaScript Hermes parser dependency family (``hermes-parser`` and its
  locked transitive ``hermes-estree`` package); and
* the website's ``@nous-research/image-size`` package.

Everything else is treated as a branding finding, including file and directory
names, generated reports, documentation, configuration, source text, and
assets whose textual metadata exposes the upstream names.  The audit is
intentionally separate from source parity: dependency lockfiles are the only
allowlisted public references.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

_REFERENCE = re.compile(r"(?i)(?<![a-z0-9])(?:hermes|nous)(?![a-z0-9])")
_IGNORED_PARTS = {".git", "node_modules", "__pycache__", ".pytest_cache"}
_ATTRIBUTION = "Powered by NousResearch"

_ALLOWED_DEPENDENCY_REFERENCES = (
    ("package-lock.json", re.compile(r"(?i)hermes-(?:parser|estree)")),
    ("website/package-lock.json", re.compile(r"(?i)@nous-research/image-size")),
    ("website/.npmrc", re.compile(r"(?i)@nous-research/image-size")),
)


@dataclass(frozen=True)
class ReferenceFinding:
    """One non-allowlisted upstream-brand reference."""

    path: str
    line: int
    kind: str
    token: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _allowed_dependency_reference(path: str, line: str) -> bool:
    for allowed_path, pattern in _ALLOWED_DEPENDENCY_REFERENCES:
        if path == allowed_path and pattern.search(line):
            return True
    return False


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        yield path


def audit_references(root: str | Path) -> tuple[ReferenceFinding, ...]:
    """Find every non-allowlisted Hermes/Nous reference in a candidate tree."""
    root_path = Path(root)
    findings: list[ReferenceFinding] = []
    for path in _iter_files(root_path):
        relative = path.relative_to(root_path).as_posix()
        path_matches = tuple(_REFERENCE.finditer(relative))
        for match in path_matches:
            findings.append(
                ReferenceFinding(
                    relative,
                    0,
                    "path",
                    match.group(0),
                    "upstream brand remains in a candidate path",
                )
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(
                ReferenceFinding(relative, 0, "read-error", "", f"cannot read file: {exc}")
            )
            continue
        if b"\x00" in data[:8192]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _allowed_dependency_reference(relative, line):
                continue
            if _ATTRIBUTION in line:
                remainder = line.replace(_ATTRIBUTION, "")
                if not _REFERENCE.search(remainder):
                    continue
            for match in _REFERENCE.finditer(line):
                findings.append(
                    ReferenceFinding(
                        relative,
                        number,
                        "text",
                        match.group(0),
                        "upstream brand remains in candidate content",
                    )
                )
    return tuple(findings)


def reference_summary(root: str | Path) -> dict[str, object]:
    """Return deterministic gate evidence for reports and receipts."""
    findings = audit_references(root)
    return {
        "gate": "PASS" if not findings else "FAIL",
        "allowed_dependency_groups": [
            "package-lock.json: hermes-parser/hermes-estree",
            "website/package-lock.json: @nous-research/image-size",
        ],
        "finding_count": len(findings),
        "findings": [finding.to_dict() for finding in findings],
    }
