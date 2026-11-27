"""PyQt6 frameless, always-on-top, click-through translucent bilingual HUD overlay.

Features:
  - Frameless dark translucent card anchored adaptively to a saved relative screen
    position (`pos_x_ratio`, `pos_y_ratio`) or default top-right corner (safe on
    1366x768, 1080p, 2K, 4K, 21:9, and 32:9 ultrawide displays).
  - Always on top (`WindowStaysOnTopHint`) + click-through (`WindowTransparentForInput`)
    with temporary drag-unlock mode controlled from the Settings & Control Panel.
  - Customizable base font size (`10px ~ 24px`, default `13px`) with automatic
    `adjustSize()` width/height adaptation and live preview.
  - Full Simplified Chinese + English bilingual UI rendering driven by `locales/*.json`.
  - Supports single card view, dual-card Kuva reroll comparison, per-stat Roll % bars,
    recommended stat highlights, and on-demand Warframe.market panel.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QConicalGradient, QFont, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from src.companion_overlays import (
    DraggableOverlayBase,
    RelicRewardsOverlayWindow,
    RerollSynergyPopupWindow,
    WMPriceOverlayWindow,
    snap_window_position,
)
from src.evaluator import (
    DualComparisonResult,
    RerollSynergyAlert,
    RivenEvaluation,
    StatEvaluation,
    analyze_reroll_synergy_alert,
    build_card_data_signature,
    build_reroll_data_signature,
    cards_stat_numbers_equivalent,
    compare_riven_evaluations,
    is_98pct_positive_highlight_stat,
    reroll_stat_numbers_equivalent,
)
from src.i18n import I18N, SUPPORTED_LANGS
from src.riven_parser import RivenData, RivenStat
from src.roi_manager import (
    MODE_CHAT_RIVEN,
    MODE_PAUSED,
    MODE_RELIC_REWARDS,
    MODE_REROLL,
    MODE_SINGLE_RIVEN,
    format_mode_badge,
    format_mode_short,
    get_mode_accent_color,
)
from src.settings_manager import (
    DEFAULT_OVERLAY_FONT_PX,
    DEFAULT_THEME_PRESET,
    clamp_overlay_font_size,
    hex_to_rgba,
    normalize_collapse_seconds,
    resolve_theme_colors,
)
from src.wm_client import WMMarketReport

GRADE_COLORS = {
    "S": ("#FFD166", "rgba(255, 209, 102, 42)", "#FFD166"),
    "A": ("#34D399", "rgba(52, 211, 153, 38)", "#34D399"),
    "B": ("#60A5FA", "rgba(96, 165, 250, 38)", "#60A5FA"),
    "C": ("#F87171", "rgba(248, 113, 113, 38)", "#F87171"),
    "?": ("#A78BFA", "rgba(167, 139, 250, 32)", "#A78BFA"),
}

TAG_STYLE_MAP = {
    "SSS 反转神负": ("#FFD166", "rgba(255, 209, 102, 62)"),
    "融合双暴": ("#FFD166", "rgba(255, 209, 102, 54)"),
    "融合基伤多重": ("#6EE7B7", "rgba(110, 231, 183, 44)"),
    "融合词条": ("#38BDF8", "rgba(56, 189, 248, 38)"),
    "弱点增伤": ("#FDE047", "rgba(253, 224, 71, 34)"),
    "重击神配": ("#FFD166", "rgba(255, 209, 102, 46)"),
    "全中推荐": ("#FFD166", "rgba(255, 209, 102, 46)"),
    "双暴多重": ("#FFD166", "rgba(255, 209, 102, 40)"),
    "双暴基伤": ("#FFD166", "rgba(255, 209, 102, 36)"),
    "双暴": ("#FDE047", "rgba(253, 224, 71, 32)"),
    "基伤多重": ("#6EE7B7", "rgba(110, 231, 183, 32)"),
    "多重配暴": ("#6EE7B7", "rgba(110, 231, 183, 32)"),
    "无害负": ("#34D399", "rgba(52, 211, 153, 34)"),
    "伤筋负": ("#FB7185", "rgba(251, 113, 133, 40)"),
}


@dataclass
class EvaluatedRivenCard:
    """Bundled display payload for a single evaluated Riven card."""

    label: str  # "[当前]" / "[新循环]" / "当前紫卡"
    riven: RivenData
    evaluation: RivenEvaluation
    avg_confidence: float = 0.0


def _ui_font_family(lang: str) -> str:
    if sys.platform != "win32":
        if lang in ("zh_CN", "zh_TW"):
            return "Noto Sans CJK SC"
        if lang == "ja_JP":
            return "Noto Sans CJK JP"
        if lang == "ko_KR":
            return "Noto Sans CJK KR"
        return "Noto Sans"
    if lang in ("zh_CN", "zh_TW"):
        return "Microsoft YaHei UI"
    if lang == "ja_JP":
        return "Yu Gothic UI"
    if lang == "ko_KR":
        return "Malgun Gothic"
    if lang == "th_TH":
        return "Leelawadee UI"
    return "Segoe UI"


def _make_px_font(
    family: str,
    px: int,
    weight: QFont.Weight = QFont.Weight.Normal,
) -> QFont:
    font = QFont(family)
    font.setPixelSize(max(8, int(px)))
    font.setWeight(weight)
    return font


def _make_grade_badge(grade: str, size: int = 38, font_px: int = 19) -> QLabel:
    fg, bg, border = GRADE_COLORS.get(grade, GRADE_COLORS["?"])
    badge = QLabel(grade)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setFixedSize(size, size)
    badge.setFont(_make_px_font("Segoe UI", font_px, QFont.Weight.Bold))
    badge.setStyleSheet(
        f"""
        QLabel {{
            color: {fg};
            background-color: {bg};
            border: 1.5px solid {border};
            border-radius: {max(6, size // 4)}px;
        }}
        """
    )
    return badge


def _make_tag_pill(canonical_tag: str, lang: str, font_px: int = 11) -> QLabel:
    fg, bg = TAG_STYLE_MAP.get(canonical_tag, ("#D8B4FE", "rgba(168, 85, 247, 36)"))
    pill = QLabel(I18N.tag_name(canonical_tag, lang))
    pill.setFont(_make_px_font(_ui_font_family(lang), font_px, QFont.Weight.DemiBold))
    pill.setStyleSheet(
        f"""
        QLabel {{
            color: {fg};
            background-color: {bg};
            border: 1px solid {fg};
            border-radius: 5px;
            padding: 2px 6px;
        }}
        """
    )
    return pill


def _localize_side_label(raw_label: str, lang: str) -> str:
    if "当前" in raw_label and "[" in raw_label:
        return I18N.t("side_current", lang)
    if "新循环" in raw_label:
        return I18N.t("side_new", lang)
    return I18N.t("side_single", lang)


def _localize_layout_label(raw_layout: str, lang: str) -> str:
    if "双卡" in raw_layout:
        return I18N.t("layout_dual", lang)
    if "预览" in raw_layout or "Preview" in raw_layout:
        return I18N.t("preview_banner_tag", lang)
    if raw_layout:
        return I18N.t("layout_single", lang)
    return ""


def _localize_comparison(
    comp: DualComparisonResult,
    left_eval: RivenEvaluation,
    right_eval: RivenEvaluation,
    lang: str,
) -> tuple[str, str]:
    if comp.winner == "right":
        headline = I18N.t("compare_right", lang)
    elif comp.winner == "left":
        headline = I18N.t("compare_left", lang)
    else:
        headline = I18N.t("compare_tie", lang)

    parts = [I18N.t("compare_grade", lang, left=left_eval.overall_grade, right=right_eval.overall_grade)]
    if comp.roll_pct_delta is not None:
        parts.append(I18N.t("compare_delta", lang, delta=comp.roll_pct_delta))
    if comp.dps_delta_pct is not None:
        parts.append(I18N.t("compare_dps_delta", lang, delta=comp.dps_delta_pct))

    skip = ("三正一负", "二正一负", "无负词条", "伤筋负")
    if comp.winner == "right":
        hi = [I18N.tag_name(t, lang) for t in right_eval.tags if t not in skip][:3]
        if hi:
            parts.append(I18N.t("compare_right_adv", lang, tags=" / ".join(hi)))
    elif comp.winner == "left":
        hi = [I18N.tag_name(t, lang) for t in left_eval.tags if t not in skip][:3]
        if "伤筋负" in right_eval.tags and "伤筋负" not in left_eval.tags:
            parts.append(I18N.t("compare_right_harmful", lang))
        elif hi:
            parts.append(I18N.t("compare_left_adv", lang, tags=" / ".join(hi)))

    return headline, " · ".join(parts)


def _build_sample_preview_card() -> EvaluatedRivenCard:
    """Build a realistic sample Riven card for live drag positioning & font size preview."""
    sample_riven = RivenData(
        weapon_name="托里德",
        riven_suffix="Crita-satiata",
        riven_name="托里德 Crita-satiata",
        mastery_rank=16,
        reroll_count=12,
        stats=[
            RivenStat("暴击几率", "暴击几率", 196.4, 196.4, "+", "%", False, "+196.4% 暴击几率"),
            RivenStat("多重射击", "多重射击", 118.2, 118.2, "+", "%", False, "+118.2% 多重射击"),
            RivenStat("暴击伤害", "暴击伤害", 156.8, 156.8, "+", "%", False, "+156.8% 暴击伤害"),
            RivenStat("变焦", "变焦", 58.6, -58.6, "-", "%", True, "-58.6% 变焦"),
        ],
        is_valid=True,
    )
    sample_eval = RivenEvaluation(
        weapon_name="托里德",
        weapon_name_en="Torid",
        weapon_slug="torid",
        weapon_class="rifle",
        disposition=1.30,
        disposition_source="database",
        combo_pattern="3+1-",
        overall_grade="S",
        overall_roll_pct=88.5,
        tags=["全中推荐", "双暴多重", "无害负"],
        summary="极品毕业组合，强烈建议保留",
        summary_key="god_roll",
        recommended_positives=["暴击几率", "暴击伤害", "多重射击", "火焰伤害"],
        recommended_negatives=["变焦", "冲击伤害"],
        recommendation_source="builtin",
        cached_wm_price="650p",
        stat_evaluations=[
            StatEvaluation(
                stat_name="暴击几率",
                sign="+",
                value=196.4,
                unit="%",
                is_negative=False,
                is_locked=False,
                min_value=164.5,
                max_value=201.1,
                roll_pct=87.2,
                delta_pct=7.4,
                grade="S",
                range_display="+164.5% ~ +201.1%",
                is_recommended=True,
            ),
            StatEvaluation(
                stat_name="多重射击",
                sign="+",
                value=118.2,
                unit="%",
                is_negative=False,
                is_locked=False,
                min_value=98.7,
                max_value=120.6,
                roll_pct=89.0,
                delta_pct=7.8,
                grade="S",
                range_display="+98.7% ~ +120.6%",
                is_recommended=True,
            ),
            StatEvaluation(
                stat_name="暴击伤害",
                sign="+",
                value=156.8,
                unit="%",
                is_negative=False,
                is_locked=False,
                min_value=131.6,
                max_value=160.9,
                roll_pct=86.0,
                delta_pct=7.2,
                grade="S",
                range_display="+131.6% ~ +160.9%",
                is_recommended=True,
            ),
            StatEvaluation(
                stat_name="变焦",
                sign="-",
                value=58.6,
                unit="%",
                is_negative=True,
                is_locked=False,
                min_value=52.6,
                max_value=64.3,
                roll_pct=91.5,
                delta_pct=0.0,
                grade="S",
                range_display="-52.6% ~ -64.3%",
                is_recommended=True,
            ),
        ],
    )
    return EvaluatedRivenCard(label="当前紫卡", riven=sample_riven, evaluation=sample_eval, avg_confidence=0.99)


class RainbowStatRowWidget(QWidget):
    """Wrapper around a single stat row that draws a flowing rainbow border for 3 seconds.

    Activated strictly in dual-card (`reroll`) mode when a newly rolled non-hybrid
    positive stat hits `>= 98%` roll perfection. Stops its animation timer automatically
    after `duration_ms` (default 3000ms) so idle CPU returns to 0%.
    """

    DEFAULT_DURATION_MS = 3000

    def __init__(
        self,
        inner_layout: QVBoxLayout,
        *,
        rainbow_active: bool = False,
        duration_ms: int = DEFAULT_DURATION_MS,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._rainbow_active = bool(rainbow_active)
        self._duration_ms = max(200, int(duration_ms))
        self._phase_deg: float = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4) if self._rainbow_active else outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addLayout(inner_layout)

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30 FPS smooth flowing spectrum
        self._anim_timer.timeout.connect(self._on_tick)

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self.stop_rainbow)

        if self._rainbow_active:
            self._anim_timer.start()
            self._stop_timer.start(self._duration_ms)

    @property
    def is_rainbow_active(self) -> bool:
        return self._rainbow_active

    @property
    def rainbow_duration_ms(self) -> int:
        return self._duration_ms

    def _on_tick(self) -> None:
        if not self._rainbow_active:
            self._anim_timer.stop()
            return
        self._phase_deg = (self._phase_deg + 12.0) % 360.0
        self.update()

    def stop_rainbow(self) -> None:
        self._rainbow_active = False
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        if self._stop_timer.isActive():
            self._stop_timer.stop()
        self.update()

    def hideEvent(self, event) -> None:  # noqa: N802
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self._rainbow_active:
            return
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if rect.width() <= 4 or rect.height() <= 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Subtle luminous inner highlight tint
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 215, 102, 24))
        painter.drawRoundedRect(rect, 7.0, 7.0)

        # Flowing multicolor conical rainbow border
        grad = QConicalGradient(rect.center(), self._phase_deg)
        grad.setColorAt(0.00, QColor("#FF3B5C"))
        grad.setColorAt(0.17, QColor("#FFB703"))
        grad.setColorAt(0.34, QColor("#06D6A0"))
        grad.setColorAt(0.51, QColor("#38BDF8"))
        grad.setColorAt(0.68, QColor("#A855F7"))
        grad.setColorAt(0.85, QColor("#EC4899"))
        grad.setColorAt(1.00, QColor("#FF3B5C"))

        pen = QPen(grad, 2.6)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 7.0, 7.0)
        painter.end()


class SingleRivenCardWidget(QFrame):
    """Compact translucent rating card for one Riven Mod with dynamic font scaling."""

    def __init__(
        self,
        card: EvaluatedRivenCard,
        lang: str = "zh_CN",
        show_side_label: bool = False,
        highlight_role: str = "normal",  # "normal" | "winner" | "loser"
        min_card_width: int = 330,
        max_card_width: int = 415,
        base_font_px: int = DEFAULT_OVERLAY_FONT_PX,
        enable_rainbow_98: bool = False,
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self._lang = lang
        self._base_px = clamp_overlay_font_size(base_font_px)
        self._enable_rainbow_98 = bool(enable_rainbow_98)
        self._theme = resolve_theme_colors(DEFAULT_THEME_PRESET, theme_colors)
        self.rainbow_stat_widgets: List[RainbowStatRowWidget] = []
        self.setObjectName("SingleCard")
        self.setMinimumWidth(min(min_card_width, max_card_width))
        self.setMaximumWidth(max_card_width)
        if highlight_role == "winner":
            border_css = f"2px solid {self._theme['accent']}"
            bg_css = hex_to_rgba(self._theme["bg"], 242)
        elif highlight_role == "loser":
            border_css = f"1.2px solid {hex_to_rgba(self._theme['border'], 125)}"
            bg_css = hex_to_rgba(self._theme["bg"], 216)
        else:
            border_css = f"1.5px solid {hex_to_rgba(self._theme['border'], 200)}"
            bg_css = hex_to_rgba(self._theme["bg"], 234)

        self.setStyleSheet(
            f"""
            QFrame#SingleCard {{
                background-color: {bg_css};
                border: {border_css};
                border-radius: 12px;
            }}
            QLabel {{
                color: {self._theme['text']};
                background: transparent;
            }}
            """
        )
        self._build_content(card, show_side_label, highlight_role)

    def _build_content(
        self,
        card: EvaluatedRivenCard,
        show_side_label: bool,
        highlight_role: str,
    ) -> None:
        riven = card.riven
        ev = card.evaluation
        lang = self._lang
        font_fam = _ui_font_family(lang)
        base_px = self._base_px
        title_px = base_px + 2
        note_px = max(9, base_px - 1)
        tag_px = max(8, base_px - 2)
        pad = max(10, int(round(base_px * 1.05)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(pad, max(9, pad - 2), pad, max(9, pad - 2))
        layout.setSpacing(max(5, int(round(base_px * 0.52))))

        if show_side_label and card.label:
            side_row = QHBoxLayout()
            side_row.setSpacing(6)
            side_lbl = QLabel(_localize_side_label(card.label, lang))
            side_lbl.setFont(_make_px_font(font_fam, note_px, QFont.Weight.Bold))
            side_lbl.setStyleSheet("color: #FFD166;" if highlight_role == "winner" else "color: #C4B5FD;")
            side_row.addWidget(side_lbl)

            if highlight_role == "winner":
                rec_text = I18N.t("badge_keep", lang) if "当前" in card.label else I18N.t("badge_pick", lang)
                rec_badge = QLabel(rec_text)
                rec_badge.setFont(_make_px_font(font_fam, tag_px, QFont.Weight.Bold))
                rec_badge.setStyleSheet(
                    """
                    QLabel {
                        color: #1A1228;
                        background-color: #FFD166;
                        border-radius: 5px;
                        padding: 1px 7px;
                    }
                    """
                )
                side_row.addWidget(rec_badge)
            side_row.addStretch(1)
            layout.addLayout(side_row)

        # Header row: overall grade badge + title + roll summary
        header = QHBoxLayout()
        header.setSpacing(10)
        big_badge_sz = max(32, int(round(base_px * 2.85)))
        big_badge_font = max(14, int(round(base_px * 1.45)))
        header.addWidget(_make_grade_badge(ev.overall_grade, size=big_badge_sz, font_px=big_badge_font))

        title_col = QVBoxLayout()
        title_col.setSpacing(2)

        if lang not in ("zh_CN", "zh_TW"):
            display_weapon = ev.weapon_name_en or ev.weapon_name or riven.weapon_name or I18N.t("unknown_weapon", lang)
        else:
            display_weapon = ev.weapon_name or riven.weapon_name or I18N.t("unknown_weapon", lang)

        display_title = (
            f"{display_weapon} {riven.riven_suffix}"
            if riven.riven_suffix
            else (riven.riven_name or display_weapon)
        )
        title_lbl = QLabel(display_title)
        title_lbl.setFont(_make_px_font(font_fam, title_px, QFont.Weight.Bold))
        title_lbl.setStyleSheet(f"color: {self._theme['text']};")
        title_lbl.setWordWrap(True)
        title_col.addWidget(title_lbl)

        meta_parts = [
            I18N.weapon_class(ev.weapon_class, lang),
            f"{I18N.t('disposition', lang)} {ev.disposition:.2f}",
        ]
        if riven.mastery_rank is not None:
            meta_parts.append(f"{I18N.t('mastery_rank', lang)} {riven.mastery_rank}")
        if riven.reroll_count is not None:
            meta_parts.append(f"{I18N.t('rerolls', lang)} {riven.reroll_count}")
        if ev.overall_roll_pct is not None:
            meta_parts.append(f"{I18N.t('avg_roll', lang)} {ev.overall_roll_pct:.0f}%")

        meta_lbl = QLabel(" · ".join(meta_parts))
        meta_lbl.setFont(_make_px_font(font_fam, note_px))
        meta_lbl.setStyleSheet("color: #B9A3DF;")
        meta_lbl.setWordWrap(True)
        title_col.addWidget(meta_lbl)

        header.addLayout(title_col, stretch=1)
        layout.addLayout(header)

        # Matched Incarnon / special archetype banner
        arch_disp = ev.matched_archetype_name if lang in ("zh_CN", "zh_TW") else (ev.matched_archetype_name_en or ev.matched_archetype_name)
        if arch_disp:
            arch_fg = "#FFD166" if ev.has_inverted_god_neg else "#7DD3FC"
            arch_bg = "rgba(255, 209, 102, 34)" if ev.has_inverted_god_neg else "rgba(56, 189, 248, 28)"
            arch_border = "rgba(255, 209, 102, 160)" if ev.has_inverted_god_neg else "rgba(56, 189, 248, 130)"
            arch_lbl = QLabel(f"✦ {I18N.t('archetype_prefix', lang)}{arch_disp}")
            arch_lbl.setFont(_make_px_font(font_fam, note_px, QFont.Weight.Bold))
            arch_lbl.setStyleSheet(
                f"""
                QLabel {{
                    color: {arch_fg};
                    background-color: {arch_bg};
                    border: 1px solid {arch_border};
                    border-radius: 5px;
                    padding: 2px 7px;
                }}
                """
            )
            arch_lbl.setWordWrap(True)
            layout.addWidget(arch_lbl)

        warn_disp = ev.archetype_warning if lang in ("zh_CN", "zh_TW") else (ev.archetype_warning_en or ev.archetype_warning)
        if warn_disp:
            warn_lbl = QLabel(f"⚠ {warn_disp}")
            warn_lbl.setFont(_make_px_font(font_fam, tag_px, QFont.Weight.DemiBold))
            warn_lbl.setStyleSheet("color: #FCD34D;")
            warn_lbl.setWordWrap(True)
            layout.addWidget(warn_lbl)

        # Tags row
        if ev.tags:
            tags_row = QHBoxLayout()
            tags_row.setSpacing(5)
            for tag in ev.tags[:5]:
                tags_row.addWidget(_make_tag_pill(tag, lang, font_px=tag_px))
            tags_row.addStretch(1)
            layout.addLayout(tags_row)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: rgba(168, 118, 255, 70); max-height: 1px;")
        layout.addWidget(divider)

        # Stats rows
        if ev.stat_evaluations:
            for se in ev.stat_evaluations:
                row_layout = self._build_stat_row(se, lang, base_px)
                should_rainbow = self._enable_rainbow_98 and is_98pct_positive_highlight_stat(se)
                row_widget = RainbowStatRowWidget(
                    row_layout,
                    rainbow_active=should_rainbow,
                    parent=self,
                )
                if should_rainbow:
                    self.rainbow_stat_widgets.append(row_widget)
                layout.addWidget(row_widget)
        else:
            empty_lbl = QLabel(I18N.t("no_stats", lang))
            empty_lbl.setFont(_make_px_font(font_fam, base_px))
            empty_lbl.setStyleSheet("color: #FCD34D;")
            empty_lbl.setWordWrap(True)
            layout.addWidget(empty_lbl)

        # Verdict summary
        summary_text = I18N.summary(ev.summary_key, lang)
        if summary_text:
            verdict = QLabel(f"{I18N.t('verdict_prefix', lang)}{summary_text}")
            verdict.setFont(_make_px_font(font_fam, note_px))
            verdict.setStyleSheet("color: #D8C4F8; margin-top: 2px;")
            verdict.setWordWrap(True)
            layout.addWidget(verdict)

        # Compact local recommendation strip (zero network)
        if ev.recommended_positives:
            rec_pos_txt = "/".join(I18N.stat_name(s, lang) for s in ev.recommended_positives[:4])
            rec_neg_txt = (
                "/".join(I18N.stat_name(s, lang) for s in ev.recommended_negatives[:2])
                if ev.recommended_negatives
                else I18N.t("rec_neg_default", lang)
            )
            src_label = I18N.rec_source(ev.recommendation_source, lang)
            src_color = "#38BDF8" if ev.recommendation_source == "wm" else "#C4B5FD"
            rec_line = f"[{src_label}] {I18N.t('rec_pos', lang)}: {rec_pos_txt} | {I18N.t('rec_neg', lang)}: {rec_neg_txt}"
            if ev.cached_wm_price:
                rec_line += f" | WM: {ev.cached_wm_price}"
            rec_lbl = QLabel(rec_line)
            rec_lbl.setFont(_make_px_font(font_fam, tag_px))
            rec_lbl.setStyleSheet(f"color: {src_color};")
            rec_lbl.setWordWrap(True)
            layout.addWidget(rec_lbl)

    @staticmethod
    def _build_stat_row(se: StatEvaluation, lang: str, base_px: int) -> QVBoxLayout:
        font_fam = _ui_font_family(lang)
        note_px = max(9, base_px - 1)
        block = QVBoxLayout()
        block.setSpacing(3)
        block.setContentsMargins(0, 1, 0, 3)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        left_col = QVBoxLayout()
        left_col.setSpacing(1)

        lock_prefix = "🔒 " if se.is_locked else ""
        rec_prefix = "★ " if se.is_recommended else ""
        hybrid_prefix = f"{I18N.t('hybrid_stat_badge', lang)} " if se.is_hybrid else ""
        unit_display = se.unit
        if lang not in ("zh_CN", "zh_TW"):
            if unit_display == "秒":
                unit_display = "s"
            elif unit_display == "米":
                unit_display = "m"

        if se.sign == "x":
            val_str = f"x{se.value:g}"
        else:
            val_str = f"{se.sign}{se.value:g}{unit_display}"

        localized_stat = I18N.stat_name(se.stat_name, lang)
        inv_suffix = f" {I18N.t('sss_inv_neg_tag', lang)}" if se.is_inverted_god_neg else ""
        stat_lbl = QLabel(f"{lock_prefix}{rec_prefix}{hybrid_prefix}{val_str} {localized_stat}{inv_suffix}")
        stat_lbl.setFont(_make_px_font(font_fam, base_px, QFont.Weight.DemiBold))
        stat_lbl.setWordWrap(True)
        if se.is_inverted_god_neg:
            stat_lbl.setStyleSheet("color: #FFD166;")
        elif se.is_recommended and not se.is_negative:
            stat_lbl.setStyleSheet("color: #FDE68A;")
        elif se.is_recommended and se.is_negative:
            stat_lbl.setStyleSheet("color: #86EFAC;")
        elif se.is_negative:
            stat_lbl.setStyleSheet("color: #FDA4AF;")
        else:
            stat_lbl.setStyleSheet("color: #A7F3D0;")
        left_col.addWidget(stat_lbl)

        if se.missing_base_range:
            notice_lbl = QLabel(I18N.t("hybrid_no_range_notice", lang))
            notice_lbl.setFont(_make_px_font(font_fam, note_px))
            notice_lbl.setStyleSheet("color: #7DD3FC;")
            notice_lbl.setWordWrap(True)
            left_col.addWidget(notice_lbl)
        elif se.range_display:
            rng_txt = se.range_display
            if lang not in ("zh_CN", "zh_TW"):
                rng_txt = rng_txt.replace("秒", "s").replace("米", "m")
            range_lbl = QLabel(f"{I18N.t('range_prefix', lang)} {rng_txt}")
            range_lbl.setFont(_make_px_font("Consolas", note_px))
            range_lbl.setStyleSheet("color: #9A86C2;")
            range_lbl.setWordWrap(True)
            left_col.addWidget(range_lbl)

        top_row.addLayout(left_col, stretch=1)

        fg, _, _ = GRADE_COLORS.get(se.grade, GRADE_COLORS["?"])
        if se.roll_pct is not None:
            delta_str = f" ({se.delta_pct:+.1f}%)" if se.delta_pct is not None else ""
            pct_lbl = QLabel(f"{se.roll_pct:.0f}%{delta_str}")
            pct_lbl.setFont(_make_px_font("Consolas", note_px, QFont.Weight.Bold))
            pct_lbl.setStyleSheet(f"color: {fg};")
            pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            top_row.addWidget(pct_lbl)

        sm_badge_sz = max(20, int(round(base_px * 1.75)))
        sm_badge_font = max(9, base_px - 1)
        top_row.addWidget(_make_grade_badge(se.grade, size=sm_badge_sz, font_px=sm_badge_font))
        block.addLayout(top_row)

        if se.roll_pct is not None:
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, int(round(se.roll_pct)))))
            bar.setTextVisible(False)
            bar.setFixedHeight(max(5, int(round(base_px * 0.45))))
            bar.setStyleSheet(
                f"""
                QProgressBar {{
                    background-color: rgba(255, 255, 255, 26);
                    border: none;
                    border-radius: 3px;
                }}
                QProgressBar::chunk {{
                    background-color: {fg};
                    border-radius: 3px;
                }}
                """
            )
            block.addWidget(bar)

        return block


class RivenOverlayWindow(QWidget):
    """Frameless, always-on-top HUD overlay coordinating 4 independent draggable windows."""

    position_changed = pyqtSignal(float, float)             # legacy main window (rx, ry)
    window_position_changed = pyqtSignal(str, float, float) # (window_key, rx, ry) for all 4 windows

    def __init__(
        self,
        click_through: bool = True,
        margin_x: int = 24,
        margin_y: int = 56,
        lang: str = "zh_CN",
        toggle_hotkey: str = "F8",
        wm_hotkey: str = "F9",
        hide_hotkey: str = "ESC",
        font_size_px: int = DEFAULT_OVERLAY_FONT_PX,
        pos_x_ratio: Optional[float] = None,
        pos_y_ratio: Optional[float] = None,
        wm_pos: Optional[tuple[Optional[float], Optional[float]]] = None,
        synergy_pos: Optional[tuple[Optional[float], Optional[float]]] = None,
        relic_pos: Optional[tuple[Optional[float], Optional[float]]] = None,
        auto_collapse_seconds: int = 5,
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self._click_through = click_through
        self._drag_unlocked = False
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._theme_colors = resolve_theme_colors(DEFAULT_THEME_PRESET, theme_colors)

        self._margin_x = margin_x
        self._margin_y = margin_y
        self._lang = lang
        self._hk_toggle = toggle_hotkey
        self._hk_wm = wm_hotkey
        self._hk_hide = hide_hotkey
        self._font_size_px = clamp_overlay_font_size(font_size_px)
        self._pos_x_ratio = pos_x_ratio
        self._pos_y_ratio = pos_y_ratio
        self._work_mode = MODE_PAUSED
        self._pending_debounce = False
        self._auto_collapse_seconds = normalize_collapse_seconds(auto_collapse_seconds)
        self._is_compact_pill = False
        self._f9_page_hold = False
        self._hotkeys_locked = False
        self._hidden_by_focus = False
        self._focus_snapshot: List[QWidget] = []

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.timeout.connect(self._on_auto_collapse_timeout)

        # Remember last render state so dynamic font/lang/drag updates can re-render cleanly
        self._display_mode = "none"  # "none" | "preview" | "debounce" | "waiting" | "manual_standby" | "relic" | "cards" | "toast" | "error"
        self._last_cards: List[EvaluatedRivenCard] = []
        self._last_elapsed_ms: Optional[float] = None
        self._last_layout_label: str = ""
        self._last_comparison: Optional[DualComparisonResult] = None
        self._last_message: Optional[str] = None
        self._last_relic_items: List[object] = []
        self._last_rendered_signature: Optional[object] = None

        wm_rx, wm_ry = wm_pos if wm_pos is not None else (None, None)
        syn_rx, syn_ry = synergy_pos if synergy_pos is not None else (None, None)
        rel_rx, rel_ry = relic_pos if relic_pos is not None else (None, None)

        # Independent draggable companion overlay windows (each saves its own screen ratio)
        self.wm_window = WMPriceOverlayWindow(
            click_through=click_through,
            lang=lang,
            font_size_px=self._font_size_px,
            pos_x_ratio=wm_rx,
            pos_y_ratio=wm_ry,
            theme_colors=self._theme_colors,
        )
        self.synergy_window = RerollSynergyPopupWindow(
            click_through=click_through,
            lang=lang,
            font_size_px=self._font_size_px,
            pos_x_ratio=syn_rx,
            pos_y_ratio=syn_ry,
            theme_colors=self._theme_colors,
        )
        self.relic_window = RelicRewardsOverlayWindow(
            click_through=click_through,
            lang=lang,
            font_size_px=self._font_size_px,
            pos_x_ratio=rel_rx,
            pos_y_ratio=rel_ry,
            theme_colors=self._theme_colors,
        )
        for companion in (self.wm_window, self.synergy_window, self.relic_window):
            companion.window_position_changed.connect(self.window_position_changed.emit)
            companion.set_snap_siblings_provider(
                lambda c=companion: [w.frameGeometry() for w in self.all_overlay_windows if w is not c and w.isVisible()]
            )

        # Compatibility aliases to WM window internal labels (used by tests & callers)
        self._wm_panel = self.wm_window._wm_panel
        self._wm_title = self.wm_window._wm_title
        self._wm_body = self.wm_window._wm_body
        self._wm_price = self.wm_window._wm_price
        self._wm_listings = self.wm_window._wm_listings
        self._wm_hint = self.wm_window._wm_hint

        self._init_window_flags()
        self.setWindowTitle("RivenLensOverlay")
        self._build_shell()
        self._apply_shell_fonts()

    @property
    def all_overlay_windows(self) -> List[QWidget]:
        return [self, self.wm_window, self.synergy_window, self.relic_window]

    def has_any_visible_overlay(self) -> bool:
        return any(w.isVisible() for w in self.all_overlay_windows)

    @property
    def is_compact_pill(self) -> bool:
        return self._is_compact_pill

    @property
    def is_f9_page_held(self) -> bool:
        return self._f9_page_hold

    @property
    def auto_collapse_seconds(self) -> int:
        return self._auto_collapse_seconds

    @property
    def hotkeys_locked(self) -> bool:
        return self._hotkeys_locked

    def set_f9_page_hold(self, active: bool) -> None:
        """Pin overlay open indefinitely after F9 capture until the user exits the Riven/Relic screen."""
        self._f9_page_hold = bool(active)
        if self._f9_page_hold:
            self._collapse_timer.stop()

    def release_f9_page_hold(self, collapse_now: bool = False) -> None:
        """Release F9 page hold (e.g. after exiting the Riven/Relic page) and optionally collapse immediately."""
        self._f9_page_hold = False
        if collapse_now and not self._drag_unlocked:
            self.collapse_to_pill()
        elif self.has_any_visible_overlay() and not self._is_compact_pill and not self._drag_unlocked:
            self.arm_auto_collapse_timer()

    def enter_standby_pill(self) -> None:
        """Shrink overlay into compact standby pill (e.g. on ESC) without exiting the active work mode."""
        self._f9_page_hold = False
        self._collapse_timer.stop()
        self.collapse_to_pill()

    def set_auto_collapse_seconds(self, seconds: int) -> None:
        self._auto_collapse_seconds = normalize_collapse_seconds(seconds)
        if self._auto_collapse_seconds == 0:
            self._collapse_timer.stop()
            if self._is_compact_pill and self.isVisible():
                self.expand_from_pill(restart_timer=False)
        elif self.isVisible() and not self._is_compact_pill and not self._drag_unlocked and not self._f9_page_hold:
            self.arm_auto_collapse_timer()

    def set_hotkeys_locked(self, locked: bool) -> None:
        self._hotkeys_locked = bool(locked)
        self._refresh_pill_text()
        if self._hotkeys_locked and self._work_mode != MODE_PAUSED and not self.isVisible() and not self._hidden_by_focus:
            self.collapse_to_pill()

    def _refresh_pill_text(self) -> None:
        lang = self._lang
        if self._hotkeys_locked:
            txt = I18N.t("pill_locked", lang)
            border = "#F59E0B"
            fg = "#FDE68A"
        elif self._work_mode == MODE_SINGLE_RIVEN:
            txt = I18N.t("pill_single", lang, toggle=self._hk_toggle, wm=self._hk_wm)
            border = get_mode_accent_color(MODE_SINGLE_RIVEN)
            fg = "#E9D5FF"
        elif self._work_mode == MODE_REROLL:
            txt = I18N.t("pill_reroll", lang, toggle=self._hk_toggle, wm=self._hk_wm)
            border = get_mode_accent_color(MODE_REROLL)
            fg = "#FDE68A"
        elif self._work_mode == MODE_CHAT_RIVEN:
            txt = I18N.t("pill_chat", lang, toggle=self._hk_toggle, wm=self._hk_wm)
            border = get_mode_accent_color(MODE_CHAT_RIVEN)
            fg = "#BAE6FD"
        elif self._work_mode == MODE_RELIC_REWARDS:
            txt = I18N.t("pill_relic", lang, toggle=self._hk_toggle, wm=self._hk_wm)
            border = get_mode_accent_color(MODE_RELIC_REWARDS)
            fg = "#A5F3FC"
        else:
            txt = I18N.t("pill_paused", lang, toggle=self._hk_toggle, wm=self._hk_wm)
            border = "#8B5CF6"
            fg = "#DDD6FE"

        self._pill_label.setText(txt)
        pill_bg = hex_to_rgba(self._theme_colors["bg"], 220)
        pill_border = self._theme_colors["border"] if not self._hotkeys_locked else "#F59E0B"
        pill_fg = self._theme_colors["text"] if not self._hotkeys_locked else "#FDE68A"
        self._pill_label.setStyleSheet(
            f"""
            QLabel#CompactPill {{
                color: {pill_fg};
                background-color: {pill_bg};
                border: 1.5px solid {pill_border};
                border-radius: 14px;
                padding: 3px 12px;
            }}
            """
        )

    def arm_auto_collapse_timer(self, delay_ms_override: Optional[int] = None) -> None:
        """Start or reset the auto-collapse countdown timer (paused while drag-unlocked or F9-pinned)."""
        self._collapse_timer.stop()
        if self._drag_unlocked or self._f9_page_hold:
            return
        delay_ms = (
            int(delay_ms_override)
            if delay_ms_override is not None
            else self._auto_collapse_seconds * 1000
        )
        if delay_ms > 0:
            self._collapse_timer.start(delay_ms)

    def _on_auto_collapse_timeout(self) -> None:
        if self._drag_unlocked or self._f9_page_hold or self._auto_collapse_seconds <= 0:
            return
        if self._work_mode == MODE_PAUSED and not self.has_any_visible_overlay():
            return
        self.collapse_to_pill()

    def collapse_to_pill(self) -> None:
        """Shrink main overlay into compact semi-transparent single-line pill & hide sub-popups."""
        self._collapse_timer.stop()
        self._is_compact_pill = True
        self._refresh_pill_text()
        self._detail_container.hide()
        self._pill_label.show()
        self.wm_window.hide()
        self.synergy_window.hide()
        self.relic_window.hide()
        self.setWindowOpacity(0.65)
        if not self._hidden_by_focus:
            self.apply_position()
            self.force_topmost()

    def expand_from_pill(self, restart_timer: bool = True) -> None:
        """Restore full detail overlay at 0.92 opacity and optionally arm the auto-collapse timer."""
        self._is_compact_pill = False
        self._pill_label.hide()
        self._detail_container.show()
        self.setWindowOpacity(0.92)
        if restart_timer:
            self.arm_auto_collapse_timer()
        else:
            self._collapse_timer.stop()

    def suspend_for_focus_loss(self) -> None:
        """Temporarily hide all visible overlays when foreground window is not Warframe."""
        if self._hidden_by_focus or self._drag_unlocked:
            return
        self._focus_snapshot = [w for w in self.all_overlay_windows if w.isVisible()]
        self._hidden_by_focus = True
        for w in self._focus_snapshot:
            w.hide()

    def restore_from_focus_loss(self) -> None:
        """Restore overlays hidden by focus loss when returning to Warframe."""
        if not self._hidden_by_focus:
            return
        self._hidden_by_focus = False
        for w in self._focus_snapshot:
            if hasattr(w, "force_topmost"):
                w.force_topmost()
            else:
                w.show()
        self._focus_snapshot.clear()

    def hide_all_overlays(self) -> None:
        self._collapse_timer.stop()
        self._is_compact_pill = False
        self._f9_page_hold = False
        self._hidden_by_focus = False
        self._focus_snapshot.clear()
        self.hide()
        self.wm_window.hide()
        self.synergy_window.hide()
        self.relic_window.hide()

    def closeEvent(self, event) -> None:  # noqa: N802
        for companion in (self.wm_window, self.synergy_window, self.relic_window):
            companion.close()
        super().closeEvent(event)

    @property
    def is_drag_unlocked(self) -> bool:
        return self._drag_unlocked

    @property
    def theme_colors(self) -> Dict[str, str]:
        return dict(self._theme_colors)

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        """Apply new `{bg, border, accent, text}` color theme live across all 4 overlay windows."""
        self._theme_colors = resolve_theme_colors("custom", colors)
        self.wm_window.set_theme_colors(self._theme_colors)
        self.synergy_window.set_theme_colors(self._theme_colors)
        self.relic_window.set_theme_colors(self._theme_colors)
        self._refresh_mode_badge()
        self._footer.setStyleSheet(
            f"""
            QLabel {{
                color: {self._theme_colors['text']};
                background-color: {hex_to_rgba(self._theme_colors['bg'], 210)};
                border: 1px solid {hex_to_rgba(self._theme_colors['border'], 140)};
                border-radius: 6px;
                padding: 3px 10px;
            }}
            """
        )
        if self._display_mode == "cards" and self._last_cards and self.isVisible():
            self.update_cards(
                self._last_cards,
                elapsed_ms=self._last_elapsed_ms,
                layout_label=self._last_layout_label,
                comparison=self._last_comparison,
                lang=self._lang,
                force_redraw=True,
            )
        elif self._display_mode == "preview" and self.isVisible():
            self.show_preview_card()

    @property
    def font_size_px(self) -> int:
        return self._font_size_px

    @property
    def display_mode(self) -> str:
        return self._display_mode

    def set_language(self, lang: str) -> None:
        if lang not in SUPPORTED_LANGS:
            return
        self._lang = lang
        self._apply_shell_fonts()
        self._drag_banner.setText(I18N.t("drag_banner", lang))
        self._refresh_mode_badge()
        self.wm_window.set_language(lang)
        self.synergy_window.set_language(lang)
        self.relic_window.set_language(lang)

        if self._display_mode == "preview" and self.isVisible():
            self.show_preview_card()
        elif self._display_mode == "cards" and self._last_cards and self.isVisible():
            self.update_cards(
                self._last_cards,
                elapsed_ms=self._last_elapsed_ms,
                layout_label=self._last_layout_label,
                comparison=self._last_comparison,
                lang=lang,
                force_redraw=True,
            )
        elif self._display_mode == "debounce" and self.isVisible():
            self.show_debounce_waiting(self._work_mode)
        elif self._display_mode == "waiting" and self.isVisible():
            self.show_monitoring_waiting(keep_pill_if_collapsed=self._is_compact_pill)
        elif self._display_mode == "manual_standby" and self.isVisible():
            self.show_manual_mode_standby(self._work_mode)
        else:
            self._footer.setText(self._fmt_hk("footer_waiting", lang))

    def set_hotkey_labels(self, toggle_hotkey: str, wm_hotkey: str, hide_hotkey: str) -> None:
        self._hk_toggle = toggle_hotkey or "F8"
        self._hk_wm = wm_hotkey or "F9"
        self._hk_hide = hide_hotkey or "ESC"
        self._refresh_mode_badge()

    def set_work_mode(self, mode: str, pending_debounce: bool = False) -> None:
        self._work_mode = mode
        self._pending_debounce = bool(pending_debounce)
        self._refresh_mode_badge()

    def _refresh_mode_badge(self) -> None:
        self._refresh_pill_text()
        if self._work_mode == MODE_PAUSED:
            self._mode_badge.hide()
            return
        accent = "#FBBF24" if self._pending_debounce else get_mode_accent_color(self._work_mode)
        if self._pending_debounce and self._work_mode == MODE_SINGLE_RIVEN:
            txt = I18N.t("debounce_badge_single", self._lang)
        elif self._pending_debounce and self._work_mode == MODE_REROLL:
            txt = I18N.t("debounce_badge_reroll", self._lang)
        elif self._work_mode == MODE_SINGLE_RIVEN:
            txt = I18N.t("active_badge_single", self._lang)
        elif self._work_mode == MODE_REROLL:
            txt = I18N.t("active_badge_reroll", self._lang)
        else:
            txt = format_mode_badge(self._work_mode, self._lang, f9_key=self._hk_wm)

        self._mode_badge.setText(txt)
        self._mode_badge.setStyleSheet(
            f"""
            QLabel {{
                color: {accent};
                background-color: rgba(16, 11, 30, 232);
                border: 1.5px solid {accent};
                border-radius: 7px;
                padding: 3px 10px;
            }}
            """
        )
        self._mode_badge.show()

    def set_saved_position_ratio(
        self,
        x_ratio: Optional[float],
        y_ratio: Optional[float],
    ) -> None:
        if x_ratio is None or y_ratio is None:
            self._pos_x_ratio = None
            self._pos_y_ratio = None
        else:
            self._pos_x_ratio = max(0.0, min(1.0, float(x_ratio)))
            self._pos_y_ratio = max(0.0, min(1.0, float(y_ratio)))
        if self.isVisible():
            self.apply_position()

    def reset_position(self) -> None:
        self._pos_x_ratio = None
        self._pos_y_ratio = None
        if self.isVisible():
            self.apply_position()
        self.wm_window.reset_position()
        self.synergy_window.reset_position()
        self.relic_window.reset_position()

    def set_font_size(self, px: int) -> None:
        """Dynamically change overlay base font size (`10px ~ 24px`) across all 4 windows."""
        new_px = clamp_overlay_font_size(px)
        self.wm_window.set_font_size(new_px)
        self.synergy_window.set_font_size(new_px)
        self.relic_window.set_font_size(new_px)
        if new_px == self._font_size_px and self._display_mode != "none":
            return
        self._font_size_px = new_px
        self._apply_shell_fonts()

        # Re-render current visible payload so all child labels & card widths resize cleanly
        if self._display_mode == "cards" and self._last_cards:
            self.update_cards(
                self._last_cards,
                elapsed_ms=self._last_elapsed_ms,
                layout_label=self._last_layout_label,
                comparison=self._last_comparison,
                lang=self._lang,
                force_redraw=True,
            )
        elif self._display_mode == "preview":
            self.show_preview_card()
        elif self._display_mode == "debounce":
            self.show_debounce_waiting(self._work_mode)
        elif self._display_mode == "waiting":
            self.show_monitoring_waiting(self._last_message)
        elif self._display_mode == "manual_standby":
            self.show_manual_mode_standby(self._work_mode)
        elif self._display_mode == "relic":
            self.show_relic_rewards(self._last_relic_items, elapsed_ms=self._last_elapsed_ms)
        elif self._display_mode == "error" and self._last_message is not None:
            self.show_error(self._last_message)
        elif self._display_mode == "toast":
            self.show_toast(self._last_message, duration_ms=0)
        elif self.isVisible():
            self.apply_position()

    def set_drag_unlocked(self, unlocked: bool, show_preview_if_empty: bool = False) -> None:
        """Temporarily unlock click-through for mouse dragging, or re-lock to click-through."""
        target = bool(unlocked)
        was_visible = self.isVisible()
        self._drag_unlocked = target
        self._dragging = False

        if target:
            self._drag_banner.setText(I18N.t("drag_banner", self._lang))
            self._drag_banner.show()
            self._shell_frame.setStyleSheet(
                """
                QFrame#OverlayShell {
                    background-color: rgba(24, 16, 44, 140);
                    border: 2px dashed #FFD166;
                    border-radius: 14px;
                }
                """
            )
            self._shell_layout.setContentsMargins(8, 8, 8, 8)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._drag_banner.hide()
            self._shell_frame.setStyleSheet(
                """
                QFrame#OverlayShell {
                    background: transparent;
                    border: none;
                }
                """
            )
            self._shell_layout.setContentsMargins(0, 0, 0, 0)
            self.unsetCursor()

        self._apply_interaction_flags(click_through=(self._click_through and not target))
        # Simultaneously unlock/lock all 3 independent companion windows too!
        self.wm_window.set_drag_unlocked(target)
        self.synergy_window.set_drag_unlocked(target)
        self.relic_window.set_drag_unlocked(target)

        if target:
            self.expand_from_pill(restart_timer=False)
            if show_preview_if_empty and self._display_mode in ("none", "toast") and not self.has_any_visible_overlay():
                self.show_preview_card()
            elif was_visible:
                self.apply_position()
                self.force_topmost()
        else:
            if self._display_mode == "preview":
                self._clear_cards()
                self._display_mode = "none"
                self.hide()
            elif was_visible:
                self.apply_position()
                self.force_topmost()
                self.arm_auto_collapse_timer()

    def _fmt_hk(self, key: str, lang: str) -> str:
        return I18N.t(
            key,
            lang,
            toggle=self._hk_toggle,
            wm=self._hk_wm,
            hide=self._hk_hide,
        )

    def _init_window_flags(self) -> None:
        self._apply_interaction_flags(click_through=self._click_through)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def _apply_interaction_flags(self, click_through: bool) -> None:
        # Must update WA_TransparentForMouseEvents BEFORE setWindowFlags, otherwise
        # Qt 6's adjustFlags() silently re-adds Qt.WindowType.WindowTransparentForInput!
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, click_through)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if click_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        if self.windowHandle() is not None:
            self.windowHandle().setFlag(Qt.WindowType.WindowTransparentForInput, click_through)
        self._apply_win32_exstyle(click_through=click_through)
        self._apply_macos_style(click_through=click_through)

    def _apply_macos_style(self, click_through: bool) -> None:
        """Apply native macOS NSWindow overlay flags (non-hiding on deactivate, Space-wide, click-through)."""
        if sys.platform != "darwin":
            return
        try:
            from src.macos_support import apply_macos_overlay_window_style

            apply_macos_overlay_window_style(int(self.winId()), click_through=click_through)
        except Exception:
            pass

    def _apply_win32_exstyle(self, click_through: bool) -> None:
        """Immediately update Win32 WS_EX_TRANSPARENT so click-through switches cleanly."""
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020

            ex = int(user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE))
            if click_through:
                ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT
            else:
                ex &= ~WS_EX_TRANSPARENT
            user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)
            user32.SetWindowPos(
                ctypes.c_void_p(hwnd),
                None,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
        except Exception:
            pass

    def force_topmost(self) -> None:
        """Enforce topmost overlay stacking without stealing focus from Borderless Fullscreen games."""
        self.show()
        self.raise_()
        if sys.platform == "darwin":
            self._apply_macos_style(click_through=self._click_through)
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                HWND_TOPMOST = -1
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOACTIVATE = 0x0010
                SWP_SHOWWINDOW = 0x0040
                user32.SetWindowPos(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_void_p(HWND_TOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
            except Exception:
                pass

    def _build_shell(self) -> None:
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._shell_frame = QFrame()
        self._shell_frame.setObjectName("OverlayShell")
        self._shell_frame.setStyleSheet(
            """
            QFrame#OverlayShell {
                background: transparent;
                border: none;
            }
            """
        )
        self._outer.addWidget(self._shell_frame)

        self._shell_layout = QVBoxLayout(self._shell_frame)
        self._shell_layout.setContentsMargins(0, 0, 0, 0)
        self._shell_layout.setSpacing(6)
        self._root = self._shell_layout

        # Top drag handle banner (ONLY shown when drag mode is unlocked)
        self._drag_banner = QLabel(I18N.t("drag_banner", self._lang))
        self._drag_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_banner.setWordWrap(True)
        self._drag_banner.setStyleSheet(
            """
            QLabel {
                color: #1A1228;
                background-color: #FFD166;
                border-radius: 7px;
                padding: 5px 10px;
            }
            """
        )
        self._drag_banner.hide()
        self._root.addWidget(self._drag_banner)

        # Compact Pill widget (shown when idle >= auto_collapse_seconds)
        self._pill_label = QLabel("")
        self._pill_label.setObjectName("CompactPill")
        self._pill_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pill_label.setMinimumSize(220, 28)
        self._pill_label.setMaximumHeight(32)
        self._pill_label.hide()
        self._root.addWidget(self._pill_label)

        # Expanded detail container (hidden cleanly in Compact Pill mode)
        self._detail_container = QWidget()
        self._detail_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._detail_layout = QVBoxLayout(self._detail_container)
        self._detail_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.setSpacing(6)
        self._root.addWidget(self._detail_container)

        # Active work mode status badge (top bar)
        self._mode_badge = QLabel("")
        self._mode_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_badge.setWordWrap(True)
        self._mode_badge.hide()
        self._detail_layout.addWidget(self._mode_badge)

        # Dual comparison verdict banner
        self._compare_banner = QFrame()
        self._compare_banner.setObjectName("CompareBanner")
        self._compare_banner.setStyleSheet(
            """
            QFrame#CompareBanner {
                background-color: rgba(24, 18, 42, 236);
                border: 1.5px solid #FFD166;
                border-radius: 8px;
            }
            """
        )
        banner_lay = QHBoxLayout(self._compare_banner)
        banner_lay.setContentsMargins(12, 6, 12, 6)
        banner_lay.setSpacing(10)

        self._compare_headline = QLabel("")
        self._compare_headline.setStyleSheet("color: #FFD166;")
        banner_lay.addWidget(self._compare_headline)

        self._compare_detail = QLabel("")
        self._compare_detail.setStyleSheet("color: #E2D4FA;")
        self._compare_detail.setWordWrap(True)
        banner_lay.addWidget(self._compare_detail, stretch=1)

        self._compare_banner.hide()
        self._detail_layout.addWidget(self._compare_banner)

        self._cards_row = QHBoxLayout()
        self._cards_row.setContentsMargins(0, 0, 0, 0)
        self._cards_row.setSpacing(10)
        self._detail_layout.addLayout(self._cards_row)

        # Footer bar
        self._footer = QLabel(self._fmt_hk("footer_waiting", self._lang))
        self._footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._footer.setWordWrap(True)
        self._footer.setStyleSheet(
            """
            QLabel {
                color: #C7B8EA;
                background-color: rgba(16, 10, 28, 200);
                border: 1px solid rgba(168, 118, 255, 110);
                border-radius: 6px;
                padding: 3px 10px;
            }
            """
        )
        self._detail_layout.addWidget(self._footer)
        self.setWindowOpacity(0.92)

    def _apply_shell_fonts(self) -> None:
        fam = _ui_font_family(self._lang)
        base_px = self._font_size_px
        note_px = max(9, base_px - 1)
        self._drag_banner.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._pill_label.setFont(_make_px_font(fam, 12, QFont.Weight.DemiBold))
        self._mode_badge.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._compare_headline.setFont(_make_px_font(fam, base_px, QFont.Weight.Bold))
        self._compare_detail.setFont(_make_px_font(fam, note_px))
        self._footer.setFont(_make_px_font(fam, note_px))

    def _compute_card_widths(self, card_count: int) -> tuple[int, int]:
        """Scale card width smoothly with font size while keeping inside screen width."""
        screen = self.screen()
        avail_w = screen.availableGeometry().width() if screen is not None else 1920
        scale = self._font_size_px / float(DEFAULT_OVERLAY_FONT_PX)
        if card_count >= 2:
            raw_max = int(round((350 if avail_w < 1500 else 410) * scale))
            max_w = max(280, min(raw_max, (avail_w - 68) // 2))
            min_w = max(250, min(int(round(310 * scale)), max_w))
        else:
            raw_max = int(round(415 * scale))
            max_w = max(310, min(raw_max, avail_w - 48))
            min_w = max(280, min(int(round(330 * scale)), max_w))
        return min_w, max_w

    def _clear_cards(self) -> None:
        self._compare_banner.hide()
        while self._cards_row.count():
            item = self._cards_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def clear_wm_panel(self) -> None:
        self.wm_window.clear_wm_panel()

    def show_preview_card(self) -> None:
        """Render a realistic sample card for live drag positioning & font size preview."""
        self._last_rendered_signature = None
        self._display_mode = "preview"
        self.expand_from_pill(restart_timer=not self._drag_unlocked)
        lang = self._lang
        min_w, max_w = self._compute_card_widths(1)
        self._clear_cards()
        sample = _build_sample_preview_card()
        self._cards_row.addWidget(
            SingleRivenCardWidget(
                sample,
                lang=lang,
                show_side_label=False,
                highlight_role="normal",
                min_card_width=min_w,
                max_card_width=max_w,
                base_font_px=self._font_size_px,
                theme_colors=self._theme_colors,
            )
        )
        self._apply_shell_fonts()
        self._footer.setText(
            f"{I18N.t('preview_banner_tag', lang)} ({self._font_size_px}px) | {self._fmt_hk('footer_toast', lang)}"
        )
        self.apply_position()
        self.force_topmost()

    def show_toast(self, message: Optional[str] = None, duration_ms: int = 2500) -> None:
        self._last_rendered_signature = None
        self._display_mode = "toast"
        self._last_message = message
        self._clear_cards()
        lang = self._lang
        min_w, max_w = self._compute_card_widths(1)
        box = QFrame()
        box.setObjectName("ToastCard")
        box.setMinimumWidth(min_w)
        box.setMaximumWidth(max_w)
        box.setStyleSheet(
            """
            QFrame#ToastCard {
                background-color: rgba(18, 12, 32, 224);
                border: 1.5px solid rgba(168, 118, 255, 190);
                border-radius: 10px;
            }
            """
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 11, 16, 11)
        lbl = QLabel(message or self._fmt_hk("startup_toast", lang))
        lbl.setFont(_make_px_font(_ui_font_family(lang), self._font_size_px, QFont.Weight.DemiBold))
        lbl.setStyleSheet("color: #E9D5FF;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self._cards_row.addWidget(box)
        self._apply_shell_fonts()
        self._footer.setText(self._fmt_hk("footer_toast", lang))
        self.apply_position()
        self.force_topmost()
        if duration_ms > 0:
            QTimer.singleShot(duration_ms, self._hide_if_toast)

    def _hide_if_toast(self) -> None:
        if self._drag_unlocked:
            return
        if self._cards_row.count() == 1:
            w = self._cards_row.itemAt(0).widget()
            if w is not None and w.objectName() == "ToastCard":
                self._display_mode = "none"
                self.hide()

    def show_wm_loading(self, weapon_display: str) -> None:
        self._collapse_timer.stop()
        self.wm_window.show_wm_loading(weapon_display)

    def show_wm_report(self, report: WMMarketReport, weapon_display: Optional[str] = None) -> None:
        if self._is_compact_pill and self._last_cards:
            self.expand_from_pill(restart_timer=True)
            self.apply_position()
            self.force_topmost()
        else:
            self.arm_auto_collapse_timer()
        self.wm_window.show_wm_report(report, weapon_display=weapon_display)

    def show_wm_notice(self, message: str, is_warning: bool = True, auto_hide_if_empty_ms: int = 3500) -> None:
        auto_ms = auto_hide_if_empty_ms if self._cards_row.count() == 0 else 0
        self.wm_window.show_wm_notice(message, is_warning=is_warning, auto_hide_ms=auto_ms)
        self.arm_auto_collapse_timer()

    def show_monitoring_waiting(
        self,
        message: Optional[str] = None,
        *,
        keep_pill_if_collapsed: bool = False,
    ) -> None:
        """Always display compact standby pill (e.g. '单卡:识别中...' / '双卡:识别中...') while waiting."""
        self._last_rendered_signature = None
        self._display_mode = "waiting"
        self._last_message = message
        self._clear_cards()
        self.wm_window.clear_wm_panel()
        self.synergy_window.clear_alert()
        self.relic_window.clear_relic()
        lang = self._lang
        self._apply_shell_fonts()
        self._footer.setText(self._fmt_hk("footer_waiting", lang))
        self.collapse_to_pill()

    def show_debounce_waiting(self, mode: str) -> None:
        """Show compact standby pill during 2-second mode-switch debounce before starting auto-OCR."""
        self._last_rendered_signature = None
        self._work_mode = mode
        self._pending_debounce = True
        self._refresh_mode_badge()
        self._display_mode = "debounce"
        self._clear_cards()
        self.wm_window.clear_wm_panel()
        self.synergy_window.clear_alert()
        self.relic_window.clear_relic()
        lang = self._lang
        self._apply_shell_fonts()
        self._footer.setText(self._fmt_hk("footer_toast", lang))
        self.collapse_to_pill()

    def show_manual_mode_standby(self, mode: str) -> None:
        """Show compact standby pill ('聊天F9抓取' / '遗物F9抓取') for F9-triggered modes."""
        self._last_rendered_signature = None
        self._work_mode = mode
        self._pending_debounce = False
        self._refresh_mode_badge()
        self._display_mode = "manual_standby"
        self._clear_cards()
        self.wm_window.clear_wm_panel()
        self.synergy_window.clear_alert()
        self.relic_window.clear_relic()
        lang = self._lang
        self._apply_shell_fonts()
        self._footer.setText(self._fmt_hk("footer_manual", lang))
        self.collapse_to_pill()

    def show_relic_rewards(
        self,
        reward_items: Sequence[object],
        elapsed_ms: Optional[float] = None,
        anchor_ratios: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        """Display Void Relic 1~4 compact price cards directly above the reward selection area."""
        self._last_rendered_signature = None
        self._work_mode = MODE_RELIC_REWARDS
        self._pending_debounce = False
        self._refresh_mode_badge()
        self._display_mode = "relic"
        self._is_compact_pill = False
        self._last_relic_items = list(reward_items)
        self._last_elapsed_ms = elapsed_ms
        self._clear_cards()
        self.wm_window.clear_wm_panel()
        self.synergy_window.clear_alert()
        self.hide()
        self.relic_window.show_relic_rewards(
            reward_items,
            elapsed_ms=elapsed_ms,
            anchor_ratios=anchor_ratios,
        )
        self.arm_auto_collapse_timer()

    def show_error(self, error_msg: str) -> None:
        self._last_rendered_signature = None
        self._display_mode = "error"
        self._last_message = error_msg
        self.expand_from_pill(restart_timer=True)
        self._clear_cards()
        lang = self._lang
        min_w, max_w = self._compute_card_widths(1)
        box = QFrame()
        box.setObjectName("ErrorCard")
        box.setMinimumWidth(min_w)
        box.setMaximumWidth(max_w)
        box.setStyleSheet(
            """
            QFrame#ErrorCard {
                background-color: rgba(36, 14, 24, 232);
                border: 1.5px solid rgba(248, 113, 113, 190);
                border-radius: 10px;
            }
            """
        )
        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 10, 14, 10)
        lbl = QLabel(f"{I18N.t('error_prefix', lang)}{error_msg}")
        lbl.setFont(_make_px_font(_ui_font_family(lang), self._font_size_px))
        lbl.setStyleSheet("color: #FCA5A5;")
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        self._cards_row.addWidget(box)
        self._apply_shell_fonts()
        self._footer.setText(self._fmt_hk("footer_active", lang))
        self.apply_position()
        self.force_topmost()

    def update_cards(
        self,
        cards: Sequence[EvaluatedRivenCard],
        elapsed_ms: Optional[float] = None,
        layout_label: str = "",
        comparison: Optional[DualComparisonResult] = None,
        lang: Optional[str] = None,
        force_redraw: bool = False,
    ) -> None:
        """Render 1 (single view) or 2 (dual reroll comparison) evaluated cards.

        Silently skips redraw / `adjustSize()` / repositioning when the structured
        card data signature, language, and font size are identical to the already
        rendered overlay (unless `force_redraw=True`).
        """
        if lang in SUPPORTED_LANGS:
            self._lang = lang
        active_lang = self._lang

        data_sig: Optional[object] = None
        if len(cards) == 2:
            data_sig = build_reroll_data_signature(cards[0], cards[1])
        elif len(cards) == 1:
            data_sig = build_card_data_signature(cards[0].riven, cards[0].evaluation)

        render_key = (active_lang, self._font_size_px, len(cards), data_sig) if data_sig is not None else None
        if (
            not force_redraw
            and render_key is not None
            and self._last_rendered_signature is not None
            and self._display_mode == "cards"
            and self.isVisible()
        ):
            prev_lang, prev_px, prev_n, prev_sig = self._last_rendered_signature
            if prev_lang == active_lang and prev_px == self._font_size_px and prev_n == len(cards):
                is_same = (
                    reroll_stat_numbers_equivalent(data_sig, prev_sig)
                    if len(cards) == 2
                    else cards_stat_numbers_equivalent(data_sig, prev_sig)
                )
                if is_same:
                    return

        self._last_rendered_signature = render_key
        self._display_mode = "cards"
        self.expand_from_pill(restart_timer=True)
        self._last_cards = list(cards)
        self._last_elapsed_ms = elapsed_ms
        self._last_layout_label = layout_label
        self._last_comparison = comparison

        min_w, max_w = self._compute_card_widths(len(cards))
        self._clear_cards()
        is_dual = len(cards) == 2

        if is_dual:
            comp = comparison or compare_riven_evaluations(cards[0].evaluation, cards[1].evaluation)
            headline, detail = _localize_comparison(comp, cards[0].evaluation, cards[1].evaluation, active_lang)
            self._compare_headline.setText(headline)
            self._compare_detail.setText(detail)
            if comp.winner == "tie":
                self._compare_banner.setStyleSheet(
                    """
                    QFrame#CompareBanner {
                        background-color: rgba(24, 18, 42, 236);
                        border: 1.5px solid #93C5FD;
                        border-radius: 8px;
                    }
                    """
                )
                self._compare_headline.setStyleSheet("color: #93C5FD;")
            else:
                self._compare_banner.setStyleSheet(
                    """
                    QFrame#CompareBanner {
                        background-color: rgba(24, 18, 42, 236);
                        border: 1.5px solid #FFD166;
                        border-radius: 8px;
                    }
                    """
                )
                self._compare_headline.setStyleSheet("color: #FFD166;")
            self._compare_banner.show()

            left_role = "winner" if comp.winner == "left" else ("loser" if comp.winner == "right" else "normal")
            right_role = "winner" if comp.winner == "right" else ("loser" if comp.winner == "left" else "normal")
            self._cards_row.addWidget(
                SingleRivenCardWidget(
                    cards[0],
                    lang=active_lang,
                    show_side_label=True,
                    highlight_role=left_role,
                    min_card_width=min_w,
                    max_card_width=max_w,
                    base_font_px=self._font_size_px,
                    enable_rainbow_98=False,
                    theme_colors=self._theme_colors,
                )
            )
            self._cards_row.addWidget(
                SingleRivenCardWidget(
                    cards[1],
                    lang=active_lang,
                    show_side_label=True,
                    highlight_role=right_role,
                    min_card_width=min_w,
                    max_card_width=max_w,
                    base_font_px=self._font_size_px,
                    enable_rainbow_98=True,
                    theme_colors=self._theme_colors,
                )
            )
        else:
            for card in cards:
                self._cards_row.addWidget(
                    SingleRivenCardWidget(
                        card,
                        lang=active_lang,
                        show_side_label=False,
                        highlight_role="normal",
                        min_card_width=min_w,
                        max_card_width=max_w,
                        base_font_px=self._font_size_px,
                        theme_colors=self._theme_colors,
                    )
                )

        parts: List[str] = [I18N.t("monitoring_tag", active_lang)]
        loc_layout = _localize_layout_label(layout_label, active_lang)
        if loc_layout:
            parts.append(loc_layout)
        if elapsed_ms is not None:
            parts.append(I18N.t("elapsed_ms", active_lang, ms=elapsed_ms))
        parts.append(self._fmt_hk("footer_active", active_lang))
        self._apply_shell_fonts()
        self._footer.setText(" | ".join(parts))

        self.relic_window.clear_relic()
        self.apply_position()
        self.force_topmost()

        # In reroll (dual-card) mode, auto-trigger independent S-grade + compound synergy popup
        if is_dual or self._work_mode == MODE_REROLL:
            synergy_alert = analyze_reroll_synergy_alert(cards)
            if synergy_alert is not None:
                self.synergy_window.show_alert(synergy_alert)
            else:
                self.synergy_window.clear_alert()
        else:
            self.synergy_window.clear_alert()

    def apply_position(self) -> None:
        """Auto-size to content and place at saved relative ratio or default top-right."""
        self._root.activate()
        self._outer.activate()
        self._shell_frame.adjustSize()
        self.adjustSize()
        hint = self.sizeHint()
        if hint.isValid():
            self.resize(hint)

        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            if self._pos_x_ratio is not None and self._pos_y_ratio is not None:
                x = geo.left() + int(round(self._pos_x_ratio * geo.width()))
                y = geo.top() + int(round(self._pos_y_ratio * geo.height()))
            else:
                x = geo.right() - self.width() - self._margin_x
                y = geo.top() + self._margin_y
            x, y = self._clamp_to_screen(x, y)
            self.move(x, y)

    def _clamp_to_screen(self, x: int, y: int) -> tuple[int, int]:
        screen = self.screen()
        if screen is None:
            return x, y
        geo = screen.availableGeometry()
        max_x = max(geo.left(), geo.right() - self.width() + 1)
        max_y = max(geo.top(), geo.bottom() - self.height() + 1)
        clamped_x = max(geo.left(), min(int(x), max_x))
        clamped_y = max(geo.top(), min(int(y), max_y))
        return clamped_x, clamped_y

    def _compute_current_ratios(self) -> tuple[float, float]:
        screen = self.screen()
        if screen is None:
            return 0.75, 0.05
        geo = screen.availableGeometry()
        w = max(1, geo.width())
        h = max(1, geo.height())
        rx = max(0.0, min(1.0, round((self.x() - geo.left()) / float(w), 5)))
        ry = max(0.0, min(1.0, round((self.y() - geo.top()) / float(h), 5)))
        return rx, ry

    # ---- Mouse drag handling (active ONLY while `_drag_unlocked` is True) ----

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_unlocked and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_unlocked and self._dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            target = event.globalPosition().toPoint() - self._drag_offset
            screen = self.screen()
            if screen is not None:
                siblings = [w.frameGeometry() for w in self.all_overlay_windows if w is not self and w.isVisible()]
                cx, cy = snap_window_position(
                    target.x(),
                    target.y(),
                    self.width(),
                    self.height(),
                    screen.availableGeometry(),
                    siblings,
                )
            else:
                cx, cy = self._clamp_to_screen(target.x(), target.y())
            self.move(cx, cy)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_unlocked and self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            rx, ry = self._compute_current_ratios()
            self._pos_x_ratio = rx
            self._pos_y_ratio = ry
            self.position_changed.emit(rx, ry)
            self.window_position_changed.emit("riven", rx, ry)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # Backwards-compatible aliases
    position_top_right = apply_position
    position_top_left = apply_position
