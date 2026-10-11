"""The shipped icon pipeline is a fixed point the release must not drift from.

The fork invests in branding, not in icon *code*: ``scripts/generate_icons.py``
and the two mascot masters are upstream's files (renames aside), so the candidate
carries no icon logic Hermes does not have.  The masters ship as upstream's
vector ``<path>`` art (``assets/nastech-bantu-{black,white}.svg``) and the
generator recolours that ``<path>`` exactly as upstream does.  The engine does
not render icons: it ships the generator, the two vector masters, and every
generated output as NasTech-owned assets, and the fork's
``icons-freshness-check`` lane regenerates them and fails on any byte
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


def test_icon_pipeline_registers_vector_masters_and_generator():
    root = Path(__file__).resolve().parents[1]
    registry = root / "config" / "owned-assets"
    manifest = json.loads((registry / "manifest.json").read_text())

    assert manifest["scripts/generate_icons.py"] == "icon-pipeline/scripts/generate_icons.py"
    for color in ("black", "white"):
        target = f"assets/nastech-bantu-{color}.svg"
        assert manifest[target] == f"icon-pipeline/{target}"
        art = (registry / manifest[target]).read_text(encoding="utf-8-sig")
        # Upstream's vector master: exactly one <path>, no embedded raster art.
        assert "<path" in art and "<image" not in art, target

    # The DMG volume is hand-made art ("the girl on a drive") and the
    # apps/desktop/packaging/dmg-volume.icns target is derived from it. If the
    # source is not owned, the candidate reads upstream's copy and ships Nous's
    # DMG art (the weekly gate's asset-exact-upstream blocker), so the source
    # must be registered too.
    assert manifest["assets/dmg-volume.png"] == "icon-pipeline/assets/dmg-volume.png"

    generator = (registry / manifest["scripts/generate_icons.py"]).read_text(encoding="utf-8")
    # No fork-only raster scaffolding: the generator recolours the vector <path>
    # and reads the vector masters, so the candidate's pipeline is upstream's.
    assert "girl_is_raster" not in generator and "girl_image" not in generator
    assert 'assets / f"nastech-bantu-{color}.svg"' in generator
    assert 're.search(r"<path' in generator


def test_owned_manifest_sources_all_exist():
    root = Path(__file__).resolve().parents[1]
    registry = root / "config" / "owned-assets"
    owned = OwnedAssets(root=str(registry))
    missing = sorted(
        source for source in owned.mapping.values() if not (registry / source).is_file()
    )
    assert missing == []
