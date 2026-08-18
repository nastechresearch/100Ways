import json
import os
import zipfile
from pathlib import Path

from hundredways.integrity import (
    audit_candidate_archive,
    audit_candidate_tree,
    audit_manifest_provenance,
    tree_digest,
)


UPSTREAM_SHA = "a" * 40


def _manifest(path: Path, **overrides: object) -> None:
    manifest = {
        "upstream_sha": UPSTREAM_SHA,
        "source_provenance": {
            "source_fingerprint": "a" * 64,
            "fetched_at": "2026-08-16T00:00:00+00:00",
            "acquisition": "fresh-direct-clone",
            "upstream_preflight": {
                "passed": True,
                "source_sha": UPSTREAM_SHA,
                "source_files": 7,
            },
            "source_census": {"files": 7, "dirs": 2},
        },
    }
    manifest.update(overrides)
    path.write_text(json.dumps(manifest))


def _archive(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)


def test_manifest_provenance_accepts_canonical_direct_source(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(manifest)

    assert audit_manifest_provenance(manifest, expected_upstream_sha=UPSTREAM_SHA) == []


def test_manifest_provenance_rejects_missing_fingerprint_or_stale_source(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(
        manifest,
        upstream_sha="b" * 40,
        source_provenance={
            "source_fingerprint": "invalid",
            "fetched_at": "not-a-time",
            "acquisition": "cache",
        },
    )

    codes = {issue.code for issue in audit_manifest_provenance(manifest, expected_upstream_sha=UPSTREAM_SHA)}

    assert {
        "source-sha",
        "source-acquisition",
        "source-fingerprint",
        "source-fetch-time",
        "source-census",
    } <= codes


def test_manifest_provenance_ignores_legacy_preflight_and_rejects_invalid_census(tmp_path):
    manifest = tmp_path / "manifest.json"
    _manifest(
        manifest,
        source_provenance={
            "source_fingerprint": "a" * 64,
            "fetched_at": "2026-08-16T00:00:00+00:00",
            "acquisition": "fresh-direct-clone",
            "upstream_preflight": {
                "passed": False,
                "source_sha": "b" * 40,
                "source_files": 4,
            },
            "source_census": {"files": 5, "dirs": 2},
        },
    )

    codes = {issue.code for issue in audit_manifest_provenance(manifest, expected_upstream_sha=UPSTREAM_SHA)}

    assert "upstream-preflight" not in codes
    assert "upstream-preflight-sha" not in codes
    assert "source-census" not in codes

    _manifest(
        manifest,
        source_provenance={
            "source_fingerprint": "a" * 64,
            "fetched_at": "2026-08-16T00:00:00+00:00",
            "acquisition": "fresh-direct-clone",
            "source_census": {"files": 0, "dirs": 2},
        },
    )
    invalid_codes = {
        issue.code for issue in audit_manifest_provenance(
            manifest, expected_upstream_sha=UPSTREAM_SHA
        )
    }
    assert "source-census" in invalid_codes


def test_tree_digest_is_stable_and_binds_file_modes(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    script = root / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n")
    os.chmod(script, 0o755)

    first = tree_digest(root)
    assert tree_digest(root) == first

    os.chmod(script, 0o644)
    assert tree_digest(root) != first


def test_tree_audit_blocks_symlinks_world_writable_and_case_collisions(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    (root / "File.txt").write_text("one")
    (root / "file.txt").write_text("two")
    unsafe = root / "unsafe.txt"
    unsafe.write_text("unsafe")
    os.chmod(unsafe, 0o666)
    (root / "linked.txt").symlink_to(unsafe)

    codes = {issue.code for issue in audit_candidate_tree(root)}

    assert {"case-collision", "world-writable", "symlink"} <= codes


def test_archive_audit_accepts_expected_root_and_manifest(tmp_path):
    archive = tmp_path / "candidate.zip"
    _archive(
        archive,
        {
            "nastech-agent/manifest.json": "{}",
            "nastech-agent/app.py": "print('ok')",
            "GATE-REPORT.md": "ok",
            "UPDATE-REPORT.md": "ok",
            "SYNC-SUMMARY.md": "ok",
        },
    )

    assert audit_candidate_archive(archive) == []


def test_archive_audit_rejects_unsafe_or_unexpected_entries(tmp_path):
    archive = tmp_path / "candidate.zip"
    _archive(
        archive,
        {
            "nastech-agent/manifest.json": "{}",
            "../escape.txt": "no",
            "other-root.txt": "no",
        },
    )

    codes = {issue.code for issue in audit_candidate_archive(archive)}

    assert {"archive-path", "archive-root"} <= codes
