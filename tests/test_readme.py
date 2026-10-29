"""Tests for the README generator and the trophies shelf."""

import os
from pathlib import Path

import pytest

from hundredways.achievements import ACHIEVEMENTS, Achievements
from hundredways.readme import (
    Coverage,
    ReadmeError,
    ReadmeInputs,
    SurfaceItem,
    measure_coverage,
    render_readme,
    surface,
)
from hundredways.telegram_commands import COMMANDS
from hundredways.updates import STAGES
from hundredways.ways import WAYS

# -- trophies ----------------------------------------------------------------

def test_trophies_empty_before_unlock(tmp_path):
    ach = Achievements(str(tmp_path))
    assert ach.trophies() == []


def test_trophies_only_unlocked(tmp_path):
    ach = Achievements(str(tmp_path))
    ach.apply_event("pull")
    ach.apply_event("report")
    names = {t.name for t in ach.trophies()}
    assert "First Pull" in names
    assert "Genesis" in names
    assert len(ach.trophies()) < len(ACHIEVEMENTS)
    assert "Century" not in names


def test_trophies_order_matches_catalog(tmp_path):
    ach = Achievements(str(tmp_path))
    for event in ("pull", "gate_pass", "violation"):
        ach.apply_event(event)
    catalog_order = [m.name for m in (ACHIEVEMENTS[k] for k in ACHIEVEMENTS)]
    trophy_order = [t.name for t in ach.trophies()]
    # trophy shelf is in catalog order (subset of it)
    assert [n for n in catalog_order if n in trophy_order] == trophy_order


# -- README rendering --------------------------------------------------------

def test_readme_is_deterministic():
    a = render_readme(ReadmeInputs(state_dir="", owned_count=21))
    b = render_readme(ReadmeInputs(state_dir="", owned_count=21))
    assert a == b


def test_readme_counts_track_registries():
    out = render_readme(ReadmeInputs(state_dir="", owned_count=3))
    assert f"{len(WAYS)} ways" in out
    assert f"{len(ACHIEVEMENTS)} achievements" in out
    assert "3 owned-asset registry entries" in out


def test_readme_lists_every_way_and_achievement():
    out = render_readme(ReadmeInputs(state_dir="", owned_count=0))
    for w in WAYS:
        assert w.name in out
    for key in ACHIEVEMENTS:
        assert key in out


def test_readme_trophy_section_states_none(tmp_path):
    out = render_readme(ReadmeInputs(state_dir=str(tmp_path), owned_count=0))
    assert "_None unlocked yet._" in out


def test_readme_trophy_section_lists_unlocked(tmp_path):
    ach = Achievements(str(tmp_path))
    ach.apply_event("pull")
    out = render_readme(ReadmeInputs(state_dir=str(tmp_path), owned_count=0))
    assert "First Pull" in out.split("## Trophies")[1]
    assert "_None unlocked yet._" not in out


def test_readme_lists_all_15_stages():
    out = render_readme(ReadmeInputs(state_dir="", owned_count=0))
    for stage in STAGES:
        assert stage in out


def test_readme_cli_regenerates_file(tmp_path, monkeypatch):
    """100ways readme writes a deterministic README.md next to the package."""
    # point the CLI at a repo path whose sibling state dir is empty
    from hundredways.cli import Cli

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    target = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__import__("hundredways.cli").__file__)), "..", "README.md"))
    original = ""
    if os.path.exists(target):
        original = open(target, encoding="utf-8").read()
    try:
        args = type(
            "A",
            (),
            {
                "repo": str(repo),
                "state_dir": "",
                "command": "readme",
                "check_coverage": False,
                "min": 0.7,
            },
        )()
        cli = Cli(args)
        cli.cmd_readme()
        assert os.path.exists(target)
        content = open(target, encoding="utf-8").read()
        assert content.startswith("# 100Ways")
        assert f"{len(WAYS)} ways" in content
    finally:
        open(target, "w", encoding="utf-8").write(original)


# -- the surface the README has to document ----------------------------------

REPO = Path(__file__).resolve().parents[1]
BAR = 0.7  # the floor the project promises its own README clears


def _rendered(items):
    return render_readme(ReadmeInputs(state_dir="", owned_count=0, surface=items))


def test_surface_lists_exactly_the_verbs_the_parser_dispatches_to():
    """There is no second verb list: whatever the parser dispatches is what gets documented.

    Both directions matter -- a verb the parser accepts but the inventory omits is
    undocumented, and an inventory entry the parser would reject is a promise the
    engine cannot keep.
    """
    from hundredways.cli import build_parser

    subparsers = [a for a in build_parser()._actions if getattr(a, "_name_parser_map", None)]
    dispatchable = {name for action in subparsers for name in action._name_parser_map}
    assert {item.name for item in surface() if item.kind == "verb"} == dispatchable


def test_surface_lists_every_telegram_command_in_registry_order():
    commands = [item for item in surface() if item.kind == "command"]
    assert [item.name for item in commands] == [command.name for command in COMMANDS]
    for item, command in zip(commands, COMMANDS):
        assert item.description == command.summary
        assert item.detail == command.level


def test_a_parser_that_hides_its_verbs_is_an_error_not_a_shorter_inventory(monkeypatch):
    import argparse

    from hundredways import cli as cli_module

    monkeypatch.setattr(cli_module, "build_parser", lambda: argparse.ArgumentParser())
    with pytest.raises(ReadmeError):
        surface()


def test_handle_shows_a_command_with_a_slash_and_a_verb_as_a_word():
    assert SurfaceItem("command", "table", "one family as a table").handle == "/table"
    assert SurfaceItem("verb", "table", "one family as a table").handle == "table"


def test_generated_readme_documents_the_whole_surface():
    items = surface()
    coverage = measure_coverage(_rendered(items), items)
    assert coverage.documented == coverage.total == len(items)
    assert coverage.missing == ()


def test_readme_at_a_glance_counts_the_live_surface():
    items = surface()
    verbs = sum(1 for item in items if item.kind == "verb")
    commands = sum(1 for item in items if item.kind == "command")
    assert f"- {verbs} CLI verbs and {commands} Telegram commands" in _rendered(items)


def test_a_verb_line_removed_from_the_readme_is_named_by_handle():
    items = surface()
    out = _rendered(items)
    target = next(item for item in items if item.kind == "verb")
    stripped = "\n".join(line for line in out.splitlines() if line != target.line)
    coverage = measure_coverage(stripped, items)
    assert coverage.documented == coverage.total - 1
    assert coverage.missing == (f"{target.handle} (its own line is not in the README)",)


def test_a_blank_help_string_lists_the_name_but_documents_nothing():
    """The bar cannot be met with bare names: a verb with no help string reads as undocumented."""
    items = (SurfaceItem("verb", "halfdone", ""),)
    out = _rendered(items)
    assert "- `halfdone`" in out
    coverage = measure_coverage(out, items)
    assert coverage.documented == 0
    assert coverage.missing == ("halfdone (the code gives it no help string)",)


def test_coverage_reports_the_figure_it_measured():
    coverage = Coverage(3, 4, ("ghost (its own line is not in the README)",))
    assert coverage.to_dict() == {
        "documented": 3,
        "total": 4,
        "ratio": 0.75,
        "missing": ["ghost (its own line is not in the README)"],
    }
    assert Coverage(0, 0).ratio == 0.0


def test_shipped_readme_meets_the_documented_bar():
    """The promise the README is held to, measured against the surface the engine ships."""
    shipped = (REPO / "README.md").read_text(encoding="utf-8")
    coverage = measure_coverage(shipped, surface())
    assert coverage.ratio >= BAR, coverage.missing
