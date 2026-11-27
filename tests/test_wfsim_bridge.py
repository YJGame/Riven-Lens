"""Unit tests for WFSim (`https://wfsim.app`) browser deep-link builder & dedicated WFSim edition."""

from __future__ import annotations

import os
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.control_panel import RivenControlPanel
from src.evaluator import RivenEvaluator
from src.focus_watcher import SmartFocusWatcher, is_warframe_game_title
from src.hotkey_manager import DynamicHotkeyManager
from src.riven_parser import RivenParser
from src.settings_manager import UserSettings
from src.wfsim_bridge import (
    build_wfsim_url,
    decode_wfsim_share_code,
    is_wfsim_edition,
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class TestWFSimBridgeAndEdition(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def test_torid_riven_encodes_valid_wfsim_url_with_baseline_build_and_exact_rolls(self) -> None:
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
        res = build_wfsim_url(ev, riven, include_baseline_build=True)

        self.assertTrue(res.ok, msg=res.error)
        self.assertEqual(res.weapon_id, "torid")
        self.assertTrue(res.url.startswith("https://wfsim.app/weapons/torid?b="))
        self.assertEqual(len(res.included_stats), 4)
        self.assertEqual(res.skipped_stats, [])

        # Decode `?b=` token and verify exact WFSim v2 positional tuple format
        parsed_url = urllib.parse.urlparse(res.url)
        code = urllib.parse.parse_qs(parsed_url.query)["b"][0]
        payload = decode_wfsim_share_code(code)

        self.assertEqual(payload[0], 2)
        self.assertEqual(payload[1], "torid")
        # Slots: 7 baseline rifle mods + "~0" Riven + 0 Exilus
        slots9 = payload[3]
        self.assertEqual(len(slots9), 9)
        self.assertEqual(slots9[0], ["galvanized_chamber"])
        self.assertEqual(slots9[7], ["~0"])
        self.assertEqual(slots9[8], 0)
        self.assertEqual(payload[4], ["primary_merciless"])

        # Riven entry: [name, shape, rank, pol, bonuses, malus]
        rivens = payload[6]
        self.assertEqual(len(rivens), 1)
        r_name, shape, rank, pol, bonuses, malus = rivens[0]
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

    def test_zariman_non_crit_laetum_uses_non_crit_baseline_and_melee_uses_melee_ids(self) -> None:
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
        res_l = build_wfsim_url(ev_laetum, r_laetum)
        self.assertTrue(res_l.ok)
        code_l = urllib.parse.parse_qs(urllib.parse.urlparse(res_l.url).query)["b"][0]
        p_l = decode_wfsim_share_code(code_l)
        slot_ids = [s[0] for s in p_l[3] if isinstance(s, list)]
        self.assertIn("hornet_strike", slot_ids)
        self.assertNotIn("creeping_bullseye", slot_ids)
        self.assertEqual(p_l[6][0][5][0], "critical_chance")  # -Crit Chance malus

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
        self.assertEqual(p_m[4], ["melee_influence"])
        bonus_ids = [b[0] for b in p_m[6][0][4]]
        self.assertEqual(bonus_ids, ["melee_damage", "attack_speed", "range"])
        self.assertEqual(p_m[6][0][5][0], "finisher_damage")

    def test_edition_isolation_standard_vs_wfsim_edition(self) -> None:
        self.assertTrue(is_wfsim_edition(cli_flag=True))
        settings = UserSettings()

        # Standard edition: WFSim button & optimizer button are NOT shown
        panel_std = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN", wfsim_edition=False)
        panel_wf = RivenControlPanel(settings=settings, initial_ui_lang="zh_CN", wfsim_edition=True)
        try:
            self.assertIsNone(panel_std._btn_open_wfsim)
            self.assertFalse(hasattr(panel_std, "_inp_hk_wfsim"))

            # WFSim edition: panel button exists, NO WFSim global hotkey exists
            self.assertIsNotNone(panel_wf._btn_open_wfsim)
            self.assertFalse(hasattr(panel_wf, "_inp_hk_wfsim"))
            self.assertNotIn("Ctrl+Alt+W", panel_wf._btn_open_wfsim.text())
            self.assertIn("WFSim", panel_wf.windowTitle())

            fired = []
            panel_wf.open_wfsim_requested.connect(lambda: fired.append("btn"))
            panel_wf._btn_open_wfsim.click()
            self.assertEqual(fired, ["btn"])

            hm = DynamicHotkeyManager()
            self.assertFalse(hasattr(hm, "wfsim_triggered"))
            self.assertFalse(hm.dispatch_action_name("wfsim", "test"))
        finally:
            panel_std.close()
            panel_wf.close()

    def test_leaving_warframe_game_including_control_panel_triggers_standby(self) -> None:
        # Strictly True only for Warframe game client windows
        self.assertTrue(is_warframe_game_title("Warframe"))
        self.assertTrue(is_warframe_game_title("WARFRAME"))
        self.assertTrue(is_warframe_game_title("星际战甲"))

        # Strictly False for our own Control Panel (Ctrl+Alt+D), browsers, Discord, desktop, etc.
        for non_game in (
            "裂罅透镜",
            "裂罅透镜 · WFSim 联动版",
            "Riven Lens",
            "Google Chrome - WFSim",
            "Warframe.market - Microsoft Edge",
            "Discord",
            "",
        ):
            self.assertFalse(is_warframe_game_title(non_game), msg=f"Should NOT treat {non_game!r} as game")

        current_title = ["Warframe"]
        watcher = SmartFocusWatcher(enabled=False, title_provider=lambda: current_title[0])

        # In game -> stay active
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=False)
        self.assertEqual((act, awaiting), ("noop", False))

        # Switch to Control Panel (Ctrl+Alt+D) -> immediately pause to Standby!
        current_title[0] = "裂罅透镜 · WFSim 联动版"
        act, awaiting = watcher.evaluate_game_focus_for_standby(is_active_mode=True, awaiting_game_focus=False)
        self.assertEqual((act, awaiting), ("pause", False))

        # Switch to browser (wfsim.app / warframe.market) -> immediately pause to Standby!
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
