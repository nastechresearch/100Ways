import json
from pathlib import Path

from hundredways.weekly_sync import (
    FULL_SYNC_CAPABILITIES,
    WeeklyFullSyncReport,
    audit_first_party_brand,
    audit_nested_lockfiles,
    load_ledger,
    save_ledger,
)


def test_weekly_full_sync_has_hardened_capability_contract():
    assert len(FULL_SYNC_CAPABILITIES) == 72
    assert len(set(FULL_SYNC_CAPABILITIES)) == 72


def test_nested_lock_audit_detects_a_stale_workspace_name(tmp_path):
    root = Path(tmp_path)
    work = root / "scripts" / "bridge"
    work.mkdir(parents=True)
    (work / "package.json").write_text(json.dumps({"name": "nastech-bridge"}))
    (work / "package-lock.json").write_text(json.dumps({
        "name": "hermes-bridge",
        "packages": {"": {"name": "hermes-bridge"}},
    }))

    issues = audit_nested_lockfiles(str(root))

    assert len(issues) == 1
    assert issues[0].code == "lock-root-name"
    assert "nastech-bridge" in issues[0].detail


def test_brand_audit_flags_first_party_brand_and_allows_vendor_package(tmp_path):
    root = Path(tmp_path)
    (root / "readme.md").write_text("Launch Hermes Desktop today.\n")
    (root / "package-lock.json").write_text('"hermes-parser": "0.25.1"\n')

    issues = audit_first_party_brand(str(root))

    assert len(issues) == 1
    assert issues[0].path == "readme.md"
    assert issues[0].code == "first-party-brand"


def test_passing_weekly_report_round_trips_through_ledger(tmp_path):
    report = WeeklyFullSyncReport(
        upstream_sha="abc123",
        previous_sha="old123",
        commits=2,
        files_changed=3,
        added_lines=16,
        deleted_lines=1,
        freshness_ok=True,
    )

    path = save_ledger(str(tmp_path), report, candidate="sync/hermes-abc123")
    stored = load_ledger(str(tmp_path))

    assert path.is_file()
    assert stored["upstream_sha"] == "abc123"
    assert stored["candidate"] == "sync/hermes-abc123"
    assert stored["gate"] == "PASS"
