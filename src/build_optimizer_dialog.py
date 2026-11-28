"""Themed Interactive Riven-Aware Local Build Optimizer dialog for 裂罅透镜 (Riven Lens).

Features:
  - Full WFCD + Warframe.market verified Mod & Weapon Arcane catalog per weapon class.
  - Weapon-exclusive Mods (e.g. `晴日伞 膛线`, `酸性弹药`, `散射正义`, `分裂飞行`, `易爆速返`)
    ONLY appear when the matching weapon is scanned, organized in a dedicated「★ 武器专属」category.
  - Cascading hover-category QMenu (`分类 ▾`): hovering over any category pops out its Mods
    immediately with stat previews.
  - Non-clobbering live search input: typing e.g. "腐蚀" keeps "腐蚀" clearly displayed in the
    input box while showing a non-focus-stealing popup of matching Toxin + Electricity Mods until
    the user picks one.
  - Signature Warframe Riven Purple card (#8) + per-card exact stat values + overall build stat bar.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QEvent, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QToolButton,
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
    categorize_mods_for_menu,
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


class CategorySearchSelector(QWidget):
    """Combined non-clobbering live search QLineEdit + cascading hover-category QMenu button.

    - Typing (e.g. "腐蚀") keeps the typed query visibly shown in the QLineEdit without
      ever overwriting it, while displaying a non-activating floating result list below until
      the user chooses an item (or cancels with Esc / focus-out).
    - Clicking the `▾` button opens a cascading QMenu of top-level categories where hovering
      over any category automatically expands its submenu of Mods.
    """

    item_id_selected = pyqtSignal(str)  # emits "" for empty/none

    def __init__(
        self,
        *,
        weapon_class: str = "rifle",
        theme_colors: Optional[Dict[str, str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._weapon_class = weapon_class
        self._theme_colors = theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
        self._lang = "zh_CN"
        self._mode = "mod"  # "mod" | "arcane"
        self._mods: List[ModDef] = []
        self._arcanes: List[ArcaneDef] = []
        self._empty_label = ""
        self._current_id = ""
        self._current_label = ""
        self._is_searching = False
        self._syncing = False
        self._active_menu: Optional[QMenu] = None

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)

        self._edit = QLineEdit(self)
        self._edit.setClearButtonEnabled(False)
        self._edit.textEdited.connect(self._on_text_edited)
        self._edit.returnPressed.connect(self._on_return_pressed)
        self._edit.installEventFilter(self)
        lay.addWidget(self._edit, stretch=1)

        self._btn_menu = QToolButton(self)
        self._btn_menu.setText("▾")
        self._btn_menu.setFixedWidth(24)
        self._btn_menu.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_menu.clicked.connect(self.open_category_menu)
        lay.addWidget(self._btn_menu)

        # Non-activating floating search results popup (never steals keyboard/IME focus!)
        self._popup = QListWidget(None)
        self._popup.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._popup.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self._popup.itemClicked.connect(self._on_popup_item_clicked)
        self._apply_selector_style()

    def _apply_selector_style(self) -> None:
        colors = self._theme_colors
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#EFE4FF")
        input_bg = _blend_hex(bg, "#000000", 0.28)
        border_soft = _blend_hex(bg, border, 0.50)
        btn_bg = _blend_hex(bg, border, 0.24)
        btn_hover = _blend_hex(bg, border, 0.45)
        sel_bg = _blend_hex(bg, border, 0.65)
        sel_fg = _contrast_text_hex(sel_bg)

        self._edit.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 5px;
                padding: 4px 7px;
                font-size: 12px;
                font-weight: 600;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: {accent};
            }}
            """
        )
        self._btn_menu.setStyleSheet(
            f"""
            QToolButton {{
                background-color: {btn_bg};
                color: {accent};
                border: 1px solid {border_soft};
                border-radius: 5px;
                padding: 3px 2px;
                font-size: 12px;
                font-weight: bold;
            }}
            QToolButton:hover {{
                background-color: {btn_hover};
                border-color: {accent};
            }}
            """
        )
        self._popup.setStyleSheet(
            f"""
            QListWidget {{
                background-color: {bg};
                color: {text};
                border: 1.5px solid {accent};
                border-radius: 6px;
                padding: 3px;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected, QListWidget::item:hover {{
                background-color: {sel_bg};
                color: {sel_fg};
            }}
            """
        )

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        self._theme_colors = colors
        self._apply_selector_style()

    def populate_mods(
        self,
        mods: Sequence[ModDef],
        *,
        empty_label: str,
        placeholder: str,
        lang: str,
        selected_id: Optional[str],
    ) -> None:
        self._mode = "mod"
        self._lang = lang
        self._mods = list(mods)
        self._arcanes = []
        self._empty_label = empty_label
        self._edit.setPlaceholderText(placeholder)
        self._btn_menu.setToolTip(
            "按大分类选择 Mod（鼠标悬停自动展开）" if lang in ("zh_CN", "zh_TW") else "Browse Mods by category (hover to expand)"
        )
        self._set_selected_id(selected_id or "")

    def populate_arcanes(
        self,
        arcanes: Sequence[ArcaneDef],
        *,
        none_label: str,
        placeholder: str,
        lang: str,
        selected_id: Optional[str],
    ) -> None:
        self._mode = "arcane"
        self._lang = lang
        self._mods = []
        self._arcanes = list(arcanes)
        self._empty_label = none_label
        self._edit.setPlaceholderText(placeholder)
        self._btn_menu.setToolTip(
            "展开全部赋能列表" if lang in ("zh_CN", "zh_TW") else "Browse all Arcanes"
        )
        self._set_selected_id(selected_id or "")

    def _label_for_id(self, target_id: str) -> str:
        is_zh = self._lang in ("zh_CN", "zh_TW")
        if not target_id:
            return self._empty_label
        if self._mode == "mod":
            for m in self._mods:
                if m.id == target_id:
                    return m.name_zh if is_zh else m.name_en
        else:
            for a in self._arcanes:
                if a.id == target_id:
                    return f"{a.name_zh} ({a.name_en})" if is_zh else a.name_en
        return self._empty_label

    def _set_selected_id(self, target_id: str) -> None:
        self._syncing = True
        try:
            self._current_id = target_id
            self._current_label = self._label_for_id(target_id)
            self._is_searching = False
            self._popup.hide()
            self._edit.setText(self._current_label)
            self._edit.setCursorPosition(0)
        finally:
            self._syncing = False

    def current_text(self) -> str:
        return self._edit.text()

    def search_matching_ids(self, query: str) -> List[str]:
        """Return all matching item IDs for `query` (also updates popup if called during typing)."""
        q = (query or "").strip()
        if self._mode == "mod":
            if not q:
                return [m.id for m in self._mods]
            return [m.id for m in self._mods if match_mod_search(m, q, self._weapon_class)]
        if not q:
            return [a.id for a in self._arcanes]
        toks = q.lower().split()
        out: List[str] = []
        for a in self._arcanes:
            blob = f"{a.name_zh} {a.name_en} {a.id} {a.desc_zh} {a.desc_en}".lower()
            if all(t in blob for t in toks):
                out.append(a.id)
        return out

    def build_category_menu(self) -> QMenu:
        """Build the cascading hover-category QMenu for Mods (or full list menu for Arcanes)."""
        colors = self._theme_colors
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#EFE4FF")
        sel_bg = _blend_hex(bg, border, 0.65)
        sel_fg = _contrast_text_hex(sel_bg)
        sep_col = _blend_hex(bg, border, 0.35)

        menu_css = f"""
            QMenu {{
                background-color: {bg};
                color: {text};
                border: 1.5px solid {border};
                border-radius: 6px;
                padding: 4px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
            }}
            QMenu::item {{
                padding: 5px 22px 5px 10px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background-color: {sel_bg};
                color: {sel_fg};
            }}
            QMenu::separator {{
                height: 1px;
                background: {sep_col};
                margin: 4px 6px;
            }}
        """
        menu = QMenu(self)
        menu.setStyleSheet(menu_css)
        is_zh = self._lang in ("zh_CN", "zh_TW")

        # Empty / clear slot entry at top
        clear_act = menu.addAction(f"✖  {self._empty_label}")
        clear_act.triggered.connect(lambda _chk=False: self._commit_id(""))
        menu.addSeparator()

        if self._mode == "mod":
            categories = categorize_mods_for_menu(self._mods, self._weapon_class, self._lang)
            for cat_title, cat_mods in categories:
                sub = QMenu(f"{cat_title} ({len(cat_mods)})", menu)
                sub.setStyleSheet(menu_css)
                for m in cat_mods:
                    disp = m.name_zh if is_zh else m.name_en
                    st_txt = format_mod_stats(m, self._weapon_class, self._lang)
                    prefix = "✓ " if m.id == self._current_id else "   "
                    label = f"{prefix}{disp}   [{st_txt}]" if st_txt and st_txt != "-" else f"{prefix}{disp}"
                    act = sub.addAction(label)
                    act.setData(m.id)
                    act.triggered.connect(lambda _chk=False, mid=m.id: self._commit_id(mid))
                menu.addMenu(sub)
        else:
            for a in self._arcanes:
                disp = f"{a.name_zh} ({a.name_en})" if is_zh else a.name_en
                desc = a.desc_zh if is_zh else a.desc_en
                prefix = "✓ " if a.id == self._current_id else "   "
                label = f"{prefix}{disp}" + (f"  —  {desc}" if desc else "")
                act = menu.addAction(label)
                act.setData(a.id)
                act.triggered.connect(lambda _chk=False, aid=a.id: self._commit_id(aid))

        return menu

    def open_category_menu(self) -> None:
        self._popup.hide()
        menu = self.build_category_menu()
        self._active_menu = menu
        pos = self._edit.mapToGlobal(QPoint(0, self._edit.height() + 2))
        menu.exec(pos)
        self._active_menu = None

    def _populate_and_show_search_popup(self, query: str) -> None:
        is_zh = self._lang in ("zh_CN", "zh_TW")
        self._popup.clear()
        matches = self.search_matching_ids(query)
        if not matches:
            self._popup.hide()
            return

        if self._mode == "mod":
            by_id = {m.id: m for m in self._mods}
            for mid in matches[:30]:
                m = by_id[mid]
                disp = m.name_zh if is_zh else m.name_en
                st_txt = format_mod_stats(m, self._weapon_class, self._lang)
                ex_tag = "★ " if m.compat_weapon else ""
                item = QListWidgetItem(f"{ex_tag}{disp}   [{st_txt}]" if st_txt and st_txt != "-" else f"{ex_tag}{disp}")
                item.setData(_ROLE_ITEM_ID, m.id)
                self._popup.addItem(item)
        else:
            by_id_a = {a.id: a for a in self._arcanes}
            for aid in matches[:30]:
                a = by_id_a[aid]
                disp = f"{a.name_zh} ({a.name_en})" if is_zh else a.name_en
                desc = a.desc_zh if is_zh else a.desc_en
                item = QListWidgetItem(f"{disp}  —  {desc}" if desc else disp)
                item.setData(_ROLE_ITEM_ID, a.id)
                self._popup.addItem(item)

        self._popup.setCurrentRow(0)
        popup_w = max(self.width() + 120, 320)
        row_h = 28
        popup_h = min(320, max(44, self._popup.count() * row_h + 10))
        self._popup.resize(popup_w, popup_h)
        pos = self._edit.mapToGlobal(QPoint(0, self._edit.height() + 2))
        self._popup.move(pos)
        self._popup.show()

    def _on_text_edited(self, text: str) -> None:
        if self._syncing:
            return
        # IMPORTANT: Never overwrite `self._edit` while user is typing (e.g. "腐蚀" stays visible!)
        self._is_searching = True
        q = text.strip()
        if not q:
            self._popup.hide()
            return
        self._populate_and_show_search_popup(q)

    def _on_return_pressed(self) -> None:
        if self._syncing:
            return
        if self._popup.isVisible() and self._popup.count() > 0:
            cur = self._popup.currentItem() or self._popup.item(0)
            if cur is not None:
                self._commit_id(str(cur.data(_ROLE_ITEM_ID) or ""))

    def _on_popup_item_clicked(self, item: QListWidgetItem) -> None:
        if item is not None:
            self._commit_id(str(item.data(_ROLE_ITEM_ID) or ""))

    def _commit_id(self, item_id: str) -> None:
        self._set_selected_id(item_id)
        self.item_id_selected.emit(item_id)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is self._edit:
            etype = event.type()
            if etype == QEvent.Type.FocusIn:
                # Select current Mod name so typing immediately starts a fresh search cleanly
                if not self._is_searching:
                    QTimer.singleShot(0, self._edit.selectAll)
            elif etype == QEvent.Type.KeyPress:
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    self._popup.hide()
                    self._set_selected_id(self._current_id)
                    return True
                if key == Qt.Key.Key_Down and self._popup.isVisible() and self._popup.count() > 0:
                    nxt = min(self._popup.count() - 1, self._popup.currentRow() + 1)
                    self._popup.setCurrentRow(nxt)
                    return True
                if key == Qt.Key.Key_Up and self._popup.isVisible() and self._popup.count() > 0:
                    prv = max(0, self._popup.currentRow() - 1)
                    self._popup.setCurrentRow(prv)
                    return True
            elif etype == QEvent.Type.FocusOut:
                # Delay slightly so clicking a popup item commits BEFORE popup hides
                QTimer.singleShot(140, self._handle_deferred_focus_out)
        return super().eventFilter(obj, event)

    def _handle_deferred_focus_out(self) -> None:
        if self._edit.hasFocus():
            return
        if self._popup.underMouse():
            return
        self._popup.hide()
        if self._is_searching:
            self._set_selected_id(self._current_id)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._popup.hide()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._popup.hide()
        super().closeEvent(event)


class BuildOptimizerDialog(QDialog):
    """Interactive local Riven-aware 7-Mod + Arcane optimizer with cascading categories & live search."""

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
        self._conflict_notice: Optional[Tuple[str, str]] = None
        self._updating_widgets: bool = False

        self._scenario_btns: Dict[str, QPushButton] = {}
        self._slot_frames: List[QFrame] = []
        self._slot_combos: List[CategorySearchSelector] = []
        self._stat_pills: List[QLabel] = []

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(840, 690)
        self.resize(910, 750)
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

        # 2 x 4 Mod Grid (7 cascading-category searchable Mod cards + Slot 8 Purple Riven card)
        self._grid_container = QFrame()
        self._grid_container.setObjectName("GridContainer")
        self._mod_grid = QGridLayout(self._grid_container)
        self._mod_grid.setContentsMargins(10, 10, 10, 10)
        self._mod_grid.setHorizontalSpacing(10)
        self._mod_grid.setVerticalSpacing(10)
        root.addWidget(self._grid_container, stretch=1)

        # Arcane searchable selector & Active Elements summary bar
        self._summary_frame = QFrame()
        self._summary_frame.setObjectName("SummaryFrame")
        sum_outer = QVBoxLayout(self._summary_frame)
        sum_outer.setContentsMargins(12, 8, 12, 8)
        sum_outer.setSpacing(4)

        sum_row = QHBoxLayout()
        sum_row.setSpacing(10)
        self._lbl_arcane = QLabel("")
        self._lbl_arcane.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.Bold))
        self._cmb_arcane = CategorySearchSelector(weapon_class="rifle", theme_colors=self._theme_colors, parent=self)
        self._cmb_arcane.setMinimumWidth(280)
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
        self._cmb_arcane.set_theme_colors(colors)

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
        pool = get_mod_pool(
            self._current_eval.weapon_class,
            allow_bane_mods=True,
            optimizer_only=False,
            weapon_slug=self._current_eval.weapon_slug,
            weapon_name_en=self._current_eval.weapon_name_en,
        )
        mod_by_id: Dict[str, ModDef] = {m.id: m for m in pool}

        new_id = str(mod_id).strip() if mod_id else None
        if new_id and new_id not in mod_by_id:
            return
        prev_id = self._slot_mod_ids[slot_idx]
        if new_id == prev_id:
            return

        self._conflict_notice = None
        if new_id is not None:
            for j, existing_id in enumerate(self._slot_mod_ids):
                if j != slot_idx and existing_id == new_id:
                    self._slot_mod_ids[j] = prev_id
                    break
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
        if self._current_eval is None:
            return
        norm = str(arcane_id).strip() if arcane_id else None
        if norm == self._selected_arcane_id:
            return
        self._conflict_notice = None
        self._selected_arcane_id = norm
        self._evaluate_current_selection()

    def reset_to_recommended(self) -> None:
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
        for sel in self._slot_combos:
            sel._popup.hide()
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

        pills_data: List[Tuple[str, str, str]] = []
        pills_data.append(("伤害" if is_zh else "Damage", _fmt_pct(summary.damage_bonus_pct), "#FDE68A"))
        if not is_melee:
            pills_data.append(("多重" if is_zh else "Multishot", _fmt_pct(summary.multishot_pct), "#93C5FD"))
            pills_data.append(("射速" if is_zh else "Fire Rate", _fmt_pct(summary.fire_rate_pct), "#A7F3D0"))
        else:
            pills_data.append(("攻速" if is_zh else "Attack Speed", _fmt_pct(summary.fire_rate_pct), "#A7F3D0"))
            pills_data.append(("范围" if is_zh else "Range", f"{summary.range_m:+.1f}m", "#C4B5FD"))

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
        pills_data.append(("元素" if is_zh else "Elemental", _fmt_pct(summary.elemental_pct), "#86EFAC"))
        if abs(summary.status_damage_pct) >= 0.5:
            pills_data.append(("异伤" if is_zh else "Status DMG", _fmt_pct(summary.status_damage_pct), "#F9A8D4"))
        if abs(summary.faction_pct) >= 0.5:
            pills_data.append(("歧视" if is_zh else "Faction", f"x{1.0 + summary.faction_pct / 100.0:.2f}", "#FCA5A5"))
        if not is_melee and abs(summary.punch_through_m) >= 0.05:
            pills_data.append(("穿透" if is_zh else "Punch", f"{summary.punch_through_m:+.1f}m", "#DDD6FE"))
        if not is_melee and abs(summary.reload_pct) >= 0.5:
            pills_data.append(("装填" if is_zh else "Reload", _fmt_pct(summary.reload_pct), "#CBD5E1"))

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
                meta_parts.append(I18N.t("panel.opt_riven_gain_badge", lang, gain=res.riven_gain_pct))
                if self._is_customized and res.delta_vs_recommended_pct is not None:
                    meta_parts.append(I18N.t("panel.opt_modified_badge", lang, delta=res.delta_vs_recommended_pct))
                self._lbl_weapon_meta.setText("  ·  ".join(meta_parts))

            colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
            bg = colors.get("bg", "#120C20")
            border = colors.get("border", "#A876FF")
            accent = colors.get("accent", "#FFD166")
            text = colors.get("text", "#EFE4FF")
            card_bg = _blend_hex(bg, "#000000", 0.20)
            edited_bg = _blend_hex(bg, accent, 0.09)
            muted = _blend_hex(text, border, 0.35)

            pool = get_mod_pool(
                res.weapon_class,
                allow_bane_mods=True,
                optimizer_only=False,
                weapon_slug=ev.weapon_slug if ev is not None else "",
                weapon_name_en=ev.weapon_name_en if ev is not None else "",
            )
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
                if cur_mod is not None and cur_mod.compat_weapon:
                    ex_lbl = QLabel("★专属" if is_zh else "★Excl")
                    ex_lbl.setStyleSheet("color: #C084FC; font-size: 10px; font-weight: bold; background: transparent; border: none;")
                    top_row.addWidget(ex_lbl)
                if slot_edited:
                    ed_lbl = QLabel(I18N.t("panel.opt_slot_modified_tag", lang))
                    ed_lbl.setStyleSheet(f"color: {accent}; font-size: 10px; font-weight: bold; background: transparent; border: none;")
                    top_row.addWidget(ed_lbl)
                lay.addLayout(top_row)

                sel = CategorySearchSelector(weapon_class=res.weapon_class, theme_colors=colors, parent=card)
                sel.populate_mods(
                    pool,
                    empty_label=empty_lbl,
                    placeholder=search_ph,
                    lang=lang,
                    selected_id=cur_mid,
                )
                sel.item_id_selected.connect(lambda mid, s_idx=idx: self._on_slot_mod_selected(s_idx, mid))
                lay.addWidget(sel)

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
                self._slot_combos.append(sel)
                self._mod_grid.addWidget(card, idx // 4, idx % 4)

            # Slot 7 (8th card): Active Scanned Riven — Warframe Riven Purple!
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

            # Searchable Arcane bar
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

            elems = res.active_elements_zh if is_zh else res.active_elements_en
            elem_txt = " + ".join(elems) if elems else "-"
            self._lbl_elements.setText(I18N.t("panel.opt_elements_label", lang, elems=elem_txt))

            self._render_overall_stats(res.stat_summary, lang)

            reasons = list(res.rationale_zh if is_zh else res.rationale_en)
            if self._conflict_notice is not None:
                reasons.insert(0, self._conflict_notice[0] if is_zh else self._conflict_notice[1])
            self._lbl_rationale_body.setText("\n".join(f"• {r}" for r in reasons))
        finally:
            self._updating_widgets = False

    def _on_send_wfsim_clicked(self) -> None:
        if self._current_eval is not None and self._last_result is not None:
            self.send_optimized_to_wfsim.emit(self._current_eval, self._current_riven, self._last_result)

    def hideEvent(self, event) -> None:  # noqa: N802
        for sel in self._slot_combos:
            sel._popup.hide()
        self._cmb_arcane._popup.hide()
        super().hideEvent(event)
