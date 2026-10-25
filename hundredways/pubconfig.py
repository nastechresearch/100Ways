"""Publisher configuration: name, before a run, the credential a fork job will ask for and not get.

A rebrand fork inherits its upstream's Actions surface by name only.  Upstream's
workflows reference signing and publishing credentials that belong to upstream's
organisation, and the fork's settings do not carry them -- so the lane that needs
them is red, and it is red for a reason no check in the tree can see: the missing
thing is not a file, it is a repository setting.  The fork CI triage dossier calls
this the fourth class, and this module is the version of it that runs *before*
dispatch: it reads the credential names out of the fork's own workflows, reads the
names the fork's Actions settings actually provide, and says which gap will burn.

Two rules shape every decision here.

**Names only, never values.**  This module must not be able to leak a credential,
so no code path reads a value: the workflow parse keeps only the ``secrets.X`` /
``vars.X`` identifier, and the ``gh`` calls ask the API for the ``name`` field and
nothing else.  A rendered report passes :func:`hundredways.secrets.redact` line by
line anyway, so a value that somehow entered the text cannot leave in it.  Secret
*names* are not secret -- they are in the workflow source -- and naming them is the
whole point, because the owner has to type them into a settings page.

**A gap is not a finding until the lane that would consume it can actually run.**
The fork deliberately publishes from Linux and Termux while the signing accounts
are unconfigured, and the engine encodes that as an all-or-nothing credential gate
(:data:`hundredways.updates.SIGNING_GATED_LANES`, ``STORE_CREDENTIALS``).  So an
absent ``CSC_LINK`` is the *documented state of the fork*, not a red, and reporting
it as one would train the owner to ignore the report.  What is a finding is a name
that no gate covers: GitHub substitutes the empty string for an unset credential,
so a job that runs with a hole in it fails somewhere far from the cause.

Deliberately absent: any attempt to read the *upstream* inventory.  Listing another
organisation's secrets needs permissions the fork does not have, and an empty
inventory read as "configured" would report every gap as closed.  A fetch that
fails raises instead of answering.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .secrets import redact
from .updates import (
    SIGNING_CREDENTIAL_CONTEXT,
    SIGNING_GATED_LANES,
    STORE_CREDENTIALS,
)

__all__ = [
    "CREDENTIAL_CONTEXTS",
    "CONFIGURED",
    "DECLARED_SKIP",
    "PARTIAL_GATE",
    "UNGATED_CONSUMED",
    "LIVE_MISSING",
    "PublisherConfigurationError",
    "CredentialReference",
    "CredentialGate",
    "ActionsInventory",
    "Finding",
    "VERDICT_ORDER",
    "workflow_references",
    "credential_gates",
    "load_workflows",
    "analyze",
    "analyze_root",
    "fetch_inventory",
    "render",
    "to_json",
]


class PublisherConfigurationError(RuntimeError):
    """The configuration could not be read, so no answer about it may be given."""


# Verdicts, ordered from "nothing to do" to "this red is already booked".  Each is
# produced by a measurement, never by a name pattern.
CONFIGURED = "configured"  # provided at repo scope or in the job's environment
DECLARED_SKIP = "declared-skip"  # the gate that owns this name sees none of it
PARTIAL_GATE = "partial-gate"  # some of the gate configured, so still skipped
UNGATED_CONSUMED = "ungated-consumed"  # a gated job consumes a name the gate cannot see
LIVE_MISSING = "live-missing"  # no gate covers it; the job gets an empty string

VERDICT_ORDER = (LIVE_MISSING, UNGATED_CONSUMED, PARTIAL_GATE, DECLARED_SKIP, CONFIGURED)

#: The contexts a credential can be read from in workflow expressions.  Only these
#: two are looked for: ``inputs.*`` is per-dispatch and ``env.*`` is set by the job
#: itself, so neither is a repository setting and neither is this module's business.
CREDENTIAL_CONTEXTS = ("secrets", "vars")

# ``GITHUB_TOKEN`` is minted per job by the runner and cannot be absent, and
# ``GH_TOKEN`` is the fork's name for a PAT the workflow may or may not use.  Both
# would otherwise appear in every report forever.
_IGNORED_NAMES = frozenset({"GITHUB_TOKEN", "GH_TOKEN"})

# GitHub's own shape rule for an Actions secret or variable name.  Used to keep a
# stray stdout line out of the inventory, not to judge whether a name is real.
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# ``${{ secrets.FOO }}`` with any inner spacing, and the bare ``secrets.FOO`` form
# that appears inside ``||`` expressions and shell-quoted defaults.  The capture is
# the identifier only, which is what keeps a value out of this module's reach.
# Built from :data:`CREDENTIAL_CONTEXTS` so the parser and the documented scope set
# cannot drift apart, and matched case-sensitively because ``Secrets.FOO`` in prose
# is not an expression.
_REFERENCE_RE = re.compile(
    r"(?P<kind>" + "|".join(CREDENTIAL_CONTEXTS) + r")\s*\.\s*(?P<name>[A-Za-z][A-Za-z0-9_]*)"
)
_COMMENT_LINE_RE = re.compile(r"^[ \t]*#.*$", re.MULTILINE)
_JOBS_KEY_RE = re.compile(r"^jobs:[ \t]*(?:#.*)?$", re.MULTILINE)
_JOB_KEY_RE = re.compile(r"^  (?P<job>[A-Za-z0-9_-]+):[ \t]*(?:#.*)?$", re.MULTILINE)
# A job's environment is either inline (``environment: release-signing``) or a
# mapping whose ``name:`` key is indented under it.  Quoted names carry spaces and
# an en dash (``"Production – docs"``), so the value is read as "until the comment".
_INLINE_ENV_RE = re.compile(
    r"^[ \t]+environment:[ \t]*['\"]?(?P<name>[^'\"\n#]+)", re.MULTILINE
)
_BLOCK_ENV_RE = re.compile(
    r"^[ \t]+environment:[ \t]*(?:#.*)?\n[ \t]+name:[ \t]*['\"]?(?P<name>[^'\"\n#]+)",
    re.MULTILINE,
)
_JOB_IF_RE = re.compile(r"^ {4}if:[ \t]*(?P<condition>.*\S)", re.MULTILINE)


@dataclass(frozen=True)
class CredentialReference:
    """One credential *name* a job reads, and where that job runs."""

    name: str
    kind: str  # "secrets" or "vars"
    workflow: str
    job: str
    environment: str  # "" when the job declares no environment
    line: int
    guard: str = ""  # the job-level ``if:``, when it has one

    @property
    def location(self) -> str:
        scope = f" (environment: {self.environment})" if self.environment else ""
        return f"{self.workflow}:{self.job}{scope}"


@dataclass(frozen=True)
class CredentialGate:
    """A lane whose existence the fork decides from the presence of its credentials.

    ``admitted_in_workflow`` records whether the engine actually wired these names
    into the release claim's admit step.  A gate that is only a Python constant can
    skip work inside one script and leave the workflow's own job running anyway, and
    a report that treated the two alike would excuse a live hole.
    """

    label: str
    names: frozenset[str]
    admitted_in_workflow: bool = False

    def covers(self, name: str) -> bool:
        return name in self.names


@dataclass(frozen=True)
class ActionsInventory:
    """Every credential *name* a repository provides, by scope.  Never a value."""

    repo_secrets: frozenset[str] = frozenset()
    repo_vars: frozenset[str] = frozenset()
    environments: Mapping[str, tuple[frozenset[str], frozenset[str]]] = field(
        default_factory=dict
    )

    def provides(self, kind: str, name: str, environment: str = "") -> bool:
        """True when ``name`` is set at repo scope, or in ``environment``.

        A job with an environment sees the repository's settings *and* that
        environment's, so either one satisfies the reference.  Reporting a
        repo-scope credential as missing because the job pins an environment would
        send the owner to create a duplicate.
        """
        if kind == "secrets":
            if name in self.repo_secrets:
                return True
            names = self.environments.get(environment, (frozenset(), frozenset()))
            return name in names[0]
        if name in self.repo_vars:
            return True
        names = self.environments.get(environment, (frozenset(), frozenset()))
        return name in names[1]

    @property
    def total_names(self) -> int:
        """Count of distinct ``(context, name)`` pairs the settings provide."""
        pairs = {("secrets", name) for name in self.repo_secrets}
        pairs |= {("vars", name) for name in self.repo_vars}
        for secrets_, vars_ in self.environments.values():
            pairs |= {("secrets", name) for name in secrets_}
            pairs |= {("vars", name) for name in vars_}
        return len(pairs)


@dataclass(frozen=True)
class Finding:
    """One credential name, its verdict, and the evidence that produced it."""

    name: str
    kind: str
    verdict: str
    scopes: tuple[str, ...]  # environments that read it, () when unscoped
    references: tuple[CredentialReference, ...]
    gate: str = ""  # the lane whose gate owns it
    note: str = ""

    @property
    def actionable(self) -> bool:
        """True when a run can consume this hole, so the owner must decide."""
        return self.verdict in (LIVE_MISSING, UNGATED_CONSUMED)

    def evidence(self) -> tuple[str, ...]:
        lines = [
            f"{reference.workflow}:{reference.line}: "
            f"{reference.kind}.{reference.name} in job {reference.job}"
            for reference in self.references[:3]
        ]
        if len(self.references) > 3:
            lines.append(
                f"    and {len(self.references) - 3} more job(s) reading {self.name}"
            )
        if self.gate:
            lines.append(f"    gate: {self.gate}")
        if self.note:
            lines.append(f"    {self.note}")
        return tuple(lines)


def credential_gates() -> tuple[CredentialGate, ...]:
    """The fork's credential-driven lane gates, read from the engine's own tables.

    ``SIGNING_GATED_LANES`` carries the desktop lanes and the names each needs; lanes
    that share a name set share one decision, so they are merged into one gate rather
    than reported three times.  The Store submission is gated separately -- the
    engine folds ``STORE_CREDENTIALS`` into ``scripts/releases/store.py``, where an
    unconfigured set raises ``StoreError`` and the sequencer warns -- so it is added
    as its own gate and marked *not* workflow-admitted, because no ``admit`` output
    reads it.  A gate with no names is not a credential gate at all.
    """
    wired = frozenset(SIGNING_CREDENTIAL_CONTEXT)
    grouped: dict[frozenset[str], list[str]] = {}
    for lane in SIGNING_GATED_LANES:
        if lane.credentials:
            grouped.setdefault(frozenset(lane.credentials), []).append(lane.label)
    grouped.setdefault(frozenset(STORE_CREDENTIALS), []).append("Microsoft Store submission")
    return tuple(
        CredentialGate(
            label=" / ".join(labels),
            names=names,
            admitted_in_workflow=bool(names & wired),
        )
        for names, labels in grouped.items()
    )


def _job_blocks(workflow_text: str) -> list[tuple[str, int, int, str]]:
    """``(job, start, end, guard)`` for each job key under the top-level ``jobs:``.

    Only text after ``jobs:`` is scanned: the ``on:`` block uses the same two-space
    indent for trigger keys, and treating ``push`` as a job would invent lanes that
    no workflow has.
    """
    jobs_key = _JOBS_KEY_RE.search(workflow_text)
    if jobs_key is None:
        return []
    matches = [
        match
        for match in _JOB_KEY_RE.finditer(workflow_text)
        if match.start() >= jobs_key.end()
    ]
    blocks: list[tuple[str, int, int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(workflow_text)
        body = workflow_text[match.start():end]
        guard_match = _JOB_IF_RE.search(body)
        blocks.append(
            (match.group("job"), match.start(), end, guard_match.group("condition").strip()
             if guard_match else "")
        )
    return blocks


def _environment_of(body: str) -> str:
    block = _BLOCK_ENV_RE.search(body)
    if block is not None:
        return block.group("name").strip()
    inline = _INLINE_ENV_RE.search(body)
    if inline is None:
        return ""
    value = inline.group("name").strip()
    # ``environment: { name: x }`` is not valid Actions YAML, but a comment on the
    # inline form is, and a trailing comment must not become part of a name.
    return value.split(" #", 1)[0].strip()


def workflow_references(
    workflow_text: str, *, workflow: str = "<workflow>"
) -> tuple[CredentialReference, ...]:
    """Every credential name each job in one workflow reads, with its scope.

    A name is attributed to the job whose text contains it, so a ``secrets.X`` used
    in a step of job A is never counted against job B -- which matters because the
    verdict depends on the environment *job A* pins.  Repeats of the same name in
    the same job collapse to the earliest line.

    Whole-line comments are blanked before the scan, because a commented-out step
    reads nothing; the blanking keeps every newline so reported line numbers still
    point at the source.  A trailing comment after live code is left alone: cutting
    at ``#`` cannot be done safely inside ``${{ }}`` or a quoted shell string, and a
    name named only in prose is harmless, since it is reported as a reference the
    owner can check in one second.
    """
    found: dict[tuple[str, str, str], CredentialReference] = {}
    for job, start, _end, guard in _job_blocks(workflow_text):
        body = workflow_text[start:_end]
        environment = _environment_of(body)
        offset = workflow_text[:start].count("\n") + 1
        for match in _REFERENCE_RE.finditer(_COMMENT_LINE_RE.sub("", body)):
            kind = match.group("kind")
            name = match.group("name")
            if name in _IGNORED_NAMES:
                continue
            line = offset + body[: match.start()].count("\n")
            key = (name, job, environment)
            existing = found.get(key)
            if existing is not None and existing.line <= line:
                continue
            found[key] = CredentialReference(
                name=name,
                kind=kind,
                workflow=workflow,
                job=job,
                environment=environment,
                line=line,
                guard=guard,
            )
    return tuple(sorted(found.values(), key=lambda r: (r.name, r.workflow, r.line)))


def load_workflows(root: Path | str) -> tuple[tuple[str, str], ...]:
    """``(filename, text)`` for every workflow in a tree, sorted by name."""
    directory = Path(root) / ".github" / "workflows"
    if not directory.is_dir():
        raise PublisherConfigurationError(f"{directory} is not a directory")
    out: list[tuple[str, str]] = []
    for path in sorted(directory.iterdir()):
        if path.suffix not in (".yml", ".yaml"):
            continue
        out.append((path.name, path.read_text(encoding="utf-8", errors="replace")))
    if not out:
        raise PublisherConfigurationError(f"{directory} holds no workflows")
    return tuple(out)


def _owning_gate(
    name: str, gates: Sequence[CredentialGate], configured: frozenset[str]
) -> tuple[CredentialGate | None, str, tuple[str, ...]]:
    """The workflow-gated lane that owns ``name``, and whether its set is complete.

    Only a gate the engine wired into the workflow can declare a lane off: the
    all-or-nothing decision is an ``include-*`` output built from the names in
    :data:`hundredways.updates.SIGNING_CREDENTIAL_CONTEXT`, and a credential the
    admit step never maps cannot switch anything off, however tidy its name looks.
    That distinction is why the Store names are not excused here -- see
    :func:`credential_gates`.
    """
    for gate in gates:
        if not gate.admitted_in_workflow or not gate.covers(name):
            continue
        present = tuple(sorted(gate.names & configured))
        return gate, (PARTIAL_GATE if present else DECLARED_SKIP), present
    return None, "", ()


def _lane_this_job_belongs_to(
    name: str, job_names: frozenset[str], gates: Sequence[CredentialGate]
) -> CredentialGate | None:
    """A gate whose own names dominate what this job reads, but which omits ``name``.

    The test is a majority of the gate's set, not one shared name: a job that happens
    to read ``CSC_LINK`` beside an unrelated credential is not doing that lane's work,
    and calling it would invent drift the owner cannot fix.  A gate that already
    covers ``name`` is not drift either -- that is the gate working.  What this looks
    for is the one shape no gate can catch: a job that *is* doing the lane's work and
    consumes a name the lane's set never lists.
    """
    for gate in gates:
        if gate.covers(name):
            continue
        if len(job_names & gate.names) * 2 > len(gate.names):
            return gate
    return None


def analyze(
    references: Sequence[CredentialReference],
    inventory: ActionsInventory,
    *,
    gates: Sequence[CredentialGate] | None = None,
) -> tuple[Finding, ...]:
    """Give every referenced name that is missing a verdict, most urgent first.

    Precedence is a chain of measurements, not wording.  A name is dropped when any
    scope that reads it provides it.  Otherwise: it belongs to a workflow-wired gate
    (the engine already took that lane out, so the gap is documented state); or a job
    that clearly works for a lane consumes it while the lane's set omits it (the gate
    can be satisfied and the hole stays open); or nothing covers it, so a job that
    runs reads an empty string.  The last two are the actionable ones, and the only
    two a report should lead with.
    """
    active_gates = credential_gates() if gates is None else tuple(gates)
    by_name: dict[tuple[str, str], list[CredentialReference]] = {}
    names_per_job: dict[tuple[str, str], set[str]] = {}
    for reference in references:
        by_name.setdefault((reference.name, reference.kind), []).append(reference)
        names_per_job.setdefault((reference.workflow, reference.job), set()).add(reference.name)

    # Configured-ness is measured over every scope that reads the name, because
    # GitHub resolves repository settings into an environment job too.  Reporting a
    # repo-scope credential as missing because the job pins an environment would
    # send the owner to create a duplicate of something that already works.
    configured = frozenset(
        name
        for (name, kind), refs in by_name.items()
        if any(inventory.provides(kind, name, reference.environment) for reference in refs)
    )

    findings: list[Finding] = []
    for (name, kind), unsorted_refs in sorted(by_name.items()):
        if name in configured:
            continue
        refs = tuple(sorted(unsorted_refs, key=lambda r: (r.workflow, r.line)))
        scopes = tuple(sorted({r.environment for r in refs if r.environment}))
        gate, verdict, present = _owning_gate(name, active_gates, configured)
        if gate is not None:
            if verdict == PARTIAL_GATE:
                note = (
                    f"{gate.label} is admitted only when all {len(gate.names)} name(s) "
                    f"are set; already configured: {', '.join(present)}"
                )
            else:
                note = (
                    f"no name for {gate.label} is configured, so the fork declares that "
                    "lane off and none of these jobs runs"
                )
            findings.append(
                Finding(
                    name=name, kind=kind, verdict=verdict, scopes=scopes,
                    references=refs, gate=gate.label, note=note,
                )
            )
            continue
        job_names: frozenset[str] = frozenset(
            n for r in refs for n in names_per_job[(r.workflow, r.job)]
        )
        owner = _lane_this_job_belongs_to(name, job_names, active_gates)
        if owner is not None:
            findings.append(
                Finding(
                    name=name, kind=kind, verdict=UNGATED_CONSUMED, scopes=scopes,
                    references=refs, gate=owner.label,
                    note=(
                        f"{owner.label} decides its lane on "
                        f"{', '.join(sorted(owner.names))} and this name is not one of "
                        "them, so the lane can be fully admitted while the job reads it "
                        "as an empty string"
                    ),
                )
            )
            continue
        guard = next((r.guard for r in refs if r.guard), "")
        note = "no credential gate covers this name, so every job above reads an empty string"
        if guard:
            note += f"; the lane runs only when: {guard}"
        findings.append(
            Finding(
                name=name, kind=kind, verdict=LIVE_MISSING, scopes=scopes,
                references=refs, note=note,
            )
        )
    return tuple(
        sorted(findings, key=lambda f: (VERDICT_ORDER.index(f.verdict), f.kind, f.name))
    )


def _gh(run: Callable[..., Any], path: str, *flags: str) -> tuple[str, ...]:
    """One ``gh api`` call, with the query written inside the path.

    ``--jq`` is asked for the ``name`` field alone, so a value never reaches this
    process.  A non-zero exit is fatal: an inventory read as empty is
    indistinguishable from an inventory read as complete, and this is the one place a
    silent failure would report every hole as closed.
    """
    result = run(["gh", "api", "--paginate", path, *flags], capture_output=True, text=True)
    if getattr(result, "returncode", 1) != 0:
        detail = (getattr(result, "stderr", "") or getattr(result, "stdout", "")).strip()
        tail = detail.splitlines()[-1] if detail else "the call failed"
        raise PublisherConfigurationError(
            f"{path} could not be read; no configuration verdict may be given: "
            f"{redact(tail)}"
        )
    return tuple(line.strip() for line in (getattr(result, "stdout", "") or "").splitlines()
                 if line.strip())


def _identifiers(lines: Sequence[str]) -> tuple[str, ...]:
    """Keep only the lines that look like a credential identifier.

    A banner or progress line that reached stdout would otherwise become a
    "configured" name and excuse a real hole, so the shape filter runs on every
    credential list.  It is not applied to environment names.
    """
    return tuple(name for name in lines if _NAME_RE.match(name))


def fetch_inventory(
    repo: str, *, run: Callable[..., Any] = subprocess.run
) -> ActionsInventory:
    """Every credential name ``repo`` provides, read through ``gh``.

    Requires a token that can read the repository's Actions settings; the fork's own
    repository is fine and another organisation's is not, which is why the upstream
    side of a comparison cannot be fetched and is not attempted.

    Environment names are kept exactly as the API returned them, spaces and en dashes
    included, because they are a scope rather than a credential.
    """
    secrets_ = frozenset(
        _identifiers(_gh(run, f"repos/{repo}/actions/secrets?per_page=100",
                         "--jq", ".secrets[].name"))
    )
    vars_ = frozenset(
        _identifiers(_gh(run, f"repos/{repo}/actions/variables?per_page=100",
                         "--jq", ".variables[].name"))
    )
    environments: dict[str, tuple[frozenset[str], frozenset[str]]] = {}
    for environment in _gh(run, f"repos/{repo}/environments?per_page=100",
                           "--jq", ".environments[].name"):
        quoted = urllib.parse.quote(environment, safe="")
        environments[environment] = (
            frozenset(_identifiers(
                _gh(run, f"repos/{repo}/environments/{quoted}/secrets?per_page=100",
                    "--jq", ".secrets[].name"))),
            frozenset(_identifiers(
                _gh(run, f"repos/{repo}/environments/{quoted}/variables?per_page=100",
                    "--jq", ".variables[].name"))),
        )
    return ActionsInventory(repo_secrets=secrets_, repo_vars=vars_, environments=environments)


_ACTIONABLE = {
    LIVE_MISSING: "configure it, or gate the lane that reads it",
    UNGATED_CONSUMED: "add the name to the gate's set in the engine, or configure it",
    PARTIAL_GATE: "finish the gate or leave it off; a half-set is already a skip",
    DECLARED_SKIP: "nothing: the fork publishes without this lane by design",
}


def render(
    findings: Sequence[Finding],
    *,
    repo: str = "",
    inventory: ActionsInventory | None = None,
) -> str:
    """Plain text, one block per verdict, every line redacted.

    Plain text because the report is delivered to Telegram, which drops a message
    whose emphasis does not balance.  A secret *name* is kept as written -- the
    owner has to type it -- while the line it appears on is still redacted, so a
    credential-shaped run cannot be echoed out of the tree.
    """
    title = f"Publisher configuration: {repo}" if repo else "Publisher configuration"
    lines = [title]
    counts = {verdict: 0 for verdict in VERDICT_ORDER}
    for finding in findings:
        counts[finding.verdict] += 1
    if inventory is not None:
        lines.append(
            f"  names provided: {len(inventory.repo_secrets)} repo secrets, "
            f"{len(inventory.repo_vars)} repo vars, {len(inventory.environments)} environment(s)"
        )
    lines.append(
        f"  verdicts: live {counts[LIVE_MISSING]}, ungated {counts[UNGATED_CONSUMED]}, "
        f"partial {counts[PARTIAL_GATE]}, declared-off {counts[DECLARED_SKIP]}"
    )
    if not findings:
        lines.append("  every credential name the workflows read is provided")
        return "\n".join(lines)
    for verdict in VERDICT_ORDER:
        block = [finding for finding in findings if finding.verdict == verdict]
        if not block:
            continue
        lines.append("")
        lines.append(f"{verdict} ({len(block)}) -- {_ACTIONABLE[verdict]}")
        for finding in block:
            where = ", ".join(finding.scopes) if finding.scopes else "repo scope"
            lines.append(f"  {finding.kind}.{finding.name} [{where}]")
            for evidence in finding.evidence():
                lines.append(f"    {evidence}")
    return "\n".join(redact(line) for line in lines)


def to_json(findings: Sequence[Finding], *, repo: str = "") -> str:
    """Machine-readable form of the same answer, values still absent."""
    payload: dict[str, Any] = {
        "gate": "publisher-configuration",
        "repo": repo,
        "actionable": sum(1 for finding in findings if finding.actionable),
        "findings": [
            {
                "name": finding.name,
                "context": finding.kind,
                "verdict": finding.verdict,
                "environments": list(finding.scopes),
                "gate": finding.gate,
                "note": finding.note,
                "evidence": list(finding.evidence()),
                "actionable": finding.actionable,
            }
            for finding in findings
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def analyze_root(
    root: Path | str,
    inventory: ActionsInventory,
    *,
    workflow_files: Sequence[tuple[str, str]] | None = None,
    gates: Sequence[CredentialGate] | None = None,
) -> tuple[Finding, ...]:
    """Convenience: whole tree to verdicts, so a caller needs one call."""
    files = load_workflows(root) if workflow_files is None else tuple(workflow_files)
    references = [
        reference
        for name, text in files
        for reference in workflow_references(text, workflow=name)
    ]
    return analyze(references, inventory, gates=gates)
