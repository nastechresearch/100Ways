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
_PUBLICATION_WORKFLOWS: dict[str, tuple[str, ...]] = {
    # The candidate path from #344.  It force-pushes the machine-generated
    # candidate branch by design, so no rule here forbids that.
    "stage-update-pr.yml": ("workflow_call",),
    # The chat path: a poll window whose /apply verb runs the engine suite and
    # then pushes one branch and opens one PR against the dispatched ref.  It is
    # listed rather than left to hide inside hundredways/telegram_transport.py,
    # where this static audit could not see it.  Dispatch-only and merge-free.
    "stage-telegram-window.yml": ("workflow_dispatch",),
}
_MERGE_IN_A_PUBLICATION_PATH = re.compile(
    r"\bgh\s+pr\s+merge\b|--admin\b", re.IGNORECASE
)

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

# A third-party source is pinned only when the clone names the revision it
# consumes.  A bare ``git clone <url>`` follows whatever that remote's default
# branch points at when the run happens, so anyone with write access to the
# remote can change the revision this CI consumes without a line changing here.
_CLONE_STATEMENT = re.compile(r"git(?:\s+-c\s+\S+)?\s+clone\b[^\n]*")
_CLONE_REF = re.compile(r"--branch\b|--revision\b|--detach\b")
_DETACHED_CHECKOUT = re.compile(r"\bcheckout\b[^\n]*--detach\b")
# The fork's own repository is not a third party: its branches are produced by
# this engine's own publication workflow, and the wrappers read the merged
# baseline from ``main`` and the open candidate from ``100WAYS`` on purpose.
_SELF_REPOSITORY = "nastechresearch/nastech-agent"


def _joined_shell(text: str) -> str:
    """Join shell line-continuations and drop YAML comments.

    A clone URL routinely sits on the line after ``git clone ... \\``, so the
    statement has to be reassembled before it can be judged.
    """
    joined: list[str] = []
    pending = ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        joined.append(pending + line)
        pending = ""
    if pending:
        joined.append(pending)
    return "\n".join(joined)


def _triggers(text: str) -> set[str]:
    """Return the events a workflow can be started by, read from its ``on:`` block.

    A publication path is only safe while something has to press the button, so
    the trigger set is part of what the audit pins down rather than a detail of
    the file's prose.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^on\s*:", line):
            start = index
            break
    if start is None:
        return set()
    found: set[str] = set()
    for line in lines[start + 1 :]:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            break
        if line.lstrip().startswith("#"):
            continue
        if indent != 2:
            continue
        listed = re.match(r"\s*-\s*([A-Za-z_][\w-]*)", line)
        if listed:
            found.add(listed.group(1))
            continue
        named = re.match(r"\s*([A-Za-z_][\w-]*)\s*:", line)
        if named:
            found.add(named.group(1))
    return found


def audit_workflow_security(
    root: str, *, enforce_publication_policy: bool = True
) -> list[WorkflowPolicyIssue]:
    """Check static workflow security and, for 100Ways, PR-only publication.

    The engine's own workflows are fail-closed for release, tag, deployment,
    issue, dispatch, self-approval, and unauthorized publication commands.
    Pushing and opening a pull request is confined to the named paths in
    ``_PUBLICATION_WORKFLOWS``, each of which may only start from its listed
    events and may never merge what it opened.  Candidate snapshots retain their
    inherited workflow inventory as review evidence; their source workflows are
    not executable by this engine.
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

        # Supply chain: a cloned third-party repository must name the revision
        # it consumes.  Detaching onto an explicit commit counts (upstream's
        # plugin-catalog-ci clones a catalog entry then ``checkout --detach``).
        shell = _joined_shell(text)
        detached = bool(_DETACHED_CHECKOUT.search(shell))
        for statement in _CLONE_STATEMENT.findall(shell):
            if _SELF_REPOSITORY in statement:
                continue
            if _CLONE_REF.search(statement) or detached:
                continue
            issues.append(
                WorkflowPolicyIssue(
                    "unpinned-source",
                    rel,
                    "external git clone must name its revision "
                    f"(git clone --branch/--detach, or a checkout --detach): {statement.strip()}",
                )
            )

        if enforce_publication_policy:
            for code, pattern, detail in _FORBIDDEN_WORKFLOW_ACTIONS:
                if pattern.search(text):
                    issues.append(WorkflowPolicyIssue(code, rel, detail))

            if workflow.name not in _PUBLICATION_WORKFLOWS and _PUBLISH_COMMAND.search(
                text
            ):
                issues.append(
                    WorkflowPolicyIssue(
                        "unauthorized-publication-path",
                        rel,
                        "candidate PR creation and push are reserved for "
                        f"{', '.join(sorted(_PUBLICATION_WORKFLOWS))} (#344)",
                    )
                )
            if workflow.name in _PUBLICATION_WORKFLOWS:
                allowed = _PUBLICATION_WORKFLOWS[workflow.name]
                for trigger in sorted(_triggers(text) - set(allowed)):
                    issues.append(
                        WorkflowPolicyIssue(
                            "publication-trigger-not-allowed",
                            rel,
                            f"{workflow.name} publishes, so it may not start from "
                            f"'{trigger}'; allowed: {', '.join(allowed)}",
                        )
                    )
                if _MERGE_IN_A_PUBLICATION_PATH.search(text):
                    issues.append(
                        WorkflowPolicyIssue(
                            "publication-path-may-not-merge",
                            rel,
                            "a publication path may open a pull request but never "
                            "merge one, with or without --admin",
                        )
                    )

    return issues
