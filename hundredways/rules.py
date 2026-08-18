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

# Ordinary English words that contain the letters ``nous`` are not upstream
# branding and must remain byte-stable. Every other residual ``nous`` token is
# a candidate brand reference and is transformed after the precise base rules.
_NONBRAND_NOUS_WORDS = {
    "anonymous", "anonymously", "asynchronous", "asynchronously",
    "autonomous", "autonomously", "luminous", "luminousix", "monotonous", "venous",
    "ominous", "pythonnousersite", "synchronous", "synchronously",
    "testsynchronousfallbackcacheplans", "testasynchronousfallbackcacheplans",
    "testoneshotclirunissynchronous",
}
_RESIDUAL_BRAND = re.compile(r"hermes|nous", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z]+")


def _rule(match: str, replace: str, anchored: bool = False) -> TokenRule:
    return TokenRule(match=match, replace=replace, anchored=anchored)


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
    _rule("hermes", "nastech"),
    _rule("Hermes", "Nastech"),
    _rule("HERMES", "NASTECH"),
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

    @staticmethod
    def _residual_replacement(text: str) -> str:
        def word_at(position: int) -> str:
            for word in _WORD.finditer(text):
                if word.start() <= position < word.end():
                    return word.group(0).lower()
            return ""

        def replace(match_obj: re.Match) -> str:
            token = match_obj.group(0)
            if token.lower() == "nous" and word_at(match_obj.start()) in _NONBRAND_NOUS_WORDS:
                return token
            if token.isupper():
                return "NASTECH"
            if token[0].isupper():
                return "Nastech"
            return "nastech"

        return _RESIDUAL_BRAND.sub(replace, text)

    def transform_text(self, text: str) -> str:
        """Apply all branding tokens to free text, including residual compounds."""
        tokens = list(self.tokens)
        pattern = self._pattern()
        return self._residual_replacement(pattern.sub(lambda m: self._replacement(m, tokens), text))

    def transform_path(self, path: str) -> str:
        """Apply branding tokens to a filesystem path (each component)."""
        tokens = list(self.tokens)
        pattern = self._pattern()
        return self._residual_replacement(pattern.sub(lambda m: self._replacement(m, tokens), path))


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
# Whole-tree transformation policy.  No inherited contributor email record is
# immutable: email filenames and content are branded like every other path so a
# strict candidate scan cannot retain upstream brand tokens.  Contributor names
# alone are identity metadata and may remain byte-stable.
# ---------------------------------------------------------------------------

IMMUTABLE_PATH_SUBSTRINGS: tuple[str, ...] = ()


def is_immutable_path(path: str) -> bool:
    """Return whether a path is byte-preserved by an explicit future policy."""
    lowered = path.lower()
    return any(sub in lowered for sub in IMMUTABLE_PATH_SUBSTRINGS)


def is_contributor_email_path(path: str) -> bool:
    """Identify contributor email records for narrow case-collision handling only."""
    return path.lower().startswith("contributors/emails/")


def is_contributor_name_path(path: str) -> bool:
    """Identify the sole identity-metadata tree allowed to remain byte-stable."""
    return path.lower().startswith("contributors/names/")


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
