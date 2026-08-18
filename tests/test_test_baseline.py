from types import SimpleNamespace

from hundredways import test_baseline, test_baseline_runner
from hundredways.test_baseline import classify_test_logs, failed_nodeids, resolve_source_nodeids


def test_failed_nodeids_extracts_pytest_summary_lines_only():
    log = """
FAILED tests/nastech_cli/test_doctor.py::test_one - assertion detail
PASSED tests/nastech_cli/test_doctor.py::test_two
FAILED other/path.py::test_three
"""

    assert failed_nodeids(log) == (
        "tests/nastech_cli/test_doctor.py::test_one",
    )


def test_collection_resolves_only_exact_forward_branded_source_nodeids(tmp_path, monkeypatch):
    source = tmp_path / "source"
    test_file = source / "tests" / "hermes_cli" / "test_doctor.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_doctor_reports_vercel_backend_diagnostics(): pass\n")

    monkeypatch.setattr(
        test_baseline.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics\n"
                "1 test collected\n"
            ),
        ),
    )

    resolved, unresolved = resolve_source_nodeids(
        (
            "tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics",
            "tests/nastech_cli/test_doctor.py::test_not_collected",
        ),
        source_root=source,
        python="python",
    )

    assert resolved == (
        (
            "tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics",
            "tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics",
        ),
    )
    assert unresolved == ("tests/nastech_cli/test_doctor.py::test_not_collected",)


def test_source_failure_maps_to_branded_candidate_as_inherited():
    candidate_log = """
FAILED tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics
"""
    source_log = """
FAILED tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics
"""

    report = classify_test_logs(
        candidate_log,
        source_log,
        candidate_exit_code=1,
        source_exit_code=1,
    )

    assert report.classification == "inherited-upstream-failures"
    assert report.review_ready is True
    assert report.inherited_failures == (
        "tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics",
    )
    assert report.nastech_only_failures == ()


def test_candidate_only_failure_is_blocking_even_when_source_has_other_failures():
    candidate_log = """
FAILED tests/nastech_cli/test_config_read_guard.py::test_no_raw_config_yaml_reads_outside_owner_modules
FAILED tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics
"""
    source_log = """
FAILED tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics
"""

    report = classify_test_logs(
        candidate_log,
        source_log,
        candidate_exit_code=1,
        source_exit_code=1,
    )

    assert report.classification == "Nastech-only-regression"
    assert report.review_ready is False
    assert report.inherited_failures == (
        "tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics",
    )
    assert report.nastech_only_failures == (
        "tests/nastech_cli/test_config_read_guard.py::test_no_raw_config_yaml_reads_outside_owner_modules",
    )


def test_unparseable_candidate_test_failure_is_blocking():
    report = classify_test_logs(
        "runner crashed before pytest summary\n",
        "",
        candidate_exit_code=2,
        source_exit_code=0,
    )

    assert report.classification == "Nastech-only-regression"
    assert report.review_ready is False
    assert report.unclassified_candidate_failure is True


def test_serial_runner_allows_confirmed_inherited_failure_and_binds_artifact_digest(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate-test-copy"
    source = tmp_path / "source"
    artifact = tmp_path / "candidate-artifact"
    output = tmp_path / "evidence.json"
    raw_log = tmp_path / "raw.log"
    confirmed_log = tmp_path / "confirmed.log"
    source_log = tmp_path / "source.log"
    for root in (candidate, source):
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / ".venv" / "bin" / "python").write_text("")
    artifact.mkdir()
    artifact_file = artifact / "manifest.txt"
    artifact_file.write_text("immutable candidate snapshot")

    candidate_nodeid = "tests/nastech_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics"
    source_nodeid = "tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics"

    def fake_run(command, *, cwd, log, env):
        if command == ("./scripts/run_tests.sh",):
            log.write_text(f"FAILED {candidate_nodeid}\n")
            return 1
        if cwd == candidate:
            log.write_text(f"FAILED {candidate_nodeid}\n")
            return 1
        log.write_text(f"FAILED {source_nodeid}\n")
        return 1

    monkeypatch.setattr(test_baseline_runner, "_run", fake_run)
    monkeypatch.setattr(
        test_baseline_runner,
        "resolve_source_nodeids",
        lambda *args, **kwargs: (((candidate_nodeid, source_nodeid),), ()),
    )

    evidence = test_baseline_runner.run_baseline_aware_tests(
        candidate_root=candidate,
        source_root=source,
        candidate_artifact=artifact,
        output=output,
        candidate_raw_log=raw_log,
        candidate_confirmed_log=confirmed_log,
        source_log=source_log,
        source_sha="abc123",
    )

    assert evidence["review_ready"] is True
    assert evidence["classification"] == "inherited-upstream-failures"
    assert evidence["candidate_tree_sha256"] == test_baseline_runner.tree_digest(artifact)
    assert evidence["candidate_tree_sha256"] != test_baseline_runner.tree_digest(candidate)


def test_clean_candidate_is_review_ready_regardless_of_source_diagnostics():
    report = classify_test_logs(
        "2 passed\n",
        "FAILED tests/hermes_cli/test_doctor.py::test_doctor_reports_vercel_backend_diagnostics\n",
        candidate_exit_code=0,
        source_exit_code=1,
    )

    assert report.classification == "clean"
    assert report.review_ready is True
