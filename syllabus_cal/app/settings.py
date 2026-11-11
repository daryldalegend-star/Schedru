"""The Anthropic API key and the app's small preferences.

The key stays in a `.env` file in the data folder -- the same place and format
the terminal version uses, so the two share one key -- written readable only by
this user. Preferences ("has the tutorial been watched") go in settings.json
beside it.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

KEY_NAME = "ANTHROPIC_API_KEY"
ENV_FILE_NAME = ".env"
SETTINGS_FILE_NAME = "settings.json"

MASKED_KEY_MESSAGE = (
    "That's the shortened preview of the key (the one with \"...\" in the middle), not "
    "the key itself. Anthropic shows the whole key only once, right after you create "
    "it. Create a new key, press Copy in the box that appears, and paste that here."
)
EMPTY_KEY_MESSAGE = "Paste your key into the box first."
NOT_A_KEY_MESSAGE = (
    "That doesn't look like an Anthropic key. They start with \"sk-ant-\" and are about "
    "100 characters long. Use the Copy button in Anthropic's console so nothing gets "
    "left out."
)


class KeyFormatError(ValueError):
    """The pasted text can't be a usable key. The message says why, plainly."""


def clean_key(raw: str) -> str:
    """Normalise a pasted key, or raise KeyFormatError explaining what's wrong.

    Catches the mistakes that are cheap to catch before bothering Anthropic:
    surrounding quotes or spaces, a pasted `ANTHROPIC_API_KEY=` prefix, and the
    console's masked preview.
    """
    key = (raw or "").strip()
    if key.upper().startswith(KEY_NAME + "="):
        key = key[len(KEY_NAME) + 1 :].strip()
    key = key.strip("'\"").strip()

    if not key:
        raise KeyFormatError(EMPTY_KEY_MESSAGE)
    if "..." in key or "…" in key:
        raise KeyFormatError(MASKED_KEY_MESSAGE)
    if any(ch.isspace() for ch in key) or not key.startswith("sk-ant-") or len(key) < 40:
        raise KeyFormatError(NOT_A_KEY_MESSAGE)
    return key


def key_hint(key: str) -> str:
    """Enough of the key to recognise it by, never enough to use it."""
    return f"sk-ant-…{key[-4:]}" if len(key) >= 12 else "sk-ant-…"


class Settings:
    def __init__(self, data_dir: Path, environ: Mapping[str, str] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self._environ = os.environ if environ is None else environ
        self._lock = threading.Lock()

    # ---- API key -------------------------------------------------------

    @property
    def env_path(self) -> Path:
        return self.data_dir / ENV_FILE_NAME

    def api_key(self) -> str | None:
        """The saved key, or one from the environment, or None.

        Returned as stored: a malformed key is reported when it's used, with
        Anthropic's own explanation, rather than silently treated as missing.
        """
        value = None
        if self.env_path.exists():
            try:
                value = dotenv_values(self.env_path).get(KEY_NAME)
            except OSError:
                value = None
        if not (value or "").strip():
            value = self._environ.get(KEY_NAME)
        value = (value or "").strip()
        return value or None

    def save_api_key(self, key: str) -> None:
        """Write the key into .env, keeping any other lines that are there."""
        with self._lock:
            lines: list[str] = []
            if self.env_path.exists():
                lines = self.env_path.read_text(encoding="utf-8", errors="replace").splitlines()

            written = False
            for index, line in enumerate(lines):
                name = line.split("=", 1)[0].strip()
                if name.removeprefix("export ").strip() == KEY_NAME:
                    if written:
                        lines[index] = ""  # an older duplicate would shadow nothing, but confuses
                    else:
                        lines[index] = f"{KEY_NAME}={key}"
                        written = True
            if not written:
                lines.append(f"{KEY_NAME}={key}")

            self._write_private(self.env_path, "\n".join(lines).rstrip("\n") + "\n")

    # ---- preferences ---------------------------------------------------

    @property
    def settings_path(self) -> Path:
        return self.data_dir / SETTINGS_FILE_NAME

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return self._load().get(name, default)

    def set(self, name: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            data[name] = value
            self._write_private(self.settings_path, json.dumps(data, indent=2) + "\n")

    # ---- helpers -------------------------------------------------------

    def _write_private(self, path: Path, text: str) -> None:
        """Replace `path` atomically, readable only by this user."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
