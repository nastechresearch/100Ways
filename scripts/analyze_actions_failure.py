#!/usr/bin/env python3
"""Render a redacted, non-authorizing report for a failed Actions step."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hundredways.actions_analyzer import analyze_decision, analyze_failure  # noqa: E402
from hundredways.ai import AIEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", type=Path)
    source.add_argument("--decision", type=Path)
    parser.add_argument("--step", default="unknown")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--ai-advice",
        action="store_true",
        help="append redacted advisory-only AI remediation guidance when configured",
    )
    args = parser.parse_args()

    evidence = ""
    if args.decision:
        evidence = args.decision.read_text(encoding="utf-8", errors="replace")
        report = analyze_decision(json.loads(evidence))
    else:
        evidence = args.log.read_text(encoding="utf-8", errors="replace")
        report = analyze_failure(evidence, step=args.step)
    payload = report.to_dict()
    if args.ai_advice:
        payload["ai_advice"] = AIEngine().advise_failure(report, evidence)
    payload["step"] = args.step
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
