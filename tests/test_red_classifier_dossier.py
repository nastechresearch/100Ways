"""The classifier measured against the dossier it was written from.

``fork-ci-triage/references/classes.md`` records the fork's known reds with their measured
numbers -- a 0.78 s stall against ``HOLD_S / 3``, a 29956 -> 30022 character chain against a
30000 cap, an upstream file delta.  This file feeds those recorded facts back through the
classifier and asserts it reaches the class the dossier reached by hand.  A module that cannot
reproduce the cases it was derived from is not a classifier, so every case here quotes the
dossier's own measurement rather than an invented one.

Where the dossier calls a red runner capacity but its recorded failure text carries no bound to
measure (a connection count, a child process that reports ``running``), the honest answer is
``UNKNOWN`` and this file says so: the class the dossier gives from a human's whole context is
not a class this module may claim from an absent measurement.

Synthetic fixtures only: no credential, no real repository, no token beyond the canonical brand
spellings the transform itself knows.
"""

from __future__ import annotations

import re

import pytest

from hundredways import red_classifier as rc
from hundredways.red_evidence import SIZE_CHECKER

BASELINE = "300a39771f2c4d6e8a0b1c2d3e4f5a6b7c8d9e0f"
CURRENT = "601491fe1a2b3c4d5e6f708192a3b4c5d6e7f809"

# The dossier's chain, measured: upstream 11794 + 18162 = 29956 against a 30000 cap, the brand
# adding one character per renamed token, 66 of them inside this chain.
UPSTREAM_ROOT = 11794
UPSTREAM_GATEWAY = 18162
ROOT_TOKENS = 26
GATEWAY_TOKENS = 40

OFF_LOOP = "tests (3.14, ubuntu-latest)"
LINT = "Python lints / Windows footguns (blocking)"
MACOS = "tests (3.14, macos-latest)"
WINDOWS = "tests (3.12, windows-latest)"


def _fork(name: str, log: str, image: str, *, retried: bool = False) -> dict:
    text = log + ("FLAKY: retried attempt 2\n" if retried else "")
    return {
        "id": 501,
        "name": name,
        "conclusion": "failure",
        "runner_name": image,
        "output": {"title": "Process completed with exit code 1.", "text": text},
    }


def _upstream(name: str, image: str, log: str = "") -> dict:
    return {
        "id": 502,
        "name": name,
        "conclusion": "success",
        "labels": [image],
        "output": {"title": "", "text": log or f"runner@{image}"},
    }


def _payload(*runs: dict) -> dict:
    return {"total_count": len(runs), "check_runs": list(runs)}


def _facts(
    run: dict,
    *,
    upstream_image: str,
    delta: tuple[str, ...] | None = (),
    upstream_log: str = "",
    checkout: str = "",
) -> rc.SyncFacts:
    return rc.SyncFacts.from_check_runs(
        _payload(run),
        lane=str(run["name"]),
        upstream_runs=_payload(_upstream(str(run["name"]), upstream_image, upstream_log)),
        upstream_changed_files=list(delta) if delta is not None else None,
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=checkout,
    )


def _chain_tree(tmp_path) -> str:
    checker = tmp_path / SIZE_CHECKER
    checker.parent.mkdir(parents=True, exist_ok=True)
    checker.write_text("CHAIN_MAX_CHARS = 30_000\nROOT_MAX_CHARS = 12_000\n", encoding="utf-8")
    hints = tmp_path / "agent/subdirectory_hints.py"
    hints.parent.mkdir(parents=True, exist_ok=True)
    hints.write_text("_MAX_HINT_CHARS = 32_000\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        "nastech " * ROOT_TOKENS + "x" * (UPSTREAM_ROOT - 7 * ROOT_TOKENS), encoding="utf-8"
    )
    gateway = tmp_path / "gateway"
    gateway.mkdir(exist_ok=True)
    (gateway / "AGENTS.md").write_text(
        "nastech " * GATEWAY_TOKENS + "y" * (UPSTREAM_GATEWAY - 7 * GATEWAY_TOKENS),
        encoding="utf-8",
    )
    return str(tmp_path)


# ---------------------------------------------------------------------------
# Class 1, as the dossier recorded it
# ---------------------------------------------------------------------------

# (lane, upstream image the fork cannot rent, failure text with its recorded numbers)
CAPACITY_REDS = (
    pytest.param(
        OFF_LOOP,
        "blacksmith-32vcpu-ubuntu-2404",
        "ubuntu-latest",
        "tests/gateway/test_api_admission_off_loop.py:57: in test_admission_off_loop\n"
        "    HOLD_S = 1.5\n"
        ">       assert max(gaps) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 0.78 < 0.5\n"
        "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[chat]\n",
        id="admission-off-loop-0.78-against-0.5",
    ),
    pytest.param(
        OFF_LOOP,
        "blacksmith-32vcpu-ubuntu-2404",
        "ubuntu-latest",
        "tests/gateway/test_mutation_prepare_off_loop.py:44: in test_mutation_prepare\n"
        "    PREPARE_HOLD_S = 1.5\n"
        ">       assert max(gaps) < (PREPARE_HOLD_S / 3)\n"
        "E       AssertionError: assert 0.69 < 0.5\n"
        "FAILED tests/gateway/test_mutation_prepare_off_loop.py::test_mutation_prepare[chat]\n",
        id="mutation-prepare-0.69-stall",
    ),
    pytest.param(
        OFF_LOOP,
        "blacksmith-32vcpu-ubuntu-2404",
        "ubuntu-latest",
        "tests/tui_gateway/test_protocol.py:880: in test_approval_dialog_survives_a_reconnect\n"
        ">       assert waited < 1\n"
        "E       AssertionError: assert 1.4 < 1\n"
        "FAILED tests/tui_gateway/test_protocol.py::test_approval_dialog_survives_a_reconnect\n",
        id="tui-approval-waited-against-1s",
    ),
    pytest.param(
        MACOS,
        "blacksmith-12vcpu-macos-latest",
        "macos-latest",
        "tests/nastech_cli/test_browse_schema_drift.py:236: in test_reconcile\n"
        "    BROWSE_RECONCILE_BUDGET_S = 3.0\n"
        "E   sqlite3.OperationalError: interrupted\n"
        "FAILED tests/nastech_cli/test_browse_schema_drift.py::test_reconcile[ddl]\n",
        id="browse-budget-spent-on-3-cores",
    ),
    pytest.param(
        OFF_LOOP,
        "blacksmith-32vcpu-ubuntu-2404",
        "ubuntu-latest",
        "tests/nastech_cli/test_plugin_secondary_profile_host.py:112: in test_hung_hosted\n"
        ">       HOSTED_REQUEST_TIMEOUT_SECS = 1.0\n"
        "E       AssertionError: assert (504, 200) == (200, 200)\n"
        "FAILED tests/nastech_cli/test_plugin_secondary_profile_host.py::"
        "test_hung_hosted_request_answers_504_when_the_host_is_cold\n",
        id="hosted-request-timeout-shared-by-warmup",
    ),
)


@pytest.mark.parametrize("lane,upstream_image,fork_image,log", CAPACITY_REDS)
def test_the_dossier_s_capacity_reds_classify_as_capacity(lane, upstream_image, fork_image, log):
    """Every recorded tell of class 1 reaches class 1 from the recorded evidence alone."""
    run = _fork(lane, log, fork_image)
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run),
        _facts(run, upstream_image=upstream_image, delta=("gateway/router.py",)),
    )
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY
    assert verdict.owner == "nobody — hardware"
    joined = " ".join(verdict.evidence)
    assert fork_image in joined
    assert upstream_image in joined


def test_the_recorded_stall_and_bound_are_quoted_without_being_copied():
    """0.5 s is derived from HOLD_S / 3 in the log, and 0.78 s is the rewrite of that line."""
    log = (
        "tests/gateway/test_api_admission_off_loop.py:57: in test_admission_off_loop\n"
        "    HOLD_S = 1.5\n"
        ">       assert max(gaps) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 0.78 < 0.5\n"
        "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[chat]\n"
    )
    run = _fork(OFF_LOOP, log, "ubuntu-latest")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run),
        _facts(run, upstream_image="blacksmith-32vcpu-ubuntu-2404"),
    )
    phrase = verdict.evidence[0]
    assert "bound HOLD_S / 3" in phrase
    assert "0.5 s" in phrase
    assert "0.78 s" in phrase
    # The retry the dossier recorded for this file is evidence, not a class of its own.
    retried_run = _fork(OFF_LOOP, log, "ubuntu-latest", retried=True)
    retried = rc.classify(
        rc.FailedCheck.from_check_run(retried_run),
        _facts(retried_run, upstream_image="blacksmith-32vcpu-ubuntu-2404"),
    )
    assert retried.kind is rc.RedClass.RUNNER_CAPACITY
    assert "retried" in retried.evidence[-1]


def test_the_lane_that_blew_its_wall_clock_cap_is_still_reported_as_a_red():
    """A timed-out job is the capacity outcome the dossier's headroom warning predicts.

    Reporting it as "nothing to classify" is the one answer a triage tool must never give.
    """
    run = _fork(OFF_LOOP, "##[error]The action has timed out.\n", "ubuntu-latest")
    timed_out = dict(run, conclusion="timed_out")
    cancelled = dict(run, conclusion="cancelled")
    assert [check.name for check in rc.failing_checks(_payload(timed_out, cancelled))] == [
        OFF_LOOP,
        OFF_LOOP,
    ]
    verdicts = rc.classify_runs(
        _payload(timed_out),
        upstream_runs=_payload(_upstream(OFF_LOOP, "blacksmith-32vcpu-ubuntu-2404")),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    assert len(verdicts) == 1
    assert verdicts[0].kind is rc.RedClass.UNKNOWN
    rendered = rc.render(verdicts)
    assert "no red checks to classify" not in rendered
    assert "UNKNOWN" in rendered


# ---------------------------------------------------------------------------
# Class 1 pressure reds the dossier refused to chase: honest UNKNOWN
# ---------------------------------------------------------------------------

PRESSURE_REDS = (
    pytest.param(
        OFF_LOOP,
        "ubuntu-latest",
        "blacksmith-32vcpu-ubuntu-2404",
        "tests/conftest.py:844: in pytest_runtest_teardown\n"
        "    sessions = mod._sessions\n"
        "ERROR tests/tui_gateway/test_kanban_resume_guard.py::test_resume_guard_keeps_a_row\n",
        id="teardown-error-with-no-bound",
    ),
    pytest.param(
        OFF_LOOP,
        "ubuntu-latest",
        "blacksmith-32vcpu-ubuntu-2404",
        "tests/pm/test_downloader_integrity.py:61: in test_partial_download_retries\n"
        "E   AssertionError: assert 2 == 1\n"
        "FAILED tests/pm/test_downloader_integrity.py::test_partial_download_retries_keep_the_"
        "connection_pool\n",
        id="connection-count-assertion",
    ),
    pytest.param(
        WINDOWS,
        "windows-2025-vs2026",
        "blacksmith-32vcpu-windows-2025",
        "tests/nastech_cli/test_local_runtime_processes.py:301: in test_failed_setup\n"
        "E   AssertionError: assert 'running' == 'stopped'\n"
        "FAILED tests/nastech_cli/test_local_runtime_processes.py::"
        "test_failed_setup_never_runs_child_and_releases_handles[assign|resume]\n",
        id="suspended-child-reporting-running",
    ),
)


@pytest.mark.parametrize("lane,fork_image,upstream_image,log", PRESSURE_REDS)
def test_a_pressure_red_without_a_bound_is_named_not_guessed(lane, fork_image, upstream_image, log):
    """The dossier owns these to the runner; the recorded text measures nothing of the sort."""
    run = _fork(lane, log, fork_image)
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run), _facts(run, upstream_image=upstream_image)
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert not verdict.classified
    joined = " ".join(verdict.evidence)
    assert "no assertion here compares against an absolute wall-clock budget" in joined
    assert "no class is claimed here" in joined
    if fork_image == "ubuntu-latest":
        # Every capacity fact is in hand except the bound, so the reader gets the hardware
        # without being told to blame it.
        assert "consistent with capacity, not called on it" in joined
        assert "a capacity call needs a bound" in joined
    else:
        assert "consistent with capacity" not in joined


# ---------------------------------------------------------------------------
# Class 2, as the dossier recorded it
# ---------------------------------------------------------------------------


def test_the_recorded_chain_breach_classifies_as_brand_inflation(tmp_path):
    """30022 branded against a 30000 cap parsed from the enforcing file, 29956 unbranded."""
    tree = _chain_tree(tmp_path)
    run = _fork(
        LINT,
        "AGENTS.md + gateway/AGENTS.md: 30022 chars > 30000 chain cap\n##[error]",
        "windows-latest",
    )
    # zero AGENTS.md files in the 219-file delta the dossier measured
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run),
        _facts(run, upstream_image="windows-latest", delta=("gateway/router.py",), checkout=tree),
    )
    assert verdict.kind is rc.RedClass.BRAND_INFLATION
    joined = " ".join(verdict.evidence)
    assert "29956 -> 30022" in joined
    assert "66 of those characters are the rename" in joined
    assert "30000" in joined and SIZE_CHECKER in joined


def test_a_newer_sync_buys_no_slack_on_the_recorded_numbers(tmp_path):
    """The dossier's decisive measurement: identical chain size at both upstream tips."""
    tree = _chain_tree(tmp_path)
    run = _fork(
        LINT, "AGENTS.md + gateway/AGENTS.md: 30022 chars > 30000 chain cap", "windows-latest"
    )
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run),
        _facts(run, upstream_image="windows-latest", checkout=tree),
    )
    assert verdict.kind is rc.RedClass.BRAND_INFLATION
    # 22 characters over a cap upstream sits 44 under: a newer sync of the same text is no fix.
    assert "29956 -> 30022 chars against the 30000 chain cap" in verdict.evidence[0]
    assert "a newer sync buys no slack" in " ".join(verdict.evidence)
    assert verdict.action.startswith("Shrink")
    assert "cap" in " ".join(verdict.evidence)


# ---------------------------------------------------------------------------
# Class 3, as the dossier recorded it
# ---------------------------------------------------------------------------


def test_a_file_upstream_actually_changed_classifies_as_divergence():
    """``FIXED UPSTREAM`` outranks the wall clock sitting in the same log."""
    log = (
        "tests/gateway/test_api_admission_off_loop.py:57: in test_admission_off_loop\n"
        "    HOLD_S = 1.5\n"
        ">       assert max(gaps) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 0.78 < 0.5\n"
        "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[chat]\n"
    )
    run = _fork(OFF_LOOP, log, "ubuntu-latest")
    delta = ("tests/gateway/test_api_admission_off_loop.py", "gateway/router.py")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(run),
        _facts(run, upstream_image="blacksmith-32vcpu-ubuntu-2404", delta=delta),
    )
    assert verdict.kind is rc.RedClass.GENUINE_DIVERGENCE
    joined = " ".join(verdict.evidence)
    assert "upstream changed test_api_admission_off_loop.py" in joined
    assert "2 changed file(s)" in joined
    assert "ranks below" in joined


def test_the_dossier_battery_renders_as_three_classes_and_no_invention():
    """One report, every recorded case: the counts are the dossier's, the fixes are not ours."""
    capacity = _fork(OFF_LOOP, CAPACITY_REDS[0].values[3], "ubuntu-latest")
    pressure = _fork(OFF_LOOP, PRESSURE_REDS[1].values[3], "ubuntu-latest")
    divergence = _fork(
        "tests (3.12, windows-latest)",
        "E   assert 2 == 1\nFAILED tests/pm/test_went_upstream.py::test_x\n",
        "windows-latest",
    )
    fork = _payload(capacity, pressure, divergence, _fork(LINT, "", "windows-latest"))
    verdicts = rc.classify_runs(
        fork,
        upstream_runs=_payload(
            _upstream(OFF_LOOP, "blacksmith-32vcpu-ubuntu-2404"),
            _upstream("tests (3.12, windows-latest)", "windows-latest"),
            _upstream(LINT, "windows-latest"),
        ),
        upstream_changed_files=["tests/pm/test_went_upstream.py"],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    kinds = [verdict.kind for verdict in verdicts]
    assert kinds.count(rc.RedClass.RUNNER_CAPACITY) == 1
    assert kinds.count(rc.RedClass.GENUINE_DIVERGENCE) == 1
    assert kinds.count(rc.RedClass.UNKNOWN) == 2
    rendered = rc.render(verdicts)
    for shape in (
        r"raise\s+(?:the\s+|any\s+|a\s+)?(?:cap|limit|budget|threshold|max)",
        r"(?:raise|extend|lengthen|relax|loosen|widen)\b[^.]*\b(?:time)?out",
        r"\b(?:weaken|skip\s+the\s+test|xfail|per-file|file-ignore|ignore-file)\b",
    ):
        assert not re.search(shape, rendered, re.IGNORECASE), shape
    assert "No verdict moves a cap, a budget, or a guard" in rendered


# ---------------------------------------------------------------------------
# Class 4, measured off the fork's own release dispatch (run 39582399772)
# ---------------------------------------------------------------------------

DARWIN_LANE = "bundled darwin-arm64 (darwin-arm64, macos-15)"
WINDOWS_LANE = "bundled win32-x64 (win32-x64, windows-latest)"
RESULT_LANE = "bundled darwin-arm64 result"

# Both verbatim from the job logs, including the line the workflow's own `echo` prints
# before it: a template (`${missing[*]}`) is not a measurement and must not parse.
DARWIN_LOG = (
    "2026-10-21T10:15:31.9610360Z   PYTHONDONTWRITEBYTECODE: 1\n"
    "2026-10-21T10:15:31.9610640Z   CSC_LINK: \n"
    "2026-10-21T10:15:31.9610930Z   CSC_KEY_PASSWORD: \n"
    "2026-10-21T10:15:31.9611230Z   APPLE_API_KEY_P8: \n"
    "2026-10-21T10:15:31.9611460Z   APPLE_API_KEY_ID: \n"
    "2026-10-21T10:15:31.9611760Z   APPLE_API_ISSUER: \n"
    "2026-10-21T10:15:31.9612060Z ##[endgroup]\n"
    "2026-10-21T10:15:32.0460390Z ##[error]required signing/notarization credentials are "
    "missing from release-signing: CSC_LINK CSC_KEY_PASSWORD APPLE_API_KEY_P8 "
    "APPLE_API_KEY_ID APPLE_API_ISSUER\n"
    "2026-10-21T10:15:32.0465950Z ##[error]Process completed with exit code 1.\n"
)
WINDOWS_LOG = (
    "2026-10-21T10:27:41.5372800Z   echo \"::error::required Azure Trusted Signing vars are "
    "missing from release-signing: ${missing[*]}\"\n"
    "2026-10-21T10:27:41.5397656Z   AZURE_SIGN_ENDPOINT: \n"
    "2026-10-21T10:27:41.5397881Z   AZURE_SIGN_ACCOUNT: \n"
    "2026-10-21T10:27:41.5398093Z   AZURE_SIGN_PROFILE: \n"
    "2026-10-21T10:27:41.5937729Z ##[error]required Azure Trusted Signing vars are missing "
    "from release-signing: AZURE_SIGN_ENDPOINT AZURE_SIGN_ACCOUNT AZURE_SIGN_PROFILE\n"
    "2026-10-21T10:27:41.5964056Z ##[error]Process completed with exit code 1.\n"
)
DOWNSTREAM_LOG = (
    "2026-10-21T10:15:42.4773025Z ##[error]selected native build did not succeed, or the "
    "other trust branch was not skipped\n"
    "2026-10-21T10:15:42.4793099Z ##[error]Process completed with exit code 1.\n"
)


def test_the_dossiers_mac_preflight_is_publisher_configuration():
    verdicts = rc.classify_runs(
        _payload(_fork(DARWIN_LANE, DARWIN_LOG, "macos-15")), upstream_changed_files=[]
    )
    assert len(verdicts) == 1
    verdict = verdicts[0]
    assert verdict.kind is rc.RedClass.PUBLISHER_CONFIGURATION
    joined = " ".join(verdict.evidence)
    assert "5 unread credentials" in joined
    assert "release-signing" in joined
    assert verdict.owner.startswith("the repo owner")


def test_the_dossiers_windows_preflight_is_publisher_configuration():
    """The Azure red words itself differently -- `vars`, not `credentials` -- and reads the same."""
    verdicts = rc.classify_runs(
        _payload(_fork(WINDOWS_LANE, WINDOWS_LOG, "windows-latest")), upstream_changed_files=[]
    )
    verdict = verdicts[0]
    assert verdict.kind is rc.RedClass.PUBLISHER_CONFIGURATION
    assert "3 unread vars" in " ".join(verdict.evidence)
    # The `${missing[*]}` source line is a template: it must not be counted as a second claim.
    assert "$" not in " ".join(verdict.evidence)


def test_the_downstream_result_job_still_refuses_a_class():
    """Known limit, recorded: a job that reports on other jobs names no measurement of its own."""
    verdicts = rc.classify_runs(
        _payload(_fork(RESULT_LANE, DOWNSTREAM_LOG, "macos-15")), upstream_changed_files=[]
    )
    assert verdicts[0].kind is rc.RedClass.UNKNOWN


def test_one_release_dispatch_sorts_into_configuration_and_nothing_else():
    verdicts = rc.classify_runs(
        _payload(
            _fork(DARWIN_LANE, DARWIN_LOG, "macos-15"),
            _fork(WINDOWS_LANE, WINDOWS_LOG, "windows-latest"),
            _fork(RESULT_LANE, DOWNSTREAM_LOG, "macos-15"),
        ),
        upstream_changed_files=["desktop-bundled-release.yml"],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    kinds = [verdict.kind for verdict in verdicts]
    assert kinds.count(rc.RedClass.PUBLISHER_CONFIGURATION) == 2
    assert kinds.count(rc.RedClass.GENUINE_DIVERGENCE) == 0
    assert kinds.count(rc.RedClass.UNKNOWN) == 1
    rendered = rc.render(verdicts)
    assert "PUBLISHER_CONFIGURATION 2" in rendered
    for name in ("CSC_LINK", "AZURE_SIGN_ENDPOINT"):
        assert name in rendered
    assert "***" not in rendered
