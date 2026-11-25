"""Warframe Riven Mod numerical evaluator & AlecaFrame-style grading engine.

Computes:
  1. Theoretical min/mid/max stat ranges using Warframe's official Riven math:
       stat_mid = base_max_rank_value * combo_multiplier * disposition
       stat_min = stat_mid * 0.90
       stat_max = stat_mid * 1.10
  2. Roll percentage (0%..100%) & AlecaFrame-style delta (-10%..+10%) per stat,
     accounting for reversed optimality on negative (curse) stats and multiplicative
     faction damage (`x1.5` / `x0.63`).
  3. Per-stat and overall `S / A / B / C` letter grades.
  4. Chinese synergy & archetype tags (e.g., 双暴, 双暴多重, 基伤多重, 无害负, 伤筋负).
"""

from __future__ import annotations

import difflib
import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.data_updater import get_data_dir
from src.riven_parser import (
    POSITIVE_ONLY_COMPOUND_ELEMENTS,
    WEAPON_COMMUNITY_ALIASES,
    RivenData,
    RivenParser,
    RivenStat,
    normalize_weapon_alias,
    reload_custom_stats,
)

DATA_DIR = get_data_dir()
DEFAULT_WEAPONS_RIVEN_PATH = DATA_DIR / "weapons_riven.json"
DEFAULT_ATTRIBUTES_PATH = DATA_DIR / "riven_attributes.json"
DEFAULT_CUSTOM_STATS_PATH = DATA_DIR / "custom_riven_stats.json"
DEFAULT_TIERS_PATH = DATA_DIR / "recommended_tiers.json"
LEGACY_WEAPONS_DB_PATH = DATA_DIR / "weapons.json"
FIXED_GLOBAL_DISPOSITION = 1.20
DEFAULT_DISPOSITION = FIXED_GLOBAL_DISPOSITION
HYBRID_MISSING_RANGE_NOTICE_ZH = "新融合词条（暂缺官方基础值区间）"

RECOMMENDATION_SOURCE_CN: Dict[str, str] = {
    "builtin": "内置推荐",
    "wm": "WM 校准",
    "inferred": "面板推导",
    "special_rule": "灵化特化",
}

# Max-rank (Rank 8 = 9x Rank 0) Riven stat baselines at Disposition = 1.0
# Verified to 0.1% against Warframe official tables (`3+1`, `3+0`, `2+1`, `2+0` at Dispo 1.20).
BASE_RIVEN_STATS: Dict[str, Dict[str, float]] = {
    "rifle": {
        "伤害": 165.0,
        "暴击几率": 150.0,
        "暴击伤害": 120.0,
        "多重射击": 90.0,
        "射速": 60.03,
        "火焰伤害": 90.0,
        "冰冻伤害": 90.0,
        "电击伤害": 90.0,
        "毒素伤害": 90.0,
        "病毒伤害": 90.0,
        "腐蚀伤害": 90.0,
        "辐射伤害": 90.0,
        "磁力伤害": 90.0,
        "毒气伤害": 90.0,
        "爆炸伤害": 90.0,
        "冲击伤害": 120.0,
        "穿刺伤害": 120.0,
        "切割伤害": 120.0,
        "触发几率": 90.0,
        "触发时间": 99.99,
        "异常状态伤害": 90.0,
        "装填速度": 50.0,
        "弹匣容量": 50.0,
        "弹药最大值": 49.95,
        "弹药效率": 45.0,
        "收起武器时弹匣每秒自动装填": 90.0,
        "投射物速度": 90.0,
        "穿透": 2.7,
        "变焦": 60.0,
        "武器后坐力": 90.0,
        "弱点伤害": 225.0,
        "弱点暴击几率": 247.5,
        "对 Grineer 伤害": 45.0,
        "对 Corpus 伤害": 45.0,
        "对 Infested 伤害": 45.0,
        "对奥罗金伤害": 45.0,
        "对炽蛇军伤害": 45.0,
        "对科腐者伤害": 45.0,
    },
    "shotgun": {
        "伤害": 164.7,
        "暴击几率": 90.0,
        "暴击伤害": 90.0,
        "多重射击": 119.7,
        "射速": 90.0,
        "火焰伤害": 90.0,
        "冰冻伤害": 90.0,
        "电击伤害": 90.0,
        "毒素伤害": 90.0,
        "病毒伤害": 90.0,
        "腐蚀伤害": 90.0,
        "辐射伤害": 90.0,
        "磁力伤害": 90.0,
        "毒气伤害": 90.0,
        "爆炸伤害": 90.0,
        "冲击伤害": 120.0,
        "穿刺伤害": 120.0,
        "切割伤害": 120.0,
        "触发几率": 90.0,
        "触发时间": 99.99,
        "异常状态伤害": 90.0,
        "装填速度": 50.0,
        "弹匣容量": 50.0,
        "弹药最大值": 90.0,
        "弹药效率": 45.0,
        "收起武器时弹匣每秒自动装填": 90.0,
        "投射物速度": 90.0,
        "穿透": 2.7,
        "变焦": 42.0,
        "武器后坐力": 90.0,
        "弱点伤害": 225.0,
        "弱点暴击几率": 247.5,
        "对 Grineer 伤害": 45.0,
        "对 Corpus 伤害": 45.0,
        "对 Infested 伤害": 45.0,
        "对奥罗金伤害": 45.0,
        "对炽蛇军伤害": 45.0,
        "对科腐者伤害": 45.0,
    },
    "pistol": {
        "伤害": 219.6,
        "暴击几率": 150.0,
        "暴击伤害": 90.0,
        "多重射击": 119.7,
        "射速": 74.7,
        "火焰伤害": 90.0,
        "冰冻伤害": 90.0,
        "电击伤害": 90.0,
        "毒素伤害": 90.0,
        "病毒伤害": 90.0,
        "腐蚀伤害": 90.0,
        "辐射伤害": 90.0,
        "磁力伤害": 90.0,
        "毒气伤害": 90.0,
        "爆炸伤害": 90.0,
        "冲击伤害": 120.0,
        "穿刺伤害": 120.0,
        "切割伤害": 120.0,
        "触发几率": 90.0,
        "触发时间": 99.99,
        "异常状态伤害": 90.0,
        "装填速度": 50.0,
        "弹匣容量": 50.0,
        "弹药最大值": 90.0,
        "弹药效率": 45.0,
        "收起武器时弹匣每秒自动装填": 90.0,
        "投射物速度": 90.0,
        "穿透": 2.7,
        "变焦": 80.1,
        "武器后坐力": 90.0,
        "弱点伤害": 225.0,
        "弱点暴击几率": 247.5,
        "对 Grineer 伤害": 45.0,
        "对 Corpus 伤害": 45.0,
        "对 Infested 伤害": 45.0,
        "对奥罗金伤害": 45.0,
        "对炽蛇军伤害": 45.0,
        "对科腐者伤害": 45.0,
    },
    "melee": {
        "伤害": 164.7,
        "暴击几率": 180.0,
        "暴击伤害": 90.0,
        "攻击速度": 54.9,
        "火焰伤害": 90.0,
        "冰冻伤害": 90.0,
        "电击伤害": 90.0,
        "毒素伤害": 90.0,
        "病毒伤害": 90.0,
        "腐蚀伤害": 90.0,
        "辐射伤害": 90.0,
        "磁力伤害": 90.0,
        "毒气伤害": 90.0,
        "爆炸伤害": 90.0,
        "冲击伤害": 119.7,
        "穿刺伤害": 119.7,
        "切割伤害": 119.7,
        "触发几率": 90.0,
        "触发时间": 99.99,
        "异常状态伤害": 90.0,
        "攻击范围": 1.94,
        "连击持续时间": 8.1,
        "初始连击": 24.48,
        "额外连击几率": 58.77,
        "重击效率": 73.44,
        "重击准备速度": 119.7,
        "在重击时的近战伤害": 119.7,
        "震地攻击伤害": 119.7,
        "招架角度": 81.0,
        "处决伤害": 119.7,
        "滑行攻击暴击几率": 120.0,
        "对 Grineer 伤害": 45.0,
        "对 Corpus 伤害": 45.0,
        "对 Infested 伤害": 45.0,
        "对奥罗金伤害": 45.0,
        "对炽蛇军伤害": 45.0,
        "对科腐者伤害": 45.0,
    },
    "archgun": {
        "伤害": 99.9,
        "暴击几率": 99.9,
        "暴击伤害": 80.1,
        "多重射击": 60.3,
        "射速": 60.03,
        "火焰伤害": 119.7,
        "冰冻伤害": 119.7,
        "电击伤害": 119.7,
        "毒素伤害": 119.7,
        "病毒伤害": 90.0,
        "腐蚀伤害": 90.0,
        "辐射伤害": 90.0,
        "磁力伤害": 90.0,
        "毒气伤害": 90.0,
        "爆炸伤害": 90.0,
        "冲击伤害": 90.0,
        "穿刺伤害": 90.0,
        "切割伤害": 90.0,
        "触发几率": 60.3,
        "触发时间": 99.99,
        "异常状态伤害": 90.0,
        "装填速度": 99.9,
        "弹匣容量": 60.3,
        "弹药最大值": 99.9,
        "弹药效率": 45.0,
        "穿透": 2.7,
        "变焦": 60.0,
        "武器后坐力": 90.0,
        "弱点伤害": 225.0,
        "弱点暴击几率": 247.5,
        "对 Grineer 伤害": 45.0,
        "对 Corpus 伤害": 45.0,
        "对 Infested 伤害": 45.0,
        "对奥罗金伤害": 45.0,
        "对炽蛇军伤害": 45.0,
        "对科腐者伤害": 45.0,
    },
}

# Combination multipliers: (num_positive, num_negative) -> (pos_mult, neg_mult)
COMBO_MULTIPLIERS: Dict[Tuple[int, int], Tuple[float, float]] = {
    (2, 0): (0.99, 0.0),
    (2, 1): (1.2375, 0.495),
    (3, 0): (0.75, 0.0),
    (3, 1): (0.9375, 0.75),
}

WEAPON_CLASS_CN: Dict[str, str] = {
    "rifle": "步枪",
    "shotgun": "霰弹枪",
    "pistol": "手枪",
    "melee": "近战",
    "archgun": "空战枪械",
}

FACTION_STATS = {
    "对 Grineer 伤害",
    "对 Corpus 伤害",
    "对 Infested 伤害",
    "对堕落者伤害",
    "对低语者伤害",
}

ELEMENTAL_STATS = {"火焰伤害", "冰冻伤害", "电击伤害", "毒素伤害"}

# Negative curses that have little to no impact in typical builds
HARMLESS_NEGATIVES = {
    "变焦",
    "冲击伤害",
    "穿刺伤害",
    "对 Infested 伤害",
    "滑行攻击暴击几率",
    "处决伤害",
    "额外连击几率",
    "弹药最大值",
    "武器后坐力",
    "招架角度",
}

# Negative curses that severely hurt DPS / usability
HARMFUL_NEGATIVES = {
    "伤害",
    "多重射击",
    "暴击几率",
    "暴击伤害",
    "射速",
    "攻击速度",
    "攻击范围",
    "触发几率",
    "异常状态伤害",
    "对 Grineer 伤害",
}

MELEE_ONLY_STATS = {
    "攻击速度",
    "攻击范围",
    "连击持续时间",
    "初始连击",
    "额外连击几率",
    "重击效率",
    "重击准备速度",
    "在重击时的近战伤害",
    "震地攻击伤害",
    "招架角度",
    "处决伤害",
    "滑行攻击暴击几率",
}


GRADE_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "?": 0}


@dataclass
class DualComparisonResult:
    """Structured comparison between left ([当前]) and right ([新循环]) Rivens."""

    winner: str  # "left" | "right" | "tie"
    left_score: float
    right_score: float
    roll_pct_delta: Optional[float]  # right - left
    headline: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _compute_comparison_score(ev: RivenEvaluation) -> float:
    """Compute composite strength score for comparing two rolls of the same weapon."""
    base_roll = ev.overall_roll_pct if ev.overall_roll_pct is not None else 50.0
    grade_pts = GRADE_RANK.get(ev.overall_grade, 0) * 14.0
    tag_pts = 0.0
    tag_weights = {
        "SSS 反转神负": 28.0,
        "重击神配": 22.0,
        "双暴多重": 26.0,
        "双暴基伤": 18.0,
        "双暴攻速/范围": 22.0,
        "双暴攻速": 18.0,
        "双暴范围": 18.0,
        "双暴": 14.0,
        "基伤多重": 14.0,
        "多重配暴": 13.0,
        "攻速范围": 11.0,
        "直出病毒": 8.0,
        "直出腐蚀": 7.0,
        "无害负": 12.0,
        "伤筋负": -20.0,
    }
    for t in ev.tags:
        tag_pts += tag_weights.get(t, 0.0)
    return round(base_roll * 0.75 + grade_pts + tag_pts, 1)


def compare_riven_evaluations(
    left_eval: RivenEvaluation,
    right_eval: RivenEvaluation,
) -> DualComparisonResult:
    """Compare [当前] (left) vs [新循环] (right) and highlight the stronger roll."""
    l_score = _compute_comparison_score(left_eval)
    r_score = _compute_comparison_score(right_eval)

    roll_delta: Optional[float] = None
    if left_eval.overall_roll_pct is not None and right_eval.overall_roll_pct is not None:
        roll_delta = round(right_eval.overall_roll_pct - left_eval.overall_roll_pct, 1)

    diff = r_score - l_score
    if diff >= 4.0:
        winner = "right"
        headline = "★ 推荐选择右侧 [新循环]"
    elif diff <= -4.0:
        winner = "left"
        headline = "★ 建议保留左侧 [当前]"
    else:
        winner = "tie"
        headline = "≈ 左右强度相近，可按手感选择"

    parts: List[str] = [f"评级 {left_eval.overall_grade} → {right_eval.overall_grade}"]
    if roll_delta is not None:
        parts.append(f"均分 {roll_delta:+.1f}%")

    if winner == "right":
        highlights = [t for t in right_eval.tags if t not in ("三正一负", "二正一负", "无负词条", "伤筋负")]
        if highlights:
            parts.append(f"右侧优势: {' / '.join(highlights[:3])}")
    elif winner == "left":
        highlights = [t for t in left_eval.tags if t not in ("三正一负", "二正一负", "无负词条", "伤筋负")]
        if "伤筋负" in right_eval.tags and "伤筋负" not in left_eval.tags:
            parts.append("右侧含伤筋负")
        elif highlights:
            parts.append(f"左侧优势: {' / '.join(highlights[:3])}")

    return DualComparisonResult(
        winner=winner,
        left_score=l_score,
        right_score=r_score,
        roll_pct_delta=roll_delta,
        headline=headline,
        detail=" · ".join(parts),
    )


@dataclass(frozen=True)
class StatFingerprint:
    stat_name: str
    sign: str
    value: float
    is_negative: bool
    is_locked: bool
    grade: str


@dataclass(frozen=True)
class CardFingerprint:
    weapon_name: str
    reroll_count: Optional[int]
    overall_grade: str
    stats: Tuple[StatFingerprint, ...]


@dataclass(frozen=True)
class ResultFingerprint:
    """Structured fingerprint of a complete overlay result for flicker-free deduplication."""

    mode: str  # "single" | "dual"
    cards: Tuple[CardFingerprint, ...]


CardDataSignature = Tuple[str, Optional[int], Tuple[Tuple[str, float], ...]]
RerollDataSignature = Tuple[CardDataSignature, CardDataSignature]


def _canonical_weapon_id(riven: RivenData, evaluation: Optional[RivenEvaluation] = None) -> str:
    """Return canonical weapon identifier resilient to OCR whitespace / punctuation noise."""
    if evaluation is not None:
        slug = (evaluation.weapon_slug or "").strip().lower()
        if slug:
            return slug
        ev_name = (evaluation.weapon_name or "").strip()
        if ev_name:
            return "".join(ev_name.replace("·", "").replace("•", "").split()).lower()
    raw_name = (riven.weapon_name or "").strip()
    if raw_name:
        return "".join(raw_name.replace("·", "").replace("•", "").split()).lower()
    return ""


def _extract_riven_and_eval(item: Any) -> Tuple[RivenData, Optional[RivenEvaluation]]:
    """Unpack either `(RivenData, RivenEvaluation)` or an object with `.riven` & `.evaluation`."""
    if isinstance(item, tuple) and len(item) == 2:
        return item[0], item[1]
    return item.riven, getattr(item, "evaluation", None)


def is_complete_riven_frame(
    riven: RivenData,
    evaluation: Optional[RivenEvaluation] = None,
    min_stats: int = 2,
) -> bool:
    """Return True only when a parsed Riven card is complete (not a mid-animation transition frame).

    Requires:
      - Valid non-empty canonical weapon name / ID
      - `riven.is_valid` is True
      - At least `min_stats` (default 2) valid recognized stats with non-empty canonical stat names
    """
    if riven is None or not riven.is_valid:
        return False
    weapon_id = _canonical_weapon_id(riven, evaluation)
    if not weapon_id:
        return False
    valid_stats = [
        s
        for s in riven.stats
        if s is not None and bool((s.stat_name or "").strip()) and isinstance(s.value, (int, float))
    ]
    return len(valid_stats) >= max(1, int(min_stats))


def build_card_data_signature(
    riven: RivenData,
    evaluation: Optional[RivenEvaluation] = None,
) -> CardDataSignature:
    """Build pure structured data fingerprint for one Riven card:

        (canonical_weapon_id, reroll_count, tuple(sorted((canonical_stat_key, round(value, 1)))))

    Completely insensitive to raw OCR spacing/punctuation noise, line ordering,
    suffix OCR noise, mastery rank flicker, or OCR confidence fluctuations.
    """
    weapon_id = _canonical_weapon_id(riven, evaluation)
    reroll_count = int(riven.reroll_count) if riven.reroll_count is not None else None

    stat_pairs: List[Tuple[str, float]] = []
    if evaluation is not None and evaluation.stat_evaluations:
        for se in evaluation.stat_evaluations:
            key = (se.stat_key or se.stat_name or "").strip()
            if not key:
                continue
            signed_val = -abs(float(se.value)) if se.is_negative else abs(float(se.value))
            stat_pairs.append((key, round(signed_val, 1)))
    else:
        for st in riven.stats:
            key = (st.stat_key or st.stat_name or "").strip()
            if not key:
                continue
            signed_val = -abs(float(st.value)) if st.is_negative else abs(float(st.value))
            stat_pairs.append((key, round(signed_val, 1)))

    return (weapon_id, reroll_count, tuple(sorted(stat_pairs)))


def build_reroll_data_signature(left_item: Any, right_item: Any) -> RerollDataSignature:
    """Build combined structured data fingerprint `(left_card_sig, right_card_sig)` for Kuva Reroll mode."""
    left_riven, left_eval = _extract_riven_and_eval(left_item)
    right_riven, right_eval = _extract_riven_and_eval(right_item)
    return (
        build_card_data_signature(left_riven, left_eval),
        build_card_data_signature(right_riven, right_eval),
    )


def cards_stat_numbers_equivalent(
    sig_a: Optional[CardDataSignature],
    sig_b: Optional[CardDataSignature],
    *,
    abs_tol: float = 1.2,
) -> bool:
    """Return True if two cards have the same stat keys & signs with numbers within `abs_tol`.

    Compares primarily by stat numbers (`以词条数字为准`) so minor OCR decimal jitter
    (e.g. `+196.4%` vs `+196.9%`), weapon-name OCR flicker, or reroll-count noise
    never causes unnecessary overlay refreshes.
    """
    if sig_a is None or sig_b is None:
        return False
    stats_a = sig_a[2]
    stats_b = sig_b[2]
    if len(stats_a) != len(stats_b) or not stats_a:
        return False
    for (k_a, v_a), (k_b, v_b) in zip(stats_a, stats_b):
        if k_a != k_b:
            return False
        if (v_a < 0) != (v_b < 0):
            return False
        if abs(float(v_a) - float(v_b)) > abs_tol:
            return False
    return True


def reroll_stat_numbers_equivalent(
    sig_a: Optional[RerollDataSignature],
    sig_b: Optional[RerollDataSignature],
    *,
    abs_tol: float = 1.2,
) -> bool:
    """Return True if both left and right reroll cards are numerically equivalent within `abs_tol`."""
    if sig_a is None or sig_b is None:
        return False
    return (
        cards_stat_numbers_equivalent(sig_a[0], sig_b[0], abs_tol=abs_tol)
        and cards_stat_numbers_equivalent(sig_a[1], sig_b[1], abs_tol=abs_tol)
    )


def build_result_fingerprint(
    mode: str,
    items: Sequence[Tuple[RivenData, RivenEvaluation]],
) -> ResultFingerprint:
    """Create a canonical structured fingerprint from parsed & evaluated cards."""
    card_fps: List[CardFingerprint] = []
    for riven, ev in items:
        weapon = _canonical_weapon_id(riven, ev) or (ev.weapon_name or riven.weapon_name or "").strip()
        stat_fps = tuple(
            sorted(
                (
                    StatFingerprint(
                        stat_name=(se.stat_key or se.stat_name or "").strip(),
                        sign=se.sign,
                        value=round(float(se.value), 1),
                        is_negative=se.is_negative,
                        is_locked=se.is_locked,
                        grade=se.grade,
                    )
                    for se in ev.stat_evaluations
                ),
                key=lambda x: (x.stat_name, x.is_negative, x.value),
            )
        )
        card_fps.append(
            CardFingerprint(
                weapon_name=weapon,
                reroll_count=riven.reroll_count,
                overall_grade=ev.overall_grade,
                stats=stat_fps,
            )
        )
    return ResultFingerprint(mode=mode, cards=tuple(card_fps))


def fingerprints_equivalent(
    fp_a: Optional[ResultFingerprint],
    fp_b: Optional[ResultFingerprint],
    value_tolerance: float = 0.15,
) -> bool:
    """Return True if two result fingerprints represent the same Riven display state.

    Tolerates negligible OCR decimal jitter (<= 0.15) or transient missing reroll
    count on one frame when weapon name, stat names, signs, and grades match.
    """
    if fp_a is None or fp_b is None:
        return False
    if fp_a.mode != fp_b.mode or len(fp_a.cards) != len(fp_b.cards):
        return False

    for ca, cb in zip(fp_a.cards, fp_b.cards):
        if ca.weapon_name != cb.weapon_name:
            return False
        if ca.overall_grade != cb.overall_grade:
            return False
        # Reroll count: if both positive and different, it's a different roll;
        # tolerate transient OCR drop (0 or None) when weapon + all stats match.
        if (
            ca.reroll_count is not None
            and cb.reroll_count is not None
            and ca.reroll_count > 0
            and cb.reroll_count > 0
            and ca.reroll_count != cb.reroll_count
        ):
            return False
        if len(ca.stats) != len(cb.stats):
            return False
        for sa, sb in zip(ca.stats, cb.stats):
            if (
                sa.stat_name != sb.stat_name
                or sa.sign != sb.sign
                or sa.is_negative != sb.is_negative
                or abs(sa.value - sb.value) > value_tolerance
            ):
                return False

    return True


def grade_from_roll_pct(roll_pct: float) -> str:
    """Convert normalized roll percentage (0..100%) to AlecaFrame-style S/A/B/C grade."""
    if roll_pct >= 80.0:
        return "S"
    if roll_pct >= 55.0:
        return "A"
    if roll_pct >= 30.0:
        return "B"
    return "C"


@dataclass
class StatEvaluation:
    """Numerical roll evaluation for a single Riven stat."""

    stat_name: str
    value: float
    sign: str
    unit: str
    is_negative: bool
    is_locked: bool = False
    is_recommended: bool = False
    is_inverted_god_neg: bool = False
    stat_key: str = ""
    min_value: Optional[float] = None
    mid_value: Optional[float] = None
    max_value: Optional[float] = None
    roll_pct: Optional[float] = None      # 0..100% within theoretical [min, max]
    delta_pct: Optional[float] = None     # -10%..+10% offset vs theoretical midpoint
    grade: str = "?"
    range_display: str = ""
    is_hybrid: bool = False
    missing_base_range: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RivenEvaluation:
    """Complete evaluation report for a parsed Riven Mod (unified 1.20 disposition)."""

    weapon_name: Optional[str] = None
    weapon_name_en: str = ""
    weapon_slug: str = ""
    weapon_class: str = "rifle"
    weapon_class_cn: str = "步枪"
    disposition: float = FIXED_GLOBAL_DISPOSITION
    disposition_source: str = "fixed_1.2"
    combo_pattern: str = ""
    overall_roll_pct: Optional[float] = None
    overall_grade: str = "?"
    stat_evaluations: List[StatEvaluation] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    summary_key: str = "average_roll"
    recommended_positives: List[str] = field(default_factory=list)
    recommended_negatives: List[str] = field(default_factory=list)
    recommendation_source: str = "inferred"  # "builtin" | "wm" | "inferred" | "special_rule"
    recommendation_source_cn: str = "面板推导"
    matched_rec_pos: int = 0
    matched_rec_neg: bool = False
    cached_wm_price: Optional[str] = None
    matched_archetype_id: str = ""
    matched_archetype_name: str = ""
    matched_archetype_name_en: str = ""
    archetype_warning: str = ""
    archetype_warning_en: str = ""
    has_inverted_god_neg: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weapon_name": self.weapon_name,
            "weapon_name_en": self.weapon_name_en,
            "weapon_slug": self.weapon_slug,
            "weapon_class": self.weapon_class,
            "weapon_class_cn": self.weapon_class_cn,
            "disposition": round(self.disposition, 2),
            "disposition_source": self.disposition_source,
            "combo_pattern": self.combo_pattern,
            "overall_roll_pct": round(self.overall_roll_pct, 1)
            if self.overall_roll_pct is not None
            else None,
            "overall_grade": self.overall_grade,
            "tags": self.tags,
            "summary": self.summary,
            "summary_key": self.summary_key,
            "recommended_positives": self.recommended_positives,
            "recommended_negatives": self.recommended_negatives,
            "recommendation_source": self.recommendation_source,
            "recommendation_source_cn": self.recommendation_source_cn,
            "matched_rec_pos": self.matched_rec_pos,
            "matched_rec_neg": self.matched_rec_neg,
            "cached_wm_price": self.cached_wm_price,
            "matched_archetype_id": self.matched_archetype_id,
            "matched_archetype_name": self.matched_archetype_name,
            "matched_archetype_name_en": self.matched_archetype_name_en,
            "archetype_warning": self.archetype_warning,
            "archetype_warning_en": self.archetype_warning_en,
            "has_inverted_god_neg": self.has_inverted_god_neg,
            "stat_evaluations": [se.to_dict() for se in self.stat_evaluations],
        }


class RivenEvaluator:
    """Evaluates Riven roll percentages, S/A/B/C grades, local recommendations, and tags."""

    def __init__(
        self,
        weapons_db_path: Optional[str | Path] = None,
        attributes_db_path: Optional[str | Path] = None,
        tiers_db_path: Optional[str | Path] = None,
        custom_stats_path: Optional[str | Path] = None,
    ) -> None:
        self.db_path = Path(weapons_db_path) if weapons_db_path else DEFAULT_WEAPONS_RIVEN_PATH
        self.attributes_path = Path(attributes_db_path) if attributes_db_path else DEFAULT_ATTRIBUTES_PATH
        self.tiers_path = Path(tiers_db_path) if tiers_db_path else DEFAULT_TIERS_PATH
        self.custom_stats_path = Path(custom_stats_path) if custom_stats_path else DEFAULT_CUSTOM_STATS_PATH
        self.weapons: Dict[str, Dict[str, Any]] = {}
        self.weapons_by_en: Dict[str, str] = {}  # lowercase English name -> canonical zh weapon key
        self.ocr_aliases: Dict[str, str] = {}
        self.base_stats: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in BASE_RIVEN_STATS.items()}
        self.combo_multipliers: Dict[Tuple[int, int], Tuple[float, float]] = dict(COMBO_MULTIPLIERS)
        self.custom_stats_meta: Dict[str, Dict[str, Any]] = {}
        self.hybrid_pairs_meta: Dict[str, Dict[str, Any]] = {}
        self.default_hybrid_scale: float = 0.6
        self.recommended_tiers: Dict[str, Dict[str, Any]] = {}
        self.special_rules: Dict[str, Dict[str, Any]] = {}
        self.special_rules_by_alias: Dict[str, str] = {}
        self.auto_save_wm_to_local: bool = True
        self.reload_data()

    def reload_recommendations(self) -> None:
        """Hot-reload strictly local `data/recommended_tiers.json` (zero network requests)."""
        self.recommended_tiers = {}
        self.special_rules = {}
        self.special_rules_by_alias = {}
        self.auto_save_wm_to_local = True
        if not self.tiers_path.is_file():
            return
        try:
            tdata = json.loads(self.tiers_path.read_text(encoding="utf-8"))
            self.auto_save_wm_to_local = bool(tdata.get("auto_save_wm_to_local", True))
            self.recommended_tiers = dict(tdata.get("weapons", {}))
            raw_special = tdata.get("special_rules", {})
            if isinstance(raw_special, dict):
                self.special_rules = dict(raw_special)
                for canon_key, rule in self.special_rules.items():
                    if not isinstance(rule, dict):
                        continue
                    self.special_rules_by_alias[canon_key.lower()] = canon_key
                    self.special_rules_by_alias[canon_key.replace(" ", "").replace("·", "").lower()] = canon_key
                    en_n = (rule.get("en_name") or "").strip().lower()
                    if en_n:
                        self.special_rules_by_alias[en_n] = canon_key
                    for al in rule.get("aliases", []):
                        al_s = str(al).strip().lower()
                        if al_s:
                            self.special_rules_by_alias[al_s] = canon_key
                            self.special_rules_by_alias[al_s.replace(" ", "").replace("·", "")] = canon_key
        except Exception:
            pass

    def reload_data(self) -> None:
        """Load or hot-reload official cached JSON tables (`weapons_riven.json`, `riven_attributes.json`, `recommended_tiers.json`)."""
        self.weapons = {}
        self.weapons_by_en = {}
        self.ocr_aliases = {}
        self.base_stats = {k: dict(v) for k, v in BASE_RIVEN_STATS.items()}
        self.combo_multipliers = dict(COMBO_MULTIPLIERS)

        # 1. Load weapons cache (prefer synced `weapons_riven.json`, fallback to legacy `weapons.json`)
        target_weapons_path = self.db_path if self.db_path.is_file() else LEGACY_WEAPONS_DB_PATH
        if target_weapons_path.is_file():
            try:
                data = json.loads(target_weapons_path.read_text(encoding="utf-8"))
                raw_weapons = data.get("weapons", {})
                for name, entry in raw_weapons.items():
                    w_class = entry.get("riven_class") or entry.get("class") or "rifle"
                    en_raw = (entry.get("en_name") or "").strip()
                    self.weapons[name] = {
                        "class": w_class,
                        "disposition": FIXED_GLOBAL_DISPOSITION,
                        "en_name": en_raw,
                        "slug": entry.get("slug", ""),
                        "wfm_disposition": entry.get("wfm_disposition"),
                        "crit_chance": entry.get("crit_chance"),
                        "crit_mult": entry.get("crit_mult"),
                        "status_chance": entry.get("status_chance"),
                    }
                    en_name = en_raw.lower()
                    if en_name and en_name not in self.weapons_by_en:
                        self.weapons_by_en[en_name] = name
                self.ocr_aliases = dict(data.get("aliases") or data.get("ocr_aliases") or {})
            except Exception:
                pass

        # 2. Load attributes cache (`riven_attributes.json`) to hydrate base values & combo multipliers
        if self.attributes_path.is_file():
            try:
                attr_data = json.loads(self.attributes_path.read_text(encoding="utf-8"))
                raw_combos = attr_data.get("combo_multipliers", {})
                for key, pair in raw_combos.items():
                    if "+" in key and key.endswith("-"):
                        p_str, n_str = key[:-1].split("+", 1)
                        self.combo_multipliers[(int(p_str), int(n_str))] = (
                            float(pair.get("positive", 1.0)),
                            abs(float(pair.get("negative", 0.0))),
                        )
                for _slug, info in attr_data.get("attributes", {}).items():
                    zh_name = info.get("zh_name", "")
                    base_vals = info.get("base_values", {})
                    if not zh_name or not isinstance(base_vals, dict):
                        continue
                    for w_cls, val in base_vals.items():
                        if w_cls not in self.base_stats:
                            self.base_stats[w_cls] = {}
                        if zh_name == "射速/攻击速度":
                            stat_key = "攻击速度" if w_cls == "melee" else "射速"
                            self.base_stats[w_cls][stat_key] = float(val)
                        else:
                            self.base_stats[w_cls][zh_name] = float(val)
            except Exception:
                pass

        # 3. Priority merge local supplemental custom/hybrid stats (`data/custom_riven_stats.json`)
        # Never overwritten by remote DB sync; merges on top of main attribute database.
        self.custom_stats_meta = {}
        self.hybrid_pairs_meta = {}
        self.default_hybrid_scale = 0.6
        custom_data = reload_custom_stats(self.custom_stats_path)
        if custom_data:
            try:
                sf = float(custom_data.get("default_hybrid_scale_factor", 0.6))
                if 0.2 <= sf <= 1.2:
                    self.default_hybrid_scale = sf
            except (TypeError, ValueError):
                pass
            raw_custom = custom_data.get("custom_stats") or {}
            if isinstance(raw_custom, dict):
                for cname, meta in raw_custom.items():
                    if not isinstance(meta, dict):
                        continue
                    self.custom_stats_meta[cname] = dict(meta)
                    c_base_vals = meta.get("base_values") or {}
                    if isinstance(c_base_vals, dict):
                        for w_cls, val in c_base_vals.items():
                            try:
                                fval = float(val)
                            except (TypeError, ValueError):
                                continue
                            if fval > 0:
                                self.base_stats.setdefault(w_cls, {})[cname] = fval
            raw_hpairs = custom_data.get("hybrid_pairs") or {}
            if isinstance(raw_hpairs, dict):
                for pkey, pmeta in raw_hpairs.items():
                    if isinstance(pmeta, dict):
                        self.hybrid_pairs_meta[str(pkey).strip()] = dict(pmeta)

        # 4. Load local recommended tiers
        self.reload_recommendations()

    def resolve_special_rule(self, weapon_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Find a weapon's Incarnon / multi-archetype special rule if configured."""
        if not weapon_name or not self.special_rules:
            return None
        candidates = [weapon_name.strip()]
        norm = normalize_weapon_alias(weapon_name)
        if norm and norm not in candidates:
            candidates.append(norm)
        entry = self.weapons.get(norm or weapon_name) or self.weapons.get(weapon_name)
        if entry and entry.get("en_name"):
            en_name = str(entry["en_name"]).strip()
            candidates.append(en_name)
            low_en = en_name.lower()
            for suf in (" prime", " wraith", " vandal", " umbra"):
                if low_en.endswith(suf):
                    candidates.append(en_name[: -len(suf)].strip())
            for pre in ("kuva ", "tenet ", "sancti ", "prisma ", "coda "):
                if low_en.startswith(pre):
                    candidates.append(en_name[len(pre) :].strip())

        for cand in candidates:
            low = cand.lower()
            compact = low.replace(" ", "").replace("·", "")
            for key in (low, compact):
                rule_key = self.special_rules_by_alias.get(key)
                if rule_key and rule_key in self.special_rules:
                    return self.special_rules[rule_key]
            # Strip Chinese variant suffix
            for suf in (" Prime", " 破坏者", " 亡魂", " Umbra"):
                if cand.endswith(suf):
                    base_c = cand[: -len(suf)].strip().lower()
                    rule_key = self.special_rules_by_alias.get(base_c)
                    if rule_key and rule_key in self.special_rules:
                        return self.special_rules[rule_key]
        return None

    @staticmethod
    def _select_best_archetype(
        rule: Dict[str, Any],
        riven: RivenData,
    ) -> Optional[Dict[str, Any]]:
        """Score all archetype templates in a special rule and return the highest-scoring match."""
        archetypes = rule.get("archetypes") or []
        if not archetypes:
            return None
        pos_names = {s.stat_name for s in riven.positive_stats}
        neg_names = {s.stat_name for s in riven.negative_stats}

        best_arch: Optional[Dict[str, Any]] = None
        best_score = -10_000.0
        for idx, arch in enumerate(archetypes):
            pos_list = list(arch.get("positives") or [])
            neg_list = set(arch.get("negatives") or [])
            inv_neg = set(arch.get("inverted_negatives") or [])
            conflict_pos = set(arch.get("conflict_positives") or [])
            deprio_pos = set(arch.get("deprioritized_positives") or [])

            score = 0.0
            for rank, st in enumerate(pos_list):
                if st in pos_names:
                    score += 16.0 if rank < 2 else 13.0
            if neg_names & inv_neg:
                score += 34.0
            elif neg_names & neg_list:
                score += 8.0
            if pos_names & conflict_pos:
                score -= 18.0
            if pos_names & deprio_pos:
                score -= 4.0

            # Slight tie-breaker favoring earlier archetype when scores are identical
            score -= idx * 0.01
            if score > best_score:
                best_score = score
                best_arch = arch
        return best_arch

    def resolve_wm_slug(self, weapon_name: Optional[str]) -> str:
        """Resolve the Warframe.market Riven `weapon_url_name` slug for a weapon.

        Prime/Vandal/Wraith/Prisma variants share their base weapon's Riven slug on WM
        (e.g. `战刃 Prime` -> `glaive`), whereas standalone variants like `赤毒·布拉玛`
        use their own WM slug (`kuva_bramma`).
        """
        if not weapon_name:
            return ""
        entry = self.weapons.get(weapon_name)
        if not entry:
            return ""
        slug = (entry.get("slug") or "").strip().lower()
        # If directly listed on WFM (wfm_disposition present), use its exact WFM slug
        if slug and entry.get("wfm_disposition") is not None:
            return slug
        # Otherwise strip Prime/Vandal/Wraith/Prisma suffix/prefix to map to base Riven slug
        if slug:
            for suf in ("_prime", "_vandal", "_wraith", "_umbra"):
                if slug.endswith(suf):
                    return slug[: -len(suf)]
            for pre in ("prisma_", "tenet_", "mk1_"):
                if slug.startswith(pre):
                    base_candidate = slug[len(pre) :]
                    if any(w.get("slug") == base_candidate for w in self.weapons.values()):
                        return base_candidate
        return slug

    def resolve_recommendations(
        self,
        weapon_name: Optional[str],
        weapon_class: str,
    ) -> Tuple[List[str], List[str], str, Optional[str]]:
        """Resolve local recommended positive/negative stats (ZERO network requests).

        Priority:
          1. Exact or base-variant match in `data/recommended_tiers.json` (builtin or WM-calibrated).
          2. Automatic derivation from weapon's WFCD crit/status profile in `weapons_riven.json`.
        """
        entry: Optional[Dict[str, Any]] = None
        if weapon_name:
            entry = self.recommended_tiers.get(weapon_name)
            if entry is None:
                # Try stripping variant suffix ("战刃 Prime" -> "战刃")
                for suf in (" Prime", " 破坏者", " 亡魂", " Umbra"):
                    if weapon_name.endswith(suf):
                        entry = self.recommended_tiers.get(weapon_name[: -len(suf)].strip())
                        if entry is not None:
                            break
            if entry is None:
                compact = weapon_name.replace(" ", "").replace("·", "")
                for k, v in self.recommended_tiers.items():
                    if k.replace(" ", "").replace("·", "") == compact:
                        entry = v
                        break

        if entry is not None:
            pos = list(entry.get("positives", []))[:4]
            neg = list(entry.get("negatives", []))[:2]
            src = entry.get("source", "builtin")
            price_str: Optional[str] = None
            min_p = entry.get("wm_min_price")
            med_p = entry.get("wm_median_price")
            if min_p is not None or med_p is not None:
                parts = []
                if min_p is not None:
                    parts.append(f"低 {min_p}p")
                if med_p is not None:
                    parts.append(f"中位 {med_p}p")
                price_str = " / ".join(parts)
            return pos, neg, src, price_str

        # Automatic fallback inference from weapon's WFCD combat profile
        w_info = self.weapons.get(weapon_name or "", {})
        cc = w_info.get("crit_chance")
        cd = w_info.get("crit_mult")
        sc = w_info.get("status_chance")

        if weapon_class == "melee":
            if (cc is not None and cc >= 0.22) or (cd is not None and cd >= 2.2):
                return ["暴击伤害", "暴击几率", "攻击速度", "攻击范围"], ["处决伤害", "滑行攻击暴击几率"], "inferred", None
            if sc is not None and sc >= 0.28 and (cc is None or cc < 0.18):
                return ["攻击速度", "攻击范围", "异常状态伤害", "毒素伤害"], ["处决伤害", "冲击伤害"], "inferred", None
            return ["暴击伤害", "攻击速度", "攻击范围", "伤害"], ["处决伤害", "冲击伤害"], "inferred", None

        # Ranged (rifle / shotgun / pistol / archgun)
        if (cc is not None and cc >= 0.22) or (cc is not None and cc >= 0.18 and cd is not None and cd >= 2.2):
            return ["暴击伤害", "多重射击", "暴击几率", "伤害"], ["变焦", "冲击伤害"], "inferred", None
        if sc is not None and sc >= 0.28 and (cc is None or cc < 0.16):
            return ["多重射击", "伤害", "异常状态伤害", "射速"], ["变焦", "冲击伤害"], "inferred", None
        return ["多重射击", "暴击伤害", "伤害", "射速"], ["变焦", "冲击伤害"], "inferred", None

    def resolve_weapon(
        self,
        weapon_name: Optional[str],
        stats: Sequence[RivenStat],
    ) -> Tuple[Optional[str], str, Optional[float], str]:
        """Resolve weapon name, class, and disposition from DB + fuzzy/alias matching.

        Returns:
            (canonical_weapon_name, weapon_class, disposition_or_None, source_label)
        """
        inferred_class = self._infer_weapon_class(stats)
        if not weapon_name:
            return None, inferred_class, None, "estimated"

        cleaned = weapon_name.strip()
        if cleaned in self.ocr_aliases:
            cleaned = self.ocr_aliases[cleaned]
        cleaned = normalize_weapon_alias(cleaned) or cleaned

        if cleaned in self.weapons:
            entry = self.weapons[cleaned]
            return cleaned, entry["class"], float(entry["disposition"]), "database"

        # English weapon name lookup (e.g. "Torid", "Kuva Bramma", "Plague Kripath", "Glaive")
        low_clean = cleaned.lower()
        if low_clean in self.weapons_by_en:
            zh_key = self.weapons_by_en[low_clean]
            entry = self.weapons[zh_key]
            return zh_key, entry["class"], float(entry["disposition"]), "database"

        # Try ignoring spaces / middle dot ("赤毒 布拉玛" -> "赤毒·布拉玛")
        compact = cleaned.replace(" ", "").replace("·", "")
        for name, entry in self.weapons.items():
            if name.replace(" ", "").replace("·", "") == compact:
                return name, entry["class"], float(entry["disposition"]), "database"

        # Fuzzy match for single-character OCR typos (ZH or EN)
        close = difflib.get_close_matches(cleaned, list(self.weapons.keys()), n=1, cutoff=0.72)
        if close:
            matched_name = close[0]
            entry = self.weapons[matched_name]
            return matched_name, entry["class"], float(entry["disposition"]), "database"

        close_en = difflib.get_close_matches(low_clean, list(self.weapons_by_en.keys()), n=1, cutoff=0.78)
        if close_en:
            zh_key = self.weapons_by_en[close_en[0]]
            entry = self.weapons[zh_key]
            return zh_key, entry["class"], float(entry["disposition"]), "database"

        return cleaned, inferred_class, None, "estimated"

    @staticmethod
    def _infer_weapon_class(stats: Sequence[RivenStat]) -> str:
        """Infer weapon class from stat names when weapon is not in DB."""
        stat_names = {s.stat_name for s in stats}
        if stat_names & MELEE_ONLY_STATS:
            return "melee"
        for s in stats:
            if "弓类武器" in s.raw_name:
                return "rifle"
        return "rifle"

    def _get_combo_multipliers(self, riven: RivenData) -> Tuple[float, float, str]:
        if any(s.is_hybrid for s in riven.stats):
            n_pos, n_neg = RivenParser.count_effective_slots(riven.stats)
            pattern = f"{n_pos}+{n_neg}-(融合)"
        else:
            n_pos = len(riven.positive_stats)
            n_neg = len(riven.negative_stats)
            pattern = f"{n_pos}+{n_neg}-"
        if (n_pos, n_neg) in self.combo_multipliers:
            pos_m, neg_m = self.combo_multipliers[(n_pos, n_neg)]
            return pos_m, neg_m, pattern
        # Graceful fallback for incomplete OCR or 1-hybrid-slot cards
        if n_pos >= 3:
            return (0.9375, 0.75, pattern) if n_neg > 0 else (0.75, 0.0, pattern)
        return (1.2375, 0.495, pattern) if n_neg > 0 else (0.99, 0.0, pattern)

    @staticmethod
    def _stat_magnitude(stat: RivenStat) -> float:
        """Extract comparable numeric magnitude (handles `x1.5` / `x0.63` faction stats)."""
        if stat.sign == "x":
            # Warframe displays multiplicative faction bonus as x(1 + pct/100) or x(1 - pct/100)
            return abs(stat.value - 1.0) * 100.0
        return abs(stat.value)

    def _estimate_disposition(
        self,
        stats: Sequence[RivenStat],
        weapon_class: str,
        pos_mult: float,
        neg_mult: float,
    ) -> float:
        """Estimate effective Riven Disposition (snapped to 0.05) from observed stat values."""
        base_table = BASE_RIVEN_STATS.get(weapon_class, BASE_RIVEN_STATS["rifle"])
        implied: List[float] = []
        for s in stats:
            base_val = base_table.get(s.stat_name)
            if not base_val:
                continue
            mult = neg_mult if s.is_negative else pos_mult
            if mult <= 0:
                continue
            mag = self._stat_magnitude(s)
            denom = base_val * mult
            if denom > 0:
                implied.append(mag / denom)

        if not implied:
            return 1.0

        med = statistics.median(implied)
        # Warframe dispositions are multiples of 0.05 in [0.50, 1.55]
        snapped = round(med / 0.05) * 0.05
        return max(0.50, min(1.55, round(snapped, 2)))

    def _lookup_hybrid_pair_meta(self, stat: RivenStat, all_stats: Sequence[RivenStat]) -> Optional[Dict[str, Any]]:
        """Find matching hybrid-pair config in `custom_riven_stats.json` for a split hybrid sub-stat."""
        if not stat.is_hybrid or stat.hybrid_group_id is None or not self.hybrid_pairs_meta:
            return None
        peers = [
            s.stat_name
            for s in all_stats
            if s.is_hybrid and s.hybrid_group_id == stat.hybrid_group_id
        ]
        if len(peers) < 2:
            return None
        a, b = peers[0], peers[1]
        for key in (f"{a}+{b}", f"{b}+{a}"):
            if key in self.hybrid_pairs_meta:
                return self.hybrid_pairs_meta[key]
        return None

    def _directional_grade_for_missing_range(
        self,
        stat: RivenStat,
        is_inverted_god_neg: bool = False,
    ) -> str:
        """Assign meaningful directional S/A/B/C grade when a brand-new stat has no official numeric range yet."""
        meta = self.custom_stats_meta.get(stat.stat_name) or {}
        tier = str(meta.get("direction_tier") or "").lower()
        weight = float(meta.get("score_weight", 1.0) or 1.0)

        if is_inverted_god_neg:
            return "S"
        if stat.is_negative:
            if stat.stat_name in HARMLESS_NEGATIVES:
                return "A"
            if stat.stat_name in HARMFUL_NEGATIVES or tier == "high":
                return "C"
            return "B"

        # Positive stat without official range yet -> directional meta quality
        if tier == "high" or weight >= 1.15 or stat.stat_name in ("暴击几率", "暴击伤害", "多重射击", "伤害", "弱点伤害"):
            return "A"
        if tier == "medium" or weight >= 1.0:
            return "B"
        return "B"

    def _evaluate_single_stat(
        self,
        stat: RivenStat,
        weapon_class: str,
        disposition: float,
        pos_mult: float,
        neg_mult: float,
        is_inverted_god_neg: bool = False,
        all_stats: Sequence[RivenStat] = (),
    ) -> StatEvaluation:
        base_table = self.base_stats.get(weapon_class, self.base_stats["rifle"])
        base_val = base_table.get(stat.stat_name)
        # Compound elements (Viral/Corrosive/Radiation/Magnetic/Gas/Blast) are strictly positive-only
        if stat.stat_name in POSITIVE_ONLY_COMPOUND_ELEMENTS and stat.is_negative:
            return StatEvaluation(
                stat_name=stat.stat_name,
                value=stat.value,
                sign=stat.sign,
                unit=stat.unit,
                is_negative=True,
                is_locked=stat.is_locked,
                stat_key=stat.stat_key,
                grade="?",
                range_display="复合元素仅限正面词条",
                is_hybrid=stat.is_hybrid,
                missing_base_range=True,
            )
        mult = neg_mult if stat.is_negative else pos_mult

        if not base_val or mult <= 0:
            dir_grade = self._directional_grade_for_missing_range(
                stat, is_inverted_god_neg=is_inverted_god_neg
            )
            return StatEvaluation(
                stat_name=stat.stat_name,
                value=stat.value,
                sign=stat.sign,
                unit=stat.unit,
                is_negative=stat.is_negative,
                is_locked=stat.is_locked,
                is_inverted_god_neg=is_inverted_god_neg,
                stat_key=stat.stat_key,
                grade=dir_grade,
                range_display=HYBRID_MISSING_RANGE_NOTICE_ZH,
                is_hybrid=stat.is_hybrid,
                missing_base_range=True,
            )

        # For hybrid dual-attribute sub-stats, apply hybrid scale factor (unified 1.20 disposition)
        hybrid_scale = 1.0
        if stat.is_hybrid:
            pair_meta = self._lookup_hybrid_pair_meta(stat, all_stats)
            hybrid_scale = float(
                (pair_meta or {}).get("scale_factor", self.default_hybrid_scale)
            )

        mid_mag = base_val * mult * disposition * hybrid_scale
        min_mag = mid_mag * 0.90
        max_mag = mid_mag * 1.10
        obs_mag = self._stat_magnitude(stat)

        # Roll factor relative to midpoint (theoretical range: 0.90 .. 1.10)
        ratio = obs_mag / mid_mag if mid_mag > 0 else 1.0

        if stat.is_negative and not is_inverted_god_neg:
            # For standard negative curses, smaller penalty magnitude is BETTER
            raw_pct = (max_mag - obs_mag) / (max_mag - min_mag) * 100.0
            delta_pct = (1.0 - ratio) * 100.0
        else:
            # For positive stats AND inverted god-negatives (e.g. -Crit Chance on Laetum/Phenmor/Felarx),
            # larger magnitude is BETTER!
            raw_pct = (obs_mag - min_mag) / (max_mag - min_mag) * 100.0
            delta_pct = (ratio - 1.0) * 100.0

        clamped_pct = round(max(0.0, min(100.0, raw_pct)), 1)
        grade = grade_from_roll_pct(clamped_pct)

        if stat.sign == "x":
            if stat.is_negative:
                disp_min = round(1.0 - max_mag / 100.0, 2)
                disp_mid = round(1.0 - mid_mag / 100.0, 2)
                disp_max = round(1.0 - min_mag / 100.0, 2)
            else:
                disp_min = round(1.0 + min_mag / 100.0, 2)
                disp_mid = round(1.0 + mid_mag / 100.0, 2)
                disp_max = round(1.0 + max_mag / 100.0, 2)
            range_str = f"x{disp_min:.2f} ~ x{disp_max:.2f}"
            return StatEvaluation(
                stat_name=stat.stat_name,
                value=stat.value,
                sign=stat.sign,
                unit=stat.unit,
                is_negative=stat.is_negative,
                is_locked=stat.is_locked,
                is_inverted_god_neg=is_inverted_god_neg,
                stat_key=stat.stat_key,
                min_value=disp_min,
                mid_value=disp_mid,
                max_value=disp_max,
                roll_pct=round(clamped_pct, 1),
                delta_pct=round(delta_pct, 1),
                grade=grade,
                range_display=range_str,
                is_hybrid=stat.is_hybrid,
                missing_base_range=False,
            )

        digits = 2 if base_val < 10 else 1
        disp_min = round(min_mag, digits)
        disp_mid = round(mid_mag, digits)
        disp_max = round(max_mag, digits)
        range_str = f"{stat.sign}{disp_min:g}{stat.unit} ~ {stat.sign}{disp_max:g}{stat.unit}"

        return StatEvaluation(
            stat_name=stat.stat_name,
            value=stat.value,
            sign=stat.sign,
            unit=stat.unit,
            is_negative=stat.is_negative,
            is_locked=stat.is_locked,
            is_inverted_god_neg=is_inverted_god_neg,
            stat_key=stat.stat_key,
            min_value=disp_min,
            mid_value=disp_mid,
            max_value=disp_max,
            roll_pct=round(clamped_pct, 1),
            delta_pct=round(delta_pct, 1),
            grade=grade,
            range_display=range_str,
            is_hybrid=stat.is_hybrid,
            missing_base_range=False,
        )

    @staticmethod
    def _detect_tags_and_synergy(
        riven: RivenData,
        weapon_class: str,
        archetype: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[str], float, str, str]:
        """Identify valuable stat combos & negative quality (archetype-aware)."""
        pos_names = {s.stat_name for s in riven.positive_stats}
        neg_names = {s.stat_name for s in riven.negative_stats}
        tags: List[str] = []
        synergy_bonus = 0.0

        inv_negs = set((archetype or {}).get("inverted_negatives") or [])
        arch_negs = set((archetype or {}).get("negatives") or [])
        deprio_pos = set((archetype or {}).get("deprioritized_positives") or [])
        arch_id = str((archetype or {}).get("id") or "")

        has_cc = "暴击几率" in pos_names
        has_cd = "暴击伤害" in pos_names
        has_ms = "多重射击" in pos_names
        has_dmg = "伤害" in pos_names
        has_fr = "射速" in pos_names
        has_as = ("攻击速度" in pos_names) and ("攻击速度" not in deprio_pos)
        has_rng = "攻击范围" in pos_names
        has_ic = "初始连击" in pos_names
        has_tox = "毒素伤害" in pos_names
        has_elec = "电击伤害" in pos_names
        has_cold = "冰冻伤害" in pos_names
        has_heat = "火焰伤害" in pos_names

        # Core high-value combos
        if arch_id == "heavy_statstick" and weapon_class == "melee":
            if has_ic and has_cd and (has_dmg or has_cc):
                tags.append("重击神配")
                synergy_bonus += 22.0
            elif has_cc and has_cd and has_dmg:
                tags.append("双暴基伤")
                synergy_bonus += 18.0
            elif has_ic and (has_cd or has_dmg or has_cc):
                tags.append("重击神配")
                synergy_bonus += 14.0
            elif has_cc and has_cd:
                tags.append("双暴")
                synergy_bonus += 12.0
        elif weapon_class == "melee":
            if has_cc and has_cd and (has_as or has_rng):
                tags.append("双暴攻速/范围" if has_as and has_rng else ("双暴攻速" if has_as else "双暴范围"))
                synergy_bonus += 18.0
            elif has_cc and has_cd:
                tags.append("双暴")
                synergy_bonus += 12.0
            elif has_as and has_rng:
                tags.append("攻速范围")
                synergy_bonus += 10.0
            elif has_dmg and (has_cc or has_cd):
                tags.append("基伤配暴")
                synergy_bonus += 8.0
        else:
            if has_cc and has_cd and has_ms:
                tags.append("双暴多重")
                synergy_bonus += 22.0
            elif has_cc and has_cd and has_dmg:
                tags.append("双暴基伤")
                synergy_bonus += 16.0
            elif has_cc and has_cd:
                tags.append("双暴")
                synergy_bonus += 12.0
            elif has_dmg and has_ms:
                tags.append("基伤多重")
                synergy_bonus += 13.0
            elif has_ms and (has_cc or has_cd):
                tags.append("多重配暴")
                synergy_bonus += 12.0
            elif has_ms and has_fr:
                tags.append("多重射速")
                synergy_bonus += 11.0 if arch_id == "non_crit_devour" else 9.0
            elif has_dmg and (has_cc or has_cd):
                tags.append("基伤配暴")
                synergy_bonus += 8.0

        # Elemental combos
        if has_cold and has_tox:
            tags.append("直出病毒")
            synergy_bonus += 8.0
        elif has_elec and has_tox:
            tags.append("直出腐蚀")
            synergy_bonus += 7.0
        elif has_heat and has_tox:
            tags.append("直出毒气")
            synergy_bonus += 4.0
        elif (pos_names & ELEMENTAL_STATS) and has_ms:
            tags.append("多重元素")
            synergy_bonus += 5.0

        if "异常状态伤害" in pos_names:
            tags.append("异常增伤")
            synergy_bonus += 5.0
        if "弹药效率" in pos_names:
            tags.append("弹药效率")
            synergy_bonus += 4.0

        # Positive / negative structure tag
        n_pos, n_neg = len(riven.positive_stats), len(riven.negative_stats)
        if n_pos == 3 and n_neg == 1:
            tags.append("三正一负")
            synergy_bonus += 4.0
        elif n_pos == 2 and n_neg == 1:
            tags.append("二正一负")
            synergy_bonus += 5.0
        elif n_neg == 0 and n_pos >= 2:
            tags.append("无负词条")

        # Negative quality assessment (archetype inverted god-neg takes top precedence!)
        if neg_names:
            if neg_names & inv_negs:
                tags.insert(0, "SSS 反转神负")
                synergy_bonus += 24.0
            elif neg_names <= (HARMLESS_NEGATIVES | arch_negs):
                tags.append("无害负")
                synergy_bonus += 10.0
            elif neg_names & HARMFUL_NEGATIVES:
                tags.append("伤筋负")
                synergy_bonus -= 16.0
            else:
                tags.append("可用负")

        # Deduplicate while preserving order
        seen = set()
        unique_tags = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)

        if synergy_bonus >= 25.0:
            summary_key = "god_roll"
            hint = "极品毕业组合，强烈建议保留"
        elif synergy_bonus >= 14.0:
            summary_key = "good_roll"
            hint = "优质可用词条组合"
        elif synergy_bonus <= -10.0:
            summary_key = "harmful_neg"
            hint = "负面词条较伤，建议继续循环"
        elif synergy_bonus >= 4.0:
            summary_key = "decent_roll"
            hint = "词条结构尚可，可按需求保留或精洗"
        else:
            summary_key = "average_roll"
            hint = "常规过渡词条，建议继续循环"

        return unique_tags, synergy_bonus, hint, summary_key

    def evaluate(
        self,
        riven: RivenData,
        disposition_override: Optional[float] = None,
        weapon_class_override: Optional[str] = None,
    ) -> RivenEvaluation:
        """Compute full numerical roll ratings (globally fixed 1.20 disposition) & Incarnon archetype tags."""
        canon_weapon, w_class, _db_disp, _source = self.resolve_weapon(
            riven.weapon_name,
            riven.stats,
        )
        if weapon_class_override:
            w_class = weapon_class_override

        pos_mult, neg_mult, combo_pattern = self._get_combo_multipliers(riven)

        # Globally unified fixed disposition = 1.20 across all weapons and variants
        disposition = FIXED_GLOBAL_DISPOSITION
        source = "fixed_1.2"

        final_weapon = canon_weapon or riven.weapon_name
        weapon_slug = self.resolve_wm_slug(final_weapon)

        # Check Incarnon / special multi-archetype rule first (zero network)
        special_rule = self.resolve_special_rule(final_weapon)
        selected_arch = self._select_best_archetype(special_rule, riven) if special_rule else None
        inv_neg_set = set((selected_arch or {}).get("inverted_negatives") or [])

        stat_evals = [
            self._evaluate_single_stat(
                s,
                w_class,
                disposition,
                pos_mult,
                neg_mult,
                is_inverted_god_neg=(s.is_negative and s.stat_name in inv_neg_set),
                all_stats=riven.stats,
            )
            for s in riven.stats
        ]

        # Resolve local recommendations (special_rule archetype > builtin/WM > WFCD inferred)
        base_rec_pos, base_rec_neg, base_rec_src, cached_wm_price = self.resolve_recommendations(
            final_weapon, w_class
        )
        if selected_arch is not None:
            rec_pos = list(selected_arch.get("positives") or base_rec_pos)[:4]
            arch_negs = list(selected_arch.get("inverted_negatives") or []) + list(
                selected_arch.get("negatives") or []
            )
            rec_neg = arch_negs[:3] if arch_negs else base_rec_neg
            rec_src = "special_rule"
        else:
            rec_pos, rec_neg, rec_src = base_rec_pos, base_rec_neg, base_rec_src

        rec_pos_set = set(rec_pos)
        rec_neg_set = set(rec_neg)

        matched_pos = 0
        matched_neg = False
        has_inverted_god_neg = False
        for se in stat_evals:
            if not se.is_negative and se.stat_name in rec_pos_set:
                se.is_recommended = True
                matched_pos += 1
            elif se.is_negative and (se.stat_name in rec_neg_set or se.is_inverted_god_neg):
                se.is_recommended = True
                matched_neg = True
                if se.is_inverted_god_neg:
                    has_inverted_god_neg = True

        # Archetype display labels & conflict hints
        arch_id = ""
        arch_name_zh = ""
        arch_name_en = ""
        arch_warn_zh = ""
        arch_warn_en = ""
        if selected_arch is not None:
            arch_id = str(selected_arch.get("id") or "")
            arch_name_zh = str(selected_arch.get("name_zh") or "")
            arch_name_en = str(selected_arch.get("name_en") or arch_name_zh)
            if has_inverted_god_neg:
                arch_name_zh = f"{arch_name_zh}（-暴击反转神负）"
                arch_name_en = f"{arch_name_en} (-CC God Neg)"
            pos_names_set = {s.stat_name for s in riven.positive_stats}
            conflict_pos = set(selected_arch.get("conflict_positives") or [])
            if pos_names_set & conflict_pos:
                arch_warn_zh = str(selected_arch.get("conflict_hint_zh") or "")
                arch_warn_en = str(selected_arch.get("conflict_hint_en") or "")

        # Weighted average roll_pct (positive stats weight 1.0 each, inverted god-neg 0.8, normal neg 0.5)
        weighted_sum = 0.0
        total_weight = 0.0
        has_missing_range = False
        _dir_grade_score = {"S": 90.0, "A": 72.0, "B": 50.0, "C": 25.0}
        for se in stat_evals:
            if se.roll_pct is None:
                if se.missing_base_range and se.grade in _dir_grade_score:
                    has_missing_range = True
                    w = 0.7 if not se.is_negative else 0.4
                    weighted_sum += _dir_grade_score[se.grade] * w
                    total_weight += w
                continue
            w = 0.8 if se.is_inverted_god_neg else (0.5 if se.is_negative else 1.0)
            weighted_sum += se.roll_pct * w
            total_weight += w

        overall_roll_pct = (weighted_sum / total_weight) if total_weight > 0 else None

        tags, synergy_bonus, hint, summary_key = self._detect_tags_and_synergy(
            riven, w_class, archetype=selected_arch
        )

        # Apply hybrid dual-stat & custom-stat synergy tags/weights from `custom_riven_stats.json`
        if any(s.is_hybrid for s in riven.stats):
            hybrid_groups: Dict[int, List[RivenStat]] = {}
            for s in riven.stats:
                if s.is_hybrid and s.hybrid_group_id is not None:
                    hybrid_groups.setdefault(s.hybrid_group_id, []).append(s)
            matched_pair_tag = False
            for grp_stats in hybrid_groups.values():
                if len(grp_stats) >= 2:
                    a, b = grp_stats[0].stat_name, grp_stats[1].stat_name
                    pmeta = self.hybrid_pairs_meta.get(f"{a}+{b}") or self.hybrid_pairs_meta.get(f"{b}+{a}")
                    if pmeta:
                        ptag = str(pmeta.get("tag") or "融合词条")
                        if ptag not in tags:
                            tags.insert(0, ptag)
                        synergy_bonus += float(pmeta.get("synergy_bonus", 4.0)) * 0.4
                        matched_pair_tag = True
            if not matched_pair_tag and "融合词条" not in tags:
                tags.insert(0, "融合词条")
                synergy_bonus += 4.0

        for s in riven.positive_stats:
            cmeta = self.custom_stats_meta.get(s.stat_name)
            if cmeta:
                ctag = str(cmeta.get("synergy_tag") or "").strip()
                if ctag and ctag not in tags:
                    tags.append(ctag)
                cweight = float(cmeta.get("score_weight", 1.0) or 1.0)
                if cweight > 1.0:
                    synergy_bonus += (cweight - 1.0) * 20.0

        if matched_pos >= 3 and matched_neg:
            if "全中推荐" not in tags:
                tags.insert(0, "全中推荐")
            synergy_bonus += 8.0
        elif matched_pos >= 2:
            synergy_bonus += 4.0

        # Combine numerical roll score + synergy bonus into final S/A/B/C grade
        has_hybrid = any(s.is_hybrid for s in riven.stats)
        if overall_roll_pct is not None:
            composite_score = max(0.0, min(100.0, overall_roll_pct + synergy_bonus * 0.65))
            # Protect Zariman Incarnon inverted god-neg combos (e.g. Laetum/Phenmor/Felarx)
            # from low raw roll_pct under unified 1.20 disposition scaling
            if has_inverted_god_neg and matched_pos >= 3:
                composite_score = max(composite_score, 85.0)
            elif has_inverted_god_neg and matched_pos >= 2:
                composite_score = max(composite_score, 68.0)
            # Protect hybrid/new-stat cards with strong directional synergy from missing-range penalty
            if has_hybrid or has_missing_range:
                if synergy_bonus >= 30.0:
                    composite_score = max(composite_score, 68.0)
                elif synergy_bonus >= 18.0:
                    composite_score = max(composite_score, 48.0)
            overall_grade = grade_from_roll_pct(composite_score)
        elif stat_evals:
            # Graceful directional grade when stats lack official numeric base ranges yet
            directional_base = 52.0 + synergy_bonus * 1.15
            overall_grade = grade_from_roll_pct(max(0.0, min(100.0, directional_base)))
        else:
            overall_grade = "?"

        en_name = (self.weapons.get(final_weapon or "", {}).get("en_name") or "").strip()
        if not en_name and riven.weapon_name and riven.weapon_name.isascii():
            en_name = riven.weapon_name

        return RivenEvaluation(
            weapon_name=final_weapon,
            weapon_name_en=en_name,
            weapon_slug=weapon_slug,
            weapon_class=w_class,
            weapon_class_cn=WEAPON_CLASS_CN.get(w_class, w_class),
            disposition=disposition,
            disposition_source=source,
            combo_pattern=combo_pattern,
            overall_roll_pct=overall_roll_pct,
            overall_grade=overall_grade,
            stat_evaluations=stat_evals,
            tags=tags,
            summary=hint,
            summary_key=summary_key,
            recommended_positives=rec_pos,
            recommended_negatives=rec_neg,
            recommendation_source=rec_src,
            recommendation_source_cn=RECOMMENDATION_SOURCE_CN.get(rec_src, rec_src),
            matched_rec_pos=matched_pos,
            matched_rec_neg=matched_neg,
            cached_wm_price=cached_wm_price,
            matched_archetype_id=arch_id,
            matched_archetype_name=arch_name_zh,
            matched_archetype_name_en=arch_name_en,
            archetype_warning=arch_warn_zh,
            archetype_warning_en=arch_warn_en,
            has_inverted_god_neg=has_inverted_god_neg,
        )


@dataclass
class RerollSynergyAlert:
    """High-potential reroll & compound-element / fusion synergy popup payload for `reroll` mode.

    Triggered ONLY when a reroll comparison card has `3正1负` (`3+1-`) or `2正1负` (`2+1-`)
    structure AND at least one `S`-grade positive attribute (or >=90% fusion-capable stat).
    """

    source_side_label: str
    weapon_name: str
    combo_label_zh: str
    combo_label_en: str
    s_grade_stats: List[str] = field(default_factory=list)
    synergy_tips_zh: List[str] = field(default_factory=list)
    synergy_tips_en: List[str] = field(default_factory=list)
    fusion_combos_zh: List[str] = field(default_factory=list)
    fusion_combos_en: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Official Warframe Update 44.1 Riven Splicing (裂罅熔接 / 复合词条) recipe table.
# Each entry is (stat_a_zh, stat_b_zh, result_zh, stat_a_en, stat_b_en, result_en, allowed_classes_or_None)
_RANGED_CLASSES = frozenset({"rifle", "shotgun", "pistol", "archgun"})
_RANGED_NON_ARCHGUN = frozenset({"rifle", "shotgun", "pistol"})
_MELEE_CLASSES = frozenset({"melee"})

OFFICIAL_RIVEN_SPLICE_RECIPES: Tuple[
    Tuple[str, str, str, str, str, str, Optional[frozenset[str]]], ...
] = (
    # Ranged weakpoint & utility splices
    ("伤害", "多重射击", "弱点伤害", "Damage", "Multishot", "Weak Point Damage", _RANGED_CLASSES),
    ("伤害", "变焦", "弱点伤害", "Damage", "Zoom", "Weak Point Damage", _RANGED_CLASSES),
    ("暴击几率", "多重射击", "弱点暴击几率", "Critical Chance", "Multishot", "Weak Point Critical Chance", _RANGED_CLASSES),
    ("暴击几率", "变焦", "弱点暴击几率", "Critical Chance", "Zoom", "Weak Point Critical Chance", _RANGED_CLASSES),
    ("弹匣容量", "装填速度", "弹药效率", "Magazine Capacity", "Reload Speed", "Ammo Efficiency", _RANGED_CLASSES),
    ("武器后坐力", "弹药最大值", "弹药效率", "Weapon Recoil", "Ammo Maximum", "Ammo Efficiency", _RANGED_CLASSES),
    ("弹药最大值", "装填速度", "收起武器时弹匣每秒自动装填", "Ammo Maximum", "Reload Speed", "Reload Holstered", _RANGED_NON_ARCHGUN),
    ("弹药最大值", "弹匣容量", "收起武器时弹匣每秒自动装填", "Ammo Maximum", "Magazine Capacity", "Reload Holstered", _RANGED_NON_ARCHGUN),
    # Universal status damage & 6 compound elements
    ("伤害", "触发几率", "异常状态伤害", "Damage", "Status Chance", "Status Damage", None),
    ("毒素伤害", "冰冻伤害", "病毒伤害", "Toxin", "Cold", "Viral", None),
    ("毒素伤害", "电击伤害", "腐蚀伤害", "Toxin", "Electricity", "Corrosive", None),
    ("毒素伤害", "火焰伤害", "毒气伤害", "Toxin", "Heat", "Gas", None),
    ("火焰伤害", "电击伤害", "辐射伤害", "Heat", "Electricity", "Radiation", None),
    ("火焰伤害", "冰冻伤害", "爆炸伤害", "Heat", "Cold", "Blast", None),
    ("电击伤害", "冰冻伤害", "磁力伤害", "Electricity", "Cold", "Magnetic", None),
    # Universal faction splices
    ("对 Grineer 伤害", "对 Corpus 伤害", "对奥罗金伤害", "Damage to Grineer", "Damage to Corpus", "Damage to Orokin", None),
    ("对 Corpus 伤害", "对 Infested 伤害", "对科腐者伤害", "Damage to Corpus", "Damage to Infested", "Damage to Techrot", None),
    ("对 Infested 伤害", "对 Grineer 伤害", "对炽蛇军伤害", "Damage to Infested", "Damage to Grineer", "Damage to Scaldra", None),
    # Melee-only splices
    ("重击效率", "额外连击几率", "在重击时的近战伤害", "Heavy Attack Efficiency", "Combo Count Chance", "Melee Damage on Heavy Attack", _MELEE_CLASSES),
    ("重击效率", "连击持续时间", "重击准备速度", "Heavy Attack Efficiency", "Combo Duration", "Heavy Attack Wind Up Speed", _MELEE_CLASSES),
    ("攻击速度", "攻击范围", "招架角度", "Attack Speed", "Range", "Parry Angle", _MELEE_CLASSES),
    ("伤害", "攻击速度", "震地攻击伤害", "Damage", "Attack Speed", "Slam Attack Damage", _MELEE_CLASSES),
)


def get_fusion_combinations_for_stat(
    stat_name: str,
    weapon_class: str = "rifle",
) -> List[Tuple[str, str, str, str, str]]:
    """Return all official Warframe Riven Splicing recipes involving `stat_name` for `weapon_class`.

    Returns list of `(self_en, partner_zh, result_zh, partner_en, result_en)`.
    """
    w_cls = (weapon_class or "rifle").lower()
    out: List[Tuple[str, str, str, str, str]] = []
    for a_zh, b_zh, res_zh, a_en, b_en, res_en, allowed in OFFICIAL_RIVEN_SPLICE_RECIPES:
        if allowed is not None and w_cls not in allowed:
            continue
        if stat_name == a_zh:
            out.append((a_en, b_zh, res_zh, b_en, res_en))
        elif stat_name == b_zh:
            out.append((b_en, a_zh, res_zh, a_en, res_en))
    return out


_COMPOUND_BUILD_TIPS: Dict[str, Tuple[str, str]] = {
    "病毒伤害": (
        "自带【病毒】复合元素 → 直接省去冰+毒 2 张元素卡槽，配卡可直出【病毒+火】或【病毒+切割】毕业体系！",
        "Built-in [Viral] → Saves 2 elemental mod slots; pairs directly with [Viral + Heat] or [Viral + Slash]!",
    ),
    "腐蚀伤害": (
        "自带【腐蚀】复合元素 → 省去电+毒双卡槽，极度适配高护甲剥甲直伤 / 【腐蚀+火】 / 【腐蚀+冰爆】流派！",
        "Built-in [Corrosive] → Saves 2 mod slots; ideal for armor-strip / [Corrosive + Heat] / [Corrosive + Cold]!",
    ),
    "辐射伤害": (
        "自带【辐射】复合元素 → 省去火+电双卡槽，可自由搭配【病毒+火】共存不串味，适配三傻/钢铁直伤！",
        "Built-in [Radiation] → Saves 2 mod slots; coexists cleanly with [Viral + Heat] without mixing!",
    ),
    "磁力伤害": (
        "自带【磁力】复合元素 → 省去冰+电双卡槽，破盾+超宏防护利器，且可与【病毒+火】独立共存！",
        "Built-in [Magnetic] → Saves 2 mod slots; shreds shields & Overguard alongside [Viral + Heat]!",
    ),
    "毒气伤害": (
        "自带【毒气】复合元素 → 省去火+毒双卡槽，适配聚怪范围持续 DOT 清图流派！",
        "Built-in [Gas] → Saves 2 mod slots; strong for grouping AoE Gas DOT builds!",
    ),
    "爆炸伤害": (
        "自带【爆炸】复合元素 → 省去火+冰双卡槽，高触发武器可走范围爆炸清图体系，且可配【毒素/腐蚀】！",
        "Built-in [Blast] → Saves 2 mod slots; excels on high-status AoE Blast builds!",
    ),
}

_SINGLE_ELEM_COMBO_TIPS: Dict[str, Tuple[str, str]] = {
    "毒素伤害": (
        "含【毒素】→ 单插冰卡直出【病毒】，或单插电卡直出【腐蚀】，节省卡槽泛用度拉满！",
        "Has [Toxin] → Add Cold for instant [Viral] or Electricity for [Corrosive] (saves 1 mod slot)!",
    ),
    "冰冻伤害": (
        "含【冰冻】→ 单插毒卡直出【病毒】，或配合暴伤流触发冰冻额外暴伤加成！",
        "Has [Cold] → Add Toxin for instant [Viral], or stack Cold crit-damage synergy!",
    ),
    "火焰伤害": (
        "含【火焰】→ 可无缝衔接主流【病毒 + 火】黄金配卡，或单卡合成【辐射 / 毒气】！",
        "Has [Heat] → Plugs straight into meta [Viral + Heat], or combines into [Radiation / Gas]!",
    ),
    "电击伤害": (
        "含【电击】→ 单插毒卡直出【腐蚀】，近战更可直触发【近战电击影响】清图体系！",
        "Has [Electricity] → Add Toxin for [Corrosive], or run [Melee Influence] AoE builds!",
    ),
}


def analyze_reroll_synergy_alert(cards: Sequence[Any]) -> Optional[RerollSynergyAlert]:
    """Check reroll cards for `3+1-` / `2+1-` + at least one `S`-grade positive stat.

    Prioritizes the right (`[新循环]`) card; falls back to the left (`[当前]`) card if
    only the left side satisfies the trigger criteria. Returns `None` if neither qualifies.
    """
    if not cards:
        return None

    # Inspect right ([新循环]) first, then left ([当前])
    ordered = list(reversed(cards)) if len(cards) >= 2 else list(cards)
    for item in ordered:
        riven, ev = _extract_riven_and_eval(item)
        if riven is None or ev is None or not riven.is_valid:
            continue

        if any(s.is_hybrid for s in riven.stats):
            n_pos, n_neg = RivenParser.count_effective_slots(riven.stats)
        else:
            n_pos = len(riven.positive_stats)
            n_neg = len(riven.negative_stats)

        if (n_pos, n_neg) not in ((3, 1), (2, 1)):
            continue

        s_positives = [
            se for se in ev.stat_evaluations if (not se.is_negative) and se.grade == "S"
        ]
        if not s_positives:
            continue

        combo_zh = "3正1负" if n_pos == 3 else "2正1负"
        combo_en = "3+1-" if n_pos == 3 else "2+1-"
        side_label = str(getattr(item, "label", "") or "[新循环]")
        weapon_disp = ev.weapon_name or riven.weapon_name or ""

        s_lines: List[str] = []
        for se in s_positives:
            val_str = f"x{se.value:g}" if se.sign == "x" else f"{se.sign}{se.value:g}{se.unit}"
            pct_str = f" ({se.roll_pct:.0f}% S)" if se.roll_pct is not None else " (S)"
            s_lines.append(f"{val_str} {se.stat_name}{pct_str}")

        pos_names = [se.stat_name for se in ev.stat_evaluations if not se.is_negative]
        pos_set = set(pos_names)
        tips_zh: List[str] = []
        tips_en: List[str] = []
        fusion_zh: List[str] = []
        fusion_en: List[str] = []

        # 0. High-roll (>= 90%) stats capable of official Riven Splicing -> list each recipe cleanly:
        #    e.g. "熔接→ 暴击几率97%+多重射击 = 弱点暴击几率"
        for se in ev.stat_evaluations:
            if se.is_negative or se.is_hybrid:
                continue
            if se.roll_pct is None or float(se.roll_pct) < 90.0:
                continue
            combos = get_fusion_combinations_for_stat(se.stat_name, weapon_class=ev.weapon_class)
            if not combos:
                continue
            pct_int = int(round(float(se.roll_pct)))
            for self_en, p_zh, r_zh, p_en, r_en in combos:
                fz = f"熔接→ {se.stat_name}{pct_int}%+{p_zh} = {r_zh}"
                fe = f"Splice→ {self_en} {pct_int}% + {p_en} = {r_en}"
                if fz not in fusion_zh:
                    fusion_zh.append(fz)
                    fusion_en.append(fe)
                    tips_zh.append(fz)
                    tips_en.append(fe)

        # 1. Built-in compound elemental stats on the Riven
        for name in pos_names:
            if name in _COMPOUND_BUILD_TIPS:
                tz, te = _COMPOUND_BUILD_TIPS[name]
                if tz not in tips_zh:
                    tips_zh.append(tz)
                    tips_en.append(te)

        # 2. Single base elements on the Riven
        for name in pos_names:
            if name in _SINGLE_ELEM_COMBO_TIPS:
                tz, te = _SINGLE_ELEM_COMBO_TIPS[name]
                if tz not in tips_zh:
                    tips_zh.append(tz)
                    tips_en.append(te)

        # 3. Core S-grade DPS multipliers (CC / CD / Multishot / Damage)
        s_names = {se.stat_name for se in s_positives}
        if s_names & {"暴击几率", "暴击伤害", "多重射击", "伤害"}:
            if {"暴击几率", "暴击伤害", "多重射击"} <= pos_set:
                tips_zh.append("S 级【双暴多重】核心毕业乘区 → 配卡补齐【病毒 + 火】或【腐蚀 + 冰】即可达顶配输出！")
                tips_en.append("S-grade [CC+CD+MS] core multipliers → Pair with [Viral + Heat] or [Corrosive + Cold] for peak DPS!")
            elif {"暴击几率", "暴击伤害"} <= pos_set:
                tips_zh.append("含 S 级【双暴】核心乘区 → 建议搭配【病毒 + 火】或【腐蚀 + 冰】元素组合放大暴击收益！")
                tips_en.append("S-grade [CC+CD] core → Pair with [Viral + Heat] or [Corrosive + Cold] to maximize crit scaling!")
            else:
                tips_zh.append("含 S 级高倍率核心词条 → 配卡优先搭配主流【病毒 + 火】或【腐蚀 + 冰】复合元素即可稳定增伤！")
                tips_en.append("S-grade core stat → Pair with meta [Viral + Heat] or [Corrosive + Cold] elemental mods!")

        if not tips_zh:
            tips_zh.append("优质高潜正负结构 → 可结合武器面板自由补齐【病毒 + 火】或【腐蚀 + 冰】元素组合。")
            tips_en.append("Strong high-potential roll structure → Pair with [Viral + Heat] or [Corrosive + Cold].")

        return RerollSynergyAlert(
            source_side_label=side_label,
            weapon_name=weapon_disp,
            combo_label_zh=combo_zh,
            combo_label_en=combo_en,
            s_grade_stats=s_lines,
            synergy_tips_zh=tips_zh,
            synergy_tips_en=tips_en,
            fusion_combos_zh=fusion_zh,
            fusion_combos_en=fusion_en,
        )

    return None


def is_98pct_positive_highlight_stat(se: StatEvaluation) -> bool:
    """Return True strictly for non-negative, non-hybrid stats with roll_pct >= 98.0.

    Explicitly excludes ALL negative stats (including positive-benefit / inverted god-negatives)
    and ALL hybrid dual-attribute stats.
    """
    return (
        not se.is_negative
        and not se.is_hybrid
        and se.roll_pct is not None
        and float(se.roll_pct) >= 98.0
    )


def is_positive_benefit_negative(se: StatEvaluation) -> bool:
    """Return True when a negative stat gives positive/harmless build value (inverted god-neg / recommended / harmless)."""
    if not se.is_negative:
        return False
    return bool(
        se.is_inverted_god_neg
        or se.is_recommended
        or se.stat_name in HARMLESS_NEGATIVES
    )


def has_rare_drop_stat(
    evaluation: RivenEvaluation,
    *,
    unlocked_only: bool = True,
    left_evaluation: Optional[RivenEvaluation] = None,
) -> bool:
    """Return True if the card contains an unlocked S-grade positive stat OR unlocked S-grade positive-benefit negative.

    When `left_evaluation` is provided (dual-card reroll mode), any stat on `evaluation`
    whose `(stat_name, is_negative, value)` is identical (within ±0.15) to a stat on
    `left_evaluation` is also treated as locked/carried-over so locked stats never cause
    false rare-drop chimes even if OCR missed the padlock glyph.
    """
    left_stats = left_evaluation.stat_evaluations if left_evaluation is not None else ()

    def _is_carried_over_from_left(se: StatEvaluation) -> bool:
        for lse in left_stats:
            if (
                lse.stat_name == se.stat_name
                and lse.is_negative == se.is_negative
                and abs(float(lse.value) - float(se.value)) <= 0.15
            ):
                return True
        return False

    for se in evaluation.stat_evaluations:
        if se.grade != "S":
            continue
        if unlocked_only and (se.is_locked or _is_carried_over_from_left(se)):
            continue
        if not se.is_negative:
            return True
        if is_positive_benefit_negative(se):
            return True
    return False


def should_play_reroll_rare_sound(
    cards: Sequence[Any],
    work_mode: str,
) -> bool:
    """Strictly limit automatic gameplay audio cues to dual-card (`reroll`) mode with unlocked S stats."""
    if work_mode != "reroll" or len(cards) != 2:
        return False
    left_ev = getattr(cards[0], "evaluation", None)
    right_ev = getattr(cards[1], "evaluation", None)
    if right_ev is None:
        return False
    return has_rare_drop_stat(right_ev, unlocked_only=True, left_evaluation=left_ev)
