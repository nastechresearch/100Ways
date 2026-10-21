"""Run the FORK's own static checks against a candidate before the engine publishes it.

``nastech-agent`` re-lints every PR the engine opens.  ``.github/workflows/lint.yml``
job ``windows-footguns`` runs six first-party checkers plus a blocking
``ruff check .`` configured by the fork's own ``pyproject.toml``, and the engine's lint
never sees any of it: it lints engine code, not the tree the fork is about to be asked
to merge.  That gap is the three reds that keep landing *after* the engine declared
itself green -- a fork checker the engine never ran, a char-count cap the brand
transform alone pushes past (``hermes`` -> ``nastech`` is +1 char per token, so the
``AGENTS.md`` + ``gateway/AGENTS.md`` chain upstream keeps 44 chars under
``CHAIN_MAX_CHARS`` is red here), and a ``subprocess.run`` block the engine *generates*
into fork code (PR #130).

The contract this module holds:

* Only the six invocations named in :data:`FORK_CHECK_SCRIPTS` run, and their
  arguments and advisory status are read out of the fork's ``lint.yml`` instead of
  remembered here -- so a fork step that gains ``continue-on-error: true`` stops
  blocking the gate without an engine edit, and a checker the workflow no longer
  invokes is reported rather than guessed at.
* Every checker runs exactly as CI runs it.  All six resolve their repository root
  from ``__file__`` and two of them enumerate the tree with ``git ls-files``, so no
  invocation can be aimed at a foreign directory: the candidate is staged as a
  disposable checkout with the FORK's copy of each script written over the
  candidate's, which is what makes the fork's rule set the thing that executes.
* A checker that cannot run is an :data:`UNAVAILABLE` outcome and is blocking.  A
  simulation that quietly skips is indistinguishable from a green fork.
* Nothing here loosens a fork guard: caps, ``select`` lists, per-file ignores and
  baselines are read from the fork and handed to its own tools untouched.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from .rules import BrandingRules
from .secrets import redact

FORK_LINT_WORKFLOW = ".github/workflows/lint.yml"
FORK_PYPROJECT = "pyproject.toml"

# The six ``run:`` steps of lint.yml job ``windows-footguns``, in the workflow's order.
# The other two steps in that job (``scripts/check_profile_scope_patterns.py``,
# ``scripts/ci/check_public_surface.py``) already carry ``continue-on-error: true`` and
# take ``--base``/``--head`` refs of the open PR, so they describe a diff a candidate
# does not have yet; they are absent from this list on purpose rather than approximated.
FORK_CHECK_SCRIPTS: tuple[str, ...] = (
    "scripts/check-windows-footguns.py",
    "scripts/check_bash_shebangs.py",
    "scripts/check_no_tmp_literals.py",
    "scripts/check_config_yaml_writers.py",
    "scripts/ci/check_os_marker_fakes.py",
    "scripts/ci/check_agents_md_size.py",
)

# The chain-cap checker this module predicts, and the two files that own its numbers.
AGENTS_SIZE_CHECKER = "scripts/ci/check_agents_md_size.py"
SUBDIRECTORY_HINTS = "agent/subdirectory_hints.py"
ROOT_AGENTS = "AGENTS.md"

# Branding only ever lengthens text, so a chain under its cap by less than one brand
# delta's worth of slack is as good as red.  The largest measured single-chain brand
# delta on this fork is 66 chars (``AGENTS.md`` + ``gateway/AGENTS.md``, PR #159) and
# that same chain grew 44 chars in one upstream week, so 512 is roughly 8x the delta it
# has to outrun and 1.7% of the 30k chain cap: wide enough to warn before the PR opens,
# narrow enough that a file with real headroom never appears in the report.
BRAND_MARGIN_CHARS = 512

# Each checker gets the budget of the job that runs it, and no more: a run that exceeds
# it is unavailable rather than given longer, because a hung simulation reads as a pass
# to everything downstream of it.  300s is ``lint.yml:224 timeout-minutes: 5`` for job
# ``windows-footguns`` (and the same for ``ruff-blocking``), measured against this
# fork's own tree: staging 69s and the six checkers between 0.3s and 77s, so the budget
# is the job's, not a guess padded to hide a slow checker.
CHECKER_TIMEOUT_S = 300
RUFF_TIMEOUT_S = 300
GIT_TIMEOUT_S = 300

# Per checker, per stream.  Output past the cap is still counted, never dropped.
OUTPUT_CAP_CHARS = 4_000
FINDINGS_CAP_PER_SCRIPT = 40

FINDING = "finding"
UNAVAILABLE = "unavailable"

# The three classes of the fork-ci-triage dossier, spelled exactly as the skill spells
# them (SKILL.md step 2, references/classes.md), so a report written here triages into
# the same owner it would be assigned by hand.  A static pre-flight can only ever emit
# the last two: runner capacity is wall-clock pressure on a hosted image -- the same
# tree lints identically there and here -- so nothing in this report may carry that
# class, and a capacity red can never be blamed on the transform from inside it.
RUNNER_CAPACITY = "runner capacity"
BRAND_INFLATION = "brand inflation"
GENUINE_DIVERGENCE = "genuine divergence"

_STAGED_IGNORES = (".git", "node_modules", "__pycache__", ".venv", "venv")


class ForkLintError(ValueError):
    """The fork's lint contract cannot be read, so no candidate can be simulated.

    A ``ValueError`` on purpose (see :class:`hundredways.updates.AgentsChainCapError`):
    it is reported as an unavailable, and unavailable is blocking, so a caller that
    records issues instead of aborting still stops the publication -- the alternative,
    publishing a tree nobody linted and calling it green, is the failure this module
    exists to prevent.
    """


@dataclass(frozen=True)
class ForkLintIssue:
    """One outcome: a checker finding, a predicted cap breach, or an unavailable
    checker.  ``blocking`` is False only for what the fork itself declares advisory.

    ``classification`` is the fork-ci-triage class of the fix the issue calls for.
    It is "" for issues that make no claim about a fork red at all -- an unavailable
    checker or the ruff-version note -- because triaging those starts with making
    the check run, not with assigning it a class.
    """

    script: str
    path: str
    detail: str
    blocking: bool
    status: str = FINDING
    classification: str = ""


@dataclass(frozen=True)
class ForkLintSummary:
    """What the simulation is worth: whether it ran, and what it said."""

    issues: tuple[ForkLintIssue, ...]

    @property
    def blocking(self) -> tuple[ForkLintIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def advisory(self) -> tuple[ForkLintIssue, ...]:
        return tuple(issue for issue in self.issues if not issue.blocking)

    @property
    def unavailable(self) -> tuple[ForkLintIssue, ...]:
        return tuple(issue for issue in self.issues if issue.status == UNAVAILABLE)

    @property
    def passed(self) -> bool:
        """True only when every checker ran and none of them said stop."""
        return not self.blocking

    @property
    def verdict(self) -> str:
        if self.blocking:
            return "FAIL"
        if self.unavailable:
            return "NOT SIMULATED"
        return "PASS"

    def line(self) -> str:
        return (
            f"fork-lint {self.verdict}: {len(self.blocking)} blocking, "
            f"{len(self.advisory)} advisory, {len(self.unavailable)} unavailable"
        )


def summarize(issues: list[ForkLintIssue] | tuple[ForkLintIssue, ...]) -> ForkLintSummary:
    """Collapse a report into the one line the gate, the log and the summary share."""
    return ForkLintSummary(tuple(issues))


def report_lines(issues: list[ForkLintIssue] | tuple[ForkLintIssue, ...]) -> list[str]:
    """The human-readable form of a report, for a gate report or a CI log line."""
    return [
        f"  [{'BLOCKING' if issue.blocking else 'advisory'}] {issue.script}"
        f"{f' {issue.path}' if issue.path else ''}: {issue.detail}"
        for issue in issues
    ]


# ---------------------------------------------------------------------------
# The workflow, read instead of remembered
# ---------------------------------------------------------------------------

_STEP_START_RE = re.compile(r"^(?P<indent>[ ]*)-[ \t]+[A-Za-z_-]+:")
# The fork's advisory steps write ``continue-on-error: true`` under a ``- name:`` that
# opened the item, but GitHub accepts step keys in any order and a future demotion can
# put the key first, so the item's opening line counts too and its dash is optional.
_CONTINUE_ON_ERROR_RE = re.compile(
    r"^[ \t]*(?:-[ \t]+)?continue-on-error:[ \t]*true[ \t]*(?:#.*)?$"
)


@dataclass(frozen=True)
class _WorkflowStep:
    args: tuple[str, ...]
    advisory: bool


def _step_blocks(workflow: str) -> list[tuple[int, int]]:
    """``(start, end)`` line spans of every step in the workflow.

    A step is one sequence item, and GitHub accepts its keys in any order -- the fork
    today opens advisory steps with ``- name:`` and carries ``continue-on-error: true``
    under it, but a demotion that reorders the keys must not change what this reads.
    So a block starts at the line that opens the item, whatever its first key is, and
    ends at the next item of the same or shallower indentation.
    """
    lines = workflow.splitlines()
    indents = {index: len(_STEP_START_RE.match(line).group("indent"))
               for index, line in enumerate(lines) if _STEP_START_RE.match(line)}
    starts = sorted(indents)
    blocks: list[tuple[int, int]] = []
    for position, start in enumerate(starts):
        end = len(lines)
        for candidate in starts[position + 1:]:
            if indents[candidate] <= indents[start]:
                end = candidate
                break
        blocks.append((start, end))
    return blocks


def _workflow_step(workflow: str, script: str) -> _WorkflowStep | None:
    """The step that invokes ``python <script> [args...]``, with its advisory status.

    Both the argument list and ``continue-on-error`` come from the fork's own
    ``lint.yml``, so the simulation cannot drift from the job it is simulating -- and
    a step that gains ``continue-on-error: true`` stops blocking the gate with no
    engine edit, which is the fork demoting its own check, not this module.
    """
    lines = workflow.splitlines()
    invocation = re.compile(rf"python3?[ \t]+{re.escape(script)}(?P<args>(?:[ \t]+\S+)*)[ \t]*$")
    for start, end in _step_blocks(workflow):
        for index in range(start, end):
            match = invocation.search(lines[index])
            if match:
                return _WorkflowStep(
                    args=tuple(match.group("args").split()),
                    advisory=any(
                        _CONTINUE_ON_ERROR_RE.match(line) for line in lines[start:end]
                    ),
                )
    return None


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Staging: the candidate, as a checkout
# ---------------------------------------------------------------------------

def _link_or_copy(source: str, destination: str, *, follow_symlinks: bool = True) -> None:
    """Hardlink the tree when staging shares its filesystem, copy when it does not."""
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination, follow_symlinks=follow_symlinks)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, errors="replace",
        timeout=GIT_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
    )


@contextlib.contextmanager
def _staged_checkout(candidate_root: str) -> str:
    """A disposable git checkout of the candidate, for checkers that require one.

    ``scripts/ci/check_agents_md_size.py`` and ``scripts/check_bash_shebangs.py``
    enumerate the tree through ``git ls-files``, and every checker derives its
    repository root from ``__file__`` -- so the only way to run the fork's invocation
    unmodified is to give it a checkout of the tree under review.  The snapshot the
    pipeline writes is not one and must not become one
    (:mod:`hundredways.prepublish` rejects ``.git`` in a candidate), so this staging
    directory exists only for the duration of the simulation and never ships.
    """
    staging = tempfile.mkdtemp(prefix="100ways-forklint-")
    tree = os.path.join(staging, "tree")
    try:
        shutil.copytree(
            candidate_root, tree, copy_function=_link_or_copy, symlinks=False,
            ignore=shutil.ignore_patterns(*_STAGED_IGNORES),
        )
        for command in (["git", "init", "-q", tree], ["git", "-C", tree, "add", "-A"]):
            result = _git(command, tree)
            if result.returncode:
                raise OSError(f"{' '.join(command[:3])} failed: {result.stderr.strip()[:200]}")
        yield tree
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# The checkers
# ---------------------------------------------------------------------------

_TRACEBACK_RE = re.compile(r"^Traceback \(most recent call last\)", re.MULTILINE)
_FINDING_RE = re.compile(r"^(?P<path>[^\s:][^:]*?):(?P<line>\d+):[ \t]*(?P<rest>.*\S)?[ \t]*$")
_CONTINUATION_LINE_RE = re.compile(r"^[ \t]+\S")


def _unavailable(script: str, path: str, detail: str) -> ForkLintIssue:
    """A checker that produced no verdict, which is never a pass."""
    return ForkLintIssue(script=script, path=path, detail=f"unavailable: {detail}",
                         blocking=True, status=UNAVAILABLE)


def _findings(script: str, output: str, advisory: bool) -> list[ForkLintIssue]:
    """The checker's finding lines, each carrying the indented detail under it.

    Every one of the six prints ``<path>:<line>: <finding>``, with the Windows-footgun
    checker adding an indented rule/message/fix block per hit.  Output that matches no
    finding shape at all is reported whole (truncated) instead of being discarded: a
    checker that exits 1 with prose still said something.
    """
    issues: list[ForkLintIssue] = []
    prose: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        match = _FINDING_RE.match(line)
        if match and len(issues) < FINDINGS_CAP_PER_SCRIPT:
            rest = (match.group("rest") or "").strip()
            issues.append(ForkLintIssue(
                script=script,
                path=match.group("path"),
                detail=redact(f"line {match.group('line')}: {rest}")[:OUTPUT_CAP_CHARS],
                blocking=not advisory,
                # A fork checker firing on the candidate is the fork's own rule saying no
                # to this tree: triage class 3, owned by the sync or by the engine rule
                # that generated the text -- never by the cap the checker reads.
                classification=GENUINE_DIVERGENCE,
            ))
        elif issues and _CONTINUATION_LINE_RE.match(line):
            tail = issues[-1]
            issues[-1] = ForkLintIssue(
                script=tail.script, path=tail.path,
                detail=f"{tail.detail} | {redact(line.strip())}"[:OUTPUT_CAP_CHARS],
                blocking=tail.blocking, status=tail.status,
                classification=tail.classification,
            )
        else:
            prose.append(line)
    if not issues:
        return [ForkLintIssue(
            script=script, path="",
            detail=redact("\n".join(prose))[:OUTPUT_CAP_CHARS] or "exit 1 with no output",
            blocking=not advisory, classification=GENUINE_DIVERGENCE,
        )]
    skipped = sum(1 for line in output.splitlines() if _FINDING_RE.match(line)) - len(issues)
    if skipped > 0:
        issues.append(ForkLintIssue(
            script=script, path="",
            detail=(
                f"{skipped} further finding line(s) beyond the "
                f"{FINDINGS_CAP_PER_SCRIPT}-finding cap"
            ),
            blocking=not advisory, classification=GENUINE_DIVERGENCE,
        ))
    return issues


def _run_checker(fork_root: str, script: str, workflow: str, tree: str) -> list[ForkLintIssue]:
    """One fork checker, invoked the way the fork's own ``lint.yml`` invokes it."""
    step = _workflow_step(workflow, script)
    if step is None:
        return [_unavailable(script, FORK_LINT_WORKFLOW,
                             f"{fork_root}/{FORK_LINT_WORKFLOW} invokes no {script}, so the "
                             "allow-list no longer mirrors the job")]
    fork_script = os.path.join(fork_root, *script.split("/"))
    if not os.path.isfile(fork_script):
        return [_unavailable(script, script, f"the fork checkout has no {fork_script}")]
    staged_script = os.path.join(tree, *script.split("/"))
    try:
        os.makedirs(os.path.dirname(staged_script), exist_ok=True)
        if os.path.lexists(staged_script):
            os.remove(staged_script)  # a hardlink: writing through it would edit the candidate
        shutil.copyfile(fork_script, staged_script)
    except OSError as exc:
        return [_unavailable(script, script,
                             f"cannot install the fork's checker into staging: {exc}")]
    command = [sys.executable, staged_script, *step.args]
    try:
        proc = subprocess.run(
            command, cwd=tree, capture_output=True, text=True, errors="replace",
            timeout=CHECKER_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [_unavailable(script, script, f"cannot run {script}: {exc}")]
    captured = f"{proc.stdout}\n{proc.stderr}".strip()
    if proc.returncode == 0:
        return []
    if _TRACEBACK_RE.search(proc.stderr.lstrip()):
        return [_unavailable(script, script,
                             f"crashed (exit {proc.returncode}): {captured[:OUTPUT_CAP_CHARS]}")]
    if proc.returncode != 1:
        return [_unavailable(script, script,
                             f"exit {proc.returncode}, outside the checker's documented 0 and 1: "
                             f"{captured[:OUTPUT_CAP_CHARS]}")]
    return _findings(script, captured, advisory=step.advisory)


# ---------------------------------------------------------------------------
# ruff, configured by the fork
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForkRuffConfig:
    """The one config file the fork's ``ruff check .`` reads, and what it enforces.

    ``config_text`` is that file's ruff-relevant text verbatim (a ``ruff.toml`` is
    copied whole; a ``pyproject.toml`` contributes its whole ``[tool.ruff]`` subtree,
    re-emitted as a ``ruff.toml``) rather than a reconstruction of the keys this module
    happens to look at, so a setting a selected rule depends on
    (``flake8-tidy-imports.banned-api`` is what gives ``TID251`` its teeth) cannot be
    dropped here and quietly narrow the fork's enforcement.
    """

    source: str
    preview: bool
    select: tuple[str, ...]
    per_file_ignores: tuple[tuple[str, tuple[str, ...]], ...]
    config_text: str


# ruff reads ONE config per run, and in a given directory it prefers ``ruff.toml`` over
# ``.ruff.toml`` over ``[tool.ruff]`` in ``pyproject.toml``.  Which of the three it is
# changes what is enforced: the fork's ``ruff.toml`` selects several hundred rules --
# including ``PLW1510``, the one that made PR #130 red -- while its ``pyproject.toml``
# lists six.  So this module discovers the file the fork's own command would resolve to
# instead of assuming a shape.
FORK_RUFF_CONFIGS = ("ruff.toml", ".ruff.toml", FORK_PYPROJECT)

_TABLE_RE = re.compile(r"^\[\s*(?P<table>[A-Za-z0-9_.\-]+)\s*\][ \t]*(?:#.*)?$")
_SELECT_RE = re.compile(
    r"^[ \t]*select[ \t]*=[ \t]*(?P<value>\[.*?\])[ \t]*(?:#.*)?$", re.MULTILINE | re.DOTALL
)
_PREVIEW_RE = re.compile(r"^[ \t]*preview[ \t]*=[ \t]*(?P<value>\S+)[ \t]*(?:#.*)?$", re.MULTILINE)
# TOML spells the same pattern in either quote style and ruff accepts both, so the
# quote is captured and matched by backreference rather than assumed.
_ENTRY_RE = re.compile(
    r"""^[\t ]*(?P<quote>["'])(?P<pattern>[^"']+)(?P=quote)[\t ]*=[\t ]*"""
    r"""\[(?P<body>[^\]]*)\]""",
    re.MULTILINE,
)
_CODE_RE = re.compile(r"\"([^\"]+)\"|'([^']+)'")
_RUFF_PIN_RE = re.compile(r"\"ruff(?P<specifier>==[0-9][^\"]*)\"")


def _codes(value: str) -> tuple[str, ...]:
    return tuple(code for pair in _CODE_RE.finditer(value) for code in pair.groups() if code)


def _toml_sections(text: str, prefix: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The ``(headerless body, tables)`` of one TOML subtree, tables named relative to it.

    ``prefix`` is ``"tool.ruff"`` for a ``pyproject.toml`` and ``""`` for a
    ``ruff.toml``, where the same shape is the whole file -- so ``[tool.ruff.lint]`` and
    ``[lint]`` both come back as ``("lint", ...)`` and one reader handles either.
    """
    top: list[str] = []
    tables: list[tuple[str, list[str]]] = []
    current: list[str] | None = top if not prefix else None
    for line in text.splitlines():
        table = _TABLE_RE.match(line.strip())
        if table:
            name = table.group("table")
            if prefix:
                inside = name == prefix or name.startswith(f"{prefix}.")
                relative = name[len(prefix) + 1:] if name.startswith(f"{prefix}.") else ""
            else:
                inside, relative = True, name
            if not inside:
                current = None
                continue
            if relative:
                current = []
                tables.append((relative, current))
            else:
                current = top
            continue
        if current is not None:
            current.append(line)
    return top, tables


def read_fork_ruff_config(fork_root: str) -> ForkRuffConfig:
    """Read the fork's ruff enforcement out of the file its own command resolves to.

    The first existing config in :data:`FORK_RUFF_CONFIGS` wins, exactly as it wins for
    ``ruff check .`` in that checkout.  Raises :class:`ForkLintError` when the fork
    declares no config file or no ``select`` list in it: with no list there is nothing
    to run, and calling that a pass is the silent skip this module exists to prevent --
    a fork that lints on ruff's defaults would have to be added here deliberately, not
    guessed at.
    """
    for name in FORK_RUFF_CONFIGS:
        path = os.path.join(fork_root, name)
        if not os.path.isfile(path):
            continue
        try:
            text = _read(path)
        except OSError as exc:
            raise ForkLintError(
                f"cannot read the fork's ruff configuration at {path}: {exc}"
            ) from exc
        top, tables = _toml_sections(text, "" if name != FORK_PYPROJECT else "tool.ruff")
        bodies = dict((relative, "\n".join(body)) for relative, body in tables)
        lint = bodies.get("lint", "")
        select = _SELECT_RE.search(lint) or _SELECT_RE.search("\n".join(top))
        if select is None:
            raise ForkLintError(
                f"{path} is the config the fork's ruff reads but names no select list"
            )
        preview = _PREVIEW_RE.search("\n".join(top)) or _PREVIEW_RE.search(lint)
        ignores = tuple(
            (entry.group("pattern"), _codes(entry.group("body")))
            for entry in _ENTRY_RE.finditer(bodies.get("lint.per-file-ignores", ""))
        )
        config_text = (text if name != FORK_PYPROJECT else "\n".join(
            ["\n".join(top)] + [f"[{relative}]\n{body}" for relative, body in tables]
        ))
        return ForkRuffConfig(
            source=name,
            preview=preview is not None and preview.group("value") == "true",
            select=_codes(select.group("value")),
            per_file_ignores=ignores,
            config_text=config_text,
        )
    raise ForkLintError(
        f"{fork_root} has none of {', '.join(FORK_RUFF_CONFIGS)}, "
        "so its ruff enforcement cannot be read"
    )


def read_fork_ruff_pin(fork_root: str) -> str:
    """The ruff version the fork installs in its own CI, or "" when it pins none.

    ruff's rule set moves between releases, so a simulation run on a different version
    can differ from the check the fork actually gates on; the pin is what says so.
    """
    try:
        text = _read(os.path.join(fork_root, FORK_PYPROJECT))
    except OSError:
        return ""
    match = _RUFF_PIN_RE.search(text)
    return match.group("specifier").removeprefix("==") if match else ""


def _ruff_executable() -> str:
    """ruff from the environment that runs the engine first, then from PATH."""
    sibling = os.path.join(os.path.dirname(sys.executable), "ruff")
    if os.path.isfile(sibling):
        return sibling
    return shutil.which("ruff") or ""


def _ruff_version(executable: str) -> str:
    """The version string of the ruff that ran, for the divergence note."""
    try:
        proc = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, errors="replace",
            timeout=CHECKER_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip().removeprefix("ruff ").split()[0] if proc.returncode == 0 else ""


def _version_divergence(fork_root: str, executable: str) -> ForkLintIssue | None:
    """Say out loud when the ruff that ran is not the ruff the fork pins.

    ruff's rule set moves between releases, so findings from another version are a
    weaker signal than the fork's own ``ruff check .``.  This does not skip the run and
    does not block on the difference -- the fork's CI installs the version the fork
    pins, so the divergence is a local-environment note, and a note is what it stays:
    reported, never passed off as the fork's answer.
    """
    pin = read_fork_ruff_pin(fork_root)
    found = _ruff_version(executable)
    if not pin or not found or found == pin:
        return None
    return ForkLintIssue(
        script="ruff", path="",
        detail=f"the fork pins ruff=={pin} and this run used {found}, so these ruff "
               "findings may differ from the fork's own",
        blocking=False,
    )


def _ruff_diagnostic_issues(candidate_root: str, stdout: str) -> list[ForkLintIssue]:
    try:
        diagnostics = json.loads(stdout)
    except ValueError as exc:
        return [_unavailable("ruff", "", f"ruff answered with something that is not JSON: {exc}")]
    root = os.path.abspath(candidate_root)
    issues: list[ForkLintIssue] = []
    for diagnostic in diagnostics if isinstance(diagnostics, list) else []:
        filename = str(diagnostic.get("filename", ""))
        relative = os.path.relpath(os.path.abspath(filename), root) if filename else ""
        location = diagnostic.get("location")
        location = location if isinstance(location, dict) else {}
        code = str(diagnostic.get("code") or diagnostic.get("kind") or "error")
        issues.append(ForkLintIssue(
            script="ruff",
            path=relative if not relative.startswith("..") else filename,
            detail=redact(f"{code} at line {location.get('row', '?')}: "
                          f"{diagnostic.get('message', '')}")[:OUTPUT_CAP_CHARS],
            blocking=True,
            # The fork's own selected rule fired on the candidate: class 3, and the
            # fix is the generated text -- never the select list or an ignore.
            classification=GENUINE_DIVERGENCE,
        ))
    return issues[:FINDINGS_CAP_PER_SCRIPT]


def run_fork_ruff(fork_root: str, candidate_root: str) -> list[ForkLintIssue]:
    """``ruff check`` over the candidate, configured by the fork's own config file.

    Run with ``cwd`` at the candidate because ruff resolves ``per-file-ignores``
    patterns against the paths of the files as seen from the process directory -- the
    fork's exemptions only land on the files the fork means to exempt when the tree
    being read is the working directory.  ``--no-cache`` because a lint of a candidate
    must not leave a ``.ruff_cache`` inside the tree that is about to be packaged.
    """
    try:
        config = read_fork_ruff_config(fork_root)
    except ForkLintError as exc:
        return [_unavailable("ruff", "", str(exc))]
    executable = _ruff_executable()
    if not executable:
        return [_unavailable("ruff", "", "ruff is not installed here, so the fork's ruff "
                                         "enforcement was not simulated")]
    staging = tempfile.mkdtemp(prefix="100ways-forklint-ruff-")
    config_path = os.path.join(staging, "ruff.toml")
    try:
        with open(config_path, "w", encoding="utf-8") as handle:
            handle.write(config.config_text)
        command = [
            executable, "check", "--no-cache", "--config", config_path,
            "--output-format", "json", ".",
        ]
        try:
            proc = subprocess.run(
                command, cwd=candidate_root, capture_output=True, text=True, errors="replace",
                timeout=RUFF_TIMEOUT_S, check=False, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [_unavailable("ruff", "", f"cannot run ruff check: {exc}")]
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    divergence = _version_divergence(fork_root, executable)
    if proc.returncode == 0:
        return [divergence] if divergence else []
    if proc.returncode != 1:
        return [_unavailable("ruff", "",
                             f"exit {proc.returncode} with the fork's own {config.source}: "
                             f"{(proc.stdout + proc.stderr).strip()[:OUTPUT_CAP_CHARS]}")]
    issues = _ruff_diagnostic_issues(candidate_root, proc.stdout)
    if divergence:
        issues.append(divergence)
    return sorted(issues, key=lambda issue: (issue.path, issue.detail))


# ---------------------------------------------------------------------------
# Brand inflation against the AGENTS.md chain cap
# ---------------------------------------------------------------------------

def _ast_int(path: str, name: str) -> int:
    """Read an integer the fork's own checker enforces, so the two cannot drift."""
    source = _read(path)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return int(ast.literal_eval(node.value))
    raise ForkLintError(f"{path}: no {name} assignment -- the AGENTS.md budget moved and this "
                        "predictor would report the wrong cap")


def _agents_texts(root: str) -> dict[str, str]:
    """Every ``AGENTS.md`` under ``root``, keyed by its path relative to ``root``."""
    texts: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _STAGED_IGNORES)
        for filename in filenames:
            if filename != ROOT_AGENTS:
                continue
            absolute = os.path.join(dirpath, filename)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            try:
                with open(absolute, encoding="utf-8-sig", errors="replace") as handle:
                    texts[relative] = handle.read()
            except OSError:
                continue
    return texts


def _agents_chain(rel: str, present: set[str]) -> list[str]:
    """The block one agent loads: every ancestor of ``rel`` above it, then ``rel``.

    Mirrors the fork's own ``_chain`` -- the file itself is never counted twice, and a
    chain of one is a file, not a chain.
    """
    chain = [ROOT_AGENTS] if rel != ROOT_AGENTS and ROOT_AGENTS in present else []
    parts = rel.split("/")[:-1]
    for depth in range(1, len(parts) + 1):
        ancestor = "/".join(parts[:depth]) + f"/{ROOT_AGENTS}"
        if ancestor != rel and ancestor in present and ancestor not in chain:
            chain.append(ancestor)
    return [*chain, rel]


def predict_brand_inflation(
    fork_root: str,
    candidate_root: str,
    upstream_root: str = "",
    rules: BrandingRules | None = None,
) -> list[ForkLintIssue]:
    """Flag every ``AGENTS.md`` and root-to-area chain branding pushed past its cap.

    ``scripts/ci/check_agents_md_size.py`` budgets per CHAIN -- each file plus all of
    its ancestors against ``CHAIN_MAX_CHARS``, every area file against
    ``_MAX_HINT_CHARS`` and the root against ``ROOT_MAX_CHARS``, because the root sits in
    every chain -- and all three numbers are read from the fork, never copied.  A unit
    over its cap is blocking; a unit inside :data:`BRAND_MARGIN_CHARS` of it is
    advisory, which is the whole point of predicting instead of observing: the chain
    that is red on the fork forty minutes from now is the one that is 36 chars under
    its cap today.
    """
    rules = rules or BrandingRules()
    checker = os.path.join(fork_root, *AGENTS_SIZE_CHECKER.split("/"))
    hints = os.path.join(fork_root, *SUBDIRECTORY_HINTS.split("/"))
    try:
        root_max = _ast_int(checker, "ROOT_MAX_CHARS")
        chain_max = _ast_int(checker, "CHAIN_MAX_CHARS")
        hint_max = _ast_int(hints, "_MAX_HINT_CHARS")
    except (ForkLintError, OSError, SyntaxError, ValueError, TypeError) as exc:
        return [_unavailable(AGENTS_SIZE_CHECKER, AGENTS_SIZE_CHECKER,
                             f"cannot read the caps the checker enforces: {exc}")]
    branded = {
        rules.transform_path(rel): text for rel, text in _agents_texts(candidate_root).items()
    }
    if not branded:
        return []
    upstream_sizes = {
        rules.transform_path(rel): len(text) for rel, text in _agents_texts(upstream_root).items()
    } if upstream_root else {}
    present = set(branded)
    units: list[tuple[str, tuple[str, ...], int]] = [
        (rel, (rel,), len(branded[rel])) for rel in sorted(branded)
    ]
    for rel in sorted(branded):
        chain = _agents_chain(rel, present)
        if len(chain) > 1:
            units.append((f"chain {' + '.join(chain)}", tuple(chain),
                          sum(len(branded[item]) for item in chain)))
    issues: list[ForkLintIssue] = []
    for label, paths, size in units:
        cap = root_max if paths == (ROOT_AGENTS,) else (
            chain_max if len(paths) > 1 else hint_max
        )
        if size <= cap - BRAND_MARGIN_CHARS:
            continue
        over = size > cap
        upstream_total = sum(upstream_sizes.get(path, 0) for path in paths)
        # Triage's class-2 tell is "a size lint red on the fork while upstream's
        # identical tree is green", so the class comes from the same comparison: only
        # an upstream unit that fits under the same cap lets the brand claim the red.
        # An upstream already over it, or a unit member upstream does not ship, is
        # class 3 -- the sync owns it.  With no upstream tree (ad-hoc CLI runs; the
        # pipeline always supplies one) the transform is the honest owner, because
        # branding only ever lengthens and the cap stays untouched either way.
        fits_upstream = upstream_total <= cap and all(path in upstream_sizes for path in paths)
        classification = (
            GENUINE_DIVERGENCE if upstream_sizes and not fits_upstream else BRAND_INFLATION
        )
        measured = (
            f"upstream {upstream_total} -> branded {size} chars "
            f"(+{size - upstream_total} from the brand transform)"
            if upstream_sizes else f"branded {size} chars (no upstream tree to compare)"
        )
        issues.append(ForkLintIssue(
            script=AGENTS_SIZE_CHECKER,
            path=" + ".join(paths),
            detail=redact(
                f"{label}: OVER CAP " if over else f"{label}: inside the margin "
            ) + f"{measured} against the cap {cap}, prediction margin {BRAND_MARGIN_CHARS} chars",
            blocking=over,
            classification=classification,
        ))
    return issues


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def run_fork_lint(
    fork_root: str, candidate_root: str, upstream_root: str = ""
) -> list[ForkLintIssue]:
    """Run the fork's lint contract over ``candidate_root`` and report every finding.

    Deterministic: issues sort by ``(script, path, detail)``.  Never optimistic: an
    absent fork checkout, a workflow that no longer invokes an allow-listed checker, a
    checker that crashes or times out or exits with a code it does not document, and a
    missing ruff are all :data:`UNAVAILABLE`, and unavailable is blocking.  Advisory is
    the fork's own ``continue-on-error``, read from ``lint.yml``, and nothing else.
    """
    if not fork_root or not os.path.isdir(fork_root):
        return [_unavailable(FORK_LINT_WORKFLOW, fork_root or "",
                             "no fork checkout, so the fork's static checks were not simulated")]
    if not os.path.isdir(candidate_root):
        return [_unavailable(FORK_LINT_WORKFLOW, candidate_root, "no candidate tree to lint")]
    workflow_path = os.path.join(fork_root, *FORK_LINT_WORKFLOW.split("/"))
    try:
        workflow = _read(workflow_path)
    except OSError as exc:
        return [_unavailable(FORK_LINT_WORKFLOW, workflow_path,
                             f"cannot read the job that defines the checks: {exc}")]
    issues: list[ForkLintIssue] = []
    try:
        with _staged_checkout(candidate_root) as tree:
            for script in FORK_CHECK_SCRIPTS:
                issues.extend(_run_checker(fork_root, script, workflow, tree))
    except (OSError, shutil.Error) as exc:
        issues.extend(
            _unavailable(script, candidate_root,
                         f"the candidate could not be staged as a checkout: {exc}")
            for script in FORK_CHECK_SCRIPTS
        )
    issues.extend(run_fork_ruff(fork_root, candidate_root))
    issues.extend(predict_brand_inflation(fork_root, candidate_root, upstream_root))
    return sorted(issues, key=lambda issue: (issue.script, issue.path, issue.detail))


def main(argv: list[str] | None = None) -> int:
    """``python -m hundredways.forklint --fork <checkout> --candidate <tree>``."""
    parser = argparse.ArgumentParser(
        description="Run the fork's own static checks against a candidate"
    )
    parser.add_argument("--fork", required=True, help="checkout of nastech-agent main")
    parser.add_argument("--candidate", required=True, help="branded candidate tree to lint")
    parser.add_argument("--upstream", default="", help="fresh upstream clone, for the brand delta")
    args = parser.parse_args(argv)
    issues = run_fork_lint(args.fork, args.candidate, args.upstream)
    summary = summarize(issues)
    print(summary.line())
    for line in report_lines(issues):
        print(line)
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
