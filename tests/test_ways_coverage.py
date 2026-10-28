"""Tests for the ways coverage truth-table.

The contracts tested here are *invariants*, not snapshots:

- a way's ``uses=`` must always resolve to a real engine symbol (no lying
  registry entries, now or after future edits);
- the default strategy of every category must have a live consumer (the
  engine's active configuration is real);
- the resolver itself must classify function, class-method, signature
  parameter, and dangling references correctly.
"""

from hundredways.coverage import by_category, coverage, resolve_symbol, summary
from hundredways.ways import Way, WaysRegistry, build_registry


def _registry(*ways: Way) -> WaysRegistry:
    return WaysRegistry(list(ways))


# -- resolver ---------------------------------------------------------------

def test_resolve_module_function():
    res = resolve_symbol("scanner.detect")
    assert res.kind == "function"
    assert res.detail == "hundredways.scanner.detect"


def test_resolve_class():
    res = resolve_symbol("watcher.Watcher")
    assert res.kind == "class"


def test_resolve_class_method():
    res = resolve_symbol("watcher.Watcher.cycle")
    assert res.kind == "function"
    assert res.detail == "hundredways.watcher.Watcher.cycle"


def test_resolve_signature_parameter():
    res = resolve_symbol("port.port_commits.dry_run")
    assert res.kind == "parameter"


def test_resolve_unknown_module_is_dangling():
    res = resolve_symbol("no_such_mod.anything")
    assert res.kind == "dangling"
    assert "no module" in res.detail


def test_resolve_missing_attribute_is_dangling():
    res = resolve_symbol("scanner.no_such_attr")
    assert res.kind == "dangling"


def test_resolve_missing_parameter_is_dangling():
    res = resolve_symbol("port.port_commits.not_a_param")
    assert res.kind == "dangling"


def test_resolve_bare_or_empty_path_is_dangling():
    assert resolve_symbol("scanner").kind == "dangling"
    assert resolve_symbol("").kind == "dangling"


# -- coverage rows ----------------------------------------------------------

def test_tagged_resolving_way_is_built():
    way = Way("scan.magic-bytes", "Magic-byte signatures", "scan",
              "d", uses="scanner.detect")
    rows = coverage(_registry(way))
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "built"
    assert row.resolution.kind == "function"


def test_untagged_way_is_catalog_only():
    way = Way("scan.shebang-hint", "Shebang detection", "scan", "d")
    row = coverage(_registry(way))[0]
    assert row.status == "catalog-only"
    assert row.resolution is None


def test_tagged_unresolving_way_is_dangling():
    way = Way("scan.magic-bytes", "Magic-byte signatures", "scan",
              "d", uses="rules.no_such_fn")
    row = coverage(_registry(way))[0]
    assert row.status == "dangling"


def test_coverage_has_one_row_per_way():
    registry = build_registry()
    rows = coverage(registry)
    assert len(rows) == registry.count == 200


def test_summary_counts_total_exactly():
    rows = coverage()
    totals = summary(rows)
    assert set(totals) == {"built", "dangling", "catalog-only"}
    assert sum(totals.values()) == len(rows) == 200


def test_by_category_groups_without_losing_ways():
    rows = coverage()
    groups = by_category(rows)
    assert len(groups) == 10
    assert sum(len(v) for v in groups.values()) == len(rows)


# -- the hard contracts -----------------------------------------------------

def test_no_declared_consumer_is_dangling():
    """A uses= entry must always point at something that really exists."""
    for row in coverage():
        assert row.status != "dangling", f"{row.way.way_id} -> {row.way.uses}"


def test_every_default_strategy_is_built():
    """The engine's active strategy per category must have a live consumer."""
    registry = build_registry()
    rows = {r.way.way_id: r for r in coverage(registry)}
    for category, way_id in registry.defaults().items():
        assert rows[way_id].status == "built", (
            f"{category} default {way_id} is {rows[way_id].status}"
        )