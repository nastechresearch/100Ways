#!/usr/bin/env python3
"""Emit a bounded Ollama advisory for a failed 100Ways workflow step.

The deterministic remediation classifier chooses the category and any allowed
low-risk action.  Ollama only explains that already-fixed decision.  This
command does not invoke recovery, alter a repository, approve a retry, or
publish anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from hundredways.ai import AIEngine
from hundredways.remediation import classify_failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True)
    parser.add_argument("--source-is-shallow", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    try:
        evidence = Path(args.log).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        evidence = f"failure log unavailable: {error}"
    decision = classify_failure(evidence, source_is_shallow=args.source_is_shallow)
    advisory = AIEngine().advise_remediation(decision)
    body = {
        "schema": "100ways.remediation-advisory/v1",
        "advisory_only": True,
        "execution_authority": False,
        "publication_authority": False,
        "decision": decision.to_dict(),
        "ollama_advice": advisory,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(body, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
