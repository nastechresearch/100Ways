#!/usr/bin/env python3
"""Build the public, secret-free status payload consumed by GitHub Pages."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load(path: str | None, default: dict[str, Any]) -> dict[str, Any]:
    if not path:
        return default
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value if isinstance(value, dict) else default


def build(decision: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    threshold = int(decision.get("threshold", 50) or 50)
    pending = int(decision.get("pending_commits", 0) or 0)
    state = str(decision.get("status", "warming")).replace("-", " ").upper()
    gates = context.get("gates")
    if not isinstance(gates, list):
        gates = [
            ["Direct Hermes fetch", "RECORDED"],
            ["Source delta audit", "RECORDED"],
            ["Branding and owned assets", "RECORDED"],
            ["Final tree conformance", "RECORDED"],
            ["Candidate test suite", "RECORDED"],
            ["#344 review PR", "HUMAN REVIEW"],
        ]
    history = context.get("history")
    if not isinstance(history, list):
        history = []
    return {
        "schema": "100ways.public-status.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "stateDetail": {
            "warming": "Full verification is held until the commit threshold is reached.",
            "THRESHOLD REACHED": "The full verification chain is authorized to start.",
            "CURRENT": "No new upstream commits require a sync.",
            "AWAITING REVIEW": "An open candidate exists; no new sync is started.",
        }.get(state, "Status recorded by 100Ways."),
        "pending": pending,
        "threshold": threshold,
        "remaining": max(0, threshold - pending),
        "verified": context.get("verified", "Latest monitor run"),
        "runUrl": context.get("runUrl", "https://github.com/nastechresearch/100Ways/actions"),
        "upstreamSha": str(decision.get("upstream_sha", ""))[:12],
        "baselineSha": str(
            decision.get("merged_baseline_sha", decision.get("baseline_sha", ""))
        )[:12],
        "candidateSha": str(decision.get("candidate_baseline_sha", ""))[:12] or "—",
        "syncState": "ENABLED" if decision.get("trigger_sync") else "ON HOLD",
        "gates": [
            [str(item[0]), str(item[1])]
            for item in gates
            if isinstance(item, list) and len(item) >= 2
        ][:12],
        "history": [
            {"title": str(item.get("title", "Event")), "detail": str(item.get("detail", ""))}
            for item in history
            if isinstance(item, dict)
        ][-12:],
        "privacy": (
            "Public sanitized evidence only; no secrets, tokens, chat IDs, or raw private logs."
        ),
        "publication": "Review PR only. No automatic merge, tag, release, or deployment.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", required=True)
    parser.add_argument("--context")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build(_load(args.decision, {}), _load(args.context, {}))
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
