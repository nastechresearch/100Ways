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
    """Rebrand a unified diff while preserving valid git patch structure."""
    out: list[str] = []
    for line in patch.splitlines(keepends=True):
        newline = "\n" if line.endswith("\n") else ""
        stripped = line[:-1] if newline else line
        if stripped.startswith("diff --git "):
            parts = stripped.split(" ", 3)
            if len(parts) == 4:
                out.append(" ".join(parts[:2] + [rules.transform_path(parts[2]), rules.transform_path(parts[3])]) + newline)
                continue
        if stripped.startswith("--- a/"):
            out.append("--- " + rules.transform_path(stripped[4:]) + newline)
            continue
        if stripped.startswith("+++ b/"):
            out.append("+++ " + rules.transform_path(stripped[4:]) + newline)
            continue
        m = _RENAME_FROM.match(stripped)
        if m:
            out.append("rename from " + rules.transform_path(m.group(1)) + newline)
            continue
        m = _RENAME_TO.match(stripped)
        if m:
            out.append("rename to " + rules.transform_path(m.group(1)) + newline)
            continue
        if line.startswith(("+", "-")):
            out.append(line[0] + rules.transform_text(line[1:]))
        else:
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
    parent = _git_ok(repo, "rev-parse", f"{sha}^1").strip()
    changes = _git_ok(repo, "diff", "--name-status", "--find-renames", parent, sha).splitlines()
    for change in changes:
        parts = change.split("\t")
        status = parts[0][0]
        old_path = parts[1]
        new_path = parts[-1]
        if status == "D":
            target = os.path.join(wt, rules.transform_path(old_path))
            if os.path.exists(target):
                os.remove(target)
            continue
        source_path = new_path
        target_rel = rules.transform_path(new_path)
        target = os.path.join(wt, target_rel)
        data = subprocess.check_output(["git", "-C", repo, "show", f"{sha}:{source_path}"], stderr=subprocess.PIPE)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            rendered = data
        else:
            rendered = rules.transform_text(text).encode("utf-8")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(rendered)

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
    """Create an isolated worktree on a unique temporary branch."""
    tmp = tempfile.mkdtemp(prefix="100ways-")
    temp_branch = f"100ways-port-{os.getpid()}-{os.path.basename(tmp)}"
    proc = _git(repo, "worktree", "add", "-b", temp_branch, tmp, branch)
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SyncError(f"worktree add failed: {proc.stderr.strip()}")
    return tmp


def _remove_worktree(repo: str, wt: str) -> None:
    branch = _git_ok(wt, "branch", "--show-current").strip() if os.path.isdir(os.path.join(wt, ".git")) else ""
    _git(repo, "worktree", "remove", "--force", wt)
    if branch:
        _git(repo, "branch", "-D", branch)
    shutil.rmtree(wt, ignore_errors=True)
