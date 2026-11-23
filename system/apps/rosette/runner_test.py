import importlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import rosette.notebook
import rosette.runner
from rosette.credentials import API_KEY_FILENAME
from rosette.data_types import Analysis, Row
from rosette.notebook import now_utc_iso


@pytest.fixture
def runner_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The runner bound to a throwaway data directory.

    The data directory is read from the environment when ``notebook`` is imported, and the
    runner's stores are built from it when the runner is, so both are reloaded, in that
    order, with the override in place.
    """
    monkeypatch.setenv("ROSETTE_DATA_DIR", str(tmp_path))
    importlib.reload(rosette.notebook)
    return importlib.reload(rosette.runner)


def _find_row(notebook, row_id: str):
    return next((r for r in notebook.rows if r.row_id == row_id), None)


def _wait_for_status(runner_module, row_id: str, timeout_seconds: float = 5.0) -> str:
    """The row's status once it stops being pending.

    Waits on the notebook's own change signal rather than polling, so this is
    as quick as the work is and never races on a sleep interval.
    """
    deadline = time.monotonic() + timeout_seconds
    version, notebook = runner_module._notebook_store.load_version()
    row = _find_row(notebook, row_id)
    while (row is None or row.status == "pending") and time.monotonic() < deadline:
        version, notebook = runner_module._notebook_store.load_changes_since(
            version, deadline - time.monotonic()
        )
        row = _find_row(notebook, row_id)
    return "pending" if row is None else row.status


def test_an_unexpected_failure_lands_on_the_row_rather_than_hanging_it(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bug in the answering path must surface, not leave the line waiting.

    Nothing reads the future a pool task runs in, so before this was handled an
    exception raised inside one vanished and the row said "Reading that..."
    for good -- which is exactly how a missing constructor argument reached a
    user.
    """

    def _explode(row_id: str, line: str, language: str) -> None:
        raise TypeError("a bug of exactly the kind that once hung a line")

    monkeypatch.setattr(runner_module, "_answer_row", _explode)

    client = runner_module.app.test_client()
    response = client.put("/api/rows/r1", json={"text": "Ieri ho andato al mercato."})
    assert response.status_code == 200

    assert _wait_for_status(runner_module, "r1") == "error"
    stored = next(
        r for r in runner_module._notebook_store.load().rows if r.row_id == "r1"
    )
    assert stored.error is not None
    assert "TypeError" in stored.error


def test_a_blank_line_is_never_sent_to_the_tutor(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def _record(row_id: str, line: str, language: str) -> None:
        asked.append(line)

    monkeypatch.setattr(runner_module, "_answer_row", _record)

    client = runner_module.app.test_client()
    client.put("/api/rows/blank", json={"text": "   "})

    assert asked == []
    stored = next(
        r for r in runner_module._notebook_store.load().rows if r.row_id == "blank"
    )
    assert stored.status == "empty"


def test_the_api_key_is_never_sent_back_to_the_page(runner_module) -> None:
    """The page is told the route and a hint, never the key itself."""
    key = "sk-ant-api03-" + "x" * 40
    client = runner_module.app.test_client()

    stored = client.put("/api/credentials", json={"api_key": key})
    assert stored.status_code == 200
    assert key not in json.dumps(stored.get_json())
    assert stored.get_json()["route"] == "key"

    for path in ("/api/credentials", "/api/notebook"):
        body = json.dumps(client.get(path).get_json())
        assert key not in body, f"the key leaked through {path}"


def test_an_implausible_api_key_is_refused_and_not_stored(runner_module) -> None:
    client = runner_module.app.test_client()
    refused = client.put("/api/credentials", json={"api_key": "not-a-key"})

    assert refused.status_code == 400
    assert client.get("/api/credentials").get_json()["route"] == "mind"


def test_the_stored_key_is_readable_only_by_its_owner(
    runner_module, tmp_path: Path
) -> None:
    client = runner_module.app.test_client()
    client.put("/api/credentials", json={"api_key": "sk-ant-api03-" + "y" * 40})

    mode = (tmp_path / API_KEY_FILENAME).stat().st_mode
    assert mode & 0o077 == 0, "the key file is readable by someone other than its owner"


def test_a_key_that_cannot_be_removed_is_reported_not_a_bare_error(
    runner_module, tmp_path: Path
) -> None:
    client = runner_module.app.test_client()
    # A directory where the key file belongs cannot be unlinked as a file.
    (tmp_path / API_KEY_FILENAME).mkdir()

    refused = client.delete("/api/credentials")

    assert refused.status_code == 500
    assert "Cannot remove the stored key" in refused.get_json()["error"]


def test_editing_a_line_keeps_its_previous_answer_until_the_new_one_lands(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "_answer_row", lambda *args: None)
    client = runner_module.app.test_client()

    client.put("/api/rows/r1", json={"text": "Ieri sono andato al mercato."})
    runner_module._store_row_update(
        "r1",
        "Ieri sono andato al mercato.",
        {
            "status": "done",
            "analysis": runner_module.Analysis(
                mode="assess", verdict="correct", note="Fine as written."
            ),
        },
    )

    client.put("/api/rows/r1", json={"text": "Ieri sono andato al mercato in bici."})
    edited = next(
        r for r in runner_module._notebook_store.load().rows if r.row_id == "r1"
    )
    assert edited.status == "pending"
    assert edited.analysis is not None, "the previous answer was dropped on edit"
    assert edited.analysis.note == "Fine as written."


def test_emptying_a_line_drops_its_answer(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "_answer_row", lambda *args: None)
    client = runner_module.app.test_client()

    client.put("/api/rows/r1", json={"text": "sciopero"})
    runner_module._store_row_update(
        "r1",
        "sciopero",
        {
            "status": "done",
            "analysis": runner_module.Analysis(
                mode="gloss", english="strike", note="Masculine noun."
            ),
        },
    )

    client.put("/api/rows/r1", json={"text": ""})
    emptied = next(
        r for r in runner_module._notebook_store.load().rows if r.row_id == "r1"
    )
    assert emptied.status == "empty"
    assert emptied.analysis is None


def test_cutting_from_a_line_removes_it_and_everything_below(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "_answer_row", lambda *args: None)
    client = runner_module.app.test_client()
    for index, text in enumerate(["keep one", "keep two", "cut here", "below", "also below"]):
        client.put(f"/api/rows/r{index}", json={"text": text})

    cut = client.delete("/api/rows/r2/and-below")

    assert cut.status_code == 200
    kept = [r["text"] for r in cut.get_json()["rows"]]
    assert kept == ["keep one", "keep two"]
    assert cut.get_json()["undo"], "nothing was kept to undo with"


def test_an_undone_cut_puts_the_lines_back_with_their_answers(
    runner_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner_module, "_answer_row", lambda *args: None)
    client = runner_module.app.test_client()
    client.put("/api/rows/r0", json={"text": "keep"})
    client.put("/api/rows/r1", json={"text": "sciopero"})
    runner_module._store_row_update(
        "r1",
        "sciopero",
        {
            "status": "done",
            "analysis": runner_module.Analysis(
                mode="gloss", english="strike", note="Masculine noun."
            ),
        },
    )

    undo_token = client.delete("/api/rows/r1/and-below").get_json()["undo"]
    restored = client.post("/api/notebook/restore", json={"undo": undo_token})

    assert restored.status_code == 200
    rows = restored.get_json()["rows"]
    assert [r["text"] for r in rows] == ["keep", "sciopero"]
    assert rows[1]["analysis"]["english"] == "strike", "the answer was lost on the way back"


def test_an_unknown_undo_token_is_refused(
    runner_module,
) -> None:
    client = runner_module.app.test_client()
    refused = client.post(
        "/api/notebook/restore", json={"undo": "../../../etc/passwd"}
    )
    assert refused.status_code == 400


def test_the_review_page_reads_both_what_is_on_the_page_and_what_was_cleared(
    runner_module,
) -> None:
    """Cleared lines are most of a past sitting, so review must read them too."""
    client = runner_module.app.test_client()
    client.put("/api/rows/kept", json={"text": ""})

    store = runner_module._notebook_store
    store.upsert_row(
        Row(
            row_id="put-away",
            text="asino",
            status="done",
            analysis=Analysis(mode="gloss", english="donkey"),
            created_at=now_utc_iso(),
        ),
        None,
    )
    store.delete_row_and_below("put-away")

    sittings = client.get("/api/review/sittings").get_json()["rows"]

    assert len(sittings) == 1
    lines = client.get(f"/api/review/lines?sitting={sittings[0]['name']}").get_json()["rows"]
    assert [row["id"] for row in lines] == ["put_away"]


def test_the_review_page_shows_what_the_query_says_and_says_which_query(
    runner_module,
) -> None:
    """The two halves of the mirror: the page runs the published query, and offers to open it."""
    client = runner_module.app.test_client()
    store = runner_module._notebook_store
    for row_id, mode, english in (("a", "gloss", "donkey"), ("b", "assess", None)):
        store.upsert_row(
            Row(
                row_id=row_id,
                text="asino",
                status="done",
                analysis=Analysis(mode=mode, english=english, verdict=None),
                created_at=now_utc_iso(),
            ),
            None,
        )

    answered = client.get("/api/review/sittings").get_json()

    # The counts are the query's, not a second tally in Python.
    (sitting,) = answered["rows"]
    assert (sitting["looked_up"], sitting["checked"], sitting["asked"]) == (1, 1, 0)
    # And the query that produced them comes back with them, for "see the query".
    assert answered["query"] == runner_module.SITTINGS_QUERY


def test_a_sitting_that_is_not_a_sitting_is_refused(runner_module) -> None:
    """The name goes into a query; nothing that is not a sitting's name may."""
    answered = runner_module.app.test_client().get("/api/review/lines?sitting=rosette.lines")

    assert answered.status_code == 400


def test_the_review_page_is_served(runner_module) -> None:
    response = runner_module.app.test_client().get("/review")

    assert response.status_code == 200
    assert "/api/review/sittings" in response.get_data(as_text=True)


def test_both_pages_draw_a_line_with_the_one_shared_formatter(runner_module) -> None:
    """Three views draw a line now; a second copy of the drawing code is how they drift apart."""
    client = runner_module.app.test_client()

    formatter = client.get("/row_format.js")
    assert formatter.status_code == 200
    assert "RosetteFormat" in formatter.get_data(as_text=True)

    for path in ("/", "/review"):
        page = client.get(path).get_data(as_text=True)
        assert '<script src="/row_format.js">' in page, path
        assert "RosetteFormat.answer(" in page, path
        # The marking of a corrected line used to be written out in both pages.
        assert 'kind === "wrong"' not in page, path


def test_the_query_pages_renderer_asks_for_the_shared_formatter(runner_module) -> None:
    """The renderer the Data app loads must reach the same formatter, not carry its own."""
    renderer = (runner_module.ASSETS_DIR / "dactal_render.js").read_text(encoding="utf-8")

    assert 'import "./row_format.js";' in renderer
    assert "add_renderer([\"rosette\"]" in renderer


def _unfinished_check_source(runner_module) -> str:
    """The page's own half-written-line check, lifted out so it can be run on its own."""
    page = (runner_module.ASSETS_DIR / "index.html").read_text(encoding="utf-8")
    start = page.index("const FINISHED_TAIL")
    end = page.index("function queueSend(", start)
    return page[start:end]


def _number_in(page: str, name: str) -> int:
    found = re.search(rf"const {name} = (\d+);", page)
    assert found is not None, f"{name} is not in the page"
    return int(found.group(1))


def test_the_page_waits_longer_for_a_line_that_is_still_being_written(runner_module) -> None:
    """The patient wait has to be the longer one, or the check makes things worse, not better."""
    page = (runner_module.ASSETS_DIR / "index.html").read_text(encoding="utf-8")
    sends = _number_in(page, "IDLE_SEND_MS")
    unfinished = _number_in(page, "IDLE_UNFINISHED_MS")

    assert unfinished > sends
    assert "looksUnfinished(text) ? Math.max(settled, IDLE_UNFINISHED_MS) : settled" in page


@pytest.mark.parametrize(
    "line, still_writing",
    [
        ("Vorrei andare a", True),  # a preposition with nothing after it
        ("Mi piace il", True),  # an article with nothing after it
        ("Non ho", True),
        ("Vorrei andare ", True),  # a space typed: the next word is coming
        ("Ho mangiato,", True),
        ("l'", True),  # an elided article
        ("Ieri sono andato al mercato", False),
        ("Ieri sono andato al mercato.", False),
        ("Come stai?", False),
        ("Buongiorno", False),
        ("Ciao. ", False),  # finished, with a stray space after it
        ("I want to", True),  # the shape holds outside Italian
        ("", False),
    ],
)
def test_a_half_written_line_is_recognised_by_shape(
    runner_module, line: str, still_writing: bool
) -> None:
    """Every one of these is a real pause mid-sentence, or a real place to stop."""
    if shutil.which("node") is None:
        pytest.skip("no node available to run the page's own check")

    program = (
        _unfinished_check_source(runner_module)
        + "\nprocess.stdout.write(String(looksUnfinished("
        + json.dumps(line)
        + ")));"
    )
    answer = subprocess.run(
        ["node", "--input-type=module", "-e", program],
        capture_output=True,
        text=True,
        check=True,
    )

    assert answer.stdout == ("true" if still_writing else "false")
