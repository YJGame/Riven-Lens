"""Unit tests for Linux Wayland compatibility layer (`src/wayland_support.py`,
`ScreenCapturer` Wayland routing, `DynamicHotkeyManager` IPC/Linux dispatch,
and `SmartFocusWatcher` graceful degradation on locked-down Wayland compositors).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.capture import ScreenCapturer, ScreenRegion
from src.focus_watcher import SmartFocusWatcher
from src.hotkey_manager import DynamicHotkeyManager
from src.roi_manager import MODE_SINGLE_RIVEN, ROIManager
from src.wayland_support import (
    WaylandMonitorInfo,
    WaylandScreenCapturer,
    detect_wayland_capture_backend,
    detect_wayland_compositor,
    generate_compositor_config_hint,
    is_linux_wayland,
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestWaylandSupport(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def test_compositor_and_backend_detection(self) -> None:
        with mock.patch("src.wayland_support.sys.platform", "linux"):
            with mock.patch.dict(
                os.environ,
                {"XDG_SESSION_TYPE": "wayland", "HYPRLAND_INSTANCE_SIGNATURE": "abc123"},
                clear=True,
            ):
                self.assertTrue(is_linux_wayland())
                self.assertEqual(detect_wayland_compositor(), "hyprland")
                with mock.patch("src.wayland_support.shutil.which", side_effect=lambda x: "/usr/bin/grim" if x == "grim" else None):
                    self.assertEqual(detect_wayland_capture_backend(), "grim")

            with mock.patch.dict(
                os.environ,
                {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"},
                clear=True,
            ):
                self.assertEqual(detect_wayland_compositor(), "kde")
                with mock.patch("src.wayland_support.shutil.which", side_effect=lambda x: "/usr/bin/spectacle" if x == "spectacle" else None):
                    self.assertEqual(detect_wayland_capture_backend(), "spectacle")

            with mock.patch.dict(
                os.environ,
                {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "GNOME"},
                clear=True,
            ):
                self.assertEqual(detect_wayland_compositor(), "gnome")

    def test_wayland_capturer_grim_decoding_and_mode_regions(self) -> None:
        # Synthesize a real 1920x1080 PNG in memory and feed through WaylandScreenCapturer(backend="grim")
        fake_frame = np.full((1080, 1920, 3), (40, 20, 80), dtype=np.uint8)
        ok, buf = cv2.imencode(".png", fake_frame)
        self.assertTrue(ok)
        png_bytes = buf.tobytes()

        wl = WaylandScreenCapturer(backend="grim")
        mon_list = [
            WaylandMonitorInfo(0, 0, 1920, 1080, name="all", scale=1.0),
            WaylandMonitorInfo(0, 0, 1920, 1080, name="DP-1", scale=1.0),
        ]
        with mock.patch("src.wayland_support.get_wayland_monitors", return_value=mon_list), mock.patch(
            "src.wayland_support.subprocess.check_output", return_value=png_bytes
        ):
            img, mon = wl.grab_monitor_full(monitor_index=1)
            self.assertEqual(img.shape, (1080, 1920, 3))
            self.assertEqual((mon.width, mon.height), (1920, 1080))

            # Verify ScreenCapturer transparently routes capture_mode_regions & capture_region_box through Wayland
            capturer = ScreenCapturer(monitor_index=1)
            with mock.patch("src.wayland_support.is_linux_wayland", return_value=True), mock.patch.object(
                capturer, "_get_wayland_capturer", return_value=wl
            ):
                with tempfile.TemporaryDirectory() as tmpdir:
                    rm = ROIManager(profiles_path=Path(tmpdir) / "roi.json")
                    crops, meta = capturer.capture_mode_regions(MODE_SINGLE_RIVEN, roi_manager=rm)
                    self.assertIn("single_riven", crops)
                    self.assertGreater(crops["single_riven"].shape[0], 100)
                    self.assertEqual(meta["monitor_width"], 1920)
                    self.assertEqual(meta["monitor_height"], 1080)

                box_img, box_meta = capturer.capture_region_box(ScreenRegion(100, 100, 300, 200))
                self.assertEqual(box_meta["monitor_width"], 1920)
                self.assertGreater(box_img.shape[0], 0)

    def test_focus_watcher_graceful_fallback_when_wayland_compositor_unsupported(self) -> None:
        # On stock GNOME Wayland where active window title cannot be queried, MUST NOT hide overlays!
        watcher = SmartFocusWatcher(enabled=True, support_checker=lambda: False)
        self.assertEqual(watcher.evaluate_focus(), "noop")
        self.assertFalse(watcher.is_hidden_by_focus)

    def test_hotkey_manager_ipc_action_dispatch_and_compositor_hints(self) -> None:
        mgr = DynamicHotkeyManager()
        hits: list[tuple[str, str]] = []
        mgr.toggle_triggered.connect(lambda src: hits.append(("toggle", src)))
        mgr.wm_triggered.connect(lambda src: hits.append(("wm", src)))
        mgr.hide_triggered.connect(lambda src: hits.append(("hide", src)))
        mgr.open_panel_triggered.connect(lambda src: hits.append(("open_panel", src)))
        mgr.export_triggered.connect(lambda src: hits.append(("export", src)))
        mgr.lock_toggled.connect(lambda src: hits.append(("lock", src)))

        self.assertTrue(mgr.dispatch_action_name("toggle", "WaylandIPC"))
        self.assertTrue(mgr.dispatch_action_name("wm", "WaylandIPC"))
        self.assertTrue(mgr.dispatch_action_name("open_panel", "WaylandIPC"))
        self.assertTrue(mgr.dispatch_action_name("export", "WaylandIPC"))
        self.assertFalse(mgr.dispatch_action_name("unknown_action", "WaylandIPC"))

        # Lock shields toggle/wm even over Wayland IPC, while open_panel still works
        mgr.set_hotkeys_locked(True)
        mgr.dispatch_action_name("toggle", "WaylandIPC")
        mgr.dispatch_action_name("open_panel", "WaylandIPC")
        self.assertEqual(
            hits,
            [
                ("toggle", "WaylandIPC"),
                ("wm", "WaylandIPC"),
                ("open_panel", "WaylandIPC"),
                ("export", "WaylandIPC"),
                ("open_panel", "WaylandIPC"),
            ],
        )

        # Verify Hyprland & Sway config hint snippets contain window rules & IPC triggers
        hypr_hint = generate_compositor_config_hint("hyprland", cmd="riven-lens")
        self.assertIn("windowrulev2 = pin", hypr_hint)
        self.assertIn("--trigger toggle", hypr_hint)
        sway_hint = generate_compositor_config_hint("sway", cmd="riven-lens")
        self.assertIn("for_window", sway_hint)
        self.assertIn("--trigger open_panel", sway_hint)


if __name__ == "__main__":
    unittest.main()
