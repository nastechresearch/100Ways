"""Port engine: apply upstream Hermes commits onto the Nastech fork.

For each upstream commit (from a ``git fetch`` of the ``upstream`` remote),
the engine:

  1. reads the commit's patch;
  2. rebrands it (paths + content) through the branding rules;
  3. applies it to a clean checkout of our branch;
  4. runs the parity gate on the resulting worktree;
  5. commits as ``port(<sha>): <subject>`` only when the gate passes.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

from .rules import BrandingRules, is_locked_path
from .verify import VerifyReport, verify_port


class SyncError(Exception):
    pass


@dataclass
class PortResult:
    upstream_sha: str
    subject: str
    port_sha: str = ""
    status: str = "skipped"   # ported | skipped | failed | would-port
    report: VerifyReport | None = None
    error: str = ""


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )


def _git_ok(repo: str, *args: str) -> str:
    proc = _git(repo, *args)
    if proc.returncode != 0:
        raise SyncError(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


# --- patch rebranding -------------------------------------------------------

_RENAME_FROM = re.compile(r"^rename from (.+)$")
_RENAME_TO = re.compile(r"^rename to (.+)$")


def _rebrand_patch(patch: str, rules: BrandingRules) -> str:
    """Rebrand a unified diff: path headers, rename lines, and content."""
    lines = patch.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        stripped = line.rstrip("\n")
        # path header lines
        if stripped.startswith(("--- a/", "+++ b/", "diff --git a/")):
            out.append(rules.transform_path(stripped) + "\n")
            continue
        # rename lines
        m = _RENAME_FROM.match(stripped)
        if m:
            out.append("rename from " + rules.transform_path(m.group(1)) + "\n")
            continue
        m = _RENAME_TO.match(stripped)
        if m:
            out.append("rename to " + rules.transform_path(m.group(1)) + "\n")
            continue
        # content lines (context stays untouched)
        if line.startswith(("+", "-")):
            body = line[1:]
            out.append(line[0] + rules.transform_text(body))
            continue
        out.append(line)
    return "".join(out)


# --- commit enumeration ------------------------------------------------------

def new_upstream_commits(repo: str, upstream_main: str, ours: str) -> list[str]:
    """Upstream commits reachable from ``upstream_main`` but not ours.

    Returned oldest-first so a port run processes history in order.
    """
    out = _git_ok(repo, "rev-list", "--reverse", f"{ours}..{upstream_main}")
    return [c for c in out.splitlines() if c]


# --- port execution ----------------------------------------------------------

def port_commits(
    repo: str,
    upstream_main: str,
    branch: str,
    rules: BrandingRules | None = None,
    threshold: float = 0.99,
    dry_run: bool = False,
) -> list[PortResult]:
    """Port every upstream commit not yet on ``branch``.

    Runs in a detached throwaway worktree so a failed port never touches the
    working branch.  Each port is committed with the ``port(<sha>):`` prefix
    and its subject, exactly like the historical hand-ported commits.
    """
    rules = rules or BrandingRules()
    results: list[PortResult] = []
    head = _git_ok(repo, "rev-parse", "HEAD").strip()

    commits = new_upstream_commits(repo, upstream_main, head)
    if not commits:
        return results

    wt = _worktree(repo, branch)
    last_good = _git_ok(repo, "rev-parse", "HEAD").strip()
    try:
        for sha in commits:
            subject = _git_ok(repo, "log", "-1", "--format=%s", sha).strip()
            res = PortResult(upstream_sha=sha, subject=subject)

            parents = _git_ok(repo, "rev-list", "--parents", "-n", "1", sha).split()
            if len(parents) > 2:
                res.status = "skipped (merge commit)"
                results.append(res)
                continue

            if dry_run:
                res.status = "would-port"
                results.append(res)
                continue

            try:
                _port_one(wt, repo, sha, rules, threshold, res, last_good)
                last_good = res.port_sha
            except SyncError as exc:
                res.status = "failed"
                res.error = str(exc)
                _git(wt, "reset", "--hard", last_good)
                _git(wt, "clean", "-fd")
            results.append(res)
    finally:
        _remove_worktree(repo, wt)

    return results


def _port_one(
    wt: str, repo: str, sha: str, rules: BrandingRules,
    threshold: float, res: PortResult, last_good: str,
) -> None:
    patch = _git_ok(repo, "show", "--format=email", sha)
    rebranded = _rebrand_patch(patch, rules)
    proc = subprocess.run(
        ["git", "-C", wt, "apply", "--3way", "--allow-empty", "--whitespace=nowarn", "-"],
        input=rebranded,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SyncError(f"git apply failed: {proc.stderr.strip() or proc.stdout.strip()}")

    _git_ok(wt, "add", "-A")
    _git_ok(wt, "commit", "-m", f"port({sha[:8]}): {res.subject}")
    res.port_sha = _git_ok(wt, "rev-parse", "HEAD").strip()

    report = verify_port(repo, sha, res.port_sha, rules)
    res.report = report

    if report.failed or report.pass_ratio < threshold:
        _git(wt, "reset", "--hard", last_good)
        _git(wt, "clean", "-fd")
        raise SyncError(f"parity gate failed: {report.summary()}")

    res.status = "ported"


def _worktree(repo: str, branch: str) -> str:
    """Create a throwaway worktree on ``branch`` for isolation."""
    tmp = tempfile.mkdtemp(prefix="100ways-")
    proc = _git(repo, "worktree", "add", "--detach", tmp, branch)
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SyncError(f"worktree add failed: {proc.stderr.strip()}")
    return tmp


def _remove_worktree(repo: str, wt: str) -> None:
    _git(repo, "worktree", "remove", "--force", wt)
    shutil.rmtree(wt, ignore_errors=True)
