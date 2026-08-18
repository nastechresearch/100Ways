"""Deterministic evidence helpers for prompt-resume compatibility gates."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PromptResumeEvidence:
    """Byte-level evidence from a first-build versus fresh-process resume."""

    passed: bool
    first_digest: str
    resumed_digest: str
    first_difference: int | None
    classification: str
    first_context: str
    resumed_context: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _first_difference(left: str, right: str) -> int | None:
    left_bytes = left.encode("utf-8")
    right_bytes = right.encode("utf-8")
    for index, (left_byte, right_byte) in enumerate(zip(left_bytes, right_bytes)):
        if left_byte != right_byte:
            return index
    if len(left_bytes) != len(right_bytes):
        return min(len(left_bytes), len(right_bytes))
    return None


def classify_prompt_divergence(first: str, resumed: str, offset: int | None) -> str:
    """Classify the earliest divergence without claiming semantic certainty."""
    if offset is None:
        return "identical"
    window = (first + "\n" + resumed)[max(0, offset - 512) : offset + 512].lower()
    workspace_tokens = ("git status", "git branch", "recent commits", "workspace")
    if any(token in window for token in workspace_tokens):
        return "workspace-git-context"
    if "plugin context:" in window or "plugin-section" in window:
        return "plugin-section"
    if any(token in window for token in ("memory", "user.md", "profile")):
        return "memory-profile"
    if any(token in window for token in ("toolset", "available tools", "function")):
        return "toolset"
    if "conversation started:" in window or "session id:" in window:
        return "session-runtime"
    return "unknown"


def compare_prompt_resume(
    first: str, resumed: str, *, context_radius: int = 160
) -> PromptResumeEvidence:
    """Compare first-build and resumed prompt bytes and retain bounded evidence."""
    offset = _first_difference(first, resumed)
    if offset is None:
        return PromptResumeEvidence(
            passed=True,
            first_digest=_digest(first),
            resumed_digest=_digest(resumed),
            first_difference=None,
            classification="identical",
            first_context="",
            resumed_context="",
        )
    start = max(0, offset - context_radius)
    end = offset + context_radius
    return PromptResumeEvidence(
        passed=False,
        first_digest=_digest(first),
        resumed_digest=_digest(resumed),
        first_difference=offset,
        classification=classify_prompt_divergence(first, resumed, offset),
        first_context=first[start:end],
        resumed_context=resumed[start:end],
    )
