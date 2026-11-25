"""Unit tests for the 4-part comprehensive upgrade of 裂罅透镜 (Riven Lens):
1. All 15 official Warframe languages + on-demand RapidOCR model check/pull/hot-reload + 15-lang Riven stat parsing.
2. Zero Chinese leakage when switching live to English, 日本語, and Русский across Control Panel, Overlay, and Tray.
3. Minimalist Control Panel labels & removal of verbose grey hint paragraphs.
4. Ultra-minimal Void Relic 4-choice overlay: strict left-to-right 4-slot order
   [★ Gold/Silver (Rank 1/2 only)] -> [Plat p] -> [|] -> [Ducats D] -> [18x18 VaultedChestIcon (Vaulted only)],
   with zero part names and zero nested inner boxes.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QCheckBox, QFrame, QGroupBox, QLabel, QPushButton

from src.companion_overlays import RelicRewardsOverlayWindow, get_vaulted_chest_icon_path
from src.control_panel import RivenControlPanel
from src.i18n import I18N, LANGUAGE_DISPLAY_NAMES, SUPPORTED_LANGS, tr
from src.ocr_engine import RivenOCREngine
from src.ocr_worker import OCRModelManager
from src.overlay_ui import RivenOverlayWindow
from src.relic_prices import PrimeItemPrice, PrimePriceStore, RelicRewardMatch
from src.riven_parser import RivenParser
from src.roi_manager import MODE_PAUSED, MODE_SINGLE_RIVEN
from src.settings_manager import UserSettings
from src.tray_manager import TrayManager

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_SIMPLIFIED_CHINESE_LEAK_TOKENS = (
    "裂罅透镜",
    "切换模式",
    "校准识别框",
    "解锁拖拽位置",
    "锁定快捷键",
    "稀有属性提示音",
    "切出游戏自动隐藏",
    "启动时自动监控",
    "后台待命",
    "当前模式",
    "触发识别",
    "悬浮窗",
)


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _collect_visible_panel_texts(panel: RivenControlPanel) -> list[str]:
    """Collect all user-visible text strings from the Control Panel (excluding language dropdown names)."""
    texts: list[str] = [panel.windowTitle()]
    for cls in (QLabel, QPushButton, QCheckBox):
        for w in panel.findChildren(cls):
            if w.isHidden():
                continue
            txt = w.text().strip()
            if txt:
                texts.append(txt)
    for grp in panel.findChildren(QGroupBox):
        if grp.title().strip():
            texts.append(grp.title().strip())
    # Include current text of mode dropdown and auto item of language dropdown
    for i in range(panel._cmb_mode.count()):
        texts.append(panel._cmb_mode.itemText(i))
    texts.append(panel._cmb_lang.itemText(0))  # "Auto" option
    return texts


class TestMultilangAndRelicV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _ensure_qapp()

    def test_all_15_official_languages_and_live_switch_zero_chinese_leak(self) -> None:
        """All 15 languages are listed; switching to English/日本語/Русский has zero Chinese leaks."""
        self.assertEqual(len(SUPPORTED_LANGS), 15)
        expected_display = {
            "简体中文",
            "繁體中文",
            "English",
            "Français",
            "Italiano",
            "Deutsch",
            "Español",
            "Português-Br",
            "Русский",
            "Polski",
            "Українська",
            "Türkçe",
            "日本語",
            "한국어",
            "ไทย",
        }
        self.assertEqual(set(LANGUAGE_DISPLAY_NAMES.values()), expected_display)

        panel = RivenControlPanel(settings=UserSettings(), initial_ui_lang="zh_CN")
        overlay = RivenOverlayWindow(click_through=True, lang="zh_CN", auto_collapse_seconds=0)
        tray = TrayManager(
            on_toggle_monitor=lambda: None,
            on_open_panel=lambda: None,
            on_fetch_wm=lambda: None,
            on_update_db=lambda: None,
            on_quit=lambda: None,
            initial_ui_lang="zh_CN",
            initial_mode=MODE_SINGLE_RIVEN,
        )
        try:
            # Dropdown has "auto" + all 15 official languages = 16 items
            self.assertEqual(panel._cmb_lang.count(), 16)
            combo_codes = [panel._cmb_lang.itemData(i) for i in range(1, panel._cmb_lang.count())]
            self.assertEqual(combo_codes, list(SUPPORTED_LANGS))

            # Verify minimalist Chinese labels & hidden verbose paragraphs (Requirement 3)
            self.assertEqual(panel.windowTitle(), "裂罅透镜")
            self.assertEqual(panel._lbl_hk_toggle.text(), "切换模式")
            self.assertEqual(panel._lbl_hk_wm.text(), "触发识别 / 查价")
            self.assertEqual(panel._lbl_hk_hide.text(), "隐藏悬浮窗")
            self.assertEqual(panel._btn_calibrate_roi.text(), "校准识别框")
            self.assertEqual(panel._btn_drag_unlock.text(), "解锁拖拽位置")
            self.assertEqual(panel._btn_lock_hotkeys.text(), "锁定快捷键 (Ctrl+Alt+L)")
            self.assertEqual(panel._btn_export_card.text(), "紫卡截图导出 (Ctrl+Alt+S)")
            self.assertEqual(panel._inp_hk_export.text(), "Ctrl+Alt+S")
            self.assertEqual(panel._inp_hk_lock.text(), "Ctrl+Alt+L")
            self.assertEqual(panel._chk_audio_cue.text(), "稀有属性提示音")
            self.assertEqual(panel._chk_auto_hide_focus.text(), "切出游戏自动隐藏")
            self.assertEqual(panel._chk_start_monitor.text(), "启动时自动监控")
            self.assertTrue(panel._hk_hint.isHidden())
            self.assertTrue(panel._lbl_pos_info.isHidden())
            self.assertTrue(panel._lbl_tray_tip.isHidden())
            self.assertTrue(panel._lbl_wm_status.isHidden())

            # Regression guard: formatting OCR download/ready/failed strings for ALL 15 langs (incl. zh_TW)
            # must never raise `TypeError: LocaleManager.t() got multiple values for argument 'lang'`!
            for code in SUPPORTED_LANGS:
                disp = LANGUAGE_DISPLAY_NAMES[code]
                s_dl = I18N.t("panel.ocr_model_downloading", code, lang_name=disp, pct=50)
                s_ok = I18N.t("panel.ocr_model_ready", code, lang_name=disp)
                s_err = I18N.t("panel.ocr_model_failed", code, lang_name=disp, err="timeout")
                self.assertIn(disp, s_dl)
                self.assertIn("50", s_dl)
                self.assertIn(disp, s_ok)
                self.assertIn("timeout", s_err)

            emitted_global: list[str] = []
            panel.language_changed.connect(emitted_global.append)

            overlay.set_work_mode(MODE_SINGLE_RIVEN)
            overlay.show_monitoring_waiting()

            # Switch to English, Japanese, Russian and verify 100% live translation with zero Chinese residue
            for target_lang in ("en_US", "ja_JP", "ru_RU"):
                idx = panel._cmb_lang.findData(target_lang)
                self.assertGreaterEqual(idx, 0)
                panel._cmb_lang.setCurrentIndex(idx)
                I18N.set_current_language(target_lang)
                overlay.set_language(target_lang)
                tray.refresh_state(ui_lang=target_lang, monitoring=True, current_mode=MODE_SINGLE_RIVEN)

                self.assertEqual(emitted_global[-1], target_lang)

                all_texts = _collect_visible_panel_texts(panel)
                all_texts.extend(
                    [
                        overlay._mode_badge.text(),
                        overlay._pill_label.text(),
                        overlay._footer.text(),
                        overlay._drag_banner.text(),
                        tray._act_toggle.text(),
                        tray._act_panel.text(),
                        tray._act_quit.text(),
                        tray._mode_menu.title(),
                    ]
                )
                joined = "\n".join(all_texts)

                if target_lang in ("en_US", "ru_RU"):
                    cjk_hits = _CJK_RE.findall(joined)
                    self.assertEqual(
                        cjk_hits,
                        [],
                        f"Found unexpected Chinese characters in {target_lang}: {cjk_hits}\n{joined}",
                    )
                if target_lang == "ja_JP":
                    self.assertIn("モード切替", joined)
                    self.assertIn("設定", joined)
                    for leak in _SIMPLIFIED_CHINESE_LEAK_TOKENS:
                        self.assertNotIn(leak, joined, f"Simplified Chinese leaked in ja_JP: {leak}")
                if target_lang == "ru_RU":
                    self.assertIn("Сменить режим", joined)
                    self.assertIn("Настройки", joined)
                if target_lang == "en_US":
                    self.assertEqual(panel.windowTitle(), "Riven Lens")
                    self.assertIn("Switch Mode", joined)
                    self.assertEqual(tr("panel.hk_toggle_label", "en_US"), "Switch Mode")
        finally:
            panel.close()
            overlay.close()
            tray.hide()

    def test_ocr_model_manager_on_demand_pull_and_hot_reload(self) -> None:
        """Builtin ch_PP-OCRv4 for zh/en; on-demand download + hot-reload for ja_JP & ru_RU."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = OCRModelManager(models_dir=tmpdir)

            # Builtin zh_CN & en_US need no external download
            st_zh = mgr.check_model_status("zh_CN")
            self.assertTrue(st_zh.is_builtin)
            self.assertTrue(st_zh.is_ready)
            st_en = mgr.check_model_status("en_US")
            self.assertTrue(st_en.is_builtin)
            self.assertTrue(st_en.is_ready)

            # ja_JP & ru_RU are missing before download
            self.assertFalse(mgr.check_model_status("ja_JP").is_ready)
            self.assertFalse(mgr.check_model_status("ru_RU").is_ready)

            downloaded_urls: list[str] = []

            def _fake_download(url: str, dest: Path, cb=None) -> None:
                downloaded_urls.append(url)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"FAKE_ONNX_OR_DICT_CONTENT")
                if cb:
                    cb(100)

            progress_ticks: list[int] = []
            st_ja = mgr.ensure_model(
                "ja_JP",
                progress_cb=lambda pct: progress_ticks.append(pct),
                downloader=_fake_download,
            )
            self.assertTrue(st_ja.is_ready)
            self.assertEqual(len(downloaded_urls), 2)  # rec ONNX + dict
            self.assertIsNotNone(st_ja.rec_model_path)
            self.assertIsNotNone(st_ja.rec_keys_path)
            self.assertTrue(st_ja.rec_model_path.is_file())
            self.assertTrue(st_ja.rec_keys_path.is_file())
            self.assertTrue(mgr.check_model_status("ja_JP").is_ready)
            self.assertIn(100, progress_ticks)

            # Verify RivenOCREngine hot-reloads cleanly with the downloaded language model paths
            engine = RivenOCREngine()
            with mock.patch("rapidocr_onnxruntime.RapidOCR", return_value=mock.MagicMock()) as mock_rapid:
                ok_reload = engine.reload_for_language(
                    "ja_JP",
                    rec_model_path=str(st_ja.rec_model_path),
                    rec_keys_path=str(st_ja.rec_keys_path),
                )
                self.assertTrue(ok_reload)
                self.assertEqual(engine.active_ocr_lang, "ja_JP")
                engine._ensure_engine()
                mock_rapid.assert_called_once()
                _, kwargs = mock_rapid.call_args
                self.assertEqual(kwargs.get("rec_model_path"), str(st_ja.rec_model_path.resolve()))
                self.assertEqual(kwargs.get("rec_keys_path"), str(st_ja.rec_keys_path.resolve()))

    def test_15_language_riven_parser_stat_aliases(self) -> None:
        """RivenParser normalizes Riven stats from Japanese, Russian, French, German, Korean, & Trad Chinese."""
        parser = RivenParser()
        samples = [
            (["Torid Crita-satiata", "+180.5% クリティカル率", "+110.2% マルチショット", "-45.0% ズーム"], "ja_JP"),
            (["Torid Crita-satiata", "+180.5% Шанс крита", "+110.2% Мультивыстрел", "-45.0% Зум"], "ru_RU"),
            (["Torid Crita-satiata", "+180.5% Chances de critique", "+110.2% Tir multiple"], "fr_FR"),
            (["Torid Crita-satiata", "+180.5% Krit. Chance", "+110.2% Mehrfachschuss"], "de_DE"),
            (["Torid Crita-satiata", "+180.5% 치명타 확률", "+110.2% 멀티샷"], "ko_KR"),
            (["托里德 Crita-satiata", "+180.5% 暴擊機率", "+110.2% 多重射擊"], "zh_TW"),
        ]
        for lines, expected_lang in samples:
            parsed = parser.parse(lines)
            names = [s.stat_name for s in parsed.stats]
            self.assertIn("暴击几率", names, f"Failed CC parsing for {expected_lang}: {lines}")
            self.assertIn("多重射击", names, f"Failed MS parsing for {expected_lang}: {lines}")

    def test_relic_overlay_v2_strict_4_slot_order_and_vaulted_chest_icon(self) -> None:
        """Relic cards render strict [★] -> [Plat p] -> [|] -> [Ducats D] -> [18x18 VaultedChest] with zero names."""
        chest_path = get_vaulted_chest_icon_path()
        self.assertTrue(chest_path.is_file(), f"Missing vaulted chest asset: {chest_path}")

        store = PrimePriceStore(cache_path=Path(tempfile.gettempdir()) / "__nonexistent_relic_v2__.json")
        store.load_records_in_memory(
            [
                PrimeItemPrice("1", "gauss_prime_systems", "高斯 Prime 系统", "Gauss Prime Systems", 4.7, 45, vaulted=True),
                PrimeItemPrice("2", "protea_prime_chassis", "Protea Prime 机体", "Protea Prime Chassis", 3.6, 45, vaulted=False),
                PrimeItemPrice("3", "paris_prime_string", "帕里斯 Prime 弓弦", "Paris Prime String", 1.5, 15, vaulted=False),
                PrimeItemPrice("4", "burston_prime_barrel", "伯斯顿 Prime 枪管", "Burston Prime Barrel", 2.0, 15, vaulted=True),
            ]
        )

        matches = store.match_ocr_lines(
            [
                "高斯 Prime 系统",
                "Protea Prime 机体",
                "帕里斯 Prime 弓弦",
                "伯斯顿 Prime 枪管",
            ]
        )
        self.assertEqual(len(matches), 4)
        # Rank 1 = Gauss (4.7p, vaulted), Rank 2 = Protea (3.6p, unvaulted), Normal = Paris (1.5p), Burston (2.0p, vaulted)
        self.assertEqual(matches[0].recommend_rank, 1)
        self.assertTrue(matches[0].is_vaulted)
        self.assertEqual(matches[1].recommend_rank, 2)
        self.assertFalse(matches[1].is_vaulted)
        self.assertEqual(matches[2].recommend_rank, 0)
        self.assertFalse(matches[2].is_vaulted)
        self.assertEqual(matches[3].recommend_rank, 0)
        self.assertTrue(matches[3].is_vaulted)

        relic_win = RelicRewardsOverlayWindow(click_through=True, lang="zh_CN")
        try:
            relic_win.show_relic_rewards(matches)
            self.assertEqual(relic_win.row_count, 4)

            def _card_widgets(idx: int):
                card = relic_win._row_widgets[idx]
                lay = card.layout()
                # No nested QFrame inner boxes inside any card (note: QLabel subclasses QFrame in Qt)!
                self.assertEqual([w for w in card.findChildren(QFrame) if type(w) is QFrame], [])
                ws = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i).widget() is not None]
                return card, ws

            # Card 0 (Rank 1 Gold + Vaulted): Gold border #F5C542, Gold Emblem -> 4.7 [plat icon] -> | -> 45 [ducat icon] -> VaultedChestIcon (18x18)
            c0, w0 = _card_widgets(0)
            self.assertIn("#F5C542", c0.styleSheet())
            self.assertEqual(
                [w.objectName() for w in w0],
                ["RelicRankStar", "RelicPlatLabel", "RelicPlatIcon", "RelicDivider", "RelicDucatLabel", "RelicDucatIcon", "VaultedChestIcon"],
            )
            self.assertEqual([w.text() for w in w0 if w.text()], ["4.7", "|", "45"])
            self.assertFalse(w0[0].pixmap().isNull())
            self.assertFalse(w0[2].pixmap().isNull())
            self.assertFalse(w0[5].pixmap().isNull())
            self.assertEqual((w0[6].width(), w0[6].height()), (18, 18))
            self.assertFalse(w0[6].pixmap().isNull())

            # Card 1 (Rank 2 Silver + Unvaulted): Silver border #C0C8D4, Silver Emblem -> 3.6 [plat icon] -> | -> 45 [ducat icon] (NO chest icon!)
            c1, w1 = _card_widgets(1)
            self.assertIn("#C0C8D4", c1.styleSheet())
            self.assertEqual(
                [w.objectName() for w in w1],
                ["RelicRankStar", "RelicPlatLabel", "RelicPlatIcon", "RelicDivider", "RelicDucatLabel", "RelicDucatIcon"],
            )
            self.assertEqual([w.text() for w in w1 if w.text()], ["3.6", "|", "45"])
            self.assertFalse(w1[0].pixmap().isNull())

            # Card 2 (Normal + Unvaulted): Dark border #334155, NO star, NO chest -> 1.5 [plat icon] -> | -> 15 [ducat icon]
            c2, w2 = _card_widgets(2)
            self.assertIn("#334155", c2.styleSheet())
            self.assertEqual(
                [w.objectName() for w in w2],
                ["RelicPlatLabel", "RelicPlatIcon", "RelicDivider", "RelicDucatLabel", "RelicDucatIcon"],
            )
            self.assertEqual([w.text() for w in w2 if w.text()], ["1.5", "|", "15"])

            # Card 3 (Normal + Vaulted): Dark border #334155, NO star -> 2 [plat icon] -> | -> 15 [ducat icon] -> VaultedChestIcon (18x18)
            c3, w3 = _card_widgets(3)
            self.assertIn("#334155", c3.styleSheet())
            self.assertEqual(
                [w.objectName() for w in w3],
                ["RelicPlatLabel", "RelicPlatIcon", "RelicDivider", "RelicDucatLabel", "RelicDucatIcon", "VaultedChestIcon"],
            )
            self.assertEqual([w.text() for w in w3 if w.text()], ["2", "|", "15"])
            self.assertEqual((w3[5].width(), w3[5].height()), (18, 18))

            # Verify ZERO item names and ZERO P/D text letters appear anywhere inside the relic overlay
            all_lbl_texts = " ".join(lbl.text() for lbl in relic_win.findChildren(QLabel))
            for forbidden in ("高斯", "Gauss", "Protea", "帕里斯", "Paris", "伯斯顿", "Burston", "Prime", "p", "P", "D"):
                self.assertNotIn(forbidden, all_lbl_texts)
        finally:
            relic_win.close()

    def test_standby_always_compact_pill_fusion_90pct_combos_and_stat_debounce(self) -> None:
        """Verify ModelScope OCR URLs, always-pill standby labels, >=90% fusion combos, and stat-number debounce."""
        from src.evaluator import RivenEvaluator, analyze_reroll_synergy_alert
        from src.ocr_worker import OCR_MODEL_GROUPS, RivenFrameDeduplicator
        from src.overlay_ui import EvaluatedRivenCard
        from src.roi_manager import MODE_CHAT_RIVEN, MODE_RELIC_REWARDS, MODE_REROLL

        # 1. All external OCR packs use verified official ModelScope v3.10.0 URLs (no stale HF 404 paths)
        for grp_name, spec in OCR_MODEL_GROUPS.items():
            if spec.is_builtin:
                continue
            self.assertTrue(spec.model_urls, f"Empty model_urls for {grp_name}")
            self.assertIn("modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0", spec.model_urls[0])
            self.assertTrue(spec.dict_urls, f"Empty dict_urls for {grp_name}")

        # 2. Standby / waiting / debounce / manual-standby ALWAYS renders as compact pill with exact concise text
        win = RivenOverlayWindow(click_through=True, lang="zh_CN", wm_hotkey="F9", auto_collapse_seconds=0)
        try:
            win.set_work_mode(MODE_SINGLE_RIVEN)
            win.show_monitoring_waiting()
            self.assertTrue(win.is_compact_pill)
            self.assertEqual(win._pill_label.text(), "单卡:识别中...")

            win.show_debounce_waiting(MODE_REROLL)
            self.assertTrue(win.is_compact_pill)
            self.assertEqual(win._pill_label.text(), "双卡:识别中...")

            win.show_manual_mode_standby(MODE_CHAT_RIVEN)
            self.assertTrue(win.is_compact_pill)
            self.assertEqual(win._pill_label.text(), "聊天F9抓取")

            win.show_manual_mode_standby(MODE_RELIC_REWARDS)
            self.assertTrue(win.is_compact_pill)
            self.assertEqual(win._pill_label.text(), "遗物F9抓取")
        finally:
            win.close()

        # 3. Dual-card 3+1- / 2+1- with >=90% fusion-capable stat lists all possible compound/hybrid combinations
        parser = RivenParser()
        ev = RivenEvaluator()
        left_r = parser.parse(["托里德 Sati-ata", "+150.0% 伤害", "+90.0% 多重射击", "段位 16 O 5"])
        # 2+1- with >=90% Toxin (+145.5% -> ~94%) and >=90% Crit Chance (+242.0% -> ~93%) on Rifle (Torid)
        right_r_90 = parser.parse([
            "托里德 Toxi-crita",
            "+145.5% 毒素伤害",
            "+242.0% 暴击几率",
            "-52.0% 变焦",
            "段位 16 O 6",
        ])
        left_card = EvaluatedRivenCard("[当前]", left_r, ev.evaluate(left_r), 0.98)
        right_card_90 = EvaluatedRivenCard("[新循环]", right_r_90, ev.evaluate(right_r_90), 0.98)

        alert = analyze_reroll_synergy_alert([left_card, right_card_90])
        self.assertIsNotNone(alert)
        # Official Warframe Riven Splicing recipes in exact format: "熔接→ {词条}{pct}%+{搭档} = {复合词条}"
        # Toxin -> Viral/Corrosive/Gas (3 recipes); Crit Chance -> Weak Point Crit Chance via Multishot/Zoom (2 recipes)
        self.assertEqual(len(alert.fusion_combos_zh), 5)
        joined_fusion = "\n".join(alert.fusion_combos_zh)
        self.assertTrue(any(line.startswith("熔接→ 毒素伤害") and "+冰冻伤害 = 病毒伤害" in line for line in alert.fusion_combos_zh))
        self.assertTrue(any(line.startswith("熔接→ 毒素伤害") and "+电击伤害 = 腐蚀伤害" in line for line in alert.fusion_combos_zh))
        self.assertTrue(any(line.startswith("熔接→ 毒素伤害") and "+火焰伤害 = 毒气伤害" in line for line in alert.fusion_combos_zh))
        self.assertTrue(any(line.startswith("熔接→ 暴击几率") and "+多重射击 = 弱点暴击几率" in line for line in alert.fusion_combos_zh))
        self.assertTrue(any(line.startswith("熔接→ 暴击几率") and "+变焦 = 弱点暴击几率" in line for line in alert.fusion_combos_zh))
        self.assertNotIn("融合双暴", joined_fusion)

        # Stats below 90% (e.g. ~85% S-grade) MUST NOT populate >=90% fusion_combos_zh
        right_r_85 = parser.parse([
            "托里德 Toxi-crita",
            "+142.0% 毒素伤害",
            "+236.0% 暴击几率",
            "-52.0% 变焦",
            "段位 16 O 6",
        ])
        alert_85 = analyze_reroll_synergy_alert([left_card, EvaluatedRivenCard("[新循环]", right_r_85, ev.evaluate(right_r_85), 0.98)])
        self.assertIsNotNone(alert_85)
        self.assertEqual(alert_85.fusion_combos_zh, [])

        # 4. Auto-OCR debounce based on stat numbers: ignore tiny jitter, unrecognizable frames, & 1-frame spikes
        dedup = RivenFrameDeduplicator(
            work_mode=MODE_SINGLE_RIVEN,
            miss_debounce=3,
            min_stats=2,
            value_jitter_tol=1.2,
            change_debounce_frames=2,
            hold_on_unrecognized=True,
        )
        base_pair = (right_r_90, ev.evaluate(right_r_90))
        jitter_r = parser.parse([
            "托里德 Toxi-crita",
            "+146.1% 毒素伤害",   # +0.6 tiny OCR jitter (<= 1.2)
            "+241.4% 暴击几率",   # -0.6 tiny OCR jitter (<= 1.2)
            "-52.3% 变焦",        # +0.3 tiny OCR jitter (<= 1.2)
            "段位 16 O 7",        # reroll count OCR flicker ignored!
        ])
        jitter_pair = (jitter_r, ev.evaluate(jitter_r))
        unrec_pair = (parser.parse(["???"]), ev.evaluate(parser.parse(["???"])))
        new_r = parser.parse([
            "托里德 Visi-satiata",
            "+185.0% 伤害",
            "+115.0% 多重射击",
            "-48.0% 变焦",
            "段位 16 O 8",
        ])
        new_pair = (new_r, ev.evaluate(new_r))

        # Initial valid card emits immediately
        self.assertEqual(dedup.process_frame([base_pair]), "emit")
        # Tiny numeric jitter -> skip_duplicate (0 refresh)
        self.assertEqual(dedup.process_frame([jitter_pair]), "skip_duplicate")
        # Unrecognizable frames -> hold_transition forever (never clears/refreshes!)
        for _ in range(5):
            self.assertEqual(dedup.process_frame([unrec_pair]), "hold_transition")
        # Single-frame transient spike -> hold_transition (debounce requires 2 matching frames)
        self.assertEqual(dedup.process_frame([new_pair]), "hold_transition")
        # Unrecognizable frame cancels transient spike
        self.assertEqual(dedup.process_frame([unrec_pair]), "hold_transition")
        # 2 consecutive stable frames of genuine new card -> debounced emit ONCE!
        self.assertEqual(dedup.process_frame([new_pair]), "hold_transition")
        self.assertEqual(dedup.process_frame([new_pair]), "emit")
        self.assertEqual(dedup.process_frame([new_pair]), "skip_duplicate")

    def test_theme_applies_to_panel_magnetic_snap_open_panel_hotkey_and_miss_collapse(self) -> None:
        """Verify 6 palettes + custom colors style BOTH panel & overlays, magnetic edge snap, Ctrl+Alt+D hotkey, and X-miss collapse."""
        from PyQt6.QtCore import QRect
        from src.companion_overlays import SNAP_SCREEN_MARGIN_PX, SNAP_SIBLING_GAP_PX, snap_window_position
        from src.evaluator import RivenEvaluator
        from src.hotkey_manager import (
            DEFAULT_OPEN_PANEL_HOTKEY,
            HOTKEY_ID_OPEN_PANEL,
            DynamicHotkeyManager,
        )
        from src.ocr_worker import RivenFrameDeduplicator
        from src.settings_manager import (
            DEFAULT_MISS_COLLAPSE_COUNT,
            MIN_PANEL_WIDTH,
            THEME_PRESET_ORDER,
            THEME_PRESETS,
            load_user_settings,
            save_user_settings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "user_settings.json"
            s = UserSettings()
            self.assertEqual(s.theme_preset, "orokin_void")
            self.assertEqual(s.hotkey_open_panel, DEFAULT_OPEN_PANEL_HOTKEY)
            self.assertEqual(s.miss_collapse_count, DEFAULT_MISS_COLLAPSE_COUNT)
            self.assertEqual(s.auto_collapse_seconds, 0)
            self.assertFalse(hasattr(s, "ctrl_drag_enabled"))

            panel = RivenControlPanel(settings=s, initial_ui_lang="zh_CN")
            win = RivenOverlayWindow(click_through=True, lang="zh_CN", theme_colors=s.theme_colors)
            try:
                # Ctrl-drag & auto-collapse UI controls must be completely removed
                self.assertFalse(hasattr(panel, "_chk_ctrl_drag"))
                self.assertFalse(hasattr(panel, "_cmb_collapse"))
                self.assertFalse(hasattr(win, "set_quick_drag_active"))
                self.assertFalse(hasattr(win.relic_window, "set_quick_drag_active"))

                # Open-panel hotkey input defaults to Ctrl+Alt+D
                self.assertEqual(panel._inp_hk_open_panel.text(), "Ctrl+Alt+D")
                self.assertEqual(panel._lbl_hk_open_panel.text(), "打开设置面板")

                # 6 curated presets + custom = 7 items; stays cleanly within 600px min width
                self.assertEqual(panel._cmb_theme_preset.count(), len(THEME_PRESET_ORDER) + 1)
                self.assertLessEqual(panel._scroll_content.minimumSizeHint().width(), MIN_PANEL_WIDTH)

                theme_events: list[tuple[str, dict]] = []
                hk_all_events: list[tuple[str, str, str, str]] = []
                panel.theme_changed.connect(lambda p, c: theme_events.append((p, c)))
                panel.hotkeys_all_apply_requested.connect(lambda t, w, h, op: hk_all_events.append((t, w, h, op)))

                # Switch to Corpus Cyber Cyan preset -> styles BOTH Control Panel AND all overlays!
                cyber_idx = panel._cmb_theme_preset.findData("corpus_cyber")
                self.assertGreaterEqual(cyber_idx, 0)
                panel._cmb_theme_preset.setCurrentIndex(cyber_idx)
                self.assertEqual(theme_events[-1][0], "corpus_cyber")
                self.assertEqual(theme_events[-1][1]["border"], THEME_PRESETS["corpus_cyber"]["border"])
                self.assertIn(THEME_PRESETS["corpus_cyber"]["bg"], panel.styleSheet())
                self.assertIn(THEME_PRESETS["corpus_cyber"]["border"], panel._header_box.styleSheet())
                self.assertIn(THEME_PRESETS["corpus_cyber"]["accent"], panel._header_title.styleSheet())

                win.set_theme_colors(theme_events[-1][1])
                win.show_monitoring_waiting()
                self.assertIn("#38BDF8", win._pill_label.styleSheet())
                self.assertIn("#38BDF8", win.wm_window._wm_panel.styleSheet())

                # Pick a free custom color -> switches to "custom" and updates BOTH panel & overlays live
                panel.set_custom_color("border", "#FF00AA")
                panel.set_custom_color("accent", "#22FF88")
                self.assertEqual(theme_events[-1][0], "custom")
                self.assertEqual(theme_events[-1][1]["border"], "#FF00AA")
                self.assertIn("#FF00AA", panel._header_box.styleSheet())
                self.assertIn("#22FF88", panel._header_title.styleSheet())
                win.set_theme_colors(theme_events[-1][1])
                self.assertIn("#FF00AA", win._pill_label.styleSheet())

                # Customize open-panel hotkey & verify persistence
                panel._inp_hk_open_panel.setText("Ctrl+Shift+P")
                panel._on_apply_hotkeys_clicked()
                self.assertEqual(hk_all_events[-1], ("F8", "F9", "ESC", "Ctrl+Shift+P"))
                panel._settings.hotkey_open_panel = "Ctrl+Shift+P"
                save_user_settings(panel._settings, path=cfg_path)
                reloaded = load_user_settings(path=cfg_path)
                self.assertEqual(reloaded.hotkey_open_panel, "Ctrl+Shift+P")
                self.assertEqual(reloaded.theme_preset, "custom")
                self.assertEqual(reloaded.theme_colors["border"], "#FF00AA")

                # Verify DynamicHotkeyManager validates & dispatches open_panel_triggered even when locked
                hk_mgr = DynamicHotkeyManager()
                open_hits: list[str] = []
                hk_mgr.open_panel_triggered.connect(open_hits.append)
                # Duplicate check with toggle key must fail cleanly
                dup_rep = hk_mgr.apply_hotkeys("Ctrl+Alt+D", "F9", "ESC", "Ctrl+Alt+D")
                self.assertFalse(dup_rep.ok)
                # Valid 4-hotkey dispatch still triggers open_panel even if F8/F9 are locked
                hk_mgr.set_hotkeys_locked(True)
                hk_mgr._dispatch_hotkey(HOTKEY_ID_OPEN_PANEL, "UnitTest")
                self.assertEqual(open_hits, ["UnitTest"])
                hk_mgr.unregister_all()
            finally:
                panel.close()
                win.close()

        # Verify Plan C: Edge Magnetic Snapping (screen edges, horizontal center, sibling edges)
        screen = QRect(0, 0, 1920, 1080)
        # 1. Near screen left & top edge (within 16px) -> snaps to margin (8, 8)
        sx, sy = snap_window_position(18, 16, 300, 200, screen)
        self.assertEqual((sx, sy), (SNAP_SCREEN_MARGIN_PX, SNAP_SCREEN_MARGIN_PX))
        # 2. Near screen right & bottom edge -> snaps to (1920 - 8 - 300, 1080 - 8 - 200)
        sx, sy = snap_window_position(1920 - 8 - 300 - 10, 1080 - 8 - 200 - 12, 300, 200, screen)
        self.assertEqual((sx, sy), (1920 - SNAP_SCREEN_MARGIN_PX - 300, 1080 - SNAP_SCREEN_MARGIN_PX - 200))
        # 3. Near screen horizontal center -> snaps to exact center
        center_x = (1920 - 300) // 2
        sx, sy = snap_window_position(center_x + 11, 300, 300, 200, screen)
        self.assertEqual((sx, sy), (center_x, 300))
        # 4. Near sibling overlay right edge -> snaps neatly beside it with 6px gap & aligned top
        sibling = QRect(500, 240, 320, 220)
        sx, sy = snap_window_position(sibling.right() + 1 + 14, 248, 300, 200, screen, sibling_rects=[sibling])
        self.assertEqual((sx, sy), (sibling.right() + 1 + SNAP_SIBLING_GAP_PX, sibling.top()))
        # 5. Far from all edges (> 16px) -> stays at exact dragged coordinate
        sx, sy = snap_window_position(220, 320, 300, 200, screen, sibling_rects=[sibling])
        self.assertEqual((sx, sy), (220, 320))

        # Verify X consecutive undetected frames collapse overlay back into compact standby pill
        parser = RivenParser()
        ev = RivenEvaluator()
        valid_r = parser.parse(["托里德 Visi-satiata", "+185.0% 伤害", "+115.0% 多重射击", "-48.0% 变焦", "段位 16 O 8"])
        valid_pair = (valid_r, ev.evaluate(valid_r))
        empty_pair = (parser.parse(["???"]), ev.evaluate(parser.parse(["???"])))
        dedup_collapse = RivenFrameDeduplicator(
            work_mode=MODE_SINGLE_RIVEN,
            miss_debounce=4,
            min_stats=2,
            value_jitter_tol=1.2,
            change_debounce_frames=2,
            hold_on_unrecognized=False,
        )
        self.assertEqual(dedup_collapse.process_frame([valid_pair]), "emit")
        self.assertEqual(dedup_collapse.process_frame([empty_pair]), "hold_transition")
        self.assertEqual(dedup_collapse.process_frame([empty_pair]), "hold_transition")
        self.assertEqual(dedup_collapse.process_frame([empty_pair]), "hold_transition")
        # 4th consecutive undetected frame triggers waiting -> collapses to compact pill!
        self.assertEqual(dedup_collapse.process_frame([empty_pair]), "waiting")

    def test_real_world_ocr_typos_glued_prevention_missing_minus_and_relic_wrap(self) -> None:
        """Regression tests from real 1080p Warframe screenshots (Riven typos, %6 artifact, dropped '-', Relic wrap)."""
        from src.ocr_engine import OCRBox

        parser = RivenParser()
        # 1. Chat Riven with 1-char OCR typos ("暴去几率"/"安击几率", "弹匝容量"), "%6" artifact, and dropped '-'
        chat_r = parser.parse([
            "18K",
            "赤毒·布拉玛",
            "Parcicron",
            "+214.3%安击几率",
            "+70.9%6弹药效率",
            "28.7%弹匝容量",
            "段位16 0105",
        ])
        self.assertTrue(chat_r.is_valid, msg=chat_r.validation_error)
        self.assertEqual(chat_r.weapon_name, "赤毒·布拉玛")
        self.assertEqual([(s.stat_name, s.value, s.is_negative) for s in chat_r.stats], [
            ("暴击几率", 214.3, False),
            ("弹药效率", 70.9, False),
            ("弹匣容量", 28.7, True),
        ])

        # Exact live-screen typo "暴去几率"
        chat_r2 = parser.parse([
            "赤毒·布拉玛",
            "Parcicron",
            "+214.3% 暴去几率",
            "+70.9% 弹药效率",
            "-28.7% 弹匣容量",
            "段位 16",
        ])
        self.assertTrue(chat_r2.is_valid, msg=chat_r2.validation_error)
        self.assertEqual(chat_r2.stats[0].stat_name, "暴击几率")

        # 2. Single Riven with locked stat ("8+55.7%弹药效率") and multiplicative faction negative
        single_r = parser.parse([
            "18r",
            "安培克斯 Parci-",
            "critadex",
            "+183.3%暴击几率",
            "8+55.7%弹药效率",
            "+107.2%触发几率",
            "x0.62 对 Corpus 的伤害",
            "段位10 O47",
        ])
        self.assertTrue(single_r.is_valid, msg=single_r.validation_error)
        self.assertEqual(single_r.weapon_name, "安培克斯")
        self.assertEqual(len(single_r.positive_stats), 3)
        self.assertEqual(len(single_r.negative_stats), 1)
        self.assertTrue(single_r.stats[1].is_locked)

        # 3. Void Relic with "已制造" / "19 已拥有" badges & 2-line wrapped "Lavos Prime 机体 蓝" + "图"
        store = PrimePriceStore(cache_path=Path(tempfile.gettempdir()) / "__nonexistent_relic_wrap__.json")
        store.load_records_in_memory([
            PrimeItemPrice("1", "caliban_prime_blueprint", "Caliban Prime 蓝图", "Caliban Prime Blueprint", 3.6, 45),
            PrimeItemPrice("2", "orthos_prime_blade", "欧特鲁斯 Prime 刀刃", "Orthos Prime Blade", 4.7, 45),
            PrimeItemPrice("3", "paris_prime_lower_limb", "帕里斯 Prime 下弓臂", "Paris Prime Lower Limb", 2.3, 15),
            PrimeItemPrice("4", "lavos_prime_chassis_blueprint", "Lavos Prime 机体 蓝图", "Lavos Prime Chassis Blueprint", 2.9, 15),
        ])
        boxes = [
            OCRBox("已制造", 0.99, [[50, 10], [130, 10], [130, 35], [50, 35]]),
            OCRBox("CalibanPrime蓝图", 0.99, [[30, 220], [230, 220], [230, 250], [30, 250]]),
            OCRBox("欧特鲁斯 Prime 刀刃", 0.99, [[280, 220], [480, 220], [480, 250], [280, 250]]),
            OCRBox("帕里斯Prime下弓臂", 0.99, [[530, 220], [730, 220], [730, 250], [530, 250]]),
            OCRBox("19已拥有", 0.99, [[780, 10], [900, 10], [900, 35], [780, 35]]),
            OCRBox("Lavos Prime机体蓝", 0.99, [[770, 210], [980, 210], [980, 240], [770, 240]]),
        ]
        matches = store.match_ocr_boxes(boxes, image_width=1001)
        self.assertEqual(len(matches), 4)
        self.assertTrue(all(m.is_matched for m in matches))
        self.assertNotIn("已制造", matches[0].raw_text)
        self.assertEqual(matches[3].matched_zh, "Lavos Prime 机体 蓝图")
        self.assertEqual(matches[3].plat_price, 2.9)
        self.assertEqual(matches[3].ducats, 15)

        # 4. WFCD Relics.json vaulted status aggregation (part is vaulted iff ALL holding Intact relics are vaulted)
        from src.relic_prices import build_prime_price_records, build_vaulted_part_map_from_relics

        fake_relics = [
            {
                "name": "Axi D5 Intact",
                "vaulted": True,
                "drops": [],
                "rewards": [{"item": {"name": "Akarius Prime Barrel"}}, {"item": {"name": "Forma Blueprint"}}],
            },
            {
                "name": "Lith F2 Intact",
                "vaulted": True,
                "drops": [],
                "rewards": [{"item": {"name": "Odonata Prime Systems Blueprint"}}],
            },
            {
                "name": "Meso V11 Intact",
                "vaulted": True,
                "drops": [],
                "rewards": [{"item": {"name": "Caliban Prime Blueprint"}}],
            },
            {
                "name": "Lith V11 Intact",
                "vaulted": False,
                "drops": [{"location": "Hepit"}],
                "rewards": [{"item": {"name": "Caliban Prime Blueprint"}}],
            },
        ]
        vmap = build_vaulted_part_map_from_relics(fake_relics)
        self.assertTrue(vmap["akariusprimebarrel"])
        self.assertTrue(vmap["odonataprimesystemsblueprint"])
        self.assertFalse(vmap["calibanprimeblueprint"])

        v2_items = [
            {"id": "a1", "slug": "akarius_prime_barrel", "tags": ["prime", "component"], "ducats": 15, "i18n": {"zh-hans": {"name": "阿利乌双枪 Prime 枪管"}, "en": {"name": "Akarius Prime Barrel"}}},
            {"id": "c1", "slug": "caliban_prime_blueprint", "tags": ["prime", "blueprint"], "ducats": 45, "i18n": {"zh-hans": {"name": "Caliban Prime 蓝图"}, "en": {"name": "Caliban Prime Blueprint"}}},
        ]
        duc_payload = {"previous_day": [{"item": "a1", "wa_price": 9.6, "ducats": 15}, {"item": "c1", "wa_price": 3.6, "ducats": 45}]}
        recs = {r.slug: r for r in build_prime_price_records(v2_items, duc_payload, vaulted_part_map=vmap)}
        self.assertTrue(recs["akarius_prime_barrel"].vaulted)
        self.assertFalse(recs["caliban_prime_blueprint"].vaulted)

    def test_rapid_qthread_retire_and_concurrent_ocr_lock_safety(self) -> None:
        """Stress-test non-blocking QThreadRetainer (zero wait stutter / zero destroyed-while-running crash) & serialized OCR lock."""
        import threading
        import time
        import numpy as np
        from PyQt6.QtCore import QCoreApplication, QThread
        from src.ocr_worker import QThreadRetainer

        class _SlowDummyWorker(QThread):
            def __init__(self) -> None:
                super().__init__()
                self.stop_ev = threading.Event()

            def stop(self) -> None:
                self.stop_ev.set()

            def run(self) -> None:
                # Simulate slow in-flight work that takes ~40ms even after stop signal
                time.sleep(0.04)

        retainer = QThreadRetainer()
        t0 = time.perf_counter()
        for _ in range(15):
            w = _SlowDummyWorker()
            retainer.track(w)
            w.start()
            # Retire immediately (non-blocking — must NOT wait 40ms * 15 = 600ms!)
            retainer.retire(w, stop_fn=w.stop)
            del w
        retire_elapsed = time.perf_counter() - t0
        self.assertLess(retire_elapsed, 0.35, f"Non-blocking retire took too long: {retire_elapsed:.3f}s")
        self.assertGreater(retainer.active_count, 0)

        # Wait cleanly and pump Qt events so finished signals release every retained thread
        retainer.wait_all(timeout_ms=1500)
        for _ in range(5):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(retainer.active_count, 0)

        # Regression guard: after a tracked worker finishes & deleteLater() deletes its C++ object,
        # checking QThreadRetainer.is_running(stale_ref) MUST return False (never raise RuntimeError)
        # and on_finished callback must clear the state slot cleanly for subsequent F9 captures!
        slot: dict[str, object] = {"manual_worker": None}
        for _ in range(3):
            self.assertFalse(QThreadRetainer.is_running(slot["manual_worker"]))  # type: ignore[arg-type]
            w_once = _SlowDummyWorker()
            slot["manual_worker"] = w_once
            retainer.track(
                w_once,
                on_finished=lambda target=w_once: slot.__setitem__("manual_worker", None)
                if slot.get("manual_worker") is target
                else None,
            )
            w_once.start()
            w_once.wait(500)
            for _ in range(5):
                QCoreApplication.processEvents()
                time.sleep(0.01)
            self.assertIsNone(slot["manual_worker"])
            # Even if caller kept a stale reference to the deleted C++ QThread, is_running() is safe!
            self.assertFalse(QThreadRetainer.is_running(w_once))

        # Verify RivenOCREngine serializes concurrent recognize() & reload_for_language() calls cleanly
        active_inside = 0
        max_concurrent = 0
        probe_lock = threading.Lock()

        def _fake_engine_call(_img):
            nonlocal active_inside, max_concurrent
            with probe_lock:
                active_inside += 1
                max_concurrent = max(max_concurrent, active_inside)
            time.sleep(0.02)
            with probe_lock:
                active_inside -= 1
            return ([[[[0, 0], [50, 0], [50, 20], [0, 20]], "+120% 伤害", 0.99]], None)

        eng = RivenOCREngine(preprocess_mode="raw")
        eng._engine = _fake_engine_call
        dummy_img = np.zeros((100, 200, 3), dtype=np.uint8)
        errors: list[Exception] = []

        def _worker_ocr():
            try:
                res = eng.recognize(dummy_img, fallback_to_raw=False)
                self.assertEqual(res.lines, ["+120% 伤害"])
            except Exception as exc:
                errors.append(exc)

        ths = [threading.Thread(target=_worker_ocr) for _ in range(4)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(max_concurrent, 1, "RapidOCR inference must never execute concurrently on two threads")

    def test_user_manual_first_run_flag_panel_button_and_live_theme_sync(self) -> None:
        """Verify first-run manual persistence, Control Panel '用户手册' button, and themed multilingual dialog."""
        from src.settings_manager import THEME_PRESETS, load_user_settings, save_user_settings
        from src.user_manual_dialog import UserManualDialog

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "user_settings.json"
            s = UserSettings()
            self.assertFalse(s.first_run_guide_shown)

            panel = RivenControlPanel(settings=s, initial_ui_lang="zh_CN")
            dlg = UserManualDialog(settings=s, lang="zh_CN")
            try:
                # Button exists, labeled "用户手册", and emits user_manual_requested when clicked
                self.assertEqual(panel._btn_user_manual.text(), "用户手册")
                clicks: list[bool] = []
                panel.user_manual_requested.connect(lambda: clicks.append(True))
                panel._btn_user_manual.click()
                self.assertEqual(clicks, [True])

                # Manual dialog shows Chinese guide with current custom hotkeys
                self.assertEqual(dlg.windowTitle(), "裂罅透镜 · 用户手册")
                self.assertIn("Ctrl+Alt+D", dlg._browser.toHtml())
                self.assertIn("边缘磁吸对齐", dlg._browser.toPlainText())

                # Live language switch to English -> zero Chinese residue in manual & button
                panel.retranslate_ui("en_US")
                dlg.set_language("en_US")
                self.assertEqual(panel._btn_user_manual.text(), "User Manual")
                self.assertEqual(dlg.windowTitle(), "Riven Lens · User Manual")
                self.assertEqual(_CJK_RE.findall(dlg._browser.toPlainText()), [])

                # Live theme & hotkey sync
                dlg.set_theme_colors(THEME_PRESETS["corpus_cyber"])
                dlg.refresh_hotkeys("F6", "F7", "ESC", "Ctrl+Shift+M")
                self.assertIn(THEME_PRESETS["corpus_cyber"]["bg"], dlg.styleSheet())
                self.assertIn("Ctrl+Shift+M", dlg._browser.toPlainText())

                # Persist first_run_guide_shown=True so subsequent launches do not auto-pop
                s.first_run_guide_shown = True
                save_user_settings(s, path=cfg_path)
                reloaded = load_user_settings(path=cfg_path)
                self.assertTrue(reloaded.first_run_guide_shown)
            finally:
                dlg.close()
                panel.close()


if __name__ == "__main__":
    unittest.main()
