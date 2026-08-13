import pytest

from hundredways.port import PortResult, _rebrand_patch, new_upstream_commits, port_commits
from hundredways.rules import BrandingRules
from hundredways.verify import gate_passes
from tests.conftest import commit, git, git_repo


def test_rebrand_patch_renames_and_rewrites(git_repo):
    rules = BrandingRules()
    patch = (
        "diff --git a/tools/hermes_runner.py b/tools/hermes_runner.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/tools/hermes_runner.py\n"
        "+++ b/tools/hermes_runner.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-def run_hermes():\n"
        "+def run_nastech():\n"
    )
    out = _rebrand_patch(patch, rules)
    assert "tools/nastech_runner.py" in out
    assert "hermes_runner" not in out
    assert "run_nastech" in out


def _make_fork_with_upstream_lead(git_repo):
    """base on master; upstream/main gets one new commit; sync-upstream at base."""
    base = commit(git_repo, {"a.txt": "a"}, "base")
    git(git_repo, "branch", "upstream/main", base)
    git(git_repo, "checkout", "-q", "upstream/main")
    commit(git_repo, {"tools/hermes_runner.py": "def run_hermes(): pass"}, "upstream change")
    git(git_repo, "checkout", "-q", "master")
    git(git_repo, "branch", "sync-upstream", base)
    return base


def test_new_upstream_commits(git_repo):
    base = _make_fork_with_upstream_lead(git_repo)
    ours = git(git_repo, "rev-parse", "HEAD")
    commits = new_upstream_commits(git_repo, "upstream/main", ours)
    assert len(commits) == 1
    assert git(git_repo, "rev-parse", "upstream/main") == base or commits


def test_port_dry_run_reports_would_port(git_repo):
    _make_fork_with_upstream_lead(git_repo)
    results = port_commits(
        git_repo, "upstream/main", "sync-upstream",
        rules=BrandingRules(), dry_run=True,
    )
    assert len(results) == 1
    assert results[0].status == "would-port"
    assert results[0].subject == "upstream change"


def test_port_applies_and_verifies(git_repo):
    _make_fork_with_upstream_lead(git_repo)
    results = port_commits(
        git_repo, "upstream/main", "sync-upstream",
        rules=BrandingRules(), dry_run=False,
    )
    assert results[0].status == "ported"
    assert results[0].port_sha
    assert results[0].report and gate_passes(results[0].report, 1.0)
    tree = git(git_repo, "show", f"{results[0].port_sha}:tools/nastech_runner.py")
    assert "nastech" in tree


def test_port_missing_branch_raises(git_repo):
    commit(git_repo, {"a.txt": "a"}, "base")
    git(git_repo, "branch", "upstream/main")
    git(git_repo, "checkout", "-q", "upstream/main")
    commit(git_repo, {"b.txt": "b"}, "upstream work")
    git(git_repo, "checkout", "-q", "master")
    with pytest.raises(Exception):
        port_commits(git_repo, "upstream/main", "no-such-branch", dry_run=False)
