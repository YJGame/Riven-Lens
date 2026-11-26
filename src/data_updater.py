"""Automatic updater for Warframe official Riven weapon dispositions & attribute data.

Fetches structured public JSON (no HTML scraping) from:
  1. WFCD/warframe-items (GitHub raw JSON): authoritative latest `omegaAttenuation`
     (official DE Riven Disposition) across all weapons and variants
     (Prime, Kuva/赤毒, Tenet/信条, Vandal/破坏者, Wraith/亡魂, Prisma/棱晶, Coda/终幕).
  2. warframe.market v2 public API (`Language: zh-hans`): official Simplified Chinese
     weapon & Riven attribute localizations, slugs, units, and weapon groups.

Caches results locally to:
  - `data/weapons_riven.json`
  - `data/riven_attributes.json`

Supports silent background refresh on startup when cache is missing or older than
7 days, with automatic fallback to existing local cache on network failures.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


def get_data_dir() -> Path:
    """Resolve writable `data/` directory cleanly on Windows, macOS, and Linux (source & frozen)."""
    if sys.platform != "win32":
        # On macOS (.app bundle) and Linux (AppImage / /opt), persist mutable caches & configs
        # outside read-only app bundles and seed bundled baseline JSONs if missing.
        if sys.platform == "darwin":
            cfg_data = Path.home() / "Library" / "Application Support" / "WarframeRivenOverlay" / "data"
        else:
            cfg_data = Path.home() / ".config" / "WarframeRivenOverlay" / "data"
        meipass = getattr(sys, "_MEIPASS", None)
        bundled_dir = (
            Path(meipass) / "data"
            if meipass
            else Path(__file__).resolve().parent.parent / "data"
        )
        # In source tree mode where repo `data/` is writable, keep using repo `data/` unless frozen
        if not getattr(sys, "frozen", False) and bundled_dir.is_dir():
            return bundled_dir
        cfg_data.mkdir(parents=True, exist_ok=True)
        if bundled_dir.is_dir():
            import shutil

            for src_file in bundled_dir.glob("*.json"):
                dst_file = cfg_data / src_file.name
                if not dst_file.exists():
                    try:
                        shutil.copy2(src_file, dst_file)
                    except Exception:
                        pass
        return cfg_data

    if getattr(sys, "frozen", False):
        exe_data = Path(sys.executable).resolve().parent / "data"
        if exe_data.is_dir():
            return exe_data
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "data"
            if bundled.is_dir():
                return bundled
        return exe_data
    return Path(__file__).resolve().parent.parent / "data"


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = get_data_dir()
WEAPONS_CACHE_PATH = DATA_DIR / "weapons_riven.json"
ATTRIBUTES_CACHE_PATH = DATA_DIR / "riven_attributes.json"

DEFAULT_MAX_AGE_DAYS = 7
HTTP_TIMEOUT_SEC = 18

WFM_V2_WEAPONS_URL = "https://api.warframe.market/v2/riven/weapons"
WFM_V2_ATTRIBUTES_URL = "https://api.warframe.market/v2/riven/attributes"
WFCD_RAW_BASE = "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json"
WFCD_WEAPON_FILES = ("Primary.json", "Secondary.json", "Melee.json", "Arch-Gun.json")

# Official Warframe Riven combination multipliers (Rank 8 = 9x Rank 0)
COMBO_MULTIPLIERS: Dict[str, Dict[str, float]] = {
    "2+0-": {"positive": 0.99, "negative": 0.0},
    "2+1-": {"positive": 1.2375, "negative": -0.495},
    "3+0-": {"positive": 0.75, "negative": 0.0},
    "3+1-": {"positive": 0.9375, "negative": -0.75},
}

# WeGame / Official Simplified Chinese client weapon name aliases
# (bridges differences between International zh-hans and CN-client localization + common OCR typos)
CN_CLIENT_WEAPON_ALIASES: Dict[str, str] = {
    "哈尔武": "Harpak",
    "冰凇": "Glaxion",
    "冰松": "Glaxion",
    "冰凇 Prime": "Glaxion",
    "信条·冰凇": "Tenet Glaxion",
    "赤毒布拉玛": "Kuva Bramma",
    "赤毒·布拉玛": "Kuva Bramma",
    "布拉玛": "Kuva Bramma",
    "赤毒沙皇": "Kuva Zarr",
    "赤毒努寇微波枪": "Kuva Nukor",
    "赤毒海克": "Kuva Hek",
    "信条漫射者": "Tenet Cycron",
    "信条弧电离子枪": "Tenet Arca Plasmor",
    "信条德特昂": "Tenet Detron",
    "瘟疫克里帕丝": "Plague Kripath",
    "瘟疫 克里帕丝": "Plague Kripath",
    "瘟疫奇沃": "Plague Keewar",
    "瘟疫 奇沃": "Plague Keewar",
    "托理德": "Torid",
    "伯斯顿": "Burston",
}

# English variant prefix/suffix -> Simplified Chinese localization patterns
VARIANT_PREFIX_ZH: Dict[str, str] = {
    "Kuva ": "赤毒·",
    "Tenet ": "信条·",
    "Prisma ": "棱晶 ",
    "Coda ": "终幕·",
    "Dex ": "Dex ",
    "MK1-": "MK1-",
    "Mara ": " mara ",
    "Telos ": "终极 ",
    "Synoid ": "枢议 ",
    "Secura ": "保障 ",
    "Sancti ": "圣洁 ",
    "Rakta ": "绯红 ",
    "Vaykor ": "勇气 ",
    "Carmine ": "绯红 ",
    "Mutalist ": "异融 ",
    "Proboscis ": "异化 ",
}

VARIANT_SUFFIX_ZH: Dict[str, str] = {
    " Prime": " Prime",
    " Vandal": " 破坏者",
    " Wraith": " 亡魂",
    " Umbra": " Umbra",
}

# Official max-rank (Rank 8 = 9x Rank 0) Riven attribute base values at Disposition = 1.0
# Exact 0.1%-verified values matching Warframe official tables (`3+1`, `3+0`, `2+1`, `2+0` at Dispo 1.20).
OFFICIAL_BASE_VALUES_BY_SLUG: Dict[str, Dict[str, float]] = {
    "base_damage_/_melee_damage": {"rifle": 165.0, "shotgun": 164.7, "pistol": 219.6, "melee": 164.7, "archgun": 99.9},
    "critical_chance": {"rifle": 150.0, "shotgun": 90.0, "pistol": 150.0, "melee": 180.0, "archgun": 99.9},
    "critical_damage": {"rifle": 120.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 80.1},
    "multishot": {"rifle": 90.0, "shotgun": 119.7, "pistol": 119.7, "archgun": 60.3},
    "fire_rate_/_attack_speed": {"rifle": 60.03, "shotgun": 90.0, "pistol": 74.7, "melee": 54.9, "archgun": 60.03},
    "heat_damage": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 119.7},
    "cold_damage": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 119.7},
    "electric_damage": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 119.7},
    "toxin_damage": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 119.7},
    "viral": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "corrosive": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "radiation": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "magnetic": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "gas": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "blast": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "impact_damage": {"rifle": 120.0, "shotgun": 120.0, "pistol": 120.0, "melee": 119.7, "archgun": 90.0},
    "puncture_damage": {"rifle": 120.0, "shotgun": 120.0, "pistol": 120.0, "melee": 119.7, "archgun": 90.0},
    "slash_damage": {"rifle": 120.0, "shotgun": 120.0, "pistol": 120.0, "melee": 119.7, "archgun": 90.0},
    "status_chance": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 60.3},
    "status_duration": {"rifle": 99.99, "shotgun": 99.99, "pistol": 99.99, "melee": 99.99, "archgun": 99.99},
    "status_damage": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "melee": 90.0, "archgun": 90.0},
    "reload_speed": {"rifle": 50.0, "shotgun": 50.0, "pistol": 50.0, "archgun": 99.9},
    "magazine_capacity": {"rifle": 50.0, "shotgun": 50.0, "pistol": 50.0, "archgun": 60.3},
    "ammo_maximum": {"rifle": 49.95, "shotgun": 90.0, "pistol": 90.0, "archgun": 99.9},
    "ammo_efficiency": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "archgun": 45.0},
    "magazine_reloaded_s_when_holstered": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0},
    "projectile_speed": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0},
    "punch_through": {"rifle": 2.7, "shotgun": 2.7, "pistol": 2.7, "archgun": 2.7},
    "zoom": {"rifle": 60.0, "shotgun": 42.0, "pistol": 80.1, "archgun": 60.0},
    "recoil": {"rifle": 90.0, "shotgun": 90.0, "pistol": 90.0, "archgun": 90.0},
    "damage_vs_grineer": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "damage_vs_corpus": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "damage_vs_infested": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "damage_to_orokin": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "damage_to_scaldra": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "damage_to_techrot": {"rifle": 45.0, "shotgun": 45.0, "pistol": 45.0, "melee": 45.0, "archgun": 45.0},
    "range": {"melee": 1.94},
    "combo_duration": {"melee": 8.1},
    "channeling_damage": {"melee": 24.48},        # Initial Combo
    "channeling_efficiency": {"melee": 73.44},    # Heavy Attack Efficiency
    "finisher_damage": {"melee": 119.7},
    "critical_chance_on_slide_attack": {"melee": 120.0},
    "chance_to_gain_extra_combo_count": {"melee": 58.77},
    "chance_to_gain_combo_count": {"melee": 58.77},
    "heavy_attack_wind_up_speed": {"melee": 119.7},
    "melee_damage_on_heavy_attack": {"melee": 119.7},
    "slam_attack_damage": {"melee": 119.7},
    "parry_angle": {"melee": 81.0},
    "weak_point_damage": {"rifle": 225.0, "shotgun": 225.0, "pistol": 225.0, "archgun": 225.0},
    "weak_point_critical_chance": {"rifle": 247.5, "shotgun": 247.5, "pistol": 247.5, "archgun": 247.5},
}

# Map WFM attribute slugs to our parser's canonical Simplified Chinese stat names
SLUG_TO_CANONICAL_ZH: Dict[str, str] = {
    "base_damage_/_melee_damage": "伤害",
    "critical_chance": "暴击几率",
    "critical_damage": "暴击伤害",
    "multishot": "多重射击",
    "fire_rate_/_attack_speed": "射速/攻击速度",
    "heat_damage": "火焰伤害",
    "cold_damage": "冰冻伤害",
    "electric_damage": "电击伤害",
    "toxin_damage": "毒素伤害",
    "blast": "爆炸伤害",
    "corrosive": "腐蚀伤害",
    "gas": "毒气伤害",
    "magnetic": "磁力伤害",
    "radiation": "辐射伤害",
    "viral": "病毒伤害",
    "magazine_reloaded_s_when_holstered": "收起武器时弹匣每秒自动装填",
    "impact_damage": "冲击伤害",
    "puncture_damage": "穿刺伤害",
    "slash_damage": "切割伤害",
    "status_chance": "触发几率",
    "status_duration": "触发时间",
    "status_damage": "异常状态伤害",
    "reload_speed": "装填速度",
    "magazine_capacity": "弹匣容量",
    "ammo_maximum": "弹药最大值",
    "ammo_efficiency": "弹药效率",
    "projectile_speed": "投射物速度",
    "punch_through": "穿透",
    "zoom": "变焦",
    "recoil": "武器后坐力",
    "damage_vs_grineer": "对 Grineer 伤害",
    "damage_vs_corpus": "对 Corpus 伤害",
    "damage_vs_infested": "对 Infested 伤害",
    "damage_to_orokin": "对奥罗金伤害",
    "damage_to_scaldra": "对炽蛇军伤害",
    "damage_to_techrot": "对科腐者伤害",
    "range": "攻击范围",
    "combo_duration": "连击持续时间",
    "channeling_damage": "初始连击",
    "channeling_efficiency": "重击效率",
    "finisher_damage": "处决伤害",
    "critical_chance_on_slide_attack": "滑行攻击暴击几率",
    "chance_to_gain_extra_combo_count": "额外连击几率",
    "chance_to_gain_combo_count": "额外连击几率",
    "heavy_attack_wind_up_speed": "重击准备速度",
    "melee_damage_on_heavy_attack": "在重击时的近战伤害",
    "slam_attack_damage": "震地攻击伤害",
    "parry_angle": "招架角度",
    "weak_point_damage": "弱点伤害",
    "weak_point_critical_chance": "弱点暴击几率",
}


def _fetch_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = HTTP_TIMEOUT_SEC) -> Any:
    hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarframeRivenOverlay/2.0", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _wfcd_riven_class(category: str, item_type: str) -> str:
    cat = (category or "").lower()
    typ = (item_type or "").lower()
    if cat == "arch-gun" or "arch-gun" in typ:
        return "archgun"
    if cat == "melee" or "melee" in typ or "zaw" in typ:
        return "melee"
    if cat == "secondary" or "pistol" in typ or "dual" in typ or "thrown" in typ:
        return "pistol"
    if "shotgun" in typ:
        return "shotgun"
    return "rifle"


def _normalize_riven_class(raw_class: Optional[str]) -> str:
    rc = (raw_class or "").lower()
    if rc in ("zaw", "melee"):
        return "melee"
    if rc in ("kitgun", "pistol", "secondary"):
        return "pistol"
    if rc == "shotgun":
        return "shotgun"
    if rc == "archgun":
        return "archgun"
    return "rifle"


def _derive_variant_zh_name(en_name: str, en_to_zh: Dict[str, str]) -> Optional[str]:
    """Synthesize Simplified Chinese name for variant weapons (Prime/Kuva/Tenet/Wraith/Vandal/etc.)."""
    if en_name in en_to_zh:
        return en_to_zh[en_name]

    # Try suffix variants (e.g. "Glaive Prime" -> "战刃 Prime")
    for en_suf, zh_suf in VARIANT_SUFFIX_ZH.items():
        if en_name.endswith(en_suf):
            base_en = en_name[: -len(en_suf)].strip()
            if base_en in en_to_zh:
                return f"{en_to_zh[base_en]}{zh_suf}"

    # Try prefix variants (e.g. "Kuva Bramma" -> "赤毒·布拉玛", "Tenet Glaxion" -> "信条·冷冻光束步枪")
    for en_pre, zh_pre in VARIANT_PREFIX_ZH.items():
        if en_name.startswith(en_pre):
            base_en = en_name[len(en_pre) :].strip()
            if base_en in en_to_zh:
                return f"{zh_pre.strip()}{en_to_zh[base_en]}" if zh_pre.endswith("·") or zh_pre.endswith("-") else f"{zh_pre}{en_to_zh[base_en]}"

    return None


def _load_legacy_weapons_file() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    legacy_path = DATA_DIR / "weapons.json"
    if not legacy_path.is_file():
        return {}, {}
    try:
        data = json.loads(legacy_path.read_text(encoding="utf-8"))
        return data.get("weapons", {}), data.get("ocr_aliases", {})
    except Exception:
        return {}, {}


def is_cache_stale(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> bool:
    """Return True if either cache file is missing or older than `max_age_days`."""
    now = time.time()
    max_age_sec = max_age_days * 86400
    for path in (WEAPONS_CACHE_PATH, ATTRIBUTES_CACHE_PATH):
        if not path.is_file():
            return True
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated_at = float(payload.get("updated_at", 0))
            if updated_at <= 0 or (now - updated_at) > max_age_sec:
                return True
        except Exception:
            return True
    return False


def sync_all_data(force: bool = False, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Dict[str, Any]:
    """Fetch latest WFCD & warframe.market v2 JSON data and update local cache files.

    If `force=False` and caches are fresh (< `max_age_days`), returns immediately.
    Raises on network/parse error only if caller invokes directly; use `safe_sync()`
    for automatic local-cache fallback.
    """
    if not force and not is_cache_stale(max_age_days=max_age_days):
        return {"status": "cached", "weapons_path": str(WEAPONS_CACHE_PATH), "attributes_path": str(ATTRIBUTES_CACHE_PATH)}

    now_ts = int(time.time())
    now_iso = datetime.fromtimestamp(now_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Fetch warframe.market v2 weapons & attributes (zh-hans + en in single response!)
    wfm_weapons_raw = _fetch_json(WFM_V2_WEAPONS_URL, headers={"Language": "zh-hans"})
    wfm_weapons: List[Dict[str, Any]] = wfm_weapons_raw.get("data", [])

    wfm_attrs_raw = _fetch_json(WFM_V2_ATTRIBUTES_URL, headers={"Language": "zh-hans"})
    wfm_attrs: List[Dict[str, Any]] = wfm_attrs_raw.get("data", [])

    # 2. Fetch WFCD/warframe-items JSON for authoritative live `omegaAttenuation` & all variants
    wfcd_items: List[Dict[str, Any]] = []
    for fname in WFCD_WEAPON_FILES:
        arr = _fetch_json(f"{WFCD_RAW_BASE}/{fname}")
        if isinstance(arr, list):
            wfcd_items.extend(arr)

    # Index WFM weapons by gameRef (uniqueName) and English name
    wfm_by_ref: Dict[str, Dict[str, Any]] = {}
    wfm_by_en: Dict[str, Dict[str, Any]] = {}
    en_to_zh: Dict[str, str] = {}

    for item in wfm_weapons:
        en_name = item.get("i18n", {}).get("en", {}).get("name", "").strip()
        zh_name = item.get("i18n", {}).get("zh-hans", {}).get("name", "").strip()
        game_ref = (item.get("gameRef") or "").strip()
        if game_ref:
            wfm_by_ref[game_ref] = item
        if en_name:
            wfm_by_en[en_name.lower()] = item
            if zh_name:
                en_to_zh[en_name] = zh_name

    # Build unified weapons dictionary keyed by Simplified Chinese name
    weapons_by_zh: Dict[str, Dict[str, Any]] = {}
    en_to_record: Dict[str, Dict[str, Any]] = {}

    # Pass A: Populate from WFCD (covers every base weapon AND variant with live omegaAttenuation)
    for item in wfcd_items:
        omega = item.get("omegaAttenuation")
        if omega is None:
            continue
        en_name = (item.get("name") or "").strip()
        if not en_name:
            continue
        unique_name = (item.get("uniqueName") or "").strip()
        wfcd_class = _wfcd_riven_class(item.get("category", ""), item.get("type", ""))

        wfm_match = wfm_by_ref.get(unique_name) or wfm_by_en.get(en_name.lower())
        slug = wfm_match.get("slug", "") if wfm_match else re.sub(r"[^a-z0-9]+", "_", en_name.lower()).strip("_")
        riven_class = _normalize_riven_class(wfm_match.get("rivenType")) if wfm_match else wfcd_class
        if wfcd_class == "archgun":
            riven_class = "archgun"

        zh_name = (
            wfm_match.get("i18n", {}).get("zh-hans", {}).get("name", "").strip()
            if wfm_match
            else _derive_variant_zh_name(en_name, en_to_zh)
        )

        rec = {
            "en_name": en_name,
            "slug": slug,
            "unique_name": unique_name,
            "riven_class": riven_class,
            "disposition": round(float(omega), 2),
            "omega_attenuation": round(float(omega), 2),
            "wfm_disposition": round(float(wfm_match["disposition"]), 2) if (wfm_match and wfm_match.get("disposition") is not None) else None,
            "crit_chance": round(float(item["criticalChance"]), 4) if item.get("criticalChance") is not None else None,
            "crit_mult": round(float(item["criticalMultiplier"]), 2) if item.get("criticalMultiplier") is not None else None,
            "status_chance": round(float(item["procChance"]), 4) if item.get("procChance") is not None else None,
        }
        en_to_record[en_name.lower()] = rec
        if zh_name:
            weapons_by_zh[zh_name] = rec

    # Pass B: Ensure any WFM weapon absent from WFCD is also included
    for item in wfm_weapons:
        en_name = item.get("i18n", {}).get("en", {}).get("name", "").strip()
        zh_name = item.get("i18n", {}).get("zh-hans", {}).get("name", "").strip()
        if not zh_name:
            continue
        if zh_name in weapons_by_zh:
            continue
        # Prefer WFCD omega if matched by English name, otherwise WFM disposition
        wfcd_rec = en_to_record.get(en_name.lower())
        if wfcd_rec:
            weapons_by_zh[zh_name] = dict(wfcd_rec)
        else:
            disp = round(float(item.get("disposition") or 1.0), 2)
            weapons_by_zh[zh_name] = {
                "en_name": en_name,
                "slug": item.get("slug", ""),
                "unique_name": item.get("gameRef", ""),
                "riven_class": _normalize_riven_class(item.get("rivenType") or item.get("group")),
                "disposition": disp,
                "omega_attenuation": None,
                "wfm_disposition": disp,
            }

    # Pass C: Register WeGame / CN-client weapon names directly + alias map
    aliases: Dict[str, str] = {}
    legacy_weapons, legacy_aliases = _load_legacy_weapons_file()
    aliases.update(legacy_aliases)

    for cn_name, target_en in CN_CLIENT_WEAPON_ALIASES.items():
        rec = en_to_record.get(target_en.lower())
        if rec:
            # Store directly under the CN-client weapon name so display name stays familiar (e.g. 哈尔武, 冰凇)
            weapons_by_zh[cn_name] = dict(rec)
            # Keep OCR typo alias pointing to clean CN name
            if cn_name == "冰松":
                aliases["冰松"] = "冰凇"
            elif cn_name == "托理德":
                aliases["托理德"] = "托里德"

    # Preserve any extra manually-entered weapons from legacy `data/weapons.json`
    for legacy_name, legacy_entry in legacy_weapons.items():
        if legacy_name not in weapons_by_zh:
            weapons_by_zh[legacy_name] = {
                "en_name": "",
                "slug": "",
                "unique_name": "",
                "riven_class": _normalize_riven_class(legacy_entry.get("class")),
                "disposition": round(float(legacy_entry.get("disposition", 1.2)), 2),
                "omega_attenuation": None,
                "wfm_disposition": None,
            }

    weapons_payload = {
        "schema_version": 2,
        "updated_at": now_ts,
        "updated_at_iso": now_iso,
        "sources": [
            f"{WFCD_RAW_BASE}/({','.join(WFCD_WEAPON_FILES)})",
            WFM_V2_WEAPONS_URL,
        ],
        "weapon_count": len(weapons_by_zh),
        "aliases": aliases,
        "weapons": dict(sorted(weapons_by_zh.items())),
    }

    # 3. Build `data/riven_attributes.json` merging WFM v2 metadata + official base stat values
    attributes_map: Dict[str, Dict[str, Any]] = {}
    for attr in wfm_attrs:
        slug = attr.get("slug", "").strip()
        if not slug:
            continue
        wfm_zh = attr.get("i18n", {}).get("zh-hans", {}).get("name", "").strip()
        wfm_en = attr.get("i18n", {}).get("en", {}).get("name", "").strip()
        canon_zh = SLUG_TO_CANONICAL_ZH.get(slug, wfm_zh or slug)
        attributes_map[slug] = {
            "slug": slug,
            "zh_name": canon_zh,
            "wfm_zh_name": wfm_zh,
            "en_name": wfm_en,
            "unit": attr.get("unit"),
            "exclusive_to": attr.get("exclusiveTo"),
            "positive_only": bool(attr.get("positiveOnly", False)),
            "negative_only": bool(attr.get("negativeOnly", False)),
            "base_values": OFFICIAL_BASE_VALUES_BY_SLUG.get(slug, {}),
        }

    attributes_payload = {
        "schema_version": 2,
        "updated_at": now_ts,
        "updated_at_iso": now_iso,
        "sources": [
            WFM_V2_ATTRIBUTES_URL,
            "Warframe Official Riven Math (Rank 8 = 9x base)",
        ],
        "attribute_count": len(attributes_map),
        "combo_multipliers": COMBO_MULTIPLIERS,
        "attributes": attributes_map,
    }

    _write_json_atomic(WEAPONS_CACHE_PATH, weapons_payload)
    _write_json_atomic(ATTRIBUTES_CACHE_PATH, attributes_payload)

    return {
        "status": "updated",
        "updated_at_iso": now_iso,
        "weapon_count": len(weapons_by_zh),
        "attribute_count": len(attributes_map),
        "weapons_path": str(WEAPONS_CACHE_PATH),
        "attributes_path": str(ATTRIBUTES_CACHE_PATH),
    }


def safe_sync(force: bool = False, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> Dict[str, Any]:
    """Run sync with automatic fallback to local cache if network fails."""
    try:
        return sync_all_data(force=force, max_age_days=max_age_days)
    except Exception as exc:
        has_cache = WEAPONS_CACHE_PATH.is_file() and ATTRIBUTES_CACHE_PATH.is_file()
        return {
            "status": "fallback_cache" if has_cache else "failed",
            "error": str(exc),
        }


def start_background_sync_if_needed(
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    on_done: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Optional[threading.Thread]:
    """Silently sync official data in a background daemon thread if cache is missing or >7 days old."""
    if not is_cache_stale(max_age_days=max_age_days):
        return None

    def _worker() -> None:
        res = safe_sync(force=True, max_age_days=max_age_days)
        if on_done is not None:
            try:
                on_done(res)
            except Exception:
                pass

    t = threading.Thread(target=_worker, name="RivenDataUpdater", daemon=True)
    t.start()
    return t


def main() -> None:
    parser = argparse.ArgumentParser(description="同步最新 Warframe 武器裂罅倾向性与词条官方 JSON 数据")
    parser.add_argument("--force", action="store_true", help="忽略 7 天缓存有效期，强制立即更新")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS, help="缓存过期天数（默认 7 天）")
    args = parser.parse_args()

    result = safe_sync(force=args.force, max_age_days=args.max_age_days)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
