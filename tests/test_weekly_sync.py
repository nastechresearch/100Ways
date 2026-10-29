import json
from pathlib import Path

from hundredways.tree_index import FileIndex
from hundredways.weekly_sync import (
    FULL_SYNC_CAPABILITIES,
    AuditIssue,
    WeeklyFullSyncReport,
    audit_brand_symbols,
    audit_branding_fixed_point,
    audit_cli_banner_identity,
    audit_first_party_brand,
    audit_fts5_trigram_fixtures,
    audit_nested_lockfiles,
    audit_snapshot_safety,
    load_ledger,
    reconcile_nested_lockfile_roots,
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


def test_brand_audit_flags_third_party_misaki_source_in_uv_lock(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text(
        'source = { git = "https://github.com/NousResearch/misaki.git?rev=abc" }\n'
    )

    issues = audit_first_party_brand(str(tmp_path))

    assert len(issues) == 1
    assert issues[0].path == "uv.lock"
    assert issues[0].code == "first-party-brand"


def test_brand_audit_flags_third_party_misaki_source_in_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = ["misaki @ git+https://github.com/NousResearch/misaki.git@abc"]\n'
    )

    issues = audit_first_party_brand(str(tmp_path))

    assert len(issues) == 1
    assert issues[0].path == "pyproject.toml"
    assert issues[0].code == "first-party-brand"


def test_brand_fixed_point_audit_blocks_transformable_text_and_paths(tmp_path):
    root = Path(tmp_path)
    (root / "hermes-notes.md").write_text("Launch Hermes with ⚕ support.\n")
    immutable = root / "contributors" / "names" / "Hermes Person"
    immutable.parent.mkdir(parents=True)
    immutable.write_text("Hermes is approved identity metadata.\n")

    issues = audit_branding_fixed_point(str(root))

    assert {issue.code for issue in issues} == {"brand-path-not-fixed-point"}
    assert issues[0].path == "hermes-notes.md"


def test_brand_audit_scans_generated_metadata_and_contributor_emails(tmp_path):
    root = Path(tmp_path)
    (root / "manifest.json").write_text('{"source": "https://github.com/NousResearch/hermes-agent"}\n')
    (root / "reports").mkdir()
    (root / "reports" / "UPDATE-REPORT.md").write_text("Hermes update evidence\n")
    emails = root / "contributors" / "emails"
    emails.mkdir(parents=True)
    (emails / "agent@hermes.local").write_text("NousResearch mailbox\n")

    issues = audit_first_party_brand(str(root))

    assert {(issue.code, issue.path) for issue in issues} == {
        ("first-party-brand", "manifest.json"),
        ("first-party-brand", "reports/UPDATE-REPORT.md"),
        ("first-party-brand", "contributors/emails/agent@hermes.local"),
        ("first-party-brand-path", "contributors/emails/agent@hermes.local"),
    }


def test_cli_banner_audit_requires_nastech_title_and_approved_symbol(tmp_path):
    root = Path(tmp_path)
    cli = root / "nastech_cli"
    cli.mkdir()
    (cli / "banner.py").write_text("_logo = _bskin.banner_logo\n")

    issues = audit_cli_banner_identity(str(root))

    assert {issue.code for issue in issues} == {"cli-banner"}
    assert len(issues) == 4


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


def test_report_includes_changed_areas_and_hard_blocker_details(tmp_path):
    from hundredways.weekly_sync import write_weekly_report

    report = WeeklyFullSyncReport(
        "sha", "", 0, 0, 0, 0, freshness_ok=False,
        changed_areas={"apps/desktop": 3, "website": 2},
    )
    path = tmp_path / "weekly.md"
    write_weekly_report(str(path), report)

    text = path.read_text()
    assert "## Changed areas" in text
    assert "`apps/desktop`: 3 changed files" in text
    assert "`website`: 2 changed files" in text
    assert "hard blockers: 1" in text
    assert "upstream-not-fresh" in text


def test_report_includes_hard_blocker_details(tmp_path):
    from hundredways.weekly_sync import write_weekly_report

    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=False)
    path = tmp_path / "weekly.md"
    write_weekly_report(str(path), report)

    text = path.read_text()
    assert "hard blockers: 1" in text
    assert "upstream-not-fresh" in text
    assert "upstream changed" in text


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


def test_gate_blockers_explain_freshness_failure():
    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=False)

    assert report.gate_passes is False
    assert report.gate_blockers == [{
        "category": "freshness",
        "code": "upstream-not-fresh",
        "path": "",
        "detail": "upstream changed or could not be confirmed stable during the gate",
    }]
    assert report.to_dict()["gate_blockers"] == report.gate_blockers


def test_gate_blockers_exclude_review_only_findings():
    from hundredways.ci_policy import WorkflowPolicyIssue

    report = WeeklyFullSyncReport("sha", "", 0, 0, 0, 0, freshness_ok=True)
    report.ci_issues = [WorkflowPolicyIssue("secret-inheritance", "workflow.yml", "review", "review")]

    assert report.gate_passes is True
    assert report.gate_blockers == []


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


WEEKLY_AUDITS = (
    audit_first_party_brand,
    audit_branding_fixed_point,
    audit_brand_symbols,
    audit_fts5_trigram_fixtures,
    audit_snapshot_safety,
)


def _gate_tree(root: Path) -> Path:
    """One tree that trips every wired weekly audit, plus paths none may report."""
    (root / "readme.md").write_text("Launch Hermes Desktop today.\n", encoding="utf-8")
    (root / "hermes-notes.md").write_text("⚕ legacy glyph\n", encoding="utf-8")
    (root / "notes.md").write_text("value = 'ok'\n", encoding="utf-8")
    (root / "empty.py").write_text("", encoding="utf-8")
    (root / "settings.py").write_text(f"token = 'ghp_{'a' * 30}'\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT License\nPermission is hereby granted.\n", encoding="utf-8")
    (root / "binary.png").write_bytes(b"\x89PNG\xff\xfe not utf-8\n")
    (root / "package-lock.json").write_text(json.dumps({
        "name": "@nastech-agent/root",
        "packages": {
            "": {"name": "@nastech-agent/root"},
            "node_modules/minimist": {"version": "1.2.5"},
        },
    }) + "\n", encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    shell = scripts / "run.sh"
    shell.write_text("#!/bin/sh\n", encoding="utf-8")
    shell.chmod(0o644)
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_sqlite_runtime.py").write_text(
        "db.execute(\"CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')\")\n"
        "db.execute(\"INSERT INTO docs VALUES ('nastech')\")\n"
        "db.execute(\"SELECT count(*) FROM docs WHERE docs MATCH 'erm'\")\n",
        encoding="utf-8",
    )
    metadata = root / ".git"
    metadata.mkdir()
    (metadata / "config").write_text("[core]\n", encoding="utf-8")
    (metadata / "HERMES.md").write_text("Hermes inside Git metadata\n", encoding="utf-8")
    vendor = root / "node_modules" / "dep"
    vendor.mkdir(parents=True)
    (vendor / "index.js").write_text("Hermes inside a dependency\n", encoding="utf-8")
    (root / "empty-dir").mkdir()
    return root


def test_first_party_brand_audit_reports_the_expected_issues_in_path_order(tmp_path):
    root = _gate_tree(Path(tmp_path))

    assert audit_first_party_brand(str(root)) == [
        AuditIssue(
            "first-party-brand-path",
            "hermes-notes.md",
            "prohibited 'hermes' token in candidate path",
        ),
        AuditIssue("first-party-brand", "readme.md", "line 1: Launch Hermes Desktop today."),
    ]


def test_fixed_point_symbol_and_fts5_audits_report_the_expected_issues(tmp_path):
    root = _gate_tree(Path(tmp_path))

    assert audit_branding_fixed_point(str(root)) == [
        AuditIssue(
            "brand-path-not-fixed-point",
            "hermes-notes.md",
            "canonical path transform would produce 'nastech-notes.md'",
        ),
        AuditIssue(
            "brand-text-not-fixed-point",
            "readme.md",
            "canonical text transform would still change this file",
        ),
    ]
    assert audit_brand_symbols(str(root)) == [
        AuditIssue(
            "inherited-brand-symbol",
            "hermes-notes.md",
            "contains source glyph '⚕'; expected '𓄃'",
        )
    ]
    assert audit_fts5_trigram_fixtures(str(root)) == [
        AuditIssue(
            "fts5-trigram-fixture",
            "tests/test_sqlite_runtime.py",
            "MATCH 'erm' is not a trigram of the branded fixture value(s): ['nastech']",
        )
    ]


def test_snapshot_safety_reports_the_expected_issues_in_path_order(tmp_path):
    root = _gate_tree(Path(tmp_path))

    assert audit_snapshot_safety(str(root)) == [
        AuditIssue(
            "unexpected-empty-file",
            "empty.py",
            "empty file may indicate incomplete source transformation",
        ),
        AuditIssue(
            "script-not-executable",
            "scripts/run.sh",
            "shell scripts must have an executable bit",
        ),
        AuditIssue("credential-signal", "settings.py", "credential-like value detected"),
        AuditIssue(
            "dependency-vulnerability-pattern",
            "package-lock.json",
            "minimist 1.2.5 is below the minimum safe version 1.2.6",
        ),
    ]


def test_snapshot_safety_flags_a_missing_license_through_the_index(tmp_path):
    root = Path(tmp_path)
    (root / "notes.md").write_text("value = 'ok'\n", encoding="utf-8")

    missing = audit_snapshot_safety(str(root))
    shared = audit_snapshot_safety(str(root), index=FileIndex(root))

    assert missing == shared == [AuditIssue(
        "license-mit", "LICENSE", "repository license must retain the MIT License text"
    )]


def test_every_weekly_audit_keeps_its_findings_through_a_shared_index(tmp_path):
    root = _gate_tree(Path(tmp_path))
    shared = FileIndex(root)

    for audit in WEEKLY_AUDITS:
        assert audit(str(root), index=shared) == audit(str(root))


def test_weekly_brand_chain_keeps_its_audit_sequence(tmp_path):
    root = _gate_tree(Path(tmp_path))
    shared = FileIndex(root)

    chain = [
        (issue.code, issue.path)
        for audit in (audit_first_party_brand, audit_branding_fixed_point, audit_brand_symbols)
        for issue in audit(str(root), index=shared)
    ] + [
        (issue.code, issue.path) for issue in audit_cli_banner_identity(str(root))
    ] + [
        (issue.code, issue.path) for issue in audit_fts5_trigram_fixtures(str(root), index=shared)
    ]

    assert chain == [
        ("first-party-brand-path", "hermes-notes.md"),
        ("first-party-brand", "readme.md"),
        ("brand-path-not-fixed-point", "hermes-notes.md"),
        ("brand-text-not-fixed-point", "readme.md"),
        ("inherited-brand-symbol", "hermes-notes.md"),
        ("cli-banner", "nastech_cli/banner.py"),
        ("fts5-trigram-fixture", "tests/test_sqlite_runtime.py"),
    ]


def test_shared_index_never_shows_git_or_dependency_directories(tmp_path):
    root = _gate_tree(Path(tmp_path))
    index = FileIndex(root)

    paths = [issue.path for audit in WEEKLY_AUDITS for issue in audit(str(root), index=index)]

    assert not [path for path in paths if ".git" in path or "node_modules" in path]


def test_a_pre_reconcile_index_would_keep_serving_upstream_bytes(tmp_path):
    """Why the weekly gate indexes the tree only after the lockfile reconcile."""
    package = tmp_path / "sidecar"
    package.mkdir()
    (package / "package.json").write_text('{"name": "@nastech-agent/sidecar"}')
    (package / "package-lock.json").write_text(
        '{"name": "@hermes-agent/sidecar", "packages": {"": {"name": "@hermes-agent/sidecar"}}}'
    )
    stale = FileIndex(tmp_path)
    assert "hermes" in stale.text("sidecar/package-lock.json")

    changed = reconcile_nested_lockfile_roots(str(tmp_path))

    assert changed == ["sidecar/package-lock.json"]
    assert "hermes" in stale.text("sidecar/package-lock.json")
    assert "hermes" not in FileIndex(tmp_path).text("sidecar/package-lock.json")
    assert audit_nested_lockfiles(str(tmp_path)) == []
