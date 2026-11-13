"""Conservative member-aware three-way merges for Python update replays.

This module deliberately accepts only ordinary, independently addressable module
members.  Anything less clear is left for the caller's normal conflict path.
"""

from __future__ import annotations

import ast
import hashlib
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Callable

MemberResolver = Callable[[tuple[str, ...], str | None, str | None, str | None], str | None]


@dataclass(frozen=True)
class _Member:
    """A supported top-level member and its exact source slice."""

    key: tuple[str, ...]
    node: ast.stmt
    text: str
    semantic: str
    is_main: bool = False


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and len(node.test.ops) == 1
        and isinstance(node.test.ops[0], ast.Eq)
        and len(node.test.comparators) == 1
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and isinstance(node.test.comparators[0], ast.Constant)
        and node.test.comparators[0].value == "__main__"
    )


def _assignment_key(node: ast.stmt) -> tuple[str, ...] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return (f"assign:{node.targets[0].id}",)
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (f"assign:{node.target.id}",)
    return None


def _keys(node: ast.stmt, position: int) -> tuple[tuple[str, ...], bool] | None:
    if position == 0 and _is_docstring(node):
        return (("docstring",), False)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ((f"member:{node.name}",), False)
    if isinstance(node, ast.Import):
        keys = tuple(f"import:{alias.asname or alias.name.split('.')[0]}" for alias in node.names)
        return (keys, False) if len(set(keys)) == len(keys) else None
    if isinstance(node, ast.ImportFrom):
        module = "." * node.level + (node.module or "")
        keys = tuple(f"from:{module}:{alias.asname or alias.name}" for alias in node.names)
        return (keys, False) if len(set(keys)) == len(keys) else None
    assignment = _assignment_key(node)
    if assignment is not None:
        return (assignment, False)
    if _is_main_guard(node):
        return (("main",), True)
    return None


def _line_offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _offset(offsets: list[int], lineno: int, col: int) -> int:
    return offsets[lineno - 1] + col


def _leading_start(source: str, previous_end: int, node_start: int) -> int:
    """Attach only comment/blank trivia immediately before a member to it."""
    gap = source[previous_end:node_start]
    if gap and all(not line.strip() or line.lstrip().startswith("#") for line in gap.splitlines()):
        return previous_end
    return node_start


def _parse_members(source: str) -> list[_Member] | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    offsets = _line_offsets(source)
    starts: list[int] = []
    previous_end = 0
    seen: set[str] = set()
    bound_names: set[str] = set()
    keyed: list[tuple[ast.stmt, tuple[str, ...], bool]] = []
    for position, node in enumerate(tree.body):
        result = _keys(node, position)
        if result is None:
            return None
        keys, is_main = result
        if any(key in seen for key in keys):
            return None
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings = {
                alias.asname
                or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                for alias in node.names
            }
            if bindings & bound_names:
                return None
            bound_names.update(bindings)
        seen.update(keys)
        node_start = _offset(offsets, node.lineno, node.col_offset)
        starts.append(_leading_start(source, previous_end, node_start))
        previous_end = _offset(offsets, node.end_lineno, node.end_col_offset)
        # Inline comments belong to this member, not the next one. Moving a
        # noqa or type-ignore comment off its statement changes validation even
        # though the AST is unchanged.
        line_end = offsets[node.end_lineno]
        if source[previous_end:line_end].lstrip().startswith("#"):
            previous_end = line_end
        keyed.append((node, keys, is_main))
    members: list[_Member] = []
    for index, (node, keys, is_main) in enumerate(keyed):
        end = starts[index + 1] if index + 1 < len(starts) else len(source)
        members.append(
            _Member(
                keys,
                node,
                source[starts[index] : end],
                ast.dump(node, annotate_fields=True, include_attributes=False),
                is_main,
            )
        )
    return members


def member_fingerprint(source: str) -> str:
    """Return a stable semantic fingerprint for policy conflict decisions.

    Formatting and source positions are deliberately excluded so policies can
    key a decision on the base/ours/theirs AST triple rather than raw bytes.
    Invalid Python is rejected by :func:`ast.parse` rather than receiving a
    misleading fingerprint.
    """
    semantic = ast.dump(ast.parse(source), include_attributes=False)
    return hashlib.sha256(semantic.encode("utf-8")).hexdigest()


def _member_map(members: list[_Member]) -> dict[str, _Member] | None:
    result: dict[str, _Member] = {}
    for member in members:
        for key in member.key:
            if key in result:
                return None
            result[key] = member
    return result


def _same(left: _Member | None, right: _Member | None) -> bool:
    return left is not None and right is not None and left.semantic == right.semantic


def _git_merge(base: str, ours: str, theirs: str) -> str | None:
    """Merge one member with git's proven textual three-way implementation."""
    with tempfile.TemporaryDirectory(prefix="hundredways-python-replay-") as directory:
        paths = []
        for name, text in (("base.py", base), ("ours.py", ours), ("theirs.py", theirs)):
            path = f"{directory}/{name}"
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            paths.append(path)
        completed = subprocess.run(
            ["git", "merge-file", "-p", paths[1], paths[0], paths[2]],
            capture_output=True,
            text=True,
            check=False,
        )
    return completed.stdout if completed.returncode == 0 else None


def _resolve_member_conflict(
    resolve: MemberResolver | None,
    key: tuple[str, ...],
    base: _Member,
    ours: _Member,
    theirs: _Member,
) -> str | None:
    """Ask a policy for one replacement member, without widening its scope."""
    if resolve is None:
        return None
    try:
        chosen = resolve(key, base.text, ours.text, theirs.text)
    except Exception:
        return None
    if not isinstance(chosen, str):
        return None

    # The callback may select or edit this member, but may not delete it,
    # rename it, or smuggle in another top-level member.
    parsed = _parse_members(chosen)
    if parsed is None or len(parsed) != 1 or parsed[0].key != key:
        return None
    return chosen


def merge_python_members(
    ancestor: str,
    ours: str,
    theirs: str,
    *,
    resolve: MemberResolver | None = None,
) -> str | None:
    """Merge supported top-level Python members, or return ``None`` safely.

    ``ours`` supplies output ordering and formatting wherever its member is
    semantically unchanged.  New members from ``theirs`` are inserted before
    their next shared anchor (and before a final ``__main__`` block), retaining
    their relative dependency order.  Ambiguous structure and true content
    conflicts intentionally fail closed. A caller may provide ``resolve`` to
    choose one complete member for a true per-member conflict. Resolver output
    is accepted only when it contains exactly the original member key; omitted
    or invalid choices still fail closed.
    """
    base_members = _parse_members(ancestor)
    our_members = _parse_members(ours)
    their_members = _parse_members(theirs)
    if base_members is None or our_members is None or their_members is None:
        return None
    base = _member_map(base_members)
    our = _member_map(our_members)
    their = _member_map(their_members)
    if base is None or our is None or their is None:
        return None

    # A multi-binding import is a single indivisible member; keys must always
    # resolve to the same member across a comparison.
    for mapping in (base, our, their):
        for member in mapping.values():
            if any(mapping[key] is not member for key in member.key):
                return None

    selected: dict[int, str | None] = {}
    for member in base_members:
        key = member.key[0]
        base_member = member
        our_member = our.get(key)
        their_member = their.get(key)
        # Identical edits on both sides may extend or replace a grouped import.
        # Keep the complete agreed statement, never synthesize its bindings.
        if (
            our_member is not None
            and their_member is not None
            and our_member.key == their_member.key
            and _same(our_member, their_member)
        ):
            selected[id(base_member)] = our_member.text
            continue
        if (our_member is not None and our_member.key != base_member.key) or (
            their_member is not None and their_member.key != base_member.key
        ):
            return None
        if our_member is None or their_member is None:
            unchanged_other = _same(base_member, their_member if our_member is None else our_member)
            if our_member is None and their_member is None:
                selected[id(base_member)] = None
            elif unchanged_other:
                selected[id(base_member)] = None
            else:
                return None
            continue
        if _same(our_member, base_member) and _same(their_member, base_member):
            selected[id(base_member)] = our_member.text
        elif _same(our_member, base_member):
            selected[id(base_member)] = their_member.text
        elif _same(their_member, base_member) or _same(our_member, their_member):
            selected[id(base_member)] = our_member.text
        else:
            merged = _git_merge(base_member.text, our_member.text, their_member.text)
            if merged is None:
                merged = _resolve_member_conflict(
                    resolve, base_member.key, base_member, our_member, their_member
                )
                if merged is None:
                    return None
            selected[id(base_member)] = merged

    # A new key must represent a whole new member, not overlap an existing
    # binding.  This also handles replacing an old assignment with an import:
    # untouched old assignments are deleted, modified ones conflict above.
    base_ids = {id(member) for member in base_members}
    their_additions: list[_Member] = []
    for member in their_members:
        if id(member) not in base_ids and not any(key in base for key in member.key):
            matching = our.get(member.key[0])
            if matching is not None:
                if matching.key != member.key or not _same(matching, member):
                    return None
            else:
                if any(key in our for key in member.key):
                    return None
                their_additions.append(member)

    before: dict[int, list[str]] = {}
    tail: list[str] = []
    for addition in their_additions:
        anchor = next(
            (
                key
                for key in their_members[their_members.index(addition) + 1 :]
                if key.key[0] in our
            ),
            None,
        )
        if anchor is None:
            main = next((member for member in our_members if member.is_main), None)
            if main is None:
                tail.append(addition.text)
            else:
                before.setdefault(id(main), []).append(addition.text)
        else:
            before.setdefault(id(our[anchor.key[0]]), []).append(addition.text)

    output: list[str] = []
    for member in our_members:
        output.extend(before.get(id(member), []))
        base_member = base.get(member.key[0])
        if base_member is None:
            output.append(member.text)
        else:
            chosen = selected[id(base_member)]
            if chosen is not None:
                output.append(chosen)
    output.extend(tail)
    merged_source = ""
    for part in output:
        if (
            merged_source
            and not merged_source.endswith(("\n", "\r"))
            and not part.startswith(("\n", "\r"))
        ):
            merged_source += "\n"
        merged_source += part
    try:
        compile(merged_source, "<merged-python>", "exec")
    except SyntaxError:
        return None
    return merged_source
