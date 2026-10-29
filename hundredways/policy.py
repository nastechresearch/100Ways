"""The narrow block-style YAML subset our policy files are written in.

``pyyaml`` is a **dev** extra (see ``pyproject.toml``): the runtime interpreter a
base install gets has no YAML library at all.  Policy files are still the right
place to keep hosts, budgets and allowlists -- reviewable, diffable, and not
code -- so this module reads the shape those files actually use instead of
pulling a new dependency into every install for four files.

The subset, in full:

* comments (``#`` at line start or after whitespace) and blank lines;
* mappings whose keys match ``[A-Za-z_][A-Za-z0-9_-]*``, at indentation steps of
  exactly two spaces (a dotted key would be ambiguous against the dotted paths
  :func:`require` takes);
* sequences of plain or quoted scalars, at the parent key's indent or two
  deeper;
* scalars are strings, except an unquoted base-10 integer or decimal, which
  resolve to ``int``/``float`` exactly as a YAML parser resolves them.  ``""``
  and ``''`` are the empty string; there is no null -- a policy that does not
  want a value omits the key rather than writing ``null``, so an absent key and
  a null-valued key cannot mean different things.

Everything else -- flow collections, anchors, aliases, tags, block scalars,
multiple documents, tabs, duplicate keys, sequences of mappings, an unquoted
truth word, or a number this reader cannot resolve to the same value a YAML
parser would (``07``, ``1e3``, ``0x1f``) -- raises :class:`PolicyError`.
Refusing is the point: a construct this reader did not read must never be
quietly dropped, because a line silently ignored in a policy file is a policy
that is not enforced.  ``tests/test_policy.py`` cross-checks every accepted
shape against ``yaml.safe_load``, so "our subset" also means "the same answer a
real parser would give".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["PolicyError", "parse", "require", "load"]

_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_INDENT_STEP = 2
_NULL_TOKENS = frozenset({"null", "~"})
_TRUE_TOKENS = frozenset({"true", "on", "yes"})
_FALSE_TOKENS = frozenset({"false", "off", "no"})
_INT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)")
_FLOAT_RE = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")
# Anything a YAML parser would resolve to a number.  When a token matches this
# but not the two strict patterns above (07, 1e3, 0x1f, 1:30) the reader refuses
# rather than guessing: keeping it a string would disagree with yaml.safe_load,
# and a policy value that means different things to two readers is a policy bug
# nobody sees.  Two-dotted names (127.0.0.1, 1.2.3) are not numbers to either.
_NUMERICISH_RE = re.compile(
    r"""^[-+]?                    # optional sign
        (?: 0[xXbBoO][0-9a-fA-F_]+      # hex / octal / binary
          | [0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][-+]?[0-9]+)?   # int, float, exponent
          | [0-9][0-9_]*(?::[0-5]?[0-9])+\.?[0-9_]*          # sexagesimal
          | \.(?:inf|Inf|INF)
          | \.(?:nan|NaN|NAN)
        )$""",
    re.VERBOSE,
)
# A YAML truth word written unquoted is a boolean to a real parser, so a plain
# scalar may not begin one: `key: yes` would read as the string "yes" here and
# True under yaml.safe_load.  Quoting it is the fix the error says.
_BOOL_WORDS = _TRUE_TOKENS | _FALSE_TOKENS


class PolicyError(ValueError):
    """A policy file that this reader could not read, or whose shape is wrong."""


def _fail(line_number: int, message: str) -> "PolicyError":
    return PolicyError(f"line {line_number}: {message}")


def _strip_comment(raw: str) -> str:
    """Drop a trailing ``#`` comment that is not inside quotes."""
    quote = ""
    for index, char in enumerate(raw):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or raw[index - 1] in " \t"):
            return raw[:index]
    return raw.rstrip()


def _unquote(value: str, line_number: int) -> str:
    if value.startswith('"'):
        body, rest = _cut_closing(value, '"', "\\", line_number)
        if rest.strip():
            raise _fail(line_number, f"trailing content after a quoted scalar: {rest!r}")
        return body
    if value.startswith("'"):
        body, rest = _cut_closing(value, "'", None, line_number)
        if rest.strip():
            raise _fail(line_number, f"trailing content after a quoted scalar: {rest!r}")
        return body
    return value


def _cut_closing(value: str, quote: str, escape: str | None, line_number: int) -> tuple[str, str]:
    """Split a quoted scalar into its inner text and whatever follows the close."""
    out: list[str] = []
    index = 1
    while index < len(value):
        char = value[index]
        if escape is not None and char == escape and index + 1 < len(value):
            nxt = value[index + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            index += 2
            continue
        if char == quote:
            if escape is None and index + 1 < len(value) and value[index + 1] == quote:
                out.append(quote)  # YAML's '' escapes a single quote
                index += 2
                continue
            return "".join(out), value[index + 1 :]
        out.append(char)
        index += 1
    raise _fail(line_number, "unterminated quoted scalar")


def _reject_outside_subset(value: str, line_number: int) -> None:
    """Name the construct rather than guess at it."""
    if value.startswith("[") or value.startswith("{"):
        raise _fail(line_number, "a flow collection is outside the subset: use block style")
    if value[0] in "|>" and (len(value) == 1 or value[1] in "-+0123456789"):
        raise _fail(line_number, "a block scalar is outside the subset: use quoted strings")
    if value.startswith("&"):
        raise _fail(line_number, "an anchor is outside the subset")
    if value.startswith("*"):
        raise _fail(line_number, "an alias is outside the subset")
    if value.startswith("!"):
        raise _fail(line_number, "a tag is outside the subset")
    if value in _NULL_TOKENS:
        raise _fail(line_number, "null is outside the subset: omit the key instead")
    head = value.split(" ", 1)[0].lower()
    if head in _BOOL_WORDS:
        raise _fail(
            line_number,
            f"{value!r} is an unquoted truth word, which a real YAML parser reads as a "
            "boolean: quote it to keep it a string",
        )
    if ": " in value or value.endswith(":"):
        raise _fail(
            line_number,
            f"{value!r} is not a plain scalar: a nested mapping needs its own key line",
        )


def _scalar_value(token: str, line_number: int) -> Any:
    """One scalar, resolved the way ``yaml.safe_load`` would resolve it."""
    if token[:1] in "\"'":
        return _unquote(token, line_number)
    _reject_outside_subset(token, line_number)
    if _INT_RE.fullmatch(token):
        return int(token)
    if _FLOAT_RE.fullmatch(token):
        return float(token)
    if _NUMERICISH_RE.fullmatch(token) or (
        token[:1] == "." and token[1:2].isdigit()
    ):
        raise _fail(
            line_number,
            f"{token!r} is a number this reader cannot resolve the way YAML would: quote it",
        )
    return token


class _Lines:
    """Non-blank, comment-stripped lines with their indent and original number."""

    def __init__(self, text: str) -> None:
        self.items: list[tuple[int, str, int]] = []
        self._parse(text)

    def _parse(self, text: str) -> None:
        for number, raw in enumerate(text.splitlines(), start=1):
            if raw.lstrip().startswith("#") or not raw.strip():
                continue
            if raw[:1] == "\t" or "\t" in raw[: len(raw) - len(raw.lstrip())]:
                raise _fail(number, "a tab is outside the subset: indent with spaces")
            stripped = raw.strip()
            if stripped in {"---", "..."} or stripped.startswith("%"):
                raise _fail(number, "a document marker is outside the subset")
            indent = len(raw) - len(raw.lstrip(" "))
            if indent % _INDENT_STEP:
                raise _fail(number, "indentation must be a multiple of two spaces")
            body = _strip_comment(raw)
            content = body.strip()
            if not content:
                continue
            self.items.append((indent, content, number))


class _Reader:
    def __init__(self, lines: list[tuple[int, str, int]]) -> None:
        self._lines = lines
        self._pos = 0

    def document(self) -> dict[str, Any]:
        if not self._lines:
            return {}
        indent, _, number = self._lines[0]
        if indent != 0:
            raise _fail(number, "the document is indented: a policy starts at column one")
        if self._is_sequence_item(self._lines[0][1]):
            raise _fail(number, "a top-level sequence is outside the subset")
        value = self._mapping(0)
        if self._pos < len(self._lines):
            extra_indent, _, extra_number = self._lines[self._pos]
            raise _fail(
                extra_number,
                f"line does not continue a key: value pair (indent {extra_indent})",
            )
        return value

    @staticmethod
    def _is_sequence_item(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def _peek(self) -> tuple[int, str, int] | None:
        if self._pos < len(self._lines):
            return self._lines[self._pos]
        return None

    def _scalar(self, content: str, number: int) -> Any:
        return _scalar_value(content, number)

    def _split_key(self, content: str, number: int) -> tuple[str, str]:
        quote = ""
        for index, char in enumerate(content):
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in "\"'":
                quote = char
            elif char == ":" and (index + 1 == len(content) or content[index + 1] in " \t"):
                key = content[:index].strip()
                if not _KEY_RE.fullmatch(key):
                    raise _fail(number, f"{key!r} is not a plain key in the subset")
                if "." in key:
                    raise _fail(number, f"a dotted key ({key!r}) is outside the subset: nest it")
                return key, content[index + 1 :].strip()
        raise _fail(number, f"has no key: value pair: {content!r}")

    def _mapping(self, indent: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        while True:
            line = self._peek()
            if line is None:
                return out
            line_indent, content, number = line
            if line_indent < indent:
                return out
            if line_indent > indent:
                raise _fail(
                    number,
                    "unexpected indent: a deeper line cannot continue a scalar value",
                )
            if self._is_sequence_item(content):
                raise _fail(number, "a sequence of mappings is outside the subset")
            key, rest = self._split_key(content, number)
            if key in out:
                raise _fail(number, f"duplicate key {key!r}")
            self._pos += 1
            if rest:
                out[key] = self._scalar(rest, number)
                continue
            out[key] = self._child(indent, number)

    def _child(self, parent_indent: int, number: int) -> Any:
        line = self._peek()
        if line is None:
            raise _fail(number, "key has no value")
        line_indent, content, line_number = line
        if self._is_sequence_item(content):
            if line_indent not in (parent_indent, parent_indent + _INDENT_STEP):
                raise _fail(
                    line_number,
                    "sequence items must align with their key or sit two spaces deeper",
                )
            return self._sequence(line_indent)
        if line_indent == parent_indent + _INDENT_STEP:
            return self._mapping(line_indent)
        if line_indent > parent_indent:
            raise _fail(line_number, "a nested mapping must be indented exactly two spaces")
        raise _fail(line_number, "key has no value")

    def _sequence(self, indent: int) -> list[str]:
        out: list[str] = []
        while True:
            line = self._peek()
            if line is None or line[0] != indent or not self._is_sequence_item(line[1]):
                if line is not None and line[0] > indent:
                    raise _fail(line[2], "a sequence of mappings is outside the subset")
                return out
            _, content, number = line
            item = content[1:].strip()
            if not item:
                raise _fail(number, "an empty sequence item is outside the subset")
            if self._is_sequence_item(item):
                raise _fail(number, "a nested sequence is outside the subset")
            key_like = item.split(":", 1)
            if len(key_like) == 2 and _KEY_RE.fullmatch(key_like[0].strip()) and (
                len(item) == len(key_like[0]) or item[len(key_like[0])] in " "
            ):
                raise _fail(number, "a sequence of mappings is outside the subset")
            out.append(self._scalar(item, number))
            self._pos += 1


def parse(text: str) -> dict[str, Any]:
    """Read the subset into ``dict[str, str | int | float | list | dict]``."""
    return _Reader(_Lines(text).items).document()


def _lookup(doc: Any, dotted: str) -> tuple[bool, Any]:
    found = doc
    for part in dotted.split("."):
        if not isinstance(found, dict) or part not in found:
            return False, None
        found = found[part]
    return True, found


def _check(kind: str, value: Any, container: dict[str, Any], key: str, path: str) -> str | None:
    if kind == "str":
        if not isinstance(value, str):
            return f"{path}: expected str, got {_shape(value)}"
        return None
    if kind == "list":
        if not isinstance(value, list):
            return f"{path}: expected list, got {_shape(value)}"
        return None
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{path}: expected int, got {_shape(value)}"
        return None
    if kind == "bool":
        if isinstance(value, bool):
            return None
        if not isinstance(value, str):
            return f"{path}: expected bool, got {_shape(value)}"
        lowered = value.lower()
        if lowered in _TRUE_TOKENS:
            container[key] = True
            return None
        if lowered in _FALSE_TOKENS:
            container[key] = False
            return None
        return f"{path}: {value!r} is not a boolean word (true/false/on/off/yes/no)"
    raise PolicyError(f"unknown kind {kind!r} for {path}")


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a mapping"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return f"the number {value}"
    if isinstance(value, float):
        return f"the number {value}"
    return repr(value)


def require(doc: dict[str, Any], schema: dict[str, str], *, source: str) -> dict[str, Any]:
    """Validate ``doc`` against ``schema`` and normalize the scalars it names.

    ``schema`` maps a dotted path (``probe.timeout_seconds``) to a kind --
    ``str``, ``int``, ``bool`` or ``list``.  Every violation is reported in one
    message, in schema order: fixing a policy file one error at a time is how
    people come to distrust the tool that checks it.
    """
    violations: list[str] = []
    for path, kind in schema.items():
        if kind not in {"str", "int", "bool", "list"}:
            raise PolicyError(f"{source}: unknown kind {kind!r} for {path}")
        found, value = _lookup(doc, path)
        if not found:
            violations.append(f"{path}: missing")
            continue
        parts = path.split(".")
        parent, key = _lookup_parent(doc, parts)
        if parent is None:
            violations.append(f"{path}: not a mapping")
            continue
        problem = _check(kind, value, parent, key, path)
        if problem:
            violations.append(problem)
    if violations:
        raise PolicyError(
            f"{source}: {len(violations)} problem(s): " + "; ".join(violations)
        )
    return doc


def _lookup_parent(doc: Any, parts: list[str]) -> tuple[Any, str]:
    node = doc
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None, ""
        node = node[part]
    return node, parts[-1]


def load(path: str, schema: dict[str, str] | None = None) -> dict[str, Any]:
    """Read a policy file from disk, validating it when a schema is given."""
    file = Path(path)
    if not file.is_file():
        raise PolicyError(f"no such policy file: {path}")
    doc = parse(file.read_text(encoding="utf-8"))
    if schema is not None:
        return require(doc, schema, source=str(file.name))
    return doc
