"""Commit-threshold monitor for the NasTech full-sync publisher.

The monitor compares the exact upstream SHA recorded in the checked-out
NasTech main branch's ``manifest.json`` with the current upstream head.  It
never publishes anything itself.  It produces deterministic evidence for the
workflow to notify operators below the threshold and to enable the complete
100Ways verification chain once the threshold is reached.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

DEFAULT_THRESHOLD = 50


@dataclass(frozen=True)
class CommitStreamDecision:
    baseline_sha: str
    merged_baseline_sha: str
    candidate_baseline_sha: str
    upstream_sha: str
    pending_commits: int
    threshold: int
    status: str
    trigger_sync: bool
    subjects: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


TELEGRAM_SAFETY_FOOTER = (
    "Safety boundary: 100Ways may only create or update a review PR after every "
    "gate passes; it never merges, tags, releases, or deploys."
)


def format_telegram_status(decision: CommitStreamDecision) -> str:
    """Return a concise, deterministic Telegram status with safe next action."""
    status_line = {
        "current": "Current — no new upstream commits require a sync.",
        "warming": "Warming — verification is held until the commit threshold is reached.",
        "awaiting-review": "Candidate awaiting review — no new sync is started.",
        "threshold-reached": "Threshold reached — the full verification chain is starting.",
    }.get(decision.status, f"State: {decision.status}.")
    if decision.status == "threshold-reached":
        action_line = (
            "Next action: run all gates; #344 may create or update the review PR only on PASS."
        )
    elif decision.status == "warming":
        remaining = max(0, decision.threshold - decision.pending_commits)
        action_line = f"Next action: hold full sync; {remaining} more commit(s) needed."
    else:
        action_line = "Next action: monitor only; no candidate publication is authorized."
    return "\n".join(
        (
            "NasTech commit stream",
            status_line,
            f"Progress: {decision.pending_commits}/{decision.threshold} pending upstream commits.",
            f"Baseline: {decision.baseline_sha[:12]} | Upstream: {decision.upstream_sha[:12]}",
            action_line,
            TELEGRAM_SAFETY_FOOTER,
        )
    )


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def load_baseline_sha(nastech_repo: str | Path) -> str:
    """Return the upstream SHA recorded by the last merged NasTech snapshot."""
    manifest = Path(nastech_repo) / "manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"cannot read merged NasTech manifest: {manifest}") from error
    baseline = data.get("upstream_sha")
    if not isinstance(baseline, str) or len(baseline) < 12:
        raise RuntimeError("merged NasTech manifest has no valid upstream_sha")
    return baseline


def inspect_commit_stream(
    upstream_repo: str | Path,
    nastech_repo: str | Path,
    *,
    candidate_repo: str | Path | None = None,
    threshold: int = DEFAULT_THRESHOLD,
    subject_limit: int = 10,
) -> CommitStreamDecision:
    """Compare merged NasTech state with upstream and make no side effects."""
    if threshold < 1:
        raise ValueError("threshold must be at least 1")
    upstream = Path(upstream_repo)
    merged_baseline = load_baseline_sha(nastech_repo)
    candidate_baseline = ""
    if candidate_repo is not None and (Path(candidate_repo) / "manifest.json").is_file():
        candidate_baseline = load_baseline_sha(candidate_repo)
    # Thresholds measure what NasTech main is missing.  An open candidate is
    # audit context only; it must never hide a 50+ commit backlog from main.
    baseline = merged_baseline
    upstream_sha = _git(upstream, "rev-parse", "HEAD")
    ancestry = subprocess.run(
        ["git", "-C", str(upstream), "merge-base", "--is-ancestor", baseline, upstream_sha],
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0:
        raise RuntimeError(
            f"effective NasTech baseline {baseline} is not an ancestor of upstream {upstream_sha}; "
            "manual reconciliation is required before threshold evaluation"
        )
    count = int(_git(upstream, "rev-list", "--count", f"{baseline}..{upstream_sha}") or "0")
    subjects = _git(
        upstream,
        "log",
        "--format=%h %s",
        f"-n{subject_limit}",
        f"{baseline}..{upstream_sha}",
    ).splitlines()
    if count == 0 and candidate_baseline:
        status = "awaiting-review"
    elif count == 0:
        status = "current"
    elif count < threshold:
        status = "warming"
    else:
        status = "threshold-reached"
    return CommitStreamDecision(
        baseline_sha=baseline,
        merged_baseline_sha=merged_baseline,
        candidate_baseline_sha=candidate_baseline,
        upstream_sha=upstream_sha,
        pending_commits=count,
        threshold=threshold,
        status=status,
        trigger_sync=count >= threshold,
        subjects=subjects,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect upstream commit backlog for NasTech sync")
    parser.add_argument("--upstream-repo", required=True)
    parser.add_argument("--nastech-repo", required=True)
    parser.add_argument(
        "--candidate-repo", help="Optional open-candidate checkout recorded for audit context"
    )
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD)
    parser.add_argument("--output", required=True, help="Path to JSON decision output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decision = inspect_commit_stream(
        args.upstream_repo,
        args.nastech_repo,
        candidate_repo=args.candidate_repo,
        threshold=args.threshold,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decision.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
