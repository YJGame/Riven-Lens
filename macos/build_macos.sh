#!/usr/bin/env bash
# One-command macOS (Apple Silicon & Intel) build script for 裂罅透镜 (Riven Lens).
# Steps:
#   1. Creates `.venv_macos` and installs macOS dependencies
#   2. Generates native macOS `assets/icon.icns` from `assets/icon.png` via `sips` + `iconutil`
#   3. Runs full unit test suite (offscreen)
#   4. Compiles `dist_macos/RivenLens.app` via PyInstaller
#   5. Packages a drag-to-Applications `.dmg` installer at `dist_macos/RivenLens_macOS_<arch>.dmg`

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv_macos"
ARCH="$(uname -m)"

cd "$PROJECT_ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "[ERROR] This script must be run on macOS (Darwin)." >&2
    exit 1
fi

PYTHON_BIN="$(command -v python3.12 || command -v python3)"
echo "Using Python: $(${PYTHON_BIN} --version) on macOS (${ARCH})"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "[1/5] Creating Python virtual environment at ${VENV_DIR}..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "[2/5] Installing macOS dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r requirements-macos.txt pyinstaller

echo "[3/5] Generating macOS .icns app icon from assets/icon.png..."
if [[ -f "${PROJECT_ROOT}/assets/icon.png" ]] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    ICONSET_DIR="${PROJECT_ROOT}/build/RivenLens.iconset"
    rm -rf "$ICONSET_DIR"
    mkdir -p "$ICONSET_DIR"
    for sz in 16 32 128 256 512; do
        sips -z "$sz" "$sz" "${PROJECT_ROOT}/assets/icon.png" --out "${ICONSET_DIR}/icon_${sz}x${sz}.png" >/dev/null
        sz2=$((sz * 2))
        sips -z "$sz2" "$sz2" "${PROJECT_ROOT}/assets/icon.png" --out "${ICONSET_DIR}/icon_${sz}x${sz}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET_DIR" -o "${PROJECT_ROOT}/assets/icon.icns"
    rm -rf "$ICONSET_DIR"
    echo "Generated assets/icon.icns"
fi

echo "[4/5] Running full unit test suite (offscreen)..."
QT_QPA_PLATFORM=offscreen "${VENV_DIR}/bin/python" -m unittest discover -s tests -q

echo "[5/5] Building RivenLens.app bundle via PyInstaller..."
"${VENV_DIR}/bin/pyinstaller" RivenOverlay_macos.spec --clean -y --distpath "${PROJECT_ROOT}/dist_macos"

APP_BUNDLE="${PROJECT_ROOT}/dist_macos/RivenLens.app"
DMG_OUT="${PROJECT_ROOT}/dist_macos/RivenLens_macOS_${ARCH}.dmg"
DMG_STAGE="${PROJECT_ROOT}/build/_dmg_stage"

if command -v hdiutil >/dev/null 2>&1 && [[ -d "$APP_BUNDLE" ]]; then
    echo "[+] Creating drag-to-Applications DMG installer..."
    rm -rf "$DMG_STAGE" "$DMG_OUT"
    mkdir -p "$DMG_STAGE"
    cp -R "$APP_BUNDLE" "${DMG_STAGE}/RivenLens.app"
    ln -s /Applications "${DMG_STAGE}/Applications"
    hdiutil create -volname "Riven Lens (裂罅透镜)" -srcfolder "$DMG_STAGE" -ov -format UDZO "$DMG_OUT" >/dev/null
    rm -rf "$DMG_STAGE"
    echo "DMG ready: ${DMG_OUT}"
fi

echo ""
echo "====================================================="
echo " macOS Build Complete!"
echo " App Bundle: ${APP_BUNDLE}"
if [[ -f "$DMG_OUT" ]]; then
    echo " DMG Image : ${DMG_OUT}"
fi
echo " Launch via: open \"${APP_BUNDLE}\""
echo "====================================================="
