"""Pure-selection visual dialog for configuring per-weapon favorite Riven stats.

Two-step point-and-click workflow (zero manual stat typing):
  1. Weapon Picker page: filter by weapon class tabs (`All`, `Rifle`, `Shotgun`,
     `Pistol`, `Melee`, `Arch-Gun`, `★ Favorites`) or optional quick search, then
     select a weapon (click Next or double-click).
  2. Available Stats Checklist page: automatically shows ONLY the legal positive
     and negative Riven stats that this weapon class can actually roll; user checks
     desired stats and clicks Save.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.evaluator import RivenEvaluator
from src.i18n import I18N, SUPPORTED_LANGS
from src.settings_manager import DEFAULT_THEME_PRESET, UserSettings, resolve_theme_colors
from src.tray_manager import load_app_icon

WEAPON_CLASS_FILTERS: Tuple[str, ...] = (
    "all",
    "rifle",
    "shotgun",
    "pistol",
    "melee",
    "archgun",
    "favorites",
)

PAGE_WEAPONS = 0
PAGE_STATS = 1


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


class FavoriteStatsDialog(QDialog):
    """Themed two-step pure-selection dialog for user-defined favorite weapon Riven stats."""

    favorites_saved = pyqtSignal(dict)  # emits updated copy of `settings.favorite_weapon_stats`

    def __init__(
        self,
        evaluator: RivenEvaluator,
        settings: UserSettings,
        lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._evaluator = evaluator
        self._settings = settings
        self._lang = lang if lang in SUPPORTED_LANGS else "en_US"
        self._theme_colors = resolve_theme_colors(settings.theme_preset, settings.theme_colors)
        self._active_class_filter: str = "all"
        self._weapons_cache: List[Dict[str, str]] = []
        self._weapons_by_key: Dict[str, Dict[str, str]] = {}
        self._selected_weapon_key: Optional[str] = None
        self._pos_checkboxes: Dict[str, QCheckBox] = {}
        self._neg_checkboxes: Dict[str, QCheckBox] = {}
        self._filter_buttons: Dict[str, QPushButton] = {}

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(660, 620)
        self.resize(720, 680)
        self.setModal(False)

        self._build_ui()
        self._apply_stylesheet()
        self.reload_weapons()
        self.retranslate_ui(self._lang)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        # Header banner
        self._hdr_frame = QFrame()
        hdr_lay = QVBoxLayout(self._hdr_frame)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(3)
        self._lbl_title = QLabel("")
        self._lbl_title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold))
        self._lbl_subtitle = QLabel("")
        self._lbl_subtitle.setWordWrap(True)
        hdr_lay.addWidget(self._lbl_title)
        hdr_lay.addWidget(self._lbl_subtitle)
        root.addWidget(self._hdr_frame)

        # Stacked two-step pages
        self._stack = QStackedWidget()
        root.addWidget(self._stack, stretch=1)

        # ---------------- Page 0: Pure-Selection Weapon Picker ----------------
        page_w = QWidget()
        pw_lay = QVBoxLayout(page_w)
        pw_lay.setContentsMargins(0, 0, 0, 0)
        pw_lay.setSpacing(10)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for fkey in WEAPON_CLASS_FILTERS:
            btn = QPushButton("")
            btn.setObjectName("FilterPillBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, k=fkey: self._set_class_filter(k))
            self._filter_buttons[fkey] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch(1)
        pw_lay.addLayout(filter_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._inp_search = QLineEdit()
        self._inp_search.setClearButtonEnabled(True)
        self._inp_search.textChanged.connect(lambda _t: self._populate_weapon_list())
        search_row.addWidget(self._inp_search, stretch=1)
        pw_lay.addLayout(search_row)

        self._list_weapons = QListWidget()
        self._list_weapons.setAlternatingRowColors(False)
        self._list_weapons.itemSelectionChanged.connect(self._on_weapon_selection_changed)
        self._list_weapons.itemDoubleClicked.connect(lambda _item: self._enter_stats_page())
        pw_lay.addWidget(self._list_weapons, stretch=1)

        pw_footer = QHBoxLayout()
        pw_footer.setSpacing(8)
        self._lbl_weapon_count = QLabel("")
        pw_footer.addWidget(self._lbl_weapon_count, stretch=1)

        self._btn_next_stats = QPushButton("")
        self._btn_next_stats.setEnabled(False)
        self._btn_next_stats.clicked.connect(self._enter_stats_page)
        pw_footer.addWidget(self._btn_next_stats)

        self._btn_close_p0 = QPushButton("")
        self._btn_close_p0.setObjectName("SecondaryBtn")
        self._btn_close_p0.clicked.connect(self.hide)
        pw_footer.addWidget(self._btn_close_p0)
        pw_lay.addLayout(pw_footer)

        self._stack.addWidget(page_w)

        # ---------------- Page 1: Legal Available Stats Checklist ----------------
        page_s = QWidget()
        ps_lay = QVBoxLayout(page_s)
        ps_lay.setContentsMargins(0, 0, 0, 0)
        ps_lay.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self._btn_back = QPushButton("")
        self._btn_back.setObjectName("SecondaryBtn")
        self._btn_back.clicked.connect(self._back_to_weapons)
        top_bar.addWidget(self._btn_back)

        self._lbl_selected_weapon = QLabel("")
        self._lbl_selected_weapon.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
        top_bar.addWidget(self._lbl_selected_weapon, stretch=1)

        self._btn_fill_recommended = QPushButton("")
        self._btn_fill_recommended.setObjectName("SecondaryBtn")
        self._btn_fill_recommended.clicked.connect(self._on_fill_recommended_clicked)
        top_bar.addWidget(self._btn_fill_recommended)

        self._btn_clear_checks = QPushButton("")
        self._btn_clear_checks.setObjectName("SecondaryBtn")
        self._btn_clear_checks.clicked.connect(self._on_clear_checks_clicked)
        top_bar.addWidget(self._btn_clear_checks)
        ps_lay.addLayout(top_bar)

        self._stats_scroll = QScrollArea()
        self._stats_scroll.setWidgetResizable(True)
        self._stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stats_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._stats_container = QWidget()
        sc_lay = QVBoxLayout(self._stats_container)
        sc_lay.setContentsMargins(2, 2, 8, 8)
        sc_lay.setSpacing(14)

        self._grp_pos = QGroupBox("")
        self._grid_pos = QGridLayout(self._grp_pos)
        self._grid_pos.setHorizontalSpacing(10)
        self._grid_pos.setVerticalSpacing(8)
        sc_lay.addWidget(self._grp_pos)

        self._grp_neg = QGroupBox("")
        self._grid_neg = QGridLayout(self._grp_neg)
        self._grid_neg.setHorizontalSpacing(10)
        self._grid_neg.setVerticalSpacing(8)
        sc_lay.addWidget(self._grp_neg)
        sc_lay.addStretch(1)

        self._stats_scroll.setWidget(self._stats_container)
        ps_lay.addWidget(self._stats_scroll, stretch=1)

        self._lbl_selection_summary = QLabel("")
        self._lbl_selection_summary.setWordWrap(True)
        ps_lay.addWidget(self._lbl_selection_summary)

        ps_footer = QHBoxLayout()
        ps_footer.setSpacing(8)
        self._btn_remove_weapon = QPushButton("")
        self._btn_remove_weapon.setObjectName("SecondaryBtn")
        self._btn_remove_weapon.clicked.connect(self._on_remove_weapon_favorite)
        ps_footer.addWidget(self._btn_remove_weapon)

        ps_footer.addStretch(1)

        self._btn_save = QPushButton("")
        self._btn_save.clicked.connect(self._on_save_clicked)
        ps_footer.addWidget(self._btn_save)

        self._btn_close_p1 = QPushButton("")
        self._btn_close_p1.setObjectName("SecondaryBtn")
        self._btn_close_p1.clicked.connect(self.hide)
        ps_footer.addWidget(self._btn_close_p1)
        ps_lay.addLayout(ps_footer)

        self._stack.addWidget(page_s)

        # Shared bottom feedback banner
        self._lbl_feedback = QLabel("")
        self._lbl_feedback.setWordWrap(True)
        self._lbl_feedback.hide()
        root.addWidget(self._lbl_feedback)

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
        pill_checked_fg = _contrast_text_hex(accent)
        item_hover = _blend_hex(bg, border, 0.20)
        item_sel = _blend_hex(bg, border, 0.42)

        self.setStyleSheet(
            f"""
            QDialog, QWidget {{
                background-color: {bg};
                color: {text};
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }}
            QGroupBox {{
                background-color: {surface};
                border: 1px solid {border_soft};
                border-radius: 10px;
                margin-top: 12px;
                padding: 14px 12px 12px 12px;
                font-weight: bold;
                color: {accent};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: {accent};
            }}
            QLineEdit {{
                background-color: {input_bg};
                color: {text};
                border: 1.5px solid {border_soft};
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1.5px solid {accent};
            }}
            QListWidget {{
                background-color: {surface};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 8px;
                padding: 4px;
                font-size: 13px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 7px 10px;
                border-radius: 6px;
                margin: 1px 0;
            }}
            QListWidget::item:hover {{
                background-color: {item_hover};
            }}
            QListWidget::item:selected {{
                background-color: {item_sel};
                color: {accent};
                font-weight: bold;
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 14px;
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
            QPushButton#FilterPillBtn {{
                background-color: {sec_bg};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 13px;
                padding: 4px 11px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#FilterPillBtn:hover {{
                background-color: {sec_hover};
            }}
            QPushButton#FilterPillBtn:checked {{
                background-color: {accent};
                color: {pill_checked_fg};
                border-color: {accent};
            }}
            QCheckBox {{
                spacing: 8px;
                color: {text};
                background-color: {input_bg};
                border: 1px solid {_blend_hex(bg, border, 0.30)};
                border-radius: 6px;
                padding: 6px 9px;
                font-size: 12px;
            }}
            QCheckBox:hover {{
                border-color: {border};
                background-color: {item_hover};
            }}
            QCheckBox:checked {{
                border: 1.5px solid {accent};
                background-color: {_blend_hex(bg, accent, 0.18)};
                color: {accent};
                font-weight: bold;
            }}
            QCheckBox::indicator {{
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1.5px solid {border};
                background-color: {bg};
            }}
            QCheckBox::indicator:checked {{
                background-color: {accent};
                border-color: {accent};
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {bg};
                width: 8px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {border_soft};
                min-height: 28px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {border};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            """
        )
        self._hdr_frame.setStyleSheet(
            f"QFrame {{ background-color: {surface_hdr}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        self._lbl_title.setStyleSheet(f"color: {accent}; background: transparent;")
        self._lbl_subtitle.setStyleSheet(f"color: {muted}; font-size: 12px; background: transparent;")
        self._lbl_weapon_count.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self._lbl_selected_weapon.setStyleSheet(f"color: {accent}; background: transparent;")
        self._lbl_selection_summary.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self._stats_container.setStyleSheet(f"background-color: {bg};")

    # ---- Public API ----

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        self._theme_colors = resolve_theme_colors("custom", colors)
        self._apply_stylesheet()

    def set_language(self, lang: str) -> None:
        self.retranslate_ui(lang)

    def reload_weapons(self) -> None:
        self._weapons_cache = self._evaluator.list_weapons_for_picker()
        self._weapons_by_key = {w["weapon_key"]: w for w in self._weapons_cache}
        self._populate_weapon_list()

    def show_and_activate(self, preselect_weapon: Optional[str] = None) -> None:
        self.reload_weapons()
        self._lbl_feedback.hide()
        self._stack.setCurrentIndex(PAGE_WEAPONS)
        if preselect_weapon:
            self._select_weapon_in_list(preselect_weapon)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def retranslate_ui(self, lang: str) -> None:
        self._lang = lang if lang in SUPPORTED_LANGS else "en_US"
        self.setWindowTitle(I18N.t("panel.fav_dialog_title", self._lang))
        self._lbl_title.setText(I18N.t("panel.fav_dialog_title", self._lang))
        self._lbl_subtitle.setText(I18N.t("panel.fav_dialog_subtitle", self._lang))
        self._inp_search.setPlaceholderText(I18N.t("panel.fav_search_placeholder", self._lang))
        self._btn_next_stats.setText(I18N.t("panel.fav_next_btn", self._lang))
        self._btn_close_p0.setText(I18N.t("panel.fav_close_btn", self._lang))
        self._btn_back.setText(I18N.t("panel.fav_back_btn", self._lang))
        self._btn_fill_recommended.setText(I18N.t("panel.fav_fill_rec_btn", self._lang))
        self._btn_clear_checks.setText(I18N.t("panel.fav_clear_btn", self._lang))
        self._grp_pos.setTitle(I18N.t("panel.fav_pos_group", self._lang))
        self._grp_neg.setTitle(I18N.t("panel.fav_neg_group", self._lang))
        self._btn_remove_weapon.setText(I18N.t("panel.fav_reset_weapon_btn", self._lang))
        self._btn_save.setText(I18N.t("panel.fav_save_btn", self._lang))
        self._btn_close_p1.setText(I18N.t("panel.fav_close_btn", self._lang))
        self._refresh_filter_button_labels()
        self._populate_weapon_list()
        if self._stack.currentIndex() == PAGE_STATS and self._selected_weapon_key:
            self._render_stats_for_weapon(self._selected_weapon_key, preserve_current_checks=True)

    # ---- Page 0: Weapon Picker ----

    def _refresh_filter_button_labels(self) -> None:
        fav_count = len(self._settings.favorite_weapon_stats)
        for fkey, btn in self._filter_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(fkey == self._active_class_filter)
            btn.blockSignals(False)
            if fkey == "all":
                btn.setText(I18N.t("panel.fav_filter_all", self._lang))
            elif fkey == "favorites":
                btn.setText(I18N.t("panel.fav_filter_saved", self._lang, count=fav_count))
            else:
                btn.setText(I18N.weapon_class(fkey, self._lang))

    def _set_class_filter(self, filter_key: str) -> None:
        if filter_key not in WEAPON_CLASS_FILTERS:
            return
        self._active_class_filter = filter_key
        self._refresh_filter_button_labels()
        self._populate_weapon_list()

    def _format_weapon_display(self, info: Dict[str, str]) -> str:
        zh = info["zh_name"]
        en = info["en_name"]
        if self._lang in ("zh_CN", "zh_TW"):
            base = f"{zh}  ({en})" if en and en.lower() != zh.lower() else zh
        else:
            base = f"{en}  ({zh})" if en and en.lower() != zh.lower() else (en or zh)
        cls_txt = I18N.weapon_class(info["weapon_class"], self._lang)
        fav = self._settings.favorite_weapon_stats.get(info["weapon_key"])
        if fav:
            n_pos = len(fav.get("positives") or [])
            n_neg = len(fav.get("negatives") or [])
            badge = I18N.t("panel.fav_saved_badge", self._lang, pos=n_pos, neg=n_neg)
            return f"★  {base}   [{cls_txt}]   {badge}"
        return f"{base}   [{cls_txt}]"

    def _populate_weapon_list(self) -> None:
        prev_key = self._selected_weapon_key
        query = (self._inp_search.text() or "").strip().lower()
        favs = self._settings.favorite_weapon_stats

        self._list_weapons.blockSignals(True)
        self._list_weapons.clear()
        matched_count = 0
        restore_row = -1

        for info in self._weapons_cache:
            w_key = info["weapon_key"]
            if self._active_class_filter == "favorites":
                if w_key not in favs:
                    continue
            elif self._active_class_filter != "all":
                if info["weapon_class"] != self._active_class_filter:
                    continue

            if query:
                haystack = f"{info['zh_name']} {info['en_name']} {info['slug']}".lower()
                if query not in haystack:
                    continue

            item = QListWidgetItem(self._format_weapon_display(info))
            item.setData(Qt.ItemDataRole.UserRole, w_key)
            self._list_weapons.addItem(item)
            if w_key == prev_key:
                restore_row = matched_count
            matched_count += 1

        if restore_row >= 0:
            self._list_weapons.setCurrentRow(restore_row)
        self._list_weapons.blockSignals(False)

        self._lbl_weapon_count.setText(
            I18N.t("panel.fav_weapon_count", self._lang, shown=matched_count, saved=len(favs))
        )
        self._on_weapon_selection_changed()

    def _select_weapon_in_list(self, weapon_name: str) -> bool:
        target = (weapon_name or "").strip().lower()
        if not target:
            return False
        for i in range(self._list_weapons.count()):
            item = self._list_weapons.item(i)
            w_key = str(item.data(Qt.ItemDataRole.UserRole) or "")
            info = self._weapons_by_key.get(w_key)
            if not info:
                continue
            if target in (w_key.lower(), info["zh_name"].lower(), info["en_name"].lower()):
                self._list_weapons.setCurrentRow(i)
                self._list_weapons.scrollToItem(item)
                return True
        return False

    def _on_weapon_selection_changed(self) -> None:
        item = self._list_weapons.currentItem()
        if item is None:
            self._selected_weapon_key = None
            self._btn_next_stats.setEnabled(False)
            return
        self._selected_weapon_key = str(item.data(Qt.ItemDataRole.UserRole) or "") or None
        self._btn_next_stats.setEnabled(self._selected_weapon_key is not None)

    # ---- Page 1: Pure-Selection Stats Checklist ----

    def select_weapon_and_open_stats(self, weapon_key: str) -> bool:
        """Programmatically select a weapon and switch directly to its legal stat checklist."""
        if weapon_key not in self._weapons_by_key:
            return False
        self._selected_weapon_key = weapon_key
        self._enter_stats_page()
        return True

    def _enter_stats_page(self) -> None:
        if not self._selected_weapon_key or self._selected_weapon_key not in self._weapons_by_key:
            return
        self._lbl_feedback.hide()
        self._render_stats_for_weapon(self._selected_weapon_key, preserve_current_checks=False)
        self._stack.setCurrentIndex(PAGE_STATS)

    def _back_to_weapons(self) -> None:
        self._lbl_feedback.hide()
        self._refresh_filter_button_labels()
        self._populate_weapon_list()
        self._stack.setCurrentIndex(PAGE_WEAPONS)

    @staticmethod
    def _clear_grid(grid: QGridLayout) -> None:
        while grid.count():
            child = grid.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

    def _render_stats_for_weapon(
        self,
        weapon_key: str,
        *,
        preserve_current_checks: bool = False,
    ) -> None:
        info = self._weapons_by_key.get(weapon_key)
        if not info:
            return

        if preserve_current_checks:
            checked_pos = set(self.get_checked_positive_stats())
            checked_neg = set(self.get_checked_negative_stats())
        else:
            saved = self._settings.get_weapon_favorite_stats(weapon_key) or {}
            checked_pos = set(saved.get("positives") or [])
            checked_neg = set(saved.get("negatives") or [])

        cls = info["weapon_class"]
        cls_label = I18N.weapon_class(cls, self._lang)
        title_name = info["zh_name"] if self._lang in ("zh_CN", "zh_TW") else (info["en_name"] or info["zh_name"])
        self._lbl_selected_weapon.setText(f"{title_name}  ·  [{cls_label}]")

        pos_pool, neg_pool = self._evaluator.get_available_stats_for_class(cls)

        self._clear_grid(self._grid_pos)
        self._clear_grid(self._grid_neg)
        self._pos_checkboxes.clear()
        self._neg_checkboxes.clear()

        cols = 3
        for idx, stat_zh in enumerate(pos_pool):
            cb = QCheckBox(f"+ {I18N.stat_name(stat_zh, self._lang)}")
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setChecked(stat_zh in checked_pos)
            cb.toggled.connect(lambda _chk=False: self._update_selection_summary())
            self._pos_checkboxes[stat_zh] = cb
            self._grid_pos.addWidget(cb, idx // cols, idx % cols)

        for idx, stat_zh in enumerate(neg_pool):
            cb = QCheckBox(f"- {I18N.stat_name(stat_zh, self._lang)}")
            cb.setCursor(Qt.CursorShape.PointingHandCursor)
            cb.setChecked(stat_zh in checked_neg)
            cb.toggled.connect(lambda _chk=False: self._update_selection_summary())
            self._neg_checkboxes[stat_zh] = cb
            self._grid_neg.addWidget(cb, idx // cols, idx % cols)

        has_saved = weapon_key in self._settings.favorite_weapon_stats
        self._btn_remove_weapon.setEnabled(has_saved)
        self._update_selection_summary()

    def get_checked_positive_stats(self) -> List[str]:
        return [k for k, cb in self._pos_checkboxes.items() if cb.isChecked()]

    def get_checked_negative_stats(self) -> List[str]:
        return [k for k, cb in self._neg_checkboxes.items() if cb.isChecked()]

    def set_stat_checked(self, stat_zh: str, checked: bool, *, is_negative: bool = False) -> bool:
        target = self._neg_checkboxes if is_negative else self._pos_checkboxes
        cb = target.get(stat_zh)
        if cb is None:
            return False
        cb.setChecked(bool(checked))
        return True

    def _update_selection_summary(self) -> None:
        pos = self.get_checked_positive_stats()
        neg = self.get_checked_negative_stats()
        pos_txt = " / ".join(I18N.stat_name(s, self._lang) for s in pos) or "-"
        neg_txt = " / ".join(I18N.stat_name(s, self._lang) for s in neg) or "-"
        self._lbl_selection_summary.setText(
            I18N.t(
                "panel.fav_selection_summary",
                self._lang,
                pos_cnt=len(pos),
                neg_cnt=len(neg),
                pos_list=pos_txt,
                neg_list=neg_txt,
            )
        )

    def _on_fill_recommended_clicked(self) -> None:
        if not self._selected_weapon_key:
            return
        info = self._weapons_by_key.get(self._selected_weapon_key)
        if not info:
            return
        rec_pos, rec_neg, _src, _price = self._evaluator.resolve_recommendations(
            self._selected_weapon_key,
            info["weapon_class"],
        )
        pos_set = set(rec_pos)
        neg_set = set(rec_neg)
        for k, cb in self._pos_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(k in pos_set)
            cb.blockSignals(False)
        for k, cb in self._neg_checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(k in neg_set)
            cb.blockSignals(False)
        self._update_selection_summary()

    def _on_clear_checks_clicked(self) -> None:
        for cb in self._pos_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        for cb in self._neg_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._update_selection_summary()

    def _show_feedback(self, msg: str, *, is_warn: bool = False) -> None:
        color = "#FCD34D" if is_warn else "#6EE7B7"
        self._lbl_feedback.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        self._lbl_feedback.setText(msg)
        self._lbl_feedback.show()

    def _on_save_clicked(self) -> None:
        if not self._selected_weapon_key:
            return
        pos = self.get_checked_positive_stats()
        neg = self.get_checked_negative_stats()
        info = self._weapons_by_key.get(self._selected_weapon_key, {})
        disp_name = (
            info.get("zh_name", self._selected_weapon_key)
            if self._lang in ("zh_CN", "zh_TW")
            else (info.get("en_name") or info.get("zh_name") or self._selected_weapon_key)
        )
        self._settings.set_weapon_favorite_stats(self._selected_weapon_key, pos, neg)
        self._evaluator.set_user_favorite_stats(self._settings.favorite_weapon_stats)
        self._btn_remove_weapon.setEnabled(self._selected_weapon_key in self._settings.favorite_weapon_stats)
        self._refresh_filter_button_labels()
        self.favorites_saved.emit(dict(self._settings.favorite_weapon_stats))
        if pos or neg:
            self._show_feedback(
                I18N.t("panel.fav_saved_ok", self._lang, weapon=disp_name, pos=len(pos), neg=len(neg))
            )
        else:
            self._show_feedback(I18N.t("panel.fav_cleared_ok", self._lang, weapon=disp_name))

    def _on_remove_weapon_favorite(self) -> None:
        if not self._selected_weapon_key:
            return
        info = self._weapons_by_key.get(self._selected_weapon_key, {})
        disp_name = (
            info.get("zh_name", self._selected_weapon_key)
            if self._lang in ("zh_CN", "zh_TW")
            else (info.get("en_name") or info.get("zh_name") or self._selected_weapon_key)
        )
        self._settings.remove_weapon_favorite_stats(self._selected_weapon_key)
        self._evaluator.set_user_favorite_stats(self._settings.favorite_weapon_stats)
        self._on_clear_checks_clicked()
        self._btn_remove_weapon.setEnabled(False)
        self._refresh_filter_button_labels()
        self.favorites_saved.emit(dict(self._settings.favorite_weapon_stats))
        self._show_feedback(I18N.t("panel.fav_cleared_ok", self._lang, weapon=disp_name))
