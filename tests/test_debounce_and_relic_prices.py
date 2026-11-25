"""Tests for 2-second auto-OCR mode debounce, fixed 1.2 disposition, and Void Relic Prime pricing."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from src.ocr_worker import DEFAULT_AUTO_OCR_DEBOUNCE_MS, AutoOCRModeDebouncer
from src.overlay_ui import RivenOverlayWindow
from src.relic_prices import (
    PrimeItemPrice,
    PrimePriceStore,
    build_prime_price_records,
)
from src.roi_manager import MODE_REROLL, MODE_SINGLE_RIVEN


def _wait_qt(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class TestAutoOCRModeDebouncer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_default_delay_is_2000ms(self) -> None:
        self.assertEqual(DEFAULT_AUTO_OCR_DEBOUNCE_MS, 2000)
        d = AutoOCRModeDebouncer()
        self.assertEqual(d.delay_ms, 2000)

    def test_rapid_f8_skip_cancels_timer_and_fires_zero_ocr(self) -> None:
        """Skipping single_riven -> reroll -> cancel within debounce window MUST fire 0 times."""
        debouncer = AutoOCRModeDebouncer(delay_ms=120)
        triggered = []
        debouncer.debounced_ready.connect(lambda m: triggered.append(m))

        debouncer.schedule(MODE_SINGLE_RIVEN)
        self.assertTrue(debouncer.is_pending)
        self.assertEqual(debouncer.pending_mode, MODE_SINGLE_RIVEN)

        _wait_qt(40)
        # User quickly presses F8 again to reroll, then skips straight to chat_riven/paused (cancel)
        debouncer.schedule(MODE_REROLL)
        self.assertEqual(debouncer.pending_mode, MODE_REROLL)
        _wait_qt(40)
        debouncer.cancel()
        self.assertFalse(debouncer.is_pending)

        # Wait well beyond debounce duration -> MUST NOT have triggered any OCR
        _wait_qt(180)
        self.assertEqual(triggered, [])

    def test_staying_in_mode_past_debounce_triggers_once(self) -> None:
        debouncer = AutoOCRModeDebouncer(delay_ms=90)
        triggered = []
        debouncer.debounced_ready.connect(lambda m: triggered.append(m))

        debouncer.schedule(MODE_SINGLE_RIVEN)
        _wait_qt(30)
        self.assertEqual(triggered, [])  # Still waiting

        _wait_qt(110)
        self.assertEqual(triggered, [MODE_SINGLE_RIVEN])
        self.assertFalse(debouncer.is_pending)

    def test_overlay_debounce_badge_transitions_to_active(self) -> None:
        win = RivenOverlayWindow(click_through=True, lang="zh_CN")
        win.show_debounce_waiting(MODE_SINGLE_RIVEN)
        self.assertEqual(win.display_mode, "debounce")
        self.assertIn("2秒后开启自动识别", win._mode_badge.text())

        win.set_work_mode(MODE_SINGLE_RIVEN, pending_debounce=False)
        self.assertIn("自动识别中", win._mode_badge.text())
        win.hide()


class TestVoidRelicPrimePricing(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_build_prime_price_records_joins_v2_items_and_v1_ducats(self) -> None:
        v2_items = [
            {
                "id": "id_set",
                "slug": "gauss_prime_set",
                "tags": ["prime", "set", "warframe"],
                "ducats": 175,
                "i18n": {"zh-hans": {"name": "高斯 Prime 一套"}, "en": {"name": "Gauss Prime Set"}},
            },
            {
                "id": "id_sys",
                "slug": "gauss_prime_systems_blueprint",
                "tags": ["prime", "component", "blueprint"],
                "ducats": 100,
                "i18n": {
                    "zh-hans": {"name": "高斯 Prime 系统 蓝图"},
                    "en": {"name": "Gauss Prime Systems Blueprint"},
                },
            },
            {
                "id": "id_bar",
                "slug": "boar_prime_barrel",
                "tags": ["prime", "component"],
                "ducats": 45,
                "i18n": {
                    "zh-hans": {"name": "野猪 Prime 枪管"},
                    "en": {"name": "Boar Prime Barrel"},
                },
            },
        ]
        ducats_payload = {
            "previous_day": [
                {"item": "id_sys", "wa_price": 42.5, "median": 40, "ducats": 100, "volume": 120},
                {"item": "id_bar", "wa_price": 19.0, "median": 18, "ducats": 45, "volume": 60},
            ]
        }
        recs = build_prime_price_records(v2_items, ducats_payload)
        # Sets are excluded; only individual Prime drop parts remain
        self.assertEqual(len(recs), 2)
        by_slug = {r.slug: r for r in recs}
        self.assertEqual(by_slug["gauss_prime_systems_blueprint"].plat_price, 42.5)
        self.assertEqual(by_slug["gauss_prime_systems_blueprint"].ducats, 100)
        self.assertEqual(by_slug["boar_prime_barrel"].plat_price, 19.0)
        self.assertEqual(by_slug["boar_prime_barrel"].ducats, 45)

    def test_fast_memory_lookup_forma_wrapped_lines_and_best_highlight(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PrimePriceStore(cache_path=Path(tmp) / "prime_prices.json")
            store.load_records_in_memory(
                [
                    PrimeItemPrice("1", "gauss_prime_systems_blueprint", "高斯 Prime 系统 蓝图", "Gauss Prime Systems Blueprint", 48.0, 100, 90),
                    PrimeItemPrice("2", "boar_prime_barrel", "野猪 Prime 枪管", "Boar Prime Barrel", 19.5, 45, 55),
                    PrimeItemPrice("3", "paris_prime_string", "帕里斯 Prime 弓弦", "Paris Prime String", 4.0, 15, 200),
                ]
            )

            # Simulate 4-choice OCR output with wrapped 2-line reward, Forma, and UI noise
            ocr_lines = [
                "选择奖励",
                "帕里斯 Prime 弓弦",
                "Forma 蓝图",
                "高斯 Prime",
                "系统 蓝图",
                "野猪 Prime 枪管",
                "已拥有",
            ]
            t0 = time.perf_counter()
            matches = store.match_ocr_lines(ocr_lines)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertLess(elapsed_ms, 50.0)  # Sub-millisecond local lookup
            self.assertEqual(len(matches), 4)

            by_zh = {m.matched_zh: m for m in matches}
            self.assertTrue(by_zh["Forma 蓝图"].is_forma)
            self.assertIsNone(by_zh["Forma 蓝图"].plat_price)

            self.assertEqual(by_zh["高斯 Prime 系统 蓝图"].plat_price, 48.0)
            self.assertEqual(by_zh["高斯 Prime 系统 蓝图"].ducats, 100)
            self.assertTrue(by_zh["高斯 Prime 系统 蓝图"].is_best_plat)

            self.assertFalse(by_zh["野猪 Prime 枪管"].is_best_plat)
            self.assertFalse(by_zh["帕里斯 Prime 弓弦"].is_best_plat)

            # Render cleanly in overlay UI
            win = RivenOverlayWindow(click_through=True, lang="zh_CN")
            win.show_relic_rewards(matches, elapsed_ms=180.0)
            self.assertEqual(win.display_mode, "relic")
            self.assertGreater(win.height(), 180)
            win.hide()

    def test_cached_local_prime_prices_file_loads_and_matches(self) -> None:
        """Verify live-synced `data/prime_prices.json` exists and matches real Prime parts."""
        store = PrimePriceStore()
        self.assertGreater(len(store.items), 400)
        hit = store.lookup_single("野猪 Prime 枪管")
        self.assertTrue(hit.is_matched)
        self.assertGreater(hit.ducats, 0)
        self.assertIsNotNone(hit.plat_price)


if __name__ == "__main__":
    unittest.main()
