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
    evaluate_custom_build_for_riven,
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

            # Mutually exclusive conflict test: picking "primed_pressure_point" auto-clears "condition_overload"
            dlg.set_slot_mod(1, "condition_overload")
            dlg.set_slot_mod(2, "primed_pressure_point")
            self.assertNotIn("condition_overload", dlg._last_result.mod_ids)
            self.assertIn("primed_pressure_point", dlg._last_result.mod_ids)

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


if __name__ == "__main__":
    unittest.main()
