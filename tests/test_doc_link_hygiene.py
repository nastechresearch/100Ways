"""Upstream's docs-link checker versus preserved fork-only documentation.

``website/scripts/check_doc_links.py`` rejects a site-route link such as
``/user-guide/profiles`` inside a hand-authored page: the route resolves when the
site is rendered but 404s on GitHub's file viewer, so the checker requires a
relative Markdown path. Its pytest wrapper
``tests/website/test_check_doc_links.py::test_hand_authored_docs_have_no_route_style_links``
runs it over the whole tree with ``--en-only``.

The brander cannot satisfy that check on its own. The fork's Portal page is not
an upstream path -- upstream ships ``nous-portal.md`` and the fork ships
``nastech-portal.md`` -- so ``preserve`` re-adds the fork page *verbatim* after
``brand``/``reconcile`` ran. A fork-local docs revert restored the rejected
route-style link there (upstream repaired its own copy in commit ``4b8a8134``,
"docs(portal): relative profiles link so the docs link check passes on main"),
so every branded candidate carried it into the candidate suite and failed the
``pipeline`` job's "Run final branded candidate test suite" step.

These tests pin the ``doc-link-hygiene`` pass that rewrites those preserved
links to the relative form upstream itself ships, and they fail when a *new*
preserved page introduces a route-style link the port table does not cover.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from hundredways.updates import _DOC_LINK_HYGIENE, _reconcile_doc_link_hygiene

# Upstream's own route grammar, mirrored from website/scripts/check_doc_links.py:
# a Markdown link target that is an absolute site route, i.e. a leading ``/``
# that is not the protocol-relative ``//`` form.
_ROUTE_LINK = re.compile(r"\]\((?P<route>/[^/)\s][^)\s]*)\)")

# Site routes that are React pages or static assets rather than documents; the
# checker exempts these, so a port table entry must never be built from one.
_NON_DOC_PREFIXES = ("/skills", "/plugins", "/img/")


def _route_links(text: str) -> list[str]:
    """Route-style link targets in *text*, minus the checker's own exemptions."""
    found: list[str] = []
    for match in _ROUTE_LINK.finditer(text):
        route = match.group("route")
        if route.startswith(("//",) + _NON_DOC_PREFIXES):
            continue
        found.append(route)
    return found


def _tree_route_links(root: Path) -> dict[str, list[str]]:
    """Every ``.md``/``.mdx`` page under *root* still carrying a route-style link."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*")):
        if path.suffix not in (".md", ".mdx") or ".git" in path.parts:
            continue
        routes = _route_links(path.read_text(encoding="utf-8", errors="ignore"))
        if routes:
            found[path.relative_to(root).as_posix()] = routes
    return found


def _entries_by_file() -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for rel, old, new in _DOC_LINK_HYGIENE:
        grouped.setdefault(rel, []).append((old, new))
    return grouped


# ---------------------------------------------------------------------------
# Structural guarantees on the port table itself
# ---------------------------------------------------------------------------

def test_port_table_entries_are_well_formed():
    assert _DOC_LINK_HYGIENE, "the docs-link port table must not be empty"
    assert len(set(_DOC_LINK_HYGIENE)) == len(_DOC_LINK_HYGIENE), "duplicate port entries"
    for rel, old, new in _DOC_LINK_HYGIENE:
        assert rel and not rel.startswith("/"), f"not a repo-relative path: {rel!r}"
        assert old != new, f"{rel}: replacement is a no-op"
        # Both sides describe the same link target; only its form may change.
        old_route, new_route = _route_links(old), _route_links(new)
        assert old_route, f"{rel}: {old!r} carries no route-style link to port"
        assert not new_route, f"{rel}: {new!r} is still a route-style link"
        assert old_route[0].split("/")[-1] in new, (
            f"{rel}: {new!r} must point at the same document as {old_route[0]!r}"
        )
        assert old.startswith("](") and new.startswith("]("), (
            f"{rel}: entries are matched inline, between ``]`` and ``)``"
        )


def test_port_table_targets_the_docs_tree_only():
    for rel, _old, _new in _DOC_LINK_HYGIENE:
        assert rel.startswith(("website/docs/", "website/i18n/")), (
            f"{rel}: only hand-authored documentation carries route-style links"
        )
        assert rel.endswith(".md"), f"{rel}: expected a Markdown page"


# ---------------------------------------------------------------------------
# The port pass itself
# ---------------------------------------------------------------------------

def _make_candidate(tmp_path: Path, *, with_checker: bool) -> Path:
    """A stand-in candidate carrying every route-style link the table expects."""
    root = tmp_path / "candidate"
    root.mkdir()
    if with_checker:
        checker = root / "website" / "scripts" / "check_doc_links.py"
        checker.parent.mkdir(parents=True, exist_ok=True)
        # The pass keys on the checker's presence, not its contents.
        checker.write_text("# upstream docs link checker\n", encoding="utf-8")
    for rel, entries in _entries_by_file().items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        page = "\n".join(f"See [Nastech profiles{old}" for old, _ in entries)
        path.write_text(page + "\n", encoding="utf-8")
    return root


def test_ported_pages_start_out_violating_the_check(tmp_path):
    """Guard against a vacuous test: the fixture must trip the rule first."""
    root = _make_candidate(tmp_path, with_checker=True)
    found = _tree_route_links(root)
    for rel in _entries_by_file():
        assert rel in found, f"{rel} should carry a route-style link before the port runs"


def _single_page(root: Path, tmp_path: Path, keep: str) -> Path:
    """A copy of the fixture with every page except *keep* removed.

    ``_make_candidate`` deliberately leaves all fixture pages route-style, so a
    test that asserts the exact set of rewritten files must isolate the page it
    is about; otherwise the siblings are legitimately reported too.
    """
    lone = tmp_path / "lone"
    shutil.copytree(root, lone)
    for other in _entries_by_file():
        if other != keep:
            (lone / other).unlink()
    return lone / keep


def test_port_pass_clears_every_flagged_page(tmp_path):
    root = _make_candidate(tmp_path, with_checker=True)
    fixed = _reconcile_doc_link_hygiene(str(root))

    assert sorted(fixed) == sorted(_entries_by_file()), "every listed page must be ported"
    assert _tree_route_links(root) == {}, "the candidate must be route-link clean after the port"

    for rel, entries in _entries_by_file().items():
        text = (root / rel).read_text(encoding="utf-8")
        for old, new in entries:
            assert old not in text, f"{rel}: route-style link survived: {old!r}"
            assert new in text, f"{rel}: relative link missing: {new!r}"
        assert not _route_links(text), f"{rel}: a route-style link survived"


def test_port_pass_rewrites_every_occurrence_on_a_page(tmp_path):
    """English page and zh-Hans translation share the identical link."""
    root = _make_candidate(tmp_path, with_checker=True)
    rel = next(iter(_entries_by_file()))
    path = _single_page(root, tmp_path, rel)
    old, new = _entries_by_file()[rel][0]
    path.write_text(f"See [a{old} and [b{old}\n", encoding="utf-8")

    assert _reconcile_doc_link_hygiene(str(path.parents[len(Path(rel).parts) - 1])) == [rel]
    text = path.read_text(encoding="utf-8")
    assert not _route_links(text)
    # The handler rewrites every occurrence on the page, not only the first.
    assert text.count(new) == 2


def test_port_pass_is_idempotent(tmp_path):
    root = _make_candidate(tmp_path, with_checker=True)
    assert _reconcile_doc_link_hygiene(str(root))
    assert _reconcile_doc_link_hygiene(str(root)) == []
    assert _tree_route_links(root) == {}


def test_port_pass_keeps_already_relative_links_untouched(tmp_path):
    root = _make_candidate(tmp_path, with_checker=True)
    rel = next(iter(_entries_by_file()))
    page = _single_page(root, tmp_path, rel)
    relative = "See [Nastech profiles](../user-guide/profiles.md)\n"
    page.write_text(relative, encoding="utf-8")

    assert _reconcile_doc_link_hygiene(str(page.parents[len(Path(rel).parts) - 1])) == []
    assert page.read_text(encoding="utf-8") == relative


def test_absent_checker_leaves_preserved_docs_untouched(tmp_path):
    """Without upstream's checker the rule does not exist; fork docs stay verbatim."""
    root = _make_candidate(tmp_path, with_checker=False)
    assert _reconcile_doc_link_hygiene(str(root)) == []
    assert _tree_route_links(root), "preserved docs must stay byte-identical when the rule is absent"


# ---------------------------------------------------------------------------
# Live-fork guard: preserve must not re-introduce an unported route link
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
def test_preserved_route_links_are_all_covered_by_the_port_table():
    """Any fork-only documentation page with a route-style link must be ported.

    The port table is a burn-down list, not a permission slip: a newly added
    fork page that links a site route repeats the failure this pass exists to
    prevent, so it must be either fixed in the fork or added here.
    """
    fork = _fork_root()
    assert fork is not None
    assert subprocess.run(
        ["git", "-C", str(fork), "rev-parse", "--git-dir"], capture_output=True
    ).returncode == 0, f"{fork} is not a git checkout"

    covered = set(_entries_by_file())
    uncovered: dict[str, list[str]] = {}
    for rel, routes in _tree_route_links(fork).items():
        if rel in covered:
            continue
        uncovered[rel] = routes
    assert not uncovered, (
        "preserved fork-local docs still link site routes and are not in the port table: "
        + ", ".join(f"{rel}:{routes}" for rel, routes in sorted(uncovered.items()))
    )
