"""Deterministic visual-asset provenance checks used by the weekly sync policy.

The module classifies exact copies and visual near-duplicates. It intentionally
makes no ownership or legal conclusion: ambiguous visual similarity is a review
signal, while missing or corrupted owned assets are blocking defects.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

RASTER_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".ico", ".bmp", ".gif"}
VECTOR_EXTENSIONS = {".svg"}
ICON_EXTENSIONS = {".icns"}
VISUAL_EXTENSIONS = RASTER_EXTENSIONS | VECTOR_EXTENSIONS | ICON_EXTENSIONS


@dataclass(frozen=True)
class VisualAsset:
    path: str
    sha256: str
    decoded_sha256: str = ""
    dhash: int | None = None
    width: int | None = None
    height: int | None = None
    alpha_pixels: int | None = None
    kind: str = "other"
    decode_error: str = ""


@dataclass(frozen=True)
class VisualIssue:
    code: str
    path: str
    detail: str
    severity: str = "review"  # review or block


def is_visual(path: Path) -> bool:
    return path.suffix.lower() in VISUAL_EXTENSIONS


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pixels(path: Path) -> tuple[bytes, int, int, int, int] | None:
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            width, height = rgba.size
            data = rgba.tobytes()
            alpha_pixels = sum(1 for alpha in data[3::4] if alpha < 255)
            return data, width, height, alpha_pixels, 0
    except Exception:
        return None


def _dhash(path: Path) -> int | None:
    try:
        with Image.open(path) as image:
            gray = ImageOps.grayscale(image.convert("RGBA")).resize((9, 8))
            flattened = getattr(gray, "get_flattened_data", None)
            pixels = list(flattened() if flattened is not None else gray.getdata())
    except Exception:
        return None
    result = 0
    for y in range(8):
        for x in range(8):
            result = (result << 1) | int(pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    return result


def inspect_visual(path: Path, root: Path) -> VisualAsset:
    rel = str(path.relative_to(root))
    raw = path.read_bytes()
    kind = "raster" if path.suffix.lower() in RASTER_EXTENSIONS else "vector" if path.suffix.lower() in VECTOR_EXTENSIONS else "icon"
    decoded = _pixels(path)
    if decoded is None:
        return VisualAsset(path=rel, sha256=sha256_bytes(raw), kind=kind, decode_error="unsupported-or-unreadable")
    pixels, width, height, alpha_pixels, _ = decoded
    canonical = b"RGBA\0" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + pixels
    return VisualAsset(
        path=rel,
        sha256=sha256_bytes(raw),
        decoded_sha256=sha256_bytes(canonical),
        dhash=_dhash(path),
        width=width,
        height=height,
        alpha_pixels=alpha_pixels,
        kind=kind,
    )


def inventory(root: str) -> list[VisualAsset]:
    base = Path(root)
    paths = [p for p in base.rglob("*") if p.is_file() and ".git" not in p.parts and is_visual(p)]
    return [inspect_visual(path, base) for path in sorted(paths)]


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def compare_owned_to_upstream(
    owned_root: str,
    owned_relative_paths: Iterable[str],
    upstream_root: str,
    *,
    near_distance: int = 4,
) -> list[VisualIssue]:
    """Compare manifest-owned visual files with the complete upstream visual inventory."""
    owned_base = Path(owned_root)
    upstream_assets = inventory(upstream_root)
    upstream_by_raw: dict[str, list[VisualAsset]] = {}
    upstream_by_decoded: dict[str, list[VisualAsset]] = {}
    for asset in upstream_assets:
        upstream_by_raw.setdefault(asset.sha256, []).append(asset)
        if asset.decoded_sha256:
            upstream_by_decoded.setdefault(asset.decoded_sha256, []).append(asset)

    issues: list[VisualIssue] = []
    for relative in sorted(owned_relative_paths):
        path = owned_base / relative
        if not path.is_file() or not is_visual(path):
            continue
        asset = inspect_visual(path, owned_base)
        exact = upstream_by_raw.get(asset.sha256, []) + upstream_by_decoded.get(asset.decoded_sha256, [])
        exact_paths = sorted({entry.path for entry in exact})
        if exact_paths:
            issues.append(VisualIssue(
                "asset-exact-upstream",
                relative,
                "exact visual identity with upstream asset(s): " + ", ".join(exact_paths),
                "block",
            ))
            continue
        if asset.dhash is None:
            continue
        candidates = [
            (hamming_distance(asset.dhash, other.dhash), other.path)
            for other in upstream_assets if other.dhash is not None
        ]
        if not candidates:
            continue
        distance, nearest = min(candidates)
        if distance <= near_distance:
            issues.append(VisualIssue(
                "asset-near-upstream",
                relative,
                f"perceptual-hash distance {distance} from upstream asset {nearest}",
                "review",
            ))
    return issues
