"""Deterministic integrity checks for 100Ways source trees and candidate archives."""

from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


CANONICAL_UPSTREAM_HOST = "github.com"
CANONICAL_UPSTREAM_PATH = "/NousResearch/hermes-agent.git"
ARCHIVE_ROOT = "nastech-agent"
_ALLOWED_ARCHIVE_ROOT_FILES = {
    "GATE-REPORT.md",
    "SYNC-SUMMARY.md",
    "UPDATE-REPORT.md",
}


@dataclass(frozen=True)
class IntegrityIssue:
    """A deterministic integrity finding suitable for an authorization gate."""

    code: str
    path: str
    detail: str


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for a regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(root: str | Path) -> str:
    """Hash paths, modes, and bytes in deterministic lexical order."""
    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(base).as_posix()
        mode = stat.S_IMODE(path.stat().st_mode)
        digest.update(f"{relative}\0{mode:o}\0{sha256_file(path)}\n".encode("utf-8"))
    return digest.hexdigest()


def _valid_relative_path(relative: PurePosixPath) -> bool:
    return not relative.is_absolute() and ".." not in relative.parts and "" not in relative.parts


def _case_collision_groups(names: list[str]) -> set[frozenset[str]]:
    grouped: dict[str, set[str]] = {}
    for name in names:
        grouped.setdefault(name.casefold(), set()).add(name)
    return {frozenset(group) for group in grouped.values() if len(group) > 1}


def audit_candidate_tree(
    root: str | Path,
    *,
    allowed_case_collision_groups: set[frozenset[str]] | None = None,
) -> list[IntegrityIssue]:
    """Reject unsafe entries, allowing only explicitly evidenced collision groups."""
    base = Path(root)
    if not base.is_dir():
        return [IntegrityIssue("candidate-root", str(base), "candidate root is unavailable")]

    issues: list[IntegrityIssue] = []
    allowed = allowed_case_collision_groups or set()
    paths = sorted(base.rglob("*"), key=lambda item: item.as_posix())
    relative_paths = [path.relative_to(base).as_posix() for path in paths]
    for group in sorted(_case_collision_groups(relative_paths), key=lambda item: sorted(item)):
        if group in allowed:
            continue
        ordered = sorted(group)
        prior = ordered[0]
        for relative in ordered[1:]:
            issues.append(
                IntegrityIssue(
                    "case-collision",
                    relative,
                    f"collides with {prior!r} on case-insensitive filesystems",
                )
            )
    for path in paths:
        relative = path.relative_to(base).as_posix()
        parts = PurePosixPath(relative).parts
        if ".git" in parts:
            issues.append(IntegrityIssue("candidate-git-metadata", relative, "Git metadata is forbidden"))
            continue
        if any(any(ord(char) < 32 for char in part) for part in parts):
            issues.append(IntegrityIssue("control-path", relative, "path contains a control character"))
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            issues.append(IntegrityIssue("unreadable-entry", relative, str(exc)))
            continue
        if stat.S_ISLNK(mode):
            issues.append(IntegrityIssue("symlink", relative, "candidate must not contain symlinks"))
            continue
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            issues.append(IntegrityIssue("special-file", relative, "candidate must contain regular files only"))
            continue
        if mode & (stat.S_ISUID | stat.S_ISGID):
            issues.append(IntegrityIssue("privileged-mode", relative, "setuid and setgid bits are forbidden"))
        if mode & stat.S_IWOTH:
            issues.append(IntegrityIssue("world-writable", relative, "world-writable files are forbidden"))
    return issues


def _parse_manifest(path: Path) -> tuple[dict[str, Any], list[IntegrityIssue]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [IntegrityIssue("manifest", path.name, f"cannot read manifest: {exc}")]
    if not isinstance(value, dict):
        return {}, [IntegrityIssue("manifest", path.name, "manifest must be a JSON object")]
    return value, []


def audit_manifest_provenance(
    manifest_path: str | Path,
    *,
    expected_upstream_sha: str,
) -> list[IntegrityIssue]:
    """Verify direct-source provenance and its binding to the fetched SHA."""
    path = Path(manifest_path)
    manifest, issues = _parse_manifest(path)
    if issues:
        return issues

    upstream_sha = manifest.get("upstream_sha")
    if not isinstance(upstream_sha, str) or len(upstream_sha) != 40:
        issues.append(IntegrityIssue("manifest-upstream-sha", path.name, "upstream SHA must be 40 hex characters"))
    elif upstream_sha != expected_upstream_sha:
        issues.append(IntegrityIssue("source-sha", path.name, "manifest SHA differs from freshly fetched upstream HEAD"))

    provenance = manifest.get("source_provenance")
    if not isinstance(provenance, dict):
        return issues + [IntegrityIssue("source-provenance", path.name, "direct-source provenance is missing")]
    if provenance.get("acquisition") != "fresh-direct-clone":
        issues.append(IntegrityIssue("source-acquisition", path.name, "source must use a fresh direct clone"))

    remote_url = provenance.get("remote_url")
    if not isinstance(remote_url, str):
        issues.append(IntegrityIssue("source-remote", path.name, "source remote URL is missing"))
    else:
        parsed = urlparse(remote_url)
        if parsed.scheme != "https" or parsed.netloc != CANONICAL_UPSTREAM_HOST or parsed.path != CANONICAL_UPSTREAM_PATH:
            issues.append(IntegrityIssue("source-remote", path.name, "source remote is not the canonical Hermes HTTPS URL"))

    fetched_at = provenance.get("fetched_at")
    try:
        parsed_time = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError:
        issues.append(IntegrityIssue("source-fetch-time", path.name, "fetched_at must be an ISO-8601 timestamp with timezone"))
    return issues


def audit_candidate_archive(
    path: str | Path,
    *,
    allowed_case_collision_groups: set[frozenset[str]] | None = None,
) -> list[IntegrityIssue]:
    """Validate the exact archive later consumed by #344 before authorization."""
    archive_path = Path(path)
    if not archive_path.is_file():
        return [IntegrityIssue("candidate-archive", archive_path.name, "archive is unavailable")]
    issues: list[IntegrityIssue] = []
    allowed = allowed_case_collision_groups or set()
    archive_names: list[str] = []
    expected_manifest = f"{ARCHIVE_ROOT}/manifest.json"
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if not infos:
                return [IntegrityIssue("candidate-archive", archive_path.name, "archive is empty")]
            for info in infos:
                name = info.filename.rstrip("/")
                if not name:
                    continue
                candidate = PurePosixPath(name)
                if not _valid_relative_path(candidate):
                    issues.append(IntegrityIssue("archive-path", name, "archive path is unsafe"))
                    continue
                archive_names.append(name)
                mode = (info.external_attr >> 16) & 0o177777
                if stat.S_IFMT(mode) == stat.S_IFLNK:
                    issues.append(IntegrityIssue("archive-symlink", name, "archives may not contain symlinks"))
                if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_IWOTH):
                    issues.append(IntegrityIssue("archive-mode", name, "archive contains an unsafe file mode"))
                top = candidate.parts[0]
                if top != ARCHIVE_ROOT and name not in _ALLOWED_ARCHIVE_ROOT_FILES:
                    issues.append(IntegrityIssue("archive-root", name, "unexpected archive root entry"))
                if name.startswith(f"{ARCHIVE_ROOT}/.git/") or name == f"{ARCHIVE_ROOT}/.git":
                    issues.append(IntegrityIssue("archive-git-metadata", name, "archive contains Git metadata"))
                if info.file_size and info.compress_size and info.file_size / info.compress_size > 250:
                    issues.append(IntegrityIssue("archive-compression-ratio", name, "compression ratio exceeds safety limit"))
    except (OSError, zipfile.BadZipFile) as exc:
        return [IntegrityIssue("candidate-archive", archive_path.name, f"invalid archive: {exc}")]
    for group in sorted(_case_collision_groups(archive_names), key=lambda item: sorted(item)):
        if group in allowed:
            continue
        ordered = sorted(group)
        prior = ordered[0]
        for name in ordered[1:]:
            issues.append(IntegrityIssue("archive-case-collision", name, f"collides with {prior!r}"))
    if expected_manifest not in archive_names:
        issues.append(IntegrityIssue("archive-manifest", expected_manifest, "candidate archive lacks its manifest"))
    return issues
