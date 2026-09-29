import os
import tempfile
import threading
from pathlib import Path
from typing import Final

from pydantic import BaseModel
from pydantic import Field
from pydantic import ValidationError

from rosette.tutor import RosetteError


class SettingsStorageError(RosetteError, OSError):
    """Raised when the settings on disk cannot be read or written."""


SETTINGS_FILENAME: Final[str] = "settings.json"

# The language this was built for, and what a fresh notebook starts on.
DEFAULT_LANGUAGE: Final[str] = "Italian"

# A language is named, not chosen from a list, so that any language can be
# used. The bound is only there to keep a stray paste out of every prompt.
MAX_LANGUAGE_LENGTH: Final[int] = 40


# A fact is one short sentence, and there are only ever a handful; the bounds
# keep a runaway reply from filling every later prompt with itself.
MAX_FACT_LENGTH: Final[int] = 200
MAX_FACT_COUNT: Final[int] = 20


class Settings(BaseModel):
    """What the notebook is set to, across sessions."""

    language: str = Field(
        default=DEFAULT_LANGUAGE, description="The language being learned"
    )
    # What the student has told the tutor about themselves, oldest first. Every
    # line is judged against these, so they are shown on the page and can be
    # taken back -- a wrong one would quietly skew every answer after it.
    known_facts: tuple[str, ...] = Field(
        default=(), description="Lasting facts the student has taught the tutor"
    )


def _read_settings(settings_path: Path) -> Settings:
    if not settings_path.exists():
        return Settings()
    try:
        raw_text = settings_path.read_text(encoding="utf-8")
    except OSError as e:
        raise SettingsStorageError(f"Cannot read settings: {settings_path}") from e
    try:
        return Settings.model_validate_json(raw_text)
    except ValidationError as e:
        raise SettingsStorageError(f"Settings are not readable: {settings_path}") from e


def _write_settings(settings_path: Path, settings: Settings) -> None:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        dir=str(settings_path.parent), prefix=".settings-", suffix=".json"
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            temp_file.write(settings.model_dump_json(indent=2))
        temp_path.replace(settings_path)
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise SettingsStorageError(f"Cannot write settings: {settings_path}") from e


def clean_language_name(requested: str) -> str:
    """The language to store, or the default when nothing usable was asked for."""
    collapsed = " ".join(requested.split())[:MAX_LANGUAGE_LENGTH].strip()
    return collapsed or DEFAULT_LANGUAGE


class SettingsStore:
    """The notebook's settings on disk, read and written under one lock."""

    def __init__(self, data_dir: Path) -> None:
        self._settings_path = data_dir / SETTINGS_FILENAME
        self._lock = threading.Lock()

    def load(self) -> Settings:
        with self._lock:
            return _read_settings(self._settings_path)

    def set_language(self, language: str) -> Settings:
        with self._lock:
            current = _read_settings(self._settings_path)
            updated_settings = current.model_copy(
                update={"language": clean_language_name(language)}
            )
            _write_settings(self._settings_path, updated_settings)
            return updated_settings

    def remember_fact(self, fact: str) -> Settings:
        """Add a fact, unless it is empty or already known."""
        cleaned = " ".join(fact.split())[:MAX_FACT_LENGTH].strip()
        with self._lock:
            current = _read_settings(self._settings_path)
            if not cleaned or cleaned in current.known_facts:
                return current
            kept = (current.known_facts + (cleaned,))[-MAX_FACT_COUNT:]
            updated_settings = current.model_copy(update={"known_facts": kept})
            _write_settings(self._settings_path, updated_settings)
            return updated_settings

    def forget_fact(self, fact: str) -> Settings:
        with self._lock:
            current = _read_settings(self._settings_path)
            updated_settings = current.model_copy(
                update={
                    "known_facts": tuple(f for f in current.known_facts if f != fact)
                }
            )
            _write_settings(self._settings_path, updated_settings)
            return updated_settings
