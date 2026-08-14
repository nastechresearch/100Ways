"""Small, deterministic static hardening audit for GitHub Actions workflows."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowPolicyIssue:
    code: str
    path: str
    detail: str
    severity: str = "block"


_ACTION_LINE = re.compile(r"^\s*-\s*uses:\s*([^\s#]+)", re.MULTILINE)
_FULL_SHA = re.compile(r"@[0-9a-f]{40}(?:\s|$|#)")


def audit_workflow_security(root: str) -> list[WorkflowPolicyIssue]:
    """Check the policy controls that can be evaluated without executing YAML."""
    base = Path(root)
    workflow_dir = base / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []
    issues: list[WorkflowPolicyIssue] = []
    for workflow in sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml"))):
        rel = str(workflow.relative_to(base))
        text = workflow.read_text(encoding="utf-8")
        if re.search(r"(?m)^\s*pull_request_target\s*:", text):
            issues.append(WorkflowPolicyIssue("unsafe-trigger", rel, "pull_request_target requires a separately reviewed threat model"))
        if re.search(r"(?m)^\s*permissions\s*:\s*write-all\s*$", text):
            issues.append(WorkflowPolicyIssue("broad-token", rel, "permissions: write-all is not allowed"))
        if "secrets: inherit" in text:
            issues.append(WorkflowPolicyIssue("secret-inheritance", rel, "inherit explicit secrets instead of passing all caller secrets", "review"))
        for match in _ACTION_LINE.finditer(text):
            target = match.group(1)
            if target.startswith("./") or target.startswith("docker://"):
                continue
            if "@" not in target or not _FULL_SHA.search(match.group(0)):
                issues.append(WorkflowPolicyIssue("unpinned-action", rel, f"action must use a full commit SHA: {target}"))
    return issues
