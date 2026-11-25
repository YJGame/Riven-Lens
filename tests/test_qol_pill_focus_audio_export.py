"""Unit tests for QoL upgrades:
  1. 5-second auto-collapse Compact Pill mode + timer reset on new data.
  2. Cross-platform SmartFocusWatcher (auto-hide when foreground window is not Warframe).
  3. Zero-dependency synthesized WAV AudioCuePlayer & Ctrl+Alt+L hotkey lock shielding.
  4. Off-screen QPainter Riven showcase PNG exporter (valid dimensions, saved file, no monitor capture).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from src.audio_cue import AudioCuePlayer
from src.card_exporter import export_riven_showcase
from src.control_panel import RivenControlPanel
from src.evaluator import RivenEvaluator
from src.focus_watcher import SmartFocusWatcher, is_overlay_allowed_for_title
from src.hotkey_manager import (
    HOTKEY_ID_LOCK,
    HOTKEY_ID_TOGGLE,
    HOTKEY_ID_WM,
    DynamicHotkeyManager,
)
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
from src.riven_parser import RivenParser
from src.roi_manager import MODE_SINGLE_RIVEN
from src.settings_manager import UserSettings, load_user_settings, save_user_settings
from src.wm_client import WMMarketReport


def _pump_qt(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class TestQoLUpgrades(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def _make_card(self, lines: list[str], label: str = "当前紫卡") -> EvaluatedRivenCard:
        r = self.parser.parse(lines)
        ev = self.evaluator.evaluate(r)
        return EvaluatedRivenCard(label=label, riven=r, evaluation=ev, avg_confidence=0.98)

    def test_auto_collapse_pill_timer_and_reset_on_new_card(self) -> None:
        """Overlay expands on new card, resets countdown on newer card, and collapses to compact pill."""
        win = RivenOverlayWindow(click_through=True, auto_collapse_seconds=5)
        try:
            win.set_work_mode(MODE_SINGLE_RIVEN, pending_debounce=False)
            c1 = self._make_card([
                "托里德 Crita-acrican",
                "+198.0% 暴击几率",
                "+158.0% 暴击伤害",
                "+118.0% 多重射击",
                "-54.0% 变焦",
                "段位 16 O 12",
            ])
            c2 = self._make_card([
                "伯斯顿 Visi-satiata",
                "+185.0% 伤害",
                "+112.0% 多重射击",
                "-50.0% 冲击伤害",
                "段位 14 O 5",
            ])

            win.update_cards([c1], elapsed_ms=80.0, layout_label="单卡识别")
            self.assertFalse(win.is_compact_pill)
            self.assertAlmostEqual(win.windowOpacity(), 0.92, places=2)

            # Use fast timer override to test timer reset when a new card arrives mid-countdown
            win.arm_auto_collapse_timer(delay_ms_override=120)
            _pump_qt(60)
            self.assertFalse(win.is_compact_pill)

            # New card arrives -> resets countdown & stays expanded
            win.update_cards([c2], elapsed_ms=75.0, layout_label="单卡识别")
            win.arm_auto_collapse_timer(delay_ms_override=120)
            _pump_qt(60)
            self.assertFalse(win.is_compact_pill)

            # Let countdown expire -> collapses into semi-transparent compact pill & hides sub-popups
            _pump_qt(110)
            self.assertTrue(win.is_compact_pill)
            self.assertAlmostEqual(win.windowOpacity(), 0.65, places=2)
            self.assertFalse(win.wm_window.isVisible())
            self.assertFalse(win.synergy_window.isVisible())
            self.assertFalse(win.relic_window.isVisible())

            # Arriving new card expands back immediately
            win.update_cards([c1], elapsed_ms=70.0, layout_label="单卡识别")
            self.assertFalse(win.is_compact_pill)
            self.assertAlmostEqual(win.windowOpacity(), 0.92, places=2)

            # Setting auto_collapse_seconds = 0 ("永不折叠") stops collapse timer
            win.set_auto_collapse_seconds(0)
            self.assertEqual(win.auto_collapse_seconds, 0)
            self.assertFalse(win._collapse_timer.isActive())
        finally:
            win.close()

    def test_smart_focus_watcher_hides_on_non_warframe_and_restores(self) -> None:
        """Foreground focus detection hides overlays on Chrome/WeChat and restores on Warframe."""
        self.assertTrue(is_overlay_allowed_for_title("Warframe"))
        self.assertTrue(is_overlay_allowed_for_title("星际战甲"))
        self.assertTrue(is_overlay_allowed_for_title("裂罅透镜 - 设置与控制面板"))
        self.assertTrue(is_overlay_allowed_for_title("Riven Lens - Settings & Control Panel"))
        self.assertFalse(is_overlay_allowed_for_title("Google Chrome"))
        self.assertFalse(is_overlay_allowed_for_title("微信"))

        current_title = {"val": "Warframe"}
        watcher = SmartFocusWatcher(enabled=True, title_provider=lambda: current_title["val"])
        win = RivenOverlayWindow(click_through=True, auto_collapse_seconds=0)
        try:
            win.show_monitoring_waiting()
            self.assertTrue(win.isVisible())

            # Staying in Warframe -> noop
            self.assertEqual(watcher.evaluate_focus(drag_unlocked=False), "noop")

            # Alt-tab to Chrome -> hide
            current_title["val"] = "Google Chrome - Warframe Market"
            # Note: "Warframe" in browser tab title is allowed; test unrelated window "Discord"
            current_title["val"] = "Discord"
            self.assertEqual(watcher.evaluate_focus(drag_unlocked=False), "hide")
            win.suspend_for_focus_loss()
            self.assertFalse(win.isVisible())

            # Return to Warframe -> restore
            current_title["val"] = "Warframe"
            self.assertEqual(watcher.evaluate_focus(drag_unlocked=False), "restore")
            win.restore_from_focus_loss()
            self.assertTrue(win.isVisible())

            # While drag_unlocked=True, never hides even on Discord
            current_title["val"] = "Discord"
            self.assertEqual(watcher.evaluate_focus(drag_unlocked=True), "noop")
        finally:
            win.close()

    def test_audio_cue_wav_synthesis_and_hotkey_lock_shielding(self) -> None:
        """AudioCuePlayer synthesizes valid WAV PCM bytes; Ctrl+Alt+L shields F8/F9."""
        player = AudioCuePlayer(enabled=True)
        self.assertTrue(player.normal_wav_bytes.startswith(b"RIFF"))
        self.assertIn(b"WAVE", player.normal_wav_bytes[:16])
        self.assertTrue(player.god_roll_wav_bytes.startswith(b"RIFF"))
        self.assertGreater(len(player.god_roll_wav_bytes), len(player.normal_wav_bytes))
        self.assertTrue(player.play_normal_cue())
        player.set_enabled(False)
        self.assertFalse(player.play_normal_cue())

        hm = DynamicHotkeyManager()
        fired: list[str] = []
        hm.toggle_triggered.connect(lambda _s: fired.append("F8"))
        hm.wm_triggered.connect(lambda _s: fired.append("F9"))
        hm.lock_toggled.connect(lambda _s: fired.append("LOCK"))

        # Unlocked: F8 & F9 fire normally
        hm._dispatch_hotkey(HOTKEY_ID_TOGGLE, "test")
        hm._dispatch_hotkey(HOTKEY_ID_WM, "test")
        self.assertEqual(fired, ["F8", "F9"])

        # Locked: F8 & F9 are shielded, Ctrl+Alt+L still fires
        hm.set_hotkeys_locked(True)
        hm._dispatch_hotkey(HOTKEY_ID_TOGGLE, "test")
        hm._dispatch_hotkey(HOTKEY_ID_WM, "test")
        hm._dispatch_hotkey(HOTKEY_ID_LOCK, "test")
        self.assertEqual(fired, ["F8", "F9", "LOCK"])

        # Overlay compact pill displays lock badge
        win = RivenOverlayWindow(click_through=True)
        try:
            win.set_hotkeys_locked(True)
            self.assertIn("热键已锁定", win._pill_label.text())
        finally:
            win.close()

    def test_offscreen_showcase_png_export_and_qol_settings_roundtrip(self) -> None:
        """Off-screen QPainter showcase PNG exports valid image file without monitor capture."""
        card = self._make_card([
            "托里德 Crita-acrican",
            "+198.0% 暴击几率",
            "+158.0% 暴击伤害",
            "+118.0% 多重射击",
            "-54.0% 变焦",
            "段位 16 O 12",
        ])
        wm_rep = WMMarketReport(
            weapon_name="托里德",
            weapon_slug="torid",
            weapon_class="rifle",
            min_price=1200,
            median_price=1650,
            sample_count=18,
            online_count=5,
            match_level="exact_3",
            match_label_zh="精准匹配 3 词条",
            match_label_en="Exact 3-Stat Match",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            res = export_riven_showcase(
                card,
                wm_report=wm_rep,
                lang="zh_CN",
                output_dir=tmpdir,
                copy_to_clipboard=True,
            )
            self.assertTrue(res.ok, msg=res.error)
            self.assertIsNotNone(res.path)
            self.assertTrue(res.path.is_file())
            self.assertTrue(res.path.name.startswith("Riven_托里德_"))
            self.assertEqual(res.width, 560)
            self.assertGreaterEqual(res.height, 360)
            raw = res.path.read_bytes()
            self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(raw), 5000)

            # Verify QoL settings persistence & control panel binding
            cfg_path = Path(tmpdir) / "user_settings.json"
            s = UserSettings(
                auto_collapse_seconds=10,
                auto_hide_on_focus_loss=False,
                audio_cue_enabled=False,
            )
            save_user_settings(s, path=cfg_path)
            loaded = load_user_settings(path=cfg_path)
            self.assertEqual(loaded.auto_collapse_seconds, 10)
            self.assertFalse(loaded.auto_hide_on_focus_loss)
            self.assertFalse(loaded.audio_cue_enabled)

            panel = RivenControlPanel(settings=loaded, initial_ui_lang="zh_CN")
            try:
                self.assertFalse(hasattr(panel, "_cmb_collapse"))
                self.assertFalse(hasattr(panel, "_chk_ctrl_drag"))
                self.assertFalse(panel._chk_auto_hide_focus.isChecked())
                self.assertFalse(panel._chk_audio_cue.isChecked())
            finally:
                panel.close()

    def test_f9_page_presence_hold_until_page_exit(self) -> None:
        """F9 capture holds Riven/Relic overlay open indefinitely until user exits that screen."""
        import numpy as np
        from src.ocr_worker import PageExitDetector
        from src.relic_prices import RelicRewardMatch
        from src.roi_manager import MODE_CHAT_RIVEN, MODE_RELIC_REWARDS

        # 1. Pure visual PageExitDetector behavior
        rng = np.random.default_rng(42)
        # Simulated structured Riven/Relic ROI thumbnail (64x96)
        anchor = np.zeros((64, 96), dtype=np.uint8)
        anchor[10:54, 15:81] = 35
        anchor[16:22, 20:75] = 210
        anchor[28:33, 22:70] = 185
        anchor[38:43, 22:65] = 175

        detector = PageExitDetector(anchor, exit_confirm_frames=2)

        # Mild background particle noise / brightness drift -> stays pinned (False)
        noisy_same = np.clip(anchor.astype(np.int16) + rng.integers(-12, 13, size=anchor.shape), 0, 255).astype(np.uint8)
        self.assertFalse(detector.update(noisy_same))
        self.assertEqual(detector.consecutive_exits, 0)

        # Completely different scene (exited Riven/Relic page to Orbiter/Mission)
        exited_scene = np.full((64, 96), 140, dtype=np.uint8)
        exited_scene[0:30, 0:45] = 230
        exited_scene[32:64, 48:96] = 40

        # 1st exit frame -> debounce waits for 2nd confirmation
        self.assertFalse(detector.update(exited_scene))
        self.assertEqual(detector.consecutive_exits, 1)

        # Single glitch returning to same page resets counter
        self.assertFalse(detector.update(noisy_same))
        self.assertEqual(detector.consecutive_exits, 0)

        # 2 consecutive exit frames -> confirmed page exit!
        self.assertFalse(detector.update(exited_scene))
        self.assertTrue(detector.update(exited_scene))

        # 2. Overlay F9 page-hold lifecycle (Chat Riven + WM & Void Relic)
        win = RivenOverlayWindow(click_through=True, auto_collapse_seconds=5)
        try:
            # --- Chat Riven mode ---
            win.set_work_mode(MODE_CHAT_RIVEN)
            win.set_f9_page_hold(True)
            card = self._make_card("托里德", 160.0)
            win.update_cards([card], layout_label="聊天链接紫卡", lang="zh_CN", force_redraw=True)
            wm_rep = WMMarketReport(
                weapon_name="托里德",
                weapon_slug="torid",
                weapon_class="rifle",
                min_price=1200,
                median_price=1650,
                sample_count=12,
                online_count=4,
            )
            win.show_wm_report(wm_rep)

            # While held, collapse timer MUST NOT run and manual timeout MUST NOT collapse
            self.assertTrue(win.is_f9_page_held)
            self.assertFalse(win._collapse_timer.isActive())
            win._on_auto_collapse_timeout()
            self.assertFalse(win.is_compact_pill)
            self.assertTrue(win.isVisible())
            self.assertTrue(win.wm_window.isVisible())

            # Exited Chat Riven page -> collapses cleanly to pill & hides WM window
            win.release_f9_page_hold(collapse_now=True)
            self.assertFalse(win.is_f9_page_held)
            self.assertTrue(win.is_compact_pill)
            self.assertFalse(win.wm_window.isVisible())

            # --- Void Relic mode ---
            win.set_work_mode(MODE_RELIC_REWARDS)
            win.set_f9_page_hold(True)
            relic_items = [
                RelicRewardMatch(
                    raw_text="Lavos Prime 系统",
                    matched_zh="Lavos Prime 系统",
                    matched_en="Lavos Prime Systems",
                    plat_price=45.0,
                    ducats=100,
                    is_matched=True,
                    is_best_plat=True,
                ),
            ]
            win.show_relic_rewards(relic_items, elapsed_ms=120.0)
            self.assertTrue(win.relic_window.isVisible())
            self.assertFalse(win._collapse_timer.isActive())
            win._on_auto_collapse_timeout()
            self.assertTrue(win.relic_window.isVisible())

            # Exited Void Relic selection screen -> hides relic window & collapses to pill
            win.release_f9_page_hold(collapse_now=True)
            self.assertFalse(win.relic_window.isVisible())
            self.assertTrue(win.is_compact_pill)

            # ESC standby pill keeps active work mode intact and stays collapsed on waiting ticks
            win.set_work_mode(MODE_CHAT_RIVEN)
            win.set_f9_page_hold(True)
            win.update_cards([card], layout_label="聊天链接紫卡", lang="zh_CN", force_redraw=True)
            win.show_wm_report(wm_rep)
            win.enter_standby_pill()
            self.assertFalse(win.is_f9_page_held)
            self.assertTrue(win.is_compact_pill)
            self.assertTrue(win.isVisible())
            self.assertFalse(win.wm_window.isVisible())
            self.assertEqual(win._work_mode, MODE_CHAT_RIVEN)

            # Background waiting tick must NOT re-expand a user-collapsed standby pill
            win.show_monitoring_waiting(keep_pill_if_collapsed=True)
            self.assertTrue(win.is_compact_pill)
            self.assertTrue(win.isVisible())
        finally:
            win.close()

    def test_dual_98pct_rainbow_border_and_rare_drop_sound_rules(self) -> None:
        """Dual-card 98%+ positive non-hybrid triggers 3s rainbow border; rare_drop.wav & custom WAV work."""
        import wave
        from src.audio_cue import get_default_rare_sound_path
        from src.evaluator import (
            RivenEvaluation,
            StatEvaluation,
            has_rare_drop_stat,
            is_98pct_positive_highlight_stat,
            should_play_reroll_rare_sound,
        )
        from src.overlay_ui import SingleRivenCardWidget
        from src.riven_parser import RivenData, RivenStat

        # 1. Verify generated assets/sounds/rare_drop.wav specs (44.1kHz, 16-bit stereo, 1.0s)
        rare_path = get_default_rare_sound_path()
        self.assertTrue(rare_path.is_file(), f"Missing {rare_path}")
        with wave.open(str(rare_path), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 2)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertEqual(wf.getframerate(), 44100)
            self.assertEqual(wf.getnframes(), 44100)

        # 2. Verify custom WAV replace / invalid rejection / reset & settings persistence
        player = AudioCuePlayer(enabled=True)
        self.assertEqual(player.active_rare_wav_bytes, rare_path.read_bytes())
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_wav = Path(tmpdir) / "my_ssr.wav"
            custom_wav.write_bytes(player.normal_wav_bytes)
            ok, fname = player.set_custom_rare_sound(custom_wav)
            self.assertTrue(ok)
            self.assertEqual(fname, "my_ssr.wav")
            self.assertEqual(player.active_rare_wav_bytes, player.normal_wav_bytes)

            bad_wav = Path(tmpdir) / "not_wav.wav"
            bad_wav.write_bytes(b"NOT_A_WAV_FILE")
            ok_bad, _ = player.set_custom_rare_sound(bad_wav)
            self.assertFalse(ok_bad)

            # Reset back to default rare_drop.wav
            ok_rst, _ = player.set_custom_rare_sound("")
            self.assertTrue(ok_rst)
            self.assertEqual(player.active_rare_wav_bytes, rare_path.read_bytes())

            # Settings roundtrip + Control Panel display
            cfg_path = Path(tmpdir) / "user_settings.json"
            s = UserSettings(custom_rare_sound_path=str(custom_wav))
            save_user_settings(s, path=cfg_path)
            loaded = load_user_settings(path=cfg_path)
            self.assertEqual(loaded.custom_rare_sound_path, str(custom_wav))
            panel = RivenControlPanel(settings=loaded, initial_ui_lang="zh_CN")
            try:
                self.assertIn("my_ssr.wav", panel._lbl_sound_file.text())
                emitted: list[str] = []
                panel.custom_rare_sound_changed.connect(emitted.append)
                panel._btn_reset_sound.click()
                self.assertEqual(emitted, [""])
                self.assertIn("rare_drop.wav", panel._lbl_sound_file.text())
            finally:
                panel.close()

        # 3. Verify strict 98%+ positive non-hybrid filter & S rare-drop trigger rules
        se_pos_98 = StatEvaluation(
            stat_name="暴击几率", value=199.5, sign="+", unit="%",
            is_negative=False, is_locked=False, roll_pct=98.5, grade="S", is_hybrid=False,
        )
        se_pos_97 = StatEvaluation(
            stat_name="暴击伤害", value=155.0, sign="+", unit="%",
            is_negative=False, is_locked=False, roll_pct=97.9, grade="S", is_hybrid=False,
        )
        se_hybrid_99 = StatEvaluation(
            stat_name="暴击几率", value=120.0, sign="+", unit="%",
            is_negative=False, is_locked=False, roll_pct=99.5, grade="S", is_hybrid=True,
        )
        se_inv_neg_99 = StatEvaluation(
            stat_name="暴击几率", value=99.0, sign="-", unit="%",
            is_negative=True, is_locked=False, roll_pct=99.2, grade="S", is_inverted_god_neg=True, is_recommended=True,
        )
        se_harmless_neg_s = StatEvaluation(
            stat_name="变焦", value=53.0, sign="-", unit="%",
            is_negative=True, is_locked=False, roll_pct=95.0, grade="S", is_recommended=True,
        )
        se_harmful_neg_s = StatEvaluation(
            stat_name="伤害", value=110.0, sign="-", unit="%",
            is_negative=True, is_locked=False, roll_pct=96.0, grade="S", is_recommended=False,
        )
        se_pos_a = StatEvaluation(
            stat_name="伤害", value=180.0, sign="+", unit="%",
            is_negative=False, is_locked=False, roll_pct=70.0, grade="A", is_hybrid=False,
        )

        self.assertTrue(is_98pct_positive_highlight_stat(se_pos_98))
        self.assertFalse(is_98pct_positive_highlight_stat(se_pos_97))
        self.assertFalse(is_98pct_positive_highlight_stat(se_hybrid_99))
        self.assertFalse(is_98pct_positive_highlight_stat(se_inv_neg_99))
        self.assertFalse(is_98pct_positive_highlight_stat(se_harmless_neg_s))

        # Rare-drop sound: S positive OR S positive-benefit negative -> True; S harmful negative alone -> False
        self.assertTrue(has_rare_drop_stat(RivenEvaluation(stat_evaluations=[se_pos_97])))
        self.assertTrue(has_rare_drop_stat(RivenEvaluation(stat_evaluations=[se_pos_a, se_inv_neg_99])))
        self.assertTrue(has_rare_drop_stat(RivenEvaluation(stat_evaluations=[se_pos_a, se_harmless_neg_s])))
        self.assertFalse(has_rare_drop_stat(RivenEvaluation(stat_evaluations=[se_pos_a, se_harmful_neg_s])))

        # Locked S stat MUST NOT trigger rare-drop sound!
        se_locked_s = StatEvaluation(
            stat_name="暴击几率", value=199.5, sign="+", unit="%",
            is_negative=False, is_locked=True, roll_pct=98.5, grade="S", is_hybrid=False,
        )
        self.assertFalse(has_rare_drop_stat(RivenEvaluation(stat_evaluations=[se_locked_s, se_pos_a])))

        # Strictly limited to dual-card (`reroll`) mode & unlocked (non-carried-over) S stats
        left_ev_with_s = RivenEvaluation(stat_evaluations=[se_pos_98, se_pos_a])
        right_ev_carried_s = RivenEvaluation(stat_evaluations=[se_pos_98, se_pos_a])  # same S carried over from left
        right_ev_new_s = RivenEvaluation(stat_evaluations=[se_pos_98, se_pos_97])      # new unlocked S (se_pos_97)
        c_left = type("_C", (), {"evaluation": left_ev_with_s})()
        c_right_carried = type("_C", (), {"evaluation": right_ev_carried_s})()
        c_right_new = type("_C", (), {"evaluation": right_ev_new_s})()

        self.assertFalse(should_play_reroll_rare_sound([c_right_new], "single_riven"))
        self.assertFalse(should_play_reroll_rare_sound([c_right_new], "chat_riven"))
        self.assertFalse(should_play_reroll_rare_sound([c_left, c_right_carried], "reroll"))
        self.assertTrue(should_play_reroll_rare_sound([c_left, c_right_new], "reroll"))

        # 4. Verify UI dual-card rainbow border activates ONLY on right card's non-hybrid >=98% positive stat for 3000ms
        def _card_with_evals(lbl: str, evals: list[StatEvaluation]) -> EvaluatedRivenCard:
            riven = RivenData(
                raw_lines=[f"托里德 Visi-crita"],
                weapon_name="托里德",
                riven_name="托里德 Visi-crita",
                riven_suffix="Visi-crita",
                stats=[
                    RivenStat(
                        raw_text="",
                        raw_name=e.stat_name,
                        stat_name=e.stat_name,
                        value=e.value,
                        signed_value=-e.value if e.is_negative else e.value,
                        sign=e.sign,
                        unit=e.unit,
                        is_negative=e.is_negative,
                        is_hybrid=e.is_hybrid,
                    )
                    for e in evals
                ],
                is_valid=True,
                detected_language="zh_CN",
            )
            ev = RivenEvaluation(
                weapon_name="托里德",
                weapon_class="rifle",
                disposition=1.20,
                overall_grade="S",
                overall_roll_pct=92.0,
                stat_evaluations=evals,
            )
            return EvaluatedRivenCard(label=lbl, riven=riven, evaluation=ev, avg_confidence=0.98)

        left_card = _card_with_evals("[当前]", [se_pos_98, se_pos_a])
        right_card = _card_with_evals("[新循环]", [se_pos_98, se_hybrid_99, se_inv_neg_99])

        win = RivenOverlayWindow(click_through=True, auto_collapse_seconds=0)
        try:
            # Single-card mode: NEVER activates rainbow border
            win.update_cards([right_card], layout_label="单卡识别", lang="zh_CN", force_redraw=True)
            single_w = win._cards_row.itemAt(0).widget()
            assert isinstance(single_w, SingleRivenCardWidget)
            self.assertEqual(len(single_w.rainbow_stat_widgets), 0)

            # Dual-card mode: Left card has 0 rainbow widgets; Right card highlights ONLY se_pos_98 (not hybrid, not inv_neg)
            win.update_cards([left_card, right_card], layout_label="洗卡双卡对比", lang="zh_CN", force_redraw=True)
            left_w = win._cards_row.itemAt(0).widget()
            right_w = win._cards_row.itemAt(1).widget()
            assert isinstance(left_w, SingleRivenCardWidget)
            assert isinstance(right_w, SingleRivenCardWidget)
            self.assertEqual(len(left_w.rainbow_stat_widgets), 0)
            self.assertEqual(len(right_w.rainbow_stat_widgets), 1)
            rb = right_w.rainbow_stat_widgets[0]
            self.assertTrue(rb.is_rainbow_active)
            self.assertEqual(rb.rainbow_duration_ms, 3000)
            rb.stop_rainbow()
            self.assertFalse(rb.is_rainbow_active)
        finally:
            win.close()

        # 5. Verify Platinum & Ducats currency icon assets exist and load cleanly
        from PyQt6.QtWidgets import QLabel
        from src.companion_overlays import (
            RelicRewardsOverlayWindow,
            WMPriceOverlayWindow,
            get_currency_icon_path,
            load_currency_pixmap,
        )
        from src.relic_prices import RelicRewardMatch
        from src.wm_client import WMMatchingListing

        for kind in ("platinum", "ducats"):
            icon_p = get_currency_icon_path(kind)
            self.assertTrue(icon_p.is_file(), f"Missing currency icon: {icon_p}")
            pm = load_currency_pixmap(kind, 18)
            self.assertFalse(pm.isNull())
            self.assertLessEqual(pm.width(), 18)
            self.assertLessEqual(pm.height(), 18)

        # 6. Verify RelicRewardsOverlayWindow floats ABOVE anchor ROI & renders strict 4-slot order:
        #    [★ (Rank 1 Gold / Rank 2 Silver)] -> [45p] -> [|] -> [100D] -> [VaultedChestIcon 18x18 if vaulted]
        relic_win = RelicRewardsOverlayWindow(click_through=True, lang="zh_CN")
        wm_win = WMPriceOverlayWindow(click_through=True, lang="zh_CN")
        try:
            anchor = (0.24115, 0.20093, 0.52135, 0.25833)
            items = [
                RelicRewardMatch("Lavos Prime 系统", "Lavos Prime 系统", "Lavos Prime Systems", 45.0, 100, is_matched=True, is_best_plat=True, is_vaulted=True, recommend_rank=1),
                RelicRewardMatch("Orthos Prime 握柄", "Orthos Prime 握柄", "Orthos Prime Handle", 12.0, 45, is_matched=True, is_vaulted=False, recommend_rank=2),
            ]
            relic_win.show_relic_rewards(items, elapsed_ms=90.0, anchor_ratios=anchor)
            self.assertEqual(relic_win.row_count, 2)
            # Must sit ABOVE the items ROI (never blocking selection area)
            screen = relic_win.screen()
            if screen is not None:
                geo = screen.geometry()
                roi_top = geo.top() + int(round(anchor[1] * geo.height()))
                self.assertLessEqual(relic_win.y() + relic_win.height(), roi_top)

            # Inspect first reward card children: NO item name, NO P/D text, exact [rank1_icon] -> 45 [plat_icon] -> | -> 100 [ducat_icon] -> VaultedChestIcon
            card0 = relic_win._row_widgets[0]
            lay0 = card0.layout()
            widgets0 = [lay0.itemAt(i).widget() for i in range(lay0.count()) if lay0.itemAt(i).widget() is not None]
            texts0 = [w.text() for w in widgets0 if isinstance(w, QLabel) and w.text()]
            self.assertEqual(texts0, ["45", "|", "100"])
            for t in texts0:
                self.assertNotIn("Lavos", t)
                self.assertNotIn("p", t.lower())
                self.assertNotIn("D", t)
            self.assertEqual(
                [w.objectName() for w in widgets0],
                ["RelicRankStar", "RelicPlatLabel", "RelicPlatIcon", "RelicDivider", "RelicDucatLabel", "RelicDucatIcon", "VaultedChestIcon"],
            )
            self.assertFalse(widgets0[0].pixmap().isNull())
            self.assertFalse(widgets0[2].pixmap().isNull())
            self.assertFalse(widgets0[5].pixmap().isNull())
            self.assertFalse(widgets0[6].pixmap().isNull())

            # Unvaulted 2nd card has silver rank emblem icon, plat+ducat icons, and NO VaultedChestIcon
            card1 = relic_win._row_widgets[1]
            lay1 = card1.layout()
            widgets1 = [lay1.itemAt(i).widget() for i in range(lay1.count()) if lay1.itemAt(i).widget() is not None]
            texts1 = [w.text() for w in widgets1 if isinstance(w, QLabel) and w.text()]
            self.assertEqual(texts1, ["12", "|", "45"])
            self.assertFalse(widgets1[0].pixmap().isNull())
            self.assertFalse(any(w.objectName() == "VaultedChestIcon" for w in widgets1))

            # Verify WMPriceOverlayWindow puts Platinum icon AFTER price numbers and strips 'p'
            rep = WMMarketReport(
                weapon_name="托里德",
                weapon_slug="torid",
                weapon_class="rifle",
                min_price=600,
                median_price=900,
                sample_count=10,
                online_count=3,
                top_listings=[WMMatchingListing(price=600, seller_status="ingame", is_online=True, positives=["伤害"], negatives=["变焦"], rerolls=12)],
            )
            wm_win.show_wm_report(rep)
            self.assertNotIn("600p", wm_win._wm_price.text())
            self.assertNotIn("900p", wm_win._wm_price.text())
            self.assertIn("600&nbsp;<img", wm_win._wm_price.text())
            self.assertNotIn("600p", wm_win._wm_listings.text())
            self.assertIn("600&nbsp;<img", wm_win._wm_listings.text())
        finally:
            relic_win.close()
            wm_win.close()

    def test_dynamic_1_2_3_4_relic_columns_audio_path_and_panel_geometry_memory(self) -> None:
        """Dynamic 1/2/3/4 relic clustering (incl. centered trio), WAV playback path, and 600x800 scrollable panel memory."""
        from src.companion_overlays import RelicRewardsOverlayWindow
        from src.ocr_engine import OCRBox
        from src.relic_prices import PrimeItemPrice, PrimePriceStore, split_ocr_boxes_into_relic_columns_with_centers
        from src.settings_manager import DEFAULT_PANEL_HEIGHT, DEFAULT_PANEL_WIDTH, MIN_PANEL_WIDTH

        def _box(text: str, x0: float, x1: float, y0: float, y1: float) -> OCRBox:
            return OCRBox(text=text, confidence=0.98, bbox=[[x0, y0], [x1, y0], [x1, y1], [x0, y1]])

        # Build deterministic in-memory PrimePriceStore
        store = PrimePriceStore(cache_path=Path(tempfile.gettempdir()) / "__nonexistent_relic_test__.json")
        store.items = [
            PrimeItemPrice("1", "lavos_prime_systems", "Lavos Prime 系统", "Lavos Prime Systems", 45.0, 100),
            PrimeItemPrice("2", "caliban_prime_blueprint", "Caliban Prime 蓝图", "Caliban Prime Blueprint", 18.0, 45),
            PrimeItemPrice("3", "orthos_prime_handle", "欧特鲁斯 Prime 握柄", "Orthos Prime Handle", 10.0, 45),
            PrimeItemPrice("4", "paris_prime_lower_limb", "帕里斯 Prime 下弓臂", "Paris Prime Lower Limb", 5.0, 15),
        ]
        store._rebuild_indices()

        roi_w = 1000
        # 1. Solo (1 centered relic right at x=500, wrapped 2 lines straddling 500) -> MUST be 1 reward!
        solo_boxes = [
            _box("Lavos Prime", 440, 552, 200, 224),
            _box("系统", 488, 526, 228, 250),
        ]
        solo_matches = store.match_ocr_boxes(solo_boxes, image_width=roi_w)
        self.assertEqual(len(solo_matches), 1)
        self.assertEqual(solo_matches[0].matched_zh, "Lavos Prime 系统")
        self.assertAlmostEqual(solo_matches[0].column_center_x_ratio or 0.0, 0.50, delta=0.06)

        # 2. Duo (2 centered relics at 37.5% and 62.5%) -> MUST be 2 rewards!
        duo_boxes = [
            _box("Lavos Prime", 315, 435, 200, 224),
            _box("系统", 355, 395, 228, 250),
            _box("Caliban Prime", 565, 685, 200, 224),
            _box("蓝图", 605, 645, 228, 250),
        ]
        duo_matches = store.match_ocr_boxes(duo_boxes, image_width=roi_w)
        self.assertEqual(len(duo_matches), 2)

        # 3. Trio (3 centered relics at 25%, 50%, 75% — exact old 4-bucket boundaries!) -> MUST be 3 rewards!
        trio_boxes = [
            _box("Lavos Prime", 190, 305, 200, 224),
            _box("系统", 232, 272, 228, 250),
            _box("Caliban Prime", 440, 560, 200, 224),
            _box("蓝图", 482, 522, 228, 250),
            _box("欧特鲁斯 Prime", 690, 810, 200, 224),
            _box("握柄", 732, 772, 228, 250),
        ]
        trio_cols = split_ocr_boxes_into_relic_columns_with_centers(trio_boxes, image_width=roi_w)
        self.assertEqual(len(trio_cols), 3)
        trio_matches = store.match_ocr_boxes(trio_boxes, image_width=roi_w)
        self.assertEqual(len(trio_matches), 3)

        # Verify RelicRewardsOverlayWindow scales width proportionally (1 < 3 < 4 cards)
        relic_win = RelicRewardsOverlayWindow(click_through=True, lang="zh_CN")
        try:
            anchor = (0.24, 0.20, 0.52, 0.26)
            relic_win.show_relic_rewards(solo_matches, anchor_ratios=anchor)
            w1 = relic_win.width()
            self.assertEqual(relic_win.row_count, 1)
            relic_win.show_relic_rewards(trio_matches, anchor_ratios=anchor)
            w3 = relic_win.width()
            self.assertEqual(relic_win.row_count, 3)
            self.assertGreater(w3, w1)
        finally:
            relic_win.close()

        # 4. AudioCuePlayer resolves real on-disk WAV paths (avoids Win32 SND_MEMORY|SND_ASYNC RuntimeError)
        player = AudioCuePlayer(enabled=True)
        rare_file = player._resolve_playback_wav_path("rare_test", player.active_rare_wav_bytes)
        normal_file = player._resolve_playback_wav_path("normal", player.normal_wav_bytes)
        self.assertTrue(rare_file.is_file())
        self.assertTrue(normal_file.is_file())

        # 5. Control Panel default 600x800, QScrollArea, section spacing >= 20, & geometry memory persistence
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "user_settings.json"
            default_panel = RivenControlPanel(settings=UserSettings(), initial_ui_lang="zh_CN")
            try:
                self.assertEqual(default_panel.width(), DEFAULT_PANEL_WIDTH)
                self.assertEqual(default_panel.height(), DEFAULT_PANEL_HEIGHT)
                self.assertEqual(default_panel.minimumWidth(), MIN_PANEL_WIDTH)
                self.assertIsNotNone(default_panel._scroll_area)
                self.assertGreaterEqual(default_panel._scroll_content.layout().spacing(), 20)
                self.assertLessEqual(default_panel._scroll_content.minimumSizeHint().width(), MIN_PANEL_WIDTH)

                # Simulate user resizing & moving, then closing -> emits & persists geometry
                recorded: list[tuple[int, int, int, int]] = []
                default_panel.panel_geometry_changed.connect(
                    lambda x, y, w, h: recorded.append((x, y, w, h))
                )
                default_panel.show()
                default_panel.move(120, 90)
                default_panel.resize(680, 740)
                default_panel.close()
                self.assertTrue(len(recorded) >= 1)
                gx, gy, gw, gh = recorded[-1]
                self.assertEqual((gw, gh), (680, 740))

                saved = UserSettings(panel_geometry=[gx, gy, gw, gh])
                save_user_settings(saved, path=cfg_path)
            finally:
                default_panel.hide()

            reloaded = load_user_settings(path=cfg_path)
            self.assertEqual(reloaded.panel_geometry, [gx, gy, gw, gh])
            restored_panel = RivenControlPanel(settings=reloaded, initial_ui_lang="zh_CN")
            try:
                self.assertEqual(restored_panel.width(), 680)
                self.assertEqual(restored_panel.height(), 740)
            finally:
                restored_panel.hide()


if __name__ == "__main__":
    unittest.main()
