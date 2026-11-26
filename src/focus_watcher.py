"""Cross-platform foreground window focus detector for smart overlay auto-hide.

Automatically hides overlays when the active foreground window is neither Warframe
nor our own Control Panel / Region Calibrator, and restores visibility the instant
the player alt-tabs back into Warframe.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import Callable, Optional


# Substrings (case-insensitive) of foreground window titles where overlays SHOULD remain visible
ALLOWED_WINDOW_TITLE_KEYWORDS = (
    "warframe",
    "星际战甲",
    "裂罅透镜",
    "riven lens",
    "warframe 紫卡",
    "warframe riven",
    "校准截图",
    "calibrate",
)


def get_foreground_window_title() -> str:
    """Return the title of the currently focused OS window (Windows, macOS & Linux safe)."""
    try:
        if sys.platform == "win32":
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""
            length = int(user32.GetWindowTextLengthW(hwnd))
            if length <= 0:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value.strip()

        if sys.platform == "darwin":
            from src.macos_support import get_macos_foreground_app_and_title

            mac_title, mac_supported = get_macos_foreground_app_and_title()
            if mac_supported:
                return mac_title
            return ""

        # Linux Wayland: query native compositor active-window API first
        from src.wayland_support import get_wayland_foreground_title, is_linux_wayland

        if is_linux_wayland():
            wl_title, wl_supported = get_wayland_foreground_title()
            if wl_supported:
                return wl_title

        # Linux X11 / XWayland: try xdotool first, then xprop fallback
        try:
            out = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"],
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                text=True,
            )
            return out.strip()
        except Exception:
            pass

        try:
            root_out = subprocess.check_output(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                text=True,
            )
            parts = root_out.strip().split()
            win_id = parts[-1] if parts else ""
            if win_id and win_id != "0x0":
                name_out = subprocess.check_output(
                    ["xprop", "-id", win_id, "WM_NAME"],
                    stderr=subprocess.DEVNULL,
                    timeout=0.6,
                    text=True,
                )
                if "=" in name_out:
                    return name_out.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    except Exception:
        pass
    return ""


def is_overlay_allowed_for_title(title: str) -> bool:
    """Return True if `title` belongs to Warframe, macOS Warframe hosts, or our own tool UI."""
    low = (title or "").strip().lower()
    if not low:
        return False
    if any(kw in low for kw in ALLOWED_WINDOW_TITLE_KEYWORDS):
        return True
    if sys.platform == "darwin":
        from src.macos_support import MACOS_WARFRAME_HOST_KEYWORDS

        return any(kw in low for kw in MACOS_WARFRAME_HOST_KEYWORDS)
    return False


def is_focus_query_supported() -> bool:
    """Return False when active-window queries are unavailable (e.g. locked-down Wayland or denied macOS)."""
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        from src.macos_support import get_macos_foreground_app_and_title

        _, mac_supported = get_macos_foreground_app_and_title()
        return mac_supported
    from src.wayland_support import get_wayland_foreground_title, is_linux_wayland

    if is_linux_wayland():
        _, wl_supported = get_wayland_foreground_title()
        if wl_supported:
            return True
        # Fall back to XWayland xdotool only if it actually returns a non-empty window title
        try:
            out = subprocess.check_output(
                ["xdotool", "getactivewindow", "getwindowname"],
                stderr=subprocess.DEVNULL,
                timeout=0.4,
                text=True,
            )
            return bool(out.strip())
        except Exception:
            return False
    return True


class SmartFocusWatcher:
    """Stateful controller deciding when overlays should auto-hide / auto-restore by focus."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        title_provider: Optional[Callable[[], str]] = None,
        support_checker: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._title_provider = title_provider or get_foreground_window_title
        self._support_checker = support_checker
        self._focus_supported: Optional[bool] = None
        self._hidden_by_focus = False
        self._last_title = ""

    def _is_supported(self) -> bool:
        if self._title_provider is not get_foreground_window_title:
            return True
        if self._focus_supported is None:
            checker = self._support_checker or is_focus_query_supported
            self._focus_supported = bool(checker())
        return self._focus_supported

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def is_hidden_by_focus(self) -> bool:
        return self._hidden_by_focus

    @property
    def last_title(self) -> str:
        return self._last_title

    def set_enabled(self, enabled: bool) -> str:
        """Enable or disable focus-based auto-hide; returns `'restore'` if overlays should reappear."""
        self._enabled = bool(enabled)
        if not self._enabled and self._hidden_by_focus:
            self._hidden_by_focus = False
            return "restore"
        return "noop"

    def evaluate_focus(self, *, drag_unlocked: bool = False) -> str:
        """Poll foreground window title and return `'hide'`, `'restore'`, or `'noop'`."""
        if not self._enabled or drag_unlocked or not self._is_supported():
            if self._hidden_by_focus:
                self._hidden_by_focus = False
                return "restore"
            return "noop"

        title = self._title_provider() or ""
        self._last_title = title
        allowed = is_overlay_allowed_for_title(title)

        if not allowed and not self._hidden_by_focus:
            self._hidden_by_focus = True
            return "hide"
        if allowed and self._hidden_by_focus:
            self._hidden_by_focus = False
            return "restore"
        return "noop"
