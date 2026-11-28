"""Unit tests for the local Riven-aware Build Optimizer & WFSim export integration."""

from __future__ import annotations

import os
import time
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.build_optimizer import (
    MOD_POOLS,
    ARCANE_POOLS,
    SCENARIO_BOSS_DIRECT,
    SCENARIO_GENERAL,
    SCENARIO_STEEL_PATH,
    categorize_mods_for_menu,
    evaluate_custom_build_for_riven,
    format_mod_stats,
    get_arcane_pool,
    get_mod_pool,
    is_mod_compatible_with_weapon,
    match_mod_search,
    optimize_build_for_riven,
)
from src.build_optimizer_dialog import BuildOptimizerDialog
from src.control_panel import RivenControlPanel
from src.evaluator import RivenEvaluator
from src.riven_parser import RivenParser
from src.settings_manager import UserSettings
from src.wfsim_bridge import build_wfsim_url, decode_wfsim_share_code


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestBuildOptimizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_riven_with_toxin_saves_toxin_mod_slot_and_runs_fast(self) -> None:
        riven = self.parser.parse(
            [
                "托里德 Acri-toxitron",
                "+145.0% 暴击伤害",
                "+105.0% 多重射击",
                "+102.0% 毒素伤害",
                "-52.0% 变焦",
                "段位 16 O 20",
            ]
        )
        ev = self.evaluator.evaluate(riven)

        t0 = time.perf_counter()
        res = optimize_build_for_riven(ev, scenario=SCENARIO_STEEL_PATH, allow_bane_mods=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(elapsed_ms, 250.0)
        self.assertEqual(len(res.mods), 7)
        self.assertIsNotNone(res.arcane)
        self.assertGreater(res.riven_gain_pct, 30.0)

        ids = set(res.mod_ids)
        # Since Riven already supplies Toxin, optimizer should NOT waste a slot on a Toxin mod!
        self.assertNotIn("malignant_force", ids)
        self.assertNotIn("infected_clip", ids)
        # Should pair with Cold -> Viral formed
        self.assertIn("病毒", res.active_elements_zh)
        # No exclusive-group conflicts among the 7 chosen mods
        groups = [s.mod.exclusive_group for s in res.mods if s.mod.exclusive_group]
        self.assertEqual(len(groups), len(set(groups)))

    def test_zariman_non_crit_laetum_strips_crit_mods_and_respects_no_bane_toggle(self) -> None:
        r_laetum = self.parser.parse(
            [
                "奏凯 Sati-croniata",
                "+115.0% 多重射击",
                "+75.0% 射速",
                "+210.0% 伤害",
                "-115.0% 暴击几率",
                "段位 14 O 18",
            ]
        )
        ev = self.evaluator.evaluate(r_laetum)
        res = optimize_build_for_riven(ev, scenario=SCENARIO_STEEL_PATH, allow_bane_mods=False)
        ids = set(res.mod_ids)

        # Non-Crit Devouring MUST strip crit chance & crit damage mods
        for crit_mod in ("creeping_bullseye", "primed_pistol_gambit", "primed_target_cracker", "galvanized_crosshairs"):
            self.assertNotIn(crit_mod, ids)
        # No-bane flag MUST exclude faction mods
        self.assertNotIn("primed_expel_grineer", ids)
        self.assertIn("galvanized_diffusion", ids)

    def test_melee_influence_and_dialog_to_wfsim_roundtrip(self) -> None:
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
        ev = self.evaluator.evaluate(r_ichor)
        res = optimize_build_for_riven(ev, scenario=SCENARIO_STEEL_PATH, allow_bane_mods=True)
        self.assertIsNotNone(res.arcane)
        self.assertEqual(res.arcane.id, "melee_influence")
        self.assertIn("电击", res.active_elements_zh)

        # Test interactive dialog + WFSim export round-trip
        settings = UserSettings()
        panel = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN")
        dlg = BuildOptimizerDialog(settings=settings, lang="zh_CN")
        try:
            self.assertIsNotNone(panel._btn_build_optimizer)
            self.assertIsNotNone(panel._btn_open_wfsim)

            dlg.show_for_card(ev, r_ichor)
            self.assertEqual(len(dlg._slot_frames), 8)  # 7 mods + 1 Riven

            # Switch scenario & toggle No-Bane live
            dlg._set_scenario(SCENARIO_GENERAL)
            self.assertEqual(dlg._last_result.scenario, SCENARIO_GENERAL)
            dlg._chk_no_bane.setChecked(True)
            self.assertNotIn("primed_smite_grineer", dlg._last_result.mod_ids)
            dlg._set_scenario(SCENARIO_BOSS_DIRECT)

            # Test live user editing of recommended Mods + Arcane & WFSim export of modified build
            self.assertEqual(len(dlg._slot_combos), 7)
            self.assertFalse(dlg._btn_reset_rec.isEnabled())

            # Swap slot 0 to a different mod ("killing_blow") and switch Arcane to "melee_exposure"
            dlg.set_slot_mod(0, "killing_blow")
            dlg.set_selected_arcane("melee_exposure")
            self.assertTrue(dlg._is_customized)
            self.assertTrue(dlg._btn_reset_rec.isEnabled())
            self.assertIn("killing_blow", dlg._last_result.mod_ids)
            self.assertEqual(dlg._last_result.arcane_ids, ["melee_exposure"])
            self.assertIsNotNone(dlg._last_result.delta_vs_recommended_pct)

            # Mutually exclusive conflict test: picking "sacrificial_pressure" auto-clears "primed_pressure_point"
            dlg.set_slot_mod(1, "primed_pressure_point")
            dlg.set_slot_mod(2, "sacrificial_pressure")
            self.assertNotIn("primed_pressure_point", dlg._last_result.mod_ids)
            self.assertIn("sacrificial_pressure", dlg._last_result.mod_ids)

            emitted = []
            dlg.send_optimized_to_wfsim.connect(lambda e, r, o: emitted.append((e, r, o)))
            dlg._btn_send_wfsim.click()
            self.assertEqual(len(emitted), 1)

            e_out, r_out, opt_out = emitted[0]
            self.assertIn("killing_blow", opt_out.mod_ids)
            self.assertEqual(opt_out.arcane_ids, ["melee_exposure"])
            wf_link = build_wfsim_url(
                e_out,
                r_out,
                custom_mods=opt_out.mod_ids,
                custom_arcanes=opt_out.arcane_ids,
                custom_build_label="本地优化配装",
            )
            self.assertTrue(wf_link.ok, msg=wf_link.error)
            code = urllib.parse.parse_qs(urllib.parse.urlparse(wf_link.url).query)["b"][0]
            payload = decode_wfsim_share_code(code)
            exported_mods = [s[0] for s in payload[3][:7] if isinstance(s, list)]
            self.assertEqual(exported_mods, opt_out.mod_ids)
            self.assertEqual(payload[3][7], ["~0"])
            self.assertEqual(payload[4], ["melee_exposure"])

            # Reset restores auto-recommended build cleanly
            dlg.reset_to_recommended()
            self.assertFalse(dlg._is_customized)
            self.assertFalse(dlg._btn_reset_rec.isEnabled())
        finally:
            dlg.close()
            panel.close()

    def test_official_simplified_chinese_mod_and_arcane_names(self) -> None:
        mods_by_id = {m.id: m.name_zh for pool in MOD_POOLS.values() for m in pool}
        arcs_by_id = {a.id: a.name_zh for pool in ARCANE_POOLS.values() for a in pool}
        expected_mods = {
            "galvanized_chamber": "镀层 分裂膛室",
            "galvanized_aptitude": "镀层 步枪才能",
            "galvanized_hell": "镀层 地狱弹膛",
            "galvanized_savvy": "镀层 通晓霰弹枪",
            "galvanized_diffusion": "镀层 弹头扩散",
            "galvanized_shot": "镀层 准确射手",
            "galvanized_reflex": "镀层 增幅线圈",
            "critical_delay": "关键延迟",
            "critical_deceleration": "降速暴击",
            "creeping_bullseye": "匍匐靶心",
            "malignant_force": "致命火力",
            "berserker_fury": "嗜血 狂暴",
            "primed_ravage": "破灭 Prime",
            "scorch": "灼痕焦点",
            "chilling_reload": "激冷装填",
            "hornet_strike": "黄蜂螫刺",
        }
        for mid, zh in expected_mods.items():
            self.assertEqual(mods_by_id[mid], zh, msg=f"Official zh-hans mismatch for {mid}")

        expected_arcanes = {
            "melee_influence": "近战·侵染",
            "melee_exposure": "近战·暴露",
            "melee_afflictions": "近战·苦痛",
            "cascadia_flare": "瀑流·耀炎",
            "primary_blight": "主要·毒侵",
        }
        for aid, zh in expected_arcanes.items():
            self.assertEqual(arcs_by_id[aid], zh, msg=f"Official zh-hans mismatch for {aid}")

        # Sanity check direct evaluate_custom_build_for_riven helper
        r_torid = self.parser.parse(["托里德 Crita-acrican", "+180% 暴击几率", "+145% 暴击伤害", "段位 16 O 8"])
        ev_torid = self.evaluator.evaluate(r_torid)
        custom = evaluate_custom_build_for_riven(
            ev_torid,
            ["galvanized_chamber", "critical_delay", "vital_sense", None, "malignant_force", "rime_rounds", "hellfire"],
            "primary_merciless",
        )
        self.assertEqual(len(custom.mods), 6)
        self.assertIn("病毒", custom.active_elements_zh)
        self.assertIn("火焰", custom.active_elements_zh)

    def test_conditional_arcanes_never_selected_when_element_merged_into_compound(self) -> None:
        # Regression test for Amprex Viral+Heat build: Toxin+Cold merges into Viral, leaving NO
        # standalone Toxin or Cold -> Primary Blight / Primary Frostbite MUST NOT be selected!
        r_amprex = self.parser.parse(
            [
                "安培克斯 Parci-critadex",
                "+110.0% 暴击几率",
                "+45.0% 弹药效率",
                "+65.0% 触发几率",
                "-28.0% 对 Corpus 伤害",
                "段位 12 O 5",
            ]
        )
        ev_amprex = self.evaluator.evaluate(r_amprex)
        res = optimize_build_for_riven(ev_amprex, scenario=SCENARIO_GENERAL, allow_bane_mods=False)
        self.assertIsNotNone(res.arcane)
        if "毒素" not in res.active_elements_zh:
            self.assertNotEqual(res.arcane.id, "primary_blight")
        if "冰冻" not in res.active_elements_zh:
            self.assertNotEqual(res.arcane.id, "primary_frostbite")
        self.assertIn(res.arcane.id, ("primary_merciless", "primary_deadhead"))
        self.assertIn("vital_sense", res.mod_ids)

        # Manually forcing Primary Blight onto a Viral+Heat build must warn about missing standalone Toxin
        warned = evaluate_custom_build_for_riven(
            ev_amprex,
            ["galvanized_chamber", "critical_delay", "vital_sense", "galvanized_aptitude", "infected_clip", "primed_cryo_rounds", "hellfire"],
            "primary_blight",
            scenario=SCENARIO_GENERAL,
        )
        self.assertTrue(any("主要·毒侵" in msg and "独立【毒素】" in msg for msg in warned.rationale_zh))

    def test_full_catalog_compound_element_search_purple_riven_and_overall_stats(self) -> None:
        # 1. Full catalog loaded with expanded Mods & Arcanes
        rifle_mods = get_mod_pool("rifle")
        rifle_arcs = get_arcane_pool("rifle")
        pistol_arcs = get_arcane_pool("pistol")
        melee_arcs = get_arcane_pool("melee")
        self.assertGreaterEqual(len(rifle_mods), 100)
        self.assertGreaterEqual(len(rifle_arcs), 12)
        self.assertGreaterEqual(len(pistol_arcs), 20)
        self.assertGreaterEqual(len(melee_arcs), 10)
        self.assertIn("primary_crux", [a.id for a in rifle_arcs])
        self.assertIn("secondary_encumber", [a.id for a in pistol_arcs])
        self.assertIn("melee_duplicate", [a.id for a in melee_arcs])
        self.assertIn("radiated_reload", [m.id for m in rifle_mods])
        self.assertIn("magnetic_capacity", [m.id for m in rifle_mods])

        # 2. Compound element search: "腐蚀" -> returns Toxin + Electricity mods!
        corr_hits = [m.id for m in rifle_mods if match_mod_search(m, "腐蚀", "rifle")]
        self.assertIn("infected_clip", corr_hits)
        self.assertIn("malignant_force", corr_hits)
        self.assertIn("high_voltage", corr_hits)
        self.assertIn("stormbringer", corr_hits)
        self.assertNotIn("hellfire", corr_hits)

        # "辐射" -> returns Heat + Electricity + direct Radiation ("radiated_reload")
        rad_hits = [m.id for m in rifle_mods if match_mod_search(m, "辐射", "rifle")]
        self.assertIn("hellfire", rad_hits)
        self.assertIn("stormbringer", rad_hits)
        self.assertIn("radiated_reload", rad_hits)

        # 3. Mod stat formatting produces explicit values
        chamber = next(m for m in rifle_mods if m.id == "galvanized_chamber")
        self.assertIn("+230%", format_mod_stats(chamber, "rifle", "zh_CN"))

        # 4. Dialog shows purple Riven card, searchable combo filtering, & overall stats (melee includes Range)
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
        ev_m = self.evaluator.evaluate(r_ichor)
        settings = UserSettings()
        dlg = BuildOptimizerDialog(settings=settings, lang="zh_CN")
        try:
            dlg.show_for_card(ev_m, r_ichor)
            # Slot #8 Riven frame uses purple border (#C084FC) and purple bg (#2A1246)
            riven_frame = dlg._slot_frames[-1]
            self.assertEqual(riven_frame.objectName(), "RivenCard")
            self.assertIn("#C084FC", riven_frame.styleSheet())
            self.assertIn("#2A1246", riven_frame.styleSheet())

            # Searchable selector preserves typed text ("腐蚀") while popup shows Toxin & Electricity melee mods!
            sel0 = dlg._slot_combos[0]
            sel0._edit.setText("腐蚀")
            sel0._on_text_edited("腐蚀")
            self.assertEqual(sel0.current_text(), "腐蚀")  # typed text stays visible until user picks a mod!
            filtered_ids = sel0.search_matching_ids("腐蚀")
            self.assertIn("voltaic_strike", filtered_ids)
            self.assertIn("virulent_scourge", filtered_ids)
            self.assertNotIn("vicious_frost", filtered_ids)

            # Cascading hover-category QMenu builds submenus cleanly
            cat_menu = sel0.build_category_menu()
            submenu_titles = [act.text() for act in cat_menu.actions() if act.menu() is not None]
            self.assertTrue(any("毒素" in t for t in submenu_titles))
            self.assertTrue(any("暴击" in t for t in submenu_titles))

            # Overall build stat summary populated & melee displays Range pill
            self.assertIsNotNone(dlg._last_result.stat_summary)
            self.assertGreater(dlg._last_result.stat_summary.range_m, 0.0)
            pill_texts = " ".join(p.text() for p in dlg._stat_pills)
            self.assertIn("攻速", pill_texts)
            self.assertIn("范围", pill_texts)
            self.assertIn("暴击", pill_texts)
            self.assertIn("爆伤", pill_texts)
            self.assertIn("触发", pill_texts)
        finally:
            dlg.close()

    def test_weapon_exclusive_mods_only_appear_for_matching_weapon_in_dedicated_category(self) -> None:
        # 1. Higasa Serration (晴日伞 膛线) MUST NOT appear for Torid / Amprex
        torid_pool = get_mod_pool("rifle", weapon_slug="torid", weapon_name_en="Torid")
        self.assertNotIn("higasa_serration", [m.id for m in torid_pool])
        torid_cats = categorize_mods_for_menu(torid_pool, "rifle", "zh_CN")
        self.assertFalse(any("武器专属" in title for title, _ in torid_cats))

        # 2. Higasa Serration (晴日伞 膛线) MUST appear for Higasa inside dedicated "★ 武器专属" category!
        higasa_pool = get_mod_pool("rifle", weapon_slug="higasa", weapon_name_en="Higasa")
        self.assertIn("higasa_serration", [m.id for m in higasa_pool])
        higasa_cats = categorize_mods_for_menu(higasa_pool, "rifle", "zh_CN")
        self.assertEqual(higasa_cats[0][0], "★ 武器专属")
        self.assertIn("higasa_serration", [m.id for m in higasa_cats[0][1]])

        # 3. Kuva Sobek gets Acid Shells (酸性弹药); Rubico gets Sniper Primed Chamber; Xoris gets Glaive Volatile Quick Return
        sobek_pool = get_mod_pool("shotgun", weapon_slug="kuva_sobek", weapon_name_en="Kuva Sobek")
        self.assertIn("acid_shells", [m.id for m in sobek_pool])
        self.assertNotIn("scattered_justice", [m.id for m in sobek_pool])

        rubico_pool = get_mod_pool("rifle", weapon_slug="rubico", weapon_name_en="Rubico")
        self.assertIn("primed_chamber", [m.id for m in rubico_pool])
        self.assertNotIn("split_flights", [m.id for m in rubico_pool])

        xoris_pool = get_mod_pool("melee", weapon_slug="xoris", weapon_name_en="Xoris")
        self.assertIn("volatile_quick_return", [m.id for m in xoris_pool])
        self.assertNotIn("amanata_pressure", [m.id for m in xoris_pool])


if __name__ == "__main__":
    unittest.main()
