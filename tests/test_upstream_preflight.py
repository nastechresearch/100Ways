"""Regression coverage for the mandatory direct-upstream test preflight."""

import os
import subprocess

import pytest

from hundredways.upstream_preflight import UpstreamPreflightError, run_upstream_preflight


def _source_repo(tmp_path, *, exit_code: int = 0):
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "100Ways Tests"], check=True)
    (repo / "source.py").write_text("VALUE = 'hermes-agent'\n", encoding="utf-8")
    scripts = repo / "scripts"
    scripts.mkdir()
    runner = scripts / "run_tests.sh"
    runner.write_text(f"#!/bin/sh\nset -eu\nexit {exit_code}\n", encoding="utf-8")
    os.chmod(runner, 0o755)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "source"], check=True)
    return repo


def test_preflight_runs_tracked_canonical_runner_and_records_unchanged_source(tmp_path):
    repo = _source_repo(tmp_path)
    expected_sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()

    report = run_upstream_preflight(repo, expected_sha=expected_sha)

    assert report.passed is True
    assert report.source_sha == expected_sha
    assert report.runner == "scripts/run_tests.sh"
    assert report.source_files == 2
    assert report.environment_prepared is False


def test_preflight_fails_before_branding_when_the_canonical_upstream_runner_fails(tmp_path):
    repo = _source_repo(tmp_path, exit_code=19)

    with pytest.raises(UpstreamPreflightError, match="canonical upstream tests failed"):
        run_upstream_preflight(repo)

    assert (repo / "source.py").read_text(encoding="utf-8") == "VALUE = 'hermes-agent'\n"


def test_preflight_rejects_a_source_head_that_differs_from_direct_clone_evidence(tmp_path):
    repo = _source_repo(tmp_path)

    with pytest.raises(UpstreamPreflightError, match="does not match"):
        run_upstream_preflight(repo, expected_sha="a" * 40)
