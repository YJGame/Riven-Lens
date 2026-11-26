"""Hot-reloadable global hotkey manager (Win32 RegisterHotKey + `keyboard` fallback).

Supports single keys (`F1`..`F12`, `ESC`, etc.) and modifier combinations
(`Alt+R`, `Ctrl+Shift+R`, `Ctrl+F8`, etc.). Never registers `F10` as an exit
hotkey — application exit is strictly controlled via the system tray menu.
"""

from __future__ import annotations

import ctypes
import select
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

# Win32 constants
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

HOTKEY_ID_TOGGLE = 2001
HOTKEY_ID_WM = 2002
HOTKEY_ID_HIDE = 2003
HOTKEY_ID_LOCK = 2004
HOTKEY_ID_EXPORT = 2005
HOTKEY_ID_OPEN_PANEL = 2006

DEFAULT_LOCK_HOTKEY = "Ctrl+Alt+L"
DEFAULT_EXPORT_HOTKEY = "Ctrl+Alt+S"
DEFAULT_OPEN_PANEL_HOTKEY = "Ctrl+Alt+D"

VK_ESCAPE = 0x1B

# Virtual key table for supported keys
_KEY_TO_VK: Dict[str, Tuple[int, str, str]] = {
    # canonical_upper: (win32_vk, display_name, keyboard_lib_name)
    **{f"F{i}": (0x6F + i, f"F{i}", f"f{i}") for i in range(1, 13)},
    **{chr(c): (c, chr(c), chr(c).lower()) for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): (0x30 + d, str(d), str(d)) for d in range(10)},
    "ESC": (0x1B, "ESC", "esc"),
    "ESCAPE": (0x1B, "ESC", "esc"),
    "TAB": (0x09, "Tab", "tab"),
    "SPACE": (0x20, "Space", "space"),
    "INSERT": (0x2D, "Insert", "insert"),
    "INS": (0x2D, "Insert", "insert"),
    "DELETE": (0x2E, "Delete", "delete"),
    "DEL": (0x2E, "Delete", "delete"),
    "HOME": (0x24, "Home", "home"),
    "END": (0x23, "End", "end"),
    "PAGEUP": (0x21, "PageUp", "page up"),
    "PGUP": (0x21, "PageUp", "page up"),
    "PAGEDOWN": (0x22, "PageDown", "page down"),
    "PGDN": (0x22, "PageDown", "page down"),
    "PAUSE": (0x13, "Pause", "pause"),
    "`": (0xC0, "`", "`"),
    "-": (0xBD, "-", "-"),
    "=": (0xBB, "=", "="),
    "[": (0xDB, "[", "["),
    "]": (0xDD, "]", "]"),
}


@dataclass(frozen=True)
class ParsedHotkey:
    """Normalized representation of a user hotkey string."""

    display: str      # e.g. "Ctrl+Shift+R", "F8", "ESC"
    win_mods: int     # Win32 MOD_* bitmask (excluding MOD_NOREPEAT)
    win_vk: int       # Win32 VK_* code
    kb_spec: str      # `keyboard` library syntax (e.g. "ctrl+shift+r", "f8", "esc")

    @property
    def is_plain_esc(self) -> bool:
        return self.win_mods == 0 and self.win_vk == VK_ESCAPE


def parse_hotkey_string(raw: str) -> ParsedHotkey:
    """Parse a human-readable hotkey string such as `F8`, `Alt+R`, `Ctrl+Shift+R`, or `ESC`."""
    cleaned = (raw or "").strip()
    if not cleaned:
        raise ValueError("热键不能为空")

    tokens = [tok.strip() for tok in cleaned.split("+") if tok.strip()]
    if not tokens:
        raise ValueError(f"无效热键: '{raw}'")

    mods = 0
    mod_names: List[str] = []
    kb_mods: List[str] = []
    main_key: Optional[str] = None

    for tok in tokens:
        up = tok.upper()
        if up in ("CTRL", "CONTROL"):
            if not (mods & MOD_CONTROL):
                mods |= MOD_CONTROL
                mod_names.append("Ctrl")
                kb_mods.append("ctrl")
        elif up in ("ALT", "OPTION", "OPT"):
            if not (mods & MOD_ALT):
                mods |= MOD_ALT
                mod_names.append("Alt")
                kb_mods.append("alt")
        elif up == "SHIFT":
            if not (mods & MOD_SHIFT):
                mods |= MOD_SHIFT
                mod_names.append("Shift")
                kb_mods.append("shift")
        elif up in ("WIN", "META", "SUPER", "CMD", "COMMAND"):
            if not (mods & MOD_WIN):
                mods |= MOD_WIN
                mod_names.append("Cmd" if up in ("CMD", "COMMAND") else "Win")
                kb_mods.append("cmd" if up in ("CMD", "COMMAND") else "windows")
        else:
            if main_key is not None:
                raise ValueError(f"不支持多个主键组合: '{raw}'")
            main_key = up

    if main_key is None or main_key not in _KEY_TO_VK:
        raise ValueError(f"不支持的按键: '{raw}'（支持 F1~F12、ESC、字母/数字及 Ctrl/Alt/Shift 组合）")

    vk, disp_key, kb_key = _KEY_TO_VK[main_key]
    display = "+".join(mod_names + [disp_key])
    kb_spec = "+".join(kb_mods + [kb_key])
    return ParsedHotkey(display=display, win_mods=mods, win_vk=vk, kb_spec=kb_spec)


@dataclass
class HotkeyApplyReport:
    """Result summary returned after registering / hot-reloading hotkeys."""

    ok: bool
    toggle_hotkey: str = ""
    wm_hotkey: str = ""
    hide_hotkey: str = ""
    open_panel_hotkey: str = DEFAULT_OPEN_PANEL_HOTKEY
    conflicts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class _Win32HotkeyLoop(QThread):
    """Background thread running Win32 RegisterHotKey + GetMessageW message pump."""

    hotkey_fired = pyqtSignal(int, str)  # hotkey_id, source

    def __init__(self, bindings: Dict[int, ParsedHotkey]) -> None:
        super().__init__()
        self._bindings = dict(bindings)
        self._win_tid: Optional[int] = None
        self.registered_ids: Dict[int, bool] = {}
        self.error_codes: Dict[int, int] = {}
        self._ready = False

    def run(self) -> None:
        if sys.platform != "win32":
            self._ready = True
            return

        from ctypes import byref, wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._win_tid = int(kernel32.GetCurrentThreadId())

        any_ok = False
        for hk_id, parsed in self._bindings.items():
            ok = bool(user32.RegisterHotKey(None, hk_id, parsed.win_mods | MOD_NOREPEAT, parsed.win_vk))
            self.registered_ids[hk_id] = ok
            if ok:
                any_ok = True
            else:
                self.error_codes[hk_id] = int(kernel32.GetLastError())

        self._ready = True
        if not any_ok:
            return

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.hotkey_fired.emit(int(msg.wParam), "Win32 RegisterHotKey")
        finally:
            for hk_id, was_ok in self.registered_ids.items():
                if was_ok:
                    user32.UnregisterHotKey(None, hk_id)

    def wait_until_registered(self, timeout_sec: float = 1.0) -> None:
        deadline = time.monotonic() + timeout_sec
        while not self._ready and time.monotonic() < deadline:
            time.sleep(0.01)

    def stop_loop(self) -> None:
        if sys.platform == "win32" and self._win_tid is not None:
            ctypes.windll.user32.PostThreadMessageW(self._win_tid, WM_QUIT, 0, 0)


# Linux evdev KEY_* name -> canonical key name matching `_KEY_TO_VK`
_EVDEV_KEY_NAME_TO_CANON: Dict[str, str] = {
    **{f"KEY_F{i}": f"F{i}" for i in range(1, 13)},
    **{f"KEY_{chr(c)}": chr(c) for c in range(ord("A"), ord("Z") + 1)},
    **{f"KEY_{d}": str(d) for d in range(10)},
    "KEY_ESC": "ESC",
    "KEY_TAB": "TAB",
    "KEY_SPACE": "SPACE",
    "KEY_INSERT": "INSERT",
    "KEY_DELETE": "DELETE",
    "KEY_HOME": "HOME",
    "KEY_END": "END",
    "KEY_PAGEUP": "PAGEUP",
    "KEY_PAGEDOWN": "PAGEDOWN",
    "KEY_PAUSE": "PAUSE",
    "KEY_GRAVE": "`",
    "KEY_MINUS": "-",
    "KEY_EQUAL": "=",
    "KEY_LEFTBRACE": "[",
    "KEY_RIGHTBRACE": "]",
}

_EVDEV_MOD_MAP: Dict[str, int] = {
    "KEY_LEFTCTRL": MOD_CONTROL,
    "KEY_RIGHTCTRL": MOD_CONTROL,
    "KEY_LEFTALT": MOD_ALT,
    "KEY_RIGHTALT": MOD_ALT,
    "KEY_LEFTSHIFT": MOD_SHIFT,
    "KEY_RIGHTSHIFT": MOD_SHIFT,
    "KEY_LEFTMETA": MOD_WIN,
    "KEY_RIGHTMETA": MOD_WIN,
}


class _EvdevHotkeyLoop(QThread):
    """Non-suppressing Linux `/dev/input/event*` global hotkey listener (works on pure Wayland & X11).

    Requires `python-evdev` and read access to `/dev/input/event*` (user in `input` group).
    Never grabs/blocks devices, so Warframe/Proton receives every keystroke including ESC!
    """

    hotkey_fired = pyqtSignal(int, str)  # hotkey_id, source

    def __init__(self, bindings: Dict[int, ParsedHotkey]) -> None:
        super().__init__()
        self._bindings = dict(bindings)
        self._stop_event = threading.Event()
        self.started_ok = False
        self.error_reason = ""
        self._ready = False

    def stop_loop(self) -> None:
        self._stop_event.set()

    def wait_until_registered(self, timeout_sec: float = 0.8) -> None:
        deadline = time.monotonic() + timeout_sec
        while not self._ready and time.monotonic() < deadline:
            time.sleep(0.01)

    def run(self) -> None:
        if not sys.platform.startswith("linux"):
            self._ready = True
            return
        try:
            import evdev  # type: ignore[import-not-found]
            from evdev import ecodes  # type: ignore[import-not-found]
        except Exception as exc:
            self.error_reason = f"evdev not installed ({exc})"
            self._ready = True
            return

        devices = []
        try:
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    caps = dev.capabilities()
                    key_codes = caps.get(ecodes.EV_KEY, [])
                    # Keep devices that have standard keyboard keys (ESC / F8 / letters)
                    if ecodes.KEY_ESC in key_codes or ecodes.KEY_F8 in key_codes or ecodes.KEY_A in key_codes:
                        devices.append(dev)
                    else:
                        dev.close()
                except Exception:
                    continue
        except Exception as exc:
            self.error_reason = f"cannot scan /dev/input: {exc}"
            self._ready = True
            return

        if not devices:
            self.error_reason = "no readable /dev/input keyboard devices (add user to `input` group or use IPC shortcuts)"
            self._ready = True
            return

        # Precompute lookup: (win_mods, win_vk) -> hotkey_id
        combo_to_id: Dict[Tuple[int, int], int] = {
            (p.win_mods, p.win_vk): hk_id for hk_id, p in self._bindings.items()
        }
        code_to_vk: Dict[int, int] = {}
        for ev_name, canon in _EVDEV_KEY_NAME_TO_CANON.items():
            ev_code = getattr(ecodes, ev_name, None)
            if ev_code is not None and canon in _KEY_TO_VK:
                code_to_vk[int(ev_code)] = _KEY_TO_VK[canon][0]
        code_to_mod: Dict[int, int] = {}
        for ev_name, mod_bit in _EVDEV_MOD_MAP.items():
            ev_code = getattr(ecodes, ev_name, None)
            if ev_code is not None:
                code_to_mod[int(ev_code)] = mod_bit

        pressed_mod_codes: Set[int] = set()
        self.started_ok = True
        self._ready = True

        fd_map = {dev.fd: dev for dev in devices}
        try:
            while not self._stop_event.is_set():
                try:
                    rlist, _, _ = select.select(list(fd_map.keys()), [], [], 0.25)
                except Exception:
                    break
                for fd in rlist:
                    dev = fd_map.get(fd)
                    if dev is None:
                        continue
                    try:
                        for event in dev.read():
                            if event.type != ecodes.EV_KEY:
                                continue
                            code = int(event.code)
                            val = int(event.value)  # 0=up, 1=down, 2=repeat
                            if code in code_to_mod:
                                if val == 0:
                                    pressed_mod_codes.discard(code)
                                else:
                                    pressed_mod_codes.add(code)
                                continue
                            if val == 1 and code in code_to_vk:
                                cur_mods = 0
                                for mc in pressed_mod_codes:
                                    cur_mods |= code_to_mod.get(mc, 0)
                                vk = code_to_vk[code]
                                hit_id = combo_to_id.get((cur_mods, vk))
                                if hit_id is not None:
                                    self.hotkey_fired.emit(hit_id, "Linux evdev")
                    except OSError:
                        fd_map.pop(fd, None)
                        try:
                            dev.close()
                        except Exception:
                            pass
        finally:
            for dev in list(fd_map.values()):
                try:
                    dev.close()
                except Exception:
                    pass


# macOS Quartz virtual keycode (HIToolbox/Events.h) -> canonical key name matching `_KEY_TO_VK`.
# Using physical keycodes ensures combos like `Ctrl+Alt+D` work even when Option transforms `.char` (e.g. `∂`).
_MACOS_VK_TO_CANON: Dict[int, str] = {
    0x00: "A", 0x0B: "B", 0x08: "C", 0x02: "D", 0x0E: "E", 0x03: "F", 0x05: "G",
    0x04: "H", 0x22: "I", 0x26: "J", 0x28: "K", 0x25: "L", 0x2E: "M", 0x2D: "N",
    0x1F: "O", 0x23: "P", 0x0C: "Q", 0x0F: "R", 0x01: "S", 0x11: "T", 0x20: "U",
    0x09: "V", 0x0D: "W", 0x07: "X", 0x10: "Y", 0x06: "Z",
    0x1D: "0", 0x12: "1", 0x13: "2", 0x14: "3", 0x15: "4",
    0x17: "5", 0x16: "6", 0x1A: "7", 0x1C: "8", 0x19: "9",
    0x7A: "F1", 0x78: "F2", 0x63: "F3", 0x76: "F4", 0x60: "F5", 0x61: "F6",
    0x62: "F7", 0x64: "F8", 0x65: "F9", 0x6D: "F10", 0x67: "F11", 0x6F: "F12",
    0x35: "ESC", 0x30: "TAB", 0x31: "SPACE", 0x75: "DELETE",
    0x73: "HOME", 0x77: "END", 0x74: "PAGEUP", 0x79: "PAGEDOWN",
    0x32: "`", 0x1B: "-", 0x18: "=", 0x21: "[", 0x1E: "]",
}

_PYNPUT_SPECIAL_TO_CANON: Dict[str, str] = {
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
    "esc": "ESC",
    "tab": "TAB",
    "space": "SPACE",
    "insert": "INSERT",
    "delete": "DELETE",
    "home": "HOME",
    "end": "END",
    "page_up": "PAGEUP",
    "page_down": "PAGEDOWN",
    "pause": "PAUSE",
}


def _pynput_key_to_mod_bit(key: object) -> int:
    name = getattr(key, "name", None)
    if not isinstance(name, str):
        return 0
    low = name.lower()
    if low in ("ctrl", "ctrl_l", "ctrl_r"):
        return MOD_CONTROL
    if low in ("alt", "alt_l", "alt_r", "alt_gr"):
        return MOD_ALT
    if low in ("shift", "shift_l", "shift_r"):
        return MOD_SHIFT
    if low in ("cmd", "cmd_l", "cmd_r"):
        return MOD_WIN
    return 0


def _pynput_key_to_win_vk(key: object) -> Optional[int]:
    """Map a `pynput` Key/KeyCode (macOS Quartz) to our canonical Win32 VK code."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        canon = _PYNPUT_SPECIAL_TO_CANON.get(name.lower())
        if canon and canon in _KEY_TO_VK:
            return _KEY_TO_VK[canon][0]

    vk = getattr(key, "vk", None)
    if isinstance(vk, int) and vk in _MACOS_VK_TO_CANON:
        canon = _MACOS_VK_TO_CANON[vk]
        if canon in _KEY_TO_VK:
            return _KEY_TO_VK[canon][0]

    ch = getattr(key, "char", None)
    if isinstance(ch, str) and len(ch) == 1:
        up = ch.upper()
        if up in _KEY_TO_VK:
            return _KEY_TO_VK[up][0]
    return None


class _MacOSHotkeyLoop(QThread):
    """Non-suppressing macOS global hotkey listener via `pynput` (Quartz CGEventTap).

    Never suppresses/swallows keystrokes so Warframe (CrossOver/Whisky/GeForce NOW/Parallels)
    always receives ESC and all game keys normally. Requires macOS Accessibility or Input
    Monitoring permission for global keystrokes outside our own windows.
    """

    hotkey_fired = pyqtSignal(int, str)  # hotkey_id, source

    def __init__(self, bindings: Dict[int, ParsedHotkey]) -> None:
        super().__init__()
        self._bindings = dict(bindings)
        self._stop_event = threading.Event()
        self._listener: Optional[object] = None
        self.started_ok = False
        self.error_reason = ""
        self._ready = False

    def stop_loop(self) -> None:
        self._stop_event.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.stop()  # type: ignore[attr-defined]
            except Exception:
                pass

    def wait_until_registered(self, timeout_sec: float = 0.8) -> None:
        deadline = time.monotonic() + timeout_sec
        while not self._ready and time.monotonic() < deadline:
            time.sleep(0.01)

    def run(self) -> None:
        if sys.platform != "darwin":
            self._ready = True
            return
        try:
            from pynput import keyboard as pynput_kb  # type: ignore[import-not-found]
        except Exception as exc:
            self.error_reason = f"pynput not installed ({exc})"
            self._ready = True
            return

        combo_to_id: Dict[Tuple[int, int], int] = {
            (p.win_mods, p.win_vk): hk_id for hk_id, p in self._bindings.items()
        }
        pressed_mods: Set[int] = set()
        last_fire_ts: Dict[int, float] = {}

        def _on_press(key: object) -> None:
            if self._stop_event.is_set():
                return
            mod_bit = _pynput_key_to_mod_bit(key)
            if mod_bit:
                pressed_mods.add(mod_bit)
                return
            vk = _pynput_key_to_win_vk(key)
            if vk is None:
                return
            cur_mods = 0
            for mb in pressed_mods:
                cur_mods |= mb
            hit_id = combo_to_id.get((cur_mods, vk))
            if hit_id is not None:
                now = time.monotonic()
                if now - last_fire_ts.get(hit_id, 0.0) >= 0.18:
                    last_fire_ts[hit_id] = now
                    self.hotkey_fired.emit(hit_id, "macOS Quartz")

        def _on_release(key: object) -> None:
            mod_bit = _pynput_key_to_mod_bit(key)
            if mod_bit:
                pressed_mods.discard(mod_bit)

        try:
            listener = pynput_kb.Listener(on_press=_on_press, on_release=_on_release)
            self._listener = listener
            listener.start()
            self.started_ok = True
            self._ready = True
            while not self._stop_event.wait(timeout=0.2):
                if not listener.is_alive():
                    break
        except Exception as exc:
            self.error_reason = f"macOS Quartz listener failed ({exc})"
            self._ready = True
        finally:
            listener = self._listener
            self._listener = None
            if listener is not None:
                try:
                    listener.stop()  # type: ignore[attr-defined]
                except Exception:
                    pass


_ACTION_NAME_TO_ID: Dict[str, int] = {
    "toggle": HOTKEY_ID_TOGGLE,
    "wm": HOTKEY_ID_WM,
    "hide": HOTKEY_ID_HIDE,
    "open_panel": HOTKEY_ID_OPEN_PANEL,
    "lock": HOTKEY_ID_LOCK,
    "export": HOTKEY_ID_EXPORT,
}


class DynamicHotkeyManager(QObject):
    """Manages hot-reloadable global hotkeys + anti-mispress lock (`Ctrl+Alt+L`) & export (`Ctrl+Alt+S`)."""

    toggle_triggered = pyqtSignal(str)
    wm_triggered = pyqtSignal(str)
    hide_triggered = pyqtSignal(str)
    open_panel_triggered = pyqtSignal(str)
    lock_toggled = pyqtSignal(str)
    export_triggered = pyqtSignal(str)

    def __init__(self, logger: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self._log = logger or (lambda _msg: None)
        self._win_thread: Optional[_Win32HotkeyLoop] = None
        self._evdev_thread: Optional[_EvdevHotkeyLoop] = None
        self._macos_thread: Optional[_MacOSHotkeyLoop] = None
        self._kb_handles: List[object] = []
        self.active_toggle: Optional[ParsedHotkey] = None
        self.active_wm: Optional[ParsedHotkey] = None
        self.active_hide: Optional[ParsedHotkey] = None
        self.active_open_panel: Optional[ParsedHotkey] = None
        self._hotkeys_locked: bool = False
        self.linux_hotkey_backend: str = ""
        self.macos_hotkey_backend: str = ""

    @property
    def hotkeys_locked(self) -> bool:
        return self._hotkeys_locked

    def set_hotkeys_locked(self, locked: bool) -> None:
        self._hotkeys_locked = bool(locked)

    def toggle_hotkeys_locked(self) -> bool:
        self._hotkeys_locked = not self._hotkeys_locked
        return self._hotkeys_locked

    def unregister_all(self) -> None:
        """Unregister all Win32/Linux evdev hotkeys and remove all `keyboard` library hooks cleanly."""
        if self._win_thread is not None:
            self._win_thread.stop_loop()
            self._win_thread.wait(1200)
            self._win_thread = None

        if self._evdev_thread is not None:
            self._evdev_thread.stop_loop()
            self._evdev_thread.wait(1000)
            self._evdev_thread = None

        if self._macos_thread is not None:
            self._macos_thread.stop_loop()
            self._macos_thread.wait(1000)
            self._macos_thread = None

        if self._kb_handles:
            try:
                import keyboard as kb_lib

                for handle in self._kb_handles:
                    try:
                        kb_lib.remove_hotkey(handle)
                    except Exception:
                        try:
                            kb_lib.unhook(handle)
                        except Exception:
                            pass
            except Exception:
                pass
            self._kb_handles.clear()

    def apply_hotkeys(
        self,
        toggle_str: str,
        wm_str: str,
        hide_str: str,
        open_panel_str: str = DEFAULT_OPEN_PANEL_HOTKEY,
    ) -> HotkeyApplyReport:
        """Validate, unregister old hotkeys, and register new hotkeys live (no restart needed)."""
        report = HotkeyApplyReport(ok=False)
        try:
            p_toggle = parse_hotkey_string(toggle_str)
            p_wm = parse_hotkey_string(wm_str)
            p_hide = parse_hotkey_string(hide_str)
            p_open_panel = parse_hotkey_string(open_panel_str or DEFAULT_OPEN_PANEL_HOTKEY)
        except ValueError as exc:
            report.errors.append(str(exc))
            return report

        # Prevent duplicate bindings across the 4 customizable actions
        displays = [
            ("自动监控", p_toggle.display),
            ("拉取 WM", p_wm.display),
            ("隐藏悬浮窗", p_hide.display),
            ("打开设置面板", p_open_panel.display),
        ]
        seen: Dict[str, str] = {}
        for label, disp in displays:
            if disp in seen:
                report.errors.append(f"热键冲突：「{seen[disp]}」与「{label}」不能绑定同一个按键 ({disp})")
                return report
            seen[disp] = label

        # Stop & unregister previous bindings cleanly
        self.unregister_all()

        self.active_toggle = p_toggle
        self.active_wm = p_wm
        self.active_hide = p_hide
        self.active_open_panel = p_open_panel
        report.toggle_hotkey = p_toggle.display
        report.wm_hotkey = p_wm.display
        report.hide_hotkey = p_hide.display
        report.open_panel_hotkey = p_open_panel.display

        p_lock = parse_hotkey_string(DEFAULT_LOCK_HOTKEY)
        p_export = parse_hotkey_string(DEFAULT_EXPORT_HOTKEY)

        # ---- macOS (Apple Silicon & Intel): non-suppressing Quartz `pynput` + UNIX IPC ----
        if sys.platform == "darwin":
            mac_bindings: Dict[int, ParsedHotkey] = {
                HOTKEY_ID_TOGGLE: p_toggle,
                HOTKEY_ID_WM: p_wm,
                HOTKEY_ID_HIDE: p_hide,
                HOTKEY_ID_OPEN_PANEL: p_open_panel,
                HOTKEY_ID_LOCK: p_lock,
                HOTKEY_ID_EXPORT: p_export,
            }
            mac_thread = _MacOSHotkeyLoop(mac_bindings)
            mac_thread.hotkey_fired.connect(self._on_win32_hotkey)
            self._macos_thread = mac_thread
            mac_thread.start()
            mac_thread.wait_until_registered(0.8)
            if mac_thread.started_ok:
                self.macos_hotkey_backend = "pynput+ipc"
                self._log(
                    f"macOS Quartz global hotkeys active (non-suppressing): "
                    f"{p_toggle.display} / {p_wm.display} / {p_hide.display} / {p_open_panel.display}"
                )
            else:
                self._macos_thread = None
                self.macos_hotkey_backend = "ipc_only"
                self._log(
                    f"macOS Quartz listener unavailable ({mac_thread.error_reason}); "
                    "global hotkeys available via IPC (`--trigger toggle|wm|hide|open_panel`)."
                )
            report.ok = True
            return report

        # ---- Linux (Wayland & X11): use non-suppressing evdev + UNIX IPC trigger server ----
        if sys.platform != "win32":
            linux_bindings: Dict[int, ParsedHotkey] = {
                HOTKEY_ID_TOGGLE: p_toggle,
                HOTKEY_ID_WM: p_wm,
                HOTKEY_ID_HIDE: p_hide,
                HOTKEY_ID_OPEN_PANEL: p_open_panel,
                HOTKEY_ID_LOCK: p_lock,
                HOTKEY_ID_EXPORT: p_export,
            }
            ev_thread = _EvdevHotkeyLoop(linux_bindings)
            ev_thread.hotkey_fired.connect(self._on_win32_hotkey)
            self._evdev_thread = ev_thread
            ev_thread.start()
            ev_thread.wait_until_registered(0.8)
            if ev_thread.started_ok:
                self.linux_hotkey_backend = "evdev+ipc"
                self._log(
                    f"Linux evdev global hotkeys active (non-suppressing): "
                    f"{p_toggle.display} / {p_wm.display} / {p_hide.display} / {p_open_panel.display}"
                )
            else:
                self._evdev_thread = None
                self.linux_hotkey_backend = "ipc_only"
                self._log(
                    f"Linux evdev unavailable ({ev_thread.error_reason}); "
                    "global hotkeys available via Wayland IPC (`--trigger toggle|wm|hide|open_panel`)."
                )
            report.ok = True
            return report

        # ---- Windows: RegisterHotKey + `keyboard` fallback ----
        # Register Win32 hotkeys (NEVER RegisterHotKey on plain ESC so Warframe's ESC menu still works!)
        win_bindings: Dict[int, ParsedHotkey] = {
            HOTKEY_ID_TOGGLE: p_toggle,
            HOTKEY_ID_WM: p_wm,
            HOTKEY_ID_OPEN_PANEL: p_open_panel,
            HOTKEY_ID_LOCK: p_lock,
            HOTKEY_ID_EXPORT: p_export,
        }
        if not p_hide.is_plain_esc:
            win_bindings[HOTKEY_ID_HIDE] = p_hide

        win_thread = _Win32HotkeyLoop(win_bindings)
        win_thread.hotkey_fired.connect(self._on_win32_hotkey)
        self._win_thread = win_thread
        win_thread.start()
        win_thread.wait_until_registered(1.0)

        # Check Win32 registration status & attach `keyboard` fallback where needed
        id_meta = [
            (HOTKEY_ID_TOGGLE, p_toggle, "自动监控", False),
            (HOTKEY_ID_WM, p_wm, "拉取 WM", False),
            (HOTKEY_ID_OPEN_PANEL, p_open_panel, "打开设置面板", True),
            (HOTKEY_ID_LOCK, p_lock, "热键锁定", True),
            (HOTKEY_ID_EXPORT, p_export, "导出展示图", True),
        ]
        if not p_hide.is_plain_esc:
            id_meta.append((HOTKEY_ID_HIDE, p_hide, "隐藏悬浮窗", False))

        try:
            import keyboard as kb_lib
        except Exception:
            kb_lib = None  # type: ignore[assignment]

        for hk_id, parsed, label, is_auxiliary in id_meta:
            if win_thread.registered_ids.get(hk_id):
                self._log(f"Win32 RegisterHotKey({label}={parsed.display}) registered OK.")
            else:
                err_code = win_thread.error_codes.get(hk_id, 0)
                if not is_auxiliary:
                    conflict_msg = f"「{label}」热键 {parsed.display} 可能已被其他软件占用 (Win32 err={err_code})，已自动启用兼容监听回退。"
                    report.conflicts.append(conflict_msg)
                    self._log(conflict_msg)
                else:
                    self._log(f"Auxiliary hotkey {label}={parsed.display} using keyboard fallback (Win32 err={err_code}).")
                if kb_lib is not None:
                    try:
                        h = kb_lib.add_hotkey(
                            parsed.kb_spec,
                            lambda hid=hk_id: self._dispatch_hotkey(hid, "keyboard fallback"),
                            suppress=False,
                        )
                        self._kb_handles.append(h)
                    except Exception as exc:
                        if not is_auxiliary:
                            report.errors.append(f"注册回退热键 {parsed.display} 失败: {exc}")

        # Always attach non-suppressing `keyboard` listener when hide key is plain ESC
        if p_hide.is_plain_esc and kb_lib is not None:
            try:
                h_esc = kb_lib.on_press_key(
                    "esc",
                    lambda _e: self.hide_triggered.emit("keyboard"),
                    suppress=False,
                )
                self._kb_handles.append(h_esc)
                self._log("Registered non-suppressing `keyboard` listener for ESC.")
            except Exception as exc:
                self._log(f"ESC keyboard hook skipped: {exc!r}")

        report.ok = len(report.errors) == 0
        return report

    def _dispatch_hotkey(self, hk_id: int, source: str) -> None:
        if hk_id == HOTKEY_ID_OPEN_PANEL:
            self.open_panel_triggered.emit(source)
            return
        if hk_id == HOTKEY_ID_LOCK:
            self.lock_toggled.emit(source)
            return
        if hk_id == HOTKEY_ID_EXPORT:
            self.export_triggered.emit(source)
            return
        # When hotkeys are locked (`Ctrl+Alt+L`), shield F8 and F9 against accidental presses!
        if self._hotkeys_locked and hk_id in (HOTKEY_ID_TOGGLE, HOTKEY_ID_WM):
            self._log(f"Hotkeys locked -> ignored hotkey id={hk_id} (source={source})")
            return
        if hk_id == HOTKEY_ID_TOGGLE:
            self.toggle_triggered.emit(source)
        elif hk_id == HOTKEY_ID_WM:
            self.wm_triggered.emit(source)
        elif hk_id == HOTKEY_ID_HIDE:
            self.hide_triggered.emit(source)

    def dispatch_action_name(self, action: str, source: str = "WaylandIPC") -> bool:
        """Dispatch a named hotkey action (`toggle`, `wm`, `hide`, `open_panel`, `lock`, `export`)."""
        hk_id = _ACTION_NAME_TO_ID.get((action or "").strip().lower())
        if hk_id is None:
            return False
        self._dispatch_hotkey(hk_id, source)
        return True

    def _on_win32_hotkey(self, hk_id: int, source: str) -> None:
        self._dispatch_hotkey(hk_id, source)
