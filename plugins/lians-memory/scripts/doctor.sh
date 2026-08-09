#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this command." >&2
  exit 2
fi

exec uv run --managed-python --no-project --python 3.11 \
  python -I -B "$SCRIPT_DIR/lians_plugin.py" doctor "$@"
