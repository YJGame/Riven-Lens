"""Tests for 5-state F8 mode machine, differentiated F9 triggers, ROIManager, and single-mode RegionCalibratorOverlay."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.capture import ScreenCapturer, ScreenRegion
from src.control_panel import RivenControlPanel
from src.overlay_ui import RivenOverlayWindow
from src.region_calibrator import RegionCalibratorOverlay
from src.roi_manager import (
    CALIBRATION_MODES,
    MODE_CHAT_RIVEN,
    MODE_PAUSED,
    MODE_RELIC_REWARDS,
    MODE_REROLL,
    MODE_SINGLE_RIVEN,
    ModeStateMachine,
    ROIManager,
    format_mode_badge,
)
from src.settings_manager import UserSettings


class TestModeStateMachineAndROIManager(unittest.TestCase):
    def test_f8_five_state_rotation_and_differentiated_triggers(self) -> None:
        sm = ModeStateMachine(initial_mode=MODE_PAUSED)
        self.assertTrue(sm.is_paused())
        self.assertFalse(sm.is_auto_ocr())
        self.assertFalse(sm.is_manual_f9_capture())

        # 1st F8 -> single_riven (auto OCR; F9 = WM price)
        self.assertEqual(sm.cycle_next(), MODE_SINGLE_RIVEN)
        self.assertTrue(sm.is_auto_ocr())
        self.assertFalse(sm.is_manual_f9_capture())
        self.assertEqual(sm.f9_action_type(), "wm_price")

        # 2nd F8 -> reroll (auto OCR; F9 = WM price)
        self.assertEqual(sm.cycle_next(), MODE_REROLL)
        self.assertTrue(sm.is_auto_ocr())
        self.assertFalse(sm.is_manual_f9_capture())
        self.assertEqual(sm.f9_action_type(), "wm_price")

        # 3rd F8 -> chat_riven (STRICTLY NO auto OCR; F9 = capture_chat_riven)
        self.assertEqual(sm.cycle_next(), MODE_CHAT_RIVEN)
        self.assertFalse(sm.is_auto_ocr())
        self.assertTrue(sm.is_manual_f9_capture())
        self.assertEqual(sm.f9_action_type(), "capture_chat_riven")

        # 4th F8 -> relic_rewards (STRICTLY NO auto OCR; F9 = capture_relic_rewards)
        self.assertEqual(sm.cycle_next(), MODE_RELIC_REWARDS)
        self.assertFalse(sm.is_auto_ocr())
        self.assertTrue(sm.is_manual_f9_capture())
        self.assertEqual(sm.f9_action_type(), "capture_relic_rewards")

        # 5th F8 -> paused (standby)
        self.assertEqual(sm.cycle_next(), MODE_PAUSED)
        self.assertTrue(sm.is_paused())
        self.assertFalse(sm.is_auto_ocr())

        # 6th F8 wraps back to single_riven
        self.assertEqual(sm.cycle_next(), MODE_SINGLE_RIVEN)

    def test_roi_manager_per_resolution_and_cross_res_ratio_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roi_profiles.json"
            mgr = ROIManager(profiles_path=p)

            # Factory defaults work on 1080p, 2K, and 3440x1440 ultrawide
            for w, h in ((1920, 1080), (2560, 1440), (3440, 1440)):
                for mode in CALIBRATION_MODES:
                    boxes = mgr.get_mode_regions(mode, w, h)
                    expected_count = 2 if mode == MODE_REROLL else 1
                    self.assertEqual(len(boxes), expected_count)
                    for reg in boxes.values():
                        self.assertGreaterEqual(reg.left, 0)
                        self.assertGreaterEqual(reg.top, 0)
                        self.assertLessEqual(reg.left + reg.width, w)
                        self.assertLessEqual(reg.top + reg.height, h)

            # Save custom box for 1920x1080 and reload from disk
            custom = ScreenRegion(left=600, top=220, width=480, height=620)
            mgr.set_region("chat_riven", custom, 1920, 1080)
            mgr.save()

            mgr2 = ROIManager(profiles_path=p)
            loaded_1080 = mgr2.get_region("chat_riven", 1920, 1080)
            self.assertEqual(loaded_1080, custom)

            # On an uncalibrated resolution (2560x1440), scales cleanly from saved relative ratios
            loaded_2k = mgr2.get_region("chat_riven", 2560, 1440)
            self.assertAlmostEqual(loaded_2k.left / 2560.0, 600 / 1920.0, places=2)
            self.assertAlmostEqual(loaded_2k.width / 2560.0, 480 / 1920.0, places=2)

            # Reset single mode restores factory default
            mgr2.reset_mode(MODE_CHAT_RIVEN, 1920, 1080)
            restored = mgr2.get_region("chat_riven", 1920, 1080)
            self.assertEqual(restored, mgr2.get_default_region("chat_riven", 1920, 1080))


class TestRegionCalibratorAndModeUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_calibrator_shows_only_one_mode_at_a_time_and_saves_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roi_profiles.json"
            mgr = ROIManager(profiles_path=p)
            cal = RegionCalibratorOverlay(
                roi_manager=mgr,
                capturer=ScreenCapturer(),
                initial_mode=MODE_SINGLE_RIVEN,
                lang="zh_CN",
            )

            # Mode 1: single_riven -> strictly 1 visible box
            self.assertEqual(cal.current_mode, MODE_SINGLE_RIVEN)
            self.assertEqual(cal.get_visible_box_keys(), ["single_riven"])

            # Modify single_riven box
            edited_single = ScreenRegion(left=700, top=320, width=400, height=500)
            cal.set_box_physical("single_riven", edited_single)

            # Cycle to Mode 2: reroll -> strictly 2 visible boxes (left + right)
            cal.cycle_next_mode()
            self.assertEqual(cal.current_mode, MODE_REROLL)
            self.assertEqual(cal.get_visible_box_keys(), ["reroll_left", "reroll_right"])

            # Cycle to Mode 3: chat_riven -> strictly 1 visible box
            cal.cycle_next_mode()
            self.assertEqual(cal.current_mode, MODE_CHAT_RIVEN)
            self.assertEqual(cal.get_visible_box_keys(), ["chat_riven"])

            # Cycle to Mode 4: relic_rewards -> strictly 1 visible box
            cal.cycle_next_mode()
            self.assertEqual(cal.current_mode, MODE_RELIC_REWARDS)
            self.assertEqual(cal.get_visible_box_keys(), ["relic_rewards"])

            # Save & verify persisted to roi_profiles.json
            saved_flag = []
            cal.calibration_saved.connect(lambda: saved_flag.append(True))
            cal.save_and_close()
            self.assertEqual(saved_flag, [True])

            mgr_reloaded = ROIManager(profiles_path=p)
            got = mgr_reloaded.get_region("single_riven", cal._phys_w, cal._phys_h)
            self.assertEqual(got, edited_single.clamp(cal._phys_w, cal._phys_h))

    def test_overlay_and_control_panel_mode_badges_and_relic_view(self) -> None:
        win = RivenOverlayWindow(click_through=True, lang="zh_CN")
        win.show_manual_mode_standby(MODE_CHAT_RIVEN)
        self.assertEqual(win.display_mode, "manual_standby")
        self.assertIn("模式 3/4", win._mode_badge.text())

        win.show_relic_rewards(["托里德 Prime 蓝图", "Forma 蓝图", "伯斯顿 Prime 枪管"], elapsed_ms=95.0)
        self.assertEqual(win.display_mode, "relic")
        self.assertIn("模式 4/4", win._mode_badge.text())
        win.hide()

        panel = RivenControlPanel(settings=UserSettings(), initial_ui_lang="zh_CN")
        mode_events = []
        cal_events = []
        panel.mode_selected.connect(lambda m: mode_events.append(m))
        panel.calibrate_regions_requested.connect(lambda: cal_events.append(True))

        idx_chat = panel._cmb_mode.findData(MODE_CHAT_RIVEN)
        self.assertGreaterEqual(idx_chat, 0)
        panel._cmb_mode.setCurrentIndex(idx_chat)
        self.assertEqual(mode_events, [MODE_CHAT_RIVEN])

        panel._btn_calibrate_roi.click()
        self.assertEqual(cal_events, [True])
        self.assertIn("模式 1/4", format_mode_badge(MODE_SINGLE_RIVEN, "zh_CN"))


if __name__ == "__main__":
    unittest.main()
