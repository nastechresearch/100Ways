"""Upstream's own surface, discovered from a checkout rather than remembered.

The engine patches upstream code, so everything upstream adds -- a CLI verb, a
workflow trigger, an env read, an entry point, one keyword on a function the
fork mocks -- is something the fork has to answer for.  Remembering that list
is how PR #166 broke: upstream widened ``curses_radiolist()`` with a ``headings``
keyword and deleted its twin of one fork test, and neither fact was known until
CI went red on a stale mock.  Discovery reads bytes on disk instead, so the
answer arrives before the breakage does.

Reuse is deliberate, because two parsers of one file agree only by luck:

* the file set is :func:`hundredways.forkcheck.walk_files`, the same
  git-authoritative walk the porter uses, so a baseline can never claim a verb
  the candidate tree does not ship;
* workflow ``on:`` blocks are read by :func:`hundredways.ci_policy.workflow_triggers`;
* orphan ownership is :func:`hundredways.forkcheck.is_explicitly_owned`, so the
  orphan report cannot contradict the rule that decided to preserve the file;
* "does this fork path have an upstream twin" is answered by
  :func:`hundredways.rules.collision_safe_path_map`, the brand map itself, rather
  than a hard-coded rename list.

Nothing is quietly skipped: a file this module could not read lands in
``unparsed`` and is diffed like any other measurement, because a silently
dropped file is a delta that never appears.  What is read is spelled out --
``argparse.add_parser``, ``click.command``/``click.group``, commander
``.command()``, ``os.environ[...]``/``os.getenv``/``os.environ.get``,
``process.env``, ``[project.scripts]``/``[project.entry-points.*]``,
``package.json`` ``bin``, and module-level public ``def``/``async def``
signatures.
"""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from . import ci_policy
from .forkcheck import is_explicitly_owned, walk_files
from .rules import DEFAULT_TOKENS, BrandingRules, collision_safe_path_map

__all__ = [
    "ADOPT",
    "BRAND",
    "IGNORE",
    "RULE",
    "BaselineError",
    "Orphan",
    "Signature",
    "Surface",
    "SurfaceDelta",
    "diff_surface",
    "find_orphans",
    "load_baseline",
    "save_baseline",
    "scan_surface",
]

# What the engine should do with a delta, as the spec names it: take it as-is,
# run it through the brand map, decide it with a rule, or leave it alone because
# the operator already declared it.
ADOPT = "adopt"
BRAND = "brand"
RULE = "rule"
IGNORE = "ignore"

BASELINE_SCHEMA = "100ways.surface/v1"

# A delta that cannot be settled by the brand map: a credential name the engine
# must decide, or a file whose surface was never measured.
_DECISION_FIELDS = frozenset({"env-var", "unparsed-file"})

_PY_SUFFIXES = frozenset({".py"})
_JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx"})
_WORKFLOW_DIR = ".github/workflows/"
_WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})

_JS_COMMAND_RE = re.compile(r"\.command\(\s*['\"`]([^'\"`\s]+)")
_JS_ENV_RE = re.compile(
    r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"
    r"|process\.env\[\s*['\"]([^'\"]+)['\"]\s*\]"
)
# Every replacement the default brand map can produce: a name carrying one of
# these is already ours, so nothing else explains it appearing.
_FORK_TOKENS = tuple(
    sorted({token.replace for token in DEFAULT_TOKENS if token.replace}, key=str.casefold)
)


class BaselineError(ValueError):
    """A stored surface baseline that cannot be trusted as a comparison point."""


@dataclass(frozen=True)
class Signature:
    """One public module-level callable and the parameters it accepts."""

    module: str
    name: str
    parameters: tuple[str, ...]

    @property
    def key(self) -> str:
        return f"{self.module}.{self.name}"

    def render(self) -> str:
        return f"{self.key}({', '.join(self.parameters)})"


@dataclass(frozen=True)
class Surface:
    """Everything one checkout exposes, in deterministic order.

    ``ignore`` travels with the scan so a baseline re-read from disk classifies
    deltas exactly as the live scan did, and ``unparsed`` names the files that
    contributed nothing -- surface the engine did not measure.
    """

    root: str = ""
    cli_verbs: tuple[str, ...] = ()
    workflows: tuple[str, ...] = ()
    workflow_triggers: tuple[tuple[str, str], ...] = ()
    env_vars: tuple[str, ...] = ()
    entry_points: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    signatures: tuple[Signature, ...] = ()
    unparsed: tuple[str, ...] = ()
    ignore: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "cli_verbs": len(self.cli_verbs),
            "workflows": len(self.workflows),
            "workflow_triggers": len(self.workflow_triggers),
            "env_vars": len(self.env_vars),
            "entry_points": len(self.entry_points),
            "packages": len(self.packages),
            "signatures": len(self.signatures),
            "unparsed": len(self.unparsed),
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": BASELINE_SCHEMA,
            "root": self.root,
            "cli_verbs": list(self.cli_verbs),
            "workflows": list(self.workflows),
            "workflow_triggers": [list(pair) for pair in self.workflow_triggers],
            "env_vars": list(self.env_vars),
            "entry_points": list(self.entry_points),
            "packages": list(self.packages),
            "signatures": [
                {
                    "module": item.module,
                    "name": item.name,
                    "parameters": list(item.parameters),
                }
                for item in self.signatures
            ],
            "unparsed": list(self.unparsed),
            "ignore": list(self.ignore),
            "counts": self.counts,
        }


@dataclass(frozen=True)
class SurfaceDelta:
    """One movement on the surface, with the classification the engine owes it."""

    kind: str      # added | removed | changed
    field: str     # cli-verb | env-var | signature | ...
    before: str
    after: str
    ignored: bool = False

    @property
    def value(self) -> str:
        return self.after or self.before

    @property
    def classification(self) -> str:
        if self.ignored:
            return IGNORE
        if self.kind != "added":
            # A removal or a widened signature is a change the fork has to make:
            # nothing adopts it on its own behalf.
            return RULE
        if _carries_our_brand(self.value):
            # Only the brand map puts our own name on a fresh upstream surface,
            # so a branded newcomer is branding work, not a decision.
            return BRAND
        if self.field in _DECISION_FIELDS:
            return RULE
        return ADOPT


@dataclass(frozen=True)
class Orphan:
    """A fork path no upstream file brands into -- permanent divergence."""

    path: str
    kind: str      # deleted-twin | unclassified
    evidence: str
    suggestion: str


# -- discovery --------------------------------------------------------------


def _dotted(node: ast.AST) -> str:
    """The dotted name of an attribute chain, or "" when it is not one.

    ``os.environ.get``, ``click.command`` and ``argparse.ArgumentParser`` are all
    read by their dotted spelling so a receiver name has to match, not merely the
    trailing method: ``panel.command("x")`` is not a click verb.
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _string_constant(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _module_name(rel: str) -> str:
    parts = rel.replace(os.sep, "/")[: -len(".py")].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _python_surface(tree: ast.AST, module: str) -> tuple[list[str], list[str], list[Signature]]:
    verbs: list[str] = []
    env: list[str] = []
    signatures: list[Signature] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = _dotted(node.func) if isinstance(node.func, (ast.Attribute, ast.Name)) else ""
            # ``add_parser`` is matched on the method name alone: argparse
            # subparsers are reached through a chained call
            # (``parser.add_subparsers().add_parser("run")``), whose receiver is
            # not a name, and no other API in the tree spells that verb.
            attribute = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if attribute == "add_parser" and node.args:
                verb = _string_constant(node.args[0])
                if verb:
                    verbs.append(verb)
            elif called in {"os.getenv", "os.environ.get", "getenv"} and node.args:
                name = _string_constant(node.args[0])
                if name:
                    env.append(name)
        elif isinstance(node, ast.Subscript) and _dotted(node.value) in {"os.environ", "environ"}:
            name = _string_constant(node.slice)
            if name:
                env.append(name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                called = _dotted(target)
                if not called.startswith("click."):
                    continue
                if called.rsplit(".", 1)[-1] not in {"command", "group"}:
                    continue
                explicit = ""
                if isinstance(decorator, ast.Call) and decorator.args:
                    explicit = _string_constant(decorator.args[0])
                # click names a bare `@click.command()` after the function,
                # dashes for underscores -- the same verb the CLI will answer to.
                verbs.append(explicit or node.name.replace("_", "-"))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        arguments = node.args
        names = [item.arg for item in list(arguments.posonlyargs) + list(arguments.args)]
        if arguments.vararg:
            names.append(f"*{arguments.vararg.arg}")
        names += [item.arg for item in arguments.kwonlyargs]
        if arguments.kwarg:
            names.append(f"**{arguments.kwarg.arg}")
        signatures.append(Signature(module=module, name=node.name, parameters=tuple(names)))
    return verbs, env, signatures


def _js_surface(text: str) -> tuple[list[str], list[str]]:
    verbs = list(_JS_COMMAND_RE.findall(text))
    env = [first or second for first, second in _JS_ENV_RE.findall(text)]
    return verbs, env


def _quoted(value: str) -> str:
    return value.strip().strip("\"'")


def _pyproject_surface(text: str) -> tuple[list[str], list[str]]:
    """``[project]`` identity and every ``[project.scripts]``/entry-point table.

    Read line by line on purpose: ``pyyaml`` and a TOML library are dev extras
    this engine does not ship at runtime, and only two shapes are ever wanted
    here -- a table header and a ``key = "value"`` pair.
    """
    packages: list[str] = []
    entry_points: list[str] = []
    table = ""
    name = ""
    version = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            if table == "project" and name:
                packages.append(f"{name}@{version}")
            table = _quoted(line[1:-1])
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = _quoted(key), _quoted(value)
        if table == "project" and key in {"name", "version"}:
            if key == "name":
                name = value
            else:
                version = value
        elif table == "project.scripts" or table.startswith("project.entry-points"):
            if key and value:
                entry_points.append(f"{key}={value}")
    if table == "project" and name:
        packages.append(f"{name}@{version}")
    return packages, entry_points


def _package_json_surface(text: str) -> tuple[list[str], list[str]]:
    data = json.loads(text)
    if not isinstance(data, dict):
        return [], []
    packages: list[str] = []
    entry_points: list[str] = []
    name = data.get("name")
    version = data.get("version")
    if isinstance(name, str) and isinstance(version, str):
        packages.append(f"{name}@{version}")
    binary = data.get("bin")
    if isinstance(binary, dict):
        entry_points += [
            f"{key}={value}"
            for key, value in binary.items()
            if isinstance(value, str)
        ]
    elif isinstance(binary, str) and isinstance(name, str):
        entry_points.append(f"{name}={binary}")
    return packages, entry_points


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def scan_surface(root: str, ignore: Iterable[str] = ()) -> Surface:
    """Measure every public surface ``root`` exposes, from its files alone.

    ``ignore`` never hides a finding -- discovery reports what is there.  It only
    labels the matching deltas :data:`IGNORE` later, so an operator's "we are not
    adopting that" stays visible instead of silently shrinking the surface.
    """
    verbs: list[str] = []
    env: list[str] = []
    entry_points: list[str] = []
    packages: list[str] = []
    signatures: list[Signature] = []
    workflows: list[str] = []
    triggers: list[tuple[str, str]] = []
    unparsed: list[str] = []

    for rel in walk_files(root):
        path = os.path.join(root, rel)
        posix = rel.replace(os.sep, "/")
        suffix = posix[posix.rfind(".") :] if "." in posix.rsplit("/", 1)[-1] else ""
        try:
            if posix.startswith(_WORKFLOW_DIR) and suffix in _WORKFLOW_SUFFIXES:
                name = posix.rsplit("/", 1)[-1][: -len(suffix)]
                workflows.append(name)
                text = _read_text(path)
                triggers += [(name, trigger) for trigger in ci_policy.workflow_triggers(text)]
            elif suffix in _PY_SUFFIXES:
                tree = ast.parse(_read_text(path), filename=rel)
                found_verbs, found_env, found_signatures = _python_surface(
                    tree, _module_name(posix)
                )
                verbs += found_verbs
                env += found_env
                signatures += found_signatures
            elif suffix in _JS_SUFFIXES:
                found_verbs, found_env = _js_surface(_read_text(path))
                verbs += found_verbs
                env += found_env
            elif posix.rsplit("/", 1)[-1] == "pyproject.toml":
                found_packages, found_entries = _pyproject_surface(_read_text(path))
                packages += found_packages
                entry_points += found_entries
            elif posix.rsplit("/", 1)[-1] == "package.json":
                found_packages, found_entries = _package_json_surface(_read_text(path))
                packages += found_packages
                entry_points += found_entries
            else:
                continue
        except (OSError, SyntaxError, ValueError, RecursionError) as error:
            # A file whose surface could not be read is itself a finding: the
            # delta list would otherwise claim a complete inventory.
            detail = str(error) or type(error).__name__
            unparsed.append(f"{rel} [{type(error).__name__}: {detail[:120]}]")

    return Surface(
        root=root,
        cli_verbs=tuple(sorted(set(verbs))),
        workflows=tuple(sorted(set(workflows))),
        workflow_triggers=tuple(sorted(set(triggers))),
        env_vars=tuple(sorted(set(env))),
        entry_points=tuple(sorted(set(entry_points))),
        packages=tuple(sorted(set(packages))),
        signatures=tuple(sorted(signatures, key=lambda item: (item.module, item.name))),
        unparsed=tuple(sorted(set(unparsed))),
        ignore=tuple(sorted(set(ignore))),
    )


# -- the baseline -----------------------------------------------------------

_REQUIRED_BASELINE_FIELDS = (
    "cli_verbs",
    "workflows",
    "workflow_triggers",
    "env_vars",
    "entry_points",
    "packages",
    "signatures",
)


def save_baseline(surface: Surface, path: str) -> None:
    """Write the measured surface so a later run can be diffed against it."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(surface.to_json(), handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_baseline(path: str) -> Surface:
    """Read a stored baseline, refusing anything this run cannot compare to."""
    try:
        text = _read_text(path)
    except FileNotFoundError:
        raise BaselineError(f"no baseline at {path}") from None
    except OSError as error:
        raise BaselineError(f"{path}: unreadable baseline: {error}") from None
    try:
        data = json.loads(text)
    except ValueError as error:
        raise BaselineError(f"{path}: not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise BaselineError(f"{path}: expected an object, found {type(data).__name__}")
    if data.get("schema") != BASELINE_SCHEMA:
        raise BaselineError(
            f"{path}: unknown baseline schema {data.get('schema')!r}, expected {BASELINE_SCHEMA!r}"
        )
    missing = [field for field in _REQUIRED_BASELINE_FIELDS if field not in data]
    if missing:
        raise BaselineError(f"{path}: baseline is missing {', '.join(missing)}")
    signatures = []
    for item in data["signatures"]:
        if not isinstance(item, dict) or "name" not in item:
            raise BaselineError(f"{path}: signature is not an object: {item!r}")
        signatures.append(
            Signature(
                module=str(item.get("module", "")),
                name=str(item["name"]),
                parameters=tuple(str(name) for name in item.get("parameters", ())),
            )
        )
    return Surface(
        root=str(data.get("root", "")),
        cli_verbs=tuple(str(item) for item in data["cli_verbs"]),
        workflows=tuple(str(item) for item in data["workflows"]),
        workflow_triggers=tuple(
            (str(pair[0]), str(pair[1])) for pair in data["workflow_triggers"] if len(pair) == 2
        ),
        env_vars=tuple(str(item) for item in data["env_vars"]),
        entry_points=tuple(str(item) for item in data["entry_points"]),
        packages=tuple(str(item) for item in data["packages"]),
        signatures=tuple(signatures),
        unparsed=tuple(str(item) for item in data.get("unparsed", ())),
        ignore=tuple(str(item) for item in data.get("ignore", ())),
    )


# -- the delta --------------------------------------------------------------

_TUPLE_FIELDS = (
    ("cli-verb", lambda surface: surface.cli_verbs),
    ("workflow", lambda surface: surface.workflows),
    ("workflow-trigger", lambda surface: [f"{a}:{b}" for a, b in surface.workflow_triggers]),
    ("env-var", lambda surface: surface.env_vars),
    ("entry-point", lambda surface: surface.entry_points),
    ("package", lambda surface: surface.packages),
    ("unparsed-file", lambda surface: surface.unparsed),
)


def _carries_our_brand(value: str) -> bool:
    lowered = value.casefold()
    return any(token.casefold() in lowered for token in _FORK_TOKENS)


def diff_surface(before: Surface, after: Surface) -> list[SurfaceDelta]:
    """Every movement between two scans, each with the action it calls for.

    Discovery does not subtract: an item in ``before`` but not ``after`` is a
    removal upstream made, which is the fork's problem, not a vanished row.
    """
    ignored = set(before.ignore) | set(after.ignore)
    deltas: list[SurfaceDelta] = []

    def build(kind: str, field: str, was: str, now: str) -> SurfaceDelta:
        value = now or was
        return SurfaceDelta(
            kind=kind, field=field, before=was, after=now, ignored=f"{field}:{value}" in ignored
        )

    for field, getter in _TUPLE_FIELDS:
        was = set(getter(before))
        now = set(getter(after))
        deltas += [build("added", field, "", value) for value in sorted(now - was)]
        deltas += [build("removed", field, value, "") for value in sorted(was - now)]

    before_signatures = {item.key: item for item in before.signatures}
    after_signatures = {item.key: item for item in after.signatures}
    for key in sorted(set(before_signatures) | set(after_signatures)):
        was, now = before_signatures.get(key), after_signatures.get(key)
        if was and now:
            if was.parameters != now.parameters:
                deltas.append(build("changed", "signature", was.render(), now.render()))
        elif now is not None:
            deltas.append(build("added", "signature", "", now.render()))
        else:
            assert was is not None
            deltas.append(build("removed", "signature", was.render(), ""))

    return sorted(deltas, key=lambda item: (item.field, item.kind, item.before, item.after))


# -- orphans ----------------------------------------------------------------


def find_orphans(
    fork_files: Iterable[str],
    upstream_files: Iterable[str],
    rules: BrandingRules | None = None,
    *,
    owned_paths: Iterable[str] = (),
    previously_obsolete: Iterable[str] = (),
) -> tuple[Orphan, ...]:
    """Name the fork paths that no upstream file brands into.

    ``upstream`` is the *current* upstream tree, so a fork file whose twin
    upstream deleted has no branded path to match.  When the last source delta
    already reported that twin retired (``previously_obsolete``, from
    :attr:`hundredways.forkcheck.SourceDeltaReport.obsolete_mapped`) the orphan is
    ``deleted-twin``: branding can never re-derive a file upstream no longer
    carries, so it is permanent divergence until something deletes or ports it.
    Anything else is ``unclassified`` and needs an ownership decision, not an
    assumption.
    """
    rules = rules or BrandingRules()
    upstream = sorted(set(upstream_files))
    twins = set(collision_safe_path_map(upstream, rules).values()) if upstream else set()
    obsolete = set(previously_obsolete)
    found: list[Orphan] = []
    for path in sorted(set(fork_files)):
        if path in twins:
            continue
        if is_explicitly_owned(path, owned_paths):
            continue
        if path in obsolete:
            found.append(
                Orphan(
                    path=path,
                    kind="deleted-twin",
                    evidence=(
                        "the last source delta retired the upstream twin of this path, and no "
                        "current upstream file brands into it"
                    ),
                    suggestion=(
                        "port upstream's replacement or delete this file: branding cannot "
                        "re-derive a path upstream removed"
                    ),
                )
            )
        else:
            found.append(
                Orphan(
                    path=path,
                    kind="unclassified",
                    evidence=(
                        f"no upstream file brands into {path} and it is not in the ownership set"
                    ),
                    suggestion=(
                        "declare it owned (owned_paths or config/owned-assets/) or delete it, so "
                        "the next sync does not have to guess"
                    ),
                )
            )
    return tuple(found)
