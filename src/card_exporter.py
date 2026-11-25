"""Off-screen dark-gold / Void Riven showcase card PNG exporter & clipboard copier.

Renders a crisp, publication-ready standalone PNG image of the currently recognized
Riven Mod (weapon title, fixed 1.20 disposition, all stats with Roll% progress bars,
overall S/A/B/C grade badge, tags, and latest WM reference price) purely via
PyQt6 `QPainter` on a `QImage` — completely independent of screen capture or monitor state.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import QApplication

from src.evaluator import RivenEvaluation
from src.i18n import I18N
from src.overlay_ui import GRADE_COLORS, EvaluatedRivenCard
from src.wm_client import WMMarketReport


@dataclass
class ExportedCardResult:
    """Result returned by `export_riven_showcase`."""

    ok: bool
    path: Optional[Path] = None
    width: int = 0
    height: int = 0
    copied_to_clipboard: bool = False
    error: str = ""


def resolve_desktop_dir() -> Path:
    """Resolve the user's Desktop directory cross-platform (Windows & Linux XDG)."""
    if sys.platform == "win32":
        # Prefer USERPROFILE/Desktop (handles localized Desktop folders & OneDrive fallback)
        candidates = [
            Path.home() / "Desktop",
            Path.home() / "OneDrive" / "Desktop",
        ]
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.insert(0, Path(userprofile) / "Desktop")
        for cand in candidates:
            if cand.is_dir():
                return cand
        return Path.home() / "Desktop"

    # Linux / POSIX XDG user-dirs.dirs
    xdg_cfg = Path.home() / ".config" / "user-dirs.dirs"
    if xdg_cfg.is_file():
        try:
            content = xdg_cfg.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'^XDG_DESKTOP_DIR="?([^"\n]+)"?', content, re.MULTILINE)
            if m:
                raw = m.group(1).replace("$HOME", str(Path.home()))
                p = Path(raw)
                if p.is_dir():
                    return p
        except Exception:
            pass
    return Path.home() / "Desktop"


def _sanitize_filename_token(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", (text or "").strip()).strip("_")
    return cleaned[:40] or "Unknown"


def _hex_qcolor(hex_or_rgba: str, alpha_override: Optional[int] = None) -> QColor:
    c = QColor(hex_or_rgba)
    if not c.isValid():
        c = QColor("#FFD166")
    if alpha_override is not None:
        c.setAlpha(alpha_override)
    return c


def _make_font(family: str, px: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(9, int(px)))
    f.setWeight(weight)
    return f


def render_riven_showcase_image(
    card: EvaluatedRivenCard,
    wm_report: Optional[WMMarketReport] = None,
    lang: str = "zh_CN",
) -> QImage:
    """Render a standalone high-DPI dark-gold / Void Riven showcase `QImage` off-screen."""
    riven = card.riven
    ev: RivenEvaluation = card.evaluation
    fam = "Segoe UI" if lang == "en_US" else "Microsoft YaHei UI"

    width = 560
    pad_x = 28
    inner_w = width - pad_x * 2

    # Compute dynamic height cleanly based on content sections
    n_stats = max(1, len(ev.stat_evaluations))
    has_arch = bool(ev.matched_archetype_name_en if lang == "en_US" else ev.matched_archetype_name)
    has_tags = bool(ev.tags)
    has_wm = (wm_report is not None) or bool(ev.cached_wm_price)

    height = 128  # Header + weapon title + meta
    if has_arch:
        height += 34
    if has_tags:
        height += 36
    height += 18  # Divider
    height += n_stats * 66
    height += 42  # Verdict line
    if has_wm:
        height += 64
    height += 38  # Bottom footer watermark

    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    fg_hex, _, border_hex = GRADE_COLORS.get(ev.overall_grade, GRADE_COLORS["?"])
    accent_color = _hex_qcolor(border_hex)

    # 1. Dark Void gradient card background
    bg_grad = QLinearGradient(0.0, 0.0, float(width), float(height))
    bg_grad.setColorAt(0.0, QColor("#120A24"))
    bg_grad.setColorAt(0.5, QColor("#1B1034"))
    bg_grad.setColorAt(1.0, QColor("#0F091D"))
    painter.setBrush(bg_grad)
    painter.setPen(QPen(accent_color, 2.5))
    painter.drawRoundedRect(QRectF(4, 4, width - 8, height - 8), 18, 18)

    # Subtle inner gold/purple frame line
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(186, 148, 255, 65), 1.0))
    painter.drawRoundedRect(QRectF(11, 11, width - 22, height - 22), 13, 13)

    # Subtle decorative top-right Void diamond watermark
    cx, cy = width - 48, 42
    diamond = QPolygonF(
        [QPointF(cx, cy - 22), QPointF(cx + 22, cy), QPointF(cx, cy + 22), QPointF(cx - 22, cy)]
    )
    painter.setBrush(QColor(168, 85, 247, 26))
    painter.setPen(QPen(QColor(255, 209, 102, 55), 1.0))
    painter.drawPolygon(diamond)

    y = 26

    # 2. Overall Grade Badge (top-left)
    badge_sz = 56
    badge_rect = QRectF(pad_x, y, badge_sz, badge_sz)
    painter.setBrush(_hex_qcolor(fg_hex, 45))
    painter.setPen(QPen(accent_color, 2.2))
    painter.drawRoundedRect(badge_rect, 12, 12)
    painter.setPen(accent_color)
    painter.setFont(_make_font("Segoe UI", 30, QFont.Weight.Bold))
    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, ev.overall_grade)

    # Weapon title & metadata
    title_x = pad_x + badge_sz + 14
    title_w = inner_w - badge_sz - 14
    if lang == "en_US":
        w_disp = ev.weapon_name_en or ev.weapon_name or riven.weapon_name or I18N.t("unknown_weapon", lang)
    else:
        w_disp = ev.weapon_name or riven.weapon_name or I18N.t("unknown_weapon", lang)
    full_title = f"{w_disp} {riven.riven_suffix}" if riven.riven_suffix else (riven.riven_name or w_disp)

    painter.setPen(QColor("#F8F3FF"))
    painter.setFont(_make_font(fam, 21, QFont.Weight.Bold))
    painter.drawText(QRectF(title_x, y + 2, title_w, 30), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, full_title)

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

    painter.setPen(QColor("#C4B5FD"))
    painter.setFont(_make_font(fam, 13))
    painter.drawText(
        QRectF(title_x, y + 34, title_w, 22),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        " · ".join(meta_parts),
    )

    y += 72

    # 3. Matched Archetype banner (if any)
    arch_disp = ev.matched_archetype_name_en if lang == "en_US" else ev.matched_archetype_name
    if arch_disp:
        arch_rect = QRectF(pad_x, y, inner_w, 26)
        painter.setBrush(QColor(255, 209, 102, 32) if ev.has_inverted_god_neg else QColor(56, 189, 248, 28))
        painter.setPen(QPen(QColor("#FFD166") if ev.has_inverted_god_neg else QColor("#38BDF8"), 1.0))
        painter.drawRoundedRect(arch_rect, 6, 6)
        painter.setPen(QColor("#FFD166") if ev.has_inverted_god_neg else QColor("#7DD3FC"))
        painter.setFont(_make_font(fam, 12, QFont.Weight.Bold))
        painter.drawText(
            arch_rect.adjusted(10, 0, -10, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"✦ {I18N.t('archetype_prefix', lang)}{arch_disp}",
        )
        y += 34

    # 4. Synergy Tags row
    if ev.tags:
        tx = float(pad_x)
        painter.setFont(_make_font(fam, 12, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        for tag in ev.tags[:5]:
            t_txt = I18N.tag_name(tag, lang)
            tw = fm.horizontalAdvance(t_txt) + 18
            if tx + tw > width - pad_x:
                break
            pill_r = QRectF(tx, y, tw, 24)
            is_gold = tag in ("SSS 反转神负", "融合双暴", "双暴多重", "全中推荐", "重击神配")
            pill_fg = QColor("#FFD166") if is_gold else QColor("#D8B4FE")
            pill_bg = QColor(255, 209, 102, 38) if is_gold else QColor(168, 85, 247, 36)
            painter.setBrush(pill_bg)
            painter.setPen(QPen(pill_fg, 1.0))
            painter.drawRoundedRect(pill_r, 6, 6)
            painter.setPen(pill_fg)
            painter.drawText(pill_r, Qt.AlignmentFlag.AlignCenter, t_txt)
            tx += tw + 8
        y += 36

    # Divider
    painter.setPen(QPen(QColor(168, 118, 255, 95), 1.0))
    painter.drawLine(pad_x, y + 6, width - pad_x, y + 6)
    y += 18

    # 5. Stat Rows with Roll% Progress Bars
    for se in ev.stat_evaluations:
        row_rect = QRectF(pad_x, y, inner_w, 58)
        painter.setBrush(QColor(255, 255, 255, 10))
        painter.setPen(QPen(QColor(168, 118, 255, 55), 1.0))
        painter.drawRoundedRect(row_rect, 8, 8)

        unit_disp = se.unit
        if lang == "en_US":
            unit_disp = "s" if unit_disp == "秒" else ("m" if unit_disp == "米" else unit_disp)
        val_str = f"x{se.value:g}" if se.sign == "x" else f"{se.sign}{se.value:g}{unit_disp}"
        rec_star = "★ " if se.is_recommended else ""
        hyb_tag = f"{I18N.t('hybrid_stat_badge', lang)} " if se.is_hybrid else ""
        stat_txt = f"{rec_star}{hyb_tag}{val_str} {I18N.stat_name(se.stat_name, lang)}"

        if se.is_inverted_god_neg:
            st_color = QColor("#FFD166")
        elif se.is_recommended and not se.is_negative:
            st_color = QColor("#FDE68A")
        elif se.is_negative:
            st_color = QColor("#FDA4AF")
        else:
            st_color = QColor("#A7F3D0")

        painter.setPen(st_color)
        painter.setFont(_make_font(fam, 15, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(pad_x + 12, y + 5, inner_w - 130, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            stat_txt,
        )

        # Range or missing-range note
        if se.missing_base_range:
            sub_txt = I18N.t("hybrid_no_range_notice", lang)
            sub_col = QColor("#7DD3FC")
        else:
            rng = se.range_display
            if lang == "en_US":
                rng = rng.replace("秒", "s").replace("米", "m")
            sub_txt = f"{I18N.t('range_prefix', lang)} {rng}" if rng else ""
            sub_col = QColor("#A78BFA")

        if sub_txt:
            painter.setPen(sub_col)
            painter.setFont(_make_font("Consolas" if not se.missing_base_range else fam, 11))
            painter.drawText(
                QRectF(pad_x + 12, y + 26, inner_w - 130, 16),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                sub_txt,
            )

        # Right side: Roll% + small grade pill
        s_fg_hex, _, s_border_hex = GRADE_COLORS.get(se.grade, GRADE_COLORS["?"])
        s_color = _hex_qcolor(s_fg_hex)
        if se.roll_pct is not None:
            pct_str = f"{se.roll_pct:.0f}%"
            painter.setPen(s_color)
            painter.setFont(_make_font("Consolas", 14, QFont.Weight.Bold))
            painter.drawText(
                QRectF(width - pad_x - 110, y + 10, 62, 24),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                pct_str,
            )

        sm_badge = QRectF(width - pad_x - 38, y + 9, 26, 26)
        painter.setBrush(_hex_qcolor(s_fg_hex, 42))
        painter.setPen(QPen(_hex_qcolor(s_border_hex), 1.4))
        painter.drawRoundedRect(sm_badge, 6, 6)
        painter.setPen(s_color)
        painter.setFont(_make_font("Segoe UI", 13, QFont.Weight.Bold))
        painter.drawText(sm_badge, Qt.AlignmentFlag.AlignCenter, se.grade)

        # Progress bar at bottom of row
        if se.roll_pct is not None:
            bar_x = pad_x + 12
            bar_y = y + 46
            bar_w = inner_w - 24
            bar_h = 6
            painter.setBrush(QColor(255, 255, 255, 28))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, bar_h), 3, 3)
            fill_w = max(4.0, bar_w * (max(0.0, min(100.0, se.roll_pct)) / 100.0))
            painter.setBrush(s_color)
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), 3, 3)

        y += 66

    # 6. Verdict summary
    summary_text = I18N.summary(ev.summary_key, lang)
    painter.setPen(QColor("#E9D5FF"))
    painter.setFont(_make_font(fam, 13, QFont.Weight.DemiBold))
    painter.drawText(
        QRectF(pad_x, y + 4, inner_w, 28),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"{I18N.t('verdict_prefix', lang)}{summary_text}",
    )
    y += 40

    # 7. WM Market Reference Price Box (if available)
    if has_wm:
        wm_rect = QRectF(pad_x, y, inner_w, 52)
        painter.setBrush(QColor(14, 30, 52, 220))
        painter.setPen(QPen(QColor("#38BDF8"), 1.3))
        painter.drawRoundedRect(wm_rect, 9, 9)

        if wm_report is not None:
            wm_header = f"Warframe.market [{wm_report.match_label(lang)}] · {wm_report.sample_count} 单参考"
            wm_price_txt = wm_report.price_summary()
        else:
            wm_header = "Warframe.market 本地缓存参考价"
            wm_price_txt = str(ev.cached_wm_price or "")

        painter.setPen(QColor("#38BDF8"))
        painter.setFont(_make_font(fam, 12, QFont.Weight.Bold))
        painter.drawText(
            QRectF(pad_x + 12, y + 6, inner_w - 24, 18),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            wm_header,
        )
        painter.setPen(QColor("#FDE047"))
        price_font = _make_font(fam, 14, QFont.Weight.Bold)
        painter.setFont(price_font)
        from src.companion_overlays import get_currency_icon_path

        # Strip trailing 'p'/'P' after numeric prices and draw Platinum icon AFTER price text
        clean_price_txt = re.sub(r"(\d+(?:\.\d+)?)\s*[pP]\b", r"\1", wm_price_txt)
        price_x = float(pad_x + 12)
        text_w = float(painter.fontMetrics().horizontalAdvance(clean_price_txt))
        painter.drawText(
            QRectF(price_x, y + 26, min(text_w + 4.0, inner_w - 46.0), 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            clean_price_txt,
        )
        plat_icon_path = get_currency_icon_path("platinum")
        if plat_icon_path.is_file():
            plat_img = QImage(str(plat_icon_path))
            if not plat_img.isNull():
                scaled_plat = plat_img.scaled(
                    18,
                    18,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                icon_x = min(price_x + text_w + 6.0, float(width - pad_x - 30))
                painter.drawImage(QRectF(icon_x, y + 27, 18, 18), scaled_plat)
        y += 64

    # 8. Footer watermark
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    painter.setPen(QColor("#9A86C2"))
    painter.setFont(_make_font(fam, 11))
    watermark = (
        f"Riven Lens · 1.20 Standard Dispo · {ts_str}"
        if lang == "en_US"
        else f"裂罅透镜 · 1.20 标准倾向评估 · {ts_str}"
    )
    painter.drawText(
        QRectF(pad_x, height - 30, inner_w, 20),
        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        watermark,
    )

    painter.end()
    return img


def export_riven_showcase(
    card: EvaluatedRivenCard,
    wm_report: Optional[WMMarketReport] = None,
    *,
    lang: str = "zh_CN",
    output_dir: Optional[str | Path] = None,
    copy_to_clipboard: bool = True,
) -> ExportedCardResult:
    """Render showcase PNG, save to Desktop (or `output_dir`), and copy to clipboard."""
    try:
        image = render_riven_showcase_image(card, wm_report=wm_report, lang=lang)
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return ExportedCardResult(ok=False, error="离屏渲染紫卡展示图失败")

        target_dir = Path(output_dir) if output_dir else resolve_desktop_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        weapon_tok = _sanitize_filename_token(
            card.evaluation.weapon_name or card.riven.weapon_name or "Riven"
        )
        ts_tok = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = target_dir / f"Riven_{weapon_tok}_{ts_tok}.png"

        saved = image.save(str(out_path), "PNG")
        if not saved or not out_path.is_file():
            return ExportedCardResult(ok=False, error=f"保存 PNG 失败: {out_path}")

        copied = False
        if copy_to_clipboard:
            app = QApplication.instance()
            if app is not None:
                cb = app.clipboard()
                if cb is not None:
                    cb.setImage(image)
                    copied = True

        return ExportedCardResult(
            ok=True,
            path=out_path,
            width=image.width(),
            height=image.height(),
            copied_to_clipboard=copied,
        )
    except Exception as exc:
        return ExportedCardResult(ok=False, error=str(exc))
