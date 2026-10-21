"""Release-tag sampling: a fork can only update from releases it published itself.

``install-e2e.yml`` builds its matrix from release tags sampled at runtime, and
``stable-release.yml`` asks for three of them.  A fork inherits the source
project's release tags when it is created, so the "oldest" the sampler is asked
for names a commit the fork never branded: its installer clones a repository the
fork does not own and writes a checkout the fork's updater never looks in, and
the leg dies in the driver's first ``git`` command with ``fatal: cannot change
to '.../.nastech/nastech-agent': No such file or directory`` (nastech-agent run
39542501628, jobs 119033266029 / 119033266322, exit 128).

These tests pin the three things the fix has to guarantee:

* ``_reconcile_fork_release_baselines`` patches **both** samplers -- a release
  run reads ``scripts/releases/pick_tags.py``, a dispatch reads
  ``scripts/sandbox/pick-release-tags.sh``, and if only one filtered, a
  maintainer would chase legs the release gate never asked for;
* it is re-derived after every brand and is a fixed point, and a moved anchor
  raises instead of shipping the old sampling again;
* the patched samplers keep chronology and spread exactly as upstream wrote them
  -- an empty tree ships no installer, so it says nothing about who published
  the tag and stays a candidate.  Upstream's own sampling assertions still hold
  against the patched file, which is the guard against this filter quietly
  narrowing what a release verifies.

``tests/fixtures/release_baselines`` holds both shapes the samplers have had:
``legacy`` (what the fork ships today) and ``tip`` (what the next sync brings).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hundredways import updates

FIXTURES = Path(__file__).parent / "fixtures" / "release_baselines"
SHAPES = ("legacy", "tip")
_PICK_TAGS = "scripts/releases/pick_tags.py"
_SHELL = "scripts/sandbox/pick-release-tags.sh"
_BASELINE_MARK = updates._FORK_BASELINE_MARK

# A tag nobody branded: it ships an installer, and its tree carries the source
# project's package directory rather than this fork's.
UNBRANDED = ("v2026.3.12", "2026-03-12T00:00:00Z",
             [("scripts/install.sh", "clone https://example.invalid/source-agent.git\n"),
              ("hermes_cli/__init__.py", "")])
# A tag this fork published.
BRANDED = ("v2026.9.18", "2026-09-18T00:00:00Z",
           [("scripts/install.sh", "clone https://example.invalid/nastech-agent.git\n"),
            ("nastech_cli/__init__.py", "")])
# A tag whose tree ships no installer at all: no evidence either way.
NO_INSTALLER = ("v2026.9.24", "2026-09-24T00:00:00Z", [("README.md", "notes\n")])
# An older fork release, so a filtered spread still starts at a real baseline.
BRANDED_OLD = ("v2026.8.11", "2026-08-11T00:00:00Z",
               [("scripts/install.sh", "clone https://example.invalid/nastech-agent.git\n"),
                ("nastech_cli/__init__.py", "")])


def _git(cwd, *args, env=None):
    return subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True,
                          capture_output=True, text=True)


def _tree(shape: str, dst: Path) -> Path:
    """Copy one fixture shape into ``dst`` at the paths the sync reconciles."""
    for rel in (_PICK_TAGS, _SHELL):
        source = FIXTURES / shape / rel
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return dst


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_repo(root: Path, releases) -> Path:
    """A directory holding a tag-carrying origin plus three checkouts of it.

    The three are the checkout shapes the sampling job really runs in, and each
    pins a different property of the patched filter:

    ``clone``
        a plain full clone -- what the matrix has to answer here at all.
    ``tagless``
        ``--no-tags``, so nothing local names a release.  Only the ``tip``
        samplers can use it: they enumerate origin with ``ls-remote``, and the
        probe then fetches the tag it is judging instead of treating an absent
        ref as evidence the tag is foreign.  The ``legacy`` samplers enumerate
        the checkout's own refs, which is upstream's documented contract -- the
        job checks out with ``fetch-tags: true``.
    ``worktreeless``
        ``--no-checkout``, so the working tree holds no files even though the
        objects do.  Both shapes answer it, because the filter reads the tag's
        tree with ``ls-tree`` and never a path on disk -- which is what a
        ``sparse`` checkout in ``install-e2e.yml`` looks like from the inside.
    """
    origin = root / "origin.git"
    seed = root / "seed"
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "--bare", "-b", "main", str(origin))
    _git(root, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "fixture")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    for tag, date, files in releases:
        # Each release owns its whole tree: the sampler judges the tag's own
        # tree, so a package directory left over from the previous tag would be
        # evidence that tag never had.
        for child in seed.glob("*"):
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        for rel, text in files:
            path = seed / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _git(seed, "add", "-A")
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        _git(seed, "commit", "--allow-empty", "-m", tag, env=env)
        _git(seed, "tag", tag, env=env)
    _git(seed, "push", str(origin), "main", "refs/tags/*:refs/tags/*")
    for name, extra in (("clone", ()), ("tagless", ("--no-tags",)),
                        ("worktreeless", ("--no-checkout",))):
        _git(root, "clone", "--no-local", *extra, str(origin), str(root / name))
    return root


def _sampling_env(tmp_path, shape, releases):
    """Return (fixture repos, patched pick_tags module) for one shape."""
    tree = _tree(shape, tmp_path / f"tree-{shape}")
    assert updates._reconcile_fork_release_baselines(str(tree)) == [_PICK_TAGS, _SHELL]
    return _seed_repo(tmp_path / f"repo-{shape}", releases), _load(
        tree / _PICK_TAGS, f"pick_tags_{shape}")


def _shell_picks(script: Path, repo: Path, count: int) -> list:
    run = subprocess.run(["bash", str(script), "--count", str(count), "--repo", str(repo)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


# ---------------------------------------------------------------------------
# the reconcile itself
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shape", SHAPES)
def test_reconcile_patches_both_samplers_and_is_a_fixed_point(shape, tmp_path):
    tree = _tree(shape, tmp_path)
    assert updates._reconcile_fork_release_baselines(str(tree)) == [_PICK_TAGS, _SHELL]
    # A second pass cannot see a hand-edit beside its marker, so the mark is the
    # contract: it has to be what the re-run looks for.
    assert updates._reconcile_fork_release_baselines(str(tree)) == []
    assert updates._reconcile_fork_release_baselines(str(tree)) == []
    for rel in (_PICK_TAGS, _SHELL):
        text = (tree / rel).read_text(encoding="utf-8")
        assert text.count(_BASELINE_MARK) == 1
        assert "_fork_baseline" in text or "baselines+=(" in text


@pytest.mark.parametrize("shape", SHAPES)
def test_reconcile_leaves_the_anchors_it_inserted_before_intact(shape, tmp_path):
    """The filter is inserted, not substituted: upstream's own checks stay put."""
    tree = _tree(shape, tmp_path)
    updates._reconcile_fork_release_baselines(str(tree))
    assert updates._PICK_TAGS_ANCHOR in (tree / _PICK_TAGS).read_text(encoding="utf-8")
    assert updates._PICK_SHELL_ANCHOR in (tree / _SHELL).read_text(encoding="utf-8")


def test_moved_anchor_stops_the_sync(tmp_path):
    tree = _tree("tip", tmp_path)
    (tree / _PICK_TAGS).write_text("def pick_tags(repo, count, exclude=''):\n    return []\n",
                                   encoding="utf-8")
    with pytest.raises(updates.ForkReleaseBaselineError) as caught:
        updates._reconcile_fork_release_baselines(str(tree))
    assert _PICK_TAGS in str(caught.value)
    assert "_reconcile_fork_release_baselines" in str(caught.value)


def test_duplicated_anchor_stops_the_sync(tmp_path):
    tree = _tree("tip", tmp_path)
    path = tree / _PICK_TAGS
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(updates._PICK_TAGS_ANCHOR, updates._PICK_TAGS_ANCHOR * 2, 1),
                    encoding="utf-8")
    with pytest.raises(updates.ForkReleaseBaselineError):
        updates._reconcile_fork_release_baselines(str(tree))


def test_tree_without_the_samplers_is_left_alone(tmp_path):
    assert updates._reconcile_fork_release_baselines(str(tmp_path / "empty")) == []


def test_dispatched_from_reconcile_tree():
    """A rule nothing calls fixes nothing; the sync's dispatch is the caller."""
    source = Path(updates.__file__).read_text(encoding="utf-8")
    dispatch = source.split("def reconcile_tree", 1)[1].split("\ndef ", 1)[0]
    assert "_reconcile_fork_release_baselines" in dispatch


# ---------------------------------------------------------------------------
# what the patched samplers actually return
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shape", SHAPES)
def test_unbranded_baseline_is_dropped_and_the_spread_moves_up(shape, tmp_path):
    repos, picker = _sampling_env(tmp_path, shape, [UNBRANDED, BRANDED_OLD,
                                                    BRANDED, NO_INSTALLER])
    kept = ["v2026.8.11", "v2026.9.18", "v2026.9.24"]
    clone = repos / "clone"
    assert picker.pick_tags(clone, 5) == kept
    # The point of the fix: with three slots a release now samples the fork's
    # oldest real baseline instead of a tag nobody can update from.
    assert picker.pick_tags(clone, 3) == kept
    assert picker.pick_tags(clone, 2) == ["v2026.8.11", "v2026.9.24"]
    assert picker.pick_tags(clone, 1, "v2026.9.24") == ["v2026.9.18"]
    # The job checks out with a sparse cone, so no release file sits on disk:
    # the filter reads the tag's tree and answers the same way.
    assert picker.pick_tags(repos / "worktreeless", 5) == kept
    # And a checkout that carries no tag refs at all gets the same answer from
    # the samplers that enumerate origin, because the probe fetches the tag it
    # is judging instead of treating an absent ref as evidence of a foreign one.
    if shape == "tip":
        assert picker.pick_tags(repos / "tagless", 5) == kept


@pytest.mark.parametrize("shape", SHAPES)
def test_a_tagless_legacy_checkout_still_says_so(shape, tmp_path):
    """The filter does not turn upstream's own loud failure into a quiet one.

    ``legacy`` samplers enumerate the checkout's refs, exactly as upstream wrote
    them, so ``--no-tags`` leaves them with nothing to sample; the job avoids
    that with ``fetch-tags: true``.  ``tip`` samplers read origin instead and
    answer correctly, which is the point of the case above.
    """
    repos, picker = _sampling_env(tmp_path, shape, [UNBRANDED, BRANDED_OLD, BRANDED])
    run = subprocess.run(["bash", str(tmp_path / f"tree-{shape}" / _SHELL),
                          "--count", "3", "--repo", str(repos / "tagless")],
                         capture_output=True, text=True)
    if shape == "legacy":
        assert run.returncode != 0
        with pytest.raises(ValueError):
            picker.pick_tags(repos / "tagless", 3)
    else:
        assert run.returncode == 0, run.stderr
        assert picker.pick_tags(repos / "tagless", 3) == ["v2026.8.11", "v2026.9.18"]


@pytest.mark.parametrize("shape", SHAPES)
def test_shell_picker_agrees_with_the_python_one(shape, tmp_path):
    tree = _tree(shape, tmp_path / f"tree-{shape}")
    updates._reconcile_fork_release_baselines(str(tree))
    repos = _seed_repo(tmp_path / f"repo-{shape}", [UNBRANDED, BRANDED_OLD, BRANDED])
    # The shell sampler only reads the calendar scheme upstream wrote it against,
    # so this fixture never hands it a v0.x tag.
    for name in ("clone", "worktreeless"):
        repo = repos / name
        assert _shell_picks(tree / _SHELL, repo, 5) == ["v2026.8.11", "v2026.9.18"]
        assert _shell_picks(tree / _SHELL, repo, 2) == ["v2026.8.11", "v2026.9.18"]
        assert _shell_picks(tree / _SHELL, repo, 1) == ["v2026.9.18"]


@pytest.mark.parametrize("shape", SHAPES)
def test_every_tag_dropped_is_a_failure_not_an_empty_matrix(shape, tmp_path):
    tree = _tree(shape, tmp_path / f"tree-{shape}")
    updates._reconcile_fork_release_baselines(str(tree))
    repos = _seed_repo(tmp_path / f"repo-{shape}", [UNBRANDED,
                                                    ("v2026.4.8", "2026-04-08T00:00:00Z",
                                                     [("scripts/install.sh", "clone x\n"),
                                                      ("hermes_cli/__init__.py", "")])])
    picker = _load(tree / _PICK_TAGS, f"pick_all_{shape}")
    with pytest.raises(ValueError):
        picker.pick_tags(repos / "clone", 3)
    run = subprocess.run(["bash", str(tree / _SHELL), "--count", "3",
                          "--repo", str(repos / "clone")],
                         capture_output=True, text=True)
    assert run.returncode != 0
    assert "install this fork can drive" in run.stderr


@pytest.mark.parametrize("shape", SHAPES)
def test_upstreams_own_sampling_assertions_still_hold(shape, tmp_path):
    """Tags with no installer at all sample exactly as upstream tests them.

    Upstream's ``tests/scripts/test_release_tag_sampling.py`` builds its fixture
    from empty commits, so the filter has to be silent on those trees: chronology
    over lexicography, newest stable kept, candidate excluded.  If this drifts,
    the fork's own CI goes red on a file the fork did not mean to change.
    """
    empty = [("v2026.7.20", "2026-08-01T00:00:00Z", []),
             ("v0.27.0", "2026-08-02T00:00:00Z", []),
             ("v0.28.0", "2026-08-03T00:00:00Z", [])]
    repos, picker = _sampling_env(tmp_path, shape, empty)
    assert picker.pick_tags(repos / "clone", 3, "v0.28.0") == ["v2026.7.20", "v0.27.0"]
    assert picker.pick_tags(repos / "clone", 1, "v0.28.0") == ["v0.27.0"]
