from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from hundredways import installer_fixture


def _fixture_path(root: Path) -> Path:
    path = root / "tests/scripts/test_fresh_source_install.py"
    path.parent.mkdir(parents=True)
    return path


def test_reconciles_exact_reviewed_fixture_and_is_idempotent(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path)
    path.write_bytes(installer_fixture._bytes(installer_fixture._INPUT_B85))

    assert installer_fixture.reconcile_installer_fixture(str(tmp_path)) == [
        "tests/scripts/test_fresh_source_install.py"
    ]
    actual = path.read_bytes()
    assert hashlib.sha256(actual).hexdigest() == installer_fixture._OUTPUT_SHA256
    assert installer_fixture.reconcile_installer_fixture(str(tmp_path)) == []


def test_unknown_partial_fixture_is_retained_and_reported(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path)
    partial = installer_fixture._bytes(installer_fixture._INPUT_B85)[:-1]
    path.write_bytes(partial)
    unresolved: list[str] = []

    assert installer_fixture.reconcile_installer_fixture(str(tmp_path), unresolved) == []
    assert path.read_bytes() == partial
    assert unresolved == ["tests/scripts/test_fresh_source_install.py"]


def test_reviewed_main_detects_actual_windows_platform(tmp_path: Path) -> None:
    path = _fixture_path(tmp_path)
    path.write_bytes(installer_fixture._bytes(installer_fixture._INPUT_B85))
    installer_fixture.reconcile_installer_fixture(str(tmp_path))
    source = ast.parse(path.read_text(encoding="utf-8"))
    rendered = next(
        call.args[0].value
        for call in ast.walk(source)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "write_text"
        and call.args
        and isinstance(call.args[0], ast.Constant)
        and isinstance(call.args[0].value, str)
        and "def _is_windows" in call.args[0].value
    )
    tree = ast.parse(rendered)
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_is_windows"
    )
    assert isinstance(helper.body[0], ast.Return)
    check = helper.body[0].value
    assert isinstance(check, ast.Compare)
    assert isinstance(check.left, ast.Attribute)
    assert isinstance(check.left.value, ast.Name) and check.left.value.id == "os"
    assert check.left.attr == "name"
    assert isinstance(check.ops[0], ast.Eq)
    assert isinstance(check.comparators[0], ast.Constant) and check.comparators[0].value == "nt"


def test_missing_fixture_is_not_an_unresolved_drift(tmp_path: Path) -> None:
    unresolved: list[str] = []
    assert installer_fixture.reconcile_installer_fixture(str(tmp_path), unresolved) == []
    assert unresolved == []
