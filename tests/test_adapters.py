"""Upstream's own surface, discovered from a real tree rather than remembered.

The engine patches upstream code, so every CLI verb, workflow trigger, env read,
entry point and *function signature* upstream adds is something the fork has to
answer for.  The measured reason this module exists: upstream added a ``headings``
keyword to ``curses_radiolist()`` and deleted its twin of one fork test, and the
fork only found out when CI went red on a stale mock.  Discovery reads a real
checkout, so these tests build real trees in ``tmp_path`` and assert what the walk
finds -- including the brand map, which is why the fixtures use upstream's own
``hermes_*`` names.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hundredways.adapters import (
    ADOPT,
    BRAND,
    IGNORE,
    RULE,
    BaselineError,
    Surface,
    SurfaceDelta,
    diff_surface,
    find_orphans,
    load_baseline,
    save_baseline,
    scan_surface,
)

PYPROJECT = """
[project]
name = "hermes-tool"
version = "1.2.3"

[project.scripts]
hermes-cli = "hermes_cli.main:main"

[project.entry-points."hermes.plugins"]
radio = "hermes_cli.radio:register"
"""

WORKFLOW = """name: Release checks
on:
  push:
    branches: [main]
  workflow_call:
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
"""

APP_PY = '''
import argparse
import os
import click


def build_parser():
    root = argparse.ArgumentParser()
    subs = root.add_subparsers()
    subs.add_parser("serve", help="run it")
    subs.add_parser("doctor")
    return root


@click.command("chat")
def chat():
    return os.environ["HERMES_TOKEN"], os.getenv("HERMES_HOME", "")


def curses_radiolist(title, items, *, selected=0, searchable=False):
    return selected


async def stream_open(channel):
    return channel


def _private(a, b):
    return a
'''

JS_CLI = """
const { program } = require('commander');
program.command('install').command('update').description('x');
const token = process.env.HERMES_JS_KEY;
const other = process.env['HERMES_OTHER'];
"""

RADIOLIST_DEF = "def curses_radiolist(title, items, *, selected=0, searchable=False):"


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    root = tmp_path / "upstream"
    (root / "hermes_cli").mkdir(parents=True)
    (root / "hermes_cli" / "app.py").write_text(APP_PY, encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "release.yml").write_text(WORKFLOW, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "web").mkdir()
    (root / "web" / "cli.js").write_text(JS_CLI, encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "@hermes/desktop",
                "version": "0.4.0",
                "bin": {"hermes": "web/cli.js"},
            }
        ),
        encoding="utf-8",
    )
    return root


# -- discovery --------------------------------------------------------------


def test_it_finds_argparse_click_and_commander_verbs(upstream):
    surface = scan_surface(str(upstream))
    assert {"serve", "doctor", "chat", "install", "update"} <= set(surface.cli_verbs)
    assert surface.cli_verbs == tuple(sorted(surface.cli_verbs))


def test_it_finds_workflows_and_their_triggers(upstream):
    surface = scan_surface(str(upstream))
    assert "release" in surface.workflows
    assert ("release", "workflow_call") in surface.workflow_triggers
    assert ("release", "push") in surface.workflow_triggers


def test_it_finds_env_reads_in_python_and_javascript(upstream):
    surface = scan_surface(str(upstream))
    assert {"HERMES_TOKEN", "HERMES_HOME", "HERMES_JS_KEY", "HERMES_OTHER"} <= set(
        surface.env_vars
    )


def test_it_finds_entry_points_and_package_identities(upstream):
    surface = scan_surface(str(upstream))
    assert "hermes-cli=hermes_cli.main:main" in surface.entry_points
    assert "radio=hermes_cli.radio:register" in surface.entry_points
    assert "hermes=web/cli.js" in surface.entry_points
    assert {"hermes-tool@1.2.3", "@hermes/desktop@0.4.0"} == set(surface.packages)


def test_it_records_public_signatures_with_their_parameters(upstream):
    surface = scan_surface(str(upstream))
    listed = {item.name: item for item in surface.signatures}
    assert listed["curses_radiolist"].parameters == ("title", "items", "selected", "searchable")
    assert listed["curses_radiolist"].module == "hermes_cli.app"
    assert "stream_open" in listed  # `async def` is public surface too
    assert "_private" not in listed


def test_a_symlinked_or_binary_file_does_not_stop_the_walk(upstream):
    (upstream / "hermes_cli" / "blob.dat").write_bytes(b"\x00\x01\x02")
    surface = scan_surface(str(upstream))
    assert "curses_radiolist" in {item.name for item in surface.signatures}


# -- the baseline round-trip ------------------------------------------------


def test_a_baseline_written_to_disk_reads_back_identical(upstream, tmp_path):
    surface = scan_surface(str(upstream))
    path = tmp_path / "surface.json"
    save_baseline(surface, str(path))
    assert load_baseline(str(path)) == surface
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema"] == "100ways.surface/v1"


@pytest.mark.parametrize(
    "payload, fragment",
    [
        pytest.param('{"schema": "other/v9"}', "schema", id="wrong-schema"),
        pytest.param('{"schema": "100ways.surface/v1"}', "cli_verbs", id="missing-fields"),
        pytest.param("[]", "object", id="not-an-object"),
    ],
)
def test_load_baseline_refuses_a_file_of_the_wrong_shape(tmp_path, payload, fragment):
    path = tmp_path / "surface.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(BaselineError) as excinfo:
        load_baseline(str(path))
    assert fragment in str(excinfo.value).lower()


def test_load_baseline_refuses_a_missing_file(tmp_path):
    with pytest.raises(BaselineError, match="no baseline"):
        load_baseline(str(tmp_path / "absent.json"))


# -- delta classification ---------------------------------------------------


def test_a_new_cli_verb_is_adopted():
    delta = SurfaceDelta("added", "cli-verb", "", "serve")
    assert delta.classification == ADOPT


def test_a_brand_only_change_is_named_as_branding_work():
    delta = SurfaceDelta("added", "env-var", "HERMES_TOKEN", "NASTECH_TOKEN")
    assert delta.classification == BRAND


def test_a_name_the_brand_map_does_not_know_needs_a_rule():
    delta = SurfaceDelta("added", "env-var", "", "ORACLE_KEY")
    assert delta.classification == RULE


def test_a_removal_always_needs_a_change():
    assert SurfaceDelta("removed", "cli-verb", "doctor", "").classification == RULE


def test_a_declared_ignore_keeps_the_delta_but_labels_it(upstream):
    before = scan_surface(str(upstream))
    (upstream / "hermes_cli" / "extra.py").write_text(
        "import argparse\n\nargparse.ArgumentParser().add_subparsers().add_parser('probe')\n",
        encoding="utf-8",
    )
    after = scan_surface(str(upstream), ignore=("cli-verb:probe",))
    assert "probe" in after.cli_verbs  # discovery does not hide things
    marked = [item for item in diff_surface(before, after) if item.after == "probe"]
    assert [item.classification for item in marked] == [IGNORE]


def test_diff_names_every_added_and_removed_item(upstream):
    before = scan_surface(str(upstream))
    (upstream / "hermes_cli" / "app.py").write_text(
        APP_PY.replace('add_parser("doctor")', 'add_parser("wizard")'), encoding="utf-8"
    )
    deltas = diff_surface(before, scan_surface(str(upstream)))
    kinds = {(item.kind, item.after or item.before) for item in deltas}
    assert ("removed", "doctor") in kinds
    assert ("added", "wizard") in kinds


def test_a_signature_that_gained_a_keyword_is_a_changed_delta(upstream):
    # The measured PR #166 break: upstream widened curses_radiolist() with a
    # `headings` keyword and a stale mock in the fork's test died on it.  The
    # delta has to name the parameter, because that is what an operator can act
    # on.
    before = scan_surface(str(upstream))
    (upstream / "hermes_cli" / "app.py").write_text(
        APP_PY.replace(
            RADIOLIST_DEF, RADIOLIST_DEF[:-2] + ", headings=None):",
        ),
        encoding="utf-8",
    )
    after = scan_surface(str(upstream))
    changed = [item for item in diff_surface(before, after) if item.kind == "changed"]
    assert [item.field for item in changed] == ["signature"]
    assert "headings" in changed[0].after
    assert "headings" not in changed[0].before
    assert changed[0].classification == RULE


def test_no_change_between_two_identical_trees(upstream):
    before = scan_surface(str(upstream))
    after = scan_surface(str(upstream))
    assert diff_surface(before, after) == []


# -- orphans ----------------------------------------------------------------

FORK_FILES = (
    "nastech_cli/app.py",
    "nastech_cli/extra.py",
    "config/owned-assets/manifest.json",
    "docs/fork-only-guide.md",
    "tests/nastech_cli/test_setup_menu_curses_migration.py",
)
UPSTREAM_FILES = (
    "hermes_cli/app.py",
    "hermes_cli/extra.py",
    "tests/hermes_cli/test_setup_menu_curses_migration.py",
)


def test_a_file_with_a_branded_twin_is_not_an_orphan():
    found = find_orphans(list(FORK_FILES), list(UPSTREAM_FILES))
    assert "nastech_cli/app.py" not in {item.path for item in found}
    assert "nastech_cli/extra.py" not in {item.path for item in found}


def test_a_file_whose_twin_upstream_deleted_is_the_sharpest_kind():
    found = find_orphans(
        list(FORK_FILES),
        # The current upstream no longer carries the test's twin; the deletion
        # evidence is what promotes it from "fork-only" to "lagging upstream".
        ["hermes_cli/app.py", "hermes_cli/extra.py"],
        previously_obsolete={"tests/nastech_cli/test_setup_menu_curses_migration.py"},
    )
    kinds = {item.path: item.kind for item in found}
    assert kinds["tests/nastech_cli/test_setup_menu_curses_migration.py"] == "deleted-twin"
    assert kinds["docs/fork-only-guide.md"] == "unclassified"
    # A registered owned asset is explained by the registry itself.
    assert "config/owned-assets/manifest.json" not in kinds


def test_an_owned_path_is_explained_when_the_operator_declares_it():
    owned = {"docs/fork-only-guide.md"}
    found = find_orphans(list(FORK_FILES), list(UPSTREAM_FILES), owned_paths=owned)
    assert "docs/fork-only-guide.md" not in {item.path for item in found}


def test_orphans_are_ordered_and_carry_evidence():
    found = find_orphans(list(FORK_FILES), ["hermes_cli/app.py"])
    assert [item.path for item in found] == sorted(item.path for item in found)
    assert all(item.evidence and item.suggestion for item in found)
    assert {item.kind for item in found} <= {"deleted-twin", "unclassified"}


# -- view payload -----------------------------------------------------------


def test_surface_serializes_for_a_view_without_losing_anything(upstream):
    surface = scan_surface(str(upstream))
    payload = json.loads(json.dumps(surface.to_json()))
    assert payload["counts"]["signatures"] == len(surface.signatures)
    assert payload["cli_verbs"] == list(surface.cli_verbs)
    assert payload["signatures"][0]["parameters"] == list(
        payload["signatures"][0]["parameters"]
    )


def test_scanning_a_tree_with_no_code_is_empty_not_an_error(tmp_path):
    (tmp_path / "notes.txt").write_text("nothing here\n", encoding="utf-8")
    surface = scan_surface(str(tmp_path))
    assert surface.cli_verbs == ()
    assert surface.packages == ()


def test_discovery_survives_a_file_that_does_not_parse(upstream):
    (upstream / "hermes_cli" / "broken.py").write_text("def (:\n  pass\n", encoding="utf-8")
    surface = scan_surface(str(upstream))
    assert "curses_radiolist" in {item.name for item in surface.signatures}
    # ...and it says so: an unreadable file is unmeasured surface, never a gap
    # that looks like upstream does not have the code at all.
    assert len(surface.unparsed) == 1
    assert surface.unparsed[0].startswith("hermes_cli/broken.py [SyntaxError")
    assert diff_surface(surface, scan_surface(str(upstream))) == []
    newcomers = [
        item
        for item in diff_surface(Surface(root=str(upstream)), surface)
        if item.field == "unparsed-file"
    ]
    assert [item.classification for item in newcomers] == [RULE]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.test",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.test",
            "PATH": os.environ["PATH"],
        },
    )


def test_scanning_a_real_git_checkout_measures_what_is_committed(tmp_path):
    """The tracked file set, not the whole directory: a baseline is compared
    against a candidate tree the porter builds from commits, so an untracked
    scratch file must not become a claimed verb (``walk_files`` is shared with
    the porter precisely so the two never disagree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\n"
        'p.add_subparsers().add_parser("run")\n\n\ndef go(x, y=1):\n    return x\n',
        encoding="utf-8",
    )
    (repo / "pkg" / "scratch.py").write_text(
        'import argparse\nargparse.ArgumentParser().add_subparsers().add_parser("ghost")\n',
        encoding="utf-8",
    )
    _git(repo, "add", "pkg/a.py")
    _git(repo, "commit", "-q", "-m", "surface fixture")

    surface = scan_surface(str(repo))
    assert "run" in surface.cli_verbs
    assert any(item.name == "go" and item.parameters == ("x", "y") for item in surface.signatures)
    assert "ghost" not in surface.cli_verbs
