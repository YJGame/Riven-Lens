"""Tests for UserSettings, DynamicHotkeyManager, TrayManager, and RivenControlPanel."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.control_panel import RivenControlPanel, read_local_db_metadata
from src.hotkey_manager import DynamicHotkeyManager, parse_hotkey_string
from src.overlay_ui import RivenOverlayWindow
from src.settings_manager import UserSettings, load_user_settings, save_user_settings
from src.tray_manager import TrayManager, get_assets_dir, load_app_icon
from src.wm_fetcher import WMMarketReport, WMStatFreq


class TestSettingsAndHotkeys(unittest.TestCase):
    def test_settings_roundtrip_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "user_settings.json"
            default_s = load_user_settings(p)
            self.assertEqual(default_s.hotkey_toggle_monitor, "F8")
            self.assertEqual(default_s.hotkey_fetch_wm, "F9")
            self.assertEqual(default_s.hotkey_hide_overlay, "ESC")
            self.assertEqual(default_s.language_mode, "auto")
            self.assertFalse(default_s.start_monitoring_on_launch)
            self.assertIsNone(default_s.overlay_pos_x_ratio)
            self.assertIsNone(default_s.overlay_pos_y_ratio)
            self.assertEqual(default_s.overlay_font_size, 13)

            custom = UserSettings(
                hotkey_toggle_monitor="Alt+R",
                hotkey_fetch_wm="Ctrl+Shift+M",
                hotkey_hide_overlay="F7",
                language_mode="en_US",
                start_monitoring_on_launch=True,
                overlay_pos_x_ratio=0.425,
                overlay_pos_y_ratio=0.185,
                overlay_font_size=19,
            )
            save_user_settings(custom, p)
            loaded = load_user_settings(p)
            self.assertEqual(loaded, custom)

            # Out-of-bounds font size and ratios are safely clamped
            clamped = UserSettings.from_dict(
                {"overlay_font_size": 99, "overlay_pos_x_ratio": 1.5, "overlay_pos_y_ratio": -0.2}
            )
            self.assertEqual(clamped.overlay_font_size, 24)
            self.assertEqual(clamped.overlay_pos_x_ratio, 1.0)
            self.assertEqual(clamped.overlay_pos_y_ratio, 0.0)

    def test_parse_hotkey_string_variants(self) -> None:
        p1 = parse_hotkey_string("f8")
        self.assertEqual(p1.display, "F8")
        self.assertEqual(p1.win_mods, 0)
        self.assertFalse(p1.is_plain_esc)

        p2 = parse_hotkey_string("ctrl+shift+r")
        self.assertEqual(p2.display, "Ctrl+Shift+R")
        self.assertEqual(p2.kb_spec, "ctrl+shift+r")

        p3 = parse_hotkey_string("Alt + M")
        self.assertEqual(p3.display, "Alt+M")

        p4 = parse_hotkey_string("esc")
        self.assertEqual(p4.display, "ESC")
        self.assertTrue(p4.is_plain_esc)

        with self.assertRaises(ValueError):
            parse_hotkey_string("")
        with self.assertRaises(ValueError):
            parse_hotkey_string("Ctrl+Shift")
        with self.assertRaises(ValueError):
            parse_hotkey_string("NotAKey")

    def test_dynamic_hotkey_duplicate_rejected_and_hot_reload(self) -> None:
        _app = QApplication.instance() or QApplication([])
        mgr = DynamicHotkeyManager()
        try:
            bad = mgr.apply_hotkeys("F8", "F8", "ESC")
            self.assertFalse(bad.ok)
            self.assertTrue(any("冲突" in e for e in bad.errors))

            # Hot-reload custom hotkeys cleanly
            ok1 = mgr.apply_hotkeys("Ctrl+Alt+F7", "Ctrl+Alt+F6", "ESC")
            self.assertTrue(ok1.ok)
            self.assertEqual(ok1.toggle_hotkey, "Ctrl+Alt+F7")
            self.assertEqual(ok1.wm_hotkey, "Ctrl+Alt+F6")
            self.assertEqual(ok1.hide_hotkey, "ESC")
            self.assertIsNotNone(mgr.active_hide)
            self.assertTrue(mgr.active_hide.is_plain_esc)

            # Rebind again without restart
            ok2 = mgr.apply_hotkeys("Ctrl+Alt+F4", "Ctrl+Alt+F3", "Ctrl+Alt+F2")
            self.assertTrue(ok2.ok)
            self.assertFalse(mgr.active_hide.is_plain_esc)
        finally:
            mgr.unregister_all()


class TestTrayAndControlPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_app_icon_asset_exists_and_loads(self) -> None:
        assets = get_assets_dir()
        self.assertTrue((assets / "icon.png").is_file())
        self.assertTrue((assets / "icon.ico").is_file())
        icon = load_app_icon()
        self.assertFalse(icon.isNull())

    def test_tray_menu_items_and_no_f10(self) -> None:
        called = []
        tray = TrayManager(
            on_toggle_monitor=lambda: called.append("toggle"),
            on_open_panel=lambda: called.append("panel"),
            on_fetch_wm=lambda: called.append("wm"),
            on_update_db=lambda: called.append("db"),
            on_quit=lambda: called.append("quit"),
            initial_ui_lang="zh_CN",
            toggle_hotkey="Alt+R",
            wm_hotkey="Ctrl+Shift+M",
        )
        try:
            actions = tray.menu.actions()
            # 1 next-mode + 1 mode-submenu + 4 actions (panel, lock, wm, db) + 1 separator + 1 exit = 8 actions
            self.assertEqual(len(actions), 8)
            self.assertTrue(actions[6].isSeparator())

            texts = [a.text() for a in actions if not a.isSeparator()]
            self.assertIn("Alt+R", texts[0])
            self.assertIn("工作模式", texts[1])
            self.assertIn("控制面板", texts[2])
            self.assertIn("Ctrl+Alt+L", texts[3])
            self.assertIn("Ctrl+Shift+M", texts[4])
            self.assertIn("数据库", texts[5])
            self.assertIn("退出软件", texts[6])
            # Strictly verify F10 is completely absent
            for txt in texts:
                self.assertNotIn("F10", txt)

            # Refresh to running & English
            tray.refresh_state(ui_lang="en_US", monitoring=True, toggle_hotkey="F6", wm_hotkey="F7")
            en_texts = [a.text() for a in actions if not a.isSeparator()]
            self.assertIn("F6", en_texts[0])
            self.assertIn("Work Mode", en_texts[1])
            self.assertIn("Control Panel", en_texts[2])
            self.assertIn("Ctrl+Alt+L", en_texts[3])
            self.assertIn("Exit Software", en_texts[6])
            for txt in en_texts:
                self.assertNotIn("F10", txt)
        finally:
            tray.hide()

    def test_control_panel_signals_retranslate_and_status(self) -> None:
        settings = UserSettings(
            hotkey_toggle_monitor="F8",
            hotkey_fetch_wm="F9",
            hotkey_hide_overlay="ESC",
            language_mode="auto",
            start_monitoring_on_launch=False,
        )
        panel = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN")

        hk_events = []
        wm_events = []
        db_events = []
        lang_events = []
        start_events = []

        panel.hotkeys_apply_requested.connect(lambda a, b, c: hk_events.append((a, b, c)))
        panel.wm_fetch_requested.connect(lambda w: wm_events.append(w))
        panel.db_update_requested.connect(lambda: db_events.append(True))
        panel.language_mode_changed.connect(lambda m: lang_events.append(m))
        panel.start_monitoring_changed.connect(lambda v: start_events.append(v))

        # Trigger hotkey apply
        panel._inp_hk_toggle.setText("Alt+R")
        panel._btn_apply_hk.click()
        self.assertEqual(hk_events, [("Alt+R", "F9", "ESC")])

        # Trigger manual WM fetch
        panel._inp_weapon.setText("托里德")
        panel._btn_fetch_wm.click()
        self.assertEqual(wm_events, ["托里德"])

        # Display WM report
        rep = WMMarketReport(
            weapon_name="托里德",
            weapon_slug="torid",
            weapon_class="rifle",
            sample_count=40,
            online_count=22,
            top_positives=[WMStatFreq("暴击几率", 28, 70.0)],
            top_negatives=[WMStatFreq("变焦", 12, 30.0)],
            min_price=400,
            median_price=650,
            high_tier_median=1200,
            saved_to_local=True,
        )
        panel.set_wm_report(rep)
        self.assertIn("托里德", panel._lbl_wm_status.text())
        self.assertIn("650p", panel._lbl_wm_status.text())

        # Trigger DB update & check progress state
        panel._btn_update_db.click()
        self.assertEqual(len(db_events), 1)
        panel.set_db_updating(True)
        self.assertFalse(panel._btn_update_db.isEnabled())
        panel.set_db_updating(False, "✓ Done")
        self.assertTrue(panel._btn_update_db.isEnabled())

        # Language switch & startup preference
        en_idx = panel._cmb_lang.findData("en_US")
        self.assertGreaterEqual(en_idx, 0)
        panel._cmb_lang.setCurrentIndex(en_idx)  # en_US
        self.assertEqual(lang_events, ["en_US"])
        panel.retranslate_ui("en_US")
        self.assertEqual(panel.windowTitle(), "Riven Lens")

        panel._chk_start_monitor.setChecked(True)
        self.assertEqual(start_events, [True])

        # Verify local DB metadata reads cleanly
        ts, cnt = read_local_db_metadata()
        self.assertIsNotNone(ts)
        self.assertGreater(cnt, 100)

    def test_overlay_footer_uses_custom_hotkeys_and_no_f10(self) -> None:
        win = RivenOverlayWindow(
            click_through=True,
            lang="zh_CN",
            toggle_hotkey="Alt+R",
            wm_hotkey="Ctrl+M",
            hide_hotkey="F7",
        )
        win.show_monitoring_waiting()
        footer = win._footer.text()
        self.assertIn("Alt+R", footer)
        self.assertIn("Ctrl+M", footer)
        self.assertIn("F7", footer)
        self.assertNotIn("F10", footer)
        win.hide()

    def test_overlay_drag_unlock_position_memory_and_font_scaling(self) -> None:
        win = RivenOverlayWindow(
            click_through=True,
            lang="zh_CN",
            font_size_px=13,
            pos_x_ratio=0.30,
            pos_y_ratio=0.20,
        )
        # Starts locked with WindowTransparentForInput
        self.assertFalse(win.is_drag_unlocked)
        self.assertTrue(bool(win.windowFlags() & Qt.WindowType.WindowTransparentForInput))

        # Unlock drag -> removes click-through and shows preview card + drag banner
        win.set_drag_unlocked(True, show_preview_if_empty=True)
        self.assertTrue(win.is_drag_unlocked)
        self.assertFalse(bool(win.windowFlags() & Qt.WindowType.WindowTransparentForInput))
        self.assertFalse(win._drag_banner.isHidden())
        self.assertEqual(win.display_mode, "preview")

        # Verify dynamic font scaling adjusts overlay dimensions cleanly (10px -> 22px)
        win.set_font_size(10)
        h_small = win.height()
        w_small = win.width()
        win.set_font_size(22)
        h_large = win.height()
        w_large = win.width()
        self.assertGreater(h_large, h_small)
        self.assertGreaterEqual(w_large, w_small)
        self.assertEqual(win.font_size_px, 22)

        # Verify saved ratio positioning and reset_position
        rx, ry = win._compute_current_ratios()
        self.assertGreaterEqual(rx, 0.0)
        self.assertLessEqual(rx, 1.0)
        self.assertGreaterEqual(ry, 0.0)
        self.assertLessEqual(ry, 1.0)

        win.reset_position()
        self.assertIsNone(win._pos_x_ratio)
        self.assertIsNone(win._pos_y_ratio)

        # Re-lock restores click-through and hides preview card
        win.set_drag_unlocked(False)
        self.assertFalse(win.is_drag_unlocked)
        self.assertTrue(bool(win.windowFlags() & Qt.WindowType.WindowTransparentForInput))
        self.assertTrue(win._drag_banner.isHidden())
        self.assertEqual(win.display_mode, "none")

    def test_control_panel_appearance_and_close_auto_relocks(self) -> None:
        settings = UserSettings(overlay_font_size=15, overlay_pos_x_ratio=0.5, overlay_pos_y_ratio=0.25)
        panel = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN")

        drag_events = []
        reset_events = []
        font_events = []
        closed_events = []

        panel.drag_unlock_changed.connect(lambda v: drag_events.append(v))
        panel.reset_position_requested.connect(lambda: reset_events.append(True))
        panel.font_size_changed.connect(lambda px: font_events.append(px))
        panel.panel_closed.connect(lambda: closed_events.append(True))

        # Initial font slider & spinbox match saved settings; verbose pos hint stays hidden
        self.assertEqual(panel._slider_font.value(), 15)
        self.assertEqual(panel._spin_font.value(), 15)
        self.assertEqual(panel._settings.overlay_pos_x_ratio, 0.5)
        self.assertTrue(panel._lbl_pos_info.isHidden())

        # Adjusting slider syncs spinbox and emits font_size_changed
        panel._slider_font.setValue(20)
        self.assertEqual(panel._spin_font.value(), 20)
        self.assertEqual(font_events[-1], 20)

        # Clicking reset position emits signal
        panel._btn_reset_pos.click()
        self.assertEqual(len(reset_events), 1)

        # Unlocking drag then closing control panel auto-relocks drag!
        panel._btn_drag_unlock.setChecked(True)
        self.assertEqual(drag_events, [True])
        panel.close()
        self.assertFalse(panel._btn_drag_unlock.isChecked())
        self.assertEqual(drag_events, [True, False])
        self.assertEqual(len(closed_events), 1)


if __name__ == "__main__":
    unittest.main()
