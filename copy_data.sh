#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "creating virtual environment: $VENV_DIR"
  "$PYTHON" -m venv "$VENV_DIR"
fi

echo "installing requirements"
"$VENV_PYTHON" -m pip install --disable-pip-version-check -q -r "$SCRIPT_DIR/requirements.txt"

exec "$VENV_PYTHON" "$SCRIPT_DIR/copy_data.py" "$@"
