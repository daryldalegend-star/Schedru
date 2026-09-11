#!/bin/bash
# Double-click this on macOS (or run `bash start.command` on Linux).
# Sets up the virtualenv on first run, then launches syllabus_cal.

cd "$(dirname "$0")" || exit 1

echo "=== syllabus_cal ==="
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 isn't installed."
    echo "Get it from https://www.python.org/downloads/ then run this again."
    echo
    read -r -p "Press Enter to close."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "First run — setting up. This takes a minute."
    python3 -m venv .venv || { echo "Could not create the virtualenv."; read -r -p "Press Enter to close."; exit 1; }
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet -r requirements.txt || { echo "Could not install dependencies."; read -r -p "Press Enter to close."; exit 1; }
    echo "Setup done."
    echo
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env — open it in a text editor and paste your Anthropic API key"
    echo "after ANTHROPIC_API_KEY= , then run this again."
    echo
    read -r -p "Press Enter to close."
    exit 1
fi

if ! grep -q "ANTHROPIC_API_KEY=." .env; then
    echo "Your .env has no Anthropic API key yet."
    echo "Open .env, paste your key after ANTHROPIC_API_KEY= , then run this again."
    echo
    read -r -p "Press Enter to close."
    exit 1
fi

# No argument: show the tutorial. Otherwise pass everything straight through,
# so `./start.command parse shot.png` works too.
if [ $# -eq 0 ]; then
    ./.venv/bin/python -m syllabus_cal
    echo
    echo "To read a screenshot, drag the image onto this window after typing:"
    echo "    ./start.command parse "
else
    ./.venv/bin/python -m syllabus_cal "$@"
fi

echo
read -r -p "Press Enter to close."
