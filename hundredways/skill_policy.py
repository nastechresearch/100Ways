"""Fail-closed skill-document firewall for branded candidate trees."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ALLOWED_SKILL_ROOTS = ("skills/", "optional-skills/", "plugins/")
_DANGEROUS_INSTRUCTIONS = (
    re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba)?sh\b", re.IGNORECASE),
    re.compile(r"\brm\s+-rf\s+/(?:\s|$)", re.IGNORECASE),
    re.compile(r"\b(?:gh|git)\s+(?:release|tag)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class SkillPolicyIssue:
    code: str
    path: str
    detail: str
    severity: str = "block"


def audit_skill_firewall(root: str) -> list[SkillPolicyIssue]:
    """Audit every ``SKILL.md`` as untrusted source content before publication.

    The explicit path-root allowlist is intentionally narrow: enabled skills,
    bundled plugins, and optional (not auto-enabled) skills. New roots, symlink
    indirection, executable Markdown, and direct remote-code execution patterns
    all stop the candidate before #344.
    """
    base = Path(root)
    issues: list[SkillPolicyIssue] = []
    for path in sorted(base.rglob("SKILL.md")):
        rel = path.relative_to(base).as_posix()
        if not rel.startswith(_ALLOWED_SKILL_ROOTS):
            issues.append(
                SkillPolicyIssue(
                    "skill-root-not-allowlisted",
                    rel,
                    "SKILL.md is outside the approved skills/, optional-skills/, or plugins/ roots",
                )
            )
            continue
        if path.is_symlink():
            issues.append(
                SkillPolicyIssue(
                    "skill-symlink", rel, "skill documents must not resolve through symlinks"
                )
            )
            continue
        if path.stat().st_mode & 0o111:
            severity = "review" if rel.startswith("optional-skills/") else "block"
            issues.append(
                SkillPolicyIssue(
                    "skill-executable",
                    rel,
                    "SKILL.md must be non-executable documentation",
                    severity,
                )
            )
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append(SkillPolicyIssue("skill-not-utf8", rel, "SKILL.md must be UTF-8 text"))
            continue
        if not text.lstrip().startswith("---"):
            issues.append(
                SkillPolicyIssue(
                    "skill-metadata-missing", rel, "SKILL.md must begin with YAML metadata"
                )
            )
        for pattern in _DANGEROUS_INSTRUCTIONS:
            if pattern.search(text):
                issues.append(
                    SkillPolicyIssue(
                        "skill-dangerous-instruction",
                        rel,
                        "contains a prohibited remote-code, destructive-root, release, "
                        "or tag instruction",
                        "review",
                    )
                )
                break
    return issues
