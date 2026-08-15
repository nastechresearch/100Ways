from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hundredways.commit_stream import (
    TELEGRAM_SAFETY_FOOTER,
    format_telegram_status,
    inspect_commit_stream,
    load_baseline_sha,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str, content: str) -> str:
    (repo / "changes.txt").write_text(content, encoding="utf-8")
    _git(repo, "add", "changes.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repos(tmp_path: Path) -> tuple[Path, Path, str]:
    upstream = tmp_path / "upstream"
    target = tmp_path / "nastech"
    upstream.mkdir()
    target.mkdir()
    for repo in (upstream, target):
        _git(repo, "init")
        _git(repo, "config", "user.name", "100Ways Tests")
        _git(repo, "config", "user.email", "tests@example.invalid")
    baseline = _commit(upstream, "baseline", "0\n")
    (target / "manifest.json").write_text(json.dumps({"upstream_sha": baseline}), encoding="utf-8")
    return upstream, target, baseline


def test_commit_stream_warms_without_trigger_below_threshold(tmp_path):
    upstream, target, baseline = _repos(tmp_path)
    for index in range(3):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    decision = inspect_commit_stream(upstream, target, threshold=5)

    assert decision.baseline_sha == baseline
    assert decision.pending_commits == 3
    assert decision.status == "warming"
    assert decision.trigger_sync is False
    assert len(decision.subjects) == 3


def test_commit_stream_triggers_only_at_threshold(tmp_path):
    upstream, target, _ = _repos(tmp_path)
    for index in range(5):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    decision = inspect_commit_stream(upstream, target, threshold=5)

    assert decision.pending_commits == 5
    assert decision.status == "threshold-reached"
    assert decision.trigger_sync is True


def test_telegram_warming_status_holds_publication_and_has_safety_footer(tmp_path):
    upstream, target, _ = _repos(tmp_path)
    for index in range(3):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    message = format_telegram_status(inspect_commit_stream(upstream, target, threshold=5))

    assert "Warming" in message
    assert "2 more commit(s) needed" in message
    assert "hold full sync" in message
    assert TELEGRAM_SAFETY_FOOTER in message


def test_telegram_threshold_status_mentions_344_pr_only_boundary(tmp_path):
    upstream, target, _ = _repos(tmp_path)
    for index in range(2):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    message = format_telegram_status(inspect_commit_stream(upstream, target, threshold=2))

    assert "Threshold reached" in message
    assert "#344" in message
    assert "never merges, tags, releases, or deploys" in message


def test_commit_stream_rejects_missing_baseline(tmp_path):
    target = tmp_path / "nastech"
    target.mkdir()

    with pytest.raises(RuntimeError, match="cannot read merged NasTech manifest"):
        load_baseline_sha(target)


def test_commit_stream_uses_open_candidate_as_effective_baseline(tmp_path):
    upstream, target, _ = _repos(tmp_path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    candidate_sha = _commit(upstream, "candidate snapshot", "candidate\n")
    (candidate / "manifest.json").write_text(
        json.dumps({"upstream_sha": candidate_sha}),
        encoding="utf-8",
    )

    decision = inspect_commit_stream(upstream, target, candidate_repo=candidate, threshold=5)

    assert decision.baseline_sha == candidate_sha
    assert decision.candidate_baseline_sha == candidate_sha
    assert decision.status == "awaiting-review"
    assert decision.pending_commits == 0
    assert decision.trigger_sync is False
