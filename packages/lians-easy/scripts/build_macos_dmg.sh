#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "Usage: $0 --binary PATH --output-directory PATH --version X.Y.Z --architecture arm64|x86_64 [--signing-identity IDENTITY]" >&2
  exit 2
}

binary=""
output_directory=""
version=""
architecture=""
signing_identity=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --binary)
      [[ $# -ge 2 ]] || usage
      binary="$2"
      shift 2
      ;;
    --output-directory)
      [[ $# -ge 2 ]] || usage
      output_directory="$2"
      shift 2
      ;;
    --version)
      [[ $# -ge 2 ]] || usage
      version="$2"
      shift 2
      ;;
    --architecture)
      [[ $# -ge 2 ]] || usage
      architecture="$2"
      shift 2
      ;;
    --signing-identity)
      [[ $# -ge 2 ]] || usage
      signing_identity="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

[[ -f "$binary" && -n "$output_directory" ]] || usage
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "Lians package version must be stable semver without a prefix: $version" >&2
  exit 1
}
[[ "$architecture" == "arm64" || "$architecture" == "x86_64" ]] || {
  echo "macOS architecture must be arm64 or x86_64: $architecture" >&2
  exit 1
}

binary="$(cd "$(dirname "$binary")" && pwd -P)/$(basename "$binary")"
output_directory="$(mkdir -p "$output_directory" && cd "$output_directory" && pwd -P)"
script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
package_directory="$(cd "$script_directory/.." && pwd -P)"
actual_architectures="$(lipo -archs "$binary")"
if [[ "$actual_architectures" != "$architecture" ]]; then
  echo "Lians runtime architecture was '$actual_architectures'; expected '$architecture'" >&2
  exit 1
fi

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/lians-macos-package.XXXXXX")"
trap 'rm -rf "$temporary_directory"' EXIT

volume_root="$temporary_directory/volume"
app="$volume_root/Lians.app"
contents="$app/Contents"
macos_directory="$contents/MacOS"
resources_directory="$contents/Resources"
mkdir -p "$macos_directory" "$resources_directory"

ditto --rsrc --extattr "$binary" "$macos_directory/LiansMemory"
chmod 755 "$macos_directory/LiansMemory"

iconset="$temporary_directory/Lians.iconset"
python "$script_directory/create_macos_icon.py" \
  --source "$package_directory/windows-lians.ico" \
  --output "$iconset"
iconutil --convert icns --output "$resources_directory/Lians.icns" "$iconset"

plist="$contents/Info.plist"
plutil -create xml1 "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDevelopmentRegion string en" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string Lians" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string LiansMemory" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string Lians" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string ai.lians.memory" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleInfoDictionaryVersion string 6.0" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string Lians" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $version" "$plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $version" "$plist"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$plist"
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$plist"
plutil -lint "$plist"

ln -s /Applications "$volume_root/Applications"
xattr -cr "$app"
if [[ -n "$signing_identity" ]]; then
  codesign --force --options runtime --timestamp --sign "$signing_identity" "$app"
else
  codesign --force --sign - "$app"
fi
codesign --verify --deep --strict --verbose=2 "$app"

dmg="$output_directory/Lians-$version-macos-$architecture.dmg"
hdiutil create \
  -quiet \
  -ov \
  -format UDZO \
  -fs HFS+ \
  -imagekey zlib-level=9 \
  -volname "Lians" \
  -srcfolder "$volume_root" \
  "$dmg"

if [[ -n "$signing_identity" ]]; then
  codesign --force --timestamp --sign "$signing_identity" "$dmg"
else
  codesign --force --sign - "$dmg"
fi
codesign --verify --strict --verbose=2 "$dmg"

echo "$dmg"
