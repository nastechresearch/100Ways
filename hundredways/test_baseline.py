"""Deterministic source-baseline classification for branded-candidate test runs.

A source test failure is allowed to accompany a review candidate only when the
same named test fails in the exact fresh source checkout.  Every candidate-only
failure remains a hard block.  The module contains no retry, repair, or AI
behavior: it only parses pytest evidence and applies the branding mapping.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .rules import BrandingRules


@dataclass(frozen=True)
class TestBaselineReport:
    """Comparison of candidate test failures with the exact source baseline."""

    candidate_exit_code: int
    source_exit_code: int
    candidate_failed: tuple[str, ...]
    source_failed: tuple[str, ...]
    inherited_failures: tuple[str, ...]
    nastech_only_failures: tuple[str, ...]
    unclassified_candidate_failure: bool

    @property
    def classification(self) -> str:
        if self.unclassified_candidate_failure or self.nastech_only_failures:
            return "Nastech-only-regression"
        if self.inherited_failures:
            return "inherited-upstream-failures"
        return "clean"

    @property
    def review_ready(self) -> bool:
        """True only when every candidate failure reproduces on fresh source."""
        return not self.unclassified_candidate_failure and not self.nastech_only_failures

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["classification"] = self.classification
        value["review_ready"] = self.review_ready
        return value


def failed_nodeids(log_text: str) -> tuple[str, ...]:
    """Extract pytest node IDs from standard ``FAILED <nodeid>`` summary lines."""
    found: set[str] = set()
    for raw_line in log_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("FAILED "):
            continue
        nodeid = line[len("FAILED "):].split(" - ", 1)[0].strip()
        if "::" in nodeid and nodeid.startswith("tests/"):
            found.add(nodeid)
    return tuple(sorted(found))


def resolve_source_nodeids(
    candidate_nodeids: Sequence[str],
    *,
    source_root: str | Path,
    python: str,
    rules: BrandingRules | None = None,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Resolve branded node IDs to exact source node IDs via pytest collection.

    A source test file is eligible only when transforming its relative path
    produces the candidate test-file path.  Collection then provides the exact
    source node IDs; only IDs whose forward branding equals the candidate ID
    are accepted.  Ambiguous, missing, or uncollectable mappings remain
    unresolved and consequently stay blocking.
    """
    rules = rules or BrandingRules()
    root = Path(source_root)
    by_file: dict[str, list[str]] = {}
    for nodeid in candidate_nodeids:
        file_name, _, _ = nodeid.partition("::")
        by_file.setdefault(file_name, []).append(nodeid)

    resolved: list[tuple[str, str]] = []
    unresolved: set[str] = set()
    source_files = [
        path for path in root.rglob("test*.py")
        if path.is_file() and ".git" not in path.parts and ".venv" not in path.parts
    ]
    for candidate_file, wanted in by_file.items():
        matches = [
            path for path in source_files
            if rules.transform_path(path.relative_to(root).as_posix()) == candidate_file
        ]
        if len(matches) != 1:
            unresolved.update(wanted)
            continue
        source_file = matches[0]
        completed = subprocess.run(
            [python, "-m", "pytest", "--collect-only", "-q", source_file.relative_to(root).as_posix()],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            unresolved.update(wanted)
            continue
        collected = {
            line.strip() for line in completed.stdout.splitlines()
            if line.strip().startswith("tests/") and "::" in line
        }
        forward = {rules.transform_text(nodeid): nodeid for nodeid in collected}
        for candidate_nodeid in wanted:
            source_nodeid = forward.get(candidate_nodeid)
            if source_nodeid is None:
                unresolved.add(candidate_nodeid)
            else:
                resolved.append((candidate_nodeid, source_nodeid))
    return tuple(sorted(resolved)), tuple(sorted(unresolved))


def classify_test_logs(
    candidate_log: str,
    source_log: str,
    *,
    candidate_exit_code: int,
    source_exit_code: int,
    rules: BrandingRules | None = None,
) -> TestBaselineReport:
    """Classify candidate failures against source results on matching test names.

    The candidate node IDs are mapped back through the inverse relationship by
    transforming source node IDs forward using the same deterministic branding
    rules.  A failed candidate test is inherited only when one transformed source
    failure has the identical candidate node ID.
    """
    rules = rules or BrandingRules()
    candidate_failed = failed_nodeids(candidate_log)
    source_failed = failed_nodeids(source_log)
    mapped_source_failures = {rules.transform_text(nodeid) for nodeid in source_failed}
    inherited = tuple(sorted(nodeid for nodeid in candidate_failed if nodeid in mapped_source_failures))
    nastech_only = tuple(sorted(set(candidate_failed) - set(inherited)))
    unclassified = candidate_exit_code != 0 and not candidate_failed
    return TestBaselineReport(
        candidate_exit_code=candidate_exit_code,
        source_exit_code=source_exit_code,
        candidate_failed=candidate_failed,
        source_failed=source_failed,
        inherited_failures=inherited,
        nastech_only_failures=nastech_only,
        unclassified_candidate_failure=unclassified,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-log", required=True)
    parser.add_argument("--candidate-exit-code", required=True, type=int)
    parser.add_argument("--source-log", required=True)
    parser.add_argument("--source-exit-code", required=True, type=int)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    report = classify_test_logs(
        Path(args.candidate_log).read_text(encoding="utf-8", errors="replace"),
        Path(args.source_log).read_text(encoding="utf-8", errors="replace"),
        candidate_exit_code=args.candidate_exit_code,
        source_exit_code=args.source_exit_code,
    )
    Path(args.output).write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.review_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
