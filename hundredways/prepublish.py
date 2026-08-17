"""Deterministic readiness scans performed before #344 candidate publication."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .integrity import (
    audit_candidate_tree,
    audit_manifest_provenance,
    sha256_file,
    tree_digest,
)
from .rules import BrandingRules, is_immutable_path

_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    path: str
    detail: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _source_delta_issues(snapshot: Path, manifest: dict) -> list[ReadinessIssue]:
    """Reject source paths that upstream retired but the candidate still retains."""
    delta = manifest.get("source_delta")
    provenance = manifest.get("source_provenance")
    baseline_sha = (
        provenance.get("baseline_sha", "") if isinstance(provenance, dict) else ""
    )
    if not isinstance(delta, dict):
        if baseline_sha:
            return [
                ReadinessIssue(
                    "source-delta",
                    "manifest.json",
                    "baseline exists but direct upstream tree-delta evidence is missing",
                )
            ]
        return []
    if baseline_sha and delta.get("complete") is not True:
        return [
            ReadinessIssue(
                "source-delta",
                "manifest.json",
                "baseline exists but direct upstream tree-delta evidence is incomplete",
            )
        ]
    owned_paths = {
        value for value in delta.get("owned_paths", []) if isinstance(value, str)
    }
    changes = delta.get("changes", [])
    if not isinstance(changes, list):
        return [
            ReadinessIssue("source-delta", "manifest.json", "changes must be a list")
        ]
    issues: list[ReadinessIssue] = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or change.get("status") not in {"deleted", "renamed"}
        ):
            continue
        retired = change.get("old_mapped")
        if (
            not isinstance(retired, str)
            or not retired
            or Path(retired).is_absolute()
            or ".." in Path(retired).parts
        ):
            issues.append(
                ReadinessIssue("source-delta", "manifest.json", "retired path is invalid")
            )
            continue
        if retired not in owned_paths and (snapshot / retired).is_file():
            issues.append(
                ReadinessIssue(
                    "stale-upstream-path",
                    retired,
                    "candidate retains a path retired by direct Hermes source evidence",
                )
            )
    return issues


def _collision_groups(paths: list[str]) -> set[frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for path in paths:
        grouped.setdefault(path.casefold(), set()).add(path)
    return {frozenset(group) for group in grouped.values() if len(group) > 1}


def inherited_case_collision_evidence(
    snapshot: Path,
    upstream: Path,
) -> tuple[set[frozenset[str]], list[dict[str, object]]]:
    """Return exact candidate collision groups directly inherited from Hermes.

    A candidate group is permitted only when its complete mapped path set is
    present in the direct source. Immutable source records must also remain
    byte-identical. This is review evidence, not a portable-artifact approval.
    """
    rules = BrandingRules()
    source_paths = [
        path.relative_to(upstream).as_posix()
        for path in upstream.rglob("*")
        if ".git" not in path.relative_to(upstream).parts
    ]
    source_by_mapped: dict[str, list[str]] = {}
    for source_path in source_paths:
        source_by_mapped.setdefault(rules.transform_path(source_path), []).append(source_path)
    source_groups = _collision_groups(list(source_by_mapped))
    candidate_paths = [
        path.relative_to(snapshot).as_posix()
        for path in snapshot.rglob("*")
        if ".git" not in path.relative_to(snapshot).parts
    ]
    allowed: set[frozenset[str]] = set()
    evidence: list[dict[str, object]] = []
    for group in sorted(_collision_groups(candidate_paths), key=lambda item: sorted(item)):
        if group not in source_groups:
            continue
        source_members: list[str] = []
        immutable_digests: dict[str, str] = {}
        valid = True
        for candidate_path in sorted(group):
            originals = source_by_mapped.get(candidate_path, [])
            if len(originals) != 1:
                valid = False
                break
            source_path = originals[0]
            source_members.append(source_path)
            if is_immutable_path(source_path):
                source_file = upstream / source_path
                candidate_file = snapshot / candidate_path
                if not source_file.is_file() or not candidate_file.is_file():
                    valid = False
                    break
                source_digest = sha256_file(source_file)
                if sha256_file(candidate_file) != source_digest:
                    valid = False
                    break
                immutable_digests[candidate_path] = source_digest
        if valid:
            allowed.add(group)
            evidence.append(
                {
                    "paths": sorted(group),
                    "source_paths": source_members,
                    "immutable_sha256": immutable_digests,
                    "portability": "review-required-case-insensitive-collision",
                }
            )
    return allowed, evidence


def _credential_signals(root: Path) -> list[ReadinessIssue]:
    """Return credential-pattern findings from a tree, excluding binary assets."""
    issues: list[ReadinessIssue] = []
    ignored_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff2", ".pdf"}
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or "node_modules" in path.parts
            or path.suffix.lower() in ignored_suffixes
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            issues.append(ReadinessIssue(
                "credential-signal",
                str(path.relative_to(root)),
                "credential-like value detected",
            ))
    return issues


def scan_snapshot_details(
    snapshot: str | Path,
    upstream: str | Path,
    expected_upstream_sha: str,
) -> tuple[list[ReadinessIssue], list[dict[str, object]]]:
    snapshot_path = Path(snapshot)
    upstream_path = Path(upstream)
    issues: list[ReadinessIssue] = []
    manifest_path = snapshot_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [ReadinessIssue("manifest", "manifest.json", f"cannot read manifest: {exc}")], []
    actual_sha = _git(upstream_path, "rev-parse", "HEAD")
    issues.extend(
        ReadinessIssue(issue.code, issue.path, issue.detail)
        for issue in audit_manifest_provenance(
            manifest_path, expected_upstream_sha=actual_sha
        )
    )
    if expected_upstream_sha and actual_sha != expected_upstream_sha:
        issues.append(
            ReadinessIssue(
                "freshness",
                ".git",
                "upstream changed during the synchronization run; retry against "
                "the latest direct source",
            )
        )
    issues.extend(_source_delta_issues(snapshot_path, manifest))
    license_path = snapshot_path / "LICENSE"
    license_ok = license_path.is_file() and license_path.read_text(
        encoding="utf-8", errors="ignore"
    ).startswith("MIT License")
    if not license_ok:
        issues.append(
            ReadinessIssue("license", "LICENSE", "MIT license text is missing or changed")
        )
    runner = snapshot_path / "scripts" / "run_tests.sh"
    if not runner.is_file() or not os.access(runner, os.X_OK):
        issues.append(
            ReadinessIssue(
                "test-runner-mode",
                "scripts/run_tests.sh",
                "test runner must exist and be executable",
            )
        )
    if (snapshot_path / ".git").exists():
        issues.append(
            ReadinessIssue("snapshot-git", ".git", "snapshot must not contain repository metadata")
        )
    allowed_collisions, collision_evidence = inherited_case_collision_evidence(
        snapshot_path,
        upstream_path,
    )
    issues.extend(
        ReadinessIssue(issue.code, issue.path, issue.detail)
        for issue in audit_candidate_tree(
            snapshot_path,
            allowed_case_collision_groups=allowed_collisions,
        )
    )
    # Keep inherited source fixtures visible in the weekly report, but do not
    # let them block candidate publication.  Any credential-like value added by
    # branding, the engine-owned asset registry, or fork preservation remains a
    # prepublication failure.
    rules = BrandingRules()
    source_credential_paths = {
        rules.transform_path(issue.path) for issue in _credential_signals(upstream_path)
    }
    for issue in _credential_signals(snapshot_path):
        if issue.path not in source_credential_paths:
            issues.append(issue)
    return issues, collision_evidence


def scan_snapshot(
    snapshot: str | Path,
    upstream: str | Path,
    expected_upstream_sha: str,
) -> list[ReadinessIssue]:
    """Return blocking readiness findings for a candidate snapshot."""
    issues, _ = scan_snapshot_details(snapshot, upstream, expected_upstream_sha)
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--expected-upstream-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    issues, inherited_collisions = scan_snapshot_details(
        args.snapshot,
        args.upstream,
        args.expected_upstream_sha,
    )
    body = {
        "gate": "PASS" if not issues else "FAIL",
        "issues": [asdict(issue) for issue in issues],
        "review_required": bool(inherited_collisions),
        "inherited_case_collisions": inherited_collisions,
        "candidate_tree_sha256": tree_digest(args.snapshot),
    }
    Path(args.output).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(body, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
