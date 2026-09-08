"""Policy decisions for automatic upstream durability checks."""
from __future__ import annotations

from dataclasses import dataclass
import argparse

DAILY_SECONDS = 24 * 60 * 60
BURST_START_COMMITS = 500
BURST_STEP_COMMITS = 50


@dataclass(frozen=True)
class DurabilityDecision:
    run: bool
    burst: bool
    reason: str


def decide(*, commits_since_base: int, seconds_since_check: int, burst: bool) -> DurabilityDecision:
    """Decide whether the scheduled worker should invoke the gated sync pipeline."""
    commits = max(0, commits_since_base)
    elapsed = max(0, seconds_since_check)
    if burst:
        if commits >= BURST_STEP_COMMITS:
            return DurabilityDecision(True, True, "burst threshold reached")
        return DurabilityDecision(False, True, "waiting for 50 new upstream commits")
    if commits >= BURST_START_COMMITS:
        return DurabilityDecision(True, True, "500-commit burst mode activated")
    if elapsed >= DAILY_SECONDS:
        return DurabilityDecision(True, False, "24-hour durability check due")
    return DurabilityDecision(False, False, "daily durability check not due")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--commits", type=int, required=True)
    parser.add_argument("--elapsed", type=int, required=True)
    parser.add_argument("--burst", choices=("0", "1"), default="0")
    args = parser.parse_args()
    decision = decide(
        commits_since_base=args.commits,
        seconds_since_check=args.elapsed,
        burst=args.burst == "1",
    )
    print(f"run={'true' if decision.run else 'false'}")
    print(f"burst={'true' if decision.burst else 'false'}")
    print(f"reason={decision.reason}")


if __name__ == "__main__":
    main()
