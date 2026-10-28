"""A duration nobody reads is not observability.

``StageResult.duration_ms`` existed before anything consumed it, so these tests hold the
reader together: the ranked report table, the JSON round-trip a later run compares against,
and the regression thresholds that decide when a slower stage is worth naming.
"""

from __future__ import annotations

import json

import pytest

from hundredways.cli import build_parser
from hundredways.profiling import (
    PROFILE_NAME,
    PROFILE_SCHEMA,
    REGRESSION_MIN_DELTA_MS,
    REGRESSION_MIN_RATIO,
    StageRecord,
    compare,
    format_comparison,
    improvements,
    payload,
    ranked,
    read_profile,
    records,
    regressions,
    slowest,
    stage_table,
    total_ms,
    write_profile,
)
from hundredways.updates import StageResult


def stages(*pairs: tuple[str, int]) -> list[StageResult]:
    return [StageResult(name, "ok", "", ms) for name, ms in pairs]


def test_total_ms_adds_every_stage_including_the_ones_that_failed():
    measured = stages(("brand", 4_000), ("verify", 6_000), ("notify", 250))

    assert total_ms(measured) == 10_250


def test_the_table_is_ranked_by_cost_not_pipeline_order():
    rows = ranked(stages(("pull", 10), ("brand", 900), ("scan", 400)))

    assert [row.name for row in rows] == ["brand", "scan", "pull"]
    assert [row.name for row in slowest(rows, 2)] == ["brand", "scan"]


def test_a_tie_keeps_the_order_the_pipeline_ran_in():
    rows = ranked(stages(("gate", 500), ("scan", 500), ("brand", 500)))

    assert [row.name for row in rows] == ["gate", "scan", "brand"]


def test_the_report_table_names_the_total_the_share_and_the_slow_one():
    lines = stage_table(stages(("brand", 7_000), ("scan", 3_000)))
    text = "\n".join(lines)

    assert "## Stage timing" in text
    assert "total measured: 10.00 s" in text
    assert "| brand | ok | 7.00 s | 70.0% |" in lines
    assert "Slowest: brand 7.0s, scan 3.0s." in text


def test_an_empty_run_reports_zero_instead_of_dividing_by_it():
    lines = stage_table([])

    assert lines[2] == "- total measured: 0.00 s"
    assert lines[-1] == "|-------|--------|----------|-------|"
    assert not any("Slowest" in line for line in lines)


def test_a_status_other_than_ok_survives_into_the_table():
    failed = [StageResult("verify", "fail", "boom", 1_200)]

    assert "| verify | fail | 1.20 s | 100.0% |" in stage_table(failed)


def test_the_profile_round_trips_through_disk(tmp_path):
    target = write_profile(tmp_path / "run.json", stages(("brand", 1_000), ("scan", 20)), run="7")
    raw = json.loads(target.read_text(encoding="utf-8"))

    assert raw["schema"] == PROFILE_SCHEMA
    assert raw["run"] == "7"
    assert raw["total_ms"] == 1_020
    assert read_profile(target) == [
        StageRecord("brand", "ok", 1_000),
        StageRecord("scan", "ok", 20),
    ]


def test_a_directory_gets_the_default_profile_name(tmp_path):
    target = write_profile(tmp_path, stages(("brand", 1)))

    assert target.name == PROFILE_NAME
    assert target.is_file()


def test_payload_and_records_read_any_object_with_the_three_fields():
    class Loose:
        name = "gate"
        status = "skip"
        duration_ms = 5

    assert records([Loose()]) == [StageRecord("gate", "skip", 5)]
    assert payload([Loose()], run="")["stages"] == [
        {"name": "gate", "status": "skip", "duration_ms": 5}
    ]


def test_a_stage_missing_from_one_side_reads_as_zero_not_as_an_error():
    deltas = compare(
        [StageRecord("brand", "ok", 5_000), StageRecord("scan", "ok", 1_000)],
        [StageRecord("brand", "ok", 4_000), StageRecord("package", "ok", 800)],
    )
    by_name = {delta.name: delta for delta in deltas}

    assert by_name["scan"].after_ms == 0
    assert by_name["package"].before_ms == 0
    assert by_name["brand"].delta_ms == -1_000


def test_a_faster_stage_is_an_improvement_measured_in_the_same_units():
    baseline = [StageRecord("brand", "ok", 30_000)]
    current = [StageRecord("brand", "ok", 9_000)]

    assert [delta.name for delta in improvements(baseline, current)] == ["brand"]
    assert improvements(baseline, current)[0].delta_ms == -21_000


def test_a_small_stage_doubling_is_not_a_regression():
    baseline = [StageRecord("notify", "ok", 10)]
    current = [StageRecord("notify", "ok", 30)]

    assert REGRESSION_MIN_RATIO == 1.5
    assert regressions(baseline, current) == []


def test_a_minute_that_becomes_two_minutes_is_a_regression():
    baseline = [StageRecord("brand", "ok", 60_000)]
    current = [StageRecord("brand", "ok", 120_000)]

    slower = regressions(baseline, current)

    assert [delta.name for delta in slower] == ["brand"]
    assert slower[0].ratio == pytest.approx(2.0)


def test_a_long_stage_barely_moving_is_not_a_regression():
    baseline = [StageRecord("brand", "ok", 100_000)]
    current = [StageRecord("brand", "ok", 100_000 + REGRESSION_MIN_DELTA_MS + 1)]

    assert regressions(baseline, current) == []


def test_regressions_are_listed_biggest_jump_first():
    baseline = [
        StageRecord("brand", "ok", 10_000),
        StageRecord("scan", "ok", 40_000),
    ]
    current = [
        StageRecord("brand", "ok", 40_000),
        StageRecord("scan", "ok", 100_000),
    ]

    assert [delta.name for delta in regressions(baseline, current)] == ["scan", "brand"]


def test_the_comparison_is_readable_without_a_shell_pipeline():
    text = "\n".join(
        format_comparison(
            [StageRecord("brand", "ok", 30_000)], [StageRecord("brand", "ok", 12_000)]
        )
    )

    assert "brand" in text
    assert "total 30.00s -> 12.00s" in text


def test_the_profile_command_takes_one_file_and_an_optional_baseline():
    args = build_parser().parse_args(["profile", "new.json", "--against", "old.json"])

    assert args.func == "cmd_profile"
    assert args.current == "new.json"
    assert args.against == "old.json"
    assert build_parser().parse_args(["profile", "new.json"]).against == ""
