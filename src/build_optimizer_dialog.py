"""Themed Riven-Aware Local Build Optimizer dialog for 裂罅透镜 (Riven Lens).

Displays the optimal 7 Mods + Arcane complementing the currently scanned Riven
(computed locally in <30ms), supports 1-click scenario switching (`Steel Path Grineer`,
`General All-Factions`, `Boss Direct DPS`) and a `No Faction/Bane Mods` toggle, and
can open the resulting build in WFSim (`https://wfsim.app`) via share link.
"""

from __future__ import annotations

from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.build_optimizer import (
    SCENARIO_BOSS_DIRECT,
    SCENARIO_GENERAL,
    SCENARIO_KEYS,
    SCENARIO_STEEL_PATH,
    BuildOptimizationResult,
    optimize_build_for_riven,
)
from src.evaluator import RivenEvaluation
from src.i18n import I18N, SUPPORTED_LANGS
from src.riven_parser import RivenData
from src.settings_manager import DEFAULT_THEME_PRESET, UserSettings, resolve_theme_colors
from src.tray_manager import load_app_icon


def _blend_hex(c1_hex: str, c2_hex: str, ratio: float) -> str:
    a = QColor(c1_hex)
    b = QColor(c2_hex)
    r = max(0, min(255, int(round(a.red() * (1.0 - ratio) + b.red() * ratio))))
    g = max(0, min(255, int(round(a.green() * (1.0 - ratio) + b.green() * ratio))))
    bl = max(0, min(255, int(round(a.blue() * (1.0 - ratio) + b.blue() * ratio))))
    return f"#{r:02X}{g:02X}{bl:02X}"


def _contrast_text_hex(bg_hex: str) -> str:
    c = QColor(bg_hex)
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#0B0F19" if lum >= 150 else "#FFFFFF"


class BuildOptimizerDialog(QDialog):
    """Interactive local Riven-aware 7-Mod + Arcane optimizer with 1-click WFSim export."""

    send_optimized_to_wfsim = pyqtSignal(object, object, object)  # (RivenEvaluation, RivenData, BuildOptimizationResult)

    def __init__(
        self,
        settings: UserSettings,
        lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._lang = lang if lang in SUPPORTED_LANGS else "en_US"
        self._theme_colors = resolve_theme_colors(settings.theme_preset, settings.theme_colors)
        self._scenario: str = SCENARIO_STEEL_PATH
        self._no_bane: bool = False
        self._current_eval: Optional[RivenEvaluation] = None
        self._current_riven: Optional[RivenData] = None
        self._last_result: Optional[BuildOptimizationResult] = None
        self._scenario_btns: Dict[str, QPushButton] = {}
        self._slot_frames: list[QFrame] = []

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(680, 560)
        self.resize(740, 610)
        self.setModal(False)

        self._build_ui()
        self._apply_stylesheet()
        self.retranslate_ui(self._lang)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # Header
        self._hdr_frame = QFrame()
        hdr_lay = QVBoxLayout(self._hdr_frame)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(3)
        self._lbl_title = QLabel("")
        self._lbl_title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold))
        self._lbl_weapon_meta = QLabel("")
        self._lbl_weapon_meta.setWordWrap(True)
        hdr_lay.addWidget(self._lbl_title)
        hdr_lay.addWidget(self._lbl_weapon_meta)
        root.addWidget(self._hdr_frame)

        # Scenario selector bar + No-Bane toggle
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        for skey in SCENARIO_KEYS:
            btn = QPushButton("")
            btn.setObjectName("ScenarioPillBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, k=skey: self._set_scenario(k))
            self._scenario_btns[skey] = btn
            ctrl_row.addWidget(btn)
        ctrl_row.addStretch(1)

        self._chk_no_bane = QCheckBox("")
        self._chk_no_bane.toggled.connect(self._on_no_bane_toggled)
        ctrl_row.addWidget(self._chk_no_bane)
        root.addLayout(ctrl_row)

        # 2 x 4 Mod Grid (7 optimized Mods + Slot 8 Riven)
        self._grid_container = QFrame()
        self._mod_grid = QGridLayout(self._grid_container)
        self._mod_grid.setContentsMargins(10, 10, 10, 10)
        self._mod_grid.setHorizontalSpacing(10)
        self._mod_grid.setVerticalSpacing(10)
        root.addWidget(self._grid_container, stretch=1)

        # Arcane & Active Elements summary bar
        self._summary_frame = QFrame()
        sum_lay = QHBoxLayout(self._summary_frame)
        sum_lay.setContentsMargins(12, 9, 12, 9)
        sum_lay.setSpacing(14)
        self._lbl_arcane = QLabel("")
        self._lbl_arcane.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self._lbl_elements = QLabel("")
        self._lbl_elements.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        sum_lay.addWidget(self._lbl_arcane, stretch=1)
        sum_lay.addWidget(self._lbl_elements)
        root.addWidget(self._summary_frame)

        # Smart Rationale bullets
        self._rationale_frame = QFrame()
        rat_lay = QVBoxLayout(self._rationale_frame)
        rat_lay.setContentsMargins(12, 9, 12, 9)
        rat_lay.setSpacing(4)
        self._lbl_rationale_hdr = QLabel("")
        self._lbl_rationale_hdr.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self._lbl_rationale_body = QLabel("")
        self._lbl_rationale_body.setWordWrap(True)
        rat_lay.addWidget(self._lbl_rationale_hdr)
        rat_lay.addWidget(self._lbl_rationale_body)
        root.addWidget(self._rationale_frame)

        # Footer action row
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._lbl_disclaimer = QLabel("")
        self._lbl_disclaimer.setWordWrap(True)
        footer.addWidget(self._lbl_disclaimer, stretch=1)

        self._btn_send_wfsim = QPushButton("")
        self._btn_send_wfsim.setEnabled(False)
        self._btn_send_wfsim.clicked.connect(self._on_send_wfsim_clicked)
        footer.addWidget(self._btn_send_wfsim)

        self._btn_close = QPushButton("")
        self._btn_close.setObjectName("SecondaryBtn")
        self._btn_close.clicked.connect(self.hide)
        footer.addWidget(self._btn_close)
        root.addLayout(footer)

    def _apply_stylesheet(self) -> None:
        colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#EFE4FF")

        surface = _blend_hex(bg, "#FFFFFF", 0.06)
        surface_hdr = _blend_hex(bg, border, 0.14)
        input_bg = _blend_hex(bg, "#000000", 0.22)
        border_soft = _blend_hex(bg, border, 0.50)
        muted = _blend_hex(text, border, 0.35)
        btn_bg = _blend_hex(bg, border, 0.68)
        btn_hover = _blend_hex(bg, border, 0.85)
        btn_fg = _contrast_text_hex(btn_bg)
        sec_bg = _blend_hex(bg, border, 0.18)
        sec_hover = _blend_hex(bg, border, 0.30)
        pill_fg = _contrast_text_hex(accent)

        self.setStyleSheet(
            f"""
            QDialog, QWidget {{
                background-color: {bg};
                color: {text};
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            QPushButton:disabled {{
                background-color: {_blend_hex(bg, "#FFFFFF", 0.10)};
                color: {_blend_hex(bg, text, 0.45)};
                border-color: {_blend_hex(bg, border, 0.28)};
            }}
            QPushButton#SecondaryBtn {{
                background-color: {sec_bg};
                color: {text};
                border: 1px solid {border_soft};
            }}
            QPushButton#SecondaryBtn:hover {{
                background-color: {sec_hover};
            }}
            QPushButton#ScenarioPillBtn {{
                background-color: {sec_bg};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 13px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#ScenarioPillBtn:hover {{
                background-color: {sec_hover};
            }}
            QPushButton#ScenarioPillBtn:checked {{
                background-color: {accent};
                color: {pill_fg};
                border-color: {accent};
            }}
            QCheckBox {{
                spacing: 7px;
                color: {text};
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1.5px solid {border};
                background-color: {input_bg};
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent};
                border-color: {accent};
            }}
            """
        )
        self._hdr_frame.setStyleSheet(
            f"QFrame {{ background-color: {surface_hdr}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        self._grid_container.setStyleSheet(
            f"QFrame {{ background-color: {surface}; border: 1px solid {border_soft}; border-radius: 10px; }}"
        )
        self._summary_frame.setStyleSheet(
            f"QFrame {{ background-color: {surface_hdr}; border: 1px solid {border_soft}; border-radius: 8px; }}"
        )
        self._rationale_frame.setStyleSheet(
            f"QFrame {{ background-color: {surface}; border: 1px solid {border_soft}; border-radius: 8px; }}"
        )
        self._lbl_title.setStyleSheet(f"color: {accent}; background: transparent;")
        self._lbl_weapon_meta.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent;")
        self._lbl_arcane.setStyleSheet(f"color: {accent}; background: transparent;")
        self._lbl_elements.setStyleSheet(f"color: {text}; background: transparent;")
        self._lbl_rationale_hdr.setStyleSheet(f"color: {accent}; background: transparent;")
        self._lbl_rationale_body.setStyleSheet(f"color: {text}; font-size: 12px; background: transparent;")
        self._lbl_disclaimer.setStyleSheet(f"color: {muted}; font-size: 11px;")

        if self._last_result is not None:
            self._render_result(self._last_result)

    # ---- Public API ----

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        self._theme_colors = resolve_theme_colors("custom", colors)
        self._apply_stylesheet()

    def set_language(self, lang: str) -> None:
        self.retranslate_ui(lang)

    def retranslate_ui(self, lang: str) -> None:
        self._lang = lang if lang in SUPPORTED_LANGS else "en_US"
        self.setWindowTitle(I18N.t("panel.opt_dialog_title", self._lang))
        self._lbl_title.setText(I18N.t("panel.opt_dialog_title", self._lang))
        self._chk_no_bane.setText(I18N.t("panel.opt_no_bane_chk", self._lang))
        self._lbl_rationale_hdr.setText(I18N.t("panel.opt_rationale_hdr", self._lang))
        self._lbl_disclaimer.setText(I18N.t("panel.opt_disclaimer", self._lang))
        self._btn_send_wfsim.setText(I18N.t("panel.opt_send_wfsim_btn", self._lang))
        self._btn_close.setText(I18N.t("panel.fav_close_btn", self._lang))

        for skey, btn in self._scenario_btns.items():
            btn.blockSignals(True)
            btn.setChecked(skey == self._scenario)
            btn.blockSignals(False)
            btn.setText(I18N.t(f"panel.opt_scenario_{skey}", self._lang))

        if self._current_eval is not None:
            self._recompute()
        else:
            self._lbl_weapon_meta.setText(I18N.t("panel.opt_need_card", self._lang))

    def show_for_card(self, evaluation: RivenEvaluation, riven: RivenData) -> None:
        self._current_eval = evaluation
        self._current_riven = riven
        self._recompute()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _set_scenario(self, scenario_key: str) -> None:
        if scenario_key not in SCENARIO_KEYS:
            return
        self._scenario = scenario_key
        for skey, btn in self._scenario_btns.items():
            btn.blockSignals(True)
            btn.setChecked(skey == self._scenario)
            btn.blockSignals(False)
        self._recompute()

    def _on_no_bane_toggled(self, checked: bool) -> None:
        self._no_bane = bool(checked)
        self._recompute()

    def _recompute(self) -> None:
        if self._current_eval is None:
            self._btn_send_wfsim.setEnabled(False)
            return
        res = optimize_build_for_riven(
            self._current_eval,
            scenario=self._scenario,
            allow_bane_mods=not self._no_bane,
        )
        self._last_result = res
        self._render_result(res)
        self._btn_send_wfsim.setEnabled(True)

    def _clear_mod_grid(self) -> None:
        while self._mod_grid.count():
            item = self._mod_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slot_frames.clear()

    def _render_result(self, res: BuildOptimizationResult) -> None:
        ev = self._current_eval
        lang = self._lang
        is_zh = lang in ("zh_CN", "zh_TW")

        if ev is not None:
            w_name = ev.weapon_name if is_zh else (ev.weapon_name_en or ev.weapon_name or "")
            cls_txt = I18N.weapon_class(ev.weapon_class, lang)
            arch_txt = (ev.matched_archetype_name if is_zh else ev.matched_archetype_name_en) or ""
            meta_parts = [f"{w_name} [{cls_txt}]"]
            if arch_txt:
                meta_parts.append(arch_txt)
            meta_parts.append(
                I18N.t("panel.opt_riven_gain_badge", lang, gain=res.riven_gain_pct)
            )
            self._lbl_weapon_meta.setText("  ·  ".join(meta_parts))

        colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#EFE4FF")
        card_bg = _blend_hex(bg, "#000000", 0.20)
        riven_bg = _blend_hex(bg, accent, 0.16)
        muted = _blend_hex(text, border, 0.35)

        self._clear_mod_grid()

        # Slots 0..6: 7 optimized Mods
        for idx, slot in enumerate(res.mods[:7]):
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background-color: {card_bg}; border: 1px solid {_blend_hex(bg, border, 0.55)}; border-radius: 8px; }}"
            )
            lay = QVBoxLayout(card)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(3)

            idx_lbl = QLabel(f"#{idx + 1}")
            idx_lbl.setStyleSheet(f"color: {muted}; font-size: 10px; background: transparent; border: none;")
            primary = slot.mod.name_zh if is_zh else slot.mod.name_en
            secondary = slot.mod.name_en if is_zh else slot.mod.name_zh
            name_lbl = QLabel(primary)
            name_lbl.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
            name_lbl.setStyleSheet(f"color: {text}; background: transparent; border: none;")
            name_lbl.setWordWrap(True)

            sub_lbl = QLabel(secondary)
            sub_lbl.setStyleSheet(f"color: {muted}; font-size: 10px; background: transparent; border: none;")
            sub_lbl.setWordWrap(True)

            gain_color = "#6EE7B7" if slot.marginal_gain_pct >= 0 else "#FCA5A5"
            gain_lbl = QLabel(I18N.t("panel.opt_mod_gain", lang, gain=slot.marginal_gain_pct))
            gain_lbl.setStyleSheet(f"color: {gain_color}; font-size: 11px; font-weight: bold; background: transparent; border: none;")

            lay.addWidget(idx_lbl)
            lay.addWidget(name_lbl)
            lay.addWidget(sub_lbl)
            lay.addStretch(1)
            lay.addWidget(gain_lbl)

            self._slot_frames.append(card)
            self._mod_grid.addWidget(card, idx // 4, idx % 4)

        # Slot 7 (8th card): Active Scanned Riven
        rcard = QFrame()
        rcard.setStyleSheet(
            f"QFrame {{ background-color: {riven_bg}; border: 1.5px solid {accent}; border-radius: 8px; }}"
        )
        rlay = QVBoxLayout(rcard)
        rlay.setContentsMargins(10, 8, 10, 8)
        rlay.setSpacing(3)

        r_tag = QLabel(I18N.t("panel.opt_riven_slot_tag", lang))
        r_tag.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        r_title = QLabel(
            (self._current_riven.riven_name if self._current_riven and self._current_riven.riven_name else res.weapon_name)
        )
        r_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        r_title.setStyleSheet(f"color: {accent}; background: transparent; border: none;")
        r_title.setWordWrap(True)

        stat_preview = ""
        if ev is not None:
            stat_preview = " · ".join(
                f"{'-' if s.is_negative else '+'}{I18N.stat_name(s.stat_name, lang)}"
                for s in ev.stat_evaluations[:4]
            )
        r_sub = QLabel(stat_preview)
        r_sub.setStyleSheet(f"color: {text}; font-size: 10px; background: transparent; border: none;")
        r_sub.setWordWrap(True)

        r_gain = QLabel(I18N.t("panel.opt_mod_gain", lang, gain=res.riven_gain_pct))
        r_gain.setStyleSheet("color: #6EE7B7; font-size: 11px; font-weight: bold; background: transparent; border: none;")

        rlay.addWidget(r_tag)
        rlay.addWidget(r_title)
        rlay.addWidget(r_sub)
        rlay.addStretch(1)
        rlay.addWidget(r_gain)
        self._slot_frames.append(rcard)
        self._mod_grid.addWidget(rcard, 1, 3)

        # Arcane & Active Elements
        if res.arcane is not None:
            arc_name = f"{res.arcane.name_zh} ({res.arcane.name_en})" if is_zh else res.arcane.name_en
        else:
            arc_name = I18N.t("panel.opt_no_arcane", lang)
        self._lbl_arcane.setText(I18N.t("panel.opt_arcane_label", lang, arcane=arc_name))

        elems = res.active_elements_zh if is_zh else res.active_elements_en
        elem_txt = " + ".join(elems) if elems else "-"
        self._lbl_elements.setText(I18N.t("panel.opt_elements_label", lang, elems=elem_txt))

        # Rationale bullets
        reasons = res.rationale_zh if is_zh else res.rationale_en
        self._lbl_rationale_body.setText("\n".join(f"• {r}" for r in reasons))

    def _on_send_wfsim_clicked(self) -> None:
        if self._current_eval is not None and self._last_result is not None:
            self.send_optimized_to_wfsim.emit(self._current_eval, self._current_riven, self._last_result)
