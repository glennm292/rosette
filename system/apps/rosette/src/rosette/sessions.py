"""Past work, grouped into the sittings it was written in.

The notebook holds only what is on the page now: the margin scissors puts a
finished passage into the cleared log, so an evening's work is almost entirely
*cleared* by the time it is over. Reviewing it means reading both stores back
together and cutting them where the student stopped writing.
"""

from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from rosette.data_types import Analysis, ClearedLog, Notebook, Row

# A gap longer than this between one line and the next is a new sitting. Long
# enough to cover looking a passage up, making tea, or an interruption inside
# one class; short enough that two evenings never merge into one.
SESSION_GAP = timedelta(minutes=90)


class Session(BaseModel):
    """One sitting: the lines written between two long gaps.

    The lines stay in the order they were written. A sitting is usually one
    passage worked through from the top, so that order is most of its sense --
    which word was looked up to understand which line, and what was asked next.
    The counts are only for saying what the sitting held.
    """

    session_id: str = Field(description="When the first line was written, UTC ISO 8601")
    started_at: str = Field(description="When the first line was written, UTC ISO 8601")
    ended_at: str = Field(description="When the last line was written, UTC ISO 8601")
    languages: tuple[str, ...] = Field(
        default=(), description="The languages the lines were answered in"
    )
    rows: tuple[Row, ...] = Field(
        default=(), description="Every line of the sitting, oldest first"
    )
    line_count: int = Field(description="How many lines the sitting holds in all")
    looked_up_count: int = Field(description="How many were words or phrases looked up")
    checked_count: int = Field(description="How many were lines written to be checked")
    asked_count: int = Field(description="How many were questions")


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


def _mode_of(row: Row) -> str | None:
    analysis: Analysis | None = row.analysis
    return None if analysis is None else analysis.mode


def _as_session(rows: list[Row]) -> Session:
    languages: list[str] = []
    for row in rows:
        if row.language is not None and row.language not in languages:
            languages.append(row.language)
    return Session(
        session_id=rows[0].created_at,
        started_at=rows[0].created_at,
        ended_at=rows[-1].created_at,
        languages=tuple(languages),
        rows=tuple(rows),
        line_count=len(rows),
        # "translate" counts with the lookups: both are the student reaching for
        # a word they did not have, only in opposite directions.
        looked_up_count=sum(1 for r in rows if _mode_of(r) in ("gloss", "translate")),
        checked_count=sum(1 for r in rows if _mode_of(r) == "assess"),
        asked_count=sum(1 for r in rows if _mode_of(r) == "answer"),
    )


def group_into_sessions(notebook: Notebook, cleared: ClearedLog) -> tuple[Session, ...]:
    """Cut everything written so far into sittings, the most recent first."""
    reviewable = [row for row in all_rows(notebook, cleared) if _is_worth_reviewing(row)]
    if not reviewable:
        return ()

    sittings: list[list[Row]] = [[reviewable[0]]]
    for row in reviewable[1:]:
        if _written_at(row) - _written_at(sittings[-1][-1]) > SESSION_GAP:
            sittings.append([row])
        else:
            sittings[-1].append(row)

    return tuple(reversed([_as_session(sitting) for sitting in sittings]))
