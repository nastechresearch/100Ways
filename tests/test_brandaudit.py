import json
from pathlib import Path

from hundredways.brandaudit import (
    _MAX_SCAN_BYTES,
    _text_entries,
    create_issues,
    discover_brand_tokens,
    plan_issues,
    render_issues_markdown,
    residual_brand_lines,
    run_audit,
    scan_credentials,
    write_json,
)
from hundredways.rules import is_immutable_path, is_locked_path
from hundredways.tree_index import FileIndex


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


def _reference_scannable(root: Path) -> list[str]:
    """The walk every brand scan used to do for itself, kept as the oracle."""
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", "node_modules"} for part in path.parts):
            continue
        relative = path.relative_to(root).as_posix()
        if is_immutable_path(relative) or is_locked_path(relative):
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
        except OSError:
            continue
        found.append(relative)
    return found


def _scan_tree(root: Path) -> Path:
    _write(root, "pkg/runon.py", "HERMESCLOUD = 1\n")
    _write(root, "pkg/clean.py", "value = 'ok'\n")
    _write(root, "pkg/dirty.py", "import hermes_cli\nprint('ready')\n")
    _write(root, "pkg/leak.py", "token = 'sk-" + "a" * 32 + "'\n")
    _write(root, "package-lock.json", '{"name": "HERMESCLOUD"}\n')
    _write(root, "contributors/names/Hermes Person", "HERMESPERSON = 1\n")
    _write(root, ".git/runon.py", "HERMESCLOUD = 1\n")
    _write(root, "node_modules/dep/runon.py", "HERMESCLOUD = 1\n")
    _write(root, "pkg/oversize.py", "HERMESCLOUD = 1\n" + "x" * _MAX_SCAN_BYTES)
    return root


def test_index_view_matches_the_walk_each_scan_used_to_do_itself(tmp_path):
    root = _scan_tree(Path(tmp_path))
    index = FileIndex(root)
    excluded = {
        "pkg/oversize.py", "package-lock.json", ".git/runon.py", "node_modules/dep/runon.py",
    }

    assert [entry.rel for entry in _text_entries(index)] == _reference_scannable(root)
    assert "pkg/oversize.py" in [entry.rel for entry in index.files()]
    assert not excluded.intersection(entry.rel for entry in _text_entries(index))
    assert "contributors/names/Hermes Person" not in [entry.rel for entry in _text_entries(index)]


def test_scans_report_their_expected_findings_and_survive_a_shared_index(tmp_path):
    root = _scan_tree(Path(tmp_path))
    index = FileIndex(root)
    target = str(root)

    assert [item.token for item in discover_brand_tokens(target)] == ["HERMESCLOUD"]
    assert discover_brand_tokens(target)[0].samples == ("pkg/runon.py:1",)
    residual = [(item.path, item.line) for item in residual_brand_lines(target)]
    assert residual == [("pkg/dirty.py", 1)]
    assert [(item.path, item.line) for item in scan_credentials(target)] == [("pkg/leak.py", 1)]

    assert discover_brand_tokens(target, index=index) == discover_brand_tokens(target)
    assert residual_brand_lines(target, index=index) == residual_brand_lines(target)
    assert scan_credentials(target, index=index) == scan_credentials(target)


def test_run_audit_counts_files_from_the_index_it_already_has(tmp_path):
    source = _scan_tree(tmp_path / "source")
    candidate = Path(tmp_path / "candidate")
    _write(candidate, "src/clean.py", "ok = True\n")
    _write(candidate, "src/dirty.py", "import hermes_cli\n")
    _write(candidate, "src/leak.py", "token = 'sk-" + "a" * 32 + "'\n")
    _write(candidate, ".git/src/dirty.py", "import hermes_cli\n")

    audit = run_audit(str(source), str(candidate))

    assert audit.scanned_source_files == len(_reference_scannable(source))
    assert audit.scanned_candidate_files == len(_reference_scannable(candidate))
    assert audit.scanned_candidate_files == 3
    assert [item.token for item in audit.uncovered] == ["HERMESCLOUD"]
    assert [(item.path, item.line) for item in audit.residual] == [("src/dirty.py", 1)]
    assert [(item.path, item.line) for item in audit.secrets] == [("src/leak.py", 1)]
    assert audit.uncovered == discover_brand_tokens(str(source))
    assert audit.residual == residual_brand_lines(str(candidate))
    assert audit.secrets == scan_credentials(str(candidate))


def test_run_audit_on_source_only_reuses_the_one_snapshot(tmp_path):
    source = _scan_tree(tmp_path / "source")

    audit = run_audit(str(source))

    assert audit.scanned_source_files == audit.scanned_candidate_files
    assert audit.scanned_source_files == len(_reference_scannable(source))
    assert audit.residual == []
    assert [item.token for item in audit.uncovered] == ["HERMESCLOUD"]
    assert [(item.path, item.line) for item in audit.secrets] == [("pkg/leak.py", 1)]
