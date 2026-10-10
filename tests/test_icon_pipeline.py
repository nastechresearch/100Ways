"""The shipped icon pipeline is a fixed point the release must not drift from.

The fork's mascot ships as raster art inside the master SVGs, so the fork's
``scripts/generate_icons.py`` composes an ``<image>`` rather than recolouring a
vector ``<path>``.  The engine does not render icons: it ships the generator,
the two raster masters, and every generated output as NasTech-owned assets, and
the fork's ``icons-freshness-check`` lane regenerates them and fails on any byte
difference.  These tests guard the two ways that contract silently breaks:

* the manifest stops registering a shipped pipeline file, so upstream bytes win;
* a bootstrap icon is re-encoded when it is already the exact RGBA PNG the
  generator wrote, so the release stops matching ``--check``.
"""

import json
from pathlib import Path

from PIL import Image

from hundredways.assets import OwnedAssets


def test_owned_bootstrap_icon_is_shipped_byte_for_byte_when_compliant(tmp_path):
    """A compliant RGBA bootstrap icon must survive ``asset_bytes`` unchanged."""
    root = tmp_path / "config" / "owned-assets"
    rel = "icon-pipeline/apps/bootstrap-installer/src-tauri/icons/32x32.png"
    source = root / rel
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (32, 32), (11, 22, 33, 255)).save(source)
    (root / "manifest.json").write_text(
        json.dumps({"apps/bootstrap-installer/src-tauri/icons/32x32.png": rel})
    )

    owned = OwnedAssets(root=str(root))
    shipped = owned.asset_bytes("apps/bootstrap-installer/src-tauri/icons/32x32.png")
    assert shipped == source.read_bytes()


def test_icon_pipeline_registers_raster_masters_and_generator():
    root = Path(__file__).resolve().parents[1]
    registry = root / "config" / "owned-assets"
    manifest = json.loads((registry / "manifest.json").read_text())

    assert manifest["scripts/generate_icons.py"] == "icon-pipeline/scripts/generate_icons.py"
    for color in ("black", "white"):
        target = f"assets/nastech-bantu-{color}.svg"
        assert manifest[target] == f"icon-pipeline/{target}"
        art = (registry / manifest[target]).read_text(encoding="utf-8-sig")
        assert "<image" in art and "<path" not in art, target

    # The DMG volume is hand-made art ("the girl on a drive") and the
    # apps/desktop/packaging/dmg-volume.icns target is derived from it. If the
    # source is not owned, the candidate reads upstream's copy and ships Nous's
    # DMG art (the weekly gate's asset-exact-upstream blocker), so the source
    # must be registered too.
    assert manifest["assets/dmg-volume.png"] == "icon-pipeline/assets/dmg-volume.png"

    generator = (registry / manifest["scripts/generate_icons.py"]).read_text(encoding="utf-8")
    assert "girl_is_raster" in generator and "<image" in generator


def test_owned_manifest_sources_all_exist():
    root = Path(__file__).resolve().parents[1]
    registry = root / "config" / "owned-assets"
    owned = OwnedAssets(root=str(registry))
    missing = sorted(
        source for source in owned.mapping.values() if not (registry / source).is_file()
    )
    assert missing == []
