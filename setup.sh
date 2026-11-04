#!/usr/bin/env sh
# Job Radar one-step setup for macOS / Linux:   ./setup.sh
# Creates a virtual environment in .venv, installs the packages, creates config.yaml
# and the database, and offers to schedule a daily check.
set -e
cd "$(dirname "$0")"

PY=""
for c in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$c"; break
  fi
done
[ -n "$PY" ] || { echo "Job Radar needs Python 3.10 or newer: https://www.python.org/downloads/"; exit 1; }

echo "Using $($PY --version)"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt
.venv/bin/python -m radar setup "$@"
echo
echo "Next time, start it with:  .venv/bin/python -m radar serve"
echo "Just looking?  Try:       .venv/bin/python -m radar demo"
