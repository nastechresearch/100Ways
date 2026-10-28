"""Ways coverage: which of the 200 ways have a live engine consumer.

The registry (``ways.py``) promises a named strategy for every sub-problem.
Not every promise is a running piece of the engine yet.  ``ways coverage``
resolves each way's ``uses=`` symbol against the live engine and reports the
truth:

- ``built``        — the declared consumer resolves to a real symbol
                     (function, class, method, module attribute, or a
                     signature parameter of a real callable);
- ``dangling``     — the declared consumer does not exist (registry lie);
- ``catalog-only`` — no consumer declared yet (future work, still listed).

The hard invariant is *no dangling*: a way must never point at a symbol that
does not exist, because the coverage table is the honest statement of what
the engine can really do today.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass

from .ways import Way, WaysRegistry, build_registry


@dataclass(frozen=True)
class SymbolResolution:
    """The outcome of resolving one ``uses=`` reference against the engine."""

    path: str
    kind: str  # function | class | attribute | parameter | dangling
    detail: str = ""


def resolve_symbol(path: str) -> SymbolResolution:
    """Resolve a ``uses=`` symbol path against the live ``hundredways`` engine.

    Accepted forms::

        module.attr            module-level attribute/function/class
        module.Class.method    method on a class
        module.fn.param        signature parameter of a function/method

    Anything else — a missing module, a missing attribute, or a parameter
    that is not in the callable's signature — resolves as ``dangling``.
    """
    parts = path.split(".")
    if len(parts) < 2 or not parts[0]:
        return SymbolResolution(path, "dangling", "needs module.attr")
    try:
        obj = importlib.import_module(f"hundredways.{parts[0]}")
    except ImportError:
        return SymbolResolution(path, "dangling", f"no module hundredways.{parts[0]}")
    for i, attr in enumerate(parts[1:], start=1):
        if hasattr(obj, attr):
            obj = getattr(obj, attr)
            continue
        # Final component may name a signature parameter of a callable
        # (e.g. ``port.port_commits.dry_run``).
        if i == len(parts) - 1 and callable(obj):
            try:
                if attr in inspect.signature(obj).parameters:
                    return SymbolResolution(
                        path,
                        "parameter",
                        f"parameter of {'.'.join(parts[:-1])}",
                    )
            except (TypeError, ValueError):  # pragma: no cover - defensive
                pass
        missing = ".".join(parts[:i])
        return SymbolResolution(path, "dangling", f"{missing} has no {attr}")
    if inspect.isclass(obj):
        kind = "class"
    elif callable(obj):
        kind = "function"
    else:
        kind = type(obj).__name__
    module = getattr(obj, "__module__", "")
    qualname = getattr(obj, "__qualname__", "")
    detail = f"{module}.{qualname}" if module and qualname else path
    return SymbolResolution(path, kind, detail)


@dataclass(frozen=True)
class WayCoverage:
    """One row of the coverage table."""

    way: Way
    status: str  # built | dangling | catalog-only
    resolution: SymbolResolution | None = None


def coverage(registry: WaysRegistry | None = None) -> list[WayCoverage]:
    """Compute the coverage table for every way in the registry."""
    reg = registry or build_registry()
    rows: list[WayCoverage] = []
    for way in reg.all():
        if way.uses:
            res = resolve_symbol(way.uses)
            rows.append(
                WayCoverage(
                    way,
                    "built" if res.kind != "dangling" else "dangling",
                    res,
                )
            )
        else:
            rows.append(WayCoverage(way, "catalog-only"))
    return rows


def summary(rows: list[WayCoverage] | None = None) -> dict[str, int]:
    """Count ways per status: ``{"built": n, "dangling": n, "catalog-only": n}``."""
    rows = rows if rows is not None else coverage()
    counts: dict[str, int] = {"built": 0, "dangling": 0, "catalog-only": 0}
    for row in rows:
        counts[row.status] += 1
    return counts


def by_category(rows: list[WayCoverage]) -> dict[str, list[WayCoverage]]:
    """Group coverage rows by way category, preserving registry order."""
    groups: dict[str, list[WayCoverage]] = {}
    for row in rows:
        groups.setdefault(row.way.category, []).append(row)
    return groups