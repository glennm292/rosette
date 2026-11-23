"""Where one sitting of writing ends and the next begins.

This is the one thing about past work that a query cannot find for itself. Everything else
the review page shows -- which lines count, how many were looked up, when a sitting started --
is a query over the rows ``datasets`` shapes (see ``datasets.SAVED_VIEWS``). Finding the gaps
is not, because it means walking the lines in order and comparing each to the one before, so
it happens here, once, and its answer rides along on every line as a property.

The notebook holds only what is on the page now: the margin scissors puts a finished passage
into the cleared log, so an evening's work is almost entirely *cleared* by the time it is
over. Reading it back means folding both stores together first.
"""

import re
from datetime import datetime, timedelta
from typing import Iterator

from rosette.data_types import ClearedLog, Notebook, Row

# A gap longer than this between one line and the next is a new sitting. Long
# enough to cover looking a passage up, making tea, or an interruption inside
# one class; short enough that two evenings never merge into one.
SESSION_GAP = timedelta(minutes=90)

_NOT_A_KEY = re.compile(r"[^0-9A-Za-z]+")


def sitting_key(started_at: str) -> str:
    """A sitting's name: when it began, with nothing in it a clickable link would misread."""
    return _NOT_A_KEY.sub("_", started_at).strip("_")


def _written_at(row: Row) -> datetime:
    return datetime.fromisoformat(row.created_at)


def _is_worth_reviewing(row: Row) -> bool:
    # A blank line is a place to type, and a line whose answer never arrived has
    # nothing to review.
    return row.text.strip() != "" and row.analysis is not None


def all_rows(notebook: Notebook, cleared: ClearedLog) -> tuple[Row, ...]:
    """Every line the student has written, each appearing once, oldest first.

    A restored line is in both stores, so the notebook's copy wins: it is the
    one carrying any answer that arrived after it came back.
    """
    rows = list(notebook.rows)
    on_the_page = {row.row_id for row in notebook.rows}
    for cleared_row in cleared.rows:
        if cleared_row.row_id in on_the_page:
            continue
        on_the_page.add(cleared_row.row_id)
        rows.append(Row.model_validate(cleared_row.model_dump(include=set(Row.model_fields))))
    return tuple(sorted(rows, key=lambda row: row.created_at))


def sittings_of(notebook: Notebook, cleared: ClearedLog) -> Iterator[tuple[str, Row]]:
    """Every line worth reading back, oldest first, paired with the sitting it belongs to."""
    previous: Row | None = None
    sitting = ""
    for row in all_rows(notebook, cleared):
        if not _is_worth_reviewing(row):
            continue
        if previous is None or _written_at(row) - _written_at(previous) > SESSION_GAP:
            sitting = sitting_key(row.created_at)
        previous = row
        yield sitting, row
