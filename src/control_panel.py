"""Visual Settings & Control Panel for Warframe Riven Overlay.

A standard interactive desktop window (clickable, NOT click-through). Closing
the window hides it back to the system tray instead of exiting the application,
and automatically re-locks the overlay to click-through mode.

Sections:
  1. Global Hotkey Customization (live key capture + hot-reload + conflict alerts)
  2. Warframe.market Query & Local Database Update (live status & progress bar)
  3. Appearance, Position & Preferences (Drag unlock / Reset position, Font size
     10px~24px live slider, Language switcher, and Launch default monitoring mode)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QColor, QFont, QGuiApplication, QIcon, QKeyEvent, QMoveEvent, QPixmap, QResizeEvent, QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.audio_cue import validate_wav_file
from src.data_updater import get_data_dir
from src.hotkey_manager import (
    DEFAULT_EXPORT_HOTKEY,
    DEFAULT_LOCK_HOTKEY,
    DEFAULT_OPEN_PANEL_HOTKEY,
    HotkeyApplyReport,
    parse_hotkey_string,
)
from src.i18n import I18N, LANGUAGE_DISPLAY_NAMES, SUPPORTED_LANGS
from src.roi_manager import (
    MODE_CYCLE_ORDER,
    MODE_PAUSED,
    MODE_SINGLE_RIVEN,
    format_mode_badge,
)
from src.settings_manager import (
    DEFAULT_PANEL_HEIGHT,
    DEFAULT_PANEL_WIDTH,
    DEFAULT_THEME_PRESET,
    MAX_OVERLAY_FONT_PX,
    MIN_OVERLAY_FONT_PX,
    MIN_PANEL_HEIGHT,
    MIN_PANEL_WIDTH,
    THEME_COLOR_KEYS,
    THEME_PRESET_ORDER,
    THEME_PRESETS,
    UserSettings,
    clamp_overlay_font_size,
    normalize_hex_color,
    resolve_theme_colors,
)
from src.tray_manager import load_app_icon
from src.wm_client import WMMarketReport

# Map Qt.Key values to canonical hotkey names accepted by `parse_hotkey_string`
_QT_KEY_NAMES = {
    **{getattr(Qt.Key, f"Key_F{i}"): f"F{i}" for i in range(1, 13)},
    **{getattr(Qt.Key, f"Key_{chr(c)}"): chr(c) for c in range(ord("A"), ord("Z") + 1)},
    **{getattr(Qt.Key, f"Key_{d}"): str(d) for d in range(10)},
    Qt.Key.Key_Escape: "ESC",
    Qt.Key.Key_Tab: "Tab",
    Qt.Key.Key_Space: "Space",
    Qt.Key.Key_Insert: "Insert",
    Qt.Key.Key_Delete: "Delete",
    Qt.Key.Key_Home: "Home",
    Qt.Key.Key_End: "End",
    Qt.Key.Key_PageUp: "PageUp",
    Qt.Key.Key_PageDown: "PageDown",
    Qt.Key.Key_Pause: "Pause",
    Qt.Key.Key_QuoteLeft: "`",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
}

_MODIFIER_ONLY_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_Meta,
    Qt.Key.Key_AltGr,
}


def read_local_db_metadata(data_dir: Optional[Path] = None) -> tuple[Optional[str], int]:
    """Read last-updated local timestamp string and weapon count from `weapons_riven.json`."""
    d = data_dir or get_data_dir()
    weapons_path = d / "weapons_riven.json"
    if not weapons_path.is_file():
        return None, 0
    try:
        payload = json.loads(weapons_path.read_text(encoding="utf-8"))
        count = int(payload.get("weapon_count") or len(payload.get("weapons") or {}))
        ts = payload.get("updated_at")
        if isinstance(ts, (int, float)) and ts > 0:
            dt = datetime.fromtimestamp(float(ts))
            return dt.strftime("%Y-%m-%d %H:%M"), count
        iso = payload.get("updated_at_iso")
        if isinstance(iso, str) and iso:
            return iso.replace("T", " ")[:16], count
        return None, count
    except Exception:
        return None, 0


class HotkeyInput(QLineEdit):
    """Interactive hotkey recorder input.

    Click the field and press any supported key or modifier combo (e.g. `F8`,
    `Alt+R`, `Ctrl+Shift+R`, `ESC`) to capture it immediately.
    """

    def __init__(self, initial_value: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(initial_value, parent)
        self.setReadOnly(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(160)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in _MODIFIER_ONLY_KEYS:
            event.accept()
            return

        if key == Qt.Key.Key_Backspace:
            event.accept()
            return

        key_name = _QT_KEY_NAMES.get(key)
        if key_name is None:
            event.accept()
            return

        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        parts.append(key_name)

        candidate = "+".join(parts)
        try:
            parsed = parse_hotkey_string(candidate)
            self.setText(parsed.display)
        except ValueError:
            pass
        event.accept()


class RivenControlPanel(QWidget):
    """Normal interactive Settings & Control Panel window (hides to tray on close)."""

    hotkeys_apply_requested = pyqtSignal(str, str, str)  # toggle, wm, hide (legacy)
    hotkeys_all_apply_requested = pyqtSignal(str, str, str, str)  # toggle, wm, hide, open_panel
    wm_fetch_requested = pyqtSignal(str)                 # weapon_query (empty = current/last)
    open_wm_browser_requested = pyqtSignal(str)          # weapon_query (empty = use last search URL)
    db_update_requested = pyqtSignal()
    language_mode_changed = pyqtSignal(str)              # "auto" | any of 15 SUPPORTED_LANGS
    language_changed = pyqtSignal(str)                   # global language change signal
    start_monitoring_changed = pyqtSignal(bool)
    toggle_monitor_requested = pyqtSignal()
    mode_selected = pyqtSignal(str)
    calibrate_regions_requested = pyqtSignal()
    drag_unlock_changed = pyqtSignal(bool)
    reset_position_requested = pyqtSignal()
    font_size_changed = pyqtSignal(int)
    auto_hide_focus_changed = pyqtSignal(bool)
    audio_cue_changed = pyqtSignal(bool)
    audio_test_requested = pyqtSignal()
    custom_rare_sound_changed = pyqtSignal(str)
    hotkey_lock_toggled = pyqtSignal()
    export_card_requested = pyqtSignal()
    user_manual_requested = pyqtSignal()
    favorite_stats_requested = pyqtSignal()
    open_wfsim_requested = pyqtSignal()
    open_build_optimizer_requested = pyqtSignal()
    theme_changed = pyqtSignal(str, dict)                # (preset_key, colors_dict)
    panel_geometry_changed = pyqtSignal(int, int, int, int)  # x, y, width, height
    panel_closed = pyqtSignal()

    def __init__(
        self,
        settings: UserSettings,
        initial_ui_lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ui_lang = initial_ui_lang
        self._settings = settings
        self._monitoring = False
        self._current_mode = MODE_PAUSED
        self._wm_busy = False
        self._db_busy = False
        self._hotkeys_locked = False
        self._theme_preset = settings.theme_preset
        self._theme_colors = resolve_theme_colors(settings.theme_preset, settings.theme_colors)
        self._color_btns: dict[str, QPushButton] = {}
        self._geometry_tracking_ready = False
        self._last_saved_geometry: Optional[tuple[int, int, int, int]] = None

        self._geo_save_timer = QTimer(self)
        self._geo_save_timer.setSingleShot(True)
        self._geo_save_timer.setInterval(350)
        self._geo_save_timer.timeout.connect(self._emit_current_geometry)

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(MIN_PANEL_WIDTH, MIN_PANEL_HEIGHT)
        self._apply_stylesheet()
        self._build_ui()
        self.sync_from_settings(settings)
        self.refresh_db_timestamp()
        self.retranslate_ui(initial_ui_lang)
        self._apply_initial_geometry(settings.panel_geometry)

    @staticmethod
    def _blend_hex(c1_hex: str, c2_hex: str, ratio: float) -> str:
        """Linearly blend two QColor hex strings (`ratio=0` -> c1, `ratio=1` -> c2)."""
        a = QColor(c1_hex)
        b = QColor(c2_hex)
        r = max(0, min(255, int(round(a.red() * (1.0 - ratio) + b.red() * ratio))))
        g = max(0, min(255, int(round(a.green() * (1.0 - ratio) + b.green() * ratio))))
        bl = max(0, min(255, int(round(a.blue() * (1.0 - ratio) + b.blue() * ratio))))
        return f"#{r:02X}{g:02X}{bl:02X}"

    @staticmethod
    def _contrast_text_hex(bg_hex: str) -> str:
        c = QColor(bg_hex)
        lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        return "#0B0F19" if lum >= 150 else "#FFFFFF"

    def _apply_stylesheet(self) -> None:
        colors = getattr(self, "_theme_colors", None) or resolve_theme_colors(DEFAULT_THEME_PRESET)
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#F3ECFF")

        surface = self._blend_hex(bg, "#FFFFFF", 0.06)
        surface_hdr = self._blend_hex(bg, border, 0.14)
        input_bg = self._blend_hex(bg, "#000000", 0.22)
        focus_bg = self._blend_hex(bg, border, 0.12)
        border_soft = self._blend_hex(bg, border, 0.55)
        border_dim = self._blend_hex(bg, border, 0.32)
        muted_text = self._blend_hex(text, border, 0.35)
        title_text = self._blend_hex(text, accent, 0.25)

        btn_bg = self._blend_hex(bg, border, 0.68)
        btn_hover = self._blend_hex(bg, border, 0.85)
        btn_pressed = self._blend_hex(bg, border, 0.52)
        btn_fg = self._contrast_text_hex(btn_bg)

        sec_bg = self._blend_hex(bg, border, 0.18)
        sec_hover = self._blend_hex(bg, border, 0.30)
        accent_btn_bg = self._blend_hex(bg, accent, 0.24)
        accent_btn_hover = self._blend_hex(bg, accent, 0.38)
        drag_checked_fg = self._contrast_text_hex(accent)
        disabled_bg = self._blend_hex(bg, "#FFFFFF", 0.10)
        disabled_fg = self._blend_hex(bg, text, 0.45)

        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {bg};
                color: {text};
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }}
            QGroupBox {{
                background-color: {surface};
                border: 1px solid {border_soft};
                border-radius: 10px;
                margin-top: 12px;
                padding: 16px 14px 14px 14px;
                font-weight: bold;
                color: {title_text};
            }}
            QScrollArea {{
                background-color: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: {bg};
                width: 8px;
                margin: 2px 0 2px 0;
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
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 6px;
                color: {muted_text};
            }}
            QLineEdit, QSpinBox {{
                background-color: {input_bg};
                color: {accent};
                border: 1.5px solid {border_soft};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 13px;
                font-weight: 600;
            }}
            QLineEdit:focus, QSpinBox:focus {{
                border: 1.5px solid {accent};
                background-color: {focus_bg};
            }}
            QComboBox {{
                background-color: {input_bg};
                color: {text};
                border: 1.5px solid {border_soft};
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 170px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {surface};
                color: {text};
                selection-background-color: {btn_bg};
                selection-color: {btn_fg};
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
            QPushButton:pressed {{
                background-color: {btn_pressed};
            }}
            QPushButton:disabled {{
                background-color: {disabled_bg};
                color: {disabled_fg};
                border-color: {border_dim};
            }}
            QPushButton#SecondaryBtn {{
                background-color: {sec_bg};
                color: {text};
                border: 1px solid {border_soft};
            }}
            QPushButton#SecondaryBtn:hover {{
                background-color: {sec_hover};
            }}
            QPushButton#AccentBtn {{
                background-color: {accent_btn_bg};
                color: {text};
                border: 1px solid {accent};
            }}
            QPushButton#AccentBtn:hover {{
                background-color: {accent_btn_hover};
            }}
            QPushButton#DragToggleBtn {{
                background-color: {sec_bg};
                color: {accent};
                border: 1.5px solid {border};
            }}
            QPushButton#DragToggleBtn:hover {{
                background-color: {sec_hover};
            }}
            QPushButton#DragToggleBtn:checked {{
                background-color: {accent};
                color: {drag_checked_fg};
                border: 1.5px solid {accent};
            }}
            QSlider::groove:horizontal {{
                background: {input_bg};
                border: 1px solid {border_soft};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::sub-page:horizontal {{
                background: {border};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {accent};
                border: 1.5px solid {bg};
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }}
            QCheckBox {{
                spacing: 8px;
                color: {text};
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1.5px solid {border};
                background-color: {input_bg};
            }}
            QCheckBox::indicator:checked {{
                background-color: {border};
                border-color: {accent};
            }}
            QProgressBar {{
                background-color: {input_bg};
                border: 1px solid {border_soft};
                border-radius: 5px;
                height: 10px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background-color: {accent};
                border-radius: 4px;
            }}
            """
        )

        if hasattr(self, "_header_box"):
            self._header_box.setStyleSheet(
                f"QFrame {{ background-color: {surface_hdr}; border: 1px solid {border}; border-radius: 10px; }}"
            )
        if hasattr(self, "_header_title"):
            self._header_title.setStyleSheet(f"color: {accent}; background: transparent;")
        if hasattr(self, "_scroll_content"):
            self._scroll_content.setStyleSheet(f"background-color: {bg};")
        readonly_css = (
            f"QLineEdit {{ background-color: {input_bg}; color: {muted_text}; "
            f"border: 1px solid {border_dim}; border-radius: 6px; padding: 5px 10px; "
            f"font-size: 13px; font-weight: 600; }}"
        )
        if hasattr(self, "_inp_hk_export"):
            self._inp_hk_export.setStyleSheet(readonly_css)
        if hasattr(self, "_inp_hk_lock"):
            self._inp_hk_lock.setStyleSheet(readonly_css)
        if hasattr(self, "_sep_data"):
            self._sep_data.setStyleSheet(f"background-color: {border_dim}; max-height: 1px;")
        if hasattr(self, "_sep_pref_1"):
            self._sep_pref_1.setStyleSheet(f"background-color: {border_dim}; max-height: 1px; margin: 4px 0;")
        if hasattr(self, "_lbl_db_info"):
            self._lbl_db_info.setStyleSheet(f"color: {muted_text}; font-size: 12px; font-weight: normal;")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        # Header banner with quick monitor status & toggle (pinned at top)
        self._header_box = QFrame()
        hdr_lay = QHBoxLayout(self._header_box)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        hdr_lay.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self._header_title = QLabel("")
        self._header_title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold))
        title_col.addWidget(self._header_title)

        self._header_status = QLabel("")
        self._header_status.setStyleSheet("color: #C4B5FD; font-size: 12px; background: transparent;")
        self._header_status.setWordWrap(True)
        title_col.addWidget(self._header_status)
        hdr_lay.addLayout(title_col, stretch=1)

        self._cmb_mode = QComboBox()
        self._cmb_mode.setMinimumWidth(185)
        for m in MODE_CYCLE_ORDER:
            self._cmb_mode.addItem("", m)
        self._cmb_mode.currentIndexChanged.connect(self._on_mode_combo_changed)
        hdr_lay.addWidget(self._cmb_mode)

        self._btn_quick_toggle = QPushButton("")
        self._btn_quick_toggle.clicked.connect(self.toggle_monitor_requested.emit)
        hdr_lay.addWidget(self._btn_quick_toggle)
        root.addWidget(self._header_box)

        # Scrollable container holding all major sections with clear generous spacing
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._scroll_content = QWidget()
        content_lay = QVBoxLayout(self._scroll_content)
        content_lay.setContentsMargins(2, 4, 8, 12)
        content_lay.setSpacing(22)  # Clear visual breathing space between major sections
        self._scroll_area.setWidget(self._scroll_content)
        root.addWidget(self._scroll_area, stretch=1)

        # --- Section 1: Global Hotkey Customization ---
        self._grp_hotkeys = QGroupBox("")
        hk_outer = QVBoxLayout(self._grp_hotkeys)
        hk_outer.setSpacing(9)

        # Verbose hint removed per minimalist UI design (attribute retained hidden for compatibility)
        self._hk_hint = QLabel("")
        self._hk_hint.hide()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setSpacing(7)

        self._lbl_hk_toggle = QLabel("")
        self._inp_hk_toggle = HotkeyInput(self._settings.hotkey_toggle_monitor)
        form.addRow(self._lbl_hk_toggle, self._inp_hk_toggle)

        self._lbl_hk_wm = QLabel("")
        self._inp_hk_wm = HotkeyInput(self._settings.hotkey_fetch_wm)
        form.addRow(self._lbl_hk_wm, self._inp_hk_wm)

        self._lbl_hk_hide = QLabel("")
        self._inp_hk_hide = HotkeyInput(self._settings.hotkey_hide_overlay)
        form.addRow(self._lbl_hk_hide, self._inp_hk_hide)

        self._lbl_hk_open_panel = QLabel("")
        self._inp_hk_open_panel = HotkeyInput(self._settings.hotkey_open_panel)
        form.addRow(self._lbl_hk_open_panel, self._inp_hk_open_panel)

        self._lbl_hk_export = QLabel("")
        self._inp_hk_export = QLineEdit(DEFAULT_EXPORT_HOTKEY)
        self._inp_hk_export.setReadOnly(True)
        self._inp_hk_export.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inp_hk_export.setMinimumWidth(160)
        self._inp_hk_export.setStyleSheet(
            "QLineEdit { background-color: #161026; color: #C4B5FD; border: 1px solid #3D2B63; border-radius: 6px; padding: 5px 10px; font-size: 13px; font-weight: 600; }"
        )
        form.addRow(self._lbl_hk_export, self._inp_hk_export)

        self._lbl_hk_lock = QLabel("")
        self._inp_hk_lock = QLineEdit(DEFAULT_LOCK_HOTKEY)
        self._inp_hk_lock.setReadOnly(True)
        self._inp_hk_lock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inp_hk_lock.setMinimumWidth(160)
        self._inp_hk_lock.setStyleSheet(
            "QLineEdit { background-color: #161026; color: #C4B5FD; border: 1px solid #3D2B63; border-radius: 6px; padding: 5px 10px; font-size: 13px; font-weight: 600; }"
        )
        form.addRow(self._lbl_hk_lock, self._inp_hk_lock)

        hk_outer.addLayout(form)

        hk_btn_row = QHBoxLayout()
        hk_btn_row.setSpacing(8)
        self._btn_apply_hk = QPushButton("")
        self._btn_apply_hk.clicked.connect(self._on_apply_hotkeys_clicked)
        hk_btn_row.addWidget(self._btn_apply_hk)

        self._btn_reset_hk = QPushButton("")
        self._btn_reset_hk.setObjectName("SecondaryBtn")
        self._btn_reset_hk.clicked.connect(self._on_reset_hotkeys_clicked)
        hk_btn_row.addWidget(self._btn_reset_hk)
        hk_btn_row.addStretch(1)
        hk_outer.addLayout(hk_btn_row)

        self._lbl_hk_feedback = QLabel("")
        self._lbl_hk_feedback.setWordWrap(True)
        self._lbl_hk_feedback.setStyleSheet("font-size: 12px; font-weight: normal;")
        self._lbl_hk_feedback.hide()
        hk_outer.addWidget(self._lbl_hk_feedback)

        content_lay.addWidget(self._grp_hotkeys)

        # --- Section 2: Data & Market Actions ---
        self._grp_data = QGroupBox("")
        data_lay = QVBoxLayout(self._grp_data)
        data_lay.setSpacing(10)

        wm_row = QHBoxLayout()
        wm_row.setSpacing(8)
        self._inp_weapon = QLineEdit()
        self._inp_weapon.returnPressed.connect(self._on_fetch_wm_clicked)
        wm_row.addWidget(self._inp_weapon, stretch=1)

        self._btn_fetch_wm = QPushButton("")
        self._btn_fetch_wm.setObjectName("AccentBtn")
        self._btn_fetch_wm.clicked.connect(self._on_fetch_wm_clicked)
        wm_row.addWidget(self._btn_fetch_wm)
        data_lay.addLayout(wm_row)

        self._lbl_wm_status = QLabel("")
        self._lbl_wm_status.setWordWrap(True)
        self._lbl_wm_status.setStyleSheet(
            """
            QLabel {
                background-color: #110B1D;
                border: 1px solid #352950;
                border-radius: 6px;
                padding: 7px 10px;
                color: #BAE6FD;
                font-size: 12px;
                font-weight: normal;
            }
            """
        )
        self._lbl_wm_status.hide()
        data_lay.addWidget(self._lbl_wm_status)

        wm_link_row = QHBoxLayout()
        wm_link_row.setSpacing(8)
        self._btn_open_wm_browser = QPushButton("")
        self._btn_open_wm_browser.setObjectName("SecondaryBtn")
        self._btn_open_wm_browser.clicked.connect(self._on_open_wm_browser_clicked)
        wm_link_row.addWidget(self._btn_open_wm_browser)

        self._btn_favorite_stats = QPushButton("")
        self._btn_favorite_stats.setObjectName("AccentBtn")
        self._btn_favorite_stats.clicked.connect(self.favorite_stats_requested.emit)
        wm_link_row.addWidget(self._btn_favorite_stats)
        wm_link_row.addStretch(1)
        data_lay.addLayout(wm_link_row)

        wfsim_row = QHBoxLayout()
        wfsim_row.setSpacing(8)
        self._btn_build_optimizer = QPushButton("")
        self._btn_build_optimizer.setObjectName("AccentBtn")
        self._btn_build_optimizer.clicked.connect(self.open_build_optimizer_requested.emit)
        wfsim_row.addWidget(self._btn_build_optimizer)

        self._btn_open_wfsim = QPushButton("")
        self._btn_open_wfsim.setObjectName("SecondaryBtn")
        self._btn_open_wfsim.clicked.connect(self.open_wfsim_requested.emit)
        wfsim_row.addWidget(self._btn_open_wfsim)
        wfsim_row.addStretch(1)
        data_lay.addLayout(wfsim_row)

        self._lbl_wfsim_status = QLabel("")
        self._lbl_wfsim_status.setWordWrap(True)
        self._lbl_wfsim_status.setStyleSheet("color: #6EE7B7; font-size: 12px; font-weight: normal;")
        self._lbl_wfsim_status.hide()
        data_lay.addWidget(self._lbl_wfsim_status)

        self._sep_data = QFrame()
        self._sep_data.setFrameShape(QFrame.Shape.HLine)
        data_lay.addWidget(self._sep_data)

        db_row = QHBoxLayout()
        db_row.setSpacing(10)
        self._lbl_db_info = QLabel("")
        self._lbl_db_info.setWordWrap(True)
        self._lbl_db_info.setStyleSheet("color: #C4B5FD; font-size: 12px; font-weight: normal;")
        db_row.addWidget(self._lbl_db_info, stretch=1)

        self._btn_update_db = QPushButton("")
        self._btn_update_db.clicked.connect(self.db_update_requested.emit)
        db_row.addWidget(self._btn_update_db)
        data_lay.addLayout(db_row)

        self._db_progress = QProgressBar()
        self._db_progress.setRange(0, 100)
        self._db_progress.setValue(0)
        self._db_progress.setTextVisible(False)
        self._db_progress.hide()
        data_lay.addWidget(self._db_progress)

        self._lbl_db_status = QLabel("")
        self._lbl_db_status.setWordWrap(True)
        self._lbl_db_status.setStyleSheet("color: #A7F3D0; font-size: 12px; font-weight: normal;")
        self._lbl_db_status.hide()
        data_lay.addWidget(self._lbl_db_status)

        content_lay.addWidget(self._grp_data)

        # --- Section 3: Appearance, Position & Preferences ---
        self._grp_prefs = QGroupBox("")
        pref_lay = QVBoxLayout(self._grp_prefs)
        pref_lay.setSpacing(11)

        # Overlay drag unlock + ROI calibrator row (never squished at 600px width)
        pos_row = QHBoxLayout()
        pos_row.setSpacing(8)
        self._btn_calibrate_roi = QPushButton("")
        self._btn_calibrate_roi.setObjectName("AccentBtn")
        self._btn_calibrate_roi.clicked.connect(self.calibrate_regions_requested.emit)
        pos_row.addWidget(self._btn_calibrate_roi)

        self._btn_drag_unlock = QPushButton("")
        self._btn_drag_unlock.setObjectName("DragToggleBtn")
        self._btn_drag_unlock.setCheckable(True)
        self._btn_drag_unlock.toggled.connect(self._on_drag_unlock_toggled)
        pos_row.addWidget(self._btn_drag_unlock)
        pos_row.addStretch(1)
        pref_lay.addLayout(pos_row)

        pos_row_2 = QHBoxLayout()
        pos_row_2.setSpacing(8)
        self._btn_reset_pos = QPushButton("")
        self._btn_reset_pos.setObjectName("SecondaryBtn")
        self._btn_reset_pos.clicked.connect(self._on_reset_pos_clicked)
        pos_row_2.addWidget(self._btn_reset_pos)

        self._btn_lock_hotkeys = QPushButton("")
        self._btn_lock_hotkeys.setObjectName("SecondaryBtn")
        self._btn_lock_hotkeys.setCheckable(True)
        self._btn_lock_hotkeys.clicked.connect(self.hotkey_lock_toggled.emit)
        pos_row_2.addWidget(self._btn_lock_hotkeys)
        pos_row_2.addStretch(1)
        pref_lay.addLayout(pos_row_2)

        # Verbose position hint removed per minimalist UI design (attribute retained hidden)
        self._lbl_pos_info = QLabel("")
        self._lbl_pos_info.hide()

        # Quick QoL action row: Export Showcase PNG (Ctrl+Alt+S) + User Manual
        qol_btn_row = QHBoxLayout()
        qol_btn_row.setSpacing(8)
        self._btn_export_card = QPushButton("")
        self._btn_export_card.setObjectName("AccentBtn")
        self._btn_export_card.clicked.connect(self.export_card_requested.emit)
        qol_btn_row.addWidget(self._btn_export_card)

        self._btn_user_manual = QPushButton("")
        self._btn_user_manual.setObjectName("SecondaryBtn")
        self._btn_user_manual.clicked.connect(self.user_manual_requested.emit)
        qol_btn_row.addWidget(self._btn_user_manual)
        qol_btn_row.addStretch(1)
        pref_lay.addLayout(qol_btn_row)

        self._lbl_export_status = QLabel("")
        self._lbl_export_status.setWordWrap(True)
        self._lbl_export_status.setStyleSheet("color: #6EE7B7; font-size: 11px; font-weight: normal;")
        self._lbl_export_status.hide()
        pref_lay.addWidget(self._lbl_export_status)

        self._sep_pref_1 = QFrame()
        self._sep_pref_1.setFrameShape(QFrame.Shape.HLine)
        pref_lay.addWidget(self._sep_pref_1)

        # Audio cue toggle + preview + custom WAV replace/reset buttons
        audio_row = QHBoxLayout()
        audio_row.setSpacing(8)
        self._chk_audio_cue = QCheckBox("")
        self._chk_audio_cue.toggled.connect(self._on_audio_cue_toggled)
        audio_row.addWidget(self._chk_audio_cue, stretch=1)
        pref_lay.addLayout(audio_row)

        audio_btn_row = QHBoxLayout()
        audio_btn_row.setSpacing(8)
        self._btn_audio_test = QPushButton("")
        self._btn_audio_test.setObjectName("SecondaryBtn")
        self._btn_audio_test.clicked.connect(self.audio_test_requested.emit)
        audio_btn_row.addWidget(self._btn_audio_test)

        self._btn_replace_sound = QPushButton("")
        self._btn_replace_sound.setObjectName("SecondaryBtn")
        self._btn_replace_sound.clicked.connect(self._on_replace_sound_clicked)
        audio_btn_row.addWidget(self._btn_replace_sound)

        self._btn_reset_sound = QPushButton("")
        self._btn_reset_sound.setObjectName("SecondaryBtn")
        self._btn_reset_sound.clicked.connect(self._on_reset_sound_clicked)
        audio_btn_row.addWidget(self._btn_reset_sound)
        audio_btn_row.addStretch(1)
        pref_lay.addLayout(audio_btn_row)

        self._lbl_sound_file = QLabel("")
        self._lbl_sound_file.setWordWrap(True)
        self._lbl_sound_file.setStyleSheet("color: #A78BFA; font-size: 11px; font-weight: normal;")
        pref_lay.addWidget(self._lbl_sound_file)

        # Smart focus auto-hide checkbox
        self._chk_auto_hide_focus = QCheckBox("")
        self._chk_auto_hide_focus.toggled.connect(self._on_auto_hide_focus_toggled)
        pref_lay.addWidget(self._chk_auto_hide_focus)

        # Font size slider + spinbox row (10px ~ 24px, default 13px)
        font_row = QHBoxLayout()
        font_row.setSpacing(10)
        self._lbl_font_size = QLabel("")
        self._lbl_font_size.setStyleSheet("font-weight: normal;")
        font_row.addWidget(self._lbl_font_size)

        self._slider_font = QSlider(Qt.Orientation.Horizontal)
        self._slider_font.setRange(MIN_OVERLAY_FONT_PX, MAX_OVERLAY_FONT_PX)
        self._slider_font.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider_font.setTickInterval(2)
        self._slider_font.valueChanged.connect(self._on_font_slider_changed)
        font_row.addWidget(self._slider_font, stretch=1)

        self._spin_font = QSpinBox()
        self._spin_font.setRange(MIN_OVERLAY_FONT_PX, MAX_OVERLAY_FONT_PX)
        self._spin_font.setSuffix(" px")
        self._spin_font.setFixedWidth(82)
        self._spin_font.valueChanged.connect(self._on_font_spin_changed)
        font_row.addWidget(self._spin_font)
        pref_lay.addLayout(font_row)

        # Color Theme Presets + Free 4-Slot Custom Color Picker
        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        self._lbl_theme = QLabel("")
        self._lbl_theme.setStyleSheet("font-weight: normal;")
        theme_row.addWidget(self._lbl_theme)

        self._cmb_theme_preset = QComboBox()
        for pkey in THEME_PRESET_ORDER:
            self._cmb_theme_preset.addItem(self._make_preset_icon(THEME_PRESETS[pkey]), "", pkey)
        self._cmb_theme_preset.addItem("", "custom")
        self._cmb_theme_preset.currentIndexChanged.connect(self._on_theme_preset_changed)
        theme_row.addWidget(self._cmb_theme_preset, stretch=1)

        self._btn_reset_theme = QPushButton("")
        self._btn_reset_theme.setObjectName("SecondaryBtn")
        self._btn_reset_theme.clicked.connect(self._on_reset_theme_clicked)
        theme_row.addWidget(self._btn_reset_theme)
        pref_lay.addLayout(theme_row)

        color_btn_row = QHBoxLayout()
        color_btn_row.setSpacing(6)
        for ckey in THEME_COLOR_KEYS:
            btn = QPushButton("")
            btn.setObjectName("SecondaryBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _chk=False, k=ckey: self._on_pick_color_clicked(k))
            self._color_btns[ckey] = btn
            color_btn_row.addWidget(btn, stretch=1)
        pref_lay.addLayout(color_btn_row)

        # Language row (all 15 official Warframe languages + Auto)
        lang_row = QHBoxLayout()
        lang_row.setSpacing(10)
        self._lbl_lang = QLabel("")
        self._lbl_lang.setStyleSheet("font-weight: normal;")
        lang_row.addWidget(self._lbl_lang)

        self._cmb_lang = QComboBox()
        self._cmb_lang.addItem("", "auto")
        for code in SUPPORTED_LANGS:
            self._cmb_lang.addItem(LANGUAGE_DISPLAY_NAMES.get(code, code), code)
        self._cmb_lang.currentIndexChanged.connect(self._on_lang_combo_changed)
        lang_row.addWidget(self._cmb_lang)
        lang_row.addStretch(1)
        pref_lay.addLayout(lang_row)

        self._lbl_ocr_model_status = QLabel("")
        self._lbl_ocr_model_status.setWordWrap(True)
        self._lbl_ocr_model_status.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: normal;")
        self._lbl_ocr_model_status.hide()
        pref_lay.addWidget(self._lbl_ocr_model_status)

        self._chk_start_monitor = QCheckBox("")
        self._chk_start_monitor.toggled.connect(self._on_start_monitor_toggled)
        pref_lay.addWidget(self._chk_start_monitor)

        # Verbose tray explanation removed per minimalist UI design (attribute retained hidden)
        self._lbl_tray_tip = QLabel("")
        self._lbl_tray_tip.hide()

        content_lay.addWidget(self._grp_prefs)
        content_lay.addStretch(1)

    # ---- Public sync helpers ----

    def sync_from_settings(self, settings: UserSettings) -> None:
        self._settings = settings
        self._inp_hk_toggle.setText(settings.hotkey_toggle_monitor)
        self._inp_hk_wm.setText(settings.hotkey_fetch_wm)
        self._inp_hk_hide.setText(settings.hotkey_hide_overlay)
        self._inp_hk_open_panel.setText(settings.hotkey_open_panel)

        font_px = clamp_overlay_font_size(settings.overlay_font_size)
        self._slider_font.blockSignals(True)
        self._spin_font.blockSignals(True)
        self._slider_font.setValue(font_px)
        self._spin_font.setValue(font_px)
        self._slider_font.blockSignals(False)
        self._spin_font.blockSignals(False)

        self._cmb_lang.blockSignals(True)
        idx = self._cmb_lang.findData(settings.language_mode)
        self._cmb_lang.setCurrentIndex(idx if idx >= 0 else 0)
        self._cmb_lang.blockSignals(False)

        self._chk_start_monitor.blockSignals(True)
        self._chk_start_monitor.setChecked(settings.start_monitoring_on_launch)
        self._chk_start_monitor.blockSignals(False)

        self._chk_auto_hide_focus.blockSignals(True)
        self._chk_auto_hide_focus.setChecked(settings.auto_hide_on_focus_loss)
        self._chk_auto_hide_focus.blockSignals(False)

        self._chk_audio_cue.blockSignals(True)
        self._chk_audio_cue.setChecked(settings.audio_cue_enabled)
        self._chk_audio_cue.blockSignals(False)

        self._theme_preset = settings.theme_preset
        self._theme_colors = resolve_theme_colors(settings.theme_preset, settings.theme_colors)
        self._cmb_theme_preset.blockSignals(True)
        t_idx = self._cmb_theme_preset.findData(self._theme_preset)
        self._cmb_theme_preset.setCurrentIndex(t_idx if t_idx >= 0 else 0)
        self._cmb_theme_preset.blockSignals(False)
        self._apply_stylesheet()
        self._refresh_color_buttons()

        self.set_custom_sound_display(settings.custom_rare_sound_path)
        self.set_position_status(settings.overlay_pos_x_ratio, settings.overlay_pos_y_ratio)
        self.refresh_favorite_stats_count()

    def refresh_favorite_stats_count(self, count: Optional[int] = None) -> None:
        n = len(self._settings.favorite_weapon_stats) if count is None else max(0, int(count))
        if n > 0:
            self._btn_favorite_stats.setText(I18N.t("panel.fav_stats_btn_count", self._ui_lang, count=n))
        else:
            self._btn_favorite_stats.setText(I18N.t("panel.fav_stats_btn", self._ui_lang))

    def set_custom_sound_display(self, path: str, error: str = "") -> None:
        self._settings.custom_rare_sound_path = str(path or "").strip()
        if error:
            self._lbl_sound_file.setStyleSheet("color: #FCA5A5; font-size: 11px; font-weight: normal;")
            self._lbl_sound_file.setText(I18N.t("panel.audio_invalid_wav", self._ui_lang, err=error))
            return
        self._lbl_sound_file.setStyleSheet("color: #A78BFA; font-size: 11px; font-weight: normal;")
        if self._settings.custom_rare_sound_path:
            name = Path(self._settings.custom_rare_sound_path).name
            self._lbl_sound_file.setText(I18N.t("panel.audio_current_custom", self._ui_lang, name=name))
        else:
            self._lbl_sound_file.setText(I18N.t("panel.audio_current_default", self._ui_lang))

    def set_hotkeys_locked_ui(self, locked: bool) -> None:
        self._hotkeys_locked = bool(locked)
        self._btn_lock_hotkeys.blockSignals(True)
        self._btn_lock_hotkeys.setChecked(self._hotkeys_locked)
        self._btn_lock_hotkeys.blockSignals(False)
        key = "panel.unlock_hotkeys_btn" if self._hotkeys_locked else "panel.lock_hotkeys_btn"
        self._btn_lock_hotkeys.setText(I18N.t(key, self._ui_lang))

    def set_export_feedback(self, message: str, is_error: bool = False) -> None:
        color = "#FCD34D" if is_error else "#6EE7B7"
        self._lbl_export_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: normal;")
        self._lbl_export_status.setText(message)
        self._lbl_export_status.show()

    def set_wfsim_feedback(self, message: str, is_error: bool = False) -> None:
        if self._lbl_wfsim_status is None:
            return
        color = "#FCD34D" if is_error else "#6EE7B7"
        self._lbl_wfsim_status.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: normal;")
        self._lbl_wfsim_status.setText(message)
        self._lbl_wfsim_status.show()

    def set_monitoring_state(self, monitoring: bool) -> None:
        self._monitoring = monitoring
        if not monitoring:
            self._current_mode = MODE_PAUSED
        elif self._current_mode == MODE_PAUSED:
            self._current_mode = MODE_SINGLE_RIVEN
        self._update_header_status()

    def set_work_mode(self, mode: str) -> None:
        if mode in MODE_CYCLE_ORDER:
            self._current_mode = mode
            self._monitoring = mode != MODE_PAUSED
            self._cmb_mode.blockSignals(True)
            idx = self._cmb_mode.findData(mode)
            if idx >= 0:
                self._cmb_mode.setCurrentIndex(idx)
            self._cmb_mode.blockSignals(False)
            self._update_header_status()

    def set_current_weapon(self, weapon_display: str) -> None:
        if weapon_display and not self._inp_weapon.hasFocus():
            self._inp_weapon.setText(weapon_display)

    def set_ocr_model_status(self, message: str, is_error: bool = False) -> None:
        """Display live RapidOCR language pack download / reload status."""
        txt = (message or "").strip()
        if not txt:
            self._lbl_ocr_model_status.clear()
            self._lbl_ocr_model_status.hide()
            return
        color = "#FCA5A5" if is_error else "#38BDF8"
        self._lbl_ocr_model_status.setStyleSheet(f"color: {color}; font-size: 11px; font-weight: normal;")
        self._lbl_ocr_model_status.setText(txt)
        self._lbl_ocr_model_status.show()

    def set_position_status(
        self,
        x_ratio: Optional[float],
        y_ratio: Optional[float],
    ) -> None:
        self._settings.overlay_pos_x_ratio = x_ratio
        self._settings.overlay_pos_y_ratio = y_ratio

    def set_drag_unlocked_ui(self, unlocked: bool) -> None:
        self._btn_drag_unlock.blockSignals(True)
        self._btn_drag_unlock.setChecked(bool(unlocked))
        self._btn_drag_unlock.blockSignals(False)
        self._update_drag_button_text()

    def show_hotkey_report(self, report: HotkeyApplyReport, show_dialog_on_issue: bool = True) -> None:
        lang = self._ui_lang
        if not report.ok:
            msg = "\n".join(report.errors)
            self._lbl_hk_feedback.setStyleSheet("color: #FCA5A5; font-size: 12px; font-weight: normal;")
            self._lbl_hk_feedback.setText(f"⚠ {msg}")
            self._lbl_hk_feedback.show()
            if show_dialog_on_issue:
                QMessageBox.warning(self, I18N.t("panel.hk_conflict_title", lang), msg)
            return

        self._inp_hk_toggle.setText(report.toggle_hotkey)
        self._inp_hk_wm.setText(report.wm_hotkey)
        self._inp_hk_hide.setText(report.hide_hotkey)
        self._inp_hk_open_panel.setText(report.open_panel_hotkey)

        if report.conflicts:
            msg = "\n".join(report.conflicts)
            self._lbl_hk_feedback.setStyleSheet("color: #FCD34D; font-size: 12px; font-weight: normal;")
            self._lbl_hk_feedback.setText(f"⚠ {msg}")
            self._lbl_hk_feedback.show()
            if show_dialog_on_issue:
                QMessageBox.warning(self, I18N.t("panel.hk_conflict_title", lang), msg)
        else:
            self._lbl_hk_feedback.setStyleSheet("color: #6EE7B7; font-size: 12px; font-weight: normal;")
            self._lbl_hk_feedback.setText(
                I18N.t(
                    "panel.hk_saved_ok",
                    lang,
                    toggle=report.toggle_hotkey,
                    wm=report.wm_hotkey,
                    hide=report.hide_hotkey,
                )
            )
            self._lbl_hk_feedback.show()

    def set_wm_loading(self, weapon_display: str) -> None:
        self._wm_busy = True
        self._btn_fetch_wm.setEnabled(False)
        self._lbl_wm_status.setStyleSheet(
            """
            QLabel {
                background-color: #110B1D;
                border: 1px solid #0284C7;
                border-radius: 6px;
                padding: 7px 10px;
                color: #38BDF8;
                font-size: 12px;
                font-weight: normal;
            }
            """
        )
        self._lbl_wm_status.setText(I18N.t("wm_loading_title", self._ui_lang, weapon=weapon_display))
        self._lbl_wm_status.show()

    def set_wm_report(self, report: WMMarketReport, weapon_display: Optional[str] = None) -> None:
        self._wm_busy = False
        self._btn_fetch_wm.setEnabled(True)
        lang = self._ui_lang
        w_label = weapon_display or report.weapon_name
        pos_txt = (
            " · ".join(f"{I18N.stat_name(x.stat_name, lang)} {x.ratio_pct:.0f}%" for x in report.top_positives[:4])
            if report.top_positives
            else I18N.t("wm_no_data", lang)
        )
        neg_txt = (
            " · ".join(f"{I18N.stat_name(x.stat_name, lang)} {x.ratio_pct:.0f}%" for x in report.top_negatives[:2])
            if report.top_negatives
            else I18N.t("wm_no_data", lang)
        )
        price_parts = []
        if report.min_price is not None:
            price_parts.append(I18N.t("wm_price_min", lang, p=report.min_price))
        if report.median_price is not None:
            price_parts.append(I18N.t("wm_price_median", lang, p=report.median_price))
        if report.high_tier_median is not None:
            price_parts.append(I18N.t("wm_price_high", lang, p=report.high_tier_median))
        price_str = " · ".join(price_parts) if price_parts else I18N.t("wm_price_none", lang)
        synced = I18N.t("wm_synced_tag", lang) if report.saved_to_local else ""

        lines = [
            I18N.t(
                "wm_title",
                lang,
                match=report.match_label(lang),
                weapon=w_label,
                samples=report.sample_count,
                online=report.online_count,
                synced=synced,
            ),
            f"{I18N.t('wm_price_prefix', lang)}: {price_str}",
        ]
        if report.ignored_unknown_stats:
            skipped_str = "、".join(I18N.stat_name(s, lang) for s in report.ignored_unknown_stats)
            lines.append(I18N.t("wm_ignored_unknown_stats", lang, stats=skipped_str))
        if report.top_listings:
            lines.append(I18N.t("wm_listings_header", lang, n=len(report.top_listings)))
            for lst in report.top_listings[:5]:
                lines.append(f"  • {lst.format_line(lang)}")
        lines.append(f"{I18N.t('wm_hot_pos', lang)}: {pos_txt} | {I18N.t('wm_hot_neg', lang)}: {neg_txt}")
        if report.search_url:
            self._btn_open_wm_browser.setToolTip(report.search_url)
        self._lbl_wm_status.setStyleSheet(
            """
            QLabel {
                background-color: #0E1729;
                border: 1px solid #38BDF8;
                border-radius: 6px;
                padding: 7px 10px;
                color: #E0F2FE;
                font-size: 12px;
                font-weight: normal;
            }
            """
        )
        self._lbl_wm_status.setText("\n".join(lines))
        self._lbl_wm_status.show()

    def set_wm_error(self, message: str) -> None:
        self._wm_busy = False
        self._btn_fetch_wm.setEnabled(True)
        self._lbl_wm_status.setStyleSheet(
            """
            QLabel {
                background-color: #241812;
                border: 1px solid #F59E0B;
                border-radius: 6px;
                padding: 7px 10px;
                color: #FCD34D;
                font-size: 12px;
                font-weight: normal;
            }
            """
        )
        self._lbl_wm_status.setText(f"{I18N.t('wm_notice_prefix', self._ui_lang)}{message}")
        self._lbl_wm_status.show()

    def set_db_updating(self, updating: bool, status_text: Optional[str] = None, is_error: bool = False) -> None:
        self._db_busy = updating
        self._btn_update_db.setEnabled(not updating)
        if updating:
            self._db_progress.setRange(0, 0)
            self._db_progress.show()
            self._lbl_db_status.setStyleSheet("color: #38BDF8; font-size: 12px; font-weight: normal;")
            self._lbl_db_status.setText(status_text or I18N.t("panel.db_syncing", self._ui_lang))
            self._lbl_db_status.show()
        else:
            self._db_progress.setRange(0, 100)
            self._db_progress.setValue(100)
            self._db_progress.hide()
            if status_text:
                color = "#FCA5A5" if is_error else "#6EE7B7"
                self._lbl_db_status.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: normal;")
                self._lbl_db_status.setText(status_text)
                self._lbl_db_status.show()
            self.refresh_db_timestamp()

    def refresh_db_timestamp(self, data_dir: Optional[Path] = None) -> None:
        ts, count = read_local_db_metadata(data_dir)
        lang = self._ui_lang
        if ts:
            self._lbl_db_info.setText(I18N.t("panel.db_last_updated", lang, time=ts, count=count))
        else:
            self._lbl_db_info.setText(I18N.t("panel.db_never_updated", lang))

    def retranslate_ui(self, lang: str) -> None:
        if lang not in SUPPORTED_LANGS:
            lang = "en_US"
        self._ui_lang = lang
        self.setWindowTitle(I18N.t("panel.window_title", lang))
        self._header_title.setText(I18N.t("panel.header_title", lang))
        self._update_header_status()

        self._grp_hotkeys.setTitle(I18N.t("panel.sec_hotkeys", lang))
        self._lbl_hk_toggle.setText(I18N.t("panel.hk_toggle_label", lang))
        self._lbl_hk_wm.setText(I18N.t("panel.hk_wm_label", lang))
        self._lbl_hk_hide.setText(I18N.t("panel.hk_hide_label", lang))
        self._lbl_hk_open_panel.setText(I18N.t("panel.hk_open_panel_label", lang))
        self._lbl_hk_export.setText(I18N.t("panel.hk_export_label", lang))
        self._lbl_hk_lock.setText(I18N.t("panel.hk_lock_label", lang))
        self._btn_apply_hk.setText(I18N.t("panel.hk_save_btn", lang))
        self._btn_reset_hk.setText(I18N.t("panel.hk_reset_btn", lang))

        self._grp_data.setTitle(I18N.t("panel.sec_data", lang))
        self._inp_weapon.setPlaceholderText(I18N.t("panel.wm_input_placeholder", lang))
        self._btn_fetch_wm.setText(I18N.t("panel.wm_fetch_btn", lang))
        self._btn_open_wm_browser.setText(I18N.t("panel.wm_open_browser_btn", lang))
        self.refresh_favorite_stats_count()
        self._btn_build_optimizer.setText(I18N.t("panel.opt_open_btn", lang))
        self._btn_open_wfsim.setText(I18N.t("panel.wfsim_open_btn", lang))
        self._btn_open_wfsim.setToolTip(I18N.t("panel.wfsim_open_tooltip", lang))
        self._btn_update_db.setText(I18N.t("panel.db_update_btn", lang))
        self.refresh_db_timestamp()

        self._grp_prefs.setTitle(I18N.t("panel.sec_prefs", lang))
        self._btn_calibrate_roi.setText(I18N.t("panel.calibrate_roi_btn", lang))
        self._update_drag_button_text()
        self._btn_reset_pos.setText(I18N.t("panel.reset_pos_btn", lang))
        self._lbl_font_size.setText(I18N.t("panel.font_size_label", lang))

        self._lbl_lang.setText(I18N.t("panel.lang_label", lang))
        self._cmb_lang.blockSignals(True)
        self._cmb_lang.setItemText(0, I18N.t("tray.lang_auto", lang))
        for i in range(1, self._cmb_lang.count()):
            code = self._cmb_lang.itemData(i)
            if isinstance(code, str):
                self._cmb_lang.setItemText(i, LANGUAGE_DISPLAY_NAMES.get(code, code))
        self._cmb_lang.blockSignals(False)

        self._chk_start_monitor.setText(I18N.t("panel.start_monitor_chk", lang))
        self._lbl_theme.setText(I18N.t("panel.theme_label", lang))
        self._btn_reset_theme.setText(I18N.t("panel.theme_reset_btn", lang))
        self._cmb_theme_preset.blockSignals(True)
        for i in range(self._cmb_theme_preset.count()):
            pkey = self._cmb_theme_preset.itemData(i)
            if isinstance(pkey, str):
                self._cmb_theme_preset.setItemText(i, I18N.t(f"panel.theme_preset_{pkey}", lang))
        self._cmb_theme_preset.blockSignals(False)
        self._refresh_color_buttons()
        self._btn_export_card.setText(I18N.t("panel.export_card_btn", lang))
        self._btn_user_manual.setText(I18N.t("panel.user_manual_btn", lang))
        self.set_hotkeys_locked_ui(self._hotkeys_locked)
        self._chk_auto_hide_focus.setText(I18N.t("panel.auto_hide_focus_chk", lang))
        self._chk_audio_cue.setText(I18N.t("panel.audio_cue_chk", lang))
        self._btn_audio_test.setText(I18N.t("panel.audio_test_btn", lang))
        self._btn_replace_sound.setText(I18N.t("panel.audio_replace_btn", lang))
        self._btn_reset_sound.setText(I18N.t("panel.audio_reset_btn", lang))
        self.set_custom_sound_display(self._settings.custom_rare_sound_path)

        # Clear transient feedback banners when switching languages so stale old-language text never lingers
        if not self._db_busy:
            self._lbl_db_status.clear()
            self._lbl_db_status.hide()
        self._lbl_hk_feedback.clear()
        self._lbl_hk_feedback.hide()
        self._lbl_export_status.clear()
        self._lbl_export_status.hide()

    def _update_drag_button_text(self) -> None:
        key = "panel.drag_lock_btn" if self._btn_drag_unlock.isChecked() else "panel.drag_unlock_btn"
        self._btn_drag_unlock.setText(I18N.t(key, self._ui_lang))

    def _update_header_status(self) -> None:
        lang = self._ui_lang
        f9_key = self._settings.hotkey_fetch_wm or "F9"
        self._cmb_mode.blockSignals(True)
        for i, m in enumerate(MODE_CYCLE_ORDER):
            self._cmb_mode.setItemText(i, format_mode_badge(m, lang, f9_key=f9_key))
        idx = self._cmb_mode.findData(self._current_mode)
        if idx >= 0:
            self._cmb_mode.setCurrentIndex(idx)
        self._cmb_mode.blockSignals(False)

        mode_txt = format_mode_badge(self._current_mode, lang, f9_key=f9_key)
        if self._current_mode != MODE_PAUSED:
            self._header_status.setText(I18N.t("panel.status_on", lang, mode=mode_txt))
            self._header_status.setStyleSheet("color: #6EE7B7; font-size: 12px; background: transparent;")
            self._btn_quick_toggle.setText(I18N.t("panel.btn_pause_monitor", lang))
        else:
            self._header_status.setText(I18N.t("panel.status_off", lang, mode=mode_txt))
            self._header_status.setStyleSheet("color: #C4B5FD; font-size: 12px; background: transparent;")
            self._btn_quick_toggle.setText(I18N.t("panel.btn_start_monitor", lang))

    def _apply_initial_geometry(self, saved_geom: Optional[list[int]]) -> None:
        """Restore saved `(x, y, w, h)` safely on an available screen, or default to 600x800."""
        w = DEFAULT_PANEL_WIDTH
        h = DEFAULT_PANEL_HEIGHT
        target_pos: Optional[tuple[int, int]] = None

        if isinstance(saved_geom, (list, tuple)) and len(saved_geom) == 4:
            sx, sy, sw, sh = (int(v) for v in saved_geom)
            w = max(MIN_PANEL_WIDTH, sw)
            h = max(MIN_PANEL_HEIGHT, sh)
            screens = QGuiApplication.screens()
            if screens:
                # Ensure window top-left sits inside at least one connected screen's available area
                matched_geo = None
                for sc in screens:
                    ag = sc.availableGeometry()
                    if ag.contains(sx + 40, sy + 20):
                        matched_geo = ag
                        break
                if matched_geo is None:
                    matched_geo = screens[0].availableGeometry()
                w = min(w, max(MIN_PANEL_WIDTH, matched_geo.width()))
                h = min(h, max(MIN_PANEL_HEIGHT, matched_geo.height()))
                cx = max(matched_geo.left(), min(sx, matched_geo.right() - w + 1))
                cy = max(matched_geo.top(), min(sy, matched_geo.bottom() - h + 1))
                target_pos = (cx, cy)
            else:
                target_pos = (sx, sy)

        self.resize(w, h)
        if target_pos is not None:
            self.move(target_pos[0], target_pos[1])
            self._last_saved_geometry = (target_pos[0], target_pos[1], w, h)

    def _emit_current_geometry(self) -> None:
        if not self._geometry_tracking_ready:
            return
        g = (int(self.x()), int(self.y()), max(MIN_PANEL_WIDTH, int(self.width())), max(MIN_PANEL_HEIGHT, int(self.height())))
        if g == self._last_saved_geometry:
            return
        self._last_saved_geometry = g
        self._settings.panel_geometry = list(g)
        self.panel_geometry_changed.emit(g[0], g[1], g[2], g[3])

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._geometry_tracking_ready = True

    def moveEvent(self, event: QMoveEvent) -> None:  # noqa: N802
        super().moveEvent(event)
        if self._geometry_tracking_ready and self.isVisible():
            self._geo_save_timer.start()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._geometry_tracking_ready and self.isVisible():
            self._geo_save_timer.start()

    def show_and_activate(self) -> None:
        self.refresh_db_timestamp()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Hide to system tray, save current window size/position, and re-lock overlay click-through."""
        if self._geo_save_timer.isActive():
            self._geo_save_timer.stop()
        if self._geometry_tracking_ready:
            self._emit_current_geometry()
        if self._btn_drag_unlock.isChecked():
            self._btn_drag_unlock.setChecked(False)
        self.panel_closed.emit()
        event.ignore()
        self.hide()

    # ---- Internal button & control slots ----

    def _on_apply_hotkeys_clicked(self) -> None:
        t = self._inp_hk_toggle.text().strip()
        w = self._inp_hk_wm.text().strip()
        h = self._inp_hk_hide.text().strip()
        op = self._inp_hk_open_panel.text().strip() or DEFAULT_OPEN_PANEL_HOTKEY
        self.hotkeys_apply_requested.emit(t, w, h)
        self.hotkeys_all_apply_requested.emit(t, w, h, op)

    def _on_reset_hotkeys_clicked(self) -> None:
        self._inp_hk_toggle.setText("F8")
        self._inp_hk_wm.setText("F9")
        self._inp_hk_hide.setText("ESC")
        self._inp_hk_open_panel.setText(DEFAULT_OPEN_PANEL_HOTKEY)
        self.hotkeys_apply_requested.emit("F8", "F9", "ESC")
        self.hotkeys_all_apply_requested.emit("F8", "F9", "ESC", DEFAULT_OPEN_PANEL_HOTKEY)

    def _on_fetch_wm_clicked(self) -> None:
        if self._wm_busy:
            return
        self.wm_fetch_requested.emit(self._inp_weapon.text().strip())

    def _on_open_wm_browser_clicked(self) -> None:
        self.open_wm_browser_requested.emit(self._inp_weapon.text().strip())

    def _on_drag_unlock_toggled(self, checked: bool) -> None:
        self._update_drag_button_text()
        self.drag_unlock_changed.emit(bool(checked))

    def _on_reset_pos_clicked(self) -> None:
        self.reset_position_requested.emit()

    def _on_font_slider_changed(self, value: int) -> None:
        if self._spin_font.value() != value:
            self._spin_font.blockSignals(True)
            self._spin_font.setValue(value)
            self._spin_font.blockSignals(False)
        self.font_size_changed.emit(int(value))

    def _on_font_spin_changed(self, value: int) -> None:
        if self._slider_font.value() != value:
            self._slider_font.blockSignals(True)
            self._slider_font.setValue(value)
            self._slider_font.blockSignals(False)
        self.font_size_changed.emit(int(value))

    def _on_mode_combo_changed(self, idx: int) -> None:
        mode = self._cmb_mode.itemData(idx)
        if isinstance(mode, str) and mode != self._current_mode:
            self.mode_selected.emit(mode)

    def _on_lang_combo_changed(self, idx: int) -> None:
        mode = self._cmb_lang.itemData(idx)
        if isinstance(mode, str):
            if mode in SUPPORTED_LANGS and mode != self._ui_lang:
                self.retranslate_ui(mode)
            self.language_mode_changed.emit(mode)
            self.language_changed.emit(mode)

    def _on_start_monitor_toggled(self, checked: bool) -> None:
        self.start_monitoring_changed.emit(bool(checked))

    def _on_auto_hide_focus_toggled(self, checked: bool) -> None:
        self.auto_hide_focus_changed.emit(bool(checked))

    def _on_audio_cue_toggled(self, checked: bool) -> None:
        self.audio_cue_changed.emit(bool(checked))

    def _on_replace_sound_clicked(self) -> None:
        start_dir = ""
        if self._settings.custom_rare_sound_path:
            p = Path(self._settings.custom_rare_sound_path)
            if p.parent.is_dir():
                start_dir = str(p.parent)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            I18N.t("panel.audio_dialog_title", self._ui_lang),
            start_dir,
            "WAV Audio (*.wav);;All Files (*.*)",
        )
        if not file_path:
            return
        ok, info = validate_wav_file(file_path)
        if not ok:
            self.set_custom_sound_display(self._settings.custom_rare_sound_path, error=info)
            return
        self.set_custom_sound_display(file_path)
        self.custom_rare_sound_changed.emit(file_path)

    def _on_reset_sound_clicked(self) -> None:
        self.set_custom_sound_display("")
        self.custom_rare_sound_changed.emit("")

    @staticmethod
    def _make_swatch_icon(hex_color: str, size: int = 12) -> QIcon:
        pm = QPixmap(size, size)
        pm.fill(QColor(hex_color))
        return QIcon(pm)

    @staticmethod
    def _make_preset_icon(colors: dict[str, str]) -> QIcon:
        pm = QPixmap(16, 12)
        pm.fill(QColor(colors.get("bg", "#120C20")))
        from PyQt6.QtGui import QPainter
        painter = QPainter(pm)
        painter.fillRect(0, 0, 8, 12, QColor(colors.get("border", "#A876FF")))
        painter.fillRect(8, 0, 8, 12, QColor(colors.get("accent", "#FFD166")))
        painter.end()
        return QIcon(pm)

    def _refresh_color_buttons(self) -> None:
        lang = self._ui_lang
        for ckey, btn in self._color_btns.items():
            hx = self._theme_colors.get(ckey, "#FFFFFF")
            lbl = I18N.t(f"panel.theme_color_{ckey}", lang)
            btn.setText(lbl)
            btn.setIcon(self._make_swatch_icon(hx, 12))
            btn.setToolTip(f"{lbl}: {hx}")

    def _on_theme_preset_changed(self, idx: int) -> None:
        pkey = self._cmb_theme_preset.itemData(idx)
        if not isinstance(pkey, str):
            return
        self._theme_preset = pkey
        if pkey in THEME_PRESETS:
            self._theme_colors = dict(THEME_PRESETS[pkey])
        self._settings.theme_preset = self._theme_preset
        self._settings.theme_colors = dict(self._theme_colors)
        self._apply_stylesheet()
        self._refresh_color_buttons()
        self.theme_changed.emit(self._theme_preset, dict(self._theme_colors))

    def set_custom_color(self, color_key: str, hex_color: str) -> None:
        """Apply a custom hex color for `bg`/`border`/`accent`/`text`, switch to custom, and emit."""
        if color_key not in THEME_COLOR_KEYS:
            return
        norm = normalize_hex_color(hex_color, self._theme_colors[color_key])
        self._theme_colors[color_key] = norm
        self._theme_preset = "custom"
        self._settings.theme_preset = "custom"
        self._settings.theme_colors = dict(self._theme_colors)
        self._cmb_theme_preset.blockSignals(True)
        c_idx = self._cmb_theme_preset.findData("custom")
        if c_idx >= 0:
            self._cmb_theme_preset.setCurrentIndex(c_idx)
        self._cmb_theme_preset.blockSignals(False)
        self._apply_stylesheet()
        self._refresh_color_buttons()
        self.theme_changed.emit("custom", dict(self._theme_colors))

    def _on_pick_color_clicked(self, color_key: str) -> None:
        current = QColor(self._theme_colors.get(color_key, "#A876FF"))
        picked = QColorDialog.getColor(
            current,
            self,
            I18N.t("panel.theme_pick_title", self._ui_lang),
        )
        if picked.isValid():
            self.set_custom_color(color_key, picked.name())

    def _on_reset_theme_clicked(self) -> None:
        idx = self._cmb_theme_preset.findData(DEFAULT_THEME_PRESET)
        if idx >= 0:
            if self._cmb_theme_preset.currentIndex() == idx:
                self._on_theme_preset_changed(idx)
            else:
                self._cmb_theme_preset.setCurrentIndex(idx)
