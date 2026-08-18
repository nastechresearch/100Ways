"""Run baseline-aware branded-candidate tests with serial failure confirmation.

The full candidate suite may execute test files in parallel.  This runner treats
that pass only as a discovery pass: every reported failure is rerun serially in
the candidate environment.  Confirmed failures are then mapped to exact source
tests, which are rerun serially in the fresh source environment.  Thus, only a
confirmed candidate failure that reproduces on the matching source test can be
classified as inherited.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Sequence

from .integrity import tree_digest
from .test_baseline import (
    classify_test_logs,
    failed_nodeids,
    resolve_source_nodeids,
)


def _python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _environment(root: Path, *, branded: bool) -> dict[str, str]:
    env = dict(os.environ)
    python_dir = str(_python(root).parent)
    env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
    env["OPENROUTER_API_KEY"] = ""
    env["OPENAI_API_KEY"] = ""
    if branded:
        env["NASTECH_API_KEY"] = ""
    else:
        env["HERMES_API_KEY"] = ""
        env["HERMES_PYTHON"] = str(_python(root))
    return env


def _run(command: Sequence[str], *, cwd: Path, log: Path, env: dict[str, str]) -> int:
    with log.open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    return completed.returncode


def run_baseline_aware_tests(
    *,
    candidate_root: str | Path,
    source_root: str | Path,
    output: str | Path,
    candidate_raw_log: str | Path,
    candidate_confirmed_log: str | Path,
    source_log: str | Path,
    source_sha: str,
    candidate_artifact: str | Path | None = None,
) -> dict[str, object]:
    """Run full candidate discovery plus serial source-baseline confirmation."""
    candidate = Path(candidate_root)
    source = Path(source_root)
    raw_log = Path(candidate_raw_log)
    confirmed_log = Path(candidate_confirmed_log)
    source_log_path = Path(source_log)
    candidate_python = _python(candidate)
    source_python = _python(source)
    if not candidate_python.is_file() or not source_python.is_file():
        raise RuntimeError("candidate and source locked test environments are required")

    raw_exit = _run(
        ("./scripts/run_tests.sh",),
        cwd=candidate,
        log=raw_log,
        env=_environment(candidate, branded=True),
    )
    raw_text = raw_log.read_text(encoding="utf-8", errors="replace")
    raw_failed = failed_nodeids(raw_text)

    if raw_exit == 0:
        confirmed_log.write_text("candidate full suite passed; serial retry not needed\n", encoding="utf-8")
        source_log_path.write_text("source comparison not needed; candidate full suite passed\n", encoding="utf-8")
        report = classify_test_logs("", "", candidate_exit_code=0, source_exit_code=0)
        mappings: tuple[tuple[str, str], ...] = ()
        unresolved: tuple[str, ...] = ()
        confirmed_exit = 0
        source_exit = 0
    elif not raw_failed:
        confirmed_log.write_text("candidate runner failed without parseable pytest node IDs\n", encoding="utf-8")
        source_log_path.write_text("source comparison unavailable for unclassified candidate failure\n", encoding="utf-8")
        report = classify_test_logs(raw_text, "", candidate_exit_code=raw_exit, source_exit_code=0)
        mappings = ()
        unresolved = ()
        confirmed_exit = raw_exit
        source_exit = 0
    else:
        confirmed_exit = _run(
            (str(candidate_python), "-m", "pytest", "-q", *raw_failed),
            cwd=candidate,
            log=confirmed_log,
            env=_environment(candidate, branded=True),
        )
        confirmed_text = confirmed_log.read_text(encoding="utf-8", errors="replace")
        confirmed_failed = failed_nodeids(confirmed_text)
        mappings, unresolved = resolve_source_nodeids(
            confirmed_failed,
            source_root=source,
            python=str(source_python),
        )
        source_nodeids = tuple(source_nodeid for _, source_nodeid in mappings)
        if source_nodeids:
            source_exit = _run(
                (str(source_python), "-m", "pytest", "-q", *source_nodeids),
                cwd=source,
                log=source_log_path,
                env=_environment(source, branded=False),
            )
        else:
            source_exit = 0
            source_log_path.write_text(
                "no exact source test IDs resolved for confirmed candidate failures\n",
                encoding="utf-8",
            )
        source_text = source_log_path.read_text(encoding="utf-8", errors="replace")
        report = classify_test_logs(
            confirmed_text,
            source_text,
            candidate_exit_code=confirmed_exit,
            source_exit_code=source_exit,
        )

    evidence = report.to_dict()
    evidence.update(
        {
            "gate": "PASS" if report.review_ready else "FAIL",
            "source_sha": source_sha,
            "candidate_runner": "scripts/run_tests.sh",
            "source_runner": "serial pytest exact mapped node IDs",
            "raw_candidate_exit_code": raw_exit,
            "parallel_candidate_failures": list(raw_failed),
            "serial_candidate_exit_code": confirmed_exit,
            "source_test_mappings": [
                {"candidate": candidate_nodeid, "source": source_nodeid}
                for candidate_nodeid, source_nodeid in mappings
            ],
            "unresolved_candidate_failures": list(unresolved),
            "candidate_tree_sha256": tree_digest(Path(candidate_artifact) if candidate_artifact else candidate),
        }
    )
    Path(output).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--candidate-artifact")
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-raw-log", required=True)
    parser.add_argument("--candidate-confirmed-log", required=True)
    parser.add_argument("--source-log", required=True)
    args = parser.parse_args(argv)
    evidence = run_baseline_aware_tests(
        candidate_root=args.candidate,
        source_root=args.source,
        output=args.output,
        candidate_raw_log=args.candidate_raw_log,
        candidate_confirmed_log=args.candidate_confirmed_log,
        source_log=args.source_log,
        source_sha=args.source_sha,
        candidate_artifact=args.candidate_artifact,
    )
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
