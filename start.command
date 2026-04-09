#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

PY_CMD=""
if command -v python3 >/dev/null 2>&1; then
  PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PY_CMD="python"
fi

if [ -z "$PY_CMD" ]; then
  if command -v brew >/dev/null 2>&1; then
    brew install python || true
    if command -v python3 >/dev/null 2>&1; then
      PY_CMD="python3"
    fi
  fi
fi

if [ -z "$PY_CMD" ]; then
  osascript -e 'display dialog "Python 3 is not installed. Click OK to open download page." buttons {"OK"} default button "OK"'
  open "https://www.python.org/downloads/macos/"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PY_CMD" -m venv .venv
fi

source ".venv/bin/activate"

echo "Installing dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "Launching Jira Duplicate Finder..."
python gui.py