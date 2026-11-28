"""Independent draggable, click-through PyQt6 companion overlay windows.

Provides reusable `DraggableOverlayBase` and three dedicated independent HUD windows
that each maintain their own saved relative screen position in `data/user_settings.json`:
  1. `WMPriceOverlayWindow` (`window_key="wm"`): F9 Warframe.market exact-stat price & top-5 listings.
  2. `RerollSynergyPopupWindow` (`window_key="synergy"`): High-potential reroll (`3+1-` / `2+1-` + S-grade)
     & compound-element synergy popup.
  3. `RelicRewardsOverlayWindow` (`window_key="relic"`): Void Relic 1~4 independent row-cards pricing.
"""

from __future__ import annotations

import ctypes
import html
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QMouseEvent, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from src.evaluator import RerollSynergyAlert
from src.i18n import I18N, SUPPORTED_LANGS
from src.settings_manager import (
    DEFAULT_OVERLAY_FONT_PX,
    DEFAULT_THEME_PRESET,
    THEME_PRESETS,
    clamp_overlay_font_size,
    hex_to_rgba,
    resolve_theme_colors,
)
from src.wm_client import WMMarketReport


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


_CURRENCY_PIXMAP_CACHE: Dict[Tuple[str, int], QPixmap] = {}


def get_currency_icon_path(kind: str) -> Path:
    """Resolve `assets/icons/platinum.png` or `assets/icons/ducats.png` (supports PyInstaller `_MEIPASS`)."""
    fname = "platinum.png" if kind == "platinum" else "ducats.png"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets" / "icons" / fname
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "assets" / "icons" / fname


def get_relic_rank_icon_path(rank: int) -> Path:
    """Resolve `assets/icons/relic_rank1_gold.png` or `relic_rank2_silver.png` (supports PyInstaller `_MEIPASS`)."""
    fname = "relic_rank1_gold.png" if int(rank) == 1 else "relic_rank2_silver.png"
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets" / "icons" / fname
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "assets" / "icons" / fname


def load_relic_rank_pixmap(rank: int, size_px: int = 18) -> QPixmap:
    """Load and smoothly scale the Gold (rank 1) or Silver (rank 2) recommendation icon (cached)."""
    r = 1 if int(rank) == 1 else 2
    sz = max(14, int(size_px))
    key = (f"relic_rank_{r}", sz)
    cached = _CURRENCY_PIXMAP_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    p = get_relic_rank_icon_path(r)
    pm = QPixmap(str(p)) if p.is_file() else QPixmap()
    if not pm.isNull():
        pm = pm.scaled(
            sz,
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        _CURRENCY_PIXMAP_CACHE[key] = pm
    return pm


def make_relic_rank_icon_label(rank: int, size_px: int = 18) -> QLabel:
    """Create a fixed QLabel displaying the Gold (rank 1) or Silver (rank 2) recommendation emblem."""
    r = 1 if int(rank) == 1 else 2
    sz = max(14, int(size_px))
    lbl = QLabel()
    lbl.setObjectName("RelicRankStar")
    lbl.setFixedSize(sz, sz)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("background: transparent; border: none;")
    pm = load_relic_rank_pixmap(r, sz)
    if not pm.isNull():
        lbl.setPixmap(pm)
    else:
        lbl.setText("★")
        color = "#F5C542" if r == 1 else "#C0C8D4"
        lbl.setStyleSheet(f"color: {color}; background: transparent; border: none;")
    return lbl


def get_vaulted_chest_icon_path() -> Path:
    """Resolve `assets/vaulted_chest.png` (supports PyInstaller `_MEIPASS`)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        cand = Path(meipass) / "assets" / "vaulted_chest.png"
        if cand.is_file():
            return cand
    return Path(__file__).resolve().parent.parent / "assets" / "vaulted_chest.png"


def load_vaulted_chest_pixmap(size_px: int = 18) -> QPixmap:
    """Load and smoothly scale `assets/vaulted_chest.png` (default 18x18px, cached)."""
    sz = max(12, int(size_px))
    key = ("vaulted_chest", sz)
    cached = _CURRENCY_PIXMAP_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    p = get_vaulted_chest_icon_path()
    pm = QPixmap(str(p)) if p.is_file() else QPixmap()
    if not pm.isNull():
        pm = pm.scaled(
            sz,
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        _CURRENCY_PIXMAP_CACHE[key] = pm
    return pm


def make_vaulted_chest_label(size_px: int = 18) -> QLabel:
    """Create a fixed 18x18 QLabel displaying the vaulted chest icon."""
    sz = max(12, int(size_px))
    lbl = QLabel()
    lbl.setObjectName("VaultedChestIcon")
    lbl.setFixedSize(sz, sz)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("background: transparent; border: none;")
    pm = load_vaulted_chest_pixmap(sz)
    if not pm.isNull():
        lbl.setPixmap(pm)
    return lbl


def load_currency_pixmap(kind: str, size_px: int) -> QPixmap:
    """Load and smoothly scale the Platinum or Ducats icon pixmap (cached in memory)."""
    sz = max(12, int(size_px))
    key = (kind, sz)
    cached = _CURRENCY_PIXMAP_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached
    p = get_currency_icon_path(kind)
    pm = QPixmap(str(p)) if p.is_file() else QPixmap()
    if not pm.isNull():
        pm = pm.scaled(
            sz,
            sz,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        _CURRENCY_PIXMAP_CACHE[key] = pm
    return pm


def make_currency_icon_label(kind: str, size_px: int) -> QLabel:
    """Create a crisp fixed-size `QLabel` displaying the Platinum or Ducats icon."""
    sz = max(12, int(size_px))
    lbl = QLabel()
    lbl.setFixedSize(sz, sz)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet("background: transparent; border: none;")
    pm = load_currency_pixmap(kind, sz)
    if not pm.isNull():
        lbl.setPixmap(pm)
    return lbl


SNAP_THRESHOLD_PX = 16
SNAP_SCREEN_MARGIN_PX = 8
SNAP_SIBLING_GAP_PX = 6


def snap_window_position(
    x: int,
    y: int,
    width: int,
    height: int,
    screen_geo: QRect,
    sibling_rects: Sequence[QRect] = (),
    *,
    threshold: int = SNAP_THRESHOLD_PX,
    screen_margin: int = SNAP_SCREEN_MARGIN_PX,
    sibling_gap: int = SNAP_SIBLING_GAP_PX,
) -> Tuple[int, int]:
    """Magnetic edge snapping against screen boundaries, screen center, and sibling overlays."""
    best_x = int(x)
    best_dx = threshold + 1

    # Screen X snap targets: left edge, right edge, horizontal center
    x_targets: List[int] = [
        screen_geo.left() + screen_margin,
        screen_geo.right() - width + 1 - screen_margin,
        screen_geo.left() + (screen_geo.width() - width) // 2,
    ]
    # Sibling X snap targets: align left/right edges, or dock cleanly left/right of sibling
    for sib in sibling_rects:
        x_targets.extend(
            [
                sib.left(),
                sib.right() - width + 1,
                sib.left() - width - sibling_gap,
                sib.right() + 1 + sibling_gap,
            ]
        )

    for tx in x_targets:
        dx = abs(int(x) - tx)
        if dx <= threshold and dx < best_dx:
            best_dx = dx
            best_x = tx

    best_y = int(y)
    best_dy = threshold + 1

    # Screen Y snap targets: top edge, bottom edge
    y_targets: List[int] = [
        screen_geo.top() + screen_margin,
        screen_geo.bottom() - height + 1 - screen_margin,
    ]
    # Sibling Y snap targets: align top/bottom edges, or dock cleanly above/below sibling
    for sib in sibling_rects:
        y_targets.extend(
            [
                sib.top(),
                sib.bottom() - height + 1,
                sib.top() - height - sibling_gap,
                sib.bottom() + 1 + sibling_gap,
            ]
        )

    for ty in y_targets:
        dy = abs(int(y) - ty)
        if dy <= threshold and dy < best_dy:
            best_dy = dy
            best_y = ty

    # Final clamp inside available screen geometry
    max_x = max(screen_geo.left(), screen_geo.right() - width + 1)
    max_y = max(screen_geo.top(), screen_geo.bottom() - height + 1)
    return (
        max(screen_geo.left(), min(best_x, max_x)),
        max(screen_geo.top(), min(best_y, max_y)),
    )


class DraggableOverlayBase(QWidget):
    """Shared base for click-through translucent overlays with independent drag & position memory."""

    position_changed = pyqtSignal(float, float)             # (rx, ry)
    window_position_changed = pyqtSignal(str, float, float) # (window_key, rx, ry)

    def __init__(
        self,
        window_key: str,
        *,
        click_through: bool = True,
        lang: str = "zh_CN",
        font_size_px: int = DEFAULT_OVERLAY_FONT_PX,
        pos_x_ratio: Optional[float] = None,
        pos_y_ratio: Optional[float] = None,
        default_anchor: Tuple[float, float] = (0.04, 0.08),
        drag_banner_key: str = "drag_banner",
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self._window_key = window_key
        self._click_through = click_through
        self._drag_unlocked = False
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._lang = lang
        self._font_size_px = clamp_overlay_font_size(font_size_px)
        self._pos_x_ratio = pos_x_ratio
        self._pos_y_ratio = pos_y_ratio
        self._default_anchor = default_anchor
        self._drag_banner_key = drag_banner_key
        self._theme_colors = resolve_theme_colors(DEFAULT_THEME_PRESET, theme_colors)
        self._snap_siblings_provider: Optional[Callable[[], Sequence[QRect]]] = None

        self._init_window_flags()
        title_map = {
            "wm": "RivenLensWM",
            "synergy": "RivenLensSynergy",
            "relic": "RivenLensRelic",
        }
        self.setWindowTitle(title_map.get(window_key, f"RivenLens_{window_key}"))
        self._build_base_shell()

    def set_snap_siblings_provider(self, provider: Callable[[], Sequence[QRect]]) -> None:
        self._snap_siblings_provider = provider

    @property
    def window_key(self) -> str:
        return self._window_key

    @property
    def is_drag_unlocked(self) -> bool:
        return self._drag_unlocked

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        self._theme_colors = resolve_theme_colors("custom", colors)

    @property
    def saved_position_ratio(self) -> Tuple[Optional[float], Optional[float]]:
        return self._pos_x_ratio, self._pos_y_ratio

    def _init_window_flags(self) -> None:
        self._apply_interaction_flags(click_through=self._click_through)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def _apply_interaction_flags(self, click_through: bool) -> None:
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
        if sys.platform != "darwin":
            return
        try:
            from src.macos_support import apply_macos_overlay_window_style

            apply_macos_overlay_window_style(int(self.winId()), click_through=click_through)
        except Exception:
            pass

    def _apply_win32_exstyle(self, click_through: bool) -> None:
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

    def _build_base_shell(self) -> None:
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._outer.setSpacing(0)

        self._shell_frame = QFrame()
        self._shell_frame.setObjectName("CompanionShell")
        self._shell_frame.setStyleSheet(
            "QFrame#CompanionShell { background: transparent; border: none; }"
        )
        self._outer.addWidget(self._shell_frame)

        self._shell_layout = QVBoxLayout(self._shell_frame)
        self._shell_layout.setContentsMargins(0, 0, 0, 0)
        self._shell_layout.setSpacing(5)

        self._drag_banner = QLabel(I18N.t(self._drag_banner_key, self._lang))
        self._drag_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drag_banner.setWordWrap(True)
        self._drag_banner.setStyleSheet(
            """
            QLabel {
                color: #1A1228;
                background-color: #FFD166;
                border-radius: 7px;
                padding: 4px 10px;
            }
            """
        )
        self._drag_banner.hide()
        self._shell_layout.addWidget(self._drag_banner)

    def set_drag_unlocked(self, unlocked: bool) -> None:
        target = bool(unlocked)
        was_visible = self.isVisible()
        self._drag_unlocked = target
        self._dragging = False
        accent = self._theme_colors["accent"]
        if target:
            self._drag_banner.setText(I18N.t(self._drag_banner_key, self._lang))
            self._drag_banner.setFont(
                _make_px_font(_ui_font_family(self._lang), max(9, self._font_size_px - 1), QFont.Weight.Bold)
            )
            self._drag_banner.show()
            self._shell_frame.setStyleSheet(
                f"""
                QFrame#CompanionShell {{
                    background-color: {hex_to_rgba(self._theme_colors['bg'], 140)};
                    border: 2px dashed {accent};
                    border-radius: 14px;
                }}
                """
            )
            self._shell_layout.setContentsMargins(7, 7, 7, 7)
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self._drag_banner.hide()
            self._shell_frame.setStyleSheet(
                "QFrame#CompanionShell { background: transparent; border: none; }"
            )
            self._shell_layout.setContentsMargins(0, 0, 0, 0)
            self.unsetCursor()

        self._apply_interaction_flags(click_through=(self._click_through and not target))
        if was_visible:
            self.apply_position()
            self.force_topmost()

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

    def apply_position(self) -> None:
        self._shell_layout.activate()
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
                rx, ry = self._pos_x_ratio, self._pos_y_ratio
            else:
                rx, ry = self._default_anchor
            x = geo.left() + int(round(rx * geo.width()))
            y = geo.top() + int(round(ry * geo.height()))
            x, y = self._clamp_to_screen(x, y)
            self.move(x, y)

    def _clamp_to_screen(self, x: int, y: int) -> Tuple[int, int]:
        screen = self.screen()
        if screen is None:
            return x, y
        geo = screen.availableGeometry()
        max_x = max(geo.left(), geo.right() - self.width() + 1)
        max_y = max(geo.top(), geo.bottom() - self.height() + 1)
        return (
            max(geo.left(), min(int(x), max_x)),
            max(geo.top(), min(int(y), max_y)),
        )

    def _compute_current_ratios(self) -> Tuple[float, float]:
        screen = self.screen()
        if screen is None:
            return self._default_anchor
        geo = screen.availableGeometry()
        w = max(1, geo.width())
        h = max(1, geo.height())
        rx = max(0.0, min(1.0, round((self.x() - geo.left()) / float(w), 5)))
        ry = max(0.0, min(1.0, round((self.y() - geo.top()) / float(h), 5)))
        return rx, ry

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
                siblings = self._snap_siblings_provider() if self._snap_siblings_provider else ()
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
            self.window_position_changed.emit(self._window_key, rx, ry)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class WMPriceOverlayWindow(DraggableOverlayBase):
    """Independent draggable Warframe.market price comparison & top-5 listings window."""

    def __init__(
        self,
        *,
        click_through: bool = True,
        lang: str = "zh_CN",
        font_size_px: int = DEFAULT_OVERLAY_FONT_PX,
        pos_x_ratio: Optional[float] = None,
        pos_y_ratio: Optional[float] = None,
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(
            window_key="wm",
            click_through=click_through,
            lang=lang,
            font_size_px=font_size_px,
            pos_x_ratio=pos_x_ratio,
            pos_y_ratio=pos_y_ratio,
            default_anchor=(0.56, 0.56),
            drag_banner_key="drag_banner_wm",
            theme_colors=theme_colors,
        )
        self._last_report: Optional[WMMarketReport] = None
        self._last_weapon_display: Optional[str] = None
        self._build_wm_ui()
        self._apply_fonts()

    def _build_wm_ui(self) -> None:
        self._wm_panel = QFrame()
        self._wm_panel.setObjectName("WMPanel")
        self._wm_panel.setMinimumWidth(360)
        self._wm_panel.setMaximumWidth(520)
        self._wm_panel.setStyleSheet(
            """
            QFrame#WMPanel {
                background-color: rgba(14, 22, 38, 240);
                border: 1.5px solid rgba(56, 189, 248, 215);
                border-radius: 10px;
            }
            QLabel { color: #E0F2FE; background: transparent; }
            """
        )
        wm_lay = QVBoxLayout(self._wm_panel)
        wm_lay.setContentsMargins(14, 10, 14, 10)
        wm_lay.setSpacing(4)

        self._wm_title = QLabel("")
        self._wm_title.setStyleSheet("color: #38BDF8;")
        self._wm_title.setWordWrap(True)
        wm_lay.addWidget(self._wm_title)

        self._wm_body = QLabel("")
        self._wm_body.setStyleSheet("color: #BAE6FD;")
        self._wm_body.setWordWrap(True)
        wm_lay.addWidget(self._wm_body)

        self._wm_price = QLabel("")
        self._wm_price.setStyleSheet("color: #FDE047;")
        self._wm_price.setTextFormat(Qt.TextFormat.RichText)
        self._wm_price.setWordWrap(True)
        wm_lay.addWidget(self._wm_price)

        self._wm_listings = QLabel("")
        self._wm_listings.setStyleSheet(
            """
            QLabel {
                color: #F0F9FF;
                background-color: rgba(8, 15, 28, 185);
                border: 1px solid rgba(56, 189, 248, 90);
                border-radius: 6px;
                padding: 5px 8px;
            }
            """
        )
        self._wm_listings.setWordWrap(True)
        self._wm_listings.hide()
        wm_lay.addWidget(self._wm_listings)

        self._wm_hint = QLabel("")
        self._wm_hint.setStyleSheet("color: #7DD3FC;")
        self._wm_hint.setWordWrap(True)
        self._wm_hint.hide()
        wm_lay.addWidget(self._wm_hint)

        self._shell_layout.addWidget(self._wm_panel)

    def _apply_fonts(self) -> None:
        fam = _ui_font_family(self._lang)
        base_px = self._font_size_px
        note_px = max(9, base_px - 1)
        scale = base_px / float(DEFAULT_OVERLAY_FONT_PX)
        screen = self.screen()
        avail_w = screen.availableGeometry().width() if screen is not None else 1920
        max_w = max(320, min(int(round(520 * scale)), avail_w - 48))
        min_w = max(280, min(int(round(360 * scale)), max_w))
        self._wm_panel.setMinimumWidth(min_w)
        self._wm_panel.setMaximumWidth(max_w)
        self._drag_banner.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._wm_title.setFont(_make_px_font(fam, base_px, QFont.Weight.Bold))
        self._wm_body.setFont(_make_px_font(fam, note_px))
        self._wm_price.setFont(_make_px_font(fam, note_px, QFont.Weight.DemiBold))
        self._wm_listings.setFont(_make_px_font("Consolas", max(9, base_px - 2)))
        self._wm_hint.setFont(_make_px_font(fam, max(8, base_px - 2)))

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        super().set_theme_colors(colors)
        bg = hex_to_rgba(self._theme_colors["bg"], 242)
        border = self._theme_colors["border"]
        accent = self._theme_colors["accent"]
        text = self._theme_colors["text"]
        self._wm_panel.setStyleSheet(
            f"""
            QFrame#WMPanel {{
                background-color: {bg};
                border: 1.5px solid {border};
                border-radius: 10px;
            }}
            QLabel {{ color: {text}; background: transparent; }}
            """
        )
        self._wm_title.setStyleSheet(f"color: {accent};")
        self._wm_body.setStyleSheet(f"color: {text};")

    def set_language(self, lang: str) -> None:
        if lang in SUPPORTED_LANGS:
            self._lang = lang
            self._drag_banner.setText(I18N.t(self._drag_banner_key, lang))
            self._apply_fonts()
            if self.isVisible() and self._last_report is not None:
                self.show_wm_report(self._last_report, weapon_display=self._last_weapon_display)

    def set_font_size(self, px: int) -> None:
        self._font_size_px = clamp_overlay_font_size(px)
        self._apply_fonts()
        if self.isVisible():
            self.apply_position()

    def clear_wm_panel(self) -> None:
        self._last_report = None
        self._wm_panel.hide()
        self.hide()

    def show_wm_loading(self, weapon_display: str) -> None:
        lang = self._lang
        self._apply_fonts()
        self._wm_panel.setStyleSheet(
            """
            QFrame#WMPanel {
                background-color: rgba(14, 22, 38, 240);
                border: 1.5px solid rgba(56, 189, 248, 210);
                border-radius: 10px;
            }
            """
        )
        self._wm_title.setStyleSheet("color: #38BDF8;")
        self._wm_title.setText(I18N.t("wm_loading_title", lang, weapon=weapon_display))
        self._wm_body.setText(I18N.t("wm_loading_body", lang))
        self._wm_body.show()
        self._wm_price.hide()
        self._wm_listings.hide()
        self._wm_hint.hide()
        self._wm_panel.show()
        self.apply_position()
        self.force_topmost()

    def show_wm_report(self, report: WMMarketReport, weapon_display: Optional[str] = None) -> None:
        self._last_report = report
        self._last_weapon_display = weapon_display
        lang = self._lang
        self._apply_fonts()
        self._wm_panel.setStyleSheet(
            """
            QFrame#WMPanel {
                background-color: rgba(14, 22, 38, 242);
                border: 1.5px solid rgba(56, 189, 248, 225);
                border-radius: 10px;
            }
            """
        )
        synced = I18N.t("wm_synced_tag", lang) if report.saved_to_local else ""
        w_label = weapon_display or report.weapon_name
        self._wm_title.setStyleSheet("color: #38BDF8;")
        self._wm_title.setText(
            I18N.t(
                "wm_title",
                lang,
                match=report.match_label(lang),
                weapon=w_label,
                samples=report.sample_count,
                online=report.online_count,
                synced=synced,
            )
        )

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
        self._wm_body.setStyleSheet("color: #E0F2FE;")
        self._wm_body.setText(
            f"{I18N.t('wm_hot_pos', lang)}: {pos_txt}\n{I18N.t('wm_hot_neg', lang)}: {neg_txt}"
        )
        self._wm_body.show()

        plat_path = get_currency_icon_path("platinum")
        price_icon_sz = max(13, self._font_size_px + 1)
        plat_img_after = (
            f'&nbsp;<img src="{plat_path.as_uri()}" width="{price_icon_sz}" height="{price_icon_sz}" '
            f'style="vertical-align: middle;">'
            if plat_path.is_file()
            else ""
        )

        def _fmt_plat_html(prefix_zh: str, prefix_en: str, val: int) -> str:
            lbl = prefix_zh if lang in ("zh_CN", "zh_TW") else prefix_en
            return f"{html.escape(lbl)} {int(val)}{plat_img_after}"

        price_parts: List[str] = []
        if report.min_price is not None:
            price_parts.append(_fmt_plat_html("最低", "Min", report.min_price))
        if report.median_price is not None:
            price_parts.append(_fmt_plat_html("均价中位", "Med", report.median_price))
        if report.high_tier_median is not None and (
            report.median_price is None or report.high_tier_median > report.median_price * 1.2
        ):
            price_parts.append(_fmt_plat_html("高配中位", "High-Tier", report.high_tier_median))
        price_html = (
            " · ".join(price_parts)
            if price_parts
            else html.escape(I18N.t("wm_price_none", lang))
        )
        self._wm_price.setTextFormat(Qt.TextFormat.RichText)
        self._wm_price.setText(f"{html.escape(I18N.t('wm_price_prefix', lang))}: {price_html}")
        self._wm_price.show()

        ignored_notice = ""
        if report.ignored_unknown_stats:
            skipped_str = "、".join(I18N.stat_name(s, lang) for s in report.ignored_unknown_stats)
            ignored_notice = I18N.t("wm_ignored_unknown_stats", lang, stats=skipped_str)

        if report.top_listings:
            header_line = html.escape(I18N.t("wm_listings_header", lang, n=len(report.top_listings)))
            list_icon_sz = max(12, self._font_size_px)
            list_plat_img = (
                f'&nbsp;<img src="{plat_path.as_uri()}" width="{list_icon_sz}" height="{list_icon_sz}" '
                f'style="vertical-align: middle;">'
                if plat_path.is_file()
                else ""
            )
            rows: List[str] = []
            for lst in report.top_listings[:5]:
                raw_ln = lst.format_line(lang)
                # Strip trailing 'p' after leading price number and place Platinum icon right after price
                prefix_tok = f"{lst.price}p"
                if raw_ln.startswith(prefix_tok):
                    rest = html.escape(raw_ln[len(prefix_tok) :])
                    rows.append(f"• {lst.price}{list_plat_img}{rest}")
                else:
                    rows.append(f"• {html.escape(raw_ln)}")
            self._wm_listings.setTextFormat(Qt.TextFormat.RichText)
            self._wm_listings.setText("<br>".join([header_line] + rows))
            self._wm_listings.show()
            hint_lines = []
            if ignored_notice:
                hint_lines.append(ignored_notice)
            hint_lines.append(I18N.t("wm_open_browser_hint", lang))
            self._wm_hint.setText("\n".join(hint_lines))
            self._wm_hint.show()
        else:
            self._wm_listings.hide()
            if ignored_notice:
                self._wm_hint.setText(ignored_notice)
                self._wm_hint.show()
            else:
                self._wm_hint.hide()

        self._wm_panel.show()
        self.apply_position()
        self.force_topmost()

    def show_wm_notice(self, message: str, is_warning: bool = True, auto_hide_ms: int = 3500) -> None:
        lang = self._lang
        self._apply_fonts()
        border = "rgba(251, 191, 36, 210)" if is_warning else "rgba(56, 189, 248, 200)"
        title_color = "#FCD34D" if is_warning else "#38BDF8"
        self._wm_panel.setStyleSheet(
            f"""
            QFrame#WMPanel {{
                background-color: rgba(30, 22, 16, 240);
                border: 1.5px solid {border};
                border-radius: 10px;
            }}
            """
        )
        self._wm_title.setStyleSheet(f"color: {title_color};")
        self._wm_title.setText(f"{I18N.t('wm_notice_prefix', lang)}{message}")
        self._wm_body.hide()
        self._wm_price.hide()
        self._wm_listings.hide()
        self._wm_hint.hide()
        self._wm_panel.show()
        self.apply_position()
        self.force_topmost()
        if auto_hide_ms > 0:
            QTimer.singleShot(auto_hide_ms, self._hide_if_notice_only)

    def _hide_if_notice_only(self) -> None:
        if self._drag_unlocked:
            return
        if self._last_report is None and self.isVisible():
            self.hide()


class RerollSynergyPopupWindow(DraggableOverlayBase):
    """Independent draggable popup triggered in `reroll` mode on `3+1-`/`2+1-` + S-grade positive stat."""

    def __init__(
        self,
        *,
        click_through: bool = True,
        lang: str = "zh_CN",
        font_size_px: int = DEFAULT_OVERLAY_FONT_PX,
        pos_x_ratio: Optional[float] = None,
        pos_y_ratio: Optional[float] = None,
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(
            window_key="synergy",
            click_through=click_through,
            lang=lang,
            font_size_px=font_size_px,
            pos_x_ratio=pos_x_ratio,
            pos_y_ratio=pos_y_ratio,
            default_anchor=(0.03, 0.08),
            drag_banner_key="drag_banner_synergy",
            theme_colors=theme_colors,
        )
        self._last_alert: Optional[RerollSynergyAlert] = None
        self._build_synergy_ui()

    def _build_synergy_ui(self) -> None:
        self._card = QFrame()
        self._card.setObjectName("SynergyPopupCard")
        self._card.setMinimumWidth(320)
        self._card.setMaximumWidth(450)
        self._card.setStyleSheet(
            """
            QFrame#SynergyPopupCard {
                background-color: rgba(24, 18, 42, 242);
                border: 2px solid #FFD166;
                border-radius: 12px;
            }
            QLabel { color: #FEF9C3; background: transparent; }
            """
        )
        lay = QVBoxLayout(self._card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet("color: #FFD166;")
        self._title_lbl.setWordWrap(True)
        lay.addWidget(self._title_lbl)

        self._meta_lbl = QLabel("")
        self._meta_lbl.setStyleSheet(
            """
            QLabel {
                color: #1A1228;
                background-color: #34D399;
                border-radius: 6px;
                padding: 2px 8px;
            }
            """
        )
        self._meta_lbl.setWordWrap(True)
        lay.addWidget(self._meta_lbl)

        self._s_header_lbl = QLabel("")
        self._s_header_lbl.setStyleSheet("color: #FDE68A;")
        lay.addWidget(self._s_header_lbl)

        self._s_stats_lbl = QLabel("")
        self._s_stats_lbl.setStyleSheet(
            """
            QLabel {
                color: #FFD166;
                background-color: rgba(255, 209, 102, 28);
                border: 1px solid rgba(255, 209, 102, 130);
                border-radius: 7px;
                padding: 5px 8px;
            }
            """
        )
        self._s_stats_lbl.setWordWrap(True)
        lay.addWidget(self._s_stats_lbl)

        self._tips_header_lbl = QLabel("")
        self._tips_header_lbl.setStyleSheet("color: #7DD3FC;")
        lay.addWidget(self._tips_header_lbl)

        self._tips_lbl = QLabel("")
        self._tips_lbl.setStyleSheet("color: #E0F2FE;")
        self._tips_lbl.setWordWrap(True)
        lay.addWidget(self._tips_lbl)

        self._shell_layout.addWidget(self._card)

    def _apply_fonts(self) -> None:
        fam = _ui_font_family(self._lang)
        base_px = self._font_size_px
        note_px = max(9, base_px - 1)
        scale = base_px / float(DEFAULT_OVERLAY_FONT_PX)
        screen = self.screen()
        avail_w = screen.availableGeometry().width() if screen is not None else 1920
        max_w = max(300, min(int(round(450 * scale)), avail_w - 48))
        min_w = max(260, min(int(round(320 * scale)), max_w))
        self._card.setMinimumWidth(min_w)
        self._card.setMaximumWidth(max_w)
        self._drag_banner.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._title_lbl.setFont(_make_px_font(fam, base_px + 1, QFont.Weight.Bold))
        self._meta_lbl.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._s_header_lbl.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._s_stats_lbl.setFont(_make_px_font(fam, base_px, QFont.Weight.Bold))
        self._tips_header_lbl.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._tips_lbl.setFont(_make_px_font(fam, note_px))

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        super().set_theme_colors(colors)
        bg = hex_to_rgba(self._theme_colors["bg"], 242)
        accent = self._theme_colors["accent"]
        text = self._theme_colors["text"]
        self._card.setStyleSheet(
            f"""
            QFrame#SynergyPopupCard {{
                background-color: {bg};
                border: 2px solid {accent};
                border-radius: 12px;
            }}
            QLabel {{ color: {text}; background: transparent; }}
            """
        )
        self._title_lbl.setStyleSheet(f"color: {accent};")
        self._tips_lbl.setStyleSheet(f"color: {text};")

    def set_language(self, lang: str) -> None:
        if lang in SUPPORTED_LANGS:
            self._lang = lang
            self._drag_banner.setText(I18N.t(self._drag_banner_key, lang))
            if self.isVisible() and self._last_alert is not None:
                self.show_alert(self._last_alert)

    def set_font_size(self, px: int) -> None:
        self._font_size_px = clamp_overlay_font_size(px)
        self._apply_fonts()
        if self.isVisible():
            self.apply_position()

    def show_alert(self, alert: RerollSynergyAlert) -> None:
        self._last_alert = alert
        lang = self._lang
        self._apply_fonts()
        combo_txt = alert.combo_label_zh if lang in ("zh_CN", "zh_TW") else alert.combo_label_en
        tips = alert.synergy_tips_zh if lang in ("zh_CN", "zh_TW") else alert.synergy_tips_en

        self._title_lbl.setText(I18N.t("synergy_popup_title", lang))
        self._meta_lbl.setText(f"{alert.source_side_label} {alert.weapon_name} · {combo_txt}")
        self._s_header_lbl.setText(I18N.t("synergy_s_stats_header", lang))
        self._s_stats_lbl.setText("\n".join(f"★ {ln}" for ln in alert.s_grade_stats))
        self._tips_header_lbl.setText(I18N.t("synergy_tips_header", lang))
        self._tips_lbl.setText("\n".join(f"• {tp}" for tp in tips))

        self.apply_position()
        self.force_topmost()

    def clear_alert(self) -> None:
        self._last_alert = None
        self.hide()


class RelicRewardsOverlayWindow(DraggableOverlayBase):
    """Independent draggable Void Relic (开核桃) horizontal price strip floated ABOVE reward items.

    Displays ONLY compact Platinum & Ducat prices (with icons placed AFTER numbers,
    no 'P'/'D' suffixes and no blocking headers/item lists) directly above the
    1~4 reward selection cards so the player can pick rewards unobstructed.
    """

    def __init__(
        self,
        *,
        click_through: bool = True,
        lang: str = "zh_CN",
        font_size_px: int = DEFAULT_OVERLAY_FONT_PX,
        pos_x_ratio: Optional[float] = None,
        pos_y_ratio: Optional[float] = None,
        theme_colors: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(
            window_key="relic",
            click_through=click_through,
            lang=lang,
            font_size_px=font_size_px,
            pos_x_ratio=pos_x_ratio,
            pos_y_ratio=pos_y_ratio,
            default_anchor=(0.24, 0.14),
            drag_banner_key="drag_banner_relic",
            theme_colors=theme_colors,
        )
        self._last_items: List[object] = []
        self._last_elapsed_ms: Optional[float] = None
        self._anchor_ratios: Optional[Tuple[float, float, float, float]] = None
        self._row_widgets: List[QFrame] = []
        self._build_relic_ui()

    def _build_relic_ui(self) -> None:
        self._box = QFrame()
        self._box.setObjectName("RelicStrip")
        self._box.setStyleSheet(
            """
            QFrame#RelicStrip {
                background: transparent;
                border: none;
            }
            QLabel { color: #ECFEFF; background: transparent; }
            """
        )
        self._rows_container = QHBoxLayout(self._box)
        self._rows_container.setContentsMargins(0, 0, 0, 0)
        self._rows_container.setSpacing(12)
        self._shell_layout.addWidget(self._box)

    def _clear_rows(self) -> None:
        self._row_widgets.clear()
        while self._rows_container.count():
            item = self._rows_container.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    @property
    def row_count(self) -> int:
        return len(self._row_widgets)

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        super().set_theme_colors(colors)
        if self.isVisible() and self._last_items is not None:
            self.show_relic_rewards(
                self._last_items,
                elapsed_ms=self._last_elapsed_ms,
                anchor_ratios=self._anchor_ratios,
            )

    def set_language(self, lang: str) -> None:
        if lang in SUPPORTED_LANGS:
            self._lang = lang
            self._drag_banner.setText(I18N.t(self._drag_banner_key, lang))
            if self.isVisible() and self._last_items is not None:
                self.show_relic_rewards(
                    self._last_items,
                    elapsed_ms=self._last_elapsed_ms,
                    anchor_ratios=self._anchor_ratios,
                )

    def set_font_size(self, px: int) -> None:
        self._font_size_px = clamp_overlay_font_size(px)
        if self.isVisible():
            self.show_relic_rewards(
                self._last_items,
                elapsed_ms=self._last_elapsed_ms,
                anchor_ratios=self._anchor_ratios,
            )

    def clear_relic(self) -> None:
        self._clear_rows()
        self.hide()

    def apply_position(self) -> None:
        """Align horizontally with the Void Relic ROI and float directly ABOVE the reward cards."""
        self._shell_layout.activate()
        self._outer.activate()
        self._shell_frame.adjustSize()
        self.adjustSize()
        hint = self.sizeHint()
        if hint.isValid():
            self.resize(hint)

        screen = self.screen()
        if screen is None:
            return

        # If user manually dragged & saved a custom position, respect it
        if self._pos_x_ratio is not None and self._pos_y_ratio is not None:
            geo = screen.availableGeometry()
            x = geo.left() + int(round(self._pos_x_ratio * geo.width()))
            y = geo.top() + int(round(self._pos_y_ratio * geo.height()))
            x, y = self._clamp_to_screen(x, y)
            self.move(x, y)
            return

        # Default: anchor directly ABOVE the calibrated `relic_rewards` ROI without blocking items!
        # Automatically scales width & horizontal centering for 1, 2, 3, or 4 recognized relics!
        if self._anchor_ratios is not None:
            geo = screen.geometry()
            rx, ry, rw, rh = self._anchor_ratios
            roi_x = geo.left() + int(round(rx * geo.width()))
            roi_y = geo.top() + int(round(ry * geo.height()))
            roi_w = max(240, int(round(rw * geo.width())))
            roi_h = max(40, int(round(rh * geo.height())))

            n_cards = max(1, min(4, len(self._row_widgets)))
            # Warframe keeps each reward card a fixed ~1/4 of the 4-slot ROI width whether N=1,2,3, or 4
            single_slot_w = roi_w / 4.0
            items_top = roi_y if roi_h >= single_slot_w * 0.65 else (roi_y + roi_h - int(round(single_slot_w * 1.05)))

            desired_w = int(round(single_slot_w * n_cards)) if self._row_widgets else min(460, roi_w)
            target_w = max(self.sizeHint().width(), min(desired_w, roi_w, geo.width() - 24))
            if target_w != self.width():
                self.setFixedWidth(target_w)
                self.adjustSize()
                self.setMinimumWidth(0)
                self.setMaximumWidth(16777215)
                self.resize(target_w, self.sizeHint().height())

            # Align horizontally over the exact recognized relic columns (1, 2, 3, or 4 centered)
            col_ratios = [
                float(getattr(it, "column_center_x_ratio"))
                for it in self._last_items[:4]
                if getattr(it, "column_center_x_ratio", None) is not None
            ]
            if col_ratios:
                avg_cx = sum(col_ratios) / len(col_ratios)
                center_x = roi_x + int(round(avg_cx * roi_w))
            else:
                center_x = roi_x + roi_w // 2

            x = center_x - self.width() // 2
            y = items_top - self.height() - 10
            x, y = self._clamp_to_screen(x, y)
            self.move(x, y)
            return

        super().apply_position()

    def show_relic_rewards(
        self,
        reward_items: Sequence[object],
        elapsed_ms: Optional[float] = None,
        anchor_ratios: Optional[Tuple[float, float, float, float]] = None,
    ) -> None:
        from src.relic_prices import RelicRewardMatch

        self._last_items = list(reward_items)
        self._last_elapsed_ms = elapsed_ms
        if anchor_ratios is not None:
            self._anchor_ratios = anchor_ratios
        lang = self._lang
        fam = _ui_font_family(lang)
        base_px = self._font_size_px
        note_px = max(9, base_px - 1)
        icon_sz = max(15, base_px + 4)

        self._drag_banner.setFont(_make_px_font(fam, note_px, QFont.Weight.Bold))
        self._clear_rows()

        matches: List[RelicRewardMatch] = []
        for item in reward_items:
            if isinstance(item, RelicRewardMatch):
                matches.append(item)
            elif isinstance(item, str) and item.strip():
                matches.append(
                    RelicRewardMatch(
                        raw_text=item.strip(),
                        matched_zh=item.strip(),
                        matched_en=item.strip(),
                        plat_price=None,
                        ducats=0,
                    )
                )

        # Compute target strip width proportionally to actual relic count (1, 2, 3, or 4)
        screen = self.screen()
        target_strip_w: Optional[int] = None
        if self._anchor_ratios is not None and screen is not None and matches:
            geo = screen.geometry()
            roi_w = max(240, int(round(self._anchor_ratios[2] * geo.width())))
            n_cards = max(1, min(4, len(matches)))
            prop_w = int(round((roi_w / 4.0) * n_cards))
            target_strip_w = max(150 * n_cards, min(prop_w, roi_w, geo.width() - 24))

        if matches:
            self._box.setMinimumWidth(target_strip_w if target_strip_w else 0)
            self._box.setMaximumWidth(16777215)

            # Determine effective Rank 1 (Gold) & Rank 2 (Silver) if caller did not pre-assign ranks
            top_matches = matches[:4]
            if any(m.recommend_rank > 0 for m in top_matches):
                eff_ranks = [m.recommend_rank for m in top_matches]
            else:
                eff_ranks = [0] * len(top_matches)
                valued_idxs = [
                    idx
                    for idx, m in enumerate(top_matches)
                    if not m.is_forma and ((m.plat_price is not None and m.plat_price > 0) or m.ducats > 0)
                ]
                if valued_idxs:
                    ranked = sorted(
                        valued_idxs,
                        key=lambda i: (
                            float(top_matches[i].plat_price or 0.0),
                            int(top_matches[i].ducats or 0),
                            -i,
                        ),
                        reverse=True,
                    )
                    eff_ranks[ranked[0]] = 1
                    if len(ranked) >= 2:
                        rem = ranked[1:]
                        r2 = rem[0]
                        if not any((top_matches[i].plat_price or 0.0) > 0 for i in rem):
                            r2 = max(rem, key=lambda i: (int(top_matches[i].ducats or 0), -i))
                        eff_ranks[r2] = 2

            for idx, m in enumerate(top_matches):
                rank = eff_ranks[idx]
                card = QFrame()
                card.setObjectName("RelicRewardCard")
                bg_card = hex_to_rgba(self._theme_colors["bg"], 236)
                if rank == 1:
                    card.setStyleSheet(
                        f"""
                        QFrame#RelicRewardCard {{
                            background-color: {bg_card};
                            border: 2px solid #F5C542;
                            border-radius: 9px;
                        }}
                        """
                    )
                elif rank == 2:
                    card.setStyleSheet(
                        f"""
                        QFrame#RelicRewardCard {{
                            background-color: {bg_card};
                            border: 2px solid #C0C8D4;
                            border-radius: 9px;
                        }}
                        """
                    )
                else:
                    card.setStyleSheet(
                        f"""
                        QFrame#RelicRewardCard {{
                            background-color: {hex_to_rgba(self._theme_colors['bg'], 226)};
                            border: 1px solid #334155;
                            border-radius: 9px;
                        }}
                        """
                    )

                # Strict left-to-right 4-slot horizontal layout (NO item names, NO inner boxes, NO P/D letters):
                #   [1. Rank Star ★ (rank 1 gold / rank 2 silver / omitted)]
                #   [2. Platinum price number + Platinum icon]
                #   [3. Divider | + Ducat price number + Ducat icon]
                #   [4. Vaulted chest icon 18x18 (ONLY if is_vaulted)]
                c_lay = QHBoxLayout(card)
                c_lay.setContentsMargins(10, 6, 10, 6)
                c_lay.setSpacing(5)
                c_lay.addStretch(1)

                # Slot 1: Recommendation Emblem Icon (ONLY rank 1 gold or rank 2 silver)
                if rank in (1, 2):
                    c_lay.addWidget(make_relic_rank_icon_label(rank, icon_sz))

                # Slot 2: Platinum average price number + Platinum icon (no 'p'/'P' letter)
                if not m.is_forma and m.plat_price is not None and m.plat_price > 0:
                    plat_str = f"{m.plat_price:g}"
                    plat_color = "#F5C542" if rank == 1 else ("#E2E8F0" if rank == 2 else "#F8FAFC")
                else:
                    plat_str = "-"
                    plat_color = "#94A3B8"
                plat_lbl = QLabel(plat_str)
                plat_lbl.setObjectName("RelicPlatLabel")
                plat_lbl.setFont(_make_px_font("Consolas", base_px + 1, QFont.Weight.Bold))
                plat_lbl.setStyleSheet(f"color: {plat_color}; background: transparent; border: none;")
                c_lay.addWidget(plat_lbl)
                plat_icon = make_currency_icon_label("platinum", icon_sz)
                plat_icon.setObjectName("RelicPlatIcon")
                c_lay.addWidget(plat_icon)

                # Slot 3: Thin divider | + Ducat value number + Ducat icon (no 'D' letter)
                sep = QLabel("|")
                sep.setObjectName("RelicDivider")
                sep.setFont(_make_px_font("Consolas", base_px, QFont.Weight.Normal))
                sep.setStyleSheet("color: #475569; background: transparent; border: none;")
                c_lay.addWidget(sep)

                if not m.is_forma and m.ducats > 0:
                    duc_str = f"{m.ducats}"
                    duc_color = "#FDE68A" if m.is_best_ducats else "#CBD5E1"
                else:
                    duc_str = "-"
                    duc_color = "#94A3B8"
                duc_lbl = QLabel(duc_str)
                duc_lbl.setObjectName("RelicDucatLabel")
                duc_lbl.setFont(_make_px_font("Consolas", base_px, QFont.Weight.Bold))
                duc_lbl.setStyleSheet(f"color: {duc_color}; background: transparent; border: none;")
                c_lay.addWidget(duc_lbl)
                duc_icon = make_currency_icon_label("ducats", icon_sz)
                duc_icon.setObjectName("RelicDucatIcon")
                c_lay.addWidget(duc_icon)

                # Slot 4: Vaulted closed chest icon (18x18 at default font, scales up cleanly with larger fonts)
                if not m.is_forma and m.is_vaulted:
                    c_lay.addWidget(make_vaulted_chest_label(max(18, base_px + 5)))

                c_lay.addStretch(1)
                self._rows_container.addWidget(card, stretch=1)
                self._row_widgets.append(card)
        else:
            self._box.setMinimumWidth(0)
            self._box.setMaximumWidth(520)
            empty_card = QFrame()
            empty_card.setStyleSheet(
                """
                QFrame {
                    background-color: rgba(18, 22, 36, 232);
                    border: 1.5px solid #FCD34D;
                    border-radius: 9px;
                }
                """
            )
            e_lay = QHBoxLayout(empty_card)
            e_lay.setContentsMargins(12, 7, 12, 7)
            empty_lbl = QLabel(I18N.t("relic_empty", lang))
            empty_lbl.setFont(_make_px_font(fam, note_px, QFont.Weight.DemiBold))
            empty_lbl.setStyleSheet("color: #FCD34D;")
            e_lay.addWidget(empty_lbl)
            self._rows_container.addWidget(empty_card)

        self.apply_position()
        self.force_topmost()
