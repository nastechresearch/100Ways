"""Every URL the fork ships, classified against policy and, when asked, a socket.

The engine rewrites text with ``rules.BrandingRules`` and can therefore *name*
the URL a link should have been; before this module nothing in a sync ever looked
at the links themselves, so a placeholder left in the README, a link still pointed
at upstream, or a documentation URL that 404s went out unseen.

Two rules shape the verdicts.

**A status is a measurement, never an assumption.**  Classification is offline and
always available: policy lists plus the brand map say ``BRAND_VIOLATION``,
``PLACEHOLDER`` and ``malformed``.  Saying a link *works* takes a probe, and a
probe is opt-in per run.  A link no probe reached is ``UNREACHABLE`` with the
reason -- including "this run made no probe" -- because reporting an unlooked-at
link as fine is exactly the failure this module exists to end.

**The fix is part of the finding.**  An unbranded URL carries the branded form the
engine's own token map produces, so the operator reads the replacement rather than
re-deriving it.  A placeholder carries the hosts that are allowed instead.

Ordering is by severity (:data:`SEVERITY_ORDER`): a brand violation explains why a
link is also dead, and the actionable verdict comes first.  Every run of the same
tree with the same inputs produces byte-identical output -- the walk is sorted, the
probes are deduplicated, and nothing here reads a clock into a finding.
"""

from __future__ import annotations

import dataclasses
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .policy import load as load_policy
from .rules import BrandingRules
from .secrets import redact

__all__ = [
    "OK",
    "BROKEN",
    "PLACEHOLDER",
    "BRAND_VIOLATION",
    "UNREACHABLE",
    "STATUSES",
    "DECIDED",
    "SEVERITY_ORDER",
    "MAX_REDIRECTS",
    "TEXT_SUFFIXES",
    "Link",
    "Probe",
    "UrlFinding",
    "UrlPolicy",
    "host_of",
    "extract_links",
    "probe_urls",
    "classify",
    "scan_tree",
    "findings_to_json",
    "render",
    "counts",
    "probe_tally",
    "URL_POLICY_PATH",
    "NOT_PROBED",
]

OK = "OK"
BROKEN = "BROKEN"
PLACEHOLDER = "PLACEHOLDER"
BRAND_VIOLATION = "BRAND_VIOLATION"
UNREACHABLE = "UNREACHABLE"

STATUSES: tuple[str, ...] = (OK, BROKEN, PLACEHOLDER, BRAND_VIOLATION, UNREACHABLE)
# The statuses that are a verdict rather than a shrug.  ``UNREACHABLE`` is absent
# from this list on purpose: a link nobody looked at is neither a red to fail a run
# on nor a pass to claim, and a gate that reads it either way invents evidence.
DECIDED: tuple[str, ...] = (BRAND_VIOLATION, PLACEHOLDER, BROKEN)
# What an operator should read first: a link that names the wrong brand explains
# itself better than "it 404s", and both beat "we could not look".
SEVERITY_ORDER: tuple[str, ...] = (
    BRAND_VIOLATION,
    PLACEHOLDER,
    BROKEN,
    UNREACHABLE,
    OK,
)

MAX_REDIRECTS = 5
USER_AGENT = "nastech-100ways-urlcheck/1.0"
# The policy that ships with the engine, resolved from ``__file__`` the same way
# ``readme`` finds the README: a caller asks for "the URL policy", not for a path
# it has to reconstruct.
URL_POLICY_PATH = str(
    Path(__file__).resolve().parent.parent / "config" / "url-policy.yml"
)
# Minted here for the reader's sake: a finding whose detail starts with this
# prefix is one where no request was made.  The count a report prints comes from
# ``UrlFinding.probed``, so this string is explanation, not evidence.
NOT_PROBED = "not probed: "

_URL_RE = re.compile(r"https?://[^\s<>\"'{}|\\^`\[\]]+")
# Files worth reading for links.  Anything else (images, lockfiles, vendored
# binaries) is skipped rather than decoded and guessed at.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".md", ".markdown", ".rst", ".txt", ".html", ".htm", ".xml", ".svg",
        ".yml", ".yaml", ".json", ".jsonc", ".toml", ".cfg", ".ini", ".conf",
        ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh", ".bash",
        ".ps1", ".css", ".scss", ".vue", ".svelte", ".rb", ".go", ".rs",
    }
)
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"})

_TRAILING_PUNCTUATION = ".,;:!?'\"`"


@dataclass(frozen=True)
class Link:
    """One URL, exactly where it was found."""

    url: str
    path: str
    line: int
    column: int
    context: str


@dataclass(frozen=True)
class Probe:
    """What a live check learned about one URL -- or why it learned nothing."""

    url: str = ""
    kind: str = "not-probed"  # ok | http | timeout | network | redirect-limit | not-probed
    probed: bool = False
    status_code: int | None = None
    final_url: str = ""
    hops: tuple[tuple[int, str], ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class UrlFinding:
    link: Link
    status: str
    detail: str
    suggestion: str = ""
    # Whether a network request was actually made for this link.  A policy verdict
    # is not a probe: a brand violation found offline must never be counted as a
    # link the network confirmed, so the fact rides on the finding instead of being
    # guessed from its prose.
    probed: bool = False


_POLICY_SCHEMA: dict[str, str] = {
    "version": "int",
    "allowed_hosts": "list",
    "upstream_hosts": "list",
    "placeholder_hosts": "list",
    "ignore_paths": "list",
    "probe.timeout_seconds": "int",
    "probe.max_urls": "int",
    "probe.wall_budget_seconds": "int",
}


@dataclass(frozen=True)
class UrlPolicy:
    version: int
    allowed_hosts: tuple[str, ...]
    upstream_hosts: tuple[str, ...]
    placeholder_hosts: tuple[str, ...]
    ignore_paths: tuple[str, ...]
    timeout_seconds: float
    max_urls: int
    wall_budget_seconds: float

    @staticmethod
    def load(path: str) -> "UrlPolicy":
        doc = load_policy(path, _POLICY_SCHEMA)
        probe = doc["probe"]
        return UrlPolicy(
            version=doc["version"],
            allowed_hosts=_hosts(doc["allowed_hosts"]),
            upstream_hosts=_hosts(doc["upstream_hosts"]),
            placeholder_hosts=_hosts(doc["placeholder_hosts"]),
            ignore_paths=tuple(str(item) for item in doc["ignore_paths"]),
            timeout_seconds=float(probe["timeout_seconds"]),
            max_urls=int(probe["max_urls"]),
            wall_budget_seconds=float(probe["wall_budget_seconds"]),
        )

    def replaced(self, **overrides: Any) -> "UrlPolicy":
        """This policy with a few fields swapped -- tests move one knob, not all."""
        values: dict[str, Any] = {
            "version": self.version,
            "allowed_hosts": self.allowed_hosts,
            "upstream_hosts": self.upstream_hosts,
            "placeholder_hosts": self.placeholder_hosts,
            "ignore_paths": self.ignore_paths,
            "timeout_seconds": self.timeout_seconds,
            "max_urls": self.max_urls,
            "wall_budget_seconds": self.wall_budget_seconds,
        }
        for name, value in overrides.items():
            if name not in values:
                raise KeyError(f"unknown policy field {name!r}")
            values[name] = _as_tuple(value) if isinstance(value, (list, tuple)) else value
        return UrlPolicy(**values)

    def matches(self, field_name: str, host: str) -> bool:
        """True when ``host`` is one of the listed hosts or lives under it.

        ``nastechresearch.com`` covers ``docs.nastechresearch.com``; it must not
        cover ``evil-nastechresearch.com``, which is why the suffix is dotted.
        """
        if not host:
            return False
        needle = host.lower()
        for listed in getattr(self, field_name):
            entry = str(listed).lower().strip(".")
            if needle == entry or needle.endswith("." + entry):
                return True
        return False

    def is_ignored(self, path: str) -> bool:
        """True when a policy fragment covers this repo-relative path.

        A fragment that starts with ``/`` anchors at the repository root, so
        ``/site/status.json`` means that file and not a file of the same name
        under ``docs/``.  Any other fragment matches wherever it appears, which
        is how ``package-lock.json`` skips every lockfile in the tree.
        """
        rooted = "/" + path.lstrip("/")
        for fragment in self.ignore_paths:
            if fragment.startswith("/"):
                if rooted.startswith(fragment) or rooted == fragment.rstrip("/"):
                    return True
            elif fragment in path:
                return True
        return False


def _hosts(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(value).strip().lower() for value in values if str(value).strip())


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(str(item).strip().lower() for item in value if str(item).strip())


def host_of(url: str) -> str:
    """The lowercase hostname of ``url``, or ``''`` when it has none."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    try:
        return (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _trim(candidate: str) -> str:
    url = candidate.rstrip(_TRAILING_PUNCTUATION)
    if url.endswith(")") and "(" not in url:
        url = url[:-1].rstrip(_TRAILING_PUNCTUATION)
    return url


def extract_links(text: str, path: str) -> list[Link]:
    """Every http(s) URL in ``text``, in file order, each with its location.

    Bare URLs, markdown links, ``<...>`` autolinks and HTML attributes all
    contain the URL literally, so one scan finds them; the wrapper is what tells
    a markdown parser where a link ends, not us.
    """
    found: list[Link] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in _URL_RE.finditer(line):
            url = _trim(match.group(0))
            if not url:
                continue
            found.append(
                Link(
                    url=url,
                    path=path,
                    line=number,
                    column=match.start(),
                    context=redact(line.strip()),
                )
            )
    return found


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Let the caller see each hop, so a chain and a loop are both reportable."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(_NoRedirect())


def _request(url: str) -> urllib.request.Request:
    # GET, not HEAD: some servers answer HEAD with 405 for a page a browser
    # loads fine, and a 405 we invent is a red we invent.
    return urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")


def _classify_error(exc: urllib.error.URLError, timeout: float) -> Probe | None:
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError, socket.timeout)) or isinstance(exc, TimeoutError):
        return Probe(kind="timeout", probed=True, reason=f"timeout after {timeout:g}s")
    if isinstance(exc, urllib.error.HTTPError):  # pragma: no cover - defensive
        return None
    text = str(reason) or type(reason).__name__
    return Probe(kind="network", probed=True, reason=text[:200])


def _probe_one(url: str, timeout: float, opener: urllib.request.OpenerDirector) -> Probe:
    current = url
    hops: list[tuple[int, str]] = []
    for _ in range(MAX_REDIRECTS + 1):
        try:
            with opener.open(_request(current), timeout=timeout) as response:
                code = int(response.status)
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            if 300 <= code < 400:
                location = exc.headers.get("Location", "") if exc.headers else ""
                hops.append((code, current))
                if not location:
                    return Probe(
                        kind="redirect-limit", probed=True, status_code=code,
                        hops=tuple(hops), reason=f"{code} with no Location header",
                    )
                current = urllib.parse.urljoin(current, location)
                continue
            return Probe(
                kind="http", probed=True, status_code=code, final_url=current,
                hops=tuple(hops), reason=(exc.reason or "")[:120],
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            probe = _classify_error(exc, timeout)
            if probe is not None:
                return probe
            return Probe(kind="network", probed=True, reason=str(exc)[:200])
        except (ValueError, OSError) as exc:
            return Probe(kind="network", probed=True, reason=f"{type(exc).__name__}: {exc}"[:200])
        hops.append((code, current))
        return Probe(
            kind="ok" if 200 <= code < 300 else "http",
            probed=True,
            status_code=code,
            final_url=current,
            hops=tuple(hops),
        )
    return Probe(
        kind="redirect-limit",
        probed=True,
        status_code=hops[-1][0] if hops else None,
        hops=tuple(hops),
        reason=f"still redirecting after {MAX_REDIRECTS} hops",
    )


def probe_urls(
    urls: Sequence[str],
    timeout_seconds: float,
    max_urls: int | None = None,
    wall_budget_seconds: float | None = None,
    *,
    opener: urllib.request.OpenerDirector | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Probe]:
    """GET every URL once, within both budgets, and report what each said.

    ``max_urls`` and ``wall_budget_seconds`` are honest cut-offs, not failures: a
    URL the budget could not reach comes back ``probed=False`` with the reason, so
    the caller reports it as unverified instead of quietly dropping it.
    """
    director = opener or _opener()
    deadline = (
        clock() + wall_budget_seconds if wall_budget_seconds is not None else None
    )
    results: dict[str, Probe] = {}
    for index, url in enumerate(dict.fromkeys(urls)):
        if max_urls is not None and index >= max_urls:
            results[url] = Probe(
                url=url, reason=f"outside the probe budget (max_urls={max_urls})"
            )
            continue
        if deadline is not None and clock() >= deadline:
            results[url] = Probe(
                url=url, reason="outside the wall budget (no time left in this run)"
            )
            continue
        try:
            probe = _probe_one(url, timeout_seconds, director)
        except Exception as exc:  # a probe must never take the run down with it
            probe = Probe(kind="network", reason=f"{type(exc).__name__}: {exc}"[:200])
        results[url] = dataclasses.replace(probe, url=url)
    return results


def _malformed(url: str, host: str) -> str:
    """Why this URL cannot be looked at at all, or ``''`` when it can."""
    if url.startswith("//"):
        return "a scheme-relative URL cannot be checked outside a page"
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        return f"cannot be parsed: {exc}"
    if parsed.scheme not in {"http", "https"}:
        return f"scheme {parsed.scheme!r} is not a web link"
    if not parsed.netloc:
        return "no host"
    if " " in parsed.netloc:
        return "the host contains a space"
    if "." not in host and not host.startswith("["):
        return "the host has no dot"
    if parsed.scheme == "https" and parsed.netloc.endswith(":"):
        return "an empty port"
    return ""


def _branded(url: str, rules: BrandingRules) -> str:
    """The URL the brand map says this should be, or ``''`` when it says nothing.

    ``transform_text`` exempts a repository path whose owner is not ours; a URL
    it leaves alone is a URL the engine considers correct, not a finding.
    """
    candidate = rules.transform_text(url)
    return candidate if candidate != url else ""


def _chain_detail(probe: Probe) -> str:
    hops = " -> ".join(f"{code} {url}" for code, url in probe.hops)
    if len(probe.hops) > 1:
        return f"HTTP {probe.hops[0][0]}, chain: {hops}"
    return f"HTTP {probe.status_code}"


def classify(
    link: Link,
    policy: UrlPolicy,
    probes: Mapping[str, Probe] | None = None,
    rules: BrandingRules | None = None,
) -> UrlFinding:
    """One link's verdict, its evidence, and the change it implies."""
    rules = rules or BrandingRules()
    host = host_of(link.url)
    problem = _malformed(link.url, host)
    if problem:
        return UrlFinding(link, BROKEN, f"malformed URL: {problem}")

    replacement = _branded(link.url, rules)
    if replacement:
        return UrlFinding(
            link,
            BRAND_VIOLATION,
            "the brand map rewrites this URL, so the file still names upstream "
            f"(host {host})",
            replacement,
        )
    if policy.matches("upstream_hosts", host):
        return UrlFinding(
            link,
            BRAND_VIOLATION,
            f"{host} is on the upstream-host list in the URL policy",
            f"point it at the Nastech page instead of {host}, or list {host} in "
            "ignore_paths if naming upstream is the sentence's whole subject",
        )
    if policy.matches("placeholder_hosts", host):
        allowed = ", ".join(policy.allowed_hosts[:3])
        return UrlFinding(
            link,
            PLACEHOLDER,
            f"{host} is a documentation placeholder that must not ship",
            f"replace it with the real page, e.g. a host our policy allows: {allowed}",
        )

    probe = None if probes is None else probes.get(link.url)
    if probes is None:
        return UrlFinding(
            link, UNREACHABLE, f"{NOT_PROBED}this run asked for offline classification"
        )
    if probe is None:
        return UrlFinding(link, UNREACHABLE, f"{NOT_PROBED}this URL was never sent")
    if not probe.probed:
        return UrlFinding(link, UNREACHABLE, f"{NOT_PROBED}{probe.reason}")
    # Everything below is the network's own answer: a request left this process.
    if probe.kind == "timeout":
        return UrlFinding(
            link, UNREACHABLE, f"probe did not complete: {probe.reason}", probed=True
        )
    if probe.kind == "network":
        return UrlFinding(
            link, UNREACHABLE, f"probe did not complete: {probe.reason}", probed=True
        )
    if probe.kind == "redirect-limit":
        return UrlFinding(
            link,
            BROKEN,
            f"redirect limit: {probe.reason} ({_chain_detail(probe)})",
            probed=True,
        )
    if probe.status_code is not None and probe.status_code >= 400:
        return UrlFinding(
            link,
            BROKEN,
            f"HTTP {probe.status_code} {probe.reason}".strip(),
            "update or remove the link; if the page moved, the redirect target is "
            "the URL to write",
            probed=True,
        )
    return UrlFinding(link, OK, _chain_detail(probe), probed=True)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binary or unreadable: not a file a link can be read out of


def scan_tree(
    root: str,
    policy: UrlPolicy,
    probe: bool = False,
    *,
    rules: BrandingRules | None = None,
    opener: urllib.request.OpenerDirector | None = None,
) -> list[UrlFinding]:
    """Every link in every text file under ``root``, in deterministic order.

    Probing is opt-in: a sync runs this on a tree of thousands of files, and the
    offline pass is the one that belongs in CI.
    """
    base = Path(root)
    links: list[Link] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(base).as_posix()
        if any(part in _SKIP_DIRS for part in relative.split("/")[:-1]):
            continue
        if policy.is_ignored(relative):
            continue
        text = _read(path)
        if text is None:
            continue
        links.extend(extract_links(text, relative))
    probes: Mapping[str, Probe] | None = None
    if probe:
        probes = probe_urls(
            [link.url for link in links],
            timeout_seconds=policy.timeout_seconds,
            max_urls=policy.max_urls,
            wall_budget_seconds=policy.wall_budget_seconds,
            opener=opener,
        )
    return [classify(link, policy, probes, rules) for link in links]


def findings_to_json(findings: Sequence[UrlFinding]) -> list[dict[str, Any]]:
    return [
        {
            "url": item.link.url,
            "path": item.link.path,
            "line": item.link.line,
            "column": item.link.column,
            "status": item.status,
            "detail": item.detail,
            "suggestion": item.suggestion,
            "context": item.link.context,
            "probed": item.probed,
        }
        for item in findings
    ]


def counts(findings: Sequence[UrlFinding]) -> dict[str, int]:
    """How many links landed in each status, every status named."""
    tally = {status: 0 for status in STATUSES}
    for item in findings:
        tally[item.status] = tally.get(item.status, 0) + 1
    return tally


def probe_tally(findings: Sequence[UrlFinding]) -> tuple[int, int]:
    """``(probed, not probed)`` for a finding list -- the split a report must state.

    Counted from :attr:`UrlFinding.probed`, the fact :func:`classify` recorded when
    it held a live answer, not from a guess about the prose.  A policy verdict and an
    offline pass both land on the unprobed side, which is what lets a reader tell
    "the network said 200" from "nobody looked".
    """
    probed = sum(1 for item in findings if item.probed)
    return probed, len(findings) - probed


def render(findings: Sequence[UrlFinding], probed: int, unprobed: int) -> str:
    """The human report: what was measured, then each verdict with its fix."""
    tally = counts(findings)
    lines = [
        f"URL check: {len(findings)} link(s); {probed} probed, {unprobed} not probed",
        "counts: " + ", ".join(f"{name}={tally[name]}" for name in STATUSES),
    ]
    for status in SEVERITY_ORDER:
        group = [item for item in findings if item.status == status]
        if not group or status == OK:
            continue
        lines.append(f"{status} ({len(group)})")
        for item in group:
            lines.append(f"  {item.link.path}:{item.link.line}  {item.link.url}")
            lines.append(f"      {item.detail}")
            if item.suggestion:
                lines.append(f"      -> {item.suggestion}")
    if not any(item.status != OK for item in findings):
        lines.append("no finding: every link is policy-clean" + ("" if probed else " (offline)"))
    return "\n".join(lines)
