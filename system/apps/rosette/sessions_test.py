from datetime import datetime, timedelta, timezone

from rosette.data_types import (
    Analysis,
    ClearedLog,
    ClearedRow,
    Notebook,
    ResponseMode,
    Row,
)
from rosette.sessions import SESSION_GAP, sitting_key, sittings_of

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


def _sittings(notebook: Notebook, cleared: ClearedLog) -> list[tuple[str, str]]:
    """Each line as (its sitting, its row id), oldest first."""
    return [(sitting, row.row_id) for sitting, row in sittings_of(notebook, cleared)]


def test_a_long_gap_starts_a_new_sitting() -> None:
    gap_minutes = SESSION_GAP.total_seconds() / 60
    cleared = ClearedLog(
        rows=(
            _cleared(_row("a", 0)),
            _cleared(_row("b", 20)),
            _cleared(_row("c", 20 + gap_minutes + 1)),
        )
    )

    sittings = _sittings(Notebook(), cleared)

    assert [row_id for _, row_id in sittings] == ["a", "b", "c"]
    first, second = sitting_key(_at(0)), sitting_key(_at(20 + gap_minutes + 1))
    assert [sitting for sitting, _ in sittings] == [first, first, second]


def test_lines_inside_the_gap_stay_one_sitting() -> None:
    gap_minutes = SESSION_GAP.total_seconds() / 60
    cleared = ClearedLog(
        rows=(_cleared(_row("a", 0)), _cleared(_row("b", gap_minutes - 1)))
    )

    sittings = _sittings(Notebook(), cleared)

    assert len({sitting for sitting, _ in sittings}) == 1
    assert len(sittings) == 2


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

    sittings = _sittings(Notebook(), cleared)

    assert [row_id for _, row_id in sittings] == ["a", "b", "c", "d"]


def test_lines_written_out_of_step_are_still_read_oldest_first() -> None:
    """The cleared log is ordered by when lines were cleared, not written."""
    cleared = ClearedLog(
        rows=(_cleared(_row("later", 5)), _cleared(_row("earlier", 1)))
    )

    assert [row_id for _, row_id in _sittings(Notebook(), cleared)] == ["earlier", "later"]


def test_a_sittings_name_sorts_it_into_place() -> None:
    """The newest sitting comes first in the view, which sorts on this name alone."""
    assert sitting_key(_at(0)) < sitting_key(_at(1))
    assert "-" not in sitting_key(_at(0))


def test_a_line_put_back_on_the_page_is_reviewed_once() -> None:
    """A restored line is in both stores; the notebook's copy is the live one."""
    restored = _row("a", 0)
    notebook = Notebook(rows=(restored,))
    cleared = ClearedLog(rows=(_cleared(restored, restored_at=_at(90)),))

    assert [row_id for _, row_id in _sittings(notebook, cleared)] == ["a"]


def test_blank_lines_and_unanswered_lines_are_left_out() -> None:
    blank = Row(row_id="blank", text="   ", status="empty", created_at=_at(0))
    unanswered = Row(row_id="waiting", text="asino", status="pending", created_at=_at(1))
    notebook = Notebook(rows=(blank, unanswered, _row("answered", 2)))

    assert [row_id for _, row_id in _sittings(notebook, ClearedLog())] == ["answered"]


def test_nothing_written_yet_is_no_sittings() -> None:
    assert _sittings(Notebook(), ClearedLog()) == []


