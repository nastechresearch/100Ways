"""First-class brand assets: logos, icons, favicons, banners, mascots.

The 100Ways pipeline brands *text* by transforming it.  Visual identity is
different: the pixels we ship are byte-for-byte the fork's, and the fork owns
them under ``config/owned-assets/``.  A text rule can never protect a PNG, so a
broken manifest silently lets upstream art back into every release.

``manifest.json`` maps a **fork target path** to an **owned source file**.  This
module reports the three ways that can go wrong:

* *unreferenced* -- an owned file (a logo or icon) the manifest never mentions,
  so the engine drops it and upstream bytes win.
* *source-missing* -- a manifest entry whose source file is gone.
* *asset-leak* -- with a candidate tree, a declared target whose shipped bytes
  differ from the owned file, i.e. the release is carrying someone else's art.

:func:`audit_brand_assets` classifies each path (logo/icon/favicon/banner/
mascot) so the review focuses on identity art.  It only *reports*: repairing a
manifest is a reviewed change to the fork.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .assets import OwnedAssets

# Filename -> identity role.  Order matters: the first hit wins.
_ROLES: tuple[tuple[str, str], ...] = (
    ("favicon", "favicon"),
    ("apple-touch", "icon"),
    ("logo", "logo"),
    ("banner", "banner"),
    ("bantu", "mascot"),
    ("mascot", "mascot"),
    ("sprite", "sprite"),
    ("frame", "frame"),
    ("installer/", "app-icon"),  # dimension-named installer icons
    ("icon", "app-icon"),
    ("pet", "pet"),
)

_ICON_SUFFIXES = (".icns", ".ico")
_IDENTITY_ROLES = frozenset({"logo", "favicon", "banner", "mascot", "icon", "app-icon"})


def asset_role(relative_path: str) -> str:
    """Classify an asset path into a reviewable identity role."""
    name = relative_path.lower()
    for needle, role in _ROLES:
        if needle in name:
            return role
    if name.endswith(_ICON_SUFFIXES):
        return "app-icon"
    return "asset"


@dataclass(frozen=True)
class AssetIssue:
    """One finding about the owned-asset registry or its materialization."""

    code: str
    path: str
    role: str
    detail: str
    severity: str = "review"  # review or block

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "path": self.path,
            "role": self.role,
            "detail": self.detail,
            "severity": self.severity,
        }


def registered_sources(assets_root: str) -> dict[str, str]:
    """Every owned asset file on disk, relative path -> absolute path."""
    root = Path(assets_root)
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        found[path.relative_to(root).as_posix()] = str(path)
    return found


def unreferenced_sources(assets_root: str) -> dict[str, str]:
    """Owned files the manifest never materializes, relative -> absolute."""
    referenced = set(OwnedAssets(root=assets_root).mapping.values())
    return {
        rel: abspath
        for rel, abspath in registered_sources(assets_root).items()
        if rel not in referenced
    }


def _severity_for(role: str) -> str:
    return "block" if role in _IDENTITY_ROLES else "review"


def audit_brand_assets(assets_root: str, candidate_root: str = "") -> list[AssetIssue]:
    """Report unowned, source-less, or non-ours brand assets.

    ``assets_root`` is the registry directory that holds ``manifest.json``;
    ``candidate_root`` is the branded tree, which lets the registry be checked
    against what a release would actually ship.
    """
    issues: list[AssetIssue] = []
    owned = OwnedAssets(root=assets_root)

    for rel in sorted(unreferenced_sources(assets_root)):
        role = asset_role(rel)
        issues.append(
            AssetIssue(
                code="asset-unreferenced",
                path=rel,
                role=role,
                detail="owned source is not in manifest.json, so upstream bytes win",
                severity=_severity_for(role),
            )
        )

    for target, source in sorted(owned.mapping.items()):
        if not (Path(assets_root) / source).is_file():
            issues.append(
                AssetIssue(
                    code="source-missing",
                    path=target,
                    role=asset_role(source),
                    detail=f"manifest maps {target!r} to a missing source {source!r}",
                    severity="block",
                )
            )

    if not candidate_root:
        return issues
    for target in sorted(owned.mapping):
        source = owned.mapping[target]
        expected = Path(assets_root) / source
        actual = Path(candidate_root) / target
        if not expected.is_file() or not actual.is_file():
            continue
        try:
            if actual.read_bytes() != expected.read_bytes():
                issues.append(
                    AssetIssue(
                        code="asset-leak",
                        path=target,
                        role=asset_role(source),
                        detail="candidate ships bytes that differ from the owned asset",
                        severity="block",
                    )
                )
        except OSError:
            continue
    return issues


def asset_summary(issues: list[AssetIssue]) -> dict[str, int]:
    """Count findings by severity for a compact report."""
    return {
        "block": sum(1 for issue in issues if issue.severity == "block"),
        "review": sum(1 for issue in issues if issue.severity == "review"),
    }
