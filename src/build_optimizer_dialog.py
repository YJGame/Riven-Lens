"""Themed Interactive Riven-Aware Local Build Optimizer dialog for 裂罅透镜 (Riven Lens).

Displays the optimal 7 Mods + Arcane complementing the currently scanned Riven
(computed locally in <30ms), lets the user directly swap/edit any of the 7 Mod slots
or the Weapon Arcane with live marginal-DPS & elemental recalculation, supports
1-click scenario switching (`Steel Path Grineer`, `General All-Factions`, `Boss Direct DPS`)
and `No Faction/Bane Mods`, and opens the active (recommended or customized) build +
scanned Riven in `https://wfsim.app` via v2 share link.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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
    SCENARIO_KEYS,
    SCENARIO_STEEL_PATH,
    BuildOptimizationResult,
    ModDef,
    evaluate_custom_build_for_riven,
    get_arcane_pool,
    get_mod_pool,
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
    """Interactive local Riven-aware 7-Mod + Arcane optimizer with live user customization."""

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
        self._recommended_result: Optional[BuildOptimizationResult] = None
        self._last_result: Optional[BuildOptimizationResult] = None
        self._slot_mod_ids: List[Optional[str]] = [None] * 7
        self._selected_arcane_id: Optional[str] = None
        self._is_customized: bool = False
        self._conflict_notice: Optional[tuple[str, str]] = None
        self._updating_widgets: bool = False

        self._scenario_btns: Dict[str, QPushButton] = {}
        self._slot_frames: List[QFrame] = []
        self._slot_combos: List[QComboBox] = []

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(740, 600)
        self.resize(820, 650)
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

        # Scenario selector bar + No-Bane toggle + Reset Recommended button
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

        self._btn_reset_rec = QPushButton("")
        self._btn_reset_rec.setObjectName("SecondaryBtn")
        self._btn_reset_rec.setEnabled(False)
        self._btn_reset_rec.clicked.connect(self._on_reset_recommended_clicked)
        ctrl_row.addWidget(self._btn_reset_rec)
        root.addLayout(ctrl_row)

        # 2 x 4 Mod Grid (7 editable Mod cards + Slot 8 Locked Riven)
        self._grid_container = QFrame()
        self._mod_grid = QGridLayout(self._grid_container)
        self._mod_grid.setContentsMargins(10, 10, 10, 10)
        self._mod_grid.setHorizontalSpacing(10)
        self._mod_grid.setVerticalSpacing(10)
        root.addWidget(self._grid_container, stretch=1)

        # Arcane dropdown & Active Elements summary bar
        self._summary_frame = QFrame()
        sum_lay = QHBoxLayout(self._summary_frame)
        sum_lay.setContentsMargins(12, 9, 12, 9)
        sum_lay.setSpacing(10)
        self._lbl_arcane = QLabel("")
        self._lbl_arcane.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self._cmb_arcane = QComboBox()
        self._cmb_arcane.setMinimumWidth(220)
        self._cmb_arcane.currentIndexChanged.connect(self._on_arcane_combo_changed)
        self._lbl_arcane_none = QLabel("")
        self._lbl_arcane_none.hide()
        self._lbl_elements = QLabel("")
        self._lbl_elements.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        sum_lay.addWidget(self._lbl_arcane)
        sum_lay.addWidget(self._cmb_arcane)
        sum_lay.addWidget(self._lbl_arcane_none)
        sum_lay.addStretch(1)
        sum_lay.addWidget(self._lbl_elements)
        root.addWidget(self._summary_frame)

        # Smart Rationale & live analysis bullets
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
        input_bg = _blend_hex(bg, "#000000", 0.28)
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
            QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 600;
            }}
            QComboBox:hover {{
                border-color: {accent};
            }}
            QComboBox QAbstractItemView {{
                background-color: {bg};
                color: {text};
                selection-background-color: {btn_bg};
                selection-color: {btn_fg};
                border: 1px solid {border};
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
        self._lbl_arcane_none.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent;")
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
        self._btn_reset_rec.setText(I18N.t("panel.opt_reset_btn", self._lang))
        self._lbl_arcane.setText(I18N.t("panel.opt_arcane_label", self._lang))
        self._lbl_arcane_none.setText(I18N.t("panel.opt_no_arcane", self._lang))
        self._lbl_rationale_hdr.setText(I18N.t("panel.opt_rationale_hdr", self._lang))
        self._lbl_disclaimer.setText(I18N.t("panel.opt_disclaimer", self._lang))
        self._btn_send_wfsim.setText(I18N.t("panel.opt_send_wfsim_btn", self._lang))
        self._btn_close.setText(I18N.t("panel.fav_close_btn", self._lang))

        for skey, btn in self._scenario_btns.items():
            btn.blockSignals(True)
            btn.setChecked(skey == self._scenario)
            btn.blockSignals(False)
            btn.setText(I18N.t(f"panel.opt_scenario_{skey}", self._lang))

        if self._current_eval is not None and self._last_result is not None:
            self._render_result(self._last_result)
        else:
            self._lbl_weapon_meta.setText(I18N.t("panel.opt_need_card", self._lang))

    def show_for_card(self, evaluation: RivenEvaluation, riven: RivenData) -> None:
        self._current_eval = evaluation
        self._current_riven = riven
        self._recompute_recommended()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def set_slot_mod(self, slot_idx: int, mod_id: Optional[str]) -> None:
        """Programmatically or interactively replace Mod in `slot_idx` (0..6) and live-recalculate."""
        if not (0 <= slot_idx < 7) or self._current_eval is None:
            return
        pool = get_mod_pool(self._current_eval.weapon_class, allow_bane_mods=True)
        mod_by_id: Dict[str, ModDef] = {m.id: m for m in pool}

        new_id = str(mod_id).strip() if mod_id else None
        if new_id and new_id not in mod_by_id:
            return
        prev_id = self._slot_mod_ids[slot_idx]
        if new_id == prev_id:
            return

        self._conflict_notice = None
        if new_id is not None:
            # 1. If already equipped in another slot, swap cleanly instead of duplicating
            for j, existing_id in enumerate(self._slot_mod_ids):
                if j != slot_idx and existing_id == new_id:
                    self._slot_mod_ids[j] = prev_id
                    break
            # 2. Auto-clear mutually exclusive Mod in another slot (e.g. Point Strike vs Critical Delay)
            new_mod = mod_by_id[new_id]
            if new_mod.exclusive_group:
                for j, existing_id in enumerate(self._slot_mod_ids):
                    if j != slot_idx and existing_id and existing_id in mod_by_id:
                        other = mod_by_id[existing_id]
                        if other.exclusive_group == new_mod.exclusive_group:
                            self._slot_mod_ids[j] = None
                            self._conflict_notice = (
                                f"已自动卸下冲突同名/同系 Mod【{other.name_zh}】（与【{new_mod.name_zh}】互斥）",
                                f"Auto-removed conflicting [{other.name_en}] (mutually exclusive with [{new_mod.name_en}])",
                            )
        self._slot_mod_ids[slot_idx] = new_id
        self._evaluate_current_selection()

    def set_selected_arcane(self, arcane_id: Optional[str]) -> None:
        """Programmatically or interactively switch the Weapon Arcane and live-recalculate."""
        if self._current_eval is None:
            return
        norm = str(arcane_id).strip() if arcane_id else None
        if norm == self._selected_arcane_id:
            return
        self._conflict_notice = None
        self._selected_arcane_id = norm
        self._evaluate_current_selection()

    def reset_to_recommended(self) -> None:
        """Restore the auto-recommended 7 Mods + Arcane for the current scenario."""
        if self._recommended_result is None:
            return
        rec = self._recommended_result
        self._slot_mod_ids = [s.mod.id for s in rec.mods[:7]]
        while len(self._slot_mod_ids) < 7:
            self._slot_mod_ids.append(None)
        self._selected_arcane_id = rec.arcane.id if rec.arcane is not None else None
        self._is_customized = False
        self._conflict_notice = None
        self._last_result = rec
        self._btn_reset_rec.setEnabled(False)
        self._render_result(rec)

    # ---- Internal logic ----

    def _set_scenario(self, scenario_key: str) -> None:
        if scenario_key not in SCENARIO_KEYS:
            return
        self._scenario = scenario_key
        for skey, btn in self._scenario_btns.items():
            btn.blockSignals(True)
            btn.setChecked(skey == self._scenario)
            btn.blockSignals(False)
        self._recompute_recommended()

    def _on_no_bane_toggled(self, checked: bool) -> None:
        self._no_bane = bool(checked)
        self._recompute_recommended()

    def _on_reset_recommended_clicked(self) -> None:
        self.reset_to_recommended()

    def _recompute_recommended(self) -> None:
        if self._current_eval is None:
            self._btn_send_wfsim.setEnabled(False)
            self._btn_reset_rec.setEnabled(False)
            return
        rec = optimize_build_for_riven(
            self._current_eval,
            scenario=self._scenario,
            allow_bane_mods=not self._no_bane,
        )
        self._recommended_result = rec
        self._slot_mod_ids = [s.mod.id for s in rec.mods[:7]]
        while len(self._slot_mod_ids) < 7:
            self._slot_mod_ids.append(None)
        self._selected_arcane_id = rec.arcane.id if rec.arcane is not None else None
        self._is_customized = False
        self._conflict_notice = None
        self._last_result = rec
        self._btn_send_wfsim.setEnabled(True)
        self._btn_reset_rec.setEnabled(False)
        self._render_result(rec)

    def _evaluate_current_selection(self) -> None:
        if self._current_eval is None:
            return
        rec = self._recommended_result
        rec_mods = [s.mod.id for s in rec.mods[:7]] if rec is not None else []
        while len(rec_mods) < 7:
            rec_mods.append(None)
        rec_arc = rec.arcane.id if (rec is not None and rec.arcane is not None) else None

        self._is_customized = (self._slot_mod_ids != rec_mods) or (self._selected_arcane_id != rec_arc)
        self._btn_reset_rec.setEnabled(self._is_customized)

        if not self._is_customized and rec is not None:
            self._last_result = rec
            self._render_result(rec)
            return

        ref_score = rec.total_dps_index if rec is not None else None
        custom_res = evaluate_custom_build_for_riven(
            self._current_eval,
            self._slot_mod_ids,
            self._selected_arcane_id,
            scenario=self._scenario,
            reference_score=ref_score,
        )
        self._last_result = custom_res
        self._render_result(custom_res)

    def _on_slot_combo_changed(self, slot_idx: int, combo: QComboBox) -> None:
        if self._updating_widgets:
            return
        mod_id = combo.currentData()
        self.set_slot_mod(slot_idx, mod_id if isinstance(mod_id, str) and mod_id else None)

    def _on_arcane_combo_changed(self, _index: int) -> None:
        if self._updating_widgets:
            return
        arc_id = self._cmb_arcane.currentData()
        self.set_selected_arcane(arc_id if isinstance(arc_id, str) and arc_id else None)

    def _clear_mod_grid(self) -> None:
        while self._mod_grid.count():
            item = self._mod_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slot_frames.clear()
        self._slot_combos.clear()

    def _render_result(self, res: BuildOptimizationResult) -> None:
        ev = self._current_eval
        lang = self._lang
        is_zh = lang in ("zh_CN", "zh_TW")

        self._updating_widgets = True
        try:
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
                if self._is_customized and res.delta_vs_recommended_pct is not None:
                    meta_parts.append(
                        I18N.t("panel.opt_modified_badge", lang, delta=res.delta_vs_recommended_pct)
                    )
                self._lbl_weapon_meta.setText("  ·  ".join(meta_parts))

            colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
            bg = colors.get("bg", "#120C20")
            border = colors.get("border", "#A876FF")
            accent = colors.get("accent", "#FFD166")
            text = colors.get("text", "#EFE4FF")
            card_bg = _blend_hex(bg, "#000000", 0.20)
            edited_bg = _blend_hex(bg, accent, 0.09)
            riven_bg = _blend_hex(bg, accent, 0.16)
            muted = _blend_hex(text, border, 0.35)

            pool = get_mod_pool(res.weapon_class, allow_bane_mods=True)
            mod_by_id: Dict[str, ModDef] = {m.id: m for m in pool}
            gain_by_id: Dict[str, float] = {s.mod.id: s.marginal_gain_pct for s in res.mods}
            rec_ids: List[Optional[str]] = (
                [s.mod.id for s in self._recommended_result.mods[:7]]
                if self._recommended_result is not None
                else []
            )
            while len(rec_ids) < 7:
                rec_ids.append(None)

            self._clear_mod_grid()

            # Slots 0..6: 7 interactive editable Mod cards
            for idx in range(7):
                cur_mid = self._slot_mod_ids[idx]
                cur_mod = mod_by_id.get(cur_mid) if cur_mid else None
                slot_edited = self._is_customized and (cur_mid != rec_ids[idx])

                card = QFrame()
                c_border = accent if slot_edited else _blend_hex(bg, border, 0.55)
                c_bg = edited_bg if slot_edited else card_bg
                card.setStyleSheet(
                    f"QFrame {{ background-color: {c_bg}; border: 1px solid {c_border}; border-radius: 8px; }}"
                )
                lay = QVBoxLayout(card)
                lay.setContentsMargins(9, 7, 9, 7)
                lay.setSpacing(4)

                top_row = QHBoxLayout()
                top_row.setContentsMargins(0, 0, 0, 0)
                idx_lbl = QLabel(f"#{idx + 1}")
                idx_lbl.setStyleSheet(f"color: {muted}; font-size: 10px; background: transparent; border: none;")
                top_row.addWidget(idx_lbl)
                top_row.addStretch(1)
                if slot_edited:
                    ed_lbl = QLabel(I18N.t("panel.opt_slot_modified_tag", lang))
                    ed_lbl.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: bold; background: transparent; border: none;")
                    top_row.addWidget(ed_lbl)
                lay.addLayout(top_row)

                cmb = QComboBox()
                cmb.addItem(I18N.t("panel.opt_empty_slot", lang), "")
                for cand in pool:
                    disp = cand.name_zh if is_zh else cand.name_en
                    cmb.addItem(disp, cand.id)
                sel_idx = cmb.findData(cur_mid or "")
                cmb.setCurrentIndex(sel_idx if sel_idx >= 0 else 0)
                cmb.currentIndexChanged.connect(lambda _i, s_idx=idx, box=cmb: self._on_slot_combo_changed(s_idx, box))
                lay.addWidget(cmb)

                if cur_mod is not None:
                    secondary = cur_mod.name_en if is_zh else cur_mod.name_zh
                    gain_val = gain_by_id.get(cur_mod.id, 0.0)
                    gain_color = "#6EE7B7" if gain_val >= 0 else "#FCA5A5"
                    gain_txt = I18N.t("panel.opt_mod_gain", lang, gain=gain_val)
                else:
                    secondary = "-"
                    gain_color = muted
                    gain_txt = I18N.t("panel.opt_empty_slot", lang)

                sub_lbl = QLabel(secondary)
                sub_lbl.setStyleSheet(f"color: {muted}; font-size: 10px; background: transparent; border: none;")
                sub_lbl.setWordWrap(True)
                lay.addWidget(sub_lbl)

                lay.addStretch(1)
                gain_lbl = QLabel(gain_txt)
                gain_lbl.setStyleSheet(f"color: {gain_color}; font-size: 11px; font-weight: bold; background: transparent; border: none;")
                lay.addWidget(gain_lbl)

                self._slot_frames.append(card)
                self._slot_combos.append(cmb)
                self._mod_grid.addWidget(card, idx // 4, idx % 4)

            # Slot 7 (8th card): Active Scanned Riven (Locked)
            rcard = QFrame()
            rcard.setStyleSheet(
                f"QFrame {{ background-color: {riven_bg}; border: 1.5px solid {accent}; border-radius: 8px; }}"
            )
            rlay = QVBoxLayout(rcard)
            rlay.setContentsMargins(9, 7, 9, 7)
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

            # Arcane selector bar
            arcanes = get_arcane_pool(res.weapon_class)
            self._cmb_arcane.clear()
            if arcanes:
                self._cmb_arcane.show()
                self._lbl_arcane_none.hide()
                self._cmb_arcane.addItem(I18N.t("panel.opt_arcane_none_opt", lang), "")
                for a in arcanes:
                    a_disp = f"{a.name_zh} ({a.name_en})" if is_zh else a.name_en
                    self._cmb_arcane.addItem(a_disp, a.id)
                a_idx = self._cmb_arcane.findData(self._selected_arcane_id or "")
                self._cmb_arcane.setCurrentIndex(a_idx if a_idx >= 0 else 0)
            else:
                self._cmb_arcane.hide()
                self._lbl_arcane_none.show()

            # Active Elements
            elems = res.active_elements_zh if is_zh else res.active_elements_en
            elem_txt = " + ".join(elems) if elems else "-"
            self._lbl_elements.setText(I18N.t("panel.opt_elements_label", lang, elems=elem_txt))

            # Rationale bullets (prepend conflict notice if user triggered an auto-swap/resolve)
            reasons = list(res.rationale_zh if is_zh else res.rationale_en)
            if self._conflict_notice is not None:
                reasons.insert(0, self._conflict_notice[0] if is_zh else self._conflict_notice[1])
            self._lbl_rationale_body.setText("\n".join(f"• {r}" for r in reasons))
        finally:
            self._updating_widgets = False

    def _on_send_wfsim_clicked(self) -> None:
        if self._current_eval is not None and self._last_result is not None:
            self.send_optimized_to_wfsim.emit(self._current_eval, self._current_riven, self._last_result)
