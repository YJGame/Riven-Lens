"""Warframe.market (WM) exact-stat Riven matching client & auction analyzer.

Capabilities:
  - Maps canonical Simplified Chinese Riven stat names to official WM v1
    `positive_stats` / `negative_stats` attribute slugs.
  - Queries `https://api.warframe.market/v1/auctions/search` with smart
    cascading degradation:
      1) 3 positive stats + high-value negative (optional) -> `exact_3_neg`
      2) 3 positive stats                                  -> `exact_3`
      3) Top 2 core positive stats                         -> `approx_2`
      4) Full weapon listings fallback                     -> `weapon_all`
  - Extracts the 5 lowest-priced active listings with seller online status,
    compact stat summary, and reroll count.
  - Builds direct `https://warframe.market/auctions/search?...` URLs for
    one-click browser opening (`webbrowser.open`).
  - Strictly on-demand (invoked only via F9 or Control Panel button; never
    called during routine F8 auto-monitoring).
"""

from __future__ import annotations

import json
import socket
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.data_updater import SLUG_TO_CANONICAL_ZH, get_data_dir

WM_AUCTIONS_API_URL = "https://api.warframe.market/v1/auctions/search"
WM_AUCTIONS_WEB_URL = "https://warframe.market/auctions/search"
DEFAULT_TIERS_PATH = get_data_dir() / "recommended_tiers.json"
DEFAULT_ATTRIBUTES_PATH = get_data_dir() / "riven_attributes.json"
WM_HTTP_TIMEOUT_SEC = 14

_VALID_WM_SLUGS_CACHE: Tuple[float, Set[str]] = (0.0, set())

# Canonical Simplified Chinese stat name -> WM v1 `positive_stats` / `negative_stats` slug
CANONICAL_ZH_TO_WM_SLUG: Dict[str, str] = {
    "伤害": "base_damage_/_melee_damage",
    "暴击几率": "critical_chance",
    "暴击伤害": "critical_damage",
    "多重射击": "multishot",
    "射速": "fire_rate_/_attack_speed",
    "攻击速度": "fire_rate_/_attack_speed",
    "火焰伤害": "heat_damage",
    "冰冻伤害": "cold_damage",
    "电击伤害": "electric_damage",
    "毒素伤害": "toxin_damage",
    "爆炸伤害": "blast",
    "腐蚀伤害": "corrosive",
    "毒气伤害": "gas",
    "磁力伤害": "magnetic",
    "辐射伤害": "radiation",
    "病毒伤害": "viral",
    "冲击伤害": "impact_damage",
    "穿刺伤害": "puncture_damage",
    "切割伤害": "slash_damage",
    "触发几率": "status_chance",
    "触发时间": "status_duration",
    "异常状态伤害": "status_damage",
    "装填速度": "reload_speed",
    "弹匣容量": "magazine_capacity",
    "弹药最大值": "ammo_maximum",
    "弹药效率": "ammo_efficiency",
    "投射物速度": "projectile_speed",
    "穿透": "punch_through",
    "变焦": "zoom",
    "武器后坐力": "recoil",
    "弱点伤害": "weak_point_damage",
    "弱点暴击几率": "weak_point_critical_chance",
    "攻击范围": "range",
    "连击持续时间": "combo_duration",
    "初始连击": "channeling_damage",
    "重击效率": "channeling_efficiency",
    "重击准备速度": "heavy_attack_wind_up_speed",
    "在重击时的近战伤害": "melee_damage_on_heavy_attack",
    "震地攻击伤害": "slam_attack_damage",
    "招架角度": "parry_angle",
    "处决伤害": "finisher_damage",
    "滑行攻击暴击几率": "critical_chance_on_slide_attack",
    "额外连击几率": "chance_to_gain_extra_combo_count",
    "对 Grineer 伤害": "damage_vs_grineer",
    "对 Corpus 伤害": "damage_vs_corpus",
    "对 Infested 伤害": "damage_vs_infested",
    "对奥罗金伤害": "damage_to_orokin",
    "对炽蛇军伤害": "damage_to_scaldra",
    "对科腐者伤害": "damage_to_techrot",
    "收起武器时弹匣每秒自动装填": "magazine_reloaded_s_when_holstered",
    "收起自动装填": "magazine_reloaded_s_when_holstered",
}

# Compact abbreviations for top-5 listing display lines
SHORT_STAT_ZH: Dict[str, str] = {
    "伤害": "基伤",
    "暴击几率": "暴击",
    "暴击伤害": "暴伤",
    "多重射击": "多重",
    "射速": "射速",
    "攻击速度": "攻速",
    "火焰伤害": "火",
    "冰冻伤害": "冰",
    "电击伤害": "电",
    "毒素伤害": "毒",
    "冲击伤害": "冲击",
    "穿刺伤害": "穿刺",
    "切割伤害": "切割",
    "触发几率": "触发",
    "触发时间": "触时",
    "异常状态伤害": "异伤",
    "装填速度": "装填",
    "弹匣容量": "弹匣",
    "弹药最大值": "备弹",
    "弹药效率": "弹效",
    "投射物速度": "弹速",
    "穿透": "穿透",
    "变焦": "变焦",
    "武器后坐力": "后坐",
    "攻击范围": "范围",
    "连击持续时间": "连持",
    "初始连击": "初连",
    "重击效率": "重效",
    "重击准备速度": "重准",
    "在重击时的近战伤害": "重伤",
    "震地攻击伤害": "震地",
    "招架角度": "招架",
    "处决伤害": "处决",
    "滑行攻击暴击几率": "滑暴",
    "额外连击几率": "额连",
    "对 Grineer 伤害": "G伤",
    "对 Corpus 伤害": "C伤",
    "对 Infested 伤害": "I伤",
    "对奥罗金伤害": "O伤",
}

SHORT_STAT_EN: Dict[str, str] = {
    "伤害": "DMG",
    "暴击几率": "CC",
    "暴击伤害": "CD",
    "多重射击": "MS",
    "射速": "FR",
    "攻击速度": "AS",
    "火焰伤害": "Heat",
    "冰冻伤害": "Cold",
    "电击伤害": "Elec",
    "毒素伤害": "Tox",
    "冲击伤害": "Imp",
    "穿刺伤害": "Punc",
    "切割伤害": "Slash",
    "触发几率": "SC",
    "触发时间": "SD",
    "异常状态伤害": "StatDMG",
    "装填速度": "Rld",
    "弹匣容量": "Mag",
    "弹药最大值": "Ammo",
    "弹药效率": "AmmoEff",
    "投射物速度": "PFS",
    "穿透": "PT",
    "变焦": "Zoom",
    "武器后坐力": "Rec",
    "攻击范围": "Rng",
    "连击持续时间": "ComboD",
    "初始连击": "IC",
    "重击效率": "HAE",
    "重击准备速度": "WindUp",
    "在重击时的近战伤害": "HvyDMG",
    "震地攻击伤害": "Slam",
    "招架角度": "Parry",
    "处决伤害": "Fin",
    "滑行攻击暴击几率": "SlideCC",
    "额外连击几率": "ComboCh",
    "对 Grineer 伤害": "DtG",
    "对 Corpus 伤害": "DtC",
    "对 Infested 伤害": "DtI",
    "对奥罗金伤害": "DtO",
}

# Default high-value positive stat ordering used when prioritizing 3 -> 2 stat degradation
CORE_POSITIVE_PRIORITY: Dict[str, int] = {
    "暴击伤害": 100,
    "多重射击": 96,
    "暴击几率": 94,
    "初始连击": 90,
    "伤害": 86,
    "射速": 82,
    "攻击速度": 80,
    "攻击范围": 78,
    "毒素伤害": 74,
    "火焰伤害": 72,
    "电击伤害": 70,
    "冰冻伤害": 68,
    "异常状态伤害": 64,
    "触发几率": 60,
}

HIGH_VALUE_NEGATIVES_DEFAULT = {
    "变焦",
    "冲击伤害",
    "穿刺伤害",
    "武器后坐力",
    "处决伤害",
    "滑行攻击暴击几率",
    "对 Infested 伤害",
    "招架角度",
}

MATCH_LEVEL_LABELS: Dict[str, Tuple[str, str]] = {
    "exact_3_neg": ("精准匹配 3正+1负", "Exact 3+1- Match"),
    "exact_3": ("精准匹配 3 词条", "Exact 3-Stat Match"),
    "exact_2_neg": ("精准匹配 2正+1负", "Exact 2+1- Match"),
    "approx_2": ("近似匹配 2 词条", "Approx 2-Stat Match"),
    "weapon_all": ("全武器均价", "All Weapon Listings"),
}


class WMFetchError(RuntimeError):
    """User-friendly error raised when Warframe.market API fails or rate-limits."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class WMStatFreq:
    """Frequency statistics for one attribute across active WM Riven listings."""

    stat_name: str
    count: int
    ratio_pct: float  # 0.0 .. 100.0


@dataclass
class WMMatchingListing:
    """One live Warframe.market auction listing for side-by-side price comparison."""

    price: int
    seller_status: str  # "ingame" | "online" | "offline"
    is_online: bool
    rerolls: int
    positives: List[str] = field(default_factory=list)
    negatives: List[str] = field(default_factory=list)
    seller_ign: str = ""
    auction_id: str = ""

    def format_line(self, lang: str = "zh_CN") -> str:
        short_map = SHORT_STAT_EN if lang == "en_US" else SHORT_STAT_ZH
        pos_part = " ".join(f"+{short_map.get(s, s)}" for s in self.positives[:3])
        neg_part = " ".join(f"-{short_map.get(s, s)}" for s in self.negatives[:1])
        stats_str = " ".join(p for p in (pos_part, neg_part) if p) or "-"

        if lang == "en_US":
            status_lbl = (
                "In-Game"
                if self.seller_status == "ingame"
                else ("Online" if self.seller_status == "online" else "Offline")
            )
            reroll_lbl = f"{self.rerolls}r"
        else:
            status_lbl = (
                "游戏内"
                if self.seller_status == "ingame"
                else ("在线" if self.seller_status == "online" else "离线")
            )
            reroll_lbl = f"{self.rerolls}洗"

        return f"{self.price}p ({status_lbl}) | {stats_str} | {reroll_lbl}"


@dataclass
class WMMarketReport:
    """Aggregated Warframe.market exact-stat or full-weapon Riven market report."""

    weapon_name: str
    weapon_slug: str
    weapon_class: str
    top_positives: List[WMStatFreq] = field(default_factory=list)
    top_negatives: List[WMStatFreq] = field(default_factory=list)
    min_price: Optional[int] = None
    median_price: Optional[int] = None
    high_tier_median: Optional[int] = None
    sample_count: int = 0
    online_count: int = 0
    fetched_at_iso: str = ""
    saved_to_local: bool = False
    match_level: str = "weapon_all"  # exact_3_neg | exact_3 | exact_2_neg | approx_2 | weapon_all
    match_label_zh: str = "全武器均价"
    match_label_en: str = "All Weapon Listings"
    matched_pos_stats: List[str] = field(default_factory=list)
    matched_neg_stat: Optional[str] = None
    ignored_unknown_stats: List[str] = field(default_factory=list)
    top_listings: List[WMMatchingListing] = field(default_factory=list)
    search_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weapon_name": self.weapon_name,
            "weapon_slug": self.weapon_slug,
            "weapon_class": self.weapon_class,
            "top_positives": [asdict(x) for x in self.top_positives],
            "top_negatives": [asdict(x) for x in self.top_negatives],
            "min_price": self.min_price,
            "median_price": self.median_price,
            "high_tier_median": self.high_tier_median,
            "sample_count": self.sample_count,
            "online_count": self.online_count,
            "fetched_at_iso": self.fetched_at_iso,
            "saved_to_local": self.saved_to_local,
            "match_level": self.match_level,
            "match_label_zh": self.match_label_zh,
            "match_label_en": self.match_label_en,
            "matched_pos_stats": self.matched_pos_stats,
            "matched_neg_stat": self.matched_neg_stat,
            "ignored_unknown_stats": self.ignored_unknown_stats,
            "top_listings": [asdict(x) for x in self.top_listings],
            "search_url": self.search_url,
        }

    def match_label(self, lang: str = "zh_CN") -> str:
        return self.match_label_en if lang == "en_US" else self.match_label_zh

    def price_summary(self) -> str:
        parts: List[str] = []
        if self.min_price is not None:
            parts.append(f"最低 {self.min_price}p")
        if self.median_price is not None:
            parts.append(f"均价中位 {self.median_price}p")
        if self.high_tier_median is not None and (
            self.median_price is None or self.high_tier_median > self.median_price * 1.2
        ):
            parts.append(f"高配中位 {self.high_tier_median}p")
        return " · ".join(parts) if parts else "暂无明确一口价"


def load_valid_wm_attribute_slugs(
    attributes_path: Optional[str | Path] = None,
) -> Set[str]:
    """Load the official set of Warframe.market `riven/attributes` slugs from `data/riven_attributes.json`.

    Uses file mtime caching for sub-millisecond lookups and refreshes automatically
    when the user updates the local database.
    """
    global _VALID_WM_SLUGS_CACHE
    path = Path(attributes_path) if attributes_path else DEFAULT_ATTRIBUTES_PATH
    if path.is_file():
        try:
            mtime = path.stat().st_mtime
            if attributes_path is None and _VALID_WM_SLUGS_CACHE[0] == mtime and _VALID_WM_SLUGS_CACHE[1]:
                return _VALID_WM_SLUGS_CACHE[1]
            data = json.loads(path.read_text(encoding="utf-8"))
            attrs = data.get("attributes") or {}
            if isinstance(attrs, dict) and attrs:
                slugs = {str(k).strip().lower() for k in attrs.keys() if str(k).strip()}
                if attributes_path is None:
                    _VALID_WM_SLUGS_CACHE = (mtime, slugs)
                return slugs
        except Exception:
            pass
    # Safe fallback to known WM slugs if riven_attributes.json is unavailable
    return {k.strip().lower() for k in SLUG_TO_CANONICAL_ZH.keys()} | {
        v.strip().lower() for v in CANONICAL_ZH_TO_WM_SLUG.values()
    }


def stat_name_to_wm_slug(
    stat_name: str,
    validate_official: bool = True,
    valid_slugs: Optional[Set[str]] = None,
) -> Optional[str]:
    """Convert canonical Simplified Chinese stat name (or WM slug) to verified official WM v1 slug.

    When `validate_official=True` (default), verifies that the resulting slug actually
    exists in WM's official `riven/attributes` list; returns `None` for newly introduced
    or hybrid stats not yet indexed by Warframe.market so F9 queries never return HTTP 400.
    """
    cleaned = (stat_name or "").strip()
    if not cleaned:
        return None
    candidate: Optional[str] = None
    if cleaned in CANONICAL_ZH_TO_WM_SLUG:
        candidate = CANONICAL_ZH_TO_WM_SLUG[cleaned]
    else:
        low = cleaned.lower()
        if low in SLUG_TO_CANONICAL_ZH:
            candidate = low
        else:
            # Also check custom_riven_stats.json for user-supplied wm_slug
            try:
                from src.riven_parser import CUSTOM_STAT_META

                cmeta = CUSTOM_STAT_META.get(cleaned) or {}
                c_slug = str(cmeta.get("wm_slug") or "").strip().lower()
                if c_slug:
                    candidate = c_slug
            except Exception:
                pass
            if candidate is None and low in (valid_slugs or load_valid_wm_attribute_slugs()):
                candidate = low

    if not candidate:
        return None

    candidate = candidate.strip().lower()
    if validate_official:
        official = valid_slugs if valid_slugs is not None else load_valid_wm_attribute_slugs()
        if official and candidate not in official:
            return None
    return candidate


def _wm_slug_to_cn_stat(url_name: str, weapon_class: str) -> str:
    """Translate a WM attribute `url_name` into our canonical Simplified Chinese name."""
    key = (url_name or "").strip().lower()
    if key == "fire_rate_/_attack_speed":
        return "攻击速度" if weapon_class == "melee" else "射速"
    return SLUG_TO_CANONICAL_ZH.get(key, key)


def build_wm_query_params(
    weapon_slug: str,
    positive_slugs: Sequence[str] = (),
    negative_slug: Optional[str] = None,
    sort_by: str = "price_asc",
    buyout_policy: Optional[str] = None,
) -> Dict[str, str]:
    """Build URL query parameters shared by both WM v1 API and WM web search page."""
    params: Dict[str, str] = {
        "type": "riven",
        "weapon_url_name": (weapon_slug or "").strip().lower(),
        "polarity": "any",
        "sort_by": sort_by,
    }
    if buyout_policy:
        params["buyout_policy"] = buyout_policy
    clean_pos = [s.strip().lower() for s in positive_slugs if s and s.strip()]
    if clean_pos:
        params["positive_stats"] = ",".join(clean_pos[:3])
    if negative_slug and negative_slug.strip():
        params["negative_stats"] = negative_slug.strip().lower()
    return params


def build_wm_search_url(
    weapon_slug: str,
    positive_stats: Sequence[str] = (),
    negative_stat: Optional[str] = None,
    sort_by: str = "price_asc",
) -> str:
    """Build a direct `https://warframe.market/auctions/search?...` web URL pre-filled with weapon & stats."""
    slug = (weapon_slug or "").strip().lower()
    if not slug:
        return WM_AUCTIONS_WEB_URL
    pos_slugs: List[str] = []
    for st in positive_stats:
        wm_s = stat_name_to_wm_slug(st)
        if wm_s and wm_s not in pos_slugs:
            pos_slugs.append(wm_s)
    neg_slug = stat_name_to_wm_slug(negative_stat) if negative_stat else None
    params = build_wm_query_params(
        weapon_slug=slug,
        positive_slugs=pos_slugs[:3],
        negative_slug=neg_slug,
        sort_by=sort_by,
    )
    return f"{WM_AUCTIONS_WEB_URL}?{urllib.parse.urlencode(params)}"


def _extract_price(auction: Dict[str, Any]) -> Optional[int]:
    buyout = auction.get("buyout_price")
    if isinstance(buyout, (int, float)) and buyout > 0:
        return int(buyout)
    start = auction.get("starting_price")
    if isinstance(start, (int, float)) and start > 0:
        return int(start)
    return None


def _seller_status(auction: Dict[str, Any]) -> str:
    status = ((auction.get("owner") or {}).get("status") or "offline").lower()
    if status in ("ingame", "online"):
        return status
    return "offline"


def _is_online(auction: Dict[str, Any]) -> bool:
    return _seller_status(auction) in ("ingame", "online")


def _filter_active_auctions(auctions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        a
        for a in auctions
        if isinstance(a, dict) and not a.get("closed", False) and a.get("visible", True)
    ]


def extract_top_listings(
    auctions: Sequence[Dict[str, Any]],
    weapon_class: str,
    limit: int = 5,
) -> List[WMMatchingListing]:
    """Extract the lowest-priced active listings (preferring online sellers first if enough exist)."""
    active = _filter_active_auctions(auctions)
    parsed: List[WMMatchingListing] = []
    seen_ids = set()

    for auc in active:
        aid = str(auc.get("id") or id(auc))
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        price = _extract_price(auc)
        if price is None or price > 50000:
            continue

        item = auc.get("item") or {}
        attrs = item.get("attributes") or []
        pos_list: List[str] = []
        neg_list: List[str] = []
        for attr in attrs:
            u = attr.get("url_name", "")
            if not u:
                continue
            cn = _wm_slug_to_cn_stat(u, weapon_class)
            if attr.get("positive", True):
                if cn not in pos_list:
                    pos_list.append(cn)
            else:
                if cn not in neg_list:
                    neg_list.append(cn)

        status = _seller_status(auc)
        owner = auc.get("owner") or {}
        rerolls = item.get("re_rolls")
        rerolls_int = int(rerolls) if isinstance(rerolls, (int, float)) else 0

        parsed.append(
            WMMatchingListing(
                price=price,
                seller_status=status,
                is_online=status in ("ingame", "online"),
                rerolls=max(0, rerolls_int),
                positives=pos_list,
                negatives=neg_list,
                seller_ign=str(owner.get("ingame_name") or ""),
                auction_id=aid,
            )
        )

    # Sort primarily by lowest price; break ties by online/ingame first, then fewer rerolls
    parsed.sort(key=lambda x: (x.price, 0 if x.seller_status == "ingame" else (1 if x.is_online else 2), x.rerolls))

    online_only = [x for x in parsed if x.is_online]
    if len(online_only) >= limit:
        return online_only[:limit]
    return parsed[:limit]


def analyze_auctions_payload(
    weapon_name: str,
    weapon_slug: str,
    weapon_class: str,
    desc_auctions: Sequence[Dict[str, Any]],
    asc_auctions: Optional[Sequence[Dict[str, Any]]] = None,
    top_pos_limit: int = 4,
    top_neg_limit: int = 2,
    match_level: str = "weapon_all",
    matched_pos_stats: Sequence[str] = (),
    matched_neg_stat: Optional[str] = None,
    ignored_unknown_stats: Sequence[str] = (),
    search_url: Optional[str] = None,
) -> WMMarketReport:
    """Compute hot positive/negative Riven stats, platinum prices, and top-5 listings."""
    active_desc = _filter_active_auctions(desc_auctions)
    active_asc = _filter_active_auctions(asc_auctions) if asc_auctions is not None else []

    if not active_desc and not active_asc:
        raise WMFetchError(f"WM 暂无「{weapon_name}」的在售紫卡挂单（已保留本地推荐词条）")

    # Prefer online/ingame high-tier listings for stat frequency; fall back to all visible desc listings
    online_desc = [a for a in active_desc if _is_online(a)]
    stat_pool = online_desc if len(online_desc) >= 8 else active_desc
    stat_pool = stat_pool[:60] if stat_pool else active_asc[:60]

    pos_counter: Counter[str] = Counter()
    neg_counter: Counter[str] = Counter()
    total_with_attrs = 0
    total_with_neg = 0

    for auc in stat_pool:
        attrs = (auc.get("item") or {}).get("attributes") or []
        if not attrs:
            continue
        total_with_attrs += 1
        seen_pos = set()
        seen_neg = set()
        has_neg = False
        for attr in attrs:
            url_name = attr.get("url_name", "")
            if not url_name:
                continue
            cn_name = _wm_slug_to_cn_stat(url_name, weapon_class)
            is_pos = bool(attr.get("positive", True))
            if is_pos:
                if cn_name not in seen_pos:
                    pos_counter[cn_name] += 1
                    seen_pos.add(cn_name)
            else:
                has_neg = True
                if cn_name not in seen_neg:
                    neg_counter[cn_name] += 1
                    seen_neg.add(cn_name)
        if has_neg:
            total_with_neg += 1

    denom_pos = max(1, total_with_attrs)
    denom_neg = max(1, total_with_neg)

    top_pos = [
        WMStatFreq(stat_name=name, count=cnt, ratio_pct=round(cnt * 100.0 / denom_pos, 1))
        for name, cnt in pos_counter.most_common(top_pos_limit)
    ]
    top_neg = [
        WMStatFreq(stat_name=name, count=cnt, ratio_pct=round(cnt * 100.0 / denom_neg, 1))
        for name, cnt in neg_counter.most_common(top_neg_limit)
    ]

    # Reference platinum prices (filter out absurd troll buyouts > 50,000p)
    price_source_asc = active_asc or active_desc
    online_asc = [a for a in price_source_asc if _is_online(a)]
    floor_pool = online_asc if len(online_asc) >= 5 else price_source_asc
    floor_prices = [p for a in floor_pool if (p := _extract_price(a)) is not None and p <= 50000]

    min_price = min(floor_prices) if floor_prices else None
    median_price = int(round(statistics.median(floor_prices))) if floor_prices else None

    high_pool = online_desc if len(online_desc) >= 5 else active_desc
    high_prices = [p for a in high_pool[:30] if (p := _extract_price(a)) is not None and p <= 30000]
    high_tier_median = int(round(statistics.median(high_prices))) if high_prices else None

    # Deduplicate combined sample count by auction id
    unique_ids = set()
    online_ids = set()
    combined = list(active_asc) + list(active_desc)
    for a in combined:
        aid = a.get("id") or id(a)
        unique_ids.add(aid)
        if _is_online(a):
            online_ids.add(aid)

    top_listings = extract_top_listings(price_source_asc, weapon_class=weapon_class, limit=5)
    lbl_zh, lbl_en = MATCH_LEVEL_LABELS.get(match_level, MATCH_LEVEL_LABELS["weapon_all"])
    final_url = search_url or build_wm_search_url(
        weapon_slug=weapon_slug,
        positive_stats=matched_pos_stats,
        negative_stat=matched_neg_stat,
        sort_by="price_asc",
    )

    now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return WMMarketReport(
        weapon_name=weapon_name,
        weapon_slug=weapon_slug,
        weapon_class=weapon_class,
        top_positives=top_pos,
        top_negatives=top_neg,
        min_price=min_price,
        median_price=median_price,
        high_tier_median=high_tier_median,
        sample_count=len(unique_ids),
        online_count=len(online_ids),
        fetched_at_iso=now_iso,
        saved_to_local=False,
        match_level=match_level,
        match_label_zh=lbl_zh,
        match_label_en=lbl_en,
        matched_pos_stats=list(matched_pos_stats),
        matched_neg_stat=matched_neg_stat,
        ignored_unknown_stats=list(ignored_unknown_stats),
        top_listings=top_listings,
        search_url=final_url,
    )


def _http_get_auctions(
    weapon_slug: str,
    sort_by: str = "price_asc",
    positive_slugs: Sequence[str] = (),
    negative_slug: Optional[str] = None,
    buyout_policy: Optional[str] = "direct",
    timeout: int = WM_HTTP_TIMEOUT_SEC,
) -> List[Dict[str, Any]]:
    params = build_wm_query_params(
        weapon_slug=weapon_slug,
        positive_slugs=positive_slugs,
        negative_slug=negative_slug,
        sort_by=sort_by,
        buyout_policy=buyout_policy,
    )
    url = f"{WM_AUCTIONS_API_URL}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/json",
        "Platform": "pc",
        "Language": "en",
        "User-Agent": "WarframeRivenOverlay/2.2",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("payload", {}).get("auctions", []) or []
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise WMFetchError(
                "WM 接口请求频繁被限流 (HTTP 429)，请稍后再试（已保留本地推荐词条）",
                status_code=429,
            ) from exc
        if exc.code == 400:
            raise WMFetchError(
                f"WM 未收录该武器标识 '{weapon_slug}' 或词条参数无效 (HTTP 400)",
                status_code=400,
            ) from exc
        raise WMFetchError(
            f"WM 接口返回异常 (HTTP {exc.code})，已保留本地推荐词条",
            status_code=exc.code,
        ) from exc
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        raise WMFetchError("WM 网络连接超时或服务繁忙，已保留本地推荐词条") from exc
    except Exception as exc:
        raise WMFetchError(f"WM 数据解析失败: {exc}（已保留本地推荐词条）") from exc


def _rank_positive_stats_for_query(
    positive_stats: Sequence[str],
    recommended_positives: Sequence[str] = (),
    valid_slugs: Optional[Set[str]] = None,
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Return `(valid_ranked_pairs, ignored_unknown_stats)` verified against WM official slugs."""
    official = valid_slugs if valid_slugs is not None else load_valid_wm_attribute_slugs()
    rec_rank = {st: (200 - i * 10) for i, st in enumerate(recommended_positives)}
    seen_slugs: Set[str] = set()
    ignored: List[str] = []
    pairs: List[Tuple[int, int, str, str]] = []
    for idx, st in enumerate(positive_stats):
        st_clean = (st or "").strip()
        if not st_clean:
            continue
        slug = stat_name_to_wm_slug(st_clean, validate_official=True, valid_slugs=official)
        if not slug:
            if st_clean not in ignored:
                ignored.append(st_clean)
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        score = rec_rank.get(st_clean, CORE_POSITIVE_PRIORITY.get(st_clean, 10))
        pairs.append((-score, idx, st_clean, slug))
    pairs.sort()
    return [(st, slug) for _, _, st, slug in pairs[:3]], ignored


def fetch_wm_exact_riven_matches(
    weapon_name: str,
    weapon_slug: str,
    weapon_class: str = "rifle",
    positive_stats: Sequence[str] = (),
    negative_stat: Optional[str] = None,
    recommended_positives: Sequence[str] = (),
    recommended_negatives: Sequence[str] = (),
    include_high_value_negative: bool = True,
    timeout: int = WM_HTTP_TIMEOUT_SEC,
    fetcher: Any = None,
    valid_slugs: Optional[Set[str]] = None,
) -> WMMarketReport:
    """Query live Warframe.market auctions matching current Riven stats with smart fallback.

    Automatically validates every stat against WM's official `riven/attributes` slug list:
    any unindexed new/hybrid stat is skipped cleanly (and reported via `ignored_unknown_stats`)
    so F9 queries never fail with HTTP 400 or 0-result crashes.

    Cascading match strategy:
      1. 3 positive stats + high-value negative (if present & high value)
      2. 3 positive stats (exact 3-stat match)
      3. Top 2 core positive stats (approx 2-stat match)
      4. Full weapon listings (weapon-wide fallback)
    """
    slug = (weapon_slug or "").strip().lower()
    if not slug:
        raise WMFetchError(f"未找到「{weapon_name}」对应的 WM 武器标识（已保留本地推荐词条）")

    official_slugs = valid_slugs if valid_slugs is not None else load_valid_wm_attribute_slugs()
    query_fn = fetcher or _http_get_auctions
    ranked_pos, ignored_unknown = _rank_positive_stats_for_query(
        positive_stats,
        recommended_positives,
        valid_slugs=official_slugs,
    )

    # Decide whether negative_stat is valid on WM & high-value enough to filter on first
    high_neg_pool = set(HIGH_VALUE_NEGATIVES_DEFAULT) | set(recommended_negatives)
    neg_slug: Optional[str] = None
    if negative_stat and negative_stat.strip():
        checked_neg = stat_name_to_wm_slug(negative_stat, validate_official=True, valid_slugs=official_slugs)
        if not checked_neg:
            if negative_stat.strip() not in ignored_unknown:
                ignored_unknown.append(negative_stat.strip())
        elif include_high_value_negative and negative_stat in high_neg_pool:
            neg_slug = checked_neg

    # Build cascading query steps: (match_level, pos_pairs, neg_zh, neg_slug_val)
    attempts: List[Tuple[str, List[Tuple[str, str]], Optional[str], Optional[str]]] = []
    if len(ranked_pos) >= 3:
        if neg_slug:
            attempts.append(("exact_3_neg", ranked_pos[:3], negative_stat, neg_slug))
        attempts.append(("exact_3", ranked_pos[:3], None, None))
        attempts.append(("approx_2", ranked_pos[:2], None, None))
    elif len(ranked_pos) == 2:
        if neg_slug:
            attempts.append(("exact_2_neg", ranked_pos[:2], negative_stat, neg_slug))
        attempts.append(("approx_2", ranked_pos[:2], None, None))
    elif len(ranked_pos) == 1:
        attempts.append(("approx_2", ranked_pos[:1], None, None))

    # Always append full-weapon fallback as the final safety net
    attempts.append(("weapon_all", [], None, None))

    for idx, (level, pos_pairs, neg_zh, neg_s) in enumerate(attempts):
        if idx > 0 and fetcher is None:
            time.sleep(0.35)

        pos_slugs = [s for _, s in pos_pairs]
        pos_names = [n for n, _ in pos_pairs]
        # For exact stat matching, allow both direct buyout & auction listings so rare god-rolls aren't missed
        policy = "direct" if level == "weapon_all" else None
        try:
            auctions = query_fn(
                slug,
                sort_by="price_asc",
                positive_slugs=pos_slugs,
                negative_slug=neg_s,
                buyout_policy=policy,
                timeout=timeout,
            )
        except WMFetchError as exc:
            # If an attribute slug triggers 400 on a specific attempt, gracefully degrade to next step
            if exc.status_code == 400 and level != "weapon_all":
                continue
            raise

        active = _filter_active_auctions(auctions)
        if active:
            # If we fell all the way back to full weapon, also fetch desc_auctions for overall meta stats
            desc_aucs: List[Dict[str, Any]] = []
            if level == "weapon_all":
                if fetcher is None:
                    time.sleep(0.35)
                try:
                    desc_aucs = query_fn(
                        slug,
                        sort_by="price_desc",
                        positive_slugs=(),
                        negative_slug=None,
                        buyout_policy="direct",
                        timeout=timeout,
                    )
                except WMFetchError:
                    desc_aucs = []
            else:
                desc_aucs = active

            url = build_wm_search_url(
                weapon_slug=slug,
                positive_stats=pos_names,
                negative_stat=neg_zh,
                sort_by="price_asc",
            )
            return analyze_auctions_payload(
                weapon_name=weapon_name,
                weapon_slug=slug,
                weapon_class=weapon_class,
                desc_auctions=desc_aucs,
                asc_auctions=active,
                match_level=level,
                matched_pos_stats=pos_names,
                matched_neg_stat=neg_zh,
                ignored_unknown_stats=ignored_unknown,
                search_url=url,
            )

    raise WMFetchError(f"WM 暂无「{weapon_name}」的在售紫卡挂单（已保留本地推荐词条）")


def fetch_wm_riven_stats(
    weapon_name: str,
    weapon_slug: str,
    weapon_class: str = "rifle",
    timeout: int = WM_HTTP_TIMEOUT_SEC,
) -> WMMarketReport:
    """Query live Warframe.market full-weapon auctions for `weapon_slug` (meta stats & floor prices)."""
    slug = (weapon_slug or "").strip().lower()
    if not slug:
        raise WMFetchError(f"未找到「{weapon_name}」对应的 WM 武器标识（已保留本地推荐词条）")

    desc_auctions = _http_get_auctions(slug, sort_by="price_desc", timeout=timeout)
    time.sleep(0.35)
    try:
        asc_auctions = _http_get_auctions(slug, sort_by="price_asc", timeout=timeout)
    except WMFetchError:
        asc_auctions = []

    return analyze_auctions_payload(
        weapon_name=weapon_name,
        weapon_slug=slug,
        weapon_class=weapon_class,
        desc_auctions=desc_auctions,
        asc_auctions=asc_auctions,
        match_level="weapon_all",
        search_url=build_wm_search_url(slug),
    )


def save_wm_recommendation_to_local(
    report: WMMarketReport,
    tiers_path: Optional[str | Path] = None,
    force_save: bool = False,
) -> bool:
    """Persist full-weapon WM-derived hot stats into `data/recommended_tiers.json`."""
    # Only overwrite weapon meta recommendation table from full-weapon scans (unless force_save)
    if report.match_level != "weapon_all" and not force_save:
        return False

    path = Path(tiers_path) if tiers_path else (get_data_dir() / "recommended_tiers.json")
    data: Dict[str, Any] = {
        "schema_version": 2,
        "auto_save_wm_to_local": True,
        "weapons": {},
        "special_rules": {},
    }
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except Exception:
            pass

    auto_save = bool(data.get("auto_save_wm_to_local", True))
    if not auto_save and not force_save:
        return False

    pos_list = [x.stat_name for x in report.top_positives[:4]]
    neg_list = [x.stat_name for x in report.top_negatives[:2]]
    if not pos_list:
        return False

    weapons_map = data.setdefault("weapons", {})
    weapons_map[report.weapon_name] = {
        "positives": pos_list,
        "negatives": neg_list,
        "source": "wm",
        "weapon_slug": report.weapon_slug,
        "wm_updated_at": report.fetched_at_iso,
        "wm_min_price": report.min_price,
        "wm_median_price": report.median_price,
        "wm_high_tier_median": report.high_tier_median,
        "wm_sample_count": report.sample_count,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    report.saved_to_local = True
    return True
