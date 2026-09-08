import pytest

from hundredways.repair_agent import (
    REPAIR_SCHEMA,
    RepairRejected,
    assert_100ways_repo,
    is_allowed_path,
    validate_proposal,
)


def _payload(patch: str, paths: list[str], **extra):
    value = {
        "schema": REPAIR_SCHEMA,
        "classification": "candidate_regression",
        "root_cause": "A deterministic fixture is stale.",
        "confidence": 0.9,
        "allowed_paths": paths,
        "patch": patch,
        "regression_test": "pytest -q tests/test_repair_agent.py",
        "risk_flags": [],
        "needs_human_review": True,
        "forbidden_actions": [
            "edit live checkout",
            "retry",
            "merge",
            "release",
            "deploy",
            "bypass gate",
        ],
    }
    value.update(extra)
    return value


def test_only_100ways_paths_are_allowed():
    assert is_allowed_path("hundredways/repair_agent.py")
    assert is_allowed_path("tests/test_repair_agent.py")
    assert not is_allowed_path("nastech-agent/main.py")
    assert not is_allowed_path(".github/workflows/release.yml")
    assert not is_allowed_path("hundredways/security.py")
    assert not is_allowed_path("../outside.py")


def test_valid_minimal_patch_is_accepted():
    patch = """--- a/hundredways/example.py
+++ b/hundredways/example.py
@@ -1 +1 @@
-old = 1
+old = 2
"""
    proposal = validate_proposal(_payload(patch, ["hundredways/example.py"]))
    assert proposal.allowed_paths == ("hundredways/example.py",)
    assert proposal.needs_human_review is True


@pytest.mark.parametrize(
    "patch,paths",
    [
        ("--- a/nastech-agent/x.py\n+++ b/nastech-agent/x.py\n", ["nastech-agent/x.py"]),
        (
            "--- a/hundredways/security.py\n+++ b/hundredways/security.py\n",
            ["hundredways/security.py"],
        ),
        ("--- a/hundredways/x.py\n+++ b/hundredways/x.py\n+force-pass\n", ["hundredways/x.py"]),
    ],
)
def test_forbidden_repairs_are_rejected(patch, paths):
    with pytest.raises(RepairRejected):
        validate_proposal(_payload(patch, paths))


def test_repo_identity_is_required(tmp_path):
    root = tmp_path / "not-100ways"
    (root / "hundredways").mkdir(parents=True)
    (root / "hundredways/__init__.py").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text('name = "other-project"\n', encoding="utf-8")
    with pytest.raises(RepairRejected):
        assert_100ways_repo(root)
