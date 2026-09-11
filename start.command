#!/bin/bash
# Double-click this on macOS (or run `bash start.command` on Linux).
# Finds a working Python, then hands off to bootstrap.py.

cd "$(dirname "$0")" || exit 1

echo "=== syllabus_cal ==="
echo

# `command -v python3` isn't enough on macOS: /usr/bin/python3 exists as a
# stub that only prompts to install the Xcode command line tools, so require
# real "Python 3.x" version output before trusting a candidate.
PYCMD=""
for candidate in python3 python3.13 python3.12 python3.11 python; do
    if version=$("$candidate" --version 2>&1) && [[ "$version" == Python\ 3* ]]; then
        PYCMD="$candidate"
        break
    fi
done

if [ -z "$PYCMD" ]; then
    echo "Python 3 isn't installed (or isn't working yet)."
    echo
    echo "Install it from: https://www.python.org/downloads/"
    echo "Then run this again."
    echo
    echo "On macOS you may instead be prompted to install the Xcode command"
    echo "line tools - accepting that also works, then run this again."
    echo
    read -r -p "Press Enter to close."
    exit 1
fi

echo "Using $version"
echo

"$PYCMD" bootstrap.py "$@"

echo
read -r -p "Press Enter to close."
