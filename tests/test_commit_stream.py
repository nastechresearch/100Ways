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


def test_commit_stream_recovers_shallow_upstream_history_before_counting(tmp_path):
    upstream, target, baseline = _repos(tmp_path)
    for index in range(3):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    shallow_upstream = tmp_path / "shallow-upstream"
    subprocess.run(
        ["git", "clone", "--depth=1", f"file://{upstream}", str(shallow_upstream)],
        check=True,
    )

    decision = inspect_commit_stream(shallow_upstream, target, threshold=3)

    assert decision.baseline_sha == baseline
    assert decision.history_recovered is True
    assert decision.pending_commits == 3
    assert decision.trigger_sync is True
    assert _git(shallow_upstream, "rev-parse", "--is-shallow-repository") == "false"


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


def test_commit_stream_reports_open_candidate_without_using_it_as_baseline(tmp_path):
    upstream, target, merged_baseline = _repos(tmp_path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    candidate_sha = _commit(upstream, "candidate snapshot", "candidate\n")
    (candidate / "manifest.json").write_text(
        json.dumps({"upstream_sha": candidate_sha}),
        encoding="utf-8",
    )

    decision = inspect_commit_stream(upstream, target, candidate_repo=candidate, threshold=5)

    assert decision.baseline_sha == merged_baseline
    assert decision.candidate_baseline_sha == candidate_sha
    assert decision.status == "warming"
    assert decision.pending_commits == 1
    assert decision.trigger_sync is False


def test_open_candidate_cannot_hide_main_backlog_at_threshold(tmp_path):
    upstream, target, merged_baseline = _repos(tmp_path)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    candidate_sha = _commit(upstream, "candidate snapshot", "candidate\n")
    (candidate / "manifest.json").write_text(
        json.dumps({"upstream_sha": candidate_sha}),
        encoding="utf-8",
    )
    for index in range(5):
        _commit(upstream, f"post-candidate change {index}", f"post-{index}\n")

    decision = inspect_commit_stream(upstream, target, candidate_repo=candidate, threshold=5)

    assert decision.baseline_sha == merged_baseline
    assert decision.candidate_baseline_sha == candidate_sha
    assert decision.pending_commits == 6
    assert decision.status == "threshold-reached"
    assert decision.trigger_sync is True


def test_pages_variant_skips_unshallow_and_reports_degraded_when_ancestry_missing(tmp_path):
    """The Pages status path must NEVER trigger ``git fetch --unshallow``.

    The full pipeline uses ancestry proof to gate branding; the public Pages
    surface does not need it and pays too high a rate-limit price for it.
    When the shallow clone cannot prove ancestry, the Pages path returns a
    safe ``awaiting-review`` decision instead of trying to recover.
    """
    upstream, target, _baseline = _repos(tmp_path)
    for index in range(3):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    shallow_upstream = tmp_path / "shallow-upstream"
    subprocess.run(
        ["git", "clone", "--depth=1", f"file://{upstream}", str(shallow_upstream)],
        check=True,
    )

    # Sanity: the shallow clone really lacks ancestry.
    assert _git(shallow_upstream, "rev-parse", "--is-shallow-repository") == "true"

    decision = inspect_commit_stream(
        shallow_upstream, target, threshold=5, pages=True
    )

    assert decision.history_recovered is False
    # Shallow repo cannot satisfy ``merge-base --is-ancestor`` so the Pages
    # variant falls back to a safe status without making any network call.
    assert decision.status == "awaiting-review"
    assert decision.trigger_sync is False
    assert decision.pending_commits == 0
    # The repo must STILL be shallow — proving we never issued ``--unshallow``.
    assert _git(shallow_upstream, "rev-parse", "--is-shallow-repository") == "true"


def test_pages_variant_with_proof_returns_normal_decision(tmp_path):
    """Pages path returns a real status when the shallow clone can prove ancestry.

    To get ``pending_commits > 0`` from a ``--depth=1`` clone the clone must
    carry both the baseline AND its descendants. We use ``--depth=10`` so
    the shallow repo's HEAD is the upstream HEAD and the baseline is an
    ancestor of it. That satisfies ``merge-base --is-ancestor`` without any
    ``--unshallow`` call.
    """
    upstream, target, baseline = _repos(tmp_path)
    for index in range(3):
        _commit(upstream, f"change {index}", f"change-{index}\n")

    shallow_upstream = tmp_path / "shallow-upstream"
    subprocess.run(
        ["git", "clone", "--depth=10", f"file://{upstream}", str(shallow_upstream)],
        check=True,
    )

    decision = inspect_commit_stream(
        shallow_upstream, target, threshold=5, pages=True
    )

    assert decision.history_recovered is False
    assert decision.baseline_sha == baseline
    assert decision.pending_commits == 3
    assert decision.status == "warming"
    assert decision.trigger_sync is False

