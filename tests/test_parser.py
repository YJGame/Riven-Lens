"""Unit tests for `src.riven_parser` using realistic Simplified Chinese OCR outputs."""

from __future__ import annotations

import json
import unittest

from src.riven_parser import RivenParser


class TestRivenParser(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RivenParser()

    def test_standard_three_plus_one_minus_riven(self) -> None:
        lines = [
            "18 V",
            "托里德 Crita-acrican",
            "+145.2% 暴击几率",
            "+121.8% 暴击伤害",
            "+92.4% 多重射击",
            "-35.4% 变焦",
            "段位 12  O 27",
        ]
        res = self.parser.parse(lines)
        self.assertEqual(res.weapon_name, "托里德")
        self.assertEqual(res.riven_suffix, "Crita-acrican")
        self.assertEqual(res.riven_name, "托里德 Crita-acrican")
        self.assertEqual(res.drain, 18)
        self.assertEqual(res.mastery_rank, 12)
        self.assertEqual(res.reroll_count, 27)
        self.assertEqual(len(res.positive_stats), 3)
        self.assertEqual(len(res.negative_stats), 1)
        self.assertEqual(res.negative_stats[0].stat_name, "变焦")
        self.assertAlmostEqual(res.negative_stats[0].signed_value, -35.4)

    def test_multiline_title_wrapped_stat_and_faction_multiplier(self) -> None:
        lines = [
            "18",
            "赤毒 布拉玛",
            "Sati-visican",
            "＋ 168。5 ％ 伤害",
            "+85.4% 滑行攻击时有几率",
            "造成暴击",
            "x0.78 对 Infested 伤害",
            "段位 15",
            "O 104",
        ]
        res = self.parser.parse(lines)
        self.assertEqual(res.weapon_name, "赤毒 布拉玛")
        self.assertEqual(res.riven_suffix, "Sati-visican")
        self.assertEqual(res.mastery_rank, 15)
        self.assertEqual(res.reroll_count, 104)
        self.assertEqual(len(res.positive_stats), 2)
        self.assertEqual(res.positive_stats[0].value, 168.5)
        self.assertEqual(res.positive_stats[1].stat_name, "滑行攻击暴击几率")
        self.assertEqual(len(res.negative_stats), 1)
        self.assertEqual(res.negative_stats[0].stat_name, "对 Infested 伤害")
        self.assertEqual(res.negative_stats[0].sign, "x")
        self.assertAlmostEqual(res.negative_stats[0].value, 0.78)

    def test_recoil_polarity_inversion_and_unrolled(self) -> None:
        lines = [
            "伯斯顿 Visi-zeticon",
            "+192.3% 伤害",
            "-81.2% 武器后坐力",
            "+2.4 穿透",
            "段位 10",
        ]
        res = self.parser.parse(lines)
        self.assertEqual(res.reroll_count, 0)
        # -武器后坐力 is beneficial (not negative)
        recoil = [s for s in res.stats if s.stat_name == "武器后坐力"][0]
        self.assertFalse(recoil.is_negative)
        self.assertEqual(recoil.sign, "-")
        # Ensure JSON serialization works cleanly with UTF-8 Chinese
        parsed_json = json.loads(res.to_json())
        self.assertEqual(parsed_json["weapon_name"], "伯斯顿")

    def test_real_ocr_wrapped_title_and_locked_stat(self) -> None:
        lines = [
            "18-",
            "哈尔武 Acri-",
            "critaada",
            "x1.5 对 Infested 的伤害",
            "+174%暴击几率",
            "6 +73.1% 弹药效率",
            "-57.5%射速（弓类武器效",
            "果加倍)",
            "段位10 4",
        ]
        res = self.parser.parse(lines)
        self.assertEqual(res.weapon_name, "哈尔武")
        self.assertEqual(res.riven_suffix, "Acri-critaada")
        self.assertEqual(res.drain, 18)
        self.assertEqual(res.mastery_rank, 10)
        self.assertEqual(res.reroll_count, 4)
        self.assertEqual(len(res.positive_stats), 3)
        self.assertEqual(len(res.negative_stats), 1)
        locked = [s for s in res.stats if s.stat_name == "弹药效率"][0]
        self.assertTrue(locked.is_locked)
        self.assertEqual(res.negative_stats[0].stat_name, "射速")
        self.assertAlmostEqual(res.negative_stats[0].signed_value, -57.5)
        self.assertTrue(res.is_valid)

    def test_elemental_icon_artifact_and_strict_4_combo_validation(self) -> None:
        # Valid 2+1- with elemental snowflake '*' artifact
        valid_res = self.parser.parse([
            "18-",
            "瘟疫克里帕丝",
            "Toxido",
            "+138.7%毒素伤害",
            "+127.2% *冰冻伤害",
            "-58.5%暴击伤害",
            "段位15 1",
        ])
        self.assertTrue(valid_res.is_valid)
        self.assertEqual(len(valid_res.positive_stats), 2)
        self.assertEqual(len(valid_res.negative_stats), 1)
        self.assertEqual(valid_res.positive_stats[1].stat_name, "冰冻伤害")

        # Invalid 1+1- (partial OCR missing a stat) -> must be rejected!
        bad_1_1 = self.parser.parse([
            "瘟疫克里帕丝 Toxido",
            "+138.7%毒素伤害",
            "-58.5%暴击伤害",
            "段位15 1",
        ])
        self.assertFalse(bad_1_1.is_valid)
        self.assertIn("非法词条组合", bad_1_1.validation_error or "")

        # Invalid 2+2- (two negatives) -> must be rejected!
        bad_2_2 = self.parser.parse([
            "托里德 Crita-acrican",
            "+150% 暴击几率",
            "+120% 暴击伤害",
            "-30% 变焦",
            "-40% 射速",
        ])
        self.assertFalse(bad_2_2.is_valid)

        # Invalid missing '%' on percentage stat -> must be rejected!
        bad_no_pct = self.parser.parse([
            "托里德 Critatis",
            "+150 暴击几率",
            "+120% 暴击伤害",
        ])
        self.assertFalse(bad_no_pct.is_valid)
        self.assertIn("缺少百分号", bad_no_pct.validation_error or "")

    def test_wrapped_combo_chance_stat(self) -> None:
        res = self.parser.parse([
            "18—",
            "瘟疫克里帕丝",
            "Manti-ignius",
            "x1.55 对 Corpus 的伤害",
            "+81%重击效率",
            "+103.2% 火焰伤害",
            "-102.7%的几率来获得连",
            "击数",
            "段位15 6",
        ])
        self.assertTrue(res.is_valid)
        self.assertEqual(len(res.positive_stats), 3)
        self.assertEqual(len(res.negative_stats), 1)
        self.assertEqual(res.negative_stats[0].stat_name, "额外连击几率")
        self.assertAlmostEqual(res.negative_stats[0].signed_value, -102.7)


if __name__ == "__main__":
    unittest.main()
