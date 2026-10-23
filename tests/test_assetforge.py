"""The owned-asset policy: what it refuses to read, and what it refuses to call clean.

``manifest.json`` can only say what the registry claims.  This module measures
whether that list covers the classes the policy says we must own, which is the
part a rename in upstream breaks.  Three properties get tested harder than the
findings themselves: a run without a tree never implies the tree is fine, a
finding never outranks the evidence behind it (only an upstream checkout can
prove the pixels are not ours), and severity comes from ``brandassets`` rather
than a second opinion here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hundredways.assetforge import (
    AssetPolicy,
    audit_asset_policy,
    branded_paths,
    class_coverage,
    not_measured,
    render,
    to_json,
)
from hundredways.assets import OwnedAssets
from hundredways.cli import Cli, build_parser
from hundredways.policy import PolicyError

REPO = Path(__file__).resolve().parents[1]


def registry(tmp_path, targets: dict[str, str]) -> OwnedAssets:
    """An owned-assets registry with the given fork target paths claimed."""
    root = tmp_path / "config" / "owned-assets"
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(targets), encoding="utf-8")
    for relative in targets.values():
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"owned pixels")
    return OwnedAssets(root=str(root))


def policy(*classes: str) -> AssetPolicy:
    return AssetPolicy(version=1, must_own=classes)


def _write(root: Path, *paths: str) -> str:
    for relative in paths:
        leaf = root / relative
        leaf.parent.mkdir(parents=True, exist_ok=True)
        leaf.write_bytes(b"pixels")
    return str(root)


def tree(tmp_path, *paths: str) -> str:
    """A candidate tree holding the given fork-relative paths."""
    return _write(tmp_path / "tree", *paths)


def upstream(tmp_path, *paths: str) -> str:
    """An upstream checkout holding the given paths, before branding renames them."""
    return _write(tmp_path / "upstream", *paths)


def codes(issues) -> list[str]:
    return [issue.code for issue in issues]



# -- the policy that ships ----------------------------------------------------

def test_the_shipped_policy_parses_in_the_reader_subset():
    loaded = AssetPolicy.load()

    assert loaded.version == 1
    assert loaded.must_own
    assert all(text.endswith("/") for text in loaded.must_own)


def test_every_class_the_shipped_policy_requires_is_claimed():
    """The engine's own registry must satisfy the engine's own policy.

    This is the assertion ``stage-selfcheck`` runs, so a class renamed in the
    manifest fails here before it fails in CI.
    """
    issues = audit_asset_policy(AssetPolicy.load(), OwnedAssets(repo=str(REPO)))

    assert issues == []


def test_the_shipped_policy_covers_the_classes_the_manifest_uses():
    coverage = class_coverage(OwnedAssets(repo=str(REPO)), AssetPolicy.load().must_own)

    assert set(coverage) == set(AssetPolicy.load().must_own)
    assert all(targets for targets in coverage.values())


# -- what it refuses to read --------------------------------------------------

@pytest.mark.parametrize(
    "class_text",
    ["assets", "/assets/", "../assets/", "assets/../"],
    ids=["no-slash", "absolute", "escapes-up", "dot-dot-inside"],
)
def test_a_class_that_cannot_match_a_fork_path_is_refused(tmp_path, class_text):
    path = tmp_path / "asset-policy.yml"
    path.write_text(f"version: 1\nmust_own:\n  - {class_text}\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="must_own"):
        AssetPolicy.load(str(path))


def test_a_class_listed_twice_is_refused(tmp_path):
    path = tmp_path / "asset-policy.yml"
    path.write_text("version: 1\nmust_own:\n  - assets/\n  - assets/\n", encoding="utf-8")

    with pytest.raises(PolicyError, match="twice"):
        AssetPolicy.load(str(path))


def test_a_policy_file_that_is_not_written_in_the_subset_is_refused(tmp_path):
    """A construct this reader cannot parse must never be quietly dropped."""
    path = tmp_path / "asset-policy.yml"
    path.write_text("version: 1\nmust_own: {assets: 1}\n", encoding="utf-8")

    with pytest.raises(PolicyError):
        AssetPolicy.load(str(path))


def test_a_missing_policy_file_is_named_not_ignored(tmp_path):
    with pytest.raises(PolicyError, match="no such policy file"):
        AssetPolicy.load(str(tmp_path / "nowhere.yml"))


def test_replaced_keeps_the_same_shape_rules():
    """A test that swaps the class list is held to the same normalization as a file."""
    with pytest.raises(PolicyError, match="must_own"):
        policy("assets/").replaced(must_own=("assets",))


# -- findings ----------------------------------------------------------------

def test_a_required_class_the_registry_never_claims_is_a_block(tmp_path):
    owned = registry(tmp_path, {"elsewhere/nastech.png": "nastech.png"})

    issues = audit_asset_policy(policy("assets/"), owned)

    assert [issue.code for issue in issues] == ["class-unowned"]
    assert issues[0].path == "assets/"
    assert issues[0].severity == "block"


def test_a_class_covered_by_one_claim_is_not_reported(tmp_path):
    owned = registry(tmp_path, {"assets/banner.png": "banner.png"})

    assert audit_asset_policy(policy("assets/"), owned) == []


def test_identity_art_in_a_required_class_is_reported_once_per_file(tmp_path):
    walked = tree(tmp_path, "assets/nastech-logo.png", "assets/favicon.ico")
    owned = registry(tmp_path, {"assets/claimed.png": "claimed.png"})

    issues = audit_asset_policy(policy("assets/"), owned, walked)

    assert codes(issues) == ["art-unclaimed", "art-unclaimed"]
    assert [issue.path for issue in issues] == ["assets/favicon.ico", "assets/nastech-logo.png"]


def test_art_with_no_upstream_tree_stays_a_review_holding_a_block(tmp_path):
    """A tree alone cannot tell upstream's logo from one the fork drew itself."""
    walked = tree(tmp_path, "assets/nastech-logo.png")
    owned = registry(tmp_path, {"assets/claimed.png": "claimed.png"})

    issues = audit_asset_policy(policy("assets/"), owned, walked)

    assert [(issue.severity, issue.code) for issue in issues] == [("review", "art-unclaimed")]
    assert "no upstream tree proves whose bytes ship" in issues[0].detail


def test_upstream_shipping_the_path_is_what_makes_the_same_file_a_block(tmp_path):
    walked = tree(tmp_path, "assets/nastech-logo.png")
    theirs = upstream(tmp_path, "assets/nastech-logo.png")
    owned = registry(tmp_path, {"assets/claimed.png": "claimed.png"})

    issues = audit_asset_policy(policy("assets/"), owned, walked, theirs)

    assert [(issue.severity, issue.code) for issue in issues] == [("block", "art-unclaimed")]
    assert issues[0].detail == "upstream art brands into this path and no manifest target names it"


def test_art_the_fork_authored_is_not_reported_as_a_leak(tmp_path):
    """Nothing overwrites a path upstream never had: that is the orphan pass's job.

    Without this rule the audit would block a release over a file the fork drew,
    and the finding would be unfalsifiable -- the registry cannot claim art that
    has no upstream twin to replace.
    """
    walked = tree(tmp_path, "assets/nastech-logo.png", "assets/nastech-banner.png")
    theirs = upstream(tmp_path, "assets/nastech-logo.png")
    owned = registry(tmp_path, {"assets/claimed.png": "claimed.png"})

    issues = audit_asset_policy(policy("assets/"), owned, walked, theirs)

    assert [issue.path for issue in issues] == ["assets/nastech-logo.png"]


def test_a_renamed_upstream_file_counts_as_upstream_at_the_branded_path(tmp_path):
    """``nous-girl.png`` becomes ``nastech-bantu.png``; the pixels never changed."""
    theirs = upstream(tmp_path, "apps/bootstrap-installer/public/nous-girl.png")
    walked = tree(tmp_path, "apps/bootstrap-installer/public/nastech-bantu.png")
    owned = registry(tmp_path, {"apps/bootstrap-installer/public/other.jpg": "other.jpg"})

    assert "apps/bootstrap-installer/public/nastech-bantu.png" in branded_paths(theirs)
    issues = audit_asset_policy(
        policy("apps/bootstrap-installer/public/"), owned, walked, theirs
    )

    assert [(issue.severity, issue.path) for issue in issues] == [
        ("block", "apps/bootstrap-installer/public/nastech-bantu.png")
    ]


def test_claiming_the_file_is_what_silences_the_finding(tmp_path):
    walked = tree(tmp_path, "assets/nastech-logo.png")
    other = audit_asset_policy(
        policy("assets/"), registry(tmp_path / "a", {"assets/other.png": "other.png"}), walked
    )
    owned = registry(tmp_path / "b", {"assets/nastech-logo.png": "logo.png"})

    assert codes(other) == ["art-unclaimed"]
    assert audit_asset_policy(policy("assets/"), owned, walked) == []


def test_only_the_suffixes_that_carry_pixels_are_measured(tmp_path):
    walked = tree(tmp_path, "assets/logo.py", "assets/icons.md", "assets/nastech.png")
    owned = registry(tmp_path, {"assets/nastech.png": "nastech.png"})

    assert audit_asset_policy(policy("assets/"), owned, walked) == []


def test_art_the_brand_classifier_cannot_name_is_never_measured(tmp_path):
    """A screenshot in an owned class is not identity art, so it is not a red."""
    walked = tree(tmp_path, "assets/screenshot-of-the-app.png")
    owned = registry(tmp_path, {"assets/nastech.png": "nastech.png"})

    assert audit_asset_policy(policy("assets/"), owned, walked) == []


def test_severity_comes_from_the_classifier_that_already_judged_the_registry(tmp_path):
    """One seriousness scale, shared with brandassets -- not a second opinion here."""
    from hundredways.brandassets import asset_role, severity_for_role

    walked = tree(tmp_path, "assets/nastech-sprite.png", "assets/nastech-logo.png")
    theirs = upstream(tmp_path, "assets/nastech-sprite.png", "assets/nastech-logo.png")
    owned = registry(tmp_path, {"assets/claimed.png": "claimed.png"})

    issues = audit_asset_policy(policy("assets/"), owned, walked, theirs)

    assert severity_for_role("logo") == "block" and severity_for_role("sprite") == "review"
    assert {(asset_role(i.path), i.severity) for i in issues} == {
        (role, severity_for_role(role))
        for role in (asset_role("assets/nastech-logo.png"), asset_role("assets/nastech-sprite.png"))
    }


def test_a_class_with_no_files_in_the_tree_is_a_review_not_a_red(tmp_path):
    walked = tree(tmp_path, "assets/nastech.png")
    owned = registry(
        tmp_path,
        {"assets/nastech.png": "desktop.png", "apps/desktop/public/nastech.png": "icon.png"},
    )

    issues = audit_asset_policy(policy("assets/", "apps/desktop/public/"), owned, walked)

    assert [(issue.code, issue.severity) for issue in issues] == [("class-absent", "review")]
    assert issues[0].path == "apps/desktop/public/"


def test_the_audit_is_the_same_twice(tmp_path):
    walked = tree(tmp_path, "assets/nastech-logo.png", "assets/other-frame.png")
    owned = registry(tmp_path, {})

    first = audit_asset_policy(policy("assets/"), owned, walked)
    second = audit_asset_policy(policy("assets/"), owned, walked)

    assert [issue.to_dict() for issue in first] == [issue.to_dict() for issue in second]


# -- what a run without a tree withholds -------------------------------------

def test_a_run_without_a_tree_withholds_the_tree_findings_instead_of_passing_them(tmp_path):
    owned = registry(tmp_path, {"assets/nastech.png": "nastech.png"})

    issues = audit_asset_policy(policy("assets/"), owned)

    assert codes(issues) == []
    assert to_json(issues)["not_measured"] == ["art-unclaimed", "class-absent"]
    assert "  not measured without a tree: art-unclaimed, class-absent" in render(issues, "")


def test_a_run_with_a_tree_stops_claiming_it_measured_nothing(tmp_path):
    walked = tree(tmp_path, "assets/nastech.png")
    owned = registry(tmp_path, {"assets/nastech.png": "nastech.png"})

    payload = to_json(audit_asset_policy(policy("assets/"), owned, walked), walked)

    assert payload["tree_measured"] is True
    assert payload["upstream_measured"] is False
    assert payload["not_measured"] == ["art-unclaimed attribution"]


def test_both_trees_measure_everything_the_policy_asks(tmp_path):
    walked = tree(tmp_path, "assets/nastech.png")
    theirs = upstream(tmp_path, "assets/nastech.png")
    owned = registry(tmp_path, {"assets/nastech.png": "nastech.png"})

    audit = audit_asset_policy(policy("assets/"), owned, walked, theirs)

    assert not_measured(walked, theirs) == []
    assert to_json(audit, walked, theirs)["not_measured"] == []
    assert "  not measured" not in "\n".join(render(audit, walked, "", theirs))


def test_render_names_the_policy_it_read(tmp_path):
    owned = registry(tmp_path, {})

    lines = render(audit_asset_policy(policy("assets/"), owned), "", str(tmp_path))

    assert lines[0].startswith("asset policy: 1 finding(s) (no tree")
    assert lines[-1] == f"  policy: {tmp_path}"


# -- the verb ----------------------------------------------------------------

def _invoke(argv: list[str]) -> Cli:
    return Cli(build_parser().parse_args(argv))


def test_the_assets_verb_is_wired_and_dry_by_default():
    args = build_parser().parse_args(["--repo", "/nowhere", "assets"])

    assert args.func == "cmd_assets"
    assert args.tree == "" and args.policy == "" and args.json_out == ""
    assert args.upstream == ""


def test_the_engine_audits_itself_green(tmp_path, capsys):
    _invoke(["--repo", str(REPO), "assets"]).cmd_assets()

    out = capsys.readouterr().out
    assert "asset policy: 0 finding(s)" in out
    assert "not measured without a tree" in out


def test_the_assets_verb_is_red_when_a_required_class_is_unclaimed(tmp_path, capsys):
    repo = tmp_path / "fork"
    registry(repo, {"elsewhere/nastech.png": "nastech.png"})
    declared = tmp_path / "asset-policy.yml"
    declared.write_text("version: 1\nmust_own:\n  - assets/\n", encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        _invoke(["--repo", str(repo), "assets", "--policy", str(declared)]).cmd_assets()

    assert stop.value.code == 1
    assert "1 finding(s) block a release" in capsys.readouterr().err


def test_the_assets_verb_stays_green_on_art_it_cannot_attribute(tmp_path, capsys):
    """A review is not a red: without upstream the finding has no owner to blame."""
    repo = tmp_path / "fork"
    registry(repo, {"assets/claimed.png": "claimed.png"})
    walked = tree(tmp_path, "assets/nastech-logo.png")
    declared = tmp_path / "asset-policy.yml"
    declared.write_text("version: 1\nmust_own:\n  - assets/\n", encoding="utf-8")

    _invoke(
        ["--repo", str(repo), "assets", "--policy", str(declared), "--tree", walked]
    ).cmd_assets()

    out = capsys.readouterr().out
    assert "[review] art-unclaimed assets/nastech-logo.png" in out
    assert "not measured without an upstream checkout: art-unclaimed attribution" in out


def test_an_upstream_tree_turns_that_same_review_into_a_red(tmp_path, capsys):
    repo = tmp_path / "fork"
    registry(repo, {"assets/claimed.png": "claimed.png"})
    walked = tree(tmp_path, "assets/nastech-logo.png")
    theirs = upstream(tmp_path, "assets/nastech-logo.png")
    declared = tmp_path / "asset-policy.yml"
    declared.write_text("version: 1\nmust_own:\n  - assets/\n", encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        _invoke(
            ["--repo", str(repo), "assets", "--policy", str(declared), "--tree", walked,
             "--upstream", theirs]
        ).cmd_assets()

    assert stop.value.code == 1
    assert "[block] art-unclaimed" in capsys.readouterr().out


def test_the_assets_verb_writes_the_payload_it_printed(tmp_path, capsys):
    walked = tree(tmp_path, "assets/nastech-logo.png")
    repo = tmp_path / "fork"
    registry(repo, {"assets/nastech-logo.png": "logo.png"})
    declared = tmp_path / "asset-policy.yml"
    declared.write_text("version: 1\nmust_own:\n  - assets/\n", encoding="utf-8")
    out = tmp_path / "assets.json"

    invoke = _invoke(
        ["--repo", str(repo), "assets", "--tree", walked, "--policy", str(declared),
         "--json-out", str(out)]
    )
    invoke.cmd_assets()

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["findings"] == []
    assert payload["tree_measured"] is True
    assert payload["upstream_measured"] is False
    assert "asset policy: 0 finding(s)" in capsys.readouterr().out


def test_the_assets_verb_refuses_a_tree_that_is_not_there(tmp_path, capsys):
    with pytest.raises(SystemExit) as stop:
        _invoke(["--repo", str(tmp_path), "assets", "--tree", str(tmp_path / "gone")]).cmd_assets()

    assert stop.value.code == 2
    assert "no tree to measure" in capsys.readouterr().err


def test_the_assets_verb_refuses_an_upstream_that_is_not_there(tmp_path, capsys):
    """A missing attribution tree is a mistake, not a licence to downgrade every red."""
    with pytest.raises(SystemExit) as stop:
        _invoke(
            ["--repo", str(tmp_path), "assets", "--upstream", str(tmp_path / "hermes")]
        ).cmd_assets()

    assert stop.value.code == 2
    assert "no upstream checkout" in capsys.readouterr().err


def test_the_assets_verb_refuses_a_policy_it_cannot_read(tmp_path, capsys):
    broken = tmp_path / "asset-policy.yml"
    broken.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as stop:
        _invoke(["--repo", str(tmp_path), "assets", "--policy", str(broken)]).cmd_assets()

    assert stop.value.code == 2
    assert "must_own: missing" in capsys.readouterr().err


def test_the_verb_never_touches_the_registry_it_audits(tmp_path):
    """Reporting is not repairing: the one write path stays in updates.py."""
    repo = tmp_path / "fork"
    owned = registry(repo, {"elsewhere/nastech.png": "nastech.png"})
    declared = tmp_path / "asset-policy.yml"
    declared.write_text("version: 1\nmust_own:\n  - elsewhere/\n", encoding="utf-8")
    before = sorted((repo / "config" / "owned-assets").rglob("*"))

    _invoke(["--repo", str(repo), "assets", "--policy", str(declared)]).cmd_assets()

    assert sorted((repo / "config" / "owned-assets").rglob("*")) == before
    assert owned.count == 1
