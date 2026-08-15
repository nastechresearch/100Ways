from pathlib import Path

from hundredways.sync_summary import write_sync_summary


def test_professional_summary_attributes_source_and_focuses_on_features(tmp_path):
    output = Path(tmp_path) / "SYNC-SUMMARY.md"

    write_sync_summary(
        output,
        upstream_repo=tmp_path,
        baseline_sha="a" * 40,
        upstream_sha="b" * 40,
        files_changed=7,
        changed_areas={"apps": 2, "agent": 5},
        verification=(("Direct source provenance", "Passed"),),
        commit_subjects=("feat: add a workflow automation capability", "fix: improve error handling"),
    )

    summary = output.read_text(encoding="utf-8")
    assert "# NasTech-Agent Update Summary" in summary
    assert "> Powered by NousResearch" in summary
    assert "## Delivered improvements" in summary
    assert "## Technical coverage" in summary
    assert "New capabilities" in summary
    assert "Reliability and fixes" in summary
    assert "rebrand" not in summary.lower()
    assert "No merge, release, or deployment" in summary
