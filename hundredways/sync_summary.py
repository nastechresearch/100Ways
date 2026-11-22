"""Professional release-summary generation for verified NasTech-Agent updates."""
from __future__ import annotations

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


def change_category(subject: str) -> str:
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
    subjects = _git(
        upstream_repo, "log", "--format=%s", f"{baseline_sha}..{upstream_sha}"
    ).splitlines()
    rules = BrandingRules()
    return [
        transform_strict_metadata_text(subject, rules).strip()
        for subject in subjects
        if subject.strip()
    ]


def _area_rows(changed_areas: dict[str, int], rules: BrandingRules) -> list[str]:
    rows = [
        "| Area | Changed files |",
        "|---|---:|",
    ]
    for area, count in sorted(changed_areas.items(), key=lambda item: (-item[1], item[0])):
        label = transform_strict_metadata_text(area, rules).strip()
        rows.append(f"| `{label}` | {count} |")
    rows.append(f"| **Total** | **{sum(changed_areas.values())}** |")
    return rows


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
    """Write a review-ready, structured summary without transformation terminology."""
    if commit_subjects is None:
        changes = collect_changes(upstream_repo, baseline_sha, upstream_sha)
    else:
        rules = BrandingRules()
        changes = [
            transform_strict_metadata_text(subject, rules).strip()
            for subject in commit_subjects
            if subject.strip()
        ]

    grouped: dict[str, list[str]] = defaultdict(list)
    for subject in changes:
        grouped[change_category(subject)].append(subject)

    rules = BrandingRules()
    previous = baseline_sha[:12] if baseline_sha else "initial baseline"
    current = upstream_sha[:12]

    lines: list[str] = [
        "# NasTech-Agent Update Summary",
        "",
        "This verified update incorporates the latest confirmed changes from the "
        "source foundation. The summary below is organized for review and focuses on "
        "delivered functionality, reliability, and operational impact.",
        "",
        "## Scope",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Changes incorporated | {len(changes)} commits |",
        f"| Files affected | {files_changed} |",
        f"| Source revision | `{current}` |",
        f"| Previous source revision | `{previous}` |",
        "",
    ]

    if changed_areas:
        lines.extend(["## Technical coverage", ""])
        lines.extend(_area_rows(changed_areas, rules))
        lines.append("")

    lines.extend(["## Delivered improvements", ""])
    if grouped:
        for category in (
            "New capabilities",
            "Reliability and fixes",
            "Security and hardening",
            "Performance",
            "Documentation",
            "Improvements",
        ):
            items = grouped.get(category, [])
            if not items:
                continue
            lines.extend([f"### {category}", ""])
            lines.extend([f"- {item}" for item in items[:12]])
            if len(items) > 12:
                remaining = len(items) - 12
                lines.append(
                    f"- *{remaining} additional {category.lower()} updates included "
                    "in this verified snapshot.*"
                )
            lines.append("")
    else:
        lines.extend(
            [
                "- NasTech-Agent is already aligned with the recorded source revision; "
                "no additional commits were pending.",
                "",
            ]
        )

    if changes:
        lines.extend(["## Included changes", ""])
        lines.extend(["| # | Commit message | Category |", "|---:|---|---|"])
        for index, subject in enumerate(changes, start=1):
            safe_subject = subject.replace("|", "\\|")
            lines.append(f"| {index} | {safe_subject} | {change_category(subject)} |")
        lines.append("")

    lines.extend(["## Verification evidence", ""])
    lines.extend(["| Check | Result |", "|---|---|"])
    for title, outcome in verification:
        safe_title = str(title).replace("|", "\\|")
        safe_outcome = str(outcome).replace("|", "\\|")
        lines.append(f"| {safe_title} | {safe_outcome} |")
    lines.extend(
        [
            "",
            "> This candidate is prepared for review only. No merge, release, or deployment "
            "is performed by the verification workflow.",
            "",
        ]
    )
    Path(path).write_text("\n".join(lines), encoding="utf-8")
