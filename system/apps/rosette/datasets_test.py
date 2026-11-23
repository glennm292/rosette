"""The rows Rosette publishes, and the views it runs over them.

Every view here goes through the real DACTAL engine, because that is the only thing that can
tell you a query is right: the engine is forgiving on purpose, so a misspelled dataset comes
back as a plain string and a misspelled property is quietly ignored. Neither errors.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from rosette.data_types import Analysis, ClearedLog, ClearedRow, Notebook, ResponseMode, Row
from rosette.datasets import (
    LINES_IN_A_SITTING_QUERY,
    SAVED_VIEWS,
    SITTINGS_QUERY,
    build_datasets,
    line_rows,
)
from rosette.notebook import NotebookStore
from rosette.sessions import SESSION_GAP
from rosette.views import run_view

_START = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)

# One of each kind of help, so a view that counts them has all four to count.
_EARLIER_LINES: list[tuple[str, float, ResponseMode]] = [
    ("a", 0, "gloss"),
    ("b", 1, "translate"),
    ("c", 2, "answer"),
    ("d", 3, "assess"),
]


def _at(minutes: float) -> str:
    return (_START + timedelta(minutes=minutes)).isoformat()


def _row(row_id: str, minutes: float, mode: ResponseMode = "gloss", text: str = "asino") -> Row:
    return Row(
        row_id=row_id,
        text=text,
        status="done",
        analysis=Analysis(
            mode=mode,
            english="donkey",
            note="A masculine noun.",
            # An assessed line is one the tutor read as Italian and had to change.
            verdict="fix" if mode == "assess" else None,
        ),
        language="Italian",
        created_at=_at(minutes),
    )


@pytest.fixture
def notebook_with_two_sittings(tmp_path: Path) -> Path:
    """Four lines in one sitting and one in another, written straight to the store."""
    gap = SESSION_GAP.total_seconds() / 60
    store = NotebookStore(tmp_path)
    kept = _row("on-the-page", gap + 10, "assess", "rimase fermo")
    store.upsert_row(kept, None)
    cleared = ClearedLog(
        rows=tuple(
            ClearedRow(
                **_row(row_id, minutes, mode).model_dump(),
                clearing="20261118T210000000000Z",
                cleared_at=_at(gap + 60),
                how_cleared="line and below",
                position=position,
            )
            for position, (row_id, minutes, mode) in enumerate(
                _EARLIER_LINES, start=1
            )
        )
    )
    (tmp_path / "cleared.json").write_text(cleared.model_dump_json(), encoding="utf-8")
    return tmp_path


def test_a_line_carries_the_sitting_it_was_written_in(notebook_with_two_sittings: Path) -> None:
    """The one thing a query cannot work out for itself, so it has to be in the rows."""
    rows = line_rows(notebook_with_two_sittings)

    assert [row["id"] for row in rows] == ["a", "b", "c", "d", "on_the_page"]
    first_sitting = {row["sitting"] for row in rows[:4]}
    assert len(first_sitting) == 1
    assert rows[4]["sitting"] not in first_sitting
    # No hyphens: the name goes into a query, which would read what follows one as an expression.
    assert all("-" not in row["sitting"] and "-" not in row["id"] for row in rows)


def test_the_records_as_the_app_stores_them_are_still_there(
    notebook_with_two_sittings: Path,
) -> None:
    """The shaped rows are an extra way to read the notebook, never a replacement for it."""
    item = build_datasets(notebook_with_two_sittings)["datasets"]["rosette"]["item"]

    assert len(item["cleared"]) == 4
    assert len(item["notebook"]) == 1
    assert item["cleared"][0]["row_id"] == "a"


def test_the_sittings_view_counts_what_each_sitting_held(
    notebook_with_two_sittings: Path,
) -> None:
    """These numbers are the review page's heading, and they come from here, not from Python."""
    rows = run_view(SITTINGS_QUERY, notebook_with_two_sittings)

    def one(value):
        return value[0] if isinstance(value, list) else value

    assert len(rows) == 2
    newest, oldest = rows
    assert one(oldest["count"]) == 4
    # A translation counts with the lookups: both are a reach for a word the student did not
    # have, only in opposite directions.
    assert one(oldest["looked_up"]) == 2
    assert one(oldest["checked"]) == 1
    assert one(oldest["asked"]) == 1
    assert one(newest["count"]) == 1
    assert one(newest["checked"]) == 1
    # Newest first, which is how the review page offers them.
    assert one(newest["started"]) > one(oldest["started"])


def test_a_sittings_lines_come_back_in_the_order_they_were_written(
    notebook_with_two_sittings: Path,
) -> None:
    sitting = line_rows(notebook_with_two_sittings)[0]["sitting"]

    rows = run_view(LINES_IN_A_SITTING_QUERY.format(sitting=sitting), notebook_with_two_sittings)

    assert [row["id"] for row in rows] == ["a", "b", "c", "d"]


@pytest.mark.parametrize("view", SAVED_VIEWS, ids=lambda view: view["name"])
def test_every_published_view_actually_runs(view: dict, notebook_with_two_sittings: Path) -> None:
    """A view that returns bare strings is the signature of a typo: the engine does not raise."""
    rows = run_view(view["query"], notebook_with_two_sittings)

    assert rows, f"{view['name']} came back empty over a notebook that has lines in it"
    assert all(isinstance(row, dict) for row in rows), (
        f"{view['name']} came back as plain values, which is what a misspelled name does"
    )
