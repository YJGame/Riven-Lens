"""Built-in themed User Manual dialog for 裂罅透镜 (Riven Lens).

Automatically displayed on first launch (`settings.first_run_guide_shown == False`),
and can be reopened anytime via the "用户手册 / User Manual" button in the Settings
& Control Panel. Live-syncs with the active color theme, UI language, and custom hotkeys.
"""

from __future__ import annotations

import html
from typing import Dict, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.hotkey_manager import DEFAULT_EXPORT_HOTKEY, DEFAULT_LOCK_HOTKEY, DEFAULT_OPEN_PANEL_HOTKEY
from src.i18n import I18N, SUPPORTED_LANGS
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


def render_user_manual_html(
    *,
    lang: str,
    theme_colors: Dict[str, str],
    hotkey_toggle: str = "F8",
    hotkey_wm: str = "F9",
    hotkey_hide: str = "ESC",
    hotkey_open_panel: str = DEFAULT_OPEN_PANEL_HOTKEY,
) -> str:
    """Build rich styled HTML content for the built-in User Manual."""
    bg = theme_colors.get("bg", "#120C20")
    border = theme_colors.get("border", "#A876FF")
    accent = theme_colors.get("accent", "#FFD166")
    text = theme_colors.get("text", "#EFE4FF")
    card_bg = _blend_hex(bg, "#FFFFFF", 0.06)
    muted = _blend_hex(text, border, 0.30)
    kbd_bg = _blend_hex(bg, border, 0.28)

    hk_t = html.escape(hotkey_toggle or "F8")
    hk_w = html.escape(hotkey_wm or "F9")
    hk_h = html.escape(hotkey_hide or "ESC")
    hk_p = html.escape(hotkey_open_panel or DEFAULT_OPEN_PANEL_HOTKEY)
    hk_s = html.escape(DEFAULT_EXPORT_HOTKEY)
    hk_l = html.escape(DEFAULT_LOCK_HOTKEY)

    style = f"""
    <style>
      body {{
        color: {text};
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
        line-height: 1.6;
        margin: 0;
        padding: 2px 4px;
      }}
      .card {{
        background-color: {card_bg};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 12px;
      }}
      h3 {{
        color: {accent};
        font-size: 14px;
        margin: 0 0 6px 0;
      }}
      ul, ol {{
        margin: 4px 0 2px 0;
        padding-left: 20px;
      }}
      li {{
        margin-bottom: 4px;
      }}
      .kbd {{
        background-color: {kbd_bg};
        color: {accent};
        font-family: Consolas, "Microsoft YaHei UI", monospace;
        font-weight: bold;
        padding: 1px 5px;
        border-radius: 4px;
      }}
      .muted {{
        color: {muted};
        font-size: 12px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        margin-top: 4px;
      }}
      td {{
        padding: 4px 6px;
        border-bottom: 1px solid {_blend_hex(bg, border, 0.25)};
      }}
      td.keycol {{
        width: 155px;
        white-space: nowrap;
      }}
    </style>
    """

    if lang == "zh_TW":
        body = f"""
        <div class="card">
          <h3>🚀 快速上手（3 步驟）</h3>
          <ol>
            <li><b>管理員權限</b>：若遊戲或啟動器以管理員身分執行，請同樣以管理員啟動本工具，避免快捷鍵與懸浮窗被系統攔截。</li>
            <li><b>校準辨識框</b>：打開設定面板（預設 <span class="kbd">{hk_p}</span>）點擊「校準辨識框」，依畫面框選單卡、雙卡、聊天紫卡與開核桃區域。</li>
            <li><b>開始使用</b>：在遊戲內按 <span class="kbd">{hk_t}</span> 循環切換 4 大模式，全程純視覺 OCR，零記憶體讀取。</li>
          </ol>
        </div>

        <div class="card">
          <h3>🎯 4 大工作模式（按 <span class="kbd">{hk_t}</span> 切換）</h3>
          <ul>
            <li><b>模式 1 · 單張紫卡（自動辨識）</b>：查看紫卡自動評分與評級；連續 4 次未偵測到紫卡自動摺疊為待命膠囊。</li>
            <li><b>模式 2 · 洗卡雙卡對比（自動辨識）</b>：即時比對新舊詞條差異；2正1負 / 3正1負出現 ≥90% 可熔接詞條時列出官方複合詞條配方；無鎖定 S 級正詞條/正收益負詞條響起提示音，≥98% 頂級詞條顯示流光邊框。</li>
            <li><b>模式 3 · 聊天連結紫卡（按 <span class="kbd">{hk_w}</span> 辨識）</b>：開啟聊天紫卡後按 <span class="kbd">{hk_w}</span> 即時評分並自動查詢 Warframe.market 精準參考價，離開頁面自動收起。</li>
            <li><b>模式 4 · 遺物開箱比價（按 <span class="kbd">{hk_w}</span> 辨識）</b>：虛空遺物 4 選 1 介面按 <span class="kbd">{hk_w}</span>，卡片上方顯示極簡橫向比價條（金色首選徽章 / 銀色次選徽章 · 白金均價 · 杜卡德金幣 · 已入庫鎖頭圖示）。</li>
          </ul>
        </div>

        <div class="card">
          <h3>⌨️ 快捷鍵一覽</h3>
          <table>
            <tr><td class="keycol"><span class="kbd">{hk_t}</span></td><td>輪詢切換工作模式（單卡 → 雙卡 → 聊天 → 遺物 → 暫停）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_w}</span></td><td>單卡/雙卡查 WM 價格；聊天紫卡/開核桃觸發識別</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_h}</span></td><td>收起懸浮窗為待命膠囊（保留目前模式）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_p}</span></td><td>呼出 / 隱藏設定與控制面板（可自訂）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_s}</span></td><td>匯出紫卡展示圖 PNG 並複製到剪貼簿</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_l}</span></td><td>鎖定 / 解鎖防誤觸熱鍵</td></tr>
          </table>
        </div>

        <div class="card">
          <h3>🎨 實用與個人化技巧</h3>
          <ul>
            <li><b>邊緣磁吸對齊</b>：點擊「解鎖拖曳位置」即可自由擺放懸浮窗，靠近螢幕四邊、水平中線或其他懸浮窗邊緣時自動磁吸對齊。</li>
            <li><b>主題配色</b>：內建 6 套常用配色與 4 槽位自由選色，同步套用於設定面板與全部懸浮窗。</li>
            <li><b>多語言自動模型</b>：支援全部 15 種官方語言，切換語種自動按需下載對應 RapidOCR 辨識包並熱重載。</li>
            <li><b>系統匣常駐</b>：關閉設定視窗會縮小至右下角系統匣，雙擊匣圖示或按 <span class="kbd">{hk_p}</span> 重新開啟；右鍵系統匣選「退出軟體」徹底結束。</li>
          </ul>
          <p class="muted">之後隨時可在設定面板點擊「使用者手冊」再次查閱本說明。</p>
        </div>
        """
    elif lang == "zh_CN":
        body = f"""
        <div class="card">
          <h3>🚀 快速上手（3 步）</h3>
          <ol>
            <li><b>管理员权限</b>：若游戏或 WeGame 以管理员身份运行，请同样以管理员启动本工具，防止快捷键或悬浮窗被系统拦截。</li>
            <li><b>校准识别框</b>：打开设置面板（默认 <span class="kbd">{hk_p}</span>）点击「校准识别框」，按提示框选单卡、双卡、聊天紫卡和开核桃区域。</li>
            <li><b>开始使用</b>：在游戏内按 <span class="kbd">{hk_t}</span> 轮询切换 4 大工作模式，全程纯视觉 OCR，零内存读取。</li>
          </ol>
        </div>

        <div class="card">
          <h3>🎯 4 大工作模式（按 <span class="kbd">{hk_t}</span> 循环切换）</h3>
          <ul>
            <li><b>模式 1 · 单张紫卡（自动识别）</b>：悬停/查看紫卡自动评分评级；连续 4 次检测不到有效紫卡自动折叠为待命胶囊。</li>
            <li><b>模式 2 · 洗卡双卡对比（自动识别）</b>：左右对比新旧词条差异；2正1负 / 3正1负中出现 ≥90% 可熔接词条时自动列出官方复合词条组合；新卡出现无锁定 S 级正词条或正收益负词条触发提示音，≥98% 顶级词条触发流光边框。</li>
            <li><b>模式 3 · 聊天链接紫卡（按 <span class="kbd">{hk_w}</span> 识别）</b>：打开聊天紫卡后按 <span class="kbd">{hk_w}</span> 识别并自动拉取 Warframe.market 精准参考价，退出页面自动收起。</li>
            <li><b>模式 4 · 开核桃比价（按 <span class="kbd">{hk_w}</span> 识别）</b>：虚空遗物 4 选 1 界面按 <span class="kbd">{hk_w}</span>，卡片上方显示横向 4 列极简比价条（金色首选徽章 / 银色次选徽章 · 白金均价 · 杜卡德金币 · 已入库锁头图标）。</li>
          </ul>
        </div>

        <div class="card">
          <h3>⌨️ 快捷键一览</h3>
          <table>
            <tr><td class="keycol"><span class="kbd">{hk_t}</span></td><td>轮询切换工作模式（单卡 → 双卡 → 聊天 → 遗物 → 暂停）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_w}</span></td><td>单卡/双卡下查询 WM 价格；聊天紫卡/开核桃下触发识别</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_h}</span></td><td>收起悬浮窗为待命胶囊（保留当前模式）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_p}</span></td><td>呼出 / 隐藏设置与控制面板（可在设置中自定义）</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_s}</span></td><td>导出紫卡精美展示图 PNG 并复制到剪贴板</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_l}</span></td><td>锁定 / 解锁防误触热键</td></tr>
          </table>
        </div>

        <div class="card">
          <h3>🎨 实用与个性化技巧</h3>
          <ul>
            <li><b>边缘磁吸对齐</b>：点击「解锁拖拽位置」即可自由调整悬浮窗位置，靠近屏幕四边、水平中线或其他悬浮窗边缘时自动磁吸对齐。</li>
            <li><b>主题配色</b>：内置 6 套常用搭配 + 4 槽位自由选色，实时同步作用于设置界面与全部悬浮窗。</li>
            <li><b>全 15 种官方语言</b>：切换语种后界面瞬间热切换，并在后台按需自动拉取对应 RapidOCR 识别模型。</li>
            <li><b>系统托盘常驻</b>：关闭设置窗口仅隐藏至托盘，双击托盘图标或按 <span class="kbd">{hk_p}</span> 可随时呼出；右键托盘选择「退出软件」彻底关闭。</li>
          </ul>
          <p class="muted">之后随时可以在设置面板点击「用户手册」按钮再次回来阅读。</p>
        </div>
        """
    else:
        body = f"""
        <div class="card">
          <h3>🚀 Quick Start (3 Steps)</h3>
          <ol>
            <li><b>Run as Administrator</b>: If Warframe runs elevated, launch Riven Lens as Administrator so global hotkeys and overlays stay active.</li>
            <li><b>Calibrate ROI</b>: Open the Settings Panel (default <span class="kbd">{hk_p}</span>) and click <b>Calibrate ROI</b> to align Single Riven, Reroll, Chat Riven, and Void Relic regions.</li>
            <li><b>Start Scanning</b>: Press <span class="kbd">{hk_t}</span> in-game to cycle through all 4 modes. 100% pure-vision OCR with zero memory reading.</li>
          </ol>
        </div>

        <div class="card">
          <h3>🎯 4 Work Modes (Cycle with <span class="kbd">{hk_t}</span>)</h3>
          <ul>
            <li><b>Mode 1 · Single Riven (Auto OCR)</b>: Automatically rates any viewed Riven and collapses into a compact standby pill after 4 consecutive undetected frames.</li>
            <li><b>Mode 2 · Reroll Compare (Auto OCR)</b>: Side-by-side comparison; lists official Riven Splicing recipes for ≥90% stats in 2+1-/3+1- rolls; plays rare chime on unlocked S-grade stats and rainbow border on ≥98% rolls.</li>
            <li><b>Mode 3 · Chat Link Riven (<span class="kbd">{hk_w}</span>)</b>: Press <span class="kbd">{hk_w}</span> on a chat-linked Riven to grade and auto-query exact Warframe.market listings; collapses when leaving the page.</li>
            <li><b>Mode 4 · Void Relic Pricer (<span class="kbd">{hk_w}</span>)</b>: Press <span class="kbd">{hk_w}</span> on the 4-choice reward screen for a compact horizontal price strip (Gold 1st / Silver 2nd emblem · Platinum · Ducats · Vaulted lock icon).</li>
          </ul>
        </div>

        <div class="card">
          <h3>⌨️ Global Hotkeys</h3>
          <table>
            <tr><td class="keycol"><span class="kbd">{hk_t}</span></td><td>Cycle work modes (Single → Reroll → Chat → Relic → Paused)</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_w}</span></td><td>Query Warframe.market (Auto modes) / Capture &amp; OCR (Chat &amp; Relic)</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_h}</span></td><td>Collapse overlay into standby pill (keeps active mode)</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_p}</span></td><td>Show / hide Settings &amp; Control Panel (customizable)</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_s}</span></td><td>Export showcase Riven PNG &amp; copy to clipboard</td></tr>
            <tr><td class="keycol"><span class="kbd">{hk_l}</span></td><td>Lock / unlock anti-mispress hotkeys</td></tr>
          </table>
        </div>

        <div class="card">
          <h3>🎨 Tips &amp; Customization</h3>
          <ul>
            <li><b>Magnetic Edge Snapping</b>: Unlock drag in Settings to freely position overlays with automatic 16px edge &amp; sibling snapping.</li>
            <li><b>Color Themes</b>: 6 curated palettes + 4 custom color pickers that live-theme both Settings and all overlays.</li>
            <li><b>15 Official Languages</b>: Instant UI translation with automatic on-demand RapidOCR model download.</li>
            <li><b>System Tray</b>: Closing Settings hides to tray; press <span class="kbd">{hk_p}</span> or double-click the tray icon to reopen, or right-click tray → Quit to exit completely.</li>
          </ul>
          <p class="muted">You can reopen this guide anytime via the "User Manual" button in Settings.</p>
        </div>
        """

    return f"<!DOCTYPE html><html><head><meta charset='utf-8'>{style}</head><body>{body}</body></html>"


class UserManualDialog(QDialog):
    """Themed, scrollable User Manual window shown on first launch and reopenable from Settings."""

    def __init__(
        self,
        settings: UserSettings,
        lang: str = "zh_CN",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._lang = lang if lang in SUPPORTED_LANGS else "zh_CN"
        self._settings = settings
        self._theme_colors: Dict[str, str] = resolve_theme_colors(
            settings.theme_preset, settings.theme_colors
        )
        self._hk_toggle = settings.hotkey_toggle_monitor or "F8"
        self._hk_wm = settings.hotkey_fetch_wm or "F9"
        self._hk_hide = settings.hotkey_hide_overlay or "ESC"
        self._hk_open_panel = settings.hotkey_open_panel or DEFAULT_OPEN_PANEL_HOTKEY

        self.setWindowIcon(load_app_icon())
        self.setMinimumSize(540, 560)
        self.resize(620, 680)
        self._build_ui()
        self._apply_theme()
        self._refresh_content()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        self._header_frame = QFrame()
        hdr_lay = QHBoxLayout(self._header_frame)
        hdr_lay.setContentsMargins(14, 10, 14, 10)
        self._lbl_title = QLabel("")
        self._lbl_title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Weight.Bold))
        hdr_lay.addWidget(self._lbl_title)
        hdr_lay.addStretch(1)
        root.addWidget(self._header_frame)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(False)
        self._browser.setReadOnly(True)
        root.addWidget(self._browser, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_close = QPushButton("")
        self._btn_close.setMinimumWidth(120)
        self._btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_close)
        root.addLayout(btn_row)

    def _apply_theme(self) -> None:
        colors = self._theme_colors or resolve_theme_colors(DEFAULT_THEME_PRESET)
        bg = colors.get("bg", "#120C20")
        border = colors.get("border", "#A876FF")
        accent = colors.get("accent", "#FFD166")
        text = colors.get("text", "#EFE4FF")
        hdr_bg = _blend_hex(bg, border, 0.16)
        btn_bg = _blend_hex(bg, border, 0.68)
        btn_hover = _blend_hex(bg, border, 0.85)
        btn_fg = _contrast_text_hex(btn_bg)
        border_soft = _blend_hex(bg, border, 0.50)

        self.setStyleSheet(
            f"""
            QDialog {{
                background-color: {bg};
                color: {text};
                font-family: "Microsoft YaHei UI", "Segoe UI";
            }}
            QTextBrowser {{
                background-color: {bg};
                color: {text};
                border: 1px solid {border_soft};
                border-radius: 8px;
                padding: 6px;
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
                background: none;
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 7px 18px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {btn_hover};
            }}
            """
        )
        self._header_frame.setStyleSheet(
            f"QFrame {{ background-color: {hdr_bg}; border: 1px solid {border}; border-radius: 10px; }}"
        )
        self._lbl_title.setStyleSheet(f"color: {accent}; background: transparent; border: none;")

    def _refresh_content(self) -> None:
        title = I18N.t("panel.user_manual_title", self._lang)
        self.setWindowTitle(title)
        self._lbl_title.setText(title)
        self._btn_close.setText(I18N.t("panel.user_manual_close_btn", self._lang))
        html_doc = render_user_manual_html(
            lang=self._lang,
            theme_colors=self._theme_colors,
            hotkey_toggle=self._hk_toggle,
            hotkey_wm=self._hk_wm,
            hotkey_hide=self._hk_hide,
            hotkey_open_panel=self._hk_open_panel,
        )
        self._browser.setHtml(html_doc)

    def set_language(self, lang: str) -> None:
        if lang in SUPPORTED_LANGS:
            self._lang = lang
            self._refresh_content()

    def set_theme_colors(self, colors: Dict[str, str]) -> None:
        self._theme_colors = resolve_theme_colors("custom", colors)
        self._apply_theme()
        self._refresh_content()

    def refresh_hotkeys(
        self,
        toggle_hotkey: str,
        wm_hotkey: str,
        hide_hotkey: str,
        open_panel_hotkey: str,
    ) -> None:
        self._hk_toggle = toggle_hotkey or "F8"
        self._hk_wm = wm_hotkey or "F9"
        self._hk_hide = hide_hotkey or "ESC"
        self._hk_open_panel = open_panel_hotkey or DEFAULT_OPEN_PANEL_HOTKEY
        self._refresh_content()

    def show_and_activate(self) -> None:
        self._refresh_content()
        self.showNormal()
        self.raise_()
        self.activateWindow()
