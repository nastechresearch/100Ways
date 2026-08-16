"""Manual-release readiness evidence for tagged Hermes-to-NasTech parity."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


_RELEASE_TAG = re.compile(r"^v20\d{2}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReleaseReadinessIssue:
    """A condition that prevents a human-approved release promotion."""

    code: str
    detail: str


@dataclass(frozen=True)
class ReleaseReadiness:
    """A non-executing release decision bound to one upstream release tag."""

    upstream_tag: str
    upstream_tag_sha: str
    candidate_upstream_sha: str
    branded_merge_sha: str
    existing_nastech_tag_sha: str
    issues: tuple[ReleaseReadinessIssue, ...]

    @property
    def ready(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": "READY" if self.ready else "BLOCKED",
            "upstream_tag": self.upstream_tag,
            "upstream_tag_sha": self.upstream_tag_sha,
            "candidate_upstream_sha": self.candidate_upstream_sha,
            "branded_merge_sha": self.branded_merge_sha,
            "existing_nastech_tag_sha": self.existing_nastech_tag_sha,
            "manual_actions_required": [
                "human-review-of-merged-candidate",
                "human-workflow-dispatch",
                "human-release-note-approval",
            ],
            "prohibited_automatic_actions": ["merge", "tag", "release", "deploy"],
            "issues": [asdict(issue) for issue in self.issues],
        }


def assess_release_readiness(
    *,
    upstream_tag: str,
    upstream_tag_sha: str,
    manifest: dict[str, Any],
    branded_merge_sha: str,
    existing_nastech_tag_sha: str = "",
) -> ReleaseReadiness:
    """Assess a proposed manual release without mutating either repository."""
    issues: list[ReleaseReadinessIssue] = []
    candidate_upstream_sha = str(manifest.get("upstream_sha", ""))
    provenance = manifest.get("source_provenance")
    provenance = provenance if isinstance(provenance, dict) else {}

    if not _RELEASE_TAG.fullmatch(upstream_tag):
        issues.append(ReleaseReadinessIssue("release-tag-format", "upstream tag is not a supported calendar version"))
    if not _SHA.fullmatch(upstream_tag_sha):
        issues.append(ReleaseReadinessIssue("release-tag-sha", "upstream tag target must be a full commit SHA"))
    if candidate_upstream_sha != upstream_tag_sha:
        issues.append(
            ReleaseReadinessIssue(
                "release-source-mismatch",
                "candidate source SHA does not match the selected upstream release tag",
            )
        )
    if provenance.get("acquisition") != "fresh-direct-clone":
        issues.append(
            ReleaseReadinessIssue(
                "release-provenance",
                "candidate is not bound to a fresh direct Hermes acquisition",
            )
        )
    if not _SHA.fullmatch(branded_merge_sha):
        issues.append(
            ReleaseReadinessIssue(
                "branded-merge-sha",
                "manual promotion must bind to a full branded NasTech merge SHA",
            )
        )
    if existing_nastech_tag_sha:
        issues.append(
            ReleaseReadinessIssue(
                "nastech-tag-exists",
                "the selected version already has a NasTech tag and cannot be recreated",
            )
        )

    return ReleaseReadiness(
        upstream_tag=upstream_tag,
        upstream_tag_sha=upstream_tag_sha,
        candidate_upstream_sha=candidate_upstream_sha,
        branded_merge_sha=branded_merge_sha,
        existing_nastech_tag_sha=existing_nastech_tag_sha,
        issues=tuple(issues),
    )
