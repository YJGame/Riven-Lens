"""Unit tests for `src.evaluator` Riven roll calculation & grading."""

from __future__ import annotations

import unittest

from src.evaluator import (
    RivenEvaluator,
    build_result_fingerprint,
    compare_riven_evaluations,
    fingerprints_equivalent,
)
from src.riven_parser import RivenParser


class TestRivenEvaluator(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_harpak_real_roll_evaluation(self) -> None:
        lines = [
            "18-",
            "哈尔武 Acri-",
            "critaada",
            "x1.5 对 Infested 的伤害",
            "+174%暴击几率",
            "+144.5% 暴击伤害",
            "-57.5%射速（弓类武器效",
            "果加倍)",
            "段位10 4",
        ]
        riven = self.parser.parse(lines)
        ev = self.evaluator.evaluate(riven)

        self.assertEqual(ev.weapon_name, "哈尔武")
        self.assertEqual(ev.weapon_class, "rifle")
        self.assertAlmostEqual(ev.disposition, 1.20)
        self.assertEqual(ev.combo_pattern, "3+1-")
        self.assertIn("双暴", ev.tags)
        self.assertIn("伤筋负", ev.tags)
        self.assertEqual(len(ev.stat_evaluations), 4)
        for se in ev.stat_evaluations:
            self.assertIsNotNone(se.roll_pct)
            self.assertIn(se.grade, ("S", "A", "B", "C"))
            self.assertGreaterEqual(se.roll_pct, 0.0)
            self.assertLessEqual(se.roll_pct, 100.0)

    def test_god_roll_torid_gets_s_grade_and_tags(self) -> None:
        lines = [
            "托里德 Crita-acrican",
            "+198.0% 暴击几率",
            "+158.0% 暴击伤害",
            "+118.0% 多重射击",
            "-54.0% 变焦",
            "段位 12 O 42",
        ]
        riven = self.parser.parse(lines)
        ev = self.evaluator.evaluate(riven)

        self.assertEqual(ev.weapon_name, "托里德")
        self.assertEqual(ev.overall_grade, "S")
        self.assertIn("双暴多重", ev.tags)
        self.assertIn("无害负", ev.tags)

    def test_ocr_alias_correction_glaxion(self) -> None:
        lines = [
            "18",
            "冰松Visi-plagacan",
            "+97.2%多重射击",
            "+201.9% 伤害",
            "θ +109.9%异常状态伤害",
            "x0.63 对 Corpus 的伤害",
            "段位16 64",
        ]
        riven = self.parser.parse(lines)
        ev = self.evaluator.evaluate(riven)

        self.assertEqual(ev.weapon_name, "冰凇")
        self.assertAlmostEqual(ev.disposition, 1.20)
        self.assertIn("基伤多重", ev.tags)
        self.assertIn(ev.overall_grade, ("S", "A", "B", "C"))

    def test_unknown_weapon_uses_unified_1_20_disposition(self) -> None:
        lines = [
            "未知新武器 Crita-acrican",
            "+180.0% 暴击几率",
            "+140.0% 暴击伤害",
            "+105.0% 多重射击",
            "-50.0% 变焦",
            "段位 14 O 5",
        ]
        riven = self.parser.parse(lines)
        ev = self.evaluator.evaluate(riven)
        self.assertAlmostEqual(ev.disposition, 1.20)
        self.assertEqual(ev.disposition_source, "fixed_1.2")

    def test_fingerprint_dedup_tolerates_tiny_ocr_jitter_and_detects_reroll(self) -> None:
        r1 = self.parser.parse([
            "哈尔武 Acri-critaada",
            "+174.0% 暴击几率",
            "+144.5% 暴击伤害",
            "-57.5% 射速",
            "段位 10 4",
        ])
        r1_jitter = self.parser.parse([
            "哈尔武 Acri-critaada",
            "+174.1% 暴击几率",
            "+144.5% 暴击伤害",
            "-57.5% 射速",
            "段位 10",  # reroll dropped transiently -> still equivalent
        ])
        r2_rerolled = self.parser.parse([
            "哈尔武 Sati-acrican",
            "+180.0% 暴击几率",
            "+142.0% 暴击伤害",
            "+105.0% 多重射击",
            "-50.0% 变焦",
            "段位 10 5",
        ])
        fp1 = build_result_fingerprint("single", [(r1, self.evaluator.evaluate(r1))])
        fp1_j = build_result_fingerprint("single", [(r1_jitter, self.evaluator.evaluate(r1_jitter))])
        fp2 = build_result_fingerprint("single", [(r2_rerolled, self.evaluator.evaluate(r2_rerolled))])

        self.assertTrue(fingerprints_equivalent(fp1, fp1_j))
        self.assertFalse(fingerprints_equivalent(fp1, fp2))

    def test_dual_comparison_highlights_stronger_side(self) -> None:
        left_r = self.parser.parse([
            "哈尔武 Acri-critaada",
            "x1.5 对 Infested 的伤害",
            "+174% 暴击几率",
            "+144.5% 暴击伤害",
            "-57.5% 射速",
            "段位 10 4",
        ])
        right_r = self.parser.parse([
            "哈尔武 Crita-acrican",
            "+182.0% 暴击几率",
            "+146.0% 暴击伤害",
            "+108.0% 多重射击",
            "-50.0% 变焦",
            "段位 10 5",
        ])
        comp = compare_riven_evaluations(
            self.evaluator.evaluate(left_r),
            self.evaluator.evaluate(right_r),
        )
        self.assertEqual(comp.winner, "right")
        self.assertIn("[新循环]", comp.headline)

    def test_finisher_and_slide_crit_from_synced_cache(self) -> None:
        r = self.parser.parse([
            "18-",
            "瘟疫克里帕丝",
            "Plecicta",
            "+151.2% 滑行攻击暴击几",
            "率",
            "+147.2% 处决伤害",
            "段位 15 14",
        ])
        self.assertTrue(r.is_valid)
        ev = self.evaluator.evaluate(r)
        self.assertEqual(ev.weapon_name, "瘟疫 克里帕丝")
        self.assertEqual(ev.weapon_class, "melee")
        self.assertAlmostEqual(ev.disposition, 1.20)
        by_name = {se.stat_name: se for se in ev.stat_evaluations}
        self.assertIn("滑行攻击暴击几率", by_name)
        self.assertIn("处决伤害", by_name)
        # Official base 119.7 * 0.99 * 1.20 = 142.2036 -> [128.0, 156.4] (处决伤害)
        # Official base 120.0 * 0.99 * 1.20 = 142.56   -> [128.3, 156.8] (滑行攻击暴击几率)
        self.assertAlmostEqual(by_name["处决伤害"].min_value, 128.0, places=1)
        self.assertAlmostEqual(by_name["处决伤害"].max_value, 156.4, places=1)
        self.assertAlmostEqual(by_name["滑行攻击暴击几率"].min_value, 128.3, places=1)
        self.assertAlmostEqual(by_name["滑行攻击暴击几率"].max_value, 156.8, places=1)

    def test_akarius_screenshot_and_authoritative_1_20_table_ranges(self) -> None:
        """Verify user's exact Akarius (阿利乌双枪) screenshot card & authoritative 1.20 ranges."""
        r = self.parser.parse([
            "阿利乌双枪 Visi-satipla",
            "+267% 伤害",
            "+145.3% 多重射击",
            "+47.5% 弹药效率",
            "x0.58 对 Infested 的伤害",
            "段位 12  ↻ 66",
        ])
        self.assertTrue(r.is_valid, msg=r.validation_error)
        self.assertEqual(r.reroll_count, 66)
        ev = self.evaluator.evaluate(r, weapon_class_override="pistol")
        by_name = {se.stat_name: se for se in ev.stat_evaluations}

        self.assertAlmostEqual(by_name["伤害"].min_value, 222.3, places=1)
        self.assertAlmostEqual(by_name["伤害"].max_value, 271.8, places=1)
        self.assertAlmostEqual(by_name["多重射击"].min_value, 121.2, places=1)
        self.assertAlmostEqual(by_name["多重射击"].max_value, 148.1, places=1)
        # Ammo Efficiency: exact 3+1 range at 1.20 dispo is +45.6% ~ +55.7%
        self.assertAlmostEqual(by_name["弹药效率"].min_value, 45.6, places=1)
        self.assertAlmostEqual(by_name["弹药效率"].max_value, 55.7, places=1)
        self.assertGreater(by_name["弹药效率"].roll_pct, 15.0)
        self.assertLess(by_name["弹药效率"].roll_pct, 25.0)

        # Verify authoritative fused/welded (熔接) & melee stats at 3+1, Dispo 1.20
        r_welded = self.parser.parse([
            "托里德 Visi-crita",
            "+255.0% 弱点伤害",
            "+280.0% 弱点暴击几率",
            "+100.0% 收起武器时弹匣每秒自动装填",
            "-52.0% 变焦",
            "段位 14 O 5",
        ])
        ev_w = self.evaluator.evaluate(r_welded, weapon_class_override="rifle")
        wm = {se.stat_name: se for se in ev_w.stat_evaluations}
        self.assertAlmostEqual(wm["弱点伤害"].min_value, 227.8, places=1)
        self.assertAlmostEqual(wm["弱点伤害"].max_value, 278.4, places=1)
        self.assertAlmostEqual(wm["弱点暴击几率"].min_value, 250.6, places=1)
        self.assertAlmostEqual(wm["弱点暴击几率"].max_value, 306.3, places=1)
        self.assertAlmostEqual(wm["收起武器时弹匣每秒自动装填"].min_value, 91.1, places=1)
        self.assertAlmostEqual(wm["收起武器时弹匣每秒自动装填"].max_value, 111.4, places=1)

        r_melee = self.parser.parse([
            "陶瓷匕首 Croni-loctinok",
            "+28.0 初始连击",
            "+66.0% 额外连击几率",
            "+135.0% 重击准备速度",
            "-130.0% 冲击伤害",
            "段位 12 O 4",
        ])
        ev_m = self.evaluator.evaluate(r_melee, weapon_class_override="melee")
        mm = {se.stat_name: se for se in ev_m.stat_evaluations}
        self.assertAlmostEqual(mm["初始连击"].min_value, 24.8, places=1)
        self.assertAlmostEqual(mm["初始连击"].max_value, 30.3, places=1)
        self.assertAlmostEqual(mm["额外连击几率"].min_value, 59.5, places=1)
        self.assertAlmostEqual(mm["额外连击几率"].max_value, 72.7, places=1)
        self.assertAlmostEqual(mm["重击准备速度"].min_value, 121.2, places=1)
        self.assertAlmostEqual(mm["重击准备速度"].max_value, 148.1, places=1)


if __name__ == "__main__":
    unittest.main()
