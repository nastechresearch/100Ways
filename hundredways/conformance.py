"""Final exact-source conformance verification for branded candidate trees.

This module is intentionally deterministic.  It rebuilds the expected branded
source from the freshly cloned Hermes tree, reapplies documented NasTech-owned
assets and reconciliation rules, then verifies the candidate immediately
before publication.  It has no network or publication side effects.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .assets import OwnedAssets
from .forkcheck import SourceDeltaReport, preserve_fork_files, source_tree_delta
from .integrity import audit_candidate_tree, tree_digest
from .rules import BrandingRules
from .updates import (
    _reconcile_docs_posix_separators,
    _reconcile_fork_skill_catalogs,
    _reconcile_fork_skill_sidebars,
    _reconcile_skill_docs_order,
    apply_owned_assets,
    brand_tree,
    fork_manifest_upstream_sha,
    reconcile_tree,
    verify_branded,
)

_REPORT_PATHS = {"GATE-REPORT.md", "UPDATE-REPORT.md", "manifest.json"}


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


def _conformance_upstream_sha(source: str) -> str:
    """Current upstream HEAD in the source clone, or \"\" when unavailable."""
    try:
        result = subprocess.run(
            ["git", "-C", source, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode:
        return ""
    return result.stdout.strip()


def _replay_fork_preservation(
    expected: str,
    source: str,
    fork_root: str,
    rules: BrandingRules,
    owned: OwnedAssets | None,
) -> list[str]:
    """Replay the pipeline's fork-preservation stage onto the expected tree.

    The pipeline preserves fork-local files after reconcile and then re-syncs
    the skill catalogs plus normalizes generated-docs separators.  The final
    candidate carries those post-preserve bytes, so the conformance rebuild
    must reproduce them from the fork -- never trust the candidate -- or a
    reconciled fork file would be reported as a source-parity mismatch.  The
    source delta is recomputed exactly as the pipeline does (baseline from the
    fork manifest, upstream from the source clone's HEAD).
    """
    if not fork_root or not os.path.isdir(fork_root):
        return []
    baseline_sha = fork_manifest_upstream_sha(fork_root)
    try:
        delta = source_tree_delta(
            source, baseline_sha, _conformance_upstream_sha(source), rules
        )
    except OSError:
        delta = SourceDeltaReport(baseline_sha, "")
    return preserve_fork_files(
        fork_root,
        expected,
        source,
        rules,
        obsolete_upstream_paths=delta.obsolete_mapped,
        owned_paths=set(owned.mapping) if owned else set(),
        allow_unclassified_fork_files=bool(baseline_sha),
    )


def verify_final_candidate(
    source: str | Path,
    candidate: str | Path,
    *,
    owned_assets_root: str | Path = "",
    rules: BrandingRules | None = None,
    allowed_extra_paths: Iterable[str] = (),
    fork_root: str | Path = "",
) -> ConformanceReport:
    """Rebuild expected branded source and verify the final candidate exactly.

    This check runs after branding, reconciliation, and fork preservation.  It
    verifies every source-provided path against the candidate's final bytes;
    known reconciliation paths are compared to a freshly recalculated expected
    tree rather than trusted from the candidate itself.  When ``fork_root`` is
    given, the pipeline's fork-preservation + post-preserve reconciles are
    replayed onto the expected tree so fork-synced content is verified against
    the same rebuilt bytes the pipeline produced.
    """
    source_path = Path(source)
    candidate_path = Path(candidate)
    fork_root_path = Path(fork_root) if fork_root else None
    rules = rules or BrandingRules()
    owned = OwnedAssets(root=str(owned_assets_root)) if owned_assets_root else None
    issues: list[ConformanceIssue] = []

    if not source_path.is_dir():
        issue = ConformanceIssue("source", str(source_path), "source tree is unavailable")
        return ConformanceReport(0, 0, (), "", (issue,))
    if not candidate_path.is_dir():
        issue = ConformanceIssue("candidate", str(candidate_path), "candidate tree is unavailable")
        return ConformanceReport(0, 0, (), "", (issue,))

    with tempfile.TemporaryDirectory(prefix="100ways-conformance-") as temporary:
        expected = Path(temporary) / "expected"
        preserved: list[str] = []
        try:
            brand_tree(str(source_path), str(expected), rules, owned)
            apply_owned_assets(str(expected), owned)
            reconciled = reconcile_tree(str(expected))
            preserved = _replay_fork_preservation(
                str(expected),
                str(source_path),
                str(fork_root_path) if fork_root_path else "",
                rules,
                owned,
            )
        except (OSError, ValueError) as exc:
            return ConformanceReport(
                0,
                0,
                (),
                tree_digest(candidate_path),
                (ConformanceIssue("expected-tree", "", str(exc)),),
            )
        reconciled_bytes: dict[str, bytes] = {}
        for relative in reconciled.fixed:
            expected_path = expected / relative
            if expected_path.is_file():
                reconciled_bytes[relative] = expected_path.read_bytes()
        for relative in _reconcile_fork_skill_catalogs(str(expected), preserved):
            expected_path = expected / relative
            if expected_path.is_file():
                reconciled_bytes[relative] = expected_path.read_bytes()
        for relative in _reconcile_docs_posix_separators(str(expected), preserved):
            expected_path = expected / relative
            if expected_path.is_file():
                reconciled_bytes[relative] = expected_path.read_bytes()
        for relative in _reconcile_fork_skill_sidebars(str(expected), preserved):
            expected_path = expected / relative
            if expected_path.is_file():
                reconciled_bytes[relative] = expected_path.read_bytes()
        for relative in _reconcile_skill_docs_order(str(expected)):
            expected_path = expected / relative
            if expected_path.is_file():
                reconciled_bytes[relative] = expected_path.read_bytes()
        verified = verify_branded(
            str(source_path),
            str(candidate_path),
            rules,
            owned,
            reconciled_bytes,
        )
        expected_paths = {
            path.relative_to(expected).as_posix()
            for path in expected.rglob("*")
            if path.is_file()
        }

    for result in verified.failed:
        issues.append(ConformanceIssue("source-parity", result.mapped_path, result.note))
    candidate_paths = {
        path.relative_to(candidate_path).as_posix()
        for path in candidate_path.rglob("*")
        if path.is_file()
    }
    allowed = set(allowed_extra_paths) | _REPORT_PATHS
    for path in sorted(candidate_paths - expected_paths):
        if path in allowed or path.startswith("config/owned-assets/"):
            continue
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
        tuple(sorted(reconciled_bytes)),
        tree_digest(candidate_path),
        tuple(issues),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Write final candidate conformance evidence and exit nonzero on mismatch."""
    parser = argparse.ArgumentParser(description="Verify final branded candidate conformance")
    parser.add_argument("--source", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--owned-assets-root", default="")
    parser.add_argument("--fork-root", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    allowed_extra_paths: tuple[str, ...] = ()
    manifest_path = Path(args.candidate) / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        preserved = manifest.get("fork", {}).get("preserved_paths", [])
        if isinstance(preserved, list) and all(isinstance(path, str) for path in preserved):
            allowed_extra_paths = tuple(preserved)
    except (OSError, ValueError, AttributeError):
        pass
    report = verify_final_candidate(
        args.source,
        args.candidate,
        owned_assets_root=args.owned_assets_root,
        allowed_extra_paths=allowed_extra_paths,
        fork_root=args.fork_root,
    )
    Path(args.output).write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
