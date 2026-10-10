import json
from pathlib import Path

from hundredways.brandaudit import (
    create_issues,
    discover_brand_tokens,
    plan_issues,
    render_issues_markdown,
    residual_brand_lines,
    run_audit,
    scan_credentials,
    write_json,
)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discovery_reports_only_uncovered_spellings(tmp_path):
    _write(tmp_path, "pkg/covered.py", "from hermes_cli import helper\nname = NousResearch\n")
    _write(tmp_path, "pkg/runon.py", "HERMESCLOUD = 1\n")
    _write(tmp_path, "pkg/words.py", "venous = 'synchronous'\n")

    found = discover_brand_tokens(str(tmp_path))

    assert [candidate.token for candidate in found] == ["HERMESCLOUD"]
    assert found[0].count == 1
    assert found[0].samples == ("pkg/runon.py:1",)


def test_discovery_ignores_camelcase_and_english_lookalikes(tmp_path):
    _write(
        tmp_path,
        "src/lookalikes.ts",
        "const noUsageData = 1;\nconst noUser = 2;\nconst venous = 3;\nconst synchronous = 4;\n",
    )
    assert discover_brand_tokens(str(tmp_path)) == []


def test_discovery_skips_locked_and_binary_paths(tmp_path):
    _write(tmp_path, "package-lock.json", '{"name": "HERMESCLOUD"}\n')
    _write(tmp_path, "assets/logo.png", "HERMESCLOUD")
    _write(tmp_path, "src/runon.py", "hermescloud = 1\n")
    tokens = [candidate.token for candidate in discover_brand_tokens(str(tmp_path))]
    assert tokens == ["hermescloud"]


def test_residual_reports_brand_lines_and_skips_fixed_points(tmp_path):
    _write(tmp_path, "pkg/clean.py", "value = 'ok'\n")
    _write(tmp_path, "pkg/dirty.py", "import hermes_cli\nprint('ready')\n")
    _write(tmp_path, "uv.lock", "name = 'hermes_cli'\n")
    residual = residual_brand_lines(str(tmp_path))
    assert [(item.path, item.line) for item in residual] == [("pkg/dirty.py", 1)]
    assert "hermes" in residual[0].tokens


def test_run_audit_and_issue_planning_are_side_effect_free(tmp_path):
    source = tmp_path / "source"
    candidate = tmp_path / "candidate"
    _write(source, "src/runon.py", "HERMESCLOUD = 1\n")
    _write(candidate, "src/clean.py", "ok = True\n")
    _write(candidate, "src/dirty.py", "import hermes_cli\n")
    _write(candidate, "src/leak.py", "token = 'sk-" + "a" * 32 + "'\n")

    audit = run_audit(str(source), str(candidate))
    assert not audit.clean
    assert [candidate_token.token for candidate_token in audit.uncovered] == ["HERMESCLOUD"]
    assert audit.residual and audit.residual[0].path == "src/dirty.py"
    assert audit.secrets and audit.secrets[0].path == "src/leak.py"
    assert "sk-" not in audit.secrets[0].snippet

    drafts = plan_issues(audit, "owner/repo")
    assert {draft.key for draft in drafts} == {
        "uncovered-brand-tokens",
        "residual-brand-lines",
        "credential-residency",
        "strong-brand-violations",
    }
    results = create_issues("owner/repo", drafts, apply=False)
    assert all(result["action"] == "skipped" for result in results)

    markdown = render_issues_markdown(drafts)
    assert "100Ways issue drafts" in markdown
    assert markdown == render_issues_markdown(drafts)


def test_clean_source_has_no_issue_drafts(tmp_path):
    _write(tmp_path, "src/clean.py", "value = 'ok'\n")
    audit = run_audit(str(tmp_path))
    assert audit.clean
    assert plan_issues(audit) == []
    assert "no findings to report" in render_issues_markdown([])


def test_scan_credentials_redacts_and_write_json_round_trips(tmp_path):
    _write(tmp_path, "src/leak.py", "key = 'AKIA" + "A" * 16 + "'\n")
    findings = scan_credentials(str(tmp_path))
    assert len(findings) == 1
    assert findings[0].path == "src/leak.py"
    assert "AKIA" + "A" * 16 not in findings[0].snippet

    audit = run_audit(str(tmp_path))
    out = tmp_path / "audit.json"
    write_json(audit, str(out))
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["source_root"] == str(tmp_path)
    assert payload["clean"] is False
