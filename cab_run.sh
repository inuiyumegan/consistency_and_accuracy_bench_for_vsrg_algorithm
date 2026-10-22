#!/usr/bin/env bash
# Entry point for the CAB-VA CLI.
#
# The interpreter is configurable ($PYTHON) and verified before use: a bare
# `python3` can resolve to an interpreter without numpy/scipy (e.g. a distro
# python or a PATH shim), which then fails deep inside cab/__init__ with a
# confusing traceback.
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "error: interpreter '$PYTHON' not found (set \$PYTHON to a python with numpy/scipy)" >&2
  exit 2
fi

if ! "$PYTHON" -c "import numpy, scipy" >/dev/null 2>&1; then
  echo "error: '$PYTHON' cannot import numpy/scipy." >&2
  echo "       Install the project first:  $PYTHON -m pip install -e ." >&2
  echo "       Or point \$PYTHON at the right interpreter." >&2
  exit 2
fi

PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}" exec "$PYTHON" -m cab.cli "$@"
