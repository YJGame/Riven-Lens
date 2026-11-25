"""Unit tests for structured Data-Signature deduplication & animation-transition filtering.

Verifies:
  1. `reroll` mode: 5 consecutive dual-card frames with identical structured data
     (despite OCR spacing/punctuation noise, suffix flicker, or stat line reordering)
     trigger UI rendering EXACTLY ONCE (4 blocked -> 0 redundant redraws).
  2. `reroll` mode: genuine stat type, value, or reroll-count change on right/left
     card triggers exactly 1 UI update.
  3. `reroll` mode: incomplete mid-animation transition frames (< 2 valid stats or
     missing weapon name on either side) are silently ignored while preserving the
     previous complete dual-card display (0 flicker, 0 premature waiting switch).
  4. `single_riven` mode: identical structured data signature dedup + transition hold.
  5. `RivenOverlayWindow.update_cards()`: zero `_clear_cards()` / zero `apply_position()`
     calls on duplicate data signatures.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.evaluator import (
    RivenEvaluator,
    build_card_data_signature,
    build_reroll_data_signature,
    is_complete_riven_frame,
)
from src.ocr_worker import RivenFrameDeduplicator
from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
from src.riven_parser import RivenData, RivenParser, RivenStat
from src.roi_manager import MODE_REROLL, MODE_SINGLE_RIVEN


class TestRerollFingerprintDedup(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.parser = RivenParser()
        self.evaluator = RivenEvaluator()

    def _parse_and_eval(self, lines: list[str]):
        riven = self.parser.parse(lines)
        ev = self.evaluator.evaluate(riven)
        return riven, ev

    def _make_card(self, label: str, lines: list[str]) -> EvaluatedRivenCard:
        riven, ev = self._parse_and_eval(lines)
        return EvaluatedRivenCard(label=label, riven=riven, evaluation=ev, avg_confidence=0.96)

    def test_five_noisy_duplicate_reroll_frames_render_only_once(self) -> None:
        """5 consecutive dual-card frames with OCR spacing/order noise -> 1 render, 4 blocked."""
        dedup = RivenFrameDeduplicator(work_mode=MODE_REROLL, miss_debounce=3, min_stats=2)

        # 5 frames representing the EXACT SAME left & right cards with OCR order/space/suffix jitter
        noisy_frames = [
            # Frame 1: clean
            (
                [
                    "托里德 Crita-acrican",
                    "+196.4% 暴击几率",
                    "+156.8% 暴击伤害",
                    "+118.2% 多重射击",
                    "-58.6% 变焦",
                    "段位 16 O 12",
                ],
                [
                    "托里德 Sati-visicron",
                    "+185.0% 伤害",
                    "+112.5% 多重射击",
                    "+142.0% 暴击几率",
                    "-49.5% 冲击伤害",
                    "段位 16 O 13",
                ],
            ),
            # Frame 2: extra whitespace & punctuation noise in OCR
            (
                [
                    "托 里 德  Crita - acrican.",
                    "+ 196.4 %  暴击几率",
                    "+ 156.8 %  暴击伤害 ",
                    "+ 118.2 %  多重射击",
                    "- 58.6 %  变焦",
                    "段位 16  12",
                ],
                [
                    "托 里 德  Sati-visicron!",
                    "+ 185.0 %  伤害",
                    "+ 112.5 %  多重射击",
                    "+ 142.0 %  暴击几率",
                    "- 49.5 %  冲击伤害",
                    "段位 16  13",
                ],
            ),
            # Frame 3: stats in reversed / permuted OCR order
            (
                [
                    "托里德 Crita-acrican",
                    "-58.6% 变焦",
                    "+118.2% 多重射击",
                    "+156.8% 暴击伤害",
                    "+196.4% 暴击几率",
                    "段位 16 O 12",
                ],
                [
                    "托里德 Sati-visicron",
                    "-49.5% 冲击伤害",
                    "+142.0% 暴击几率",
                    "+112.5% 多重射击",
                    "+185.0% 伤害",
                    "段位 16 O 13",
                ],
            ),
            # Frame 4: different suffix OCR glitch + mastery rank OCR flicker
            (
                [
                    "托里德 Cr1ta-acr1can",
                    "+156.8% 暴击伤害",
                    "+196.4% 暴击几率",
                    "-58.6% 变焦",
                    "+118.2% 多重射击",
                    "段位 15 O 12",
                ],
                [
                    "托里德 Satl-vlsicron",
                    "+112.5% 多重射击",
                    "+185.0% 伤害",
                    "-49.5% 冲击伤害",
                    "+142.0% 暴击几率",
                    "段位 15 O 13",
                ],
            ),
            # Frame 5: rounding-invariant formatting (+196.40% -> round(196.4, 1))
            (
                [
                    "托里德 Crita-acrican",
                    "+196.40% 暴击几率",
                    "+118.20% 多重射击",
                    "+156.80% 暴击伤害",
                    "-58.60% 变焦",
                    "段位 16 O 12",
                ],
                [
                    "托里德 Sati-visicron",
                    "+185.00% 伤害",
                    "+142.00% 暴击几率",
                    "+112.50% 多重射击",
                    "-49.50% 冲击伤害",
                    "段位 16 O 13",
                ],
            ),
        ]

        render_calls = 0
        decisions: list[str] = []
        sigs = []

        for left_lines, right_lines in noisy_frames:
            left_pair = self._parse_and_eval(left_lines)
            right_pair = self._parse_and_eval(right_lines)
            sigs.append(build_reroll_data_signature(left_pair, right_pair))
            decision = dedup.process_frame([left_pair, right_pair])
            decisions.append(decision)
            if decision == "emit":
                render_calls += 1

        # All 5 frames must produce the EXACT same structured reroll signature
        for s in sigs[1:]:
            self.assertEqual(sigs[0], s)

        self.assertEqual(decisions, ["emit", "skip_duplicate", "skip_duplicate", "skip_duplicate", "skip_duplicate"])
        self.assertEqual(render_calls, 1)

    def test_reroll_right_card_change_and_transition_frame_filtering(self) -> None:
        """Genuine right-card change renders once; <2-stat or nameless transition frames keep old UI."""
        dedup = RivenFrameDeduplicator(work_mode=MODE_REROLL, miss_debounce=3, min_stats=2)

        left_pair = self._parse_and_eval([
            "托里德 Crita-acrican",
            "+196.4% 暴击几率",
            "+156.8% 暴击伤害",
            "+118.2% 多重射击",
            "-58.6% 变焦",
            "段位 16 O 12",
        ])
        right_v1 = self._parse_and_eval([
            "托里德 Sati-visicron",
            "+185.0% 伤害",
            "+112.5% 多重射击",
            "-49.5% 冲击伤害",
            "段位 16 O 13",
        ])
        right_v2_new_roll = self._parse_and_eval([
            "托里德 Igni-critatis",
            "+175.2% 暴击几率",
            "+138.4% 暴击伤害",
            "+96.8% 火焰伤害",
            "-52.1% 变焦",
            "段位 16 O 14",
        ])

        render_calls = 0
        waiting_calls = 0

        def feed(pairs) -> str:
            nonlocal render_calls, waiting_calls
            d = dedup.process_frame(pairs)
            if d == "emit":
                render_calls += 1
            elif d == "waiting":
                waiting_calls += 1
            return d

        # 1. Initial complete dual frame -> renders once
        self.assertEqual(feed([left_pair, right_v1]), "emit")
        self.assertEqual(render_calls, 1)
        saved_sig = dedup.last_rendered_reroll_signature
        self.assertIsNotNone(saved_sig)

        # 2. Transition frame A: right card mid-flip with only 1 stat (< 2)
        incomplete_1stat = RivenData(
            weapon_name="托里德",
            stats=[RivenStat("暴击几率", "暴击几率", 175.2, 175.2, "+", "%", False, "+175.2% 暴击几率")],
            is_valid=True,
        )
        incomplete_1stat_ev = self.evaluator.evaluate(incomplete_1stat)
        self.assertFalse(is_complete_riven_frame(incomplete_1stat, incomplete_1stat_ev, min_stats=2))
        self.assertEqual(feed([left_pair, (incomplete_1stat, incomplete_1stat_ev)]), "hold_transition")
        self.assertEqual(render_calls, 1)
        self.assertEqual(waiting_calls, 0)
        self.assertEqual(dedup.last_rendered_reroll_signature, saved_sig)

        # 3. Transition frame B: right card weapon name empty during flip
        nameless_card = RivenData(
            weapon_name=None,
            stats=[
                RivenStat("暴击几率", "暴击几率", 175.2, 175.2, "+", "%", False, "+175.2% 暴击几率"),
                RivenStat("暴击伤害", "暴击伤害", 138.4, 138.4, "+", "%", False, "+138.4% 暴击伤害"),
            ],
            is_valid=True,
        )
        nameless_ev = self.evaluator.evaluate(nameless_card)
        self.assertFalse(is_complete_riven_frame(nameless_card, nameless_ev, min_stats=2))
        self.assertEqual(feed([left_pair, (nameless_card, nameless_ev)]), "hold_transition")
        self.assertEqual(render_calls, 1)
        self.assertEqual(waiting_calls, 0)
        self.assertEqual(dedup.last_rendered_reroll_signature, saved_sig)

        # 4. Transition frame C: right card completely blank while left card still visible
        blank_right = self._parse_and_eval([])
        for _ in range(5):
            self.assertEqual(feed([left_pair, blank_right]), "hold_transition")
        self.assertEqual(render_calls, 1)
        self.assertEqual(waiting_calls, 0)
        self.assertEqual(dedup.last_rendered_reroll_signature, saved_sig)

        # 5. Flip completes with genuinely new right card -> triggers 1 smooth UI update
        self.assertEqual(feed([left_pair, right_v2_new_roll]), "emit")
        self.assertEqual(render_calls, 2)
        self.assertNotEqual(dedup.last_rendered_reroll_signature, saved_sig)

        # 6. Duplicate frame of the new roll -> blocked cleanly
        self.assertEqual(feed([left_pair, right_v2_new_roll]), "skip_duplicate")
        self.assertEqual(render_calls, 2)

    def test_single_riven_mode_data_signature_dedup(self) -> None:
        """`single_riven` mode uses the exact same structured data signature dedup."""
        dedup = RivenFrameDeduplicator(work_mode=MODE_SINGLE_RIVEN, miss_debounce=3, min_stats=2)

        f1 = self._parse_and_eval([
            "托里德 Crita-acrican",
            "+196.4% 暴击几率",
            "+156.8% 暴击伤害",
            "-58.6% 变焦",
            "段位 16 O 12",
        ])
        f2_reordered_noisy = self._parse_and_eval([
            "托 里 德  Crita - acrican",
            "- 58.6 %  变焦",
            "+ 156.8 %  暴击伤害",
            "+ 196.4 %  暴击几率",
            "段位 16  12",
        ])
        f3_changed_value = self._parse_and_eval([
            "托里德 Crita-acrican",
            "+199.1% 暴击几率",
            "+156.8% 暴击伤害",
            "-58.6% 变焦",
            "段位 16 O 12",
        ])

        self.assertEqual(build_card_data_signature(*f1), build_card_data_signature(*f2_reordered_noisy))
        self.assertNotEqual(build_card_data_signature(*f1), build_card_data_signature(*f3_changed_value))

        self.assertEqual(dedup.process_frame([f1]), "emit")
        self.assertEqual(dedup.process_frame([f2_reordered_noisy]), "skip_duplicate")
        self.assertEqual(dedup.process_frame([f2_reordered_noisy]), "skip_duplicate")
        self.assertEqual(dedup.process_frame([f3_changed_value]), "emit")

    def test_overlay_window_update_cards_skips_redraw_and_reposition_on_same_signature(self) -> None:
        """Even if `window.update_cards` is called repeatedly with identical data, 0 redraws / 0 moves occur."""
        win = RivenOverlayWindow(click_through=True)
        try:
            left_a = self._make_card(
                "[当前]",
                [
                    "托里德 Crita-acrican",
                    "+196.4% 暴击几率",
                    "+156.8% 暴击伤害",
                    "+118.2% 多重射击",
                    "-58.6% 变焦",
                    "段位 16 O 12",
                ],
            )
            right_a = self._make_card(
                "[新循环]",
                [
                    "托里德 Sati-visicron",
                    "+185.0% 伤害",
                    "+112.5% 多重射击",
                    "-49.5% 冲击伤害",
                    "段位 16 O 13",
                ],
            )
            # Same structured data but reordered stats + noisy spacing
            left_a_noisy = self._make_card(
                "[当前]",
                [
                    "托 里 德  Crita-acrican",
                    "-58.6% 变焦",
                    "+118.2% 多重射击",
                    "+156.8% 暴击伤害",
                    "+196.4% 暴击几率",
                    "段位 16 O 12",
                ],
            )
            right_b_changed = self._make_card(
                "[新循环]",
                [
                    "托里德 Igni-critatis",
                    "+175.2% 暴击几率",
                    "+138.4% 暴击伤害",
                    "-52.1% 变焦",
                    "段位 16 O 14",
                ],
            )

            with patch.object(win, "apply_position", wraps=win.apply_position) as spy_pos, patch.object(
                win, "_clear_cards", wraps=win._clear_cards
            ) as spy_clear:
                # 1st call renders once
                win.update_cards([left_a, right_a], elapsed_ms=110.0, layout_label="洗卡双卡对比")
                self.assertEqual(spy_pos.call_count, 1)
                self.assertEqual(spy_clear.call_count, 1)

                # Calls 2..5 with identical data (even with reordered/noisy stats and different elapsed_ms)
                for ms in (115.0, 122.0, 98.0, 130.0):
                    win.update_cards([left_a_noisy, right_a], elapsed_ms=ms, layout_label="洗卡双卡对比")
                self.assertEqual(spy_pos.call_count, 1)
                self.assertEqual(spy_clear.call_count, 1)

                # Genuine right-card change triggers 1 redraw
                win.update_cards([left_a, right_b_changed], elapsed_ms=105.0, layout_label="洗卡双卡对比")
                self.assertEqual(spy_pos.call_count, 2)
                self.assertEqual(spy_clear.call_count, 2)

                # Explicit force_redraw (e.g. language or hotkey label refresh) still redraws
                win.update_cards(
                    [left_a, right_b_changed],
                    elapsed_ms=105.0,
                    layout_label="洗卡双卡对比",
                    force_redraw=True,
                )
                self.assertEqual(spy_pos.call_count, 3)
                self.assertEqual(spy_clear.call_count, 3)
        finally:
            win.close()


if __name__ == "__main__":
    unittest.main()
