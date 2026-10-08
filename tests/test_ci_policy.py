from pathlib import Path

from hundredways.ci_policy import audit_workflow_security


def test_ci_policy_accepts_sha_pinned_read_only_workflow(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "safe.yml").write_text(
        "permissions:\n  contents: read\n"
        "jobs:\n  verify:\n    steps:\n"
        "      - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6\n"
    )

    assert audit_workflow_security(str(tmp_path)) == []


def test_ci_policy_flags_unpinned_action_and_unsafe_trigger(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "unsafe.yml").write_text(
        "on:\n  pull_request_target:\n"
        "permissions: write-all\n"
        "jobs:\n  verify:\n    steps:\n"
        "      - uses: actions/checkout@v6\n"
    )

    codes = {issue.code for issue in audit_workflow_security(str(tmp_path))}

    assert codes == {"unsafe-trigger", "broad-token", "unpinned-action"}


def test_ci_policy_enforces_pr_only_publication_path(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "legacy.yml").write_text(
        "jobs:\n  publish:\n    steps:\n"
        "      - run: |\n"
        "          gh release create v1 artifact.zip\n"
        "          git tag v1\n"
        "          kubectl apply -f deploy.yml\n"
        "          gh issue create --title noisy\n"
        "          gh workflow run ci.yml\n"
        "          gh pr review 21 --approve\n"
        "          gh pr create --title duplicate\n"
    )

    codes = {issue.code for issue in audit_workflow_security(str(tmp_path))}

    assert codes == {
        "auto-release",
        "auto-tag",
        "auto-deploy",
        "issue-notification",
        "autonomous-dispatch",
        "self-approval",
        "unauthorized-publication-path",
    }


def test_ci_policy_allows_publication_only_in_344_workflow(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "stage-update-pr.yml").write_text(
        "jobs:\n  publish:\n    steps:\n"
        "      - run: |\n"
        "          git push origin candidate\n"
        "          gh pr create --title candidate\n"
    )

    assert audit_workflow_security(str(tmp_path)) == []


def test_repository_workflows_have_no_blocking_publication_policy_violations():
    root = Path(__file__).resolve().parents[1]

    blocking = [
        issue
        for issue in audit_workflow_security(str(root))
        if issue.severity == "block"
    ]

    assert blocking == []


def test_ci_policy_snapshot_mode_does_not_apply_engine_publication_denials(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "inherited.yml").write_text(
        "jobs:\n  legacy:\n    steps:\n"
        "      - run: |\n"
        "          gh release create v1 artifact.zip\n"
        "          gh pr create --title source-maintenance\n"
    )

    assert audit_workflow_security(str(tmp_path), enforce_publication_policy=False) == []


def test_stage_pipeline_checks_candidate_conformance_without_running_nastech_tests():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    conformance = workflow.index("Verify final branded candidate against exact Hermes source")
    receipt = workflow.index("Write tamper-evident gate decision receipt")

    assert conformance < receipt
    assert workflow.count("python3 -m hundredways.conformance") == 1
    assert "scripts/run_tests.sh" not in workflow
    assert "scripts/ci/setup_toolchain.py" not in workflow
    assert "Run final branded candidate test suite" not in workflow
    assert "candidate-tests.json" not in workflow


def test_ci_runs_100ways_checks_without_nastech_application_test_workflow():
    root = Path(__file__).resolve().parents[1]
    ci = (root / ".github" / "workflows" / "ci.yml").read_text()
    pipeline = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    assert "stage-forkcheck.yml" not in ci
    assert "  forkcheck:" not in ci
    assert "  readme:\n    needs: pipeline" in ci
    assert "needs: [pipeline, all-checks-pass]" in ci
    assert "Verify rebrand parity vs birth commit" in (root / ".github" / "workflows" / "stage-rules.yml").read_text()
    assert "compare" in pipeline.lower() or "conformance" in pipeline.lower()
    assert not (root / ".github" / "workflows" / "stage-forkcheck.yml").exists()


def test_weekly_gate_uses_immutable_update_source_sha():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    assert "HUNDREDWAYS_UPSTREAM_SHA: ${{ steps.update.outputs.upstream_sha }}" in workflow
    assert 'ref=os.environ["HUNDREDWAYS_UPSTREAM_SHA"] or "origin/main"' in workflow
