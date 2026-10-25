"""The error feed: which lane turned red, which cleared, and which one nobody owns yet.

:mod:`hundredways.red_classifier` already answers "who owns this red" for one payload.
That answer is a snapshot, and a snapshot cannot tell an operator anything they need at
03:00: the only thing worth interrupting for is *change*.  This module keeps the small
piece the classifier does not -- the set of lanes that were red last cycle and the class
each was given -- and reports the delta between cycles:

* ``new`` -- red this cycle, absent from the previous state;
* ``ongoing`` -- still red, still the same class, so nothing new has happened to it;
* ``reclassified`` -- still red, but the evidence now points at a different owner;
* ``resolved`` -- present last cycle and not in this one, **only** when the payload is
  provably complete.

That last condition is the reason this module exists as its own file.  GitHub's
``commits/{sha}/check-runs`` endpoint paginates, so a payload that lists 30 of 61 runs
silently omits lanes.  Reading such a payload as "those lanes went green" would be the
most damaging lie this pipeline could tell: it would look like a fix landed.  A partial
read therefore yields no ``resolved`` events at all and reports the lanes it cannot
account for instead.

Nothing here reads a clock.  The ``at:`` stamp is the payload's own ``updated_at`` (or
``completed_at``) for that lane, so a feed built from the same two files is byte-for-byte
reproducible; a lane whose run carries no timestamp says so rather than borrowing one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .red_classifier import CHECK_RUNS_KEY, OWNERS, RedClass, Verdict, classify_runs
from .secrets import redact

__all__ = [
    "SCHEMA",
    "NEW",
    "FIRST",
    "ONGOING",
    "RECLASSIFIED",
    "RESOLVED",
    "UNPROVED",
    "KINDS",
    "ErrorFeedError",
    "Event",
    "Feed",
    "observe",
    "load_state",
    "save_state",
    "render",
    "to_json",
]

SCHEMA = "100ways.errorfeed/v1"

NEW = "new"
ONGOING = "ongoing"
RECLASSIFIED = "reclassified"
RESOLVED = "resolved"
# Nothing can honestly be called *new* on the first cycle: there is no earlier state to
# be new against.  It is its own kind so a feed that has just started cannot read as a
# lane that just broke.
FIRST = "first"
# A lane in the previous state that this payload does not list, on a read too partial to
# call a clearance.  It is not an event about the CI: it is a hole in this observation.
UNPROVED = "unproved"

KINDS: tuple[str, ...] = (NEW, FIRST, ONGOING, RECLASSIFIED, RESOLVED, UNPROVED)

_UNNAMED = "(unnamed check run)"


class ErrorFeedError(RuntimeError):
    """The previous state could not be read, so no change can be reported."""


@dataclass(frozen=True)
class Event:
    """One movement between two cycles, phrased so it can be sent as-is."""

    lane: str
    kind: str
    detail: str
    cls: str = ""
    owner: str = ""
    action: str = ""
    stamp: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "lane": self.lane,
            "kind": self.kind,
            "detail": self.detail,
            "class": self.cls,
            "owner": self.owner,
            "action": self.action,
            "at": self.stamp,
        }


@dataclass(frozen=True)
class Feed:
    """What changed, what it is now, and whether this observation was able to know."""

    events: tuple[Event, ...]
    verdicts: tuple[Verdict, ...]
    reds: Mapping[str, str]
    compared: bool
    complete: bool
    partial_reason: str = ""

    def count(self, kind: str) -> int:
        return sum(1 for event in self.events if event.kind == kind)

    @property
    def unowned(self) -> tuple[str, ...]:
        """Lanes the classifier could not place -- the ones still needing a human.

        An ``UNKNOWN`` verdict is not a small verdict: it is the one case where nobody
        has been named yet, so it has to be readable apart from the counted classes.
        """
        return tuple(
            verdict.check
            for verdict in self.verdicts
            if verdict.kind is RedClass.UNKNOWN
        )

    @property
    def untimed(self) -> int:
        """Events whose lane carried no timestamp in the payload."""
        return sum(1 for event in self.events if not event.stamp and event.kind != UNPROVED)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "compared": self.compared,
            "complete": self.complete,
            "partial": self.partial_reason,
            "reds": dict(sorted(self.reds.items())),
            "counts": {kind: self.count(kind) for kind in KINDS if self.count(kind)},
            "unowned": list(self.unowned),
            "untimed": self.untimed,
            "events": [event.to_dict() for event in self.events],
        }


def _lane(verdict: Verdict) -> str:
    return verdict.check or _UNNAMED


def _stamp(runs: Mapping[str, Any], lane: str) -> str:
    """The payload's own time for this lane, or "" when the payload did not record one."""
    for run in _run_list(runs):
        if str(run.get("name", "")) == lane or (not lane and not run.get("name")):
            return str(run.get("updated_at") or run.get("completed_at") or "")
    return ""


def _run_list(runs: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    items = runs.get(CHECK_RUNS_KEY) or []
    return tuple(item for item in items if isinstance(item, dict))


def _partial_read(runs: Mapping[str, Any]) -> str:
    """Why this payload cannot prove a clearance, or "" when it can.

    ``total_count`` is GitHub's own tally of runs for the commit, so a mismatch against
    what was actually listed is measured evidence of truncation -- not a guess about
    whether the caller paginated.
    """
    listed = len(_run_list(runs))
    declared = runs.get("total_count")
    if isinstance(declared, int) and declared != listed:
        return f"payload lists {listed} of {declared} check run(s)"
    if declared is None:
        return "payload records no total_count, so nothing here proves a lane went green"
    return ""


def _owner_of(cls: str) -> str:
    try:
        return OWNERS[RedClass(cls)]
    except (ValueError, KeyError):
        return ""


def observe(
    runs: Mapping[str, Any],
    *,
    previous: Mapping[str, str] | None = None,
    **facts: Any,
) -> Feed:
    """Diff one check-runs payload against the last cycle's reds.

    ``previous`` is the lane -> class map from the last feed (or ``None`` for a first
    cycle, which is reported as such instead of being read as "everything is new").
    The keyword arguments after it are handed straight to
    :func:`hundredways.red_classifier.classify_runs`, so the classification a feed
    reports is the same one ``why-red`` would print for the same payload.
    """
    verdicts = classify_runs(runs, **facts)
    before = dict(previous or {})
    compared = bool(previous)
    reason = _partial_read(runs)
    complete = not reason
    reds = {_lane(verdict): verdict.kind.value for verdict in verdicts}
    events: list[Event] = []
    for verdict in verdicts:
        lane = _lane(verdict)
        was = before.get(lane)
        if was is None:
            if not compared:
                kind = FIRST
                detail = "red this cycle; first cycle recorded, so novelty is not claimed"
            else:
                kind = NEW
                detail = "red this cycle, absent from the previous state"
                if not complete:
                    detail += "; this read is partial, so a lane absent from it proves nothing"
        elif was == verdict.kind.value:
            kind = ONGOING
            detail = f"still {was}, unchanged since the previous cycle"
        else:
            kind = RECLASSIFIED
            detail = f"was {was}: the evidence now names a different owner"
        events.append(
            Event(
                lane=lane,
                kind=kind,
                detail=detail,
                cls=verdict.kind.value,
                owner=verdict.owner,
                action=verdict.action,
                stamp=_stamp(runs, verdict.check),
            )
        )
    seen = set(reds)
    for lane in sorted(before):
        if lane in seen:
            continue
        if complete:
            events.append(
                Event(
                    lane=lane,
                    kind=RESOLVED,
                    detail=f"cleared: was {before[lane]}",
                    cls=before[lane],
                    owner=_owner_of(before[lane]),
                    action="nothing: this lane is not red in the payload that was read",
                    stamp=_stamp(runs, lane),
                )
            )
            continue
        events.append(
            Event(
                lane=lane,
                kind=UNPROVED,
                detail=f"not in this payload ({reason}): no clearance claimed",
                cls=before[lane],
                owner=_owner_of(before[lane]),
                action="re-read the check runs with --paginate before saying it went green",
            )
        )
    events.sort(key=lambda event: (event.lane, event.kind))
    return Feed(
        events=tuple(events),
        verdicts=verdicts,
        reds=reds,
        compared=compared,
        complete=complete,
        partial_reason=reason,
    )


def load_state(path: str) -> dict[str, str]:
    """Read a saved feed state, refusing anything that is not this schema.

    A state file that parses but holds something else would silently turn every lane
    into ``new``; the schema is checked so that a wrong path is an error, not a mood.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ErrorFeedError(f"{path} could not be read as feed state: {redact(str(exc))}")
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        got = payload.get("schema") if isinstance(payload, dict) else type(payload).__name__
        raise ErrorFeedError(f"{path}: schema {got!r} is not {SCHEMA!r}")
    reds = payload.get("reds")
    if not isinstance(reds, dict):
        raise ErrorFeedError(f"{path}: 'reds' is not a lane -> class map")
    return {str(lane): str(cls) for lane, cls in reds.items()}


def save_state(feed: Feed, path: str) -> None:
    """Write the cycle's reds so the next cycle can report a change against them."""
    body = json.dumps(
        {"schema": SCHEMA, "reds": dict(sorted(feed.reds.items()))}, indent=2, sort_keys=True
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")


def render(feed: Feed, *, limit: int = 0) -> str:
    """Plain text, one block per movement, in the house layout.

    No markup: this is delivered to a chat that drops a message whose emphasis does not
    balance.  ``limit`` keeps the count honest when a caller prints only the head of a
    long feed -- the withheld rows are named, never implied to be absent.
    """
    if not feed.events:
        return (
            "Fork CI error feed: no red lane this cycle and nothing carried over.\n"
            f"previous cycle: {'yes' if feed.compared else 'not recorded'}\n"
        )
    counts = ", ".join(f"{kind} {feed.count(kind)}" for kind in KINDS if feed.count(kind))
    lines = [f"Fork CI error feed: {len(feed.events)} movement(s) ({counts})"]
    lines.append(f"previous cycle: {'yes' if feed.compared else 'not recorded'}")
    lines.append(f"payload: {'every check run listed' if feed.complete else feed.partial_reason}")
    lines.append("")
    shown = feed.events[:limit] if limit else feed.events
    for position, event in enumerate(shown, start=1):
        lines.append(f"{position}. {event.lane}")
        lines.append(f"   event: {event.kind} ({event.detail})")
        if event.cls:
            lines.append(f"   class: {event.cls}")
        if event.owner:
            lines.append(f"   owner: {event.owner}")
        if event.action:
            lines.append(f"   next: {event.action}")
        lines.append(f"   at: {event.stamp}" if event.stamp else "   at: not in the payload")
        lines.append("")
    if limit and len(feed.events) > limit:
        lines.append(
            f"_showing {len(shown)} of {len(feed.events)} movement(s): "
            f"{len(feed.events) - len(shown)} withheld by --limit_"
        )
    if feed.unowned:
        lines.append(f"{len(feed.unowned)} lane(s) still have no owner: {', '.join(feed.unowned)}")
    if feed.untimed:
        lines.append(f"{feed.untimed} movement(s) carried no timestamp in the payload")
    lines.append("The feed reports what changed; the class says who owns the fix.")
    return "\n".join(redact(line) for line in lines).rstrip("\n") + "\n"


def to_json(feed: Feed) -> dict[str, object]:
    """The machine form: the same numbers :func:`render` prints."""
    return feed.to_dict()
