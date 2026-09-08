"""Fast source/test compatibility checks for generated branded candidates."""
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContractIssue:
    code: str
    path: str
    detail: str


# These are fork behaviors that must survive upstream branding and reconciliation.
_REQUIRED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "nastech_cli/main.py": ("_prompt_reasoning_effort_selection",),
}
_REQUIRED_FILES = (
    "plugins/platforms/telegram/adapter.py",
    "tests/gateway/test_telegram_mention_context.py",
    "tests/nastech_cli/test_setup_menu_curses_migration.py",
)


def _defined_symbols(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise ValueError(str(exc)) from exc
    symbols: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
    return symbols


def audit_candidate_contract(root: str | Path) -> list[ContractIssue]:
    """Return deterministic findings for required fork files, symbols, and tests."""
    base = Path(root)
    if not base.is_dir():
        return [ContractIssue("candidate-root", str(base), "candidate root is unavailable")]

    issues: list[ContractIssue] = []
    for relative in _REQUIRED_FILES:
        if not (base / relative).is_file():
            issues.append(ContractIssue("required-file", relative, "required fork file or regression test is missing"))

    for relative, required in _REQUIRED_SYMBOLS.items():
        path = base / relative
        if not path.is_file():
            issues.append(ContractIssue("required-file", relative, "required source file is missing"))
            continue
        try:
            symbols = _defined_symbols(path)
        except ValueError as exc:
            issues.append(ContractIssue("syntax", relative, f"cannot parse source: {exc}"))
            continue
        for symbol in required:
            if symbol not in symbols:
                issues.append(ContractIssue("required-symbol", relative, f"missing required symbol {symbol!r}"))

    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", help="generated candidate tree to audit")
    parser.add_argument("--json", dest="json_path", help="write machine-readable findings")
    args = parser.parse_args(argv)
    issues = audit_candidate_contract(args.candidate)
    report = {"gate": "PASS" if not issues else "FAIL", "issues": [asdict(issue) for issue in issues]}
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if issues:
        print("candidate source/test contract: FAIL")
        for issue in issues:
            print(f"  [{issue.code}] {issue.path}: {issue.detail}")
        return 1
    print("candidate source/test contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
