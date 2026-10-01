import os
import tempfile
import threading
from datetime import datetime
from datetime import timezone
from pathlib import Path

from loguru import logger
from pydantic import BaseModel
from pydantic import ValidationError

from rosette.data_types import ClearedLog
from rosette.data_types import ClearedRow
from rosette.data_types import HowCleared
from rosette.data_types import Notebook
from rosette.data_types import Row
from rosette.tutor import RosetteError


class NotebookStorageError(RosetteError, OSError):
    """Raised when the notebook on disk cannot be read or written."""


NOTEBOOK_FILENAME = "notebook.json"

# Every cleared line, each labelled with the clearing that removed it, so
# clearing is recoverable and the history reads as one table.
CLEARED_FILENAME = "cleared.json"

# Where earlier versions kept one file per clearing. Folded into the cleared
# log the first time the store opens.
LEGACY_ARCHIVE_DIRNAME = "cleared"


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


def _write_json(path: Path, model: BaseModel) -> None:
    # Written via a neighbouring temp file and renamed, so a crash part-way
    # through leaves the previous file intact rather than a truncated one.
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".notebook-", suffix=".json"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            temp_file.write(model.model_dump_json(indent=2))
        temp_path.replace(path)
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise NotebookStorageError(f"Cannot write {path.name}: {path}") from e


def _read_cleared_log(cleared_path: Path) -> ClearedLog:
    if not cleared_path.exists():
        return ClearedLog()
    try:
        return ClearedLog.model_validate_json(cleared_path.read_text(encoding="utf-8"))
    except OSError as e:
        raise NotebookStorageError(f"Cannot read cleared lines: {cleared_path}") from e
    except ValidationError as e:
        raise NotebookStorageError(f"Cleared lines are not readable: {cleared_path}") from e


def _clearing_id(moment: datetime) -> str:
    # No hyphens: the id is used as a join key, and as the undo token.
    return f"{moment:%Y%m%dT%H%M%S%f}Z"


def _cleared_rows(
    rows: tuple[Row, ...], clearing: str, cleared_at: datetime, how_cleared: HowCleared
) -> tuple[ClearedRow, ...]:
    return tuple(
        ClearedRow(
            **row.model_dump(),
            clearing=clearing,
            cleared_at=cleared_at.isoformat(),
            how_cleared=how_cleared,
            position=position,
        )
        for position, row in enumerate(rows, start=1)
    )


def _as_row(cleared_row: ClearedRow) -> Row:
    return Row.model_validate(cleared_row.model_dump(include=set(Row.model_fields)))


# Legacy archive names: "from-<microseconds>Z.json" for a line and everything
# below it, "notebook-<seconds>Z.json" for a whole cleared notebook.
_LEGACY_ARCHIVE_KINDS: tuple[tuple[str, str, HowCleared], ...] = (
    ("from-", "%Y%m%dT%H%M%S%fZ", "line and below"),
    ("notebook-", "%Y%m%dT%H%M%SZ", "whole notebook"),
)


def _parse_legacy_archive_name(name: str) -> tuple[datetime, HowCleared] | None:
    stem = name.removesuffix(".json")
    for prefix, time_format, how_cleared in _LEGACY_ARCHIVE_KINDS:
        if stem.startswith(prefix):
            try:
                moment = datetime.strptime(stem.removeprefix(prefix), time_format)
            except ValueError:
                return None
            return moment.replace(tzinfo=timezone.utc), how_cleared
    return None


def fold_legacy_archives(data_dir: Path) -> int:
    """Move every per-clearing archive file into the cleared log. Returns how many were folded.

    Each file is removed only once the log holding its lines is safely on disk;
    a file that cannot be read or named is left where it is.
    """
    archive_dir = data_dir / LEGACY_ARCHIVE_DIRNAME
    if not archive_dir.is_dir():
        return 0
    cleared_path = data_dir / CLEARED_FILENAME
    log = _read_cleared_log(cleared_path)
    known = {r.clearing for r in log.rows}
    folded: list[tuple[datetime, Path, tuple[ClearedRow, ...]]] = []
    for path in archive_dir.glob("*.json"):
        parsed = _parse_legacy_archive_name(path.name)
        if parsed is None:
            continue
        moment, how_cleared = parsed
        try:
            notebook = _read_notebook(path)
        except NotebookStorageError:
            continue
        clearing = _clearing_id(moment)
        rows = () if clearing in known else _cleared_rows(
            notebook.rows, clearing, moment, how_cleared
        )
        folded.append((moment, path, rows))
    if not folded:
        return 0
    folded.sort(key=lambda item: item[0])
    merged = sorted(
        log.rows + tuple(r for _, _, rows in folded for r in rows),
        key=lambda r: (r.cleared_at, r.position),
    )
    _write_json(cleared_path, ClearedLog(rows=tuple(merged)))
    for _, path, _ in folded:
        path.unlink()
    if not any(archive_dir.iterdir()):
        archive_dir.rmdir()
    return len(folded)


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
        self._cleared_path = data_dir / CLEARED_FILENAME
        try:
            fold_legacy_archives(data_dir)
        except NotebookStorageError as e:
            # The old files stay where they are and are tried again on the next
            # start; a cleared log that cannot be read must not stop the
            # notebook itself from opening.
            logger.warning("Could not fold old cleared files into the cleared log: {}", e)
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

    def _set_aside(self, rows: tuple[Row, ...], how_cleared: HowCleared) -> str:
        """Add cleared lines to the cleared log. Only valid while holding the lock."""
        moment = datetime.now(timezone.utc)
        clearing = _clearing_id(moment)
        log = _read_cleared_log(self._cleared_path)
        _write_json(
            self._cleared_path,
            ClearedLog(rows=log.rows + _cleared_rows(rows, clearing, moment, how_cleared)),
        )
        return clearing

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
            _write_json(self._notebook_path, updated_notebook)
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
            _write_json(self._notebook_path, Notebook(rows=replaced_rows))
            self._mark_changed()
            return True

    def delete_row_and_below(self, row_id: str) -> tuple[Notebook, str | None]:
        """Drop a row and everything after it, keeping what was dropped aside.

        Returns the notebook and the clearing that set the removed rows aside,
        so they can be put straight back. A stretch of lines is often a
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
            clearing = self._set_aside(removed, "line and below")
            updated_notebook = Notebook(rows=notebook.rows[:index])
            _write_json(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook, clearing

    def restore_clearing(self, clearing: str) -> Notebook:
        """Append one clearing's rows back onto the notebook, and note that they are back.

        Raises NotebookStorageError if no clearing of this notebook has that id.
        """
        with self._lock:
            log = _read_cleared_log(self._cleared_path)
            restored = tuple(r for r in log.rows if r.clearing == clearing)
            if not restored:
                raise NotebookStorageError(f"Not a clearing of this notebook: {clearing}")
            notebook = _read_notebook(self._notebook_path)
            present = {r.row_id for r in notebook.rows}
            updated_notebook = Notebook(
                rows=notebook.rows
                + tuple(_as_row(r) for r in restored if r.row_id not in present)
            )
            _write_json(self._notebook_path, updated_notebook)
            restored_at = now_utc_iso()
            _write_json(
                self._cleared_path,
                ClearedLog(
                    rows=tuple(
                        r.model_copy(update={"restored_at": restored_at})
                        if r.clearing == clearing and r.restored_at is None
                        else r
                        for r in log.rows
                    )
                ),
            )
            self._mark_changed()
            return updated_notebook

    def delete_row(self, row_id: str) -> Notebook:
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            updated_notebook = Notebook(
                rows=tuple(r for r in notebook.rows if r.row_id != row_id)
            )
            _write_json(self._notebook_path, updated_notebook)
            self._mark_changed()
            return updated_notebook

    def clear(self) -> Notebook:
        """Empty the notebook, keeping the cleared one aside.

        A notebook can hold a week of work before a class, so clearing puts its
        lines in the cleared log rather than destroying them -- nothing in the
        page offers them back, but they are on disk if they are ever asked for.
        """
        with self._lock:
            notebook = _read_notebook(self._notebook_path)
            if notebook.rows:
                self._set_aside(notebook.rows, "whole notebook")
            empty_notebook = Notebook()
            _write_json(self._notebook_path, empty_notebook)
            self._mark_changed()
            return empty_notebook
