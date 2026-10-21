"""Stage timing, made readable.

``StageResult`` has carried ``duration_ms`` for a long time and nothing ever read it: the
pipeline knew where its minutes went and never said so.  This module is the reader -- it turns
a run's stage list into a ranked table for the report, a JSON payload a later run can be
compared against, and a regression check that names the stage that got slower.

The numbers here are observations, not gates.  A regression warning never fails a pipeline and
never changes a threshold: only a human decides that a stage is now expected to be slower.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

PROFILE_SCHEMA = "100ways.stage-profile.v1"
PROFILE_NAME = "stage-profile.json"

# A stage is only worth calling out when it moved by both a ratio and an absolute amount:
# a 40 ms stage doubling is noise, a 40 s stage growing by half is the reason a run is late.
REGRESSION_MIN_RATIO = 1.5
REGRESSION_MIN_DELTA_MS = 2_000


@dataclass(frozen=True)
class StageRecord:
    name: str
    status: str
    duration_ms: int

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "StageRecord":
        return cls(
            name=str(raw.get("name", "")),
            status=str(raw.get("status", "ok")),
            duration_ms=int(raw.get("duration_ms", 0) or 0),
        )


@dataclass(frozen=True)
class StageDelta:
    name: str
    before_ms: int
    after_ms: int

    @property
    def delta_ms(self) -> int:
        return self.after_ms - self.before_ms

    @property
    def ratio(self) -> float:
        if self.before_ms <= 0:
            return float("inf") if self.after_ms > 0 else 1.0
        return self.after_ms / self.before_ms


def total_ms(stages: Iterable[Any]) -> int:
    return sum(int(getattr(stage, "duration_ms", 0) or 0) for stage in stages)


def records(stages: Iterable[Any]) -> list[StageRecord]:
    return [
        StageRecord(
            name=str(getattr(stage, "name", "")),
            status=str(getattr(stage, "status", "ok")),
            duration_ms=int(getattr(stage, "duration_ms", 0) or 0),
        )
        for stage in stages
    ]


def ranked(stages: Iterable[Any]) -> list[StageRecord]:
    """Slowest first; ties keep pipeline order so the table is stable across runs."""
    return sorted(records(stages), key=lambda item: -item.duration_ms)


def slowest(stages: Iterable[Any], limit: int = 3) -> list[StageRecord]:
    return ranked(stages)[: max(int(limit), 0)]


def stage_table(stages: Iterable[Any]) -> list[str]:
    """Markdown lines for UPDATE-REPORT.md: the same table the CLI prints, ranked.

    Shares are relative to the measured stages only, so a stage that failed early still shows
    its cost instead of hiding behind a percentage that no longer adds up.
    """
    rows = list(stages)
    measured = total_ms(rows)
    lines = [
        "## Stage timing",
        "",
        f"- total measured: {measured / 1000:.2f} s",
        f"- profile: `{PROFILE_SCHEMA}`",
        "",
        "| stage | status | duration | share |",
        "|-------|--------|----------|-------|",
    ]
    for record in ranked(rows):
        share = (record.duration_ms / measured * 100.0) if measured else 0.0
        lines.append(
            f"| {record.name} | {record.status} | "
            f"{record.duration_ms / 1000:.2f} s | {share:.1f}% |"
        )
    top = ", ".join(f"{item.name} {item.duration_ms / 1000:.1f}s" for item in slowest(rows))
    if top:
        lines += ["", f"Slowest: {top}."]
    return lines


def payload(stages: Iterable[Any], *, run: str = "") -> dict[str, Any]:
    return {
        "schema": PROFILE_SCHEMA,
        "run": run,
        "total_ms": total_ms(stages),
        "stages": [
            {"name": item.name, "status": item.status, "duration_ms": item.duration_ms}
            for item in records(stages)
        ],
    }


def write_profile(path: str | Path, stages: Iterable[Any], *, run: str = "") -> Path:
    target = Path(path)
    if target.is_dir():
        target = target / PROFILE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload(stages, run=run), indent=2) + "\n", encoding="utf-8")
    return target


def read_profile(path: str | Path) -> list[StageRecord]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stages = raw.get("stages", []) if isinstance(raw, dict) else raw
    return [StageRecord.from_mapping(item) for item in stages if isinstance(item, dict)]


def compare(
    baseline: Sequence[StageRecord], current: Sequence[StageRecord]
) -> list[StageDelta]:
    """Per-stage deltas in current-run order; a stage missing from one side reads as 0 ms."""
    before = {item.name: item.duration_ms for item in baseline}
    after = {item.name: item.duration_ms for item in current}
    return [
        StageDelta(name, before.get(name, 0), after[name])
        for name in after
    ] + [
        StageDelta(name, before[name], 0)
        for name in before
        if name not in after
    ]


def regressions(
    baseline: Sequence[StageRecord],
    current: Sequence[StageRecord],
    *,
    min_ratio: float = REGRESSION_MIN_RATIO,
    min_delta_ms: int = REGRESSION_MIN_DELTA_MS,
) -> list[StageDelta]:
    """Stages that got materially slower, biggest absolute jump first."""
    return sorted(
        (
            delta
            for delta in compare(baseline, current)
            if delta.delta_ms >= min_delta_ms and delta.ratio >= min_ratio
        ),
        key=lambda delta: -delta.delta_ms,
    )


def improvements(
    baseline: Sequence[StageRecord],
    current: Sequence[StageRecord],
    *,
    min_delta_ms: int = REGRESSION_MIN_DELTA_MS,
) -> list[StageDelta]:
    return sorted(
        (
            delta
            for delta in compare(baseline, current)
            if -delta.delta_ms >= min_delta_ms
        ),
        key=lambda delta: delta.delta_ms,
    )


def format_comparison(
    baseline: Sequence[StageRecord], current: Sequence[StageRecord]
) -> list[str]:
    lines = [
        f"{'stage':<16}{'before':>10}{'after':>10}{'delta':>10}",
        "-" * 46,
    ]
    for delta in compare(baseline, current):
        lines.append(
            f"{delta.name:<16}{delta.before_ms / 1000:>9.2f}s"
            f"{delta.after_ms / 1000:>9.2f}s{delta.delta_ms / 1000:>+9.2f}s"
        )
    lines += [
        "",
        f"total {total_ms(baseline) / 1000:.2f}s -> {total_ms(current) / 1000:.2f}s",
    ]
    return lines
