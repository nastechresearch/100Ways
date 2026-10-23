from pathlib import Path

import pytest

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


def test_ci_policy_allows_the_telegram_window_to_publish(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "stage-telegram-window.yml").write_text(
        "on:\n  workflow_dispatch:\n    inputs:\n      hop:\n        type: string\n"
        "permissions:\n  contents: write\n  pull-requests: write\n  actions: write\n"
        "jobs:\n  window:\n    steps:\n"
        "      - run: |\n"
        "          git push origin telegram-edit-1\n"
        "          gh pr create --base main --title from-chat\n"
    )

    assert audit_workflow_security(str(tmp_path)) == []


def test_ci_policy_blocks_a_publication_path_started_by_a_timer(tmp_path):
    # Listing a path is not a free pass: a window nobody asked for is a PR nobody
    # reviewed, so the chat path stays dispatch-only.
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "stage-telegram-window.yml").write_text(
        "on:\n  schedule:\n    - cron: '*/15 * * * *'\n"
        "jobs:\n  window:\n    steps:\n      - run: gh pr create --base main\n"
    )

    issues = audit_workflow_security(str(tmp_path))

    assert {issue.code for issue in issues} == {"publication-trigger-not-allowed"}
    assert "schedule" in issues[0].detail


def test_ci_policy_blocks_a_publication_path_that_merges(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "stage-update-pr.yml").write_text(
        "on:\n  workflow_call:\njobs:\n  p:\n    steps:\n"
        "      - run: gh pr merge 12 --admin\n"
    )
    (workflows / "stage-telegram-window.yml").write_text(
        "on:\n  workflow_dispatch:\njobs:\n  p:\n    steps:\n"
        "      - run: gh pr merge 13\n"
    )

    codes = {issue.code for issue in audit_workflow_security(str(tmp_path))}

    assert codes == {"publication-path-may-not-merge"}


def test_ci_policy_reads_the_trigger_block_not_its_prose(tmp_path):
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "stage-telegram-window.yml").write_text(
        "on:\n  workflow_dispatch:\n"
        "# someone suggested a schedule: cadence and a push: trigger here\n"
        "jobs:\n  p:\n    steps:\n      - run: gh pr create --base main\n"
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


def test_ci_policy_flags_unpinned_external_source(tmp_path):
    """A bare third-party clone is an unpinned, mutable supply-chain input."""
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "unpinned.yml").write_text(
        "jobs:\n  monitor:\n    steps:\n"
        "      - run: |\n"
        "          git clone https://github.com/NousResearch/hermes-agent.git upstream-agent\n"
    )

    codes = {issue.code for issue in audit_workflow_security(str(tmp_path))}

    assert codes == {"unpinned-source"}


def test_ci_policy_accepts_named_ref_fork_clone_and_detached_checkout(tmp_path):
    """Named refs, the fork's own repo, and a detach pin are all acceptable."""
    workflows = Path(tmp_path) / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "named-ref.yml").write_text(
        "jobs:\n  monitor:\n    steps:\n"
        "      - run: |\n"
        "          git clone --branch main https://github.com/NousResearch/hermes-agent.git upstream\n"
        "          git clone --depth=1 https://github.com/nastechresearch/nastech-agent.git fork\n"
    )
    (workflows / "detached.yml").write_text(
        "jobs:\n  catalog:\n    steps:\n"
        "      - run: |\n"
        "          git clone \"$REPO\" \"$CLONE_DIR\" </dev/null\n"
        "          git -C \"$CLONE_DIR\" checkout --detach \"$SHA\" </dev/null\n"
    )
    (workflows / "continued.yml").write_text(
        "jobs:\n  rules:\n    steps:\n"
        "      - run: |\n"
        "          git clone --depth=35 --no-checkout \\\n"
        "            https://github.com/nastechresearch/nastech-agent.git fork\n"
    )

    assert audit_workflow_security(str(tmp_path)) == []


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


def _pipeline_step(name: str) -> tuple[dict[str, str], str]:
    """One stage-pipeline step's ``run`` body, read from the workflow that ships it."""
    yaml = pytest.importorskip("yaml", reason="pyyaml is a dev extra (base installs have none)")
    root = Path(__file__).resolve().parents[1]
    doc = yaml.safe_load((root / ".github" / "workflows" / "stage-pipeline.yml").read_text())
    step = next(s for s in doc["jobs"]["pipeline"]["steps"] if s.get("name") == name)
    return step, step["run"]


def test_stage_pipeline_audits_the_owned_asset_policy_on_the_shipped_tree():
    """The policy is measured on the exact candidate that gets packaged, mid-stage."""
    step, run = _pipeline_step("Audit the candidate against the owned-asset policy")
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()
    conformance = workflow.index("Verify final branded candidate against exact Hermes source")
    audit = workflow.index("Audit the candidate against the owned-asset policy")

    assert step["env"]["HUNDREDWAYS_UPDATES_DIR"] == "${{ runner.temp }}/Updates-Commits"
    assert step["env"]["HUNDREDWAYS_ASSET_AUDIT"].endswith("asset-policy.json")
    assert '--tree "$SNAPSHOT"' in run
    assert '--upstream "$HUNDREDWAYS_UPDATES_DIR/hermes-agent"' in run
    # both trees, or the audit says so rather than reporting an empty finding list
    assert "--json-out" in run
    assert 'if [ "$status" -gt 1 ]' in run
    assert conformance < audit
    assert "${{ runner.temp }}/asset-policy.json" in workflow


def test_the_asset_audit_blocks_only_on_a_class_the_registry_lost():
    """art-unclaimed is advisory: the fork ships 15 unowned files today.

    Failing the stage on those would halt every sync over a brand decision the
    engine cannot make, while class-unowned means a rename already swallowed the
    whole registry, so only that one is allowed to stop the pipeline.
    """
    _, run = _pipeline_step("Audit the candidate against the owned-asset policy")
    guard = run[run.index("if hard:") :]

    assert 'hard = groups.get("class-unowned", [])' in run
    assert "raise SystemExit" in guard
    assert "art-unclaimed (advisory)" in run
    for line in run[run.index("art = groups"): run.index("if hard:")].splitlines():
        assert "raise" not in line
