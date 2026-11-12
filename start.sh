#!/bin/sh
# Roomwalk launcher for macOS and Linux.  Usage: ./start.sh   (add --lan to allow phones on your Wi-Fi)
cd "$(dirname "$0")" || exit 1
PY=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && \
     "$candidate" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 14) else 1)'; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Roomwalk needs Python 3.10 to 3.14 (3.15 is too new for one of its parts)."
  echo "Install Python 3.14 from https://www.python.org/downloads/ and run this again."
  exit 1
fi
if [ ! -f .venv/installed.txt ]; then
  echo "Setting up Roomwalk for the first time (downloads a few hundred MB)..."
  "$PY" -m venv .venv && .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt || {
    echo "Setup did not finish. Check your internet connection and run ./start.sh again."; exit 1; }
  echo ok > .venv/installed.txt
fi
exec .venv/bin/python -m roomwalk "$@"
