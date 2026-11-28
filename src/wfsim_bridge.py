"""WFSim (`https://wfsim.app`) share-link (`?b=`) exporter for 裂罅透镜 (Riven Lens).

Unofficial independent project compatible with WFSim v2 JSON share links (`0` / `1` prefix,
plaintext IDs without depending on `share_order.yaml`). Opens `https://wfsim.app/weapons/<id>?b=<code>`
in the user's browser only on explicit panel button click.

Defaults to Riven-only import (`include_baseline_build=False`) so players can run WFSim's
own build optimizer and Riven gain analysis without hardcoded class mods interfering, while
also supporting exporting a locally optimized build from `BuildOptimizerDialog`.

Note: `?b=` is WFSim's internal share-link format rather than a frozen public API; future
schema or weapon ID updates on WFSim may require updating the mappings in this module.
Zero WFSim source code is reused; uses only Python stdlib (`json`, `zlib`, `base64`).
"""

from __future__ import annotations

import base64
import json
import re
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from src.evaluator import RivenEvaluation, StatEvaluation
from src.riven_parser import RivenData

WFSIM_ORIGIN = "https://wfsim.app"

# Modular Kitgun chamber bases -> WFSim splits them into `<base>_primary` and `<base>_secondary`
_WFSIM_KITGUN_BASES = frozenset(
    {
        "tombfinger",
        "catchmoon",
        "gaze",
        "rattleguts",
        "sporelacer",
        "vermisplicer",
    }
)

# Modular Zaw strike bases -> valid default WFSim weapon YAML IDs (`_one_handed` / `_two_handed`)
_WFSIM_ZAW_DEFAULT_IDS: Dict[str, str] = {
    "balla": "balla_one_handed",
    "cyath": "cyath_two_handed",
    "dehtat": "dehtat_one_handed",
    "dokrahm": "dokrahm_two_handed",
    "kronsh": "kronsh_two_handed",
    "mewan": "mewan_two_handed",
    "ooltha": "ooltha_one_handed",
    "plague_keewar": "plague_keewar_two_handed",
    "plague_kripath": "plague_kripath_two_handed",
    "rabvee": "rabvee_two_handed",
    "sepfahn": "sepfahn_one_handed",
}

# Explicit slug/ID overrides where WFSim ID differs from Warframe.market / simple snake_case
_WFSIM_WEAPON_ID_OVERRIDES: Dict[str, str] = {
    "ack_brunt": "ack_and_brunt",
    "cobra_crane": "cobra_and_crane",
    "cobra_crane_prime": "cobra_and_crane_prime",
    "silva_aegis": "silva_and_aegis",
    "silva_aegis_prime": "silva_and_aegis_prime",
    "dark_split_sword_(dual_swords)": "dark_split_sword",
    "dark_split_sword_dual_swords": "dark_split_sword",
    "vinquibus_melee": "vinquibus",
}

# Canonical Simplified Chinese stat name -> WFSim ranged stat ID (`rifle`/`shotgun`/`pistol`/`archgun`)
_STAT_ZH_TO_WFSIM_RANGED: Dict[str, str] = {
    "伤害": "damage",
    "暴击几率": "critical_chance",
    "暴击伤害": "critical_damage",
    "多重射击": "multishot",
    "射速": "fire_rate",
    "火焰伤害": "heat",
    "冰冻伤害": "cold",
    "电击伤害": "electricity",
    "毒素伤害": "toxin",
    "病毒伤害": "viral",
    "腐蚀伤害": "corrosive",
    "辐射伤害": "radiation",
    "磁力伤害": "magnetic",
    "毒气伤害": "gas",
    "爆炸伤害": "blast",
    "冲击伤害": "impact",
    "穿刺伤害": "puncture",
    "切割伤害": "slash",
    "触发几率": "status_chance",
    "触发时间": "status_duration",
    "异常状态伤害": "status_damage",
    "装填速度": "reload_speed",
    "弹匣容量": "magazine_capacity",
    "弹药最大值": "ammo_maximum",
    "弹药效率": "ammo_efficiency",
    "收起武器时弹匣每秒自动装填": "reload_while_holstered",
    "投射物速度": "projectile_speed",
    "穿透": "punch_through",
    "变焦": "zoom",
    "武器后坐力": "weapon_recoil",
    "弱点伤害": "weak_point_damage",
    "弱点暴击几率": "weak_point_critical_chance",
    "对 Grineer 伤害": "damage_to_grineer",
    "对 Corpus 伤害": "damage_to_corpus",
    "对 Infested 伤害": "damage_to_infested",
    "对奥罗金伤害": "damage_to_orokin",
    "对炽蛇军伤害": "damage_to_scaldra",
    "对科腐者伤害": "damage_to_techrot",
}

# Canonical Simplified Chinese stat name -> WFSim melee stat ID
_STAT_ZH_TO_WFSIM_MELEE: Dict[str, str] = {
    "伤害": "melee_damage",
    "暴击几率": "critical_chance",
    "暴击伤害": "critical_damage",
    "攻击速度": "attack_speed",
    "火焰伤害": "heat",
    "冰冻伤害": "cold",
    "电击伤害": "electricity",
    "毒素伤害": "toxin",
    "病毒伤害": "viral",
    "腐蚀伤害": "corrosive",
    "辐射伤害": "radiation",
    "磁力伤害": "magnetic",
    "毒气伤害": "gas",
    "爆炸伤害": "blast",
    "冲击伤害": "impact",
    "穿刺伤害": "puncture",
    "切割伤害": "slash",
    "触发几率": "status_chance",
    "触发时间": "status_duration",
    "异常状态伤害": "status_damage",
    "攻击范围": "range",
    "连击持续时间": "combo_duration",
    "初始连击": "initial_combo",
    "额外连击几率": "additional_combo_count_chance",
    "重击效率": "heavy_attack_efficiency",
    "重击准备速度": "heavy_attack_wind_up_speed",
    "在重击时的近战伤害": "melee_damage_on_heavy_attack",
    "震地攻击伤害": "slam_attack_damage",
    "招架角度": "parry_angle",
    "处决伤害": "finisher_damage",
    "滑行攻击暴击几率": "critical_chance_for_slide_attack",
    "对 Grineer 伤害": "damage_to_grineer",
    "对 Corpus 伤害": "damage_to_corpus",
    "对 Infested 伤害": "damage_to_infested",
    "对奥罗金伤害": "damage_to_orokin",
    "对炽蛇军伤害": "damage_to_scaldra",
    "对科腐者伤害": "damage_to_techrot",
}

# Curated Steel Path baseline builds (7 standard mods; slot 8 is our Riven "~0", slot 9 is Exilus)
BASELINE_BUILDS: Dict[str, Tuple[List[str], List[str], str]] = {
    "rifle": (
        [
            "galvanized_chamber",
            "critical_delay",
            "vital_sense",
            "galvanized_aptitude",
            "malignant_force",
            "rime_rounds",
            "hellfire",
        ],
        ["primary_merciless"],
        "步枪钢铁满配基准",
    ),
    "rifle_non_crit": (
        [
            "galvanized_chamber",
            "serration",
            "vile_acceleration",
            "galvanized_aptitude",
            "malignant_force",
            "rime_rounds",
            "hellfire",
        ],
        ["primary_merciless"],
        "灵化非暴吞噬步枪基准",
    ),
    "shotgun": (
        [
            "galvanized_hell",
            "critical_deceleration",
            "primed_ravage",
            "galvanized_savvy",
            "toxic_barrage",
            "chilling_reload",
            "incendiary_coat",
        ],
        ["primary_merciless"],
        "霰弹钢铁满配基准",
    ),
    "shotgun_non_crit": (
        [
            "galvanized_hell",
            "primed_point_blank",
            "galvanized_savvy",
            "toxic_barrage",
            "chilling_reload",
            "incendiary_coat",
            "primed_charged_shell",
        ],
        ["primary_merciless"],
        "灵化非暴吞噬霰弹基准",
    ),
    "pistol": (
        [
            "galvanized_diffusion",
            "creeping_bullseye",
            "primed_target_cracker",
            "lethal_torrent",
            "pistol_pestilence",
            "frostbite",
            "primed_heated_charge",
        ],
        ["secondary_merciless"],
        "手枪钢铁满配基准",
    ),
    "pistol_non_crit": (
        [
            "galvanized_diffusion",
            "hornet_strike",
            "lethal_torrent",
            "galvanized_shot",
            "pistol_pestilence",
            "frostbite",
            "primed_heated_charge",
        ],
        ["secondary_merciless"],
        "灵化非暴吞噬手枪基准",
    ),
    "melee": (
        [
            "condition_overload",
            "blood_rush",
            "organ_shatter",
            "weeping_wounds",
            "berserker_fury",
            "primed_reach",
            "shocking_touch",
        ],
        ["melee_influence"],
        "近战钢铁电击影响基准",
    ),
    "archgun": (
        [
            "primed_rubedo_lined_barrel",
            "primed_dual_rounds",
            "parallax_scope",
            "hollowed_bullets",
            "automatic_trigger",
            "venomous_clip",
            "polar_magazine",
        ],
        [],
        "空战枪械满配基准",
    ),
}


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _deflate_raw(raw: bytes) -> bytes:
    comp = zlib.compressobj(level=9, method=zlib.DEFLATED, wbits=-15)
    return comp.compress(raw) + comp.flush()


def encode_wfsim_share_code(payload: list) -> str:
    """Encode a WFSim v2 positional build tuple into the shortest plain/deflate `?b=` token."""
    raw_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    compressed = _deflate_raw(raw_json)
    if len(compressed) < len(raw_json):
        return "1" + _b64url_encode(compressed)
    return "0" + _b64url_encode(raw_json)


def decode_wfsim_share_code(code: str) -> list:
    """Decode a v2 plain (`0...`) or raw-deflate (`1...`) WFSim `?b=` token back to its payload."""
    if not code or code[0] not in ("0", "1"):
        raise ValueError("Unsupported WFSim share code prefix")
    body = code[1:]
    pad = "=" * ((4 - len(body) % 4) % 4)
    raw = base64.urlsafe_b64decode(body + pad)
    if code[0] == "1":
        raw = zlib.decompress(raw, wbits=-15)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("Decoded WFSim payload is not a list")
    return data


def map_stat_to_wfsim_id(stat_name: str, weapon_class: str) -> Optional[str]:
    """Map canonical Chinese Riven stat name to WFSim's exact stat identifier."""
    if (weapon_class or "").lower() == "melee":
        return _STAT_ZH_TO_WFSIM_MELEE.get(stat_name)
    return _STAT_ZH_TO_WFSIM_RANGED.get(stat_name)


def compute_wfsim_roll_factor(se: StatEvaluation) -> float:
    """Compute WFSim's 0.900..1.100 roll multiplier directly from observed vs theoretical midpoint.

    Works consistently for positive stats, normal negative stats, and inverted god-negatives.
    """
    if se.mid_value is not None:
        if se.sign == "x":
            obs_mag = abs(float(se.value) - 1.0)
            mid_mag = abs(float(se.mid_value) - 1.0)
        else:
            obs_mag = abs(float(se.value))
            mid_mag = abs(float(se.mid_value))
        if mid_mag > 1e-6:
            ratio = obs_mag / mid_mag
            return round(max(0.900, min(1.100, ratio)), 3)

    if se.roll_pct is not None:
        pct = max(0.0, min(100.0, float(se.roll_pct)))
        if se.is_negative and not se.is_inverted_god_neg:
            # Smaller penalty magnitude -> higher roll_pct -> lower WFSim roll factor (0.900)
            ratio = 1.100 - (pct / 100.0) * 0.200
        else:
            ratio = 0.900 + (pct / 100.0) * 0.200
        return round(max(0.900, min(1.100, ratio)), 3)

    return 1.000


def resolve_wfsim_weapon_id(
    evaluation: RivenEvaluation,
    riven: Optional[RivenData] = None,
    *,
    kitgun_slot: Optional[str] = None,
) -> str:
    """Resolve WFSim's exact weapon ID (handling Kitguns, Zaws, `&` names, and special slugs)."""
    raw_id = ""
    slug = (evaluation.weapon_slug or "").strip().lower()
    en = (evaluation.weapon_name_en or "").strip().lower()
    if slug:
        raw_id = re.sub(r"[\s-]+", "_", slug)
    elif en:
        # Preserve `&` as `_and_` (e.g. "Silva & Aegis Prime" -> "silva_and_aegis_prime")
        en_norm = en.replace("&", " and ")
        raw_id = re.sub(r"[^a-z0-9]+", "_", en_norm).strip("_")
    else:
        raw = (riven.weapon_name if riven else evaluation.weapon_name) or ""
        if raw and raw.isascii():
            raw_norm = raw.strip().lower().replace("&", " and ")
            raw_id = re.sub(r"[^a-z0-9]+", "_", raw_norm).strip("_")

    if not raw_id:
        return ""

    if raw_id in _WFSIM_WEAPON_ID_OVERRIDES:
        return _WFSIM_WEAPON_ID_OVERRIDES[raw_id]

    if raw_id in _WFSIM_KITGUN_BASES:
        slot = (kitgun_slot or "").strip().lower()
        if slot not in ("primary", "secondary"):
            cls = (evaluation.weapon_class or "").strip().lower()
            slot = "primary" if cls in ("rifle", "shotgun") else "secondary"
        return f"{raw_id}_{slot}"

    if raw_id in _WFSIM_ZAW_DEFAULT_IDS:
        return _WFSIM_ZAW_DEFAULT_IDS[raw_id]

    return raw_id


def _select_baseline_build(evaluation: RivenEvaluation) -> Tuple[List[str], List[str], str]:
    cls = (evaluation.weapon_class or "rifle").strip().lower()
    if evaluation.matched_archetype_id == "non_crit_devour":
        key = f"{cls}_non_crit"
        if key in BASELINE_BUILDS:
            return BASELINE_BUILDS[key]
    return BASELINE_BUILDS.get(cls, BASELINE_BUILDS["rifle"])


@dataclass
class WFSimLinkResult:
    """Structured result of building a WFSim browser deep-link."""

    ok: bool
    url: str = ""
    weapon_id: str = ""
    build_label: str = ""
    included_stats: List[Tuple[str, float]] = field(default_factory=list)
    skipped_stats: List[str] = field(default_factory=list)
    error: str = ""


def build_wfsim_url(
    evaluation: RivenEvaluation,
    riven: Optional[RivenData] = None,
    *,
    include_baseline_build: bool = False,
    custom_mods: Optional[Sequence[str]] = None,
    custom_arcanes: Optional[Sequence[str]] = None,
    custom_build_label: Optional[str] = None,
    kitgun_slot: Optional[str] = None,
    origin: str = WFSIM_ORIGIN,
) -> WFSimLinkResult:
    """Build a WFSim v2 JSON (`0`/`1` prefix) share URL `https://wfsim.app/weapons/<id>?b=<code>`.

    Defaults to Riven-only import (`include_baseline_build=False`) so WFSim's in-site
    optimizer & Riven gain analysis work cleanly without hardcoded class mods.
    """
    weapon_id = resolve_wfsim_weapon_id(evaluation, riven, kitgun_slot=kitgun_slot)
    if not weapon_id:
        return WFSimLinkResult(
            ok=False,
            error="无法解析该武器的英文 WFSim ID，请先识别已收录武器。",
        )

    w_cls = (evaluation.weapon_class or "rifle").strip().lower()
    bonuses: List[List[object]] = []
    malus: object = 0
    included: List[Tuple[str, float]] = []
    skipped: List[str] = []

    for se in evaluation.stat_evaluations:
        sid = map_stat_to_wfsim_id(se.stat_name, w_cls)
        if not sid:
            skipped.append(se.stat_name)
            continue
        roll = compute_wfsim_roll_factor(se)
        if se.is_negative:
            if malus == 0:
                malus = [sid, roll]
                included.append((f"-{sid}", roll))
        else:
            if len(bonuses) < 3:
                bonuses.append([sid, roll])
                included.append((f"+{sid}", roll))

    # Derive WFSim Riven shape ("3+1", "3", "2+1", "2")
    n_pos = len(bonuses)
    has_neg = malus != 0
    if n_pos >= 3:
        shape = "3+1" if has_neg else "3"
    elif n_pos == 2:
        shape = "2+1" if has_neg else "2"
    else:
        shape = "2+1" if has_neg else "2"

    base_mods, arcanes, build_label = _select_baseline_build(evaluation)
    if custom_mods is not None:
        base_mods = [str(m).strip() for m in custom_mods if str(m).strip()][:7]
        arcanes = [str(a).strip() for a in (custom_arcanes or []) if str(a).strip()]
        build_label = custom_build_label or "本地优化配装"
        slots9: List[object] = [[m] for m in base_mods]
        while len(slots9) < 7:
            slots9.append(0)
        slots9.append(["~0"] if bonuses else 0)
        slots9.append(0)  # Exilus
    elif include_baseline_build:
        slots9 = [[m] for m in base_mods[:7]]
        while len(slots9) < 7:
            slots9.append(0)
        slots9.append(["~0"] if bonuses else 0)
        slots9.append(0)  # Exilus
    else:
        slots9 = [["~0"] if bonuses else 0, 0, 0, 0, 0, 0, 0, 0, 0]
        arcanes = []
        build_label = "仅导入紫卡"

    riven_title = (
        f"{evaluation.weapon_name_en or weapon_id} Riven"
        if not (riven and riven.riven_name and riven.riven_name.isascii())
        else riven.riven_name
    )
    rivens_field = (
        [[riven_title, shape, 8, "M", bonuses, malus]]
        if bonuses
        else []
    )

    payload = [
        2,              # schema version
        weapon_id,      # WFSim weapon id
        "Riven Lens",   # build preset label
        slots9,         # 9 slots (7 baseline mods + "~0" Riven + empty Exilus)
        list(arcanes),  # baseline arcane
        [],             # evolutions (let WFSim defaults apply)
        rivens_field,   # custom Riven carrying exact stat IDs & roll factors
        0,              # scenario (untouched)
        0,              # measurement (untouched)
    ]

    code = encode_wfsim_share_code(payload)
    base = (origin or WFSIM_ORIGIN).rstrip("/")
    url = f"{base}/weapons/{weapon_id}?b={code}"
    return WFSimLinkResult(
        ok=True,
        url=url,
        weapon_id=weapon_id,
        build_label=build_label,
        included_stats=included,
        skipped_stats=skipped,
    )
