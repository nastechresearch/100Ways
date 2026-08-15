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


_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    path: str
    detail: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def scan_snapshot(snapshot: str | Path, upstream: str | Path, expected_upstream_sha: str) -> list[ReadinessIssue]:
    snapshot_path = Path(snapshot)
    upstream_path = Path(upstream)
    issues: list[ReadinessIssue] = []
    manifest_path = snapshot_path / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [ReadinessIssue("manifest", "manifest.json", f"cannot read manifest: {exc}")]
    actual_sha = _git(upstream_path, "rev-parse", "HEAD")
    if manifest.get("upstream_sha") != actual_sha:
        issues.append(ReadinessIssue("source-sha", "manifest.json", "snapshot source SHA does not match freshly fetched upstream HEAD"))
    if expected_upstream_sha and actual_sha != expected_upstream_sha:
        issues.append(ReadinessIssue("freshness", ".git", "upstream changed during the synchronization run; retry against the latest direct source"))
    license_path = snapshot_path / "LICENSE"
    if not license_path.is_file() or not license_path.read_text(encoding="utf-8", errors="ignore").startswith("MIT License"):
        issues.append(ReadinessIssue("license", "LICENSE", "MIT license text is missing or changed"))
    runner = snapshot_path / "scripts" / "run_tests.sh"
    if not runner.is_file() or not os.access(runner, os.X_OK):
        issues.append(ReadinessIssue("test-runner-mode", "scripts/run_tests.sh", "test runner must exist and be executable"))
    if (snapshot_path / ".git").exists():
        issues.append(ReadinessIssue("snapshot-git", ".git", "snapshot must not contain repository metadata"))
    for path in snapshot_path.rglob("*"):
        if not path.is_file() or "node_modules" in path.parts or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff2", ".pdf"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
            issues.append(ReadinessIssue("credential-signal", str(path.relative_to(snapshot_path)), "credential-like value detected"))
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--expected-upstream-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    issues = scan_snapshot(args.snapshot, args.upstream, args.expected_upstream_sha)
    body = {"gate": "PASS" if not issues else "FAIL", "issues": [asdict(issue) for issue in issues]}
    Path(args.output).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(body, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
