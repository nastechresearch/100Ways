"""Red CI classification, encoded as the measurements the fork-ci dossier recorded.

Every number below is a measured fact about a real candidate (fork 040a8e78 and f7c3af5a,
upstream 300a3977 -> 601491fe), not a convenient value: the 29956 -> 30022 chain, the
0.5 s bound against a 0.78 s stall, the 32 vCPU Blacksmith image the fork's org cannot run.
"""

from __future__ import annotations

import re

import pytest

from hundredways import red_classifier as rc
from hundredways.red_evidence import SIZE_CHECKER

BASELINE = "300a39771f2c4d6e8a0b1c2d3e4f5a6b7c8d9e0f"
CURRENT = "601491fe1a2b3c4d5e6f708192a3b4c5d6e7f809"

# Upstream measures this chain at 29956 characters, 44 under the cap; hermes->nastech adds
# one character per renamed token and 66 of them land in the chain, giving 30022.
UPSTREAM_ROOT = 11794
UPSTREAM_GATEWAY = 18162
ROOT_TOKENS = 26
GATEWAY_TOKENS = 40

TEST_ID = "tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[chat]"
OFF_LOOP_LOG = (
    "tests/gateway/test_api_admission_off_loop.py:57: in test_admission_off_loop\n"
    "    HOLD_S = 1.5\n"
    ">       assert max(gaps) < (HOLD_S / 3)\n"
    "E       AssertionError: assert 0.78 < 0.5\n"
    f"FAILED {TEST_ID}\n"
)


def _fork_run(name: str, text: str, image: str, *, conclusion: str = "failure") -> dict:
    return {
        "id": 501,
        "name": name,
        "conclusion": conclusion,
        "runner_name": image,
        "output": {"title": "Process completed with exit code 1.", "text": text},
    }


def _upstream_run(name: str, image: str, conclusion: str = "success", text: str = "") -> dict:
    return {
        "id": 502,
        "name": name,
        "conclusion": conclusion,
        "labels": [image],
        "output": {"title": "", "text": text or f"runner@{image}"},
    }


def _payload(*runs: dict) -> dict:
    return {"total_count": len(runs), "check_runs": list(runs)}


def _chain_tree(tmp_path, *, root_tokens=ROOT_TOKENS, gateway_tokens=GATEWAY_TOKENS):
    """A fixture tree carrying the dossier's chain, plus the two files that enforce the cap."""
    checker = tmp_path / SIZE_CHECKER
    checker.parent.mkdir(parents=True, exist_ok=True)
    checker.write_text("CHAIN_MAX_CHARS = 30_000\nROOT_MAX_CHARS = 12_000\n", encoding="utf-8")
    hints = tmp_path / "agent/subdirectory_hints.py"
    hints.parent.mkdir(parents=True, exist_ok=True)
    hints.write_text("_MAX_HINT_CHARS = 32_000\n", encoding="utf-8")
    # Each branded token is 8 characters and its upstream spelling 7, so the chain reverses
    # to exactly the 11794 + 18162 = 29956 upstream measures.
    (tmp_path / "AGENTS.md").write_text(
        "nastech " * root_tokens + "x" * (UPSTREAM_ROOT - 7 * root_tokens), encoding="utf-8"
    )
    gateway = tmp_path / "gateway"
    gateway.mkdir(exist_ok=True)
    (gateway / "AGENTS.md").write_text(
        "nastech " * gateway_tokens + "y" * (UPSTREAM_GATEWAY - 7 * gateway_tokens),
        encoding="utf-8",
    )
    return str(tmp_path)


def _lint_run(tmp_path) -> dict:
    return _fork_run(
        "Python lints / Windows footguns (blocking)",
        "AGENTS.md + gateway/AGENTS.md: 30022 chars > 30000 chain cap\n##[error]",
        "windows-latest",
    )


def _off_loop_facts(tmp_path=None, *, delta=("gateway/router.py",), log=OFF_LOOP_LOG):
    return rc.SyncFacts.from_check_runs(
        _payload(_fork_run("tests (3.14, ubuntu-latest)", log, "ubuntu-latest")),
        lane="tests (3.14, ubuntu-latest)",
        upstream_runs=_payload(
            _upstream_run("tests (3.14, ubuntu-latest)", "blacksmith-32vcpu-ubuntu-2404")
        ),
        upstream_changed_files=list(delta),
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=str(tmp_path) if tmp_path else "",
    )


def _brand_facts(tmp_path, *, delta=("gateway/router.py",)):
    return rc.SyncFacts.from_check_runs(
        _payload(_lint_run(tmp_path)),
        lane="Python lints / Windows footguns (blocking)",
        upstream_runs=_payload(
            _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest")
        ),
        upstream_changed_files=list(delta),
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# The dossier's shape, read out of raw check-runs JSON
# ---------------------------------------------------------------------------


def test_from_check_run_reads_the_payload_ci_hands_over():
    check = rc.FailedCheck.from_check_run(_fork_run("tests (3.14)", OFF_LOOP_LOG, "ubuntu-latest"))
    assert check.name == "tests (3.14)"
    assert check.test_ids == (TEST_ID,)
    assert check.files == ("test_api_admission_off_loop.py",)


def test_from_check_runs_builds_images_and_delta_from_raw_json():
    facts = _off_loop_facts()
    assert facts.fork_image == "ubuntu-latest"
    assert facts.upstream_image == "blacksmith-32vcpu-ubuntu-2404"
    assert facts.upstream_succeeded is True
    assert facts.delta_known is True
    assert facts.touched_upstream("test_api_admission_off_loop.py") is False
    assert facts.touched_upstream("router.py") is True
    with pytest.raises(ValueError, match="no check-run named"):
        rc.SyncFacts.from_check_runs(
            _payload(_fork_run("tests (3.14)", OFF_LOOP_LOG, "ubuntu-latest")), lane="nothing"
        )


def test_branded_and_upstream_spellings_of_one_file_compare_equal():
    facts = _off_loop_facts(delta=("tests/hermes_cli/test_browse_schema_drift.py",))
    assert facts.touched_upstream("tests/nastech_cli/test_browse_schema_drift.py") is True


def test_class_one_precondition_is_asked_of_the_brand_transform():
    """The image the classifier blames comes from rules.py, not from a constant here."""
    shape = _off_loop_facts().runner_shape()
    assert shape.requested == "blacksmith-32vcpu-ubuntu-2404"
    assert shape.actual == "ubuntu-latest"
    assert shape.requested_cores == 32
    assert shape.forced_down is True
    from hundredways.rules import BrandingRules

    assert BrandingRules().transform_text("runs-on: blacksmith-32vcpu-ubuntu-2404") == (
        "runs-on: ubuntu-latest"
    )


# ---------------------------------------------------------------------------
# Class 1: runner capacity
# ---------------------------------------------------------------------------


def test_off_loop_shape_on_a_downsized_image_is_runner_capacity():
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "")),
        _off_loop_facts(),
    )
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY
    assert verdict.owner == "nobody — hardware"
    joined = " ".join(verdict.evidence)
    assert "blacksmith-32vcpu-ubuntu-2404" in joined
    assert "ubuntu-latest" in joined
    assert "32 vCPU" in joined


def test_capacity_evidence_names_the_measured_bound_and_observed_stall():
    verdict = rc.classify(rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")), _off_loop_facts())
    joined = " ".join(verdict.evidence)
    assert "HOLD_S / 3" in joined
    assert "0.5 s" in joined
    assert "0.78 s" in joined
    assert "test_api_admission_off_loop.py" in joined


def test_capacity_is_called_from_the_assertion_shape_not_a_test_name():
    """An invented test id with the same shape is still a capacity red."""
    log = (
        "tests/gateway/test_brand_new.py:12: in test_whatever\n"
        ">       assert waited < 1\n"
        "E       AssertionError: assert 1.4 < 1\n"
        "FAILED tests/gateway/test_brand_new.py::test_whatever\n"
    )
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", log, "ubuntu-latest")
    )
    facts = _off_loop_facts()
    assert rc.classify(check, facts).kind is rc.RedClass.RUNNER_CAPACITY
    # The same assertion against another measurement is not a wall clock claim at all.
    relative = log.replace("assert waited < 1", "assert waited < baseline_waited")
    check2 = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", relative, "ubuntu-latest")
    )
    assert rc.classify(check2, facts).kind is rc.RedClass.UNKNOWN


def test_browse_budget_and_create_suspended_shapes_are_judged_on_their_own_evidence():
    """An armed budget counts as a wall clock; a state assertion without one does not."""
    armed = rc.FailedCheck.from_check_run(
        _fork_run(
            "tests (3.14, ubuntu-latest)",
            "BROWSE_RECONCILE_BUDGET_S = 3.0\nsqlite3.OperationalError: interrupted\n",
            "ubuntu-latest",
        )
    )
    assert rc.classify(armed, _off_loop_facts()).kind is rc.RedClass.RUNNER_CAPACITY
    state = rc.FailedCheck.from_check_run(
        _fork_run(
            "tests (3.12, windows-latest)",
            "    assert child.status == 'running'\n"
            "E   AssertionError: assert 'running' == 'stopped'\n",
            "windows-latest",
        )
    )
    verdict = rc.classify(state, _off_loop_facts())
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert not verdict.classified


def test_a_state_red_with_a_test_id_reports_hardware_without_claiming_capacity():
    """The dossier's pressure shapes: every capacity fact in hand, no bound in the text.

    ``assert 2 == 1`` on the downsized ubuntu lane and the ``CREATE_SUSPENDED`` child on the
    Windows slice lane are the two reds the dossier refused to chase individually.  The first
    gets its hardware facts quoted without a class; the second gets nothing, because there the
    brand never rewrote the label and no capacity story is even available.
    """
    pressure = (
        "E   AssertionError: assert 2 == 1\n"
        "FAILED tests/pm/test_downloader_integrity.py::"
        "test_partial_download_retries_keep_the_connection_pool\n"
    )
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", pressure, "ubuntu-latest")
    )
    verdict = rc.classify(check, _off_loop_facts(delta=(), log=pressure))
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert not verdict.classified
    joined = " ".join(verdict.evidence)
    assert "consistent with capacity, not called on it" in joined
    assert "ubuntu-latest" in joined
    assert "blacksmith-32vcpu-ubuntu-2404" in joined
    assert "32 vCPU" in joined
    assert "a capacity call needs a bound" in joined
    hint = next(line for line in verdict.evidence if "consistent with capacity" in line)
    assert re.search(r"\d", hint), hint
    # Missing-fact lines name nothing measurable; only the quoted hardware facts do.
    assert verdict.evidence[0].startswith("no assertion here")

    child_log = (
        "E   AssertionError: assert 'running' == 'stopped'\n"
        "FAILED tests/nastech_cli/test_local_runtime_processes.py::"
        "test_failed_setup_never_runs_child_and_releases_handles[assign|resume]\n"
    )
    lane = "tests (3.12, windows-latest)"
    run = _fork_run(lane, child_log, "windows-2025-vs2026")
    child_facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane=lane,
        upstream_runs=_payload(_upstream_run(lane, "blacksmith-32vcpu-windows-2025")),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    child_verdict = rc.classify(rc.FailedCheck.from_check_run(run), child_facts)
    assert child_verdict.kind is rc.RedClass.UNKNOWN
    assert "consistent with capacity" not in " ".join(child_verdict.evidence)


def test_capacity_refuses_without_the_image_being_taken_away():
    facts = _off_loop_facts()
    same_image = rc.SyncFacts(
        lane=facts.lane,
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        fork_image="blacksmith-32vcpu-ubuntu-2404",
        upstream_image="blacksmith-32vcpu-ubuntu-2404",
        upstream_changed_files=facts.upstream_changed_files,
        upstream_succeeded=True,
    )
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "blacksmith-32vcpu-ubuntu-2404")
        ),
        same_image,
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "did not take a bigger machine away" in " ".join(verdict.evidence)


def test_capacity_refuses_when_upstream_reports_the_same_test_failing():
    """A bound that is unsatisfiable upstream too is a product bug, never hardware noise."""
    upstream = _payload(
        _upstream_run(
            "tests (3.14, ubuntu-latest)",
            "blacksmith-32vcpu-ubuntu-2404",
            conclusion="failure",
            text=f"FAILED {TEST_ID}",
        )
    )
    facts = rc.SyncFacts.from_check_runs(
        _payload(_fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")),
        lane="tests (3.14, ubuntu-latest)",
        upstream_runs=upstream,
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
        ),
        facts,
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "never satisfiable upstream" in " ".join(verdict.evidence)


def test_flaky_retry_is_evidence_and_never_a_class_of_its_own():
    log = OFF_LOOP_LOG + "FLAKY tests/gateway/test_mutation_prepare_off_loop.py\n"
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run("tests (3.14, ubuntu-latest)", log, "ubuntu-latest")
        ),
        _off_loop_facts(log=log),
    )
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY
    assert "retried" in verdict.evidence[-1]
    bare = rc.FailedCheck.from_check_run(
        _fork_run(
            "tests (3.14, ubuntu-latest)",
            "FAILED tests/gateway/test_brand_new.py::test_y\nFLAKY again\n",
            "ubuntu-latest",
        )
    )
    plain = rc.classify(bare, _off_loop_facts(log="FAILED x.py::test_y\nFLAKY again\n"))
    assert plain.kind is rc.RedClass.UNKNOWN
    assert "the failure moved" in " ".join(plain.evidence)


# ---------------------------------------------------------------------------
# Class 2: brand inflation
# ---------------------------------------------------------------------------


def test_over_cap_chain_with_a_clean_delta_is_brand_inflation(tmp_path):
    tree = _chain_tree(tmp_path)
    check = rc.FailedCheck.from_check_run(_lint_run(tmp_path))
    verdict = rc.classify(check, _brand_facts(tmp_path))
    assert verdict.kind is rc.RedClass.BRAND_INFLATION
    assert verdict.owner == "100Ways brand transform"
    joined = " ".join(verdict.evidence)
    assert "29956 -> 30022" in joined
    assert "30000" in joined
    assert SIZE_CHECKER in joined
    assert tree


def test_the_cap_is_parsed_from_the_enforcing_file_not_copied(tmp_path):
    """Move the number where the checker itself would move it and the evidence follows."""
    _chain_tree(tmp_path)
    (tmp_path / SIZE_CHECKER).write_text("CHAIN_MAX_CHARS = 31_000\nROOT_MAX_CHARS = 12_000\n")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_lint_run(tmp_path)), _brand_facts(tmp_path)
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    joined = " ".join(verdict.evidence)
    assert "30022" in joined
    assert "31000" in joined
    assert "30000" not in joined


def test_chain_under_cap_after_a_shrink_is_not_reported_as_inflation(tmp_path):
    tree = _chain_tree(tmp_path, root_tokens=26, gateway_tokens=40)
    (tmp_path / "gateway/AGENTS.md").write_text("z" * 18100, encoding="utf-8")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_lint_run(tmp_path)),
        rc.SyncFacts.from_check_runs(
            _payload(_lint_run(tmp_path)),
            lane="Python lints / Windows footguns (blocking)",
            upstream_changed_files=[],
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            checkout_path=tree,
        ),
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "under" in " ".join(verdict.evidence)


def test_inflation_never_asks_to_move_the_number(tmp_path):
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_lint_run(tmp_path)), _brand_facts(_chain_tree(tmp_path))
    )
    assert verdict.kind is rc.RedClass.BRAND_INFLATION
    assert "shrink" in verdict.action.lower()
    assert "cap" in " ".join(verdict.evidence)  # quoted as the reason, never as the thing to move


FORBIDDEN_FIXES = (
    r"raise\s+(?:the\s+|any\s+|a\s+)?(?:cap|limit|budget|threshold|max)",
    r"increase\s+(?:the\s+|a\s+)?(?:cap|limit|threshold|workers?)",
    r"bump\s+(?:the\s+|a\s+)?(?:cap|limit)",
    r"lower\s+(?:the\s+|a\s+)?(?:cap|limit|threshold|bound)",
    r"(?:raise|extend|lengthen|relax|loosen|widen)\b[^.]*\b(?:time)?out",
    r"\b(?:weaken|skip\s+the\s+test|xfail|per-file|file-ignore|ignore-file)\b",
)


def test_no_action_anywhere_asks_for_a_weakened_guard(tmp_path):
    """The policy test: no verdict, in any class, proposes moving a number or dodging a test."""
    tree = _chain_tree(tmp_path)
    verdicts = list(
        rc.classify_runs(
            _payload(
                _lint_run(tmp_path),
                _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest"),
                _fork_run(
                    "tests (3.12, windows-latest)",
                    "E   assert 2 == 1\nFAILED a.py::test_b\n",
                    "windows-2025",
                ),
            ),
            upstream_runs=_payload(
                _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest"),
                _upstream_run("tests (3.14, ubuntu-latest)", "blacksmith-32vcpu-ubuntu-2404"),
                _upstream_run("tests (3.12, windows-latest)", "blacksmith-32vcpu-windows-2025"),
            ),
            upstream_changed_files=["gateway/router.py"],
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            checkout_path=tree,
        )
    )
    verdicts.append(
        rc.classify(
            rc.FailedCheck.from_check_run(
                _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
            ),
            _off_loop_facts(delta=("test_api_admission_off_loop.py",)),
        )
    )
    kinds = {verdict.kind for verdict in verdicts}
    assert rc.RedClass.UNKNOWN in kinds
    for verdict in verdicts:
        for text in (verdict.action, " ".join(verdict.evidence)):
            for pattern in FORBIDDEN_FIXES:
                assert not re.search(pattern, text, re.IGNORECASE), (verdict.kind, text)


def test_render_carries_no_token_looking_string(tmp_path):
    tree = _chain_tree(tmp_path)
    out = rc.render(
        rc.classify_runs(
            _payload(_lint_run(tmp_path), _fork_run("tests", OFF_LOOP_LOG, "ubuntu-latest")),
            upstream_runs=_payload(
                _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest"),
                _upstream_run("tests", "blacksmith-32vcpu-ubuntu-2404"),
            ),
            upstream_changed_files=[],
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            checkout_path=tree,
        )
    )
    for pattern in (
        r"gh[pousr]_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9_-]{20,}",
        r"xox[baprs]-",
        r"AKIA[0-9A-Z]{16}",
        r"\b[0-9a-f]{20,}\b",
        r"\b[A-Za-z0-9+/]{32,}={0,2}\b",
        r"(?i)bearer\s+\S{8,}",
    ):
        assert not re.search(pattern, out), pattern
    assert "300a3977" in out  # a short commit name, which is a label and not a credential


def test_dossier_shapes_for_the_other_lanes():
    """macOS, Windows and the pre-Blacksmith paid labels share one derived precondition."""
    for upstream, fork in (
        ("blacksmith-12vcpu-macos-latest", "macos-latest"),
        ("blacksmith-32vcpu-windows-2025", "windows-latest"),
        ("ubuntu-latest-32-core", "ubuntu-latest"),
        ("ubuntu-latest-32-arm-core", "ubuntu-24.04-arm"),
    ):
        facts = rc.SyncFacts(
            lane="tests",
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            fork_image=fork,
            upstream_image=upstream,
            upstream_changed_files=frozenset(),
            upstream_succeeded=True,
        )
        assert facts.runner_shape().forced_down is True, upstream
        verdict = rc.classify(_browse_check(), facts)
        assert verdict.kind is rc.RedClass.RUNNER_CAPACITY, upstream
        assert fork in " ".join(verdict.evidence)


def _browse_check() -> rc.FailedCheck:
    log = (
        "    BROWSE_RECONCILE_BUDGET_S = 3.0\n"
        "E   sqlite3.OperationalError: interrupted\n"
        "FAILED tests/nastech_cli/test_browse_schema_drift.py::test_reconcile[ddl]\n"
    )
    return rc.FailedCheck.from_check_run(_fork_run("tests", log, "macos-latest"))


def test_chain_over_cap_on_upstream_text_too_is_not_blamed_on_the_brand(tmp_path):
    """A breach the brand did not cause is a person's call, not a transform bug."""
    _chain_tree(tmp_path, root_tokens=26, gateway_tokens=40)
    (tmp_path / "gateway/AGENTS.md").write_text("nastech " * 40 + "w" * 25_000, encoding="utf-8")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_lint_run(tmp_path)),
        _brand_facts(tmp_path, delta=[]),
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "not the brand" in " ".join(verdict.evidence)


def test_hint_cap_file_is_measured_against_its_own_number(tmp_path):
    from hundredways import red_evidence as ev

    tree = _chain_tree(tmp_path)
    (tmp_path / "agent/AGENTS.md").write_text("q" * 32_001, encoding="utf-8")
    reports = ev.size_reports(tree, ["agent/AGENTS.md"])
    file_report = next(report for report in reports if report.kind == "file")
    assert file_report.cap == 32_000
    assert file_report.over_cap is True


def test_every_classified_verdict_quotes_a_number_or_a_file_per_line(tmp_path):
    """Evidence is the deliverable: a line that names nothing measurable is not evidence."""
    tree = _chain_tree(tmp_path)
    verdicts = list(
        rc.classify_runs(
            _payload(
                _lint_run(tmp_path),
                _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest"),
            ),
            upstream_runs=_payload(
                _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest"),
                _upstream_run("tests (3.14, ubuntu-latest)", "blacksmith-32vcpu-ubuntu-2404"),
            ),
            upstream_changed_files=["gateway/router.py"],
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            checkout_path=tree,
        )
    )
    verdicts.append(
        rc.classify(
            rc.FailedCheck.from_check_run(
                _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
            ),
            _off_loop_facts(delta=("tests/gateway/test_api_admission_off_loop.py",)),
        )
    )
    assert [verdict.kind for verdict in verdicts] == [
        rc.RedClass.BRAND_INFLATION,
        rc.RedClass.RUNNER_CAPACITY,
        rc.RedClass.GENUINE_DIVERGENCE,
    ]
    for verdict in verdicts:
        assert verdict.classified
        assert len(verdict.evidence) >= 3, verdict
        for line in verdict.evidence:
            assert re.search(r"\d|[\w./-]+\.(?:py|md)\b", line), (verdict.kind, line)


def test_size_lint_without_the_checker_in_the_tree_is_never_inflation(tmp_path):
    check = rc.FailedCheck.from_check_run(_lint_run(tmp_path))
    facts = rc.SyncFacts.from_check_runs(
        _payload(_lint_run(tmp_path)),
        lane="Python lints / Windows footguns (blocking)",
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=str(tmp_path),
    )
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert SIZE_CHECKER in " ".join(verdict.evidence)


# ---------------------------------------------------------------------------
# Class 3 outranks 1 and 2
# ---------------------------------------------------------------------------


def test_upstream_change_outranks_a_wall_clock_shape():
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    )
    facts = _off_loop_facts(delta=("tests/gateway/test_api_admission_off_loop.py",))
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.GENUINE_DIVERGENCE
    assert verdict.owner == "upstream sync"
    joined = " ".join(verdict.evidence)
    assert "test_api_admission_off_loop.py" in joined
    assert "HOLD_S / 3" in joined
    assert "ranks below" in joined


def test_upstream_change_outranks_an_over_cap_chain(tmp_path):
    _chain_tree(tmp_path)
    check = rc.FailedCheck.from_check_run(_lint_run(tmp_path))
    verdict = rc.classify(
        check,
        rc.SyncFacts.from_check_runs(
            _payload(_lint_run(tmp_path)),
            lane="Python lints / Windows footguns (blocking)",
            upstream_runs=_payload(
                _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest")
            ),
            upstream_changed_files=["gateway/AGENTS.md"],
            baseline_sha=BASELINE,
            current_sha=CURRENT,
            checkout_path=str(tmp_path),
        ),
    )
    assert verdict.kind is rc.RedClass.GENUINE_DIVERGENCE


def test_divergence_action_does_not_hand_edit_upstream_owned_text():
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    )
    verdict = rc.classify(check, _off_loop_facts(delta=("test_api_admission_off_loop.py",)))
    assert "sync" in verdict.action.lower()
    assert "branding re-derives" in " ".join(verdict.evidence)


# ---------------------------------------------------------------------------
# UNKNOWN is reachable and honest
# ---------------------------------------------------------------------------


def test_no_upstream_comparison_leaves_the_red_unclassified(tmp_path):
    """A red nobody diffed against upstream gets no class, and is told why."""
    _chain_tree(tmp_path)
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    )
    facts = rc.SyncFacts.from_check_runs(
        _payload(_fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")),
        lane="tests (3.14, ubuntu-latest)",
        checkout_path=str(tmp_path),
    )
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert not verdict.classified
    joined = " ".join(verdict.evidence)
    assert "no upstream file delta" in joined
    assert "no upstream check-run" in joined


def test_capacity_is_not_claimed_on_a_time_shape_alone(tmp_path):
    """The dossier's own failure mode: a wrong "it is just the runner" ships a divergence."""
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    )
    facts = rc.SyncFacts(
        lane="tests (3.14, ubuntu-latest)",
        fork_image="ubuntu-latest",
        upstream_changed_files=[],
        upstream_succeeded=None,
    )
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert any("no upstream check-run" in line for line in verdict.evidence)


def test_unmeasurable_size_lint_says_what_it_cannot_read(tmp_path):
    check = rc.FailedCheck.from_check_run(_lint_run(tmp_path))
    facts = rc.SyncFacts.from_check_runs(
        _payload(_lint_run(tmp_path)),
        lane="Python lints / Windows footguns (blocking)",
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=str(tmp_path),
    )
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "no measurable" in " ".join(verdict.evidence)


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------


def test_render_is_plain_text_with_no_markdown_entities(tmp_path):
    verdicts = (
        rc.classify(
            rc.FailedCheck.from_check_run(_lint_run(tmp_path)), _brand_facts(_chain_tree(tmp_path))
        ),
        rc.classify(
            rc.FailedCheck.from_check_run(
                _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
            ),
            _off_loop_facts(),
        ),
    )
    out = rc.render(verdicts)
    assert "*" not in out
    assert "`" not in out
    assert "__" not in out
    assert "](" not in out
    assert not re.search(r"(?<![\w/])_[^_\n]{1,40}_(?![\w])", out), out
    assert not re.search(r"^\s*#{1,6}\s", out, re.MULTILINE)
    assert "RUNNER_CAPACITY" in out and "BRAND_INFLATION" in out
    assert "blacksmith-32vcpu-ubuntu-2404" in out
    assert out.count("evidence:") == 2
    assert rc.render(()) == "Fork CI red classification: no red checks to classify.\n"


def test_rendered_lines_carry_no_secret_value(tmp_path):
    """Secret names may appear; a credential shape from the log may never reach a line."""
    leaked = "ghp_" + "A" * 36
    check = rc.FailedCheck.from_check_run(
        _fork_run(
            "tests (3.14, ubuntu-latest)",
            OFF_LOOP_LOG + f"NASTECH_API_KEY={leaked}\n",
            "ubuntu-latest",
        )
    )
    verdict = rc.classify(check, _off_loop_facts())
    out = rc.render((verdict,))
    assert leaked not in out
    assert "NASTECH_API_KEY" not in out or "ghp_" not in out


def test_classify_runs_sorts_every_red_in_one_payload(tmp_path):
    tree = _chain_tree(tmp_path)
    fork = _payload(
        _lint_run(tmp_path),
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest"),
        _fork_run("Required checks passed", "nothing to see", "ubuntu-latest"),
    )
    upstream = _payload(
        _upstream_run("Python lints / Windows footguns (blocking)", "windows-latest"),
        _upstream_run("tests (3.14, ubuntu-latest)", "blacksmith-32vcpu-ubuntu-2404"),
    )
    verdicts = rc.classify_runs(
        fork,
        upstream_runs=upstream,
        upstream_changed_files=["gateway/router.py"],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=tree,
    )
    assert [v.kind for v in verdicts] == [
        rc.RedClass.BRAND_INFLATION,
        rc.RedClass.RUNNER_CAPACITY,
    ]
    assert rc.failing_checks(fork).__len__() == 2


# ---------------------------------------------------------------------------
# Every number in the report is one the enforcing thing still says
# ---------------------------------------------------------------------------


def test_the_worker_ceiling_follows_the_rule_that_imposes_it(monkeypatch):
    """Nothing here remembers ``_TEST_WORKERS_CAP``: move it and the evidence moves.

    And when the rule stops capping at all, the ceiling is reported as unmeasurable -- quoting
    the probe back as a ceiling would be the same invention as copying the number.
    """
    from hundredways import red_evidence as ev
    from hundredways import rules

    assert ev.worker_ceiling() == rules._TEST_WORKERS_CAP
    monkeypatch.setattr(rules, "_TEST_WORKERS_CAP", 2)
    assert ev.worker_ceiling() == 2
    monkeypatch.setattr(rules, "_TEST_WORKERS_CAP", 20_000)
    assert ev.worker_ceiling() is None

    monkeypatch.setattr(rules, "_TEST_WORKERS_CAP", 3)
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
        ),
        _off_loop_facts(),
    )
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY
    assert "worker ceiling is 3" in " ".join(verdict.evidence)


def test_capacity_precondition_is_what_the_transform_does_today(monkeypatch):
    """Buy the Blacksmith account and the capacity excuse disappears with the rewrite.

    The Class-1 precondition is asked of :class:`BrandingRules`, never compared to a remembered
    image: with the label left intact, nothing took a bigger machine away from this lane.
    """
    from hundredways import rules

    verdict = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
        ),
        _off_loop_facts(),
    )
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY

    monkeypatch.setattr(rules, "_normalize_blacksmith", lambda match: match.group(0))
    kept = rc.SyncFacts(
        lane="tests (3.14, ubuntu-latest)",
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        fork_image="blacksmith-32vcpu-ubuntu-2404",
        upstream_image="blacksmith-32vcpu-ubuntu-2404",
        upstream_changed_files=frozenset(),
        upstream_succeeded=True,
    )
    stayed = rc.classify(
        rc.FailedCheck.from_check_run(
            _fork_run(
                "tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "blacksmith-32vcpu-ubuntu-2404"
            )
        ),
        kept,
    )
    assert stayed.kind is rc.RedClass.UNKNOWN
    assert "did not take a bigger machine away" in " ".join(stayed.evidence)


def test_every_size_cap_is_the_number_the_enforcing_file_carries(tmp_path):
    """Move a cap in the file that enforces it and both the measurement and the verdict move."""
    from hundredways import red_evidence as ev

    tree = _chain_tree(tmp_path)
    (tmp_path / "agent/AGENTS.md").write_text("q" * 32_001, encoding="utf-8")
    measured = {item.kind: item for item in ev.size_reports(tree, ["agent/AGENTS.md"])}
    assert measured["file"].cap == 32_000
    assert measured["file"].over_cap is True

    (tmp_path / "agent/subdirectory_hints.py").write_text("_MAX_HINT_CHARS = 40_000\n")
    moved = {item.kind: item for item in ev.size_reports(tree, ["agent/AGENTS.md"])}
    assert moved["file"].cap == 40_000
    assert moved["file"].over_cap is False

    (tmp_path / "agent/subdirectory_hints.py").write_text("_MAX_HINT_CHARS = 9_000\n")
    lowered = {item.kind: item for item in ev.size_reports(tree, ["agent/AGENTS.md"])}
    assert lowered["file"].cap == 9_000
    assert lowered["file"].over_cap is True


def test_a_moved_chain_cap_moves_the_brand_attribution_too(tmp_path):
    """The attribution compares against the parsed cap, never against the recorded 30000.

    With the cap at 20000 the same chain is over it with the brand tokens reversed as well, so
    the breach stops being the rename's and stops being classified at all.
    """
    _chain_tree(tmp_path)
    (tmp_path / SIZE_CHECKER).write_text("CHAIN_MAX_CHARS = 20_000\nROOT_MAX_CHARS = 12_000\n")
    verdict = rc.classify(
        rc.FailedCheck.from_check_run(_lint_run(tmp_path)), _brand_facts(tmp_path)
    )
    assert verdict.kind is rc.RedClass.UNKNOWN
    joined = " ".join(verdict.evidence)
    assert "29956" in joined
    assert "not the brand" in joined


def test_observed_stall_is_read_from_the_assertion_it_stalled_on():
    """Two tests sharing one budget must not share one measurement.

    ``0.78`` belongs to the first assertion and ``2.1`` to the second: an observed number taken
    from whichever rewrite happened to carry the matching bound would quote a stall this test
    never reported.
    """
    from hundredways import red_evidence as ev

    log = (
        "    HOLD_S = 1.5\n"
        ">       assert max(gaps) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 0.78 < 0.5\n"
        "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[chat]\n"
        ">       assert max(idle) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 2.1 < 0.5\n"
        "FAILED tests/gateway/test_api_admission_off_loop.py::test_admission_off_loop[json]\n"
    )
    claims = [claim for claim in ev.wall_clock_claims(log) if claim.origin == "assertion"]
    assert len(claims) == 2
    assert [claim.observed_seconds for claim in claims] == [0.78, 2.1]
    assert [claim.bound_seconds for claim in claims] == [0.5, 0.5]

    # A measurement on the same line as the bound needs no rewrite to be read.
    inline = ev.wall_clock_claims("E   assert 1.9 < HOLD_S\n    HOLD_S = 1.5\n")
    assert [(c.budget, c.bound_seconds, c.observed_seconds) for c in inline] == [
        ("HOLD_S", 1.5, 1.9)
    ]


def test_a_budget_the_traceback_shows_with_its_gutter_is_still_measured():
    """The log prints the armed constant behind an ``E``/``>`` gutter; the number is still his."""
    from hundredways import red_evidence as ev

    plain = ev.declared_budgets("HOSTED_REQUEST_TIMEOUT_SECS = 1.0\n")
    guttered = ev.declared_budgets(">       HOSTED_REQUEST_TIMEOUT_SECS = 1.0\n")
    indented = ev.declared_budgets("        HOSTED_REQUEST_TIMEOUT_SECS = 1.0\n")
    assert plain == guttered == indented == {"HOSTED_REQUEST_TIMEOUT_SECS": 1.0}


# ---------------------------------------------------------------------------
# A green comparison means a run that was actually made
# ---------------------------------------------------------------------------


def test_capacity_refuses_when_upstream_red_named_no_test_at_all():
    """A cancelled upstream lane reports nothing, so it cannot have passed this test."""
    run = _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane="tests (3.14, ubuntu-latest)",
        upstream_runs=_payload(
            _upstream_run(
                "tests (3.14, ubuntu-latest)",
                "blacksmith-32vcpu-ubuntu-2404",
                conclusion="cancelled",
                text="##[error]The job was cancelled",
            )
        ),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    verdict = rc.classify(rc.FailedCheck.from_check_run(run), facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "no green comparison" in " ".join(verdict.evidence)


def test_capacity_refuses_when_upstream_shares_any_reported_test():
    """Every id this red reports has to be clear upstream, not just the first one."""
    other = "tests/gateway/test_mutation_prepare_off_loop.py::test_mutation_prepare[chat]"
    log = OFF_LOOP_LOG + f"FAILED {other}\n"
    run = _fork_run("tests (3.14, ubuntu-latest)", log, "ubuntu-latest")
    facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane="tests (3.14, ubuntu-latest)",
        upstream_runs=_payload(
            _upstream_run(
                "tests (3.14, ubuntu-latest)",
                "blacksmith-32vcpu-ubuntu-2404",
                conclusion="failure",
                text=f"FAILED {other}\n",
            )
        ),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    check = rc.FailedCheck.from_check_run(run)
    assert len(check.test_ids) == 2
    assert rc.classify(check, facts).kind is rc.RedClass.UNKNOWN

    # Upstream's own red names a different test: that is the pairing the class is entitled to.
    disjoint = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane="tests (3.14, ubuntu-latest)",
        upstream_runs=_payload(
            _upstream_run(
                "tests (3.14, ubuntu-latest)",
                "blacksmith-32vcpu-ubuntu-2404",
                conclusion="failure",
                text="FAILED tests/pm/test_elsewhere.py::test_y\n",
            )
        ),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    verdict = rc.classify(check, disjoint)
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY
    phrase = next(line for line in verdict.evidence if "upstream ran" in line)
    assert "its own red names 1 other test id(s)" in phrase


def test_the_upstream_lane_is_found_across_the_brand():
    """A job keeps its identity through the rename; the comparison has to follow it."""
    log = OFF_LOOP_LOG
    fork_lane = "nastech tests (3.14, ubuntu-latest)"
    run = _fork_run(fork_lane, log, "ubuntu-latest")
    facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane=fork_lane,
        upstream_runs=_payload(
            _upstream_run("hermes tests (3.14, ubuntu-latest)", "blacksmith-32vcpu-ubuntu-2404")
        ),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    assert facts.upstream_image == "blacksmith-32vcpu-ubuntu-2404"
    assert facts.upstream_succeeded is True
    assert rc.classify(rc.FailedCheck.from_check_run(run), facts).kind is (
        rc.RedClass.RUNNER_CAPACITY
    )


@pytest.mark.parametrize(
    "lane,test_id",
    [
        ("tests (3.14, ubuntu-latest)", TEST_ID),
        ("py 3.14 shard 7", "tests/renamed/test_moved_by_upstream_this_week.py::test_new_shape"),
        ("Linux full suite", "tests/gateway/test_zzz.py::test_a[chat]"),
    ],
)
def test_the_class_follows_the_measurement_and_not_the_name(lane, test_id):
    """Rename the job and the test until nothing is recognisable: the class does not move."""
    log = (
        "tests/gateway/test_api_admission_off_loop.py:57: in test_admission_off_loop\n"
        "    HOLD_S = 1.5\n"
        ">       assert max(gaps) < (HOLD_S / 3)\n"
        "E       AssertionError: assert 0.78 < 0.5\n"
        f"FAILED {test_id}\n"
    )
    run = _fork_run(lane, log, "ubuntu-latest")
    facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane=lane,
        upstream_runs=_payload(_upstream_run(lane, "blacksmith-32vcpu-ubuntu-2404")),
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
    )
    assert rc.classify(rc.FailedCheck.from_check_run(run), facts).kind is (
        rc.RedClass.RUNNER_CAPACITY
    )


def test_a_job_named_like_nothing_measure_still_gets_the_measured_class(tmp_path):
    """No size-lint word anywhere, yet the chain itself is over the cap the checker reads."""
    tree = _chain_tree(tmp_path)
    run = _fork_run(
        "nightly docs audit", "AGENTS.md + gateway/AGENTS.md: 30022 chars\n", "ubuntu-latest"
    )
    facts = rc.SyncFacts.from_check_runs(
        _payload(run),
        lane="nightly docs audit",
        upstream_changed_files=[],
        baseline_sha=BASELINE,
        current_sha=CURRENT,
        checkout_path=tree,
    )
    verdict = rc.classify(rc.FailedCheck.from_check_run(run), facts)
    assert verdict.kind is rc.RedClass.BRAND_INFLATION


def test_unknown_names_every_precondition_that_did_not_measure_out():
    """Three gates shut at once, and the report says so three times over.

    No upstream delta, no upstream run, no image the brand was forced to give up: a report that
    picked a class here would be guessing, and a report that named one missing fact while
    hiding two would be a worse one.
    """
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "")
    )
    facts = rc.SyncFacts(lane="tests (3.14, ubuntu-latest)", fork_image="", upstream_image="")
    verdict = rc.classify(check, facts)
    assert verdict.kind is rc.RedClass.UNKNOWN
    joined = " ".join(verdict.evidence)
    assert "no upstream file delta" in joined
    assert "no upstream check-run" in joined
    assert "did not take a bigger machine away" in joined
    assert "no class is claimed here" in joined
    # One line per unmet gate -- delta, upstream run, image -- plus the closing statement.
    assert len(verdict.evidence) == 5
    assert verdict.owner == "the person reading this red"
    assert "Fill in the missing fact" in verdict.action


# ---------------------------------------------------------------------------
# Publisher configuration: the red that no file in the tree owns
# ---------------------------------------------------------------------------

SIGNING_LOG = (
    "2026-10-21T10:15:31.9610360Z   PYTHONDONTWRITEBYTECODE: 1\n"
    "2026-10-21T10:15:31.9610640Z   CSC_LINK: \n"
    "2026-10-21T10:15:31.9610930Z   CSC_KEY_PASSWORD: \n"
    "2026-10-21T10:15:31.9611230Z   APPLE_API_KEY_P8: \n"
    "2026-10-21T10:15:31.9611460Z   APPLE_API_KEY_ID: \n"
    "2026-10-21T10:15:31.9611760Z   APPLE_API_ISSUER: \n"
    "2026-10-21T10:15:32.0460390Z ##[error]required signing/notarization credentials are "
    "missing from release-signing: CSC_LINK CSC_KEY_PASSWORD APPLE_API_KEY_P8 "
    "APPLE_API_KEY_ID APPLE_API_ISSUER\n"
    "2026-10-21T10:15:32.0465950Z ##[error]Process completed with exit code 1.\n"
)
RELEASE_LANE = "bundled darwin-arm64 (darwin-arm64, macos-15)"


def _signing_log(**overrides: str) -> str:
    text = SIGNING_LOG
    for name, value in overrides.items():
        text = text.replace(f"  {name}: \n", f"  {name}: {value}\n")
    return text


def _release_facts(**kwargs) -> rc.SyncFacts:
    defaults = {
        "lane": RELEASE_LANE,
        "fork_image": "macos-15",
        "upstream_changed_files": [],
        "upstream_succeeded": None,
    }
    return rc.SyncFacts(**{**defaults, **kwargs})


def test_an_unset_signing_credential_is_configuration_not_a_product_red():
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, SIGNING_LOG, "macos-15"))
    verdict = rc.classify(check, _release_facts())
    assert verdict.kind is rc.RedClass.PUBLISHER_CONFIGURATION
    assert verdict.owner == "the repo owner — this repo's Actions settings"
    assert "release-signing" in verdict.action
    assert "re-run" in verdict.action


def test_the_configuration_call_outranks_a_divergence_in_the_same_file():
    """Syncing cannot set a credential, so a delta touching the lane's own workflow stays put."""
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, SIGNING_LOG, "macos-15"))
    verdict = rc.classify(
        check, _release_facts(upstream_changed_files=["desktop-bundled-release.yml"])
    )
    assert verdict.kind is rc.RedClass.PUBLISHER_CONFIGURATION
    assert not any("upstream changed" in line for line in verdict.evidence)


def test_the_configuration_verdict_names_the_environment_and_counts_the_inputs():
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, SIGNING_LOG, "macos-15"))
    verdict = rc.classify(check, _release_facts())
    joined = " ".join(verdict.evidence)
    assert "5 unread credentials" in joined
    assert "release-signing environment" in joined
    assert "CSC_LINK" in joined
    assert "nothing after the colon" in joined


def test_a_named_input_shown_carrying_a_value_refuses_the_class():
    """The falsification: a log that contradicts its own pre-flight is some other red."""
    log = _signing_log(CSC_LINK="p12-data-here")
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, log, "macos-15"))
    verdict = rc.classify(check, _release_facts())
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert any("carrying a value" in line for line in verdict.evidence)


def test_a_masked_value_counts_as_configured_not_missing():
    """GitHub prints a set secret as ``***``; that is a value, and the call must refuse it."""
    log = _signing_log(APPLE_API_ISSUER="***")
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, log, "macos-15"))
    assert rc.classify(check, _release_facts()).kind is rc.RedClass.UNKNOWN


def test_no_value_of_a_named_input_reaches_the_report():
    log = _signing_log(CSC_KEY_PASSWORD="hunter2-not-a-secret")
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, log, "macos-15"))
    verdict = rc.classify(check, _release_facts())
    assert verdict.kind is rc.RedClass.UNKNOWN
    assert "hunter2" not in " ".join(verdict.evidence + (verdict.action,))


def test_prose_about_missing_credentials_selects_nothing():
    """A human sentence is not a pre-flight: without the env and the name list there is no call."""
    log = "the signing credentials are missing from this run, which is expected on a fork\n"
    check = rc.FailedCheck.from_check_run(_fork_run(RELEASE_LANE, log, "macos-15"))
    verdict = rc.classify(check, _release_facts())
    assert verdict.kind is not rc.RedClass.PUBLISHER_CONFIGURATION


def test_a_capacity_red_still_reads_as_capacity_with_the_new_class_present(tmp_path):
    """Adding a class must not swallow the dossier's own reds."""
    check = rc.FailedCheck.from_check_run(
        _fork_run("tests (3.14, ubuntu-latest)", OFF_LOOP_LOG, "ubuntu-latest")
    )
    verdict = rc.classify(check, _off_loop_facts())
    assert verdict.kind is rc.RedClass.RUNNER_CAPACITY


def test_configuration_is_reachable_through_the_payload_surface():
    verdicts = rc.classify_runs(
        _payload(_fork_run(RELEASE_LANE, SIGNING_LOG, "macos-15")),
        upstream_changed_files=[],
    )
    assert [verdict.kind for verdict in verdicts] == [rc.RedClass.PUBLISHER_CONFIGURATION]
    rendered = rc.render(verdicts)
    assert "PUBLISHER_CONFIGURATION 1" in rendered
    assert "CSC_LINK" in rendered
    assert "release-signing" in rendered


def test_missing_inputs_returns_identifiers_and_never_their_values():
    from hundredways import red_evidence as evidence

    claims = evidence.missing_inputs(_signing_log(APPLE_API_KEY_P8="a-value-123"))
    assert len(claims) == 1
    claim = claims[0]
    assert claim.names[0] == "CSC_LINK"
    assert claim.bound == ("APPLE_API_KEY_P8",)
    assert "a-value-123" not in repr(claim)
    assert evidence.input_bindings(SIGNING_LOG)[0] >= {"CSC_LINK", "APPLE_API_ISSUER"}
