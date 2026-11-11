"""Where Schedru keeps its files.

Two layouts, because the app runs two ways:

- From this folder (start.command, or `python -m ...`): everything lives in
  the project folder, exactly where earlier versions put it, so an existing
  `.env`, `credentials.json` and `token.json` keep working untouched.
- As the packaged Schedru.app: the bundle is read-only and gets replaced on
  every update, so data goes in the per-user Application Support folder.

`SCHEDRU_DATA_DIR` overrides both. Tests and CI use it so they never touch a
real sign-in.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "Schedru"
DATA_DIR_ENV = "SCHEDRU_DATA_DIR"


def is_frozen() -> bool:
    """True inside the packaged app (PyInstaller sets sys.frozen)."""
    return bool(getattr(sys, "frozen", False))


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """Where the package's read-only files are: the unpacked bundle when
    frozen, otherwise the project folder."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return project_root()


def _user_data_home() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_DIR_NAME.lower()


def data_dir(create: bool = True) -> Path:
    """The folder holding the API key, the Google files and app settings."""
    override = os.environ.get(DATA_DIR_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
    elif is_frozen():
        path = _user_data_home()
    else:
        path = project_root()

    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path
