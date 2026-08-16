import json
import os
import subprocess
from pathlib import Path

from hundredways.prepublish import scan_snapshot


def _git_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    return str(path)


def _snapshot(path: Path, sha: str) -> None:
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps({
            "upstream_sha": sha,
            "source_provenance": {
                "remote_url": "https://github.com/NousResearch/hermes-agent.git",
                "fetched_at": "2026-08-16T00:00:00+00:00",
                "acquisition": "fresh-direct-clone",
            },
        })
    )
    (path / "LICENSE").write_text("MIT License\n")
    runner = path / "scripts" / "run_tests.sh"
    runner.parent.mkdir()
    runner.write_text("#!/bin/sh\n")
    os.chmod(runner, 0o755)


def test_scan_snapshot_allows_inherited_source_credential_fixture(tmp_path):
    upstream = Path(_git_repo(tmp_path / "upstream"))
    source_fixture = upstream / "hermes_cli" / "fixture.py"
    source_fixture.parent.mkdir()
    source_fixture.write_text("token = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "fixture"], check=True)
    sha = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()

    snapshot = tmp_path / "snapshot"
    _snapshot(snapshot, sha)
    candidate_fixture = snapshot / "nastech_cli" / "fixture.py"
    candidate_fixture.parent.mkdir()
    candidate_fixture.write_text("token = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'\n")

    assert scan_snapshot(snapshot, upstream, sha) == []


def test_scan_snapshot_blocks_unsafe_candidate_tree_entry(tmp_path):
    upstream = Path(_git_repo(tmp_path / "upstream"))
    (upstream / "clean.py").write_text("value = 'clean'\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "clean"], check=True)
    sha = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()

    snapshot = tmp_path / "snapshot"
    _snapshot(snapshot, sha)
    unsafe = snapshot / "unsafe.txt"
    unsafe.write_text("unsafe")
    os.chmod(unsafe, 0o666)

    issues = scan_snapshot(snapshot, upstream, sha)

    assert [(issue.code, issue.path) for issue in issues] == [("world-writable", "unsafe.txt")]


def test_scan_snapshot_blocks_new_candidate_credential_signal(tmp_path):
    upstream = Path(_git_repo(tmp_path / "upstream"))
    (upstream / "clean.py").write_text("value = 'clean'\n")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "clean"], check=True)
    sha = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()

    snapshot = tmp_path / "snapshot"
    _snapshot(snapshot, sha)
    (snapshot / "new.py").write_text("token = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'\n")

    issues = scan_snapshot(snapshot, upstream, sha)

    assert [(issue.code, issue.path) for issue in issues] == [("credential-signal", "new.py")]
