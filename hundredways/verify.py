"""File-to-file parity verification between upstream and the Nastech fork.

Two modes:

  * ``verify_rebrand`` - prove the branding rules fully explain a historical
    rebrand commit (e.g. parent tree ``03fa32c92`` -> birth commit
    ``0cafd22fb``).  This is the ground-truth validation of the rules
    themselves: if the transform reproduces the birth commit's tree to
    >=99%, the engine is trustworthy for future ports.

  * ``verify_port`` - prove a single ported commit matches its upstream
    source after branding (the per-commit parity gate).
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field

from .rules import BrandingRules, is_contributor_name_path, is_locked_path


@dataclass
class FileResult:
    path: str            # upstream path
    mapped_path: str     # branded path expected in our tree
    pass_: bool
    locked: bool
    note: str = ""
    added_lines: int = 0
    deleted_lines: int = 0
    added_chars: int = 0
    deleted_chars: int = 0


@dataclass
class VerifyReport:
    results: list[FileResult] = field(default_factory=list)
    total: int = 0
    passed: int = 0
    locked: int = 0
    failed: list[FileResult] = field(default_factory=list)

    @property
    def pass_ratio(self) -> float:
        return self.passed / self.total if self.total else 1.0

    def summary(self) -> str:
        return (
            f"{self.passed}/{self.total} files pass "
            f"({self.pass_ratio * 100:.1f}%), {self.locked} locked-for-review, "
            f"{len(self.failed)} failed"
        )


def _git(repo: str, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=True
    )
    return out.stdout


def _git_ok(repo: str, *args: str) -> str:
    """Run git, raising a readable error on failure."""
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _tree_files(repo: str, commit: str) -> list[str]:
    out = _git(repo, "ls-tree", "-r", "--name-only", commit)
    return [p for p in out.splitlines() if p]


def _blob(repo: str, commit: str, path: str) -> bytes:
    try:
        raw = subprocess.run(
            ["git", "-C", repo, "show", f"{commit}:{path}"],
            capture_output=True,
        )
        return raw.stdout
    except Exception:
        return b""


class BlobReader:
    """Batch blob reads through one `git cat-file --batch` subprocess.

    Reading N blobs with ``git show`` spawns N subprocesses; a batch reader
    is a single long-lived process.  For a 5000-file tree that is the
    difference between ~seconds and ~minutes.
    """

    def __init__(self, repo: str):
        self.proc = subprocess.Popen(
            ["git", "-C", repo, "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )

    def read(self, commit: str, path: str) -> bytes:
        if self.proc.stdin is None or self.proc.stdout is None:
            return b""
        self.proc.stdin.write(f"{commit}:{path}\n".encode())
        self.proc.stdin.flush()
        header = self.proc.stdout.readline().decode()
        parts = header.split()
        if not parts or parts[0] == "missing" or len(parts) < 3:
            return b""
        size = int(parts[2])
        data = self.proc.stdout.read(size)
        self.proc.stdout.read(1)  # trailing newline
        return data

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.wait(timeout=10)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _numstat(repo: str, a: str, b: str, path: str) -> tuple[int, int]:
    out = _git(repo, "diff", "--numstat", a, b, "--", path).strip()
    if not out:
        return 0, 0
    added, deleted, _ = out.split("\t", 2)
    return int(added), int(deleted)


def _char_delta(a: bytes, b: bytes) -> tuple[int, int]:
    sm = difflib.SequenceMatcher(None, a.decode("utf-8", "replace"), b.decode("utf-8", "replace"))
    added = sum((op[2] - op[1]) for op in sm.get_opcodes() if op[0] == "insert")
    deleted = sum((op[4] - op[3]) for op in sm.get_opcodes() if op[0] == "delete")
    return added, deleted


def verify_rebrand(
    repo: str,
    base_commit: str,
    rebrand_commit: str,
    rules: BrandingRules | None = None,
    expected: list[str] | None = None,
    compute_deltas: bool = False,
) -> VerifyReport:
    """Prove ``rules`` transform ``base_commit``'s tree into ``rebrand``'s.

    ``expected`` optionally restricts the check to a set of upstream paths
    (used for per-commit port verification where we only care about the
    files a commit touched).

    ``compute_deltas`` toggles the expensive per-failed-file line/char diff.
    The gate itself never needs it (pass/fail is exact byte equality); leave
    it off for full-tree runs over thousands of files.
    """
    rules = rules or BrandingRules()
    report = VerifyReport()

    paths = expected if expected is not None else _tree_files(repo, base_commit)
    rebrand_files = set(_tree_files(repo, rebrand_commit))
    reader = BlobReader(repo)

    for path in paths:
        report.total += 1
        mapped = path if is_contributor_name_path(path) else rules.transform_path(path)
        locked = (
            is_contributor_name_path(path)
            or is_locked_path(path)
            or is_locked_path(mapped)
        )

        base_blob = reader.read(base_commit, path)
        if mapped in rebrand_files:
            rebrand_blob = reader.read(rebrand_commit, mapped)
        else:
            rebrand_blob = b""

        if locked or _looks_binary(base_blob) or _looks_binary(rebrand_blob):
            # binary/locked: require the file to exist at the mapped path
            res = FileResult(
                path=path, mapped_path=mapped,
                pass_=(mapped in rebrand_files),
                locked=True,
                note="binary/locked: existence checked, content not compared",
            )
            report.results.append(res)
            report.locked += 1
            if res.pass_:
                report.passed += 1
            else:
                report.failed.append(res)
            continue

        transformed = rules.transform_text(base_blob.decode("utf-8", "replace")).encode("utf-8")
        identical = transformed == rebrand_blob
        res = FileResult(path=path, mapped_path=mapped, pass_=identical, locked=False)
        if identical:
            report.passed += 1
        else:
            if compute_deltas:
                res.added_lines, res.deleted_lines = _numstat(repo, base_commit, rebrand_commit, mapped)
                res.added_chars, res.deleted_chars = _char_delta(transformed, rebrand_blob)
            res.note = "content differs after branding"
            report.failed.append(res)
        report.results.append(res)

    reader.close()
    return report


def verify_port(
    repo: str,
    upstream_commit: str,
    port_commit: str,
    rules: BrandingRules | None = None,
) -> VerifyReport:
    """Verify one ported commit: files it changed must match after branding."""
    rules = rules or BrandingRules()
    changed = _git(repo, "show", "--name-only", "--format=", upstream_commit).splitlines()
    changed = [c for c in changed if c]
    return verify_rebrand(repo, upstream_commit, port_commit, rules, expected=changed)


def gate_passes(report: VerifyReport, threshold: float = 0.99) -> bool:
    """True when parity >= threshold with no hard failures."""
    if report.failed:
        return False
    return report.pass_ratio >= threshold
