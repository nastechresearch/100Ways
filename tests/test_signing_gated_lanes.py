"""The signing-gated release lanes: the reconcile and the gate that accepts them.

The fork must publish a Linux + Termux stable release while the Apple,
Windows/Azure and Microsoft Store signing accounts are unconfigured.  Two
things have to hold at once, and these tests pin both:

* the reconcile re-derives the fork's declared-skip policy after every brand
  (so a fresh upstream sync cannot clobber it), and
* the publish gate treats a declared skip as a non-failure while still
  blocking a genuine failure in a lane whose credentials *are* configured.

The decision is the presence of a lane's whole signing configuration, never an
operator flag: ``admit`` folds the credentials into the claim it already
publishes, and every later step reads the values it wrote.  The lanes live in
their own ``SIGNING_LANES`` table, so the opt-in claim flags, the shared
handoff map and the manifest target check stay exactly as they are.

The fixture tree under ``tests/fixtures/signing_lanes`` carries the fork's own
gate helpers and the exact anchor regions, so the behaviour tested here is the
behaviour the fork gets.
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from hundredways import updates

LANE_OUTPUTS = ("${{ needs.admit.outputs.include-macos-arm64 }}",
               "${{ needs.admit.outputs.include-windows }}")
ADMIT_OUTPUTS = ("include-macos-arm64: ${{ steps.admit.outputs.include-macos-arm64 }}",
                 "include-windows: ${{ steps.admit.outputs.include-windows }}")
FIXTURE = Path(__file__).parent / "fixtures" / "signing_lanes"
REAL_TREE = os.environ.get("NASTECH_AGENT_TREE")

LANE_PATHS = ("scripts/releases/optional_platforms.py", "scripts/releases/stable.py",
              "scripts/releases/store.py", ".github/workflows/stable-release.yml")
# A fork checkout that moved to the lane table carries lanes.py instead of
# optional_platforms.py, so the fixed-point check reads both shapes.  The five
# files that read the lane default are part of the same surface: a drift there
# is the fork's release tests asserting a policy the shipped tree does not have.
_RELEASE_SURFACE = (*LANE_PATHS, updates._LANES_MODULE,
                    *(rel for rel, _marker, _edits in updates._FORK_LANE_RELEASE))
ALL_APPLE = {"CSC_LINK": "x", "CSC_KEY_PASSWORD": "x", "APPLE_API_KEY_P8": "x",
             "APPLE_API_KEY_ID": "x", "APPLE_API_ISSUER": "x"}
ALL_WINDOWS = {"AZURE_SIGN_ENDPOINT": "x", "AZURE_SIGN_ACCOUNT": "x", "AZURE_SIGN_PROFILE": "x",
               "AZURE_SIGN_PUBLISHER": "x", "AZURE_CLIENT_ID": "x", "AZURE_TENANT_ID": "x"}
BOTH_LEFT_OUT = {"include_macos_arm64": False, "include_windows": False}
BOTH_BUILT = {"include_macos_arm64": True, "include_windows": True}

# Every stable-release.yml job a signed lane gates.  A lane the credentials left
# out must report each of them skipped.
GATED = ("candidates-darwin-arm64", "transitions-darwin-arm64", "macos-packaged-arm64",
         "candidates-win32-x64", "candidates-win32-arm64", "candidates-win32-bundle",
         "transitions-win32", "windows-packaged")


@pytest.fixture()
def tree(tmp_path):
    """A copy of the fixture fork tree the reconcile is free to rewrite."""
    dst = tmp_path / "fork"
    shutil.copytree(FIXTURE, dst)
    return dst


def _reconcile(dst):
    return updates._reconcile_signing_gated_release_lanes(str(dst))


def _reconcile_lane_surface(dst):
    """Both lane rules: the table itself, then the files that read its default."""
    changed = list(updates._reconcile_signing_gated_release_lanes(str(dst)))
    changed.extend(updates._reconcile_fork_lane_release_surface(str(dst)))
    return changed


def _import_fork(dst):
    """Import the copied tree's release modules, with the fixture's own stubs.

    The stubs keep the fork modules free of the real dependency tree, and every
    name the fixture owns is dropped from ``sys.modules`` first so a second test
    never sees the first test's copy.
    """
    saved_path = list(sys.path)
    owned = [name for name in sys.modules
             if name == "scripts" or name.startswith("scripts.")
             or name == "nastech_cli" or name.startswith("nastech_cli.")]
    saved = {name: sys.modules.pop(name) for name in owned}
    sys.path.insert(0, str(dst))
    importlib.invalidate_caches()
    try:
        package = importlib.import_module("scripts.releases")
        return (importlib.import_module("scripts.releases.optional_platforms"),
                importlib.import_module("scripts.releases.stable"),
                importlib.import_module("scripts.releases.store"),
                package)
    finally:
        sys.path[:] = saved_path
        for name in [name for name in sys.modules
                     if name == "scripts" or name.startswith("scripts.")
                     or name == "nastech_cli" or name.startswith("nastech_cli.")]:
            del sys.modules[name]
        sys.modules.update(saved)


@pytest.fixture()
def fork(tree):
    """The reconciled fixture modules, imported from the copied tree."""
    _reconcile(tree)
    return _import_fork(tree)


def _job_block(text, job):
    lines = text.splitlines()
    start = lines.index(f"  {job}:")
    end = next(index for index in range(start + 1, len(lines))
               if re.fullmatch(r"  [a-z0-9][a-z0-9-]*:", lines[index]))
    return "\n".join(lines[start:end])


def _written(path, env):
    """The ``GITHUB_OUTPUT`` / step-summary text a fixture step wrote under ``env``."""
    return Path(env[path]).read_text(encoding="utf-8")


def _admitted(metadata=None):
    """The admitted claim the fork's own ``check_claim`` returns.

    The snake-case keys are what every later step reads (``admitted[p.key]``);
    the camelCase keys are the claim metadata they were folded from.
    """
    claim = {"claim_tag": "v1.2.4", "claim_epoch": 1000, "commit": "a" * 40,
             "version": "1.2.4", "tag": "v1.2.4", "autopublish": True,
             "skip_bundles": False, "skip_tests": False,
             "include_macos_intel": False, "include_termux": True,
             "includeMacosIntel": False, "includeTermux": True}
    if metadata:
        claim.update(metadata)
    return claim


def _admit_env(tmp_path, credentials):
    env = {"GITHUB_REPOSITORY": "nastech/agent",
           "GITHUB_OUTPUT": str(tmp_path / "out"), "GITHUB_STEP_SUMMARY": str(tmp_path / "sum")}
    Path(env["GITHUB_OUTPUT"]).write_text("", encoding="utf-8")
    Path(env["GITHUB_STEP_SUMMARY"]).write_text("", encoding="utf-8")
    env.update(credentials)
    return env


def _smokes(result):
    return {job: {"result": result} for job in
            ("smoke-darwin-arm64", "smoke-darwin-x64", "smoke-win32-arm64", "smoke-win32-x64")}


# --- the reconcile itself -------------------------------------------------


def test_reconcile_declares_every_signing_lane_skippable(tree):
    assert _reconcile(tree) == list(LANE_PATHS)


def test_reconcile_is_a_no_op_the_second_time(tree):
    _reconcile(tree)
    assert _reconcile(tree) == []


def test_reconcile_is_wired_into_reconcile_tree(tree, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "_reconcile_signing_gated_release_lanes",
                        lambda dst: calls.append(dst) or [])
    updates.reconcile_tree(str(tree))
    assert calls == [str(tree)]


def test_reconcile_skips_a_tree_without_the_fork(tmp_path):
    assert _reconcile(tmp_path) == []
    assert _import_fork  # the fixture helper exists even for an empty tree


def test_reconcile_fails_loudly_when_an_anchor_moved(tree):
    stable = tree / "scripts" / "releases" / "stable.py"
    stable.write_text(stable.read_text(encoding="utf-8").replace(
        "def require_smokes_match_claim(manifest: dict, *, skip_tests: bool) -> None:",
        "def require_smokes_match_claim(manifest: dict, *, skip_tests: bool, extra=None) -> None:"),
        encoding="utf-8")
    with pytest.raises(updates.SigningGatedLaneError, match="anchor"):
        _reconcile(tree)


def test_reconcile_matches_the_real_fork(tmp_path):
    """The tree the fork ships must already be a fixed point of the reconcile.

    A first pass that rewrites anything means the fork's checked-in release lane
    policy and the engine's disagree, so the next generated sync would quietly
    move the fork off the policy its signing accounts support.
    """
    if not REAL_TREE:
        pytest.skip("set NASTECH_AGENT_TREE to the fork checkout to compare against it")
    dst = tmp_path / "real"
    for rel in _RELEASE_SURFACE:
        source = Path(REAL_TREE) / rel
        if source.is_file():
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    lanes = dst / updates._LANES_MODULE
    if not lanes.is_file() and not (dst / "scripts/releases/optional_platforms.py").is_file():
        pytest.fail(f"{REAL_TREE} carries neither {updates._LANES_MODULE} nor "
                    "scripts/releases/optional_platforms.py, so it is not a fork "
                    "checkout of the release surface")
    assert _reconcile_lane_surface(dst) == []
    assert _reconcile_lane_surface(dst) == []
    if lanes.is_file():
        # The rule keys off its own marker comment, so re-running it cannot see a
        # hand-edit to the set beside the marker. Compare the shipped block to
        # the engine's literal instead: that is what a sync has to reproduce.
        assert updates._LANES_DEFAULT_FORK in lanes.read_text(encoding="utf-8"), (
            "the fork's checked-in default lane set is not the set the engine "
            "derives, so the next sync would move it back")
    # The real workflow is the one that ships, so its reconciled YAML must load:
    # a bad indent or a swallowed newline here is a broken release workflow.
    import yaml

    doc = yaml.safe_load((dst / ".github/workflows/stable-release.yml").read_text(encoding="utf-8"))
    admit = doc["jobs"]["admit"]
    assert any("scripts.releases.stable admit" in str(step.get("run", ""))
               for step in admit["steps"]), "admit no longer derives the lane set"
    assert admit["outputs"]["lanes"] == "${{ steps.admit.outputs.lanes }}"
    # The fork's narrowed DEFAULT has to reach the legs it gates: a Windows
    # candidate that stopped reading the lane set would demand Azure signing
    # credentials this org does not have.
    body = (dst / ".github/workflows/stable-release.yml").read_text(encoding="utf-8")
    assert "fromJSON(needs.admit.outputs.lanes).windows" in body, \
        "no job reads the Windows lane from the claim"


# --- upstream's lane table ------------------------------------------------

# The shape upstream moved to: the lane table replaced the optional platforms,
# ``admit`` turns the claim's lane set into one flag per lane, and every job's
# ``if`` reads it.  The gate this file exists to keep is then upstream's, so the
# reconcile has to stay out of it and only set the fork's default lane set.
UPSTREAM_LANES = '''\
from dataclasses import dataclass


@dataclass(frozen=True)
class Lane:
    name: str
    default: bool
    doc: str


LANES = (
    Lane("docker", True, "build and publish the Docker image"),
    Lane("windows", True, "signed Windows x64 + arm64 MSIX"),
    Lane("windows-store", True, "unsigned Microsoft Store bundle"),
    Lane("macos-arm64", True, "macOS Apple Silicon DMG + ZIP"),
    Lane("macos-intel", False, "macOS Intel DMG + ZIP"),
    Lane("termux", False, "Termux (Android aarch64) APT package"),
    Lane("ci", True, "the full CI pipeline"),
)

DEFAULT = frozenset(lane.name for lane in LANES if lane.default)
'''


@pytest.fixture()
def lane_tree(tmp_path):
    """A branded tree carrying upstream's lane system instead of the fork's."""
    dst = tmp_path / "branded"
    released = dst / "scripts" / "releases"
    released.mkdir(parents=True)
    (released / "lanes.py").write_text(UPSTREAM_LANES, encoding="utf-8")
    (released / "stable.py").write_text(
        "from scripts.releases import lanes as lane_set\n\n\n"
        "def gate_expectations(required, lanes):\n"
        "    return lane_set.jobs_to_run(lanes)\n",
        encoding="utf-8")
    return dst


def _default_of(dst):
    """The set the reconciled lane table's ``DEFAULT`` actually evaluates to."""
    source = (dst / "scripts" / "releases" / "lanes.py").read_text(encoding="utf-8")
    namespace = {}
    exec(compile(source, "lanes.py", "exec"), namespace)
    return set(namespace["DEFAULT"])


def test_reconcile_sets_the_default_lane_set_on_upstreams_lane_table(lane_tree):
    assert _reconcile(lane_tree) == ["scripts/releases/lanes.py"]
    default = _default_of(lane_tree)
    assert {"macos-intel", "macos-arm64", "windows", "windows-store"}.isdisjoint(default)
    assert {"termux", "docker", "ci"} <= default


def test_reconcile_leaves_the_lane_table_alone_the_second_time(lane_tree):
    _reconcile(lane_tree)
    assert _reconcile(lane_tree) == []


def test_reconcile_fails_loudly_when_the_lane_table_moved(lane_tree):
    lanes = lane_tree / "scripts" / "releases" / "lanes.py"
    lanes.write_text(lanes.read_text(encoding="utf-8").replace(
        "DEFAULT = frozenset(lane.name for lane in LANES if lane.default)",
        "DEFAULT = frozenset(lane.name for lane in LANES if lane.default and lane.doc)"),
        encoding="utf-8")
    with pytest.raises(updates.SigningGatedLaneError, match="anchor"):
        _reconcile(lane_tree)


def test_the_lane_shape_needs_both_the_table_and_its_importer(tmp_path):
    """A fork-shaped ``stable.py`` beside a lane table is still the fork's shape.

    Upstream's own release code imports the table, so a tree that merely carries
    a file called ``lanes.py`` must not be mistaken for one whose lane gate is
    upstream's: the fork's anchors are the ones that still have to hold.
    """
    released = tmp_path / "scripts" / "releases"
    released.mkdir(parents=True)
    (released / "lanes.py").write_text(UPSTREAM_LANES, encoding="utf-8")
    (released / "stable.py").write_text(
        "from scripts.releases import optional_platforms\n", encoding="utf-8")
    assert updates._lanes_own_the_signing_gate(str(tmp_path)) is False


# --- the lane table -------------------------------------------------------


def test_the_lane_table_stays_out_of_the_claim_flags(fork):
    optional_platforms, _, _, _ = fork
    # The lanes are not claim flags: only the two opt-in rows reach the claim,
    # the handoff map and the manifest target check, which is why adding them
    # cannot weaken those checks.
    assert [p.key for p in optional_platforms.PLATFORMS] == [
        "include_macos_intel", "include_termux"]
    assert optional_platforms.CLAIM_FLAGS == ("includeMacosIntel", "includeTermux")
    assert optional_platforms.TARGETS == ("macos/x64", "termux/aarch64")
    assert optional_platforms.HANDOFFS == {"darwin-x64": "macos/x64", "termux": "termux/aarch64"}
    assert [lane.key for lane in optional_platforms.SIGNING_LANES] == [
        "include_macos_arm64", "include_windows", "include_windows", "include_windows"]
    assert {lane.handoff for lane in optional_platforms.SIGNING_LANES} == {
        "darwin-arm64", "win32-x64", "win32-arm64", "windows-universal"}


def test_absent_credentials_leave_every_lane_out(fork):
    optional_platforms, _, _, _ = fork
    assert optional_platforms.configured_lanes({}) == {
        "include_macos_arm64": False, "include_windows": False}


def test_a_complete_apple_configuration_builds_only_the_macos_lane(fork):
    optional_platforms, _, _, _ = fork
    assert optional_platforms.configured_lanes(ALL_APPLE) == {
        "include_macos_arm64": True, "include_windows": False}


def test_a_complete_azure_configuration_builds_only_the_windows_lanes(fork):
    optional_platforms, _, _, _ = fork
    assert optional_platforms.configured_lanes(ALL_WINDOWS) == {
        "include_macos_arm64": False, "include_windows": True}


def test_partial_credentials_leave_the_lane_out(tmp_path, fork):
    optional_platforms, _, _, _ = fork
    partial = {**ALL_APPLE, "APPLE_API_ISSUER": ""}
    # Half a signing configuration cannot sign, so the lane is left out and the
    # release still ships -- it never half-enables a lane and fails at the end.
    assert optional_platforms.configured_lanes(partial) == {
        "include_macos_arm64": False, "include_windows": False}


def test_admitted_lanes_reads_only_the_published_values(fork):
    optional_platforms, _, _, _ = fork
    assert optional_platforms.admitted_lanes({}) == {}
    assert optional_platforms.admitted_lanes(
        {"INCLUDE_MACOS_ARM64": "false", "INCLUDE_WINDOWS": "true"}) == {
        "include_macos_arm64": False, "include_windows": True}
    with pytest.raises(ValueError, match="INCLUDE_WINDOWS"):
        optional_platforms.admitted_lanes({"INCLUDE_WINDOWS": "maybe"})


def test_left_out_lanes_name_their_jobs_and_handoffs(fork):
    optional_platforms, _, _, _ = fork
    assert optional_platforms.lanes_left_out(None) == ()
    assert optional_platforms.lane_jobs(None) == frozenset()
    assert set(optional_platforms.lane_jobs(BOTH_LEFT_OUT)) == set(GATED)
    handoffs = ("win32-x64", "win32-arm64", "darwin-arm64", "windows-universal",
                "darwin-x64", "termux")
    assert optional_platforms.lane_handoffs(handoffs, BOTH_LEFT_OUT) == ("darwin-x64", "termux")
    # A lane the caller was not told about is kept: fetching an unstaged group
    # fails loudly, which is better than publishing a release that dropped it.
    assert optional_platforms.lane_handoffs(handoffs, {}) == handoffs
    assert optional_platforms.lane_handoffs(handoffs, BOTH_BUILT) == handoffs


# --- admit folds the credentials -----------------------------------------


def test_admit_folds_the_credentials_and_publishes_the_flags(tmp_path, fork):
    optional_platforms, stable, _, _ = fork
    claim = _admitted({"includeMacosArm64": True, "includeWindows": True})
    stable.check_claim = lambda env: dict(claim)
    env = _admit_env(tmp_path, {})
    stable.admit(env)
    outputs = _written("GITHUB_OUTPUT", env)
    # No credentials: both lanes are published as skipped, whatever the claim says.
    assert "include-macos-arm64=false" in outputs
    assert "include-windows=false" in outputs
    assert "include-termux=true" in outputs
    assert "Opt-in packages: Termux" in _written("GITHUB_STEP_SUMMARY", env)


def test_admit_says_so_when_no_platform_is_built(tmp_path, fork):
    optional_platforms, stable, _, _ = fork
    stable.check_claim = lambda env: dict(
        _admitted({"include_termux": False, "includeTermux": False}))
    env = _admit_env(tmp_path, {})
    stable.admit(env)
    assert "Opt-in packages: none (no opt-in or signed desktop platform is built)" in _written(
        "GITHUB_STEP_SUMMARY", env)


def test_admit_builds_the_lane_the_credentials_cover(tmp_path, fork):
    optional_platforms, stable, _, _ = fork
    stable.check_claim = lambda env: dict(_admitted())
    env = _admit_env(tmp_path, ALL_APPLE)
    stable.admit(env)
    outputs = _written("GITHUB_OUTPUT", env)
    assert "include-macos-arm64=true" in outputs
    assert "include-windows=false" in outputs


# --- the gate -------------------------------------------------------------


def test_gate_expects_a_lane_the_credentials_left_out_to_skip(fork):
    _, stable, _, _ = fork
    required = ["ci", "candidates-termux", *GATED]
    expected = stable.gate_expectations(required, skip_bundles=False, skip_tests=False,
                                        signing=BOTH_LEFT_OUT, include_termux=True)
    for job in GATED:
        assert expected[job] == "skipped"
    assert expected["ci"] == "success"
    assert expected["candidates-termux"] == "success"


def test_gate_accepts_the_declared_skip_and_blocks_a_failure(fork):
    _, stable, _, _ = fork
    required = ["ci", *GATED]
    expected = stable.gate_expectations(required, skip_bundles=False, skip_tests=False,
                                        signing=BOTH_LEFT_OUT)
    needs = {job: {"result": expected[job]} for job in required}
    stable.require_gate(needs, required, skip_bundles=False, skip_tests=False,
                            signing=BOTH_LEFT_OUT, include_termux=True)
    needs["ci"] = {"result": "failure"}
    with pytest.raises(ValueError, match="ci=failure"):
        stable.require_gate(needs, required, skip_bundles=False, skip_tests=False,
                            signing=BOTH_LEFT_OUT, include_termux=True)


def test_gate_blocks_a_declared_skip_that_actually_ran(fork):
    _, stable, _, _ = fork
    required = ["ci", *GATED]
    needs = {"ci": {"result": "success"}, **{job: {"result": "skipped"} for job in GATED}}
    needs["candidates-darwin-arm64"] = {"result": "success"}
    with pytest.raises(ValueError, match="candidates-darwin-arm64=success"):
        stable.require_gate(needs, required, skip_bundles=False, skip_tests=False,
                            signing=BOTH_LEFT_OUT, include_termux=True)


def test_a_configured_lane_must_succeed(fork):
    _, stable, _, _ = fork
    required = ["ci", *GATED]
    expected = stable.gate_expectations(required, skip_bundles=False, skip_tests=False,
                                        signing=BOTH_BUILT, include_termux=True)
    assert all(expected[job] == "success" for job in GATED)
    needs = {job: {"result": expected[job]} for job in required}
    needs["macos-packaged-arm64"] = {"result": "failure"}
    with pytest.raises(ValueError, match="macos-packaged-arm64=failure"):
        stable.require_gate(needs, required, skip_bundles=False, skip_tests=False,
                            signing=BOTH_BUILT, include_termux=True)


def test_a_lane_the_gate_was_not_told_about_is_not_excused(fork):
    _, stable, _, _ = fork
    required = ["ci", *GATED]
    # No signing map: the gate keeps the claim's own behaviour, so a lane that
    # ran anyway is judged on its result and a skipped one is not excused.
    expected = stable.gate_expectations(required, skip_bundles=False, skip_tests=False)
    assert all(expected[job] == "success" for job in GATED)
    needs = {job: {"result": "skipped"} for job in required}
    with pytest.raises(ValueError, match="candidates-win32-x64=skipped"):
        stable.require_gate(needs, required, skip_bundles=False, skip_tests=False)


def test_the_gate_cli_reads_the_published_lanes(tmp_path, fork):
    _, stable, _, _ = fork
    required = ["ci", *GATED]
    env = {"RELEASE_NEEDS": "{}", "SKIP_BUNDLES": "false", "SKIP_TESTS": "false",
           "INCLUDE_MACOS_INTEL": "false", "INCLUDE_TERMUX": "true",
           "INCLUDE_MACOS_ARM64": "false", "INCLUDE_WINDOWS": "false",
           "GITHUB_STEP_SUMMARY": str(tmp_path / "sum")}
    Path(env["GITHUB_STEP_SUMMARY"]).write_text("", encoding="utf-8")
    env["RELEASE_NEEDS"] = _needs(required, {**{job: "skipped" for job in GATED}, "ci": "success"})
    stable.main(["gate", *required], env)
    env["RELEASE_NEEDS"] = _needs(required, {**{job: "skipped" for job in GATED}, "ci": "failure"})
    with pytest.raises(ValueError, match="ci=failure"):
        stable.main(["gate", *required], env)


def _needs(required, results):
    import json
    return json.dumps({name: {"result": results.get(name, "success")} for name in required})


# --- the candidate manifest and its smokes --------------------------------


def _manifest(optional_platforms, targets):
    return {"packages": [{"platform": target.split("/")[0], "arch": target.split("/")[1]}
                         for target in targets],
            "smoke_results": _smokes("skipped")}


def test_the_manifest_drops_the_lanes_that_stayed_out(tmp_path, fork):
    optional_platforms, stable, _, _ = fork
    env = {"RELEASE_CLAIM": _admitted(), "INCLUDE_MACOS_ARM64": "false",
           "INCLUDE_WINDOWS": "false"}
    built = stable.candidate_manifest(env)
    assert "darwin-arm64" not in built["handoffs"]
    assert "win32-x64" not in built["handoffs"]
    assert "windows-universal" not in built["handoffs"]
    assert "termux" in built["handoffs"]
    assert {"candidates-darwin-arm64", "candidates-win32-x64"} <= built["skipped_calls"]
    assert stable.CALL_SMOKE_JOBS["candidates-win32-x64"] not in built["calls"].values()


def test_the_manifest_keeps_a_lane_whose_credentials_are_configured(tmp_path, fork):
    optional_platforms, stable, _, _ = fork
    env = {"RELEASE_CLAIM": _admitted(), "INCLUDE_MACOS_ARM64": "true",
           "INCLUDE_WINDOWS": "false"}
    built = stable.candidate_manifest(env)
    assert "darwin-arm64" in built["handoffs"]
    assert "win32-x64" not in built["handoffs"]


def test_smokes_all_skipped_are_accepted_without_a_desktop_lane(fork):
    optional_platforms, stable, _, _ = fork
    # A Linux + Termux release records every smoke as skipped without the claim
    # skipping tests, because the lanes the credentials left out never ran one.
    stable.require_smokes_match_claim(_manifest(optional_platforms, ["termux/aarch64"]),
                                      skip_tests=False)


def test_a_built_lane_still_has_to_record_its_smoke(fork):
    optional_platforms, stable, _, _ = fork
    manifest = _manifest(optional_platforms, ["termux/aarch64", "macos/arm64"])
    with pytest.raises(ValueError, match="smoke"):
        stable.require_smokes_match_claim(manifest, skip_tests=False)


def test_smokes_must_be_skipped_when_the_claim_skips_tests(fork):
    optional_platforms, stable, _, _ = fork
    manifest = _manifest(optional_platforms, ["termux/aarch64"])
    manifest["smoke_results"] = _smokes("success")
    with pytest.raises(ValueError, match="smoke"):
        stable.require_smokes_match_claim(manifest, skip_tests=True)


# --- the Store submission -------------------------------------------------


def test_store_without_credentials_is_a_declared_skip(fork):
    _, _, store, _ = fork
    assert store.check_from_env({}) == "not-configured"
    with pytest.raises(store.StoreError, match="MS_STORE_TENANT_ID"):
        store.check_from_env({"MS_STORE_PRODUCT_ID": "9ABC"})


def test_store_with_credentials_runs_the_check(fork):
    _, _, store, _ = fork
    assert store.check_from_env({"MS_STORE_PRODUCT_ID": "9ABC", "MS_STORE_TENANT_ID": "t",
                                 "MS_STORE_CLIENT_ID": "c",
                                 "MS_STORE_CLIENT_SECRET": "s"}) == "verified"


# --- the workflow ---------------------------------------------------------


def test_workflow_gates_every_lane_job_on_its_admit_output(fork):
    # Read the reconciled copy, not the pristine fixture.
    reconciled = _reconciled_workflow()
    for job in ("candidates-darwin-arm64", "transitions-darwin-arm64", "macos-packaged-arm64"):
        assert "needs.admit.outputs.include-macos-arm64 == 'true'" in _job_block(reconciled, job)
    for job in ("candidates-win32-x64", "candidates-win32-arm64", "candidates-win32-bundle",
                "transitions-win32", "windows-packaged"):
        assert "needs.admit.outputs.include-windows == 'true'" in _job_block(reconciled, job)


def test_workflow_gives_admit_the_credentials_and_its_environment(fork):
    reconciled = _reconciled_workflow()
    admit = _job_block(reconciled, "admit")
    assert "environment: release-signing" in admit
    for name in ("CSC_LINK", "CSC_KEY_PASSWORD", "APPLE_API_KEY_P8", "APPLE_API_KEY_ID",
                 "APPLE_API_ISSUER"):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in admit
    for name in ("AZURE_SIGN_ENDPOINT", "AZURE_SIGN_ACCOUNT", "AZURE_SIGN_PROFILE",
                 "AZURE_SIGN_PUBLISHER", "AZURE_CLIENT_ID", "AZURE_TENANT_ID"):
        assert f"{name}: ${{{{ vars.{name} }}}}" in admit


def test_workflow_publishes_the_lane_outputs_and_feeds_the_gate(fork):
    reconciled = _reconciled_workflow()
    for declared in ADMIT_OUTPUTS:
        assert reconciled.count(declared) == 1
    assert reconciled.count("INCLUDE_MACOS_ARM64:") == 4  # three gate steps and the manifest step
    assert reconciled.count("INCLUDE_WINDOWS:") >= 4
    assert "optional_platforms.configured_lanes(env)" in _reconciled("stable.py")
    assert "lane_handoffs(handoffs, lanes)" in _reconciled("stable.py")


def test_workflow_publishes_the_names_the_gate_reads(fork):
    optional_platforms, _, _, _ = fork
    # The gate reads ``INCLUDE_<KEY>`` for every lane; the workflow must export
    # exactly those names (and the two opt-in ones it always had).
    reconciled = _reconciled_workflow()
    assert "INCLUDE_INCLUDE" not in reconciled
    read = {lane.key.upper() for lane in optional_platforms.SIGNING_LANES}
    written = set(re.findall(r"(?m)^\s*(INCLUDE_[A-Z0-9_]+):", reconciled))
    assert read == written - {"INCLUDE_MACOS_INTEL", "INCLUDE_TERMUX"}
    assert optional_platforms.admitted_lanes({name: "false" for name in read}) == {
        lane.key: False for lane in optional_platforms.SIGNING_LANES}


def test_the_reconciled_workflow_stays_valid_yaml(fork):
    # A corrupted env block (an implicit string join swallowing a "\\n") parses
    # as a text blob in the other checks; only loading the YAML exposes it.
    import yaml

    doc = yaml.safe_load(_reconciled_workflow())
    assert doc["jobs"]["admit"]["environment"] == "release-signing"
    assert doc["jobs"]["admit"]["name"] == "Admit the claim"

    def env_blocks(job):
        blocks = []
        if doc["jobs"][job].get("env"):
            blocks.append(doc["jobs"][job]["env"])
        for step in doc["jobs"][job].get("steps", []):
            if step.get("env"):
                blocks.append(step["env"])
        return blocks

    manifest = [b for b in env_blocks("candidate-manifest") if "INCLUDE_MACOS_ARM64" in b]
    assert manifest, "the candidate-manifest env must carry the lane values"
    gate_steps = [name for name in doc["jobs"]
                  if any("INCLUDE_TERMUX" in block for block in env_blocks(name))]
    assert len(gate_steps) >= 3  # acceptance, publication, complete
    for job in ["candidate-manifest", *gate_steps]:
        for block in env_blocks(job):
            if "INCLUDE_MACOS_ARM64" in block or "INCLUDE_WINDOWS" in block:
                assert block["INCLUDE_MACOS_ARM64"] == LANE_OUTPUTS[0]
                assert block["INCLUDE_WINDOWS"] == "${{ needs.admit.outputs.include-windows }}"
    for job in gate_steps:
        for block in env_blocks(job):
            if "INCLUDE_TERMUX" in block:
                assert "INCLUDE_MACOS_ARM64" in block and "INCLUDE_WINDOWS" in block
    outputs = doc["jobs"]["admit"]["outputs"]
    assert outputs["include-macos-arm64"] == "${{ steps.admit.outputs.include-macos-arm64 }}"
    assert outputs["include-windows"] == "${{ steps.admit.outputs.include-windows }}"


def _reconciled(name):
    return (_tree_cache / "scripts" / "releases" / name).read_text(encoding="utf-8")


def _reconciled_workflow():
    workflow = _tree_cache / ".github" / "workflows" / "stable-release.yml"
    return workflow.read_text(encoding="utf-8")


_tree_cache = None


@pytest.fixture(autouse=True)
def _remember_tree(tree):
    """Keep the reconciled copy reachable from the plain-text workflow checks."""
    global _tree_cache
    _tree_cache = tree
    yield
    _tree_cache = None


def test_the_entrypoint_banner_is_untouched(fork):
    # The lanes are not opt-in flags, so the entrypoint's "opt in with ..."
    # banner must not learn a flag it cannot honour.
    text = _reconciled("entrypoint.py")
    assert "include-macos-arm64" not in text
