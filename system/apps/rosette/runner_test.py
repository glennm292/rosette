import importlib
import json
import time
from pathlib import Path

import pytest
import rosette.runner

from rosette.credentials import API_KEY_FILENAME


@pytest.fixture
def runner_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The runner bound to a throwaway data directory.

    Its stores are built at import time from the data directory, so the
    override has to be in place before the module is reloaded.
    """
    monkeypatch.setenv("ROSETTE_DATA_DIR", str(tmp_path))
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


def test_an_undo_token_cannot_reach_outside_the_notebook_archive(
    runner_module,
) -> None:
    client = runner_module.app.test_client()
    refused = client.post(
        "/api/notebook/restore", json={"undo": "../../../etc/passwd"}
    )
    assert refused.status_code == 400
