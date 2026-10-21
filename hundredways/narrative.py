"""Deterministic review narrative: the title, evidence and body a human reads.

Every value is derived from the manifest the pipeline already writes, so a title
never claims work the run did not do and a verification line never reads
"Passed" while the recorded gate says otherwise.  The optional Ollama garnish
adds one labelled prose paragraph and nothing else: with no key, no network, or
a failing model it is absent rather than approximated.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .rules import BrandingRules, transform_strict_metadata_text
from .sync_summary import change_category

MAX_TITLE_CHARS = 100
MAX_GARNISH_CHARS = 600
GARNISH_HEADING = "## Editorial note"
GARNISH_DISCLAIMER = "Generated advisory text; it does not authorize anything and is not verification evidence."

_COMPACT_LABELS: dict[str, tuple[str, str]] = {
    "New capabilities": ("new capability", "new capabilities"),
    "Reliability and fixes": ("fix", "fixes"),
    "Security and hardening": ("security fix", "security fixes"),
    "Performance": ("performance win", "performance wins"),
    "Documentation": ("documentation update", "documentation updates"),
    "Improvements": ("improvement", "improvements"),
}
_MIXED_LABELS = ("verified update", "verified updates")
# Below this share the leading category is not the story, so the headline says
# "verified updates" rather than naming one bucket for a mixed delivery.
_DOMINANT_SHARE = 0.6


def _text(value: Any) -> str:
    return transform_strict_metadata_text(" ".join(str(value).split()), BrandingRules())


def subjects_of(manifest: dict[str, Any]) -> list[str]:
    return [_text(subject) for subject in manifest.get("commit_subjects", ()) if str(subject).strip()]


def category_counts(manifest: dict[str, Any]) -> Counter[str]:
    return Counter(change_category(subject) for subject in subjects_of(manifest))


def _bounded(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    trimmed = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return (trimmed or text[: limit - 1]) + "…"


def _label(counts: Counter[str], total: int) -> str:
    top, hits = counts.most_common(1)[0]
    singular, plural = _COMPACT_LABELS.get(top, _MIXED_LABELS)
    if hits / total < _DOMINANT_SHARE:
        singular, plural = _MIXED_LABELS
    return singular if total == 1 else plural


def title_from_manifest(manifest: dict[str, Any]) -> str:
    """Return the review headline the candidate deserves, from real delta data."""
    subjects = subjects_of(manifest)
    revision = _text(manifest.get("upstream_sha", ""))[:8]
    if not subjects:
        aligned = f"Align NasTech-Agent with upstream {revision}" if revision else "Align NasTech-Agent with upstream"
        return _bounded(aligned, MAX_TITLE_CHARS)
    total = len(subjects)
    label = _label(category_counts(manifest), total)
    for candidate in (
        f"Sync NasTech-Agent with upstream {revision}: {total} {label}",
        f"Sync NasTech-Agent with {total} {label}",
    ):
        if len(candidate) <= MAX_TITLE_CHARS:
            return candidate
    return _bounded(f"Sync NasTech-Agent with {total} {label}", MAX_TITLE_CHARS)


def _count(group: dict[str, Any], key: str) -> int:
    try:
        return int(group.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def evidence_from_manifest(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """State what each recorded gate actually concluded, never a fixed "Passed"."""
    verify = manifest.get("verify") or {}
    brand = manifest.get("brand") or {}
    fork = manifest.get("fork") or {}
    delta = manifest.get("source_delta") or {}
    provenance = manifest.get("source_provenance") or {}

    failed = verify.get("failed") or []
    total = _count(verify, "total")
    passed = _count(verify, "passed")
    parity = f"{passed}/{total} files verified byte-faithful after branding"
    if failed:
        parity += f"; {len(failed)} still differ"
    else:
        parity += "; no outstanding differences"

    violations = _count(fork, "violations")
    consistency = (
        "No fork checkout supplied"
        if not fork
        else f"{violations} inconsistency violations, {len(fork.get('preserved_paths') or [])} fork-owned files carried forward"
    )

    counts = delta.get("counts") or {}
    accounted = ", ".join(f"{status} {value}" for status, value in sorted(counts.items())) or "no upstream baseline recorded"

    return [
        (
            "Direct source provenance",
            _text(
                f"{provenance.get('acquisition') or 'recorded source'} at "
                f"{_text(manifest.get('upstream_sha', ''))[:12]}, fetched {provenance.get('fetched_at') or 'time not recorded'}"
            ),
        ),
        (
            "Publication gate",
            "PASS - cleared for human review only" if manifest.get("gate") else "FAIL - publication stays blocked",
        ),
        ("Tree parity", parity),
        (
            "Branding",
            f"{_count(brand, 'total')} files branded, {_count(brand, 'rewritten')} rewritten, {_count(brand, 'errors')} errors",
        ),
        ("Fork consistency", consistency),
        ("Upstream change evidence", accounted),
    ]


def garnish(manifest: dict[str, Any], *, engine: Any = None) -> str:
    """Return one short prose paragraph, or "" when AI cannot honestly add one."""
    try:
        from .ai import AIEngine, sanitize_ai_context

        ai = engine if engine is not None else AIEngine()
        if not getattr(ai, "available", False):
            return ""
        context = _text(sanitize_ai_context(_digest(manifest), limit=3_000))
        paragraph = " ".join(str(ai.narrative(context)).split())
    except Exception:
        return ""
    if not paragraph:
        return ""
    return _bounded(_text(paragraph), MAX_GARNISH_CHARS)


def _digest(manifest: dict[str, Any]) -> str:
    counts = ", ".join(f"{count} {name.lower()}" for name, count in sorted(category_counts(manifest).items()))
    verify = manifest.get("verify") or {}
    fork = manifest.get("fork") or {}
    return (
        f"upstream revision {_text(manifest.get('upstream_sha', ''))[:12]}; "
        f"{len(subjects_of(manifest))} commits ({counts or 'no commits'}); "
        f"parity {_count(verify, 'passed')}/{_count(verify, 'total')} files; "
        f"fork violations {_count(fork, 'violations')}; "
        f"gate {'PASS' if manifest.get('gate') else 'FAIL'}"
    )


def body_from_manifest(manifest: dict[str, Any], summary_markdown: str, *, paragraph: str = "") -> str:
    """Compose the review body: verified summary first, labelled advisory text last."""
    lines = [summary_markdown.rstrip()]
    if paragraph:
        lines.extend(
            [
                "",
                GARNISH_HEADING,
                "",
                f"{_text(paragraph)}",
                "",
                GARNISH_DISCLAIMER,
            ]
        )
    return "\n".join(lines) + "\n"


def write_narrative(
    title_path: str,
    body_path: str,
    *,
    manifest: dict[str, Any],
    summary_markdown: str,
    paragraph: str = "",
) -> str:
    """Persist the title and body a reviewer reads; return the title."""
    title = title_from_manifest(manifest)
    Path(title_path).write_text(title + "\n", encoding="utf-8")
    Path(body_path).write_text(body_from_manifest(manifest, summary_markdown, paragraph=paragraph), encoding="utf-8")
    return title
