"""CLI & Overlay entry point for Warframe Simplified Chinese Riven OCR & Rating Tool.

Usage examples:
  1. Test OCR + Parser + Evaluator on a local screenshot:
     python main.py --image Warframe0000.jpg --preset reroll_center

  2. Preview the PyQt6 translucent rating overlay directly on a local screenshot:
     python main.py --image Warframe0000.jpg --preset reroll_center --show-overlay-preview

  3. Capture screen once and print JSON evaluation:
     python main.py --capture --preset auto

  4. Launch the click-through PyQt6 HUD overlay (F8 scan/refresh, ESC hide):
     python main.py --overlay
"""

from __future__ import annotations

import argparse
import ctypes
import faulthandler
import gc
import json
import sys
import threading
import time
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


def _enable_high_dpi_awareness() -> None:
    """Enable Windows Per-Monitor High-DPI awareness BEFORE mss or PyQt6 initializes.

    Ensures `mss` physical monitor coordinates match real screen pixels 1:1 even
    under Windows 125% / 150% / 200% display scaling.
    """
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_high_dpi_awareness()

import numpy as np

from src.capture import (
    RIVEN_ROI_PRESETS_1080P,
    ScreenCapturer,
    ScreenRegion,
    compute_frame_mse,
    detect_layout_crops,
    extract_change_signature,
    extract_crops_change_signature,
    extract_dual_crops,
)
from src.data_updater import safe_sync, start_background_sync_if_needed
from src.evaluator import (
    DualComparisonResult,
    ResultFingerprint,
    RivenEvaluation,
    RivenEvaluator,
    build_card_data_signature,
    build_reroll_data_signature,
    build_result_fingerprint,
    cards_stat_numbers_equivalent,
    compare_riven_evaluations,
    fingerprints_equivalent,
    is_complete_riven_frame,
    reroll_stat_numbers_equivalent,
)
from src.i18n import I18N, LANG_MODES, LANGUAGE_DISPLAY_NAMES, SUPPORTED_LANGS
from src.ocr_engine import OCRResult, RivenImagePreprocessor, RivenOCREngine
from src.riven_parser import RivenData, RivenParser
from src.wm_client import (
    WMFetchError,
    WMMarketReport,
    build_wm_search_url,
    fetch_wm_exact_riven_matches,
    fetch_wm_riven_stats,
    save_wm_recommendation_to_local,
)


def _debug_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [DEBUG] {msg}", flush=True)


def is_running_as_admin() -> bool:
    """Check whether current process has Windows Administrator elevation."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def get_windows_desktop_name() -> Optional[str]:
    """Return current Windows desktop name (should be 'Default' for interactive user desktop)."""
    if sys.platform != "win32":
        return None
    try:
        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        hdesk = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        buf = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_uint(0)
        if u32.GetUserObjectInformationW(hdesk, 2, buf, 512, ctypes.byref(needed)):
            return buf.value
    except Exception:
        pass
    return None


def build_arg_parser() -> argparse.ArgumentParser:
    preset_choices = sorted(list(RIVEN_ROI_PRESETS_1080P.keys()) + ["auto", "reroll_dual"])
    parser = argparse.ArgumentParser(
        description="《星际战甲》简体中文紫卡屏幕识别、评级与悬浮窗工具 (纯视觉 OCR，无内存读取)",
    )
    mode_group = parser.add_mutually_exclusive_group(required=False)
    mode_group.add_argument(
        "--image",
        type=str,
        help="读取本地紫卡截图路径进行 OCR、解析与评级测试",
    )
    mode_group.add_argument(
        "--capture",
        action="store_true",
        help="执行一次屏幕截图并输出识别与评级 JSON",
    )
    mode_group.add_argument(
        "--overlay",
        action="store_true",
        help="启动 PyQt6 无边框透明鼠标穿透悬浮窗与系统托盘（默认模式，自定义热键，右键托盘设置/退出）",
    )
    mode_group.add_argument(
        "--sync-data",
        action="store_true",
        help="立即从 WFCD / warframe.market 同步最新武器裂罅倾向性与词条数据到本地 data/ 目录",
    )
    mode_group.add_argument(
        "--wm-query",
        type=str,
        default=None,
        help="命令行测试主动拉取指定简体中文武器（如 托里德）的 WM 热门词条与参考价",
    )
    mode_group.add_argument(
        "--trigger",
        type=str,
        default=None,
        choices=["toggle", "wm", "hide", "open_panel", "lock", "export", "quit"],
        help="Linux/Wayland 快捷键 IPC 触发器：向已运行的裂罅透镜发送动作指令（toggle/wm/hide/open_panel/lock/export/quit）",
    )

    parser.add_argument(
        "--preset",
        type=str,
        default="auto",
        choices=preset_choices,
        help="截图区域预设：auto 自动兼容单卡与洗卡左右对比（默认 auto），或指定 reroll_center / reroll_dual / chat_link / mod_station / full",
    )
    parser.add_argument(
        "--region",
        type=str,
        default=None,
        help="自定义区域 'left,top,width,height'（优先级高于 --preset）",
    )
    parser.add_argument(
        "--no-scale",
        action="store_true",
        help="关闭分辨率自动缩放，直接使用原始像素坐标",
    )
    parser.add_argument(
        "--monitor",
        type=int,
        default=1,
        help="mss 显示器编号（1 为主显示器，默认 1）",
    )
    parser.add_argument(
        "--preprocess",
        type=str,
        default="riven_enhanced",
        choices=RivenImagePreprocessor.VALID_MODES,
        help="图像预处理模式（默认 riven_enhanced）",
    )
    parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="关闭低置信度自动回退 raw 模式",
    )
    parser.add_argument(
        "--disposition",
        type=float,
        default=None,
        help="手动指定武器裂罅倾向性（覆盖词典与自动校准，例如 1.30）",
    )
    parser.add_argument(
        "--weapons-db",
        type=str,
        default=None,
        help="自定义武器倾向性词典 JSON 路径（默认 data/weapons.json）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.85,
        help="自动连续监控截图检测间隔秒数（默认 0.85 秒，建议 0.8~1.0）",
    )
    parser.add_argument(
        "--mse-threshold",
        type=float,
        default=18.0,
        help="画面变化触发 OCR 的灰度 MSE 阈值（默认 18.0，越大越迟钝）",
    )
    parser.add_argument(
        "--miss-debounce",
        type=int,
        default=4,
        help="连续未检测到有效紫卡达到该次数后自动折叠为待命胶囊（默认 4 次）",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default=None,
        choices=list(LANG_MODES),
        help="临时覆盖界面语言模式：auto / zh_CN / en_US（默认读取 data/user_settings.json）",
    )
    parser.add_argument(
        "--debug-dir",
        type=str,
        default=None,
        help="保存裁剪原图与预处理图像到指定目录以便排查识别效果",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        help="将结构化 JSON 结果写入指定文件",
    )
    parser.add_argument(
        "--show-overlay-preview",
        action="store_true",
        help="配合 --image 或 --capture 使用：弹出右上角悬浮窗预览评级效果（按 ESC 关闭）",
    )
    return parser


def _resolve_custom_region(region_str: Optional[str]) -> Optional[ScreenRegion]:
    if not region_str:
        return None
    return ScreenRegion.from_string(region_str)


def _evaluate_crops_pipeline(
    full_or_cropped_image: np.ndarray,
    preset: str,
    custom_region: Optional[ScreenRegion],
    auto_scale: bool,
    ocr: RivenOCREngine,
    parser: RivenParser,
    evaluator: RivenEvaluator,
    fallback_to_raw: bool = True,
    disposition_override: Optional[float] = None,
    debug_dir: Optional[str] = None,
) -> Tuple[str, List[Tuple[str, OCRResult, RivenData, RivenEvaluation]], float]:
    """Run layout crop selection + OCR + Parse + Evaluate.

    Returns:
        (layout_label, [(card_label, ocr_res, riven_data, evaluation), ...], total_elapsed_ms)
    """
    t0 = time.perf_counter()
    results: List[Tuple[str, OCRResult, RivenData, RivenEvaluation]] = []

    def _run_one(label: str, img: np.ndarray, tag: str) -> Tuple[str, OCRResult, RivenData, RivenEvaluation]:
        sub_debug = str(Path(debug_dir) / tag) if debug_dir else None
        if sub_debug:
            ScreenCapturer.save_image(img, Path(sub_debug) / "captured_crop.png")
        ocr_res = ocr.recognize(img, fallback_to_raw=fallback_to_raw, debug_dir=sub_debug)
        riven_data = parser.parse(ocr_res.lines)
        eval_data = evaluator.evaluate(riven_data, disposition_override=disposition_override)
        return label, ocr_res, riven_data, eval_data

    # Case 1: explicit custom region or explicit non-auto single preset already cropped by caller
    if custom_region is not None or (preset not in ("auto", "reroll_dual")):
        results.append(_run_one("当前紫卡", full_or_cropped_image, "single"))
        layout_label = "单卡识别"
        return layout_label, results, (time.perf_counter() - t0) * 1000.0

    h, w = full_or_cropped_image.shape[:2]

    # Case 2: explicit dual comparison mode
    if preset == "reroll_dual":
        for idx, (lbl, crop) in enumerate(extract_dual_crops(full_or_cropped_image, auto_scale=auto_scale)):
            results.append(_run_one(lbl, crop, f"dual_{idx}"))
        return "洗卡双卡对比", results, (time.perf_counter() - t0) * 1000.0

    # Case 3: Auto-detect layout (supports single card, pre-cropped card, and dual reroll comparison)
    # If loaded image is already a tight single-card crop (aspect ratio tall or small resolution), OCR directly
    if w < 900 or h < 600:
        results.append(_run_one("当前紫卡", full_or_cropped_image, "direct"))
        return "单卡识别", results, (time.perf_counter() - t0) * 1000.0

    detected_layout, candidates = detect_layout_crops(full_or_cropped_image, auto_scale=auto_scale)
    if detected_layout == "reroll_dual":
        dual_entries = [_run_one(lbl, crop, f"dual_{idx}") for idx, (lbl, crop) in enumerate(candidates)]
        # Strict validation: both [当前] and [新循环] must pass 4-combo Riven validation
        if len(dual_entries) == 2 and dual_entries[0][2].is_valid and dual_entries[1][2].is_valid:
            return "洗卡双卡对比", dual_entries, (time.perf_counter() - t0) * 1000.0
        # If only the right active card passed strict validation, return it as single-card result
        if len(dual_entries) == 2 and dual_entries[1][2].is_valid:
            lbl_r, ocr_r, riven_r, eval_r = dual_entries[1]
            return "单卡识别", [("当前紫卡", ocr_r, riven_r, eval_r)], (time.perf_counter() - t0) * 1000.0
        # Otherwise fall back to single-card candidates
        center_reg = RIVEN_ROI_PRESETS_1080P["reroll_center"]
        chat_reg = RIVEN_ROI_PRESETS_1080P["chat_link"]
        assert center_reg is not None and chat_reg is not None
        if auto_scale:
            center_reg = center_reg.scale_for_resolution(w, h)
            chat_reg = chat_reg.scale_for_resolution(w, h)
        c_clamped = center_reg.clamp(w, h)
        ch_clamped = chat_reg.clamp(w, h)
        candidates = [
            ("当前紫卡", full_or_cropped_image[c_clamped.top : c_clamped.top + c_clamped.height, c_clamped.left : c_clamped.left + c_clamped.width].copy()),
            ("聊天/仓库预览", full_or_cropped_image[ch_clamped.top : ch_clamped.top + ch_clamped.height, ch_clamped.left : ch_clamped.left + ch_clamped.width].copy()),
        ]

    # Single-card mode: try primary candidate first; prefer a strictly valid Riven
    best_entry: Optional[Tuple[str, OCRResult, RivenData, RivenEvaluation]] = None
    for idx, (lbl, crop) in enumerate(candidates):
        entry = _run_one(lbl, crop, f"cand_{idx}")
        if best_entry is None or (entry[2].is_valid and not best_entry[2].is_valid) or (
            entry[2].is_valid == best_entry[2].is_valid and len(entry[2].stats) > len(best_entry[2].stats)
        ):
            best_entry = entry
        if entry[2].is_valid:
            break

    # Safety fallback: if single-card crops did not yield a valid Riven, probe dual reroll layout once
    if best_entry is None or not best_entry[2].is_valid:
        dual_probe = [
            _run_one(lbl, crop, f"probe_dual_{idx}")
            for idx, (lbl, crop) in enumerate(extract_dual_crops(full_or_cropped_image, auto_scale=auto_scale))
        ]
        if len(dual_probe) == 2 and dual_probe[0][2].is_valid and dual_probe[1][2].is_valid:
            return "洗卡双卡对比", dual_probe, (time.perf_counter() - t0) * 1000.0

    if best_entry is not None:
        results.append(best_entry)

    return "单卡识别", results, (time.perf_counter() - t0) * 1000.0


def run_once(args: argparse.Namespace) -> Tuple[str, List[Tuple[str, OCRResult, RivenData, RivenEvaluation]], float]:
    capturer = ScreenCapturer(monitor_index=args.monitor)
    custom_region = _resolve_custom_region(args.region)
    auto_scale = not args.no_scale
    crop_preset = None if args.preset in ("auto", "reroll_dual") else args.preset

    if args.image:
        image = capturer.load_image(
            args.image,
            region=custom_region,
            preset=crop_preset,
            auto_scale=auto_scale,
        )
        source_desc = f"local image: {args.image}"
    else:
        image, meta = capturer.capture_screen(
            region=custom_region,
            preset=crop_preset,
            auto_scale=auto_scale,
        )
        source_desc = (
            f"screen monitor={meta['monitor_index']} "
            f"({meta['monitor_width']}x{meta['monitor_height']}) "
            f"box=({meta['capture_left']},{meta['capture_top']},"
            f"{meta['capture_width']}x{meta['capture_height']})"
        )

    ocr = RivenOCREngine(preprocess_mode=args.preprocess)
    parser = RivenParser()
    evaluator = RivenEvaluator(weapons_db_path=args.weapons_db)

    layout_label, entries, total_ms = _evaluate_crops_pipeline(
        full_or_cropped_image=image,
        preset=args.preset,
        custom_region=custom_region,
        auto_scale=auto_scale,
        ocr=ocr,
        parser=parser,
        evaluator=evaluator,
        fallback_to_raw=not args.no_fallback,
        disposition_override=args.disposition,
        debug_dir=args.debug_dir,
    )

    if len(entries) == 1:
        _, ocr_res, riven_data, eval_data = entries[0]
        payload = {
            "source": source_desc,
            "layout": layout_label,
            "total_elapsed_ms": round(total_ms, 2),
            "ocr": {
                "preprocess_mode": ocr_res.preprocess_mode,
                "elapsed_ms": round(ocr_res.elapsed_ms, 2),
                "avg_confidence": round(ocr_res.avg_confidence, 4),
                "lines": ocr_res.lines,
            },
            "riven": riven_data.to_dict(),
            "evaluation": eval_data.to_dict(),
        }
    else:
        payload = {
            "source": source_desc,
            "layout": layout_label,
            "total_elapsed_ms": round(total_ms, 2),
            "comparison": compare_riven_evaluations(entries[0][3], entries[1][3]).to_dict()
            if len(entries) == 2
            else None,
            "cards": [
                {
                    "label": lbl,
                    "ocr": {
                        "preprocess_mode": ocr_res.preprocess_mode,
                        "elapsed_ms": round(ocr_res.elapsed_ms, 2),
                        "avg_confidence": round(ocr_res.avg_confidence, 4),
                        "lines": ocr_res.lines,
                    },
                    "riven": riven_data.to_dict(),
                    "evaluation": eval_data.to_dict(),
                }
                for lbl, ocr_res, riven_data, eval_data in entries
            ],
        }

    output_json = json.dumps(payload, ensure_ascii=False, indent=2)
    print(output_json)

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")

    return layout_label, entries, total_ms


def run_overlay_mode(args: argparse.Namespace) -> None:
    """Launch click-through PyQt6 HUD overlay, system tray, control panel & hot-reloadable hotkeys."""
    from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtWidgets import QApplication

    from src.audio_cue import AUDIO_CUE
    from src.card_exporter import export_riven_showcase
    from src.control_panel import RivenControlPanel
    from src.evaluator import analyze_reroll_synergy_alert, should_play_reroll_rare_sound
    from src.focus_watcher import SmartFocusWatcher
    from src.hotkey_manager import DynamicHotkeyManager, VK_ESCAPE
    from src.ocr_worker import (
        DEFAULT_AUTO_OCR_DEBOUNCE_MS,
        OCR_MODEL_MANAGER,
        AutoOCRModeDebouncer,
        F9PagePresenceWatcher,
        ManualCaptureWorker,
        MonitorWorker,
        OCRModelDownloadWorker,
        QThreadRetainer,
    )
    from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow
    from src.region_calibrator import RegionCalibratorOverlay
    from src.relic_prices import PRIME_PRICE_STORE
    from src.roi_manager import (
        MODE_CHAT_RIVEN,
        MODE_PAUSED,
        MODE_RELIC_REWARDS,
        MODE_REROLL,
        MODE_SINGLE_RIVEN,
        ROI_MANAGER,
        ModeStateMachine,
        format_mode_badge,
    )
    from src.settings_manager import load_user_settings, save_user_settings
    from src.tray_manager import TrayManager, load_app_icon
    from src.user_manual_dialog import UserManualDialog
    from src.wayland_support import (
        WaylandIPCServer,
        detect_wayland_capture_backend,
        detect_wayland_compositor,
        is_linux_wayland,
    )

    class DataSyncWorker(QThread):
        """Asynchronous worker triggered from Tray or Control Panel to refresh Riven DB & Prime prices."""

        sync_finished = pyqtSignal(dict)

        def run(self) -> None:
            _debug_log("DataSyncWorker(QThread) started manual DB + Prime prices update...")
            res = safe_sync(force=True)
            prime_res = PRIME_PRICE_STORE.sync_from_network()
            res["prime_status"] = prime_res.get("status")
            res["prime_count"] = prime_res.get("item_count", 0)
            _debug_log(
                f"DataSyncWorker(QThread) finished: riven={res.get('status')}, "
                f"prime={res.get('prime_status')} ({res.get('prime_count')} items)"
            )
            self.sync_finished.emit(res)

    class WMFetchWorker(QThread):
        """On-demand background thread invoked strictly by F9 / Control Panel to query Warframe.market."""

        fetched_ok = pyqtSignal(int, object)  # req_id, WMMarketReport
        fetched_err = pyqtSignal(int, str)    # req_id, user_message

        def __init__(
            self,
            req_id: int,
            weapon_name: str,
            weapon_slug: str,
            weapon_class: str,
            tiers_path: Path,
            positive_stats: Optional[List[str]] = None,
            negative_stat: Optional[str] = None,
            recommended_positives: Optional[List[str]] = None,
            recommended_negatives: Optional[List[str]] = None,
        ) -> None:
            super().__init__()
            self.req_id = req_id
            self.weapon_name = weapon_name
            self.weapon_slug = weapon_slug
            self.weapon_class = weapon_class
            self.tiers_path = tiers_path
            self.positive_stats = list(positive_stats or [])
            self.negative_stat = negative_stat
            self.recommended_positives = list(recommended_positives or [])
            self.recommended_negatives = list(recommended_negatives or [])

        def run(self) -> None:
            _debug_log(
                f"WMFetchWorker(QThread) started (req={self.req_id}, "
                f"weapon={self.weapon_name}, slug={self.weapon_slug}, "
                f"pos={self.positive_stats}, neg={self.negative_stat})"
            )
            try:
                if self.positive_stats:
                    report = fetch_wm_exact_riven_matches(
                        weapon_name=self.weapon_name,
                        weapon_slug=self.weapon_slug,
                        weapon_class=self.weapon_class,
                        positive_stats=self.positive_stats,
                        negative_stat=self.negative_stat,
                        recommended_positives=self.recommended_positives,
                        recommended_negatives=self.recommended_negatives,
                    )
                else:
                    report = fetch_wm_riven_stats(
                        weapon_name=self.weapon_name,
                        weapon_slug=self.weapon_slug,
                        weapon_class=self.weapon_class,
                    )
                saved = save_wm_recommendation_to_local(report, tiers_path=self.tiers_path)
                _debug_log(
                    f"WM fetch OK: {report.weapon_name} | match={report.match_level} | "
                    f"samples={report.sample_count} (online={report.online_count}) | "
                    f"top_listings={len(report.top_listings)} | {report.price_summary()} | saved_local={saved}"
                )
                self.fetched_ok.emit(self.req_id, report)
            except WMFetchError as exc:
                _debug_log(f"WM fetch warning: {exc}")
                self.fetched_err.emit(self.req_id, str(exc))
            except Exception as exc:
                _debug_log(f"WM fetch unexpected error: {exc!r}")
                self.fetched_err.emit(self.req_id, f"WM 拉取异常: {exc}（已保留本地推荐词条）")

    # Desktop & Admin privilege check (critical for interactive visibility & Warframe UIPI)
    desktop_name = get_windows_desktop_name()
    admin = is_running_as_admin()
    _debug_log(f"Windows Desktop: {desktop_name!r}, Admin privilege (IsUserAnAdmin): {admin}")
    if desktop_name and desktop_name.lower() != "default":
        print(
            f"[WARNING] 当前进程运行在隔离桌面 ({desktop_name}) 而非交互桌面 Default！"
            "在此环境下无法显示悬浮窗或接收你的键盘 F8 按键。请在本机资源管理器中双击 `启动悬浮窗_管理员.bat` 或在自己的 PowerShell 窗口中运行。",
            flush=True,
        )
    if sys.platform == "win32" and not admin:
        print(
            "[WARNING] 当前终端未以管理员身份运行。如果《星际战甲》或 WeGame 启动器以管理员权限运行，"
            "Windows UIPI 可能会拦截按键监听或悬浮窗置顶；若游戏内按 F8 无响应，请双击 `启动悬浮窗_管理员.bat` 以管理员身份运行。",
            flush=True,
        )
    elif sys.platform == "darwin":
        from src.macos_support import check_macos_permissions

        perm = check_macos_permissions()
        _debug_log(
            f"macOS runtime: {perm.macos_summary}, "
            f"screen_recording={perm.screen_recording}, accessibility={perm.accessibility}"
        )
        if not perm.all_granted:
            missing_txt = "；".join(perm.missing_items_zh())
            print(
                f"[WARNING] macOS 权限未完整授予：{missing_txt}。"
                "请打开「系统设置 -> 隐私与安全性」勾选允许 裂罅透镜 / 终端，然后重新启动软件。",
                flush=True,
            )

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("裂罅透镜")
    app.setApplicationDisplayName("裂罅透镜")
    if sys.platform != "win32":
        app.setDesktopFileName("riven-lens")
    # Keep running in system tray even when control panel is closed or overlay is hidden
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(load_app_icon())

    settings = load_user_settings()
    if args.lang in LANG_MODES:
        settings.language_mode = args.lang

    initial_lang_mode = settings.language_mode if settings.language_mode in LANG_MODES else "auto"
    initial_ui_lang = initial_lang_mode if initial_lang_mode in SUPPORTED_LANGS else "zh_CN"
    I18N.set_current_language(initial_ui_lang)

    AUDIO_CUE.set_enabled(settings.audio_cue_enabled)
    if settings.custom_rare_sound_path:
        ok_snd, err_snd = AUDIO_CUE.set_custom_rare_sound(settings.custom_rare_sound_path)
        if not ok_snd:
            _debug_log(f"Saved custom rare sound invalid ({err_snd}); falling back to default rare_drop.wav.")
            settings.custom_rare_sound_path = ""
    focus_watcher = SmartFocusWatcher(enabled=settings.auto_hide_on_focus_loss)

    riven_rx, riven_ry = settings.get_window_pos("riven")
    window = RivenOverlayWindow(
        click_through=True,
        lang=initial_ui_lang,
        toggle_hotkey=settings.hotkey_toggle_monitor,
        wm_hotkey=settings.hotkey_fetch_wm,
        hide_hotkey=settings.hotkey_hide_overlay,
        font_size_px=settings.overlay_font_size,
        pos_x_ratio=riven_rx,
        pos_y_ratio=riven_ry,
        wm_pos=settings.get_window_pos("wm"),
        synergy_pos=settings.get_window_pos("synergy"),
        relic_pos=settings.get_window_pos("relic"),
        auto_collapse_seconds=0,
        theme_colors=settings.theme_colors,
    )
    control_panel = RivenControlPanel(settings=settings, initial_ui_lang=initial_ui_lang)
    user_manual_dialog = UserManualDialog(settings=settings, lang=initial_ui_lang)
    capturer = ScreenCapturer(monitor_index=args.monitor)
    custom_region = _resolve_custom_region(args.region)
    auto_scale = not args.no_scale
    ocr = RivenOCREngine(preprocess_mode=args.preprocess)
    parser = RivenParser()
    evaluator = RivenEvaluator(weapons_db_path=args.weapons_db)

    # Silent 7-day auto-sync in background (never blocks startup; falls back to local cache on error)
    def _on_bg_data_synced(sync_res: dict) -> None:
        status = sync_res.get("status")
        if status == "updated":
            evaluator.reload_data()
            _debug_log(
                f"Background data sync completed: {sync_res.get('weapon_count')} weapons, "
                f"{sync_res.get('attribute_count')} attributes hot-reloaded."
            )
        elif status in ("fallback_cache", "failed"):
            _debug_log(f"Background data sync skipped ({status}): {sync_res.get('error')}")

    bg_sync = start_background_sync_if_needed(max_age_days=7, on_done=_on_bg_data_synced)
    if bg_sync is not None:
        _debug_log("Local data cache missing or >7 days old -> started silent background update.")
    else:
        _debug_log(f"Local data cache is fresh ({len(evaluator.weapons)} weapons loaded).")

    # Silently refresh full Prime parts platinum & Ducat price table on startup
    def _on_prime_prices_synced(p_res: dict) -> None:
        _debug_log(
            f"Background Prime prices sync finished: status={p_res.get('status')}, "
            f"items={p_res.get('item_count', 0)}"
        )

    PRIME_PRICE_STORE.start_background_sync(on_done=_on_prime_prices_synced)
    _debug_log(
        f"Prime price cache loaded ({len(PRIME_PRICE_STORE.items)} items); silent startup refresh started."
    )

    mode_machine = ModeStateMachine(initial_mode=MODE_PAUSED)
    roi_manager = ROI_MANAGER
    roi_manager.reload()
    auto_ocr_debouncer = AutoOCRModeDebouncer(delay_ms=DEFAULT_AUTO_OCR_DEBOUNCE_MS)
    thread_retainer = QThreadRetainer()

    def _clear_worker_slot(slot_key: str, target_worker: object) -> None:
        if state.get(slot_key) is target_worker:
            state[slot_key] = None

    # Coalesce rapid settings writes (font slider / theme scroll / geometry drag) to avoid disk stutter
    settings_save_timer = QTimer()
    settings_save_timer.setSingleShot(True)
    settings_save_timer.setInterval(250)

    def _flush_settings_save() -> None:
        if settings_save_timer.isActive():
            settings_save_timer.stop()
        save_user_settings(settings)

    def _schedule_settings_save() -> None:
        settings_save_timer.start()

    settings_save_timer.timeout.connect(_flush_settings_save)

    # Coalesce rapid overlay theme updates (e.g. fast mouse-wheel scrolling across theme presets)
    theme_apply_timer = QTimer()
    theme_apply_timer.setSingleShot(True)
    theme_apply_timer.setInterval(50)

    def _apply_pending_overlay_theme() -> None:
        window.set_theme_colors(settings.theme_colors)
        user_manual_dialog.set_theme_colors(settings.theme_colors)
        if not window.isVisible() and control_panel.isVisible():
            window.show_preview_card()

    theme_apply_timer.timeout.connect(_apply_pending_overlay_theme)

    state = {
        "monitoring": False,
        "session_id": 0,
        "worker": None,
        "manual_req_id": 0,
        "manual_worker": None,
        "f9_watch_id": 0,
        "f9_watcher": None,
        "wm_req_id": 0,
        "wm_worker": None,
        "db_worker": None,
        "ocr_model_worker": None,
        "ocr_model_req_id": 0,
        "ocr_model_pending_lang": None,
        "calibrator": None,
        "last_toggle_ts": 0.0,
        "last_wm_ts": 0.0,
        "esc_was_down": False,
        "lang_mode": initial_lang_mode,
        "active_lang": initial_ui_lang,
        "last_weapon_name": None,
        "last_weapon_name_en": "",
        "last_weapon_slug": "",
        "last_weapon_class": "rifle",
        "last_layout_label": "",
        "last_cards": [],
        "last_comparison": None,
        "last_elapsed_ms": 0.0,
        "last_wm_report": None,
        "last_rendered_signature": None,
        "tray": None,
    }

    hotkey_manager = DynamicHotkeyManager(logger=_debug_log)

    def _effective_lang(detected: Optional[str] = None) -> str:
        mode = state["lang_mode"]
        if mode in SUPPORTED_LANGS:
            return mode
        if detected in SUPPORTED_LANGS:
            return detected
        return state["active_lang"]

    def _sync_tray_and_panel() -> None:
        cur_mode = mode_machine.current_mode
        tray_obj = state["tray"]
        if tray_obj is not None:
            tray_obj.refresh_state(
                ui_lang=state["active_lang"],
                monitoring=(cur_mode != MODE_PAUSED),
                toggle_hotkey=settings.hotkey_toggle_monitor,
                wm_hotkey=settings.hotkey_fetch_wm,
                current_mode=cur_mode,
                hotkeys_locked=hotkey_manager.hotkeys_locked,
            )
        control_panel.set_work_mode(cur_mode)
        control_panel.set_hotkeys_locked_ui(hotkey_manager.hotkeys_locked)

    def _display_weapon_for_lang(lang: str) -> str:
        if lang not in ("zh_CN", "zh_TW") and state["last_weapon_name_en"]:
            return state["last_weapon_name_en"]
        return state["last_weapon_name"] or ""

    ocr_model_debounce_timer = QTimer()
    ocr_model_debounce_timer.setSingleShot(True)
    ocr_model_debounce_timer.setInterval(350)

    def _ensure_ocr_model_for_language(lang_code: str) -> None:
        """Check local RapidOCR cache for `lang_code`; hot-reload immediately or download in background."""
        state["ocr_model_req_id"] += 1
        req_id = state["ocr_model_req_id"]
        status = OCR_MODEL_MANAGER.check_model_status(lang_code)
        lang_disp = LANGUAGE_DISPLAY_NAMES.get(lang_code, lang_code)
        if status.is_ready:
            rec_path = str(status.rec_model_path) if status.rec_model_path else None
            keys_path = str(status.rec_keys_path) if status.rec_keys_path else None
            if status.is_builtin:
                ocr.reload_for_language(lang_code, rec_model_path=None, rec_keys_path=None)
                control_panel.set_ocr_model_status("")
                _debug_log(f"RapidOCR model hot-reloaded for {lang_code}: <builtin ch_PP-OCRv4>")
            else:
                ok = ocr.reload_for_language(lang_code, rec_model_path=rec_path, rec_keys_path=keys_path)
                if ok:
                    control_panel.set_ocr_model_status(
                        I18N.t("panel.ocr_model_ready", state["active_lang"], lang_name=lang_disp)
                    )
                    _debug_log(f"RapidOCR model hot-reloaded for {lang_code}: {rec_path}")
            return

        # Missing external ONNX rec model + dict -> download asynchronously without blocking UI
        control_panel.set_ocr_model_status(
            I18N.t("panel.ocr_model_downloading", state["active_lang"], lang_name=lang_disp, pct=0)
        )
        dl_worker = OCRModelDownloadWorker(lang_code, manager=OCR_MODEL_MANAGER)

        def _on_dl_progress(target_lang: str, pct: int) -> None:
            if req_id != state["ocr_model_req_id"] or target_lang != state["active_lang"]:
                return
            l_name = LANGUAGE_DISPLAY_NAMES.get(target_lang, target_lang)
            control_panel.set_ocr_model_status(
                I18N.t("panel.ocr_model_downloading", state["active_lang"], lang_name=l_name, pct=pct)
            )

        def _on_dl_succeeded(target_lang: str, rec_p: object, keys_p: object) -> None:
            if req_id != state["ocr_model_req_id"] or target_lang != state["active_lang"]:
                _debug_log(
                    f"Ignored stale RapidOCR download for {target_lang} (active_lang={state['active_lang']}, req={req_id} != {state['ocr_model_req_id']})"
                )
                return
            l_name = LANGUAGE_DISPLAY_NAMES.get(target_lang, target_lang)
            rec_s = str(rec_p) if rec_p else None
            keys_s = str(keys_p) if keys_p else None
            ocr.reload_for_language(
                target_lang,
                rec_model_path=rec_s,
                rec_keys_path=keys_s,
            )
            control_panel.set_ocr_model_status(
                I18N.t("panel.ocr_model_ready", state["active_lang"], lang_name=l_name)
            )
            _debug_log(f"RapidOCR model hot-reloaded for {target_lang}: {rec_s or '<builtin>'}")

        def _on_dl_failed(target_lang: str, err: str) -> None:
            if req_id != state["ocr_model_req_id"] or target_lang != state["active_lang"]:
                return
            l_name = LANGUAGE_DISPLAY_NAMES.get(target_lang, target_lang)
            control_panel.set_ocr_model_status(
                I18N.t("panel.ocr_model_failed", state["active_lang"], lang_name=l_name, err=err),
                is_error=True,
            )
            _debug_log(f"RapidOCR model download failed for {target_lang}: {err}")

        dl_worker.progress_changed.connect(_on_dl_progress)
        dl_worker.download_succeeded.connect(_on_dl_succeeded)
        dl_worker.download_failed.connect(_on_dl_failed)
        prev_dl = state["ocr_model_worker"]
        if prev_dl is not None:
            thread_retainer.retire(prev_dl)
        state["ocr_model_worker"] = dl_worker
        thread_retainer.track(dl_worker, on_finished=lambda w=dl_worker: _clear_worker_slot("ocr_model_worker", w))
        dl_worker.start()

    def _on_ocr_model_debounce_timeout() -> None:
        pending = state.get("ocr_model_pending_lang")
        if isinstance(pending, str) and pending == state["active_lang"]:
            _ensure_ocr_model_for_language(pending)

    ocr_model_debounce_timer.timeout.connect(_on_ocr_model_debounce_timeout)

    # Lightweight Win32 GetAsyncKeyState poller for plain ESC — ONLY runs while monitoring/visible
    # AND hide hotkey is plain ESC, so idle paused state uses 0% CPU and Warframe ESC is never blocked.
    esc_timer = QTimer()
    esc_timer.setInterval(60)

    def _sync_esc_timer() -> None:
        need_esc = (
            hotkey_manager.active_hide is not None
            and hotkey_manager.active_hide.is_plain_esc
            and (state["monitoring"] or window.has_any_visible_overlay())
        )
        if need_esc:
            if not esc_timer.isActive():
                esc_timer.start()
        else:
            if esc_timer.isActive():
                esc_timer.stop()
            state["esc_was_down"] = False

    def _stop_f9_page_watcher(release_hold: bool = True) -> None:
        state["f9_watch_id"] += 1
        watcher = state["f9_watcher"]
        state["f9_watcher"] = None
        if watcher is not None:
            # Non-blocking cooperative retire: NEVER block GUI thread or destroy running QThread!
            thread_retainer.retire(watcher, stop_fn=watcher.stop_watching)
        if release_hold:
            window.set_f9_page_hold(False)

    def _start_f9_page_watcher(work_mode: str, anchor_sig: Optional[np.ndarray]) -> None:
        """Keep overlay pinned after F9 capture until the user exits the Riven or Void Relic page."""
        _stop_f9_page_watcher(release_hold=False)
        window.set_f9_page_hold(True)
        if anchor_sig is None:
            return
        wid = state["f9_watch_id"]
        watcher = F9PagePresenceWatcher(
            watch_id=wid,
            work_mode=work_mode,
            anchor_signature=anchor_sig,
            capturer=capturer,
            roi_manager=roi_manager,
            logger=_debug_log,
        )

        def on_page_exited(exited_wid: int, exited_mode: str) -> None:
            if exited_wid != state["f9_watch_id"]:
                return
            state["f9_watcher"] = None
            state["last_rendered_signature"] = None
            _debug_log(
                f"F9 captured page exited (mode={exited_mode}, watch_id={exited_wid}) -> collapsing overlay to pill."
            )
            window.release_f9_page_hold(collapse_now=True)

        watcher.page_exited.connect(on_page_exited)
        state["f9_watcher"] = watcher
        thread_retainer.track(watcher, on_finished=lambda w=watcher: _clear_worker_slot("f9_watcher", w))
        watcher.start()

    def _stop_auto_worker_only() -> None:
        auto_ocr_debouncer.cancel()
        state["session_id"] += 1  # Invalidate any in-flight MonitorWorker signals
        worker = state["worker"]
        state["worker"] = None
        if worker is not None:
            # Non-blocking cooperative retire: 0ms GUI freeze & zero QThread destruction crashes!
            thread_retainer.retire(worker, stop_fn=worker.stop_monitoring)

    def stop_monitor(reason: str = "Paused") -> None:
        was_active = mode_machine.current_mode != MODE_PAUSED
        mode_machine.set_mode(MODE_PAUSED)
        state["monitoring"] = False
        state["manual_req_id"] += 1  # Discard any in-flight F9 capture result
        state["last_rendered_signature"] = None
        _stop_f9_page_watcher(release_hold=True)
        _stop_auto_worker_only()
        window.set_work_mode(MODE_PAUSED, pending_debounce=False)
        if window.has_any_visible_overlay():
            window.hide_all_overlays()
        _sync_esc_timer()
        _sync_tray_and_panel()
        if was_active:
            _debug_log(f"Work mode -> PAUSED (reason={reason})")

    def _start_auto_ocr_worker(target_mode: str) -> None:
        """Actually launch MonitorWorker after the 2-second mode-switch debounce completes."""
        if mode_machine.current_mode != target_mode or not mode_machine.is_auto_ocr(target_mode):
            return
        sid = state["session_id"]
        window.set_work_mode(target_mode, pending_debounce=False)
        window.show_monitoring_waiting(keep_pill_if_collapsed=True)
        _debug_log(f"2s debounce elapsed -> starting MonitorWorker(mode={target_mode}, session={sid})")

        worker = MonitorWorker(
            session_id=sid,
            work_mode=target_mode,
            roi_manager=roi_manager,
            capturer=capturer,
            ocr=ocr,
            parser=parser,
            evaluator=evaluator,
            fallback_to_raw=not args.no_fallback,
            disposition_override=args.disposition,
            debug_dir=args.debug_dir,
            interval_sec=args.interval,
            mse_threshold=args.mse_threshold,
            miss_debounce=max(2, int(args.miss_debounce or settings.miss_collapse_count)),
            logger=_debug_log,
        )

        def on_cards(
            session_id: int,
            layout_lbl: str,
            cards: object,
            comparison: object,
            elapsed_ms: float,
        ) -> None:
            if not mode_machine.is_auto_ocr() or session_id != state["session_id"]:
                return
            if window.is_f9_page_held:
                window.set_f9_page_hold(False)
            _apply_recognized_cards(layout_lbl, list(cards), comparison, elapsed_ms)  # type: ignore[arg-type]

        def on_waiting(session_id: int, _msg: str) -> None:
            if not mode_machine.is_auto_ocr() or session_id != state["session_id"]:
                return
            state["last_rendered_signature"] = None
            window.set_f9_page_hold(False)
            window.show_monitoring_waiting(keep_pill_if_collapsed=True)

        def on_fail(session_id: int, err: str) -> None:
            if not mode_machine.is_auto_ocr() or session_id != state["session_id"]:
                return
            window.show_error(err)

        worker.cards_updated.connect(on_cards)
        worker.waiting_for_riven.connect(on_waiting)
        worker.failed.connect(on_fail)
        state["worker"] = worker
        thread_retainer.track(worker, on_finished=lambda w=worker: _clear_worker_slot("worker", w))
        _sync_esc_timer()
        worker.start()

    auto_ocr_debouncer.debounced_ready.connect(_start_auto_ocr_worker)

    def _apply_recognized_cards(
        layout_lbl: str,
        card_list: List[EvaluatedRivenCard],
        comparison: object,
        elapsed_ms: float,
        force_redraw: bool = False,
        play_audio: bool = True,
    ) -> None:
        if not card_list:
            return

        # Strict completeness gate & structured Data-Signature dedup (zero redraw on unchanged data)
        if len(card_list) == 2:
            if not force_redraw and not (
                is_complete_riven_frame(card_list[0].riven, card_list[0].evaluation, min_stats=2)
                and is_complete_riven_frame(card_list[1].riven, card_list[1].evaluation, min_stats=2)
            ):
                return
            data_sig: object = build_reroll_data_signature(card_list[0], card_list[1])
        else:
            if not force_redraw and not is_complete_riven_frame(
                card_list[0].riven, card_list[0].evaluation, min_stats=2
            ):
                return
            data_sig = build_card_data_signature(card_list[0].riven, card_list[0].evaluation)

        if (
            not force_redraw
            and state["last_rendered_signature"] is not None
            and window.display_mode == "cards"
            and window.isVisible()
        ):
            prev_n, prev_sig = state["last_rendered_signature"]
            if prev_n == len(card_list):
                is_same = (
                    reroll_stat_numbers_equivalent(data_sig, prev_sig)
                    if len(card_list) == 2
                    else cards_stat_numbers_equivalent(data_sig, prev_sig)
                )
                if is_same:
                    return

        primary_card = card_list[-1]
        detected_lang = primary_card.riven.detected_language
        ui_lang = _effective_lang(detected_lang)
        if ui_lang != state["active_lang"]:
            state["active_lang"] = ui_lang
            control_panel.retranslate_ui(ui_lang)
        window.set_language(ui_lang)

        new_weapon = primary_card.evaluation.weapon_name or primary_card.riven.weapon_name
        if new_weapon and new_weapon != state["last_weapon_name"]:
            window.clear_wm_panel()
            state["last_wm_report"] = None
        if new_weapon:
            state["last_weapon_name"] = new_weapon
            state["last_weapon_name_en"] = primary_card.evaluation.weapon_name_en
            state["last_weapon_slug"] = primary_card.evaluation.weapon_slug
            state["last_weapon_class"] = primary_card.evaluation.weapon_class
            control_panel.set_current_weapon(_display_weapon_for_lang(ui_lang))
        state["last_layout_label"] = layout_lbl
        state["last_cards"] = card_list
        state["last_comparison"] = comparison
        state["last_elapsed_ms"] = elapsed_ms
        state["last_rendered_signature"] = (len(card_list), data_sig)

        window.update_cards(
            card_list,
            elapsed_ms=elapsed_ms,
            layout_label=layout_lbl,
            comparison=comparison,  # type: ignore[arg-type]
            lang=ui_lang,
            force_redraw=force_redraw,
        )
        # Audio cues are strictly limited to dual-card (`reroll`) mode when an UNLOCKED S positive
        # or UNLOCKED S positive-benefit negative stat appears on the newly rolled right card.
        if play_audio and should_play_reroll_rare_sound(card_list, mode_machine.current_mode):
            AUDIO_CUE.play_rare_drop_cue()
        _sync_tray_and_panel()

    def set_work_mode(new_mode: str, source: str = "F8") -> None:
        """Switch to one of the 5 work modes (`single_riven`, `reroll`, `chat_riven`, `relic_rewards`, `paused`)."""
        if new_mode == MODE_PAUSED:
            stop_monitor(reason=source)
            return

        _stop_f9_page_watcher(release_hold=True)
        _stop_auto_worker_only()
        state["manual_req_id"] += 1  # Discard any in-flight F9 capture from previous mode
        state["last_rendered_signature"] = None
        mode_machine.set_mode(new_mode)
        state["monitoring"] = True
        window.set_language(state["active_lang"])
        _sync_tray_and_panel()
        _debug_log(
            f"Work mode switched -> {new_mode} ({format_mode_badge(new_mode, 'zh_CN', settings.hotkey_fetch_wm)}) [source={source}]"
        )

        # Modes 3 & 4: ZERO background OCR — wait quietly for explicit F9 capture trigger!
        if mode_machine.is_manual_f9_capture(new_mode):
            window.set_work_mode(new_mode, pending_debounce=False)
            window.show_manual_mode_standby(new_mode)
            _sync_esc_timer()
            return

        # Modes 1 & 2 (`single_riven` / `reroll`): arm 2.0-second debounce timer before launching OCR!
        window.show_debounce_waiting(new_mode)
        _sync_esc_timer()
        auto_ocr_debouncer.schedule(new_mode)
        _debug_log(f"Armed 2.0s debounce timer for {new_mode} (prevents wasted OCR on rapid F8 cycling).")

    def start_monitor() -> None:
        set_work_mode(MODE_SINGLE_RIVEN, source="LaunchPref")

    def handle_language_change(new_mode: str, persist: bool = True) -> None:
        if new_mode not in LANG_MODES:
            return
        state["lang_mode"] = new_mode
        settings.language_mode = new_mode
        if persist:
            _schedule_settings_save()

        if new_mode in SUPPORTED_LANGS:
            state["active_lang"] = new_mode
        elif state["last_cards"]:
            det = state["last_cards"][-1].riven.detected_language
            if det in SUPPORTED_LANGS:
                state["active_lang"] = det
        ui_lang = state["active_lang"]
        I18N.set_current_language(ui_lang)
        window.set_language(ui_lang)
        control_panel.retranslate_ui(ui_lang)
        user_manual_dialog.set_language(ui_lang)
        _sync_tray_and_panel()
        _debug_log(f"UI language mode changed -> mode={new_mode}, active_lang={ui_lang}")
        if ui_lang in ("zh_CN", "en_US"):
            ocr_model_debounce_timer.stop()
            state["ocr_model_pending_lang"] = None
            _ensure_ocr_model_for_language(ui_lang)
        else:
            state["ocr_model_req_id"] += 1
            state["ocr_model_pending_lang"] = ui_lang
            ocr_model_debounce_timer.start()

        if window.isVisible() and state["last_cards"]:
            window.update_cards(
                state["last_cards"],
                elapsed_ms=state["last_elapsed_ms"],
                layout_label=state["last_layout_label"],
                comparison=state["last_comparison"],
                lang=ui_lang,
                force_redraw=True,
            )
            if state["last_wm_report"] is not None:
                window.show_wm_report(state["last_wm_report"], weapon_display=_display_weapon_for_lang(ui_lang))
        elif window.isVisible() and state["monitoring"]:
            window.show_monitoring_waiting()

    def handle_start_monitoring_pref(enabled: bool) -> None:
        settings.start_monitoring_on_launch = bool(enabled)
        save_user_settings(settings)
        _debug_log(f"Preference saved: start_monitoring_on_launch={settings.start_monitoring_on_launch}")

    def handle_apply_hotkeys(
        toggle_str: str,
        wm_str: str,
        hide_str: str,
        open_panel_str: Optional[str] = None,
    ) -> None:
        op_str = open_panel_str or settings.hotkey_open_panel
        report = hotkey_manager.apply_hotkeys(toggle_str, wm_str, hide_str, op_str)
        if report.ok:
            settings.hotkey_toggle_monitor = report.toggle_hotkey
            settings.hotkey_fetch_wm = report.wm_hotkey
            settings.hotkey_hide_overlay = report.hide_hotkey
            settings.hotkey_open_panel = report.open_panel_hotkey
            save_user_settings(settings)
            window.set_hotkey_labels(report.toggle_hotkey, report.wm_hotkey, report.hide_hotkey)
            user_manual_dialog.refresh_hotkeys(
                report.toggle_hotkey,
                report.wm_hotkey,
                report.hide_hotkey,
                report.open_panel_hotkey,
            )
            _sync_esc_timer()
            _sync_tray_and_panel()
            if window.isVisible() and state["last_cards"]:
                window.update_cards(
                    state["last_cards"],
                    elapsed_ms=state["last_elapsed_ms"],
                    layout_label=state["last_layout_label"],
                    comparison=state["last_comparison"],
                    lang=state["active_lang"],
                    force_redraw=True,
                )
            elif window.isVisible() and state["monitoring"]:
                window.show_monitoring_waiting()
            _debug_log(
                f"Hotkeys hot-reloaded & saved: toggle={report.toggle_hotkey}, "
                f"wm={report.wm_hotkey}, hide={report.hide_hotkey}, open_panel={report.open_panel_hotkey}"
            )
        control_panel.show_hotkey_report(report, show_dialog_on_issue=True)

    def handle_toggle(source: str) -> None:
        now = time.monotonic()
        if now - state["last_toggle_ts"] < 0.28:
            return
        state["last_toggle_ts"] = now
        next_mode = mode_machine.cycle_next()
        _debug_log(f"Mode cycle hotkey triggered ({settings.hotkey_toggle_monitor}, source={source}) -> {next_mode}")
        set_work_mode(next_mode, source=source)

    def handle_manual_f9_capture(work_mode: str, source: str) -> None:
        """One-shot F9 capture & OCR for `chat_riven` or `relic_rewards`."""
        existing = state["manual_worker"]
        if QThreadRetainer.is_running(existing):
            _debug_log("Manual capture already running; ignoring duplicate F9.")
            return
        state["manual_worker"] = None
        state["manual_req_id"] += 1
        req_id = state["manual_req_id"]
        _debug_log(f"Manual F9 capture started: mode={work_mode}, source={source}, req={req_id}")

        m_worker = ManualCaptureWorker(
            req_id=req_id,
            work_mode=work_mode,
            capturer=capturer,
            roi_manager=roi_manager,
            ocr=ocr,
            parser=parser,
            evaluator=evaluator,
            prime_prices=PRIME_PRICE_STORE,
            fallback_to_raw=not args.no_fallback,
            disposition_override=args.disposition,
        )

        def on_chat_ready(rid: int, cards_obj: object, elapsed_ms: float) -> None:
            if rid != state["manual_req_id"] or mode_machine.current_mode != work_mode:
                return
            cards_list: List[EvaluatedRivenCard] = list(cards_obj)  # type: ignore[arg-type]
            if cards_list and cards_list[0].riven.is_valid:
                # Hold overlay indefinitely until the player exits this chat Riven screen
                window.set_f9_page_hold(True)
                _apply_recognized_cards(
                    "聊天链接紫卡",
                    cards_list,
                    None,
                    elapsed_ms,
                    force_redraw=True,
                )
                _start_f9_page_watcher(MODE_CHAT_RIVEN, m_worker.captured_anchor_signature)
                # Automatically fetch exact-stat WM price for the captured chat-link Riven!
                handle_fetch_wm("ChatRivenAutoWM")
            else:
                _stop_f9_page_watcher(release_hold=True)
                err_detail = cards_list[0].riven.validation_error if cards_list else "未识别到紫卡"
                window.show_error(f"聊天紫卡未识别：{err_detail}")

        def on_relic_ready(rid: int, lines_obj: object, elapsed_ms: float) -> None:
            if rid != state["manual_req_id"] or mode_machine.current_mode != work_mode:
                return
            matches = list(lines_obj)  # type: ignore[arg-type]
            anchor_ratios = m_worker.captured_anchor_ratios
            if matches:
                # Hold Void Relic overlay open until the player leaves the 4-choice reward screen
                window.set_f9_page_hold(True)
                window.show_relic_rewards(
                    matches,
                    elapsed_ms=elapsed_ms,
                    anchor_ratios=anchor_ratios,
                )
                _start_f9_page_watcher(MODE_RELIC_REWARDS, m_worker.captured_anchor_signature)
            else:
                _stop_f9_page_watcher(release_hold=True)
                window.show_relic_rewards(
                    matches,
                    elapsed_ms=elapsed_ms,
                    anchor_ratios=anchor_ratios,
                )

        def on_manual_fail(rid: int, err: str) -> None:
            if rid != state["manual_req_id"] or mode_machine.current_mode != work_mode:
                return
            _stop_f9_page_watcher(release_hold=True)
            window.show_error(err)

        m_worker.chat_riven_ready.connect(on_chat_ready)
        m_worker.relic_ready.connect(on_relic_ready)
        m_worker.failed.connect(on_manual_fail)
        state["manual_worker"] = m_worker
        thread_retainer.track(m_worker, on_finished=lambda w=m_worker: _clear_worker_slot("manual_worker", w))
        m_worker.start()

    def handle_f9_trigger(source: str) -> None:
        """Differentiated F9 dispatcher based on active work mode."""
        action = mode_machine.f9_action_type()
        if action == "capture_chat_riven":
            handle_manual_f9_capture(MODE_CHAT_RIVEN, source)
        elif action == "capture_relic_rewards":
            handle_manual_f9_capture(MODE_RELIC_REWARDS, source)
        else:
            handle_fetch_wm(source)

    def handle_open_calibrator() -> None:
        """Launch fullscreen single-mode visual region calibrator (pausing global hotkeys cleanly)."""
        if state["calibrator"] is not None:
            return
        prev_mode = mode_machine.current_mode
        stop_monitor(reason="Calibration")
        control_panel.hide()
        hotkey_manager.unregister_all()

        init_cal_mode = prev_mode if prev_mode != MODE_PAUSED else MODE_SINGLE_RIVEN
        cal = RegionCalibratorOverlay(
            roi_manager=roi_manager,
            capturer=capturer,
            ocr=ocr,
            initial_mode=init_cal_mode,
            lang=state["active_lang"],
        )

        def _finish_cal(saved: bool) -> None:
            state["calibrator"] = None
            if saved:
                roi_manager.reload()
                _debug_log("ROI profiles saved & hot-reloaded from RegionCalibratorOverlay.")
            hotkey_manager.apply_hotkeys(
                settings.hotkey_toggle_monitor,
                settings.hotkey_fetch_wm,
                settings.hotkey_hide_overlay,
                settings.hotkey_open_panel,
            )
            control_panel.show_and_activate()

        cal.calibration_saved.connect(lambda: _finish_cal(True))
        cal.calibration_cancelled.connect(lambda: _finish_cal(False))
        state["calibrator"] = cal
        cal.showFullScreen()
        cal.raise_()
        cal.activateWindow()

    def handle_fetch_wm(source: str, manual_weapon: str = "") -> None:
        now = time.monotonic()
        if source != "ControlPanel" and now - state["last_wm_ts"] < 0.8:
            return
        state["last_wm_ts"] = now
        _debug_log(f"WM-fetch triggered (source={source}, manual_weapon={manual_weapon!r})")
        ui_lang = state["active_lang"]

        manual_clean = (manual_weapon or "").strip()
        prev_weapon = state["last_weapon_name"]
        if manual_clean:
            canon, w_cls, _, _ = evaluator.resolve_weapon(manual_clean, [])
            weapon_name = canon or manual_clean
            weapon_slug = evaluator.resolve_wm_slug(weapon_name)
            weapon_class = w_cls or "rifle"
            w_meta = evaluator.weapons.get(weapon_name) or {}
            weapon_en = str(w_meta.get("en_name") or "")
            state["last_weapon_name"] = weapon_name
            state["last_weapon_name_en"] = weapon_en
            state["last_weapon_slug"] = weapon_slug
            state["last_weapon_class"] = weapon_class
        else:
            weapon_name = state["last_weapon_name"]
            if not weapon_name:
                msg = I18N.t("wm_need_weapon", ui_lang)
                _debug_log("WM fetch requested before any weapon was recognized.")
                if source == "ControlPanel":
                    control_panel.set_wm_error(msg)
                else:
                    window.show_wm_notice(msg, is_warning=True)
                return
            weapon_slug = state["last_weapon_slug"] or evaluator.resolve_wm_slug(weapon_name)
            weapon_class = state["last_weapon_class"] or "rifle"

        # Extract current card's positive/negative stats for exact-stat WM matching when weapon matches
        pos_stats: List[str] = []
        neg_stat: Optional[str] = None
        rec_pos: List[str] = []
        rec_neg: List[str] = []
        if state["last_cards"] and (not manual_clean or weapon_name == prev_weapon):
            active_card = state["last_cards"][-1]
            pos_stats = [s.stat_name for s in active_card.riven.positive_stats]
            if active_card.riven.negative_stats:
                neg_stat = active_card.riven.negative_stats[0].stat_name
            rec_pos = list(active_card.evaluation.recommended_positives)
            rec_neg = list(active_card.evaluation.recommended_negatives)

        current_wm = state["wm_worker"]
        if QThreadRetainer.is_running(current_wm):
            _debug_log("WM fetch already in progress; ignoring duplicate request.")
            return
        state["wm_worker"] = None

        state["wm_req_id"] += 1
        req_id = state["wm_req_id"]
        weapon_disp = _display_weapon_for_lang(ui_lang) or weapon_name
        control_panel.set_wm_loading(weapon_disp)
        if source not in ("ControlPanel", "ChatRivenAutoWM") and mode_machine.is_auto_ocr() and state["last_cards"]:
            # In single_riven/reroll mode, pressing F9 pins the card + WM popup until exiting the Riven screen
            window.set_f9_page_hold(True)
        if source != "ControlPanel" or window.isVisible():
            window.show_wm_loading(weapon_disp)

        wm_worker = WMFetchWorker(
            req_id=req_id,
            weapon_name=weapon_name,
            weapon_slug=weapon_slug,
            weapon_class=weapon_class,
            tiers_path=evaluator.tiers_path,
            positive_stats=pos_stats,
            negative_stat=neg_stat,
            recommended_positives=rec_pos,
            recommended_negatives=rec_neg,
        )

        def on_wm_ok(rid: int, report_obj: object) -> None:
            if rid != state["wm_req_id"]:
                return
            report: WMMarketReport = report_obj  # type: ignore[arg-type]
            state["last_wm_report"] = report
            cur_lang = state["active_lang"]
            w_disp = _display_weapon_for_lang(cur_lang) or report.weapon_name
            if report.saved_to_local:
                evaluator.reload_recommendations()
                if state["last_cards"] and window.isVisible():
                    refreshed_cards = [
                        EvaluatedRivenCard(
                            label=c.label,
                            riven=c.riven,
                            evaluation=evaluator.evaluate(c.riven, disposition_override=args.disposition),
                            avg_confidence=c.avg_confidence,
                        )
                        for c in state["last_cards"]
                    ]
                    state["last_cards"] = refreshed_cards
                    comp = (
                        compare_riven_evaluations(refreshed_cards[0].evaluation, refreshed_cards[1].evaluation)
                        if len(refreshed_cards) == 2
                        else None
                    )
                    state["last_comparison"] = comp
                    window.update_cards(
                        refreshed_cards,
                        elapsed_ms=state["last_elapsed_ms"],
                        layout_label=state["last_layout_label"],
                        comparison=comp,
                        lang=cur_lang,
                        force_redraw=True,
                    )
            control_panel.set_wm_report(report, weapon_display=w_disp)
            if source != "ControlPanel" or window.isVisible():
                window.show_wm_report(report, weapon_display=w_disp)

        def on_wm_err(rid: int, err_msg: str) -> None:
            if rid != state["wm_req_id"]:
                return
            control_panel.set_wm_error(err_msg)
            if source != "ControlPanel" or window.isVisible():
                window.show_wm_notice(err_msg, is_warning=True)

        wm_worker.fetched_ok.connect(on_wm_ok)
        wm_worker.fetched_err.connect(on_wm_err)
        state["wm_worker"] = wm_worker
        thread_retainer.track(wm_worker, on_finished=lambda w=wm_worker: _clear_worker_slot("wm_worker", w))
        wm_worker.start()

    def handle_open_wm_browser(manual_weapon: str = "") -> None:
        ui_lang = state["active_lang"]
        manual_clean = (manual_weapon or "").strip()
        last_rep: Optional[WMMarketReport] = state["last_wm_report"]

        # If we already fetched an exact WM search for this weapon, open its exact URL
        if last_rep is not None and last_rep.search_url:
            if not manual_clean or manual_clean in (last_rep.weapon_name, state["last_weapon_name_en"]):
                _debug_log(f"Opening WM exact search URL in browser: {last_rep.search_url}")
                webbrowser.open(last_rep.search_url)
                return

        if manual_clean:
            canon, _, _, _ = evaluator.resolve_weapon(manual_clean, [])
            w_name = canon or manual_clean
            slug = evaluator.resolve_wm_slug(w_name)
        else:
            w_name = state["last_weapon_name"] or ""
            slug = state["last_weapon_slug"] or evaluator.resolve_wm_slug(w_name)

        if not slug:
            control_panel.set_wm_error(I18N.t("wm_need_weapon", ui_lang))
            return

        pos_stats: List[str] = []
        neg_stat: Optional[str] = None
        if state["last_cards"] and (not manual_clean or w_name == state["last_weapon_name"]):
            active_card = state["last_cards"][-1]
            pos_stats = [s.stat_name for s in active_card.riven.positive_stats[:3]]
            if active_card.riven.negative_stats and active_card.evaluation.matched_rec_neg:
                neg_stat = active_card.riven.negative_stats[0].stat_name

        url = build_wm_search_url(slug, positive_stats=pos_stats, negative_stat=neg_stat)
        _debug_log(f"Opening WM search URL in browser: {url}")
        webbrowser.open(url)

    def handle_update_db(source: str) -> None:
        existing = state["db_worker"]
        if QThreadRetainer.is_running(existing):
            _debug_log("DB update already running; ignoring duplicate request.")
            return
        state["db_worker"] = None
        _debug_log(f"Manual DB update triggered (source={source})")
        ui_lang = state["active_lang"]
        control_panel.set_db_updating(True, I18N.t("panel.db_syncing", ui_lang))
        if state["tray"] is not None:
            state["tray"].set_db_updating(True)

        db_worker = DataSyncWorker()

        def on_sync_done(res: dict) -> None:
            cur_lang = state["active_lang"]
            status = res.get("status")
            if status == "updated":
                evaluator.reload_data()
                msg = I18N.t(
                    "panel.db_done_ok",
                    cur_lang,
                    weapons=res.get("weapon_count", len(evaluator.weapons)),
                    attrs=res.get("attribute_count", 0),
                )
                control_panel.set_db_updating(False, msg, is_error=False)
                if source == "SystemTray" and state["tray"] is not None:
                    state["tray"].show_message(I18N.t("app_title", cur_lang), msg)
            else:
                err_txt = str(res.get("error") or status or "unknown")
                msg = I18N.t("panel.db_done_fallback", cur_lang, err=err_txt)
                control_panel.set_db_updating(False, msg, is_error=True)
                if source == "SystemTray" and state["tray"] is not None:
                    state["tray"].show_message(I18N.t("app_title", cur_lang), msg)

            if state["tray"] is not None:
                state["tray"].set_db_updating(False)

        db_worker.sync_finished.connect(on_sync_done)
        state["db_worker"] = db_worker
        thread_retainer.track(db_worker, on_finished=lambda w=db_worker: _clear_worker_slot("db_worker", w))
        db_worker.start()

    def handle_hide(source: str) -> None:
        if not state["monitoring"] and not window.has_any_visible_overlay():
            return
        if window.is_drag_unlocked:
            control_panel.set_drag_unlocked_ui(False)
            window.set_drag_unlocked(False)

        cur_mode = mode_machine.current_mode
        if cur_mode == MODE_PAUSED:
            _stop_f9_page_watcher(release_hold=True)
            if window.has_any_visible_overlay():
                window.hide_all_overlays()
            _sync_esc_timer()
            return

        # Shrink into compact standby pill WITHOUT exiting the active work mode!
        _stop_f9_page_watcher(release_hold=False)
        if mode_machine.is_manual_f9_capture(cur_mode):
            state["last_rendered_signature"] = None

        already_pill_only = (
            window.is_compact_pill
            and window.isVisible()
            and not window.wm_window.isVisible()
            and not window.synergy_window.isVisible()
            and not window.relic_window.isVisible()
        )
        if not already_pill_only:
            _debug_log(
                f"Hide hotkey pressed ({settings.hotkey_hide_overlay}, source={source}) "
                f"-> shrinking to standby pill (keeping mode={cur_mode})."
            )
            window.enter_standby_pill()
        _sync_esc_timer()

    def handle_drag_unlock_changed(unlocked: bool) -> None:
        _debug_log(f"Overlay drag unlock toggled -> {unlocked} (applies to all 4 independent windows)")
        window.set_drag_unlocked(unlocked, show_preview_if_empty=True)
        if not unlocked and not state["monitoring"] and window.display_mode == "preview":
            window.hide()
        _sync_esc_timer()

    def handle_window_position_changed(win_key: str, rx: float, ry: float) -> None:
        settings.set_window_pos(win_key, rx, ry)
        save_user_settings(settings)
        if win_key == "riven":
            control_panel.set_position_status(settings.overlay_pos_x_ratio, settings.overlay_pos_y_ratio)
        _debug_log(
            f"Saved independent overlay position [{win_key}]: x_ratio={rx:.4f}, y_ratio={ry:.4f}"
        )

    def handle_reset_position() -> None:
        settings.reset_all_positions()
        save_user_settings(settings)
        window.reset_position()
        control_panel.set_position_status(None, None)
        _debug_log("All 4 overlay window positions reset to defaults.")

    def handle_font_size_changed(px: int) -> None:
        settings.overlay_font_size = int(px)
        _schedule_settings_save()
        window.set_font_size(settings.overlay_font_size)
        # If overlay is hidden while user adjusts font size in panel, show live preview card
        if not window.isVisible() and control_panel.isVisible():
            window.show_preview_card()
        _debug_log(f"Overlay font size updated: {settings.overlay_font_size}px")

    def handle_auto_hide_focus_changed(enabled: bool) -> None:
        settings.auto_hide_on_focus_loss = bool(enabled)
        save_user_settings(settings)
        action = focus_watcher.set_enabled(settings.auto_hide_on_focus_loss)
        if action == "restore":
            window.restore_from_focus_loss()
        _debug_log(f"Auto-hide on focus loss updated & saved: {settings.auto_hide_on_focus_loss}")

    def handle_audio_cue_changed(enabled: bool) -> None:
        settings.audio_cue_enabled = bool(enabled)
        save_user_settings(settings)
        AUDIO_CUE.set_enabled(settings.audio_cue_enabled)
        _debug_log(f"Audio cues updated & saved: {settings.audio_cue_enabled}")

    def handle_audio_test() -> None:
        AUDIO_CUE.play_preview()

    def handle_custom_rare_sound_changed(path: str) -> None:
        ok, info = AUDIO_CUE.set_custom_rare_sound(path)
        if not ok:
            control_panel.set_custom_sound_display(settings.custom_rare_sound_path, error=info)
            _debug_log(f"Custom rare sound rejected ({path!r}): {info}")
            return
        settings.custom_rare_sound_path = AUDIO_CUE.custom_rare_sound_path
        save_user_settings(settings)
        control_panel.set_custom_sound_display(settings.custom_rare_sound_path)
        AUDIO_CUE.play_preview()
        _debug_log(
            f"Custom rare sound updated & saved: {settings.custom_rare_sound_path or '<default rare_drop.wav>'}"
        )

    def handle_toggle_hotkey_lock(source: str) -> None:
        locked = hotkey_manager.toggle_hotkeys_locked()
        window.set_hotkeys_locked(locked)
        _sync_tray_and_panel()
        _debug_log(f"Anti-mispress hotkeys lock -> {locked} (source={source})")

    def handle_export_card(source: str) -> None:
        ui_lang = state["active_lang"]
        if not state["last_cards"]:
            msg = I18N.t("panel.export_need_card", ui_lang)
            control_panel.set_export_feedback(msg, is_error=True)
            window.show_toast(msg, duration_ms=2200)
            return
        target_card = state["last_cards"][-1]
        res = export_riven_showcase(
            target_card,
            wm_report=state["last_wm_report"],
            lang=ui_lang,
            copy_to_clipboard=True,
        )
        if res.ok and res.path is not None:
            msg = I18N.t("panel.export_done_ok", ui_lang, path=str(res.path.name))
            control_panel.set_export_feedback(msg, is_error=False)
            if state["tray"] is not None:
                state["tray"].show_message(I18N.t("app_title", ui_lang), msg)
            _debug_log(f"Exported Riven showcase PNG ({res.width}x{res.height}) -> {res.path} [clipboard={res.copied_to_clipboard}, source={source}]")
        else:
            err_msg = f"导出失败：{res.error}"
            control_panel.set_export_feedback(err_msg, is_error=True)
            _debug_log(f"Export Riven showcase failed: {res.error}")

    def handle_panel_geometry_changed(x: int, y: int, w: int, h: int) -> None:
        settings.panel_geometry = [int(x), int(y), int(w), int(h)]
        _schedule_settings_save()
        _debug_log(f"Control panel geometry queued: pos=({x},{y}), size={w}x{h}")

    def handle_theme_changed(preset: str, colors: dict) -> None:
        settings.theme_preset = str(preset or "orokin_void")
        settings.theme_colors = dict(colors)
        _schedule_settings_save()
        theme_apply_timer.start()
        _debug_log(f"Overlay & panel color theme updated: preset={settings.theme_preset}")

    def handle_panel_closed() -> None:
        if window.is_drag_unlocked:
            window.set_drag_unlocked(False)
        if not state["monitoring"] and window.display_mode == "preview":
            window.hide()
        _sync_esc_timer()

    def open_control_panel() -> None:
        control_panel.set_monitoring_state(state["monitoring"])
        control_panel.show_and_activate()

    def open_user_manual() -> None:
        user_manual_dialog.refresh_hotkeys(
            settings.hotkey_toggle_monitor,
            settings.hotkey_fetch_wm,
            settings.hotkey_hide_overlay,
            settings.hotkey_open_panel,
        )
        user_manual_dialog.show_and_activate()

    def handle_toggle_control_panel(source: str) -> None:
        _debug_log(f"Open control panel hotkey triggered ({settings.hotkey_open_panel}, source={source})")
        if control_panel.isVisible() and control_panel.isActiveWindow():
            control_panel.hide()
        else:
            open_control_panel()

    # On Linux (especially Wayland), expose a fast UNIX socket IPC server so compositor
    # native keybinds (`riven-lens --trigger toggle|wm|hide|open_panel|lock|export`) work with 0 privileges.
    from PyQt6.QtCore import QObject

    class _IPCBridge(QObject):
        action_received = pyqtSignal(str)

    ipc_bridge = _IPCBridge()
    ipc_server: Optional[WaylandIPCServer] = None

    def _on_ipc_action(action_name: str) -> None:
        if action_name == "quit":
            handle_quit("WaylandIPC")
        else:
            hotkey_manager.dispatch_action_name(action_name, source="WaylandIPC")

    ipc_bridge.action_received.connect(_on_ipc_action)
    if sys.platform != "win32":
        ipc_server = WaylandIPCServer(
            on_action=lambda act: ipc_bridge.action_received.emit(act),
            logger=_debug_log,
        )
        ipc_server.start()
        if is_linux_wayland():
            _debug_log(
                f"Linux Wayland session detected: compositor={detect_wayland_compositor()}, "
                f"capture_backend={detect_wayland_capture_backend()}, "
                f"hotkey_backend={hotkey_manager.linux_hotkey_backend}"
            )
        elif sys.platform == "darwin":
            from src.macos_support import detect_macos_capture_backend, get_macos_system_summary

            _debug_log(
                f"macOS session detected: {get_macos_system_summary()}, "
                f"capture_backend={detect_macos_capture_backend()}, "
                f"hotkey_backend={hotkey_manager.macos_hotkey_backend}"
            )

    def handle_quit(source: str) -> None:
        _debug_log(f"Exit requested (source={source}) -> stopping all workers & unregistering hotkeys.")
        _flush_settings_save()
        stop_monitor(reason="Tray exit")
        thread_retainer.wait_all(timeout_ms=2500)
        if ipc_server is not None:
            ipc_server.stop()
        hotkey_manager.unregister_all()
        user_manual_dialog.hide()
        control_panel.hide()
        window.hide_all_overlays()
        if state["tray"] is not None:
            state["tray"].hide()
        app.quit()

    # Connect hotkeys, control panel, and system tray
    hotkey_manager.toggle_triggered.connect(handle_toggle)
    hotkey_manager.wm_triggered.connect(handle_f9_trigger)
    hotkey_manager.hide_triggered.connect(handle_hide)
    hotkey_manager.lock_toggled.connect(handle_toggle_hotkey_lock)
    hotkey_manager.export_triggered.connect(handle_export_card)
    hotkey_manager.open_panel_triggered.connect(handle_toggle_control_panel)

    init_report = hotkey_manager.apply_hotkeys(
        settings.hotkey_toggle_monitor,
        settings.hotkey_fetch_wm,
        settings.hotkey_hide_overlay,
        settings.hotkey_open_panel,
    )
    if init_report.ok:
        settings.hotkey_toggle_monitor = init_report.toggle_hotkey
        settings.hotkey_fetch_wm = init_report.wm_hotkey
        settings.hotkey_hide_overlay = init_report.hide_hotkey
        settings.hotkey_open_panel = init_report.open_panel_hotkey
        window.set_hotkey_labels(
            settings.hotkey_toggle_monitor,
            settings.hotkey_fetch_wm,
            settings.hotkey_hide_overlay,
        )
    else:
        _debug_log(f"Initial hotkey registration had errors: {init_report.errors}")

    window.window_position_changed.connect(handle_window_position_changed)
    control_panel.hotkeys_all_apply_requested.connect(handle_apply_hotkeys)
    control_panel.wm_fetch_requested.connect(lambda q: handle_fetch_wm("ControlPanel", manual_weapon=q))
    control_panel.open_wm_browser_requested.connect(handle_open_wm_browser)
    control_panel.db_update_requested.connect(lambda: handle_update_db("ControlPanel"))
    control_panel.language_mode_changed.connect(lambda m: handle_language_change(m, persist=True))
    control_panel.start_monitoring_changed.connect(handle_start_monitoring_pref)
    control_panel.toggle_monitor_requested.connect(lambda: handle_toggle("ControlPanel"))
    control_panel.mode_selected.connect(lambda m: set_work_mode(m, source="ControlPanel"))
    control_panel.calibrate_regions_requested.connect(handle_open_calibrator)
    control_panel.drag_unlock_changed.connect(handle_drag_unlock_changed)
    control_panel.reset_position_requested.connect(handle_reset_position)
    control_panel.font_size_changed.connect(handle_font_size_changed)
    control_panel.auto_hide_focus_changed.connect(handle_auto_hide_focus_changed)
    control_panel.audio_cue_changed.connect(handle_audio_cue_changed)
    control_panel.audio_test_requested.connect(handle_audio_test)
    control_panel.custom_rare_sound_changed.connect(handle_custom_rare_sound_changed)
    control_panel.hotkey_lock_toggled.connect(lambda: handle_toggle_hotkey_lock("ControlPanel"))
    control_panel.export_card_requested.connect(lambda: handle_export_card("ControlPanel"))
    control_panel.user_manual_requested.connect(open_user_manual)
    control_panel.theme_changed.connect(handle_theme_changed)
    control_panel.panel_geometry_changed.connect(handle_panel_geometry_changed)
    control_panel.panel_closed.connect(handle_panel_closed)

    state["tray"] = TrayManager(
        on_toggle_monitor=lambda: handle_toggle("SystemTray"),
        on_open_panel=open_control_panel,
        on_fetch_wm=lambda: handle_f9_trigger("SystemTray"),
        on_update_db=lambda: handle_update_db("SystemTray"),
        on_quit=lambda: handle_quit("SystemTray"),
        on_select_mode=lambda m: set_work_mode(m, source="SystemTray"),
        on_toggle_lock=lambda: handle_toggle_hotkey_lock("SystemTray"),
        initial_ui_lang=initial_ui_lang,
        toggle_hotkey=settings.hotkey_toggle_monitor,
        wm_hotkey=settings.hotkey_fetch_wm,
        initial_mode=mode_machine.current_mode,
    )

    # Smart Foreground Focus Timer (450ms lightweight poll — hides overlays when Alt-Tabbed out of Warframe)
    focus_timer = QTimer()
    focus_timer.setInterval(450)

    def poll_foreground_focus() -> None:
        if not state["monitoring"] and not window.has_any_visible_overlay() and not focus_watcher.is_hidden_by_focus:
            return
        if state["calibrator"] is not None:
            return
        action = focus_watcher.evaluate_focus(drag_unlocked=window.is_drag_unlocked)
        if action == "hide":
            window.suspend_for_focus_loss()
        elif action == "restore":
            window.restore_from_focus_loss()

    focus_timer.timeout.connect(poll_foreground_focus)
    focus_timer.start()

    def poll_esc_key() -> None:
        if not state["monitoring"] and not window.has_any_visible_overlay():
            state["esc_was_down"] = False
            return
        if hotkey_manager.active_hide is None or not hotkey_manager.active_hide.is_plain_esc:
            return
        if sys.platform == "win32":
            esc_down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            if esc_down and not state["esc_was_down"]:
                state["esc_was_down"] = True
                handle_hide("Win32 GetAsyncKeyState")
            elif not esc_down:
                state["esc_was_down"] = False

    esc_timer.timeout.connect(poll_esc_key)

    print(
        f"\n裂罅透镜已就绪（预设: {args.preset} | 语言: {state['lang_mode']} | 管理员权限: {admin}）\n"
        f"  - 本地词条库：{len(evaluator.recommended_tiers)} 把内置/已校准武器 | 官方倾向缓存：{len(evaluator.weapons)} 把武器\n"
        f"  - 全局快捷键：监控={settings.hotkey_toggle_monitor} | 拉取WM={settings.hotkey_fetch_wm} | 隐藏={settings.hotkey_hide_overlay} | 设置={settings.hotkey_open_panel}\n"
        f"  - 系统托盘：双击托盘图标或按 {settings.hotkey_open_panel} 打开设置与控制面板，右键托盘选择「退出软件」彻底退出\n",
        flush=True,
    )

    if initial_ui_lang not in ("zh_CN", "en_US"):
        _ensure_ocr_model_for_language(initial_ui_lang)

    if settings.start_monitoring_on_launch:
        _debug_log("start_monitoring_on_launch=True -> starting auto-monitor immediately.")
        start_monitor()
    else:
        _debug_log("Auto-monitor: OFF (initial standby state)")
        if getattr(sys, "frozen", False):
            window.show_toast(duration_ms=2500)
            _debug_log("Frozen-mode startup ready toast shown for 2.5s.")

    if not settings.first_run_guide_shown:
        settings.first_run_guide_shown = True
        save_user_settings(settings)
        _debug_log("First launch detected -> displaying User Manual dialog.")
        QTimer.singleShot(250, open_user_manual)

    try:
        sys.exit(app.exec())
    finally:
        _flush_settings_save()
        stop_monitor(reason="shutdown")
        thread_retainer.wait_all(timeout_ms=2500)
        if ipc_server is not None:
            ipc_server.stop()
        hotkey_manager.unregister_all()
        if state["tray"] is not None:
            state["tray"].hide()


def _setup_frozen_stdio() -> Optional[Path]:
    """Ensure stdout/stderr are valid writable streams & enable crash diagnostics in frozen mode."""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    try:
        base_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
        log_path = base_dir / "裂罅透镜.log"
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
        try:
            faulthandler.enable(file=stream, all_threads=True)
        except Exception:
            pass

        def _log_unhandled(exc_type, exc_value, exc_tb) -> None:
            try:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                stream.write(f"[{ts}] [CRASH] Unhandled exception:\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=stream)
                stream.flush()
            except Exception:
                pass

        sys.excepthook = _log_unhandled
        return log_path
    except Exception:
        return None


def main() -> None:
    # Fast-path for Linux Wayland IPC trigger (`--trigger <action>`): skips Qt/OpenCV/OCR startup!
    if "--trigger" in sys.argv:
        try:
            t_idx = sys.argv.index("--trigger")
            act = sys.argv[t_idx + 1] if t_idx + 1 < len(sys.argv) else ""
        except Exception:
            act = ""
        from src.wayland_support import send_ipc_trigger

        ok, msg = send_ipc_trigger(act)
        if not ok:
            print(msg, file=sys.stderr)
            sys.exit(1)
        print(msg)
        return

    _setup_frozen_stdio()
    arg_parser = build_arg_parser()
    args = arg_parser.parse_args()

    if args.sync_data:
        res = safe_sync(force=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if args.wm_query:
        ev = RivenEvaluator(weapons_db_path=args.weapons_db)
        canon, w_cls, _, _ = ev.resolve_weapon(args.wm_query, [])
        w_name = canon or args.wm_query
        slug = ev.resolve_wm_slug(w_name)
        report = fetch_wm_riven_stats(w_name, slug, w_cls)
        save_wm_recommendation_to_local(report, tiers_path=ev.tiers_path)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return

    # Default to overlay mode when no explicit mode flag is passed
    if args.overlay or (not args.image and not args.capture):
        run_overlay_mode(args)
        return

    layout_label, entries, total_ms = run_once(args)

    if args.show_overlay_preview:
        from PyQt6.QtWidgets import QApplication
        from pynput import keyboard as pynput_keyboard
        from PyQt6.QtCore import QObject, pyqtSignal
        from src.overlay_ui import EvaluatedRivenCard, RivenOverlayWindow

        class PreviewCloser(QObject):
            close_requested = pyqtSignal()

        app = QApplication(sys.argv)
        window = RivenOverlayWindow(click_through=True)
        cards = [
            EvaluatedRivenCard(
                label=lbl,
                riven=riven_data,
                evaluation=eval_data,
                avg_confidence=ocr_res.avg_confidence,
            )
            for lbl, ocr_res, riven_data, eval_data in entries
        ]
        window.update_cards(cards, elapsed_ms=total_ms, layout_label=layout_label)

        closer = PreviewCloser()
        closer.close_requested.connect(app.quit)

        def on_press(key) -> None:
            if key == pynput_keyboard.Key.esc:
                closer.close_requested.emit()

        listener = pynput_keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        print("悬浮窗预览已显示在屏幕左上角，按 ESC 关闭预览。")
        try:
            sys.exit(app.exec())
        finally:
            listener.stop()


if __name__ == "__main__":
    main()
