"""Upstream's plugin-catalog date format versus its own consumer test.

``website/scripts/extract-plugins.py`` stamps every plugin-catalog entry with
``addedAt``/``updatedAt`` taken from git history.  It reads the history with::

    git log --format=%x00%cI --name-status --relative -M -- .

and stores the resulting committer date verbatim.  ``%cI`` is *strict* ISO-8601,
which renders a zero UTC offset either as numeric ``+00:00`` (git 2.39, the
development sandbox) or as the ``Z`` designator (the newer git on GitHub's
``ubuntu-latest`` runner).  Which one appears is a property of the local git
version, so the catalog's timestamp format was not deterministic across the
environments that run this pipeline.

The extractor's own consumer test disagrees:
``tests/website/test_extract_plugins.py``
(``test_git_dates_added_is_first_commit_updated_is_last_and_renames_keep_added``)
asserts ``{"addedAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-02-01T00:00:00Z"}``
-- the ``Z`` form the catalog schema documents.

That is a genuine upstream defect, not fork drift: it reproduces against a
pristine checkout of upstream ``dbaec6a2`` (the commit the pipeline brands) and
against current upstream ``main``.  It reaches 100Ways because the file is
upstream-owned -- ``brand`` copies upstream's content over the fork's copy -- and
``tests/website/`` sits inside the candidate suite's discovery roots
(``scripts/run_tests_parallel._DEFAULT_SKIPS`` covers only
``integration``/``e2e``/``docker``), so the ``pipeline`` job's "Run final branded
candidate test suite" step runs it against the candidate tree.

These tests pin the ``extract-plugins-date-hygiene`` pass that normalizes those
timestamps to the ``Z`` form, plus a live guard that fails when the upstream
extractor's date handling drifts away from the shape the port table expects.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hundredways.updates import (
    _EXTRACT_PLUGINS_DATE_HYGIENE,
    _reconcile_extract_plugins_date_hygiene,
)

_EXTRACTOR_REL = "website/scripts/extract-plugins.py"

# Upstream's loader shape the port table rewrites, i.e. the two statements that
# read one ``git log`` committer-date header out of the ``%x00``-prefixed line.
_UNNORMALIZED_READ = 'when = line[1:].strip()'


def _entry() -> tuple[str, str, str]:
    """The single port-table entry, asserted to cover exactly one site."""
    assert len(_EXTRACT_PLUGINS_DATE_HYGIENE) == 1, (
        "the extractor normalizes dates in one place; a second entry means the "
        "upstream source was restructured and the table must be re-derived"
    )
    return _EXTRACT_PLUGINS_DATE_HYGIENE[0]


# ---------------------------------------------------------------------------
# Port-table structure
# ---------------------------------------------------------------------------

def test_table_targets_the_upstream_extractor() -> None:
    rel, old, new = _entry()
    assert rel == _EXTRACTOR_REL
    assert _UNNORMALIZED_READ in old
    assert "line.startswith" in old


def test_table_replacement_normalizes_to_the_z_form() -> None:
    _, _, new = _entry()
    # The replacement must keep reading the same line into a local, then render
    # the instant in UTC with the ``Z`` designator the consumer test requires.
    assert "stamp = line[1:].strip()" in new, "must still read the %cI header"
    assert "datetime.fromisoformat(stamp)" in new, "must parse the stamp"
    assert "astimezone(timezone.utc)" in new, "must pin the instant to UTC"
    assert '.replace("+00:00", "Z")' in new, "must render the UTC offset as Z"
    assert "when = stamp" in new, "a non-ISO stamp must survive verbatim"
    # The loop must still advance to the next commit header.
    assert new.rstrip().endswith("continue")


def test_table_replacement_is_valid_python() -> None:
    _, old, new = _entry()
    synthetic = (
        "from datetime import datetime, timezone\n"
        "\n"
        "\n"
        "def load_dates(log):\n"
        '    dates = {}\n'
        '    when = ""\n'
        "    for line in log.splitlines():\n"
        + old.replace(_UNNORMALIZED_READ, "pass")  # anchor shape, then replaced below
        + "        dates[line] = when\n"
        "    return dates\n"
    ).replace(old.replace(_UNNORMALIZED_READ, "pass"), new)
    compile(synthetic, "<synthetic-extractor>", "exec")


# ---------------------------------------------------------------------------
# The pass itself
# ---------------------------------------------------------------------------

def _seed(tmp_path: Path, text: str) -> Path:
    """A candidate-shaped tree carrying *text* as the upstream extractor."""
    dst = tmp_path / "candidate"
    target = dst / _EXTRACTOR_REL
    target.parent.mkdir(parents=True)
    target.write_text(text, encoding="utf-8")
    return dst


def test_pass_normalizes_the_extractor(tmp_path: Path) -> None:
    _, old, new = _entry()
    dst = _seed(tmp_path, "HEADER\n" + old + "TAIL\n")

    fixed = _reconcile_extract_plugins_date_hygiene(str(dst))

    assert fixed == [_EXTRACTOR_REL]
    text = (dst / _EXTRACTOR_REL).read_text(encoding="utf-8")
    assert new in text
    assert old not in text
    assert text.startswith("HEADER\n") and text.endswith("TAIL\n"), (
        "the pass must rewrite only the anchor, not the surrounding file"
    )


def test_pass_is_idempotent(tmp_path: Path) -> None:
    _, old, _ = _entry()
    dst = _seed(tmp_path, old)

    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == [_EXTRACTOR_REL]
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == []


def test_pass_is_a_no_op_without_the_extractor(tmp_path: Path) -> None:
    """Generations predating the extractor carry no date format to normalize."""
    assert _reconcile_extract_plugins_date_hygiene(str(tmp_path / "empty")) == []
    dst = tmp_path / "other"
    (dst / "website" / "scripts").mkdir(parents=True)
    (dst / "website" / "scripts" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == []


def test_pass_leaves_a_file_without_the_anchor_untouched(tmp_path: Path) -> None:
    """An upstream restructure must degrade to a no-op, never to corruption."""
    dst = _seed(tmp_path, "def load_dates(log):\n    return {}\n")
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == []
    assert (dst / _EXTRACTOR_REL).read_text(encoding="utf-8") == (
        "def load_dates(log):\n    return {}\n"
    )


# ---------------------------------------------------------------------------
# Behaviour after the pass: the consumer test's own expectation
# ---------------------------------------------------------------------------

def _load_dates_from(path: Path, log_text: str) -> dict[str, str]:
    """Import the rewritten extractor and run it over a synthetic ``git log``."""
    pytest.importorskip("yaml", reason="upstream extractor imports yaml")
    spec = importlib.util.spec_from_file_location("extractor_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["extractor_under_test"] = module
    try:
        spec.loader.exec_module(module)
        return module.load_dates(log_text)
    finally:
        # The temp tree goes away with the test; keep sys.modules clean.
        sys.modules.pop("extractor_under_test", None)


def _synthetic_extractor() -> str:
    """A minimal extractor built around the upstream anchor the table targets.

    Mirrors ``load_git_dates``'s walk: the ``%x00``-prefixed header line carries
    the committer date, every following ``--name-status`` line is a file.  The
    anchor block is spliced in verbatim so the pass has the real text to rewrite.
    """
    _, old, _ = _entry()
    return (
        "from datetime import datetime, timezone\n"
        "\n"
        "\n"
        "def load_dates(log):\n"
        "    dates = {}\n"
        '    when = ""\n'
        "    for line in log.splitlines():\n"
        + old
        + "        parts = line.split('\\t')\n"
        "        if len(parts) < 2 or not when:\n"
        "            continue\n"
        "        dates[parts[-1]] = when\n"
        "    return dates\n"
    )


def test_normalized_utc_dates_render_as_z(tmp_path: Path) -> None:
    """The exact assertion the upstream consumer test makes, at the source."""
    dst = _seed(tmp_path, _synthetic_extractor())
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == [_EXTRACTOR_REL]

    log = "\x002026-01-01T00:00:00+00:00\nM\tplugin-catalog/alpha.yaml\n"
    dates = _load_dates_from(dst / _EXTRACTOR_REL, log)

    assert dates == {"plugin-catalog/alpha.yaml": "2026-01-01T00:00:00Z"}


def test_normalized_non_utc_dates_keep_their_instant(tmp_path: Path) -> None:
    """A non-UTC committer date must convert, never be dropped or shifted."""
    dst = _seed(tmp_path, _synthetic_extractor())
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == [_EXTRACTOR_REL]

    log = "\x002026-01-01T00:00:00-07:00\nM\tplugin-catalog/alpha.yaml\n"
    dates = _load_dates_from(dst / _EXTRACTOR_REL, log)

    assert dates == {"plugin-catalog/alpha.yaml": "2026-01-01T07:00:00Z"}


def test_unparseable_dates_are_kept_verbatim(tmp_path: Path) -> None:
    """A malformed stamp must not be dropped: dropping it would mis-date files."""
    dst = _seed(tmp_path, _synthetic_extractor())
    assert _reconcile_extract_plugins_date_hygiene(str(dst)) == [_EXTRACTOR_REL]

    log = "\x00not-a-date\nM\tplugin-catalog/alpha.yaml\n"
    dates = _load_dates_from(dst / _EXTRACTOR_REL, log)

    assert dates == {"plugin-catalog/alpha.yaml": "not-a-date"}


# ---------------------------------------------------------------------------
# Live guard: the real fork checkout, when one is available
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("HUNDREDWAYS_FORK_ROOT", "").strip(),
    reason="set HUNDREDWAYS_FORK_ROOT to a nastech-agent checkout to run the live guard",
)
def test_live_fork_extractor_still_carries_the_anchor(tmp_path: Path) -> None:
    """Fail loudly when upstream restructures the date read.

    The port table matches an exact source region, so an upstream refactor would
    silently turn the pass into a no-op and hand the candidate suite back its
    ``+00:00`` dates.  This guard notices that in seconds instead of 80 minutes
    into the ``pipeline`` job.
    """
    fork = Path(os.environ["HUNDREDWAYS_FORK_ROOT"].strip())
    source = fork / _EXTRACTOR_REL
    if not source.is_file():
        pytest.skip(f"no extractor at {source}")

    text = source.read_text(encoding="utf-8")
    _, old, _ = _entry()
    if old in text:
        return

    # Either upstream already normalizes the offset itself -- in which case the
    # table entry is obsolete -- or the anchor moved and the table must be
    # re-derived.  Distinguish the two so the failure tells you which.
    if parse_annotation := _has_upstream_fix(text):
        pytest.skip(f"upstream now normalizes the offset itself ({parse_annotation})")
    pytest.fail(
        "the upstream extractor no longer carries the date-read anchor this "
        "port table targets, so _reconcile_extract_plugins_date_hygiene() is a "
        "no-op and the candidate suite will see '+00:00' timestamps again; "
        f"re-derive _EXTRACT_PLUGINS_DATE_HYGIENE against {source}"
    )


def _has_upstream_fix(text: str) -> str:
    """Short note naming the upstream normalization, when one is present."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        segment = ast.get_source_segment(text, node) or ""
        if "astimezone" in segment and "Z" in segment:
            return "astimezone + Z"
    return ""


def test_live_fork_extractor_normalizes_to_z(tmp_path: Path) -> None:
    """Run the real upstream extractor through the pass and check the Z form."""
    pytest.importorskip("yaml", reason="upstream extractor imports yaml")
    if not os.environ.get("HUNDREDWAYS_FORK_ROOT", "").strip():
        pytest.skip("set HUNDREDWAYS_FORK_ROOT to a nastech-agent checkout")
    fork = Path(os.environ["HUNDREDWAYS_FORK_ROOT"].strip())
    source = fork / _EXTRACTOR_REL
    if not source.is_file():
        pytest.skip(f"no extractor at {source}")

    dst = _seed(tmp_path, source.read_text(encoding="utf-8"))
    if _reconcile_extract_plugins_date_hygiene(str(dst)) != [_EXTRACTOR_REL]:
        pytest.skip("upstream already normalized the offset itself")

    log = "\x002026-01-01T00:00:00+00:00\nM\tplugin-catalog/alpha.yaml\n"
    assert _load_dates_from(dst / _EXTRACTOR_REL, log) == {
        "plugin-catalog/alpha.yaml": "2026-01-01T00:00:00Z"
    }


def test_git_log_cI_carries_an_explicit_utc_designator(tmp_path: Path) -> None:
    """Upstream's premise: ``%cI`` always carries an explicit UTC designator.

    ``+00:00`` and ``Z`` are both valid strict ISO-8601 for a zero offset, and
    which one git prints is a property of the local git version -- 2.39 (the
    development sandbox) prints ``+00:00``, the newer git on GitHub's
    ``ubuntu-latest`` runner prints ``Z``.  The port table exists because the
    extractor stored whichever designator the local git produced, so the catalog
    format varied by environment.  The invariant worth pinning is therefore the
    designator being *explicit* -- a naive stamp would defeat
    ``fromisoformat``-based consumers -- not one git's cosmetic choice.  An
    earlier revision of this test asserted ``+00:00`` outright and so failed
    only on CI.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00",
        HOME=str(repo),
    )
    run = lambda *args: subprocess.run(  # noqa: E731 - tiny local helper
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    run("init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    run("add", ".")
    run("commit", "-q", "-m", "add")

    stamp = run("log", "-1", "--format=%cI").stdout.strip()

    assert stamp.startswith("2026-01-01T00:00:00")
    assert stamp.endswith(("Z", "+00:00")), (
        "%cI must carry an explicit UTC designator so the extractor never stores "
        f"a naive timestamp; got {stamp!r}"
    )


def _normalise_stamp_via_table(stamp: str) -> str:
    """Run the port table's replacement over one ``%cI`` stamp, standalone.

    The anchor is taken from the shipped table -- so it cannot drift from the
    real fix -- and executed on its own, which keeps this check independent of
    the upstream extractor's ``yaml`` import that the module-level probes need
    (CI installs no ``yaml``, so those probes skip there and only this shape of
    check can regress-proof the normalisation on the runner).
    """
    _, _, new = _entry()
    body = new.replace("continue", "return when", 1)
    src = (
        "from datetime import datetime, timezone\n"
        "\n"
        "\n"
        "def normalise(line):\n"
        '    when = ""\n'
        "    for _line in (line,):\n"
        "        line = _line\n"
        + body
        + "    return when\n"
    )
    namespace: dict[str, object] = {}
    exec(compile(src, "<anchor>", "exec"), namespace)
    return namespace["normalise"]("\x00" + stamp)  # type: ignore[operator]


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ("2026-01-01T00:00:00-07:00", "2026-01-01T07:00:00Z"),
        ("not-a-date", "not-a-date"),
    ],
)
def test_table_normalises_every_git_zero_offset_rendering(
    stamp: str, expected: str
) -> None:
    """Whichever designator the local git printed, the pass yields ``Z``.

    ``+00:00`` (git 2.39) and ``Z`` (newer git on ``ubuntu-latest``) must both
    come out as ``Z``.  That equivalence is exactly what an earlier revision of
    this file missed when it asserted ``+00:00`` outright: the check passed
    locally and failed on CI.  A non-UTC offset must keep its instant, and
    unparseable input must survive verbatim rather than be dropped.
    """
    assert _normalise_stamp_via_table(stamp) == expected
