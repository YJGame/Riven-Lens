"""Unit tests for local recommended tiers, WFCD profile inference, and on-demand WM fetcher."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.evaluator import RivenEvaluator
from src.riven_parser import RivenParser
from src.wm_fetcher import (
    WMFetchError,
    analyze_auctions_payload,
    save_wm_recommendation_to_local,
)


class TestWMAndRecommendations(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_f8_local_evaluation_makes_zero_network_calls(self) -> None:
        r = self.parser.parse([
            "18-",
            "战刃 Critatis",
            "+145.9% 暴击伤害",
            "+292.3% 暴击几率（重击时 x2）",
            "x0.74 对 Corpus 的伤害",
            "段位 16 2",
        ])
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("Network must NOT be called during F8")):
            ev = self.evaluator.evaluate(r)

        self.assertEqual(ev.weapon_name, "战刃")
        self.assertIn(ev.recommendation_source, ("builtin", "wm"))
        self.assertIn(ev.recommendation_source_cn, ("内置推荐", "WM 校准"))
        self.assertIn("暴击伤害", ev.recommended_positives)
        self.assertIn("暴击几率", ev.recommended_positives)
        self.assertGreaterEqual(ev.matched_rec_pos, 2)
        by_name = {se.stat_name: se for se in ev.stat_evaluations}
        self.assertTrue(by_name["暴击伤害"].is_recommended)
        self.assertTrue(by_name["暴击几率"].is_recommended)

    def test_unlisted_weapon_infers_recommendations_from_wfcd_stats(self) -> None:
        # Pick a weapon present in WFCD weapons_riven.json but NOT in builtin recommended_tiers.json
        unlisted = [
            name
            for name, info in self.evaluator.weapons.items()
            if name not in self.evaluator.recommended_tiers and info.get("crit_chance") is not None
        ]
        self.assertTrue(len(unlisted) > 0)
        sample_name = unlisted[0]
        w_cls = self.evaluator.weapons[sample_name]["class"]
        pos, neg, src, _ = self.evaluator.resolve_recommendations(sample_name, w_cls)
        self.assertEqual(src, "inferred")
        self.assertGreaterEqual(len(pos), 3)
        self.assertGreaterEqual(len(neg), 1)

    def test_wm_slug_resolution_strips_prime_variants(self) -> None:
        self.assertEqual(self.evaluator.resolve_wm_slug("战刃 Prime"), "glaive")
        self.assertEqual(self.evaluator.resolve_wm_slug("伯斯顿 Prime"), "burston")
        self.assertEqual(self.evaluator.resolve_wm_slug("赤毒·布拉玛"), "kuva_bramma")
        self.assertEqual(self.evaluator.resolve_wm_slug("瘟疫 克里帕丝"), "plague_kripath")

    def test_analyze_auctions_payload_filters_outliers_and_counts_stats(self) -> None:
        desc_auctions = [
            {
                "id": "a1",
                "closed": False,
                "visible": True,
                "buyout_price": 99999,  # troll price -> filtered from price stats
                "owner": {"status": "ingame"},
                "item": {
                    "attributes": [
                        {"url_name": "critical_damage", "positive": True, "value": 150.0},
                        {"url_name": "multishot", "positive": True, "value": 110.0},
                        {"url_name": "zoom", "positive": False, "value": -55.0},
                    ]
                },
            },
            {
                "id": "a2",
                "closed": False,
                "visible": True,
                "buyout_price": 1800,
                "owner": {"status": "ingame"},
                "item": {
                    "attributes": [
                        {"url_name": "critical_damage", "positive": True, "value": 148.0},
                        {"url_name": "critical_chance", "positive": True, "value": 185.0},
                        {"url_name": "multishot", "positive": True, "value": 108.0},
                        {"url_name": "zoom", "positive": False, "value": -50.0},
                    ]
                },
            },
            {
                "id": "a3",
                "closed": True,  # closed -> ignored!
                "visible": True,
                "buyout_price": 50,
                "owner": {"status": "ingame"},
                "item": {"attributes": [{"url_name": "impact_damage", "positive": True, "value": 100.0}]},
            },
        ]
        asc_auctions = [
            {"id": "b1", "closed": False, "visible": True, "buyout_price": 250, "owner": {"status": "online"}, "item": {"attributes": []}},
            {"id": "b2", "closed": False, "visible": True, "buyout_price": 320, "owner": {"status": "ingame"}, "item": {"attributes": []}},
            {"id": "b3", "closed": False, "visible": True, "buyout_price": 400, "owner": {"status": "ingame"}, "item": {"attributes": []}},
        ]
        report = analyze_auctions_payload("托里德", "torid", "rifle", desc_auctions, asc_auctions)
        self.assertEqual(report.top_positives[0].stat_name, "暴击伤害")
        self.assertEqual(report.top_positives[0].ratio_pct, 100.0)
        self.assertEqual(report.top_negatives[0].stat_name, "变焦")
        self.assertEqual(report.min_price, 250)
        self.assertEqual(report.median_price, 320)
        self.assertEqual(report.high_tier_median, 1800)

        with self.assertRaises(WMFetchError):
            analyze_auctions_payload("托里德", "torid", "rifle", [], [])

    def test_auto_save_wm_to_local_respects_flag(self) -> None:
        desc_auctions = [
            {
                "id": "m1",
                "closed": False,
                "visible": True,
                "buyout_price": 600,
                "owner": {"status": "ingame"},
                "item": {
                    "attributes": [
                        {"url_name": "critical_damage", "positive": True, "value": 140.0},
                        {"url_name": "fire_rate_/_attack_speed", "positive": True, "value": 70.0},
                        {"url_name": "finisher_damage", "positive": False, "value": -95.0},
                    ]
                },
            }
        ]
        report = analyze_auctions_payload("测试近战", "test_melee", "melee", desc_auctions, desc_auctions)
        # Melee should translate fire_rate_/_attack_speed -> 攻击速度
        self.assertIn("攻击速度", [x.stat_name for x in report.top_positives])

        with tempfile.TemporaryDirectory() as tmpdir:
            tiers_file = Path(tmpdir) / "recommended_tiers.json"
            tiers_file.write_text(
                json.dumps({"schema_version": 1, "auto_save_wm_to_local": False, "weapons": {}}),
                encoding="utf-8",
            )
            self.assertFalse(save_wm_recommendation_to_local(report, tiers_path=tiers_file))

            tiers_file.write_text(
                json.dumps({"schema_version": 1, "auto_save_wm_to_local": True, "weapons": {}}),
                encoding="utf-8",
            )
            self.assertTrue(save_wm_recommendation_to_local(report, tiers_path=tiers_file))
            saved_data = json.loads(tiers_file.read_text(encoding="utf-8"))
            self.assertIn("测试近战", saved_data["weapons"])
            self.assertEqual(saved_data["weapons"]["测试近战"]["source"], "wm")
            self.assertEqual(saved_data["weapons"]["测试近战"]["wm_min_price"], 600)


if __name__ == "__main__":
    unittest.main()
