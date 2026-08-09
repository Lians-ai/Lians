#!/bin/sh
set -eu

action=${1:-}
case "$action" in
  hook|prewarm) ;;
  *) exit 0 ;;
esac

# Resolve only trusted, native per-user locations. The active project never
# participates in executable discovery for a prompt hook.
unset LIANS_MEMORY_HOME
unset PYTHONPATH PYTHONHOME
export PYTHONNOUSERSITE=1
export PYTHONSAFEPATH=1
script_dir=$(CDPATH= cd -- "${0%/*}" && pwd -P)
if [ -x /usr/bin/uname ]; then
  platform=$(/usr/bin/uname -s)
else
  platform=$(/bin/uname -s)
fi

is_absolute() {
  case ${1:-} in
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

if [ "$platform" = "Darwin" ]; then
  is_absolute "${HOME:-}" || exit 0
  data_home=${HOME}/Library/Application\ Support/Lians/CodexMemory
else
  if is_absolute "${XDG_DATA_HOME:-}"; then
    native_base=${XDG_DATA_HOME}
  elif is_absolute "${HOME:-}"; then
    native_base=${HOME}/.local/share
  else
    exit 0
  fi
  data_home=${native_base}/lians/codex-memory
fi
python=${data_home}/venv/bin/python

# Before first-run setup the hook must remain silent and fail open.
[ -x "$python" ] || exit 0
exec "$python" -B "$script_dir/lians_plugin.py" "$action"
