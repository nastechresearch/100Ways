from pathlib import Path

from hundredways.candidate_contract import audit_candidate_contract


def _candidate(root: Path, *, include_symbol: bool = True) -> Path:
    (root / "plugins/platforms/telegram").mkdir(parents=True)
    (root / "tests/gateway").mkdir(parents=True)
    (root / "tests/nastech_cli").mkdir(parents=True)
    (root / "plugins/platforms/telegram/adapter.py").write_text(
        "async def _handle_text_message(update, context):\n    pass\n",
        encoding="utf-8",
    )
    (root / "tests/gateway/test_telegram_mention_context.py").write_text("def test_reply(): pass\n", encoding="utf-8")
    (root / "tests/nastech_cli/test_setup_menu_curses_migration.py").write_text(
        "def test_selector(): pass\n", encoding="utf-8"
    )
    (root / "nastech_cli").mkdir()
    source = "def _prompt_reasoning_effort_selection(efforts, current_effort=''): pass\n" if include_symbol else ""
    (root / "nastech_cli/main.py").write_text(source, encoding="utf-8")
    return root


def test_candidate_contract_passes_for_required_tree(tmp_path):
    assert audit_candidate_contract(_candidate(tmp_path / "candidate")) == []


def test_candidate_contract_reports_missing_source_symbol(tmp_path):
    issues = audit_candidate_contract(_candidate(tmp_path / "candidate", include_symbol=False))
    assert [(issue.code, issue.path) for issue in issues] == [("required-symbol", "nastech_cli/main.py")]


def test_candidate_contract_reports_missing_regression_files(tmp_path):
    candidate = _candidate(tmp_path / "candidate")
    (candidate / "tests/nastech_cli/test_setup_menu_curses_migration.py").unlink()
    issues = audit_candidate_contract(candidate)
    assert ("required-file", "tests/nastech_cli/test_setup_menu_curses_migration.py") in {
        (issue.code, issue.path) for issue in issues
    }
