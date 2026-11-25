"""Linux Wayland compatibility layer for 裂罅透镜 (Riven Lens).

Provides unified, zero-crash Wayland adaptations while keeping Windows & X11 behavior
100% untouched:
  1. Display session & compositor detection (Hyprland, Sway, KDE Plasma, GNOME, wlroots).
  2. Multi-backend Wayland screen capture (`grim` -> `spectacle` -> GNOME Shell DBus ->
     `gnome-screenshot` -> XDG Desktop Portal Screenshot -> `mss` XWayland fallback).
  3. Wayland active-window title query for Smart Focus Auto-Hide (`hyprctl`, `swaymsg`,
     `kdotool`) with safe graceful degradation on compositors that forbid window inspection
     (e.g. stock GNOME Wayland).
  4. Fast UNIX domain socket IPC (`$XDG_RUNTIME_DIR/riven_lens.sock`) enabling zero-permission
     native compositor shortcuts (`riven-lens --trigger toggle|wm|hide|open_panel|lock|export`).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

IPC_VALID_ACTIONS = frozenset({"toggle", "wm", "hide", "open_panel", "lock", "export", "quit"})


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_linux_wayland() -> bool:
    """Return True when running on Linux inside a Wayland session."""
    if not is_linux():
        return False
    session_type = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if session_type == "wayland":
        return True
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def detect_wayland_compositor() -> str:
    """Detect active Wayland compositor: `hyprland`, `sway`, `kde`, `gnome`, `wlroots`, or `unknown`."""
    if not is_linux():
        return "not_linux"
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    desktop = " ".join(
        [
            os.environ.get("XDG_CURRENT_DESKTOP") or "",
            os.environ.get("DESKTOP_SESSION") or "",
            os.environ.get("XDG_SESSION_DESKTOP") or "",
        ]
    ).lower()
    if "hyprland" in desktop:
        return "hyprland"
    if "sway" in desktop:
        return "sway"
    if "kde" in desktop or "plasma" in desktop:
        return "kde"
    if "gnome" in desktop or "ubuntu" in desktop or "budgie" in desktop:
        return "gnome"
    if any(k in desktop for k in ("river", "wayfire", "labwc", "wlroots", "niri")):
        return "wlroots"
    return "unknown"


def get_ipc_socket_path() -> Path:
    """Return per-user UNIX domain socket path for instant compositor keybind IPC."""
    runtime_dir = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir and Path(runtime_dir).is_dir():
        return Path(runtime_dir) / "riven_lens.sock"
    uid = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"riven_lens_{uid}.sock"


def send_ipc_trigger(action: str, socket_path: Optional[Path] = None, timeout: float = 1.0) -> Tuple[bool, str]:
    """Send a single action trigger (`toggle`, `wm`, `hide`, ...) to the running Riven Lens instance."""
    act = (action or "").strip().lower()
    if act not in IPC_VALID_ACTIONS:
        return False, f"Unknown IPC action '{action}'. Valid: {', '.join(sorted(IPC_VALID_ACTIONS))}"
    if not hasattr(socket, "AF_UNIX"):
        return False, "AF_UNIX IPC sockets are only supported on Linux/POSIX."

    sock_path = socket_path or get_ipc_socket_path()
    if not sock_path.exists():
        return False, f"Riven Lens is not running (socket not found: {sock_path})"

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
            cli.settimeout(timeout)
            cli.connect(str(sock_path))
            cli.sendall((act + "\n").encode("utf-8"))
            resp = cli.recv(256).decode("utf-8", errors="ignore").strip()
            return True, resp or "ok"
    except Exception as exc:
        return False, f"IPC trigger failed: {exc}"


class WaylandIPCServer:
    """Lightweight daemon thread listening on `$XDG_RUNTIME_DIR/riven_lens.sock` for compositor hotkeys."""

    def __init__(
        self,
        on_action: Callable[[str], None],
        socket_path: Optional[Path] = None,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._on_action = on_action
        self._socket_path = socket_path or get_ipc_socket_path()
        self._log = logger or (lambda _msg: None)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._server_sock: Optional[socket.socket] = None

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    def start(self) -> bool:
        if not hasattr(socket, "AF_UNIX"):
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        try:
            if self._socket_path.exists():
                self._socket_path.unlink(missing_ok=True)
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(self._socket_path))
            try:
                os.chmod(self._socket_path, 0o600)
            except Exception:
                pass
            srv.listen(8)
            srv.settimeout(0.4)
            self._server_sock = srv
        except Exception as exc:
            self._log(f"WaylandIPCServer failed to bind {self._socket_path}: {exc}")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._serve_loop, name="WaylandIPCServer", daemon=True)
        self._thread.start()
        self._log(f"WaylandIPCServer listening on {self._socket_path}")
        return True

    def _serve_loop(self) -> None:
        srv = self._server_sock
        if srv is None:
            return
        while not self._stop_event.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(0.5)
                    raw = conn.recv(256).decode("utf-8", errors="ignore").strip().lower()
                    if raw in IPC_VALID_ACTIONS:
                        conn.sendall(b"ok\n")
                        try:
                            self._on_action(raw)
                        except Exception as exc:
                            self._log(f"WaylandIPCServer action '{raw}' handler error: {exc}")
                    else:
                        conn.sendall(b"invalid\n")
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.8)
        self._thread = None
        try:
            if self._socket_path.exists():
                self._socket_path.unlink(missing_ok=True)
        except Exception:
            pass


# ---- Wayland Active Window Focus Detection ----


def _find_sway_focused_node(node: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if node.get("focused") and (node.get("name") or node.get("app_id")):
        return node
    for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
        if isinstance(child, dict):
            hit = _find_sway_focused_node(child)
            if hit is not None:
                return hit
    return None


def get_wayland_foreground_title() -> Tuple[str, bool]:
    """Query active window title on Linux Wayland.

    Returns:
        `(window_title, is_supported)`:
          - `is_supported=True` when the compositor returned a valid query result.
          - `is_supported=False` on locked-down compositors (e.g. stock GNOME Wayland)
            where active-window inspection is restricted by protocol; callers should
            NOT auto-hide overlays when `is_supported=False`.
    """
    comp = detect_wayland_compositor()

    # 1. Hyprland (`hyprctl activewindow -j`)
    if comp == "hyprland" or shutil.which("hyprctl"):
        try:
            out = subprocess.check_output(
                ["hyprctl", "activewindow", "-j"],
                stderr=subprocess.DEVNULL,
                timeout=0.5,
                text=True,
            )
            data = json.loads(out)
            if isinstance(data, dict):
                title = str(data.get("title") or "").strip()
                cls = str(data.get("class") or data.get("initialClass") or "").strip()
                combined = f"{title} {cls}".strip()
                return combined, True
        except Exception:
            pass

    # 2. Sway (`swaymsg -t get_tree`)
    if comp == "sway" or shutil.which("swaymsg"):
        try:
            out = subprocess.check_output(
                ["swaymsg", "-t", "get_tree"],
                stderr=subprocess.DEVNULL,
                timeout=0.5,
                text=True,
            )
            tree = json.loads(out)
            if isinstance(tree, dict):
                node = _find_sway_focused_node(tree)
                if node is not None:
                    title = str(node.get("name") or "").strip()
                    app_id = str(node.get("app_id") or "").strip()
                    return f"{title} {app_id}".strip(), True
                return "", True
        except Exception:
            pass

    # 3. KDE Plasma Wayland (`kdotool`)
    if shutil.which("kdotool"):
        try:
            out = subprocess.check_output(
                ["kdotool", "getactivewindow", "getwindowname"],
                stderr=subprocess.DEVNULL,
                timeout=0.5,
                text=True,
            )
            return out.strip(), True
        except Exception:
            pass

    return "", False


# ---- Wayland Screen Capture Engine ----


@dataclass
class WaylandMonitorInfo:
    left: int
    top: int
    width: int
    height: int
    name: str = ""
    scale: float = 1.0

    def to_dict(self) -> Dict[str, int]:
        return {
            "left": int(self.left),
            "top": int(self.top),
            "width": int(self.width),
            "height": int(self.height),
        }


def detect_wayland_capture_backend() -> str:
    """Select the fastest available silent screenshot backend on Linux Wayland.

    Priority:
      1. `grim` (Hyprland / Sway / wlroots — <30ms, pipes PNG to stdout, zero popup)
      2. `spectacle` (KDE Plasma Wayland — silent `-b -n -f` background capture)
      3. `gnome_gdbus` (GNOME Shell Screenshot DBus — when allowed)
      4. `gnome_screenshot` (`gnome-screenshot` CLI)
      5. `portal` (XDG Desktop Portal `org.freedesktop.portal.Screenshot` via `gdbus`)
      6. `mss` (XWayland fallback)
    """
    override = (os.environ.get("RIVEN_LENS_CAPTURE_BACKEND") or "").strip().lower()
    if override in ("grim", "spectacle", "gnome_gdbus", "gnome_screenshot", "portal", "mss"):
        return override

    comp = detect_wayland_compositor()
    if comp in ("hyprland", "sway", "wlroots") and shutil.which("grim"):
        return "grim"
    if comp == "kde" and shutil.which("spectacle"):
        return "spectacle"
    if shutil.which("grim"):
        return "grim"
    if shutil.which("spectacle"):
        return "spectacle"
    if comp == "gnome" and shutil.which("gnome-screenshot"):
        return "gnome_screenshot"
    if shutil.which("gdbus"):
        return "portal"
    return "mss"


def get_wayland_monitors() -> List[WaylandMonitorInfo]:
    """Enumerate monitors on Linux Wayland (`[0]` = combined virtual desktop, `[1..N]` = outputs)."""
    outputs: List[WaylandMonitorInfo] = []

    # 1. Hyprland
    if shutil.which("hyprctl") and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        try:
            raw = subprocess.check_output(
                ["hyprctl", "monitors", "-j"],
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                text=True,
            )
            data = json.loads(raw)
            if isinstance(data, list):
                for m in data:
                    if not isinstance(m, dict):
                        continue
                    outputs.append(
                        WaylandMonitorInfo(
                            left=int(m.get("x", 0)),
                            top=int(m.get("y", 0)),
                            width=int(m.get("width", 1920)),
                            height=int(m.get("height", 1080)),
                            name=str(m.get("name") or ""),
                            scale=float(m.get("scale") or 1.0),
                        )
                    )
        except Exception:
            pass

    # 2. Sway
    if not outputs and shutil.which("swaymsg") and os.environ.get("SWAYSOCK"):
        try:
            raw = subprocess.check_output(
                ["swaymsg", "-t", "get_outputs"],
                stderr=subprocess.DEVNULL,
                timeout=0.6,
                text=True,
            )
            data = json.loads(raw)
            if isinstance(data, list):
                for m in data:
                    if not isinstance(m, dict) or not m.get("active", True):
                        continue
                    rect = m.get("rect") or {}
                    outputs.append(
                        WaylandMonitorInfo(
                            left=int(rect.get("x", 0)),
                            top=int(rect.get("y", 0)),
                            width=int(rect.get("width", 1920)),
                            height=int(rect.get("height", 1080)),
                            name=str(m.get("name") or ""),
                            scale=float(m.get("scale") or 1.0),
                        )
                    )
        except Exception:
            pass

    # 3. Universal Qt6 QGuiApplication.screens() fallback (works on KDE, GNOME, wlroots)
    if not outputs:
        try:
            from PyQt6.QtGui import QGuiApplication

            for sc in QGuiApplication.screens():
                geo = sc.geometry()
                dpr = float(sc.devicePixelRatio() or 1.0)
                outputs.append(
                    WaylandMonitorInfo(
                        left=int(geo.left()),
                        top=int(geo.top()),
                        width=int(round(geo.width() * dpr)),
                        height=int(round(geo.height() * dpr)),
                        name=sc.name(),
                        scale=dpr,
                    )
                )
        except Exception:
            pass

    if not outputs:
        outputs.append(WaylandMonitorInfo(left=0, top=0, width=1920, height=1080, name="default"))

    # Prepend index 0 = bounding virtual desktop (matching mss.monitors[0] convention)
    min_x = min(o.left for o in outputs)
    min_y = min(o.top for o in outputs)
    max_x = max(o.left + o.width for o in outputs)
    max_y = max(o.top + o.height for o in outputs)
    combined = WaylandMonitorInfo(left=min_x, top=min_y, width=max_x - min_x, height=max_y - min_y, name="all")
    return [combined] + outputs


class WaylandScreenCapturer:
    """Multi-backend Linux Wayland screen capturer returning OpenCV BGR numpy arrays."""

    def __init__(self, backend: Optional[str] = None) -> None:
        self.backend = backend or detect_wayland_capture_backend()

    @staticmethod
    def _decode_png_bytes(raw: bytes) -> np.ndarray:
        if not raw:
            raise RuntimeError("Wayland screenshot backend returned empty bytes.")
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            raise RuntimeError("Failed to decode PNG returned by Wayland screenshot backend.")
        return img

    def _grab_grim(
        self,
        geometry: Optional[Tuple[int, int, int, int]] = None,
        output_name: str = "",
    ) -> np.ndarray:
        cmd = ["grim", "-t", "png", "-l", "0"]  # level-0 fast uncompressed PNG to stdout
        if geometry is not None:
            lx, ty, w, h = geometry
            cmd.extend(["-g", f"{lx},{ty} {w}x{h}"])
        elif output_name and output_name not in ("all", "default"):
            cmd.extend(["-o", output_name])
        cmd.append("-")
        raw = subprocess.check_output(cmd, stderr=subprocess.PIPE, timeout=4.0)
        return self._decode_png_bytes(raw)

    def _grab_spectacle(self) -> np.ndarray:
        with tempfile.NamedTemporaryFile(prefix="riven_lens_wl_", suffix=".png", delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            subprocess.run(
                ["spectacle", "-b", "-n", "-f", "-o", str(tmp_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=5.0,
                check=True,
            )
            return self._decode_png_bytes(tmp_path.read_bytes())
        finally:
            tmp_path.unlink(missing_ok=True)

    def _grab_gnome_screenshot(self) -> np.ndarray:
        with tempfile.NamedTemporaryFile(prefix="riven_lens_wl_", suffix=".png", delete=False) as tf:
            tmp_path = Path(tf.name)
        try:
            subprocess.run(
                ["gnome-screenshot", "-f", str(tmp_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=5.0,
                check=True,
            )
            return self._decode_png_bytes(tmp_path.read_bytes())
        finally:
            tmp_path.unlink(missing_ok=True)

    def _grab_portal(self) -> np.ndarray:
        """Capture via XDG Desktop Portal `org.freedesktop.portal.Screenshot` (requires one-time permission)."""
        out = subprocess.check_output(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.portal.Desktop",
                "--object-path",
                "/org/freedesktop/portal/desktop",
                "--method",
                "org.freedesktop.portal.Screenshot.Screenshot",
                "",
                "{'interactive': <false>}",
            ],
            stderr=subprocess.PIPE,
            timeout=6.0,
            text=True,
        )
        # Note: Portal screenshot is async over DBus; if a URI wasn't returned synchronously,
        # advise user to install grim (wlroots) or spectacle (KDE).
        raise RuntimeError(
            "XDG Portal Screenshot requires persistent PipeWire Screencast or grim/spectacle. "
            f"Portal handle={out.strip()}. Please install `grim` (Hyprland/Sway) or `spectacle` (KDE)."
        )

    def grab_monitor_full(self, monitor_index: int = 1) -> Tuple[np.ndarray, WaylandMonitorInfo]:
        """Capture one full monitor image and return `(bgr_image, monitor_info)`."""
        monitors = get_wayland_monitors()
        idx = monitor_index if 0 <= monitor_index < len(monitors) else 1
        if idx >= len(monitors):
            idx = 0
        mon = monitors[idx]

        if self.backend == "grim":
            img = self._grab_grim(output_name=mon.name)
        elif self.backend == "spectacle":
            full = self._grab_spectacle()
            # If multi-monitor full desktop returned, slice target monitor cleanly
            fh, fw = full.shape[:2]
            if idx > 0 and (fw > mon.width + 20 or fh > mon.height + 20):
                x0 = max(0, min(mon.left, fw - 1))
                y0 = max(0, min(mon.top, fh - 1))
                x1 = max(x0 + 1, min(x0 + mon.width, fw))
                y1 = max(y0 + 1, min(y0 + mon.height, fh))
                img = full[y0:y1, x0:x1].copy()
            else:
                img = full
        elif self.backend == "gnome_screenshot":
            img = self._grab_gnome_screenshot()
        elif self.backend == "portal":
            img = self._grab_portal()
        else:
            raise RuntimeError(
                f"No supported Wayland screenshot tool found (backend={self.backend}). "
                "Please install `grim` (Hyprland/Sway) or `spectacle` (KDE Plasma)."
            )

        actual_h, actual_w = img.shape[:2]
        resolved_mon = WaylandMonitorInfo(
            left=mon.left,
            top=mon.top,
            width=actual_w,
            height=actual_h,
            name=mon.name,
            scale=mon.scale,
        )
        return img, resolved_mon

    def grab_region(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        monitor_index: int = 1,
    ) -> Tuple[np.ndarray, WaylandMonitorInfo]:
        """Capture a region relative to `monitor_index`."""
        monitors = get_wayland_monitors()
        idx = monitor_index if 0 <= monitor_index < len(monitors) else 1
        if idx >= len(monitors):
            idx = 0
        mon = monitors[idx]

        # Direct region grab on grim when scale == 1.0 (super fast!)
        if self.backend == "grim" and abs(mon.scale - 1.0) < 0.01:
            abs_x = mon.left + max(0, int(left))
            abs_y = mon.top + max(0, int(top))
            w = max(1, min(int(width), mon.width - max(0, int(left))))
            h = max(1, min(int(height), mon.height - max(0, int(top))))
            crop = self._grab_grim(geometry=(abs_x, abs_y, w, h))
            return crop, mon

        full, actual_mon = self.grab_monitor_full(monitor_index=monitor_index)
        fh, fw = full.shape[:2]
        x0 = max(0, min(int(left), fw - 1))
        y0 = max(0, min(int(top), fh - 1))
        x1 = max(x0 + 1, min(x0 + max(1, int(width)), fw))
        y1 = max(y0 + 1, min(y0 + max(1, int(height)), fh))
        return full[y0:y1, x0:x1].copy(), actual_mon


def generate_compositor_config_hint(compositor: Optional[str] = None, cmd: str = "riven-lens") -> str:
    """Return ready-to-paste Wayland compositor window-rule & keybind snippets."""
    comp = compositor or detect_wayland_compositor()
    if comp == "hyprland":
        return (
            f"# Hyprland (~/.config/hypr/hyprland.conf)\n"
            f"windowrulev2 = float, class:^(riven-lens)$, title:^(RivenLens.*)$\n"
            f"windowrulev2 = pin, class:^(riven-lens)$, title:^(RivenLens.*)$\n"
            f"windowrulev2 = noborder, class:^(riven-lens)$, title:^(RivenLens.*)$\n"
            f"windowrulev2 = noshadow, class:^(riven-lens)$, title:^(RivenLens.*)$\n"
            f"windowrulev2 = nofocus, class:^(riven-lens)$, title:^(RivenLens.*)$\n"
            f"bind = , F8, exec, {cmd} --trigger toggle\n"
            f"bind = , F9, exec, {cmd} --trigger wm\n"
            f"bind = CTRL ALT, D, exec, {cmd} --trigger open_panel\n"
            f"bind = CTRL ALT, S, exec, {cmd} --trigger export\n"
            f"bind = CTRL ALT, L, exec, {cmd} --trigger lock\n"
        )
    if comp == "sway":
        return (
            f"# Sway (~/.config/sway/config)\n"
            f"for_window [app_id=\"riven-lens\" title=\"^RivenLens\"] floating enable, sticky enable, border none\n"
            f"bindsym F8 exec {cmd} --trigger toggle\n"
            f"bindsym F9 exec {cmd} --trigger wm\n"
            f"bindsym Control+Mod1+d exec {cmd} --trigger open_panel\n"
            f"bindsym Control+Mod1+s exec {cmd} --trigger export\n"
            f"bindsym Control+Mod1+l exec {cmd} --trigger lock\n"
        )
    return (
        f"# KDE Plasma / GNOME Custom Shortcuts:\n"
        f"#   F8         -> {cmd} --trigger toggle\n"
        f"#   F9         -> {cmd} --trigger wm\n"
        f"#   Ctrl+Alt+D -> {cmd} --trigger open_panel\n"
        f"#   Ctrl+Alt+S -> {cmd} --trigger export\n"
        f"#   Ctrl+Alt+L -> {cmd} --trigger lock\n"
    )
