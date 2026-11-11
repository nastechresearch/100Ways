"""Final exact-source conformance verification for branded candidate trees.

This module is intentionally deterministic. It rebuilds the expected branded
source from the Hermes tree and verifies the candidate without applying fork
assets, reconciliation, or preservation. It has no network or publication side effects.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .integrity import audit_candidate_tree, tree_digest
from .rules import BrandingRules
from .tree_index import FileIndex
from .updates import brand_tree, verify_branded


@dataclass(frozen=True)
class ConformanceIssue:
    """One final candidate mismatch that blocks publication."""

    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class ConformanceReport:
    """Evidence that the candidate matches the exact source after branding."""

    source_files: int
    verified_files: int
    reconciled_paths: tuple[str, ...]
    candidate_digest: str
    issues: tuple[ConformanceIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues and self.source_files == self.verified_files

    def to_dict(self) -> dict[str, object]:
        return {
            "gate": "PASS" if self.passed else "FAIL",
            "source_files": self.source_files,
            "verified_files": self.verified_files,
            "reconciled_paths": list(self.reconciled_paths),
            "candidate_tree_sha256": self.candidate_digest,
            "issues": [asdict(issue) for issue in self.issues],
        }


def verify_final_candidate(
    source: str | Path,
    candidate: str | Path,
    *,
    owned_assets_root: str | Path = "",
    rules: BrandingRules | None = None,
    allowed_extra_paths: Iterable[str] = (),
    fork_root: str | Path = "",
) -> ConformanceReport:
    """Rebuild only the branded source and verify the candidate exactly.

    Compatibility parameters for former fork layers are accepted but ignored;
    no fork assets, reconciliation, report, or other extra files are allowed.
    """
    source_path = Path(source)
    candidate_path = Path(candidate)
    rules = rules or BrandingRules()
    issues: list[ConformanceIssue] = []

    if not source_path.is_dir():
        issue = ConformanceIssue("source", str(source_path), "source tree is unavailable")
        return ConformanceReport(0, 0, (), "", (issue,))
    if not candidate_path.is_dir():
        issue = ConformanceIssue("candidate", str(candidate_path), "candidate tree is unavailable")
        return ConformanceReport(0, 0, (), "", (issue,))

    with tempfile.TemporaryDirectory(prefix="100ways-conformance-") as temporary:
        expected = Path(temporary) / "expected"
        try:
            brand_tree(str(source_path), str(expected), rules)
        except (OSError, ValueError) as exc:
            return ConformanceReport(
                0,
                0,
                (),
                tree_digest(candidate_path),
                (ConformanceIssue("expected-tree", "", str(exc)),),
            )
        verified = verify_branded(str(source_path), str(candidate_path), rules)
        expected_paths = {entry.rel for entry in FileIndex(expected).entries}

    for result in verified.failed:
        issues.append(ConformanceIssue("source-parity", result.mapped_path, result.note))
    candidate_index = FileIndex(candidate_path)
    candidate_paths = {entry.rel for entry in candidate_index.entries}
    for path in sorted(candidate_paths - expected_paths):
        issues.append(
            ConformanceIssue(
                "unexpected-candidate-path",
                path,
                "candidate path is absent from the exact branded source tree",
            )
        )
    for integrity_issue in audit_candidate_tree(candidate_path):
        issues.append(
            ConformanceIssue(
                f"candidate-{integrity_issue.code}",
                integrity_issue.path,
                integrity_issue.detail,
            )
        )
    return ConformanceReport(
        verified.total,
        verified.passed,
        (),
        candidate_index.digest(),
        tuple(issues),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify final branded candidate conformance")
    parser.add_argument("--source", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--owned-assets-root", default="")
    parser.add_argument("--fork-root", default="")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Write final candidate conformance evidence and exit nonzero on mismatch."""
    parser = build_parser()
    args = parser.parse_args(argv)
    report = verify_final_candidate(args.source, args.candidate)
    Path(args.output).write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
