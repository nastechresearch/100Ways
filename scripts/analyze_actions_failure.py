#!/usr/bin/env python3
"""Render a redacted, non-authorizing report for a failed Actions step."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hundredways.actions_analyzer import analyze_failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--step", default="unknown")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    report = analyze_failure(args.log.read_text(encoding="utf-8", errors="replace"), step=args.step)
    payload = report.to_dict()
    payload["step"] = args.step
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
