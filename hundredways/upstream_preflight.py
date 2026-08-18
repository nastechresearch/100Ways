"""Fail-closed direct-upstream test preflight for 100Ways candidate builds.

A candidate is never branded from a source revision that has not first passed that
source's canonical test runner in a clean, direct clone.  The preflight does not
patch, retry, or otherwise alter source code.  It prepares an isolated external
virtual environment when the source is a uv project, executes the repository's
canonical ``scripts/run_tests.sh`` runner, and verifies that the source HEAD and
tracked files remain unchanged.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

_CANONICAL_RUNNER = Path("scripts/run_tests.sh")
_SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
_TEST_EXTRAS = (
    "all",
    "dev",
    "anthropic",
    "mistral",
    "fal",
    "modal",
    "daytona",
    "hindsight",
    "parallel-web",
)
_MAX_OUTPUT_CHARS = 4_000


@dataclass(frozen=True)
class UpstreamPreflightReport:
    """Evidence that a direct source revision was test-validated before branding."""

    source_sha: str
    source_files: int
    runner: str
    command: tuple[str, ...]
    environment_prepared: bool
    duration_ms: int
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class UpstreamPreflightError(RuntimeError):
    """Raised when a direct source cannot prove a clean canonical test pass."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
    )


def _detail(completed: subprocess.CompletedProcess[str]) -> str:
    value = (completed.stderr or completed.stdout or "").strip()
    return value[-_MAX_OUTPUT_CHARS:] or f"command exited {completed.returncode}"


def _source_file_count(root: Path) -> int:
    return sum(
        1
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )


def _git(root: Path, *args: str) -> str:
    completed = _run(("git", "-C", str(root), *args), cwd=root)
    if completed.returncode:
        raise UpstreamPreflightError(f"source Git evidence failed: {_detail(completed)}")
    return completed.stdout.strip()


def _prepare_uv_environment(root: Path) -> tuple[dict[str, str], Path]:
    """Prepare a temporary external test environment for a locked uv project."""
    uv = shutil.which("uv")
    if not uv:
        raise UpstreamPreflightError(
            "canonical upstream test environment requires uv, but uv is unavailable"
        )
    temporary_root = Path(tempfile.mkdtemp(prefix="100ways-upstream-preflight-"))
    environment = temporary_root / "venv"
    setup_env = dict(os.environ)
    setup_env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    command = [uv, "sync", "--locked", "--python", "3.11"]
    command.extend(
        argument for extra in _TEST_EXTRAS for argument in ("--extra", extra)
    )
    completed = _run(command, cwd=root, env=setup_env)
    if completed.returncode:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise UpstreamPreflightError(
            f"upstream test environment setup failed: {_detail(completed)}"
        )
    python = environment / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if not python.is_file():
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise UpstreamPreflightError(
            "upstream test environment setup did not create a Python executable"
        )
    test_env = dict(os.environ)
    test_env["HERMES_PYTHON"] = str(python)
    return test_env, temporary_root


def run_upstream_preflight(
    source_root: str | Path,
    *,
    expected_sha: str = "",
    command: Sequence[str] | None = None,
) -> UpstreamPreflightReport:
    """Run the canonical direct-source test suite before branding may begin.

    The default command is the tracked canonical runner.  A command override is
    solely a deterministic test seam for local unit tests; production callers
    use the default and therefore require ``scripts/run_tests.sh``.
    """
    root = Path(source_root)
    if not root.is_dir():
        raise UpstreamPreflightError("direct upstream clone is unavailable")
    source_sha = _git(root, "rev-parse", "HEAD")
    if expected_sha and (
        not _SHA_PATTERN.fullmatch(expected_sha) or source_sha != expected_sha
    ):
        raise UpstreamPreflightError(
            "direct upstream HEAD does not match the recorded source revision"
        )
    runner = root / _CANONICAL_RUNNER
    if command is None:
        if not runner.is_file() or not os.access(runner, os.X_OK):
            raise UpstreamPreflightError(
                "direct upstream lacks an executable canonical scripts/run_tests.sh runner"
            )
        test_command = (str(runner),)
    else:
        test_command = tuple(command)
        if not test_command:
            raise UpstreamPreflightError("upstream preflight command is empty")
    before_files = _source_file_count(root)
    before_status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if before_status:
        raise UpstreamPreflightError(
            "fresh direct upstream clone has modified tracked files before testing"
        )

    environment_prepared = False
    temporary_root: Path | None = None
    test_env = dict(os.environ)
    runner_needs_python_tests = False
    if command is None:
        try:
            runner_needs_python_tests = "pytest" in runner.read_text(encoding="utf-8")
        except OSError as exc:
            raise UpstreamPreflightError(
                f"cannot read canonical upstream runner: {exc}"
            ) from exc
    if (
        command is None
        and runner_needs_python_tests
        and (root / "pyproject.toml").is_file()
        and (root / "uv.lock").is_file()
    ):
        test_env, temporary_root = _prepare_uv_environment(root)
        environment_prepared = True

    started = time.monotonic()
    try:
        completed = _run(test_command, cwd=root, env=test_env)
    finally:
        duration_ms = int((time.monotonic() - started) * 1_000)
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)

    if completed.returncode:
        raise UpstreamPreflightError(
            f"canonical upstream tests failed: {_detail(completed)}"
        )
    if _git(root, "rev-parse", "HEAD") != source_sha:
        raise UpstreamPreflightError(
            "direct upstream HEAD changed while canonical tests were running"
        )
    after_status = _git(root, "status", "--porcelain", "--untracked-files=no")
    if after_status:
        raise UpstreamPreflightError(
            "canonical upstream tests modified tracked source files"
        )
    after_files = _source_file_count(root)
    if after_files != before_files:
        raise UpstreamPreflightError(
            "canonical upstream tests changed the direct source file census"
        )
    return UpstreamPreflightReport(
        source_sha=source_sha,
        source_files=before_files,
        runner=_CANONICAL_RUNNER.as_posix() if command is None else "unit-test override",
        command=test_command,
        environment_prepared=environment_prepared,
        duration_ms=duration_ms,
        passed=True,
        detail="canonical direct upstream test suite passed before branding",
    )
