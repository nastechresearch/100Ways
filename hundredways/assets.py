"""Owned-assets registry: images we own that must win over upstream's.

The 100Ways pipeline brands the real Hermes tree into a Nastech snapshot.
For most files "branding" means token/rename transforms of Hermes content.
But the fork owns a handful of BINARY assets (logo, banner, mascot, icons)
that are NOT derived from Hermes at all - the upstream versions are just
renamed copies.  If we ship the upstream bytes, every Nastech-Update#N would
silently push Hermes branding back into the fork.

The registry lives in the fork repo at ``config/owned-assets/``:

    config/owned-assets/
      manifest.json            # {"<fork path>": "<asset file>"}
      banner.png               # our banner (1145x196)
      logo.png                 # our logo
      desktop/nastech-bantu.jpg  # our mascot (renamed from girl)
      ...

``manifest.json`` maps a FORK-RELATIVE TARGET PATH to the asset file that
must be substituted.  During brand/verify, any upstream file that maps to a
target path in the manifest is replaced by OUR asset instead of the upstream
bytes.  The parity gate then verifies against our asset, so updates always
match the fork.
"""

from __future__ import annotations

import io
import json
import os

from PIL import Image

MANIFEST_NAME = "manifest.json"

# Tauri validates these named bootstrap icons at compile time. Preserve the
# approved NasTech pixels while normalizing container mode and dimensions every
# time an owned candidate asset is materialized.
_TAURI_BOOTSTRAP_ICON_SIZES: dict[str, tuple[int, int]] = {
    "apps/bootstrap-installer/src-tauri/icons/32x32.png": (32, 32),
    "apps/bootstrap-installer/src-tauri/icons/128x128.png": (128, 128),
    "apps/bootstrap-installer/src-tauri/icons/128x128@2x.png": (256, 256),
}


def _normalized_tauri_icon_bytes(path: str, size: tuple[int, int]) -> bytes | None:
    """Return a deterministic RGBA PNG for a declared bootstrap icon."""
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            if rgba.size != size:
                rgba = rgba.resize(size, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            rgba.save(out, format="PNG")
            return out.getvalue()
    except (OSError, ValueError):
        return None


def default_owned_assets_dir(repo: str) -> str:
    """The registry lives at the repo root: config/owned-assets/."""
    return os.path.join(repo, "config", "owned-assets")


class OwnedAssets:
    """Resolve fork target paths to the local asset file that owns them."""

    def __init__(self, root: str | None = None, repo: str | None = None):
        if root is None:
            root = default_owned_assets_dir(repo or "")
        self.root = root
        self._map: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        manifest = os.path.join(self.root, MANIFEST_NAME)
        if not os.path.isfile(manifest):
            return
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._map = {str(k): str(v) for k, v in data.items()}

    @property
    def count(self) -> int:
        return len(self._map)

    @property
    def mapping(self) -> dict[str, str]:
        """Copy of the declared target-to-owned-source map for audit code."""
        return dict(self._map)

    def has(self, fork_path: str) -> bool:
        return fork_path in self._map

    def asset_bytes(self, fork_path: str) -> bytes | None:
        """Bytes of the owned asset for a fork target path, or None."""
        rel = self._map.get(fork_path)
        if not rel:
            return None
        path = os.path.join(self.root, rel)
        if not os.path.isfile(path):
            return None
        required_size = _TAURI_BOOTSTRAP_ICON_SIZES.get(fork_path)
        if required_size:
            return _normalized_tauri_icon_bytes(path, required_size)
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def asset_path(self, fork_path: str) -> str | None:
        rel = self._map.get(fork_path)
        if not rel:
            return None
        path = os.path.join(self.root, rel)
        return path if os.path.isfile(path) else None
