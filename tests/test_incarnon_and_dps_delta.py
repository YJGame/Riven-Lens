"""Unit tests for expanded Incarnon weapon archetypes & Steel Path practical DPS delta comparison."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.evaluator import (
    RivenEvaluator,
    compare_riven_evaluations,
    estimate_riven_dps_multiplier,
)
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow, _localize_comparison
from src.riven_parser import RivenParser


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestIncarnonRulesAndDPSDelta(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_expanded_incarnon_special_rules_coverage_and_archetypes(self) -> None:
        # Must cover >= 40 Incarnon weapons
        self.assertGreaterEqual(len(self.evaluator.special_rules), 40)

        # Furis (盗贼) has its own dedicated Heat Beam rule and no longer collides with Lex (雷克斯)
        r_furis = self.parser.parse(
            [
                "盗贼 Sati-igniata",
                "+115.0% 多重射击",
                "+95.0% 火焰伤害",
                "+92.0% 异常状态伤害",
                "-60.0% 变焦",
                "段位 12 O 8",
            ]
        )
        ev_furis = self.evaluator.evaluate(r_furis)
        self.assertEqual(ev_furis.weapon_name, "盗贼")
        self.assertEqual(ev_furis.matched_archetype_id, "incarnon_heat_beam")
        self.assertIn("火焰射线", ev_furis.matched_archetype_name)

        # Dera (德拉) Incarnon Status-to-Crit archetype
        r_dera = self.parser.parse(
            [
                "德拉 Hexa-acrican",
                "+100.0% 触发几率",
                "+130.0% 暴击伤害",
                "+100.0% 多重射击",
                "-55.0% 变焦",
                "段位 14 O 4",
            ]
        )
        ev_dera = self.evaluator.evaluate(r_dera)
        self.assertEqual(ev_dera.matched_archetype_id, "incarnon_status_to_crit")
        self.assertIn("触发转暴击", ev_dera.matched_archetype_name)

        # Dual Ichor (恶脓双斧) Melee Influence archetype with Electricity + Range + Attack Speed
        r_ichor = self.parser.parse(
            [
                "恶脓双斧 Croni-loctitron",
                "+95.0% 电击伤害",
                "+60.0% 攻击速度",
                "+2.1 攻击范围",
                "-90.0% 处决伤害",
                "段位 15 O 12",
            ]
        )
        ev_ichor = self.evaluator.evaluate(r_ichor)
        self.assertEqual(ev_ichor.matched_archetype_id, "melee_influence")
        self.assertIn("侵染", ev_ichor.matched_archetype_name)

        # Kunai (苦无) Auto-Headshot Weak Point archetype
        r_kunai = self.parser.parse(
            [
                "苦无 Sati-acrican",
                "+100.0% 暴击伤害",
                "+125.0% 多重射击",
                "+235.0% 弱点伤害",
                "-75.0% 变焦",
                "段位 13 O 6",
            ]
        )
        ev_kunai = self.evaluator.evaluate(r_kunai)
        self.assertEqual(ev_kunai.matched_archetype_id, "incarnon_headshot")

        # Sibear (西伯利亚冰锤) Heavy Slam archetype
        r_sibear = self.parser.parse(
            [
                "西伯利亚冰锤 Acri-toriata",
                "+100.0% 暴击伤害",
                "+190.0% 暴击几率",
                "+130.0% 震地攻击伤害",
                "-95.0% 处决伤害",
                "段位 14 O 3",
            ]
        )
        ev_sibear = self.evaluator.evaluate(r_sibear)
        self.assertEqual(ev_sibear.matched_archetype_id, "heavy_slam")

    def test_steel_path_dps_delta_reflects_dilution_and_zariman_non_crit(self) -> None:
        # On Torid under Steel Path baseline (Merciless +360% DMG), CD+MS+FR beats pure DMG+IPS!
        left_dmg_roll = self.parser.parse(
            [
                "托里德 Visi-concidra",
                "+180.0% 伤害",
                "+130.0% 冲击伤害",
                "+55.0% 弹匣容量",
                "-50.0% 变焦",
                "段位 14 O 10",
            ]
        )
        right_god_roll = self.parser.parse(
            [
                "托里德 Crita-acrican",
                "+165.0% 暴击几率",
                "+135.0% 暴击伤害",
                "+100.0% 多重射击",
                "-55.0% 变焦",
                "段位 14 O 11",
            ]
        )
        ev_left = self.evaluator.evaluate(left_dmg_roll)
        ev_right = self.evaluator.evaluate(right_god_roll)

        dps_l = estimate_riven_dps_multiplier(ev_left)
        dps_r = estimate_riven_dps_multiplier(ev_right)
        self.assertIsNotNone(dps_l)
        self.assertIsNotNone(dps_r)
        self.assertGreater(dps_r, dps_l * 1.25)

        comp = compare_riven_evaluations(ev_left, ev_right)
        self.assertEqual(comp.winner, "right")
        self.assertIsNotNone(comp.dps_delta_pct)
        self.assertGreater(comp.dps_delta_pct, 25.0)
        self.assertIn("实战DPS +", comp.detail)

        # Inline localization in overall rating line (Chinese & English, zero extra UI elements)
        _hl_zh, det_zh = _localize_comparison(comp, ev_left, ev_right, "zh_CN")
        _hl_en, det_en = _localize_comparison(comp, ev_left, ev_right, "en_US")
        self.assertIn("实战DPS +", det_zh)
        self.assertIn("Est. DPS +", det_en)

        # Render in RivenOverlayWindow and verify DPS delta appears right inside `_compare_detail`
        win = RivenOverlayWindow(click_through=True, lang="zh_CN")
        try:
            cards = [
                EvaluatedRivenCard(label="[当前]", riven=left_dmg_roll, evaluation=ev_left, avg_confidence=0.98),
                EvaluatedRivenCard(label="[新循环]", riven=right_god_roll, evaluation=ev_right, avg_confidence=0.98),
            ]
            win.update_cards(cards, elapsed_ms=42.0, layout_label="洗卡双卡对比", comparison=comp)
            self.assertIn("实战DPS +", win._compare_detail.text())
        finally:
            win.close()

        # Zariman Non-Crit Devouring: -Crit Chance MUST produce higher DPS than +Zoom negative!
        laetum_no_neg_cc = self.parser.parse(
            [
                "奏凯 Sati-croniata",
                "+115.0% 多重射击",
                "+75.0% 射速",
                "+210.0% 伤害",
                "-70.0% 变焦",
                "段位 14 O 20",
            ]
        )
        laetum_neg_cc = self.parser.parse(
            [
                "奏凯 Sati-croniata",
                "+115.0% 多重射击",
                "+75.0% 射速",
                "+210.0% 伤害",
                "-115.0% 暴击几率",
                "段位 14 O 21",
            ]
        )
        ev_l_plain = self.evaluator.evaluate(laetum_no_neg_cc)
        ev_l_god = self.evaluator.evaluate(laetum_neg_cc)
        self.assertGreater(
            estimate_riven_dps_multiplier(ev_l_god),
            estimate_riven_dps_multiplier(ev_l_plain),
        )


if __name__ == "__main__":
    unittest.main()
