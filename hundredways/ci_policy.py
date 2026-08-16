"""Deterministic static security and publication-policy audit for workflows."""

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


_ACTION_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
_FULL_SHA = re.compile(r"@[0-9a-f]{40}(?:\s|$|#)")
_PUBLICATION_WORKFLOW = "stage-update-pr.yml"
_MANUAL_PUBLICATION_WORKFLOWS = {"release-promotion.yml", "release-deploy.yml"}

# The system is deliberately PR-only. These expressions cover GitHub CLI and
# common deployment actions without attempting to interpret arbitrary shell.
_FORBIDDEN_WORKFLOW_ACTIONS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "auto-release",
        re.compile(r"\bgh\s+release\s+(?:create|upload|edit)\b", re.IGNORECASE),
        "GitHub releases are prohibited; publication is PR-only",
    ),
    (
        "auto-tag",
        re.compile(r"\bgit\s+tag\b|\bgh\s+release\b", re.IGNORECASE),
        "tag creation is prohibited; publication is PR-only",
    ),
    (
        "auto-deploy",
        re.compile(
            r"\b(?:kubectl|helm|terraform\s+apply|aws\s+deploy|gcloud\s+run\s+deploy|vercel\s+--prod)\b",
            re.IGNORECASE,
        ),
        "deployment commands are prohibited",
    ),
    (
        "issue-notification",
        re.compile(r"\bgh\s+issue\s+(?:create|edit|close)\b", re.IGNORECASE),
        "GitHub issues are not a notification channel; use Telegram only",
    ),
    (
        "autonomous-dispatch",
        re.compile(r"\bgh\s+workflow\s+run\b", re.IGNORECASE),
        "workflows must not dispatch publication workflows autonomously",
    ),
    (
        "self-approval",
        re.compile(r"\bgh\s+pr\s+review\b[^\n]*(?:--approve|-a\b)", re.IGNORECASE),
        "workflows must not approve their own pull requests",
    ),
)
_PUBLISH_COMMAND = re.compile(
    r"\b(?:gh\s+pr\s+(?:create|edit)|git\s+push)\b", re.IGNORECASE
)


def audit_workflow_security(
    root: str, *, enforce_publication_policy: bool = True
) -> list[WorkflowPolicyIssue]:
    """Check static workflow security and, for 100Ways, PR-only publication.

    The engine's own workflows are fail-closed for release, tag, deployment,
    issue, dispatch, self-approval, and unauthorized publication commands.
    Candidate snapshots retain their inherited workflow inventory as review
    evidence; their source workflows are not executable by this engine.
    """
    base = Path(root)
    workflow_dir = base / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return []

    issues: list[WorkflowPolicyIssue] = []
    for workflow in sorted((*workflow_dir.glob("*.yml"), *workflow_dir.glob("*.yaml"))):
        rel = str(workflow.relative_to(base))
        text = workflow.read_text(encoding="utf-8")

        if re.search(r"(?m)^\s*pull_request_target\s*:", text):
            issues.append(
                WorkflowPolicyIssue(
                    "unsafe-trigger",
                    rel,
                    "pull_request_target requires a separately reviewed threat model",
                )
            )
        if re.search(r"(?m)^\s*permissions\s*:\s*write-all\s*$", text):
            issues.append(
                WorkflowPolicyIssue("broad-token", rel, "permissions: write-all is not allowed")
            )
        if "secrets: inherit" in text:
            issues.append(
                WorkflowPolicyIssue(
                    "secret-inheritance",
                    rel,
                    "inherit explicit secrets instead of passing all caller secrets",
                    "review",
                )
            )

        for match in _ACTION_LINE.finditer(text):
            target = match.group(1)
            if target.startswith("./") or target.startswith("docker://"):
                continue
            if "@" not in target or not _FULL_SHA.search(match.group(0)):
                issues.append(
                    WorkflowPolicyIssue(
                        "unpinned-action",
                        rel,
                        f"action must use a full commit SHA: {target}",
                    )
                )

        if enforce_publication_policy:
            is_manual_publication = workflow.name in _MANUAL_PUBLICATION_WORKFLOWS
            if is_manual_publication:
                has_manual_trigger = re.search(r"(?m)^\s*workflow_dispatch\s*:", text)
                has_forbidden_trigger = re.search(
                    r"(?m)^\s*(?:push|pull_request|schedule)\s*:", text
                )
                has_confirmation = "confirmation" in text and "PUBLISH" in text
                if not has_manual_trigger or has_forbidden_trigger or not has_confirmation:
                    issues.append(
                        WorkflowPolicyIssue(
                            "unguarded-manual-publication",
                            rel,
                            "manual publication workflows require workflow_dispatch only "
                            "and a typed PUBLISH confirmation",
                        )
                    )
            else:
                for code, pattern, detail in _FORBIDDEN_WORKFLOW_ACTIONS:
                    if pattern.search(text):
                        issues.append(WorkflowPolicyIssue(code, rel, detail))

                if workflow.name != _PUBLICATION_WORKFLOW and _PUBLISH_COMMAND.search(text):
                    issues.append(
                        WorkflowPolicyIssue(
                            "unauthorized-publication-path",
                            rel,
                            "candidate PR creation and push are reserved for "
                            f"{_PUBLICATION_WORKFLOW} (#344)",
                        )
                    )

    return issues
