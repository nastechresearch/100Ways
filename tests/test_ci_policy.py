import re
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
    # The candidate suite must run under the fork's own pm test environment -
    # the same harness upstream CI runs its suite under - and never through a
    # checkout-local re-exec: a locked, pinned toolchain via the tree's
    # stdlib-only setup script, a locked test environment with a fixed extras
    # list, and NASTECH_PYTHON so run_tests.sh honors the pm interpreter
    # directly.
    assert "scripts/ci/setup_toolchain.py" in workflow
    assert "--test-environment" in workflow
    assert "NASTECH_PYTHON" in workflow
    assert "--extra hindsight" not in workflow
    assert "--extra dev" not in workflow
    assert "RG_SHA256=1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599" in workflow


def test_weekly_gate_uses_immutable_update_source_sha():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-pipeline.yml").read_text()

    assert "HUNDREDWAYS_UPSTREAM_SHA: ${{ steps.update.outputs.upstream_sha }}" in workflow
    assert 'ref=os.environ["HUNDREDWAYS_UPSTREAM_SHA"] or "origin/main"' in workflow


def test_stage_forkcheck_mirrors_the_candidate_tests_yml_recipe():
    """The forkcheck slice and smoke jobs must provision the candidate's pm
    test-environment exactly as stage-pipeline.yml does: the tree's own
    stdlib-only scripts/ci/setup_toolchain.py locked install into a sibling
    forkcheck-pm-home, then a locked `dependencies --test-environment` build
    with the full extras list, and the suite is driven through NASTECH_PYTHON.

    The candidate pyproject declares `[tool.uv] default-groups = []` (dev is a
    PEP 735 dependency-group, not an extra), so the pm test-environment must
    carry the dev+test groups - a plain sync leaves pytest uninstalled and
    slice 1 fails with ``pytest: command not found``. The extras list (bedrock
    included) is upstream's exact set; omitting it breaks the
    parallel-web/fal/bedrock import probes (`pm.extras.available`). The fork's
    old recipe (`--extra dev`, `--extra hindsight`, python 3.11) is a
    pre-guard-era mirror and must not come back. A checkout-local `uv sync`
    into `.venv` is forbidden: without NASTECH_PYTHON, run_tests.sh re-execs
    pm machinery into the guarded home and trips tests/home_io_guard.py.
    """
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-forkcheck.yml").read_text()

    # The provisioning recipe, exactly as the pipeline mirrors it (a stale
    # uv.lock fails here on the frozen pm sync, not on the PR).
    assert "scripts/ci/setup_toolchain.py\" install" in workflow
    assert "--toolchain python --home \"$RUNNER_TEMP/forkcheck-pm-home\"" in workflow

    steps = re.split(r"\n(?=      - name:)", workflow)
    dependency_steps = [
        step for step in steps
        if "setup_toolchain.py\" dependencies" in step
    ]
    assert dependency_steps, "stage-forkcheck.yml must provision the pm test environment"
    for step in dependency_steps:
        assert "--toolchain python" in step
        assert "'[\"all\", \"anthropic\", \"bedrock\", \"mistral\", \"fal\", \"modal\", \"daytona\", \"parallel-web\"]'" in step
        assert "--home \"$RUNNER_TEMP/forkcheck-pm-home\"" in step
        assert "--test-environment" in step

    # No checkout-local uv sync: the env comes from the pm test-environment,
    # and no legacy extras/group flags or pre-guard-era python pin sneak back.
    assert "uv sync --locked" not in workflow
    assert "source .venv/bin/activate" not in workflow
    assert "--extra dev" not in workflow
    assert "--extra hindsight" not in workflow


def test_stage_forkcheck_isolates_slice_home_before_tests():
    """The slice gate and test steps must run through NASTECH_PYTHON against a
    fresh, empty HOME.

    run_tests.sh honors NASTECH_PYTHON directly and never re-execs, so no pm
    or machine state is ever built into $HOME/.nastech. The suite's home-io
    guard (tests/home_io_guard.py) refuses any test file I/O that lands in the
    REAL nastech home - with HOME pointed at the empty guarded root and
    NASTECH_PYTHON driving the interpreter, the guard stays inert, exactly as
    the pipeline's candidate step does.
    """
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "stage-forkcheck.yml").read_text()

    assert 'export HOME="$RUNNER_TEMP/forkcheck-home"' in workflow
    assert "NASTECH_PYTHON" in workflow
    # Both the prompt-resume gate and the slice run step must isolate.
    assert workflow.count('mkdir -p "$RUNNER_TEMP/forkcheck-home"') >= 2
    # Everything that drives the pm interpreter is re-exec-safe by contract:
    # run_tests.sh consumes NASTECH_PYTHON, never the checkout .venv.
    assert "run_tests.sh" in workflow
