"""Unit tests for the local Riven-aware Build Optimizer & WFSim export integration."""

from __future__ import annotations

import os
import time
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.build_optimizer import (
    SCENARIO_BOSS_DIRECT,
    SCENARIO_GENERAL,
    SCENARIO_STEEL_PATH,
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

            emitted = []
            dlg.send_optimized_to_wfsim.connect(lambda e, r, o: emitted.append((e, r, o)))
            dlg._btn_send_wfsim.click()
            self.assertEqual(len(emitted), 1)

            e_out, r_out, opt_out = emitted[0]
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
            self.assertEqual(payload[4], opt_out.arcane_ids)
        finally:
            dlg.close()
            panel.close()


if __name__ == "__main__":
    unittest.main()
