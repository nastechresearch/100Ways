"""Gap analysis between upstream Hermes and the Nastech fork.

Scans both trees file-by-file and produces a report of:

  * files only in upstream (missing from Nastech)
  * files only in Nastech
  * files present on both sides but differing after branding
  * per-file added/deleted line and character counts
  * brand-rule violations (Hermes tokens left in Nastech text files)
  * binary/image assets by format (so logos & frames are flagged for
    rename-only handling and notifications)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .rules import BrandingRules, is_locked_path
from .scanner import classify_path, is_text
from .verify import _git, _tree_files, BlobReader
from .codes import EXTRA, file_code


@dataclass
class FileEntry:
    path: str
    status: str = ""  # upstream-only | nastech-only | changed | identical
    upstream_type: str = ""
    upstream_has: bool = False
    nastech_has: bool = False
    upstream_similar: float = 0.0
    added_lines: int = 0
    deleted_lines: int = 0
    added_chars: int = 0
    deleted_chars: int = 0
    locked: bool = False
    brand_violations: list[str] = field(default_factory=list)
    code: int = 0
    explanation: str = ""


@dataclass
class GapReport:
    upstream_commit: str
    nastech_commit: str
    entries: list[FileEntry] = field(default_factory=list)

    def upstream_only(self) -> list[FileEntry]:
        return [e for e in self.entries if e.status == "upstream-only"]

    def nastech_only(self) -> list[FileEntry]:
        return [e for e in self.entries if e.status == "nastech-only"]

    def changed(self) -> list[FileEntry]:
        return [e for e in self.entries if e.status == "changed"]

    def violations(self) -> list[FileEntry]:
        return [e for e in self.entries if e.brand_violations]

    def assets(self) -> list[FileEntry]:
        return [e for e in self.entries if e.upstream_type in _IMAGE_FORMATS]

    def code_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for e in self.entries:
            counts[e.code] = counts.get(e.code, 0) + 1
        return counts

    @property
    def summary(self) -> str:
        return (
            f"{len(self.upstream_only())} upstream-only, "
            f"{len(self.changed())} changed, "
            f"{len(self.violations())} brand violations, "
            f"{len(self.assets())} image assets, "
            f"{len(self.nastech_only())} nastech-only"
        )


_IMAGE_FORMATS = {
    "png", "jpeg", "gif", "webp", "bmp", "tiff", "psd", "ico",
    "avif", "heic", "heif",
}


# A set of tokens that must never appear in a Nastech text file.  Compiled
# per-file so we can report the exact offending tokens.
_VIOLATION_TOKENS = (
    "hermes-agent",
    "Hermes Agent",
    "NousResearch",
    "Nous Research",
    "nousresearch",
    "hermes_cli",
    "hermes_constants",
    "HERMES_HOME",
    "get_hermes_home",
    "@hermes/ink",
    "hermes-parser",
    "hermes@nousresearch",
)


def analyze(
    upstream_commit: str,
    nastech_commit: str,
    repo: str,
    rules: BrandingRules | None = None,
    max_diff_bytes: int = 8 * 1024 * 1024,
) -> GapReport:
    """Compare upstream vs Nastech trees after applying brand rules."""
    rules = rules or BrandingRules()
    report = GapReport(upstream_commit=upstream_commit, nastech_commit=nastech_commit)

    up_paths = _tree_files(repo, upstream_commit)
    na_paths = _tree_files(repo, nastech_commit)
    reader = BlobReader(repo)
    up_files = {p: reader.read(upstream_commit, p) for p in up_paths}
    na_files = {p: reader.read(nastech_commit, p) for p in na_paths}
    reader.close()

    # map upstream paths through branding to the expected Nastech path
    all_paths = sorted(set(up_files) | set(na_files))
    na_only = [p for p in na_files if p not in set(rules.transform_path(q) for q in up_files)]
    added_by = _added_by_commit(repo, nastech_commit, na_only) if na_only else {}
    for path in all_paths:
        mapped = rules.transform_path(path)
        up = up_files.get(path)
        na = na_files.get(mapped) if mapped in na_files else na_files.get(path)

        entry = FileEntry(
            path=path,
            upstream_has=path in up_files,
            nastech_has=mapped in na_files,
            upstream_type=classify_path(up or b"", path).fmt if up else "",
            locked=is_locked_path(path) or is_locked_path(mapped),
        )

        if not up:
            entry.status = "nastech-only"
        elif not na:
            entry.status = "upstream-only"
        else:
            transformed = rules.transform_text(up.decode("utf-8", "replace")).encode("utf-8")
            entry.upstream_similar = _similarity(transformed, na)
            if transformed == na:
                entry.status = "identical"
            else:
                entry.status = "changed"
                entry.added_lines, entry.deleted_lines = _numstat(repo, upstream_commit, nastech_commit, mapped)
                entry.added_chars, entry.deleted_chars = _char_delta(transformed, na)

        if entry.status != "identical" and is_text(up or na or b""):
            entry.brand_violations = _find_violations(up or na or b"", path)
            if entry.status == "changed":
                entry.brand_violations = _find_violations(na or b"", path)

        entry.code = _file_code(entry)
        if entry.code == EXTRA:
            entry.explanation = _explain_extra(path, added_by)

        report.entries.append(entry)

    return report


def _blob(repo: str, commit: str, path: str) -> bytes:
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "show", f"{commit}:{path}"],
            capture_output=True,
        )
        return proc.stdout
    except Exception:
        return b""


def _find_violations(data: bytes, path: str) -> list[str]:
    text = data.decode("utf-8", "replace")
    return [t for t in _VIOLATION_TOKENS if t in text and not is_locked_path(path)]


def _file_code(entry: FileEntry) -> int:
    """Error code for a FileEntry: 404 missing, 82 violation, 83 drift, 84 extra."""
    return file_code(
        entry.upstream_has,
        entry.nastech_has,
        identical=None if entry.status == "nastech-only" else entry.status == "identical",
        violations=entry.brand_violations,
    )


def _added_by_commit(repo: str, commit: str, paths: set[str]) -> dict[str, str]:
    """Map paths added in ``commit``'s history to the commit that added them.

    One subprocess covers the whole tree (``git log --diff-filter=A``), so
    explaining every extra file costs a single call, not one per file.
    """
    wanted = set(paths)
    if not wanted:
        return {}
    try:
        out = _git(repo, "log", "--diff-filter=A", "--format=%H", "--name-only", commit)
    except Exception:
        return {}
    result: dict[str, str] = {}
    cur: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line in ("", cur):
            continue
        if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
            cur = line
            continue
        if line in wanted and cur:
            result[line] = cur[:8]
    return result


def _explain_extra(path: str, added_by: dict[str, str]) -> str:
    """Explain why a Nastech-only file exists with no upstream twin."""
    lower = path.lower()
    reason = []
    if "nastech" in lower:
        reason.append("fork-specific naming")
    if added_by.get(path):
        reason.append(f"added in nastech commit {added_by[path]}")
    if any(seg in lower for seg in ("/test", "tests/", "_test", ".test.")):
        reason.append("test file")
    if any(seg in lower for seg in (".github/", "docs/", "website/", "config/")):
        reason.append("project infrastructure")
    if path.endswith((".png", ".jpg", ".jpeg", ".ico", ".icns", ".woff", ".woff2", ".ttf", ".pdf")):
        reason.append("branded asset (replaces an upstream image)")
    if not reason:
        reason.append("no upstream twin; new/modified for the fork")
    return "; ".join(reason)


def _similarity(a: bytes, b: bytes) -> float:
    if not a and not b:
        return 1.0
    sm = __import__("difflib").SequenceMatcher(None, a.decode("utf-8", "replace"), b.decode("utf-8", "replace"))
    return sm.ratio()


def _numstat(repo: str, a: str, b: str, path: str) -> tuple[int, int]:
    out = _git(repo, "diff", "--numstat", a, b, "--", path).strip()
    if not out:
        return 0, 0
    added, deleted, _ = out.split("\t", 2)
    return int(added), int(deleted)


def _char_delta(a: bytes, b: bytes) -> tuple[int, int]:
    sm = __import__("difflib").SequenceMatcher(None, a.decode("utf-8", "replace"), b.decode("utf-8", "replace"))
    added = sum((op[2] - op[1]) for op in sm.get_opcodes() if op[0] == "insert")
    deleted = sum((op[4] - op[3]) for op in sm.get_opcodes() if op[0] == "delete")
    return added, deleted
