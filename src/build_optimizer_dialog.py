"""Themed Interactive Riven-Aware Local Build Optimizer dialog for 裂罅透镜 (Riven Lens).

Features:
  - Full WFCD + Warframe.market verified Mod & Weapon Arcane catalog per weapon class.
  - Searchable Mod & Arcane dropdowns supporting live keyword & compound-element filtering
    (e.g. typing "腐蚀" shows Toxin + Electricity Mods; "病毒" shows Toxin + Cold Mods).
  - Exact stat values displayed inside every Mod card AND inside the purple Riven card (#8).
  - Live aggregated overall build stats (Damage, Multishot, Fire Rate / Attack Speed,
    Melee Range, Crit Chance, Crit Damage, Status Chance, Elemental, etc.) in the analysis panel.
  - One-click scenario switching, No-Bane toggle, Reset Recommended, and WFSim v2 share-link export.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
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
    ArcaneDef,
    BuildOptimizationResult,
    BuildStatSummary,
    ModDef,
    evaluate_custom_build_for_riven,
    format_mod_stats,
    get_arcane_pool,
    get_mod_pool,
    match_mod_search,
    optimize_build_for_riven,
)
from src.evaluator import RivenEvaluation, StatEvaluation
from src.i18n import I18N, SUPPORTED_LANGS
from src.riven_parser import RivenData
from src.settings_manager import DEFAULT_THEME_PRESET, UserSettings, resolve_theme_colors
from src.tray_manager import load_app_icon

_ROLE_ITEM_ID = int(Qt.ItemDataRole.UserRole) + 1
_ROLE_MOD_OBJ = int(Qt.ItemDataRole.UserRole) + 2
_ROLE_SEARCH_TEXT = int(Qt.ItemDataRole.UserRole) + 3


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


def _format_riven_stat_line(se: StatEvaluation, lang: str) -> str:
    name = I18N.stat_name(se.stat_name, lang)
    val = float(se.value)
    if se.sign == "x":
        return f"x{val:.2f} {name}"
    sign = "-" if se.is_negative else "+"
    unit = (se.unit or "").strip()
    if unit == "%":
        return f"{sign}{abs(val):.1f}% {name}"
    if unit:
        return f"{sign}{abs(val):.1f}{unit} {name}"
    return f"{sign}{abs(val):.1f} {name}"


class _ModFilterProxyModel(QSortFilterProxyModel):
    """Custom filter proxy supporting Mod names, stat keywords, and compound elements (e.g. 腐蚀 -> 毒+电)."""

    def __init__(self, weapon_class: str = "rifle", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._weapon_class = weapon_class
        self._query = ""

    def set_query(self, query: str) -> None:
        q = (query or "").strip()
        if q == self._query:
            return
        self._query = q
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        if not self._query:
            return True
        idx = self.sourceModel().index(source_row, 0, source_parent)
        item_id = idx.data(_ROLE_ITEM_ID) or ""
        if not item_id:
            return False  # hide "(Empty)" while actively searching
        mod_obj = idx.data(_ROLE_MOD_OBJ)
        if isinstance(mod_obj, ModDef):
            return match_mod_search(mod_obj, self._query, self._weapon_class)
        blob = (idx.data(_ROLE_SEARCH_TEXT) or "").lower()
        return all(tok in blob for tok in self._query.lower().split())


class SearchableItemComboBox(QComboBox):
    """Editable QComboBox with instant live filtering (supports compound element queries)."""

    item_id_selected = pyqtSignal(str)  # emits "" for empty/none

    def __init__(self, weapon_class: str = "rifle", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._weapon_class = weapon_class
        self._current_id: str = ""
        self._current_label: str = ""
        self._syncing: bool = False

        self._source_model = QStandardItemModel(self)
        self._proxy = _ModFilterProxyModel(weapon_class=weapon_class, parent=self)
        self._proxy.setSourceModel(self._source_model)

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setModel(self._proxy)
        self.setMaxVisibleItems(14)

        self._completer = QCompleter(self._proxy, self)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setMaxVisibleItems(14)
        self.setCompleter(self._completer)

        le = self.lineEdit()
        if le is not None:
            le.textEdited.connect(self._on_text_edited)
            le.returnPressed.connect(self._on_return_pressed)

        self._completer.activated[QModelIndex].connect(self._on_completer_index_activated)
        self.activated.connect(self._on_combo_activated)

    def populate_mods(
        self,
        mods: Sequence[ModDef],
        *,
        empty_label: str,
        placeholder: str,
        lang: str,
        selected_id: Optional[str],
    ) -> None:
        is_zh = lang in ("zh_CN", "zh_TW")
        self._syncing = True
        try:
            self._proxy.set_query("")
            self._source_model.clear()

            empty_item = QStandardItem(empty_label)
            empty_item.setData("", _ROLE_ITEM_ID)
            empty_item.setData(None, _ROLE_MOD_OBJ)
            empty_item.setData(empty_label.lower(), _ROLE_SEARCH_TEXT)
            self._source_model.appendRow(empty_item)

            for m in mods:
                disp = m.name_zh if is_zh else m.name_en
                st_txt = format_mod_stats(m, self._weapon_class, lang)
                it = QStandardItem(disp)
                it.setToolTip(f"{m.name_zh} ({m.name_en})\n{st_txt}")
                it.setData(m.id, _ROLE_ITEM_ID)
                it.setData(m, _ROLE_MOD_OBJ)
                it.setData(f"{m.name_zh} {m.name_en} {st_txt}".lower(), _ROLE_SEARCH_TEXT)
                self._source_model.appendRow(it)

            le = self.lineEdit()
            if le is not None:
                le.setPlaceholderText(placeholder)
            self._select_by_id(selected_id or "")
        finally:
            self._syncing = False

    def populate_arcanes(
        self,
        arcanes: Sequence[ArcaneDef],
        *,
        none_label: str,
        placeholder: str,
        lang: str,
        selected_id: Optional[str],
    ) -> None:
        is_zh = lang in ("zh_CN", "zh_TW")
        self._syncing = True
        try:
            self._proxy.set_query("")
            self._source_model.clear()

            none_item = QStandardItem(none_label)
            none_item.setData("", _ROLE_ITEM_ID)
            none_item.setData(None, _ROLE_MOD_OBJ)
            none_item.setData(none_label.lower(), _ROLE_SEARCH_TEXT)
            self._source_model.appendRow(none_item)

            for a in arcanes:
                disp = f"{a.name_zh} ({a.name_en})" if is_zh else a.name_en
                desc = a.desc_zh if is_zh else a.desc_en
                it = QStandardItem(disp)
                it.setToolTip(f"{disp}\n{desc}" if desc else disp)
                it.setData(a.id, _ROLE_ITEM_ID)
                it.setData(None, _ROLE_MOD_OBJ)
                it.setData(f"{a.name_zh} {a.name_en} {a.id} {a.desc_zh} {a.desc_en}".lower(), _ROLE_SEARCH_TEXT)
                self._source_model.appendRow(it)

            le = self.lineEdit()
            if le is not None:
                le.setPlaceholderText(placeholder)
            self._select_by_id(selected_id or "")
        finally:
            self._syncing = False

    def _select_by_id(self, target_id: str) -> None:
        self._proxy.set_query("")
        chosen_row = 0
        chosen_text = ""
        for r in range(self._proxy.rowCount()):
            idx = self._proxy.index(r, 0)
            if (idx.data(_ROLE_ITEM_ID) or "") == target_id:
                chosen_row = r
                chosen_text = str(idx.data(Qt.ItemDataRole.DisplayRole) or "")
                break
        self._current_id = target_id
        self._current_label = chosen_text
        self.setCurrentIndex(chosen_row)
        if self.lineEdit() is not None:
            self.lineEdit().setText(chosen_text)

    def filter_count(self, query: str) -> int:
        """Apply filter `query` and return number of matching rows (useful for tests & UX)."""
        self._proxy.set_query(query)
        return self._proxy.rowCount()

    def filtered_item_ids(self, query: str) -> List[str]:
        self._proxy.set_query(query)
        return [str(self._proxy.index(r, 0).data(_ROLE_ITEM_ID) or "") for r in range(self._proxy.rowCount())]

    def _on_text_edited(self, text: str) -> None:
        if self._syncing:
            return
        self._proxy.set_query(text)
        if self._proxy.rowCount() > 0:
            self._completer.complete()

    def _on_return_pressed(self) -> None:
        if self._syncing:
            return
        if self._proxy.rowCount() > 0:
            idx = self._proxy.index(0, 0)
            item_id = str(idx.data(_ROLE_ITEM_ID) or "")
            self._commit_id(item_id)

    def _on_completer_index_activated(self, proxy_idx: QModelIndex) -> None:
        if self._syncing or not proxy_idx.isValid():
            return
        item_id = str(proxy_idx.data(_ROLE_ITEM_ID) or "")
        QTimer.singleShot(0, lambda iid=item_id: self._commit_id(iid))

    def _on_combo_activated(self, row: int) -> None:
        if self._syncing or row < 0 or row >= self._proxy.rowCount():
            return
        item_id = str(self._proxy.index(row, 0).data(_ROLE_ITEM_ID) or "")
        self._commit_id(item_id)

    def _commit_id(self, item_id: str) -> None:
        self._syncing = True
        try:
            self._select_by_id(item_id)
        finally:
            self._syncing = False
        self.item_id_selected.emit(item_id)

    def showPopup(self) -> None:  # noqa: N802
        self._proxy.set_query("")
        super().showPopup()

    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        if self.lineEdit() is not None:
            QTimer.singleShot(0, self.lineEdit().selectAll)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if not self._syncing:
            self._syncing = True
            try:
                self._select_by_id(self._current_id)
            finally:
                self._syncing = False


class BuildOptimizerDialog(QDialog):
    """Interactive local Riven-aware 7-Mod + Arcane optimizer with searchable customization."""

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
        self._slot_combos: List[SearchableItemComboBox] = []
        self._stat_pills: List[QLabel] = []

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(820, 680)
        self.resize(890, 740)
        self.setModal(False)

        self._build_ui()
        self._apply_stylesheet()
        self.retranslate_ui(self._lang)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header
        self._hdr_frame = QFrame()
        self._hdr_frame.setObjectName("HdrFrame")
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

        # 2 x 4 Mod Grid (7 searchable editable Mod cards + Slot 8 Purple Riven card)
        self._grid_container = QFrame()
        self._grid_container.setObjectName("GridContainer")
        self._mod_grid = QGridLayout(self._grid_container)
        self._mod_grid.setContentsMargins(10, 10, 10, 10)
        self._mod_grid.setHorizontalSpacing(10)
        self._mod_grid.setVerticalSpacing(10)
        root.addWidget(self._grid_container, stretch=1)

        # Arcane searchable dropdown & Active Elements summary bar
        self._summary_frame = QFrame()
        self._summary_frame.setObjectName("SummaryFrame")
        sum_outer = QVBoxLayout(self._summary_frame)
        sum_outer.setContentsMargins(12, 8, 12, 8)
        sum_outer.setSpacing(4)

        sum_row = QHBoxLayout()
        sum_row.setSpacing(10)
        self._lbl_arcane = QLabel("")
        self._lbl_arcane.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self._cmb_arcane = SearchableItemComboBox(weapon_class="rifle", parent=self)
        self._cmb_arcane.setMinimumWidth(260)
        self._cmb_arcane.item_id_selected.connect(self._on_arcane_selected)
        self._lbl_arcane_none = QLabel("")
        self._lbl_arcane_none.hide()
        self._lbl_elements = QLabel("")
        self._lbl_elements.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        sum_row.addWidget(self._lbl_arcane)
        sum_row.addWidget(self._cmb_arcane)
        sum_row.addWidget(self._lbl_arcane_none)
        sum_row.addStretch(1)
        sum_row.addWidget(self._lbl_elements)
        sum_outer.addLayout(sum_row)

        self._lbl_arcane_desc = QLabel("")
        self._lbl_arcane_desc.setWordWrap(True)
        sum_outer.addWidget(self._lbl_arcane_desc)
        root.addWidget(self._summary_frame)

        # Overall Build Stats + Smart Rationale Frame
        self._rationale_frame = QFrame()
        self._rationale_frame.setObjectName("RationaleFrame")
        rat_lay = QVBoxLayout(self._rationale_frame)
        rat_lay.setContentsMargins(12, 9, 12, 9)
        rat_lay.setSpacing(6)

        self._lbl_rationale_hdr = QLabel("")
        self._lbl_rationale_hdr.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        rat_lay.addWidget(self._lbl_rationale_hdr)

        # Live Overall Stat Grid (Damage, MS, FR/AS, Melee Range, CC, CD, SC, Elem, ...)
        self._stats_grid_widget = QWidget()
        self._stats_grid_widget.setStyleSheet("background: transparent;")
        self._stats_grid = QGridLayout(self._stats_grid_widget)
        self._stats_grid.setContentsMargins(0, 0, 0, 0)
        self._stats_grid.setHorizontalSpacing(8)
        self._stats_grid.setVerticalSpacing(5)
        rat_lay.addWidget(self._stats_grid_widget)

        self._lbl_rationale_body = QLabel("")
        self._lbl_rationale_body.setWordWrap(True)
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
            QComboBox:hover, QComboBox:focus {{
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
            f"QFrame#HdrFrame {{ background-color: {surface_hdr}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        self._grid_container.setStyleSheet(
            f"QFrame#GridContainer {{ background-color: {surface}; border: 1px solid {border_soft}; border-radius: 10px; }}"
        )
        self._summary_frame.setStyleSheet(
            f"QFrame#SummaryFrame {{ background-color: {surface_hdr}; border: 1px solid {border_soft}; border-radius: 8px; }}"
        )
        self._rationale_frame.setStyleSheet(
            f"QFrame#RationaleFrame {{ background-color: {surface}; border: 1px solid {border_soft}; border-radius: 8px; }}"
        )
        self._lbl_title.setStyleSheet(f"color: {accent}; background: transparent; border: none;")
        self._lbl_weapon_meta.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent; border: none;")
        self._lbl_arcane.setStyleSheet(f"color: {accent}; background: transparent; border: none;")
        self._lbl_arcane_none.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent; border: none;")
        self._lbl_arcane_desc.setStyleSheet(f"color: {muted}; font-size: 11px; background: transparent; border: none;")
        self._lbl_elements.setStyleSheet(f"color: {text}; background: transparent; border: none;")
        self._lbl_rationale_hdr.setStyleSheet(f"color: {accent}; background: transparent; border: none;")
        self._lbl_rationale_body.setStyleSheet(f"color: {text}; font-size: 12px; background: transparent; border: none;")
        self._lbl_disclaimer.setStyleSheet(f"color: {muted}; font-size: 11px; border: none;")

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
        """Replace Mod in `slot_idx` (0..6) and live-recalculate."""
        if not (0 <= slot_idx < 7) or self._current_eval is None:
            return
        pool = get_mod_pool(self._current_eval.weapon_class, allow_bane_mods=True, optimizer_only=False)
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
            # 2. Auto-clear mutually exclusive Mod in another slot
            new_mod = mod_by_id[new_id]
            if new_mod.exclusive_group:
                for j, existing_id in enumerate(self._slot_mod_ids):
                    if j != slot_idx and existing_id and existing_id in mod_by_id:
                        other = mod_by_id[existing_id]
                        if other.exclusive_group == new_mod.exclusive_group:
                            self._slot_mod_ids[j] = None
                            self._conflict_notice = (
                                f"已自动卸下互斥 Mod【{other.name_zh}】（与【{new_mod.name_zh}】同系不可共存）",
                                f"Auto-removed conflicting [{other.name_en}] (mutually exclusive with [{new_mod.name_en}])",
                            )
        self._slot_mod_ids[slot_idx] = new_id
        self._evaluate_current_selection()

    def set_selected_arcane(self, arcane_id: Optional[str]) -> None:
        """Switch the Weapon Arcane and live-recalculate."""
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

    def _on_slot_mod_selected(self, slot_idx: int, mod_id: str) -> None:
        if self._updating_widgets:
            return
        self.set_slot_mod(slot_idx, mod_id or None)

    def _on_arcane_selected(self, arcane_id: str) -> None:
        if self._updating_widgets:
            return
        self.set_selected_arcane(arcane_id or None)

    def _clear_mod_grid(self) -> None:
        while self._mod_grid.count():
            item = self._mod_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._slot_frames.clear()
        self._slot_combos.clear()

    def _render_overall_stats(self, summary: Optional[BuildStatSummary], lang: str) -> None:
        while self._stats_grid.count():
            item = self._stats_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._stat_pills.clear()

        if summary is None:
            return

        is_zh = lang in ("zh_CN", "zh_TW")
        is_melee = summary.weapon_class == "melee"
        colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        text = colors.get("text", "#EFE4FF")
        pill_bg = _blend_hex(bg, border, 0.16)
        pill_bd = _blend_hex(bg, border, 0.45)

        def _fmt_pct(v: float) -> str:
            return f"{v:+.0f}%" if abs(v - round(v)) < 0.05 else f"{v:+.1f}%"

        pills_data: List[tuple[str, str, str]] = []
        pills_data.append(
            ("伤害" if is_zh else "Damage", _fmt_pct(summary.damage_bonus_pct), "#FDE68A")
        )
        if not is_melee:
            pills_data.append(
                ("多重" if is_zh else "Multishot", _fmt_pct(summary.multishot_pct), "#93C5FD")
            )
            pills_data.append(
                ("射速" if is_zh else "Fire Rate", _fmt_pct(summary.fire_rate_pct), "#A7F3D0")
            )
        else:
            pills_data.append(
                ("攻速" if is_zh else "Attack Speed", _fmt_pct(summary.fire_rate_pct), "#A7F3D0")
            )
            pills_data.append(
                ("范围" if is_zh else "Range", f"{summary.range_m:+.1f}m", "#C4B5FD")
            )

        pills_data.append(
            (
                "暴击" if is_zh else "Crit",
                f"{summary.eff_crit_chance_pct:.1f}% ({_fmt_pct(summary.crit_chance_bonus_pct)})",
                "#FCD34D",
            )
        )
        pills_data.append(
            (
                "爆伤" if is_zh else "Crit DMG",
                f"{summary.eff_crit_mult:.2f}x ({_fmt_pct(summary.crit_damage_bonus_pct)})",
                "#FDBA74",
            )
        )
        pills_data.append(
            (
                "触发" if is_zh else "Status",
                f"{summary.eff_status_chance_pct:.1f}% ({_fmt_pct(summary.status_chance_bonus_pct)})",
                "#67E8F9",
            )
        )
        pills_data.append(
            ("元素" if is_zh else "Elemental", _fmt_pct(summary.elemental_pct), "#86EFAC")
        )
        if abs(summary.status_damage_pct) >= 0.5:
            pills_data.append(
                ("异伤" if is_zh else "Status DMG", _fmt_pct(summary.status_damage_pct), "#F9A8D4")
            )
        if abs(summary.faction_pct) >= 0.5:
            pills_data.append(
                ("歧视" if is_zh else "Faction", f"x{1.0 + summary.faction_pct / 100.0:.2f}", "#FCA5A5")
            )
        if not is_melee and abs(summary.punch_through_m) >= 0.05:
            pills_data.append(
                ("穿透" if is_zh else "Punch", f"{summary.punch_through_m:+.1f}m", "#DDD6FE")
            )
        if not is_melee and abs(summary.reload_pct) >= 0.5:
            pills_data.append(
                ("装填" if is_zh else "Reload", _fmt_pct(summary.reload_pct), "#CBD5E1")
            )

        cols = 4
        for idx, (label, val_txt, val_color) in enumerate(pills_data):
            pill = QLabel(f"<span style='color:{text};'>{label}：</span><b style='color:{val_color};'>{val_txt}</b>")
            pill.setTextFormat(Qt.TextFormat.RichText)
            pill.setStyleSheet(
                f"QLabel {{ background-color: {pill_bg}; border: 1px solid {pill_bd}; border-radius: 6px; padding: 4px 8px; font-size: 11px; }}"
            )
            self._stat_pills.append(pill)
            self._stats_grid.addWidget(pill, idx // cols, idx % cols)

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
            muted = _blend_hex(text, border, 0.35)

            pool = get_mod_pool(res.weapon_class, allow_bane_mods=True, optimizer_only=False)
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

            # Slots 0..6: 7 searchable editable Mod cards with stat values displayed
            search_ph = I18N.t("panel.opt_search_mod_ph", lang)
            empty_lbl = I18N.t("panel.opt_empty_slot", lang)
            for idx in range(7):
                cur_mid = self._slot_mod_ids[idx]
                cur_mod = mod_by_id.get(cur_mid) if cur_mid else None
                slot_edited = self._is_customized and (cur_mid != rec_ids[idx])

                card = QFrame()
                card.setObjectName(f"ModCard_{idx}")
                c_border = accent if slot_edited else _blend_hex(bg, border, 0.55)
                c_bg = edited_bg if slot_edited else card_bg
                card.setStyleSheet(
                    f"QFrame#ModCard_{idx} {{ background-color: {c_bg}; border: 1px solid {c_border}; border-radius: 8px; }}"
                )
                lay = QVBoxLayout(card)
                lay.setContentsMargins(9, 7, 9, 7)
                lay.setSpacing(3)

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

                cmb = SearchableItemComboBox(weapon_class=res.weapon_class, parent=card)
                cmb.populate_mods(
                    pool,
                    empty_label=empty_lbl,
                    placeholder=search_ph,
                    lang=lang,
                    selected_id=cur_mid,
                )
                cmb.item_id_selected.connect(lambda mid, s_idx=idx: self._on_slot_mod_selected(s_idx, mid))
                lay.addWidget(cmb)

                if cur_mod is not None:
                    secondary = cur_mod.name_en if is_zh else cur_mod.name_zh
                    stat_txt = format_mod_stats(cur_mod, res.weapon_class, lang)
                    gain_val = gain_by_id.get(cur_mod.id, 0.0)
                    gain_color = "#6EE7B7" if gain_val >= 0 else "#FCA5A5"
                    gain_txt = I18N.t("panel.opt_mod_gain", lang, gain=gain_val)
                else:
                    secondary = ""
                    stat_txt = empty_lbl
                    gain_color = muted
                    gain_txt = ""

                if secondary:
                    sub_lbl = QLabel(secondary)
                    sub_lbl.setStyleSheet(f"color: {muted}; font-size: 10px; background: transparent; border: none;")
                    sub_lbl.setWordWrap(True)
                    lay.addWidget(sub_lbl)

                stat_lbl = QLabel(stat_txt)
                stat_lbl.setStyleSheet("color: #BAE6FD; font-size: 11px; background: transparent; border: none;")
                stat_lbl.setWordWrap(True)
                lay.addWidget(stat_lbl)

                lay.addStretch(1)
                if gain_txt:
                    gain_lbl = QLabel(gain_txt)
                    gain_lbl.setStyleSheet(f"color: {gain_color}; font-size: 11px; font-weight: bold; background: transparent; border: none;")
                    lay.addWidget(gain_lbl)

                self._slot_frames.append(card)
                self._slot_combos.append(cmb)
                self._mod_grid.addWidget(card, idx // 4, idx % 4)

            # Slot 7 (8th card): Active Scanned Riven — ALWAYS Warframe Riven Purple!
            rcard = QFrame()
            rcard.setObjectName("RivenCard")
            rcard.setStyleSheet(
                "QFrame#RivenCard { background-color: #2A1246; border: 2px solid #C084FC; border-radius: 8px; }"
            )
            rlay = QVBoxLayout(rcard)
            rlay.setContentsMargins(9, 7, 9, 7)
            rlay.setSpacing(3)

            r_tag = QLabel(I18N.t("panel.opt_riven_slot_tag", lang))
            r_tag.setStyleSheet("color: #D8B4FE; font-size: 10px; font-weight: bold; background: transparent; border: none;")
            r_title = QLabel(
                (self._current_riven.riven_name if self._current_riven and self._current_riven.riven_name else res.weapon_name)
            )
            r_title.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
            r_title.setStyleSheet("color: #F5D0FE; background: transparent; border: none;")
            r_title.setWordWrap(True)

            rlay.addWidget(r_tag)
            rlay.addWidget(r_title)

            if ev is not None:
                for se in ev.stat_evaluations[:4]:
                    line_txt = _format_riven_stat_line(se, lang)
                    s_col = "#FCA5A5" if se.is_negative else "#E9D5FF"
                    sl = QLabel(line_txt)
                    sl.setStyleSheet(f"color: {s_col}; font-size: 11px; background: transparent; border: none;")
                    sl.setWordWrap(True)
                    rlay.addWidget(sl)

            rlay.addStretch(1)
            r_gain = QLabel(I18N.t("panel.opt_mod_gain", lang, gain=res.riven_gain_pct))
            r_gain.setStyleSheet("color: #86EFAC; font-size: 11px; font-weight: bold; background: transparent; border: none;")
            rlay.addWidget(r_gain)
            self._slot_frames.append(rcard)
            self._mod_grid.addWidget(rcard, 1, 3)

            # Searchable Arcane selector bar + selected Arcane effect description
            arcanes = get_arcane_pool(res.weapon_class, optimizer_only=False)
            if arcanes:
                self._cmb_arcane.show()
                self._lbl_arcane_none.hide()
                self._cmb_arcane.populate_arcanes(
                    arcanes,
                    none_label=I18N.t("panel.opt_arcane_none_opt", lang),
                    placeholder=I18N.t("panel.opt_search_arcane_ph", lang),
                    lang=lang,
                    selected_id=self._selected_arcane_id,
                )
                arc_desc = ""
                if res.arcane is not None:
                    arc_desc = res.arcane.desc_zh if is_zh else res.arcane.desc_en
                self._lbl_arcane_desc.setText(arc_desc)
                self._lbl_arcane_desc.setVisible(bool(arc_desc))
            else:
                self._cmb_arcane.hide()
                self._lbl_arcane_none.show()
                self._lbl_arcane_desc.hide()

            # Active Elements
            elems = res.active_elements_zh if is_zh else res.active_elements_en
            elem_txt = " + ".join(elems) if elems else "-"
            self._lbl_elements.setText(I18N.t("panel.opt_elements_label", lang, elems=elem_txt))

            # Overall Build Stats pills
            self._render_overall_stats(res.stat_summary, lang)

            # Rationale bullets
            reasons = list(res.rationale_zh if is_zh else res.rationale_en)
            if self._conflict_notice is not None:
                reasons.insert(0, self._conflict_notice[0] if is_zh else self._conflict_notice[1])
            self._lbl_rationale_body.setText("\n".join(f"• {r}" for r in reasons))
        finally:
            self._updating_widgets = False

    def _on_send_wfsim_clicked(self) -> None:
        if self._current_eval is not None and self._last_result is not None:
            self.send_optimized_to_wfsim.emit(self._current_eval, self._current_riven, self._last_result)
