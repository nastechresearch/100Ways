"""Deterministic readiness scans performed before #344 candidate publication."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from .forklint import run_fork_lint
from .integrity import audit_candidate_tree, audit_manifest_provenance, tree_digest
from .rules import BrandingRules

_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)

# The fork re-lints the candidate itself (``.github/workflows/lint.yml``), and the same
# three classes of red keep landing there after this gate went green: a fork checker
# the engine never ran, a char-count cap the brand transform alone pushes past, and a
# ``subprocess.run`` block the engine generates into fork code.  ``forklint`` runs the
# fork's rules over the candidate, and its blocking findings are blocking issues here;
# what the fork itself declares ``continue-on-error`` lands as an advisory issue, which
# the report shows and the gate does not fail on -- the fork's own contract, not a
# judgement made here.
_FORK_LINT_CODE = "fork-lint"
_FORK_LINT_ADVISORY_CODE = "fork-lint-advisory"

BLOCKER = "blocker"
ADVISORY = "advisory"


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    path: str
    detail: str


def is_blocking(issue: ReadinessIssue) -> bool:
    """Advisory is only ever advisory because the FORK says so.

    ``fork-lint-advisory`` is the single code this gate does not fail on: it is the
    outcome of a ``lint.yml`` step that carries ``continue-on-error: true``, so the
    fork declared it non-fatal and this gate has no authority to promote it.
    Everything else blocks -- including every ``unavailable`` forklint outcome, since
    a check that did not run is not a check that passed.
    """
    return issue.code != _FORK_LINT_ADVISORY_CODE


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _source_delta_issues(snapshot: Path, manifest: dict) -> list[ReadinessIssue]:
    """Reject source paths that upstream retired but the candidate still retains."""
    delta = manifest.get("source_delta")
    provenance = manifest.get("source_provenance")
    baseline_sha = (
        provenance.get("baseline_sha", "") if isinstance(provenance, dict) else ""
    )
    if not isinstance(delta, dict):
        if baseline_sha:
            return [
                ReadinessIssue(
                    "source-delta",
                    "manifest.json",
                    "baseline exists but direct upstream tree-delta evidence is missing",
                )
            ]
        return []
    if baseline_sha and delta.get("complete") is not True:
        return [
            ReadinessIssue(
                "source-delta",
                "manifest.json",
                "baseline exists but direct upstream tree-delta evidence is incomplete",
            )
        ]
    owned_paths = {
        value for value in delta.get("owned_paths", []) if isinstance(value, str)
    }
    changes = delta.get("changes", [])
    if not isinstance(changes, list):
        return [
            ReadinessIssue("source-delta", "manifest.json", "changes must be a list")
        ]
    issues: list[ReadinessIssue] = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or change.get("status") not in {"deleted", "renamed"}
        ):
            continue
        retired = change.get("old_mapped")
        if (
            not isinstance(retired, str)
            or not retired
            or Path(retired).is_absolute()
            or ".." in Path(retired).parts
        ):
            issues.append(
                ReadinessIssue("source-delta", "manifest.json", "retired path is invalid")
            )
            continue
        if retired not in owned_paths and (snapshot / retired).is_file():
            issues.append(
                ReadinessIssue(
                    "stale-upstream-path",
                    retired,
                    "candidate retains a path retired by direct Hermes source evidence",
                )
            )
    return issues


def _credential_signals(root: Path) -> list[ReadinessIssue]:
    """Return credential-pattern findings from a tree, excluding binary assets."""
    issues: list[ReadinessIssue] = []
    ignored_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff2", ".pdf"}
    for path in root.rglob("*"):
        if (
            not path.is_file()
            or "node_modules" in path.parts
            or path.suffix.lower() in ignored_suffixes
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            issues.append(ReadinessIssue(
                "credential-signal",
                str(path.relative_to(root)),
                "credential-like value detected",
            ))
    return issues


def _fork_lint_issues(snapshot: Path, upstream: Path, fork_root: str) -> list[ReadinessIssue]:
    """The fork's own static checks, run over the candidate before it is shipped.

    An empty ``fork_root`` skips the simulation entirely, and that path exists only for
    tests: :func:`main` requires ``--fork-root``, so the scan CI runs cannot publish an
    un-simulated tree.  A wrong ``fork_root`` is not skipped -- :func:`run_fork_lint`
    reports it as unavailable, which is blocking.
    """
    if not fork_root:
        return []
    return [
        ReadinessIssue(
            _FORK_LINT_CODE if issue.blocking else _FORK_LINT_ADVISORY_CODE,
            issue.path or issue.script,
            f"{issue.script}: {issue.detail}",
        )
        for issue in run_fork_lint(fork_root, str(snapshot), str(upstream))
    ]


def scan_snapshot(
    snapshot: str | Path,
    upstream: str | Path,
    expected_upstream_sha: str,
    fork_root: str = "",
) -> list[ReadinessIssue]:
    """Everything that must be true before this candidate becomes a PR.

    ``fork_root`` is a checkout of the fork at its current ``main``: the candidate is
    linted by the fork's own checkers and ruff rules, not by an engine copy of them.
    """
    snapshot_path = Path(snapshot)
    upstream_path = Path(upstream)
    issues: list[ReadinessIssue] = []
    manifest_path = snapshot_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [ReadinessIssue("manifest", "manifest.json", f"cannot read manifest: {exc}")]
    actual_sha = _git(upstream_path, "rev-parse", "HEAD")
    issues.extend(
        ReadinessIssue(issue.code, issue.path, issue.detail)
        for issue in audit_manifest_provenance(
            manifest_path, expected_upstream_sha=actual_sha
        )
    )
    if expected_upstream_sha and actual_sha != expected_upstream_sha:
        issues.append(
            ReadinessIssue(
                "freshness",
                ".git",
                "upstream changed during the synchronization run; retry against "
                "the latest direct source",
            )
        )
    issues.extend(_source_delta_issues(snapshot_path, manifest))
    license_path = snapshot_path / "LICENSE"
    license_ok = license_path.is_file() and license_path.read_text(
        encoding="utf-8", errors="ignore"
    ).startswith("MIT License")
    if not license_ok:
        issues.append(
            ReadinessIssue("license", "LICENSE", "MIT license text is missing or changed")
        )
    runner = snapshot_path / "scripts" / "run_tests.sh"
    if not runner.is_file() or not os.access(runner, os.X_OK):
        issues.append(
            ReadinessIssue(
                "test-runner-mode",
                "scripts/run_tests.sh",
                "test runner must exist and be executable",
            )
        )
    if (snapshot_path / ".git").exists():
        issues.append(
            ReadinessIssue("snapshot-git", ".git", "snapshot must not contain repository metadata")
        )
    issues.extend(
        ReadinessIssue(issue.code, issue.path, issue.detail)
        for issue in audit_candidate_tree(snapshot_path)
    )
    # Keep inherited source fixtures visible in the weekly report, but do not
    # let them block candidate publication.  Any credential-like value added by
    # branding, the engine-owned asset registry, or fork preservation remains a
    # prepublication failure.
    rules = BrandingRules()
    source_credential_paths = {
        rules.transform_path(issue.path) for issue in _credential_signals(upstream_path)
    }
    for issue in _credential_signals(snapshot_path):
        if issue.path not in source_credential_paths:
            issues.append(issue)
    issues.extend(_fork_lint_issues(snapshot_path, upstream_path, fork_root))
    return issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--expected-upstream-sha", required=True)
    parser.add_argument("--fork-root", required=True)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    issues = scan_snapshot(
        args.snapshot, args.upstream, args.expected_upstream_sha, args.fork_root
    )
    blockers = [issue for issue in issues if is_blocking(issue)]
    body = {
        "gate": "PASS" if not blockers else "FAIL",
        "issues": [
            {**asdict(issue), "severity": BLOCKER if is_blocking(issue) else ADVISORY}
            for issue in issues
        ],
        "candidate_tree_sha256": tree_digest(args.snapshot),
    }
    Path(args.output).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(body, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
