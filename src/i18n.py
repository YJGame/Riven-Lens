"""15-language localization manager backed by `locales/*.json` and `locales/multilang.json`.

Supports all 15 official Warframe client languages:
  zh_CN (简体中文), zh_TW (繁體中文), en_US (English), fr_FR (Français),
  it_IT (Italiano), de_DE (Deutsch), es_ES (Español), pt_BR (Português-Br),
  ru_RU (Русский), pl_PL (Polski), uk_UA (Українська), tr_TR (Türkçe),
  ja_JP (日本語), ko_KR (한국어), th_TH (ไทย).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SUPPORTED_LANGS: Tuple[str, ...] = (
    "zh_CN",
    "zh_TW",
    "en_US",
    "fr_FR",
    "it_IT",
    "de_DE",
    "es_ES",
    "pt_BR",
    "ru_RU",
    "pl_PL",
    "uk_UA",
    "tr_TR",
    "ja_JP",
    "ko_KR",
    "th_TH",
)

LANG_MODES: Tuple[str, ...] = ("auto",) + SUPPORTED_LANGS

LANGUAGE_DISPLAY_NAMES: Dict[str, str] = {
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
    "en_US": "English",
    "fr_FR": "Français",
    "it_IT": "Italiano",
    "de_DE": "Deutsch",
    "es_ES": "Español",
    "pt_BR": "Português-Br",
    "ru_RU": "Русский",
    "pl_PL": "Polski",
    "uk_UA": "Українська",
    "tr_TR": "Türkçe",
    "ja_JP": "日本語",
    "ko_KR": "한국어",
    "th_TH": "ไทย",
}

# Traditional-only CJK sample characters to distinguish zh_TW from zh_CN
_TRAD_CHARS = frozenset("擊傷觸發機彈裝擴範連持續時間對體藍圖系統頭機神經光元槍管握柄")


def get_locales_dir() -> Path:
    """Resolve `locales/` directory cleanly in both source and PyInstaller frozen mode."""
    if getattr(sys, "frozen", False):
        exe_loc = Path(sys.executable).resolve().parent / "locales"
        if exe_loc.is_dir():
            return exe_loc
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "locales"
            if bundled.is_dir():
                return bundled
    return Path(__file__).resolve().parent.parent / "locales"


def detect_text_language(lines: Sequence[str]) -> str:
    """Detect the dominant Warframe client language from OCR text lines."""
    cjk = 0
    trad = 0
    kana = 0
    hangul = 0
    cyrillic = 0
    ukrainian = 0
    thai = 0
    latin = 0

    for ln in lines:
        for ch in ln:
            code = ord(ch)
            if 0x3040 <= code <= 0x30FF:
                kana += 1
            elif 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:
                hangul += 1
            elif 0x0E00 <= code <= 0x0E7F:
                thai += 1
            elif 0x0400 <= code <= 0x04FF:
                cyrillic += 1
                if ch in "іїєґІЇЄҐ":
                    ukrainian += 1
            elif 0x4E00 <= code <= 0x9FFF:
                cjk += 1
                if ch in _TRAD_CHARS:
                    trad += 1
            elif ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
                latin += 1

    if kana >= 2:
        return "ja_JP"
    if hangul >= 2:
        return "ko_KR"
    if thai >= 3:
        return "th_TH"
    if cyrillic >= 3:
        return "uk_UA" if ukrainian >= 1 else "ru_RU"
    if cjk >= 3:
        return "zh_TW" if trad >= 2 else "zh_CN"
    if latin >= 6 and cjk == 0:
        return "en_US"
    return "zh_CN" if cjk >= latin else "en_US"


def _deep_merge_bundle(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for sec in set(base.keys()) | set(override.keys()):
        bv = base.get(sec)
        ov = override.get(sec)
        if isinstance(bv, dict) and isinstance(ov, dict):
            d = dict(bv)
            d.update(ov)
            merged[sec] = d
        elif ov is not None:
            merged[sec] = ov
        else:
            merged[sec] = bv
    return merged


class LocaleManager:
    """Loads all 15 official Warframe locales and provides zero-Chinese-leak lookups."""

    def __init__(self, locales_dir: Optional[str | Path] = None) -> None:
        self.locales_dir = Path(locales_dir) if locales_dir else get_locales_dir()
        self._bundles: Dict[str, Dict[str, Any]] = {}
        self._current_lang: str = "zh_CN"
        self._listeners: List[Callable[[str], None]] = []
        self._load_all()

    @property
    def current_lang(self) -> str:
        return self._current_lang

    def set_current_language(self, lang: str) -> None:
        """Update active language globally and notify all registered listeners."""
        target = lang if lang in SUPPORTED_LANGS else "en_US"
        self._current_lang = target
        for cb in list(self._listeners):
            try:
                cb(target)
            except Exception:
                pass

    def add_language_listener(self, cb: Callable[[str], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def _load_all(self) -> None:
        # 1. Load primary standalone locale JSON files (zh_CN, en_US, and any individual <lang>.json)
        for code in SUPPORTED_LANGS:
            path = self.locales_dir / f"{code}.json"
            if path.is_file():
                try:
                    self._bundles[code] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    self._bundles[code] = {}

        # 2. Load consolidated multi-language pack (`locales/multilang.json`) for the remaining 13 languages
        multi_path = self.locales_dir / "multilang.json"
        if multi_path.is_file():
            try:
                raw_multi = json.loads(multi_path.read_text(encoding="utf-8"))
                if isinstance(raw_multi, dict):
                    for code, payload in raw_multi.items():
                        if code in SUPPORTED_LANGS and isinstance(payload, dict):
                            existing = self._bundles.get(code) or {}
                            self._bundles[code] = _deep_merge_bundle(payload, existing)
            except Exception:
                pass

        for code in SUPPORTED_LANGS:
            self._bundles.setdefault(code, {})

    def _fallback_lang(self, lang: str) -> str:
        """Non-Chinese languages strictly fall back to English (NEVER Chinese!) to eliminate Chinese leaks."""
        if lang in ("zh_CN", "zh_TW"):
            return "zh_CN"
        return "en_US"

    def _bundle(self, lang: str) -> Dict[str, Any]:
        return self._bundles.get(lang) or self._bundles.get(self._fallback_lang(lang)) or {}

    def t(self, key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
        """Translate a `ui.<key>` or dotted `<section>.<key>` string cleanly for any of the 15 languages."""
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        section, subkey = key.split(".", 1) if "." in key else ("ui", key)
        val = self._bundle(eff_lang).get(section, {}).get(subkey)
        if val is None:
            fb = self._fallback_lang(eff_lang)
            val = self._bundles.get(fb, {}).get(section, {}).get(subkey)
        if val is None:
            val = self._bundles.get("en_US", {}).get(section, {}).get(subkey)
        if val is None:
            val = self._bundles.get("zh_CN", {}).get(section, {}).get(subkey, subkey)
        text = str(val)
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

    def stat_name(self, canonical_zh_stat: str, lang: Optional[str] = None) -> str:
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        val = self._bundle(eff_lang).get("stats", {}).get(canonical_zh_stat)
        if val is not None:
            return str(val)
        fb = self._fallback_lang(eff_lang)
        val = self._bundles.get(fb, {}).get("stats", {}).get(canonical_zh_stat)
        return str(val) if val is not None else canonical_zh_stat

    def tag_name(self, canonical_zh_tag: str, lang: Optional[str] = None) -> str:
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        val = self._bundle(eff_lang).get("tags", {}).get(canonical_zh_tag)
        if val is not None:
            return str(val)
        fb = self._fallback_lang(eff_lang)
        val = self._bundles.get(fb, {}).get("tags", {}).get(canonical_zh_tag)
        return str(val) if val is not None else canonical_zh_tag

    def weapon_class(self, class_key: str, lang: Optional[str] = None) -> str:
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        val = self._bundle(eff_lang).get("weapon_classes", {}).get(class_key)
        if val is not None:
            return str(val)
        fb = self._fallback_lang(eff_lang)
        val = self._bundles.get(fb, {}).get("weapon_classes", {}).get(class_key)
        return str(val) if val is not None else class_key

    def rec_source(self, source_key: str, lang: Optional[str] = None) -> str:
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        val = self._bundle(eff_lang).get("rec_sources", {}).get(source_key)
        if val is not None:
            return str(val)
        fb = self._fallback_lang(eff_lang)
        val = self._bundles.get(fb, {}).get("rec_sources", {}).get(source_key)
        return str(val) if val is not None else source_key

    def summary(self, summary_key: str, lang: Optional[str] = None) -> str:
        eff_lang = lang if lang in SUPPORTED_LANGS else self._current_lang
        val = self._bundle(eff_lang).get("summaries", {}).get(summary_key)
        if val is not None:
            return str(val)
        fb = self._fallback_lang(eff_lang)
        val = self._bundles.get(fb, {}).get("summaries", {}).get(summary_key)
        return str(val) if val is not None else summary_key

    def language_display_name(self, lang_code: str) -> str:
        return LANGUAGE_DISPLAY_NAMES.get(lang_code, lang_code)


# Shared singleton instance & unified `tr()` shorthand
I18N = LocaleManager()


def tr(key: str, lang: Optional[str] = None, **kwargs: Any) -> str:
    """Unified translation helper delegating to `I18N.t`."""
    return I18N.t(key, lang=lang, **kwargs)
