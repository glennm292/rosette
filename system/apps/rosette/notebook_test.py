import json
from pathlib import Path

import pytest

from rosette.data_types import Notebook
from rosette.data_types import Row
from rosette.notebook import CLEARED_FILENAME
from rosette.notebook import LEGACY_ARCHIVE_DIRNAME
from rosette.notebook import NotebookStorageError
from rosette.notebook import NotebookStore


def _row(row_id: str, text: str) -> Row:
    return Row(row_id=row_id, text=text, status="done", created_at="2026-09-29T10:00:00+00:00")


def _cleared(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / CLEARED_FILENAME).read_text(encoding="utf-8"))["rows"]


def _store_with(data_dir: Path, *texts: str) -> NotebookStore:
    store = NotebookStore(data_dir)
    previous = None
    for index, text in enumerate(texts):
        store.upsert_row(_row(f"r{index}", text), previous)
        previous = f"r{index}"
    return store


def test_every_clearing_lands_in_one_log_labelled_by_how_and_when(tmp_path: Path) -> None:
    store = _store_with(tmp_path, "keep", "cut one", "cut two")
    store.delete_row_and_below("r1")
    store.clear()

    rows = _cleared(tmp_path)
    assert [(r["text"], r["how_cleared"], r["position"]) for r in rows] == [
        ("cut one", "line and below", 1),
        ("cut two", "line and below", 2),
        ("keep", "whole notebook", 1),
    ]
    assert rows[0]["clearing"] == rows[1]["clearing"] != rows[2]["clearing"]
    assert all("-" not in r["clearing"] and r["cleared_at"] for r in rows)
    assert all(r["restored_at"] is None for r in rows)
    assert not (tmp_path / LEGACY_ARCHIVE_DIRNAME).exists()


def test_undo_puts_a_clearing_back_and_marks_it_restored(tmp_path: Path) -> None:
    store = _store_with(tmp_path, "keep", "cut")
    _, clearing = store.delete_row_and_below("r1")
    store.delete_row_and_below("r0")

    notebook = store.restore_clearing(clearing)

    assert [r.text for r in notebook.rows] == ["cut"]
    restored = {r["text"]: r["restored_at"] for r in _cleared(tmp_path)}
    assert restored["cut"] is not None
    assert restored["keep"] is None


def test_an_unknown_clearing_cannot_be_restored(tmp_path: Path) -> None:
    store = _store_with(tmp_path, "keep")
    with pytest.raises(NotebookStorageError):
        store.restore_clearing("../../../etc/passwd")


def test_old_per_clearing_files_are_folded_into_the_log(tmp_path: Path) -> None:
    archive = tmp_path / LEGACY_ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / "notebook-20260924T134944Z.json").write_text(
        Notebook(rows=(_row("a", "old whole"),)).model_dump_json(), encoding="utf-8"
    )
    (archive / "from-20260929T201651448088Z.json").write_text(
        Notebook(rows=(_row("b", "cut first"), _row("c", "cut second"))).model_dump_json(),
        encoding="utf-8",
    )

    store = NotebookStore(tmp_path)

    rows = _cleared(tmp_path)
    assert [(r["text"], r["how_cleared"], r["position"], r["clearing"]) for r in rows] == [
        ("old whole", "whole notebook", 1, "20260924T134944000000Z"),
        ("cut first", "line and below", 1, "20260929T201651448088Z"),
        ("cut second", "line and below", 2, "20260929T201651448088Z"),
    ]
    assert rows[1]["cleared_at"] == "2026-09-29T20:16:51.448088+00:00"
    assert not archive.exists(), "the old files were left behind"
    # A folded cut can still be put back, by its clearing id.
    restored = store.restore_clearing("20260929T201651448088Z")
    assert [r.text for r in restored.rows] == ["cut first", "cut second"]


def test_folding_twice_never_duplicates_lines(tmp_path: Path) -> None:
    archive = tmp_path / LEGACY_ARCHIVE_DIRNAME
    archive.mkdir()
    old = Notebook(rows=(_row("a", "old"),)).model_dump_json()
    (archive / "from-20260929T201651448088Z.json").write_text(old, encoding="utf-8")
    NotebookStore(tmp_path)
    # A copy of the same old file turning up again, e.g. restored from a backup.
    archive.mkdir()
    (archive / "from-20260929T201651448088Z.json").write_text(old, encoding="utf-8")
    NotebookStore(tmp_path)

    assert [r["text"] for r in _cleared(tmp_path)] == ["old"]
    assert not archive.exists()


def test_an_unreadable_old_file_is_left_alone(tmp_path: Path) -> None:
    archive = tmp_path / LEGACY_ARCHIVE_DIRNAME
    archive.mkdir()
    (archive / "from-20260929T201651448088Z.json").write_text("not json", encoding="utf-8")

    NotebookStore(tmp_path)

    assert (archive / "from-20260929T201651448088Z.json").exists()
    assert not (tmp_path / CLEARED_FILENAME).exists()


def test_an_unreadable_cleared_log_leaves_the_old_files_and_the_notebook_usable(
    tmp_path: Path,
) -> None:
    archive = tmp_path / LEGACY_ARCHIVE_DIRNAME
    archive.mkdir()
    old_file = archive / "from-20260929T201651448088Z.json"
    old_file.write_text(Notebook(rows=(_row("a", "old"),)).model_dump_json(), encoding="utf-8")
    (tmp_path / CLEARED_FILENAME).write_text("not json", encoding="utf-8")

    store = NotebookStore(tmp_path)
    store.upsert_row(_row("n", "still typing"), None)

    assert old_file.exists(), "an old cut was dropped while the log could not take it"
    assert (tmp_path / CLEARED_FILENAME).read_text(encoding="utf-8") == "not json"
    assert [r.text for r in store.load().rows] == ["still typing"]
