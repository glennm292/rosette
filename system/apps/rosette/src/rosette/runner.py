import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Final

from flask import Flask
from flask import Response
from flask import jsonify
from flask import request
from loguru import logger
from werkzeug.serving import run_simple

from rosette.data_types import Row
from rosette.notebook import NotebookStore
from rosette.notebook import now_utc_iso
from rosette.settings import SettingsStore
from rosette.tutor import TUTOR_MODEL
from rosette.tutor import RosetteError
from rosette.tutor import analyze_line

ASSETS_DIR = Path(__file__).parent / "assets"

# Persistent state for this app lives under DATA_DIR. It defaults to
# ``data/.apps/rosette/`` but is overridable via the
# ``ROSETTE_DATA_DIR`` env var so a throwaway instance can run against a
# copy of the data while editing -- see the update-app skill.
DATA_DIR = Path(os.environ.get("ROSETTE_DATA_DIR", "data/.apps/rosette"))

# Listen port. Overridable via ``ROSETTE_PORT`` so an editing agent can
# boot a throwaway instance on a spare port next to the live one.
PORT = int(os.environ.get("ROSETTE_PORT", "8082"))

# Each reply is one `claude -p` subprocess taking a few seconds. A student
# typing quickly can leave several lines waiting at once, so replies run in
# parallel -- but the pool stays small, since these are whole subprocesses and
# an unbounded burst would starve the machine rather than answer sooner.
TUTOR_WORKER_COUNT: Final[int] = 4

# How long a waiting page is held before being answered with the notebook
# unchanged. Short enough that a dropped connection is noticed and renewed,
# long enough that a quiet notebook is not reconnecting constantly.
CHANGE_WAIT_SECONDS: Final[float] = 25.0

app = Flask("rosette", static_folder=None)

_notebook_store = NotebookStore(DATA_DIR)
_settings_store = SettingsStore(DATA_DIR)
_tutor_pool = ThreadPoolExecutor(
    max_workers=TUTOR_WORKER_COUNT, thread_name_prefix="tutor"
)


def _answer_row_in_background(row_id: str, line: str, language: str) -> None:
    """Ask the tutor about one line and record the answer against that line."""
    try:
        analysis, result = analyze_line(line, language)
    except RosetteError as e:
        logger.warning("Failed to answer line (row_id={}): {}", row_id, e)
        _store_tutor_failure(row_id, line, str(e))
        return

    _store_row_update(
        row_id,
        line,
        {
            "status": "done",
            "analysis": analysis,
            "raw_response": result.text,
            "error": None,
            "model": TUTOR_MODEL,
            "language": language,
            "cost_usd": result.cost_usd,
            "analyzed_at": now_utc_iso(),
        },
    )


def _store_tutor_failure(row_id: str, line: str, message: str) -> None:
    _store_row_update(
        row_id,
        line,
        {
            "status": "error",
            "error": message,
            "analysis": None,
            "analyzed_at": now_utc_iso(),
        },
    )


def _store_row_update(row_id: str, expected_text: str, changes: dict[str, object]) -> None:
    """Apply an answer to a row, unless the line changed while it was waiting."""
    notebook = _notebook_store.load()
    stored_row = next((r for r in notebook.rows if r.row_id == row_id), None)
    if stored_row is None:
        return
    _notebook_store.replace_row_if_text_unchanged(
        stored_row.model_copy(update=changes), expected_text
    )


@app.route("/")
def index() -> Response:
    # The page carries the location beacon, so the workspace shell can reopen
    # this app's tab at the place it was showing.
    return Response(
        (ASSETS_DIR / "index.html").read_text(encoding="utf-8"),
        mimetype="text/html",
    )


@app.route("/api/notebook")
def get_notebook() -> Response:
    """The notebook now, or -- given a `since` -- once it differs from that.

    The page holds one of these open, so an answer reaches it the moment it is
    stored rather than on the next tick of a poll. The wait is bounded so the
    request still returns on a quiet notebook and the connection is renewed.
    """
    since_argument = request.args.get("since")
    if since_argument is None:
        version, notebook = _notebook_store.load_version()
    else:
        try:
            seen_version = int(since_argument)
        except ValueError:
            return Response('{"error": "since must be a whole number"}', 400)
        version, notebook = _notebook_store.load_changes_since(
            seen_version, CHANGE_WAIT_SECONDS
        )
    return jsonify({"version": version, **notebook.model_dump(mode="json")})


@app.route("/api/rows/<row_id>", methods=["PUT"])
def put_row(row_id: str) -> Response:
    """Save what a line says now, and ask the tutor about it unless it is blank."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("text"), str):
        return Response('{"error": "expected a JSON body with a text string"}', 400)
    line = body["text"]
    after_row_id = body.get("after") if isinstance(body.get("after"), str) else None

    notebook = _notebook_store.load()
    stored_row = next((r for r in notebook.rows if r.row_id == row_id), None)

    # A blank line is a place to type, not a question -- it is kept so the row
    # holds its place on the page, but nothing is asked about it.
    is_blank = line.strip() == ""
    # Re-asking an unchanged line would spend a call to get the same answer.
    is_unchanged = stored_row is not None and stored_row.text == line
    if is_unchanged and stored_row is not None and stored_row.status != "error":
        return jsonify(stored_row.model_dump(mode="json"))

    updated_row = Row(
        row_id=row_id,
        text=line,
        status="empty" if is_blank else "pending",
        analysis=None,
        raw_response=None,
        error=None,
        model=None,
        cost_usd=None,
        created_at=stored_row.created_at if stored_row else now_utc_iso(),
        analyzed_at=None,
    )
    _notebook_store.upsert_row(updated_row, after_row_id)
    if not is_blank:
        language = _settings_store.load().language
        _tutor_pool.submit(_answer_row_in_background, row_id, line, language)
    return jsonify(updated_row.model_dump(mode="json"))


@app.route("/api/rows/<row_id>", methods=["DELETE"])
def delete_row(row_id: str) -> Response:
    return jsonify(_notebook_store.delete_row(row_id).model_dump(mode="json"))


@app.route("/api/notebook", methods=["DELETE"])
def clear_notebook() -> Response:
    """Empty the notebook. The cleared one is kept in the archive."""
    return jsonify(_notebook_store.clear().model_dump(mode="json"))


@app.route("/api/settings", methods=["GET", "PUT"])
def settings() -> Response:
    """Read the notebook's settings, or set the language it is being kept in."""
    if request.method == "GET":
        return jsonify(_settings_store.load().model_dump(mode="json"))
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("language"), str):
        return Response('{"error": "expected a JSON body with a language string"}', 400)
    return jsonify(_settings_store.set_language(body["language"]).model_dump(mode="json"))


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
