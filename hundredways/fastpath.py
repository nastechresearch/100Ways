"""Optional Rust acceleration for deterministic filesystem hot paths.

The branding rules and all policy remain in Python.  Rust is used only for
mechanical traversal and hashing; if the binary is unavailable, every caller
falls back to the original Python implementation with identical semantics.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def _binary() -> str | None:
    configured = os.environ.get("HUNDREDWAYS_BRANDFAST", "").strip()
    candidates = [
        configured,
        str(Path(__file__).resolve().parent.parent / "rust" / "brandfast" / "target" / "release" / "brandfast"),
        str(Path(__file__).resolve().parent.parent / "rust" / "brandfast" / "target" / "debug" / "brandfast"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run(*args: str) -> list[str] | None:
    binary = _binary()
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    return result.stdout.splitlines()


def list_files(root: str, mode: str) -> list[str] | None:
    """Return sorted relative regular files, or ``None`` for Python fallback."""
    output = _run("list", root, mode)
    if output is None:
        return None
    return sorted(path for path in output if path)


def tree_hash_rows(root: str) -> list[tuple[str, str, str]] | None:
    """Return ``(relative_path, mode, sha256)`` rows, or ``None`` on fallback."""
    output = _run("tree-hash", root)
    if output is None:
        return None
    rows: list[tuple[str, str, str]] = []
    try:
        for line in output:
            relative, mode, digest = line.split("\t", 2)
            rows.append((relative, mode, digest))
    except ValueError:
        return None
    return rows
