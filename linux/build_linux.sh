#!/usr/bin/env bash
# One-command Linux (Wayland & X11) standalone build script for 裂罅透镜 (Riven Lens).
# Creates `.venv_linux`, installs dependencies, runs all unit tests, and builds `dist_linux/riven-lens/`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv_linux"

cd "$PROJECT_ROOT"

if [[ ! -d "$VENV_DIR" ]]; then
    echo "[1/4] Creating Python virtual environment at ${VENV_DIR}..."
    python3 -m venv "$VENV_DIR"
fi

echo "[2/4] Installing Linux dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r requirements-linux.txt pyinstaller

echo "[3/4] Running full unit test suite (offscreen)..."
QT_QPA_PLATFORM=offscreen "${VENV_DIR}/bin/python" -m unittest discover -s tests -q

echo "[4/4] Building standalone Linux bundle via PyInstaller..."
"${VENV_DIR}/bin/pyinstaller" RivenOverlay_linux.spec --clean -y --distpath "${PROJECT_ROOT}/dist_linux"

echo ""
echo "Build complete: ${PROJECT_ROOT}/dist_linux/riven-lens/riven-lens"
echo "Launch via:     bash linux/run_wayland.sh"
