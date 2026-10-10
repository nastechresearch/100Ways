"""Strong upstream-brand detector: see *every* ``hermes`` / ``nous`` run.

The frozen token map (:mod:`hundredways.rules`) and the word-boundary gate
(:func:`hundredways.weekly_sync.audit_first_party_brand`) both stop at a token
edge.  That protects ordinary English words, but it also lets a *glued* brand
run ship: ``HERMESCLOUD``, ``hermesagent26``, ``hermesbot-cron``, ``nousnet``,
``HERMESTEXINLINE``.  None of those are reachable by the map, so no existing
check has ever looked at them.

This module is the strong half of the pair:

* :data:`STRONG_BRAND_RE` matches ``hermes`` / ``nous`` / ``nousresearch``
  anywhere a word can *begin*, with no right edge.  The left guard blocks a
  preceding letter, so ``synchronous`` / ``asynchronous`` never match -- but
  ``hermesagent`` and ``nousnet`` do.
* :func:`scan_brand` walks a tree's text *and* paths and classifies each hit
  with the same inherited-fixture allow-list the weekly gate uses.
* :func:`propose_token_overrides` renders the surviving runs as additive
  token-map entries (``config/rules_override.json`` shape) for a human.

Nothing here edits the map or a gate.  It only *sees* and proposes; adopting a
token stays a reviewed change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .rules import BrandingRules, is_immutable_path, is_locked_path

# A word may begin with the upstream brand and then run on
# (``hermesagent``, ``nousresearch``, ``HERMESCLOUD``).  The look-behind
# rejects a preceding alphanumeric so ``synchronous`` is never a hit, while
# ``hermesagent`` after a space, dot, underscore, or path separator is.
STRONG_BRAND_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<brand>hermes|nousresearch|nous)",
    re.IGNORECASE,
)

_SKIP_DIRS = frozenset({".git", "node_modules"})
_MAX_SCAN_BYTES = 2 * 1024 * 1024
# '.' is a separator, not a run char: ``hermesagent.ts`` is the run
# ``hermesagent`` with an extension, not one identifier.
_RUN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)


def _run_at(text: str, start: int, end: int) -> str:
    """Expand ``[start, end)`` to the surrounding identifier run."""
    while start > 0 and text[start - 1] in _RUN_CHARS:
        start -= 1
    while end < len(text) and text[end] in _RUN_CHARS:
        end += 1
    return text[start:end]


def _is_camel_nous(word: str) -> bool:
    """True for a camelCase word that merely *starts* like ``nous``.

    Dropping the right edge is what lets the detector see ``nousnet`` and
    ``nousbot``, but it also catches ``noUsage`` / ``noUser`` -- a lowercase
    letter, then an internal uppercase.  A real glued run is uniformly cased
    (``nousnet``, ``NOUSNET``) or Title-case, so this one shape is noise.
    ``hermes`` needs no such guard: it is not an English prefix.
    """
    return word.lower() == "nous" and word[:1].islower() and any(c.isupper() for c in word[1:])


def _allowed(path: str, word: str, line: str) -> bool:
    """Path-scoped inherited fixtures the weekly gate deliberately permits.

    This mirrors :func:`hundredways.weekly_sync._allowed_brand_occurrence`
    exactly; ``tests/test_brandscan.py`` asserts the two never drift.  A hit is
    "allowed" (inherited) rather than a new first-party brand leak.
    """
    word = word.lower()
    if path.startswith("contributors/names/"):
        return True
    lower = line.lower()
    if path == "package-lock.json" and word == "hermes":
        return "hermes-parser" in lower or "hermes-estree" in lower
    if path in {"website/package-lock.json", "website/.npmrc"} and word in {"nous", "nousresearch"}:
        return "@nous-research/image-size" in lower
    if path == "reports/SYNC-SUMMARY.md" and word in {"nous", "nousresearch"}:
        return line.strip() == "> powered by nousresearch"
    return False


@dataclass(frozen=True)
class BrandOccurrence:
    """One brand run found in a tree, with enough evidence to review it."""

    word: str
    run: str
    path: str
    line: int
    kind: str  # "text" or "path"
    allowed: bool
    sample: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "word": self.word,
            "run": self.run,
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "allowed": self.allowed,
            "sample": self.sample,
        }


@dataclass
class BrandScan:
    """Every brand run in the tree, split into allowed and violating."""

    root: str
    occurrences: list[BrandOccurrence] = field(default_factory=list)
    scanned_files: int = 0

    @property
    def allowed(self) -> list[BrandOccurrence]:
        return [o for o in self.occurrences if o.allowed]

    @property
    def violations(self) -> list[BrandOccurrence]:
        return [o for o in self.occurrences if not o.allowed]

    @property
    def clean(self) -> bool:
        return not self.violations

    def distinct_runs(self) -> list[str]:
        return sorted({o.run for o in self.violations})

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "scanned_files": self.scanned_files,
            "clean": self.clean,
            "occurrences": [o.to_dict() for o in self.occurrences],
        }


def _iter_text_files(root: str):
    base = Path(root)
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(base).as_posix()
        if is_immutable_path(relative) or is_locked_path(relative):
            continue
        try:
            if path.stat().st_size > _MAX_SCAN_BYTES:
                continue
        except OSError:
            continue
        yield path, relative


def scan_brand(root: str, max_findings: int = 5000) -> BrandScan:
    """Return every ``hermes`` / ``nous`` run under ``root``, allowed or not."""
    scan = BrandScan(root=root)
    base = Path(root)
    if not base.is_dir():
        return scan
    for path, relative in _iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scan.scanned_files += 1
        lowered = text.lower()
        if "hermes" not in lowered and "nous" not in lowered:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in STRONG_BRAND_RE.finditer(line):
                word = match.group("brand")
                if _is_camel_nous(word):
                    continue
                run = _run_at(line, match.start(), match.end())
                scan.occurrences.append(
                    BrandOccurrence(
                        word=word.lower(),
                        run=run,
                        path=relative,
                        line=number,
                        kind="text",
                        allowed=_allowed(relative, word, line),
                        sample=line.strip()[:200],
                    )
                )
                if len(scan.occurrences) >= max_findings:
                    return scan
    return scan


def scan_paths(root: str, max_findings: int = 5000) -> list[BrandOccurrence]:
    """Return brand runs that live in file *names*, which text scans miss."""
    base = Path(root)
    found: list[BrandOccurrence] = []
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("*")):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(base).as_posix()
        for match in STRONG_BRAND_RE.finditer(relative):
            word = match.group("brand")
            if _is_camel_nous(word):
                continue
            run = _run_at(relative, match.start(), match.end())
            found.append(
                BrandOccurrence(
                    word=word.lower(),
                    run=run,
                    path=relative,
                    line=0,
                    kind="path",
                    allowed=_allowed(relative, word, relative),
                    sample=relative,
                )
            )
            if len(found) >= max_findings:
                return found
    return found


def _recase(value: str, replacement: str) -> str:
    """Match ``value``'s casing class onto ``replacement``."""
    if value.isupper():
        return replacement.upper()
    if value[:1].isupper():
        return replacement.capitalize()
    return replacement


def propose_token_overrides(
    scan: BrandScan, rules: BrandingRules | None = None
) -> list[dict[str, str]]:
    """Render surviving runs as additive ``config/rules_override.json`` tokens.

    Only proposals: an operator adopts them into the dashboard.  A run the
    canonical ``rules`` already covers is skipped, because proposing it would be
    a no-op or -- worse -- grow the birth-parity drift set.
    """
    rules = rules or BrandingRules()
    proposals: list[dict[str, str]] = []
    for run in scan.distinct_runs():
        if not run or rules.transform_text(run) != run:
            continue
        replaced = re.sub(
            r"(?i)nousresearch", lambda m: _recase(m.group(0), "nastechresearch"), run
        )
        replaced = re.sub(r"(?i)hermes", lambda m: _recase(m.group(0), "nastech"), replaced)
        replaced = re.sub(r"(?i)nous", lambda m: _recase(m.group(0), "nastech"), replaced)
        proposals.append({"match": run, "replace": replaced, "anchored": False})
    return proposals
