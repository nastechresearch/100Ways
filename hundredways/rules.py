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

# The upstream test workflow is tuned for paid larger runners (initially
# ``ubuntu-latest-96-core``; later ``ubuntu-latest-32-core``).  The Nastech
# organization uses standard GitHub-hosted runners, where that fan-out causes
# timing-sensitive subprocess, SQLite, and gateway tests to contend.  Cap every
# bare integer worker count above the standard-runner ceiling (``96`` -> ``8``
# and ``32`` -> ``8``); quoted counts and expression-valued counts (the fork's
# own ``"3"``/``"6"`` and the OS-matrix expressions) are left untouched.
_TEST_WORKERS_CAP = 8
_TEST_WORKERS_RE = re.compile(
    r"(?m)^(?P<prefix>\s*NASTECH_TEST_WORKERS:\s*)(?P<value>\d+)(?P<suffix>\s*(?:#.*)?)$"
)


def _normalize_test_workers(text: str) -> str:
    def _cap(match_obj: re.Match) -> str:
        if int(match_obj.group("value")) <= _TEST_WORKERS_CAP:
            return match_obj.group(0)
        return f"{match_obj.group('prefix')}{_TEST_WORKERS_CAP}{match_obj.group('suffix')}"

    return _TEST_WORKERS_RE.sub(_cap, text)


# The OS-matrix Windows lane fans out to 16 workers upstream (a 32-core paid
# Windows image) and its ``||`` arm is ``''``, which the runner reads as unset and
# defaults to ``os.cpu_count()`` -- 12 cores on upstream's macOS image.  This
# fork's OS lanes are the free hosted images (4-core Windows, 3-core macOS), and a
# lane that spawns its own timed children (nested PowerShell trees, the
# flock/gateway-boot probes in ``tests/nastech_cli/test_browse_schema_drift.py``)
# starves those children into their deadlines.  Rewrite the whole expression
# rather than leaving the rows upstream's: the cap has to hold after every sync,
# and a hand-edit on the fork is regenerated away.  The ``||`` arm is macOS-only --
# the matrix ``include`` rows carry the ``macos``/``windows`` markers -- and the
# output matches this same pattern, so the rule is its own fixed point.
_OS_MARKED_WORKERS_RE = re.compile(
    r"(?m)^(?P<prefix>[ \t]*NASTECH_TEST_WORKERS:[ \t]*)"
    r"\$\{\{[ \t]*matrix\.marker == 'windows'[ \t]*&&.*?\|\|[ \t]*'(?P<other>\d*)'"
    r"[ \t]*\}\}[ \t]*$"
)
_OS_WINDOWS_WORKERS = 4
_OS_MACOS_WORKERS = 2


def _normalize_os_test_workers(text: str) -> str:
    def _cap(match_obj: re.Match) -> str:
        return (
            f"{match_obj.group('prefix')}"
            f"${{{{ matrix.marker == 'windows' && '{_OS_WINDOWS_WORKERS}'"
            f" || '{_OS_MACOS_WORKERS}' }}}}"
        )

    return _OS_MARKED_WORKERS_RE.sub(_cap, text)


# Standard runners need a little more wall-clock allowance for the complete
# Python suite, while the smaller auxiliary jobs retain their upstream limits.
# (The branded Python lane's own job budget is widened out of band, in
# ``updates._reconcile_python_test_timeout``: the birth-parity drift monitor
# pins this rules output to the fork-birth commit, so it must keep the
# original 30->40 behaviour.)
_FULL_TEST_TIMEOUT_RE = re.compile(
    r"(?ms)(^[ \t]*name:[ \t]*Run tests[ \t]*$\n"
    r"(?:(?!^[ \t]*(?:name|timeout-minutes):).*\n)*?"
    r"^[ \t]*timeout-minutes:[ \t]*)30([ \t]*$)"
)


def _normalize_test_timeout(text: str) -> str:
    # Upstream can define multiple jobs named "Run tests" (for example the
    # regular and e2e lanes). Normalize every matching 30-minute job so the
    # branded result is a true transform fixed point.
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() != "name: Run tests":
            continue
        name_indent = len(line) - len(line.lstrip(" \t"))
        for candidate in range(index + 1, len(lines)):
            current = lines[candidate]
            stripped = current.strip()
            indent = len(current) - len(current.lstrip(" \t"))
            if indent <= name_indent and stripped.startswith("name:"):
                break
            if indent <= name_indent and stripped.startswith("timeout-minutes:"):
                lines[candidate] = re.sub(
                    r"(timeout-minutes:[ \t]*)30\b", r"\g<1>40", current
                )
                break
    return "".join(lines)


def _normalize_runner(match_obj: re.Match) -> str:
    base, _, arm = match_obj.group(1), match_obj.group(2), match_obj.group(3)
    if arm:
        # An arm64 label is an architecture requirement, not a size hint: an
        # win32-arm64 job on an x64 host builds a win32-x64 payload and the
        # bundle's own audit rejects the package (``audit-bundle-arch.mjs:
        # 659 binaries do not match --arch=arm64``, run 39508427430).  Windows
        # has the standard ``windows-11-arm`` tier for that; macOS does not, so
        # a macOS arm row keeps its base label and still runs natively.
        #
        # Native hardware is necessary but not yet sufficient here: upstream
        # ships ``dependency_injector`` abi3 wheels for win32 and win_amd64
        # only, so an arm64 bundle compiles it and the free image's MSVC ARM64
        # toolset fails (``LNK1104: cannot open file
        # ...containers.cp314-win_arm64.exp``, run 39539131670) where upstream's
        # paid ``windows-latest-32-arm-core`` gets through (run 39383704420).
        # The label is still the honest one -- on x64 the lane cannot produce a
        # package at all, while on arm64 it fails somewhere a toolchain or a
        # wheel can fix -- and no fork release is blocked by it, because the
        # lane table leaves the unsigned ``windows`` lane out of ``DEFAULT``.
        if base.startswith("ubuntu"):
            return "ubuntu-24.04-arm"
        return "windows-11-arm" if base.startswith("windows") else base
    return base


# Upstream Hermes re-homed its CI onto Blacksmith hosted runners
# (2026-10-13..15, ``ci: run everything on blacksmith.sh runners``): every
# ``runs-on``/``runner`` became ``blacksmith-<N>vcpu-<os>-<ver>[-arm]`` and the
# action steps became the ``useblacksmith/*`` forks.  This org has no Blacksmith
# account, so — exactly like the 2026-08-24 larger-runner incident above —
# a branded update carrying those labels over leaves the whole CI queued with no
# runner to match.  Restore the standard hosted labels and the upstream action
# pins the fork used before the switch.  Kept inside the canonical transform so
# brander, porter, forkcheck, analyzer, and verify (birth parity) agree.
_BLACKSMITH_LABEL_RE = re.compile(
    r"\bblacksmith-(\d+)vcpu-(ubuntu|windows|macos)-([a-z0-9.]+)(-arm)?\b"
)
_BLACKSMITH_ACTION_RE = re.compile(
    r"\buseblacksmith/(checkout|setup-docker-builder|build-push-action)"
    r"@[0-9a-zA-Z._-]+(?:[ \t]*#[^\n]*)?"
)
_BLACKSMITH_ACTIONS = {
    "checkout": "actions/checkout@f548e57e544e1ff5a4c46bf1e1b8685f8e4a348a # v7.0.1",
    "setup-docker-builder": (
        "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3"
    ),
    "build-push-action": (
        "docker/build-push-action@bcafcacb16a39f128d818304e6c9c0c18556b85f # v7.1.0"
    ),
}


def _normalize_blacksmith(match_obj: re.Match) -> str:
    os_name, arm = match_obj.group(2), match_obj.group(4)
    if os_name == "ubuntu":
        return "ubuntu-24.04-arm" if arm else "ubuntu-latest"
    if os_name == "windows":
        # Same architecture rule as ``_normalize_runner``: upstream has moved
        # its ARM rows onto blacksmith labels before, and an arm64 Windows lane
        # landing on the x64 image fails its own bundle-architecture audit.
        return "windows-11-arm" if arm else "windows-latest"
    return f"{os_name}-latest"


def _normalize_blacksmith_action(match_obj: re.Match) -> str:
    return _BLACKSMITH_ACTIONS[match_obj.group(1)]


# The canonical learned set.  Order matters: longest compounds first.
DEFAULT_TOKENS: list[TokenRule] = [
    # -- hosted community plugin: whole repo path ---------------------------
    # Branding renames the repo NAME inside a GitHub URL
    # (hermes-plugin-holographic -> nastech-plugin-holographic) but cannot
    # rename a foreign OWNER, so branding alone left
    # ``itpartypattaya/nastech-plugin-holographic`` -- a repo that does not
    # exist.  We host our own copy of this plugin under the ``nastechtest``
    # organization, pinned at the same commit, so the whole path maps.
    # It must be a whole-path compound, not the bare ``itpartypattaya``
    # owner: the other three plugins that owner publishes are not ours, and
    # the maintainer's contributor record must stay verbatim.  Listed first
    # because ``transform_strict_metadata_text`` applies tokens in order, so
    # this has to win before the generic ``hermes`` token below.
    _rule(
        "itpartypattaya/hermes-plugin-holographic",
        "nastechtest/nastech-plugin-holographic",
    ),
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
    # HTTP Authorization scheme for cross-gateway room grants.  Must be an
    # explicit compound: the lowercase `hermes` token is boundary-guarded so
    # it never fires inside `hermesroom` (protected lowercase substring),
    # leaving a thr:scheme mismatch that 401s every room-grant request.
    _rule("hermesroom", "nastechroom"),
    _rule("hermes", "nastech", anchored=True),
    _rule("Hermes", "Nastech", anchored=True),
    _rule("HERMES", "NASTECH", anchored=True),
    # -- brand symbol: replace both inherited medical-symbol variants with
    #    the user-approved NasTech glyph across UI, locales, docs, and SVGs.
    _rule("⚕", "𓄃"),
    _rule("☤", "𓄃"),
]


# -- repository identifiers -------------------------------------------------
# A GitHub repository path is not a brand claim: we have no authority to
# rename somebody else's repository.  Branding DID rename the repo name inside
# the URL (``hermes-plugin-foo`` -> ``nastech-plugin-foo``) while leaving the
# foreign owner untouched, which produced 383 catalog entries pointing at
# repositories that do not exist (verified: 15/15 sampled URLs 404 while every
# matching upstream ``hermes-*`` repository exists).  Any owner outside
# :data:`OWNED_GITHUB_ORGS` is therefore preserved verbatim, unless a token rule
# redirects that path into an organization we do own -- which is how a plugin we
# host ourselves (``nastechtest/nastech-plugin-holographic``) still gets branded.
# That fix did not cover the whole catalog: 191 distinct foreign-owner entries
# also spell their repository as a ``raw.githubusercontent.com`` asset URL, and
# because that host was not matched the file was re-branded anyway (measured
# again 8/8: ``Adolanium/nastech-clinepass`` 404, ``Adolanium/hermes-clinepass``
# 200).
#
# Matching the host was still only half the identifier.  A catalog entry or a
# docs line reaches into somebody else's tree --
# ``github.com/plastic-labs/honcho/tree/main/hermes-plugin-honcho``, or the
# screenshot ``raw.githubusercontent.com/cocktailpeanut/hermes-mod/master/hermes.png``
# -- and the segment after the repository name is exactly as little ours as the
# name is.  Branding it leaves the owner and branch intact and renames a
# directory or an image that has never carried our brand, so the link 404s while
# its inherited spelling returns 200 (measured in the published fork tree: 78
# distinct URLs of this shape, 76 of them with a live 200 twin).  The tail is
# therefore part of the matched identifier.
GITHUB_REPO_RE = re.compile(
    # A repository identifier in the web form
    # (``github.com/<owner>/<repo>``), the REST form
    # (``api.github.com/repos/<owner>/<repo>``) or the raw-asset form
    # (``raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>``).  The ``repos/``
    # segment belongs to the API path and never to the owner, so it is consumed
    # explicitly; the leading boundary stops ``api.github.com`` from matching with
    # the owner read as a literal ``repos``, which silently preserved a
    # first-party URL (``NousResearch``) that branding must rename, and renamed a
    # foreign one.
    #
    # The raw host is in this alternation because a catalog entry spells its
    # repository in both forms at once, and only matching one of them made the
    # other read as a bare identifier, which silently defeated the
    # preservation above.
    r"(?<![.\w-])"
    r"(?:(?:api|uploads)\.github\.com/repos/|raw\.githubusercontent\.com/"
    r"|(?:[a-z0-9-]+\.)?github\.com/)"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)"
    # The path after the repository name, segment by segment.  Each segment
    # stops at whitespace and at the punctuation that ends a URL in prose (a
    # closing bracket, ``)``, a comma or a sentence mark), at a query or a
    # fragment, and at the next ``/``.  Stopping at the delimiters matters as
    # much as matching: an over-greedy tail would swallow a second URL written
    # right after the first and leave that one un-branded.
    r"""(?P<tail>(?:/[^/?#\s<>"'`|\\)`,;:!]+)*)"""
)

# GitHub owners whose repositories we control and may therefore rename.
OWNED_GITHUB_ORGS: frozenset[str] = frozenset({"nastechresearch", "nastechtest"})


def is_owned_repo_path(path: str) -> bool:
    """True when a GitHub repository identifier sits in an org we control."""
    match = GITHUB_REPO_RE.search(path)
    return match is not None and match.group("owner").lower() in OWNED_GITHUB_ORGS


def preserved_repo_url_spans(
    line: str, rules: "BrandingRules | None" = None
) -> tuple[re.Match, ...]:
    """Repository identifiers in ``line`` that branding must leave verbatim.

    A path is preserved when neither the owner we inherited nor the owner the
    rules would move it into is one we control -- renaming somebody else's
    repository yields a URL that 404s.  ``NousResearch/misaki.git`` is NOT
    preserved, because the rules move it into an organization we do own, so it
    stays a first-party brand leak for the audit to catch.
    """
    rules = rules if rules is not None else BrandingRules()
    spans: list[re.Match] = []
    for match in GITHUB_REPO_RE.finditer(line):
        original = match.group(0)
        if is_owned_repo_path(original):
            continue
        if is_owned_repo_path(rules.brand_repo_path(original)):
            continue
        spans.append(match)
    return tuple(spans)


# ---------------------------------------------------------------------------
# External service identifiers.  A name that lives inside a service somebody
# else runs is as little ours to rename as somebody else's repository: the
# branded spelling is an object the service has never heard of, so the feature
# resolves a 404 at runtime instead of failing to build.
# ---------------------------------------------------------------------------

# DocSearch's index is selected by the ``indexName`` of the app its committed
# ``appId``/``apiKey`` pair can read, so branding that one word while the pair
# stays inherited splits the triple.  Both the JS object form the fork actually
# ships (``indexName: 'hermes docs'``) and the JSON form a crawler config would
# use (``"indexName": "hermes docs"``) are matched; an unquoted YAML value is
# not, because there the boundary of the name is ambiguous.
ALGOLIA_INDEX_NAME_RE = re.compile(
    r"""(?<![A-Za-z0-9_-])["']?indexName["']?[ \t]*:[ \t]*"""
    r"""(?P<quote>["'])(?P<value>[^"'\n]+)(?P=quote)"""
)

# Indices that exist in the app the site's DocSearch credentials point at.
# Measured 2026-10-21 against ``GET /1/indexes`` for appId ``2JLBVEYZN5``, which
# answers exactly ``hermes docs`` and ``hermes docs.tmp``; the fork owns nothing
# there, and the docs-search monitor has been red on every run since the index
# name was branded.
PROVISIONED_ALGOLIA_INDICES: frozenset[str] = frozenset({"hermes docs"})


def provisioned_algolia_indices() -> frozenset[str]:
    """Index names the fork may brand *to*, i.e. names that really exist.

    ``HUNDREDWAYS_ALGOLIA_INDICES`` is a comma list an owner fills in once they
    have created their own Algolia app, indexed the fork's docs domain, and put
    that app's id and search-only key in the site config.  Until then the only
    reachable object is the inherited index, so that name has to survive.
    """
    raw = os.environ.get("HUNDREDWAYS_ALGOLIA_INDICES", "")
    extra = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return {name.lower() for name in PROVISIONED_ALGOLIA_INDICES} | extra


def preserved_algolia_index_spans(
    line: str, rules: "BrandingRules | None" = None
) -> tuple[re.Match, ...]:
    """``indexName`` values in ``line`` that branding must leave verbatim.

    A value is preserved when it names an index the configured app really has
    and branding would move it to a name that app has never heard of -- the
    same test the repository rule makes of an owner, made here of a service's
    object list.  An index nobody has provisioned is not protected: branding an
    unknown string is the ordinary policy.
    """
    rules = rules if rules is not None else BrandingRules()
    provisioned = provisioned_algolia_indices()
    spans: list[re.Match] = []
    for match in ALGOLIA_INDEX_NAME_RE.finditer(line):
        value = match.group("value")
        if value.lower() not in provisioned:
            continue
        if rules.brand_service_name(value).lower() in provisioned:
            continue
        spans.append(match)
    return tuple(spans)


# A whole URL whose host somebody else runs is the same object in a bigger
# namespace: ``docs.honcho.dev/v3/guides/integrations/hermes`` is *their* page
# about the integration, and the branded spelling is a page that has never
# existed.  Measured 2026-10-27 by fetching both spellings of every third-party
# link the published fork tree carries: 20 of 23 sampled pairs answer 200
# inherited and 4xx branded -- ``botcabinet.com/hermes/manual/`` 200 against
# ``botcabinet.com/nastech/manual/`` 404, ``vshulcz.github.io/deja-vu/guide/memory-for-hermes.html``
# 200 against ``.../memory-for-nastech.html`` 404 -- and the two pairs that
# differ only in their fragment carry the inherited anchor id and not the
# branded one.  The scheme is required so a bare domain in prose is never
# mistaken for a link.
EXTERNAL_URL_RE = re.compile(
    r"https?://(?P<host>[A-Za-z0-9._-]+)(?P<tail>[/?#][^\s\"'<>()\[\]{}|`]*)?"
)

# Hosts the fork runs, so a path under them is ours to brand.  Inherited hosts
# are not listed: a URL the rules move *into* one of these is a first-party
# link, which is why the test is made of both spellings.
OWNED_URL_HOSTS: frozenset[str] = frozenset(
    {"nastechresearch.github.io", "nastechresearch.com", "nastechresearch.workers.dev"}
)

# Third-party hosts whose path, query or fragment names an object on *their*
# service, measured live on 2026-10-27 (inherited 200, branded 4xx or an absent
# anchor).  A GitHub Pages host needs no entry here: ``<user>.github.io`` is
# provably that user's site, so it is covered by the shape rule below.
#
# The test for this set is whether the fork CAN create the branded object, not
# whether the branded link happens to 404.  ``discord.gg`` is deliberately
# absent: ``https://discord.com/api/v10/invites/NousResearch`` answers the guild
# "Nous Research" while ``.../invites/NastechResearch`` answers
# ``{"message": "Unknown Invite", "code": 10006}``, and the code
# ``nastechresearch`` is unclaimed.  So 32 links across 19 files of the
# published fork tree -- the new-issue contact form, every README badge, the
# desktop crash overlay and its send-diagnostics dialog, ``nastech debug`` --
# point at an invite nobody has made yet, and the fix is five minutes of owner
# action, not a permanent rule that sends Nastech's users to the upstream
# community.  Preserving it would also have taken 10 birth-tree files against
# the 8 of reviewed drift this engine's parity floor has room for.
#
# When the fork creates its own object on one of these -- a Nastech page on
# Honcho's docs site, say -- delete that host from this set and branding
# resumes on it.  Do not instead delete the URL from the docs: every row here
# is a link a human clicks.
THIRD_PARTY_SERVICE_HOSTS: frozenset[str] = frozenset(
    {
        "botcabinet.com",
        "docs.apify.com",
        "docs.cortexlayer.net",
        "docs.honcho.dev",
        "docs.openmail.sh",
        "hindsight.vectorize.io",
        "linchpin.studio",
        "mybot.farm",
        "pidgeon.ai",
        # ``app.supermemory.ai/integrations?connect=hermes`` is Supermemory's
        # connect deep-link for its own integration, and the guide documenting
        # it is ``supermemory.ai/docs/integrations/hermes``: measured 2026-10-27,
        # that page answers 200 while ``.../integrations/nastech`` answers 404.
        # A subdomain is covered, because the host test matches a host sitting
        # *under* a listed root.
        "supermemory.ai",
    }
)


def owned_url_hosts() -> frozenset[str]:
    """Hosts the fork runs, including any the owner has since provisioned.

    ``HUNDREDWAYS_OWNED_URL_HOSTS`` is a comma list of hosts the fork now
    controls, so a branded path under one is a real object rather than a 404.
    """
    raw = os.environ.get("HUNDREDWAYS_OWNED_URL_HOSTS", "")
    extra = {item.strip().lower().removeprefix("*.") for item in raw.split(",") if item.strip()}
    return {host.lower() for host in OWNED_URL_HOSTS} | extra


def third_party_service_hosts() -> frozenset[str]:
    """Third-party hosts whose branded paths are measured 404s."""
    return {host.lower() for host in THIRD_PARTY_SERVICE_HOSTS}


def _host_under(host: str, roots: frozenset[str]) -> bool:
    """True when ``host`` is one of ``roots`` or sits under one of them."""
    return any(host == root or host.endswith(f".{root}") for root in roots)


def preserved_external_url_spans(
    line: str, rules: "BrandingRules | None" = None
) -> tuple[re.Match, ...]:
    """URLs in ``line`` whose host branding does not control.

    A host is somebody else's when neither the inherited spelling nor the
    branded one is a host the fork runs, and it is either a GitHub Pages site
    (which belongs to the user its subdomain names) or a host on
    :data:`THIRD_PARTY_SERVICE_HOSTS`.  The scan gates call this the same way
    they call :func:`preserved_repo_url_spans`: a brand word inside one of
    these spans is inherited text, not a leak the fork has to fix.
    """
    rules = rules if rules is not None else BrandingRules()
    return tuple(
        match
        for match in EXTERNAL_URL_RE.finditer(line)
        if rules.preserves_external_url(match.group(0))
    )


# ---------------------------------------------------------------------------
# Plugin catalog directories.  A catalog entry says which repository to clone
# and which directory inside it holds the plugin, so the directory belongs to
# whoever runs that repository.  Branding it points the installer at a path
# that has never existed there.
# ---------------------------------------------------------------------------

# The catalog's own shape: one entry per file, both keys at column zero.  A
# value may be quoted, bare, or followed by a YAML comment, and an unquoted
# value may contain an apostrophe -- all of which are ordinary catalog YAML.
CATALOG_REPO_KEY_RE = re.compile(
    r"^repo:[ \t]*(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^#\n]*?)[ \t]*(?:#[^\n]*)?$", re.M
)
CATALOG_SUBDIR_KEY_RE = re.compile(
    r"^subdir:[ \t]*(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^#\n]*?)[ \t]*(?:#[^\n]*)?$", re.M
)


def catalog_key_value(match: re.Match) -> str:
    """The value without its YAML quotes or surrounding space."""
    value = match.group("value").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def catalog_entry_repo_url(text: str) -> str | None:
    """The ``repo:`` URL of a single plugin catalog entry, or ``None``.

    ``None`` when the text is not shaped like one entry: a file with two or
    zero top-level ``repo:`` keys is not a catalog document, and guessing which
    entry an indented ``subdir`` belongs to would protect the wrong repository.
    """
    matches = list(CATALOG_REPO_KEY_RE.finditer(text))
    if len(matches) != 1:
        return None
    return catalog_key_value(matches[0])


def _looks_like_url(text: str) -> bool:
    """True for the scheme-less spelling of an address, as a link label uses it."""
    head = text.split("/")[0]
    if not head or "." not in head:
        return False
    return re.fullmatch(
        r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?(:[0-9]+)?", head
    ) is not None and re.search(r"\.[A-Za-z]{2,}$", head) is not None


# ``[app.supermemory.ai/integrations?connect=hermes](https://app.supermemory.ai/...)``
# shows the reader an address and links to it.  Branding only the href leaves a
# link that works and an address printed beside it that does not exist.
MARKDOWN_URL_LABEL_RE = re.compile(r"\[(?P<label>[^\][\n]+)\]\((?P<href>[^)\s]+)\)")


def preserved_url_link_label_spans(
    line: str, rules: "BrandingRules | None" = None
) -> tuple[re.Match, ...]:
    """Markdown labels that are the address of a URL branding leaves verbatim."""
    rules = rules if rules is not None else BrandingRules()
    return tuple(
        match
        for match in MARKDOWN_URL_LABEL_RE.finditer(line)
        if rules.preserves_url_link_label(match.group("label"), match.group("href"))
    )


def preserved_identifier_names(
    text: str, rules: "BrandingRules | None" = None
) -> tuple[str, ...]:
    """Repository identifiers this text keeps verbatim, as ``owner/repo``.

    The transform preserves such a repository wherever the file spells it --
    its URL, and the same ``owner/repo`` written bare in a comment, a label or a
    sentence (see :meth:`BrandingRules.transform_text`).  The line-scoped brand
    audits cannot repeat the file-level test, so the caller passes this set and
    they exempt a token only while every occurrence of it on the line sits
    inside one of these names.
    """
    rules = rules if rules is not None else BrandingRules()
    return tuple(
        sorted(
            {
                f"{match.group('owner')}/{match.group('repo')}"
                for match in GITHUB_REPO_RE.finditer(text)
                if rules.preserves_repo_path(match.group(0))
            }
        )
    )


def token_only_inside(line: str, token: str, names: tuple[str, ...]) -> bool:
    """True when every ``token`` on ``line`` sits inside one of ``names``."""
    lowered, needle = line.lower(), token.lower()
    spans: list[tuple[int, int]] = []
    for name in names:
        start = lowered.find(name.lower())
        while start != -1:
            spans.append((start, start + len(name)))
            start = lowered.find(name.lower(), start + 1)
    if not spans:
        return False
    position = lowered.find(needle)
    if position == -1:
        return False
    while position != -1:
        if not any(lo <= position and position + len(needle) <= hi for lo, hi in spans):
            return False
        position = lowered.find(needle, position + 1)
    return True


def preserved_catalog_subdir_spans(line: str) -> tuple[re.Match, ...]:
    """The ``subdir`` key on a catalog line whose value the brand must not carry.

    The scan gates cannot repeat the whole-document test
    :meth:`BrandingRules.preserves_catalog_subdir` makes, so they pair this with
    :func:`catalog_entry_repo_url`: the caller passes the document's single
    ``repo:`` URL, and a surviving inherited word then means that repository is
    not ours -- an entry we *do* own arrives already branded.  A document with
    no single ``repo:`` line gets no exemption, exactly as the transform
    preserves nothing in it.
    """
    return tuple(
        match
        for match in CATALOG_SUBDIR_KEY_RE.finditer(line)
        if catalog_key_value(match)
    )


# Placeholder sentinels used to carry a preserved URL through the token pass.
# They live in a private-use block and hold no digits, so no normalizer can
# rewrite them and no brand token can match inside them.
_MASK_OPEN = ""
_MASK_CLOSE = ""
_MASK_BASE = 0xE100


def _mask(index: int) -> str:
    return f"{_MASK_OPEN}{chr(_MASK_BASE + index)}{_MASK_CLOSE}"


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

    def _apply_tokens(self, text: str) -> str:
        """Every rule, with no repository-path exemption."""
        tokens = list(self.tokens)
        pattern = self._pattern()
        branded = pattern.sub(lambda m: self._replacement(m, tokens), text)
        # ``python -m<module>`` is a valid compact spelling. The normal
        # boundary-aware matcher does not rewrite the embedded module name,
        # which would leave generated candidates with stale ``-mhermes_*``
        # targets after branding.
        for tok in tokens:
            branded = re.sub(
                rf"(?<![A-Za-z0-9_])(-I?m){re.escape(tok.match)}(?=[./_A-Za-z0-9-]|$)",
                lambda m, replacement=tok.replace: m.group(1) + replacement,
                branded,
                flags=re.IGNORECASE,
            )
        branded = _RUNNER_LABEL_RE.sub(_normalize_runner, branded)
        branded = _BLACKSMITH_LABEL_RE.sub(_normalize_blacksmith, branded)
        branded = _BLACKSMITH_ACTION_RE.sub(_normalize_blacksmith_action, branded)
        branded = _normalize_test_workers(branded)
        branded = _normalize_os_test_workers(branded)
        return _normalize_test_timeout(branded)

    def brand_repo_path(self, path: str) -> str:
        """The branded form of a single repository identifier."""
        return self._apply_tokens(path)

    def brand_service_name(self, value: str) -> str:
        """The branded form of a single external service object name."""
        return self._apply_tokens(value)

    def preserves_repo_path(self, path: str) -> bool:
        """True when branding must leave this repository identifier verbatim.

        Preserved only when neither endpoint owner is ours: the inherited owner,
        which means it is somebody else's repository, and the owner the rules
        would move it into, which means no rule claims it for us.
        """
        return not is_owned_repo_path(path) and not is_owned_repo_path(
            self.brand_repo_path(path)
        )

    def preserves_external_url(self, url: str) -> bool:
        """True when branding must leave this whole URL verbatim.

        The repository rule's test, made of a host instead of an owner: neither
        the inherited host nor the one the rules would move it into may be a host
        the fork runs, and the path has to be an object that host serves -- a
        GitHub Pages site, which belongs to the user its subdomain names, or a
        host measured on :data:`THIRD_PARTY_SERVICE_HOSTS`.  Anything else stays
        on the ordinary policy, so a badge label or a display-text parameter on a
        third-party host still carries our wordmark.
        """
        match = EXTERNAL_URL_RE.search(url)
        if match is None:
            return False
        host = match.group("host").lower()
        roots = owned_url_hosts()
        if _host_under(host, roots):
            return False
        if _host_under(self.brand_service_name(host).lower(), roots):
            return False
        return host.endswith(".github.io") or _host_under(
            host, third_party_service_hosts()
        )

    def preserves_url_link_label(self, label: str, href: str) -> bool:
        """True when a markdown label is the address its href points at.

        Both halves have to agree before anything is protected: the label is a
        bare URL, its host is the href's host, and that href is a URL branding
        leaves verbatim.  Prose that merely mentions our wordmark beside a link
        -- ``[upstream Hermes integration docs](...)`` -- is still ours to brand.
        """
        if not _looks_like_url(label) or not self.preserves_external_url(href):
            return False
        href_match = EXTERNAL_URL_RE.search(href)
        return href_match is not None and label.split("/")[0].lower() == href_match.group(
            "host"
        ).lower()

    def preserves_catalog_subdir(self, repo_url: str, subdir: str) -> bool:
        """True when a catalog entry's ``subdir`` names a path we do not run.

        The same two-ended test the repository rule makes of an owner, made here
        of the directory inside it: the entry is preserved only when neither the
        inherited repository nor the one the rules would move it into is ours.
        An entry that points at ``NousResearch/hermes-official-plugins`` is not
        preserved, because the rules re-point it at a repository we do publish,
        where a branded directory is one we can create.
        """
        if not subdir:
            return False
        if self.brand_repo_path(subdir) == subdir:
            return False
        return self.preserves_repo_path(repo_url)

    def transform_text(self, text: str) -> str:
        """Apply all branding tokens to free text.

        A ``github.com/<owner>/<repo>`` path that :meth:`preserves_repo_path`
        keeps its inherited spelling -- renaming it yields a repository nobody
        owns -- while everything around it is branded normally.  That holds
        without exception: naming the same identifier again in the same file --
        as a quote-delimited value (``target_repository = "owner/repo"``), a
        markdown label, a YAML comment or a prose sentence -- does not make it
        ours to rename.  A file that spells it twice must keep both halves
        inherited, or the URL and the string it is compared against disagree
        and the link 404s.

        Measured 2026-10-27/28 (upstream 200 -> fork 404): the label in
        ``optional-skills/creative/comfyui/references/template-integrity.md``;
        the comment in ``plugin-catalog/aichipmunk.yaml``; the ``description``
        of the cloud-file-manager entry, which dragged seven raw screenshots
        with it; and a ``.git`` URL beside its bare ``target_repository`` pair,
        branded to ``roqianjas/nastech-account-pool``, a repository that does
        not exist.  The exemption is exactly one drift file at the birth gate;
        ``.github/workflows/stage-rules.yml`` names it and carries the floor
        that admits it.
        """
        return self._transform_text(text, preserve_foreign_repos=True)

    def _transform_text(self, text: str, *, preserve_foreign_repos: bool) -> str:
        if not preserve_foreign_repos:
            return self._apply_tokens(text)
        spans: list[str] = []
        # ``None`` marks a stashed span with no owner to brand into -- an
        # external service's object name, restored exactly as it was found.
        identifiers: list[str | None] = []

        def _stash(match: re.Match) -> str:
            spans.append(match.group(0))
            identifiers.append(f"{match.group('owner')}/{match.group('repo')}")
            return _mask(len(spans) - 1)

        working = GITHUB_REPO_RE.sub(_stash, text)
        provisioned = provisioned_algolia_indices()

        def _stash_index(match: re.Match) -> str:
            value = match.group("value")
            if value.lower() not in provisioned:
                # No evidence this index exists, so there is nothing to protect:
                # branding it is the ordinary policy for an unknown string.
                return match.group(0)
            if self.brand_service_name(value).lower() in provisioned:
                return match.group(0)
            spans.append(match.group(0))
            identifiers.append(None)
            return _mask(len(spans) - 1)

        working = ALGOLIA_INDEX_NAME_RE.sub(_stash_index, working)

        def _stash_url(match: re.Match) -> str:
            url = match.group(0)
            if not self.preserves_external_url(url):
                return url
            if _MASK_OPEN in url:
                # The span holds a repository identifier an earlier pass already
                # stashed.  Stashing the whole URL again would re-insert that
                # placeholder after its own replacement had run, leaving the
                # sentinel in the published text, so the inner identifier keeps
                # its own preservation and the URL is not masked a second time.
                return url
            if self.brand_service_name(url) == url:
                # Nothing would rename it, so it needs no protection -- and
                # stashing it anyway would hide a brand word elsewhere in the
                # same line from the scan gates.
                return url
            spans.append(url)
            identifiers.append(None)
            return _mask(len(spans) - 1)

        def _stash_label(match: re.Match) -> str:
            label, href = match.group("label"), match.group("href")
            if not self.preserves_url_link_label(label, href):
                return match.group(0)
            if self.brand_service_name(label) == label:
                return match.group(0)
            spans.append(label)
            identifiers.append(None)
            return f"[{_mask(len(spans) - 1)}]({href})"

        working = MARKDOWN_URL_LABEL_RE.sub(_stash_label, working)

        working = EXTERNAL_URL_RE.sub(_stash_url, working)

        repo_url = catalog_entry_repo_url(text)
        if repo_url:

            def _stash_subdir(match: re.Match) -> str:
                if _MASK_OPEN in match.group(0):
                    # The line already carries a stashed span; masking it whole
                    # would re-insert that placeholder after its own replacement
                    # had run.  The inner span keeps its own preservation.
                    return match.group(0)
                if not self.preserves_catalog_subdir(repo_url, catalog_key_value(match)):
                    return match.group(0)
                spans.append(match.group(0))
                identifiers.append(None)
                return _mask(len(spans) - 1)

            working = CATALOG_SUBDIR_KEY_RE.sub(_stash_subdir, working)

        # A file that names a preserved repository again must keep that second
        # spelling too, or the URL and the copy code compares against it
        # disagree: `url = ".../roqianjas/hermes-account-pool.git"` beside
        # `target = "roqianjas/hermes-account-pool"` branded the pair to a
        # repository that does not exist.  Masked last, so the URLs above are
        # already placeholders and cannot be matched again.
        preserved_names = {
            name[: -len(".git")] if name.endswith(".git") else name
            for index, name in enumerate(identifiers)
            if name is not None and self.preserves_repo_path(spans[index])
        }
        if preserved_names:
            # A preserved identifier is preserved at every spelling the file
            # uses, including as a path segment.  The REST path form
            # ``/repos/<owner>/<repo>/releases/latest`` is how the fixture
            # servers in the fork's release tests key their responses, and a
            # slash before or after the name is a separator, not part of a
            # different identifier -- blocking on it branded that path while
            # the URL beside it stayed inherited, so the test asked for a
            # repository the fixture never served (404).
            bare = re.compile(
                r"(?<![.\w-])(?P<bare>"
                + "|".join(re.escape(name) for name in sorted(preserved_names))
                + r")(?![\w-])"
            )

            def _stash_bare(match: re.Match) -> str:
                spans.append(match.group(0))
                identifiers.append(None)
                return _mask(len(spans) - 1)

            working = bare.sub(_stash_bare, working)

        if not spans:
            return self._apply_tokens(working)
        branded = self._apply_tokens(working)
        for index, original in enumerate(spans):
            if identifiers[index] is None or self.preserves_repo_path(original):
                keep = original
            else:
                keep = self.brand_repo_path(original)
            branded = branded.replace(_mask(index), keep)
        return branded

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

# Whole path segments whose content belongs to somebody else.  Vendored
# third-party sources and fixtures are content-addressed by their origin: a
# sibling ``*.sha256`` manifest and ``SOURCE.json`` pin the bytes and name the
# real owner.  Branding one changes the digest the consumer verifies and leaves
# the provenance URL pointing at a repository we do not own, so the file is
# copied byte-for-byte like a lockfile.  The match is a full segment, not a
# substring: ``src/vendored_helpers.py`` is ours and still brands.
LOCKED_PATH_SEGMENTS = ("vendor",)

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


def is_vendored_path(path: str) -> bool:
    """True when ``path`` is inside a vendored third-party tree.

    Kept separate from :func:`is_locked_path` because the audits need to tell
    *why* a file is locked: a lockfile's brand words may still be a first-party
    leak worth reporting, while a vendored file's bytes are somebody else's and
    content-addressed by a sibling manifest, so its words are inherited.
    """
    return any(
        segment in LOCKED_PATH_SEGMENTS for segment in path.lower().split("/")
    )


def is_locked_path(path: str) -> bool:
    """True when a path must NOT be auto-branded (content or rename)."""
    lowered = path.lower()
    if lowered.endswith(LOCKED_EXTENSIONS):
        return True
    if path.rsplit("/", 1)[-1] in LOCKED_FILENAMES:
        return True
    if is_vendored_path(path):
        return True
    for sub in LOCKED_PATH_SUBSTRINGS:
        if sub in path:
            return True
    return False
