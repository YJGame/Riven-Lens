"""Unit tests for:
  1. Positive-only 6 compound elements & exact `+111.2% 病毒伤害` -> 99% S-grade math.
  2. Void Relic 4-column X-coordinate OCR splitting (fixing glued-row bug) & 4-row UI.
  3. Reroll mode `3+1-` / `2+1-` + S-grade independent compound-element synergy popup.
  4. All 4 independent draggable overlay windows (`riven`, `wm`, `synergy`, `relic`) & position persistence.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.evaluator import RivenEvaluator, analyze_reroll_synergy_alert
from src.ocr_engine import OCRBox
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
from src.relic_prices import PRIME_PRICE_STORE, split_ocr_boxes_into_relic_columns
from src.riven_parser import POSITIVE_ONLY_COMPOUND_ELEMENTS, RivenParser
from src.settings_manager import UserSettings, load_user_settings, save_user_settings


def _box(text: str, x1: float, y1: float, x2: float, y2: float) -> OCRBox:
    return OCRBox(
        text=text,
        confidence=0.96,
        bbox=[[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
    )


class TestCompoundSynergyRelicAndWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_compound_elements_strictly_positive_only_and_exact_viral_s_grade(self) -> None:
        """All 6 compound elements are positive-only; +111.2% 病毒伤害 (3+1-, 1.20) -> 99% S."""
        self.assertEqual(
            POSITIVE_ONLY_COMPOUND_ELEMENTS,
            frozenset({"病毒伤害", "腐蚀伤害", "辐射伤害", "磁力伤害", "毒气伤害", "爆炸伤害"}),
        )

        # Positive +111.2% Viral in 3+1- at unified 1.20 disposition -> [91.1, 111.4], 99%, S
        r_pos = self.parser.parse([
            "托里德 Visi-crita",
            "+111.2% 病毒伤害",
            "+175.0% 暴击几率",
            "+140.0% 暴击伤害",
            "-52.0% 变焦",
            "段位 16 O 7",
        ])
        self.assertTrue(r_pos.is_valid, msg=r_pos.validation_error)
        ev = self.evaluator.evaluate(r_pos)
        by_name = {se.stat_name: se for se in ev.stat_evaluations}
        viral = by_name["病毒伤害"]
        self.assertAlmostEqual(viral.min_value, 91.1, places=1)
        self.assertAlmostEqual(viral.max_value, 111.4, places=1)
        self.assertGreaterEqual(viral.roll_pct, 98.5)
        self.assertLessEqual(viral.roll_pct, 100.0)
        self.assertEqual(viral.grade, "S")

        # Negative compound element MUST be rejected by validator
        for elem in ("病毒伤害", "腐蚀伤害", "辐射伤害", "磁力伤害", "毒气伤害", "爆炸伤害"):
            r_neg = self.parser.parse([
                "托里德 Visi-crita",
                "+175.0% 暴击几率",
                "+140.0% 暴击伤害",
                f"-48.0% {elem}",
                "段位 16 O 7",
            ])
            self.assertFalse(r_neg.is_valid, msg=f"Negative {elem} should never be valid")

    def test_relic_four_column_x_split_prevents_gluing_and_matches_all_four(self) -> None:
        """Simulate exact 4-column screenshot boxes: Lavos / Caliban / Orthos / Paris -> 4 priced items."""
        boxes = [
            # Col 0 (leftmost, 2 stacked lines)
            _box("Lavos Prime", 40, 120, 210, 150),
            _box("机体 蓝图", 60, 158, 195, 186),
            # Col 1
            _box("Caliban Prime", 285, 120, 465, 150),
            _box("蓝图", 345, 158, 410, 186),
            # Col 2
            _box("欧特鲁斯 Prime", 535, 120, 725, 150),
            _box("刀刃", 600, 158, 660, 186),
            # Col 3 (rightmost)
            _box("帕里斯 Prime", 785, 120, 965, 150),
            _box("下弓臂", 840, 158, 920, 186),
        ]

        cols = split_ocr_boxes_into_relic_columns(boxes, image_width=1000)
        self.assertEqual(
            cols,
            [
                "Lavos Prime 机体 蓝图",
                "Caliban Prime 蓝图",
                "欧特鲁斯 Prime 刀刃",
                "帕里斯 Prime 下弓臂",
            ],
        )

        # Match against real cached `data/prime_prices.json`
        matches = PRIME_PRICE_STORE.match_ocr_boxes(boxes, image_width=1000)
        self.assertEqual(len(matches), 4)
        for m in matches:
            self.assertTrue(m.is_matched, msg=f"Failed to match relic item: {m.raw_text}")
            self.assertIsNotNone(m.plat_price)
            self.assertGreater(m.plat_price, 0.0)
            self.assertGreater(m.ducats, 0)
        self.assertTrue(any(m.is_best_plat for m in matches))
        self.assertTrue(any(m.is_best_ducats for m in matches))

        # Verify independent RelicRewardsOverlayWindow renders 4 separate row cards
        win = RivenOverlayWindow(click_through=True)
        try:
            win.show_relic_rewards(matches, elapsed_ms=82.0)
            self.assertTrue(win.relic_window.isVisible())
            self.assertEqual(win.relic_window.row_count, 4)
        finally:
            win.close()

        # Also test partial 2-column screenshot -> strictly 2 items
        two_col_boxes = boxes[:4]
        cols_2 = split_ocr_boxes_into_relic_columns(two_col_boxes, image_width=1000)
        self.assertEqual(len(cols_2), 2)

        # Also test glued single-line fallback safeguard
        glued_matches = PRIME_PRICE_STORE.match_ocr_lines(
            ["Caliban Prime 蓝图 欧特鲁斯 Prime 刀刃 帕里斯 Prime 下弓臂"]
        )
        self.assertEqual(len(glued_matches), 3)
        self.assertTrue(all(m.is_matched for m in glued_matches))

    def test_reroll_s_grade_synergy_popup_triggers_and_hides_correctly(self) -> None:
        """3+1- or 2+1- with >=1 S-grade positive triggers independent synergy popup; otherwise hides."""
        left_r = self.parser.parse([
            "托里德 Sati-visicron",
            "+140.0% 伤害",
            "+90.0% 多重射击",
            "段位 16 O 6",
        ])
        right_s_viral = self.parser.parse([
            "托里德 Visi-crita",
            "+111.2% 病毒伤害",
            "+180.0% 暴击几率",
            "+145.0% 暴击伤害",
            "-52.0% 变焦",
            "段位 16 O 7",
        ])
        right_2p1n_toxin_s = self.parser.parse([
            "托里德 Toxi-crita",
            "+145.0% 毒素伤害",
            "+235.0% 暴击几率",
            "-52.0% 变焦",
            "段位 16 O 8",
        ])
        right_no_s_2p0n = self.parser.parse([
            "托里德 Sati-ata",
            "+155.0% 伤害",
            "+95.0% 多重射击",
            "段位 16 O 9",
        ])

        left_card = EvaluatedRivenCard("[当前]", left_r, self.evaluator.evaluate(left_r), 0.96)
        right_card_s = EvaluatedRivenCard("[新循环]", right_s_viral, self.evaluator.evaluate(right_s_viral), 0.97)
        right_card_tox = EvaluatedRivenCard("[新循环]", right_2p1n_toxin_s, self.evaluator.evaluate(right_2p1n_toxin_s), 0.97)
        right_card_plain = EvaluatedRivenCard("[新循环]", right_no_s_2p0n, self.evaluator.evaluate(right_no_s_2p0n), 0.95)

        alert1 = analyze_reroll_synergy_alert([left_card, right_card_s])
        self.assertIsNotNone(alert1)
        self.assertEqual(alert1.combo_label_zh, "3正1负")
        self.assertTrue(any("病毒伤害" in s for s in alert1.s_grade_stats))
        self.assertTrue(any("病毒" in t for t in alert1.synergy_tips_zh))

        alert2 = analyze_reroll_synergy_alert([left_card, right_card_tox])
        self.assertIsNotNone(alert2)
        self.assertEqual(alert2.combo_label_zh, "2正1负")
        self.assertTrue(any("病毒" in t or "腐蚀" in t for t in alert2.synergy_tips_zh))

        alert_none = analyze_reroll_synergy_alert([left_card, right_card_plain])
        self.assertIsNone(alert_none)

        win = RivenOverlayWindow(click_through=True)
        try:
            win.update_cards([left_card, right_card_s], elapsed_ms=90.0, layout_label="洗卡双卡对比")
            self.assertTrue(win.synergy_window.isVisible())
            self.assertIn("3正1负", win.synergy_window._meta_lbl.text())
            self.assertIn("病毒", win.synergy_window._tips_lbl.text())

            # Switching to non-qualifying reroll cards automatically hides the synergy popup
            win.update_cards([left_card, right_card_plain], elapsed_ms=90.0, layout_label="洗卡双卡对比")
            self.assertFalse(win.synergy_window.isVisible())
        finally:
            win.close()

    def test_all_four_independent_windows_drag_unlock_and_persist_positions(self) -> None:
        """All 4 windows unlock/lock drag together and persist independent positions in user_settings.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "user_settings.json"
            s = UserSettings()
            s.set_window_pos("riven", 0.72, 0.05)
            s.set_window_pos("wm", 0.55, 0.58)
            s.set_window_pos("synergy", 0.04, 0.12)
            s.set_window_pos("relic", 0.30, 0.08)
            save_user_settings(s, path=cfg_path)

            loaded = load_user_settings(path=cfg_path)
            self.assertEqual(loaded.get_window_pos("riven"), (0.72, 0.05))
            self.assertEqual(loaded.get_window_pos("wm"), (0.55, 0.58))
            self.assertEqual(loaded.get_window_pos("synergy"), (0.04, 0.12))
            self.assertEqual(loaded.get_window_pos("relic"), (0.30, 0.08))

            win = RivenOverlayWindow(
                click_through=True,
                pos_x_ratio=loaded.get_window_pos("riven")[0],
                pos_y_ratio=loaded.get_window_pos("riven")[1],
                wm_pos=loaded.get_window_pos("wm"),
                synergy_pos=loaded.get_window_pos("synergy"),
                relic_pos=loaded.get_window_pos("relic"),
            )
            try:
                self.assertEqual(len(win.all_overlay_windows), 4)
                win.set_drag_unlocked(True, show_preview_if_empty=True)
                for w in win.all_overlay_windows:
                    self.assertTrue(w.is_drag_unlocked)

                win.set_drag_unlocked(False)
                for w in win.all_overlay_windows:
                    self.assertFalse(w.is_drag_unlocked)

                loaded.reset_all_positions()
                for key in ("riven", "wm", "synergy", "relic"):
                    self.assertEqual(loaded.get_window_pos(key), (None, None))
            finally:
                win.close()


if __name__ == "__main__":
    unittest.main()
