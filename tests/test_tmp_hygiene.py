"""Upstream's literal-``/tmp`` gate versus preserved fork-only content.

Upstream hermes-agent added ``scripts/check_no_tmp_literals.py`` (commit
``b905042d``, 2026-09-21): a first-party gate that fails on any literal ``/tmp``
path in production code, skills, docs or prompts outside a one-entry baseline.
Its test ``tests/scripts/test_check_no_tmp_literals.py`` calls ``mod.main([])``
against the repository root, so it runs in the branded candidate suite.

The brander cannot satisfy that gate on its own.  It transforms the freshly
pulled upstream tree, which is faithful to the rule, and the ``preserve`` stage
then re-adds fork-local files (fork-only skills, bundled skill docs) *after*
``brand``/``reconcile`` ran — verbatim, literal ``/tmp`` included.  A candidate
that is otherwise byte-perfect therefore still fails the branded suite, which is
exactly the ``pipeline`` job's "Run final branded candidate test suite" step.

These tests pin the ``scratch-hygiene`` pass that ports those preserved scratch
paths to the portable tempdir idiom upstream prescribes, and they fail when a
*new* preserved file introduces a literal ``/tmp`` that the port table does not
cover.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from hundredways.updates import _TMP_HYGIENE, _reconcile_tmp_literal_hygiene

# Upstream's own token grammar, mirrored from scripts/check_no_tmp_literals.py:
# a ``/tmp`` path token that is not glued to a preceding path/word char
# (``/var/tmp``, ``~/tmp``, ``a/tmp``), is not the ``${TMPDIR:-/tmp}`` fallback
# idiom, and is not followed by a word char (``/tmpfs``).
_TMP_TOKEN = re.compile(r"(?<![\w./~\\-])(?<!:-)/tmp(?![\w-])")
_MARKER = "no-tmp: ok"


def _tmp_hits(text: str) -> list[int]:
    """Line numbers carrying an unflagged literal ``/tmp``, for Markdown.

    Mirrors upstream's ``_iter_lines_with_hits`` for the ``.md`` suffix: no
    comment exemption (Markdown prose is read by both humans and the model), but
    an inline ``no-tmp: ok`` marker — on the line or the line directly above —
    opts a line out.
    """
    hits: list[int] = []
    prev_marked = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        marked = _MARKER in line
        try:
            if _TMP_TOKEN.search(line) and not marked and not prev_marked:
                hits.append(lineno)
        finally:
            prev_marked = marked
    return hits


def _tree_hits(root: Path) -> dict[str, list[int]]:
    """Every Markdown file under *root* that still carries a literal ``/tmp``."""
    found: dict[str, list[int]] = {}
    for path in sorted(root.rglob("*.md")):
        if ".git" in path.parts:
            continue
        hits = _tmp_hits(path.read_text(encoding="utf-8", errors="ignore"))
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


def _entries_by_file() -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for rel, old, new in _TMP_HYGIENE:
        grouped.setdefault(rel, []).append((old, new))
    return grouped


# ---------------------------------------------------------------------------
# Structural guarantees on the port table itself
# ---------------------------------------------------------------------------

def test_port_table_entries_are_well_formed():
    assert _TMP_HYGIENE, "the scratch-hygiene port table must not be empty"
    assert len(set(_TMP_HYGIENE)) == len(_TMP_HYGIENE), "duplicate port entries"
    for rel, old, new in _TMP_HYGIENE:
        assert rel and not rel.startswith("/"), f"not a repo-relative path: {rel!r}"
        assert old != new, f"{rel}: replacement is a no-op"
        # The gate only flags path tokens; a port that keeps one would be inert.
        assert _TMP_TOKEN.search(old), f"{rel}: {old!r} carries no literal /tmp to port"
        assert not _TMP_TOKEN.search(new), f"{rel}: {new!r} still carries a literal /tmp"


def test_port_table_covers_only_documentation_surfaces():
    """Fork-local skills/docs are the preserved text the gate scans hardest."""
    for rel, _old, _new in _TMP_HYGIENE:
        assert rel.endswith(".md"), f"{rel}: expected a Markdown skill/doc surface"
        assert not rel.startswith("tests/"), f"{rel}: tests are exempt from the gate"


# ---------------------------------------------------------------------------
# The port pass itself
# ---------------------------------------------------------------------------

def _make_candidate(tmp_path: Path, *, with_gate: bool) -> Path:
    """A stand-in candidate carrying every literal the port table expects.

    Each file contains all of that file's ``old`` strings, so applying the table
    reproduces the real fork-local content the ``preserve`` stage writes.
    """
    root = tmp_path / "candidate"
    root.mkdir()
    if with_gate:
        gate = root / "scripts" / "check_no_tmp_literals.py"
        gate.parent.mkdir(parents=True, exist_ok=True)
        # The pass keys on the gate's presence, not its contents.
        gate.write_text("# upstream literal-/tmp gate\n", encoding="utf-8")
    for rel, entries in _entries_by_file().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(old for old, _ in entries) + "\n", encoding="utf-8")
    return root


def test_ported_surfaces_start_out_violating_the_gate(tmp_path):
    """Guard against a vacuous test: the fixture must trip the rule first."""
    root = _make_candidate(tmp_path, with_gate=True)
    hits = _tree_hits(root)
    assert hits, "fixture should carry literal /tmp paths before the port runs"
    for rel in _entries_by_file():
        assert rel in hits, f"{rel} should be flagged before the port runs"


def test_port_pass_clears_every_flagged_surface(tmp_path):
    root = _make_candidate(tmp_path, with_gate=True)
    fixed = _reconcile_tmp_literal_hygiene(str(root))

    assert sorted(fixed) == sorted(_entries_by_file()), "every listed file must be ported"
    assert _tree_hits(root) == {}, "the candidate must be gate-clean after the port"

    for rel, entries in _entries_by_file().items():
        text = (root / rel).read_text(encoding="utf-8")
        for old, new in entries:
            assert old not in text, f"{rel}: literal path survived: {old!r}"
            assert new in text, f"{rel}: replacement missing: {new!r}"


def test_port_pass_is_idempotent(tmp_path):
    root = _make_candidate(tmp_path, with_gate=True)
    assert _reconcile_tmp_literal_hygiene(str(root))
    assert _reconcile_tmp_literal_hygiene(str(root)) == []
    assert _tree_hits(root) == {}


def test_port_pass_keeps_already_portable_content_untouched(tmp_path):
    """Modern fork content routes scratch space through the tempdir idiom."""
    root = _make_candidate(tmp_path, with_gate=True)
    rel = next(iter(_entries_by_file()))
    path = root / rel
    portable = 'scene.render.filepath = os.path.join(tempfile.gettempdir(), "render.png")\n'
    path.write_text(portable, encoding="utf-8")

    fixed = _reconcile_tmp_literal_hygiene(str(root))
    assert rel not in fixed
    assert path.read_text(encoding="utf-8") == portable


def test_absent_gate_leaves_preserved_text_untouched(tmp_path):
    """Without upstream's gate the rule does not exist; fork text stays verbatim."""
    root = _make_candidate(tmp_path, with_gate=False)
    assert _reconcile_tmp_literal_hygiene(str(root)) == []
    assert _tree_hits(root), "preserved text must remain byte-identical when the rule is absent"


# ---------------------------------------------------------------------------
# Live-fork guard: preserve must not re-introduce an unported literal
# ---------------------------------------------------------------------------

def _fork_root() -> Path | None:
    """A nastech-agent checkout, if the caller provided one.

    Optional by design: the offline unit suite has no fork checkout, and CI's
    ``pipeline`` job exercises the real candidate end to end instead.
    """
    candidate = os.environ.get("HUNDREDWAYS_FORK_ROOT", "").strip()
    if candidate and Path(candidate).is_dir():
        return Path(candidate)
    return None


@pytest.mark.skipif(
    _fork_root() is None,
    reason="set HUNDREDWAYS_FORK_ROOT to a nastech-agent checkout to run the live guard",
)
def test_preserved_literals_are_all_covered_by_the_port_table():
    """Any fork-only Markdown surface with a literal /tmp must be ported.

    The port table is a burn-down list, not a permission slip: a newly added
    fork skill or doc that hard-codes ``/tmp`` repeats the failure this pass
    exists to prevent, so it must be either fixed upstream-in-fork or added here.
    """
    fork = _fork_root()
    assert fork is not None
    assert subprocess.run(
        ["git", "-C", str(fork), "rev-parse", "--git-dir"], capture_output=True
    ).returncode == 0, f"{fork} is not a git checkout"

    covered = set(_entries_by_file())
    uncovered: dict[str, list[int]] = {}
    for rel, hits in _tree_hits(fork).items():
        # Only fork-local surfaces reach the candidate via `preserve`; files the
        # branded upstream tree provides are replaced and never matter here.
        if rel.startswith(("tests/", "evals/", ".github/", "website/i18n/")):
            continue
        if rel not in covered:
            uncovered[rel] = hits
    assert not uncovered, (
        "preserved fork-local Markdown still hard-codes /tmp and is not in the port table: "
        + ", ".join(f"{rel}:{lines}" for rel, lines in sorted(uncovered.items()))
    )
