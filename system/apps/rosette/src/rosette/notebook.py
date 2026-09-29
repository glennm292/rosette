import json
import os
import tempfile
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path

from pydantic import ValidationError

from rosette.data_types import Notebook
from rosette.data_types import Row
from rosette.tutor import RosetteError


class NotebookStorageError(RosetteError, OSError):
    """Raised when the notebook on disk cannot be read or written."""


NOTEBOOK_FILENAME = "notebook.json"

# Where a cleared notebook is kept, so clearing is recoverable.
ARCHIVE_DIRNAME = "cleared"


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_notebook(notebook_path: Path) -> Notebook:
    if not notebook_path.exists():
        return Notebook()
    try:
        raw_text = notebook_path.read_text(encoding="utf-8")
    except OSError as e:
        raise NotebookStorageError(f"Cannot read notebook: {notebook_path}") from e
    try:
        return Notebook.model_validate_json(raw_text)
    except ValidationError as e:
        raise NotebookStorageError(f"Notebook is not readable: {notebook_path}") from e


def _write_notebook(notebook_path: Path, notebook: Notebook) -> None:
    # Written via a neighbouring temp file and renamed, so a crash part-way
    # through leaves the previous notebook intact rather than a truncated one.
    notebook_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(notebook_path.parent), prefix=".notebook-", suffix=".json"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            temp_file.write(notebook.model_dump_json(indent=2))
        temp_path.replace(notebook_path)
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise NotebookStorageError(f"Cannot write notebook: {notebook_path}") from e


def _rows_with_row_replaced(
    rows: tuple[Row, ...], replacement: Row
) -> tuple[Row, ...] | None:
    """The rows with one replaced in place, or None when it is not among them."""
    for index, existing_row in enumerate(rows):
        if existing_row.row_id == replacement.row_id:
            return rows[:index] + (replacement,) + rows[index + 1 :]
    return None


def _rows_with_row_inserted(
    rows: tuple[Row, ...], new_row: Row, after_row_id: str | None
) -> tuple[Row, ...]:
    if after_row_id is None:
        return rows + (new_row,)
    for index, existing_row in enumerate(rows):
        if existing_row.row_id == after_row_id:
            return rows[: index + 1] + (new_row,) + rows[index + 1 :]
    return rows + (new_row,)


class NotebookStore:
    """The notebook on disk, read and written under one lock."""

    def __init__(self, data_dir: Path) -> None:
        self._notebook_path = data_dir / NOTEBOOK_FILENAME
        self._archive_dir = data_dir / ARCHIVE_DIRNAME
        self._lock = threading.Lock()
        # Bumped on every write, so a reader can name the state it has already
        # seen and be woken the moment there is a newer one -- which is what
        # lets an answer reach the page as soon as it lands, rather than on the
        # next tick of a poll.
        self._version = 0
        self._changed = threading.Condition(self._lock)

    def load(self) -> Notebook:
        with self._lock:
            return _read_notebook(self._notebook_path)

    def load_version(self) -> tuple[int, Notebook]:
        with self._lock:
            return self._version, _read_notebook(self._notebook_path)

    def load_changes_since(
        self, seen_version: int, timeout_seconds: float
    ) -> tuple[int, Notebook]:
        """Wait for a state newer than the caller's, or give back the current one."""
        with self._changed:
            if self._version == seen_version:
                self._changed.wait(timeout=timeout_seconds)
            return self._version, _read_notebook(self._notebook_path)

    def _mark_changed(self) -> None:
        """Only valid while holding the lock."""
        self._version += 1
        self._changed.notify_all()

    def upsert_row(self, row: Row, after_row_id: str | None) -> Notebook:
        """Replace the row if the notebook already holds it, else insert it."""
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            replaced_rows = _rows_with_row_replaced(notebook.rows, row)
            updated_rows = (
                replaced_rows
                if replaced_rows is not None
                else _rows_with_row_inserted(notebook.rows, row, after_row_id)
            )
            updated_notebook = Notebook(rows=updated_rows)
            _write_notebook(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook

    def replace_row_if_text_unchanged(self, row: Row, expected_text: str) -> bool:
        """Store an answer only if the line still says what it said when asked.

        A reply takes seconds to arrive, and the student keeps typing, so an
        answer to a line they have since rewritten must be dropped rather than
        shown against the new text.
        """
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            current_row = next(
                (r for r in notebook.rows if r.row_id == row.row_id), None
            )
            if current_row is None or current_row.text != expected_text:
                return False
            replaced_rows = _rows_with_row_replaced(notebook.rows, row)
            if replaced_rows is None:
                return False
            _write_notebook(self._notebook_path, Notebook(rows=replaced_rows))
            self._mark_changed()
            return True

    def delete_row_and_below(self, row_id: str) -> tuple[Notebook, str | None]:
        """Drop a row and everything after it, keeping what was dropped aside.

        Returns the notebook and the name of the archive holding the removed
        rows, so they can be put straight back. A stretch of lines is often a
        whole worked-through passage, and losing it to one click would be worse
        than the clutter it removes.
        """
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            index = next(
                (i for i, r in enumerate(notebook.rows) if r.row_id == row_id), None
            )
            if index is None:
                return notebook, None
            removed = notebook.rows[index:]
            if not removed:
                return notebook, None
            archive_name = f"from-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}Z.json"
            _write_notebook(self._archive_dir / archive_name, Notebook(rows=removed))
            updated_notebook = Notebook(rows=notebook.rows[:index])
            _write_notebook(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook, archive_name

    def restore_archive(self, archive_name: str) -> Notebook:
        """Append an archive's rows back onto the notebook.

        Raises NotebookStorageError if that archive is not one of ours.
        """
        archive_path = (self._archive_dir / archive_name).resolve()
        # Resolved and checked, so a crafted name cannot reach outside the
        # archive directory.
        if archive_path.parent != self._archive_dir.resolve():
            raise NotebookStorageError(f"Not an archive of this notebook: {archive_name}")
        with self._lock:
            restored = _read_notebook(archive_path)
            notebook = _read_notebook(self._notebook_path)
            present = {r.row_id for r in notebook.rows}
            updated_notebook = Notebook(
                rows=notebook.rows
                + tuple(r for r in restored.rows if r.row_id not in present)
            )
            _write_notebook(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook

    def delete_row(self, row_id: str) -> Notebook:
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            updated_notebook = Notebook(
                rows=tuple(r for r in notebook.rows if r.row_id != row_id)
            )
            _write_notebook(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook

    def clear(self) -> Notebook:
        """Empty the notebook, keeping the cleared one aside.

        A notebook can hold a week of work before a class, so clearing puts the
        old one in the archive rather than destroying it -- nothing in the page
        offers it back, but it is on disk if it is ever asked for.
        """
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            if notebook.rows:
                archive_path = (
                    self._archive_dir
                    / f"notebook-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
                )
                _write_notebook(archive_path, notebook)
            empty_notebook = Notebook()
            _write_notebook(self._notebook_path, empty_notebook)
            self._mark_changed()
            return empty_notebook
