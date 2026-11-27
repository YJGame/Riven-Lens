"""System tray icon and right-click context menu manager.

Provides:
  - `get_assets_dir()` & `load_app_icon()` supporting both source tree and PyInstaller frozen bundle.
  - `TrayManager`: persistent `QSystemTrayIcon` showing the active 5-state work mode,
    direct jump submenu for all 5 modes, double-click to open the control panel,
    and the sole `退出软件` exit entry (no `F10` exit hotkey).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Dict, Optional

from PyQt6.QtCore import QObject, QPointF, Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from src.i18n import I18N
from src.roi_manager import (
    MODE_CYCLE_ORDER,
    MODE_PAUSED,
    MODE_SINGLE_RIVEN,
    format_mode_badge,
    format_mode_short,
)


def get_assets_dir() -> Path:
    """Resolve `assets/` directory cleanly in both source mode and PyInstaller frozen mode."""
    if getattr(sys, "frozen", False):
        exe_assets = Path(sys.executable).resolve().parent / "assets"
        if exe_assets.is_dir():
            return exe_assets
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "assets"
            if bundled.is_dir():
                return bundled
        return exe_assets
    return Path(__file__).resolve().parent.parent / "assets"


def create_fallback_diamond_icon() -> QIcon:
    """Fallback crisp purple Warframe Riven diamond icon if `assets/icon.png` is missing."""
    size = 64
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    outer = QPolygonF([QPointF(32, 4), QPointF(60, 32), QPointF(32, 60), QPointF(4, 32)])
    painter.setBrush(QColor(34, 18, 62, 245))
    painter.setPen(QPen(QColor(186, 128, 255), 4))
    painter.drawPolygon(outer)

    inner = QPolygonF([QPointF(32, 16), QPointF(48, 32), QPointF(32, 48), QPointF(16, 32)])
    painter.setBrush(QColor(168, 85, 247, 230))
    painter.setPen(QPen(QColor(255, 209, 102), 2))
    painter.drawPolygon(inner)
    painter.end()
    return QIcon(pm)


def load_app_icon() -> QIcon:
    """Load `assets/icon.png` (or `assets/icon.ico`), falling back to programmatic diamond icon."""
    assets_dir = get_assets_dir()
    for fname in ("icon.png", "icon.ico"):
        candidate = assets_dir / fname
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    return create_fallback_diamond_icon()


class TrayManager(QObject):
    """Persistent Windows system tray icon & right-click control menu."""

    def __init__(
        self,
        on_toggle_monitor: Callable[[], None],
        on_open_panel: Callable[[], None],
        on_fetch_wm: Callable[[], None],
        on_update_db: Callable[[], None],
        on_quit: Callable[[], None],
        on_select_mode: Optional[Callable[[str], None]] = None,
        on_toggle_lock: Optional[Callable[[], None]] = None,
        on_open_optimizer: Optional[Callable[[], None]] = None,
        initial_ui_lang: str = "zh_CN",
        toggle_hotkey: str = "F8",
        wm_hotkey: str = "F9",
        initial_mode: str = MODE_PAUSED,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._on_open_panel = on_open_panel
        self._ui_lang = initial_ui_lang
        self._monitoring = initial_mode != MODE_PAUSED
        self._current_mode = initial_mode
        self._toggle_hotkey = toggle_hotkey
        self._wm_hotkey = wm_hotkey
        self._db_updating = False
        self._hotkeys_locked = False

        self._icon = load_app_icon()
        self._tray = QSystemTrayIcon(self._icon, self)
        self._menu = QMenu()

        # 1. Cycle to Next Mode (F8)
        self._act_toggle = QAction("", self._menu)
        self._act_toggle.triggered.connect(on_toggle_monitor)
        self._menu.addAction(self._act_toggle)

        # 2. Direct jump submenu for all 5 work modes
        self._mode_menu = QMenu("", self._menu)
        self._mode_group = QActionGroup(self._mode_menu)
        self._mode_group.setExclusive(True)
        self._mode_actions: Dict[str, QAction] = {}
        for m in MODE_CYCLE_ORDER:
            act = QAction("", self._mode_menu, checkable=True)
            self._mode_group.addAction(act)
            self._mode_menu.addAction(act)
            if on_select_mode is not None:
                act.triggered.connect(lambda _chk=False, mode_val=m: on_select_mode(mode_val))
            self._mode_actions[m] = act
        self._menu.addMenu(self._mode_menu)

        # 3. Open Settings & Control Panel
        self._act_panel = QAction("", self._menu)
        self._act_panel.triggered.connect(on_open_panel)
        self._menu.addAction(self._act_panel)

        # 4. Lock / Unlock Anti-Mispress Hotkeys (Ctrl+Alt+L)
        self._act_lock = QAction("", self._menu, checkable=True)
        if on_toggle_lock is not None:
            self._act_lock.triggered.connect(lambda _chk=False: on_toggle_lock())
        self._menu.addAction(self._act_lock)

        # 5. Trigger F9 Action (WM Price / Capture)
        self._act_wm = QAction("", self._menu)
        self._act_wm.triggered.connect(on_fetch_wm)
        self._menu.addAction(self._act_wm)

        # Optional WFSim edition optimizer action (WFSim sending is strictly via panel)
        self._act_optimizer: Optional[QAction] = None
        if on_open_optimizer is not None:
            self._act_optimizer = QAction("", self._menu)
            self._act_optimizer.triggered.connect(lambda _chk=False: on_open_optimizer())
            self._menu.addAction(self._act_optimizer)

        # 5. Update Weapons & Riven Database
        self._act_update_db = QAction("", self._menu)
        self._act_update_db.triggered.connect(on_update_db)
        self._menu.addAction(self._act_update_db)

        # 6. Separator
        self._menu.addSeparator()

        # 7. Exit Software (sole exit entry)
        self._act_quit = QAction("", self._menu)
        self._act_quit.triggered.connect(on_quit)
        self._menu.addAction(self._act_quit)

        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)

        self.refresh_state(
            ui_lang=initial_ui_lang,
            monitoring=(initial_mode != MODE_PAUSED),
            toggle_hotkey=toggle_hotkey,
            wm_hotkey=wm_hotkey,
            current_mode=initial_mode,
        )
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    @property
    def menu(self) -> QMenu:
        return self._menu

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_open_panel()

    def set_db_updating(self, updating: bool) -> None:
        self._db_updating = updating
        self._act_update_db.setEnabled(not updating)
        self.refresh_state(
            ui_lang=self._ui_lang,
            monitoring=self._monitoring,
            toggle_hotkey=self._toggle_hotkey,
            wm_hotkey=self._wm_hotkey,
            current_mode=self._current_mode,
        )

    def refresh_state(
        self,
        ui_lang: str,
        monitoring: bool,
        toggle_hotkey: Optional[str] = None,
        wm_hotkey: Optional[str] = None,
        current_mode: Optional[str] = None,
        hotkeys_locked: Optional[bool] = None,
    ) -> None:
        self._ui_lang = ui_lang
        self._monitoring = monitoring
        if hotkeys_locked is not None:
            self._hotkeys_locked = bool(hotkeys_locked)
        if current_mode is not None:
            self._current_mode = current_mode
        elif not monitoring:
            self._current_mode = MODE_PAUSED
        elif self._current_mode == MODE_PAUSED:
            self._current_mode = MODE_SINGLE_RIVEN

        if toggle_hotkey:
            self._toggle_hotkey = toggle_hotkey
        if wm_hotkey:
            self._wm_hotkey = wm_hotkey

        mode_short = format_mode_short(self._current_mode, ui_lang)
        mode_badge = format_mode_badge(self._current_mode, ui_lang, f9_key=self._wm_hotkey)
        self._tray.setToolTip(
            I18N.t("tray.tooltip_fmt", ui_lang, status=mode_badge, hotkey=self._toggle_hotkey)
        )

        self._act_toggle.setText(
            I18N.t(
                "tray.toggle_pause" if monitoring else "tray.toggle_start",
                ui_lang,
                mode=mode_short,
                hotkey=self._toggle_hotkey,
            )
        )
        self._mode_menu.setTitle(I18N.t("tray.mode_menu", ui_lang))
        for m, act in self._mode_actions.items():
            act.setText(format_mode_badge(m, ui_lang, f9_key=self._wm_hotkey))
            act.setChecked(m == self._current_mode)

        self._act_panel.setText(I18N.t("tray.open_panel", ui_lang))
        lock_key = "tray.unlock_hotkeys" if self._hotkeys_locked else "tray.lock_hotkeys"
        self._act_lock.setText(I18N.t(lock_key, ui_lang))
        self._act_lock.setChecked(self._hotkeys_locked)
        self._act_wm.setText(I18N.t("tray.fetch_wm", ui_lang, hotkey=self._wm_hotkey))
        if self._act_optimizer is not None:
            self._act_optimizer.setText(I18N.t("tray.open_build_optimizer", ui_lang))
        db_key = "tray.updating_db" if self._db_updating else "tray.update_db"
        self._act_update_db.setText(I18N.t(db_key, ui_lang))
        self._act_quit.setText(I18N.t("tray.quit", ui_lang))

    def show_message(self, title: str, message: str, duration_ms: int = 2500) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable() and self._tray.isVisible():
            self._tray.showMessage(title, message, self._icon, duration_ms)

    def hide(self) -> None:
        self._tray.hide()
