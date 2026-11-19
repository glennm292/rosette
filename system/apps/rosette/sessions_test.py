from datetime import datetime, timedelta, timezone

from rosette.data_types import (
    Analysis,
    ClearedLog,
    ClearedRow,
    Notebook,
    ResponseMode,
    Row,
)
from rosette.sessions import SESSION_GAP, group_into_sessions

_START = datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)


def _at(minutes: float) -> str:
    return (_START + timedelta(minutes=minutes)).isoformat()


def _row(
    row_id: str,
    minutes: float,
    text: str = "asino",
    mode: ResponseMode = "gloss",
    language: str = "Italian",
) -> Row:
    return Row(
        row_id=row_id,
        text=text,
        status="done",
        analysis=Analysis(mode=mode, english="donkey", note="A masculine noun."),
        language=language,
        created_at=_at(minutes),
    )


def _cleared(row: Row, restored_at: str | None = None) -> ClearedRow:
    return ClearedRow(
        **row.model_dump(),
        clearing="20261118T210000000000Z",
        cleared_at=_at(120),
        how_cleared="line and below",
        position=1,
        restored_at=restored_at,
    )


def test_a_long_gap_starts_a_new_sitting() -> None:
    gap_minutes = SESSION_GAP.total_seconds() / 60
    cleared = ClearedLog(
        rows=(
            _cleared(_row("a", 0)),
            _cleared(_row("b", 20)),
            _cleared(_row("c", 20 + gap_minutes + 1)),
        )
    )

    sessions = group_into_sessions(Notebook(), cleared)

    assert [s.line_count for s in sessions] == [1, 2]
    assert sessions[1].started_at == _at(0)
    assert sessions[1].ended_at == _at(20)


def test_lines_inside_the_gap_stay_one_sitting() -> None:
    gap_minutes = SESSION_GAP.total_seconds() / 60
    cleared = ClearedLog(
        rows=(_cleared(_row("a", 0)), _cleared(_row("b", gap_minutes - 1)))
    )

    sessions = group_into_sessions(Notebook(), cleared)

    assert len(sessions) == 1
    assert sessions[0].line_count == 2


def test_a_sitting_keeps_its_lines_in_the_order_they_were_written() -> None:
    """The order is most of a sitting's sense, so nothing re-sorts it."""
    cleared = ClearedLog(
        rows=(
            _cleared(_row("a", 0, "asino", "gloss")),
            _cleared(_row("b", 1, "rimase fermo", "assess")),
            _cleared(_row("c", 2, "? what is the subjunctive", "answer")),
            _cleared(_row("d", 3, "the evening train", "translate")),
        )
    )

    (session,) = group_into_sessions(Notebook(), cleared)

    assert [r.row_id for r in session.rows] == ["a", "b", "c", "d"]
    assert session.line_count == 4
    # A translation counts with the lookups: both are a reach for a word the
    # student did not have, only in opposite directions.
    assert session.looked_up_count == 2
    assert session.checked_count == 1
    assert session.asked_count == 1


def test_lines_written_out_of_step_are_still_read_oldest_first() -> None:
    """The cleared log is ordered by when lines were cleared, not written."""
    cleared = ClearedLog(
        rows=(_cleared(_row("later", 5)), _cleared(_row("earlier", 1)))
    )

    (session,) = group_into_sessions(Notebook(), cleared)

    assert [r.row_id for r in session.rows] == ["earlier", "later"]


def test_the_newest_sitting_comes_first() -> None:
    gap_minutes = SESSION_GAP.total_seconds() / 60
    cleared = ClearedLog(
        rows=(_cleared(_row("old", 0)), _cleared(_row("new", gap_minutes + 1)))
    )

    sessions = group_into_sessions(Notebook(), cleared)

    assert [s.rows[0].row_id for s in sessions] == ["new", "old"]


def test_a_line_put_back_on_the_page_is_reviewed_once() -> None:
    """A restored line is in both stores; the notebook's copy is the live one."""
    restored = _row("a", 0)
    notebook = Notebook(rows=(restored,))
    cleared = ClearedLog(rows=(_cleared(restored, restored_at=_at(90)),))

    (session,) = group_into_sessions(notebook, cleared)

    assert session.line_count == 1


def test_blank_lines_and_unanswered_lines_are_left_out() -> None:
    blank = Row(row_id="blank", text="   ", status="empty", created_at=_at(0))
    unanswered = Row(row_id="waiting", text="asino", status="pending", created_at=_at(1))
    notebook = Notebook(rows=(blank, unanswered, _row("answered", 2)))

    (session,) = group_into_sessions(notebook, ClearedLog())

    assert session.line_count == 1
    assert session.rows[0].row_id == "answered"


def test_nothing_written_yet_is_no_sittings() -> None:
    assert group_into_sessions(Notebook(), ClearedLog()) == ()


def test_the_languages_a_sitting_was_written_in_are_named() -> None:
    notebook = Notebook(
        rows=(
            _row("a", 0, language="Italian"),
            _row("b", 1, language="German"),
            _row("c", 2, language="Italian"),
        )
    )

    (session,) = group_into_sessions(notebook, ClearedLog())

    assert session.languages == ("Italian", "German")
