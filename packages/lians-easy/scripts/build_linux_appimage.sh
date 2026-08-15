#!/usr/bin/env bash
set -euo pipefail

binary=""
icon=""
output_directory=""
version=""
architecture=""
appimagetool=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary) binary="$2"; shift 2 ;;
    --icon) icon="$2"; shift 2 ;;
    --output-directory) output_directory="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --architecture) architecture="$2"; shift 2 ;;
    --appimagetool) appimagetool="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

for name in binary icon output_directory version architecture appimagetool; do
  if [[ -z "${!name}" ]]; then
    echo "Missing required --${name//_/-}" >&2
    exit 2
  fi
done

if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "Lians AppImage version must be stable X.Y.Z" >&2
  exit 2
fi
if [[ "$architecture" != "x86_64" ]]; then
  echo "Lians AppImage currently supports x86_64 only" >&2
  exit 2
fi
if [[ "$(uname -m)" != "$architecture" ]]; then
  echo "Build host architecture $(uname -m) does not match $architecture" >&2
  exit 2
fi
for source in "$binary" "$icon" "$appimagetool"; do
  if [[ ! -f "$source" ]]; then
    echo "Required AppImage input was not found: $source" >&2
    exit 2
  fi
done

mkdir -p "$output_directory"
output_directory="$(cd "$output_directory" && pwd)"
output="$output_directory/Lians-$version-linux-$architecture.AppImage"
if [[ -e "$output" ]]; then
  echo "Refusing to replace an existing AppImage: $output" >&2
  exit 2
fi

work_directory="$(mktemp -d "${TMPDIR:-/tmp}/lians-appimage.XXXXXX")"
trap 'rm -rf "$work_directory"' EXIT
app_directory="$work_directory/Lians.AppDir"
mkdir -p "$app_directory/usr/bin" "$app_directory/usr/share/applications" \
  "$app_directory/usr/share/icons/hicolor/256x256/apps"

install -m 0755 "$binary" "$app_directory/usr/bin/LiansMemory"
install -m 0644 "$icon" "$app_directory/lians.png"
install -m 0644 "$icon" \
  "$app_directory/usr/share/icons/hicolor/256x256/apps/lians.png"
ln -s lians.png "$app_directory/.DirIcon"

printf '%s\n' \
  '#!/bin/sh' \
  'set -eu' \
  'APPDIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"' \
  'exec "$APPDIR/usr/bin/LiansMemory" "$@"' \
  > "$app_directory/AppRun"
chmod 0755 "$app_directory/AppRun"

printf '%s\n' \
  '[Desktop Entry]' \
  'Type=Application' \
  'Name=Lians' \
  'Comment=Private cross-tool memory for AI clients' \
  'Exec=LiansMemory' \
  'Icon=lians' \
  'Terminal=false' \
  'Categories=Utility;Development;' \
  'StartupNotify=true' \
  'X-AppImage-Version='"$version" \
  > "$app_directory/lians.desktop"
install -m 0644 "$app_directory/lians.desktop" \
  "$app_directory/usr/share/applications/lians.desktop"

ARCH="$architecture" "$appimagetool" "$app_directory" "$output"
chmod 0755 "$output"
printf '%s\n' "$output"
