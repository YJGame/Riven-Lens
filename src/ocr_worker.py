"""Background OCR worker threads & 2-second mode-switch debouncer.

Provides:
  - `AutoOCRModeDebouncer`: 2.0-second `QTimer` debounce guard before launching
    auto-OCR (`single_riven` / `reroll`). Rapid F8 presses cancel the pending
    timer immediately so skipping past auto-OCR modes triggers ZERO capture or OCR.
  - `MonitorWorker(QThread)`: periodic MSE change-gated RapidOCR strictly for
    `single_riven` and `reroll` modes.
  - `ManualCaptureWorker(QThread)`: one-shot F9 capture worker for `chat_riven`
    and `relic_rewards` (0% idle CPU).
"""

from __future__ import annotations

import gc
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from src.capture import (
    ScreenCapturer,
    compute_frame_mse,
    extract_crops_change_signature,
    is_page_exited,
)
from src.evaluator import (
    CardDataSignature,
    DualComparisonResult,
    RerollDataSignature,
    RivenEvaluation,
    RivenEvaluator,
    build_card_data_signature,
    build_reroll_data_signature,
    cards_stat_numbers_equivalent,
    compare_riven_evaluations,
    is_complete_riven_frame,
    reroll_stat_numbers_equivalent,
)
from src.ocr_engine import RivenOCREngine
from src.overlay_ui import EvaluatedRivenCard
from src.relic_prices import PrimePriceStore
from src.riven_parser import RivenData, RivenParser
from src.roi_manager import (
    MODE_CHAT_RIVEN,
    MODE_RELIC_REWARDS,
    MODE_REROLL,
    ROIManager,
)

DEFAULT_AUTO_OCR_DEBOUNCE_MS = 2000


def get_ocr_models_dir() -> Path:
    """Return writable local cache directory for on-demand multilingual RapidOCR ONNX models."""
    if sys.platform == "win32":
        from src.data_updater import get_data_dir

        return get_data_dir() / "ocr_models"
    return Path.home() / ".config" / "WarframeRivenOverlay" / "ocr_models"


@dataclass(frozen=True)
class OCRModelSpec:
    """Metadata & download sources for one multilingual RapidOCR recognition model group."""

    group: str
    is_builtin: bool
    model_filename: str = ""
    dict_filename: str = ""
    model_urls: Tuple[str, ...] = ()
    dict_urls: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OCRModelStatus:
    """Inspection result returned by `OCRModelManager.check_model_status`."""

    lang_code: str
    group: str
    is_builtin: bool
    is_ready: bool
    rec_model_path: Optional[Path] = None
    rec_keys_path: Optional[Path] = None


_MS_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0"
_GH_DICT_BASE = "https://raw.githubusercontent.com/PaddlePaddle/PaddleOCR/release/2.7/ppocr/utils/dict"

OCR_MODEL_GROUPS: Dict[str, OCRModelSpec] = {
    "builtin_ch_en": OCRModelSpec(group="builtin_ch_en", is_builtin=True),
    "chinese_cht": OCRModelSpec(
        group="chinese_cht",
        is_builtin=False,
        model_filename="chinese_cht_PP-OCRv3_rec_mobile.onnx",
        dict_filename="chinese_cht_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv4/rec/chinese_cht_PP-OCRv3_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv4/rec/chinese_cht_PP-OCRv3_rec_mobile/chinese_cht_dict.txt",
            f"{_GH_DICT_BASE}/chinese_cht_dict.txt",
        ),
    ),
    "japan": OCRModelSpec(
        group="japan",
        is_builtin=False,
        model_filename="japan_PP-OCRv4_rec_mobile.onnx",
        dict_filename="japan_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv4/rec/japan_PP-OCRv4_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv4/rec/japan_PP-OCRv4_rec_mobile/japan_dict.txt",
            f"{_GH_DICT_BASE}/japan_dict.txt",
        ),
    ),
    "korean": OCRModelSpec(
        group="korean",
        is_builtin=False,
        model_filename="korean_PP-OCRv4_rec_mobile.onnx",
        dict_filename="korean_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv4/rec/korean_PP-OCRv4_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv4/rec/korean_PP-OCRv4_rec_mobile/korean_dict.txt",
            f"{_GH_DICT_BASE}/korean_dict.txt",
        ),
    ),
    "cyrillic": OCRModelSpec(
        group="cyrillic",
        is_builtin=False,
        model_filename="cyrillic_PP-OCRv3_rec_mobile.onnx",
        dict_filename="cyrillic_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv4/rec/cyrillic_PP-OCRv3_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv4/rec/cyrillic_PP-OCRv3_rec_mobile/cyrillic_dict.txt",
            f"{_GH_DICT_BASE}/cyrillic_dict.txt",
        ),
    ),
    "latin": OCRModelSpec(
        group="latin",
        is_builtin=False,
        model_filename="latin_PP-OCRv3_rec_mobile.onnx",
        dict_filename="latin_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv4/rec/latin_PP-OCRv3_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv4/rec/latin_PP-OCRv3_rec_mobile/latin_dict.txt",
            f"{_GH_DICT_BASE}/latin_dict.txt",
        ),
    ),
    "thai": OCRModelSpec(
        group="thai",
        is_builtin=False,
        model_filename="th_PP-OCRv5_rec_mobile.onnx",
        dict_filename="ppocrv5_th_dict.txt",
        model_urls=(f"{_MS_BASE}/onnx/PP-OCRv5/rec/th_PP-OCRv5_rec_mobile.onnx",),
        dict_urls=(
            f"{_MS_BASE}/paddle/PP-OCRv5/rec/th_PP-OCRv5_rec_mobile/ppocrv5_th_dict.txt",
        ),
    ),
}

LANG_TO_OCR_GROUP: Dict[str, str] = {
    "zh_CN": "builtin_ch_en",
    "en_US": "builtin_ch_en",
    "zh_TW": "chinese_cht",
    "ja_JP": "japan",
    "ko_KR": "korean",
    "ru_RU": "cyrillic",
    "uk_UA": "cyrillic",
    "fr_FR": "latin",
    "it_IT": "latin",
    "de_DE": "latin",
    "es_ES": "latin",
    "pt_BR": "latin",
    "pl_PL": "latin",
    "tr_TR": "latin",
    "th_TH": "thai",
}


class OCRModelManager:
    """Checks local cache and downloads missing multilingual RapidOCR ONNX models on demand."""

    def __init__(self, models_dir: Optional[str | Path] = None) -> None:
        self.models_dir = Path(models_dir) if models_dir else get_ocr_models_dir()

    def get_spec(self, lang_code: str) -> OCRModelSpec:
        grp = LANG_TO_OCR_GROUP.get(lang_code, "builtin_ch_en")
        return OCR_MODEL_GROUPS[grp]

    def check_model_status(self, lang_code: str) -> OCRModelStatus:
        spec = self.get_spec(lang_code)
        if spec.is_builtin:
            return OCRModelStatus(
                lang_code=lang_code,
                group=spec.group,
                is_builtin=True,
                is_ready=True,
            )
        model_path = self.models_dir / spec.group / spec.model_filename
        dict_path = self.models_dir / spec.group / spec.dict_filename
        ready = (
            model_path.is_file()
            and model_path.stat().st_size > 0
            and dict_path.is_file()
            and dict_path.stat().st_size > 0
        )
        return OCRModelStatus(
            lang_code=lang_code,
            group=spec.group,
            is_builtin=False,
            is_ready=ready,
            rec_model_path=model_path if ready else None,
            rec_keys_path=dict_path if ready else None,
        )

    @staticmethod
    def _default_download(
        url: str,
        dest: Path,
        progress_cb: Optional[Callable[[int], None]] = None,
    ) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "RivenLens/3.0"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            read_bytes = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    out.write(chunk)
                    read_bytes += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(min(99, int(round(read_bytes * 100.0 / total))))
        tmp.replace(dest)

    def ensure_model(
        self,
        lang_code: str,
        *,
        progress_cb: Optional[Callable[[int], None]] = None,
        downloader: Optional[Callable[[str, Path, Optional[Callable[[int], None]]], None]] = None,
    ) -> OCRModelStatus:
        status = self.check_model_status(lang_code)
        if status.is_ready:
            if progress_cb:
                progress_cb(100)
            return status

        spec = self.get_spec(lang_code)
        model_dest = self.models_dir / spec.group / spec.model_filename
        dict_dest = self.models_dir / spec.group / spec.dict_filename
        dl_fn = downloader or self._default_download

        # Download ONNX model (0..85%) and dictionary (85..100%)
        last_err: Optional[Exception] = None
        if not (model_dest.is_file() and model_dest.stat().st_size > 0):
            for url in spec.model_urls:
                try:
                    dl_fn(
                        url,
                        model_dest,
                        (lambda pct: progress_cb(int(pct * 0.85))) if progress_cb else None,
                    )
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
            if last_err is not None:
                raise last_err

        if not (dict_dest.is_file() and dict_dest.stat().st_size > 0):
            for url in spec.dict_urls:
                try:
                    dl_fn(
                        url,
                        dict_dest,
                        (lambda pct: progress_cb(85 + int(pct * 0.15))) if progress_cb else None,
                    )
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
            if last_err is not None:
                raise last_err

        if progress_cb:
            progress_cb(100)
        return self.check_model_status(lang_code)


class OCRModelDownloadWorker(QThread):
    """Background worker that downloads missing multilingual RapidOCR ONNX models without blocking UI."""

    progress_changed = pyqtSignal(str, int)                 # lang_code, pct
    download_succeeded = pyqtSignal(str, object, object)    # lang_code, rec_model_path, rec_keys_path
    download_failed = pyqtSignal(str, str)                  # lang_code, error_msg

    def __init__(
        self,
        lang_code: str,
        manager: Optional[OCRModelManager] = None,
        downloader: Optional[Callable[[str, Path, Optional[Callable[[int], None]]], None]] = None,
    ) -> None:
        super().__init__()
        self.lang_code = lang_code
        self.manager = manager or OCR_MODEL_MANAGER
        self._downloader = downloader

    def run(self) -> None:
        try:
            st = self.manager.ensure_model(
                self.lang_code,
                progress_cb=lambda pct: self.progress_changed.emit(self.lang_code, int(pct)),
                downloader=self._downloader,
            )
            self.download_succeeded.emit(self.lang_code, st.rec_model_path, st.rec_keys_path)
        except Exception as exc:
            self.download_failed.emit(self.lang_code, str(exc))


OCR_MODEL_MANAGER = OCRModelManager()


class QThreadRetainer:
    """Keeps active & retiring QThread instances alive until Qt emits `finished`.

    Prevents both:
      1. Fatal Qt C++ abort `QThread: Destroyed while thread is still running` when
         users rapidly switch modes/languages/hotkeys while background OCR or
         downloads are still unwinding.
      2. Main GUI thread freezes caused by synchronous `thread.wait(...)` calls.
      3. `RuntimeError: wrapped C/C++ object ... has been deleted` when checking
         whether a previously completed worker is still running.
    """

    def __init__(self) -> None:
        self._threads: set[QThread] = set()

    @property
    def active_count(self) -> int:
        return len(self._threads)

    @staticmethod
    def is_running(thread: Optional[QThread]) -> bool:
        """Safely check `thread.isRunning()`, returning False if None or C++ object already deleted."""
        if thread is None:
            return False
        try:
            return bool(thread.isRunning())
        except RuntimeError:
            return False

    def _release(self, thread: QThread, on_finished: Optional[Callable[[], None]] = None) -> None:
        self._threads.discard(thread)
        if on_finished is not None:
            try:
                on_finished()
            except Exception:
                pass
        try:
            thread.deleteLater()
        except RuntimeError:
            pass

    def track(
        self,
        thread: Optional[QThread],
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        """Retain a reference to a QThread until its `finished` signal fires."""
        if thread is None:
            return
        if thread in self._threads:
            return
        self._threads.add(thread)
        try:
            thread.finished.connect(lambda t=thread, cb=on_finished: self._release(t, cb))
        except RuntimeError:
            self._release(thread, on_finished)

    def retire(
        self,
        thread: Optional[QThread],
        stop_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        """Request cooperative stop WITHOUT blocking the GUI thread, retaining until finished."""
        if thread is None:
            return
        if stop_fn is not None:
            try:
                stop_fn()
            except Exception:
                pass
        if self.is_running(thread):
            self.track(thread)
            # Guard against race where thread finished right after isRunning() returned True
            if not self.is_running(thread):
                self._release(thread)
        else:
            self._release(thread)

    def wait_all(self, timeout_ms: int = 2000) -> None:
        """Bounded join used ONLY during final application shutdown."""
        deadline = time.monotonic() + max(0.05, timeout_ms / 1000.0)
        for th in list(self._threads):
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000.0))
            try:
                if self.is_running(th):
                    th.wait(remaining_ms)
            except RuntimeError:
                pass
            if not self.is_running(th):
                self._threads.discard(th)


class AutoOCRModeDebouncer(QObject):
    """Debounce guard delaying auto-OCR startup by `delay_ms` (default 2000ms) on mode switches."""

    debounced_ready = pyqtSignal(str)  # work_mode

    def __init__(
        self,
        delay_ms: int = DEFAULT_AUTO_OCR_DEBOUNCE_MS,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._delay_ms = max(0, int(delay_ms))
        self._pending_mode: Optional[str] = None
        self._generation: int = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

    @property
    def delay_ms(self) -> int:
        return self._delay_ms

    @property
    def pending_mode(self) -> Optional[str]:
        return self._pending_mode

    @property
    def is_pending(self) -> bool:
        return self._timer.isActive() and self._pending_mode is not None

    def cancel(self) -> None:
        """Immediately cancel any pending auto-OCR startup."""
        self._generation += 1
        self._pending_mode = None
        if self._timer.isActive():
            self._timer.stop()

    def schedule(self, mode: str) -> None:
        """Cancel any previous timer and arm a fresh `delay_ms` countdown for `mode`."""
        self.cancel()
        self._pending_mode = mode
        self._timer.start(self._delay_ms)

    def _on_timeout(self) -> None:
        mode = self._pending_mode
        self._pending_mode = None
        if mode is not None:
            self.debounced_ready.emit(mode)


class ManualCaptureWorker(QThread):
    """One-shot F9 capture worker for `chat_riven` & `relic_rewards` (0% background CPU)."""

    chat_riven_ready = pyqtSignal(int, object, float)   # req_id, List[EvaluatedRivenCard], elapsed_ms
    relic_ready = pyqtSignal(int, object, float)        # req_id, List[RelicRewardMatch], elapsed_ms
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        req_id: int,
        work_mode: str,
        capturer: ScreenCapturer,
        roi_manager: ROIManager,
        ocr: RivenOCREngine,
        parser: RivenParser,
        evaluator: RivenEvaluator,
        prime_prices: Optional[PrimePriceStore] = None,
        fallback_to_raw: bool = True,
        disposition_override: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.req_id = req_id
        self.work_mode = work_mode
        self.capturer = capturer
        self.roi_manager = roi_manager
        self.ocr = ocr
        self.parser = parser
        self.evaluator = evaluator
        self.prime_prices = prime_prices
        self.fallback_to_raw = fallback_to_raw
        self.disposition_override = disposition_override
        self.captured_anchor_signature: Optional[np.ndarray] = None
        self.captured_anchor_ratios: Optional[Tuple[float, float, float, float]] = None

    def run(self) -> None:
        t0 = time.perf_counter()
        try:
            crops, meta = self.capturer.capture_mode_regions(
                self.work_mode, roi_manager=self.roi_manager
            )
            self.captured_anchor_signature = extract_crops_change_signature(crops)
            ratios_map = meta.get("region_ratios") if isinstance(meta, dict) else None
            if isinstance(ratios_map, dict) and self.work_mode in ratios_map:
                self.captured_anchor_ratios = ratios_map[self.work_mode]
            if self.work_mode == MODE_CHAT_RIVEN:
                img = crops.get("chat_riven")
                if img is None:
                    raise RuntimeError("聊天紫卡区域截图为空")
                ocr_res = self.ocr.recognize(img, fallback_to_raw=self.fallback_to_raw)
                riven_data = self.parser.parse(ocr_res.lines)
                eval_data = self.evaluator.evaluate(
                    riven_data, disposition_override=self.disposition_override
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                card = EvaluatedRivenCard(
                    label="聊天链接紫卡",
                    riven=riven_data,
                    evaluation=eval_data,
                    avg_confidence=ocr_res.avg_confidence,
                )
                self.chat_riven_ready.emit(self.req_id, [card], elapsed_ms)
            elif self.work_mode == MODE_RELIC_REWARDS:
                img = crops.get("relic_rewards")
                if img is None:
                    raise RuntimeError("开核桃奖励区域截图为空")
                ocr_res = self.ocr.recognize(img, fallback_to_raw=self.fallback_to_raw)
                if self.prime_prices is not None:
                    if ocr_res.boxes:
                        matches = self.prime_prices.match_ocr_boxes(
                            ocr_res.boxes,
                            image_width=ocr_res.image_width or int(img.shape[1]),
                        )
                    else:
                        matches = self.prime_prices.match_ocr_lines(ocr_res.lines)
                else:
                    matches = [ln.strip() for ln in ocr_res.lines if ln and len(ln.strip()) >= 2]
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self.relic_ready.emit(self.req_id, matches, elapsed_ms)
        except Exception as exc:
            self.failed.emit(self.req_id, str(exc))


class PageExitDetector:
    """Stateful debounced detector checking whether the F9-captured page has been exited.

    Pure numpy logic (zero OCR, zero Qt threads) so both `F9PagePresenceWatcher` and
    headless unit tests can evaluate frames deterministically.
    """

    def __init__(
        self,
        anchor_signature: np.ndarray,
        *,
        exit_confirm_frames: int = 2,
        corr_threshold: float = 0.52,
        mse_threshold: float = 320.0,
        extreme_mse_threshold: float = 1350.0,
    ) -> None:
        self.anchor_signature = anchor_signature.copy()
        self.exit_confirm_frames = max(1, int(exit_confirm_frames))
        self.corr_threshold = float(corr_threshold)
        self.mse_threshold = float(mse_threshold)
        self.extreme_mse_threshold = float(extreme_mse_threshold)
        self._consecutive_exits: int = 0
        self.last_mse: float = 0.0
        self.last_corr: float = 1.0

    @property
    def consecutive_exits(self) -> int:
        return self._consecutive_exits

    def update(self, curr_signature: np.ndarray) -> bool:
        """Feed one live ROI thumbnail; returns True once exit is confirmed."""
        exited, mse, corr = is_page_exited(
            self.anchor_signature,
            curr_signature,
            corr_threshold=self.corr_threshold,
            mse_threshold=self.mse_threshold,
            extreme_mse_threshold=self.extreme_mse_threshold,
        )
        self.last_mse = mse
        self.last_corr = corr
        if exited:
            self._consecutive_exits += 1
            return self._consecutive_exits >= self.exit_confirm_frames
        self._consecutive_exits = 0
        return False


class F9PagePresenceWatcher(QThread):
    """Ultra-lightweight (zero-OCR) watcher keeping an F9 overlay pinned until page exit.

    After the player presses F9 in `chat_riven` or `relic_rewards` mode, this thread
    periodically captures only the small calibrated ROI (~2ms) and compares its blurred
    grayscale thumbnail against the F9 anchor frame. As long as the player stays on the
    Riven or Void Relic reward screen, the overlay remains pinned open indefinitely;
    once the player closes/exits that screen (confirmed across `exit_confirm_frames`),
    `page_exited` is emitted and the thread terminates immediately (back to 0% CPU).
    """

    page_exited = pyqtSignal(int, str)  # watch_id, work_mode

    def __init__(
        self,
        watch_id: int,
        work_mode: str,
        anchor_signature: np.ndarray,
        capturer: ScreenCapturer,
        roi_manager: ROIManager,
        *,
        interval_sec: float = 0.55,
        exit_confirm_frames: int = 2,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self.watch_id = int(watch_id)
        self.work_mode = work_mode
        self.capturer = capturer
        self.roi_manager = roi_manager
        self.interval_sec = max(0.25, float(interval_sec))
        self._detector = PageExitDetector(
            anchor_signature,
            exit_confirm_frames=exit_confirm_frames,
        )
        self._log = logger or (lambda _msg: None)
        self._stop_event = threading.Event()

    def stop_watching(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        self._log(
            f"F9PagePresenceWatcher started (watch_id={self.watch_id}, mode={self.work_mode}, "
            f"interval={self.interval_sec:.2f}s)"
        )
        try:
            while not self._stop_event.wait(timeout=self.interval_sec):
                try:
                    crops, _meta = self.capturer.capture_mode_regions(
                        self.work_mode, roi_manager=self.roi_manager
                    )
                    curr_sig = extract_crops_change_signature(crops)
                except Exception as exc:
                    self._log(f"F9PagePresenceWatcher capture error (ignored): {exc}")
                    continue

                if self._detector.update(curr_sig):
                    self._log(
                        f"F9PagePresenceWatcher detected page exit (watch_id={self.watch_id}, "
                        f"mode={self.work_mode}, mse={self._detector.last_mse:.1f}, "
                        f"corr={self._detector.last_corr:.2f})"
                    )
                    if not self._stop_event.is_set():
                        self.page_exited.emit(self.watch_id, self.work_mode)
                    break
        finally:
            self._log(f"F9PagePresenceWatcher stopped (watch_id={self.watch_id}, mode={self.work_mode})")


class RivenFrameDeduplicator:
    """Strict structured Data-Signature deduplicator & animation-transition filter.

    Prevents overlay flicker / redraw / `adjustSize()` jitter when Warframe's
    particle effects or lighting cause slight OCR whitespace/punctuation/order
    variations on an otherwise unchanged Riven screen.

    Rules:
      1. `reroll` mode:
         - Both left and right cards MUST pass `is_complete_riven_frame(..., min_stats=2)`
           (valid weapon name + at least 2 valid stats). Otherwise the frame is treated
           as a Kuva-flip animation transition frame and silently ignored while keeping
           the previous complete dual-card display intact.
         - Computes `reroll_data_signature = (left_card_sig, right_card_sig)` where each
           card signature is `(canonical_weapon_id, reroll_count, tuple(sorted((stat_key, round(val, 1)))))`.
         - If `reroll_data_signature == last_rendered_reroll_signature`: returns `"skip_duplicate"`
           so ZERO UI signals are emitted.
      2. `single_riven` mode:
         - Same structured card data signature + completeness gate.
    """

    def __init__(
        self,
        work_mode: str,
        miss_debounce: int = 3,
        min_stats: int = 2,
        value_jitter_tol: float = 1.2,
        change_debounce_frames: int = 1,
        hold_on_unrecognized: bool = False,
    ) -> None:
        self.work_mode = work_mode
        self.miss_debounce = max(1, int(miss_debounce))
        self.min_stats = max(1, int(min_stats))
        self.value_jitter_tol = max(0.0, float(value_jitter_tol))
        self.change_debounce_frames = max(1, int(change_debounce_frames))
        self.hold_on_unrecognized = bool(hold_on_unrecognized)
        self.last_rendered_reroll_signature: Optional[RerollDataSignature] = None
        self.last_rendered_single_signature: Optional[CardDataSignature] = None
        self._pending_reroll_sig: Optional[RerollDataSignature] = None
        self._pending_single_sig: Optional[CardDataSignature] = None
        self._pending_hits: int = 0
        self._consecutive_empty: int = 0
        self._showing_waiting: bool = True

    @property
    def last_rendered_signature(self) -> Optional[object]:
        if self.work_mode == MODE_REROLL:
            return self.last_rendered_reroll_signature
        return self.last_rendered_single_signature

    @property
    def has_pending_empty_miss(self) -> bool:
        if self.hold_on_unrecognized:
            return False
        limit = max(self.miss_debounce, 6) if self.work_mode == MODE_REROLL else self.miss_debounce
        return 0 < self._consecutive_empty < limit

    @property
    def has_pending_change(self) -> bool:
        return self._pending_hits > 0

    def reset(self) -> None:
        self.last_rendered_reroll_signature = None
        self.last_rendered_single_signature = None
        self._pending_reroll_sig = None
        self._pending_single_sig = None
        self._pending_hits = 0
        self._consecutive_empty = 0
        self._showing_waiting = True

    @staticmethod
    def _has_partial_riven_signal(parsed_pairs: Sequence[Tuple[RivenData, RivenEvaluation]]) -> bool:
        """Return True if any side shows real partial Riven stats or a DB-matched weapon (flip animation)."""
        for riven, ev in parsed_pairs:
            if riven is None:
                continue
            known_weapon = bool(ev and ev.weapon_slug)
            if known_weapon or len(riven.stats) >= 1:
                return True
        return False

    def process_frame(
        self,
        parsed_pairs: Sequence[Tuple[RivenData, RivenEvaluation]],
    ) -> str:
        """Decide how to handle one OCR-parsed frame (stat-number driven + debounced).

        Returns:
          - `"emit"`: New valid complete data whose stat numbers genuinely changed (and passed debounce).
          - `"skip_duplicate"`: Stat numbers match current displayed card within jitter tolerance -> 0 redraws.
          - `"hold_transition"`: Unrecognizable, incomplete, or pending debounce frame -> keep current UI intact.
          - `"waiting"`: Sustained completely empty screen (only when `hold_on_unrecognized=False`).
        """
        if self.work_mode == MODE_REROLL:
            if len(parsed_pairs) == 2:
                left_r, left_e = parsed_pairs[0]
                right_r, right_e = parsed_pairs[1]
                if is_complete_riven_frame(left_r, left_e, min_stats=self.min_stats) and is_complete_riven_frame(
                    right_r, right_e, min_stats=self.min_stats
                ):
                    self._consecutive_empty = 0
                    reroll_sig = build_reroll_data_signature((left_r, left_e), (right_r, right_e))
                    if reroll_stat_numbers_equivalent(
                        reroll_sig,
                        self.last_rendered_reroll_signature,
                        abs_tol=self.value_jitter_tol,
                    ):
                        self._pending_reroll_sig = None
                        self._pending_hits = 0
                        return "skip_duplicate"

                    # First card renders immediately; subsequent changes require debounce confirmation
                    if self.last_rendered_reroll_signature is not None and self.change_debounce_frames > 1:
                        if reroll_stat_numbers_equivalent(
                            reroll_sig,
                            self._pending_reroll_sig,
                            abs_tol=self.value_jitter_tol,
                        ):
                            self._pending_hits += 1
                        else:
                            self._pending_reroll_sig = reroll_sig
                            self._pending_hits = 1
                        if self._pending_hits < self.change_debounce_frames:
                            return "hold_transition"

                    self._pending_reroll_sig = None
                    self._pending_hits = 0
                    self.last_rendered_reroll_signature = reroll_sig
                    self._showing_waiting = False
                    return "emit"

            # Unrecognizable or incomplete frame -> discard pending change & NEVER flicker displayed card
            self._pending_reroll_sig = None
            self._pending_hits = 0
            if self.hold_on_unrecognized or self._has_partial_riven_signal(parsed_pairs):
                return "hold_transition"

            self._consecutive_empty += 1
            empty_limit = max(self.miss_debounce, 6)
            if self._consecutive_empty >= empty_limit:
                if self.last_rendered_reroll_signature is not None or not self._showing_waiting:
                    self.last_rendered_reroll_signature = None
                    self._showing_waiting = True
                    return "waiting"
            return "hold_transition"

        # Single Riven auto-OCR mode
        if len(parsed_pairs) >= 1:
            r, e = parsed_pairs[0]
            if is_complete_riven_frame(r, e, min_stats=self.min_stats):
                self._consecutive_empty = 0
                single_sig = build_card_data_signature(r, e)
                if cards_stat_numbers_equivalent(
                    single_sig,
                    self.last_rendered_single_signature,
                    abs_tol=self.value_jitter_tol,
                ):
                    self._pending_single_sig = None
                    self._pending_hits = 0
                    return "skip_duplicate"

                if self.last_rendered_single_signature is not None and self.change_debounce_frames > 1:
                    if cards_stat_numbers_equivalent(
                        single_sig,
                        self._pending_single_sig,
                        abs_tol=self.value_jitter_tol,
                    ):
                        self._pending_hits += 1
                    else:
                        self._pending_single_sig = single_sig
                        self._pending_hits = 1
                    if self._pending_hits < self.change_debounce_frames:
                        return "hold_transition"

                self._pending_single_sig = None
                self._pending_hits = 0
                self.last_rendered_single_signature = single_sig
                self._showing_waiting = False
                return "emit"

        self._pending_single_sig = None
        self._pending_hits = 0
        if self.hold_on_unrecognized or (
            self.last_rendered_single_signature is not None and self._has_partial_riven_signal(parsed_pairs)
        ):
            return "hold_transition"

        self._consecutive_empty += 1
        if self._consecutive_empty >= self.miss_debounce:
            if self.last_rendered_single_signature is not None or not self._showing_waiting:
                self.last_rendered_single_signature = None
                self._showing_waiting = True
                return "waiting"
        return "hold_transition"


class MonitorWorker(QThread):
    """Low-overhead periodic monitor running ONLY for auto-OCR modes (`single_riven` / `reroll`)."""

    cards_updated = pyqtSignal(int, str, object, object, float)  # session_id, layout_label, cards, comparison, elapsed_ms
    waiting_for_riven = pyqtSignal(int, str)  # session_id, message
    failed = pyqtSignal(int, str)  # session_id, error_msg

    def __init__(
        self,
        session_id: int,
        work_mode: str,
        roi_manager: ROIManager,
        capturer: ScreenCapturer,
        ocr: RivenOCREngine,
        parser: RivenParser,
        evaluator: RivenEvaluator,
        fallback_to_raw: bool = True,
        disposition_override: Optional[float] = None,
        debug_dir: Optional[str] = None,
        interval_sec: float = 0.85,
        mse_threshold: float = 18.0,
        miss_debounce: int = 3,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__()
        self.session_id = session_id
        self.work_mode = work_mode
        self.roi_manager = roi_manager
        self.capturer = capturer
        self.ocr = ocr
        self.parser = parser
        self.evaluator = evaluator
        self.fallback_to_raw = fallback_to_raw
        self.disposition_override = disposition_override
        self.debug_dir = debug_dir
        self.interval_sec = max(0.3, float(interval_sec))
        self.mse_threshold = max(0.5, float(mse_threshold))
        self.miss_debounce = max(1, int(miss_debounce))
        self._log = logger or (lambda _msg: None)
        self._stop_event = threading.Event()

    def stop_monitoring(self) -> None:
        self._stop_event.set()

    def _interruptible_sleep(self, seconds: float) -> None:
        self._stop_event.wait(timeout=max(0.0, seconds))

    def run(self) -> None:
        self._log(
            f"MonitorWorker(QThread) started (session={self.session_id}, mode={self.work_mode}, "
            f"interval={self.interval_sec:.2f}s, mse_threshold={self.mse_threshold:.1f}, "
            f"miss_debounce={self.miss_debounce})"
        )
        prev_sig: Optional[np.ndarray] = None
        deduplicator = RivenFrameDeduplicator(
            work_mode=self.work_mode,
            miss_debounce=max(2, self.miss_debounce),
            min_stats=2,
            value_jitter_tol=1.2,
            change_debounce_frames=2,
            hold_on_unrecognized=False,
        )

        try:
            while not self._stop_event.is_set():
                tick_start = time.perf_counter()
                crops: Optional[dict] = None
                curr_sig: Optional[np.ndarray] = None

                try:
                    crops, _meta = self.capturer.capture_mode_regions(
                        self.work_mode, roi_manager=self.roi_manager
                    )
                    curr_sig = extract_crops_change_signature(crops)

                    confirming_miss = deduplicator.has_pending_empty_miss
                    confirming_change = deduplicator.has_pending_change
                    if prev_sig is None:
                        should_ocr = True
                        mse_val = float("inf")
                    else:
                        mse_val = compute_frame_mse(curr_sig, prev_sig)
                        should_ocr = (mse_val >= self.mse_threshold) or confirming_miss or confirming_change

                    if should_ocr and not self._stop_event.is_set():
                        t_ocr = time.perf_counter()
                        evaluated_cards: List[EvaluatedRivenCard] = []
                        parsed_pairs: List[Tuple[RivenData, RivenEvaluation]] = []

                        if self.work_mode == MODE_REROLL:
                            layout_label = "洗卡双卡对比"
                            box_pairs = [("[当前]", "reroll_left"), ("[新循环]", "reroll_right")]
                        else:
                            layout_label = "单卡识别"
                            box_pairs = [("当前紫卡", "single_riven")]

                        for lbl, box_key in box_pairs:
                            if self._stop_event.is_set():
                                break
                            crop_img = crops.get(box_key)
                            if crop_img is None:
                                continue
                            sub_debug = str(Path(self.debug_dir) / box_key) if self.debug_dir else None
                            ocr_res = self.ocr.recognize(
                                crop_img,
                                fallback_to_raw=self.fallback_to_raw,
                                debug_dir=sub_debug,
                            )
                            if self._stop_event.is_set():
                                break
                            riven_data = self.parser.parse(ocr_res.lines)
                            eval_data = self.evaluator.evaluate(
                                riven_data, disposition_override=self.disposition_override
                            )
                            self._log(
                                f"OCR+Eval [{self.work_mode}/{lbl}]: valid={riven_data.is_valid}, "
                                f"lines={len(ocr_res.lines)}, weapon={eval_data.weapon_name}, "
                                f"grade={eval_data.overall_grade}, stats={len(riven_data.positive_stats)}+{len(riven_data.negative_stats)}-"
                                + (f", err={riven_data.validation_error}" if not riven_data.is_valid and riven_data.validation_error else "")
                            )
                            evaluated_cards.append(
                                EvaluatedRivenCard(
                                    label=lbl,
                                    riven=riven_data,
                                    evaluation=eval_data,
                                    avg_confidence=ocr_res.avg_confidence,
                                )
                            )
                            parsed_pairs.append((riven_data, eval_data))

                        total_ms = (time.perf_counter() - t_ocr) * 1000.0
                        prev_sig = curr_sig
                        curr_sig = None

                        if not self._stop_event.is_set():
                            decision = deduplicator.process_frame(parsed_pairs)
                            if decision == "emit":
                                comp: Optional[DualComparisonResult] = (
                                    compare_riven_evaluations(
                                        evaluated_cards[0].evaluation,
                                        evaluated_cards[1].evaluation,
                                    )
                                    if len(evaluated_cards) == 2
                                    else None
                                )
                                self.cards_updated.emit(
                                    self.session_id,
                                    layout_label,
                                    evaluated_cards,
                                    comp,
                                    total_ms,
                                )
                            elif decision == "waiting":
                                self.waiting_for_riven.emit(
                                    self.session_id,
                                    "● 自动监控中，等待紫卡画面...",
                                )
                            # decision in ("skip_duplicate", "hold_transition"):
                            # Silently drop without emitting any UI signal (0 redraws, 0 flicker).
                except Exception as exc:
                    self._log(f"MonitorWorker(QThread) cycle ERROR: {exc!r}")
                    if not self._stop_event.is_set():
                        self.failed.emit(self.session_id, str(exc))
                finally:
                    if crops is not None:
                        del crops
                    if curr_sig is not None:
                        del curr_sig

                elapsed = time.perf_counter() - tick_start
                remaining = max(0.05, self.interval_sec - elapsed)
                self._interruptible_sleep(remaining)
        finally:
            prev_sig = None
            deduplicator.reset()
            gc.collect()
            self._log(f"MonitorWorker(QThread) stopped (session={self.session_id}, mode={self.work_mode}).")
