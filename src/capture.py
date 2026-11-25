"""Screen capture and local image loading utilities for Warframe Riven Overlay.

Security & TOS note:
    This module strictly performs passive screen capture (via `mss`) or reads
    local screenshot files from disk. It NEVER accesses game process memory,
    hooks DirectX, or injects into the Warframe client.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import mss
import numpy as np

# Reference design resolution (Borderless Fullscreen 1080p)
BASE_WIDTH = 1920
BASE_HEIGHT = 1080


@dataclass(frozen=True)
class HeightAnchoredROI:
    """Resolution- & aspect-ratio-independent ROI anchored to screen center `(cx, cy)`.

    Warframe's Mod / Riven / Kuva-reroll UI is always centered on the screen and
    scales proportionally to the effective UI height `eff_h`:
      - On 16:9 (1366x768, 1920x1080, 2560x1440, 3840x2160), 21:9 (2560x1080,
        3440x1440), and 32:9 (5120x1440) monitors: `eff_h = H`. Ultrawide displays
        only add extra peripheral space on left/right without stretching the card!
      - On narrower displays like 16:10 (1920x1200) or 4:3: `eff_h = W * 9 / 16`
        so centered boxes stay safely within horizontal screen bounds.

    All offsets and dimensions are expressed as fractions of `eff_h` relative to
    the exact screen center `(cx = W / 2, cy = H / 2)`.
    """

    dx_ratio: float  # (left - cx) / eff_h
    dy_ratio: float  # (top - cy) / eff_h
    w_ratio: float   # width / eff_h
    h_ratio: float   # height / eff_h

    @staticmethod
    def effective_height(screen_width: int, screen_height: int) -> float:
        """Compute Warframe's effective UI scaling height `eff_h` for any aspect ratio."""
        return min(float(screen_height), float(screen_width) * (BASE_HEIGHT / BASE_WIDTH))

    def to_screen_region(self, screen_width: int, screen_height: int) -> "ScreenRegion":
        eff_h = self.effective_height(screen_width, screen_height)
        cx = screen_width / 2.0
        cy = screen_height / 2.0
        left = int(round(cx + self.dx_ratio * eff_h))
        top = int(round(cy + self.dy_ratio * eff_h))
        width = max(1, int(round(self.w_ratio * eff_h)))
        height = max(1, int(round(self.h_ratio * eff_h)))
        return ScreenRegion(left=left, top=top, width=width, height=height).clamp(
            screen_width, screen_height
        )

    @classmethod
    def from_1080p_box(cls, left: int, top: int, width: int, height: int) -> "HeightAnchoredROI":
        cx = BASE_WIDTH / 2.0
        cy = BASE_HEIGHT / 2.0
        return cls(
            dx_ratio=(left - cx) / BASE_HEIGHT,
            dy_ratio=(top - cy) / BASE_HEIGHT,
            w_ratio=width / BASE_HEIGHT,
            h_ratio=height / BASE_HEIGHT,
        )


@dataclass(frozen=True)
class ScreenRegion:
    """Rectangular region defined in pixel coordinates."""

    left: int
    top: int
    width: int
    height: int

    def scale_for_resolution(
        self,
        actual_width: int,
        actual_height: int,
        base_width: int = BASE_WIDTH,
        base_height: int = BASE_HEIGHT,
        preserve_aspect_centered: bool = True,
    ) -> "ScreenRegion":
        """Scale a region defined at `base_width x base_height` to target resolution.

        Uses height-anchored center scaling (`eff_h = min(H, W * 9/16)`) by default,
        guaranteeing pixel-accurate alignment across 1366x768, 1080p, 2K, 4K,
        21:9 (3440x1440), and 32:9 (5120x1440) ultrawide monitors.
        """
        if actual_width == base_width and actual_height == base_height:
            return self

        if not preserve_aspect_centered:
            scale_x = actual_width / base_width
            scale_y = actual_height / base_height
            return ScreenRegion(
                left=int(round(self.left * scale_x)),
                top=int(round(self.top * scale_y)),
                width=max(1, int(round(self.width * scale_x))),
                height=max(1, int(round(self.height * scale_y))),
            )

        eff_h = min(float(actual_height), float(actual_width) * (base_height / base_width))
        scale = eff_h / float(base_height)
        base_cx = base_width / 2.0
        base_cy = base_height / 2.0
        actual_cx = actual_width / 2.0
        actual_cy = actual_height / 2.0

        return ScreenRegion(
            left=int(round(actual_cx + (self.left - base_cx) * scale)),
            top=int(round(actual_cy + (self.top - base_cy) * scale)),
            width=max(1, int(round(self.width * scale))),
            height=max(1, int(round(self.height * scale))),
        )

    def to_mss_dict(self, monitor_left: int = 0, monitor_top: int = 0) -> Dict[str, int]:
        """Convert to `mss.grab()` monitor dictionary with virtual-screen offset."""
        return {
            "left": monitor_left + self.left,
            "top": monitor_top + self.top,
            "width": self.width,
            "height": self.height,
        }

    def clamp(self, max_width: int, max_height: int) -> "ScreenRegion":
        """Clamp coordinates so the crop never exceeds image/screen bounds."""
        left = max(0, min(self.left, max_width - 1))
        top = max(0, min(self.top, max_height - 1))
        width = max(1, min(self.width, max_width - left))
        height = max(1, min(self.height, max_height - top))
        return ScreenRegion(left=left, top=top, width=width, height=height)

    @classmethod
    def from_string(cls, region_str: str) -> "ScreenRegion":
        """Parse 'left,top,width,height' into a ScreenRegion."""
        parts = [int(p.strip()) for p in region_str.split(",")]
        if len(parts) != 4:
            raise ValueError(
                f"Invalid region '{region_str}'. Expected 'left,top,width,height'."
            )
        left, top, width, height = parts
        if width <= 0 or height <= 0:
            raise ValueError("Region width and height must be positive integers.")
        return cls(left=left, top=top, width=width, height=height)


# Height-anchored center-relative ROI presets (100% ultrawide & multi-DPI safe).
# Derived directly from calibrated 1920x1080 in-game Riven screenshots.
RIVEN_HEIGHT_ANCHORED_PRESETS: Dict[str, Optional[HeightAnchoredROI]] = {
    "full": None,
    "chat_link": HeightAnchoredROI.from_1080p_box(left=730, top=190, width=460, height=680),
    "mod_station": HeightAnchoredROI.from_1080p_box(left=710, top=175, width=500, height=700),
    "reroll_center": HeightAnchoredROI.from_1080p_box(left=785, top=430, width=355, height=480),
    "reroll_left": HeightAnchoredROI.from_1080p_box(left=455, top=480, width=295, height=390),
    "reroll_right": HeightAnchoredROI.from_1080p_box(left=785, top=430, width=355, height=480),
}

# Backwards-compatible 1080p pixel presets
RIVEN_ROI_PRESETS_1080P: Dict[str, Optional[ScreenRegion]] = {
    name: (roi.to_screen_region(BASE_WIDTH, BASE_HEIGHT) if roi is not None else None)
    for name, roi in RIVEN_HEIGHT_ANCHORED_PRESETS.items()
}


def compute_preset_region(
    preset_name: str,
    screen_width: int,
    screen_height: int,
    auto_scale: bool = True,
) -> Optional[ScreenRegion]:
    """Compute exact pixel ScreenRegion for a preset on any monitor resolution."""
    if preset_name not in RIVEN_HEIGHT_ANCHORED_PRESETS:
        valid = ", ".join(sorted(RIVEN_HEIGHT_ANCHORED_PRESETS.keys()))
        raise ValueError(f"Unknown preset '{preset_name}'. Valid presets: {valid}")
    anchor = RIVEN_HEIGHT_ANCHORED_PRESETS[preset_name]
    if anchor is None:
        return None
    if auto_scale:
        return anchor.to_screen_region(screen_width, screen_height)
    return RIVEN_ROI_PRESETS_1080P[preset_name].clamp(screen_width, screen_height)  # type: ignore[union-attr]


def _crop_region(image: np.ndarray, region: ScreenRegion) -> np.ndarray:
    h, w = image.shape[:2]
    clamped = region.clamp(w, h)
    return image[
        clamped.top : clamped.top + clamped.height,
        clamped.left : clamped.left + clamped.width,
    ].copy()


def _riven_card_visual_score(crop: np.ndarray) -> float:
    """Fast (<2ms) heuristic scoring whether a crop contains a bright active Warframe Riven card."""
    if crop is None or crop.size == 0:
        return 0.0
    h, w = crop.shape[:2]
    if h < 80 or w < 60:
        return 0.0

    panel = crop[int(h * 0.42) : int(h * 0.86), int(w * 0.14) : int(w * 0.86)]
    if panel.size == 0:
        return 0.0
    hsv = cv2.cvtColor(panel, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    s = hsv[:, :, 1]
    dark_ratio = float(np.mean(v < 78))
    text_ratio = float(np.mean((v > 125) & (s < 145)))

    full_hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    fh, fs, fv = full_hsv[:, :, 0], full_hsv[:, :, 1], full_hsv[:, :, 2]
    purple_ratio = float(np.mean((fh >= 115) & (fh <= 165) & (fs >= 40) & (fv >= 55)))

    score = 0.0
    if 0.45 <= dark_ratio <= 0.96:
        score += 0.45
    if 0.025 <= text_ratio <= 0.38:
        score += 0.35
    if purple_ratio >= 0.05:
        score += min(0.20, purple_ratio * 0.8)
    return score


def has_left_reroll_card(left_crop: np.ndarray) -> bool:
    """Detect whether the left Kuva-reroll slot (`reroll_left`) holds a `[当前]` Riven card.

    On Warframe's single-card cycling screen, the region `455..750` contains bright
    rotating purple 3D crystal shards (`bright_crystal >= 0.16`, `panel_dark <= 0.52`).
    On the dual comparison screen, the crystal shifts right and the left slot holds
    the `[当前]` Riven card with a clean dark text interior (`panel_dark >= 0.58`,
    `bright_crystal < 0.10`) and pale-lavender stat text.
    """
    if left_crop is None or left_crop.size == 0:
        return False
    h, w = left_crop.shape[:2]
    if h < 80 or w < 60:
        return False

    hsv = cv2.cvtColor(left_crop, cv2.COLOR_BGR2HSV)
    v_all = hsv[:, :, 2]
    bright_crystal = float(np.mean(v_all > 185))

    panel = hsv[int(h * 0.48) : int(h * 0.86), int(w * 0.14) : int(w * 0.86)]
    pv = panel[:, :, 2]
    ps = panel[:, :, 1]
    panel_dark = float(np.mean(pv < 68))
    panel_text = float(np.mean((pv >= 65) & (pv <= 200) & (ps < 155)))

    return bright_crystal < 0.10 and panel_dark >= 0.58 and panel_text >= 0.08


def detect_layout_crops(
    full_image: np.ndarray,
    auto_scale: bool = True,
) -> Tuple[str, list[Tuple[str, np.ndarray]]]:
    """Automatically detect single-card vs dual-card (Kuva reroll comparison) layout.

    Returns:
        (layout_name, list_of_(label, bgr_crop))
        - Dual reroll comparison: ("reroll_dual", [("[当前]", left), ("[新循环]", right)])
        - Single card view: prioritized single-card crops for the caller to evaluate.
    """
    h, w = full_image.shape[:2]

    def _get(preset_name: str) -> np.ndarray:
        reg = RIVEN_ROI_PRESETS_1080P[preset_name]
        assert reg is not None
        if auto_scale:
            reg = reg.scale_for_resolution(w, h)
        return _crop_region(full_image, reg)

    left_crop = _get("reroll_left")
    right_crop = _get("reroll_right")  # same slot as reroll_center
    chat_crop = _get("chat_link")

    right_score = _riven_card_visual_score(right_crop)

    if right_score >= 0.65 and has_left_reroll_card(left_crop):
        return "reroll_dual", [("[当前]", left_crop), ("[新循环]", right_crop)]

    if right_score >= 0.65:
        return "reroll_center", [("当前紫卡", right_crop), ("聊天/仓库预览", chat_crop)]

    return "chat_link", [("当前紫卡", chat_crop), ("循环单卡", right_crop)]


def extract_dual_crops(
    full_image: np.ndarray,
    auto_scale: bool = True,
) -> list[Tuple[str, np.ndarray]]:
    """Extract left ([当前]) and right ([新循环]) crops from a full-screen image."""
    h, w = full_image.shape[:2]
    left_reg = RIVEN_ROI_PRESETS_1080P["reroll_left"]
    right_reg = RIVEN_ROI_PRESETS_1080P["reroll_right"]
    assert left_reg is not None and right_reg is not None
    if auto_scale:
        left_reg = left_reg.scale_for_resolution(w, h)
        right_reg = right_reg.scale_for_resolution(w, h)
    return [
        ("[当前]", _crop_region(full_image, left_reg)),
        ("[新循环]", _crop_region(full_image, right_reg)),
    ]


def extract_change_signature(
    image: np.ndarray,
    preset: str = "auto",
    auto_scale: bool = True,
) -> np.ndarray:
    """Build a compact blurred grayscale thumbnail over Riven text interiors for MSE diffing.

    Focusing strictly on the card text interior(s) ignores animated background crystals,
    lightning particles, and FPS overlays so static Riven screens yield near-zero MSE.
    """
    h, w = image.shape[:2]

    def _text_interior_thumb(crop: np.ndarray, size: Tuple[int, int] = (96, 64)) -> np.ndarray:
        ch, cw = crop.shape[:2]
        # Card text area sits in lower-middle interior (avoids outer animated purple border)
        sub = crop[int(ch * 0.36) : int(ch * 0.88), int(cw * 0.10) : int(cw * 0.90)]
        if sub.size == 0:
            sub = crop
        gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        return cv2.GaussianBlur(small, (3, 3), 0)

    if preset in ("auto", "reroll_dual") and w >= 900 and h >= 600:
        thumbs = []
        roi_names = ("reroll_left", "reroll_right")
        for name in roi_names:
            reg = RIVEN_ROI_PRESETS_1080P[name]
            assert reg is not None
            if auto_scale:
                reg = reg.scale_for_resolution(w, h)
            thumbs.append(_text_interior_thumb(_crop_region(image, reg), size=(96, 64)))
        return np.hstack(thumbs)

    return _text_interior_thumb(image, size=(120, 80))


def extract_crops_change_signature(crops: Dict[str, np.ndarray]) -> np.ndarray:
    """Compute a compact blurred grayscale signature over one or more pre-cropped ROI images."""
    thumbs = []
    for key in sorted(crops.keys()):
        crop = crops[key]
        if crop is None or crop.size == 0:
            continue
        ch, cw = crop.shape[:2]
        sub = crop[int(ch * 0.12) : int(ch * 0.92), int(cw * 0.06) : int(cw * 0.94)]
        if sub.size == 0:
            sub = crop
        gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (96, 64), interpolation=cv2.INTER_AREA)
        thumbs.append(cv2.GaussianBlur(small, (3, 3), 0))
    if not thumbs:
        return np.zeros((64, 96), dtype=np.uint8)
    return np.hstack(thumbs) if len(thumbs) > 1 else thumbs[0]


def compute_frame_mse(sig_a: np.ndarray, sig_b: np.ndarray) -> float:
    """Compute pixel Mean Squared Error (MSE) between two grayscale thumbnails."""
    if sig_a.shape != sig_b.shape:
        return float("inf")
    diff = sig_a.astype(np.float32) - sig_b.astype(np.float32)
    return float(np.mean(diff * diff))


def compute_frame_correlation(sig_a: np.ndarray, sig_b: np.ndarray) -> float:
    """Compute normalized Pearson correlation in `[-1.0, 1.0]` between two grayscale thumbnails."""
    if sig_a.shape != sig_b.shape or sig_a.size == 0:
        return 0.0
    a = sig_a.astype(np.float32).ravel()
    b = sig_b.astype(np.float32).ravel()
    a_centered = a - float(np.mean(a))
    b_centered = b - float(np.mean(b))
    denom = float(np.linalg.norm(a_centered) * np.linalg.norm(b_centered))
    if denom < 1e-6:
        # Both frames are flat/uniform; treat as matching if intensity is close
        return 1.0 if float(np.mean(np.abs(a - b))) < 12.0 else 0.0
    return float(np.clip(np.dot(a_centered, b_centered) / denom, -1.0, 1.0))


def is_page_exited(
    anchor_sig: np.ndarray,
    curr_sig: np.ndarray,
    *,
    corr_threshold: float = 0.52,
    mse_threshold: float = 320.0,
    extreme_mse_threshold: float = 1350.0,
) -> Tuple[bool, float, float]:
    """Determine whether the user has left an F9-captured Riven or Void Relic screen.

    Combines structural Pearson correlation (robust to Warframe background particles,
    hover borders, and teammate relic picks) with pixel MSE so subtle in-page animations
    never trigger false exits while actually closing the Riven/Relic page is caught cleanly.

    Returns:
        `(exited, mse, corr)`
    """
    mse = compute_frame_mse(anchor_sig, curr_sig)
    corr = compute_frame_correlation(anchor_sig, curr_sig)
    exited = (mse >= extreme_mse_threshold) or (corr < corr_threshold and mse >= mse_threshold)
    return exited, mse, corr


class ScreenCapturer:
    """High-speed screen capture and local screenshot loader (Windows/X11 `mss` + Linux Wayland)."""

    def __init__(self, monitor_index: int = 1) -> None:
        """Initialize capturer.

        Args:
            monitor_index: 1-based monitor index used by `mss` (1 = primary display).
        """
        self.monitor_index = monitor_index
        self._wayland_capturer = None

    def _get_wayland_capturer(self):
        from src.wayland_support import WaylandScreenCapturer, is_linux_wayland

        if not is_linux_wayland():
            return None
        if self._wayland_capturer is None:
            wl = WaylandScreenCapturer()
            if wl.backend == "mss":
                return None
            self._wayland_capturer = wl
        return self._wayland_capturer

    def get_monitor_info(self, monitor_index: Optional[int] = None) -> Dict[str, int]:
        """Return target monitor geometry (`left`, `top`, `width`, `height`)."""
        idx = self.monitor_index if monitor_index is None else monitor_index
        wl = self._get_wayland_capturer()
        if wl is not None:
            from src.wayland_support import get_wayland_monitors

            mons = get_wayland_monitors()
            if idx < 0 or idx >= len(mons):
                raise ValueError(
                    f"Monitor index {idx} out of range (available: 0..{len(mons) - 1})."
                )
            return mons[idx].to_dict()
        with mss.mss() as sct:
            if idx < 0 or idx >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {idx} out of range (available: 0..{len(sct.monitors) - 1})."
                )
            return dict(sct.monitors[idx])

    def resolve_region(
        self,
        screen_width: int,
        screen_height: int,
        region: Optional[ScreenRegion] = None,
        preset: Optional[str] = None,
        auto_scale: bool = True,
    ) -> Optional[ScreenRegion]:
        """Resolve an explicit region or preset name, applying resolution scaling."""
        target = region
        if target is None and preset is not None:
            if preset not in RIVEN_ROI_PRESETS_1080P:
                valid = ", ".join(sorted(RIVEN_ROI_PRESETS_1080P.keys()))
                raise ValueError(f"Unknown preset '{preset}'. Valid presets: {valid}")
            target = RIVEN_ROI_PRESETS_1080P[preset]

        if target is None:
            return None

        if auto_scale:
            target = target.scale_for_resolution(screen_width, screen_height)

        return target.clamp(screen_width, screen_height)

    def capture_region_box(
        self,
        region: ScreenRegion,
        monitor_index: Optional[int] = None,
    ) -> Tuple[np.ndarray, Dict[str, int]]:
        """Capture an explicit physical-pixel ScreenRegion relative to target monitor."""
        idx = self.monitor_index if monitor_index is None else monitor_index
        wl = self._get_wayland_capturer()
        if wl is not None:
            mon_info = self.get_monitor_info(idx)
            clamped = region.clamp(mon_info["width"], mon_info["height"])
            bgr, actual_mon = wl.grab_region(
                clamped.left, clamped.top, clamped.width, clamped.height, monitor_index=idx
            )
            meta = {
                "monitor_index": idx,
                "monitor_width": actual_mon.width,
                "monitor_height": actual_mon.height,
                "capture_left": actual_mon.left + clamped.left,
                "capture_top": actual_mon.top + clamped.top,
                "capture_width": int(bgr.shape[1]),
                "capture_height": int(bgr.shape[0]),
            }
            return bgr, meta
        with mss.mss() as sct:
            if idx < 0 or idx >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {idx} out of range (available: 0..{len(sct.monitors) - 1})."
                )
            mon = sct.monitors[idx]
            clamped = region.clamp(mon["width"], mon["height"])
            grab_box = clamped.to_mss_dict(monitor_left=mon["left"], monitor_top=mon["top"])
            frame = np.asarray(sct.grab(grab_box), dtype=np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            meta = {
                "monitor_index": idx,
                "monitor_width": mon["width"],
                "monitor_height": mon["height"],
                "capture_left": grab_box["left"],
                "capture_top": grab_box["top"],
                "capture_width": grab_box["width"],
                "capture_height": grab_box["height"],
            }
            return bgr, meta

    def capture_mode_regions(
        self,
        mode_name: str,
        roi_manager: Optional[object] = None,
        monitor_index: Optional[int] = None,
    ) -> Tuple[Dict[str, np.ndarray], Dict[str, int]]:
        """Capture all ROI boxes defined for `mode_name` via `roi_manager.get_region(...)`.

        Uses High-DPI physical monitor dimensions reported by `mss` so saved
        calibration coordinates align 1:1 with real screen pixels.
        """
        from src.roi_manager import ROI_MANAGER

        mgr = roi_manager if roi_manager is not None else ROI_MANAGER
        idx = self.monitor_index if monitor_index is None else monitor_index
        wl = self._get_wayland_capturer()
        if wl is not None:
            # Capture full monitor ONCE on Wayland and slice all ROI boxes in memory
            full_bgr, actual_mon = wl.grab_monitor_full(monitor_index=idx)
            mon_w = int(actual_mon.width)
            mon_h = int(actual_mon.height)
            mode_regions = mgr.get_mode_regions(mode_name, mon_w, mon_h)  # type: ignore[attr-defined]
            crops: Dict[str, np.ndarray] = {}
            region_ratios: Dict[str, Tuple[float, float, float, float]] = {}
            for box_key, reg in mode_regions.items():
                cl = reg.clamp(mon_w, mon_h)
                crops[box_key] = full_bgr[cl.top : cl.top + cl.height, cl.left : cl.left + cl.width].copy()
                if mon_w > 0 and mon_h > 0:
                    region_ratios[box_key] = (
                        float(cl.left) / float(mon_w),
                        float(cl.top) / float(mon_h),
                        float(cl.width) / float(mon_w),
                        float(cl.height) / float(mon_h),
                    )
            meta = {
                "monitor_index": idx,
                "monitor_width": mon_w,
                "monitor_height": mon_h,
                "mode": mode_name,
                "region_ratios": region_ratios,
            }
            return crops, meta
        with mss.mss() as sct:
            if idx < 0 or idx >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {idx} out of range (available: 0..{len(sct.monitors) - 1})."
                )
            mon = sct.monitors[idx]
            mon_w = int(mon["width"])
            mon_h = int(mon["height"])
            mode_regions = mgr.get_mode_regions(mode_name, mon_w, mon_h)  # type: ignore[attr-defined]
            crops: Dict[str, np.ndarray] = {}
            region_ratios: Dict[str, Tuple[float, float, float, float]] = {}
            for box_key, reg in mode_regions.items():
                grab_box = reg.to_mss_dict(monitor_left=mon["left"], monitor_top=mon["top"])
                frame = np.asarray(sct.grab(grab_box), dtype=np.uint8)
                crops[box_key] = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                if mon_w > 0 and mon_h > 0:
                    region_ratios[box_key] = (
                        float(reg.left) / float(mon_w),
                        float(reg.top) / float(mon_h),
                        float(reg.width) / float(mon_w),
                        float(reg.height) / float(mon_h),
                    )
            meta = {
                "monitor_index": idx,
                "monitor_width": mon_w,
                "monitor_height": mon_h,
                "mode": mode_name,
                "region_ratios": region_ratios,
            }
            return crops, meta

    def capture_screen(
        self,
        region: Optional[ScreenRegion] = None,
        preset: Optional[str] = None,
        monitor_index: Optional[int] = None,
        auto_scale: bool = True,
    ) -> Tuple[np.ndarray, Dict[str, int]]:
        """Capture the screen or a scaled ROI and return (BGR image, capture_meta).

        Returns:
            Tuple of:
                - `image`: OpenCV BGR uint8 numpy array.
                - `meta`: dict containing monitor size and actual captured box.
        """
        idx = self.monitor_index if monitor_index is None else monitor_index
        wl = self._get_wayland_capturer()
        if wl is not None:
            full_bgr, actual_mon = wl.grab_monitor_full(monitor_index=idx)
            resolved = self.resolve_region(
                screen_width=actual_mon.width,
                screen_height=actual_mon.height,
                region=region,
                preset=preset,
                auto_scale=auto_scale,
            )
            if resolved is None:
                bgr = full_bgr
                cap_l, cap_t, cap_w, cap_h = actual_mon.left, actual_mon.top, actual_mon.width, actual_mon.height
            else:
                cl = resolved.clamp(actual_mon.width, actual_mon.height)
                bgr = full_bgr[cl.top : cl.top + cl.height, cl.left : cl.left + cl.width].copy()
                cap_l = actual_mon.left + cl.left
                cap_t = actual_mon.top + cl.top
                cap_w, cap_h = cl.width, cl.height
            meta = {
                "monitor_index": idx,
                "monitor_width": actual_mon.width,
                "monitor_height": actual_mon.height,
                "capture_left": cap_l,
                "capture_top": cap_t,
                "capture_width": cap_w,
                "capture_height": cap_h,
            }
            return bgr, meta
        with mss.mss() as sct:
            if idx < 0 or idx >= len(sct.monitors):
                raise ValueError(
                    f"Monitor index {idx} out of range (available: 0..{len(sct.monitors) - 1})."
                )
            mon = sct.monitors[idx]
            resolved = self.resolve_region(
                screen_width=mon["width"],
                screen_height=mon["height"],
                region=region,
                preset=preset,
                auto_scale=auto_scale,
            )
            if resolved is None:
                grab_box = {
                    "left": mon["left"],
                    "top": mon["top"],
                    "width": mon["width"],
                    "height": mon["height"],
                }
            else:
                grab_box = resolved.to_mss_dict(
                    monitor_left=mon["left"],
                    monitor_top=mon["top"],
                )

            sct_img = sct.grab(grab_box)
            # mss returns BGRA; convert to standard OpenCV BGR without alpha channel
            frame = np.asarray(sct_img, dtype=np.uint8)
            bgr = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

            meta = {
                "monitor_index": idx,
                "monitor_width": mon["width"],
                "monitor_height": mon["height"],
                "capture_left": grab_box["left"],
                "capture_top": grab_box["top"],
                "capture_width": grab_box["width"],
                "capture_height": grab_box["height"],
            }
            return bgr, meta

    def load_image(
        self,
        image_path: str | Path,
        region: Optional[ScreenRegion] = None,
        preset: Optional[str] = None,
        auto_scale: bool = True,
    ) -> np.ndarray:
        """Load a local screenshot image safely (supports Chinese/Unicode paths on Windows).

        Optionally crops the image using `region` or a 1080p `preset` (auto-scaled
        to the loaded image dimensions when `auto_scale=True`).
        """
        path = Path(image_path)
        if not path.is_file():
            raise FileNotFoundError(f"Image file not found: {path}")

        # Use numpy.fromfile + cv2.imdecode to handle non-ASCII Windows paths cleanly
        raw_bytes = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(raw_bytes, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Failed to decode image file: {path}")

        height, width = image.shape[:2]
        resolved = self.resolve_region(
            screen_width=width,
            screen_height=height,
            region=region,
            preset=preset,
            auto_scale=auto_scale,
        )
        if resolved is not None:
            image = image[
                resolved.top : resolved.top + resolved.height,
                resolved.left : resolved.left + resolved.width,
            ].copy()

        return image

    @staticmethod
    def save_image(image: np.ndarray, output_path: str | Path) -> Path:
        """Save an OpenCV image to disk with full Unicode path support."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        suffix = path.suffix.lower() or ".png"
        ok, encoded = cv2.imencode(suffix, image)
        if not ok:
            raise RuntimeError(f"Failed to encode image as '{suffix}'.")
        path.write_bytes(encoded.tobytes())
        return path
