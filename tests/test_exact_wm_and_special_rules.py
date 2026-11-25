"""Tests for exact-stat WM matching, cascading fallback, multi-variant family dispositions, and Incarnon special rules."""

from __future__ import annotations

import os
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.control_panel import RivenControlPanel
from src.evaluator import FIXED_GLOBAL_DISPOSITION, RivenEvaluator
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
from src.riven_parser import RivenData, RivenStat
from src.settings_manager import UserSettings
from src.wm_client import (
    build_wm_search_url,
    fetch_wm_exact_riven_matches,
    stat_name_to_wm_slug,
)


def _make_stat(name: str, value: float, is_neg: bool = False) -> RivenStat:
    sign = "-" if is_neg else "+"
    signed = -value if is_neg else value
    return RivenStat(
        stat_name=name,
        raw_name=name,
        value=value,
        signed_value=signed,
        sign=sign,
        unit="%",
        is_negative=is_neg,
        raw_text=f"{sign}{value}% {name}",
    )


def _mock_auction(
    aid: str,
    price: int,
    status: str,
    rerolls: int,
    pos_slugs: list[str],
    neg_slug: str | None = None,
) -> dict:
    attrs = [{"url_name": s, "positive": True, "value": 100.0} for s in pos_slugs]
    if neg_slug:
        attrs.append({"url_name": neg_slug, "positive": False, "value": -40.0})
    return {
        "id": aid,
        "closed": False,
        "visible": True,
        "buyout_price": price,
        "starting_price": price,
        "owner": {"status": status, "ingame_name": f"Seller_{aid}"},
        "item": {"re_rolls": rerolls, "attributes": attrs},
    }


class TestIncarnonSpecialRulesAndVariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ev = RivenEvaluator()

    def test_laetum_negative_crit_chance_is_sss_inverted_god_neg(self) -> None:
        """Laetum (悦音 / 奏凯) with -Crit Chance MUST hit Non-Crit Devouring & SSS God Neg."""
        for alias in ("悦音", "奏凯", "Laetum"):
            riven = RivenData(
                weapon_name=alias,
                riven_name=f"{alias} Sati-croniata",
                stats=[
                    _make_stat("多重射击", 65.0, False),
                    _make_stat("射速", 42.0, False),
                    _make_stat("伤害", 118.0, False),
                    _make_stat("暴击几率", 62.0, True),  # -Crit Chance!
                ],
                is_valid=True,
            )
            res = self.ev.evaluate(riven, disposition_override=0.50)
            self.assertEqual(res.matched_archetype_id, "non_crit_devour", f"Failed for alias={alias}")
            self.assertTrue(res.has_inverted_god_neg)
            self.assertIn("非暴吞噬流", res.matched_archetype_name)
            self.assertIn("反转神负", res.matched_archetype_name)
            self.assertIn("SSS 反转神负", res.tags)
            self.assertNotIn("伤筋负", res.tags)
            self.assertIn(res.overall_grade, ("S", "A"))

            neg_se = [se for se in res.stat_evaluations if se.is_negative][0]
            self.assertTrue(neg_se.is_inverted_god_neg)
            self.assertTrue(neg_se.is_recommended)

    def test_braton_negative_crit_chance_stays_harmful_neg(self) -> None:
        """Regular weapon (布莱顿 / Braton) with -Crit Chance MUST still be penalized as 伤筋负."""
        riven = RivenData(
            weapon_name="布莱顿",
            riven_name="布莱顿 Sati-visicron",
            stats=[
                _make_stat("多重射击", 105.0, False),
                _make_stat("伤害", 190.0, False),
                _make_stat("暴击几率", 138.0, True),  # -Crit Chance on regular weapon!
            ],
            is_valid=True,
        )
        res = self.ev.evaluate(riven, disposition_override=1.20)
        self.assertFalse(res.has_inverted_god_neg)
        self.assertIn("伤筋负", res.tags)
        self.assertNotIn("SSS 反转神负", res.tags)

    def test_laetum_crit_positive_selects_crit_evolution(self) -> None:
        """Laetum with +CC +CD +MS selects Crit Evolution archetype instead of Non-Crit."""
        riven = RivenData(
            weapon_name="Laetum",
            stats=[
                _make_stat("暴击几率", 72.0, False),
                _make_stat("暴击伤害", 44.0, False),
                _make_stat("多重射击", 58.0, False),
                _make_stat("变焦", 30.0, True),
            ],
            is_valid=True,
        )
        res = self.ev.evaluate(riven, disposition_override=0.50)
        self.assertEqual(res.matched_archetype_id, "crit_evolution")
        self.assertIn("双暴进化流", res.matched_archetype_name)
        self.assertFalse(res.has_inverted_god_neg)

    def test_incarnon_heavy_melee_statstick_rules(self) -> None:
        """Ceramic Dagger & Magistar (执政官之怒) prioritize Initial Combo + CD + DMG over Attack Speed."""
        for alias in ("陶瓷匕首", "执政官之怒", "Magistar", "灵化之刃"):
            riven = RivenData(
                weapon_name=alias,
                stats=[
                    _make_stat("初始连击", 35.0, False),
                    _make_stat("暴击伤害", 115.0, False),
                    _make_stat("伤害", 210.0, False),
                    _make_stat("触发几率", 85.0, True),
                ],
                is_valid=True,
            )
            res = self.ev.evaluate(riven, disposition_override=1.35)
            self.assertEqual(res.matched_archetype_id, "heavy_statstick", f"Failed for {alias}")
            self.assertIn("重击神配", res.tags)
            self.assertIn("无害负", res.tags)
            self.assertNotIn("伤筋负", res.tags)

    def test_global_fixed_1_20_disposition_across_all_weapons_and_variants(self) -> None:
        """All weapons & variants (base, Prime, Kuva, Zariman, unknown) MUST use unified fixed 1.20 disposition."""
        for w in ("托里德", "拉特昂", "拉特昂 Prime", "伯斯顿 Prime", "赤毒·布拉玛", "悦音", "执政官之怒", "未知新武器"):
            riven = RivenData(
                weapon_name=w,
                stats=[
                    _make_stat("暴击几率", 160.0, False),
                    _make_stat("暴击伤害", 130.0, False),
                    _make_stat("多重射击", 100.0, False),
                    _make_stat("变焦", 55.0, True),
                ],
                is_valid=True,
            )
            res = self.ev.evaluate(riven)
            self.assertAlmostEqual(res.disposition, FIXED_GLOBAL_DISPOSITION, msg=f"Failed for {w}")
            self.assertEqual(res.disposition_source, "fixed_1.2")
            self.assertFalse(hasattr(res, "weapon_variants"))


class TestExactWMMatchingAndURL(unittest.TestCase):
    def test_stat_slug_mapping_and_search_url_builder(self) -> None:
        self.assertEqual(stat_name_to_wm_slug("伤害"), "base_damage_/_melee_damage")
        self.assertEqual(stat_name_to_wm_slug("暴击几率"), "critical_chance")
        self.assertEqual(stat_name_to_wm_slug("暴击伤害"), "critical_damage")
        self.assertEqual(stat_name_to_wm_slug("多重射击"), "multishot")
        self.assertEqual(stat_name_to_wm_slug("初始连击"), "channeling_damage")
        self.assertEqual(stat_name_to_wm_slug("变焦"), "zoom")

        url = build_wm_search_url(
            "torid",
            positive_stats=["暴击几率", "暴击伤害", "多重射击"],
            negative_stat="变焦",
        )
        self.assertTrue(url.startswith("https://warframe.market/auctions/search?"))
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(parsed["type"], ["riven"])
        self.assertEqual(parsed["weapon_url_name"], ["torid"])
        self.assertEqual(parsed["polarity"], ["any"])
        self.assertEqual(parsed["sort_by"], ["price_asc"])
        self.assertEqual(parsed["positive_stats"], ["critical_chance,critical_damage,multishot"])
        self.assertEqual(parsed["negative_stats"], ["zoom"])

    def test_exact_wm_cascading_fallback_3_to_2_and_top5_listings(self) -> None:
        """If 3+1- and 3-stat return 0 results, degrades cleanly to top-2 positive stats & formats top 5."""
        calls = []

        def fake_fetcher(
            slug: str,
            sort_by: str = "price_asc",
            positive_slugs=(),
            negative_slug=None,
            buyout_policy=None,
            timeout=14,
        ):
            calls.append((list(positive_slugs), negative_slug))
            # Return empty for 3-stat queries, return 6 listings on 2-stat fallback!
            if len(positive_slugs) >= 3:
                return []
            return [
                _mock_auction("1", 1200, "ingame", 0, ["critical_damage", "multishot", "critical_chance"], "zoom"),
                _mock_auction("2", 950, "online", 14, ["critical_damage", "multishot"], "recoil"),
                _mock_auction("3", 800, "offline", 5, ["critical_damage", "multishot"]),
                _mock_auction("4", 1500, "ingame", 2, ["critical_damage", "multishot"]),
                _mock_auction("5", 1700, "ingame", 9, ["critical_damage", "multishot"]),
                _mock_auction("6", 1900, "online", 3, ["critical_damage", "multishot"]),
                _mock_auction("7", 2500, "online", 1, ["critical_damage", "multishot"]),
            ]

        rep = fetch_wm_exact_riven_matches(
            weapon_name="托里德",
            weapon_slug="torid",
            weapon_class="rifle",
            positive_stats=["装填速度", "暴击伤害", "多重射击"],
            negative_stat="变焦",
            recommended_positives=["暴击伤害", "多重射击", "暴击几率"],
            recommended_negatives=["变焦"],
            fetcher=fake_fetcher,
        )

        # Call sequence: 1) 3pos+neg, 2) 3pos, 3) top-2 core pos (暴伤+多重)
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1], "zoom")
        self.assertIsNone(calls[1][1])
        self.assertEqual(calls[2][0], ["critical_damage", "multishot"])

        self.assertEqual(rep.match_level, "approx_2")
        self.assertIn("近似匹配 2 词条", rep.match_label("zh_CN"))
        self.assertEqual(len(rep.top_listings), 5)
        # Because >=5 online listings exist, lowest 5 online listings are prioritized (950, 1200, 1500, 1700, 1900)
        self.assertEqual([x.price for x in rep.top_listings], [950, 1200, 1500, 1700, 1900])
        line0 = rep.top_listings[0].format_line("zh_CN")
        self.assertIn("950p", line0)
        self.assertIn("+暴伤", line0)
        self.assertIn("+多重", line0)
        self.assertIn("14洗", line0)
        self.assertIn("positive_stats=critical_damage%2Cmultishot", rep.search_url)

    def test_overlay_and_panel_render_variants_archetype_and_wm_listings(self) -> None:
        _app = QApplication.instance() or QApplication([])
        ev = RivenEvaluator()
        riven = RivenData(
            weapon_name="拉特昂",
            riven_name="拉特昂 Crita-satiata",
            stats=[
                _make_stat("暴击几率", 180.0, False),
                _make_stat("暴击伤害", 150.0, False),
                _make_stat("多重射击", 110.0, False),
                _make_stat("穿刺伤害", 80.0, True),
            ],
            is_valid=True,
        )
        evaluation = ev.evaluate(riven)
        self.assertAlmostEqual(evaluation.disposition, FIXED_GLOBAL_DISPOSITION)

        win = RivenOverlayWindow(click_through=True, lang="zh_CN")
        win.update_cards([EvaluatedRivenCard("当前紫卡", riven, evaluation)])
        self.assertGreater(win.height(), 200)

        panel = RivenControlPanel(settings=UserSettings(), initial_ui_lang="zh_CN")
        opened = []
        panel.open_wm_browser_requested.connect(lambda q: opened.append(q))
        panel._inp_weapon.setText("托里德")
        panel._btn_open_wm_browser.click()
        self.assertEqual(opened, ["托里德"])
        win.hide()


if __name__ == "__main__":
    unittest.main()
