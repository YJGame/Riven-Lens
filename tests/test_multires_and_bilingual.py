"""Unit tests for multi-resolution/ultrawide ROI scaling and ZH/EN bilingual parsing & UI."""

from __future__ import annotations

import unittest

from src.capture import HeightAnchoredROI, compute_preset_region
from src.evaluator import RivenEvaluator
from src.i18n import I18N
from src.riven_parser import RivenParser


class TestMultiResAndUltrawideROI(unittest.TestCase):
    RESOLUTIONS = [
        ("1366x768 (13\" Laptop)", 1366, 768),
        ("1920x1080 (1080p)", 1920, 1080),
        ("2560x1440 (2K QHD)", 2560, 1440),
        ("3440x1440 (21:9 Ultrawide)", 3440, 1440),
        ("5120x1440 (32:9 Super-Ultrawide)", 5120, 1440),
        ("3840x2160 (4K UHD)", 3840, 2160),
        ("1920x1200 (16:10)", 1920, 1200),
    ]

    def test_all_resolutions_centered_within_bounds_and_non_overlapping(self) -> None:
        for label, w, h in self.RESOLUTIONS:
            with self.subTest(resolution=label):
                center = compute_preset_region("reroll_center", w, h, auto_scale=True)
                left = compute_preset_region("reroll_left", w, h, auto_scale=True)
                right = compute_preset_region("reroll_right", w, h, auto_scale=True)
                chat = compute_preset_region("chat_link", w, h, auto_scale=True)
                assert center and left and right and chat

                # 1. All boxes strictly within screen bounds
                for reg in (center, left, right, chat):
                    self.assertGreaterEqual(reg.left, 0)
                    self.assertGreaterEqual(reg.top, 0)
                    self.assertLessEqual(reg.left + reg.width, w)
                    self.assertLessEqual(reg.top + reg.height, h)

                # 2. Active center/right card spans horizontal center cx = W/2
                cx = w / 2.0
                self.assertLess(center.left, cx)
                self.assertGreater(center.left + center.width, cx)

                # 3. Chat link card is strictly centered horizontally around cx
                chat_mid_x = chat.left + chat.width / 2.0
                self.assertAlmostEqual(chat_mid_x, cx, delta=2.0)

                # 4. Left card and right card NEVER overlap and keep clean gap
                self.assertLess(left.left + left.width + 15, right.left)

    def test_ultrawide_card_dimensions_stay_height_locked_not_stretched(self) -> None:
        # 2560x1440 (16:9), 3440x1440 (21:9), and 5120x1440 (32:9) share H=1440 -> identical card size!
        r_16_9 = compute_preset_region("reroll_center", 2560, 1440)
        r_21_9 = compute_preset_region("reroll_center", 3440, 1440)
        r_32_9 = compute_preset_region("reroll_center", 5120, 1440)
        assert r_16_9 and r_21_9 and r_32_9

        self.assertEqual(r_16_9.width, r_21_9.width)
        self.assertEqual(r_16_9.width, r_32_9.width)
        self.assertEqual(r_16_9.height, r_21_9.height)
        self.assertEqual(r_16_9.height, r_32_9.height)
        self.assertEqual(r_16_9.top, r_21_9.top)
        self.assertEqual(r_16_9.top, r_32_9.top)

        # Horizontal center shifts exactly with W/2
        self.assertEqual(r_21_9.left - r_16_9.left, (3440 - 2560) // 2)
        self.assertEqual(r_32_9.left - r_16_9.left, (5120 - 2560) // 2)

        # Effective height on 16:10 stays safely bounded by W * 9/16
        self.assertAlmostEqual(HeightAnchoredROI.effective_height(1920, 1200), 1080.0, places=1)


class TestBilingualParsingAndLocalization(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_english_riven_card_parsing_and_unified_evaluation(self) -> None:
        en_lines = [
            "18",
            "Torid Crita-acrican",
            "+180.5% Critical Chance",
            "+142.0% Critical Damage",
            "+104.2% Multishot",
            "-52.4% Zoom",
            "MR 14  Rerolls 28",
        ]
        zh_lines = [
            "18",
            "托里德 Crita-acrican",
            "+180.5% 暴击几率",
            "+142.0% 暴击伤害",
            "+104.2% 多重射击",
            "-52.4% 变焦",
            "段位 14  O 28",
        ]
        r_en = self.parser.parse(en_lines)
        r_zh = self.parser.parse(zh_lines)

        self.assertTrue(r_en.is_valid)
        self.assertTrue(r_zh.is_valid)
        self.assertEqual(r_en.detected_language, "en_US")
        self.assertEqual(r_zh.detected_language, "zh_CN")
        self.assertEqual(r_en.mastery_rank, 14)
        self.assertEqual(r_en.reroll_count, 28)

        # Unified internal stat_key matches 1:1 between English and Chinese!
        self.assertEqual([s.stat_key for s in r_en.stats], [s.stat_key for s in r_zh.stats])
        self.assertEqual(
            [s.stat_key for s in r_en.stats],
            ["critical_chance", "critical_damage", "multishot", "zoom"],
        )

        # Both feed into the exact same evaluation math and produce identical grades & roll %!
        ev_en = self.evaluator.evaluate(r_en)
        ev_zh = self.evaluator.evaluate(r_zh)
        self.assertEqual(ev_en.weapon_slug, "torid")
        self.assertEqual(ev_en.weapon_name_en, "Torid")
        self.assertEqual(ev_en.overall_grade, ev_zh.overall_grade)
        self.assertAlmostEqual(ev_en.overall_roll_pct, ev_zh.overall_roll_pct, places=1)

    def test_english_melee_wrapped_stats_and_i18n_bundles(self) -> None:
        en_melee = [
            "18-",
            "Glaive Critatis",
            "+145.9% Crit Damage",
            "+292.3% Critical Chance (x2",
            "for Heavy Attacks)",
            "x0.74 Damage to Corpus",
            "Mastery 16  2",
        ]
        r = self.parser.parse(en_melee)
        self.assertTrue(r.is_valid)
        self.assertEqual(r.detected_language, "en_US")
        self.assertEqual(r.mastery_rank, 16)
        self.assertEqual(r.reroll_count, 2)
        ev = self.evaluator.evaluate(r)
        self.assertEqual(ev.weapon_class, "melee")
        self.assertEqual(ev.weapon_slug, "glaive")
        self.assertEqual(ev.overall_grade, "S")

        # Verify i18n bundles translate key stats, tags, and tray strings cleanly
        self.assertEqual(I18N.stat_name("暴击几率", "en_US"), "Critical Chance")
        self.assertEqual(I18N.stat_name("对 Corpus 伤害", "en_US"), "Damage to Corpus")
        self.assertEqual(I18N.tag_name("双暴", "en_US"), "CC+CD")
        self.assertEqual(I18N.weapon_class("melee", "en_US"), "Melee")
        self.assertEqual(I18N.t("tray.lang_en", "en_US"), "English")


if __name__ == "__main__":
    unittest.main()
