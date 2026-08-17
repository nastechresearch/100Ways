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

    first_conformance = workflow.index("Verify final branded candidate against exact Hermes source")
    candidate_tests = workflow.index("Run final branded candidate test suite")
    post_test_conformance = workflow.index("Re-attest candidate after tests")
    receipt = workflow.index("Write tamper-evident gate decision receipt")

    assert workflow.count("python3 -m hundredways.conformance") == 2
    assert first_conformance < candidate_tests < post_test_conformance < receipt
    assert "./scripts/run_tests.sh" in workflow
    assert 'cp -a "$SNAPSHOT" "$TEST_TREE"' in workflow
    assert "uv sync --locked --python 3.11" in workflow
    assert "astral-sh/setup-uv@fac544c07dec837d0ccb6301d7b5580bf5edae39" in workflow
    assert "RG_SHA256=1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599" in workflow
    assert "source .venv/bin/activate" in workflow


def test_pull_requests_do_not_run_the_upstream_candidate_pipeline():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text()

    pipeline = workflow[workflow.index("  pipeline:") : workflow.index("  forkcheck:")]
    assert "if: ${{ github.event_name != 'pull_request' }}" in pipeline
    assert "uses: ./.github/workflows/stage-pipeline.yml" in pipeline


def test_weekly_gate_analyzer_runs_after_a_failed_gate():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    analyzer = workflow[workflow.index("      - name: Analyze weekly gate failure") :]
    assert "if: ${{ failure() }}" in analyzer
    assert "--decision \"$HUNDREDWAYS_DECISION\"" in analyzer
