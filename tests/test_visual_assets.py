import io
import json
from pathlib import Path

from PIL import Image

from hundredways.assets import OwnedAssets
from hundredways.visual_assets import compare_owned_to_upstream, inventory


def make_image(path: Path, color: tuple[int, int, int], *, mutate: bool = False) -> None:
    image = Image.new("RGB", (32, 32), color)
    if mutate:
        image.putpixel((0, 0), (color[0] ^ 1, color[1], color[2]))
    image.save(path)


def test_owned_bootstrap_icons_are_normalized_to_tauri_rgba_dimensions(tmp_path):
    assets = tmp_path / "config" / "owned-assets"
    installer = assets / "installer"
    installer.mkdir(parents=True)
    mappings = {
        "apps/bootstrap-installer/src-tauri/icons/32x32.png": ("32x32.png", (32, 32)),
        "apps/bootstrap-installer/src-tauri/icons/128x128.png": ("128x128.png", (128, 128)),
        "apps/bootstrap-installer/src-tauri/icons/128x128@2x.png": ("128x128@2x.png", (256, 256)),
    }
    for source, _ in mappings.values():
        Image.new("RGB", (1024, 1024), (255, 255, 255)).save(installer / source)
    (assets / "manifest.json").write_text(
        json.dumps({target: f"installer/{source}" for target, (source, _) in mappings.items()}),
        encoding="utf-8",
    )

    owned = OwnedAssets(root=str(assets))
    for target, (_, expected_size) in mappings.items():
        payload = owned.asset_bytes(target)
        assert payload is not None
        with Image.open(io.BytesIO(payload)) as image:
            assert image.mode == "RGBA"
            assert image.size == expected_size


def test_visual_inventory_records_raster_identity(tmp_path):
    image = tmp_path / "logo.png"
    make_image(image, (20, 40, 60))

    assets = inventory(str(tmp_path))

    assert len(assets) == 1
    assert assets[0].path == "logo.png"
    assert assets[0].sha256
    assert assets[0].decoded_sha256
    assert assets[0].dhash is not None
    assert assets[0].width == 32


def test_visual_comparison_blocks_exact_upstream_copy(tmp_path):
    owned = tmp_path / "owned"
    upstream = tmp_path / "upstream"
    owned.mkdir()
    upstream.mkdir()
    make_image(owned / "nastech-logo.png", (20, 40, 60))
    make_image(upstream / "hermes-logo.png", (20, 40, 60))

    issues = compare_owned_to_upstream(str(owned), ["nastech-logo.png"], str(upstream))

    assert len(issues) == 1
    assert issues[0].code == "asset-exact-upstream"
    assert issues[0].severity == "block"


def test_visual_comparison_routes_near_duplicate_to_review(tmp_path):
    owned = tmp_path / "owned"
    upstream = tmp_path / "upstream"
    owned.mkdir()
    upstream.mkdir()
    make_image(owned / "nastech-logo.png", (20, 40, 60), mutate=True)
    make_image(upstream / "hermes-logo.png", (20, 40, 60))

    issues = compare_owned_to_upstream(str(owned), ["nastech-logo.png"], str(upstream))

    assert len(issues) == 1
    assert issues[0].code == "asset-near-upstream"
    assert issues[0].severity == "review"
