"""Markdown report generation.

Turns a verify or gap result into a human-readable report that 100Ways keeps
in ``reports/`` (the "keep information and generate mds" requirement).  Every
report is bilingual-free: labels use the requested locale, and the body lists
the error codes, gaps, extras with explanations, behind/ahead, and violations.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass

from .analyzer import GapReport
from .codes import code_for, summarize
from .i18n import t
from .verify import VerifyReport


@dataclass
class ReportSection:
    title: str
    lines: list[str]


def _verify_lines(report: VerifyReport, lang: str) -> list[str]:
    lines = [f"- {t(lang, 'gate')}"]
    lines.append(f"- `{report.summary()}`")
    return lines


def _gap_lines(report: GapReport, lang: str, repo: str, hermes_commit: str = "") -> list[str]:
    lines = [f"`{report.summary}`"]
    if hermes_commit:
        lines.append(f"- live upstream: `{report.upstream_commit[:12]}`  local Hermes: `{hermes_commit[:12]}`")
    lines.append(f"- nastech HEAD: `{report.nastech_commit[:12]}`")
    lines.append(f"- codes: `{summarize(list(report.code_counts().values() and [c for e in report.entries for c in [e.code]]))}`")
    counts = report.code_counts()
    lines.append("- counts: " + "  ".join(f"`{code_for(c).name}={n}`" for c, n in sorted(counts.items(), reverse=True)))
    missing = report.upstream_only()
    if missing:
        lines.append(f"\n### error 404 - missing (must port)  ({len(missing)})")
        for e in missing[:50]:
            lines.append(f"- `{e.path}`")
        if len(missing) > 50:
            lines.append(f"- ... and {len(missing) - 50} more")
    violations = report.violations()
    if violations:
        lines.append(f"\n### error 82 - brand violations  ({len(violations)})")
        for e in violations[:50]:
            lines.append(f"- `{e.path}`: {', '.join(e.brand_violations)}")
    changed = report.changed()
    if changed:
        lines.append(f"\n### error 83 - drift  ({len(changed)})")
        for e in changed[:30]:
            lines.append(f"- `{e.path}`  +{e.added_lines}/-{e.deleted_lines} lines")
    extras = report.nastech_only()
    if extras:
        lines.append(f"\n### error 84 - extras with explanations  ({len(extras)})")
        for e in extras[:50]:
            lines.append(f"- `{e.path}`  - {e.explanation}")
        if len(extras) > 50:
            lines.append(f"- ... and {len(extras) - 50} more")
    assets = report.assets()
    if assets:
        lines.append(f"\n### image assets  ({len(assets)})")
        for e in assets[:20]:
            lines.append(f"- `{e.path}`  ({e.upstream_type})")
    return lines


def render_report(
    report: VerifyReport | GapReport,
    lang: str = "en",
    repo: str = "",
    hermes_commit: str = "",
) -> str:
    """Render a markdown report for a verify or gap result."""
    title = t(lang, "title").split(" - ")[0]
    lines = [
        f"# {title} report",
        "",
        f"generated: {_dt.datetime.now().isoformat(timespec='seconds')} UTC",
    ]
    if repo:
        lines.append(f"repo: `{repo}`")
    lines.append("")
    if isinstance(report, VerifyReport):
        lines.extend(_verify_lines(report, lang))
    else:
        lines.extend(_gap_lines(report, lang, repo, hermes_commit))
    lines.append("")
    lines.append("---")
    lines.append("_100Ways report - keep this file, it is the audit trail._")
    return "\n".join(lines)


def write_report(report: VerifyReport | GapReport, reports_dir: str, lang: str = "en", repo: str = "", hermes_commit: str = "") -> str:
    """Write the report under ``reports_dir`` and return its path."""
    os.makedirs(reports_dir, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    path = os.path.join(reports_dir, f"report-{stamp}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_report(report, lang, repo, hermes_commit))
    return path
