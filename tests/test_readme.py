"""Tests for the README generator and the trophies shelf."""

import os
import subprocess

import pytest

from hundredways.achievements import ACHIEVEMENTS, Achievements
from hundredways.readme import ReadmeInputs, render_readme
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
        cli = Cli(type("A", (), {"repo": str(repo), "state_dir": "", "command": "readme"})())
        cli.cmd_readme()
        assert os.path.exists(target)
        content = open(target, encoding="utf-8").read()
        assert content.startswith("# 100Ways")
        assert f"{len(WAYS)} ways" in content
    finally:
        open(target, "w", encoding="utf-8").write(original)
