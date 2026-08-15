import json
from pathlib import Path

from hundredways.weekly_sync import (
    FULL_SYNC_CAPABILITIES,
    WeeklyFullSyncReport,
    audit_first_party_brand,
    audit_fts5_trigram_fixtures,
    audit_nested_lockfiles,
    load_ledger,
    save_ledger,
)


def test_weekly_full_sync_has_hardened_capability_contract():
    assert len(FULL_SYNC_CAPABILITIES) == 73
    assert len(set(FULL_SYNC_CAPABILITIES)) == 73


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


def test_fts5_fixture_audit_blocks_stale_branded_query_token(tmp_path):
    root = Path(tmp_path)
    fixture = root / "tests" / "docker" / "test_sqlite_runtime.py"
    fixture.parent.mkdir(parents=True)
    fixture.write_text(
        "db.execute(\"CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')\")\n"
        "db.execute(\"INSERT INTO docs VALUES ('nastech')\")\n"
        "db.execute(\"SELECT count(*) FROM docs WHERE docs MATCH 'erm'\")\n"
    )

    issues = audit_fts5_trigram_fixtures(str(root))

    assert len(issues) == 1
    assert issues[0].code == "fts5-trigram-fixture"
    assert issues[0].path == "tests/docker/test_sqlite_runtime.py"


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


def test_owned_asset_mapping_is_a_copy(tmp_path):
    from hundredways.assets import OwnedAssets

    assets = tmp_path / "config" / "owned-assets"
    assets.mkdir(parents=True)
    (assets / "manifest.json").write_text('{"assets/logo.png": "logo.png"}')
    registry = OwnedAssets(root=str(assets))

    mapping = registry.mapping
    mapping["assets/logo.png"] = "changed.png"

    assert registry.mapping["assets/logo.png"] == "logo.png"


def test_reconcile_nested_lockfile_roots_repairs_branded_identity(tmp_path):
    from hundredways.weekly_sync import audit_nested_lockfiles, reconcile_nested_lockfile_roots

    package = tmp_path / "sidecar"
    package.mkdir()
    (package / "package.json").write_text('{"name": "@nastech-agent/sidecar"}')
    (package / "package-lock.json").write_text(
        '{"name": "@hermes-agent/sidecar", "packages": {"": {"name": "@hermes-agent/sidecar"}}}'
    )

    changed = reconcile_nested_lockfile_roots(str(tmp_path))

    assert changed == ["sidecar/package-lock.json"]
    assert audit_nested_lockfiles(str(tmp_path)) == []


def test_review_only_ci_issue_does_not_block_candidate_gate():
    from hundredways.ci_policy import WorkflowPolicyIssue
    from hundredways.weekly_sync import WeeklyFullSyncReport

    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=True)
    report.ci_issues = [WorkflowPolicyIssue("secret-inheritance", "workflow.yml", "review", "review")]

    assert report.gate_passes is True
    assert report.review_required is True
    assert report.to_dict()["gate"] == "REVIEW"


def test_upstream_advance_is_review_evidence_not_a_hard_failure():
    from hundredways.ci_policy import WorkflowPolicyIssue
    from hundredways.weekly_sync import WeeklyFullSyncReport

    report = WeeklyFullSyncReport("captured", "", 0, 0, 0, 0, freshness_ok=True)
    report.ci_issues = [WorkflowPolicyIssue(
        "upstream-advanced",
        "manifest.json",
        "a newer upstream head is available for the next sync",
        "review",
    )]

    assert report.gate_passes is True
    assert report.review_required is True
    assert report.to_dict()["gate"] == "REVIEW"
