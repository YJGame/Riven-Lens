"""Unit tests for macOS (Apple Silicon & Intel) compatibility layer."""

from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.audio_cue import AudioCuePlayer
from src.capture import ScreenCapturer, ScreenRegion
from src.card_exporter import resolve_desktop_dir
from src.data_updater import get_data_dir
from src.focus_watcher import (
    SmartFocusWatcher,
    get_foreground_window_title,
    is_overlay_allowed_for_title,
)
from src.hotkey_manager import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_WIN,
    _pynput_key_to_mod_bit,
    _pynput_key_to_win_vk,
    parse_hotkey_string,
)
from src.macos_support import (
    MacOSMonitorInfo,
    MacOSPermissionReport,
    MacOSScreenCapturer,
    detect_macos_capture_backend,
    get_macos_foreground_app_and_title,
)
from src.ocr_worker import get_ocr_models_dir
from src.roi_manager import MODE_SINGLE_RIVEN, ROIManager


class TestMacOSSupport(unittest.TestCase):
    def test_permission_report_and_backend_detection(self) -> None:
        rep_missing = MacOSPermissionReport(
            screen_recording=False,
            accessibility=False,
            macos_summary="macOS 15.2 (arm64)",
        )
        self.assertFalse(rep_missing.all_granted)
        self.assertEqual(len(rep_missing.missing_items_zh()), 2)

        rep_ok = MacOSPermissionReport(
            screen_recording=True,
            accessibility=True,
            macos_summary="macOS 15.2 (arm64)",
        )
        self.assertTrue(rep_ok.all_granted)
        self.assertEqual(rep_ok.missing_items_zh(), [])

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(detect_macos_capture_backend(), "mss")
        with mock.patch.dict(os.environ, {"RIVEN_MAC_CAPTURE": "screencapture"}, clear=True):
            self.assertEqual(detect_macos_capture_backend(), "screencapture")

    def test_macos_hotkey_aliases_and_quartz_vk_mapping(self) -> None:
        p_opt = parse_hotkey_string("Option+R")
        self.assertEqual(p_opt.display, "Alt+R")
        self.assertEqual(p_opt.win_mods, MOD_ALT)

        p_cmd = parse_hotkey_string("Cmd+Shift+D")
        self.assertEqual(p_cmd.display, "Cmd+Shift+D")
        self.assertTrue(bool(p_cmd.win_mods & MOD_WIN))

        # On macOS, Option+D produces composed char '∂', but physical Quartz vk=0x02 MUST map to 'D' (0x44)
        fake_key_d = types.SimpleNamespace(vk=0x02, char="∂")
        self.assertEqual(_pynput_key_to_win_vk(fake_key_d), ord("D"))

        fake_key_f8 = types.SimpleNamespace(name="f8")
        self.assertEqual(_pynput_key_to_win_vk(fake_key_f8), 0x77)

        fake_key_esc = types.SimpleNamespace(vk=0x35)
        self.assertEqual(_pynput_key_to_win_vk(fake_key_esc), 0x1B)

        self.assertEqual(_pynput_key_to_mod_bit(types.SimpleNamespace(name="ctrl_l")), MOD_CONTROL)
        self.assertEqual(_pynput_key_to_mod_bit(types.SimpleNamespace(name="alt_r")), MOD_ALT)
        self.assertEqual(_pynput_key_to_mod_bit(types.SimpleNamespace(name="cmd")), MOD_WIN)

    def test_macos_focus_detection_and_warframe_hosts(self) -> None:
        with mock.patch("src.macos_support.sys.platform", "darwin"), mock.patch(
            "src.focus_watcher.sys.platform", "darwin"
        ):
            self.assertTrue(is_overlay_allowed_for_title("CrossOver - Warframe"))
            self.assertTrue(is_overlay_allowed_for_title("Whisky"))
            self.assertTrue(is_overlay_allowed_for_title("GeForce NOW"))
            self.assertTrue(is_overlay_allowed_for_title("Parallels Desktop"))
            self.assertFalse(is_overlay_allowed_for_title("Safari"))
            self.assertFalse(is_overlay_allowed_for_title("Slack"))

            with mock.patch(
                "src.macos_support.subprocess.check_output",
                return_value="CrossOver - Warframe\n",
            ):
                title, supported = get_macos_foreground_app_and_title()
                self.assertTrue(supported)
                self.assertEqual(title, "CrossOver - Warframe")
                self.assertEqual(get_foreground_window_title(), "CrossOver - Warframe")

            # Graceful degradation: if osascript is blocked, SmartFocusWatcher MUST NOT hide overlays
            watcher = SmartFocusWatcher(enabled=True, support_checker=lambda: False)
            self.assertEqual(watcher.evaluate_focus(), "noop")
            self.assertFalse(watcher.is_hidden_by_focus)

    def test_macos_screencapture_fallback_and_retina_cropping(self) -> None:
        fake_retina = np.full((1800, 2880, 3), (35, 18, 70), dtype=np.uint8)
        ok, buf = cv2.imencode(".png", fake_retina)
        self.assertTrue(ok)
        png_bytes = buf.tobytes()

        mac_cap = MacOSScreenCapturer()
        mon = MacOSMonitorInfo(left=0, top=0, width=2880, height=1800, display_index=1)
        capturer = ScreenCapturer(monitor_index=1)

        with mock.patch("src.macos_support.sys.platform", "darwin"), mock.patch.object(
            capturer, "_use_macos_screencapture_directly", return_value=True
        ), mock.patch.object(
            capturer, "_get_macos_fallback_capturer", return_value=mac_cap
        ), mock.patch.object(
            mac_cap, "grab_monitor_full", return_value=(mac_cap._decode_png_bytes(png_bytes), mon)
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                rm = ROIManager(profiles_path=Path(tmpdir) / "roi.json")
                crops, meta = capturer.capture_mode_regions(MODE_SINGLE_RIVEN, roi_manager=rm)
                self.assertIn("single_riven", crops)
                self.assertEqual(meta["monitor_width"], 2880)
                self.assertEqual(meta["monitor_height"], 1800)
                self.assertGreater(crops["single_riven"].shape[0], 150)

            box_img, box_meta = capturer.capture_region_box(ScreenRegion(200, 150, 400, 300))
            self.assertEqual(box_img.shape[:2], (300, 400))
            self.assertEqual(box_meta["monitor_width"], 2880)

    def test_macos_audio_and_writable_paths(self) -> None:
        player = AudioCuePlayer(enabled=True)
        with mock.patch("src.audio_cue.sys.platform", "darwin"), mock.patch(
            "src.audio_cue.shutil.which", return_value="/usr/bin/afplay"
        ), mock.patch("src.audio_cue.subprocess.run") as mock_run:
            player._play_sync("normal", player.normal_wav_bytes)
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd[0], "/usr/bin/afplay")
            self.assertTrue(cmd[1].endswith(".wav"))

        with tempfile.TemporaryDirectory() as fake_home:
            home_p = Path(fake_home)
            with mock.patch("src.ocr_worker.sys.platform", "darwin"), mock.patch(
                "src.ocr_worker.Path.home", return_value=home_p
            ):
                m_dir = get_ocr_models_dir()
                self.assertIn("Application Support", str(m_dir))
                self.assertTrue(str(m_dir).endswith("ocr_models"))

            with mock.patch("src.card_exporter.sys.platform", "darwin"), mock.patch(
                "src.card_exporter.Path.home", return_value=home_p
            ):
                self.assertEqual(resolve_desktop_dir(), home_p / "Desktop")

            with mock.patch("src.data_updater.sys.platform", "darwin"), mock.patch(
                "src.data_updater.Path.home", return_value=home_p
            ), mock.patch.object(
                __import__("sys"), "frozen", True, create=True
            ):
                d_dir = get_data_dir()
                self.assertEqual(
                    d_dir,
                    home_p / "Library" / "Application Support" / "WarframeRivenOverlay" / "data",
                )
                self.assertTrue(d_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
