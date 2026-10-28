"""Professional release-summary generation for verified NasTech-Agent updates."""
from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .rules import BrandingRules, transform_strict_metadata_text


def _git(repo: str | Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _category(subject: str) -> str:
    prefix = subject.lower().split("(", 1)[0].split(":", 1)[0].strip()
    if prefix in {"feat", "feature"}:
        return "New capabilities"
    if prefix in {"fix", "bugfix", "hotfix"}:
        return "Reliability and fixes"
    if prefix in {"security", "sec"}:
        return "Security and hardening"
    if prefix in {"perf", "performance"}:
        return "Performance"
    if prefix in {"docs", "doc"}:
        return "Documentation"
    return "Improvements"


def collect_changes(upstream_repo: str | Path, baseline_sha: str, upstream_sha: str) -> list[str]:
    if not baseline_sha or baseline_sha == upstream_sha:
        return []
    subjects = _git(upstream_repo, "log", "--format=%s", f"{baseline_sha}..{upstream_sha}").splitlines()
    rules = BrandingRules()
    return [transform_strict_metadata_text(subject, rules).strip() for subject in subjects if subject.strip()]


def write_sync_summary(
    path: str | Path,
    *,
    upstream_repo: str | Path,
    baseline_sha: str,
    upstream_sha: str,
    files_changed: int,
    changed_areas: dict[str, int],
    verification: Iterable[tuple[str, str]],
    commit_subjects: Iterable[str] | None = None,
) -> None:
    """Write a review-ready feature summary without transformation terminology."""
    if commit_subjects is None:
        changes = collect_changes(upstream_repo, baseline_sha, upstream_sha)
    else:
        rules = BrandingRules()
        changes = [transform_strict_metadata_text(subject, rules).strip() for subject in commit_subjects if subject.strip()]
    grouped: dict[str, list[str]] = defaultdict(list)
    for subject in changes:
        grouped[_category(subject)].append(subject)
    lines = [
        "# NasTech-Agent Update Summary",
        "",
        "> Powered by NousResearch",
        "",
        "This verified NasTech-Agent update incorporates the newest confirmed improvements from its open-source foundation. "
        "The summary below focuses on delivered functionality, reliability, and operational impact.",
        "",
        "## Update scope",
        "",
        f"- **Changes incorporated:** {len(changes)} commits affecting {files_changed} files.",
        f"- **Source revision:** `{upstream_sha[:12]}`.",
        f"- **Previous source revision:** `{baseline_sha[:12] if baseline_sha else 'initial baseline'}`.",
        "",
    ]
    if changed_areas:
        rules = BrandingRules()
        lines.extend(["## Technical coverage", ""])
        lines.extend(
            f"- **{transform_strict_metadata_text(area, rules)}/:** {count} changed files."
            for area, count in sorted(changed_areas.items())
        )
        lines.append("")
    if grouped:
        lines.extend(["## Delivered improvements", ""])
        for category in ("New capabilities", "Reliability and fixes", "Security and hardening", "Performance", "Documentation", "Improvements"):
            items = grouped.get(category, [])
            if not items:
                continue
            lines.extend([f"### {category}", ""])
            for item in items[:12]:
                lines.append(f"- {item}")
            if len(items) > 12:
                lines.append(f"- {len(items) - 12} additional {category.lower()} updates are included in this verified snapshot.")
            lines.append("")
    else:
        lines.extend(["## Delivered improvements", "", "- NasTech-Agent is already aligned with the recorded source revision; no additional commits were pending.", ""])
    lines.extend(["## Verification evidence", ""])
    for title, outcome in verification:
        lines.append(f"- **{title}:** {outcome}")
    lines.extend(["", "This candidate is prepared for review only. No merge, release, or deployment is performed by the verification workflow.", ""])
    Path(path).write_text("\n".join(lines), encoding="utf-8")
