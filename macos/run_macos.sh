#!/usr/bin/env bash
# Launch 裂罅透镜 (Riven Lens) on macOS (Apple Silicon & Intel).
# Uses built `dist_macos/RivenLens.app` when available, otherwise runs from `.venv_macos` / `python3`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -d "${PROJECT_ROOT}/dist_macos/RivenLens.app" && "${1:-}" != "--source" ]]; then
    exec "${PROJECT_ROOT}/dist_macos/RivenLens.app/Contents/MacOS/RivenLens" "$@"
fi

if [[ "${1:-}" == "--source" ]]; then
    shift
fi

if [[ -x "${PROJECT_ROOT}/.venv_macos/bin/python" ]]; then
    exec "${PROJECT_ROOT}/.venv_macos/bin/python" "${PROJECT_ROOT}/main.py" "$@"
fi

exec python3 "${PROJECT_ROOT}/main.py" "$@"
