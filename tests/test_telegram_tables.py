"""Chat tables: bounded, whole rows only, and never a silent cut.

Telegram drops a message whose emphasis does not balance and hard-caps one at 4096
characters, so the failure this module has to prevent is not ugliness -- it is an
answer that arrives complete-looking while its tail was discarded.  Every test here
therefore asserts the *contract* a commander reads: a page never exceeds the limit it
was built to, a row is either whole or named as withheld, a credential-shaped cell
arrives masked, and nothing is claimed about rows nobody measured.
"""

from __future__ import annotations

import re

import pytest

from hundredways import telegram_tables as tt
from hundredways.operation_safety import TELEGRAM_SAFETY_FOOTER

A_TOKEN = "ghp_" + "A" * 36
HEADLINE = re.compile(r"^\d+\. ")


def table(rows=None, *, title="TEST", columns=("name", "detail"), notes=(), primary=0) -> tt.Table:
    return tt.build(title, columns, rows if rows is not None else [("alpha", "one")],
                   notes=notes, primary=primary)


def _without_page_marks(pages):
    """The pages as they would read before the ``(i/n)`` line was stamped on them."""
    return [page.rsplit("\n(", 1)[0] if "\n(" in page else page for page in pages]


# --- building ---------------------------------------------------------------


def test_build_keeps_a_rectangle_or_refuses():
    with pytest.raises(tt.TableError, match="2 cell"):
        tt.build("T", ("a", "b", "c"), [("only", "two")])


def test_build_refuses_no_columns_and_a_primary_that_is_not_a_column():
    with pytest.raises(tt.TableError, match="at least one column"):
        tt.build("T", (), [])
    with pytest.raises(tt.TableError, match="primary column"):
        tt.build("T", ("a",), [], primary=1)


def test_a_credential_shaped_cell_arrives_masked():
    built = tt.build("T", ("name", "token"), [("lane", A_TOKEN)])

    assert built.rows == (("lane", "[REDACTED]"),)
    assert A_TOKEN not in tt.block(built)


def test_a_build_note_is_stored_and_reaches_both_layouts():
    built = table(notes=("measured from the payload that was read",))
    for rendered in (tt.block(built), tt.grid(built)):
        assert "measured from the payload that was read" in rendered


def test_a_table_reports_its_own_size():
    built = table([("a", "1"), ("b", "2")], columns=("name", "detail"))

    assert (built.row_count, built.column_count) == (2, 2)
    assert built.with_rows((("c", "3"),)).row_count == 1
    assert built.row_count == 2, "with_rows must not mutate the table it was called on"


# --- layout -----------------------------------------------------------------


def test_block_leads_with_the_primary_and_labels_the_rest():
    lines = tt.block(table([("python-tests", "runner capacity")])).splitlines()

    assert lines[0] == "TEST  1 row(s)"
    assert lines[1] == "1. python-tests"
    assert lines[2] == "   detail: runner capacity"


def test_block_numbers_rows_from_one_and_names_an_empty_table_as_empty():
    assert "1. alpha" in tt.block(table())
    assert "_no rows_" in tt.block(table([]))


def test_block_clips_a_cell_that_would_wrap_and_marks_that_it_was_clipped():
    rendered = tt.block(table([("lane", "x" * 300)]), cell=40)

    assert "x" * 40 not in rendered
    assert "x" * 38 + ".." in rendered


def test_grid_pads_each_column_to_the_widest_value_it_holds():
    lines = tt.grid(table([("a", "short"), ("b", "much longer value")])).splitlines()

    assert lines[1] == "name detail"
    assert lines[2] == "---- -----------------"
    assert lines[3] == "a    short"
    assert lines[4] == "b    much longer value"


def test_grid_clips_at_the_column_budget_rather_than_widening_the_table():
    rendered = tt.grid(table([("lane", "y" * 120)]), max_cell=20)

    assert "y" * 21 not in rendered
    assert "y" * 18 + ".." in rendered


def test_render_only_knows_the_two_layouts_it_ships():
    assert tt.render(table(), style=tt.GRID) == tt.grid(table())
    assert tt.render(table()) == tt.block(table())
    with pytest.raises(tt.TableError, match="unknown table style"):
        tt.render(table(), style="markdown")


# --- pagination -------------------------------------------------------------


def test_pages_cut_between_whole_lines_only():
    text = "\n".join(f"line-{i}" for i in range(6))
    pages = tt.paginate(text + "\n", limit=22)

    assert len(pages) > 1
    for page in _without_page_marks(pages):
        for line in page.splitlines():
            assert line in text.splitlines()


def test_an_over_long_line_is_split_without_losing_a_character():
    long_line = "z" * 95
    pages = tt.paginate(long_line + "\n", limit=30)

    assert "".join(_without_page_marks(pages)) == long_line
    assert all(len(page) <= 30 for page in pages)


def test_more_than_one_page_is_numbered_and_one_page_is_not():
    assert tt.paginate("hello\n", limit=40) == ("hello",)
    pages = tt.paginate("a" * 20 + "\n" + "b" * 20 + "\n", limit=40)

    assert len(pages) == 2
    assert "(1/2)" in pages[0] and "(2/2)" in pages[1]


def test_a_page_mark_is_left_off_when_it_would_cost_more_than_it_reports():
    pages = tt.paginate("a" * 20 + "\n" + "b" * 20 + "\n", limit=21)

    assert len(pages) == 2
    assert all("/" not in page for page in pages)


def test_no_text_is_still_one_sendable_message():
    assert tt.paginate("", limit=10) == ("",)
    assert tt.paginate("\n \n", limit=10) == ("",)


def test_a_message_limit_has_to_exist():
    with pytest.raises(tt.TableError, match="must be positive"):
        tt.paginate("x", limit=0)


# --- the answer the transport sends ----------------------------------------


def test_answer_never_exceeds_the_page_cap_it_was_given():
    wide = table([(f"row-{i}", "x" * 60) for i in range(40)])

    for style in (tt.BLOCK, tt.GRID):
        for cap in (1, 2, 3, 5):
            pages = tt.answer(wide, style=style, limit=400, max_pages=cap)
            assert len(pages) <= cap, f"{style} cap {cap} returned {len(pages)} messages"
            assert tt.over_budget(pages, 400) == 0


def test_withheld_rows_are_named_on_the_answer_that_omits_them():
    wide = table([(f"row-{i}", "x" * 60) for i in range(40)])
    body = "\n".join(tt.answer(wide, limit=400, max_pages=1))

    assert "withheld to fit 1 message(s)" in body
    shown = int(body.split("showing ")[1].split(" of")[0])
    headlines = [line for line in body.splitlines() if line[:2].rstrip(".").isdigit()]
    assert len(headlines) == shown < 40
    assert f"of {wide.row_count} row(s)" in body


def test_a_message_boundary_never_falls_inside_a_row():
    wide = table([(f"row-{i}", "x" * 60) for i in range(40)])

    for page in tt.answer(wide, limit=400, max_pages=2):
        lines = page.splitlines()
        for position, line in enumerate(lines):
            if line.startswith("   detail:"):
                assert HEADLINE.match(lines[position - 1]), lines[position - 1]


def test_only_whole_rows_survive_so_a_row_cannot_arrive_half_read():
    wide = table([(f"row-{i}", "x" * 60) for i in range(40)])
    body = "\n".join(tt.answer(wide, limit=400, max_pages=2))

    assert "x" * 60 in body, "a kept row keeps its whole second cell"
    assert "x" * 61 not in body


def test_a_table_that_fits_carries_no_apology():
    body = "\n".join(tt.answer(table([("a", "1"), ("b", "2")]), limit=400, max_pages=1))

    assert "withheld" not in body
    assert body.count("row(s)") == 1


def test_answer_rejects_a_cap_that_would_send_nothing():
    with pytest.raises(tt.TableError, match="at least 1"):
        tt.answer(table(), max_pages=0)


def test_a_one_page_answer_leaves_room_for_the_footer_the_transport_adds():
    assert tt.CHAT_LIMIT < tt.MAX_MESSAGE_CHARS
    assert tt.CHAT_LIMIT == tt.MAX_MESSAGE_CHARS - len(TELEGRAM_SAFETY_FOOTER) - 32
    pages = tt.answer(table([("lane", "u" * 300) for _ in range(40)]), limit=tt.CHAT_LIMIT,
                      max_pages=1)
    for page in pages:
        assert len(page) + len(TELEGRAM_SAFETY_FOOTER) + 3 <= tt.MAX_MESSAGE_CHARS
