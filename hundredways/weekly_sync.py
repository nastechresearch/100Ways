"""Weekly full-sync planning, audit, and freshness gates for 100Ways.

This module deliberately treats a weekly run as a complete branded-tree update,
not a cherry-pick selection mechanism.  It never pushes, merges, or releases.
It records the exact upstream SHA, validates local branding invariants, and
produces a reviewable report for the operator.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .assets import OwnedAssets
from .ci_policy import WorkflowPolicyIssue, audit_workflow_security
from .rules import BrandingRules, is_immutable_path
from .visual_assets import VisualIssue, compare_owned_to_upstream


# A visible, testable capability contract for the weekly full-sync workflow.
# Each entry is enforced by the command itself or its corresponding 100Ways
# stage; future extensions may add richer per-capability evidence.
BASE_SYNC_CAPABILITIES: tuple[str, ...] = (
    # Discovery and scope (1-8)
    "resolve-upstream-head", "record-upstream-sha", "load-last-sync-ledger",
    "count-upstream-commits", "collect-commit-subjects", "collect-file-numstat",
    "detect-renames-and-deletions", "classify-affected-areas",
    # Snapshot control (9-16)
    "isolated-worktree", "fresh-upstream-clone", "full-tree-snapshot",
    "pre-brand-file-census", "post-brand-file-census", "source-path-map",
    "missing-path-gate", "snapshot-manifest",
    # Branding and assets (17-24)
    "case-preserving-token-map", "path-renaming", "text-rewriting",
    "owned-asset-overlay", "asset-byte-integrity", "immutable-data-protection",
    "first-party-brand-audit", "third-party-name-allowlist",
    # Package and dependency integrity (25-32)
    "root-uv-lock-reconciliation", "root-npm-lock-reconciliation",
    "nested-npm-lock-audit", "workspace-name-consistency", "dependency-name-allowlist",
    "package-root-identity-gate", "install-script-inventory", "manifest-consistency",
    # Verification and freshness (33-40)
    "transformed-content-parity", "locked-file-accounting", "binary-file-accounting",
    "fork-local-preservation", "nested-lock-gate", "whitespace-gate",
    "post-run-upstream-refetch", "upstream-freshness-lock",
    # Clean integration records and operations (41-50)
    "upstream-ledger", "candidate-branch-plan", "nas-tech-integration-summary",
    "release-note-draft", "no-push-default", "no-merge-default",
    "review-report", "machine-readable-output", "failure-reason-catalog",
    "weekly-schedule-template",
)

HARDENED_CI_CAPABILITIES: tuple[str, ...] = (
    "single-run-concurrency", "action-sha-pin-audit", "engine-sha-pin-audit",
    "workflow-codeowners-audit", "least-privilege-permission-audit", "forbidden-trigger-audit",
    "artifact-digest-capture", "report-retention-policy", "cache-path-audit",
    "cache-secret-scan", "candidate-bundle-attestation-policy", "visual-asset-inventory",
    "binary-identity-digest", "canonical-pixel-digest", "perceptual-similarity-shortlist",
    "geometry-and-alpha-comparison", "svg-and-icon-inventory", "credential-signal-inspection",
    "asset-approval-ledger", "visual-review-queue", "stale-sha-retry-policy",
    "immutable-decision-record",
)

FULL_SYNC_CAPABILITIES = BASE_SYNC_CAPABILITIES + HARDENED_CI_CAPABILITIES

THIRD_PARTY_BRAND_ALLOWLIST = (
    "hermes-parser", "hermes-estree", "@nous-research/image-size",
)


@dataclass(frozen=True)
class AuditIssue:
    code: str
    path: str
    detail: str


@dataclass
class WeeklyFullSyncReport:
    upstream_sha: str
    previous_sha: str
    commits: int
    files_changed: int
    added_lines: int
    deleted_lines: int
    snapshot_upstream_sha: str = ""
    capabilities: int = len(FULL_SYNC_CAPABILITIES)
    brand_issues: list[AuditIssue] = field(default_factory=list)
    lock_issues: list[AuditIssue] = field(default_factory=list)
    asset_issues: list[AuditIssue] = field(default_factory=list)
    visual_issues: list[VisualIssue] = field(default_factory=list)
    ci_issues: list[WorkflowPolicyIssue] = field(default_factory=list)
    freshness_ok: bool = False
    mode: str = "report"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def gate_passes(self) -> bool:
        return not (self.brand_issues or self.lock_issues or self.asset_issues or self.visual_issues or self.ci_issues) and self.freshness_ok

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"gate": "PASS" if self.gate_passes else "FAIL"}


def _run(repo: str, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout.strip()


def _git_changed_numstat(repo: str, previous: str, current: str) -> tuple[int, int, int]:
    if not previous:
        return 0, 0, 0
    out = _run(repo, "diff", "--numstat", f"{previous}..{current}")
    added = deleted = files = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0].isdigit():
            added += int(parts[0])
        if parts[1].isdigit():
            deleted += int(parts[1])
    return files, added, deleted


def ledger_path(state_dir: str) -> Path:
    return Path(state_dir) / "upstream-ledger.json"


def load_ledger(state_dir: str) -> dict[str, Any]:
    path = ledger_path(state_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_ledger(state_dir: str, report: WeeklyFullSyncReport, candidate: str = "") -> Path:
    path = ledger_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = report.to_dict() | {"candidate": candidate, "recorded_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return path


def fetch_upstream(upstream_repo: str, ref: str = "origin/main") -> str:
    _run(upstream_repo, "fetch", "origin", "--prune")
    return _run(upstream_repo, "rev-parse", ref)


def audit_nested_lockfiles(root: str) -> list[AuditIssue]:
    """Ensure every package-lock root agrees with its adjacent package.json."""
    issues: list[AuditIssue] = []
    for lock in Path(root).rglob("package-lock.json"):
        if ".git" in lock.parts or "node_modules" in lock.parts:
            continue
        manifest = lock.with_name("package.json")
        if not manifest.is_file():
            continue
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
            locked = json.loads(lock.read_text(encoding="utf-8"))
            expected = package.get("name")
            actual = (locked.get("packages") or {}).get("", {}).get("name") or locked.get("name")
        except (OSError, ValueError, AttributeError):
            issues.append(AuditIssue("lock-unreadable", str(lock.relative_to(root)), "cannot read package metadata"))
            continue
        if expected and actual != expected:
            issues.append(AuditIssue("lock-root-name", str(lock.relative_to(root)), f"package.json={expected!r}; lock={actual!r}"))
    return issues


def audit_owned_assets(root: str) -> list[AuditIssue]:
    owned = OwnedAssets(repo=root)
    issues: list[AuditIssue] = []
    for target, rel_source in owned.mapping.items():
        target_path = Path(root) / target
        source_path = Path(owned.root) / rel_source
        if not target_path.is_file() or not source_path.is_file():
            issues.append(AuditIssue("asset-missing", target, "target or owned source is missing"))
        elif target_path.read_bytes() != source_path.read_bytes():
            issues.append(AuditIssue("asset-mismatch", target, "target bytes differ from owned source"))
    return issues


def audit_visual_assets(branded_root: str, upstream_root: str) -> list[VisualIssue]:
    """Compare registered NasTech-owned visual assets with the upstream asset tree."""
    owned = OwnedAssets(repo=branded_root)
    return compare_owned_to_upstream(owned.root, owned.mapping.values(), upstream_root)


def _is_allowed(line: str, path: str) -> bool:
    if path.startswith("contributors/emails/"):
        return True
    return any(allowed in line.lower() for allowed in THIRD_PARTY_BRAND_ALLOWLIST)


def audit_first_party_brand(root: str, rules: BrandingRules | None = None) -> list[AuditIssue]:
    """Flag exact first-party Hermes/Nous names while allowing immutable/vendor data."""
    rules = rules or BrandingRules()
    pattern = re.compile(r"(?i)(?<![a-z0-9_-])(hermes|nous)(?![a-z0-9_-])")
    issues: list[AuditIssue] = []
    for path in Path(root).rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(root))
        if is_immutable_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line) and not _is_allowed(line, rel):
                issues.append(AuditIssue("first-party-brand", rel, f"line {number}: {line.strip()[:180]}"))
    return issues


def _snapshot_upstream_sha(branded_root: str) -> str:
    """Read the exact Hermes SHA captured in a 100Ways snapshot, if present."""
    path = Path(branded_root) / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("upstream_sha", "")
    return value if isinstance(value, str) else ""


def build_weekly_report(
    upstream_repo: str,
    branded_root: str,
    state_dir: str,
    *,
    mode: str = "report",
    ref: str = "origin/main",
) -> WeeklyFullSyncReport:
    """Build a deterministic full-sync report without publishing any repository change."""
    before = load_ledger(state_dir).get("upstream_sha", "")
    current = fetch_upstream(upstream_repo, ref)
    files, added, deleted = _git_changed_numstat(upstream_repo, before, current)
    commits = int(_run(upstream_repo, "rev-list", "--count", f"{before}..{current}") or 0) if before else 0
    captured = _snapshot_upstream_sha(branded_root)
    report = WeeklyFullSyncReport(
        upstream_sha=current,
        previous_sha=before,
        snapshot_upstream_sha=captured,
        commits=commits,
        files_changed=files,
        added_lines=added,
        deleted_lines=deleted,
        mode=mode,
    )
    report.lock_issues = audit_nested_lockfiles(branded_root)
    report.brand_issues = audit_first_party_brand(branded_root)
    report.asset_issues = audit_owned_assets(branded_root)
    report.visual_issues = audit_visual_assets(branded_root, upstream_repo)
    report.ci_issues = audit_workflow_security(branded_root)
    report.freshness_ok = fetch_upstream(upstream_repo, ref) == current
    if captured and captured != current:
        report.brand_issues.append(AuditIssue(
            "snapshot-stale",
            "manifest.json",
            f"snapshot captured {captured}; current upstream is {current}",
        ))
    return report


def write_weekly_report(path: str, report: WeeklyFullSyncReport) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 100Ways weekly full-sync report", "",
        f"- upstream SHA: `{report.upstream_sha}`",
        f"- previous SHA: `{report.previous_sha or 'none'}`",
        f"- snapshot SHA: `{report.snapshot_upstream_sha or 'not recorded'}`",
        f"- upstream commits: {report.commits}",
        f"- changed files: {report.files_changed}",
        f"- line delta: +{report.added_lines}/-{report.deleted_lines}",
        f"- full-sync capabilities: {report.capabilities}",
        f"- freshness lock: {'PASS' if report.freshness_ok else 'FAIL'}",
        f"- gate: {'PASS' if report.gate_passes else 'FAIL'}", "",
    ]
    for title, issues in (("Brand issues", report.brand_issues), ("Nested lock issues", report.lock_issues), ("Owned asset issues", report.asset_issues), ("Visual asset issues", report.visual_issues), ("CI policy issues", report.ci_issues)):
        lines.extend([f"## {title}", ""])
        lines.extend(["- None"] if not issues else [f"- `{i.code}` `{i.path}` — {i.detail}" for i in issues])
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")


def capability_count() -> int:
    return len(FULL_SYNC_CAPABILITIES)
