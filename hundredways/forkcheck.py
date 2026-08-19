"""Fork-consistency check: prove the branded tree stays byte-faithful to the
nastech-agent fork, so a pushed PR shows ONLY the upstream updates (clean new
commits, never whole-tree churn) and never drops a fork-local feature.

Three guarantees, enforced by ``fork_consistency`` against the real fork:

  * **No feature loss.**  Every file the fork carries must exist in the
    branded tree.  Fork-local files (owned-assets registry, contributor
    emails, fork-only skills/tests) have no upstream source and must be
    PRESERVED verbatim -- otherwise the PR deletes them.
  * **No spurious modification.**  Files upstream did not touch must be
    byte-identical to the fork (``identical``).  Only files upstream changed
    may differ (``updated``), and every added line must be brand-clean.
  * **No brand violation on the update.**  For files that DO change, the
    added lines (diff fork -> branded) must contain no ``hermes``/``nous``
    token the rules would rewrite.  A leftover token means the update
    slipped past branding -- reported with file:line so it can be fixed,
    never silently merged.

Line-level accounting makes the report exact: every ``updated`` entry carries
its added/deleted line counts, every violation carries its line number.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .rules import (
    BrandingRules,
    collision_safe_path_map,
    is_immutable_path,
    is_locked_path,
    transform_contributor_email_path,
    transform_contributor_email_text,
)
from .scanner import is_text


@dataclass
class ForkViolation:
    """A brand token the rules WOULD rewrite, found where it should not be.

    ``line`` is 1-based; ``snippet`` is the raw offending line.
    """
    path: str
    line: int
    snippet: str


@dataclass
class ForkEntry:
    """Per-file fork-consistency status."""
    path: str            # path in the branded tree / fork (fork-relative)
    status: str          # identical | updated | added | missing | local_only | locked
    added_lines: int = 0
    deleted_lines: int = 0
    violations: list[ForkViolation] = field(default_factory=list)


@dataclass
class ForkCheckReport:
    entries: list[ForkEntry] = field(default_factory=list)
    features_fork: int = 0          # feature-doc count in the fork
    features_branded: int = 0       # feature-doc count in the branded tree
    preserved: list[str] = field(default_factory=list)  # fork-local files carried over

    @property
    def statuses(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries:
            counts[entry.status] = counts.get(entry.status, 0) + 1
        return counts

    @property
    def violation_count(self) -> int:
        return sum(len(entry.violations) for entry in self.entries)

    @property
    def updated_lines(self) -> tuple[int, int]:
        added = sum(entry.added_lines for entry in self.entries if entry.status == "updated")
        deleted = sum(entry.deleted_lines for entry in self.entries if entry.status == "updated")
        return added, deleted

    def summary(self) -> str:
        statuses = self.statuses
        added_lines, deleted_lines = self.updated_lines
        return (
            f"{statuses.get('identical', 0)} identical, {statuses.get('updated', 0)} updated "
            f"(+{added_lines}/-{deleted_lines} lines), {statuses.get('added', 0)} added, "
            f"{statuses.get('missing', 0)} missing, "
            f"{statuses.get('local_only', 0)} fork-local-unpreserved, "
            f"{statuses.get('stale_upstream', 0)} stale-upstream, "
            f"{statuses.get('locked', 0)} locked/binary, "
            f"{statuses.get('relocated', 0)} collision-safe relocated, "
            f"{len(self.preserved)} preserved fork-local files, "
            f"{self.violation_count} violations"
        )

    def gate_passes(self, allow_violations: int = 0) -> bool:
        """True when the branded candidate contains no unsafe retained source."""
        for entry in self.entries:
            if entry.status in ("missing", "local_only", "stale_upstream"):
                return False
        return self.violation_count <= allow_violations


@dataclass(frozen=True)
class SourceChange:
    """One direct upstream tree change, mapped to the NasTech candidate path."""

    status: str  # added | modified | deleted | renamed
    old_path: str = ""
    new_path: str = ""
    old_mapped: str = ""
    new_mapped: str = ""


@dataclass
class SourceDeltaReport:
    """Exact `git diff --name-status` evidence for a source synchronization."""

    baseline_sha: str
    upstream_sha: str
    changes: list[SourceChange] = field(default_factory=list)
    complete: bool = False

    @property
    def obsolete_mapped(self) -> set[str]:
        """Candidate paths retired by an upstream deletion or rename."""
        return {
            change.old_mapped
            for change in self.changes
            if change.status in {"deleted", "renamed"} and change.old_mapped
        }

    @property
    def counts(self) -> dict[str, int]:
        values = {"added": 0, "modified": 0, "deleted": 0, "renamed": 0}
        for change in self.changes:
            values[change.status] = values.get(change.status, 0) + 1
        return values

    def summary(self) -> str:
        counts = self.counts
        status = "complete" if self.complete else "baseline-unavailable"
        return (
            f"{status}: +{counts['added']} ~{counts['modified']} "
            f"-{counts['deleted']} ↪{counts['renamed']}"
        )


_JUNK_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv"}


def _walk(root: str) -> list[str]:
    """All files under ``root``, skipping dependency/build junk.

    When ``root`` is a git checkout, the git-tracked file set is authoritative
    (build artifacts like ``website/node_modules/`` or ``__pycache__`` must not
    be compared — they never enter the PR).
    """
    git_root = os.path.join(root, ".git")
    if os.path.isdir(git_root) or os.path.isfile(git_root):
        try:
            out = subprocess.run(
                ["git", "-C", root, "ls-files"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode == 0:
                return sorted(line for line in out.stdout.splitlines() if line)
        except (OSError, subprocess.TimeoutExpired):
            pass
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _JUNK_DIRS]
        for fn in filenames:
            out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


def _read(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


def _brand_violations(text: str, rules: BrandingRules) -> list[int]:
    """Line numbers (1-based) whose line would change under the rules."""
    lines = text.splitlines()
    return [i + 1 for i, line in enumerate(lines) if rules.transform_text(line) != line]


def scan_brand_violations(root: str, rules: BrandingRules | None = None,
                          skip_immutable: bool = True) -> list[ForkViolation]:
    """Scan a whole tree for brand tokens the rules would rewrite.

    Skips locked (lockfiles, binaries) and, by default, immutable real-data
    files (contributor emails) where a token is a legitimate email address.
    Returns every (path, line, snippet) hit -- the full-tree sweep used by
    the CI stage and the ``forkcheck`` command.
    """
    rules = rules or BrandingRules()
    hits: list[ForkViolation] = []
    for rel in _walk(root):
        if is_locked_path(rel):
            continue
        if skip_immutable and is_immutable_path(rel):
            continue
        data = _read(os.path.join(root, rel))
        if not is_text(data):
            continue
        for line_no in _brand_violations(data.decode("utf-8", "replace"), rules):
            snippet = data.decode("utf-8", "replace").splitlines()[line_no - 1]
            hits.append(ForkViolation(path=rel, line=line_no, snippet=snippet[:200]))
    return hits


def _feature_docs(root: str) -> int:
    """Count feature documentation pages (the fork's 'features' surface)."""
    base = os.path.join(root, "website", "docs", "user-guide", "features")
    if not os.path.isdir(base):
        return 0
    return len([f for f in os.listdir(base) if f.endswith(".md")])


def _added_line_violations(fork_text: str, branded_text: str,
                           rel: str, rules: BrandingRules) -> list[ForkViolation]:
    """Diff fork version -> branded version; flag ADDED lines that still carry
    a brandable token.  Added lines are the upstream update -- they must be
    branded.  ``branded_text`` line numbers are reported (1-based)."""
    fork_lines = fork_text.splitlines()
    branded_lines = branded_text.splitlines()
    sm = difflib.SequenceMatcher(None, fork_lines, branded_lines)
    hits: list[ForkViolation] = []
    for op in sm.get_opcodes():
        if op[0] != "insert":
            continue
        for k in range(op[3], op[4]):
            line = branded_lines[k] if k < len(branded_lines) else ""
            if rules.transform_text(line) != line:
                hits.append(ForkViolation(path=rel, line=k + 1, snippet=line[:200]))
    return hits


def source_tree_delta(
    repo: str,
    baseline_sha: str,
    upstream_sha: str,
    rules: BrandingRules | None = None,
) -> SourceDeltaReport:
    """Return direct, rename-aware upstream tree evidence for one sync.

    A previous upstream SHA is required for deletion and rename evidence.  The
    caller treats an incomplete report as a fail-closed review condition rather
    than guessing whether an extra fork file is truly local or stale upstream.
    """
    rules = rules or BrandingRules()
    report = SourceDeltaReport(baseline_sha, upstream_sha)
    if not baseline_sha or not upstream_sha:
        return report
    ancestry = subprocess.run(
        [
            "git",
            "-C",
            repo,
            "merge-base",
            "--is-ancestor",
            baseline_sha,
            upstream_sha,
        ],
        capture_output=True,
        text=True,
    )
    if ancestry.returncode:
        return report
    result = subprocess.run(
        [
            "git", "-C", repo, "diff", "--name-status", "--find-renames=50%",
            f"{baseline_sha}..{upstream_sha}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return report
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if not fields:
            continue
        code = fields[0][:1]
        if code in {"R", "C"} and len(fields) == 3:
            old_path, new_path = fields[1:]
            status = "renamed" if code == "R" else "added"
            report.changes.append(
                SourceChange(
                    status,
                    old_path,
                    new_path,
                    rules.transform_path(old_path),
                    rules.transform_path(new_path),
                )
            )
        elif code == "D" and len(fields) == 2:
            old_path = fields[1]
            report.changes.append(
                SourceChange(
                    "deleted",
                    old_path=old_path,
                    old_mapped=rules.transform_path(old_path),
                )
            )
        elif code in {"A", "M", "T"} and len(fields) == 2:
            new_path = fields[1]
            status = "added" if code == "A" else "modified"
            report.changes.append(
                SourceChange(
                    status,
                    new_path=new_path,
                    new_mapped=rules.transform_path(new_path),
                )
            )
    report.complete = True
    return report


def _upstream_mapped(upstream: list[str], rules: BrandingRules) -> set[str]:
    """Paths upstream files map to in the branded tree — mirroring brand_tree.

    Contributor records retain their bytes, but their destination names are
    collision-safe so distinct upstream identities never alias on a
    case-insensitive filesystem.  A fork file at a mapped path is upstream
    provided; anything else remains fork-local and is preserved.
    """
    return set(collision_safe_path_map(upstream, rules).values())


def _is_relocated_collision_alias(
    rel: str,
    path_map: dict[str, str],
    fork_root: str,
    branded_root: str,
    rules: BrandingRules | None = None,
) -> bool:
    """Return true only when a legacy raw path is byte-preserved at its safe path.

    A prior candidate may contain an unsuffixed contributor record from before
    collision handling existed.  It is safe to omit that legacy alias only if
    its collision-safe upstream destination exists and contains exactly the
    same bytes; custom fork data is never discarded.
    """
    rules = rules or BrandingRules()
    mapped = path_map.get(rel)
    is_email = "contributors/emails/" in rel.lower()
    # A legacy fork can carry a pre-policy email alias whose source spelling
    # differs from the current upstream spelling only by case or a brand token.
    # Resolve it through the same strict email mapper, but only when that
    # destination is an actual current upstream destination.
    if is_email and (not mapped or mapped == rel):
        candidate = transform_contributor_email_path(rel, rules)
        if candidate in set(path_map.values()) and candidate != rel:
            mapped = candidate
    if not mapped or mapped == rel:
        return False
    source = os.path.join(fork_root, rel)
    destination = os.path.join(branded_root, mapped)
    if not os.path.isfile(source) or not os.path.isfile(destination):
        return False
    source_data = _read(source)
    if is_email and is_text(source_data):
        expected = transform_contributor_email_text(source_data.decode("utf-8", "replace"), rules).encode("utf-8")
        return expected == _read(destination)
    return source_data == _read(destination)


def preserve_fork_files(
    fork_root: str,
    branded_root: str,
    upstream_root: str,
    rules: BrandingRules | None = None,
    *,
    obsolete_upstream_paths: set[str] | None = None,
    owned_paths: set[str] | None = None,
    allow_unclassified_fork_files: bool = True,
) -> list[str]:
    """Copy fork-local files (no upstream source) into the branded tree.

    Fork files whose path no upstream path maps to are fork-only content
    (owned-assets registry, contributor emails, fork-added skills/tests).
    Branding upstream alone would silently drop them from the PR; this
    carries them over verbatim so nothing is lost.
    """
    if not fork_root or not os.path.isdir(fork_root):
        return []
    rules = rules or BrandingRules()
    obsolete_upstream_paths = obsolete_upstream_paths or set()
    owned_paths = owned_paths or set()
    upstream = _walk(upstream_root)
    upstream_path_map = collision_safe_path_map(upstream, rules)
    upstream_mapped = set(upstream_path_map.values())
    engine_registry = os.path.isfile(
        os.path.join(branded_root, "config", "owned-assets", "manifest.json")
    )
    preserved: list[str] = []
    for rel in _walk(fork_root):
        if rel in upstream_mapped:
            continue  # upstream provides it
        if _is_relocated_collision_alias(rel, upstream_path_map, fork_root, branded_root, rules):
            continue  # exact contributor bytes already exist at their safe destination
        explicitly_owned = (
            rel in owned_paths
            or rel.startswith("config/owned-assets/")
            or is_immutable_path(rel)
        )
        if rel in obsolete_upstream_paths and not explicitly_owned:
            continue  # direct source evidence proves this is stale upstream code
        if not allow_unclassified_fork_files and not explicitly_owned:
            continue  # without a baseline, stop for ownership review
        dst = os.path.join(branded_root, rel)
        src = os.path.join(fork_root, rel)
        # A 100Ways-owned registry is an explicit, reviewable visual identity
        # overlay.  Preserve the fork's registry only when no engine-owned
        # replacement already exists; otherwise a stale fork asset would
        # overwrite the verified white NasTech asset pack.
        if rel.startswith("config/owned-assets/") and engine_registry:
            continue
        if os.path.abspath(dst) == os.path.abspath(src):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        try:
            os.chmod(dst, os.stat(src).st_mode)
        except OSError:
            pass
        preserved.append(rel)
    return preserved


def fork_consistency(
    fork_root: str,
    branded_root: str,
    upstream_root: str,
    rules: BrandingRules | None = None,
    *,
    obsolete_upstream_paths: set[str] | None = None,
    owned_paths: set[str] | None = None,
) -> ForkCheckReport:
    """Diff the branded tree against the nastech-agent fork, file by file.

    ``fork_root`` is a checkout of nastech-agent main (the previous branded
    state); ``branded_root`` is the freshly-branded pipeline snapshot;
    ``upstream_root`` is the freshly-pulled hermes source.  Every fork file
    must survive in the branded tree (identical, updated, or preserved);
    anything missing or fork-local-but-unpreserved fails the gate.
    """
    rules = rules or BrandingRules()
    obsolete_upstream_paths = obsolete_upstream_paths or set()
    owned_paths = owned_paths or set()
    report = ForkCheckReport()
    if not fork_root or not os.path.isdir(fork_root):
        return report
    report.features_fork = _feature_docs(fork_root)
    report.features_branded = _feature_docs(branded_root)

    fork_files = set(_walk(fork_root))
    branded_files = set(_walk(branded_root))
    upstream = set(_walk(upstream_root))
    upstream_path_map = collision_safe_path_map(list(upstream), rules)
    upstream_mapped = set(upstream_path_map.values())

    for rel in sorted(fork_files):
        entry = ForkEntry(path=rel, status="missing")
        if _is_relocated_collision_alias(rel, upstream_path_map, fork_root, branded_root, rules):
            entry.status = "relocated"
            report.entries.append(entry)
            continue
        explicitly_owned = (
            rel in owned_paths
            or rel.startswith("config/owned-assets/")
            or is_immutable_path(rel)
        )
        if rel in obsolete_upstream_paths and not explicitly_owned:
            entry.status = "stale_upstream" if rel in branded_files else "upstream_deleted"
            report.entries.append(entry)
            continue
        if rel not in branded_files:
            # upstream has a file mapping here -> branding dropped it (bug).
            # no upstream source -> fork-local content that was NOT preserved.
            entry.status = "missing" if rel in upstream_mapped else "local_only"
            report.entries.append(entry)
            continue
        fork_data = _read(os.path.join(fork_root, rel))
        branded_data = _read(os.path.join(branded_root, rel))
        if fork_data == branded_data:
            entry.status = "identical"
        elif is_locked_path(rel) or not is_text(fork_data) or not is_text(branded_data):
            entry.status = "locked"
        else:
            entry.status = "updated"
            entry.added_lines, entry.deleted_lines = _line_delta(
                fork_data.decode("utf-8", "replace"),
                branded_data.decode("utf-8", "replace"),
            )
            entry.violations = _added_line_violations(
                fork_data.decode("utf-8", "replace"),
                branded_data.decode("utf-8", "replace"),
                rel, rules,
            )
        report.entries.append(entry)

    for rel in sorted(branded_files - fork_files):
        explicitly_owned = (
            rel in owned_paths
            or rel.startswith("config/owned-assets/")
            or is_immutable_path(rel)
        )
        if rel in obsolete_upstream_paths and not explicitly_owned:
            report.entries.append(ForkEntry(path=rel, status="stale_upstream"))
            continue
        if rel in upstream_mapped:
            # brand-new upstream file: every line must be brand-clean
            data = _read(os.path.join(branded_root, rel))
            entry = ForkEntry(path=rel, status="added")
            if is_text(data) and not is_locked_path(rel) and not is_immutable_path(rel):
                text = data.decode("utf-8", "replace")
                for line_no in _brand_violations(text, rules):
                    entry.violations.append(
                        ForkViolation(path=rel, line=line_no,
                                      snippet=text.splitlines()[line_no - 1][:200])
                    )
            report.entries.append(entry)

    # Fork-local files (no upstream source) that exist in the branded tree:
    # these are the ones ``preserve_fork_files`` carried over verbatim.
    report.preserved = sorted(f for f in fork_files & branded_files
                              if f not in upstream_mapped)
    return report


def _line_delta(a: str, b: str) -> tuple[int, int]:
    sm = difflib.SequenceMatcher(None, a.splitlines(), b.splitlines())
    added = sum((op[2] - op[1]) for op in sm.get_opcodes() if op[0] == "insert")
    deleted = sum((op[4] - op[3]) for op in sm.get_opcodes() if op[0] == "delete")
    return added, deleted
