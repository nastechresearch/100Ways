"""Regression: stage-forkcheck.yml must init a real .git, not stub an empty dir.

On 2026-08-23 the upstream test
``tests/nastech_cli/test_ensure_windows_bin_launchers.py::
test_repo_gitignores_the_legacy_bin_dir`` failed in forkcheck with
``git check-ignore`` exit code 128 (``fatal: not a git repository``)
because the workflow extracted the zip but created an empty ``.git/``
directory. The ``.exists()`` existence gate was satisfied, so the test's
``pytest.skip("not running from a git checkout")`` guard did NOT fire,
but ``git check-ignore`` returned 128 because the empty directory was
not a real git repository.

Hermes CI solves this by using ``actions/checkout`` which creates a
fully-initialized repo. 100Ways forkcheck extracts a zip instead, so
we mirror that by running ``git init -q --initial-branch=main``,
configuring a committer identity, and committing the snapshot — making
the extracted tree a real working repo.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "stage-forkcheck.yml"
)


def test_stage_forkcheck_replaces_empty_dot_git_stub_with_git_init() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    # The legacy empty-stub pattern is gone.
    assert 'os.makedirs(os.path.join(tree_dir, ".git"), exist_ok=True)' not in text

    # Three extraction blocks (generate, test, smoke) each call
    # `git -C tree_dir init` to mirror what actions/checkout gives Hermes.
    assert text.count("- name: Extract branded tree") == 3
    assert text.count('"git", "-C", tree_dir, "init"') == 3
    assert text.count('"git", "-C", tree_dir, "add", "-A"') == 3
    assert text.count('"git", "-C", tree_dir, "commit"') == 3
