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


def test_stage_pipeline_requires_final_conformance_and_candidate_tests_before_receipt():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    source_evidence = workflow.index("fresh direct-source evidence")
    first_conformance = workflow.index("Verify final branded candidate against exact Hermes source")
    candidate_tests = workflow.index("Run baseline-aware final branded candidate tests")
    post_test_conformance = workflow.index("Re-attest candidate after tests")
    final_gate = workflow.index("Write final pipeline gate")
    receipt = workflow.index("Write tamper-evident gate decision receipt")

    assert workflow.count("python3 -m hundredways.conformance") == 2
    assert (
        source_evidence
        < first_conformance
        < candidate_tests
        < post_test_conformance
        < final_gate
        < receipt
    )
    assert "Canonical upstream test execution: **NOT RUN**" in workflow
    assert "direct upstream source evidence is incomplete" in workflow
    assert "candidate_ready=false" in workflow
    assert "gate=WITHHELD" in workflow
    assert "UPSTREAM_TEST_FAILURE" not in workflow
    assert "Candidate withheld" in workflow
    assert "Publication allowed: **NO**" in workflow
    assert workflow.count("steps.candidate-state.outputs.candidate_ready == 'true'") >= 5
    assert "hundredways.test_baseline_runner" in workflow
    assert "HUNDREDWAYS_UPSTREAM_TESTS" in workflow
    assert "hundredways.test_baseline" in workflow
    assert "evidence['nastech_only_failures']" in workflow
    assert "steps.candidate-tests.outputs.review_ready == 'true'" in workflow
    assert "steps.final-gate.outputs.gate == 'PASS'" in workflow
    assert "CANDIDATE_VALIDATION_FAILURE" in workflow
    assert 'gh.write(f"review_ready={\'true\' if review_ready else \'false\'}\\n")' in workflow
    assert 'gh.write(f"review_ready={\'true\' if review_ready else \'false\'}\\\\n")' not in workflow
    assert 'cp -a "$SNAPSHOT" "$TEST_TREE"' in workflow
    assert "uv sync --locked --python 3.11" in workflow
    assert "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39" in workflow
    assert "RG_SHA256=1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599" in workflow


def test_ci_withheld_pipeline_gate_skips_candidate_jobs_and_publication():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()

    assert "if: ${{ needs.pipeline.outputs.gate == 'PASS' }}" in workflow
    assert "needs.pipeline.outputs.gate == 'PASS'" in workflow
    assert "github.event_name == 'schedule'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "complete candidate pipeline reaches PASS" in workflow
    assert "WITHHELD source-test" not in workflow
