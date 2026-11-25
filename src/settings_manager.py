"""Persistent user settings stored in `data/user_settings.json`."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.data_updater import get_data_dir

DEFAULT_SETTINGS_FILENAME = "user_settings.json"
MIN_OVERLAY_FONT_PX = 10
MAX_OVERLAY_FONT_PX = 24
DEFAULT_OVERLAY_FONT_PX = 13
WINDOW_POS_KEYS = ("riven", "wm", "synergy", "relic")
VALID_COLLAPSE_SECONDS = (0, 3, 5, 10)
DEFAULT_PANEL_WIDTH = 600
DEFAULT_PANEL_HEIGHT = 800
MIN_PANEL_WIDTH = 600
MIN_PANEL_HEIGHT = 480
DEFAULT_MISS_COLLAPSE_COUNT = 4
MIN_MISS_COLLAPSE_COUNT = 2
MAX_MISS_COLLAPSE_COUNT = 20


def clamp_miss_collapse_count(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        return DEFAULT_MISS_COLLAPSE_COUNT
    return max(MIN_MISS_COLLAPSE_COUNT, min(MAX_MISS_COLLAPSE_COUNT, n))

# Curated Warframe-inspired overlay color palettes + free custom color support
THEME_COLOR_KEYS = ("bg", "border", "accent", "text")
DEFAULT_THEME_PRESET = "orokin_void"
THEME_PRESET_ORDER = (
    "orokin_void",
    "orokin_prime",
    "corpus_cyber",
    "kuva_crimson",
    "jade_emerald",
    "obsidian_minimal",
)
THEME_PRESETS: Dict[str, Dict[str, str]] = {
    "orokin_void": {
        "bg": "#120C20",
        "border": "#A876FF",
        "accent": "#FFD166",
        "text": "#EFE4FF",
    },
    "orokin_prime": {
        "bg": "#17140D",
        "border": "#F5C542",
        "accent": "#FFE58F",
        "text": "#FFFBEB",
    },
    "corpus_cyber": {
        "bg": "#0B1726",
        "border": "#38BDF8",
        "accent": "#22D3EE",
        "text": "#E0F2FE",
    },
    "kuva_crimson": {
        "bg": "#1D0B12",
        "border": "#F43F5E",
        "accent": "#FB7185",
        "text": "#FFE4E6",
    },
    "jade_emerald": {
        "bg": "#0A1A16",
        "border": "#34D399",
        "accent": "#6EE7B7",
        "text": "#ECFDF5",
    },
    "obsidian_minimal": {
        "bg": "#111318",
        "border": "#64748B",
        "accent": "#F8FAFC",
        "text": "#F1F5F9",
    },
}

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_hex_color(value: Any, fallback: str) -> str:
    s = str(value or "").strip()
    if s and not s.startswith("#") and len(s) == 6:
        s = "#" + s
    if _HEX_COLOR_RE.match(s):
        return s.upper()
    return fallback.upper()


def resolve_theme_colors(
    preset: str = DEFAULT_THEME_PRESET,
    custom_colors: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """Return a clean `{bg, border, accent, text}` hex color dict for a preset or custom override."""
    base = THEME_PRESETS.get(preset) or THEME_PRESETS[DEFAULT_THEME_PRESET]
    if preset != "custom" and not custom_colors:
        return dict(base)
    src = custom_colors if isinstance(custom_colors, dict) else {}
    return {k: normalize_hex_color(src.get(k), base[k]) for k in THEME_COLOR_KEYS}


def hex_to_rgba(hex_color: str, alpha: int = 232) -> str:
    """Convert `#RRGGBB` + alpha (0..255) into a Qt stylesheet `rgba(r, g, b, a)` string."""
    h = normalize_hex_color(hex_color, "#120C20")
    r = int(h[1:3], 16)
    g = int(h[3:5], 16)
    b = int(h[5:7], 16)
    a = max(0, min(255, int(alpha)))
    return f"rgba({r}, {g}, {b}, {a})"


def _parse_panel_geometry(value: Any) -> Optional[List[int]]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = (int(round(float(v))) for v in value)
    except Exception:
        return None
    if w < 300 or h < 300 or w > 10000 or h > 10000:
        return None
    return [x, y, max(MIN_PANEL_WIDTH, w), max(MIN_PANEL_HEIGHT, h)]


def normalize_collapse_seconds(value: Any) -> int:
    try:
        sec = int(value)
    except Exception:
        return 5
    return sec if sec in VALID_COLLAPSE_SECONDS else 5


def clamp_overlay_font_size(value: Any) -> int:
    try:
        px = int(round(float(value)))
    except Exception:
        return DEFAULT_OVERLAY_FONT_PX
    return max(MIN_OVERLAY_FONT_PX, min(MAX_OVERLAY_FONT_PX, px))


def _parse_ratio(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if not (f == f):  # NaN guard
        return None
    return max(0.0, min(1.0, round(f, 5)))


def _parse_pos_pair(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    rx = _parse_ratio(value[0])
    ry = _parse_ratio(value[1])
    if rx is None or ry is None:
        return None
    return [rx, ry]


def get_settings_path() -> Path:
    return get_data_dir() / DEFAULT_SETTINGS_FILENAME


@dataclass
class UserSettings:
    """Persisted user preferences, custom global hotkeys, and independent overlay window positions."""

    hotkey_toggle_monitor: str = "F8"
    hotkey_fetch_wm: str = "F9"
    hotkey_hide_overlay: str = "ESC"
    hotkey_open_panel: str = "Ctrl+Alt+D"
    language_mode: str = "auto"  # "auto" | any of 15 SUPPORTED_LANGS
    start_monitoring_on_launch: bool = False
    overlay_pos_x_ratio: Optional[float] = None
    overlay_pos_y_ratio: Optional[float] = None
    riven_pos: Optional[List[float]] = None
    wm_pos: Optional[List[float]] = None
    synergy_pos: Optional[List[float]] = None
    relic_pos: Optional[List[float]] = None
    overlay_font_size: int = DEFAULT_OVERLAY_FONT_PX
    auto_collapse_seconds: int = 0
    miss_collapse_count: int = DEFAULT_MISS_COLLAPSE_COUNT
    auto_hide_on_focus_loss: bool = True
    audio_cue_enabled: bool = True
    custom_rare_sound_path: str = ""
    panel_geometry: Optional[List[int]] = None  # [x, y, width, height]
    theme_preset: str = DEFAULT_THEME_PRESET
    theme_colors: Dict[str, str] = field(
        default_factory=lambda: dict(THEME_PRESETS[DEFAULT_THEME_PRESET])
    )
    first_run_guide_shown: bool = False

    def __post_init__(self) -> None:
        # Keep legacy `overlay_pos_x_ratio`/`overlay_pos_y_ratio` synchronized with `riven_pos`
        if self.riven_pos is None and self.overlay_pos_x_ratio is not None and self.overlay_pos_y_ratio is not None:
            self.riven_pos = [round(float(self.overlay_pos_x_ratio), 5), round(float(self.overlay_pos_y_ratio), 5)]
        elif self.riven_pos is not None and (self.overlay_pos_x_ratio is None or self.overlay_pos_y_ratio is None):
            self.overlay_pos_x_ratio = round(float(self.riven_pos[0]), 5)
            self.overlay_pos_y_ratio = round(float(self.riven_pos[1]), 5)
        if self.theme_preset not in THEME_PRESETS and self.theme_preset != "custom":
            self.theme_preset = DEFAULT_THEME_PRESET
        if self.theme_preset in THEME_PRESETS and not self.theme_colors:
            self.theme_colors = dict(THEME_PRESETS[self.theme_preset])
        else:
            self.theme_colors = resolve_theme_colors(self.theme_preset, self.theme_colors)

    def get_window_pos(self, key: str) -> Tuple[Optional[float], Optional[float]]:
        pair = getattr(self, f"{key}_pos", None)
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            return float(pair[0]), float(pair[1])
        if key == "riven" and self.overlay_pos_x_ratio is not None and self.overlay_pos_y_ratio is not None:
            return float(self.overlay_pos_x_ratio), float(self.overlay_pos_y_ratio)
        return None, None

    def set_window_pos(self, key: str, rx: Optional[float], ry: Optional[float]) -> None:
        if key not in WINDOW_POS_KEYS:
            return
        if rx is None or ry is None:
            setattr(self, f"{key}_pos", None)
            if key == "riven":
                self.overlay_pos_x_ratio = None
                self.overlay_pos_y_ratio = None
        else:
            cx = max(0.0, min(1.0, round(float(rx), 5)))
            cy = max(0.0, min(1.0, round(float(ry), 5)))
            setattr(self, f"{key}_pos", [cx, cy])
            if key == "riven":
                self.overlay_pos_x_ratio = cx
                self.overlay_pos_y_ratio = cy

    def reset_all_positions(self) -> None:
        self.overlay_pos_x_ratio = None
        self.overlay_pos_y_ratio = None
        self.riven_pos = None
        self.wm_pos = None
        self.synergy_pos = None
        self.relic_pos = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "UserSettings":
        from src.i18n import LANG_MODES

        lang = str(raw.get("language_mode", "auto"))
        if lang not in LANG_MODES:
            lang = "auto"
        rx = _parse_ratio(raw.get("overlay_pos_x_ratio"))
        ry = _parse_ratio(raw.get("overlay_pos_y_ratio"))
        if rx is None or ry is None:
            rx, ry = None, None
        riven_pos = _parse_pos_pair(raw.get("riven_pos"))
        if riven_pos is None and rx is not None and ry is not None:
            riven_pos = [rx, ry]
        elif riven_pos is not None:
            rx, ry = riven_pos[0], riven_pos[1]
        raw_preset = str(raw.get("theme_preset") or DEFAULT_THEME_PRESET).strip()
        if raw_preset not in THEME_PRESETS and raw_preset != "custom":
            raw_preset = DEFAULT_THEME_PRESET
        raw_colors = raw.get("theme_colors")
        resolved_colors = resolve_theme_colors(
            raw_preset,
            raw_colors if isinstance(raw_colors, dict) else None,
        )
        return cls(
            hotkey_toggle_monitor=str(raw.get("hotkey_toggle_monitor") or "F8").strip(),
            hotkey_fetch_wm=str(raw.get("hotkey_fetch_wm") or "F9").strip(),
            hotkey_hide_overlay=str(raw.get("hotkey_hide_overlay") or "ESC").strip(),
            hotkey_open_panel=str(raw.get("hotkey_open_panel") or "Ctrl+Alt+D").strip(),
            language_mode=lang,
            start_monitoring_on_launch=bool(raw.get("start_monitoring_on_launch", False)),
            overlay_pos_x_ratio=rx,
            overlay_pos_y_ratio=ry,
            riven_pos=riven_pos,
            wm_pos=_parse_pos_pair(raw.get("wm_pos")),
            synergy_pos=_parse_pos_pair(raw.get("synergy_pos")),
            relic_pos=_parse_pos_pair(raw.get("relic_pos")),
            overlay_font_size=clamp_overlay_font_size(
                raw.get("overlay_font_size", DEFAULT_OVERLAY_FONT_PX)
            ),
            auto_collapse_seconds=normalize_collapse_seconds(raw.get("auto_collapse_seconds", 0)),
            miss_collapse_count=clamp_miss_collapse_count(
                raw.get("miss_collapse_count", DEFAULT_MISS_COLLAPSE_COUNT)
            ),
            auto_hide_on_focus_loss=bool(raw.get("auto_hide_on_focus_loss", True)),
            audio_cue_enabled=bool(raw.get("audio_cue_enabled", True)),
            custom_rare_sound_path=str(raw.get("custom_rare_sound_path") or "").strip(),
            panel_geometry=_parse_panel_geometry(raw.get("panel_geometry")),
            theme_preset=raw_preset,
            theme_colors=resolved_colors,
            first_run_guide_shown=bool(raw.get("first_run_guide_shown", False)),
        )


def load_user_settings(path: Optional[str | Path] = None) -> UserSettings:
    target = Path(path) if path else get_settings_path()
    if not target.is_file():
        return UserSettings()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return UserSettings.from_dict(data)
    except Exception:
        pass
    return UserSettings()


def save_user_settings(settings: UserSettings, path: Optional[str | Path] = None) -> Path:
    target = Path(path) if path else get_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)
    return target
