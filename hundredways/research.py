"""Open-source research: search the web for ideas we can steal.

Two sources:

  * ``catalog`` - an offline, hand-picked list of open-source projects and
    techniques relevant to fork-syncing, rebranding, and patch porting.
  * ``search``  - live GitHub repository search via the public REST API
    (stdlib only, no auth: rate-limited to ~10 req/min).

Both return the same structured ``Idea`` records so the ``100ways research``
command can merge them.  Nothing here writes to disk.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class Idea:
    source: str      # "catalog" | "github"
    title: str
    url: str
    summary: str
    tags: tuple[str, ...] = ()


# Offline idea catalog: open-source techniques we can adapt.  URL-free lines
# describe the idea, not a claim of ownership.
CATALOG: list[Idea] = [
    Idea("catalog", "Patch streaming", "https://git-scm.com/docs/git-format-patch",
         "Stream-edit unified diffs before applying; works for any token rename, not just ours.", ("port", "diff")),
    Idea("catalog", "Tree comparison", "https://git-scm.com/docs/git-ls-tree",
         "Compare whole trees by path set and blob hash before touching the working tree.", ("diff", "verify")),
    Idea("catalog", "Worktree isolation", "https://git-scm.com/docs/git-worktree",
         "Apply risky ports in a throwaway detached worktree so the working branch is never harmed.", ("port", "gate")),
    Idea("catalog", "Word-anchored replace", "https://docs.python.org/3/library/re.html",
         "Lookbehind/lookahead word boundaries so renames never corrupt English words.", ("brand",)),
    Idea("catalog", "Magic-byte typing", "https://en.wikipedia.org/wiki/Magic_number_(programming)",
         "Identify file formats by signature bytes, never by extension.", ("scan",)),
    Idea("catalog", "Cherry-pick + amend", "https://git-scm.com/docs/git-cherry-pick",
         "Cherry-pick upstream then amend with the brand fix; keeps history and authors.", ("port",)),
    Idea("catalog", "First-parent review", "https://git-scm.com/docs/git-rev-list",
         "Walk the first-parent chain to review only merged changes, not every branch point.", ("diff",)),
    Idea("catalog", "Three-way apply", "https://git-scm.com/docs/git-apply",
         "--3way applies a patch against the nearest common ancestor, not just the index.", ("port",)),
    Idea("catalog", "File type fuzzing", "https://en.wikipedia.org/wiki/Fuzzing",
         "Feed mutated assets through the pipeline to prove the scanner never misbrands a binary.", ("scan", "verify")),
    Idea("catalog", "Golden-file tests", "https://docs.pytest.org/",
         "Reproduce the known-good birth commit as a golden test of the whole brand pipeline.", ("verify",)),
    Idea("catalog", "Renumbered IDs", "https://en.wikipedia.org/wiki/Sequence_diagram",
         "Verify refs to renamed identifiers are renumbered consistently, not just strings swapped.", ("brand", "verify")),
    Idea("catalog", "Sidecar manifest", "https://en.wikipedia.org/wiki/Sidecar_file",
         "Keep a manifest of upstream->branded path pairs to prove rename coverage.", ("verify", "port")),
    Idea("catalog", "Difference engine", "https://en.wikipedia.org/wiki/Data_diff",
         "Report character and line deltas so operators see exactly how far off a port is.", ("diff",)),
    Idea("catalog", "Contract tests", "https://en.wikipedia.org/wiki/Design_by_contract",
         "Assert invariants between upstream and branded trees (every path has a twin), not snapshots.", ("verify",)),
    Idea("catalog", "Renamed-from tracking", "https://git-scm.com/docs/git-diff",
         "Use rename detection in diffs so a renamed file is recognized, not deleted+added.", ("diff",)),
]


def search_github(query: str, limit: int = 10, max_results: int = 8) -> list[Idea]:
    """Live GitHub repository search (public REST API, no auth needed)."""
    params = urllib.parse.urlencode(
        {"q": query, "sort": "stars", "order": "desc", "per_page": limit}
    )
    url = f"https://api.github.com/search/repositories?{params}"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "100ways"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # network/rate-limit failures degrade to catalog
        return [Idea("github", f"search failed: {exc}", "", "live search unavailable", ("offline",))]
    ideas: list[Idea] = []
    for item in payload.get("items", [])[:max_results]:
        ideas.append(
            Idea(
                source="github",
                title=item.get("full_name", query),
                url=item.get("html_url", ""),
                summary=(item.get("description") or "").strip() or "(no description)",
                tags=("search",),
            )
        )
    return ideas


def research(query: str, include_catalog: bool = True, live: bool = True) -> list[Idea]:
    """Merge offline catalog hits with live GitHub search for ``query``."""
    ideas: list[Idea] = []
    if include_catalog:
        terms = query.lower().split()
        matched = [
            idea for idea in CATALOG
            if any(term in idea.summary.lower() or term in " ".join(idea.tags)
                   or term in idea.title.lower() for term in terms)
        ]
        # a query is a topic, not a literal match: show the full catalog when
        # nothing hits so users always get ideas to steal
        ideas.extend(matched or CATALOG)
    if live:
        ideas.extend(search_github(query))
    return ideas
