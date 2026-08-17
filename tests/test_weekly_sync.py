import json
from pathlib import Path

from hundredways.weekly_sync import (
    FULL_SYNC_CAPABILITIES,
    WeeklyFullSyncReport,
    audit_brand_symbols,
    audit_branding_fixed_point,
    audit_first_party_brand,
    audit_fts5_trigram_fixtures,
    audit_nested_lockfiles,
    audit_snapshot_safety,
    load_ledger,
    save_ledger,
)


def test_weekly_full_sync_has_hardened_capability_contract():
    assert len(FULL_SYNC_CAPABILITIES) == 100
    assert len(set(FULL_SYNC_CAPABILITIES)) == 100


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


def test_brand_fixed_point_audit_blocks_transformable_text_and_paths(tmp_path):
    root = Path(tmp_path)
    (root / "hermes-notes.md").write_text("Launch Hermes with ⚕ support.\n")
    immutable = root / "contributors" / "emails" / "hermes@example.com"
    immutable.parent.mkdir(parents=True)
    immutable.write_text("Hermes is a real contributor record.\n")

    issues = audit_branding_fixed_point(str(root))

    assert {issue.code for issue in issues} == {"brand-path-not-fixed-point"}
    assert issues[0].path == "hermes-notes.md"


def test_brand_fixed_point_audit_blocks_transformable_text(tmp_path):
    root = Path(tmp_path)
    (root / "notes.md").write_text("Launch Hermes with 𓄃 support.\n")

    issues = audit_branding_fixed_point(str(root))

    assert [(issue.code, issue.path) for issue in issues] == [
        ("brand-text-not-fixed-point", "notes.md")
    ]


def test_brand_symbol_audit_blocks_any_unreplaced_source_glyph(tmp_path):
    root = Path(tmp_path)
    (root / "ui.txt").write_text("⚕ Nastech")

    issues = audit_brand_symbols(str(root))

    assert len(issues) == 1
    assert issues[0].code == "inherited-brand-symbol"
    assert "𓄃" in issues[0].detail


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
    assert report.to_dict()["gate"] == "PASS"
    assert report.to_dict()["review_required"] is True


def test_skill_firewall_review_does_not_block_but_unallowlisted_skill_does():
    from hundredways.skill_policy import SkillPolicyIssue

    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=True)
    report.skill_issues = [
        SkillPolicyIssue("skill-dangerous-instruction", "skills/example/SKILL.md", "review", "review")
    ]

    assert report.gate_passes is True
    assert report.review_required is True

    report.skill_issues.append(
        SkillPolicyIssue("skill-root-not-allowlisted", "unreviewed/SKILL.md", "block")
    )
    assert report.gate_passes is False


def test_snapshot_safety_blocks_credentials_old_dependencies_bad_modes_and_empty_files(tmp_path):
    root = Path(tmp_path)
    (root / "LICENSE").write_text("MIT License\n")
    shell = root / "scripts" / "run.sh"
    shell.parent.mkdir()
    shell.write_text("#!/bin/sh\n")
    (root / "empty.py").write_text("")
    (root / "settings.py").write_text("token = 'ghp_abcdefghijklmnopqrstuvwxyz1234567890'\n")
    (root / "package-lock.json").write_text(
        '{"packages": {"node_modules/minimist": {"version": "1.2.5"}}}'
    )

    codes = {issue.code for issue in audit_snapshot_safety(str(root))}

    assert {"credential-signal", "dependency-vulnerability-pattern", "script-not-executable", "unexpected-empty-file"} <= codes


def test_inherited_security_findings_require_review_but_new_findings_block():
    from hundredways.weekly_sync import (
        AuditIssue,
        WeeklyFullSyncReport,
        partition_inherited_security_issues,
    )
    from hundredways.rules import BrandingRules

    candidate = [
        AuditIssue("credential-signal", "nastech_cli/example.py", "source fixture"),
        AuditIssue("credential-signal", "new/unsafe.py", "new candidate signal"),
    ]
    upstream = [AuditIssue("credential-signal", "hermes_cli/example.py", "source fixture")]

    blocking, inherited = partition_inherited_security_issues(candidate, upstream, BrandingRules())
    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=True)
    report.security_issues = blocking
    report.inherited_security_issues = inherited

    assert [issue.path for issue in blocking] == ["new/unsafe.py"]
    assert [issue.path for issue in inherited] == ["nastech_cli/example.py"]
    assert report.gate_passes is False
    assert report.review_required is True


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
    assert report.to_dict()["gate"] == "PASS"
    assert report.to_dict()["review_required"] is True


def test_pinned_weekly_report_accepts_its_captured_source_and_marks_later_head_for_review(
    tmp_path, monkeypatch
):
    import hundredways.weekly_sync as weekly_sync

    upstream = tmp_path / "upstream"
    candidate = tmp_path / "candidate"
    upstream.mkdir()
    candidate.mkdir()
    expected = "a" * 40
    newer = "b" * 40
    (candidate / "manifest.json").write_text(json.dumps({"upstream_sha": expected}))

    monkeypatch.setattr(weekly_sync, "fetch_upstream", lambda *_args: newer)
    monkeypatch.setattr(weekly_sync, "_run", lambda _repo, *args: expected if args[0] == "rev-parse" else "")
    monkeypatch.setattr(weekly_sync, "_git_changed_numstat", lambda *_args: (0, 0, 0))
    monkeypatch.setattr(weekly_sync, "audit_nested_lockfiles", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_first_party_brand", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_branding_fixed_point", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_brand_symbols", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_fts5_trigram_fixtures", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_owned_assets", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_visual_assets", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_workflow_security", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(weekly_sync, "audit_skill_firewall", lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_snapshot_safety", lambda *_args: [])

    report = weekly_sync.build_weekly_report(
        str(upstream),
        str(candidate),
        str(tmp_path / "state"),
        expected_upstream_sha=expected,
    )

    assert report.upstream_sha == expected
    assert report.snapshot_upstream_sha == expected
    assert report.freshness_ok is True
    assert report.gate_passes is True
    assert [(issue.code, issue.severity) for issue in report.ci_issues] == [
        ("upstream-advanced", "review")
    ]


def test_pinned_weekly_report_blocks_a_candidate_from_the_wrong_source_commit(tmp_path, monkeypatch):
    import hundredways.weekly_sync as weekly_sync

    upstream = tmp_path / "upstream"
    candidate = tmp_path / "candidate"
    upstream.mkdir()
    candidate.mkdir()
    expected = "a" * 40
    wrong = "c" * 40
    newer = "b" * 40
    (candidate / "manifest.json").write_text(json.dumps({"upstream_sha": wrong}))

    monkeypatch.setattr(weekly_sync, "fetch_upstream", lambda *_args: newer)
    monkeypatch.setattr(weekly_sync, "_run", lambda _repo, *args: expected if args[0] == "rev-parse" else "")
    monkeypatch.setattr(weekly_sync, "_git_changed_numstat", lambda *_args: (0, 0, 0))
    for name in (
        "audit_nested_lockfiles",
        "audit_first_party_brand",
        "audit_branding_fixed_point",
        "audit_brand_symbols",
        "audit_fts5_trigram_fixtures",
        "audit_owned_assets",
        "audit_visual_assets",
        "audit_skill_firewall",
        "audit_snapshot_safety",
    ):
        monkeypatch.setattr(weekly_sync, name, lambda *_args: [])
    monkeypatch.setattr(weekly_sync, "audit_workflow_security", lambda *_args, **_kwargs: [])

    report = weekly_sync.build_weekly_report(
        str(upstream),
        str(candidate),
        str(tmp_path / "state"),
        expected_upstream_sha=expected,
    )

    assert report.freshness_ok is False
    assert report.gate_passes is False
    assert {issue.code for issue in report.ci_issues} == {
        "source-sha-mismatch",
        "upstream-advanced",
    }
