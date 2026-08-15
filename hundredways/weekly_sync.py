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
from .rules import BrandingRules, is_immutable_path, is_locked_path
from .skill_policy import SkillPolicyIssue, audit_skill_firewall
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
    "first-party-brand-audit", "brand-fixture-consistency-audit", "third-party-name-allowlist",
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
    "brand-fixed-point-audit", "skill-firewall-audit", "immutable-decision-record",
    "dependency-vulnerability-pattern-audit",
    "secret-and-credential-pattern-audit", "mit-license-compliance-audit",
    "executable-permission-audit", "unexpected-empty-file-audit",
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
    skill_issues: list[SkillPolicyIssue] = field(default_factory=list)
    security_issues: list[AuditIssue] = field(default_factory=list)
    inherited_security_issues: list[AuditIssue] = field(default_factory=list)
    freshness_ok: bool = False
    mode: str = "report"
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def gate_passes(self) -> bool:
        visual_blocks = any(issue.severity == "block" for issue in self.visual_issues)
        ci_blocks = any(issue.severity == "block" for issue in self.ci_issues)
        skill_blocks = any(issue.severity == "block" for issue in self.skill_issues)
        return not (
            self.brand_issues
            or self.lock_issues
            or self.asset_issues
            or self.security_issues
            or visual_blocks
            or ci_blocks
            or skill_blocks
        ) and self.freshness_ok

    @property
    def review_required(self) -> bool:
        return bool(self.inherited_security_issues) or any(
            issue.severity == "review"
            for issue in (*self.visual_issues, *self.ci_issues, *self.skill_issues)
        )

    def to_dict(self) -> dict[str, Any]:
        decision = "FAIL" if not self.gate_passes else "REVIEW" if self.review_required else "PASS"
        return asdict(self) | {"gate": decision}


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


def reconcile_nested_lockfile_roots(root: str) -> list[str]:
    """Align nested package-lock roots with adjacent branded package.json files."""
    changed: list[str] = []
    for package_file in Path(root).rglob("package.json"):
        lock_file = package_file.with_name("package-lock.json")
        if not lock_file.is_file():
            continue
        try:
            package = json.loads(package_file.read_text(encoding="utf-8"))
            lock = json.loads(lock_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = package.get("name")
        if not isinstance(name, str) or not name:
            continue
        changed_here = lock.get("name") != name
        lock["name"] = name
        root_entry = lock.get("packages", {}).get("")
        if isinstance(root_entry, dict) and root_entry.get("name") != name:
            root_entry["name"] = name
            changed_here = True
        if changed_here:
            lock_file.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
            changed.append(str(lock_file.relative_to(root)))
    return changed


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
    generated_reports = {"UPDATE-REPORT.md", "GATE-REPORT.md", "manifest.json"}
    for path in Path(root).rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(root))
        if path.name in generated_reports or is_immutable_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line) and not _is_allowed(line, rel):
                issues.append(AuditIssue("first-party-brand", rel, f"line {number}: {line.strip()[:180]}"))
    return issues


def audit_branding_fixed_point(
    root: str, rules: BrandingRules | None = None
) -> list[AuditIssue]:
    """Require eligible branded text and paths to be a transform fixed point.

    This deliberately ignores immutable records, package locks, and binary
    assets because those are governed by separate preservation and lock gates.
    Any remaining file or path that would change under the canonical rules is
    deterministic evidence that the candidate was not fully branded.
    """
    rules = rules or BrandingRules()
    issues: list[AuditIssue] = []
    generated = {"UPDATE-REPORT.md", "GATE-REPORT.md", "manifest.json"}
    for path in Path(root).rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(root))
        if path.name in generated or is_immutable_path(rel) or is_locked_path(rel):
            continue
        transformed_path = rules.transform_path(rel)
        if transformed_path != rel:
            issues.append(
                AuditIssue(
                    "brand-path-not-fixed-point",
                    rel,
                    f"canonical path transform would produce {transformed_path!r}",
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rules.transform_text(text) != text:
            issues.append(
                AuditIssue(
                    "brand-text-not-fixed-point",
                    rel,
                    "canonical text transform would still change this file",
                )
            )
    return issues


def audit_brand_symbols(root: str) -> list[AuditIssue]:
    """Block inherited medical-symbol glyphs after the NasTech text transformation."""
    issues: list[AuditIssue] = []
    generated_reports = {"UPDATE-REPORT.md", "GATE-REPORT.md", "manifest.json"}
    for path in Path(root).rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(root))
        if path.name in generated_reports or is_immutable_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for glyph in ("⚕", "☤"):
            if glyph in text:
                issues.append(AuditIssue("inherited-brand-symbol", rel, f"contains source glyph {glyph!r}; expected '𓄃'"))
                break
    return issues


def audit_fts5_trigram_fixtures(root: str) -> list[AuditIssue]:
    """Block branded FTS5 fixtures whose query is not a trigram of their value.

    Branding can change an inserted fixture value while leaving an abbreviated
    assertion literal intact.  These tests then fail only in a container run;
    treat that mismatch as a hard brand-integrity error before publication.
    """
    issues: list[AuditIssue] = []
    insert_re = re.compile(r"INSERT INTO docs VALUES \('([^']+)'\)")
    match_re = re.compile(r"MATCH '([^']{3})'")
    for path in Path(root).rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "fts5" not in text.lower():
            continue
        inserted = set(insert_re.findall(text))
        if not inserted:
            continue
        valid = {word[index:index + 3] for word in inserted for index in range(len(word) - 2)}
        for token in match_re.findall(text):
            if token not in valid:
                rel = str(path.relative_to(root))
                issues.append(AuditIssue(
                    "fts5-trigram-fixture",
                    rel,
                    f"MATCH '{token}' is not a trigram of the branded fixture value(s): {sorted(inserted)}",
                ))
    return issues


_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
)

# Conservative lower bounds for package versions with broadly documented fixes.
_MIN_SAFE_DEPENDENCIES = {"minimist": (1, 2, 6), "tar": (6, 2, 1), "urllib3": (2, 2, 2)}


def _version_tuple(value: str) -> tuple[int, ...] | None:
    numbers = re.findall(r"\d+", value)
    return tuple(int(item) for item in numbers[:3]) if numbers else None


def audit_snapshot_safety(root: str) -> list[AuditIssue]:
    """Check for credentials, MIT compliance, unsafe dependency pins, modes and empty source files."""
    issues: list[AuditIssue] = []
    base = Path(root)
    license_path = base / "LICENSE"
    if not license_path.is_file() or not license_path.read_text(encoding="utf-8", errors="ignore").startswith("MIT License"):
        issues.append(AuditIssue("license-mit", "LICENSE", "repository license must retain the MIT License text"))
    allowed_empty = {".gitkeep", ".keep", "__init__.py"}
    ignored_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff2", ".pdf", ".zip"}
    for path in base.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "node_modules" in path.parts:
            continue
        rel = str(path.relative_to(base))
        if path.suffix.lower() not in ignored_suffixes and path.stat().st_size == 0 and path.name not in allowed_empty:
            issues.append(AuditIssue("unexpected-empty-file", rel, "empty file may indicate incomplete source transformation"))
        if path.suffix == ".sh" and not os.access(path, os.X_OK):
            issues.append(AuditIssue("script-not-executable", rel, "shell scripts must have an executable bit"))
        if path.suffix.lower() in ignored_suffixes:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            issues.append(AuditIssue("credential-signal", rel, "credential-like value detected"))
    dependency_files = list(base.rglob("package-lock.json")) + list(base.rglob("uv.lock"))
    for package_file in dependency_files:
        if "node_modules" in package_file.parts:
            continue
        try:
            content = package_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for dependency, floor in _MIN_SAFE_DEPENDENCIES.items():
            if package_file.name == "uv.lock":
                match = re.search(rf'name = "{re.escape(dependency)}".*?version = "([0-9][0-9.]+)"', content, re.DOTALL)
            else:
                match = re.search(rf'node_modules/{re.escape(dependency)}"\s*:\s*\{{.*?"version"\s*:\s*"([0-9][0-9.]+)"', content, re.DOTALL)
            if match and (version := _version_tuple(match.group(1))) and version < floor:
                issues.append(AuditIssue("dependency-vulnerability-pattern", str(package_file.relative_to(base)), f"{dependency} {match.group(1)} is below the minimum safe version {'.'.join(map(str, floor))}"))
    return issues


def _security_issue_key(issue: AuditIssue, rules: BrandingRules | None = None) -> tuple[str, str]:
    """Return a comparable identity for a scan finding across branded trees."""
    path = rules.transform_path(issue.path) if rules else issue.path
    return issue.code, path


def partition_inherited_security_issues(
    candidate_issues: Iterable[AuditIssue],
    upstream_issues: Iterable[AuditIssue],
    rules: BrandingRules | None = None,
) -> tuple[list[AuditIssue], list[AuditIssue]]:
    """Split candidate findings into new blocks and source-inherited review evidence.

    A scan signal remains visible whenever it is present in the direct source,
    but it does not prevent a clean branded candidate from being reviewed. Any
    new signal introduced by branding, the asset pack, or fork preservation
    remains a hard block.
    """
    source_keys = {_security_issue_key(issue, rules) for issue in upstream_issues}
    blocking: list[AuditIssue] = []
    inherited: list[AuditIssue] = []
    for issue in candidate_issues:
        if _security_issue_key(issue) in source_keys:
            inherited.append(issue)
        else:
            blocking.append(issue)
    return blocking, inherited


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
    reconcile_nested_lockfile_roots(branded_root)
    report.lock_issues = audit_nested_lockfiles(branded_root)
    report.brand_issues = audit_first_party_brand(branded_root)
    report.brand_issues.extend(audit_branding_fixed_point(branded_root))
    report.brand_issues.extend(audit_brand_symbols(branded_root))
    report.brand_issues.extend(audit_fts5_trigram_fixtures(branded_root))
    report.asset_issues = audit_owned_assets(branded_root)
    report.visual_issues = audit_visual_assets(branded_root, upstream_repo)
    report.ci_issues = audit_workflow_security(branded_root)
    report.skill_issues = audit_skill_firewall(branded_root)
    candidate_security = audit_snapshot_safety(branded_root)
    upstream_security = audit_snapshot_safety(upstream_repo)
    report.security_issues, report.inherited_security_issues = partition_inherited_security_issues(
        candidate_security,
        upstream_security,
        BrandingRules(),
    )
    report.freshness_ok = fetch_upstream(upstream_repo, ref) == current
    if captured and captured != current:
        report.ci_issues.append(WorkflowPolicyIssue(
            "upstream-advanced",
            "manifest.json",
            f"snapshot captured {captured}; a newer upstream head {current} is available for the next sync",
            "review",
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
        f"- review required: {'YES' if report.review_required else 'NO'}",
        f"- gate: {report.to_dict()['gate']}", "",
    ]
    for title, issues in (
        ("Brand issues", report.brand_issues),
        ("Nested lock issues", report.lock_issues),
        ("Owned asset issues", report.asset_issues),
        ("Visual asset issues", report.visual_issues),
        ("CI policy issues", report.ci_issues),
        ("Skill firewall issues", report.skill_issues),
        ("Security and snapshot issues", report.security_issues),
        ("Inherited upstream security evidence (review required)", report.inherited_security_issues),
    ):
        lines.extend([f"## {title}", ""])
        lines.extend(["- None"] if not issues else [f"- `{i.code}` `{i.path}` — {i.detail}" for i in issues])
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")


def capability_count() -> int:
    return len(FULL_SYNC_CAPABILITIES)
