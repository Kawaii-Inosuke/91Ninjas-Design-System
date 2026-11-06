#!/usr/bin/env bash
# Build 91Ninjas Studio for macOS and package it as a DMG (D-040).
#   STUDIO_VERSION=1.0.0 studio/packaging/macos/package.sh arm64|x86_64
# Writes $DIST/91ninjas-studio-<version>-macos-<arch>.dmg (the asset name craft-update looks for), holding
# "91Ninjas Studio.app": the editor, the bundled uv (Contents/MacOS/uv) and the agents (Contents/Resources/agents).
# Signed ad hoc (free, D-035): the first open needs System Settings › Privacy & Security › Open Anyway; updates don't.
set -euo pipefail
ARCH="${1:-arm64}"
case "$ARCH" in
  arm64) TARGET=aarch64-apple-darwin; UV_ASSET=uv-aarch64-apple-darwin ;;
  x86_64) TARGET=x86_64-apple-darwin; UV_ASSET=uv-x86_64-apple-darwin ;;
  *) echo "arch is arm64 or x86_64" >&2; exit 2 ;;
esac
: "${STUDIO_VERSION:?set STUDIO_VERSION, e.g. 1.0.0}"
STUDIO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EDITOR="$STUDIO/editor"
DIST="${DIST:-$STUDIO/dist}"
UV_VERSION="${UV_VERSION:-0.13.1}"
export MACOSX_DEPLOYMENT_TARGET=11.0
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$EDITOR/target}"
mkdir -p "$DIST"

if [[ "${SKIP_BUILD:-0}" != 1 ]]; then
  (cd "$EDITOR" && cargo build --release --locked -p vectorcraft --target "$TARGET")
fi

WORK="$CARGO_TARGET_DIR/studio-macos-$ARCH"
APP="$WORK/91Ninjas Studio.app"
rm -rf "$WORK"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$CARGO_TARGET_DIR/$TARGET/release/vectorcraft" "$APP/Contents/MacOS/91ninjas-studio"
cp "$EDITOR/assets/91studio/icon.icns" "$APP/Contents/Resources/91Ninjas Studio.icns"

# uv (Astral, MIT/Apache-2.0): runs the agents; the first start fetches Python and the packages with it.
curl -fsSL "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$UV_ASSET.tar.gz" | tar -xz -C "$WORK"
cp "$WORK/$UV_ASSET/uv" "$APP/Contents/MacOS/uv"

# The agents: source and lock file only.
rsync -a --exclude .venv --exclude tests --exclude __pycache__ --exclude .pytest_cache --exclude '*.pyc' --exclude .env \
  "$STUDIO/agents/" "$APP/Contents/Resources/agents/"
# Marks the agents as installed with the app (D-049): the editor then uses only these, inside the bundle.
printf 'installed with 91Ninjas Studio' > "$APP/Contents/Resources/agents/.installed"

SHORT="${STUDIO_VERSION%%-*}"
cat >"$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>91Ninjas Studio</string>
  <key>CFBundleDisplayName</key><string>91Ninjas Studio</string>
  <key>CFBundleExecutable</key><string>91ninjas-studio</string>
  <key>CFBundleIdentifier</key><string>com.91ninjas.studio</string>
  <key>CFBundleIconFile</key><string>91Ninjas Studio</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$SHORT</string>
  <key>CFBundleVersion</key><string>$STUDIO_VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.graphics-design</string>
  <key>NSHumanReadableCopyright</key><string>© 2026 91Ninjas. All rights reserved. Internal use only.</string>
  <key>CFBundleDocumentTypes</key>
  <array><dict>
    <key>CFBundleTypeName</key><string>91Ninjas Studio design</string>
    <key>CFBundleTypeRole</key><string>Editor</string>
    <key>LSHandlerRank</key><string>Owner</string>
    <key>CFBundleTypeIconFile</key><string>91Ninjas Studio</string>
    <key>LSItemContentTypes</key><array><string>com.91ninjas.studio.design</string></array>
  </dict></array>
  <key>UTExportedTypeDeclarations</key>
  <array><dict>
    <key>UTTypeIdentifier</key><string>com.91ninjas.studio.design</string>
    <key>UTTypeDescription</key><string>91Ninjas Studio design</string>
    <key>UTTypeConformsTo</key><array><string>public.data</string></array>
    <key>UTTypeIconFile</key><string>91Ninjas Studio</string>
    <key>UTTypeTagSpecification</key>
    <dict><key>public.filename-extension</key><array><string>91studio</string><string>vectorcraft</string></array></dict>
  </dict></array>
</dict>
</plist>
PLIST
plutil -lint "$APP/Contents/Info.plist"
printf 'APPL????' >"$APP/Contents/PkgInfo"

# Ad-hoc signature (free): without one, new Macs call the app "damaged" instead of asking to open it.
codesign --force --sign - "$APP/Contents/MacOS/uv"
codesign --force --deep --sign - "$APP"
codesign --verify --deep --verbose=2 "$APP"

DMG="$DIST/91ninjas-studio-$STUDIO_VERSION-macos-$ARCH.dmg"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/91Ninjas Studio.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
for attempt in 1 2 3; do
  hdiutil create -srcfolder "$STAGE" -volname "91Ninjas Studio" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$DMG" && break
  sleep $((attempt * 10))
done
ls -lh "$DMG"
