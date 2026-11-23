"""Rosette's data, shaped once, for everything that reads it back.

Run by the workspace's Data app as ``python -m rosette.datasets``, and by Rosette's own
review page through ``build_datasets()``. One shaping step, two readers: the review page
cannot drift from what a query over ``rosette.lines`` in the Data app says, because both are
reading what this module produced.

What is here and why:

- ``cleared``, ``notebook`` and ``settings`` are the records exactly as the app stores them.
  They are the raw source; nothing below replaces them.
- ``lines`` is every line worth reading back -- the two stores folded together, oldest first,
  one row per line. It carries the one thing a query cannot work out for itself: which
  **sitting** the line belongs to. A sitting is a stretch of writing with no gap longer than
  ``SESSION_GAP`` in it, and finding those gaps means walking the lines in order, comparing
  each to the one before. Everything else the review page shows -- which lines count, how many
  were looked up, when a sitting began -- is a query over these rows, not code here.
"""

import json
import sys
from pathlib import Path

from rosette.data_types import Analysis, Row
from rosette.notebook import CLEARED_FILENAME, DATA_DIR, NOTEBOOK_FILENAME, NotebookStore
from rosette.sessions import SESSION_GAP, sittings_of
from rosette.settings import SETTINGS_FILENAME, SettingsStore

# A key used in a clickable link must not contain a hyphen: the query page reads what follows
# one as an expression. Row ids are minted in the browser and do contain them.
def key_of(row_id: str) -> str:
    return row_id.replace("-", "_")


def _answer_text(analysis: Analysis | None) -> str:
    """What came back, as one readable line -- whichever kind of help it was."""
    if analysis is None:
        return ""
    if analysis.mode == "assess":
        return "".join(
            segment.text for segment in analysis.segments if segment.kind != "wrong"
        )
    return analysis.italian or analysis.english or analysis.answer or ""


def _line_row(row: Row, sitting: str, source: Path) -> dict:
    # Every line here came through ``sittings_of``, which only yields lines that got an answer.
    analysis = row.analysis
    assert analysis is not None
    return {
        "id": key_of(row.row_id),
        "name": row.text.strip(),
        "sitting": sitting,
        # The raw mode, not a friendlier word for it: a view that wants "looked up" says so
        # by asking for the two modes that mean it, where the asking can be read.
        "mode": analysis.mode,
        "language": row.language or "",
        "verdict": analysis.verdict or "",
        "answer": _answer_text(analysis),
        "note": analysis.note,
        "cost_usd": row.cost_usd,
        "model": row.model or "",
        # The tutor's reply as it arrived and as it was read, kept so the page can show the
        # line the way the notebook drew it, and show the unparsed original behind it.
        "analysis": analysis.model_dump(mode="json"),
        "raw_response": row.raw_response,
        "written": row.created_at,
        "answered": row.analyzed_at,
        "ts": row.created_at,
        "source": str(source),
    }


def line_rows(data_dir: Path = DATA_DIR) -> list[dict]:
    """Every line worth reading back, oldest first, each tagged with its sitting."""
    store = NotebookStore(data_dir)
    notebook, cleared = store.load(), store.load_cleared()
    on_the_page = {row.row_id for row in notebook.rows}
    rows = []
    for sitting, row in sittings_of(notebook, cleared):
        source = data_dir / (
            NOTEBOOK_FILENAME if row.row_id in on_the_page else CLEARED_FILENAME
        )
        rows.append(_line_row(row, sitting, source))
    return rows


def _stored(path: Path) -> dict:
    """One of the app's stored files as it sits on disk, or nothing when it is not there yet."""
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def build_datasets(data_dir: Path = DATA_DIR) -> dict:
    notebook = _stored(data_dir / NOTEBOOK_FILENAME)
    cleared = _stored(data_dir / CLEARED_FILENAME)
    settings = _stored(data_dir / SETTINGS_FILENAME)
    item = {
        "id": "rosette",
        "name": "Rosette",
        "lines": line_rows(data_dir),
        "notebook": notebook.get("rows", []),
        "cleared": cleared.get("rows", []),
        "settings": settings,
    }
    return {
        "datasets": {
            "rosette": {
                "about": (
                    "Every line written in the notebook and what the tutor said back: "
                    "'lines' is all of them, oldest first, each one tagged with the sitting "
                    "it was written in; 'notebook' and 'cleared' are the two stores those "
                    "lines are read from, exactly as the app keeps them."
                ),
                "item": item,
            }
        },
        "queries": SAVED_VIEWS,
        "includes_app_records": True,
    }


# How a sitting is found, said once, where anyone reading a view can see it.
SITTING_NOTE = (
    f"???note=[ A sitting is a stretch of writing with no gap longer than "
    f"{int(SESSION_GAP.total_seconds() // 60)} minutes in it. Which sitting a line belongs to "
    f"is worked out when the data is shaped, because finding the gaps means walking the lines "
    f"in order; everything else here is this query. ]"
)

# The views the review page runs, and the same ones the Data app offers under "project:
# rosette". The page renders the result of these; it does not work any of it out again.
SITTINGS_QUERY = (
    "rosette.lines/sitting#-sitting"
    # Looking a word up is reaching for one you did not have, in either direction: a single
    # Italian word you wanted the meaning of, or English you wanted the Italian for.
    "|looked_up=~(.of:(.=mode:=gloss,=translate)....count)"
    "|checked=~(.of:mode=assess....count)"
    "|asked=~(.of:mode=answer....count)"
    "|started=~(.of#written:@1.=written)"
    "|ended=~(.of#-written:@1.=written)"
    f"\n{SITTING_NOTE}"
)
# One sitting's lines, in the order they were written: a sitting is usually one passage worked
# through from the top, and sorting it by what was asked of each line takes that away.
LINES_IN_A_SITTING_QUERY = "rosette.lines:sitting={sitting}#written"

SAVED_VIEWS = [
    {
        "name": "Rosette sittings, most recent first",
        "query": SITTINGS_QUERY,
        "hide": [],
    },
    {
        "name": "Rosette lines, most recent first",
        "query": "rosette.lines#-written",
        "hide": ["analysis", "raw_response", "note", "source"],
    },
    {
        "name": "Rosette lines that needed fixing",
        "query": (
            "rosette.lines:verdict=fix#-written"
            "\n???note=[ A line the tutor read as Italian and had to change. ]"
        ),
        "hide": ["analysis", "raw_response", "source"],
    },
    {
        "name": "Rosette words looked up",
        "query": (
            "rosette.lines:(.=mode:=gloss,=translate)#-written"
            "\n???note=[ Reaching for a word you did not have, in either direction: a single "
            "Italian word you wanted the meaning of, or English you wanted the Italian for. ]"
        ),
        "hide": ["analysis", "raw_response", "note", "source"],
    },
]


def main() -> None:
    json.dump(build_datasets(), sys.stdout)


if __name__ == "__main__":
    main()
