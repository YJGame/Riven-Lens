"""Fullscreen single-mode visual ROI region calibrator with live OCR preview.

Design rules:
  - Strictly displays ONLY ONE business mode's box(es) at a time so boxes from
    different screens never overlap or clutter the view:
      1. `single_riven`  -> 1 green box
      2. `reroll`        -> 2 boxes (blue left + purple right)
      3. `chat_riven`    -> 1 orange box
      4. `relic_rewards` -> 1 cyan horizontal strip
  - Pressing `F8` (or clicking Next/Prev Mode) cycles smoothly through the 4 modes.
  - Supports dragging the box center to move, dragging 8 corner/edge handles to resize,
    live physical-pixel `(X, Y, W, H)` readout, one-click live OCR preview popup
    (thumbnail + raw OCR text), per-mode reset, and atomic save to `data/roi_profiles.json`.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.capture import ScreenCapturer, ScreenRegion
from src.ocr_engine import OCRResult, RivenOCREngine
from src.roi_manager import (
    CALIBRATION_MODES,
    MODE_BOX_KEYS,
    MODE_SINGLE_RIVEN,
    ROI_BOX_SPECS,
    ROIManager,
    format_mode_short,
)
from src.tray_manager import load_app_icon

HANDLE_SIZE = 10
HANDLE_HIT_RADIUS = 13
MIN_BOX_W_PHYS = 60
MIN_BOX_H_PHYS = 40

_HANDLE_CURSORS = {
    "tl": Qt.CursorShape.SizeFDiagCursor,
    "br": Qt.CursorShape.SizeFDiagCursor,
    "tr": Qt.CursorShape.SizeBDiagCursor,
    "bl": Qt.CursorShape.SizeBDiagCursor,
    "t": Qt.CursorShape.SizeVerCursor,
    "b": Qt.CursorShape.SizeVerCursor,
    "l": Qt.CursorShape.SizeHorCursor,
    "r": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


def _bgr_to_pixmap(bgr: np.ndarray, max_w: int = 380, max_h: int = 260) -> QPixmap:
    if bgr is None or bgr.size == 0:
        return QPixmap()
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w, ch = rgb.shape
    bytes_per_line = ch * w
    qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
    pm = QPixmap.fromImage(qimg)
    return pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class OCRPreviewDialog(QDialog):
    """Modal preview popup showing captured ROI thumbnail(s) + raw RapidOCR output."""

    def __init__(
        self,
        entries: List[Tuple[str, ScreenRegion, np.ndarray, OCRResult]],
        lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowIcon(load_app_icon())
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(
            "OCR 框选即时预览测试" if lang != "en_US" else "Live ROI OCR Preview"
        )
        self.setMinimumWidth(560)
        self.setMaximumHeight(680)
        self.setStyleSheet(
            """
            QDialog {
                background-color: #140E22;
                color: #F3ECFF;
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }
            QFrame#PreviewItem {
                background-color: #1C1430;
                border: 1px solid #5B4290;
                border-radius: 8px;
            }
            QTextEdit {
                background-color: #0F0A1A;
                color: #E0F2FE;
                border: 1px solid #4A3575;
                border-radius: 6px;
                padding: 6px;
                font-family: Consolas, "Microsoft YaHei UI";
                font-size: 12px;
            }
            QPushButton {
                background-color: #6D28D9;
                color: #FFFFFF;
                border: 1px solid #8B5CF6;
                border-radius: 6px;
                padding: 6px 18px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #7C3AED; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        cont_lay = QVBoxLayout(container)
        cont_lay.setContentsMargins(0, 0, 0, 0)
        cont_lay.setSpacing(10)

        for label, reg, img, ocr_res in entries:
            card = QFrame()
            card.setObjectName("PreviewItem")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(8)

            hdr = QLabel(
                f"{label}  |  X={reg.left}, Y={reg.top}, W={reg.width}, H={reg.height}  |  "
                f"{len(ocr_res.lines)} 行 · 置信度 {ocr_res.avg_confidence * 100:.1f}% · {ocr_res.elapsed_ms:.0f}ms"
            )
            hdr.setStyleSheet("color: #FFD166; font-weight: bold;")
            hdr.setWordWrap(True)
            lay.addWidget(hdr)

            body_row = QHBoxLayout()
            body_row.setSpacing(10)

            thumb_lbl = QLabel()
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_lbl.setStyleSheet("background-color: #090612; border: 1px solid #4A3575; border-radius: 4px; padding: 4px;")
            pm = _bgr_to_pixmap(img, max_w=260, max_h=240)
            if not pm.isNull():
                thumb_lbl.setPixmap(pm)
            else:
                thumb_lbl.setText("(无图像)")
            body_row.addWidget(thumb_lbl)

            text_box = QTextEdit()
            text_box.setReadOnly(True)
            if ocr_res.lines:
                text_box.setPlainText("\n".join(ocr_res.lines))
            else:
                text_box.setPlainText(
                    "（未识别到文字，请确认游戏画面位于当前框内并微调边界）"
                    if lang != "en_US"
                    else "(No text detected — align box over the game card and retry)"
                )
            text_box.setMinimumHeight(140)
            body_row.addWidget(text_box, stretch=1)
            lay.addLayout(body_row)

            cont_lay.addWidget(card)

        cont_lay.addStretch(1)
        scroll.setWidget(container)
        root.addWidget(scroll, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_ok = QPushButton("关闭预览" if lang != "en_US" else "Close")
        btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(btn_ok)
        root.addLayout(btn_row)


class RegionCalibratorOverlay(QWidget):
    """Fullscreen translucent single-mode ROI calibrator overlay."""

    calibration_saved = pyqtSignal()
    calibration_cancelled = pyqtSignal()

    def __init__(
        self,
        roi_manager: ROIManager,
        capturer: ScreenCapturer,
        ocr: Optional[RivenOCREngine] = None,
        initial_mode: str = MODE_SINGLE_RIVEN,
        lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._roi_manager = roi_manager
        self._capturer = capturer
        self._ocr = ocr
        self._lang = lang
        self._current_mode = initial_mode if initial_mode in CALIBRATION_MODES else MODE_SINGLE_RIVEN

        # Physical screen dimensions (from mss) & Qt logical screen geometry
        screen = QApplication.primaryScreen()
        geo = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
        self._logical_geo = geo
        self._logical_w = max(1, geo.width())
        self._logical_h = max(1, geo.height())

        try:
            mon = capturer.get_monitor_info()
            self._phys_w = max(1, int(mon["width"]))
            self._phys_h = max(1, int(mon["height"]))
        except Exception:
            dpr = screen.devicePixelRatio() if screen is not None else 1.0
            self._phys_w = max(1, int(round(self._logical_w * dpr)))
            self._phys_h = max(1, int(round(self._logical_h * dpr)))

        # Draft physical-pixel boxes for ALL 4 modes (only the active mode is drawn!)
        self._draft_boxes: Dict[str, ScreenRegion] = {}
        self._modified_keys: set[str] = set()
        self._load_all_drafts()

        # Interaction state for drag/resize
        self._active_box_key: Optional[str] = None
        self._active_handle: Optional[str] = None
        self._drag_start_pos = QPoint(0, 0)
        self._drag_start_rect = QRect()

        self.setWindowIcon(load_app_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(self._logical_geo)

        self._build_chrome()
        self._refresh_chrome_text()

    # ---- Coordinate conversion (Physical <-> Qt Logical) ----

    def _phys_to_logical_rect(self, reg: ScreenRegion) -> QRect:
        sx = self._logical_w / float(self._phys_w)
        sy = self._logical_h / float(self._phys_h)
        x = int(round(reg.left * sx))
        y = int(round(reg.top * sy))
        w = max(24, int(round(reg.width * sx)))
        h = max(20, int(round(reg.height * sy)))
        return QRect(x, y, w, h)

    def _logical_to_phys_region(self, rect: QRect) -> ScreenRegion:
        sx = self._phys_w / float(self._logical_w)
        sy = self._phys_h / float(self._logical_h)
        left = int(round(rect.x() * sx))
        top = int(round(rect.y() * sy))
        width = max(MIN_BOX_W_PHYS, int(round(rect.width() * sx)))
        height = max(MIN_BOX_H_PHYS, int(round(rect.height() * sy)))
        return ScreenRegion(left=left, top=top, width=width, height=height).clamp(
            self._phys_w, self._phys_h
        )

    def _load_all_drafts(self) -> None:
        self._draft_boxes.clear()
        for mode in CALIBRATION_MODES:
            for key, reg in self._roi_manager.get_mode_regions(mode, self._phys_w, self._phys_h).items():
                self._draft_boxes[key] = reg

    # ---- Public API (also used by unit tests) ----

    @property
    def current_mode(self) -> str:
        return self._current_mode

    def get_visible_box_keys(self) -> List[str]:
        return list(MODE_BOX_KEYS[self._current_mode])

    def get_box_physical(self, box_key: str) -> ScreenRegion:
        return self._draft_boxes[box_key]

    def set_box_physical(self, box_key: str, region: ScreenRegion) -> None:
        self._draft_boxes[box_key] = region.clamp(self._phys_w, self._phys_h)
        self._modified_keys.add(box_key)
        self._refresh_chrome_text()
        self.update()

    def set_mode(self, mode_name: str) -> str:
        if mode_name not in CALIBRATION_MODES:
            raise ValueError(f"Unknown calibration mode: {mode_name}")
        self._current_mode = mode_name
        self._active_box_key = None
        self._active_handle = None
        self._refresh_chrome_text()
        self.update()
        return self._current_mode

    def cycle_next_mode(self) -> str:
        idx = CALIBRATION_MODES.index(self._current_mode)
        return self.set_mode(CALIBRATION_MODES[(idx + 1) % len(CALIBRATION_MODES)])

    def cycle_prev_mode(self) -> str:
        idx = CALIBRATION_MODES.index(self._current_mode)
        return self.set_mode(CALIBRATION_MODES[(idx - 1) % len(CALIBRATION_MODES)])

    def reset_current_mode(self) -> None:
        for key in MODE_BOX_KEYS[self._current_mode]:
            self._draft_boxes[key] = self._roi_manager.get_default_region(
                key, self._phys_w, self._phys_h
            )
            self._modified_keys.discard(key)
        self._roi_manager.reset_mode(self._current_mode, self._phys_w, self._phys_h)
        self._refresh_chrome_text()
        self.update()

    def save_and_close(self) -> None:
        for key in self._modified_keys:
            if key in self._draft_boxes:
                self._roi_manager.set_region(key, self._draft_boxes[key], self._phys_w, self._phys_h)
        self._roi_manager.save()
        self.calibration_saved.emit()
        self.close()

    def cancel_and_close(self) -> None:
        self._roi_manager.reload()
        self.calibration_cancelled.emit()
        self.close()

    # ---- Floating HUD Header & Bottom Toolbar ----

    def _build_chrome(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 24)
        root.setSpacing(0)

        # Top HUD banner
        top_bar = QFrame()
        top_bar.setStyleSheet(
            """
            QFrame {
                background-color: rgba(18, 12, 34, 235);
                border: 1.5px solid #8B5CF6;
                border-radius: 10px;
            }
            QLabel { color: #F3ECFF; background: transparent; }
            """
        )
        top_lay = QVBoxLayout(top_bar)
        top_lay.setContentsMargins(16, 10, 16, 10)
        top_lay.setSpacing(4)

        self._lbl_top_title = QLabel("")
        self._lbl_top_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_top_title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Weight.Bold))
        self._lbl_top_title.setStyleSheet("color: #FFD166;")
        top_lay.addWidget(self._lbl_top_title)

        self._lbl_top_coords = QLabel("")
        self._lbl_top_coords.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_top_coords.setFont(QFont("Consolas", 10, QFont.Weight.DemiBold))
        self._lbl_top_coords.setStyleSheet("color: #E0F2FE;")
        top_lay.addWidget(self._lbl_top_coords)

        self._lbl_top_help = QLabel("")
        self._lbl_top_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_top_help.setFont(QFont("Microsoft YaHei UI", 9))
        self._lbl_top_help.setStyleSheet("color: #C4B5FD;")
        top_lay.addWidget(self._lbl_top_help)

        top_wrap = QHBoxLayout()
        top_wrap.addStretch(1)
        top_wrap.addWidget(top_bar)
        top_wrap.addStretch(1)
        root.addLayout(top_wrap)

        root.addStretch(1)

        # Bottom floating toolbar
        bot_bar = QFrame()
        bot_bar.setStyleSheet(
            """
            QFrame {
                background-color: rgba(18, 12, 34, 238);
                border: 1.5px solid #6D28D9;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #2A1E47;
                color: #F3ECFF;
                border: 1px solid #6D28D9;
                border-radius: 6px;
                padding: 7px 14px;
                font-family: "Microsoft YaHei UI", "Segoe UI";
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #3D2B66; }
            QPushButton#TestBtn { background-color: #0284C7; border-color: #38BDF8; }
            QPushButton#TestBtn:hover { background-color: #0369A1; }
            QPushButton#SaveBtn { background-color: #16A34A; border-color: #4ADE80; }
            QPushButton#SaveBtn:hover { background-color: #15803D; }
            QPushButton#CancelBtn { background-color: #7F1D1D; border-color: #F87171; }
            QPushButton#CancelBtn:hover { background-color: #991B1B; }
            """
        )
        bot_lay = QHBoxLayout(bot_bar)
        bot_lay.setContentsMargins(12, 9, 12, 9)
        bot_lay.setSpacing(8)

        is_en = self._lang == "en_US"
        btn_prev = QPushButton("◀ Prev Mode" if is_en else "◀ 上一模式")
        btn_prev.clicked.connect(self.cycle_prev_mode)
        bot_lay.addWidget(btn_prev)

        btn_next = QPushButton("Next Mode (F8) ▶" if is_en else "下一模式 (F8) ▶")
        btn_next.clicked.connect(self.cycle_next_mode)
        bot_lay.addWidget(btn_next)

        btn_test = QPushButton("🔍 Test OCR Now" if is_en else "🔍 测试当前框选 OCR")
        btn_test.setObjectName("TestBtn")
        btn_test.clicked.connect(self._on_test_ocr_clicked)
        bot_lay.addWidget(btn_test)

        btn_reset = QPushButton("↺ Reset Mode Default" if is_en else "↺ 恢复当前模式默认框")
        btn_reset.clicked.connect(self.reset_current_mode)
        bot_lay.addWidget(btn_reset)

        btn_save = QPushButton("💾 Save & Apply" if is_en else "💾 保存并生效")
        btn_save.setObjectName("SaveBtn")
        btn_save.clicked.connect(self.save_and_close)
        bot_lay.addWidget(btn_save)

        btn_cancel = QPushButton("✖ Cancel (ESC)" if is_en else "✖ 取消 (ESC)")
        btn_cancel.setObjectName("CancelBtn")
        btn_cancel.clicked.connect(self.cancel_and_close)
        bot_lay.addWidget(btn_cancel)

        bot_wrap = QHBoxLayout()
        bot_wrap.addStretch(1)
        bot_wrap.addWidget(bot_bar)
        bot_wrap.addStretch(1)
        root.addLayout(bot_wrap)

    def _refresh_chrome_text(self) -> None:
        idx = CALIBRATION_MODES.index(self._current_mode) + 1
        total = len(CALIBRATION_MODES)
        mode_name = format_mode_short(self._current_mode, self._lang)
        if self._lang == "en_US":
            self._lbl_top_title.setText(
                f"[Calibrating {idx}/{total}] {mode_name} ({self._current_mode})  ·  Screen {self._phys_w}x{self._phys_h}"
            )
            self._lbl_top_help.setText(
                "Press F8 to switch mode · Drag box interior to move · Drag 8 handles to resize · ESC to cancel"
            )
        else:
            self._lbl_top_title.setText(
                f"[校准 {idx}/{total}] {mode_name} ({self._current_mode})  ·  物理分辨率 {self._phys_w}×{self._phys_h}"
            )
            self._lbl_top_help.setText(
                "按 F8 轮流切换 4 种模式 · 按住框中心拖动位置 · 拖拽 8 个角/边手柄调整大小 · 按 ESC 取消"
            )

        coord_parts = []
        for key in MODE_BOX_KEYS[self._current_mode]:
            spec = ROI_BOX_SPECS[key]
            reg = self._draft_boxes[key]
            lbl = spec.label_en if self._lang == "en_US" else spec.label_zh
            coord_parts.append(f"{lbl}: (X={reg.left}, Y={reg.top}, W={reg.width}, H={reg.height})")
        self._lbl_top_coords.setText("   |   ".join(coord_parts))

    # ---- Painting (strictly ONLY current mode's boxes!) ----

    def _handle_rects(self, rect: QRect) -> Dict[str, QRect]:
        hs = HANDLE_SIZE
        half = hs // 2
        cx = rect.left() + rect.width() // 2
        cy = rect.top() + rect.height() // 2
        return {
            "tl": QRect(rect.left() - half, rect.top() - half, hs, hs),
            "tr": QRect(rect.right() - half, rect.top() - half, hs, hs),
            "bl": QRect(rect.left() - half, rect.bottom() - half, hs, hs),
            "br": QRect(rect.right() - half, rect.bottom() - half, hs, hs),
            "t": QRect(cx - half, rect.top() - half, hs, hs),
            "b": QRect(cx - half, rect.bottom() - half, hs, hs),
            "l": QRect(rect.left() - half, cy - half, hs, hs),
            "r": QRect(rect.right() - half, cy - half, hs, hs),
        }

    def paintEvent(self, _event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        visible_keys = MODE_BOX_KEYS[self._current_mode]
        logical_rects = {k: self._phys_to_logical_rect(self._draft_boxes[k]) for k in visible_keys}

        # Semi-transparent dark backdrop with clear cutout inside active mode box(es)
        backdrop_path = QPainterPath()
        backdrop_path.addRect(0, 0, self.width(), self.height())
        for rect in logical_rects.values():
            cut = QPainterPath()
            cut.addRect(rect.x(), rect.y(), rect.width(), rect.height())
            backdrop_path = backdrop_path.subtracted(cut)

        painter.fillPath(backdrop_path, QColor(8, 6, 18, 138))

        # Draw each box for the ACTIVE mode only
        for key in visible_keys:
            spec = ROI_BOX_SPECS[key]
            rect = logical_rects[key]
            color = QColor(spec.color_hex)

            # Subtle interior tint so box remains visually clear
            tint = QColor(color)
            tint.setAlpha(28)
            painter.fillRect(rect, tint)

            # Crisp border
            pen = QPen(color, 2.5)
            painter.setPen(pen)
            painter.drawRect(rect)

            # Label tag at top-left inside/above box
            reg = self._draft_boxes[key]
            lbl_txt = f"{spec.label_en if self._lang == 'en_US' else spec.label_zh} ({reg.width}×{reg.height})"
            painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(lbl_txt) + 14
            th = fm.height() + 6
            tag_y = rect.top() - th - 4 if rect.top() > th + 95 else rect.top() + 6
            tag_rect = QRect(rect.left() + 4, tag_y, tw, th)
            painter.fillRect(tag_rect, QColor(color.red(), color.green(), color.blue(), 225))
            painter.setPen(QColor("#0F0A1A"))
            painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, lbl_txt)

            # Draw 8 resize handles
            painter.setBrush(QColor("#FFFFFF"))
            painter.setPen(QPen(color, 2))
            for hrect in self._handle_rects(rect).values():
                painter.drawRect(hrect)

        painter.end()

    # ---- Hit testing & mouse drag / resize ----

    def _hit_test(self, pos: QPoint) -> Tuple[Optional[str], Optional[str]]:
        visible_keys = MODE_BOX_KEYS[self._current_mode]
        # Check handles first (reverse order so later boxes take priority if adjacent)
        for key in reversed(visible_keys):
            rect = self._phys_to_logical_rect(self._draft_boxes[key])
            for hname, hrect in self._handle_rects(rect).items():
                expanded = hrect.adjusted(
                    -HANDLE_HIT_RADIUS // 2,
                    -HANDLE_HIT_RADIUS // 2,
                    HANDLE_HIT_RADIUS // 2,
                    HANDLE_HIT_RADIUS // 2,
                )
                if expanded.contains(pos):
                    return key, hname
            if rect.contains(pos):
                return key, "move"
        return None, None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        box_key, handle = self._hit_test(pos)
        if box_key and handle:
            self._active_box_key = box_key
            self._active_handle = handle
            self._drag_start_pos = pos
            self._drag_start_rect = self._phys_to_logical_rect(self._draft_boxes[box_key])
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._active_box_key and self._active_handle and (event.buttons() & Qt.MouseButton.LeftButton):
            dx = pos.x() - self._drag_start_pos.x()
            dy = pos.y() - self._drag_start_pos.y()
            r = QRect(self._drag_start_rect)
            min_w = max(30, int(round(MIN_BOX_W_PHYS * (self._logical_w / float(self._phys_w)))))
            min_h = max(24, int(round(MIN_BOX_H_PHYS * (self._logical_h / float(self._phys_h)))))

            h = self._active_handle
            if h == "move":
                nx = max(0, min(self._logical_w - r.width(), r.x() + dx))
                ny = max(0, min(self._logical_h - r.height(), r.y() + dy))
                r.moveTo(nx, ny)
            else:
                left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
                if "l" in h:
                    left = max(0, min(right - min_w, left + dx))
                if "r" in h:
                    right = min(self._logical_w - 1, max(left + min_w, right + dx))
                if "t" in h:
                    top = max(0, min(bottom - min_h, top + dy))
                if "b" in h:
                    bottom = min(self._logical_h - 1, max(top + min_h, bottom + dy))
                r = QRect(QPoint(left, top), QPoint(right, bottom))

            self._draft_boxes[self._active_box_key] = self._logical_to_phys_region(r)
            self._modified_keys.add(self._active_box_key)
            self._refresh_chrome_text()
            self.update()
            event.accept()
            return

        # Hover cursor feedback
        _, handle = self._hit_test(pos)
        if handle and handle in _HANDLE_CURSORS:
            self.setCursor(_HANDLE_CURSORS[handle])
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._active_box_key:
            self._active_box_key = None
            self._active_handle = None
            event.accept()

    # ---- Keyboard shortcuts (F8 cycle mode, ESC cancel) ----

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_F8, Qt.Key.Key_Tab, Qt.Key.Key_Right):
            self.cycle_next_mode()
            event.accept()
            return
        if key == Qt.Key.Key_Left:
            self.cycle_prev_mode()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.cancel_and_close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- Live background OCR test for current mode's box(es) ----

    def _on_test_ocr_clicked(self) -> None:
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(140, self._capture_and_show_ocr_preview)

    def _capture_and_show_ocr_preview(self) -> None:
        ocr_engine = self._ocr or RivenOCREngine()
        entries: List[Tuple[str, ScreenRegion, np.ndarray, OCRResult]] = []
        try:
            for key in MODE_BOX_KEYS[self._current_mode]:
                spec = ROI_BOX_SPECS[key]
                reg = self._draft_boxes[key]
                lbl = spec.label_en if self._lang == "en_US" else spec.label_zh
                bgr, _ = self._capturer.capture_region_box(reg)
                ocr_res = ocr_engine.recognize(bgr, fallback_to_raw=True)
                entries.append((lbl, reg, bgr, ocr_res))
        finally:
            self.show()
            self.raise_()
            self.activateWindow()

        if entries:
            dlg = OCRPreviewDialog(entries=entries, lang=self._lang, parent=self)
            dlg.exec()
