from hundredways.release_readiness import assess_release_readiness


SHA = "a" * 40
MERGE_SHA = "b" * 40


def _manifest(**overrides):
    manifest = {
        "upstream_sha": SHA,
        "source_provenance": {"acquisition": "fresh-direct-clone"},
    }
    manifest.update(overrides)
    return manifest


def test_release_readiness_allows_only_a_matching_direct_source_candidate():
    readiness = assess_release_readiness(
        upstream_tag="v2026.8.13",
        upstream_tag_sha=SHA,
        manifest=_manifest(),
        branded_merge_sha=MERGE_SHA,
    )

    assert readiness.ready is True
    assert readiness.to_dict()["gate"] == "READY"
    assert readiness.to_dict()["prohibited_automatic_actions"] == [
        "merge",
        "tag",
        "release",
        "deploy",
    ]


def test_release_readiness_blocks_mismatch_provenance_and_existing_tag():
    readiness = assess_release_readiness(
        upstream_tag="invalid",
        upstream_tag_sha="short",
        manifest=_manifest(
            upstream_sha="c" * 40,
            source_provenance={"acquisition": "cache"},
        ),
        branded_merge_sha="not-a-sha",
        existing_nastech_tag_sha="d" * 40,
    )

    assert readiness.ready is False
    assert {issue.code for issue in readiness.issues} == {
        "release-tag-format",
        "release-tag-sha",
        "release-source-mismatch",
        "release-provenance",
        "branded-merge-sha",
        "nastech-tag-exists",
    }


def test_release_readiness_accepts_the_line_upstream_actually_publishes():
    """Upstream moved from the calendar line to semver; the gate has to follow.

    Releases upstream has published, and the ref forms that number an attempt
    rather than spend a version.  ``v0.22.2`` is what a release of today's code
    would carry; rejecting it would leave every current upstream release
    blocked and the fork unable to name a version that matches its source.
    """
    published = ["v2026.9.24", "v2026.8.16.2", "v0.21.6", "v0.22.0", "v0.22.2"]
    not_releases = ["invalid", "rc.1-v0.22.0", "v0.22.0+canary.20261020T205619Z",
                    "v0.22.0-rc.1", "0.22.2", "v0.22", "v2026.9"]

    for tag in published:
        readiness = assess_release_readiness(
            upstream_tag=tag, upstream_tag_sha=SHA, manifest=_manifest(),
            branded_merge_sha=MERGE_SHA,
        )
        assert readiness.ready is True, f"{tag} is a published upstream release"

    for tag in not_releases:
        readiness = assess_release_readiness(
            upstream_tag=tag, upstream_tag_sha=SHA, manifest=_manifest(),
            branded_merge_sha=MERGE_SHA,
        )
        assert {issue.code for issue in readiness.issues} == {"release-tag-format"}, tag
