"""Sort a red CI check on the rebrand fork into the one class that owns the fix.

The fork's CI can only go red three ways, and each has a different owner:

* ``RUNNER_CAPACITY`` -- the org has no paid runner, so the brand transform rewrites every
  upstream ``runs-on`` down to a free hosted image and drops its vCPU count.  An assertion
  against an absolute wall clock then fails here and passes upstream on the same commit.
  No product fix exists, and writing one is the wrong act.
* ``BRAND_INFLATION`` -- a size lint goes red while upstream's identical tree is green,
  because branding adds roughly one character per renamed token to files upstream keeps a
  few dozen characters under the cap.
* ``GENUINE_DIVERGENCE`` -- upstream changed the implicated file since our baseline.  The
  answer is the next sync, never a hand edit: branding re-derives every upstream-owned file.

Classifying is safe to automate because each call is gated on a *measured* precondition,
read at call time from :mod:`hundredways.red_evidence`: the image the transform was obliged
to substitute, the cap out of the file that enforces it, the chain's size with the brand
tokens reversed, and the upstream file delta.  Precedence is divergence, then brand
inflation, then capacity -- strongest evidence first and cheapest-to-be-wrong last, because
a false "it is only the runner" is how a real divergence ships.

What this module refuses to do: claim a class whose precondition is missing (it returns
``UNKNOWN`` and names the missing fact), drop a lane that did not pass (a job that reports
``timed_out`` or ``cancelled`` is a red like any other, and a report that says "nothing to
classify" about a red CI is the one answer that is never allowed), propose moving a cap, a
budget, a timeout or a scanner, propose skipping or ignoring a test, or quote a raw log line.
Evidence is composed from names and measured numbers only, and every rendered line passes the
engine's canonical secret shapes.

There is no allowlist of failing test names anywhere in the decision path -- such a list rots
the day upstream renames a test.  The only two name-shaped patterns here are
:data:`_AGGREGATE_HINTS`, which decides whether a check-run is a red at all (a job that reports
on other jobs has no product outcome to classify), and :data:`_SIZE_LINT_TEXT`, which only
words an ``UNKNOWN`` for a lint whose budget cannot be measured.  Neither can select a class,
and both read *job* names, never test names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from . import red_evidence as evidence
from .secrets import redact

CHECK_RUNS_KEY = "check_runs"

# Conclusions that mean the lane did not pass.  A job that blew its ``timeout-minutes`` budget
# reports ``timed_out`` and a cancelled one ``cancelled`` -- both are reds a human has to own,
# and the first is exactly the outcome the dossier's worker-ceiling headroom warning predicts.
# Treating only ``failure`` as red would let those lanes vanish and the report would read as a
# CI with nothing wrong in it.
RED_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled"})

# Jobs that report on other jobs instead of running anything: never a red of their own.
_AGGREGATE_HINTS = ("required checks", "aggregate", "detect affected")


class RedClass(str, Enum):
    RUNNER_CAPACITY = "RUNNER_CAPACITY"
    BRAND_INFLATION = "BRAND_INFLATION"
    GENUINE_DIVERGENCE = "GENUINE_DIVERGENCE"
    UNKNOWN = "UNKNOWN"


OWNERS: Mapping[RedClass, str] = {
    RedClass.RUNNER_CAPACITY: "nobody — hardware",
    RedClass.BRAND_INFLATION: "100Ways brand transform",
    RedClass.GENUINE_DIVERGENCE: "upstream sync",
    RedClass.UNKNOWN: "the person reading this red",
}

# Only used to word an honest UNKNOWN for a lint whose budget this module cannot measure;
# it never selects a class.
_SIZE_LINT_TEXT = re.compile(
    r"chain cap|chars >|MAX_CHARS|MAX_HINT|too long|size budget|code-health", re.IGNORECASE
)


@dataclass(frozen=True)
class FailedCheck:
    """One red check: its lane name, what it reports failing, and the text saying so."""

    name: str
    test_ids: tuple[str, ...] = ()
    log: str = ""
    files: tuple[str, ...] = ()

    @classmethod
    def from_check_run(cls, run: Mapping[str, Any]) -> "FailedCheck":
        """Build it from one element of GitHub's check-runs payload."""
        text = evidence.run_text(run)
        test_ids = evidence.failing_test_ids(text)
        files = list(evidence.traceback_files(text))
        for test_id in test_ids:
            name = test_id.split("::")[0].split("/")[-1]
            if name and name not in files:
                files.append(name)
        return cls(name=str(run.get("name", "")), test_ids=test_ids, log=text, files=tuple(files))

    @property
    def implicated_files(self) -> tuple[str, ...]:
        """Every file this red names, size-lint output included."""
        found = list(self.files)
        for path in evidence.agents_files(self.log):
            if path not in found:
                found.append(path)
        return tuple(found)


@dataclass(frozen=True)
class SyncFacts:
    """What the sync looks like from outside this red: baselines, images, upstream history.

    ``None`` for an upstream field means nobody diffed or re-ran upstream for this
    candidate.  That is a fact to report, not an empty set: an empty delta says upstream is
    unchanged, which is the opposite claim and a different verdict.
    """

    lane: str = ""
    baseline_sha: str = ""
    current_sha: str = ""
    fork_image: str = ""
    upstream_image: str = ""
    upstream_changed_files: frozenset[str] | None = None
    upstream_failed_tests: frozenset[str] = frozenset()
    upstream_succeeded: bool | None = None
    retried: bool = False
    checkout_path: str = ""

    @classmethod
    def from_check_runs(
        cls,
        fork_runs: Mapping[str, Any],
        *,
        lane: str,
        upstream_runs: Mapping[str, Any] | None = None,
        upstream_changed_files: Sequence[str] | None = None,
        baseline_sha: str = "",
        current_sha: str = "",
        checkout_path: str = "",
    ) -> "SyncFacts":
        """Build it from the raw ``commits/{sha}/check-runs`` payloads CI hands over.

        ``upstream_changed_files`` is the upstream file delta since ``baseline_sha`` -- the
        commit the candidate synced from, read off the PR body or the sync manifest, never
        off fork main.  Pass ``None`` when no delta was computed; that keeps the divergence
        and brand-inflation calls out of reach instead of guessing them.

        The lane is looked for by its own name in the fork payload and, in the upstream
        payload, by the same name read through the brand: a job keeps its identity across the
        rename, and a comparison that quietly failed to find the other side would report the
        red as unclassifiable.  ``upstream_succeeded`` stays strictly ``== "success"``, so a
        lane that timed out or was cancelled upstream is not read as a green run.
        """
        fork_run = _pick(_runs(fork_runs), lane)
        if fork_run is None:
            raise ValueError(f"no check-run named {lane} in the fork payload")
        upstream_run = _pick(_runs(upstream_runs or {}), lane)
        delta = (
            None
            if upstream_changed_files is None
            else frozenset(_match_names(upstream_changed_files))
        )
        return cls(
            lane=lane,
            baseline_sha=baseline_sha,
            current_sha=current_sha,
            fork_image=evidence.run_image(fork_run),
            upstream_image=evidence.run_image(upstream_run) if upstream_run else "",
            upstream_changed_files=delta,
            upstream_failed_tests=(
                frozenset(_match_names(evidence.failing_test_ids(evidence.run_text(upstream_run))))
                if upstream_run
                else frozenset()
            ),
            upstream_succeeded=(
                upstream_run.get("conclusion") == "success" if upstream_run else None
            ),
            retried=evidence.run_retried(fork_run),
            checkout_path=checkout_path,
        )

    @property
    def delta_known(self) -> bool:
        return self.upstream_changed_files is not None

    def touched_upstream(self, path: str) -> bool:
        """True when upstream changed this file between the baseline and its current tip."""
        if self.upstream_changed_files is None:
            return False
        return _match_names((path,))[0] in self.upstream_changed_files

    def green_upstream(self, test_ids: Sequence[str] = ()) -> bool:
        """True when upstream's same lane clears every test this red reports, on evidence.

        Absence of a failure is not a green comparison: a lane that never ran, or one that is
        red without naming a single test id (a lint prints its last step, not a summary), says
        nothing about this test.  Only upstream's own success, or an upstream red whose reported
        ids name none of ours, pairs a fork wall-clock failure with a run that passed it.
        """
        if self.upstream_succeeded is None:
            return False
        if self.upstream_succeeded:
            return True
        if not test_ids or not self.upstream_failed_tests:
            return False
        return not any(test in self.upstream_failed_tests for test in _match_names(test_ids))

    def runner_shape(self) -> evidence.RunnerShape:
        """Ask the brand transform whether it was the reason this job lost its machine."""
        return evidence.runner_shape(self.upstream_image, self.fork_image)

    def baseline_phrase(self) -> str:
        if self.baseline_sha and self.current_sha:
            return f"between {self.baseline_sha[:8]} and {self.current_sha[:8]}"
        return "between the candidate baseline and upstream's current tip"


def _match_names(paths: Iterable[str]) -> tuple[str, ...]:
    """Fork-spelled paths or test ids, compared upstream-spelled on their last component.

    Branding renames directory components too (``tests/hermes_cli`` -> ``tests/nastech_cli``),
    so a cross-brand comparison works on the reversed tail -- the matching the hand triage
    script measured.  A test id keeps its ``::test_name[param]`` tail, which branding spells
    identically on both sides.
    """
    return tuple(evidence.unbrand(path.rstrip("/").rsplit("/", 1)[-1]) for path in paths)


@dataclass(frozen=True)
class Verdict:
    """One red, sorted, with the quotable reason and the single next action."""

    check: str
    kind: RedClass
    evidence: tuple[str, ...]
    owner: str
    action: str

    @property
    def classified(self) -> bool:
        return self.kind is not RedClass.UNKNOWN


def classify(check: FailedCheck, facts: SyncFacts) -> Verdict:
    """Sort one red check; every class below is gated on a measurement, never on a name."""
    files = check.implicated_files
    claims = evidence.wall_clock_claims(check.log)
    missing: list[str] = []
    if not facts.delta_known:
        missing.append(
            f"no upstream file delta for {check.name or facts.lane or 'this check'}: "
            "cannot tell a fork-only failure from something upstream already fixed"
        )
    else:
        touched = [path for path in files if facts.touched_upstream(path)]
        if touched:
            return _divergence(check, facts, touched, files, claims)

    inflation = _brand_inflation(check, facts, files, missing)
    if inflation is not None:
        return inflation

    capacity = _runner_capacity(check, facts, files, claims, missing)
    if capacity is not None:
        return capacity

    if not claims and not _is_size_lint(check):
        missing.append(
            "no assertion here compares against an absolute wall-clock budget, and no size "
            "budget in the handed tree measures over its own cap"
        )
    return _unknown(check, facts, missing)


def _divergence(
    check: FailedCheck,
    facts: SyncFacts,
    touched: Sequence[str],
    files: Sequence[str],
    claims: Sequence[evidence.WallClock],
) -> Verdict:
    lines = [f"upstream changed {path} {facts.baseline_phrase()}" for path in touched[:4]]
    if len(touched) > 4:
        lines.append(f"and {len(touched) - 4} more implicated files in the same delta")
    if claims:
        lines.append(
            f"this red also carries an absolute wall-clock bound ({claims[0].budget}), which "
            "ranks below an upstream change: the sync moves the code that assertion runs on"
        )
    measured = evidence.size_reports(facts.checkout_path, files)
    over = [report for report in measured if report.over_cap]
    if over:
        lines.append(
            f"the over-cap measurement ({over[0].label} at {over[0].branded_chars} chars against "
            f"{over[0].cap}) ranks below it too, for the same reason"
        )
    lines.append(
        f"branding re-derives upstream-owned files, so a hand edit here is gone at the next "
        f"sync, which carries {len(facts.upstream_changed_files or ())} changed file(s)"
    )
    return Verdict(
        check=check.name,
        kind=RedClass.GENUINE_DIVERGENCE,
        evidence=tuple(lines),
        owner=OWNERS[RedClass.GENUINE_DIVERGENCE],
        action=(
            f"Sync the next candidate and re-run {check.name}; leave the upstream-owned file "
            "alone on the fork."
        ),
    )


def _brand_inflation(
    check: FailedCheck, facts: SyncFacts, files: Sequence[str], missing: list[str]
) -> Verdict | None:
    reports = evidence.size_reports(facts.checkout_path, files)
    if not reports:
        if _is_size_lint(check):
            missing.append(
                f"{check.name} reports a size budget, but the handed checkout "
                f"({facts.checkout_path or 'unset'}) exposes no measurable {evidence.SIZE_CHECKER}"
                f" chain for the files it names, so nothing here says the brand pushed it over"
            )
        return None
    over = [report for report in reports if report.over_cap]
    if not over:
        if _is_size_lint(check):
            closest = max(reports, key=lambda report: report.branded_chars)
            missing.append(
                f"the largest budget this red names measures {closest.branded_chars} chars, "
                f"{closest.cap - closest.branded_chars} under the {closest.cap} cap "
                f"{evidence.SIZE_CHECKER} reads today, so this lint is red for a reason this "
                "classifier does not measure"
            )
        return None
    if not facts.delta_known:
        missing.append(
            f"{over[0].label} measures {over[0].branded_chars} chars over its {over[0].cap} cap, "
            "but with no upstream delta an over-cap chain cannot be attributed to the brand "
            "rather than to text upstream wrote"
        )
        return None
    attributable = [report for report in over if report.upstream_chars <= report.cap]
    if not attributable:
        missing.append(
            f"{over[0].label} measures {over[0].upstream_chars} chars even with the brand tokens "
            "reversed, so this cap breach is not the brand's doing and needs a person"
        )
        return None
    report = min(attributable, key=lambda item: item.branded_chars - item.cap)
    lines = [
        f"{report.label}: {report.upstream_chars} -> {report.branded_chars} chars against the "
        f"{report.cap} {report.kind} cap parsed from {evidence.SIZE_CHECKER}",
        f"{report.brand_delta} of those characters are the rename: the same {report.kind} measures "
        f"{report.upstream_chars} with the brand tokens reversed",
        f"upstream changed none of the {len(files)} file(s) this lint names "
        f"{facts.baseline_phrase()}: the delta is {len(facts.upstream_changed_files or ())} "
        "changed file(s) and none of them one this lint measures, so a newer sync buys no slack",
        f"{evidence.SIZE_CHECKER} enforces this budget by shrinking a file: the number is the "
        "context an agent is truncated at, not a style preference",
    ]
    return Verdict(
        check=check.name,
        kind=RedClass.BRAND_INFLATION,
        evidence=tuple(lines),
        owner=OWNERS[RedClass.BRAND_INFLATION],
        action=(
            f"Shrink the branded text of {report.label.split(' + ')[0]} in 100Ways -- the "
            "transform's output or the fork-owned prose it widened -- until the chain measures "
            f"under the {report.cap} the checker reads."
        ),
    )


def _runner_capacity(
    check: FailedCheck,
    facts: SyncFacts,
    files: Sequence[str],
    claims: Sequence[evidence.WallClock],
    missing: list[str],
) -> Verdict | None:
    if not claims:
        return None
    blocked = False
    if not facts.delta_known:
        missing.append(
            f"an absolute wall-clock bound ({claims[0].budget}) is present, but with no upstream "
            "delta this red cannot be cleared of an upstream change"
        )
        blocked = True
    if facts.upstream_succeeded is None:
        missing.append(
            f"no upstream check-run for {check.name or facts.lane}: a wall-clock bound on the "
            "fork cannot be paired with the same test passing upstream on a bigger image"
        )
        blocked = True
    shape = facts.runner_shape()
    if not shape.forced_down:
        missing.append(
            f"the fork ran this job on {facts.fork_image or 'an unnamed image'} while upstream "
            f"asked {facts.upstream_image or 'an image this transform does not recognize'}: the "
            "brand did not take a bigger machine away from this lane, so a slow test here is "
            "not a capacity story"
        )
        blocked = True
    if blocked:
        return None
    if not facts.green_upstream(check.test_ids):
        missing.append(
            f"upstream gives no green comparison for {check.name}: a wall-clock bound that was "
            "never satisfiable upstream is a product bug, and this classifier cannot tell the "
            "two apart"
        )
        return None
    assertion = next((claim for claim in claims if claim.origin == "assertion"), claims[0])
    lines = [
        f"the failing assertion compares against an absolute wall clock: "
        f"{_budget_phrase(assertion)}",
        f"upstream ran {check.name} on {shape.requested}{_upstream_comparison(facts, check)}",
        f"the brand transform rewrites {shape.requested} to "
        f"{evidence.branded_image(shape.requested)} because this org has no runner that would "
        f"take the label, dropping the {shape.requested_cores} vCPU the job asked for",
    ]
    ceiling = evidence.worker_ceiling()
    workers = evidence.workers_in_log(check.log)
    if ceiling is not None:
        lines.append(
            f"the engine's own worker ceiling is {ceiling}"
            + (
                f", and this lane reported a fan-out of {'/'.join(str(count) for count in workers)}"
                if workers
                else f", where upstream runs one worker per core on {shape.requested_cores} cores"
            )
        )
    if files:
        lines.append(
            f"upstream changed none of the file(s) this red names "
            f"({', '.join(files[:3])}{' etc' if len(files) > 3 else ''}) {facts.baseline_phrase()}"
        )
    if facts.retried:
        lines.append(
            f"the lane retried and the same {len(check.test_ids)} test id(s) came back as a "
            "flake, which reports pressure rather than a new failure"
        )
    return Verdict(
        check=check.name,
        kind=RedClass.RUNNER_CAPACITY,
        evidence=tuple(lines),
        owner=OWNERS[RedClass.RUNNER_CAPACITY],
        action=(
            f"Report {check.name} as a fork runner-capacity red in the candidate's PR body and "
            "change no test and no product code; the existing worker ceiling is the only knob "
            "and it needs this lane's wall-clock budget measured against it first."
        ),
    )


def _unknown(check: FailedCheck, facts: SyncFacts, missing: Sequence[str]) -> Verdict:
    """Name every fact that did not measure out, and quote the hardware without claiming it.

    :func:`classify` guarantees ``missing`` is never empty -- every gate that declines adds
    what it was missing -- and every UNKNOWN ends with the same closing line, so a reader is
    never left with a verdict and no reason.

    The dossier's pressure reds (a connection-count assertion, a suspended child reporting as
    running) carry no bound in their failure text, so no capacity call is available for them.
    Their image facts are still what a reader needs next, so they are reported as a
    consistency observation -- never as a class, and never as a reason to close the red.
    """
    lines = list(missing)
    if facts.retried:
        lines.append(
            "the lane retried, which says the failure moved; it does not say what owns it"
        )
    shape = facts.runner_shape()
    if check.test_ids and shape.forced_down and facts.upstream_succeeded is True:
        lines.append(
            f"consistent with capacity, not called on it: this lane ran {shape.actual} where "
            f"{evidence.branded_image(shape.requested)} is what the brand transform can give for "
            f"upstream's {shape.requested} ({shape.requested_cores} vCPU) and upstream reported "
            f"none of the {len(check.test_ids)} test id(s) failing; a capacity call needs a bound "
            "in the failure text, and this text carries none"
        )
    lines.append(
        "no class is claimed here, because the measurement that would justify it is absent"
    )
    return Verdict(
        check=check.name,
        kind=RedClass.UNKNOWN,
        evidence=tuple(lines),
        owner=OWNERS[RedClass.UNKNOWN],
        action=(
            f"Fill in the missing fact named above for {check.name} before filing this red as "
            "anything, and do not treat it as hardware noise."
        ),
    )


def _upstream_comparison(facts: SyncFacts, check: FailedCheck) -> str:
    """Say what upstream's own lane reported about these tests, in the form the evidence allows.

    A green upstream lane and a red one that names other tests are different statements, and
    the capacity call is entitled to whichever one was actually measured.
    """
    if facts.upstream_succeeded:
        comparison = " and reported no failing test at all"
    else:
        comparison = (
            f" and its own red names {len(facts.upstream_failed_tests)} other test id(s), "
            "none of them a test this red reports"
        )
    return (
        f"{comparison}; the fork ran the same commit on {facts.fork_image} and reported "
        f"{len(check.test_ids)} failing test id(s)"
    )


def _budget_phrase(claim: evidence.WallClock) -> str:
    parts = [f"bound {claim.budget}"]
    if claim.bound_seconds is not None:
        parts.append(f"= {_seconds(claim.bound_seconds)}")
    if claim.observed_seconds is not None:
        parts.append(f"observed {_seconds(claim.observed_seconds)}")
    parts.append(f"reported as an {claim.origin}")
    return " ".join(parts)


def _seconds(value: float) -> str:
    return f"{value:g} s"


def _is_size_lint(check: FailedCheck) -> bool:
    return bool(_SIZE_LINT_TEXT.search(check.name) or _SIZE_LINT_TEXT.search(check.log))


def failing_checks(runs: Mapping[str, Any]) -> tuple[FailedCheck, ...]:
    """Every red in a check-runs payload, aggregate reporters left out.

    ``conclusion`` is read against :data:`RED_CONCLUSIONS`, so a lane that timed out or was
    cancelled is reported rather than dropped: only a completed-and-successful lane is.
    """
    return tuple(
        FailedCheck.from_check_run(run)
        for run in _runs(runs)
        if run.get("conclusion") in RED_CONCLUSIONS
        and not any(hint in str(run.get("name", "")).lower() for hint in _AGGREGATE_HINTS)
    )


def classify_runs(
    fork_runs: Mapping[str, Any],
    *,
    upstream_runs: Mapping[str, Any] | None = None,
    upstream_changed_files: Sequence[str] | None = None,
    baseline_sha: str = "",
    current_sha: str = "",
    checkout_path: str = "",
) -> tuple[Verdict, ...]:
    """Sort every red in a payload: facts gathered once, then each lane judged on its own."""
    verdicts: list[Verdict] = []
    for check in failing_checks(fork_runs):
        facts = SyncFacts.from_check_runs(
            fork_runs,
            lane=check.name,
            upstream_runs=upstream_runs,
            upstream_changed_files=upstream_changed_files,
            baseline_sha=baseline_sha,
            current_sha=current_sha,
            checkout_path=checkout_path,
        )
        verdicts.append(classify(check, facts))
    return tuple(verdicts)


def render(verdicts: Sequence[Verdict]) -> str:
    """Plain text, one block per red, fit for a Telegram message and a PR body.

    No markup at all: this repository delivers plain text because Telegram silently drops a
    whole message whose emphasis does not balance.  Lines are built from names and measured
    numbers, never from quoted log text, and each one still passes the engine's canonical
    secret shapes.
    """
    if not verdicts:
        return "Fork CI red classification: no red checks to classify.\n"
    counts = ", ".join(
        f"{kind.value} {sum(1 for verdict in verdicts if verdict.kind is kind)}"
        for kind in RedClass
        if any(verdict.kind is kind for verdict in verdicts)
    )
    lines = [f"Fork CI red classification: {len(verdicts)} red check(s) ({counts})", ""]
    for index, verdict in enumerate(verdicts, start=1):
        lines.append(f"{index}. {verdict.check}")
        lines.append(f"   class: {verdict.kind.value}")
        lines.append(f"   owner: {verdict.owner}")
        lines.append(f"   next: {verdict.action}")
        lines.append("   evidence:")
        lines.extend(f"     {item}" for item in verdict.evidence)
        lines.append("")
    lines.append("No verdict moves a cap, a budget, or a guard, and none skips a test.")
    return "\n".join(redact(line) for line in lines).rstrip("\n") + "\n"


def _runs(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    items = payload.get(CHECK_RUNS_KEY) or []
    return tuple(item for item in items if isinstance(item, dict))


def _pick(runs: Sequence[Mapping[str, Any]], lane: str) -> Mapping[str, Any] | None:
    """The check-run for this lane: its own name first, then the same lane across the brand.

    A branded job keeps the shape of upstream's name but not its spelling, so a fork lane named
    after Nastech still has to find the upstream run named after Hermes.  Exact identity always
    wins: the reversed comparison is only reached when no run in the payload carries the name.
    """
    for run in runs:
        if str(run.get("name", "")) == lane:
            return run
    reversed_lane = evidence.unbrand(lane)
    for run in runs:
        if evidence.unbrand(str(run.get("name", ""))) == reversed_lane:
            return run
    return None

