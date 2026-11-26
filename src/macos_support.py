"""macOS (Apple Silicon & Intel) native compatibility layer for 裂罅透镜 (Riven Lens).

Provides zero-dependency, crash-safe macOS adaptations while leaving Windows and
Linux behavior untouched:
  1. Native `NSWindow` overlay styling via `libobjc.A.dylib` (explicit arm64-safe
     `CFUNCTYPE` signatures for `objc_msgSend`):
       - `setHidesOnDeactivate:(NO)` so Qt `Tool` overlays NEVER vanish when
         clicking back into Warframe (CrossOver / Whisky / GeForce NOW / Parallels).
       - `setLevel:(NSStatusWindowLevel)` + `CanJoinAllSpaces | FullScreenAuxiliary |
         Stationary` so overlays float cleanly above borderless & full-screen Spaces.
       - `setIgnoresMouseEvents:(click_through)` for reliable native click-through.
       - `setHasShadow:(NO)` to avoid translucent border artifacts.
  2. macOS TCC privacy permission checks:
       - Screen Recording (`CGPreflightScreenCaptureAccess` / `CGRequestScreenCaptureAccess`)
       - Accessibility (`AXIsProcessTrusted`)
       - Direct System Settings pane deep-links (`open_privacy_pane`).
  3. Native silent `/usr/sbin/screencapture -x -t png` fallback capturer for
     macOS 12-15+ (Retina-safe, ScreenCaptureKit-compatible when `mss` fails or
     when `RIVEN_MAC_CAPTURE=screencapture` is requested).
  4. Frontmost application & active window title query (`osascript`) for
     `SmartFocusWatcher`, recognizing Warframe and common Mac Warframe runners.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np


# AppKit / CoreGraphics constants (macOS 10.15 - 15+)
NS_STATUS_WINDOW_LEVEL = 25
NS_COLLECTION_CAN_JOIN_ALL_SPACES = 1 << 0
NS_COLLECTION_STATIONARY = 1 << 4
NS_COLLECTION_FULL_SCREEN_AUXILIARY = 1 << 8
NS_OVERLAY_COLLECTION_BEHAVIOR = (
    NS_COLLECTION_CAN_JOIN_ALL_SPACES
    | NS_COLLECTION_STATIONARY
    | NS_COLLECTION_FULL_SCREEN_AUXILIARY
)

# Common macOS wrappers/cloud clients used to run Warframe on Apple Silicon / Intel Macs
MACOS_WARFRAME_HOST_KEYWORDS = (
    "crossover",
    "whisky",
    "wine64",
    "wine-preloader",
    "geforce now",
    "geforcenow",
    "parallels",
    "moonlight",
    "playcover",
    "boosteroid",
)


def is_macos() -> bool:
    """Return True when running on macOS (`darwin`)."""
    return sys.platform == "darwin"


def get_macos_app_support_dir() -> Path:
    """Return standard writable macOS Application Support directory."""
    return Path.home() / "Library" / "Application Support" / "WarframeRivenOverlay"


def get_macos_system_summary() -> str:
    """Return concise macOS version + architecture summary for diagnostics."""
    ver = platform.mac_ver()[0] or "unknown"
    arch = platform.machine() or "unknown"
    return f"macOS {ver} ({arch})"


# ---- TCC Privacy Permissions (Screen Recording & Accessibility) ----


@dataclass(frozen=True)
class MacOSPermissionReport:
    """Snapshot of macOS privacy permissions required by Riven Lens."""

    screen_recording: bool
    accessibility: bool
    macos_summary: str

    @property
    def all_granted(self) -> bool:
        return self.screen_recording and self.accessibility

    def missing_items_zh(self) -> List[str]:
        missing: List[str] = []
        if not self.screen_recording:
            missing.append("屏幕录制（Screen Recording，用于纯视觉 OCR 截屏）")
        if not self.accessibility:
            missing.append("辅助功能 / 输入监控（Accessibility，用于全局 F8/F9/ESC 热键）")
        return missing


def has_screen_recording_permission() -> bool:
    """Check macOS Screen Recording TCC access without triggering a system prompt."""
    if not is_macos():
        return True
    try:
        cg_path = ctypes.util.find_library("CoreGraphics")
        if not cg_path:
            return True
        cg = ctypes.CDLL(cg_path)
        fn = getattr(cg, "CGPreflightScreenCaptureAccess", None)
        if fn is None:
            return True
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return True


def request_screen_recording_permission() -> bool:
    """Trigger the native macOS Screen Recording permission prompt once (if not yet granted)."""
    if not is_macos():
        return True
    try:
        cg_path = ctypes.util.find_library("CoreGraphics")
        if not cg_path:
            return True
        cg = ctypes.CDLL(cg_path)
        fn = getattr(cg, "CGRequestScreenCaptureAccess", None)
        if fn is None:
            return has_screen_recording_permission()
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return True


def has_accessibility_permission() -> bool:
    """Check macOS Accessibility TCC trust (`AXIsProcessTrusted`) without prompting."""
    if not is_macos():
        return True
    try:
        as_path = ctypes.util.find_library("ApplicationServices")
        if not as_path:
            return True
        app_services = ctypes.CDLL(as_path)
        fn = getattr(app_services, "AXIsProcessTrusted", None)
        if fn is None:
            return True
        fn.argtypes = []
        fn.restype = ctypes.c_bool
        return bool(fn())
    except Exception:
        return True


def check_macos_permissions() -> MacOSPermissionReport:
    """Inspect current macOS Screen Recording & Accessibility permissions."""
    return MacOSPermissionReport(
        screen_recording=has_screen_recording_permission(),
        accessibility=has_accessibility_permission(),
        macos_summary=get_macos_system_summary(),
    )


def open_privacy_pane(pane: str) -> bool:
    """Open the corresponding macOS System Settings -> Privacy & Security pane."""
    if not is_macos():
        return False
    urls = {
        "screen_recording": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    }
    target = urls.get((pane or "").strip().lower())
    if not target:
        return False
    try:
        subprocess.Popen(["open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


# ---- Native NSWindow Overlay Styling (Apple Silicon arm64 & Intel x86_64 safe) ----


def apply_macos_overlay_window_style(
    win_id: int,
    *,
    click_through: bool = True,
    floating_level: int = NS_STATUS_WINDOW_LEVEL,
) -> bool:
    """Apply native Cocoa `NSWindow` overlay flags from a Qt widget's `winId()` (NSView*).

    Uses explicit `ctypes.CFUNCTYPE` prototypes around `objc_msgSend` so Apple Silicon
    (`arm64`) calling conventions are respected with zero PyObjC dependency.
    """
    if not is_macos() or not win_id:
        return False
    try:
        objc_path = ctypes.util.find_library("objc") or "/usr/lib/libobjc.A.dylib"
        objc = ctypes.CDLL(objc_path)

        sel_register = objc.sel_registerName
        sel_register.argtypes = [ctypes.c_char_p]
        sel_register.restype = ctypes.c_void_p

        msg_send_addr = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not msg_send_addr:
            return False

        msg_ret_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(msg_send_addr)
        msg_set_bool = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool)(msg_send_addr)
        msg_set_long = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(msg_send_addr)
        msg_set_ulong = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong)(msg_send_addr)

        ns_view = ctypes.c_void_p(int(win_id))
        ns_window = msg_ret_ptr(ns_view, sel_register(b"window"))
        if not ns_window:
            return False

        # Never vanish when Warframe takes focus (Qt.WindowType.Tool defaults to hidesOnDeactivate=YES)
        msg_set_bool(ns_window, sel_register(b"setHidesOnDeactivate:"), False)
        # Float above borderless fullscreen windows
        msg_set_long(ns_window, sel_register(b"setLevel:"), int(floating_level))
        # Stay visible across all Spaces & macOS full-screen game Spaces
        msg_set_ulong(ns_window, sel_register(b"setCollectionBehavior:"), NS_OVERLAY_COLLECTION_BEHAVIOR)
        # Native mouse click-through
        msg_set_bool(ns_window, sel_register(b"setIgnoresMouseEvents:"), bool(click_through))
        # Eliminate rectangular drop-shadow artifacts on translucent rounded cards
        msg_set_bool(ns_window, sel_register(b"setHasShadow:"), False)
        return True
    except Exception:
        return False


# ---- Frontmost App & Active Window Title Query ----


def get_macos_foreground_app_and_title() -> Tuple[str, bool]:
    """Return `(combined_title, is_supported)` for the active macOS application/window.

    Reads the frontmost application name without requiring Accessibility, and appends
    the active window title whenever Accessibility permission is granted.
    """
    if not is_macos():
        return "", False

    script = (
        'tell application "System Events"\n'
        '  set frontApp to first application process whose frontmost is true\n'
        '  set appName to name of frontApp\n'
        '  set winTitle to ""\n'
        '  try\n'
        '    set winTitle to name of front window of frontApp\n'
        '  end try\n'
        '  if winTitle is not "" then\n'
        '    return appName & " - " & winTitle\n'
        '  else\n'
        '    return appName\n'
        '  end if\n'
        'end tell'
    )
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            text=True,
        ).strip()
        if out:
            return out, True
    except Exception:
        pass

    # Fallback that only asks Finder/AppKit for frontmost app name (never requires Accessibility)
    try:
        out2 = subprocess.check_output(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first application process whose frontmost is true',
            ],
            stderr=subprocess.DEVNULL,
            timeout=0.5,
            text=True,
        ).strip()
        if out2:
            return out2, True
    except Exception:
        pass

    return "", False


# ---- Native macOS `screencapture` Fallback Capturer ----


@dataclass(frozen=True)
class MacOSMonitorInfo:
    """Monitor geometry entry matching `mss` monitor dictionary keys."""

    left: int
    top: int
    width: int
    height: int
    display_index: int = 1

    def to_dict(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


def detect_macos_capture_backend() -> str:
    """Return preferred macOS capture backend: `'mss'` (default fast path) or `'screencapture'`."""
    forced = os.environ.get("RIVEN_MAC_CAPTURE", "").strip().lower()
    if forced in ("screencapture", "native", "cli"):
        return "screencapture"
    if forced == "mss":
        return "mss"
    return "mss"


class MacOSScreenCapturer:
    """Silent macOS `/usr/sbin/screencapture -x -t png` fallback capturer.

    Works on macOS 12-15+ (Apple Silicon & Intel) via Apple's ScreenCaptureKit
    pipeline even when legacy `CGWindowListCreateImage` (`mss`) is unavailable.
    """

    def __init__(self) -> None:
        self._exe = shutil.which("screencapture") or "/usr/sbin/screencapture"

    @property
    def is_available(self) -> bool:
        return bool(self._exe) and (Path(self._exe).is_file() or shutil.which(self._exe) is not None)

    @staticmethod
    def _decode_png_bytes(raw: bytes) -> np.ndarray:
        if not raw:
            raise RuntimeError("macOS screencapture returned empty PNG bytes.")
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("Failed to decode PNG from macOS screencapture.")
        return img

    def grab_monitor_full(self, monitor_index: int = 1) -> Tuple[np.ndarray, MacOSMonitorInfo]:
        """Silently capture display `monitor_index` (1-based) to an OpenCV BGR array."""
        if not self.is_available:
            raise RuntimeError("macOS `screencapture` tool not found.")

        tmp_fd, tmp_name = tempfile.mkstemp(prefix="riven_mac_cap_", suffix=".png")
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            cmd = [self._exe, "-x", "-t", "png"]
            if monitor_index and monitor_index >= 1:
                cmd.extend(["-D", str(int(monitor_index))])
            cmd.append(str(tmp_path))
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=4.0,
                check=False,
            )
            if res.returncode != 0 or not tmp_path.is_file() or tmp_path.stat().st_size == 0:
                err_txt = (res.stderr or b"").decode("utf-8", errors="ignore").strip()
                perm_hint = (
                    " 请前往「系统设置 -> 隐私与安全性 -> 屏幕录制」勾选允许 裂罅透镜 / 终端，然后重启程序。"
                    if not has_screen_recording_permission()
                    else ""
                )
                raise RuntimeError(f"macOS screencapture failed (rc={res.returncode}): {err_txt}{perm_hint}")
            bgr = self._decode_png_bytes(tmp_path.read_bytes())
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        h, w = int(bgr.shape[0]), int(bgr.shape[1])
        return bgr, MacOSMonitorInfo(left=0, top=0, width=w, height=h, display_index=max(1, int(monitor_index)))
