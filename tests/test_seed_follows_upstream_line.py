"""The seeded version line follows the upstream stable the synced tree carries.

``scripts/releases/versioning.py`` falls back to its ``SEED`` constant while the
repository has published no stable release.  Upstream never reads it -- its
published head has overridden the seed on every read -- so upstream could leave
it naming the last CalVer-era release (``0.21.5``).  The fork has published zero
stables, so the seed IS the version line every canary is stamped from, and a
tree synced from an upstream commit carrying ``v0.22.7`` shipped canaries as
``v0.21.5+canary.*`` (nastech-agent PR #213).  The file is upstream-owned, so
branding re-derives it every sync and the hand-fixed value is lost; these tests
pin the rule that rewrites it durably.

What they guarantee:

* the seed is rewritten to the newest ``vX.Y.Z`` stable merged into the head of
  the fresh upstream clone -- the CalVer line, a prerelease on the tip, and a
  stable on a branch that never landed are all excluded, matching the fork's
  ``STABLE_TAG_RE`` (``nastech_cli/update_channel.py``);
* a moved ``SEED`` assignment or a moved seed comment stops the sync
  (``ForkSeedLineError``) instead of shipping a seed nothing re-derives, and an
  unreadable/absent upstream tag set stops it too;
* the rule is a fixed point: a second pass reports nothing and the rewritten
  comment -- PR #213's explanation, without its example versions -- appears
  exactly once, so no pass duplicates it;
* ``reconcile_tree`` dispatches it and records the rewrite in ``result.fixed``,
  which the parity gates require.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from hundredways import updates

_VERSIONING = "scripts/releases/versioning.py"

# The file exactly as branding reproduces it from upstream today: upstream's
# CalVer comment directly above the assignment it seeds the line with.
_UPSTREAM_SHAPE = '''from __future__ import annotations

from nastech_cli.update_channel import STABLE_TAG_RE

# The legacy CalVer line published up to 0.21.5 (v2026.9.24); the pipeline continues it.
SEED = "0.21.5"
BUMPS = ("major", "minor", "patch")
'''


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                          capture_output=True, text=True)


def _upstream_clone(root: Path) -> Path:
    """A fresh full clone whose merged head carries exactly one newest stable.

    ``v0.22.7`` is the answer; every other tag tests one exclusion:
    ``v0.21.5``/``v0.22.6`` are older stables, ``v2026.9.18`` is the CalVer
    shape, ``v0.23.0-rc.1`` is a prerelease on the head itself, and ``v0.99.0``
    is a newer stable on a branch that was never merged -- the clone has the
    tag, the synced tree does not carry it.
    """
    origin = root / "origin.git"
    seed = root / "seed"
    _git(root, "init", "--bare", "-b", "main", str(origin))
    _git(root, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "fixture")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    for step, tag in enumerate(("v0.21.5", "v2026.9.18", "v0.22.6", "v0.22.7")):
        (seed / "file.txt").write_text(f"step {step}\n", encoding="utf-8")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", f"release {tag}")
        _git(seed, "tag", tag)
    _git(seed, "commit", "--allow-empty", "-m", "next attempt")
    _git(seed, "tag", "v0.23.0-rc.1")
    _git(seed, "checkout", "-b", "never-landed")
    _git(seed, "commit", "--allow-empty", "-m", "a stable nobody shipped")
    _git(seed, "tag", "v0.99.0")
    _git(seed, "checkout", "main")
    _git(seed, "push", str(origin), "main", "never-landed", "refs/tags/*:refs/tags/*")
    # The clone the pipeline reconciles against is pull_hermes' shape: a plain
    # ``git clone --no-local``, no --no-tags, no --depth -- tags included.
    _git(root, "clone", "--no-local", str(origin), str(root / "src"))
    return root / "src"


@pytest.fixture
def tree(tmp_path):
    dst = tmp_path / "tree"
    (dst / Path(_VERSIONING).parent).mkdir(parents=True)
    (dst / _VERSIONING).write_text(_UPSTREAM_SHAPE, encoding="utf-8")
    return dst


@pytest.fixture
def src(tmp_path):
    return _upstream_clone(tmp_path)


def _seed_of(dst: Path) -> str:
    text = (dst / _VERSIONING).read_text(encoding="utf-8")
    return re.search(r'^SEED = "(.*)"$', text, re.MULTILINE).group(1)


def test_seed_rewritten_to_newest_qualifying_stable(tree, src):
    assert updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src)) == [_VERSIONING]
    text = (tree / _VERSIONING).read_text(encoding="utf-8")
    assert _seed_of(tree) == "0.22.7"
    # Excluded tag shapes never reach the seed, and upstream's CalVer comment
    # -- which would contradict the rewritten value -- is gone.
    assert "2026.9.18" not in text and "0.99.0" not in text and "0.23.0" not in text
    assert updates._UPSTREAM_SEED_COMMENT not in text
    assert text.count(updates._FORK_SEED_HEAD) == 1
    # The lines beside the assignment are untouched: only the block the rule
    # owns moved.
    assert 'BUMPS = ("major", "minor", "patch")' in text


def test_rewritten_comment_names_no_version(tree, src):
    """The seed moves every sync, so nothing but the assignment may name one."""
    updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src))
    lines = [line for line in updates._FORK_SEED_COMMENT.splitlines() if line.strip()]
    assert all(not any(c.isdigit() for c in line) for line in lines)


def test_second_pass_reports_nothing_and_changes_no_bytes(tree, src):
    updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src))
    before = (tree / _VERSIONING).read_bytes()
    assert updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src)) == []
    assert (tree / _VERSIONING).read_bytes() == before
    again = updates.reconcile_tree(str(tree), upstream_src=str(src))
    assert _VERSIONING not in again.fixed
    assert (tree / _VERSIONING).read_bytes() == before


def test_remarked_tree_only_moves_the_value(tree, src):
    """A tree already carrying the fork's seed comment never gets a second one."""
    (tree / _VERSIONING).write_text(
        _UPSTREAM_SHAPE.replace(updates._UPSTREAM_SEED_COMMENT, updates._FORK_SEED_COMMENT),
        encoding="utf-8")
    assert updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src)) == [_VERSIONING]
    text = (tree / _VERSIONING).read_text(encoding="utf-8")
    assert _seed_of(tree) == "0.22.7"
    assert text.count(updates._FORK_SEED_HEAD) == 1


def test_dispatched_by_reconcile_tree_and_recorded(tree, src):
    """An unrecorded rewrite bricks the parity gate; dispatch is the contract."""
    result = updates.reconcile_tree(str(tree), upstream_src=str(src))
    assert result.fixed.count(_VERSIONING) == 1
    assert _seed_of(tree) == "0.22.7"


def test_moved_seed_assignment_stops_the_sync(tree, src):
    path = tree / _VERSIONING
    text = path.read_text(encoding="utf-8")
    path.write_text(text + 'SEED = "0.21.5"\n', encoding="utf-8")
    with pytest.raises(updates.ForkSeedLineError) as caught:
        updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src))
    assert _VERSIONING in str(caught.value)


def test_moved_seed_comment_stops_the_sync(tree, src):
    path = tree / _VERSIONING
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(updates._UPSTREAM_SEED_COMMENT.rstrip("\n"),
                                 "# Upstream reworded the line above the seed."),
                    encoding="utf-8")
    with pytest.raises(updates.ForkSeedLineError) as caught:
        updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src))
    assert _VERSIONING in str(caught.value)


def test_unavailable_upstream_tags_stop_the_sync(tree, tmp_path):
    """No clone, or a clone whose tags cannot be read, is a stop not a guess."""
    absent = tmp_path / "no-such-clone"
    with pytest.raises(updates.ForkSeedLineError) as caught:
        updates._reconcile_fork_seed_from_upstream_line(str(tree), str(absent))
    assert "pull_hermes" in str(caught.value)

    missing = tmp_path / "not-a-clone"
    missing.mkdir()
    with pytest.raises(updates.ForkSeedLineError) as caught:
        updates._reconcile_fork_seed_from_upstream_line(str(tree), str(missing))
    assert "reading merged tags" in str(caught.value)
    assert "stops" in str(caught.value)

    # A clone with tags but no merged stable at all (only CalVer + prerelease)
    # fails the grammar filter and stops too.
    root = tmp_path / "calver-only"
    root.mkdir()
    origin = root / "origin.git"
    seed = root / "seed"
    _git(root, "init", "--bare", "-b", "main", str(origin))
    _git(root, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "fixture")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    (seed / "file.txt").write_text("only the calendar line\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "release v2026.9.18")
    _git(seed, "tag", "v2026.9.18")
    _git(seed, "tag", "v0.23.0-rc.1")
    _git(seed, "push", str(origin), "main", "refs/tags/*:refs/tags/*")
    _git(root, "clone", "--no-local", str(origin), str(root / "src"))
    with pytest.raises(updates.ForkSeedLineError) as caught:
        updates._reconcile_fork_seed_from_upstream_line(str(tree), str(root / "src"))
    assert "no stable" in str(caught.value)


def test_tree_without_a_release_plane_is_left_alone(tmp_path, src):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert updates._reconcile_fork_seed_from_upstream_line(str(empty), str(src)) == []


def test_versioning_module_without_a_seed_is_left_alone(tree, src):
    """No seeded line means no stale canary can come from it: nothing to re-derive."""
    (tree / _VERSIONING).write_text(
        '"""The line comes only from published releases; there is no seed."""\n',
        encoding="utf-8")
    assert updates._reconcile_fork_seed_from_upstream_line(str(tree), str(src)) == []


# --- the seed block and the release-plane merge are one region ---------------
#
# Candidate 6acd9332: ``scripts/releases/versioning.py`` regenerated to
# upstream's one-line ``_gh``, while its test still injected ``run=`` and
# ``sleep=``.  Fork PR #213 wrote both halves into the same file; the merge that
# replays the fork's release plane refused the whole file because this rule had
# already re-written the six lines above ``SEED``, and the retry seam went with
# it -- two reds in ``Python tests (1/8)`` plus ``HX006  _gh  subprocess/urlopen
# without timeout`` in ``Python lints``, on every sync from here.

_PLANE_BASE = '''import json
import subprocess


# The legacy CalVer line published up to 0.21.5 (v2026.9.24); the pipeline continues it.
SEED = "0.21.5"


BUMPS = ("major", "minor", "patch")


def published_stable_version(repository: str) -> str:
    return max(repository, SEED)


def compare(a: str, b: str) -> int:
    return (a > b) - (a < b)


def _gh(argv: list[str]) -> str:
    return subprocess.check_output(argv, text=True, encoding="utf-8")
'''

# Upstream keeps working in the middle of the file -- far from both regions the
# fork edits, because that is what upstream does between two syncs.
_PLANE_TIP = _PLANE_BASE.replace(
    "    return max(repository, SEED)",
    "    return max(repository, SEED, key=lambda value: value)",
)

# Fork PR #213 rewrote the seed block, and the one shared ``gh api`` call under
# it: every check on the repository spends one Actions installation's request
# budget, so a 403 here is a queue position, and the tests inject ``run=`` and
# ``sleep=`` to watch it retried.
_PLANE_FORK = _PLANE_TIP.replace(
    "# The legacy CalVer line published up to 0.21.5 (v2026.9.24); the pipeline continues it.\n"
    'SEED = "0.21.5"',
    "# The head of the line before any release is published from here. Upstream's own\n"
    "# seed named its last CalVer-era release, and the canary is stamped from it\n"
    "# until a stable publishes.\n"
    'SEED = "0.22.7"',
).replace(
    "def _gh(argv: list[str]) -> str:\n"
    '    return subprocess.check_output(argv, text=True, encoding="utf-8")\n',
    "def _gh(argv: list[str], *, run=subprocess.run, sleep=None, tries: int = 5) -> str:\n"
    "    for attempt in range(1, tries + 1):\n"
    '        done = run(argv, text=True, encoding="utf-8", capture_output=True)\n'
    "        if done.returncode == 0:\n"
    "            return done.stdout\n"
    "    raise subprocess.CalledProcessError(done.returncode, argv)\n",
)


def _plane(tmp_path, *, ours_text, fork_text):
    """The three trees the sync reads: upstream with history, candidate, fork."""
    origin = tmp_path / "plane-origin.git"
    seed = tmp_path / "plane-seed"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "fixture")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    for text, tag in ((_PLANE_BASE, "v0.22.7"), (_PLANE_TIP, "")):
        path = seed / _VERSIONING
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", "upstream works on the release plane")
        if tag:
            _git(seed, "tag", tag)
    baseline = _git(seed, "rev-parse", "v0.22.7").stdout.strip()
    _git(seed, "push", "--quiet", str(origin), "main", "refs/tags/*:refs/tags/*")

    src = tmp_path / "plane-src"
    _git(tmp_path, "clone", "--quiet", "--no-local", str(origin), str(src))

    dst = tmp_path / "candidate"
    (dst.joinpath(*Path(_VERSIONING).parts[:-1])).mkdir(parents=True)
    (dst / _VERSIONING).write_text(ours_text, encoding="utf-8")

    fork = tmp_path / "nastech-agent"
    (fork.joinpath(*Path(_VERSIONING).parts[:-1])).mkdir(parents=True)
    (fork / _VERSIONING).write_text(fork_text, encoding="utf-8")
    (fork / "manifest.json").write_text(json.dumps({"upstream_sha": baseline}),
                                        encoding="utf-8")
    return str(dst), str(fork), str(src)


def test_the_seed_rule_leaves_the_forks_seed_comment_to_the_fork(tmp_path):
    """Rewriting #213's prose is what made the merge refuse the whole file."""
    dst, fork, src = _plane(tmp_path, ours_text=_PLANE_TIP, fork_text=_PLANE_FORK)
    result = updates.reconcile_tree(dst, fork_root=fork, upstream_src=src)
    text = Path(dst, _VERSIONING).read_text(encoding="utf-8")
    assert result.unresolved == []
    assert text.count(updates._FORK_SEED_HEAD) == 1
    assert updates._UPSTREAM_SEED_COMMENT not in text


def test_the_forks_gh_retry_seam_survives_the_seed_rewrite(tmp_path):
    """One file, two owners: the merge carries the seam, the rule moves the number."""
    dst, fork, src = _plane(tmp_path, ours_text=_PLANE_TIP, fork_text=_PLANE_FORK)
    result = updates.reconcile_tree(dst, fork_root=fork, upstream_src=src)
    text = Path(dst, _VERSIONING).read_text(encoding="utf-8")
    assert "run=subprocess.run, sleep=None, tries: int = 5" in text
    assert "subprocess.check_output" not in text
    assert _seed_of(Path(dst)) == "0.22.7"
    assert "key=lambda value: value" in text
    assert _VERSIONING in result.fixed
