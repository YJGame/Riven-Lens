"""Unit tests for Warframe Hybrid Dual-Attribute Riven stats, custom_riven_stats.json, & WM slug safety.

Verifies:
  1. `RivenParser` splits CN/EN hybrid dual-stat lines (`与`, `/`, `and`, wrapped lines)
     into 2 independent `RivenStat(..., is_hybrid=True)` sub-stats while preserving
     valid slot-combo validation.
  2. Local supplemental `data/custom_riven_stats.json` merges with priority (and is
     never overwritten by DB sync), providing aliases, base values (at unified 1.20
     disposition), and graceful missing-range fallback (`新融合词条（暂缺官方基础值区间）`)
     with directional grading instead of blank/`?`.
  3. `wm_client` validates query stats against WM official `riven/attributes` slugs,
     automatically skips unindexed new stats so F9 never returns HTTP 400 / crashes,
     and surfaces `已自动忽略 WM 暂未收录的新词条` in both overlay and control panel.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.evaluator import HYBRID_MISSING_RANGE_NOTICE_ZH, RivenEvaluator
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
from src.riven_parser import RivenParser, get_custom_stats_path
from src.wm_client import (
    build_wm_search_url,
    fetch_wm_exact_riven_matches,
    load_valid_wm_attribute_slugs,
    stat_name_to_wm_slug,
)


def _fake_auction(aid: str, price: int, pos: list[str], neg: list[str], rerolls: int = 3) -> dict:
    attrs = [{"url_name": s, "positive": True, "value": 100.0} for s in pos] + [
        {"url_name": s, "positive": False, "value": -45.0} for s in neg
    ]
    return {
        "id": aid,
        "closed": False,
        "visible": True,
        "buyout_price": price,
        "owner": {"status": "ingame", "ingame_name": f"Tenno_{aid}"},
        "item": {"re_rolls": rerolls, "attributes": attrs},
    }


class TestHybridRivenStats(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_custom_riven_stats_file_exists_and_loads(self) -> None:
        self.assertTrue(get_custom_stats_path().is_file())
        self.assertIn("弱点伤害", self.evaluator.custom_stats_meta)
        self.assertIn("融合增幅", self.evaluator.custom_stats_meta)
        self.assertIn("暴击几率+暴击伤害", self.evaluator.hybrid_pairs_meta)

    def test_parse_cn_and_en_hybrid_dual_stat_lines(self) -> None:
        # 1. Chinese "与" hybrid + standard stats + negative (3+1- slots -> 5 sub-stats)
        r_cn = self.parser.parse([
            "托里德 Crita-acrican",
            "+95% 暴击几率 与 +75% 暴击伤害",
            "+112.4% 多重射击",
            "+165.0% 伤害",
            "-52.0% 变焦",
            "段位 16 O 8",
        ])
        self.assertTrue(r_cn.is_valid, msg=r_cn.validation_error)
        self.assertEqual(len(r_cn.stats), 5)
        hybrid_subs = [s for s in r_cn.stats if s.is_hybrid]
        self.assertEqual(len(hybrid_subs), 2)
        self.assertEqual([s.stat_name for s in hybrid_subs], ["暴击几率", "暴击伤害"])
        self.assertEqual([s.value for s in hybrid_subs], [95.0, 75.0])
        self.assertEqual(hybrid_subs[0].hybrid_group_id, hybrid_subs[1].hybrid_group_id)

        ev_cn = self.evaluator.evaluate(r_cn)
        self.assertAlmostEqual(ev_cn.disposition, 1.20)
        self.assertIn("融合双暴", ev_cn.tags)
        self.assertIn(ev_cn.overall_grade, ("S", "A"))
        for se in ev_cn.stat_evaluations:
            self.assertIsNotNone(se.roll_pct)
            self.assertTrue(bool(se.range_display))

        # 2. Slash "/" hybrid connector
        r_slash = self.parser.parse([
            "托里德 Visi-satiata",
            "+80% 伤害 / +60% 多重射击",
            "+145.0% 暴击几率",
            "-48.0% 冲击伤害",
            "段位 14 O 3",
        ])
        self.assertTrue(r_slash.is_valid, msg=r_slash.validation_error)
        self.assertEqual(len([s for s in r_slash.stats if s.is_hybrid]), 2)
        ev_slash = self.evaluator.evaluate(r_slash)
        self.assertIn("融合基伤多重", ev_slash.tags)

        # 3. English "and" hybrid connector
        r_en = self.parser.parse([
            "Torid Crita-acrican",
            "+90% Critical Chance and +70% Critical Damage",
            "+108.0% Multishot",
            "-51.0% Zoom",
            "Mastery 16 10",
        ])
        self.assertTrue(r_en.is_valid, msg=r_en.validation_error)
        en_hybrids = [s for s in r_en.stats if s.is_hybrid]
        self.assertEqual([s.stat_name for s in en_hybrids], ["暴击几率", "暴击伤害"])
        self.assertEqual([s.value for s in en_hybrids], [90.0, 70.0])

        # 4. Wrapped hybrid across two OCR lines
        r_wrap = self.parser.parse([
            "托里德 Crita-acrican",
            "+95% 暴击几率 与",
            "+75% 暴击伤害",
            "+110.0% 多重射击",
            "段位 12 O 4",
        ])
        self.assertTrue(r_wrap.is_valid, msg=r_wrap.validation_error)
        self.assertEqual(len([s for s in r_wrap.stats if s.is_hybrid]), 2)

    def test_new_unrecorded_hybrid_stat_graceful_fallback_and_directional_grade(self) -> None:
        """Brand-new custom/hybrid stat without official range shows friendly notice & directional grade."""
        r_new = self.parser.parse([
            "托里德 Crita-novis",
            "+95% 暴击几率 与 +45% 融合增幅",
            "+115.0% 多重射击",
            "-50.0% 变焦",
            "段位 16 O 5",
        ])
        self.assertTrue(r_new.is_valid, msg=r_new.validation_error)
        ev = self.evaluator.evaluate(r_new)
        self.assertIn(ev.overall_grade, ("S", "A", "B"))
        by_name = {se.stat_name: se for se in ev.stat_evaluations}
        self.assertIn("融合增幅", by_name)
        fusion_se = by_name["融合增幅"]
        self.assertTrue(fusion_se.is_hybrid)
        self.assertTrue(fusion_se.missing_base_range)
        self.assertEqual(fusion_se.range_display, HYBRID_MISSING_RANGE_NOTICE_ZH)
        self.assertIn(fusion_se.grade, ("S", "A", "B"))

        # Also test completely brand-new stat NOT even listed in custom_riven_stats.json yet
        r_brand_new = self.parser.parse([
            "托里德 Crita-voida",
            "+92% 暴击几率 与 +38% 虚空共鸣伤害",
            "+110.0% 多重射击",
            "-48.0% 变焦",
            "段位 15 O 2",
        ])
        self.assertTrue(r_brand_new.is_valid, msg=r_brand_new.validation_error)
        ev_bn = self.evaluator.evaluate(r_brand_new)
        bn_map = {se.stat_name: se for se in ev_bn.stat_evaluations}
        self.assertIn("虚空共鸣伤害", bn_map)
        self.assertTrue(bn_map["虚空共鸣伤害"].missing_base_range)
        self.assertEqual(bn_map["虚空共鸣伤害"].range_display, HYBRID_MISSING_RANGE_NOTICE_ZH)

    def test_wm_f9_skips_unindexed_new_stats_without_400_and_notifies_overlay(self) -> None:
        """F9 WM lookup validates against official WM slugs, skips unknown new stats, and renders notice."""
        official = load_valid_wm_attribute_slugs()
        self.assertIn("critical_chance", official)
        self.assertIn("multishot", official)
        self.assertIsNone(stat_name_to_wm_slug("融合增幅"))
        self.assertIsNone(stat_name_to_wm_slug("虚空共鸣伤害"))

        # URL builder must also exclude unindexed stats cleanly
        url = build_wm_search_url(
            "torid",
            positive_stats=["暴击几率", "融合增幅", "多重射击"],
            negative_stat="变焦",
        )
        self.assertIn("critical_chance", url)
        self.assertIn("multishot", url)
        self.assertNotIn("融合增幅", url)

        captured_calls = []

        def fake_fetcher(weapon_slug, sort_by="price_asc", positive_slugs=(), negative_slug=None, **_kw):
            captured_calls.append((list(positive_slugs), negative_slug))
            # Simulate WM rejecting any unverified slug with HTTP 400
            for s in list(positive_slugs) + ([negative_slug] if negative_slug else []):
                if s not in official:
                    raise AssertionError(f"Unverified WM slug sent to API: {s}")
            return [
                _fake_auction("a1", 950, ["critical_chance", "multishot"], ["zoom"], rerolls=5),
                _fake_auction("a2", 1100, ["critical_chance", "multishot"], ["zoom"], rerolls=12),
            ]

        report = fetch_wm_exact_riven_matches(
            weapon_name="托里德",
            weapon_slug="torid",
            weapon_class="rifle",
            positive_stats=["暴击几率", "融合增幅", "多重射击"],
            negative_stat="变焦",
            recommended_positives=["暴击几率", "暴击伤害", "多重射击"],
            recommended_negatives=["变焦"],
            fetcher=fake_fetcher,
        )

        self.assertGreaterEqual(len(captured_calls), 1)
        self.assertEqual(report.ignored_unknown_stats, ["融合增幅"])
        self.assertEqual(report.min_price, 950)
        self.assertEqual(len(report.top_listings), 2)

        # Verify overlay renders both the hybrid card notice AND the WM skipped-stat notice cleanly
        r_new = self.parser.parse([
            "托里德 Crita-novis",
            "+95% 暴击几率 与 +45% 融合增幅",
            "+115.0% 多重射击",
            "-50.0% 变焦",
            "段位 16 O 5",
        ])
        ev = self.evaluator.evaluate(r_new)
        card = EvaluatedRivenCard(label="当前紫卡", riven=r_new, evaluation=ev, avg_confidence=0.97)
        win = RivenOverlayWindow(click_through=True)
        try:
            win.update_cards([card], elapsed_ms=95.0, layout_label="单卡识别")
            win.show_wm_report(report, weapon_display="托里德")
            self.assertTrue(win._wm_hint.isVisible())
            self.assertIn("已自动忽略 WM 暂未收录的新词条", win._wm_hint.text())
            self.assertIn("融合增幅", win._wm_hint.text())
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
