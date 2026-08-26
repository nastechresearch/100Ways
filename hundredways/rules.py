"""Branding rules for the Nastech fork.

Learned from commit ``0cafd22fb`` ("Fresh Nastech Research Powered By Nous
Research"), the fork's birth commit: 5299 files, 73k lines, every
``hermes``/``Hermes``/``HERMES`` and ``nous``/``Nous``/``NOUS`` token
rewritten to the Nastech spelling.

Rules are case-precise and longest-first.  Short ``nous``-family tokens are
boundary-guarded so English words (``venous``, ``anonymous``,
``autonomous``) are never touched, while identifiers (``nous_rate_guard``,
``NousResearch``) are rewritten.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Token map.  Longest-first; the matcher scans in this order so compound
# tokens (nousresearch, hermes-agent) win before their short parts do.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenRule:
    match: str
    replace: str
    # word-anchored: only rewritten when not preceded/followed by the same
    # casing class (protects embedded English words, allows snake/kebab case)
    anchored: bool = False


# A regex for "not adjacent to a letter" boundary.  The BEFORE guards must
# not treat the trailing letter of an escape sequence (`\n`, `\b`, `\xfe`,
# `\u00NN`, `\U0000NNNN`) as a word-adjacent letter: `\bhermes` is a regex
# word-boundary + the token, and `\xfehermes` is a hex byte + the token, not
# the token embedded inside an English word.  So a letter only counts as an
# adjacency blocker when it is NOT itself escape-prefixed.
_ESCAPE_LETTER = (
    r"(?<!\\)"               # not `\n`/`\b`/`\r`/... (single-char escapes)
    r"(?<!\\x[0-9a-fA-F])"   # not `\xNN` hex-escape tail (e.g. `\xfe`)
    r"(?<!\\u[0-9a-fA-F]{3})"  # not `\uNNNN`
    r"(?<!\\U[0-9a-fA-F]{7})"  # not `\UNNNNNNNN`
)
_LOWER_BEFORE = r"(?<!" + _ESCAPE_LETTER + r"[a-z])"
_LOWER_AFTER = r"(?![a-z])"
_UPPER_BEFORE = r"(?<!" + _ESCAPE_LETTER + r"[A-Z])"
_UPPER_AFTER = r"(?![A-Z])"
_ANY_BEFORE = r"(?<!" + _ESCAPE_LETTER + r"[A-Za-z])"
_ANY_AFTER = r"(?![A-Za-z])"


def _rule(match: str, replace: str, anchored: bool = False) -> TokenRule:
    return TokenRule(match=match, replace=replace, anchored=anchored)


# ---------------------------------------------------------------------------
# Runner-label normalization.  Upstream Hermes pays for GitHub larger runners
# (ubuntu-latest-96-core, ubuntu-latest-32-arm-core, ...).  This org does not
# configure them, so any such label queues a job forever (2026-08-24 incident:
# the whole nastech-agent CI sat queued 4h+ after a branded update carried the
# labels over).  Normalizing here — inside the canonical transform — keeps the
# brander, porter, forkcheck, and analyzer in exact agreement.
# ---------------------------------------------------------------------------

_RUNNER_LABEL_RE = re.compile(
    r"\b"
    r"((?:ubuntu|windows|macos)-[a-z0-9.]+"      # base: ubuntu-latest, macos-14, ...
    r"(?:-latest)?)"                              # tolerate ...-latest-NN-core
    r"-(\d+)"
    r"-(arm-)?"                                   # optional arm variant
    r"core\b"
)

# The upstream test workflow is tuned for a 96-core larger runner.  The
# Nastech organization uses standard GitHub-hosted runners, where that fan-out
# causes timing-sensitive subprocess, SQLite, and gateway tests to contend.
_TEST_WORKERS_RE = re.compile(
    r"(?m)^(?P<prefix>\s*NASTECH_TEST_WORKERS:\s*)96(?P<suffix>\s*(?:#.*)?)$"
)


def _normalize_test_workers(text: str) -> str:
    return _TEST_WORKERS_RE.sub(r"\g<prefix>8\g<suffix>", text)


def _normalize_runner(match_obj: re.Match) -> str:
    base, _, arm = match_obj.group(1), match_obj.group(2), match_obj.group(3)
    if arm:
        # Standard hosted arm64 label; windows/macos have no arm hosted tier.
        return "ubuntu-24.04-arm" if base.startswith("ubuntu") else base
    return base


# The canonical learned set.  Order matters: longest compounds first.
DEFAULT_TOKENS: list[TokenRule] = [
    # -- nous family: compounds before short forms -------------------------
    # Girl mascot file keeps Hermes' structure — hermes ships nous-girl.jpg,
    # so nastech ships nastech-<mascot-name>.jpg.  Only the word "girl" is
    # swapped for the mascot's name ("bantu"); the nous->nastech prefix is
    # preserved.  This compound must win before the generic 'nous' token.
    _rule("nous-girl", "nastech-bantu"),
    # The .com compounds MUST precede the bare nousresearch / hermes-agent
    # tokens, otherwise the alternation matches the prefix first and the
    # domain rule never fires.  (They keep the upstream .com spelling here
    # because this token set is validated against the fork BIRTH commit,
    # which used nastechresearch.com; the github.io migration is a
    # fork-local correction applied by the reconcile stage, not a token.)
    _rule("hermes-agent.nousresearch.com", "nastech-agent.nastechresearch.com"),
    _rule("NousResearch.com", "NastechResearch.com"),
    _rule("nousresearch.com", "nastechresearch.com"),
    _rule("nousresearch", "nastechresearch"),
    _rule("NousResearch", "NastechResearch"),
    _rule("NOUSRESEARCH", "NASTECHRESEARCH"),
    _rule("nous", "nastech", anchored=True),
    _rule("Nous", "Nastech", anchored=True),
    _rule("NOUS", "NASTECH", anchored=True),
    # -- hermes family ------------------------------------------------------
    _rule("hermes-agent", "nastech-agent"),
    _rule("hermes", "nastech", anchored=True),
    _rule("Hermes", "Nastech", anchored=True),
    _rule("HERMES", "NASTECH", anchored=True),
    # -- brand symbol: replace both inherited medical-symbol variants with
    #    the user-approved NasTech glyph across UI, locales, docs, and SVGs.
    _rule("⚕", "𓄃"),
    _rule("☤", "𓄃"),
]


@dataclass
class BrandingRules:
    """Compiled, order-preserving token rules."""

    tokens: list[TokenRule] = field(default_factory=lambda: list(DEFAULT_TOKENS))

    def _pattern(self) -> re.Pattern:
        parts: list[str] = []
        for idx, tok in enumerate(self.tokens):
            inner = re.escape(tok.match)
            if tok.anchored:
                if tok.match[0].isupper():
                    if tok.match.isupper():
                        inner = _UPPER_BEFORE + inner + _UPPER_AFTER
                    else:
                        # Title-case (Hermes, Nous): only block a preceding
                        # UPPERCASE letter so camelCase boundaries still match
                        # (refreshHermesConfig, titleNous) while embedded-caps
                        # runs (rare) stay intact.  Lowercase substrings are
                        # protected by the lowercase-token guard instead.
                        inner = _UPPER_BEFORE + inner + _LOWER_AFTER
                else:
                    inner = _LOWER_BEFORE + inner + _LOWER_AFTER
            parts.append(f"(?P<t{idx}>{inner})")
        return re.compile("|".join(parts))

    @staticmethod
    def _replacement(match_obj: re.Match, tokens: list[TokenRule]) -> str:
        for idx, tok in enumerate(tokens):
            if match_obj.group(f"t{idx}") is not None:
                return tok.replace
        return match_obj.group(0)  # pragma: no cover - defensive

    def transform_text(self, text: str) -> str:
        """Apply all branding tokens to free text."""
        tokens = list(self.tokens)
        pattern = self._pattern()
        branded = pattern.sub(lambda m: self._replacement(m, tokens), text)
        branded = _RUNNER_LABEL_RE.sub(_normalize_runner, branded)
        return _normalize_test_workers(branded)

    def transform_path(self, path: str) -> str:
        """Apply branding tokens to a filesystem path (each component)."""
        tokens = list(self.tokens)
        pattern = self._pattern()
        return pattern.sub(lambda m: self._replacement(m, tokens), path)


def transform_strict_metadata_text(text: str, rules: BrandingRules | None = None) -> str:
    """Brand every token occurrence in generated metadata and report text.

    Generated records often contain source paths such as ``hermes_cli`` or
    ``hermes-bots``.  Those separators are identifiers rather than normal
    prose, so the user’s zero-upstream-brand policy requires replacement even
    where the conservative source-code transformer intentionally preserves an
    embedded identifier.  This helper is restricted to generated metadata,
    reports, and inventories; production source files retain the normal
    boundary-safe transformer.
    """
    rules = rules or BrandingRules()
    transformed = text
    for token in rules.tokens:
        pattern = re.compile(re.escape(token.match), re.IGNORECASE)

        def replace(match: re.Match, replacement: str = token.replace) -> str:
            value = match.group(0)
            if value.isupper():
                return replacement.upper()
            if value[:1].isupper():
                return replacement[:1].upper() + replacement[1:]
            return replacement

        transformed = pattern.sub(replace, transformed)
    return transformed


def transform_contributor_email_text(text: str, rules: BrandingRules | None = None) -> str:
    """Apply the strict policy to a contributor-email record payload.

    Contributor email records are text identity data, not source code.  Their
    filename and payload must therefore not retain an inherited brand—even when
    it is embedded in a mailbox label such as ``mchermes``.  Contributor names
    remain the separate approved identity exception.
    """
    return transform_strict_metadata_text(text, rules)


def transform_contributor_email_path(path: str, rules: BrandingRules | None = None) -> str:
    """Map contributor-email filenames even when a brand is embedded in an address.

    Email local parts and host labels frequently join an inherited product name
    directly to another word (for example ``hermesagent@example.invalid``),
    where ordinary identifier rules correctly avoid a broad text replacement.
    The strict candidate policy nevertheless forbids that brand in an email
    filename.  Apply the canonical longest-first replacements to that filename
    only; no contributor record is removed and content still uses normal text
    token boundaries.
    """
    rules = rules or BrandingRules()
    marker = "contributors/emails/"
    lowered = path.lower()
    index = lowered.find(marker)
    if index < 0:
        return rules.transform_path(path)
    prefix = path[: index + len(marker)]
    suffix = path[index + len(marker):]
    for token in rules.tokens:
        suffix = suffix.replace(token.match, token.replace)
    return prefix + suffix


def collision_safe_path_map(paths: list[str], rules: BrandingRules | None = None) -> dict[str, str]:
    """Map every source path to a deterministic case-safe candidate path.

    Two distinct upstream paths can differ only by case.  That is valid on the
    upstream's case-sensitive filesystem but unsafe on macOS and Windows.  A
    collision is resolved *before* candidate materialization by appending a
    stable source-path digest to each member of the colliding group.  Contributor
    names remain identity metadata, while contributor-email paths use the same
    NasTech-safe token mapping as every other candidate path.  No record is
    dropped or merged.
    """
    rules = rules or BrandingRules()
    paths = sorted(paths)
    mapped = {
        path: (
            path if is_immutable_path(path)
            else transform_contributor_email_path(path, rules)
            if "contributors/emails/" in path.lower()
            else rules.transform_path(path)
        )
        for path in paths
    }
    groups: dict[str, list[str]] = {}
    for path, target in mapped.items():
        groups.setdefault(target.casefold(), []).append(path)

    used = {target.casefold() for key, values in groups.items() if len(values) == 1
            for target in (mapped[values[0]],)}
    result = dict(mapped)
    for values in groups.values():
        if len(values) < 2:
            continue
        for path in sorted(values):
            target = mapped[path]
            parent, basename = os.path.split(target)
            stem, extension = os.path.splitext(basename)
            digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
            candidate = os.path.join(parent, f"{stem}--case-{digest}{extension}")
            ordinal = 2
            while candidate.casefold() in used:
                candidate = os.path.join(parent, f"{stem}--case-{digest}-{ordinal}{extension}")
                ordinal += 1
            result[path] = candidate
            used.add(candidate.casefold())
    return result


def tokens_from_overrides(path: str | None) -> list[TokenRule]:
    """Load admin-added tokens from a dashboard override file.

    Overrides are additive: ``match`` / ``replace`` pairs, each with an
    optional ``anchored`` flag.  The base DEFAULT_TOKENS are always active.
    """
    rules = [TokenRule(t.match, t.replace, t.anchored) for t in DEFAULT_TOKENS]
    if not path or not os.path.exists(path):
        return rules
    try:
        import json

        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data.get("tokens", []):
            if not item.get("match"):
                continue
            rules.append(
                TokenRule(
                    match=item["match"],
                    replace=item.get("replace", ""),
                    anchored=bool(item.get("anchored", False)),
                )
            )
    except (OSError, ValueError):
        pass
    return rules


# ---------------------------------------------------------------------------
# Locked files.  These are protected from auto-branding:
#   - binary artifacts (logos, frames, icons) -- renamed, content untouched
#   - lockfiles / integrity hashes -- verified in the verifier, not rewritten
#   - anything the operator pins explicitly
# ---------------------------------------------------------------------------

LOCKED_PATH_SUBSTRINGS = (
    "public/hermes-frames/",
    "public/nastech-frames/",
    "package-lock.json",
    "uv.lock",
    "poetry.lock",
)

# Exact basenames that must NOT be auto-branded even though they are text.
# Real secret files (.env) and package-manager rc files are real data.
LOCKED_FILENAMES = (".env", ".npmrc")

LOCKED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".icns",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".exe",
    ".dll",
    ".bin",
)


# ---------------------------------------------------------------------------
# Immutable identity metadata.  Contributor names are the one approved
# identity exception in the strict brand policy, so they stay verbatim.  Email
# records are intentionally NOT immutable: their path and textual payload must
# pass through the NasTech token mapping, while collision_safe_path_map keeps
# case-colliding records distinct and lossless.
# ---------------------------------------------------------------------------

IMMUTABLE_PATH_SUBSTRINGS = (
    "contributors/names/",
)

def is_immutable_path(path: str) -> bool:
    """True only for approved contributor-name identity metadata."""
    lowered = path.lower()
    return any(sub in lowered for sub in IMMUTABLE_PATH_SUBSTRINGS)


def is_locked_path(path: str) -> bool:
    """True when a path must NOT be auto-branded (content or rename)."""
    lowered = path.lower()
    if lowered.endswith(LOCKED_EXTENSIONS):
        return True
    if path.rsplit("/", 1)[-1] in LOCKED_FILENAMES:
        return True
    for sub in LOCKED_PATH_SUBSTRINGS:
        if sub in path:
            return True
    return False
