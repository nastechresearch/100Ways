import json
from pathlib import Path

from hundredways.brandassets import (
    asset_role,
    asset_summary,
    audit_brand_assets,
    registered_sources,
    unreferenced_sources,
)


def _registry(tmp_path: Path, mapping: dict[str, str], files: dict[str, bytes]) -> Path:
    root = tmp_path / "config" / "owned-assets"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps(mapping), encoding="utf-8")
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_asset_role_classifies_identity_art():
    assert asset_role("website/favicon.ico") == "favicon"
    assert asset_role("website/logo.png") == "logo"
    assert asset_role("assets/banner.png") == "banner"
    assert asset_role("desktop/nastech-bantu.jpg") == "mascot"
    assert asset_role("desktop/icon.icns") == "app-icon"
    assert asset_role("pet/pet-egg-sheet.png") == "pet"
    assert asset_role("misc/blob.bin") == "asset"


def test_registered_sources_skips_the_manifest_itself(tmp_path):
    _registry(tmp_path, {}, {"website/logo.png": b"X"})

    found = registered_sources(str(tmp_path / "config" / "owned-assets"))

    assert set(found) == {"website/logo.png"}


def test_unreferenced_sources_lists_only_unmapped_files(tmp_path):
    root = _registry(
        tmp_path,
        {"website/static/img/logo.png": "website/logo.png"},
        {"website/logo.png": b"OURS", "website/favicon.ico": b"FAV"},
    )

    assert set(unreferenced_sources(str(root))) == {"website/favicon.ico"}


def test_audit_blocks_unreferenced_identity_art_and_reviews_the_rest(tmp_path):
    root = _registry(
        tmp_path,
        {"a/logo.png": "website/logo.png"},
        {"website/logo.png": b"OURS", "website/favicon.ico": b"FAV", "misc/blob.bin": b"Z"},
    )

    issues = audit_brand_assets(str(root))
    by_path = {issue.path: issue for issue in issues}

    assert by_path["website/favicon.ico"].severity == "block"
    assert by_path["website/favicon.ico"].role == "favicon"
    assert by_path["misc/blob.bin"].severity == "review"
    assert "website/logo.png" not in by_path


def test_audit_blocks_a_manifest_entry_with_a_missing_source(tmp_path):
    root = _registry(tmp_path, {"a/logo.png": "website/gone.png"}, {})

    issues = audit_brand_assets(str(root))

    assert any(
        issue.code == "source-missing"
        and issue.path == "a/logo.png"
        and issue.severity == "block"
        for issue in issues
    )


def test_audit_blocks_candidate_bytes_that_differ_from_the_owned_asset(tmp_path):
    root = _registry(
        tmp_path,
        {"website/static/img/logo.png": "website/logo.png"},
        {"website/logo.png": b"OURS"},
    )
    candidate = tmp_path / "candidate"
    (candidate / "website" / "static" / "img").mkdir(parents=True)
    (candidate / "website" / "static" / "img" / "logo.png").write_bytes(b"HERMES")

    issues = audit_brand_assets(str(root), candidate_root=str(candidate))

    assert any(issue.code == "asset-leak" and issue.severity == "block" for issue in issues)


def test_audit_is_clean_when_the_candidate_matches(tmp_path):
    root = _registry(
        tmp_path,
        {"website/static/img/logo.png": "website/logo.png"},
        {"website/logo.png": b"OURS"},
    )
    candidate = tmp_path / "candidate"
    (candidate / "website" / "static" / "img").mkdir(parents=True)
    (candidate / "website" / "static" / "img" / "logo.png").write_bytes(b"OURS")

    assert audit_brand_assets(str(root), candidate_root=str(candidate)) == []


def test_asset_summary_counts_by_severity(tmp_path):
    root = _registry(
        tmp_path,
        {"a/logo.png": "website/gone.png"},
        {"website/favicon.ico": b"FAV"},
    )

    issues = audit_brand_assets(str(root))

    assert asset_summary(issues) == {"block": 2, "review": 0}
