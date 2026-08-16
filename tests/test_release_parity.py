import json

import pytest

from hundredways.release_parity import HermesRelease, build_release_parity_report, main

SHA_11 = "a" * 40
SHA_13 = "b" * 40


def test_release_parity_backlog_tracks_only_unmatched_exact_versions():
    report = build_release_parity_report(
        (
            HermesRelease("v2026.8.11", SHA_11, "https://example.invalid/11"),
            HermesRelease("v2026.8.13", SHA_13, "https://example.invalid/13"),
        ),
        ("v2026.8.11",),
    )

    assert report.to_dict()["gate"] == "PENDING"
    assert report.to_dict()["pending_count"] == 1
    assert report.latest_pending is not None
    assert report.latest_pending.tag == "v2026.8.13"
    assert report.latest_pending.upstream_sha == SHA_13
    assert report.to_dict()["prohibited_automatic_actions"] == [
        "merge",
        "tag",
        "release",
        "deploy",
    ]


def test_release_parity_is_current_when_all_matching_tags_exist():
    report = build_release_parity_report(
        (HermesRelease("v2026.8.13", SHA_13),),
        ("v2026.8.13",),
    )

    assert report.to_dict()["gate"] == "CURRENT"
    assert report.backlog == ()


def test_release_parity_rejects_mutable_or_invalid_release_targets():
    with pytest.raises(ValueError, match="immutable target SHA"):
        build_release_parity_report((HermesRelease("v2026.8.13", "short"),), ())


def test_release_parity_cli_uses_peeled_tag_targets(tmp_path):
    releases = tmp_path / "releases.json"
    hermes_refs = tmp_path / "hermes-tags.txt"
    nastech_refs = tmp_path / "nastech-tags.txt"
    output = tmp_path / "parity.json"
    releases.write_text(json.dumps([{"tag_name": "v2026.8.13", "html_url": "https://example.invalid/13"}]))
    hermes_refs.write_text(
        f"tag-object\trefs/tags/v2026.8.13\n{SHA_13}\trefs/tags/v2026.8.13^{{}}\n"
    )
    nastech_refs.write_text("")

    assert main([
        "--hermes-releases", str(releases),
        "--hermes-tag-refs", str(hermes_refs),
        "--nastech-tag-refs", str(nastech_refs),
        "--output", str(output),
    ]) == 0

    assert json.loads(output.read_text())["latest_pending_sha"] == SHA_13


def test_release_parity_ignores_non_calendar_tags():
    report = build_release_parity_report(
        (HermesRelease("nightly", SHA_13),),
        (),
    )

    assert report.backlog == ()
