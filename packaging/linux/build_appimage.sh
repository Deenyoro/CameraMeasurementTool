#!/usr/bin/env bash
# Build the Linux x86_64 AppImage of Camera Wire Tool.
#
#   Usage:  packaging/linux/build_appimage.sh [VERSION] [OUT_DIR]
#
#   VERSION  defaults to $APP_VERSION, then "0.0.0-dev"
#   OUT_DIR  defaults to packaging/linux/out (gitignored)
#
# Result: $OUT_DIR/CameraWireTool-<VERSION>-linux-x86_64.AppImage
#
# Steps: PyInstaller (camera_wire_tool.spec -> single-file CameraWireTool
# binary) -> AppDir (usr/bin binary, AppRun, .desktop, icon) -> appimagetool.
#
# Requirements: a Python with Tk 8.6 plus requirements.txt, pyinstaller and
# (for PDF export in the bundle) reportlab installed. Set $PYTHON to pick the
# interpreter (default: python3). Network is used only to fetch the pinned,
# checksum-verified appimagetool below (cached in $OUT_DIR/tools).
#
# The script never deletes anything: every run builds into a fresh
# mktemp directory under $OUT_DIR, and it refuses to overwrite an existing
# AppImage of the same name.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-${APP_VERSION:-0.0.0-dev}}"
OUT_DIR="${2:-$REPO_ROOT/packaging/linux/out}"
PYTHON="${PYTHON:-python3}"

APP_NAME="CameraWireTool"
APP_ID="cameramtool"            # .desktop basename and icon name
ARCH="x86_64"

# Pinned appimagetool release (NOT the moving "continuous" tag), verified by
# sha256 so a replaced/compromised asset fails the build instead of being
# executed. To upgrade: change the version, download once, update the hash.
APPIMAGETOOL_VERSION="1.9.1"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-${ARCH}.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"

case "$VERSION" in
  *[!0-9A-Za-z.+-]*|"") echo "error: invalid version '$VERSION'" >&2; exit 2 ;;
esac

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
TARGET="$OUT_DIR/${APP_NAME}-${VERSION}-linux-${ARCH}.AppImage"
if [ -e "$TARGET" ]; then
  echo "error: $TARGET already exists; move it away or pass another OUT_DIR" >&2
  exit 1
fi

# ---- appimagetool (pinned + verified) ---------------------------------- #
TOOLS="$OUT_DIR/tools"
TOOL="$TOOLS/appimagetool-${APPIMAGETOOL_VERSION}-${ARCH}.AppImage"
mkdir -p "$TOOLS"
if [ ! -f "$TOOL" ]; then
  echo "Downloading appimagetool ${APPIMAGETOOL_VERSION}"
  curl -fL --retry 3 --retry-all-errors --connect-timeout 20 --max-time 300 \
       "$APPIMAGETOOL_URL" -o "$TOOL.part"
  mv "$TOOL.part" "$TOOL"
fi
echo "${APPIMAGETOOL_SHA256}  ${TOOL}" | sha256sum -c -
chmod +x "$TOOL"

# appimagetool 1.9.x otherwise downloads the AppImage runtime from the
# type2-runtime "continuous" release at build time (unpinned, unverified).
# Instead reuse the runtime embedded in the verified appimagetool itself: an
# AppImage is <runtime ELF><squashfs>, and --appimage-offset is where the
# squashfs starts. This is the same static type2 runtime (no libfuse2 needed
# on end-user machines) and keeps the build fully pinned and offline.
RUNTIME="$TOOLS/runtime-${ARCH}-from-appimagetool-${APPIMAGETOOL_VERSION}"
if [ ! -f "$RUNTIME" ]; then
  offset="$("$TOOL" --appimage-offset)"
  head -c "$offset" "$TOOL" > "$RUNTIME.part"
  mv "$RUNTIME.part" "$RUNTIME"
fi

# ---- PyInstaller -------------------------------------------------------- #
WORK="$(mktemp -d "$OUT_DIR/run.XXXXXX")"
echo "Work dir: $WORK"
(
  cd "$REPO_ROOT"
  "$PYTHON" -m PyInstaller --noconfirm \
    --distpath "$WORK/dist" --workpath "$WORK/build" \
    camera_wire_tool.spec
)
BIN="$WORK/dist/$APP_NAME"
[ -x "$BIN" ] || { echo "error: PyInstaller did not produce $BIN" >&2; exit 1; }

# ---- AppDir ------------------------------------------------------------- #
APPDIR="$WORK/AppDir"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp "$BIN" "$APPDIR/usr/bin/$APP_NAME"

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
# AppImage entry point: run the bundled PyInstaller binary.
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/CameraWireTool" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/$APP_ID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Camera Wire Tool
Comment=Plan security-camera placement and estimate cable runs on floor plans
Exec=$APP_NAME
Icon=$APP_ID
Terminal=false
Categories=Graphics;Engineering;
DESKTOP
cp "$APPDIR/$APP_ID.desktop" "$APPDIR/usr/share/applications/"

ICON_SRC="$REPO_ROOT/packaging/icons/$APP_ID.png"
cp "$ICON_SRC" "$APPDIR/$APP_ID.png"
cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
ln -s "$APP_ID.png" "$APPDIR/.DirIcon"

# ---- appimagetool ------------------------------------------------------- #
# --appimage-extract-and-run: run the tool without FUSE (CI runners and
# containers often lack /dev/fuse).
ARCH="$ARCH" VERSION="$VERSION" "$TOOL" --appimage-extract-and-run \
  --runtime-file "$RUNTIME" "$APPDIR" "$TARGET"

echo "Built: $TARGET"
ls -l "$TARGET"
