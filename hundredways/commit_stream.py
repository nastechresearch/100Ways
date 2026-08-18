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
    history_recovered: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


TELEGRAM_SAFETY_FOOTER = (
    "Safety boundary: 100Ways may only create or update a review PR after every "
    "gate passes; it never merges, tags, releases, or deploys."
)


def format_telegram_status(decision: CommitStreamDecision) -> str:
    """Return a readable, deterministic Telegram status card."""
    labels = {
        "current": ("Current", "No new upstream commits require a sync."),
        "warming": ("Warming", "Full verification is held until the threshold is reached."),
        "awaiting-review": ("Awaiting review", "An open candidate exists; no new sync is started."),
        "threshold-reached": (
            "Threshold reached",
            "The full verification chain is authorized to start.",
        ),
    }
    state, explanation = labels.get(decision.status, (decision.status.upper(), "State recorded."))
    remaining = max(0, decision.threshold - decision.pending_commits)
    if decision.status == "threshold-reached":
        next_action = "Run every gate; #344 may create or update the review PR only after PASS."
    elif decision.status == "warming":
        next_action = f"hold full sync; {remaining} more commit(s) needed before automatic start."
    elif decision.status == "awaiting-review":
        next_action = "Review the existing NasTech candidate; publication is not authorized here."
    else:
        next_action = "Continue monitoring; no candidate publication is authorized."
    lines = [
        "NASTECH / 100WAYS",
        "COMMIT STREAM STATUS",
        "────────────────────",
        f"STATE     {state}",
        explanation,
        "",
        "SOURCE",
        f"Hermes head        {decision.upstream_sha[:12]}",
        f"NasTech main base  {decision.merged_baseline_sha[:12]}",
    ]
    if decision.candidate_baseline_sha:
        lines.append(f"Open candidate      {decision.candidate_baseline_sha[:12]}")
    lines.extend(
        (
            "",
            "PROGRESS",
            f"Pending upstream   {decision.pending_commits}/{decision.threshold}",
            f"Remaining          {remaining}",
            f"Full sync          {'ENABLED' if decision.trigger_sync else 'ON HOLD'}",
            "",
            "NEXT ACTION",
            next_action,
            "",
            "SAFETY",
            "PR creation/update only after every gate passes.",
            "No automatic merge, tag, release, or deployment.",
            TELEGRAM_SAFETY_FOOTER,
        )
    )
    return "\n".join(lines)


def _git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _is_shallow_repository(repo: Path) -> bool:
    """Return whether ``repo`` lacks complete Git ancestry evidence."""
    return _git(repo, "rev-parse", "--is-shallow-repository") == "true"


def _recover_complete_history(repo: Path) -> None:
    """Complete direct-source history only when a shallow clone blocks proof.

    This is a deterministic, read-only recovery.  It fetches from the existing
    direct ``origin`` remote and never changes candidate files, gates, or refs.
    """
    completed = subprocess.run(
        [
            "git", "-C", str(repo), "-c", "http.version=HTTP/1.1",
            "-c", "protocol.version=2", "fetch", "--no-tags", "--unshallow", "origin",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"could not complete direct upstream history: {detail}")


def _is_ancestor(repo: Path, baseline: str, upstream_sha: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", baseline, upstream_sha],
        capture_output=True,
        text=True,
    ).returncode == 0


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
    pages: bool = False,
) -> CommitStreamDecision:
    """Compare merged NasTech state with upstream and make no side effects.

    When ``pages`` is true the function returns a shallow-safe decision that
    NEVER triggers ``git fetch --unshallow``.  This is the variant used by
    the GitHub Pages status surface, where rate-limit exposure is the worst
    possible failure mode.  The full pipeline (``pages=False``) keeps the
    ancestor proof because it actually needs to brand the upstream tree.
    """
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
    history_recovered = False
    if pages:
        # Pages path: trust the upstream HEAD reported by the shallow clone;
        # never call ``_recover_complete_history`` because the resulting
        # ``git fetch --unshallow`` is what triggers HTTP 429 storms against
        # github.com from CI runners.
        if not _is_ancestor(upstream, baseline, upstream_sha):
            return CommitStreamDecision(
                baseline_sha=baseline,
                merged_baseline_sha=merged_baseline,
                candidate_baseline_sha=candidate_baseline,
                upstream_sha=upstream_sha,
                pending_commits=0,
                threshold=threshold,
                status="awaiting-review",
                trigger_sync=False,
                subjects=[],
                history_recovered=False,
            )
    elif not _is_ancestor(upstream, baseline, upstream_sha) and _is_shallow_repository(upstream):
        _recover_complete_history(upstream)
        history_recovered = True
        upstream_sha = _git(upstream, "rev-parse", "HEAD")
    if not pages and not _is_ancestor(upstream, baseline, upstream_sha):
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
        history_recovered=history_recovered,
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
    parser.add_argument(
        "--pages",
        action="store_true",
        help=(
            "Shallow-safe variant for the GitHub Pages status surface. Skips the "
            "complete-history fetch that triggers HTTP 429 storms from CI runners."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decision = inspect_commit_stream(
        args.upstream_repo,
        args.nastech_repo,
        candidate_repo=args.candidate_repo,
        threshold=args.threshold,
        pages=args.pages,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(decision.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(decision.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
