"""Unit tests for pure-selection per-weapon favorite Riven stats feature."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.control_panel import RivenControlPanel
from src.evaluator import RivenEvaluator
from src.favorite_stats_dialog import PAGE_STATS, PAGE_WEAPONS, FavoriteStatsDialog
from src.riven_parser import RivenParser
from src.settings_manager import UserSettings, load_user_settings, save_user_settings


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestFavoriteWeaponStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_settings_roundtrip_and_sanitization(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "user_settings.json"
            s = UserSettings()
            s.set_weapon_favorite_stats(
                "托里德",
                ["暴击几率", "暴击伤害", "暴击几率", "射速"],
                ["变焦", "变焦"],
            )
            self.assertEqual(
                s.get_weapon_favorite_stats("托里德"),
                {"positives": ["暴击几率", "暴击伤害", "射速"], "negatives": ["变焦"]},
            )
            save_user_settings(s, path=cfg)

            loaded = load_user_settings(path=cfg)
            self.assertEqual(
                loaded.favorite_weapon_stats,
                {"托里德": {"positives": ["暴击几率", "暴击伤害", "射速"], "negatives": ["变焦"]}},
            )

            # Removing or saving empty lists removes the weapon entry cleanly
            loaded.set_weapon_favorite_stats("托里德", [], [])
            self.assertNotIn("托里德", loaded.favorite_weapon_stats)

    def test_available_stats_strictly_filtered_by_weapon_class_and_polarity(self) -> None:
        rifle_pos, rifle_neg = self.evaluator.get_available_stats_for_class("rifle")
        melee_pos, melee_neg = self.evaluator.get_available_stats_for_class("melee")

        # Rifle has Multishot & Zoom, NOT Melee Range / Initial Combo
        self.assertIn("多重射击", rifle_pos)
        self.assertIn("变焦", rifle_neg)
        self.assertNotIn("攻击范围", rifle_pos)
        self.assertNotIn("初始连击", rifle_pos)

        # Melee has Range & Initial Combo & Attack Speed, NOT Multishot / Zoom
        self.assertIn("攻击范围", melee_pos)
        self.assertIn("初始连击", melee_pos)
        self.assertIn("攻击速度", melee_pos)
        self.assertNotIn("多重射击", melee_pos)
        self.assertNotIn("变焦", melee_neg)

        # Positive-only elemental/compound/utility stats MUST NOT appear in negative pool
        for pos_only in ("毒素伤害", "火焰伤害", "病毒伤害", "腐蚀伤害", "穿透"):
            self.assertIn(pos_only, rifle_pos)
            self.assertNotIn(pos_only, rifle_neg)
        self.assertNotIn("初始连击", melee_neg)
        self.assertNotIn("重击效率", melee_neg)

    def test_evaluator_prioritizes_user_favorites_over_builtin_and_special_rules(self) -> None:
        # Customize Torid with user's personal preference (Status Chance + Fire Rate + Electric, -Ammo Max)
        self.evaluator.set_user_favorite_stats(
            {
                "托里德": {
                    "positives": ["触发几率", "射速", "电击伤害"],
                    "negatives": ["弹药最大值"],
                }
            }
        )
        riven = self.parser.parse(
            [
                "托里德 Hexa-cronitron",
                "+110.0% 触发几率",
                "+72.0% 射速",
                "+108.0% 电击伤害",
                "-48.0% 弹药最大值",
                "段位 16 O 3",
            ]
        )
        ev = self.evaluator.evaluate(riven)
        self.assertEqual(ev.recommendation_source, "user_custom")
        self.assertEqual(ev.recommendation_source_cn, "自选词条")
        self.assertEqual(ev.recommended_positives, ["触发几率", "射速", "电击伤害"])
        self.assertEqual(ev.recommended_negatives, ["弹药最大值"])
        self.assertEqual(ev.matched_rec_pos, 3)
        self.assertTrue(ev.matched_rec_neg)
        self.assertTrue(all(se.is_recommended for se in ev.stat_evaluations))

        # Also verify user favorites override Incarnon special_rules (e.g. 奏凯 / Laetum)
        self.evaluator.set_user_favorite_stats(
            {
                "奏凯": {
                    "positives": ["装填速度", "弹匣容量"],
                    "negatives": ["变焦"],
                }
            }
        )
        r_laetum = self.parser.parse(
            [
                "奏凯 Feva-magna",
                "+60.0% 装填速度",
                "+60.0% 弹匣容量",
                "-60.0% 变焦",
                "段位 14 O 1",
            ]
        )
        ev_laetum = self.evaluator.evaluate(r_laetum)
        self.assertEqual(ev_laetum.recommendation_source, "user_custom")
        self.assertEqual(ev_laetum.recommended_positives, ["装填速度", "弹匣容量"])
        self.assertEqual(ev_laetum.matched_rec_pos, 2)
        self.assertTrue(ev_laetum.matched_rec_neg)

    def test_pure_selection_dialog_two_step_workflow_and_panel_button(self) -> None:
        settings = UserSettings()
        panel = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN")
        dlg = FavoriteStatsDialog(evaluator=self.evaluator, settings=settings, lang="zh_CN")
        try:
            # Control panel button emits signal cleanly
            clicked = []
            panel.favorite_stats_requested.connect(lambda: clicked.append(True))
            panel._btn_favorite_stats.click()
            self.assertEqual(clicked, [True])

            # Step 1: starts on weapon picker page with class filter pills
            self.assertEqual(dlg._stack.currentIndex(), PAGE_WEAPONS)
            self.assertGreater(dlg._list_weapons.count(), 100)

            # Filter to melee only -> all listed weapons must be melee
            dlg._set_class_filter("melee")
            self.assertGreater(dlg._list_weapons.count(), 20)
            first_key = dlg._list_weapons.item(0).data(256)  # Qt.ItemDataRole.UserRole = 256
            self.assertEqual(dlg._weapons_by_key[first_key]["weapon_class"], "melee")

            # Filter back to rifle and open a known rifle ("托里德") into Step 2
            dlg._set_class_filter("rifle")
            self.assertTrue(dlg.select_weapon_and_open_stats("托里德"))
            self.assertEqual(dlg._stack.currentIndex(), PAGE_STATS)

            # Verify only rifle-legal checkboxes are presented (no melee Range, no negative Toxin)
            self.assertIn("多重射击", dlg._pos_checkboxes)
            self.assertNotIn("攻击范围", dlg._pos_checkboxes)
            self.assertIn("变焦", dlg._neg_checkboxes)
            self.assertNotIn("毒素伤害", dlg._neg_checkboxes)

            # Pure-selection checking + Save
            saved_events = []
            dlg.favorites_saved.connect(lambda d: saved_events.append(dict(d)))
            self.assertTrue(dlg.set_stat_checked("暴击几率", True, is_negative=False))
            self.assertTrue(dlg.set_stat_checked("暴击伤害", True, is_negative=False))
            self.assertTrue(dlg.set_stat_checked("多重射击", True, is_negative=False))
            self.assertTrue(dlg.set_stat_checked("变焦", True, is_negative=True))
            dlg._btn_save.click()

            self.assertEqual(len(saved_events), 1)
            self.assertEqual(
                settings.favorite_weapon_stats["托里德"],
                {"positives": ["暴击几率", "暴击伤害", "多重射击"], "negatives": ["变焦"]},
            )
            panel.refresh_favorite_stats_count(len(settings.favorite_weapon_stats))
            self.assertIn("(1)", panel._btn_favorite_stats.text())

            # Return to Step 1 & filter by "favorites" -> shows exactly 1 saved weapon
            dlg._back_to_weapons()
            self.assertEqual(dlg._stack.currentIndex(), PAGE_WEAPONS)
            dlg._set_class_filter("favorites")
            self.assertEqual(dlg._list_weapons.count(), 1)
            self.assertIn("托里德", dlg._list_weapons.item(0).text())

            # Re-open weapon & click Reset -> clears custom override
            self.assertTrue(dlg.select_weapon_and_open_stats("托里德"))
            dlg._btn_remove_weapon.click()
            self.assertNotIn("托里德", settings.favorite_weapon_stats)
        finally:
            dlg.close()
            panel.close()


if __name__ == "__main__":
    unittest.main()
