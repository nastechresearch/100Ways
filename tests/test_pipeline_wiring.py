"""The pipeline's own steps, checked against the parsers of the scans they run.

Every CI scan is invoked straight from a ``run:`` block -- ``python3 -m
hundredways.prepublish --snapshot ... --fork-root ...`` -- so nothing but this file stands
between "a scan gained a required argument" and "the pipeline fails 20 minutes in, at the
step that needed it".  argparse exits 2 on a missing required flag, which is a red CI with
no product fact in it, and the class the fork-ci-triage skill would file it under does not
exist in the dossier.  Reading the requirement out of the parser instead of a hand-kept
list means the guard follows the scan: add a ``required=True`` argument and every workflow
that has not been updated to pass it turns red here, on the branch, in seconds.

Synthetic workflow text is used for the falsification cases so the guard is proven to be a
guard: a check that cannot fail is a comment.
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import pytest

from hundredways import forklint

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# The step that broke while this guard was being written: ``--fork-root`` became
# ``required=True`` for the fork-lint simulation, and the step that runs the scan did not
# pass it.  It is the worked example for every case below.
READINESS_STEP = """      - name: Run source-provenance and publication-readiness scans
        id: readiness
        run: |
          set -euo pipefail
          python3 -m hundredways.prepublish \\
            --snapshot "$SNAPSHOT" \\
            --upstream "$HUNDREDWAYS_UPDATES_DIR/hermes-agent" \\
            --expected-upstream-sha "$EXPECTED_UPSTREAM_SHA" \\
            --fork-root nastech-agent \\
            --output "$HUNDREDWAYS_READINESS"
"""


def _workflow_texts() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in WORKFLOWS.glob("*.yml")}


def _invocations_of(module: str, workflow: str) -> list[forklint.ModuleInvocation]:
    return [call for call in forklint.module_invocations(workflow) if call.module == module]


def _parser(module: str):
    name = module.split(".", 1)[1]
    return importlib.import_module(f"hundredways.{name}").build_parser()


def test_the_real_pipeline_passes_every_required_flag_of_the_scans_it_runs():
    covered = 0
    for filename, text in _workflow_texts().items():
        for call in forklint.module_invocations(text):
            try:
                parser = _parser(call.module)
            except AttributeError:
                continue
            covered += 1
            gaps = forklint.required_flag_gaps(call, parser)
            assert not gaps, f"{filename} step {call.step!r} omits {gaps} from {call.module}"
    assert covered, "no CI scan is covered by this guard, which means it checks nothing"


def test_the_guard_covers_the_readiness_scan_that_broke():
    calls = _invocations_of("hundredways.prepublish", READINESS_STEP)
    assert [call.step for call in calls] == [
        "Run source-provenance and publication-readiness scans"
    ]
    assert forklint.required_flag_gaps(calls[0], _parser("hundredways.prepublish")) == ()


def test_a_required_flag_dropped_from_the_step_is_the_gap_the_guard_reports():
    broken = READINESS_STEP.replace("            --fork-root nastech-agent \\\n", "")
    calls = _invocations_of("hundredways.prepublish", broken)
    assert forklint.required_flag_gaps(calls[0], _parser("hundredways.prepublish")) == (
        "--fork-root",
    )


def test_flags_spread_over_continuation_lines_fold_into_one_call():
    call = _invocations_of("hundredways.prepublish", READINESS_STEP)[0]
    assert set(call.argv) >= {
        "--snapshot",
        "$SNAPSHOT",
        "--upstream",
        "--expected-upstream-sha",
        "--fork-root",
        "nastech-agent",
        "--output",
    }


def test_a_single_line_call_is_read_as_well_as_a_folded_one():
    text = (
        "      - name: One line\n"
        "        run: python3 -m hundredways.commit_stream"
        " --upstream-repo a/b --nastech-repo c/d --output out.json\n"
    )
    call = _invocations_of("hundredways.commit_stream", text)[0]
    assert forklint.required_flag_gaps(call, _parser("hundredways.commit_stream")) == ()


def test_a_value_is_never_mistaken_for_the_subcommand_that_gates_the_call():
    parser = _parser("hundredways.cli")
    call = forklint.ModuleInvocation(
        module="hundredways.cli",
        argv=("--repo", "update", "update", "--fork-root", "nastech-agent"),
        step="Run the update pipeline",
        line=0,
    )
    assert forklint.running_parser(parser, call.argv).prog.endswith("update")


def test_a_subcommand_is_gated_by_its_own_required_flags_and_not_its_siblings():
    parser = argparse.ArgumentParser(prog="synthetic")
    parser.add_argument("--top", required=True)
    children = parser.add_subparsers(dest="command")
    go = children.add_parser("go")
    go.add_argument("--deep", required=True)
    stop = children.add_parser("stop")
    stop.add_argument("--other", required=True)

    def call(argv: tuple[str, ...]) -> forklint.ModuleInvocation:
        return forklint.ModuleInvocation(
            module="synthetic", argv=argv, step="a step", line=0
        )

    assert forklint.required_flag_gaps(call(("--top", "a", "go", "--deep", "b")), parser) == ()
    assert forklint.required_flag_gaps(call(("--top", "a", "go")), parser) == ("--deep",)
    assert forklint.required_flag_gaps(call(("--top", "a", "stop")), parser) == ("--other",)
    assert forklint.required_flag_gaps(call(("--top", "a")), parser) == ()


def test_an_equals_form_of_a_flag_counts_as_passed():
    parser = _parser("hundredways.prepublish")
    call = forklint.ModuleInvocation(
        module="hundredways.prepublish",
        argv=tuple(
            f"--{name}={value}"
            for name, value in (
                ("snapshot", "s"),
                ("upstream", "u"),
                ("expected-upstream-sha", "sha"),
                ("fork-root", "nastech-agent"),
                ("output", "o"),
            )
        ),
        step="inline",
        line=0,
    )
    assert forklint.required_flag_gaps(call, parser) == ()


def test_the_pipeline_installs_the_ruff_the_fork_pins_before_the_readiness_scan():
    text = (WORKFLOWS / "stage-pipeline.yml").read_text(encoding="utf-8")
    install = text.index("name: Install the ruff the fork pins")
    scan = text.index("-m hundredways.prepublish")
    clone = text.index("name: Shallow clone of the fork")
    assert clone < install < scan, "the pin is read from the fork clone, and ruff must exist"
    assert "read_fork_ruff_pin" in text[install:scan], "the pin must come from the fork"
    assert 'if [ -z "$PIN" ]' in text[install:scan], "an unpinned fork must stop the run"


def test_an_uncovered_ci_scan_is_a_decision_not_an_oversight():
    known_without_a_parser = {"hundredways.telegram_agent"}
    seen = {
        call.module
        for text in _workflow_texts().values()
        for call in forklint.module_invocations(text)
    }
    uncovered = set()
    for module in sorted(seen):
        try:
            _parser(module)
        except AttributeError:
            uncovered.add(module)
    assert uncovered == known_without_a_parser, (
        f"{sorted(uncovered - known_without_a_parser)} is run from CI with no way to check "
        "its required flags; give it a build_parser() or name it here deliberately"
    )


def test_a_step_that_is_not_a_scan_is_not_read_as_one():
    text = (
        "      - name: Install ruff\n"
        "        run: pip install ruff==0.15.10\n"
        "      - name: Nothing\n"
        "        run: echo hundredways.prepublish\n"
    )
    assert forklint.module_invocations(text) == ()


@pytest.mark.parametrize(
    "module",
    ["hundredways.prepublish", "hundredways.conformance", "hundredways.commit_stream"],
)
def test_a_parser_built_by_a_module_is_the_parser_its_main_uses(module):
    parser = _parser(module)
    required = forklint.required_flags(parser)
    assert required, f"{module} declares no required flags, so this guard would be vacuous"
    with pytest.raises(SystemExit):
        parser.parse_args([])
