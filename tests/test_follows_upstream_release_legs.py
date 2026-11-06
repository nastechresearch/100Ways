"""The engine follows upstream's release plane; it does not re-grow a leg upstream deleted.

Upstream retired the macOS Intel (``darwin-x64`` / ``macos-intel``) desktop build in
``06192b894c`` and the deletion is inside the seed this fork syncs from.  The fork carried
that leg as two reconcile rules, so every brand re-emitted a lane row and a set of workflow
jobs the seed does not contain -- and the candidate then failed its own release tests,
because a lane with no job behind it (and a job with no lane behind it) is a release plane
that contradicts itself.

These two tests are the contract on the other side: brand and reconcile a tree seeded from
upstream's current bytes and what comes out must be upstream's plane, with the legs upstream
still builds left intact.  A rule that re-adds a retired leg makes one of these fail, either
by putting the text back or by stopping the sync over it.

The inputs are the files the sync really sees: ``tests/fixtures/desktop_bundled`` is
upstream's bundled desktop workflow at the seed and ``tests/fixtures/upstream_lanes`` is
upstream's lane table at the revision the fork's default rewrite anchors on.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from hundredways import updates

BUNDLED_WORKFLOW = (
    Path(__file__).parent / "fixtures" / "desktop_bundled" / "desktop-bundled-release.yml"
)
UPSTREAM_LANES = Path(__file__).parent / "fixtures" / "upstream_lanes" / "lanes.txt"


def test_reconciling_a_seed_without_the_intel_leg_builds_no_intel_jobs(tmp_path):
    """``darwin-x64`` may not appear in the desktop workflow the engine emits.

    The leg guards, the handoff plane and the channel seam all edit this one workflow, so
    the whole ordered chain runs here; anything that re-derives the retired Intel legs shows
    up as a ``darwin-x64`` job name.  The Apple Silicon half of the plane must survive the
    same run -- an engine that deleted the desktop release plane to pass this test would be
    worse than the one that re-added a leg to it.
    """
    workflow_path = tmp_path / updates._BUNDLED_WORKFLOW_REL
    workflow_path.parent.mkdir(parents=True)
    shutil.copyfile(BUNDLED_WORKFLOW, workflow_path)

    updates.reconcile_tree(str(tmp_path))

    text = workflow_path.read_text(encoding="utf-8")
    assert "darwin-x64" not in text
    assert "macos-intel" not in text
    for leg in ("build-darwin-arm64-release", "stage-receipt-darwin-arm64",
                "smoke-darwin-arm64"):
        assert f"  {leg}:" in text, f"the engine dropped the live desktop leg {leg}"


def test_reconciling_a_seed_without_the_intel_lane_keeps_the_lane_table(tmp_path):
    """The lane table the engine emits is upstream's, plus only this fork's lane default.

    Upstream's own refusal -- ``lane_set.select(with_=["macos-intel"])`` raises ``unknown
    lane`` -- is the behaviour being protected here, so the assertion is on the evaluated
    module and not on a restated set: no ``macos-intel`` row, ``DESKTOP`` unchanged, and the
    fork's default still every lane it can ship unsigned.
    """
    releases = tmp_path / "scripts" / "releases"
    releases.mkdir(parents=True)
    shutil.copyfile(UPSTREAM_LANES, releases / "lanes.py")
    (releases / "stable.py").write_text(
        "from scripts.releases import lanes as lane_set\n", encoding="utf-8"
    )

    updates.reconcile_tree(str(tmp_path))

    namespace: dict = {}
    exec(
        compile((releases / "lanes.py").read_text(encoding="utf-8"), "lanes.py", "exec"),
        namespace,
    )
    assert "macos-intel" not in namespace["NAMES"]
    assert namespace["DESKTOP"] == ("windows", "macos-arm64")
    assert "macos-intel" not in namespace["DEFAULT"]
    assert "termux" in namespace["DEFAULT"], "the fork's own lane default must survive"
