import os
import stat
from pathlib import Path
from typing import Final

from pydantic import BaseModel
from pydantic import Field

from rosette.tutor import RosetteError


class CredentialStorageError(RosetteError, OSError):
    """Raised when the stored API key cannot be read or written."""


# The key the app was given, kept with the app's own data rather than the
# workspace's, so the app carries its own credentials wherever it runs.
API_KEY_FILENAME: Final[str] = "anthropic-api-key"

# Anthropic keys start with this. Checked only to catch an obvious paste of the
# wrong thing; the real verification is the first call succeeding.
API_KEY_PREFIX: Final[str] = "sk-ant-"

MIN_API_KEY_LENGTH: Final[int] = 20


class CredentialStatus(BaseModel):
    """Whether a key is set and where it came from -- never the key itself."""

    # How Claude is reached: "key" is a direct API call with the key below,
    # "mind" is the host workspace's own signed-in Claude.
    route: str = Field(description='Either "key" or "mind"')
    source: str | None = Field(
        default=None, description='Where the key came from: "app" or "environment"'
    )
    hint: str | None = Field(
        default=None, description="The key's last few characters, to tell keys apart"
    )


def key_hint(api_key: str) -> str:
    return "…" + api_key[-4:]


class ResolvedCredentials(BaseModel):
    """The key to call with, if there is one, and where it was found."""

    api_key: str | None = Field(default=None, description="The key itself")
    source: str | None = Field(default=None, description='"app" or "environment"')


def _key_path(data_dir: Path) -> Path:
    return data_dir / API_KEY_FILENAME


def read_api_key(data_dir: Path) -> ResolvedCredentials:
    """The key to use, preferring the environment so a deployment can override.

    The environment comes first because that is how a standalone copy of this
    app is configured; the stored file is what the page writes.
    """
    from_environment = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if from_environment:
        return ResolvedCredentials(api_key=from_environment, source="environment")
    path = _key_path(data_dir)
    if not path.exists():
        return ResolvedCredentials()
    try:
        stored = path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise CredentialStorageError(f"Cannot read the stored key: {path}") from e
    return ResolvedCredentials(api_key=stored or None, source="app" if stored else None)


def describe_credentials(data_dir: Path) -> CredentialStatus:
    resolved = read_api_key(data_dir)
    if resolved.api_key is None:
        return CredentialStatus(route="mind")
    return CredentialStatus(
        route="key", source=resolved.source, hint=key_hint(resolved.api_key)
    )


def is_plausible_api_key(candidate: str) -> bool:
    cleaned = candidate.strip()
    return cleaned.startswith(API_KEY_PREFIX) and len(cleaned) >= MIN_API_KEY_LENGTH


def store_api_key(data_dir: Path, api_key: str) -> CredentialStatus:
    """Write the key readable only by this user. Raises on an implausible key."""
    cleaned = api_key.strip()
    if not is_plausible_api_key(cleaned):
        raise CredentialStorageError("That does not look like an Anthropic API key")
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _key_path(data_dir)
    try:
        # Created with no group or other access before anything is written to
        # it, so the key is never briefly world-readable on disk.
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(handle, "w", encoding="utf-8") as key_file:
            key_file.write(cleaned)
    except OSError as e:
        raise CredentialStorageError(f"Cannot store the key: {path}") from e
    return describe_credentials(data_dir)


def clear_api_key(data_dir: Path) -> CredentialStatus:
    try:
        _key_path(data_dir).unlink(missing_ok=True)
    except OSError as e:
        raise CredentialStorageError("Cannot remove the stored key") from e
    return describe_credentials(data_dir)
