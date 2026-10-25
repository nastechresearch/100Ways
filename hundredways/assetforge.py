"""Owned-asset policy: which classes of art the fork must own, and whether it does.

``config/owned-assets/manifest.json`` records what the registry claims today.  It
cannot say what the registry *must* claim, so two ways upstream art reaches a
release go unnoticed: Hermes renames a directory our assets land in and every
claim under the old prefix stops landing, or Hermes adds a logo beside the one we
already replace and their pixels ship under our name.  ``config/asset-policy.yml``
carries that intent; this module measures it.

It only reports.  ``updates.apply_owned_assets`` is the one path that writes owned
bytes into a tree, and a checker that also copied would be a second policy that
nobody reviewed.

Three findings, each named for what it needed to see:

* ``class-unowned`` -- the policy requires a class the registry claims nothing
  under.  Measured from policy plus manifest, so it runs with no checkout at all.
* ``art-unclaimed`` -- art in a required class that no manifest target names.
  With an upstream checkout it is proved to be *their* bytes -- the path branding
  writes into exists upstream -- and identity art then blocks a release.  Without
  one it cannot be told from a file the fork authored itself, so it is a review
  and the run says the attribution is missing.
* ``class-absent`` -- a required class has no files in the tree: the directory
  moved or vanished, so the registry is claiming a path nobody ships.

The last two need a tree, and ``art-unclaimed`` needs an upstream checkout beside
it to say whose bytes ship.  A run missing either never reports those as clean --
the summary names what it could not measure, because an unprobed class judges
nothing.  Art upstream does not ship is a different question, already answered by
:func:`hundredways.adapters.find_orphans`: a fork path no upstream file brands
into has no sync to overwrite it, so it needs no registry claim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assets import OwnedAssets
from .brandassets import AssetIssue, asset_role, severity_for_role
from .forkcheck import walk_files
from .policy import PolicyError
from .policy import load as load_policy
from .rules import BrandingRules, collision_safe_path_map

POLICY_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "asset-policy.yml")
)

_SCHEMA = {"version": "int", "must_own": "list"}

# Only these are art: a ``logo.py`` or ``icons/README.md`` inside a required class
# is not pixels and must never be measured as though it were.
_ART_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".icns", ".svg")

# What :func:`brandassets.asset_role` answers for a path it does not recognize.
_GENERIC_ROLE = "asset"

# What no run can judge without a checkout to walk.
_NOT_WITHOUT_TREE = ("art-unclaimed", "class-absent")

# What no run can judge without the upstream checkout beside it.
_NOT_WITHOUT_UPSTREAM = ("art-unclaimed attribution",)


@dataclass(frozen=True)
class AssetPolicy:
    """The classes of identity art a Nastech release must own outright."""

    version: int
    must_own: tuple[str, ...]

    def __post_init__(self) -> None:
        # Normalized here, not in load(): a policy built by hand in a test is
        # checked the same way a policy read from disk is.
        object.__setattr__(self, "must_own", _prefixes(list(self.must_own), "asset policy"))

    @staticmethod
    def load(path: str = "") -> "AssetPolicy":
        source = path or POLICY_PATH
        doc = load_policy(source, _SCHEMA)
        return AssetPolicy(version=doc["version"], must_own=tuple(str(p) for p in doc["must_own"]))

    def replaced(self, **overrides: Any) -> "AssetPolicy":
        """This policy with one field swapped -- tests move a knob, not all."""
        values: dict[str, Any] = {"version": self.version, "must_own": self.must_own}
        for name, value in overrides.items():
            if name not in values:
                raise KeyError(f"unknown policy field {name!r}")
            values[name] = value
        return AssetPolicy(**values)


def _prefixes(values: list[Any], source: str) -> tuple[str, ...]:
    """The declared classes, checked for the one shape that can match a path.

    A trailing slash is not decoration.  ``assets`` would also prefix-match
    ``assetsmith/``, and a policy that claims a directory it never meant to is
    worse than one that says nothing.
    """
    prefixes: list[str] = []
    for value in values:
        text = str(value)
        if not text.endswith("/") or text.startswith("/") or ".." in text:
            raise PolicyError(
                f"{source}: a must_own class must be a fork-relative directory "
                f"ending in '/', not {text!r}"
            )
        if text in prefixes:
            raise PolicyError(f"{source}: {text!r} is listed in must_own twice")
        prefixes.append(text)
    return tuple(prefixes)


def class_coverage(owned: OwnedAssets, prefixes: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    """Each required class with the manifest targets that currently cover it."""
    mapping = owned.mapping
    return {
        prefix: tuple(sorted(t for t in mapping if t.startswith(prefix)))
        for prefix in prefixes
    }


def _art(paths: list[str], prefix: str) -> list[str]:
    """The art files one class holds, out of an already-walked tree, sorted."""
    return sorted(
        path
        for path in paths
        if path.startswith(prefix) and Path(path).suffix.lower() in _ART_SUFFIXES
    )


def branded_paths(root: str, rules: BrandingRules | None = None) -> set[str]:
    """Every fork-relative path branding writes into, read from one upstream checkout.

    ``apps/bootstrap-installer/public/nous-girl.png`` brands to
    ``.../nastech-bantu.png``, so the fork name is the only thing a tree shows and
    the upstream name is the only thing that proves the pixels are not ours.
    """
    return set(collision_safe_path_map(walk_files(root), rules or BrandingRules()).values())


def audit_asset_policy(
    policy: AssetPolicy,
    owned: OwnedAssets,
    tree: str = "",
    upstream: str = "",
    rules: BrandingRules | None = None,
) -> list[AssetIssue]:
    """Measure the registry -- and a tree, if there is one -- against the policy.

    ``owned`` reads its own manifest, so a caller pointed at a fork checkout audits
    that fork's claims.  ``tree`` and ``upstream`` only ever widen what gets
    measured: with no tree the two tree findings are withheld and said out loud,
    and with no upstream every ``art-unclaimed`` is held at review, since nothing
    proves the bytes are upstream's rather than a file the fork wrote.
    """
    issues: list[AssetIssue] = []
    coverage = class_coverage(owned, policy.must_own)
    fork_files = walk_files(tree) if tree else []
    theirs: set[str] | None = branded_paths(upstream, rules) if upstream else None

    for prefix in policy.must_own:
        if not coverage[prefix]:
            issues.append(
                AssetIssue(
                    code="class-unowned",
                    path=prefix,
                    role=asset_role(prefix),
                    detail="the policy requires this class, the registry claims nothing under it",
                    severity="block",
                )
            )
        if not tree:
            continue
        if not any(path.startswith(prefix) for path in fork_files):
            issues.append(
                AssetIssue(
                    code="class-absent",
                    path=prefix,
                    role=asset_role(prefix),
                    detail="the class has no files in the tree, so claims here land nowhere",
                    severity="review",
                )
            )
            continue
        for relative in _art(fork_files, prefix):
            if relative in owned.mapping:
                continue
            role = asset_role(relative)
            if role == _GENERIC_ROLE:
                continue
            if theirs is not None and relative not in theirs:
                continue
            issues.append(_unclaimed(relative, role, theirs))
    return issues


def _unclaimed(path: str, role: str, theirs: set[str] | None) -> AssetIssue:
    """One unclaimed art file, as serious as the evidence about it allows.

    Only called for a path an upstream tree proves, or for a run that has no
    upstream tree at all: a fork-authored file, which no sync can overwrite, is
    the orphan pass's question, not this one.
    """
    if theirs is None:
        detail = "no manifest target names it, and no upstream tree proves whose bytes ship"
        severity = "review"
    else:
        detail = "upstream art brands into this path and no manifest target names it"
        severity = severity_for_role(role)
    return AssetIssue(code="art-unclaimed", path=path, role=role, detail=detail, severity=severity)


def not_measured(tree: str = "", upstream: str = "") -> list[str]:
    """The findings this run could not judge, named instead of assumed clean."""
    if not tree:
        return list(_NOT_WITHOUT_TREE)
    return list(_NOT_WITHOUT_UPSTREAM) if not upstream else []


def render(
    issues: list[AssetIssue], tree: str = "", policy_path: str = "", upstream: str = ""
) -> list[str]:
    """Readable lines in measurement order, naming anything the run withheld."""
    if tree:
        scope = f"tree {tree}, " + (f"upstream {upstream}" if upstream else "no upstream")
    else:
        scope = "no tree: registry and policy only"
    lines = [f"asset policy: {len(issues)} finding(s) ({scope})"]
    for issue in issues:
        lines.append(f"  [{issue.severity}] {issue.code} {issue.path} — {issue.detail}")
    if not issues:
        clean = "  no required class is uncovered"
        lines.append(clean if tree else f"{clean} in what could be measured")
    missing = not_measured(tree, upstream)
    if missing:
        needed = "a tree" if not tree else "an upstream checkout"
        lines.append(f"  not measured without {needed}: " + ", ".join(missing))
    if policy_path:
        lines.append(f"  policy: {policy_path}")
    return lines


def to_json(issues: list[AssetIssue], tree: str = "", upstream: str = "") -> dict[str, Any]:
    """The same findings as a payload, for a chat table or an issue body."""
    return {
        "findings": [issue.to_dict() for issue in issues],
        "tree_measured": bool(tree),
        "upstream_measured": bool(upstream),
        "not_measured": not_measured(tree, upstream),
    }
