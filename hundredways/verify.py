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

from .rules import BrandingRules, is_locked_path

# A stalled plumbing process would otherwise hold a runner for the whole job
# timeout, so every batched call declares its own deadline.
CAT_FILE_TIMEOUT_SECONDS = 120
NUMSTAT_TIMEOUT_SECONDS = 300

# ``verify_rebrand`` asks for two blobs per path, so this bounds one batch's
# peak memory to ~512 blobs while still reading a whole tree in a handful of
# subprocesses.
_PATHS_PER_BATCH = 256


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
    """Single-blob read kept because the ways catalog names it; batching callers
    use :func:`read_blobs`, which never spawns one process per blob."""
    try:
        raw = subprocess.run(
            ["git", "-C", repo, "show", f"{commit}:{path}"],
            capture_output=True,
        )
        return raw.stdout
    except Exception:
        return b""


class BlobError(RuntimeError):
    """Git could not answer a blob request.

    Not derived from ``port.SyncError`` on purpose: a blob that a diff promised
    must abort the run rather than be recorded as one failed commit, which is
    what the old ``subprocess.check_output`` did by raising.
    """


def read_blobs(
    repo: str,
    requests: list[tuple[str, str]],
    timeout: float = CAT_FILE_TIMEOUT_SECONDS,
) -> list[bytes | None]:
    """Answer every ``rev:path`` request from ONE ``git cat-file --batch``.

    ``None`` marks a request git reported missing, leaving each caller its own
    old failure mode: ``verify`` treats a missing blob as empty bytes, ``port``
    aborts.  One spawn for a whole batch is the difference between seconds and
    minutes on a multi-thousand-file tree.
    """
    if not requests:
        return []
    payload = "".join(f"{rev}:{path}\n" for rev, path in requests).encode("utf-8")
    proc = subprocess.run(
        ["git", "-C", repo, "cat-file", "--batch"],
        input=payload,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise BlobError(
            f"git cat-file --batch failed: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )
    return _parse_batch(proc.stdout, len(requests))


def _parse_batch(stream: bytes, count: int) -> list[bytes | None]:
    """Split cat-file's ``header / body`` records back out in request order."""
    records: list[bytes | None] = []
    position = 0
    for _ in range(count):
        newline = stream.find(b"\n", position)
        if newline < 0:
            raise BlobError("git cat-file --batch ended mid-record")
        header = stream[position:newline].split()
        position = newline + 1
        if header and header[-1] == b"missing":
            records.append(None)
            continue
        if len(header) != 3:
            raise BlobError(f"unexpected git cat-file --batch header: {header!r}")
        size = int(header[2])
        records.append(stream[position:position + size])
        position += size + 1  # past the body and its terminating newline
    return records


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _numstat(repo: str, a: str, b: str, path: str) -> tuple[int, int]:
    """Line delta of one pathspec, one subprocess. Only :func:`numstat_for`'s
    glob-magic fallback still needs it; tree-wide callers use :func:`tree_numstat`."""
    out = _git(repo, "diff", "--numstat", a, b, "--", path).strip()
    if not out:
        return 0, 0
    added, deleted, _ = out.split("\t", 2)
    return int(added), int(deleted)


# git's default pathspec matching treats these as wildcards, so a path holding
# one never mapped to exactly itself in the old per-path call.
_PATHSPEC_MAGIC = frozenset("*?[\\")


def tree_numstat(
    repo: str, a: str, b: str, timeout: float = NUMSTAT_TIMEOUT_SECONDS
) -> dict[str, tuple[str, str]]:
    """Whole-range per-path line deltas from ONE ``git diff --numstat -z``.

    ``--no-renames`` is what makes this equal the per-path calls it replaces: a
    pathspec matching only one side of a rename pair is reported by git as a bare
    add (or delete) of that side, and a rename-free tree diff reports each side
    exactly that way.  ``-z`` keeps paths unquoted, so names with spaces or
    non-ASCII bytes key the map just like the pathspec received them.  Counts
    stay text because git prints ``-`` for binary files and the ``int()`` has to
    happen where the old per-path call raised.
    """
    proc = subprocess.run(
        ["git", "-C", repo, "diff", "--numstat", "--no-renames", "-z", a, b],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git diff --numstat failed: {proc.stderr.strip()}")
    stats: dict[str, tuple[str, str]] = {}
    for record in proc.stdout.split("\0"):
        if not record:
            continue
        added, deleted, path = record.split("\t", 2)
        stats[path] = (added, deleted)
    return stats


def numstat_for(
    stats: dict[str, tuple[str, str]], repo: str, a: str, b: str, path: str
) -> tuple[int, int]:
    """One path's line delta from a tree-wide map; unchanged paths are ``(0, 0)``."""
    if path.startswith(":") or _PATHSPEC_MAGIC.intersection(path):
        # A glob pathspec also matched *other* files, whose numbers the old call
        # returned; an exact tree map cannot reproduce that, so these stay per-path.
        return _numstat(repo, a, b, path)
    added, deleted = stats.get(path, ("0", "0"))
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
    it off for full-tree runs over thousands of files.  The line counts come
    from one tree-wide numstat, the character counts stay per file in process.
    """
    rules = rules or BrandingRules()
    report = VerifyReport()

    paths = expected if expected is not None else _tree_files(repo, base_commit)
    rebrand_files = set(_tree_files(repo, rebrand_commit))
    stats: dict[str, tuple[str, str]] | None = None

    # Paths are checked in batches so only one batch of blob bytes is resident
    # at a time, exactly as the streaming reader this replaced was.
    for start in range(0, len(paths), _PATHS_PER_BATCH):
        plan = [
            (path, rules.transform_path(path))
            for path in paths[start:start + _PATHS_PER_BATCH]
        ]
        blobs = read_blobs(repo, [
            request
            for path, mapped in plan
            for request in ((base_commit, path), (rebrand_commit, mapped))
        ])

        for slot, (path, mapped) in enumerate(plan):
            report.total += 1
            locked = is_locked_path(path) or is_locked_path(mapped)

            base_blob = blobs[2 * slot] or b""
            rebrand_blob = (blobs[2 * slot + 1] or b"") if mapped in rebrand_files else b""

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
                    if stats is None:
                        stats = tree_numstat(repo, base_commit, rebrand_commit)
                    res.added_lines, res.deleted_lines = numstat_for(
                        stats, repo, base_commit, rebrand_commit, mapped
                    )
                    res.added_chars, res.deleted_chars = _char_delta(transformed, rebrand_blob)
                res.note = "content differs after branding"
                report.failed.append(res)
            report.results.append(res)

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
