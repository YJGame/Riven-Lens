"""Unit tests for WFSim (`https://wfsim.app`) v2 share-link export, trademark compliance, and auto-standby."""

from __future__ import annotations

import os
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.build_optimizer_dialog import BuildOptimizerDialog
from src.control_panel import RivenControlPanel
from src.evaluator import RivenEvaluator
from src.focus_watcher import SmartFocusWatcher, is_warframe_game_title
from src.hotkey_manager import DynamicHotkeyManager
from src.riven_parser import RivenParser
from src.settings_manager import UserSettings
from src.wfsim_bridge import (
    build_wfsim_url,
    decode_wfsim_share_code,
    resolve_wfsim_weapon_id,
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestWFSimShareLinkCompliance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_default_export_is_riven_only_v2_json_share_code(self) -> None:
        riven = self.parser.parse(
            [
                "托里德 Crita-acrican",
                "+185.0% 暴击几率",
                "+148.0% 暴击伤害",
                "+111.0% 多重射击",
                "-54.0% 变焦",
                "段位 16 O 12",
            ]
        )
        ev = self.evaluator.evaluate(riven)
        # Default: Riven-only (`include_baseline_build=False`)
        res = build_wfsim_url(ev, riven)

        self.assertTrue(res.ok, msg=res.error)
        self.assertEqual(res.weapon_id, "torid")
        self.assertEqual(res.build_label, "仅导入紫卡")
        self.assertTrue(res.url.startswith("https://wfsim.app/weapons/torid?b="))
        self.assertEqual(len(res.included_stats), 4)
        self.assertEqual(res.skipped_stats, [])

        parsed_url = urllib.parse.urlparse(res.url)
        code = urllib.parse.parse_qs(parsed_url.query)["b"][0]
        # Must use v2 JSON plaintext-ID prefix ("0" or "1")
        self.assertIn(code[0], ("0", "1"))
        payload = decode_wfsim_share_code(code)

        self.assertEqual(payload[0], 2)
        self.assertEqual(payload[1], "torid")
        # Riven-only: only slot 0 has ["~0"], remaining 8 slots are 0, arcanes empty
        self.assertEqual(payload[3], [["~0"], 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(payload[4], [])

        # Riven entry: [name, shape, rank, pol, bonuses, malus]
        rivens = payload[6]
        self.assertEqual(len(rivens), 1)
        _r_name, shape, rank, pol, bonuses, malus = rivens[0]
        self.assertEqual(shape, "3+1")
        self.assertEqual(rank, 8)
        self.assertEqual(pol, "M")
        self.assertEqual([b[0] for b in bonuses], ["critical_chance", "critical_damage", "multishot"])
        for _sid, roll in bonuses:
            self.assertGreaterEqual(roll, 0.900)
            self.assertLessEqual(roll, 1.100)
        self.assertEqual(malus[0], "zoom")
        self.assertGreaterEqual(malus[1], 0.900)
        self.assertLessEqual(malus[1], 1.100)

    def test_optional_baseline_build_and_melee_stat_mapping(self) -> None:
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
        ev_laetum = self.evaluator.evaluate(r_laetum)
        res_l = build_wfsim_url(ev_laetum, r_laetum, include_baseline_build=True)
        self.assertTrue(res_l.ok)
        code_l = urllib.parse.parse_qs(urllib.parse.urlparse(res_l.url).query)["b"][0]
        p_l = decode_wfsim_share_code(code_l)
        slot_ids = [s[0] for s in p_l[3] if isinstance(s, list)]
        self.assertIn("hornet_strike", slot_ids)
        self.assertNotIn("creeping_bullseye", slot_ids)
        self.assertEqual(p_l[6][0][5][0], "critical_chance")

        r_ichor = self.parser.parse(
            [
                "恶脓双斧 Visi-loctidra",
                "+180.0% 伤害",
                "+60.0% 攻击速度",
                "+2.1 攻击范围",
                "-90.0% 处决伤害",
                "段位 15 O 6",
            ]
        )
        ev_ichor = self.evaluator.evaluate(r_ichor)
        res_m = build_wfsim_url(ev_ichor, r_ichor)
        self.assertTrue(res_m.ok)
        code_m = urllib.parse.parse_qs(urllib.parse.urlparse(res_m.url).query)["b"][0]
        p_m = decode_wfsim_share_code(code_m)
        self.assertEqual(p_m[4], [])  # Default Riven-only
        bonus_ids = [b[0] for b in p_m[6][0][4]]
        self.assertEqual(bonus_ids, ["melee_damage", "attack_speed", "range"])
        self.assertEqual(p_m[6][0][5][0], "finisher_damage")

    def test_special_weapon_id_mapping_kitguns_zaws_and_ampersands(self) -> None:
        # Kitguns map cleanly to _secondary / _primary
        r_tomb = self.parser.parse(["墓指 Crita-satiata", "+150% 暴击几率", "+100% 多重射击", "段位 12 O 3"])
        ev_tomb = self.evaluator.evaluate(r_tomb)
        self.assertEqual(resolve_wfsim_weapon_id(ev_tomb, r_tomb), "tombfinger_secondary")
        self.assertEqual(resolve_wfsim_weapon_id(ev_tomb, r_tomb, kitgun_slot="primary"), "tombfinger_primary")

        # Zaws map cleanly to valid _one_handed / _two_handed WFSim IDs
        r_kripath = self.parser.parse(["瘟疫 克里帕丝 Crita-loctidra", "+140% 暴击几率", "+1.8 攻击范围", "段位 14 O 5"])
        ev_kripath = self.evaluator.evaluate(r_kripath)
        self.assertEqual(resolve_wfsim_weapon_id(ev_kripath, r_kripath), "plague_kripath_two_handed")

        r_sepfahn = self.parser.parse(["瑟普梵 Crita-loctidra", "+140% 暴击几率", "+1.8 攻击范围", "段位 14 O 2"])
        ev_sepfahn = self.evaluator.evaluate(r_sepfahn)
        self.assertEqual(resolve_wfsim_weapon_id(ev_sepfahn, r_sepfahn), "sepfahn_one_handed")

        # Ampersand & special slug overrides (Riven names use base weapon; Prime slugs also map cleanly)
        r_silva = self.parser.parse(["席瓦 & 神盾 Crita-visi", "+140% 暴击几率", "+150% 伤害", "段位 13 O 1"])
        ev_silva = self.evaluator.evaluate(r_silva)
        self.assertEqual(resolve_wfsim_weapon_id(ev_silva, r_silva), "silva_and_aegis")
        ev_silva.weapon_slug = "silva_aegis_prime"
        self.assertEqual(resolve_wfsim_weapon_id(ev_silva, r_silva), "silva_and_aegis_prime")
        ev_silva.weapon_slug = "cobra_crane_prime"
        self.assertEqual(resolve_wfsim_weapon_id(ev_silva, r_silva), "cobra_and_crane_prime")

        r_split = self.parser.parse(["暗黑分合剑 Crita-visi", "+140% 暴击几率", "+150% 伤害", "段位 12 O 1"])
        ev_split = self.evaluator.evaluate(r_split)
        self.assertEqual(resolve_wfsim_weapon_id(ev_split, r_split), "dark_split_sword")

    def test_trademark_compliant_panel_branding_and_no_wfsim_hotkey(self) -> None:
        settings = UserSettings()
        panel = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN")
        dlg = BuildOptimizerDialog(settings=settings, lang="zh_CN")
        try:
            # Window title MUST remain plain "裂罅透镜" (never "WFSim 联动版" / "WFSim Edition")
            self.assertEqual(panel.windowTitle(), "裂罅透镜")
            self.assertNotIn("WFSim", panel.windowTitle())

            # WFSim export is integrated inside BuildOptimizerDialog (not duplicated on Control Panel)
            self.assertFalse(hasattr(panel, "_btn_open_wfsim"))
            self.assertFalse(hasattr(panel, "_inp_hk_wfsim"))
            self.assertIn("配装优化", panel._btn_build_optimizer.text())
            self.assertIn("wfsim.app", panel._btn_build_optimizer.toolTip())
            self.assertIn("非官方独立项目", panel._btn_build_optimizer.toolTip())

            # Inside BuildOptimizerDialog: both full-build and Riven-only WFSim buttons exist with compliant text
            self.assertIn("在 WFSim 中打开", dlg._btn_send_wfsim.text())
            self.assertIn("wfsim.app", dlg._btn_send_wfsim.text())
            self.assertIn("仅导入紫卡", dlg._btn_send_riven_only.text())
            self.assertIn("非官方独立项目", dlg._btn_send_riven_only.toolTip())

            riven = self.parser.parse(["托里德 Crita-acrican", "+185.0% 暴击几率", "+148.0% 暴击伤害", "段位 16 O 12"])
            ev = self.evaluator.evaluate(riven)
            dlg.show_for_card(ev, riven)
            fired = []
            dlg.send_riven_only_to_wfsim.connect(lambda _ev, _rv: fired.append("riven_only"))
            dlg._btn_send_riven_only.click()
            self.assertEqual(fired, ["riven_only"])

            hm = DynamicHotkeyManager()
            self.assertFalse(hasattr(hm, "wfsim_triggered"))
            self.assertFalse(hm.dispatch_action_name("wfsim", "test"))
        finally:
            dlg.close()
            panel.close()

    def test_leaving_warframe_game_including_control_panel_triggers_standby(self) -> None:
        self.assertTrue(is_warframe_game_title("Warframe"))
        self.assertTrue(is_warframe_game_title("WARFRAME"))
        self.assertTrue(is_warframe_game_title("星际战甲"))

        for non_game in (
            "裂罅透镜",
            "Riven Lens",
            "Google Chrome - WFSim",
            "Warframe.market - Microsoft Edge",
            "Discord",
            "",
        ):
            self.assertFalse(is_warframe_game_title(non_game), msg=f"Should NOT treat {non_game!r} as game")

        current_title = ["Warframe"]
        watcher = SmartFocusWatcher(enabled=False, title_provider=lambda: current_title[0])

        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=False)
        self.assertEqual((act, awaiting), ("noop", False))

        # Switch to Control Panel (Ctrl+Alt+D) -> immediately pause to Standby
        current_title[0] = "裂罅透镜"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=False)
        self.assertEqual((act, awaiting), ("pause", False))

        # Switch to browser (wfsim.app) -> immediately pause to Standby
        current_title[0] = "WFSim - Torid - Google Chrome"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=False)
        self.assertEqual((act, awaiting), ("pause", False))

        # Pre-armed from Control Panel: waits until player clicks back into Warframe, then pauses on exit
        current_title[0] = "裂罅透镜"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=True)
        self.assertEqual((act, awaiting), ("noop", True))

        current_title[0] = "Warframe"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=awaiting)
        self.assertEqual((act, awaiting), ("noop", False))

        current_title[0] = "Discord"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=awaiting)
        self.assertEqual((act, awaiting), ("pause", False))


if __name__ == "__main__":
    unittest.main()
