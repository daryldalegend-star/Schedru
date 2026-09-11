"""First-run setup: create the virtualenv, install dependencies, check .env,
then hand off to syllabus_cal.

This lives in Python rather than in start.bat/start.command because the
Windows batch file cannot be run on the machine this was developed on. Keeping
the launchers down to "find a working Python, run this file" shrinks the
untestable surface to a few lines, and puts everything else somewhere it can
actually be tested.

Runs on the system Python, so it must not import anything third-party.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
STAMP = VENV / ".deps-installed"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
KEY = "ANTHROPIC_API_KEY"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def fail(*lines: str) -> int:
    print()
    for line in lines:
        print(line)
    return 1


def ensure_venv() -> int:
    if venv_python().exists():
        return 0

    print("First run - setting up. This takes a minute.")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
    if result.returncode != 0 or not venv_python().exists():
        return fail(
            f"Could not create the virtual environment using: {sys.executable}",
            "",
            "If you installed Python from the Microsoft Store, try the installer",
            "from https://www.python.org/downloads/ instead - the Store version",
            "has permission limits that can break this step.",
        )
    return 0


def ensure_dependencies() -> int:
    if STAMP.exists():
        return 0

    python = str(venv_python())
    subprocess.run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    result = subprocess.run(
        [python, "-m", "pip", "install", "--quiet", "-r", str(ROOT / "requirements.txt")]
    )
    if result.returncode != 0:
        return fail(
            "Could not download the dependencies.",
            "",
            "This is usually no internet connection, or a firewall/antivirus",
            "blocking pip. Check your connection and try again.",
        )

    STAMP.write_text("ok\n")
    print("Setup done.")
    print()
    return 0


def has_key() -> bool:
    """True if .env carries a non-empty ANTHROPIC_API_KEY."""
    for raw in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == KEY and value.strip().strip("'\""):
            return True
    return False


def ensure_env_file() -> int:
    if not ENV_FILE.exists():
        ENV_FILE.write_text(
            ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else f"{KEY}=\n"
        )
        return fail(
            f"Created a file called  .env  in {ROOT}",
            "",
            "Open it in a text editor, paste your Anthropic API key after",
            f"    {KEY}=",
            "save it, then run this again.",
            "",
            "You can get a key at https://console.anthropic.com  (paid, per use).",
        )

    if not has_key():
        return fail(
            "Your .env file has no Anthropic API key in it yet.",
            "",
            f"Open .env in a text editor, paste your key after {KEY}=",
            "save it, then run this again.",
            "",
            "You can get a key at https://console.anthropic.com  (paid, per use).",
        )

    return 0


def main(argv: list[str]) -> int:
    for step in (ensure_venv, ensure_dependencies, ensure_env_file):
        code = step()
        if code:
            return code

    result = subprocess.run([str(venv_python()), "-m", "syllabus_cal", *argv], cwd=ROOT)

    if not argv:
        print()
        print("To read a screenshot, run this again with the image, e.g.:")
        print(
            "    start.bat parse shot.png"
            if os.name == "nt"
            else "    ./start.command parse shot.png"
        )

    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
