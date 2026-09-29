import os
from collections.abc import Sequence
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Final

from flask import Flask
from flask import Response
from flask import jsonify
from flask import request
from loguru import logger
from werkzeug.serving import run_simple

from rosette.credentials import CredentialStorageError
from rosette.credentials import clear_api_key
from rosette.credentials import describe_credentials
from rosette.credentials import read_api_key
from rosette.credentials import store_api_key
from rosette.data_types import Analysis
from rosette.data_types import Row
from rosette.notebook import NotebookStorageError
from rosette.notebook import NotebookStore
from rosette.notebook import now_utc_iso
from rosette.settings import SettingsStore
from rosette.tutor import CONTEXT_LINE_COUNT
from rosette.tutor import TUTOR_MODEL
from rosette.tutor import PrecedingLine
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


def _summarize_answer(row: Row) -> str | None:
    """The gist of what the tutor said about a row, for showing as context."""
    analysis = row.analysis
    if analysis is None:
        return None
    if analysis.mode == "translate":
        return analysis.italian
    if analysis.mode == "gloss":
        return analysis.english
    if analysis.mode == "answer":
        return analysis.answer
    if analysis.verdict == "correct":
        return f"correct ({analysis.note})" if analysis.note else "correct"
    corrected = "".join(s.text for s in analysis.segments if s.kind != "wrong")
    return f"corrected to: {corrected} ({analysis.note})" if corrected else analysis.note


def _rows_above(rows: Sequence[Row], row_id: str) -> list[tuple[int, Row]]:
    """The written rows above one, paired with how far above it each sits."""
    index = next((i for i, r in enumerate(rows) if r.row_id == row_id), None)
    if index is None:
        return []
    above: list[tuple[int, Row]] = []
    for row in reversed(rows[:index]):
        if row.text.strip() == "":
            continue
        above.append((len(above) + 1, row))
        if len(above) >= CONTEXT_LINE_COUNT:
            break
    return above


def _preceding_lines(rows: Sequence[Row], row_id: str) -> list[PrecedingLine]:
    return [
        PrecedingLine(
            distance=distance, text=row.text, answer_summary=_summarize_answer(row)
        )
        for distance, row in _rows_above(rows, row_id)
    ]


def _row_ids_at_distances(
    rows: Sequence[Row], row_id: str, distances: Sequence[int]
) -> list[str]:
    """Resolve "N lines above this one" back to the rows the tutor meant."""
    row_by_distance = {distance: row for distance, row in _rows_above(rows, row_id)}
    resolved: list[str] = []
    for distance in distances:
        row = row_by_distance.get(distance)
        if row is not None and row.row_id not in resolved:
            resolved.append(row.row_id)
    return resolved


def _submit_answer(row_id: str, line: str, language: str) -> None:
    """Queue a line for answering, with its failure guaranteed to reach the row.

    Nothing reads the future a pool task runs in, so an exception raised inside
    one would vanish and leave the row saying "Reading that..." for good. The
    callback below asks the finished task for its exception rather than
    catching one, so an unforeseen failure still lands on the row where the
    student can see it and try again.
    """
    future = _tutor_pool.submit(_answer_row, row_id, line, language)
    future.add_done_callback(
        partial(_record_unexpected_failure, row_id, line)
    )


def _record_unexpected_failure(row_id: str, line: str, future: Future) -> None:
    error = future.exception()
    if error is None:
        return
    logger.opt(exception=error).error(
        "Unexpected failure answering a line (row_id={})", row_id
    )
    _store_tutor_failure(row_id, line, f"{type(error).__name__}: {error}")


def _answer_row(row_id: str, line: str, language: str) -> None:
    notebook = _notebook_store.load()
    settings = _settings_store.load()
    preceding = _preceding_lines(notebook.rows, row_id)
    try:
        analysis, result = analyze_line(
            line,
            language,
            preceding,
            settings.known_facts,
            read_api_key(DATA_DIR).api_key,
        )
    except RosetteError as e:
        logger.warning("Failed to answer line (row_id={}): {}", row_id, e)
        _store_tutor_failure(row_id, line, str(e))
        return

    if analysis.learned and analysis.learned.strip():
        _settings_store.remember_fact(analysis.learned)
        logger.info("Learned from a question: {}", analysis.learned)

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
    _requeue_the_lines_that_fact_changes(row_id, analysis, language)


def _requeue_the_lines_that_fact_changes(
    row_id: str, analysis: Analysis, language: str
) -> None:
    """Re-read the earlier lines a newly-learned fact changes the reading of."""
    if not analysis.learned or not analysis.revisit_distances:
        return
    notebook = _notebook_store.load()
    for target_id in _row_ids_at_distances(
        notebook.rows, row_id, analysis.revisit_distances
    ):
        target = next((r for r in notebook.rows if r.row_id == target_id), None)
        # Only a line that already has an answer is worth re-reading; one still
        # waiting will pick the new fact up when its own turn comes.
        if target is None or target.status != "done":
            continue
        _store_row_update(target_id, target.text, {"status": "pending"})
        _submit_answer(target_id, target.text, language)


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
    settings = _settings_store.load()
    return jsonify(
        {
            "version": version,
            "known_facts": list(settings.known_facts),
            "credentials": describe_credentials(DATA_DIR).model_dump(mode="json"),
            **notebook.model_dump(mode="json"),
        }
    )


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

    # An edited line keeps the answer it already had until the new one arrives,
    # rather than emptying the margin the moment a key is pressed: the previous
    # answer is still the most useful thing to have on screen while the tutor
    # re-reads the line, and blanking it makes the page flicker on every edit.
    # A line emptied to nothing is the exception -- there is nothing left to
    # have answered.
    is_keeping_previous_answer = not is_blank and stored_row is not None
    updated_row = Row(
        row_id=row_id,
        text=line,
        status="empty" if is_blank else "pending",
        analysis=stored_row.analysis if is_keeping_previous_answer else None,
        raw_response=stored_row.raw_response if is_keeping_previous_answer else None,
        error=None,
        model=stored_row.model if is_keeping_previous_answer else None,
        language=stored_row.language if is_keeping_previous_answer else None,
        cost_usd=stored_row.cost_usd if is_keeping_previous_answer else None,
        created_at=stored_row.created_at if stored_row else now_utc_iso(),
        analyzed_at=stored_row.analyzed_at if is_keeping_previous_answer else None,
    )
    _notebook_store.upsert_row(updated_row, after_row_id)
    if not is_blank:
        language = _settings_store.load().language
        _submit_answer(row_id, line, language)
    return jsonify(updated_row.model_dump(mode="json"))


@app.route("/api/rows/<row_id>", methods=["DELETE"])
def delete_row(row_id: str) -> Response:
    return jsonify(_notebook_store.delete_row(row_id).model_dump(mode="json"))


@app.route("/api/rows/<row_id>/and-below", methods=["DELETE"])
def delete_row_and_below(row_id: str) -> Response:
    """Drop a line and everything under it, with a way to put them back."""
    notebook, archive_name = _notebook_store.delete_row_and_below(row_id)
    return jsonify(
        {"undo": archive_name, **notebook.model_dump(mode="json")}
    )


@app.route("/api/notebook/restore", methods=["POST"])
def restore_notebook() -> Response:
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("undo"), str):
        return Response('{"error": "expected a JSON body with an undo string"}', 400)
    try:
        notebook = _notebook_store.restore_archive(body["undo"])
    except NotebookStorageError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(notebook.model_dump(mode="json"))


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
    if not isinstance(body, dict):
        return Response('{"error": "expected a JSON body"}', 400)
    # Taking a fact back matters as much as learning one: a wrong fact would
    # quietly skew every line judged after it.
    if isinstance(body.get("forget"), str):
        return jsonify(
            _settings_store.forget_fact(body["forget"]).model_dump(mode="json")
        )
    if not isinstance(body.get("language"), str):
        return Response('{"error": "expected a language or a fact to forget"}', 400)
    return jsonify(_settings_store.set_language(body["language"]).model_dump(mode="json"))


@app.route("/api/credentials", methods=["GET", "PUT", "DELETE"])
def credentials() -> Response:
    """Report, set, or remove the API key. The key itself is never sent back."""
    if request.method == "GET":
        return jsonify(describe_credentials(DATA_DIR).model_dump(mode="json"))
    if request.method == "DELETE":
        return jsonify(clear_api_key(DATA_DIR).model_dump(mode="json"))
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("api_key"), str):
        return Response('{"error": "expected a JSON body with an api_key string"}', 400)
    try:
        status = store_api_key(DATA_DIR, body["api_key"])
    except CredentialStorageError as e:
        # The message is shown to the student, so it must not echo the key.
        return jsonify({"error": str(e)}), 400
    logger.info("Stored an API key; replies now go straight to the API")
    return jsonify(status.model_dump(mode="json"))


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def _resume_lines_left_waiting() -> None:
    """Re-ask about any line that was mid-answer when the app last stopped.

    A restart kills whatever the pool was working on, and nothing else would
    ever pick those rows up again -- they would sit on "Reading that..."
    forever, which is exactly what happened to a real line. Anything still
    pending at startup therefore goes back in the queue.
    """
    notebook = _notebook_store.load()
    waiting = [r for r in notebook.rows if r.status == "pending" and r.text.strip()]
    if not waiting:
        return
    language = _settings_store.load().language
    logger.info("Resuming {} line(s) left unanswered by a restart", len(waiting))
    for row in waiting:
        _submit_answer(row.row_id, row.text, language)


def main() -> None:
    _resume_lines_left_waiting()
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
