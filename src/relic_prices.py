"""Void Relic (开核桃) Prime reward instant local price & Ducat lookup engine.

Fetches all tradable Prime parts' ZH/EN names, recent 24h average platinum price,
and Ducat value in TWO lightweight bulk requests to Warframe.market:
  1. `https://api.warframe.market/v2/items` (`Language: zh-hans`) -> ~759 Prime items
  2. `https://api.warframe.market/v1/tools/ducats`                -> live 24h `wa_price`, `median`, `ducats`

Caches results atomically to `data/prime_prices.json` and keeps an indexed
in-memory dictionary for sub-millisecond (`< 1ms`) fuzzy matching when F9 is
pressed in `[Mode 4/4] Relic Rewards`.
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.data_updater import get_data_dir

WM_V2_ITEMS_URL = "https://api.warframe.market/v2/items"
WM_V1_DUCATS_URL = "https://api.warframe.market/v1/tools/ducats"
WFCD_RELICS_URLS = (
    "https://cdn.jsdelivr.net/gh/WFCD/warframe-items@master/data/json/Relics.json",
    "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/Relics.json",
)
DEFAULT_PRIME_PRICES_FILENAME = "prime_prices.json"
HTTP_TIMEOUT_SEC = 16

# Common Void Relic UI chrome text that is NOT a reward name
_RELIC_NOISE_PATTERNS = (
    "选择奖励",
    "選擇獎勵",
    "虚空遗物",
    "虛空遺物",
    "奖励选择",
    "獎勵選擇",
    "已拥有",
    "已擁有",
    "已制造",
    "已製造",
    "制作完成",
    "製作完成",
    "剩余时间",
    "剩餘時間",
    "select reward",
    "void fissure",
    "owned",
    "crafted",
    "vaulted",
)

# Common Prime part suffixes (used to merge wrapped 2-line OCR reward labels)
_PART_SUFFIX_KEYWORDS = (
    "蓝图",
    "机体",
    "系统",
    "头部神经光元",
    "枪管",
    "枪机",
    "枪托",
    "握柄",
    "刀刃",
    "拳套",
    "弓臂",
    "弓弦",
    "连接器",
    "饰物",
    "外壳",
    "头部",
    "翅膀",
    "动力装置",
    "blueprint",
    "chassis",
    "systems",
    "neuroptics",
    "barrel",
    "receiver",
    "stock",
    "handle",
    "blade",
    "gauntlet",
    "limb",
    "string",
    "link",
    "ornament",
    "carapace",
    "cerebrum",
    "harness",
    "wings",
)


def get_prime_prices_path() -> Path:
    return get_data_dir() / DEFAULT_PRIME_PRICES_FILENAME


def _normalize_key(text: str) -> str:
    cleaned = re.sub(r"[\s·•\-_:：|（）()\[\]【】]+", "", (text or "").strip().lower())
    return cleaned


def _strip_blueprint_suffix(norm_key: str) -> str:
    for suf in ("蓝图", "藍圖", "blueprint"):
        if norm_key.endswith(suf) and len(norm_key) > len(suf) + 2:
            return norm_key[: -len(suf)]
    # Handle 2-line wrap where "蓝图" split into line1 "...蓝" + line2 "图" (e.g. "Lavos Prime 机体 蓝")
    for suf in ("蓝", "藍"):
        if norm_key.endswith(suf) and len(norm_key) > len(suf) + 4 and "prime" in norm_key:
            return norm_key[: -len(suf)]
    return norm_key


def _is_forma_text(text: str) -> bool:
    low = _normalize_key(text)
    if not low:
        return False
    return low in ("forma", "forma蓝图", "formablueprint", "福马", "福马蓝图") or (
        "forma" in low and "prime" not in low and len(low) <= 16
    )


def _is_noise_line(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 2:
        return True
    if re.fullmatch(r"[\d\s.,:%/+\-xX秒s]+", t):
        return True
    low = t.lower()
    if any(p in low for p in _RELIC_NOISE_PATTERNS):
        return True
    return False


@dataclass
class PrimeItemPrice:
    """Cached market & Ducat value for one tradable Prime part."""

    item_id: str
    slug: str
    zh_name: str
    en_name: str
    plat_price: float
    ducats: int
    volume: int = 0
    vaulted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RelicRewardMatch:
    """Resolved reward entry displayed in the Void Relic comparison overlay."""

    raw_text: str
    matched_zh: str
    matched_en: str
    plat_price: Optional[float]  # None for Forma / unknown
    ducats: int
    is_forma: bool = False
    is_matched: bool = False
    is_best_plat: bool = False
    is_best_ducats: bool = False
    is_vaulted: bool = False
    recommend_rank: int = 0  # 1=1st recommendation (Gold), 2=2nd recommendation (Silver), 0=Normal
    match_score: float = 0.0
    column_center_x_ratio: Optional[float] = None

    def display_name(self, lang: str = "zh_CN") -> str:
        if self.is_forma:
            return "Forma 蓝图" if lang in ("zh_CN", "zh_TW") else "Forma Blueprint"
        if lang not in ("zh_CN", "zh_TW"):
            return self.matched_en or self.matched_zh or self.raw_text
        return self.matched_zh or self.matched_en or self.raw_text

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_box_geometry(box: Any) -> Tuple[str, float, float, float, float]:
    """Extract `(text, x_min, x_max, y_min, y_max)` from an `OCRBox`, dict, or `(bbox, text, score)`."""
    if isinstance(box, dict):
        text = str(box.get("text") or "").strip()
        pts = box.get("bbox") or []
    elif hasattr(box, "text") and hasattr(box, "bbox"):
        text = str(box.text or "").strip()
        pts = box.bbox or []
    elif isinstance(box, (list, tuple)) and len(box) >= 2:
        pts, text = box[0], str(box[1] or "").strip()
    else:
        return "", 0.0, 0.0, 0.0, 0.0

    if not pts:
        return text, 0.0, 0.0, 0.0, 0.0
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return text, min(xs), max(xs), min(ys), max(ys)


def _split_glued_prime_string(text: str) -> List[str]:
    """Safety net: if one OCR box accidentally glued 2+ Prime rewards together, split them cleanly."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    # Count how many times "Prime" appears (case-insensitive)
    prime_hits = list(re.finditer(r"prime", cleaned, re.IGNORECASE))
    if len(prime_hits) <= 1:
        return [cleaned]

    # Split after a known part suffix whenever another Prime item name follows it
    suffix_alt = "|".join(re.escape(k) for k in sorted(_PART_SUFFIX_KEYWORDS, key=len, reverse=True))
    boundary_re = re.compile(
        rf"((?:{suffix_alt})(?:\s*(?:蓝图|blueprint))?)\s+(?=[\u4e00-\u9fffA-Za-z])",
        re.IGNORECASE,
    )
    parts: List[str] = []
    start = 0
    for m in boundary_re.finditer(cleaned):
        end_pos = m.end(1)
        seg = cleaned[start:end_pos].strip()
        rem = cleaned[m.end() :].strip()
        if seg and rem and re.search(r"prime|forma|福马", rem, re.IGNORECASE):
            parts.append(seg)
            start = m.end()
    tail = cleaned[start:].strip()
    if tail:
        parts.append(tail)
    return parts if len(parts) >= 2 else [cleaned]


def split_ocr_boxes_into_relic_columns_with_centers(
    boxes: Sequence[Any],
    image_width: int = 0,
    max_columns: int = 4,
) -> List[Tuple[str, Optional[float]]]:
    """Group RapidOCR text boxes into 1..4 dynamic horizontal reward columns by X clustering.

    Automatically handles 1 (solo), 2 (duo), 3 (trio), and 4 (full squad) Void Relic
    layouts — including centered 1/2/3-card screens where fixed 4-bucket slicing would
    otherwise split centered reward names across bucket boundaries.

    Returns:
        List of `(assembled_text, center_x_ratio_in_roi_or_None)` ordered left-to-right.
    """
    parsed: List[Tuple[str, float, float, float, float, float, float]] = []
    eff_width = float(image_width) if image_width and image_width > 0 else 0.0
    slot_w_est = (eff_width / float(max_columns)) if eff_width > 0 else 0.0

    for box in boxes:
        text, x_min, x_max, y_min, y_max = _extract_box_geometry(box)
        if not text or _is_noise_line(text):
            continue
        box_w = x_max - x_min
        # If a single OCR detector box spanned > 1.35 card widths and glued 2+ Prime names, split first
        if slot_w_est > 0 and box_w > slot_w_est * 1.35:
            split_pieces = _split_glued_prime_string(text)
            if len(split_pieces) >= 2:
                sub_w = box_w / float(len(split_pieces))
                y_center = (y_min + y_max) / 2.0
                for si, sp in enumerate(split_pieces):
                    sx0 = x_min + si * sub_w
                    sx1 = x_min + (si + 1) * sub_w
                    parsed.append((sp, sx0, sx1, y_min, y_max, (sx0 + sx1) / 2.0, y_center))
                continue
        x_center = (x_min + x_max) / 2.0
        y_center = (y_min + y_max) / 2.0
        parsed.append((text, x_min, x_max, y_min, y_max, x_center, y_center))

    if not parsed:
        return []

    # Estimate single reward card width (Warframe uses fixed card width ~= ROI_width / 4 for 1..4 rewards)
    if slot_w_est > 0:
        card_w = slot_w_est
    else:
        span = max(p[2] for p in parsed) - min(p[1] for p in parsed)
        card_w = max(120.0, span / float(max_columns)) if span >= 240.0 else max(180.0, span + 40.0)

    # Dynamic 1D X-center & horizontal-span clustering (works seamlessly for 1, 2, 3, or 4 centered cards!)
    center_gap_thresh = card_w * 0.58
    max_cluster_span = card_w * 1.18

    by_x = sorted(parsed, key=lambda p: p[5])
    clusters: List[List[Tuple[str, float, float, float, float, float, float]]] = [[by_x[0]]]
    for item in by_x[1:]:
        prev_cluster = clusters[-1]
        cl_center = sum(p[5] for p in prev_cluster) / len(prev_cluster)
        cl_xmin = min(p[1] for p in prev_cluster)
        cl_xmax = max(p[2] for p in prev_cluster)
        merged_span = max(cl_xmax, item[2]) - min(cl_xmin, item[1])
        same_col = (
            abs(item[5] - cl_center) <= center_gap_thresh
            and merged_span <= max_cluster_span
        )
        if same_col:
            prev_cluster.append(item)
        else:
            clusters.append([item])

    # Assemble each cluster strictly top-to-bottom (NEVER across columns)
    column_entries: List[Tuple[str, Optional[float]]] = []
    for col_boxes in clusters:
        col_boxes.sort(key=lambda p: (p[6], p[1]))
        v_rows: List[List[Tuple[str, float, float, float, float, float, float]]] = []
        for item in col_boxes:
            placed = False
            h_item = max(8.0, item[4] - item[3])
            for vr in v_rows:
                avg_y = sum(x[6] for x in vr) / len(vr)
                avg_h = sum(max(8.0, x[4] - x[3]) for x in vr) / len(vr)
                if abs(item[6] - avg_y) <= max(8.0, min(h_item, avg_h) * 0.55):
                    vr.append(item)
                    placed = True
                    break
            if not placed:
                v_rows.append([item])

        v_rows.sort(key=lambda vr: sum(x[6] for x in vr) / len(vr))
        row_strings: List[str] = []
        for vr in v_rows:
            vr.sort(key=lambda x: x[1])
            row_txt = " ".join(x[0].strip() for x in vr if x[0].strip()).strip()
            if row_txt:
                row_strings.append(row_txt)

        joined = re.sub(r"\s+", " ", " ".join(row_strings)).strip()
        if not joined or _is_noise_line(joined):
            continue

        cl_x0 = min(p[1] for p in col_boxes)
        cl_x1 = max(p[2] for p in col_boxes)
        cl_xc = (cl_x0 + cl_x1) / 2.0

        segs = [s for s in _split_glued_prime_string(joined) if s and not _is_noise_line(s)]
        if len(segs) >= 2 and eff_width > 0:
            seg_w = max(1.0, (cl_x1 - cl_x0) / float(len(segs)))
            for si, seg in enumerate(segs):
                ratio = max(0.0, min(1.0, (cl_x0 + (si + 0.5) * seg_w) / eff_width))
                column_entries.append((seg, round(ratio, 4)))
        else:
            ratio = round(max(0.0, min(1.0, cl_xc / eff_width)), 4) if eff_width > 0 else None
            for seg in segs:
                column_entries.append((seg, ratio))

    return column_entries[:max_columns]


def split_ocr_boxes_into_relic_columns(
    boxes: Sequence[Any],
    image_width: int = 0,
    max_columns: int = 4,
) -> List[str]:
    """Group RapidOCR text boxes into 1..4 independent horizontal reward strings."""
    return [
        txt
        for txt, _ratio in split_ocr_boxes_into_relic_columns_with_centers(
            boxes,
            image_width=image_width,
            max_columns=max_columns,
        )
    ]


def _http_get_json(url: str, timeout: int = HTTP_TIMEOUT_SEC) -> Any:
    headers = {
        "Accept": "application/json",
        "Platform": "pc",
        "Language": "zh-hans",
        "User-Agent": "WarframeRivenOverlay/2.3",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_vaulted_part_map_from_relics(relics_data: Sequence[Dict[str, Any]]) -> Dict[str, bool]:
    """Compute `{normalized_en_part_key: is_vaulted}` from WFCD `Relics.json`.

    Ground-truth rule:
      - Inspects all `Intact` Void Relics.
      - A relic is currently unvaulted (farmable) iff `not relic.get("vaulted", True)`
        or it has active mission `drops`.
      - A Prime part is `vaulted=False` if AT LEAST ONE unvaulted relic contains it,
        and `vaulted=True` if ALL relics containing it are vaulted.
    """
    part_has_unvaulted: Dict[str, bool] = {}
    for rel in relics_data:
        if not isinstance(rel, dict):
            continue
        name = str(rel.get("name") or "")
        if not name.endswith("Intact"):
            continue
        is_unvaulted_relic = (not bool(rel.get("vaulted", True))) or bool(rel.get("drops"))
        for rw in rel.get("rewards") or []:
            if not isinstance(rw, dict):
                continue
            item_obj = rw.get("item")
            if not isinstance(item_obj, dict):
                continue
            part_name = str(item_obj.get("name") or "").strip()
            if not part_name or _is_forma_text(part_name):
                continue
            nk = _normalize_key(part_name)
            bk = _strip_blueprint_suffix(nk)
            for key in (nk, bk):
                if not key:
                    continue
                if is_unvaulted_relic:
                    part_has_unvaulted[key] = True
                elif key not in part_has_unvaulted:
                    part_has_unvaulted[key] = False
    return {k: (not has_uv) for k, has_uv in part_has_unvaulted.items()}


def fetch_wfcd_relics_vaulted_map(timeout: int = HTTP_TIMEOUT_SEC) -> Dict[str, bool]:
    """Fetch `WFCD/warframe-items` `Relics.json` (CDN + GitHub fallback) and build vaulted map."""
    for url in WFCD_RELICS_URLS:
        try:
            data = _http_get_json(url, timeout=timeout)
            if isinstance(data, list) and data:
                vmap = build_vaulted_part_map_from_relics(data)
                if vmap:
                    return vmap
        except Exception:
            continue
    return {}


def build_prime_price_records(
    v2_items: Sequence[Dict[str, Any]],
    ducats_payload: Dict[str, Any],
    vaulted_part_map: Optional[Dict[str, bool]] = None,
    fallback_vaulted_by_slug: Optional[Dict[str, bool]] = None,
) -> List[PrimeItemPrice]:
    """Join WM v2 items metadata with WM v1 tools/ducats 24h price snapshot & WFCD Vault status."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in v2_items:
        if not isinstance(item, dict):
            continue
        iid = str(item.get("id") or "").strip()
        if iid:
            by_id[iid] = item

    # Prefer 24h `previous_day` snapshot for smoother averages, fallback to `previous_hour`
    rows = ducats_payload.get("previous_day") or ducats_payload.get("previous_hour") or []
    price_by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("item") or "").strip()
        if iid:
            price_by_id[iid] = row

    records: List[PrimeItemPrice] = []
    seen_slugs = set()

    for iid, item in by_id.items():
        tags = set(item.get("tags") or [])
        if "prime" not in tags or "set" in tags:
            continue
        slug = str(item.get("slug") or "").strip()
        if not slug or slug in seen_slugs:
            continue

        i18n = item.get("i18n") or {}
        zh_name = str((i18n.get("zh-hans") or {}).get("name") or "").strip()
        en_name = str((i18n.get("en") or {}).get("name") or "").strip()
        if not zh_name and not en_name:
            continue

        prow = price_by_id.get(iid) or {}
        raw_plat = prow.get("wa_price")
        if not isinstance(raw_plat, (int, float)) or raw_plat <= 0:
            raw_plat = prow.get("median")
        if not isinstance(raw_plat, (int, float)) or raw_plat <= 0:
            raw_plat = prow.get("plat_worth")
        plat_val = round(float(raw_plat), 1) if isinstance(raw_plat, (int, float)) and raw_plat > 0 else 0.0

        raw_ducats = prow.get("ducats")
        if not isinstance(raw_ducats, (int, float)):
            raw_ducats = item.get("ducats", 0)
        ducats_val = int(raw_ducats) if isinstance(raw_ducats, (int, float)) and raw_ducats >= 0 else 0

        vol = prow.get("volume", 0)
        vol_int = int(vol) if isinstance(vol, (int, float)) else 0

        # Only keep relic-relevant Prime parts (ducats > 0 or price > 0)
        if ducats_val <= 0 and plat_val <= 0:
            continue

        is_vaulted = bool(item.get("vaulted", False))
        if vaulted_part_map:
            ek = _normalize_key(en_name)
            ekb = _strip_blueprint_suffix(ek)
            if ek in vaulted_part_map:
                is_vaulted = bool(vaulted_part_map[ek])
            elif ekb in vaulted_part_map:
                is_vaulted = bool(vaulted_part_map[ekb])
        elif fallback_vaulted_by_slug and slug in fallback_vaulted_by_slug:
            is_vaulted = bool(fallback_vaulted_by_slug[slug])

        seen_slugs.add(slug)
        records.append(
            PrimeItemPrice(
                item_id=iid,
                slug=slug,
                zh_name=zh_name or en_name,
                en_name=en_name or zh_name,
                plat_price=plat_val,
                ducats=ducats_val,
                volume=vol_int,
                vaulted=is_vaulted,
            )
        )

    records.sort(key=lambda r: r.slug)
    return records


class PrimePriceStore:
    """In-memory + disk-cached (`data/prime_prices.json`) Prime parts price store."""

    def __init__(self, cache_path: Optional[str | Path] = None) -> None:
        self.cache_path = Path(cache_path) if cache_path else get_prime_prices_path()
        self._lock = threading.RLock()
        self.items: List[PrimeItemPrice] = []
        self.updated_at_iso: str = ""
        self._exact_index: Dict[str, PrimeItemPrice] = {}
        self._base_index: Dict[str, PrimeItemPrice] = {}
        self._all_keys: List[str] = []
        self.reload()

    def _rebuild_indices(self) -> None:
        exact: Dict[str, PrimeItemPrice] = {}
        base: Dict[str, PrimeItemPrice] = {}
        for item in self.items:
            for raw in (item.zh_name, item.en_name):
                nk = _normalize_key(raw)
                if not nk:
                    continue
                exact[nk] = item
                bk = _strip_blueprint_suffix(nk)
                if bk and bk != nk and bk not in base:
                    base[bk] = item
        self._exact_index = exact
        self._base_index = base
        self._all_keys = list(exact.keys())

    def reload(self) -> int:
        """Load cached `data/prime_prices.json` into memory."""
        if not self.cache_path.is_file():
            return 0
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            raw_items = payload.get("items") or []
            parsed: List[PrimeItemPrice] = []
            for entry in raw_items:
                if not isinstance(entry, dict):
                    continue
                parsed.append(
                    PrimeItemPrice(
                        item_id=str(entry.get("item_id") or ""),
                        slug=str(entry.get("slug") or ""),
                        zh_name=str(entry.get("zh_name") or ""),
                        en_name=str(entry.get("en_name") or ""),
                        plat_price=float(entry.get("plat_price") or 0.0),
                        ducats=int(entry.get("ducats") or 0),
                        volume=int(entry.get("volume") or 0),
                        vaulted=bool(entry.get("vaulted", False)),
                    )
                )
            with self._lock:
                self.items = parsed
                self.updated_at_iso = str(payload.get("updated_at_iso") or "")
                self._rebuild_indices()
            return len(parsed)
        except Exception:
            return 0

    def load_records_in_memory(self, records: Sequence[PrimeItemPrice]) -> None:
        """Hydrate store directly from in-memory records (useful for unit tests)."""
        with self._lock:
            self.items = list(records)
            self._rebuild_indices()

    def sync_from_network(self, timeout: int = HTTP_TIMEOUT_SEC) -> Dict[str, Any]:
        """Fetch latest Prime prices & Ducats from WM public JSON endpoints and persist locally."""
        try:
            items_resp = _http_get_json(WM_V2_ITEMS_URL, timeout=timeout)
            v2_items = items_resp.get("data", []) if isinstance(items_resp, dict) else []
            time.sleep(0.25)
            duc_resp = _http_get_json(WM_V1_DUCATS_URL, timeout=timeout)
            duc_payload = duc_resp.get("payload", {}) if isinstance(duc_resp, dict) else {}

            # Fetch ground-truth Vaulted/Unvaulted map from WFCD Relics.json (non-fatal fallback)
            vaulted_map = fetch_wfcd_relics_vaulted_map(timeout=timeout)
            with self._lock:
                prev_vaulted_by_slug = {it.slug: bool(it.vaulted) for it in self.items}

            records = build_prime_price_records(
                v2_items,
                duc_payload,
                vaulted_part_map=vaulted_map or None,
                fallback_vaulted_by_slug=prev_vaulted_by_slug,
            )
            if not records:
                raise RuntimeError("WM returned 0 Prime items")

            now_ts = int(time.time())
            now_iso = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            out_payload = {
                "schema_version": 1,
                "updated_at": now_ts,
                "updated_at_iso": now_iso,
                "sources": [WM_V2_ITEMS_URL, WM_V1_DUCATS_URL, WFCD_RELICS_URLS[0]],
                "item_count": len(records),
                "vaulted_count": sum(1 for r in records if r.vaulted),
                "items": [r.to_dict() for r in records],
            }

            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
            tmp.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.cache_path)

            with self._lock:
                self.items = records
                self.updated_at_iso = now_iso
                self._rebuild_indices()

            return {
                "status": "updated",
                "item_count": len(records),
                "updated_at_iso": now_iso,
                "path": str(self.cache_path),
            }
        except Exception as exc:
            has_cache = len(self.items) > 0 or self.cache_path.is_file()
            if not self.items and self.cache_path.is_file():
                self.reload()
            return {
                "status": "fallback_cache" if has_cache else "failed",
                "item_count": len(self.items),
                "error": str(exc),
            }

    def start_background_sync(
        self,
        on_done: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> threading.Thread:
        """Silently refresh Prime price dictionary on startup in a daemon thread."""

        def _worker() -> None:
            res = self.sync_from_network()
            if on_done is not None:
                try:
                    on_done(res)
                except Exception:
                    pass

        t = threading.Thread(target=_worker, name="PrimePriceSync", daemon=True)
        t.start()
        return t

    def lookup_single(self, raw_text: str) -> RelicRewardMatch:
        """Look up a single reward label in memory (<1ms)."""
        cleaned = (raw_text or "").strip()
        if _is_forma_text(cleaned):
            return RelicRewardMatch(
                raw_text=cleaned,
                matched_zh="Forma 蓝图",
                matched_en="Forma Blueprint",
                plat_price=None,
                ducats=0,
                is_forma=True,
                is_matched=True,
                match_score=1.0,
            )

        nk = _normalize_key(cleaned)
        bk = _strip_blueprint_suffix(nk)

        with self._lock:
            # 1. Exact O(1) match
            hit = self._exact_index.get(nk) or self._exact_index.get(bk) or self._base_index.get(nk) or self._base_index.get(bk)
            if hit is not None:
                return RelicRewardMatch(
                    raw_text=cleaned,
                    matched_zh=hit.zh_name,
                    matched_en=hit.en_name,
                    plat_price=hit.plat_price,
                    ducats=hit.ducats,
                    is_matched=True,
                    is_vaulted=bool(hit.vaulted),
                    match_score=1.0,
                )

            # 2. Fast fuzzy + token-overlap match against normalized keys (handles OCR typos & mixed ZH/EN)
            if nk and self._all_keys:
                close = difflib.get_close_matches(nk, self._all_keys, n=1, cutoff=0.68)
                if not close and bk != nk:
                    close = difflib.get_close_matches(bk, self._all_keys, n=1, cutoff=0.68)
                if close:
                    best_k = close[0]
                    hit = self._exact_index[best_k]
                    score = round(difflib.SequenceMatcher(None, nk, best_k).ratio(), 3)
                    return RelicRewardMatch(
                        raw_text=cleaned,
                        matched_zh=hit.zh_name,
                        matched_en=hit.en_name,
                        plat_price=hit.plat_price,
                        ducats=hit.ducats,
                        is_matched=True,
                        is_vaulted=bool(hit.vaulted),
                        match_score=score,
                    )

                # 3. Substring / token-based fallback for mixed ZH/EN or partial OCR drops
                # e.g. "CalibanPrime蓝图" or "帕里斯Prime下弓臂" with minor noise tokens
                best_item: Optional[PrimeItemPrice] = None
                best_ratio = 0.0
                for key_cand, item_cand in self._exact_index.items():
                    if bk and (bk in key_cand or key_cand in nk):
                        r = difflib.SequenceMatcher(None, nk, key_cand).ratio()
                        if r > best_ratio:
                            best_ratio = r
                            best_item = item_cand
                if best_item is not None and best_ratio >= 0.62:
                    return RelicRewardMatch(
                        raw_text=cleaned,
                        matched_zh=best_item.zh_name,
                        matched_en=best_item.en_name,
                        plat_price=best_item.plat_price,
                        ducats=best_item.ducats,
                        is_matched=True,
                        is_vaulted=bool(best_item.vaulted),
                        match_score=round(best_ratio, 3),
                    )

        return RelicRewardMatch(
            raw_text=cleaned,
            matched_zh=cleaned,
            matched_en=cleaned,
            plat_price=None,
            ducats=0,
            is_matched=False,
            match_score=0.0,
        )

    def _merge_wrapped_reward_lines(self, lines: Sequence[str]) -> List[str]:
        """Merge wrapped 2-line OCR reward labels (e.g. '高斯 Prime' + '头部神经光元 蓝图')."""
        cleaned = [ln.strip() for ln in lines if ln and not _is_noise_line(ln)]
        merged: List[str] = []
        i = 0
        while i < len(cleaned):
            cur = cleaned[i]
            if i + 1 < len(cleaned):
                nxt = cleaned[i + 1]
                cur_low = cur.lower()
                nxt_low = nxt.lower()
                # Merge when current line ends with "prime" (missing part name) and next line is a part suffix
                if (
                    ("prime" in cur_low and not any(s in cur_low for s in _PART_SUFFIX_KEYWORDS))
                    and ("prime" not in nxt_low and any(s in nxt_low for s in _PART_SUFFIX_KEYWORDS))
                ):
                    merged.append(f"{cur} {nxt}")
                    i += 2
                    continue
                # Or when combining current + next yields an exact match in our Prime dictionary!
                combined_nk = _normalize_key(f"{cur} {nxt}")
                with self._lock:
                    if combined_nk in self._exact_index or combined_nk in self._base_index:
                        merged.append(f"{cur} {nxt}")
                        i += 2
                        continue
            merged.append(cur)
            i += 1
        return merged

    @staticmethod
    def _highlight_best_rewards(results: List[RelicRewardMatch]) -> List[RelicRewardMatch]:
        """Mark highest-platinum item(s), highest-ducat item(s), and Rank 1 (Gold) / Rank 2 (Silver)."""
        for r in results:
            r.is_best_plat = False
            r.is_best_ducats = False
            r.recommend_rank = 0

        valid_prices = [r.plat_price for r in results if r.plat_price is not None and r.plat_price > 0]
        if valid_prices:
            max_p = max(valid_prices)
            for r in results:
                if r.plat_price is not None and r.plat_price >= max_p:
                    r.is_best_plat = True

        valid_ducats = [r.ducats for r in results if r.ducats > 0]
        if valid_ducats:
            max_d = max(valid_ducats)
            for r in results:
                if r.ducats >= max_d:
                    r.is_best_ducats = True

        # Assign Rank 1 (Gold: highest Platinum, tie-break Ducats) and Rank 2 (Silver: 2nd highest Plat / high Ducat alt)
        valued_indices = [
            idx
            for idx, r in enumerate(results)
            if not r.is_forma and ((r.plat_price is not None and r.plat_price > 0) or r.ducats > 0)
        ]
        if valued_indices:
            ranked = sorted(
                valued_indices,
                key=lambda i: (
                    float(results[i].plat_price or 0.0),
                    int(results[i].ducats or 0),
                    -i,
                ),
                reverse=True,
            )
            rank1_idx = ranked[0]
            results[rank1_idx].recommend_rank = 1
            if len(ranked) >= 2:
                # 2nd recommendation: 2nd highest Platinum, or highest Ducat alternative if plats are tied/absent
                remaining = ranked[1:]
                rank2_idx = remaining[0]
                if not any((results[i].plat_price or 0.0) > 0 for i in remaining):
                    rank2_idx = max(remaining, key=lambda i: (int(results[i].ducats or 0), -i))
                results[rank2_idx].recommend_rank = 2

        return results

    def match_ocr_boxes(
        self,
        boxes: Sequence[Any],
        image_width: int = 0,
        max_rewards: int = 4,
    ) -> List[RelicRewardMatch]:
        """Split RapidOCR bounding boxes horizontally into 1..4 dynamic columns & look up each reward."""
        column_entries = split_ocr_boxes_into_relic_columns_with_centers(
            boxes,
            image_width=image_width,
            max_columns=max_rewards,
        )
        all_results: List[RelicRewardMatch] = []
        for col_txt, center_ratio in column_entries[:max_rewards]:
            m = self.lookup_single(col_txt)
            m.column_center_x_ratio = center_ratio
            all_results.append(m)

        # If at least one real Prime/Forma reward matched, drop stray non-reward UI noise columns
        # so 1/2/3-relic squads show strictly the true 1/2/3 rewards!
        matched_only = [
            m for m in all_results if m.is_matched or m.is_forma or ("prime" in m.raw_text.lower())
        ]
        results = matched_only if matched_only else all_results
        return self._highlight_best_rewards(results)

    def match_ocr_lines(
        self,
        lines: Sequence[str],
        max_rewards: int = 4,
    ) -> List[RelicRewardMatch]:
        """Match 1~4 Void Relic reward OCR lines (splitting any glued Prime lines) & highlight best items."""
        expanded: List[str] = []
        for ln in lines:
            for seg in _split_glued_prime_string(ln):
                if seg and not _is_noise_line(seg):
                    expanded.append(seg)
        candidates = self._merge_wrapped_reward_lines(expanded)
        results: List[RelicRewardMatch] = []
        for cand in candidates:
            m = self.lookup_single(cand)
            if m.is_matched or ("prime" in cand.lower()):
                results.append(m)
            if len(results) >= max_rewards:
                break

        # If nothing matched Prime/Forma cleanly, still return raw non-noise candidates up to 4
        if not results:
            for cand in candidates[:max_rewards]:
                results.append(self.lookup_single(cand))

        return self._highlight_best_rewards(results)


# Shared singleton store
PRIME_PRICE_STORE = PrimePriceStore()
