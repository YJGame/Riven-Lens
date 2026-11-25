"""Multi-scene ROI profile manager & 5-state work mode state machine.

Manages 4 independent capture scenarios stored per screen resolution in
`data/roi_profiles.json` with relative `[0.0 .. 1.0]` fallback ratios and
height-anchored factory defaults:
  1. `single_riven`  -> 1 box  (`single_riven`)
  2. `reroll`        -> 2 boxes (`reroll_left`, `reroll_right`)
  3. `chat_riven`    -> 1 box  (`chat_riven`)
  4. `relic_rewards` -> 1 box  (`relic_rewards`)

Also defines `ModeStateMachine` implementing the 5-state F8 cycle:
  `single_riven` (auto OCR) -> `reroll` (auto OCR) -> `chat_riven` (F9 capture)
  -> `relic_rewards` (F9 capture) -> `paused` (standby) -> ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.capture import HeightAnchoredROI, ScreenRegion
from src.data_updater import get_data_dir

DEFAULT_ROI_PROFILES_FILENAME = "roi_profiles.json"

# 4 calibration / operational business modes
MODE_SINGLE_RIVEN = "single_riven"
MODE_REROLL = "reroll"
MODE_CHAT_RIVEN = "chat_riven"
MODE_RELIC_REWARDS = "relic_rewards"
MODE_PAUSED = "paused"

CALIBRATION_MODES: Tuple[str, ...] = (
    MODE_SINGLE_RIVEN,
    MODE_REROLL,
    MODE_CHAT_RIVEN,
    MODE_RELIC_REWARDS,
)

# 5-state F8 rotation cycle
MODE_CYCLE_ORDER: Tuple[str, ...] = (
    MODE_SINGLE_RIVEN,
    MODE_REROLL,
    MODE_CHAT_RIVEN,
    MODE_RELIC_REWARDS,
    MODE_PAUSED,
)

AUTO_OCR_MODES = frozenset({MODE_SINGLE_RIVEN, MODE_REROLL})
MANUAL_F9_CAPTURE_MODES = frozenset({MODE_CHAT_RIVEN, MODE_RELIC_REWARDS})


@dataclass(frozen=True)
class ROIBoxSpec:
    """Metadata & factory default anchor for one named capture box."""

    key: str
    mode: str
    label_zh: str
    label_en: str
    color_hex: str
    default_anchor: HeightAnchoredROI


# Factory default boxes anchored to screen center & effective height H (ultrawide & HiDPI safe)
ROI_BOX_SPECS: Dict[str, ROIBoxSpec] = {
    "single_riven": ROIBoxSpec(
        key="single_riven",
        mode=MODE_SINGLE_RIVEN,
        label_zh="单张紫卡识别区",
        label_en="Single Riven Area",
        color_hex="#22C55E",  # Green
        default_anchor=HeightAnchoredROI.from_1080p_box(left=785, top=430, width=355, height=480),
    ),
    "reroll_left": ROIBoxSpec(
        key="reroll_left",
        mode=MODE_REROLL,
        label_zh="洗卡左侧 [旧卡]",
        label_en="Reroll Left [Current]",
        color_hex="#38BDF8",  # Blue
        default_anchor=HeightAnchoredROI.from_1080p_box(left=455, top=480, width=295, height=390),
    ),
    "reroll_right": ROIBoxSpec(
        key="reroll_right",
        mode=MODE_REROLL,
        label_zh="洗卡右侧 [新卡]",
        label_en="Reroll Right [New]",
        color_hex="#A855F7",  # Purple
        default_anchor=HeightAnchoredROI.from_1080p_box(left=785, top=430, width=355, height=480),
    ),
    "chat_riven": ROIBoxSpec(
        key="chat_riven",
        mode=MODE_CHAT_RIVEN,
        label_zh="聊天链接紫卡预览区",
        label_en="Chat Link Riven Area",
        color_hex="#F97316",  # Orange
        default_anchor=HeightAnchoredROI.from_1080p_box(left=730, top=190, width=460, height=680),
    ),
    "relic_rewards": ROIBoxSpec(
        key="relic_rewards",
        mode=MODE_RELIC_REWARDS,
        label_zh="开核桃 4 选 1 奖励条",
        label_en="Relic Rewards Strip",
        color_hex="#06B6D4",  # Cyan
        default_anchor=HeightAnchoredROI.from_1080p_box(left=420, top=390, width=1080, height=140),
    ),
}

MODE_BOX_KEYS: Dict[str, Tuple[str, ...]] = {
    MODE_SINGLE_RIVEN: ("single_riven",),
    MODE_REROLL: ("reroll_left", "reroll_right"),
    MODE_CHAT_RIVEN: ("chat_riven",),
    MODE_RELIC_REWARDS: ("relic_rewards",),
}

MODE_META: Dict[str, Dict[str, str]] = {
    MODE_SINGLE_RIVEN: {
        "short_zh": "单张紫卡",
        "short_en": "Single Riven",
        "badge_zh": "[模式 1/4] 单张紫卡（实时自动识别）",
        "badge_en": "[Mode 1/4] Single Riven (Auto OCR)",
        "color": "#22C55E",
    },
    MODE_REROLL: {
        "short_zh": "洗卡双卡对比",
        "short_en": "Dual Reroll Compare",
        "badge_zh": "[模式 2/4] 洗卡双卡对比（实时自动识别）",
        "badge_en": "[Mode 2/4] Dual Reroll (Auto OCR)",
        "color": "#A855F7",
    },
    MODE_CHAT_RIVEN: {
        "short_zh": "聊天链接紫卡",
        "short_en": "Chat Link Riven",
        "badge_zh": "[模式 3/4] 聊天链接紫卡（按 {f9} 截图识别）",
        "badge_en": "[Mode 3/4] Chat Riven (Press {f9} to Scan)",
        "color": "#F97316",
    },
    MODE_RELIC_REWARDS: {
        "short_zh": "开核桃奖励比价",
        "short_en": "Relic Rewards",
        "badge_zh": "[模式 4/4] 开核桃奖励比价（按 {f9} 截图识别）",
        "badge_en": "[Mode 4/4] Relic Rewards (Press {f9} to Scan)",
        "color": "#06B6D4",
    },
    MODE_PAUSED: {
        "short_zh": "已暂停待命",
        "short_en": "Paused / Standby",
        "badge_zh": "[已暂停] 后台待命",
        "badge_en": "[Paused] Standby",
        "color": "#94A3B8",
    },
}


def format_mode_badge(mode: str, lang: str = "zh_CN", f9_key: str = "F9") -> str:
    from src.i18n import I18N

    valid_mode = mode if mode in MODE_META else MODE_PAUSED
    return I18N.t(f"modes.badge_{valid_mode}", lang, f9=f9_key or "F9")


def format_mode_short(mode: str, lang: str = "zh_CN") -> str:
    from src.i18n import I18N

    valid_mode = mode if mode in MODE_META else MODE_PAUSED
    return I18N.t(f"modes.short_{valid_mode}", lang)


def get_mode_accent_color(mode: str) -> str:
    return MODE_META.get(mode, MODE_META[MODE_PAUSED])["color"]


class ModeStateMachine:
    """5-state work mode state machine cycled by F8 (or directly selected from Tray/Panel)."""

    def __init__(self, initial_mode: str = MODE_PAUSED) -> None:
        self._mode: str = initial_mode if initial_mode in MODE_CYCLE_ORDER else MODE_PAUSED

    @property
    def current_mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> str:
        if mode not in MODE_CYCLE_ORDER:
            raise ValueError(f"Invalid work mode '{mode}'. Valid: {MODE_CYCLE_ORDER}")
        self._mode = mode
        return self._mode

    def cycle_next(self) -> str:
        """Advance to the next mode in the 5-state F8 rotation."""
        if self._mode == MODE_PAUSED:
            self._mode = MODE_SINGLE_RIVEN
            return self._mode
        idx = MODE_CYCLE_ORDER.index(self._mode)
        self._mode = MODE_CYCLE_ORDER[(idx + 1) % len(MODE_CYCLE_ORDER)]
        return self._mode

    def is_auto_ocr(self, mode: Optional[str] = None) -> bool:
        m = mode or self._mode
        return m in AUTO_OCR_MODES

    def is_manual_f9_capture(self, mode: Optional[str] = None) -> bool:
        m = mode or self._mode
        return m in MANUAL_F9_CAPTURE_MODES

    def is_paused(self, mode: Optional[str] = None) -> bool:
        return (mode or self._mode) == MODE_PAUSED

    def f9_action_type(self, mode: Optional[str] = None) -> str:
        """Return what F9 should do in the given mode: 'wm_price' | 'capture_chat_riven' | 'capture_relic_rewards'."""
        m = mode or self._mode
        if m == MODE_CHAT_RIVEN:
            return "capture_chat_riven"
        if m == MODE_RELIC_REWARDS:
            return "capture_relic_rewards"
        return "wm_price"


def get_roi_profiles_path() -> Path:
    return get_data_dir() / DEFAULT_ROI_PROFILES_FILENAME


class ROIManager:
    """Per-resolution & ratio-backed ROI coordinate profile manager (`data/roi_profiles.json`)."""

    def __init__(self, profiles_path: Optional[str | Path] = None) -> None:
        self.profiles_path = Path(profiles_path) if profiles_path else get_roi_profiles_path()
        self.resolutions: Dict[str, Dict[str, Dict[str, int]]] = {}
        self.ratios: Dict[str, Dict[str, float]] = {}
        self.reload()

    def reload(self) -> None:
        self.resolutions = {}
        self.ratios = {}
        if not self.profiles_path.is_file():
            return
        try:
            raw = json.loads(self.profiles_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                res_map = raw.get("resolutions")
                if isinstance(res_map, dict):
                    for res_key, boxes in res_map.items():
                        if isinstance(boxes, dict):
                            self.resolutions[str(res_key)] = {
                                str(k): dict(v) for k, v in boxes.items() if isinstance(v, dict)
                            }
                rat_map = raw.get("ratios")
                if isinstance(rat_map, dict):
                    for k, v in rat_map.items():
                        if isinstance(v, dict):
                            self.ratios[str(k)] = dict(v)
        except Exception:
            pass

    def save(self) -> Path:
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "resolutions": self.resolutions,
            "ratios": self.ratios,
        }
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.profiles_path.with_suffix(self.profiles_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.profiles_path)
        return self.profiles_path

    @staticmethod
    def _res_key(screen_w: int, screen_h: int) -> str:
        return f"{int(screen_w)}x{int(screen_h)}"

    def get_default_region(self, region_key: str, screen_w: int, screen_h: int) -> ScreenRegion:
        """Compute factory default height-anchored physical pixel box for any resolution."""
        key = self._normalize_region_key(region_key)
        spec = ROI_BOX_SPECS[key]
        return spec.default_anchor.to_screen_region(int(screen_w), int(screen_h))

    @staticmethod
    def _normalize_region_key(name: str) -> str:
        alias_map = {
            "reroll_center": "single_riven",
            "mod_station": "single_riven",
            "chat_link": "chat_riven",
        }
        resolved = alias_map.get(name, name)
        if resolved not in ROI_BOX_SPECS:
            valid = ", ".join(sorted(ROI_BOX_SPECS.keys()))
            raise ValueError(f"Unknown ROI region key '{name}'. Valid: {valid}")
        return resolved

    def get_region(
        self,
        region_key: str,
        screen_w: int,
        screen_h: int,
    ) -> ScreenRegion:
        """Resolve physical pixel `ScreenRegion` for `region_key` at `(screen_w, screen_h)`.

        Priority:
          1. Exact saved pixel profile for `{screen_w}x{screen_h}`
          2. Saved relative ratio `[0.0 .. 1.0]` scaled to `(screen_w, screen_h)`
          3. Factory default height-anchored center-relative ROI
        """
        key = self._normalize_region_key(region_key)
        sw = max(1, int(screen_w))
        sh = max(1, int(screen_h))
        rk = self._res_key(sw, sh)

        # 1. Exact resolution match
        res_entry = self.resolutions.get(rk, {}).get(key)
        if isinstance(res_entry, dict):
            try:
                return ScreenRegion(
                    left=int(res_entry["x"]),
                    top=int(res_entry["y"]),
                    width=max(20, int(res_entry["w"])),
                    height=max(20, int(res_entry["h"])),
                ).clamp(sw, sh)
            except Exception:
                pass

        # 2. Cross-resolution saved ratio fallback
        rat_entry = self.ratios.get(key)
        if isinstance(rat_entry, dict):
            try:
                rx = max(0.0, min(1.0, float(rat_entry["x"])))
                ry = max(0.0, min(1.0, float(rat_entry["y"])))
                rw = max(0.02, min(1.0, float(rat_entry["w"])))
                rh = max(0.02, min(1.0, float(rat_entry["h"])))
                return ScreenRegion(
                    left=int(round(rx * sw)),
                    top=int(round(ry * sh)),
                    width=max(20, int(round(rw * sw))),
                    height=max(20, int(round(rh * sh))),
                ).clamp(sw, sh)
            except Exception:
                pass

        # 3. Height-anchored factory default
        return self.get_default_region(key, sw, sh)

    def get_mode_regions(
        self,
        mode_name: str,
        screen_w: int,
        screen_h: int,
    ) -> Dict[str, ScreenRegion]:
        """Return `{box_key: ScreenRegion}` for all boxes belonging to `mode_name`."""
        if mode_name not in MODE_BOX_KEYS:
            raise ValueError(f"Unknown mode '{mode_name}'. Valid: {CALIBRATION_MODES}")
        return {
            box_key: self.get_region(box_key, screen_w, screen_h)
            for box_key in MODE_BOX_KEYS[mode_name]
        }

    def set_region(
        self,
        region_key: str,
        region: ScreenRegion,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """Save calibrated physical pixel box + relative ratios in memory (call `.save()` to persist)."""
        key = self._normalize_region_key(region_key)
        sw = max(1, int(screen_w))
        sh = max(1, int(screen_h))
        clamped = region.clamp(sw, sh)
        rk = self._res_key(sw, sh)

        self.resolutions.setdefault(rk, {})[key] = {
            "x": int(clamped.left),
            "y": int(clamped.top),
            "w": int(clamped.width),
            "h": int(clamped.height),
        }
        self.ratios[key] = {
            "x": round(clamped.left / float(sw), 5),
            "y": round(clamped.top / float(sh), 5),
            "w": round(clamped.width / float(sw), 5),
            "h": round(clamped.height / float(sh), 5),
        }

    def reset_mode(
        self,
        mode_name: str,
        screen_w: Optional[int] = None,
        screen_h: Optional[int] = None,
    ) -> None:
        """Reset one mode's saved boxes back to factory height-anchored defaults."""
        if mode_name not in MODE_BOX_KEYS:
            return
        keys = MODE_BOX_KEYS[mode_name]
        rk = self._res_key(screen_w, screen_h) if (screen_w and screen_h) else None
        for box_key in keys:
            if rk and rk in self.resolutions:
                self.resolutions[rk].pop(box_key, None)
            elif rk is None:
                for res_dict in self.resolutions.values():
                    res_dict.pop(box_key, None)
            self.ratios.pop(box_key, None)

    def reset_all(self) -> None:
        self.resolutions.clear()
        self.ratios.clear()


# Shared application-wide ROI profile manager
ROI_MANAGER = ROIManager()
