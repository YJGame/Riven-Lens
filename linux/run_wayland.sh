#!/usr/bin/env bash
# Smart launcher for 裂罅透镜 (Riven Lens) on Linux Wayland / X11.
#
# Environment toggles:
#   RIVEN_LENS_DISPLAY=auto|wayland|xwayland   (default: auto)
#     - auto: Uses XWayland (xcb) if available so PyQt6 overlays can position freely
#             and stay always-on-top, while STILL capturing screen via native Wayland
#             backends (`grim` / `spectacle`)! Set `wayland` for pure native Wayland Qt.
#   RIVEN_LENS_CAPTURE_BACKEND=grim|spectacle|gnome_screenshot|portal|mss

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Fast path for compositor hotkey IPC (`run_wayland.sh --trigger toggle`)
for arg in "$@"; do
    if [[ "$arg" == "--trigger" ]]; then
        PYTHON_BIN="${PROJECT_ROOT}/.venv_linux/bin/python"
        [[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"
        exec "$PYTHON_BIN" "${PROJECT_ROOT}/main.py" "$@"
    fi
done

DISPLAY_MODE="${RIVEN_LENS_DISPLAY:-auto}"
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    if [[ "$DISPLAY_MODE" == "wayland" ]]; then
        export QT_QPA_PLATFORM="wayland"
    elif [[ "$DISPLAY_MODE" == "xwayland" || ( "$DISPLAY_MODE" == "auto" && -n "${DISPLAY:-}" ) ]]; then
        # XWayland Qt platform allows arbitrary overlay move(x,y) + StayOnTop + click-through
        # while capture still uses native Wayland `grim`/`spectacle` automatically!
        export QT_QPA_PLATFORM="xcb"
    else
        export QT_QPA_PLATFORM="wayland;xcb"
    fi
fi

# Diagnostic hints for missing Wayland tools
if [[ "${XDG_SESSION_TYPE:-}" == "wayland" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    if ! command -v grim >/dev/null 2>&1 && ! command -v spectacle >/dev/null 2>&1 && ! command -v gnome-screenshot >/dev/null 2>&1; then
        echo "[Riven Lens] 提示：未检测到 grim / spectacle / gnome-screenshot，Wayland 截屏可能受限。"
        echo "  - Hyprland / Sway / wlroots: 安装 grim"
        echo "  - KDE Plasma Wayland:        安装 spectacle"
        echo "  - GNOME Wayland:             安装 gnome-screenshot"
    fi
    if ! command -v pw-play >/dev/null 2>&1 && ! command -v paplay >/dev/null 2>&1 && ! command -v aplay >/dev/null 2>&1; then
        echo "[Riven Lens] 提示：未检测到 pw-play / paplay / aplay，稀有属性提示音将静音。"
    fi
fi

# Prefer standalone built binary if present, otherwise .venv_linux, otherwise system python3
if [[ -x "${PROJECT_ROOT}/dist_linux/riven-lens/riven-lens" ]]; then
    exec "${PROJECT_ROOT}/dist_linux/riven-lens/riven-lens" --overlay "$@"
fi

PYTHON_BIN="${PROJECT_ROOT}/.venv_linux/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="python3"
fi

cd "$PROJECT_ROOT"
exec "$PYTHON_BIN" main.py --overlay "$@"
