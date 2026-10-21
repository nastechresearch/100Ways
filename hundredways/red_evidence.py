"""Measured evidence for fork CI classification: numbers read at call time, never remembered.

A classification is only worth printing if every number in it came from the thing that
enforces it.  This module holds those measurements and nothing else:

* :func:`unbrand` reverses the brand with the canonical token table from
  :mod:`hundredways.rules`, so a fork path matches an upstream path and a branded file can
  be measured at its upstream size without a second checkout.
* :func:`branded_image` and :func:`worker_ceiling` ask the brand transform itself what a
  job's ``runs-on`` became and what worker count it became, so the Class-1 precondition
  (the fork *had* to lose that machine) cannot drift from the transform that imposes it.
* :func:`wall_clock_claims` recognises an assertion shape -- a comparison against an
  absolute seconds budget -- instead of a list of test names, which is the only form of
  this rule that survives upstream renaming a test.  A measured number is read from the
  assertion that produced it, never from whichever rewrite in the log happened to share
  the bound.
* :func:`size_reports` measures the chain the fork's own checker measures, against the cap
  parsed out of the file that enforces it.

Nothing here decides a class, and nothing here rewrites a file.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .rules import DEFAULT_TOKENS, BrandingRules

# The two files that enforce a size budget on AGENTS.md.  Names, not numbers: the numbers
# are parsed out of them (:func:`cap_constant`).
SIZE_CHECKER = "scripts/ci/check_agents_md_size.py"
HINTS_MODULE = "agent/subdirectory_hints.py"
_ROOT_AGENTS = "AGENTS.md"
_SCAN_SKIP = frozenset({".git", ".tox", ".venv", "__pycache__", "node_modules"})

# Reverse of the canonical token table, longest branded form first.  Where two upstream
# spellings collapse to one branded spelling (``nous`` and ``hermes`` both to ``nastech``)
# the LONGER upstream spelling wins, so a reversed file is measured at its largest possible
# upstream size -- the reading least likely to blame the brand for a cap breach.
_UNBRAND_PAIRS: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((rule.replace, rule.match) for rule in DEFAULT_TOKENS if rule.replace),
        key=lambda pair: (-len(pair[0]), -len(pair[1])),
    )
)

_FAILED_ID = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.MULTILINE)
_RUNNER_TAG = re.compile(r"runner@([A-Za-z0-9._-]+)")
_IMAGE_LABEL = re.compile(r"^(?:blacksmith-|ubuntu|windows|macos)")
_FILE_IN_TRACEBACK = re.compile(r'File "([^"]+)", line \d+')
_SOURCE_LOCATION = re.compile(r"([\w./-]+\.(?:py|md)):\d+")
_AGENTS_PATH = re.compile(r"([\w./-]*AGENTS\.md)")
_CORES_IN_LABEL = re.compile(r"(\d+)(?:vcpu|-(?:arm-)?core)")
_RETRY_MARKER = re.compile(r"\b(?:FLAKY|retried|attempt [2-9]\d*)\b", re.IGNORECASE)
_WORKERS_IN_LOG = re.compile(r"(?:TEST_WORKERS:\s*|-j\s+)(\d+)")


def unbrand(text: str) -> str:
    """Fork spelling -> upstream spelling, so branded paths and sizes compare to upstream."""
    reversed_text = text
    for branded, upstream in _UNBRAND_PAIRS:
        reversed_text = reversed_text.replace(branded, upstream)
    return reversed_text


def failing_test_ids(text: str) -> tuple[str, ...]:
    """The FAILED/ERROR test ids a pytest summary line reports, as the fork's CI spelled them."""
    return tuple(sorted({m.group(1) for m in _FAILED_ID.finditer(text)}))


def traceback_files(text: str) -> tuple[str, ...]:
    """Every source file named in a traceback or a pytest location, as the fork named it.

    Basenames, because branding renames directory components too (``tests/hermes_cli`` ->
    ``tests/nastech_cli``) and a path comparison across the brand boundary misses more than it
    catches.  This is the matching the hand triage script measured.
    """
    found = [m.group(1) for m in _FILE_IN_TRACEBACK.finditer(text)]
    found += [m.group(1) for m in _SOURCE_LOCATION.finditer(text)]
    seen: list[str] = []
    for path in found:
        name = path.replace(os.sep, "/").split("/")[-1]
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def agents_files(text: str) -> tuple[str, ...]:
    """AGENTS.md paths a size lint printed, as tree-relative paths."""
    return tuple(sorted({m.group(1).removeprefix("./") for m in _AGENTS_PATH.finditer(text)}))


def run_text(run: dict[str, Any]) -> str:
    """Everything GitHub put in a check-run's output box: title, summary, text."""
    output = run.get("output") or {}
    return "\n".join(
        str(output.get(key)) for key in ("title", "summary", "text") if output.get(key)
    )


def run_image(run: dict[str, Any]) -> str:
    """The image a check-run actually ran on, from whichever field the API filled in.

    The runner name is what the job really got; ``labels`` are what it asked for, which for an
    upstream check-run is upstream's own spelling -- so the name is read first and only the
    labels when no runner was reported.
    """
    if run.get("runner_name"):
        return str(run["runner_name"])
    for label in (str(label) for label in (run.get("labels") or []) if label):
        if _IMAGE_LABEL.match(label):
            return label
    match = _RUNNER_TAG.search(run_text(run))
    return match.group(1) if match else ""


def run_retried(run: dict[str, Any]) -> bool:
    """True when the lane's own output reports a retry or a flake."""
    return bool(_RETRY_MARKER.search(run_text(run)))


# ---------------------------------------------------------------------------
# The runner the brand transform was forced to give this job
# ---------------------------------------------------------------------------

# A fan-out above any worker ceiling this engine has ever imposed, so :func:`worker_ceiling`
# reads back the ceiling the transform applies rather than the number it was handed.
_WORKER_PROBE = 10_000


@dataclass(frozen=True)
class RunnerShape:
    """What a ``runs-on`` label asked for, and what this organization can actually run."""

    requested: str
    actual: str
    requested_cores: int | None
    actual_cores: int | None
    forced_down: bool


def label_cores(image: str) -> int | None:
    """The vCPU count written inside a runner label, if the label carries one."""
    match = _CORES_IN_LABEL.search(image)
    return int(match.group(1)) if match else None


def branded_image(runs_on: str) -> str:
    """Ask the canonical transform what image this ``runs-on`` becomes in the fork."""
    transformed = BrandingRules().transform_text(f"runs-on: {runs_on}")
    match = re.search(r"runs-on:[ \t]*(\S+)", transformed)
    return match.group(1) if match else runs_on


def runner_shape(runs_on: str, actual: str) -> RunnerShape:
    """The Class-1 precondition, taken from the transform rather than from a remembered image.

    ``forced_down`` is only true when the transform itself rewrote the label AND the
    rewritten label carries no size while the requested one did: that is a machine the fork
    was obliged to give up, not a lane that simply happens to be slow.
    """
    if not runs_on or not actual:
        return RunnerShape(runs_on, actual, label_cores(runs_on), label_cores(actual), False)
    produced = branded_image(runs_on)
    requested_cores = label_cores(runs_on)
    actual_cores = label_cores(produced)
    forced = produced != runs_on and produced == actual and requested_cores is not None and (
        actual_cores is None or actual_cores < requested_cores
    )
    return RunnerShape(runs_on, actual, requested_cores, actual_cores, forced)


def worker_ceiling() -> int | None:
    """The worker count the engine's own normalizer imposes on a wide fan-out.

    Read from the canonical transform's output on a synthetic workflow line written in
    upstream's spelling, so it exercises the branding of the variable name and the cap on its
    value exactly as a sync does.  The number is therefore whatever ``hundredways/rules.py``
    enforces today, not a copy of it that rots.  ``None`` when the transform does not lower the
    probe at all: quoting the probe back as if it were the ceiling would be the same invention
    as hardcoding the cap.
    """
    transformed = BrandingRules().transform_text(f"HERMES_TEST_WORKERS: {_WORKER_PROBE}")
    match = re.search(r"NASTECH_TEST_WORKERS:[ \t]*(\d+)", transformed)
    if match is None:
        return None
    ceiling = int(match.group(1))
    return ceiling if ceiling < _WORKER_PROBE else None


def workers_in_log(text: str) -> tuple[int, ...]:
    """Worker counts the lane reported for itself (``-j`` or a TEST_WORKERS env line)."""
    return tuple(sorted({int(m.group(1)) for m in _WORKERS_IN_LOG.finditer(text)}))


# ---------------------------------------------------------------------------
# Absolute wall-clock assertions, recognised by shape
# ---------------------------------------------------------------------------

# Timing vocabulary.  These are time nouns and verbs, matched on whole snake_case words --
# NOT a list of test names, and not used to decide anything on their own: they only say that
# a bound in the log is expressed in seconds rather than relative to another measurement.
_TIME_WORDS = frozenset(
    {
        "budget",
        "cadence",
        "deadline",
        "delay",
        "duration",
        "elapsed",
        "hang",
        "hold",
        "interval",
        "latency",
        "pace",
        "pause",
        "secs",
        "sec",
        "sleep",
        "stall",
        "time",
        "timeout",
        "ttl",
        "wait",
    }
)
_ASSERTION = re.compile(r"\bassert\b", re.IGNORECASE)
_COMPARISON = re.compile(r"(?P<lhs>.+?)\s*(?P<op><=|>=|<|>|==|!=)\s*(?P<rhs>.*)$")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_DECLARED_BUDGET = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>\d+(?:\.\d+)?)\b")
_PREFIX_NOISE = re.compile(r"^(?:[>|E]+\s*:?\s*)+")
# How far below an assertion line pytest's rewritten numbers may sit and still be read as that
# assertion's own numbers.
_REWRITE_WINDOW = 4
# A comparison against a literal this large is not a wall clock a test asserts against; it is a
# count of something else wearing a timing name.
_MAX_BOUND_SECONDS = 3600.0


def _assertion_body(raw: str) -> str:
    """The expression of an assert line, whether pytest showed the source or the rewrite.

    Both forms appear in a CI log: ``assert max(gaps) < (HOLD_S / 3)`` names the budget, and
    ``AssertionError: assert 0.78 < 0.5`` gives its value.  Everything before them is the
    traceback gutter (``E``, ``>>>``, ``>``, the exception name).
    """
    line = _PREFIX_NOISE.sub("", raw.strip())
    line = re.sub(r"^AssertionError:\s*", "", line, flags=re.IGNORECASE)
    match = _ASSERTION.search(line)
    return line[match.end():].strip() if match else ""


def _is_time_word(word: str) -> bool:
    folded = word.lower()
    return any(
        candidate in _TIME_WORDS
        for candidate in (folded, folded.rstrip("s"), folded.removesuffix("ed"))
    )


def _is_time_name(expression: str) -> bool:
    return any(_is_time_word(word) for word in re.split(r"[^A-Za-z0-9]+", expression) if word)


def _is_constant_name(expression: str) -> bool:
    """True when an operand is spelled like a module-level constant, not a measured value.

    This is what makes a bound *absolute*: Python writes a budget as ``HOLD_S`` or
    ``BROWSE_RECONCILE_BUDGET_S`` and writes a measurement as a lowercase local.  Without
    this split, ``assert waited < baseline_waited`` -- a comparison against another timing
    measurement -- would read as a wall-clock budget and hand its red to the hardware.
    """
    return any(word.isupper() for word in re.split(r"[^A-Za-z0-9]+", expression) if word.isalpha())


def _atom(token: str) -> str:
    """A comparison operand stripped of the wrapping parentheses a rewritten assert keeps."""
    token = token.strip()
    while token.startswith(("(", "[")) and token.endswith((")", "]")):
        token = token[1:-1].strip()
    return token


def _number(token: str) -> float | None:
    cleaned = _atom(token).rstrip(",;")
    return float(cleaned) if _NUMBER.match(cleaned) else None


def _simple_value(expression: str, declared: dict[str, float]) -> float | None:
    """Evaluate ``NAME``, ``NUM``, or ``NAME / NUM`` -- the shapes an absolute bound takes."""
    parts = [_atom(token) for token in re.split(r"\s+", expression.strip()) if token]
    parts = [part.rstrip(",;") for part in parts]
    if len(parts) == 1:
        value = _number(parts[0])
        if value is not None:
            return value
        return declared.get(parts[0]) if _is_time_name(parts[0]) else None
    if len(parts) == 3 and parts[1] in {"*", "/", "+", "-"}:
        for name_index, value_index in ((0, 2), (2, 0)):
            name, other = parts[name_index], parts[value_index]
            other_value = _number(other)
            if _is_time_name(name) and other_value is not None:
                value = declared.get(name)
                if value is None:
                    return None
                if parts[1] == "/":
                    return value / other_value
                if parts[1] == "*":
                    return value * other_value
                return None
        left, right = _number(parts[0]), _number(parts[2])
        if left is not None and right is not None:
            return {"*": left * right, "/": left / right, "+": left + right, "-": left - right}[
                parts[1]
            ]
    return None


@dataclass(frozen=True)
class WallClock:
    """One failure-text signal that a bound is a wall-clock budget, not a measured value."""

    budget: str
    bound_seconds: float | None
    observed_seconds: float | None
    origin: str


def _strip_context(line: str) -> str:
    """A log line with its traceback gutter gone, so a declaration is read wherever it sat.

    GitHub Actions prints the failing statement indented under its test, pytest prefixes the
    same statement's rewrite with ``E``: both spell the budget ``HOLD_S = 1.5``, and both have
    to reach the same :func:`declared_budgets` match.
    """
    return _PREFIX_NOISE.sub("", line.strip())


def declared_budgets(text: str) -> dict[str, float]:
    """Constants in the log spelled TIME/SECS/BUDGET-style, with the value the log gives them."""
    budgets: dict[str, float] = {}
    for raw in text.splitlines():
        match = _DECLARED_BUDGET.match(_strip_context(raw))
        name = match.group("name") if match else ""
        if name and _is_time_name(name) and _is_constant_name(name):
            budgets[name] = float(match.group("value"))
    return budgets


def _numeric_comparisons(lines: Sequence[str]) -> tuple[tuple[int, float, float], ...]:
    """``(line, observed, bound)`` for every fully numeric assert in the log.

    These are pytest's rewritten form: they carry both numbers and no names, so a number here
    can only be read as belonging to the assertion line printed above it.
    """
    found: list[tuple[int, float, float]] = []
    for index, raw in enumerate(lines):
        comparison = _COMPARISON.search(_assertion_body(raw))
        if not comparison or comparison.group("op") not in {"<", "<=", ">", ">="}:
            continue
        lhs, rhs = _number(comparison.group("lhs")), _number(comparison.group("rhs"))
        if lhs is not None and rhs is not None:
            found.append((index, lhs, rhs))
    return tuple(found)


def _adjacent_numbers(
    pairs: Sequence[tuple[int, float, float]], index: int, bound: float | None
) -> tuple[float | None, float | None]:
    """The numbers of the rewrite printed under this assertion -- never of a later assertion.

    When the bound already resolved from the source line, a rewrite only counts as its own when
    its right-hand side *is* that bound: two tests sharing one budget would otherwise lend each
    other their measured value.  When the source line named a constant the log never declared,
    the adjacent rewrite is where both numbers come from.
    """
    for pair_index, observed, rhs in pairs:
        if pair_index <= index:
            continue
        if pair_index - index > _REWRITE_WINDOW:
            break
        if bound is None:
            return observed, rhs
        return (observed if rhs == bound else None), bound
    return None, bound


def wall_clock_claims(text: str) -> tuple[WallClock, ...]:
    """Assertions that compare against an absolute seconds budget, plus budgets the log arms.

    Recognised structurally: the compared-to side is a literal, or arithmetic over a
    timing-named constant, never a second measurement.  A bound that is itself a measured
    value is relative and does not count.  Observed numbers come from the rewrite of the same
    assertion, or from the same line when the log put the measurement there, so an unrelated
    ``assert 2 == 1`` -- or another test that happens to share the budget -- cannot lend this
    claim its arithmetic.
    """
    lines = text.splitlines()
    declared = declared_budgets(text)
    pairs = _numeric_comparisons(lines)
    claims: list[WallClock] = []
    for index, raw in enumerate(lines):
        comparison = _COMPARISON.search(_assertion_body(raw))
        if not comparison:
            continue
        lhs = _atom(comparison.group("lhs"))
        bound_expression = _rhs_head(comparison.group("rhs"))
        lhs_value, literal_bound = _number(lhs), _number(bound_expression)
        if lhs_value is not None and literal_bound is not None:
            continue  # a rewritten line: its numbers attach to the source line that named it
        absolute_bound = _is_time_name(bound_expression) and _is_constant_name(bound_expression)
        if not absolute_bound:
            # A literal seconds figure counts only against a timing-named quantity: an
            # absolute wall clock, not a comparison between two measurements.
            absolute_bound = (
                literal_bound is not None
                and abs(literal_bound) <= _MAX_BOUND_SECONDS
                and _is_time_name(lhs)
            )
        if not absolute_bound:
            continue
        bound = _simple_value(bound_expression, declared)
        if bound is not None and lhs_value is not None:
            # ``assert 0.78 < TIMEOUT_SECS`` states the measurement on the line itself.
            observed = lhs_value
        else:
            observed, bound = _adjacent_numbers(pairs, index, bound)
        claims.append(WallClock(bound_expression, bound, observed, "assertion"))
    for name, value in declared.items():
        if not any(claim.budget.startswith(name) for claim in claims):
            claims.append(WallClock(name, value, None, "armed budget"))
    return tuple(claims)


def _rhs_head(rhs: str) -> str:
    """The operand the assertion compares against, stopping at any chained comparison."""
    split = re.split(r"\s+(?:and|or|if|else)\s+", rhs)
    return _atom(split[0])


# ---------------------------------------------------------------------------
# The chain the fork's checker measures, against the cap that file enforces
# ---------------------------------------------------------------------------


def cap_constant(checkout: str, relative: str, name: str) -> int | None:
    """Read one integer cap out of the source file that enforces it. ``None`` if not there."""
    path = os.path.join(checkout, *relative.split("/"))
    try:
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
    except (OSError, SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == name for target in node.targets
        ):
            try:
                return int(ast.literal_eval(node.value))
            except (SyntaxError, ValueError, TypeError):
                return None
    return None


def agents_texts(checkout: str) -> dict[str, str]:
    """Every AGENTS.md under ``checkout``, keyed by forward-slashed relative path."""
    texts: dict[str, str] = {}
    for root, dirs, files in os.walk(checkout):
        dirs[:] = [d for d in dirs if d not in _SCAN_SKIP]
        if _ROOT_AGENTS not in files:
            continue
        path = os.path.join(root, _ROOT_AGENTS)
        relative = os.path.relpath(path, checkout).replace(os.sep, "/")
        try:
            with open(path, encoding="utf-8") as handle:
                texts[relative] = handle.read()
        except (OSError, UnicodeDecodeError):
            continue
    return texts


def agents_chain(relative: str, present: Iterable[str]) -> tuple[str, ...]:
    """Root AGENTS.md down to ``relative`` -- the block one agent actually loads."""
    present = set(present)
    directories = relative.split("/")[:-1]
    chain: list[str] = []
    if relative != _ROOT_AGENTS and _ROOT_AGENTS in present:
        chain.append(_ROOT_AGENTS)
    for depth in range(1, len(directories) + 1):
        ancestor = "/".join(directories[:depth]) + "/AGENTS.md"
        if ancestor in present and ancestor != relative:
            chain.append(ancestor)
    if relative not in chain:
        chain.append(relative)
    return tuple(chain)


@dataclass(frozen=True)
class SizeReport:
    """One measured budget: a file against its own cap, or a chain against the chain cap."""

    kind: str
    members: tuple[str, ...]
    branded_chars: int
    upstream_chars: int
    cap: int

    @property
    def label(self) -> str:
        return " + ".join(self.members)

    @property
    def over_cap(self) -> bool:
        return self.branded_chars > self.cap

    @property
    def brand_delta(self) -> int:
        return self.branded_chars - self.upstream_chars


def size_reports(checkout: str, implicated: Sequence[str]) -> tuple[SizeReport, ...]:
    """Measure every implicated AGENTS.md the way the fork's checker measures it.

    Caps come from ``scripts/ci/check_agents_md_size.py`` and ``agent/subdirectory_hints.py``
    as those files read today.  A tree with neither the checker nor any AGENTS.md yields no
    reports: there is nothing to call brand inflation about.
    """
    if not checkout or not os.path.exists(os.path.join(checkout, *SIZE_CHECKER.split("/"))):
        return ()
    texts = agents_texts(checkout)
    if not texts:
        return ()
    chain_cap = cap_constant(checkout, SIZE_CHECKER, "CHAIN_MAX_CHARS")
    root_cap = cap_constant(checkout, SIZE_CHECKER, "ROOT_MAX_CHARS")
    hint_cap = cap_constant(checkout, HINTS_MODULE, "_MAX_HINT_CHARS")
    wanted: set[str] = set()
    for path in implicated:
        if not path.endswith(_ROOT_AGENTS):
            continue
        cleaned = path.removeprefix("./")
        for relative in texts:
            if cleaned == relative:
                wanted.add(relative)
            elif relative == _ROOT_AGENTS:
                continue  # the root is only ever spelled AGENTS.md, never a suffix of a path
            elif relative.endswith("/" + cleaned) or cleaned.endswith("/" + relative):
                # A traceback names an absolute path and a lint names a partial one; either
                # way the file meant is the checkout-relative chain member it ends with.
                wanted.add(relative)
    reports: list[SizeReport] = []
    for relative in sorted(wanted):
        file_cap = root_cap if relative == _ROOT_AGENTS else hint_cap
        if file_cap is not None:
            reports.append(_measure("file", (relative,), file_cap, texts))
        chain = agents_chain(relative, texts)
        if chain_cap is not None and len(chain) > 1:
            reports.append(_measure("chain", chain, chain_cap, texts))
    return tuple(reports)


def _measure(kind: str, members: tuple[str, ...], cap: int, texts: dict[str, str]) -> SizeReport:
    branded = sum(len(texts[member]) for member in members)
    upstream = sum(len(unbrand(texts[member])) for member in members)
    return SizeReport(kind, members, branded, upstream, cap)
