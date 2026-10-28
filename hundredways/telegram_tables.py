"""Tables that survive the trip to a chat: plain text, bounded, never a partial row.

Telegram's ``sendMessage`` limit is 4096 characters and a whole message is dropped when
emphasis does not balance, so every answer here is plain text built from measured
numbers.  Two renderers exist because two consumers read them: :func:`block` lays one
row out as an indented record, which stays readable in a proportional chat font, and
:func:`grid` pads its columns for a monospace terminal or the dashboard.  Neither one
is the delivery path -- :func:`answer` wraps either style, paginates on line
boundaries, and names the rows it left out rather than dropping them silently.

Every cell passes the engine's canonical credential shapes on the way in, so a token
that reached a finding can be reported without being repeated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from .operation_safety import TELEGRAM_SAFETY_FOOTER
from .secrets import redact

__all__ = [
    "Table",
    "TableError",
    "BLOCK",
    "GRID",
    "MAX_MESSAGE_CHARS",
    "CHAT_LIMIT",
    "MAX_PAGES",
    "TRUNCATION_MARK",
    "RULE",
    "build",
    "render",
    "block",
    "grid",
    "paginate",
    "answer",
    "over_budget",
]

BLOCK = "block"
GRID = "grid"

MAX_MESSAGE_CHARS = 4096
MAX_PAGES = 10
TRUNCATION_MARK = ".."
RULE = "─" * 24
# The transport appends the run's safety footer to whatever this module returns, so a
# chat answer is built to leave room for it: a page that only fits before the footer is
# added is a page the transport will silently shorten mid-table.
CHAT_LIMIT = MAX_MESSAGE_CHARS - len(TELEGRAM_SAFETY_FOOTER) - 32


class TableError(ValueError):
    """The table cannot be built from what it was handed -- never a silent repair."""


@dataclass(frozen=True)
class Table:
    """A titled rectangle of measured strings, redacted before it is stored."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    notes: tuple[str, ...] = ()
    primary: int = 0

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def with_rows(self, rows: Sequence[Sequence[str]]) -> "Table":
        return replace(self, rows=tuple(tuple(row) for row in rows))


def build(
    title: str,
    columns: Iterable[str],
    rows: Iterable[Iterable[object]],
    *,
    notes: Iterable[str] = (),
    primary: int = 0,
) -> Table:
    """Assemble a :class:`Table`, refusing a ragged rectangle instead of padding it.

    A row with the wrong number of cells is a programming error at the call site, not
    data to be guessed at: silently blanking the missing cell would print a verdict
    with no owner next to it.
    """
    names = tuple(str(column) for column in columns)
    if not names:
        raise TableError("a table needs at least one column")
    if not 0 <= primary < len(names):
        raise TableError(f"primary column {primary} is not one of {len(names)} column(s)")
    body: list[tuple[str, ...]] = []
    for row in rows:
        cells = tuple(redact(str(cell)) for cell in row)
        if len(cells) != len(names):
            raise TableError(
                f"row {cells} has {len(cells)} cell(s), not {len(names)} for {title!r}"
            )
        body.append(cells)
    return Table(
        title=redact(title),
        columns=names,
        rows=tuple(body),
        notes=tuple(redact(note) for note in notes),
        primary=primary,
    )


def _clip(value: str, budget: int) -> str:
    """Shorten ``value`` to ``budget`` characters, marking that it was shortened."""
    if budget <= 0 or len(value) <= budget:
        return value
    return value[: max(0, budget - len(TRUNCATION_MARK))] + TRUNCATION_MARK


def _head(table: Table) -> list[str]:
    lines = [f"{table.title}  {table.row_count} row(s)"]
    lines.extend(table.notes)
    return lines


def _block_records(table: Table, *, cell: int = 120) -> tuple[tuple[str, ...], ...]:
    """The head as one record, then exactly one record per row.

    A record is the unit a page boundary is allowed to fall between.  Returning them
    separately from the joined text is what lets :func:`answer` refuse to send a row's
    label lines in a different message from its headline.
    """
    records: list[tuple[str, ...]] = [tuple(_head(table))]
    if not table.rows:
        return (*records, ("_no rows_",))
    others = [index for index in range(table.column_count) if index != table.primary]
    label_width = max((len(table.columns[index]) for index in others), default=0)
    for position, row in enumerate(table.rows, start=1):
        lines = [f"{position}. {_clip(row[table.primary], cell)}"]
        lines += [
            f"   {table.columns[index].ljust(label_width)}: {_clip(row[index], cell)}"
            for index in others
        ]
        records.append(tuple(lines))
    return tuple(records)


def _grid_records(table: Table, *, max_cell: int = 32) -> tuple[tuple[str, ...], ...]:
    """The head, then one line per row -- a grid row is already one record."""
    widths = []
    for index in range(table.column_count):
        needed = max(
            (len(row[index]) for row in table.rows),
            default=0,
        )
        widths.append(max(len(table.columns[index]), min(needed, max_cell)))
    rule = " ".join("-" * width for width in widths)
    records = [
        tuple(_head(table)),
        (
            " ".join(name.ljust(w) for name, w in zip(table.columns, widths)).rstrip(),
            rule,
        ),
    ]
    records += [
        (" ".join(_clip(cell_value, w).ljust(w) for cell_value, w in zip(row, widths)).rstrip(),)
        for row in table.rows
    ]
    return tuple(records)


def _records(table: Table, *, style: str = BLOCK, **kwargs: object) -> tuple[tuple[str, ...], ...]:
    if style == BLOCK:
        return _block_records(table, **kwargs)  # type: ignore[arg-type]
    if style == GRID:
        return _grid_records(table, **kwargs)  # type: ignore[arg-type]
    raise TableError(f"unknown table style {style!r}: use {BLOCK} or {GRID}")


def block(table: Table, *, cell: int = 120) -> str:
    """One row per record: the primary column as a headline, the rest as labelled lines.

    This is the chat layout.  Nothing depends on the reader having a fixed-width font,
    so a row cannot turn into a column of unrelated fragments on a phone.
    """
    return _join(_block_records(table, cell=cell))


def grid(table: Table, *, max_cell: int = 32) -> str:
    """The monospace layout: columns padded to the widest value they hold."""
    return _join(_grid_records(table, max_cell=max_cell))


def _join(records: Iterable[Iterable[str]]) -> str:
    return "\n".join(line for record in records for line in record) + "\n"


def render(table: Table, *, style: str = BLOCK, **kwargs: object) -> str:
    """Render ``table`` in the asked-for style; only the two known layouts exist."""
    if style not in (BLOCK, GRID):
        raise TableError(f"unknown table style {style!r}: use {BLOCK} or {GRID}")
    return _join(_records(table, style=style, **kwargs))


def _numbered(pages: list[str], limit: int) -> tuple[str, ...]:
    """Stamp multi-page answers -- but only when the stamp itself still fits."""
    if not pages:
        return ("",)
    if len(pages) == 1:
        return (pages[0],)
    total = len(pages)
    return tuple(
        f"{page}\n({index}/{total})" if len(page) + len(str(total)) + 6 <= limit else page
        for index, page in enumerate(pages, start=1)
    )


def paginate(text: str, *, limit: int = MAX_MESSAGE_CHARS) -> tuple[str, ...]:
    """Split rendered text into messages, cutting only between whole lines.

    A single line longer than ``limit`` is the one case that has to be cut mid-line:
    losing its tail would be worse than an ugly break, so it is split without
    truncation and every character still arrives.
    """
    if limit <= 0:
        raise TableError(f"a message limit must be positive, not {limit}")
    pages: list[str] = []
    current: list[str] = []
    used = 0

    def flush() -> None:
        if current:
            pages.append("\n".join(current))
            current.clear()

    for line in (text.rstrip("\n").split("\n") if text.strip() else []):
        if len(line) > limit:
            flush()
            for start in range(0, len(line), limit):
                pages.append(line[start : start + limit])
            continue
        extra = len(line) + (1 if current else 0)
        if used + extra > limit:
            flush()
            used = 0
        current.append(line)
        used += len(line) + 1
    flush()
    return _numbered(pages, limit)


def _pack(records: Iterable[Iterable[str]], *, limit: int) -> list[str]:
    """Join records into messages, breaking only between records.

    A record that alone exceeds ``limit`` is split without truncation rather than
    dropped or clipped: an ugly break that arrives complete beats a message that loses
    characters.
    """
    pages: list[str] = []
    current: list[str] = []
    used = 0
    for record in records:
        chunk = "\n".join(record)
        if len(chunk) > limit:
            if current:
                pages.append("\n".join(current))
                current, used = [], 0
            for start in range(0, len(chunk), limit):
                pages.append(chunk[start : start + limit])
            continue
        extra = len(chunk) + (1 if current else 0)
        if used + extra > limit:
            pages.append("\n".join(current))
            current, used = [], 0
            extra = len(chunk) + 1
        current.append(chunk)
        used += extra
    if current:
        pages.append("\n".join(current))
    return pages


def over_budget(pages: Sequence[str], limit: int = MAX_MESSAGE_CHARS) -> int:
    """How many pages break the limit -- 0 is the only acceptable answer."""
    return sum(1 for page in pages if len(page) > limit)


def _withheld(table: Table, kept: tuple[tuple[str, ...], ...], max_pages: int) -> str:
    """The line that names rows the budget could not carry -- part of the budget itself."""
    dropped = table.row_count - len(kept)
    if not dropped:
        return ""
    return (
        f"_showing {len(kept)} of {table.row_count} row(s): "
        f"{dropped} withheld to fit {max_pages} message(s)_"
    )


def answer(
    table: Table,
    *,
    style: str = BLOCK,
    limit: int = MAX_MESSAGE_CHARS,
    max_pages: int = MAX_PAGES,
    **kwargs: object,
) -> tuple[str, ...]:
    """The messages to send for ``table``: whole rows only, capped, and honest about the cap.

    When the table cannot fit in ``max_pages`` messages, whole rows are withheld from
    the end and the last page says how many and what to pass to see them.  A truncated
    answer that reads as a complete one is the failure this function exists to prevent.

    Three budgets are honoured, because breaking any of them would turn a measured
    answer into a misleading one: a page never exceeds ``limit`` including the withheld
    note it carries, a message boundary never falls inside a row, and the page count
    never exceeds ``max_pages`` -- except where nothing can be withheld any more, which
    is a single row too large for ``limit``.  There the cap yields to completeness, and
    every character still arrives.
    """
    if max_pages < 1:
        raise TableError(f"max_pages must be at least 1, not {max_pages}")
    kept = table.rows

    def pages_for(body: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
        records = list(_records(table.with_rows(body), style=style, **kwargs))
        note = _withheld(table, body, max_pages)
        if note:
            records.append((note,))
        return _numbered(_pack(records, limit=limit), limit)

    pages = pages_for(kept)
    while len(pages) > max_pages and len(kept) > 1:
        guess = max(1, len(kept) * max_pages // len(pages))
        kept = kept[: min(guess, len(kept) - 1)]
        pages = pages_for(kept)
    return pages
