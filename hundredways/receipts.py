"""Tamper-evident evidence receipts for 100Ways PR-only synchronization."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .integrity import audit_candidate_archive, audit_candidate_tree, tree_digest

if TYPE_CHECKING:
    from .weekly_sync import WeeklyFullSyncReport

GATE_RECEIPT_SCHEMA = "100ways.gate-decision-receipt/v1"
PUBLICATION_RECEIPT_SCHEMA = "100ways.publication-authorization-receipt/v1"


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest for a file without loading it all at once."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tool_version(*command: str) -> str:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"
    return (completed.stdout or completed.stderr).strip().splitlines()[0]


def verify_receipt_integrity(receipt: dict[str, Any]) -> bool:
    """Validate a receipt's canonical payload digest without mutating its data."""
    integrity = receipt.get("integrity")
    if not isinstance(integrity, dict):
        return False
    expected = integrity.get("payload_sha256")
    if not isinstance(expected, str):
        return False
    unsigned = {key: value for key, value in receipt.items() if key != "integrity"}
    return expected == _canonical_digest(unsigned)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _source_provenance(candidate_root: Path) -> dict[str, Any]:
    manifest = _load_json(candidate_root / "manifest.json")
    provenance = manifest.get("source_provenance", {})
    return provenance if isinstance(provenance, dict) else {}


def _decision_payload(report: WeeklyFullSyncReport | dict[str, Any]) -> dict[str, Any]:
    if not isinstance(report, dict) and hasattr(report, "to_dict"):
        serialized = report.to_dict()
        if isinstance(serialized, dict) and isinstance(serialized.get("gate"), str):
            return serialized
    if isinstance(report, dict) and isinstance(report.get("gate"), str):
        return report
    raise ValueError("gate receipt requires a serialized decision with a gate result")


def build_gate_decision_receipt(
    report: WeeklyFullSyncReport | dict[str, Any],
    *,
    candidate_root: str | Path,
    artifact_path: str | Path,
) -> dict[str, Any]:
    """Build an immutable evidence payload for a completed gate decision."""
    root = Path(candidate_root)
    artifact = Path(artifact_path)
    if not root.is_dir():
        raise ValueError(f"candidate root does not exist: {root}")
    if not artifact.is_file():
        raise ValueError(f"candidate artifact does not exist: {artifact}")

    candidate_issues = audit_candidate_tree(root)
    archive_issues = audit_candidate_archive(artifact)
    if candidate_issues or archive_issues:
        details = ", ".join(
            f"{issue.code}:{issue.path}" for issue in (*candidate_issues, *archive_issues)
        )
        raise ValueError(f"candidate integrity checks failed: {details}")

    decision = _decision_payload(report)
    gate = decision["gate"]
    payload: dict[str, Any] = {
        "schema": GATE_RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "decision": decision["gate"],
        "hard_gate_inputs": decision,
        "hard_gate_output": {
            "gate_passes": gate == "PASS",
            "review_required": bool(decision.get("review_required", False)),
            "publication_allowed": gate == "PASS",
        },
        "source": {
            "upstream_sha": decision.get("upstream_sha", ""),
            "snapshot_upstream_sha": decision.get("snapshot_upstream_sha", ""),
            "previous_sha": decision.get("previous_sha", ""),
            "provenance": _source_provenance(root),
        },
        "candidate_artifact": {
            "filename": artifact.name,
            "bytes": artifact.stat().st_size,
            "sha256": sha256_file(artifact),
            "candidate_tree_sha256": tree_digest(root),
        },
        "tools": {
            "python": sys.version.split()[0],
            "git": _tool_version("git", "--version"),
            "platform": platform.platform(),
        },
    }
    payload["integrity"] = {"payload_sha256": _canonical_digest(payload)}
    return payload


def write_gate_decision_receipt(
    path: str | Path,
    report: WeeklyFullSyncReport | dict[str, Any],
    *,
    candidate_root: str | Path,
    artifact_path: str | Path,
) -> Path:
    """Write the gate receipt and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = build_gate_decision_receipt(
        report,
        candidate_root=candidate_root,
        artifact_path=artifact_path,
    )
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def build_publication_authorization_receipt(
    *,
    gate_receipt_path: str | Path,
    artifact_path: str | Path,
    upstream_sha: str,
    candidate_branch: str,
    target_repository: str,
    target_base: str,
) -> dict[str, Any]:
    """Authorize exactly one candidate-branch PR action after a PASS receipt."""
    gate_path = Path(gate_receipt_path)
    gate_receipt = _load_json(gate_path)
    if gate_receipt.get("schema") != GATE_RECEIPT_SCHEMA:
        raise ValueError("missing or invalid gate decision receipt")
    if not verify_receipt_integrity(gate_receipt):
        raise ValueError("gate decision receipt integrity check failed")
    if gate_receipt.get("decision") != "PASS":
        raise ValueError("publication authorization requires a PASS gate receipt")
    artifact = gate_receipt.get("candidate_artifact", {})
    if not isinstance(artifact, dict) or not artifact.get("sha256"):
        raise ValueError("gate receipt is missing the candidate artifact digest")
    actual_digest = sha256_file(artifact_path)
    if actual_digest != artifact["sha256"]:
        raise ValueError("candidate artifact digest does not match the PASS gate receipt")

    payload: dict[str, Any] = {
        "schema": PUBLICATION_RECEIPT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "upstream_sha": upstream_sha,
        "target": {
            "repository": target_repository,
            "base": target_base,
            "candidate_branch": candidate_branch,
        },
        "evidence": {
            "gate_receipt_sha256": sha256_file(gate_path),
            "candidate_artifact_sha256": actual_digest,
        },
        "authorized_actions": ["force-push-candidate-branch", "create-or-update-pull-request"],
        "prohibited_actions": ["merge", "tag", "release", "deploy"],
    }
    payload["integrity"] = {"payload_sha256": _canonical_digest(payload)}
    return payload


def write_publication_authorization_receipt(path: str | Path, **kwargs: Any) -> Path:
    """Write the narrow #344 authorization receipt and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_publication_authorization_receipt(**kwargs), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return target
