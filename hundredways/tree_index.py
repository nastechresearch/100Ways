"""One read-only snapshot of a tree, serving many audit views.

Every brand, security, and conformance audit used to hand itself its own
``Path(root).rglob("*")`` loop, so a single weekly gate re-walked and re-read
the same ~4000-file branded tree five times before reading any byte of value
from the earlier passes.  :class:`FileIndex` walks the tree once, keeps the
entry list in deterministic order, and serves filtered path views plus UTF-8
text through a bounded cache.

This is a SNAPSHOT, not a live view.  Paths, sizes and modes are fixed while
building the index, and text is read on first use and then replayed from the
cache, so a later write, rename, or delete is either invisible or stale.  Build
one only after every stage that mutates the tree has finished, and never carry
an index across a mutation -- an audit reading through a stale index judges bytes
that are no longer on disk, which is a correctness bug, not a slower result.

The pruned set is exactly what the loops this replaced skip: ``.git`` and
``node_modules``.  Adding ``__pycache__``, ``dist``, or ``build`` here would
silently drop path findings those loops still report, so it stays two names.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .integrity import sha256_file

PRUNED_DIRS = frozenset({".git", "node_modules"})

# The byte ceiling is the real bound: a branded tree is ~4000 files and a few
# megabytes, so one pass can hold the whole working set and the later passes read
# from RAM.  Caching without a ceiling would let a single 2 MB fixture, or the
# tree itself, stay resident as Python strings for the rest of the run; on an
# eviction the caller simply reads the file again, which is what every audit did
# before an index existed.
_CACHE_MAX_ENTRIES = 65536
_CACHE_MAX_CHARS = 128 * 1024 * 1024


@dataclass(frozen=True)
class FileEntry:
    """One readable file as the single walk saw it."""

    rel: str
    path: Path
    name: str
    suffix: str
    size: int
    mode: int
    link: bool
    pruned: bool


class FileIndex:
    """A read-only, per-run index of one tree."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cache: OrderedDict[str, str | None] = OrderedDict()
        self._cached_chars = 0
        root_pruned = any(part in PRUNED_DIRS for part in self.root.parts)
        entries: list[FileEntry] = []
        self._walk(self.root, "", root_pruned, entries)
        entries.sort(key=lambda entry: entry.rel)
        self._entries = entries
        self._by_rel = {entry.rel: entry for entry in entries}

    @property
    def entries(self) -> tuple[FileEntry, ...]:
        """Every file found, including inside pruned directories, in path order."""
        return tuple(self._entries)

    def files(self, *, matching: str = "") -> list[FileEntry]:
        """Return the auditable files in path order, optionally one basename only."""
        if matching:
            return [entry for entry in self._entries if not entry.pruned and entry.name == matching]
        return [entry for entry in self._entries if not entry.pruned]

    def entry(self, rel: str) -> FileEntry | None:
        """Return one file by its index-relative path, or None when absent."""
        return self._by_rel.get(rel)

    def text(self, rel: str) -> str | None:
        """Return a file decoded as UTF-8, or None where a caller would skip it.

        ``None`` covers the absent path, the unreadable file, and the binary
        file -- the same ``(OSError, UnicodeDecodeError)`` pair every wired loop
        already swallowed with ``continue``.
        """
        if rel in self._cache:
            self._cache.move_to_end(rel)
            return self._cache[rel]
        entry = self._by_rel.get(rel)
        if entry is None:
            return None
        try:
            value: str | None = entry.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            value = None
        self._remember(rel, value)
        return value

    def digest(self) -> str:
        """Return the hex :func:`hundredways.integrity.tree_digest` returns here.

        Same lexical path order, same symlink exclusion, same path/mode/bytes
        framing, so a receipt digest computed through the index still binds the
        tree it was gated on.
        """
        digest = hashlib.sha256()
        for entry in self._entries:
            if entry.link:
                continue
            digest.update(f"{entry.rel}\0{entry.mode:o}\0{sha256_file(entry.path)}\n".encode("utf-8"))
        return digest.hexdigest()

    def _remember(self, rel: str, value: str | None) -> None:
        self._cache[rel] = value
        self._cached_chars += len(value or "")
        while len(self._cache) > _CACHE_MAX_ENTRIES or self._cached_chars > _CACHE_MAX_CHARS:
            _, evicted = self._cache.popitem(last=False)
            self._cached_chars -= len(evicted or "")

    def _walk(
        self,
        directory: Path,
        prefix: str,
        pruned: bool,
        entries: list[FileEntry],
    ) -> None:
        """Collect readable files below ``directory``, mirroring ``rglob("*")``.

        Symlinked directories are listed but never descended into, and an
        unreadable directory yields nothing, which is how ``Path.rglob`` behaves.
        """
        try:
            scanned = os.scandir(directory)
        except OSError:
            return
        with scanned:
            for child in scanned:
                try:
                    is_directory = child.is_dir()
                    is_regular = child.is_file() if not is_directory else False
                except OSError:
                    continue
                if is_directory:
                    if child.is_symlink():
                        continue
                    self._walk(
                        Path(child.path),
                        f"{prefix}{child.name}/",
                        pruned or child.name in PRUNED_DIRS,
                        entries,
                    )
                    continue
                if not is_regular:
                    continue
                try:
                    info = child.stat()
                except OSError:
                    continue
                rel = f"{prefix}{child.name}"
                path = Path(child.path)
                entries.append(
                    FileEntry(
                        rel=rel,
                        path=path,
                        name=child.name,
                        suffix=path.suffix,
                        size=info.st_size,
                        mode=stat.S_IMODE(info.st_mode),
                        link=child.is_symlink(),
                        pruned=pruned or child.name in PRUNED_DIRS,
                    )
                )
