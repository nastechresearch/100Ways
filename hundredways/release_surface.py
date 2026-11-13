"""Fail-closed reconciliation of confirmed release-test and legacy-claim drift.

Each edit is pinned to a reviewed complete input and output, then constrained
to one literal expression inside its real containing function. Unknown revisions,
duplicate anchors, and non-Python files are left unchanged and reported.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

# Complete f4082ebb inputs and independently reviewed repaired outputs. These
# constants are never learned from the running tree or from matching anchors.
_REVIEWED_FILES = {
    "tests/scripts/test_release_darwin.py": (
        "ea5ade0db4fa0703de935d28b7c4b55467f44296fb2764b7e797f6add27728d6",
        "c5a90d1705e3354c0355f84aed0a61c283889a04e7c8658aeb681a9c8ca9e8e6",
    ),
    "tests/scripts/test_stable_release.py": (
        "212f9406c246997d96badc06f5fc97ded88f69a5ee4693fec8be49330ff8b7c3",
        "8f9da2a4e18231e4f20a5e00decc178305145fded85f0ea7df72ca4f7dd09f68",
    ),
    "tests/scripts/test_release_lanes.py": (
        "1569f8a5dd8bfef83bf61f434b178db4e137225a3725d304e508082ca3f37162",
        "bd638df121f321a1aba4a69390119682492d804125dcfc2dab14b42130863bc9",
    ),
    "tests/scripts/test_release_entrypoint.py": (
        "4ad9dbc5d09e951c793c2cb3dedf10db668da2cdc6fa5dbd515b5415aa742582",
        "40dd8852fd820c9ec65fccc23e0abee7ab4e3c1a47d69cafee154dd2c86c364d",
    ),
    "tests/ci/test_unsigned_desktop_legs.py": (
        "aeaea8dbddea3da1eb74fced536ebed687676d2ab79c95c9cfd7bdcddb392093",
        "0f6c29c3af671ac8d7f4c7cbab83de8c9b1a3a12df69328d074e2caa9eaa0805",
    ),
}

_REPAIRS = (
    (
        "tests/scripts/test_release_darwin.py",
        "test_finalize_publishes_the_feed_pointer_without_any_r2_configuration",
        'assert len(feed["files"]) == 2',
        'assert len(feed["files"]) == len(darwin.ARCHES)',
    ),
    (
        "tests/scripts/test_stable_release.py",
        "test_transitions_bind_all_arches_identity_version_and_archive",
        'with pytest.raises(ValueError, match="path encoding"):',
        'with pytest.raises(ValueError, match="outside its immutable tag archive: windows/arm64"):',
    ),
    (
        "tests/scripts/test_stable_release.py",
        "test_complete_writes_no_final_tag_and_leaves_the_draft_on_the_attempt_ref",
        'monkeypatch.setattr(stable, "read_candidate", lambda env: candidate)',
        (
            'monkeypatch.setattr(stable, "read_candidate", lam'
            "bda env, *, tag, commit, base, archive: candidate)"
        ),
    ),
    (
        "tests/scripts/test_release_lanes.py",
        "test_a_pre_lanes_claim_reads_only_while_it_left_macos_intel_out",
        "== lane_set.DEFAULT",
        '== frozenset(lane_set.NAMES) - {"termux"}',
    ),
    (
        "tests/scripts/test_release_entrypoint.py",
        "test_an_oversized_body_is_refused_before_the_claim",
        "def test_an_oversized_body_is_refused_before_the_claim(source, tmp_path, bump):",
        (
            "def test_an_oversized_body_is_refused_before_t"
            "he_claim(source, tmp_path, bump, monkeypatch):"
        ),
    ),
    (
        "tests/scripts/test_release_entrypoint.py",
        "test_an_oversized_body_is_refused_before_the_claim",
        "    commit = _oversized_commit(source, tmp_path)\n",
        "    commit = _oversized_commit(source, tmp_path)\n"
        "    # The changelog renderer now caps subjects. Inject an oversized rendered\n"
        "    # body to exercise the independent pre-claim refusal, not that cap.\n"
        "    from scripts.releases import entrypoint\n"
        '    monkeypatch.setattr(entrypoint, "_changelog", l'
        'ambda *a, **kw: "x" * entrypoint.GITHUB_BODY_LIMIT)\n',
    ),
)


def _function_span(source: str, name: str) -> tuple[int, int] | None:
    """Return the character span of exactly one module-level function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]
    if len(matches) != 1:
        return None
    node = matches[0]
    lines = source.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets[node.lineno - 1], offsets[node.end_lineno]


def _replace_in_function(source: str, function: str, old: str, new: str) -> str | None:
    """Make one literal replacement inside its named function, or decline."""
    span = _function_span(source, function)
    if span is None:
        return None
    start, end = span
    body = source[start:end]
    # A function may already use the desired expression elsewhere.  The obsolete
    # literal is authoritative; only its exact single occurrence is rewritten.
    if old in new and new in body:
        return source
    if body.count(old) == 0:
        return source if new in body else None
    if body.count(old) != 1:
        return None
    rewritten = source[:start] + body.replace(old, new, 1) + source[end:]
    try:
        ast.parse(rewritten)
    except SyntaxError:
        return None
    return rewritten


def reconcile_release_surface(dst: str, *, unresolved: list[str] | None = None) -> list[str]:
    """Apply complete reviewed migrations; unknown inputs are never rewritten.

    A file is written only after every repair and its final digest are verified.
    This avoids partial migrations when a later anchor does not match. Already
    reviewed outputs are a fixed point. Callers can collect unresolved paths.
    """
    root = Path(dst)
    changed: list[str] = []

    def conflict(rel: str) -> None:
        if unresolved is not None and rel not in unresolved:
            unresolved.append(rel)

    for rel, (before_digest, after_digest) in _REVIEWED_FILES.items():
        path = root / rel
        if path.is_symlink():
            conflict(rel)
            continue
        if not path.is_file():
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest == after_digest:
            continue
        if digest != before_digest:
            conflict(rel)
            continue
        source = data.decode("utf-8")
        rewritten = source
        for repair_rel, function, old, new in _REPAIRS:
            if repair_rel != rel:
                continue
            candidate = _replace_in_function(rewritten, function, old, new)
            if candidate is None:
                break
            rewritten = candidate
        else:
            # Unsigned receipts retain the canonical ARM-only target set. This
            # is not a signing skip or a relaxation of receipt validation.
            if rel == "tests/ci/test_unsigned_desktop_legs.py":
                old = 'STABLE_RECEIPTS = ("darwin-arm64", "darwin-x64", "win32-bundle")'
                new = 'STABLE_RECEIPTS = ("darwin-arm64", "win32-bundle")'
                if rewritten.count(old) == 1:
                    rewritten = rewritten.replace(old, new, 1)
            output = rewritten.encode("utf-8")
            if hashlib.sha256(output).hexdigest() == after_digest:
                path.write_bytes(output)
                changed.append(rel)
                continue
        conflict(rel)
    return sorted(changed)
