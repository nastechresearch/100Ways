import json
import zipfile

import pytest

from hundredways.receipts import (
    GATE_RECEIPT_SCHEMA,
    PUBLICATION_RECEIPT_SCHEMA,
    build_gate_decision_receipt,
    build_publication_authorization_receipt,
    sha256_file,
    verify_receipt_integrity,
    write_gate_decision_receipt,
)
from hundredways.weekly_sync import WeeklyFullSyncReport


def _candidate_archive(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("nastech-agent/manifest.json", "{}")
        archive.writestr("nastech-agent/README.md", "verified tree")


def _passing_report() -> WeeklyFullSyncReport:
    return WeeklyFullSyncReport(
        upstream_sha="a" * 40,
        previous_sha="b" * 40,
        snapshot_upstream_sha="a" * 40,
        commits=4,
        files_changed=7,
        added_lines=21,
        deleted_lines=3,
        freshness_ok=True,
    )


def test_gate_receipt_binds_pass_decision_source_and_artifact(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_text(
        json.dumps({"source_provenance": {"acquisition": "fresh-direct-clone"}})
    )
    artifact = tmp_path / "candidate.zip"
    _candidate_archive(artifact)

    receipt = build_gate_decision_receipt(
        _passing_report(), candidate_root=candidate, artifact_path=artifact
    )

    assert receipt["schema"] == GATE_RECEIPT_SCHEMA
    assert receipt["decision"] == "PASS"
    assert receipt["hard_gate_output"]["publication_allowed"] is True
    assert receipt["source"]["provenance"]["acquisition"] == "fresh-direct-clone"
    assert receipt["candidate_artifact"]["sha256"] == sha256_file(artifact)
    assert len(receipt["integrity"]["payload_sha256"]) == 64
    assert verify_receipt_integrity(receipt) is True


def test_publication_receipt_requires_pass_gate_and_is_pr_only(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_text("{}")
    artifact = tmp_path / "candidate.zip"
    _candidate_archive(artifact)
    gate_path = write_gate_decision_receipt(
        tmp_path / "gate_receipt.json",
        _passing_report(),
        candidate_root=candidate,
        artifact_path=artifact,
    )

    receipt = build_publication_authorization_receipt(
        gate_receipt_path=gate_path,
        artifact_path=artifact,
        upstream_sha="a" * 40,
        candidate_branch="100WAYS",
        target_repository="nastechresearch/nastech-agent",
        target_base="main",
    )

    assert receipt["schema"] == PUBLICATION_RECEIPT_SCHEMA
    assert receipt["target"]["candidate_branch"] == "100WAYS"
    assert receipt["authorized_actions"] == [
        "force-push-candidate-branch",
        "create-or-update-pull-request",
    ]
    assert receipt["prohibited_actions"] == ["merge", "tag", "release", "deploy"]


def test_publication_receipt_rejects_tampered_gate_evidence(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_text("{}")
    artifact = tmp_path / "candidate.zip"
    _candidate_archive(artifact)
    gate_path = write_gate_decision_receipt(
        tmp_path / "gate_receipt.json",
        _passing_report(),
        candidate_root=candidate,
        artifact_path=artifact,
    )
    receipt = json.loads(gate_path.read_text())
    receipt["decision"] = "FAIL"
    gate_path.write_text(json.dumps(receipt))

    with pytest.raises(ValueError, match="integrity"):
        build_publication_authorization_receipt(
            gate_receipt_path=gate_path,
            artifact_path=artifact,
            upstream_sha="a" * 40,
            candidate_branch="100WAYS",
            target_repository="nastechresearch/nastech-agent",
            target_base="main",
        )


def test_publication_receipt_rejects_tampered_artifact(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_text("{}")
    artifact = tmp_path / "candidate.zip"
    _candidate_archive(artifact)
    gate_path = write_gate_decision_receipt(
        tmp_path / "gate_receipt.json",
        _passing_report(),
        candidate_root=candidate,
        artifact_path=artifact,
    )
    artifact.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="digest"):
        build_publication_authorization_receipt(
            gate_receipt_path=gate_path,
            artifact_path=artifact,
            upstream_sha="a" * 40,
            candidate_branch="100WAYS",
            target_repository="nastechresearch/nastech-agent",
            target_base="main",
        )


def test_gate_receipt_rejects_invalid_archive(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "manifest.json").write_text("{}")
    artifact = tmp_path / "candidate.zip"
    artifact.write_bytes(b"not-a-zip")

    with pytest.raises(ValueError, match="integrity"):
        build_gate_decision_receipt(
            _passing_report(), candidate_root=candidate, artifact_path=artifact
        )


def test_publication_receipt_rejects_nonpass_gate(tmp_path):
    gate_path = tmp_path / "gate_receipt.json"
    gate_path.write_text(
        json.dumps({
            "schema": GATE_RECEIPT_SCHEMA,
            "decision": "FAIL",
            "candidate_artifact": {"sha256": "a" * 64},
        })
    )

    with pytest.raises(ValueError, match="integrity"):
        build_publication_authorization_receipt(
            gate_receipt_path=gate_path,
            artifact_path=gate_path,
            upstream_sha="a" * 40,
            candidate_branch="100WAYS",
            target_repository="nastechresearch/nastech-agent",
            target_base="main",
        )
