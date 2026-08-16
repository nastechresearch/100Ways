"""Release-parity evidence for exact Hermes tags and branded NasTech releases."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .operation_safety import bound_telegram_text

_RELEASE_TAG = re.compile(r"^v20\d{2}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class HermesRelease:
    """A release tag resolved to its immutable upstream commit target."""

    tag: str
    target_sha: str
    url: str = ""
    published_at: str = ""


@dataclass(frozen=True)
class ReleaseParityItem:
    """One exact upstream release that lacks a NasTech branded counterpart."""

    tag: str
    upstream_sha: str
    upstream_url: str
    published_at: str
    status: str = "pending-branded-release"


@dataclass(frozen=True)
class ReleaseParityReport:
    """Read-only release-parity evidence; it has no publication side effects."""

    upstream_releases: tuple[HermesRelease, ...]
    nastech_tags: tuple[str, ...]
    backlog: tuple[ReleaseParityItem, ...]

    @property
    def latest_pending(self) -> ReleaseParityItem | None:
        return self.backlog[-1] if self.backlog else None

    def to_dict(self) -> dict[str, Any]:
        latest = self.latest_pending
        return {
            "gate": "PENDING" if self.backlog else "CURRENT",
            "pending_count": len(self.backlog),
            "latest_pending_tag": latest.tag if latest else "",
            "latest_pending_sha": latest.upstream_sha if latest else "",
            "upstream_releases": [asdict(release) for release in self.upstream_releases],
            "nastech_tags": list(self.nastech_tags),
            "backlog": [asdict(item) for item in self.backlog],
            "prohibited_automatic_actions": ["merge", "tag", "release", "deploy"],
        }


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag.removeprefix("v").split("."))


def build_release_parity_report(
    upstream_releases: Iterable[HermesRelease], nastech_tags: Iterable[str]
) -> ReleaseParityReport:
    """Compare exact upstream release tags with existing NasTech tag names."""
    accepted: list[HermesRelease] = []
    for release in upstream_releases:
        if not _RELEASE_TAG.fullmatch(release.tag):
            continue
        if not _SHA.fullmatch(release.target_sha):
            raise ValueError(f"release {release.tag} has no immutable target SHA")
        accepted.append(release)
    upstream = tuple(sorted(accepted, key=lambda release: _version_key(release.tag)))
    nastech = tuple(
        sorted(
            {tag for tag in nastech_tags if _RELEASE_TAG.fullmatch(tag)},
            key=_version_key,
        )
    )
    known_tags = set(nastech)
    backlog = tuple(
        ReleaseParityItem(release.tag, release.target_sha, release.url, release.published_at)
        for release in upstream
        if release.tag not in known_tags
    )
    return ReleaseParityReport(upstream, nastech, backlog)


def format_release_parity_status(report: ReleaseParityReport) -> str:
    """Format a Telegram-safe read-only status; it never authorizes publication."""
    latest = report.latest_pending
    if latest is None:
        body = (
            "NasTech release parity: current — every tracked Hermes release "
            "has a matching NasTech tag."
        )
    else:
        body = (
            f"NasTech release parity: {len(report.backlog)} pending release(s).\n"
            f"Latest pending: {latest.tag} ({latest.upstream_sha[:12]}).\n"
            "Next action: exact-tag verification and human-reviewed NasTech release cycle; "
            "no tag, release, or deployment is automatic."
        )
    return bound_telegram_text(body)


def _tag_targets(path: str | Path) -> dict[str, str]:
    """Read git ls-remote --tags output, preferring peeled annotated tag targets."""
    targets: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        sha, _, ref = line.partition("\t")
        if not sha or not ref.startswith("refs/tags/"):
            continue
        raw = ref.removeprefix("refs/tags/")
        tag = raw.removesuffix("^{}")
        if raw.endswith("^{}") or tag not in targets:
            targets[tag] = sha
    return targets


def _github_releases(path: str | Path, tag_targets: dict[str, str]) -> tuple[HermesRelease, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("GitHub releases payload must be a list")
    releases: list[HermesRelease] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag = item.get("tag_name")
        if not isinstance(tag, str) or tag not in tag_targets:
            continue
        releases.append(
            HermesRelease(
                tag=tag,
                target_sha=tag_targets[tag],
                url=str(item.get("html_url", "")),
                published_at=str(item.get("published_at", "")),
            )
        )
    return tuple(releases)


def main(argv: Sequence[str] | None = None) -> int:
    """Build a read-only parity report from GitHub release JSON and tag refs."""
    parser = argparse.ArgumentParser(description="Build Hermes-to-NasTech release parity evidence")
    parser.add_argument("--hermes-releases", required=True)
    parser.add_argument("--hermes-tag-refs", required=True)
    parser.add_argument("--nastech-tag-refs", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    upstream_targets = _tag_targets(args.hermes_tag_refs)
    nastech_targets = _tag_targets(args.nastech_tag_refs)
    report = build_release_parity_report(
        _github_releases(args.hermes_releases, upstream_targets), nastech_targets
    )
    write_release_parity_report(args.output, report)
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def write_release_parity_report(path: str | Path, report: ReleaseParityReport) -> Path:
    """Write the read-only parity report for CI artifacts and Telegram summaries."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"
    target.write_text(content, encoding="utf-8")
    return target


if __name__ == "__main__":
    raise SystemExit(main())
