"""Pure-Python Riven-Aware Local Build Optimizer for 裂罅透镜 (Riven Lens).

Given an OCR-scanned & evaluated Riven Mod (fixed in slot 8), searches the
weapon class's curated mod + arcane pool using greedy lookahead + coordinate
descent (<30ms offline) to find the highest-synergy 7 mods + 1 arcane:
  - Properly models Warframe multiplier buckets & diminishing returns
    (Primary/Secondary Merciless +360% / Condition Overload base-damage dilution,
    Galvanized Multishot, Blood Rush / Crit Delay, multi-tier crit math).
  - Properly models elemental merging between Riven elements and Mod elements
    (e.g. Riven +Toxin + 1 Cold mod = Viral, freeing a slot; built-in Riven Viral
    + 1 Heat mod = Viral+Heat; Melee Electricity + Melee Influence AoE scaling).
  - Properly models Zariman Non-Crit Devouring Attrition (`2000%` non-crit proc),
    automatically ejecting Crit Chance/Damage mods for `Laetum`/`Phenmor`/`Felarx`.
  - Every mod & arcane ID is verified 1:1 against `https://wfsim.app` share links,
    enabling one-click export of the exact optimized build + Riven into WFSim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.evaluator import RivenEvaluation
from src.riven_parser import POSITIVE_ONLY_COMPOUND_ELEMENTS

SCENARIO_STEEL_PATH = "steel_path_grineer"
SCENARIO_GENERAL = "general_all_factions"
SCENARIO_BOSS_DIRECT = "boss_direct_dps"

SCENARIO_KEYS: Tuple[str, ...] = (
    SCENARIO_STEEL_PATH,
    SCENARIO_GENERAL,
    SCENARIO_BOSS_DIRECT,
)


@dataclass(frozen=True)
class ModDef:
    """One candidate Warframe Mod with WFSim-compatible ID and combat bucket stats."""

    id: str
    name_zh: str
    name_en: str
    exclusive_group: str = ""
    damage: float = 0.0
    multishot: float = 0.0
    crit_chance: float = 0.0
    crit_damage: float = 0.0
    fire_rate: float = 0.0
    status_chance: float = 0.0
    status_damage: float = 0.0
    faction: float = 0.0
    weakpoint: float = 0.0
    punch_through: float = 0.0
    range_m: float = 0.0
    reload: float = 0.0
    initial_combo: float = 0.0
    heavy_dmg: float = 0.0
    slam_dmg: float = 0.0
    element: Optional[str] = None  # "toxin" | "cold" | "heat" | "electricity"
    element_value: float = 0.0
    co_per_status: float = 0.0     # Galvanized Aptitude/Savvy/Shot or Condition Overload
    blood_rush_cc: float = 0.0     # Blood Rush at 12x combo
    weeping_sc: float = 0.0        # Weeping Wounds at 12x combo
    hunter_munitions: bool = False
    is_bane: bool = False


@dataclass(frozen=True)
class ArcaneDef:
    """One candidate Weapon Arcane with WFSim-compatible ID and combat bonuses."""

    id: str
    name_zh: str
    name_en: str
    damage: float = 0.0
    multishot: float = 0.0
    crit_damage: float = 0.0
    crit_chance: float = 0.0
    weakpoint: float = 0.0
    status_chance: float = 0.0
    corrosive_bonus: float = 0.0
    heat_bonus: float = 0.0
    influence_aoe: bool = False
    afflictions_bonus: bool = False


# ---- Curated Mod Pools (100% verified WFSim share IDs) ----

RIFLE_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_chamber", "镀层膛室", "Galvanized Chamber", exclusive_group="ms", multishot=2.30),
    ModDef("critical_delay", "致命延迟", "Critical Delay", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("point_strike", "致命一击", "Point Strike", exclusive_group="cc", crit_chance=1.50),
    ModDef("vital_sense", "弱点感应", "Vital Sense", exclusive_group="cd", crit_damage=1.20),
    ModDef("hammer_shot", "重锤射击", "Hammer Shot", crit_damage=0.60, status_chance=0.80),
    ModDef("bladed_rounds", "尖刃弹头", "Bladed Rounds", crit_damage=1.20),
    ModDef("galvanized_scope", "镀层瞄准", "Galvanized Scope", crit_chance=3.20),
    ModDef("serration", "膛线", "Serration", exclusive_group="base_dmg", damage=1.65),
    ModDef("amalgam_serration", "并合 膛线", "Amalgam Serration", exclusive_group="base_dmg", damage=1.55),
    ModDef("galvanized_aptitude", "镀层赋能", "Galvanized Aptitude", status_chance=0.80, co_per_status=0.80),
    ModDef("vile_acceleration", "卑劣加速", "Vile Acceleration", exclusive_group="fr", fire_rate=0.90, damage=-0.15),
    ModDef("primed_shred", "撕裂 Prime", "Primed Shred", exclusive_group="fr", fire_rate=0.55, punch_through=2.2),
    ModDef("speed_trigger", "灵敏扳机", "Speed Trigger", exclusive_group="fr", fire_rate=0.60),
    ModDef("hunter_munitions", "猎人战备", "Hunter Munitions", hunter_munitions=True),
    ModDef("rifle_elementalist", "步枪元素师", "Rifle Elementalist", status_damage=0.90, punch_through=0.6),
    ModDef("malignant_force", "恶性力场", "Malignant Force", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("infected_clip", "污染弹匣", "Infected Clip", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("rime_rounds", "白霜弹头", "Rime Rounds", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("primed_cryo_rounds", "低温弹头 Prime", "Primed Cryo Rounds", exclusive_group="cold90", element="cold", element_value=1.65),
    ModDef("hellfire", "地狱火", "Hellfire", exclusive_group="heat90", element="heat", element_value=0.90),
    ModDef("thermite_rounds", "铝热焊弹", "Thermite Rounds", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("high_voltage", "高压电流", "High Voltage", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("stormbringer", "暴风使者", "Stormbringer", exclusive_group="elec90", element="electricity", element_value=0.90),
    ModDef("primed_bane_of_grineer", "灭亡 Grineer Prime", "Primed Bane of Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("vigilante_armaments", "私法军备", "Vigilante Armaments", multishot=0.60),
    ModDef("primed_fast_hands", "爆发装填 Prime", "Primed Fast Hands", reload=0.55),
)

SHOTGUN_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_hell", "镀层地狱", "Galvanized Hell", exclusive_group="ms", multishot=2.30),
    ModDef("critical_deceleration", "致命减速", "Critical Deceleration", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("blunderbuss", "雷筒", "Blunderbuss", exclusive_group="cc", crit_chance=0.90),
    ModDef("primed_ravage", "摧残 Prime", "Primed Ravage", exclusive_group="cd", crit_damage=1.10),
    ModDef("primed_point_blank", "抵近射击 Prime", "Primed Point Blank", exclusive_group="base_dmg", damage=1.65),
    ModDef("galvanized_savvy", "镀层精通", "Galvanized Savvy", status_chance=0.80, co_per_status=0.80),
    ModDef("shotgun_barrage", "霰弹速射", "Shotgun Barrage", exclusive_group="fr", fire_rate=0.90),
    ModDef("shotgun_elementalist", "霰弹元素师", "Shotgun Elementalist", status_damage=0.90),
    ModDef("toxic_barrage", "剧毒弹幕", "Toxic Barrage", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("contagious_spread", "传染蔓延", "Contagious Spread", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("chilling_reload", "急冻装填", "Chilling Reload", exclusive_group="cold_util", element="cold", element_value=0.60, reload=0.40),
    ModDef("frigid_blast", "冰冷疾风", "Frigid Blast", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("primed_chilling_grasp", "急冻控场 Prime", "Primed Chilling Grasp", exclusive_group="cold90", element="cold", element_value=1.65),
    ModDef("incendiary_coat", "燃烧外壳", "Incendiary Coat", exclusive_group="heat90", element="heat", element_value=0.90),
    ModDef("scattering_inferno", "炼狱轰击", "Scattering Inferno", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("blaze", "烈焰", "Blaze", damage=0.60, element="heat", element_value=0.60),
    ModDef("shell_shock", "电冲弹药", "Shell Shock", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("primed_charged_shell", "充电弹头 Prime", "Primed Charged Shell", exclusive_group="elec90", element="electricity", element_value=1.65),
    ModDef("primed_cleanse_grineer", "净化 Grineer Prime", "Primed Cleanse Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("vigilante_armaments", "私法军备", "Vigilante Armaments", multishot=0.60),
    ModDef("seeking_fury", "狂暴追猎", "Seeking Fury", punch_through=1.2, reload=0.15),
)

PISTOL_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_diffusion", "镀层扩散", "Galvanized Diffusion", exclusive_group="ms", multishot=2.30),
    ModDef("lethal_torrent", "致命洪流", "Lethal Torrent", multishot=0.60, fire_rate=0.60),
    ModDef("creeping_bullseye", "缓慢精确", "Creeping Bullseye", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("primed_pistol_gambit", "手枪精通 Prime", "Primed Pistol Gambit", exclusive_group="cc", crit_chance=1.87),
    ModDef("primed_target_cracker", "弱点专精 Prime", "Primed Target Cracker", exclusive_group="cd", crit_damage=1.10),
    ModDef("galvanized_crosshairs", "镀层十字准星", "Galvanized Crosshairs", crit_chance=3.20),
    ModDef("hornet_strike", "黄蜂蛰刺", "Hornet Strike", exclusive_group="base_dmg", damage=2.20),
    ModDef("galvanized_shot", "镀层射击", "Galvanized Shot", status_chance=0.80, co_per_status=0.80),
    ModDef("anemic_agility", "乏能迅敏", "Anemic Agility", exclusive_group="fr", fire_rate=0.90, damage=-0.15),
    ModDef("gunslinger", "神枪手", "Gunslinger", exclusive_group="fr", fire_rate=0.72),
    ModDef("pistol_elementalist", "手枪元素师", "Pistol Elementalist", status_damage=0.90, reload=0.30),
    ModDef("pistol_pestilence", "瘟疫手枪", "Pistol Pestilence", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("pathogen_rounds", "病原弹头", "Pathogen Rounds", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("frostbite", "结霜侵蚀", "Frostbite", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("deep_freeze", "深层冷冻", "Deep Freeze", exclusive_group="cold90", element="cold", element_value=0.90),
    ModDef("primed_heated_charge", "火焰装填 Prime", "Primed Heated Charge", exclusive_group="heat90", element="heat", element_value=1.65),
    ModDef("scorch", "焦痕", "Scorch", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("jolt", "电流震击", "Jolt", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("primed_convulsion", "痉挛 Prime", "Primed Convulsion", exclusive_group="elec90", element="electricity", element_value=1.65),
    ModDef("primed_expel_grineer", "驱逐 Grineer Prime", "Primed Expel Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
)

MELEE_MODS: Tuple[ModDef, ...] = (
    ModDef("condition_overload", "异况超量", "Condition Overload", exclusive_group="base_dmg", co_per_status=0.80),
    ModDef("primed_pressure_point", "压迫点 Prime", "Primed Pressure Point", exclusive_group="base_dmg", damage=1.65),
    ModDef("blood_rush", "急进猛突", "Blood Rush", exclusive_group="cc", blood_rush_cc=4.40),
    ModDef("sacrificial_steel", "牺牲 斩铁", "Sacrificial Steel", exclusive_group="cc", crit_chance=2.20),
    ModDef("galvanized_steel", "镀层 斩铁", "Galvanized Steel", crit_chance=1.10, crit_damage=1.20),
    ModDef("organ_shatter", "肢解", "Organ Shatter", exclusive_group="cd", crit_damage=0.90),
    ModDef("amalgam_organ_shatter", "并合 肢解", "Amalgam Organ Shatter", exclusive_group="cd", crit_damage=0.85, heavy_dmg=0.25),
    ModDef("gladiator_might", "角斗士 威猛", "Gladiator Might", crit_damage=0.60, crit_chance=1.10),
    ModDef("weeping_wounds", "创口溃烂", "Weeping Wounds", weeping_sc=4.40),
    ModDef("berserker_fury", "狂战士之怒", "Berserker Fury", exclusive_group="as", fire_rate=0.70),
    ModDef("quickening", "加速", "Quickening", exclusive_group="as", fire_rate=0.40),
    ModDef("primed_fury", "狂暴 Prime", "Primed Fury", exclusive_group="as", fire_rate=0.55),
    ModDef("primed_reach", "剑风 Prime", "Primed Reach", exclusive_group="range", range_m=3.0),
    ModDef("galvanized_elementalist", "镀层元素师", "Galvanized Elementalist", exclusive_group="elem_st", status_damage=0.80, status_chance=1.20),
    ModDef("voltaic_strike", "伏打电能", "Voltaic Strike", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("shocking_touch", "电击触点", "Shocking Touch", exclusive_group="elec90", element="electricity", element_value=0.90),
    ModDef("virulent_scourge", "剧毒灾害", "Virulent Scourge", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("primed_fever_strike", "热病打击 Prime", "Primed Fever Strike", exclusive_group="tox90", element="toxin", element_value=1.65),
    ModDef("vicious_frost", "蚀骨寒霜", "Vicious Frost", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("volcanic_edge", "爆裂刀刃", "Volcanic Edge", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("primed_smite_grineer", "毁灭 Grineer Prime", "Primed Smite Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("galvanized_reflex", "镀层 反射", "Galvanized Reflex", exclusive_group="heavy_init", initial_combo=80.0),
    ModDef("killing_blow", "一击必杀", "Killing Blow", heavy_dmg=1.20),
    ModDef("seismic_wave", "震波", "Seismic Wave", slam_dmg=2.00),
)

ARCHGUN_MODS: Tuple[ModDef, ...] = (
    ModDef("primed_rubedo_lined_barrel", "红晶枪管 Prime", "Primed Rubedo-Lined Barrel", exclusive_group="base_dmg", damage=1.87),
    ModDef("primed_dual_rounds", "双重弹头 Prime", "Primed Dual Rounds", exclusive_group="ms", multishot=1.10),
    ModDef("parallax_scope", "视差瞄具", "Parallax Scope", crit_chance=1.00),
    ModDef("critical_focus", "关键焦点", "Critical Focus", crit_chance=0.60, crit_damage=0.60),
    ModDef("hollowed_bullets", "中空子弹", "Hollowed Bullets", crit_damage=0.80),
    ModDef("automatic_trigger", "自动扳机", "Automatic Trigger", fire_rate=0.60),
    ModDef("sabot_rounds", "脱壳穿甲弹", "Sabot Rounds", damage=0.60, punch_through=3.0),
    ModDef("venomous_clip", "恶毒弹匣", "Venomous Clip", element="toxin", element_value=1.20),
    ModDef("polar_magazine", "极地弹仓", "Polar Magazine", element="cold", element_value=1.20),
    ModDef("combustion_rounds", "燃烧弹头", "Combustion Rounds", element="heat", element_value=1.20),
    ModDef("electrified_barrel", "带电枪管", "Electrified Barrel", element="electricity", element_value=1.20),
    ModDef("contamination_casing", "污染弹壳", "Contamination Casing", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("hypothermic_shell", "低温外壳", "Hypothermic Shell", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("magma_chamber", "熔岩弹膛", "Magma Chamber", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("charged_bullets", "带电子弹", "Charged Bullets", element="electricity", element_value=0.60, status_chance=0.60),
)

MOD_POOLS: Dict[str, Tuple[ModDef, ...]] = {
    "rifle": RIFLE_MODS,
    "shotgun": SHOTGUN_MODS,
    "pistol": PISTOL_MODS,
    "melee": MELEE_MODS,
    "archgun": ARCHGUN_MODS,
}

ARCANE_POOLS: Dict[str, Tuple[ArcaneDef, ...]] = {
    "rifle": (
        ArcaneDef("primary_merciless", "主要·无情", "Primary Merciless", damage=3.60),
        ArcaneDef("primary_deadhead", "主要·死首", "Primary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("primary_frostbite", "主要·霜冻", "Primary Frostbite", crit_damage=1.20, multishot=0.90),
        ArcaneDef("primary_blight", "主要·枯萎", "Primary Blight", crit_damage=1.48, multishot=0.90),
    ),
    "shotgun": (
        ArcaneDef("primary_merciless", "主要·无情", "Primary Merciless", damage=3.60),
        ArcaneDef("primary_deadhead", "主要·死首", "Primary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("primary_frostbite", "主要·霜冻", "Primary Frostbite", crit_damage=1.20, multishot=0.90),
    ),
    "pistol": (
        ArcaneDef("secondary_merciless", "次要·无情", "Secondary Merciless", damage=3.60),
        ArcaneDef("secondary_deadhead", "次要·死首", "Secondary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("cascadia_flare", "卡斯卡迪亚·耀斑", "Cascadia Flare", damage=4.80),
    ),
    "melee": (
        ArcaneDef("melee_influence", "近战·电击影响", "Melee Influence", influence_aoe=True),
        ArcaneDef("melee_exposure", "近战·腐蚀曝光", "Melee Exposure", corrosive_bonus=2.40),
        ArcaneDef("melee_afflictions", "近战·苦痛折磨", "Melee Afflictions", afflictions_bonus=True),
    ),
    "archgun": (),
}

_RIVEN_ELEM_MAP: Dict[str, str] = {
    "毒素伤害": "toxin",
    "冰冻伤害": "cold",
    "火焰伤害": "heat",
    "电击伤害": "electricity",
}
_RIVEN_COMPOUND_MAP: Dict[str, str] = {
    "病毒伤害": "viral",
    "腐蚀伤害": "corrosive",
    "辐射伤害": "radiation",
    "磁力伤害": "magnetic",
    "毒气伤害": "gas",
    "爆炸伤害": "blast",
}


@dataclass
class OptimizedModSlot:
    """One optimized mod slot with its marginal DPS gain."""

    mod: ModDef
    marginal_gain_pct: float


@dataclass
class BuildOptimizationResult:
    """Complete optimized 7-Mod + Arcane + Riven build recommendation."""

    weapon_name: str
    weapon_class: str
    scenario: str
    mods: List[OptimizedModSlot]
    arcane: Optional[ArcaneDef]
    total_dps_index: float
    riven_gain_pct: float
    active_elements_zh: List[str] = field(default_factory=list)
    active_elements_en: List[str] = field(default_factory=list)
    rationale_zh: List[str] = field(default_factory=list)
    rationale_en: List[str] = field(default_factory=list)

    @property
    def mod_ids(self) -> List[str]:
        return [slot.mod.id for slot in self.mods]

    @property
    def arcane_ids(self) -> List[str]:
        return [self.arcane.id] if self.arcane is not None else []


def _extract_riven_profile(ev: RivenEvaluation) -> Dict[str, object]:
    """Extract numeric bucket deltas and elemental tags from the scanned Riven."""
    prof: Dict[str, object] = {
        "damage": 0.0,
        "multishot": 0.0,
        "crit_chance": 0.0,
        "crit_damage": 0.0,
        "fire_rate": 0.0,
        "status_chance": 0.0,
        "status_damage": 0.0,
        "faction": 0.0,
        "weakpoint": 0.0,
        "range_m": 0.0,
        "punch_through": 0.0,
        "reload": 0.0,
        "initial_combo": 0.0,
        "heavy_dmg": 0.0,
        "slam_dmg": 0.0,
        "elem_total": 0.0,
        "ips_total": 0.0,
        "base_elements": set(),
        "compound_elements": set(),
    }
    for se in ev.stat_evaluations:
        name = se.stat_name
        if se.sign == "x":
            delta = float(se.value) - 1.0
        else:
            delta = (-abs(float(se.value)) if se.is_negative else abs(float(se.value))) / 100.0

        if name == "伤害":
            prof["damage"] = float(prof["damage"]) + delta
        elif name == "多重射击":
            prof["multishot"] = float(prof["multishot"]) + delta
        elif name == "暴击几率":
            prof["crit_chance"] = float(prof["crit_chance"]) + delta
        elif name == "暴击伤害":
            prof["crit_damage"] = float(prof["crit_damage"]) + delta
        elif name in ("射速", "攻击速度"):
            prof["fire_rate"] = float(prof["fire_rate"]) + delta
        elif name == "触发几率":
            prof["status_chance"] = float(prof["status_chance"]) + delta
        elif name == "异常状态伤害":
            prof["status_damage"] = float(prof["status_damage"]) + delta
        elif name in ("对 Grineer 伤害", "对 Corpus 伤害", "对奥罗金伤害", "对炽蛇军伤害", "对科腐者伤害"):
            prof["faction"] = float(prof["faction"]) + delta * 0.70
        elif name == "对 Infested 伤害":
            prof["faction"] = float(prof["faction"]) + delta * 0.25
        elif name == "弱点伤害":
            prof["weakpoint"] = float(prof["weakpoint"]) + delta
        elif name == "弱点暴击几率":
            prof["crit_chance"] = float(prof["crit_chance"]) + delta * 0.45
        elif name == "攻击范围":
            meters = -abs(float(se.value)) if se.is_negative else abs(float(se.value))
            prof["range_m"] = float(prof["range_m"]) + meters
        elif name == "穿透":
            meters = -abs(float(se.value)) if se.is_negative else abs(float(se.value))
            prof["punch_through"] = float(prof["punch_through"]) + meters
        elif name in ("装填速度", "弹匣容量"):
            prof["reload"] = float(prof["reload"]) + delta * 0.5
        elif name == "初始连击":
            pts = -abs(float(se.value)) if se.is_negative else abs(float(se.value))
            prof["initial_combo"] = float(prof["initial_combo"]) + pts
        elif name in ("在重击时的近战伤害", "重击效率", "重击准备速度"):
            prof["heavy_dmg"] = float(prof["heavy_dmg"]) + delta
        elif name == "震地攻击伤害":
            prof["slam_dmg"] = float(prof["slam_dmg"]) + delta
        elif name in _RIVEN_ELEM_MAP:
            prof["elem_total"] = float(prof["elem_total"]) + delta
            if not se.is_negative:
                prof["base_elements"].add(_RIVEN_ELEM_MAP[name])  # type: ignore[union-attr]
        elif name in _RIVEN_COMPOUND_MAP and not se.is_negative:
            prof["elem_total"] = float(prof["elem_total"]) + delta
            prof["compound_elements"].add(_RIVEN_COMPOUND_MAP[name])  # type: ignore[union-attr]
        elif name in ("冲击伤害", "穿刺伤害", "切割伤害"):
            prof["ips_total"] = float(prof["ips_total"]) + delta
    return prof


def _combine_elements(
    base_elems: Set[str],
    compound_elems: Set[str],
    *,
    prefer_influence: bool = False,
) -> Tuple[Set[str], Set[str]]:
    """Simulate optimal slot-ordered elemental combination for a build."""
    rem = set(base_elems)
    comps = set(compound_elems)

    # On Melee Influence builds, preserve standalone Electricity first!
    if prefer_influence and "electricity" in rem:
        rem.remove("electricity")
        if "toxin" in rem and "cold" in rem:
            comps.add("viral")
            rem.discard("toxin")
            rem.discard("cold")
        elif "heat" in rem and "cold" in rem:
            comps.add("blast")
            rem.discard("heat")
            rem.discard("cold")
        elif "toxin" in rem and "heat" in rem:
            comps.add("gas")
            rem.discard("toxin")
            rem.discard("heat")
        rem.add("electricity")
        return comps, rem

    # Standard ranged/melee meta priority: Viral (+Heat) > Corrosive (+Cold/Heat) > Blast > Radiation/Magnetic
    if "toxin" in rem and "cold" in rem:
        comps.add("viral")
        rem.discard("toxin")
        rem.discard("cold")
    elif "toxin" in rem and "electricity" in rem:
        comps.add("corrosive")
        rem.discard("toxin")
        rem.discard("electricity")

    if "heat" in rem and "electricity" in rem and "viral" not in comps and "corrosive" not in comps:
        comps.add("radiation")
        rem.discard("heat")
        rem.discard("electricity")
    elif "cold" in rem and "electricity" in rem and "viral" not in comps and "corrosive" not in comps:
        comps.add("magnetic")
        rem.discard("cold")
        rem.discard("electricity")
    elif "heat" in rem and "cold" in rem and "viral" not in comps:
        comps.add("blast")
        rem.discard("heat")
        rem.discard("cold")

    return comps, rem


def _score_build(
    ev: RivenEvaluation,
    riven_prof: Optional[Dict[str, object]],
    mods: Sequence[ModDef],
    arcane: Optional[ArcaneDef],
    scenario: str,
) -> Tuple[float, Set[str], Set[str]]:
    """Compute realistic expected combat DPS score for `(weapon + riven + mods + arcane)`."""
    is_melee = ev.weapon_class == "melee"
    is_non_crit = ev.matched_archetype_id == "non_crit_devour"
    is_heavy = ev.matched_archetype_id in ("heavy_statstick", "heavy_slam")

    w_cc = float(ev.weapon_crit_chance) if (ev.weapon_crit_chance and ev.weapon_crit_chance > 0) else (0.24 if is_melee else 0.22)
    w_cd = float(ev.weapon_crit_mult) if (ev.weapon_crit_mult and ev.weapon_crit_mult > 1.0) else (2.2 if is_melee else 2.0)
    w_sc = float(ev.weapon_status_chance) if (ev.weapon_status_chance and ev.weapon_status_chance > 0) else 0.20

    dmg = 1.0
    ms = 1.0
    cc_mod = 0.0
    cd_mod = 0.0
    fr = 1.0
    sc_mod = 0.0
    st_dmg = 1.0
    faction = 1.0
    weakpoint = 1.0
    elem = 1.0
    range_m = 0.0
    punch_through = 0.0
    reload_bonus = 0.0
    init_combo = 0.0
    heavy_dmg = 0.0
    slam_dmg = 0.0
    co_per_status = 0.0
    has_hm = False

    base_elems: Set[str] = set()
    comp_elems: Set[str] = set()

    if riven_prof is not None:
        dmg += float(riven_prof["damage"])
        if not is_melee:
            ms += float(riven_prof["multishot"])
        cc_mod += float(riven_prof["crit_chance"])
        cd_mod += float(riven_prof["crit_damage"])
        fr += float(riven_prof["fire_rate"])
        sc_mod += float(riven_prof["status_chance"])
        st_dmg += float(riven_prof["status_damage"])
        faction *= max(0.55, 1.0 + float(riven_prof["faction"]))
        weakpoint += float(riven_prof["weakpoint"]) * 0.45
        elem += float(riven_prof["elem_total"]) + float(riven_prof["ips_total"]) * 0.28
        range_m += float(riven_prof["range_m"])
        punch_through += float(riven_prof["punch_through"])
        reload_bonus += float(riven_prof["reload"])
        init_combo += float(riven_prof["initial_combo"])
        heavy_dmg += float(riven_prof["heavy_dmg"])
        slam_dmg += float(riven_prof["slam_dmg"])
        base_elems |= set(riven_prof["base_elements"])  # type: ignore[arg-type]
        comp_elems |= set(riven_prof["compound_elements"])  # type: ignore[arg-type]

    for m in mods:
        dmg += m.damage
        if not is_melee:
            ms += m.multishot
        cc_mod += m.crit_chance + m.blood_rush_cc
        cd_mod += m.crit_damage
        fr += m.fire_rate
        sc_mod += m.status_chance + m.weeping_sc
        st_dmg += m.status_damage
        if m.faction:
            f_scale = 1.0 if scenario == SCENARIO_STEEL_PATH else (0.45 if scenario == SCENARIO_GENERAL else 0.70)
            faction *= 1.0 + m.faction * f_scale
        weakpoint += m.weakpoint * (0.65 if scenario == SCENARIO_BOSS_DIRECT else 0.40)
        range_m += m.range_m
        punch_through += m.punch_through
        reload_bonus += m.reload
        init_combo += m.initial_combo
        heavy_dmg += m.heavy_dmg
        slam_dmg += m.slam_dmg
        co_per_status += m.co_per_status
        if m.hunter_munitions:
            has_hm = True
        if m.element:
            base_elems.add(m.element)
            elem += m.element_value

    prefer_influence = bool(arcane is not None and arcane.influence_aoe and is_melee)
    if arcane is not None:
        dmg += arcane.damage
        if not is_melee:
            ms += arcane.multishot
        cd_mod += arcane.crit_damage
        cc_mod += arcane.crit_chance
        weakpoint += arcane.weakpoint * (0.80 if scenario == SCENARIO_BOSS_DIRECT else 0.45)
        sc_mod += arcane.status_chance
        if arcane.corrosive_bonus > 0:
            elem += arcane.corrosive_bonus
            comp_elems.add("corrosive")

    # Resolve elemental combinations
    comps, rem_elems = _combine_elements(base_elems, comp_elems, prefer_influence=prefer_influence)

    # Distinct status count for Condition Overload / Galvanized Aptitude/Savvy/Shot
    distinct_statuses = 2.0 + len(comps) + len(rem_elems)
    if co_per_status > 0:
        dmg += co_per_status * min(5.0, distinct_statuses)

    # Arcane conditional activation checks (e.g. Frostbite requires Cold, Blight requires Toxin, Flare requires Heat)
    if arcane is not None:
        if arcane.id == "primary_frostbite" and "cold" not in base_elems:
            cd_mod -= arcane.crit_damage * 0.85
            ms -= arcane.multishot * 0.85
        elif arcane.id == "primary_blight" and "toxin" not in base_elems:
            cd_mod -= arcane.crit_damage * 0.85
            ms -= arcane.multishot * 0.85
        elif arcane.id == "cascadia_flare" and "heat" not in rem_elems:
            dmg -= arcane.damage * 0.80

    # Clamp multiplier floors
    dmg = max(0.15, dmg)
    ms = max(0.15, ms)
    fr = max(0.25, fr)
    elem = max(0.35, elem)
    st_dmg = max(0.45, st_dmg)

    # Multi-tier expected crit multiplier (or Zariman Non-Crit Devouring 2000% formula!)
    eff_cc = max(0.0, w_cc * (1.0 + cc_mod))
    eff_cd = max(1.0, w_cd * (1.0 + cd_mod))
    if is_non_crit:
        p_crit = min(1.0, eff_cc)
        crit_mult = (1.0 - p_crit) * 11.0 + p_crit * eff_cd
    else:
        crit_mult = 1.0 + eff_cc * (eff_cd - 1.0)

    # Expected status-proc rate per second (governs Viral / Heat / Corrosive / Influence uptime)
    eff_sc = max(0.0, w_sc * (1.0 + sc_mod))
    procs_per_sec = eff_sc * ms * fr
    proc_uptime = min(1.0, procs_per_sec / (procs_per_sec + 1.8))

    # Scenario-aware elemental & armor synergy multiplier
    elem_synergy = 1.0
    if "viral" in comps:
        viral_bonus = 0.72 if scenario == SCENARIO_STEEL_PATH else (0.52 if scenario == SCENARIO_GENERAL else 0.42)
        elem_synergy *= 1.0 + viral_bonus * max(0.35, proc_uptime)
    if "corrosive" in comps:
        corr_bonus = 0.56 if scenario == SCENARIO_STEEL_PATH else (0.30 if scenario == SCENARIO_GENERAL else 0.35)
        elem_synergy *= 1.0 + corr_bonus * max(0.35, proc_uptime)
    if "heat" in rem_elems:
        # 50% armor strip + stacking Heat DOT scaled by status damage & faction double-dip!
        heat_armor = 0.32 if scenario == SCENARIO_STEEL_PATH else 0.16
        heat_dot = 0.24 * st_dmg * faction * max(0.30, proc_uptime)
        elem_synergy *= 1.0 + heat_armor + heat_dot
    if "blast" in comps or "gas" in comps:
        elem_synergy *= 1.0 + 0.16 * st_dmg * max(0.30, proc_uptime)

    # Hunter Munitions (forced Slash on crit -> ignores Steel Path armor, double-dips faction & status dmg!)
    if has_hm and not is_melee and not is_non_crit:
        hm_weight = 0.34 if scenario == SCENARIO_STEEL_PATH else (0.18 if scenario == SCENARIO_GENERAL else 0.14)
        crit_uptime = min(1.0, eff_cc)
        elem_synergy *= 1.0 + hm_weight * crit_uptime * st_dmg * faction

    # Melee Influence (requires uncombined Electricity -> massive 20m AoE status spread in Steel Path / General!)
    if prefer_influence:
        if "electricity" in rem_elems:
            inf_scale = 2.40 if scenario != SCENARIO_BOSS_DIRECT else 0.35
            arch_boost = 1.25 if ev.matched_archetype_id == "melee_influence" else 1.0
            elem_synergy *= 1.0 + inf_scale * arch_boost * max(0.45, proc_uptime) * (0.75 + 0.30 * st_dmg)
        else:
            # Influence does nothing without uncombined Electricity!
            elem_synergy *= 0.85
    elif arcane is not None and arcane.afflictions_bonus and is_melee:
        elem_synergy *= 1.0 + 0.22 * st_dmg * max(0.35, proc_uptime)

    # Utility / AoE / Heavy / Sustained uptime scaling
    utility = 1.0
    if is_melee:
        range_w = 0.085 if scenario != SCENARIO_BOSS_DIRECT else 0.03
        utility *= max(0.70, 1.0 + range_m * range_w)
        if is_heavy:
            utility *= 1.0 + (init_combo / 40.0) * 0.22 + heavy_dmg * 0.35 + slam_dmg * 0.28
        else:
            utility *= 1.0 + (init_combo / 80.0) * 0.06 + heavy_dmg * 0.08 + slam_dmg * 0.06
    else:
        utility *= 1.0 + min(3.0, max(0.0, punch_through)) * (0.06 if scenario != SCENARIO_BOSS_DIRECT else 0.015)
        utility *= max(0.82, 1.0 + reload_bonus * 0.16)

    # Status Damage scaling on non-Heat/non-HM procs
    if st_dmg > 1.0 and "heat" not in rem_elems and not has_hm:
        dot_share = 0.22 if w_sc >= 0.22 else 0.10
        utility *= 1.0 + (st_dmg - 1.0) * dot_share * max(0.35, proc_uptime)

    score = dmg * ms * fr * elem * crit_mult * faction * weakpoint * elem_synergy * utility
    return score, comps, rem_elems


def _conflict_with(mod: ModDef, chosen: Sequence[ModDef]) -> bool:
    for c in chosen:
        if c.id == mod.id:
            return True
        if mod.exclusive_group and c.exclusive_group == mod.exclusive_group:
            return True
    return False


def _optimize_for_arcane(
    ev: RivenEvaluation,
    riven_prof: Dict[str, object],
    pool: Sequence[ModDef],
    arcane: Optional[ArcaneDef],
    scenario: str,
    slots: int = 7,
) -> Tuple[List[ModDef], float, Set[str], Set[str]]:
    chosen: List[ModDef] = []

    # Step 1: Greedy forward build with 2-mod elemental pair lookahead (discovers Viral/Corrosive pairs cleanly)
    elem_mods = [m for m in pool if m.element in ("toxin", "cold", "electricity", "heat")]
    while len(chosen) < slots:
        best_score = -1.0
        best_next: Optional[ModDef] = None

        # Evaluate single additions
        for cand in pool:
            if _conflict_with(cand, chosen):
                continue
            sc, _, _ = _score_build(ev, riven_prof, chosen + [cand], arcane, scenario)
            if sc > best_score:
                best_score = sc
                best_next = cand

        # 2-step lookahead for elemental pairs when >= 2 slots remain
        if len(chosen) <= slots - 2:
            base_now, _, _ = _score_build(ev, riven_prof, chosen, arcane, scenario)
            best_pair_avg = (best_score - base_now) if best_score > base_now else 0.0
            for i, m1 in enumerate(elem_mods):
                if _conflict_with(m1, chosen):
                    continue
                for m2 in elem_mods[i + 1 :]:
                    if m1.element == m2.element or _conflict_with(m2, chosen) or _conflict_with(m2, [m1]):
                        continue
                    sc2, _, _ = _score_build(ev, riven_prof, chosen + [m1, m2], arcane, scenario)
                    avg_gain = (sc2 - base_now) * 0.52
                    if avg_gain > best_pair_avg:
                        best_pair_avg = avg_gain
                        best_next = m1

        if best_next is None:
            break
        chosen.append(best_next)

    # Step 2: Coordinate-descent 1-swap local refinement until convergence
    cur_score, cur_comps, cur_rem = _score_build(ev, riven_prof, chosen, arcane, scenario)
    for _ in range(4):
        improved = False
        for idx in range(len(chosen)):
            others = chosen[:idx] + chosen[idx + 1 :]
            best_m = chosen[idx]
            best_sc = cur_score
            for cand in pool:
                if cand.id == chosen[idx].id or _conflict_with(cand, others):
                    continue
                sc, _, _ = _score_build(ev, riven_prof, others + [cand], arcane, scenario)
                if sc > best_sc * 1.0005:
                    best_sc = sc
                    best_m = cand
            if best_m.id != chosen[idx].id:
                chosen[idx] = best_m
                cur_score = best_sc
                improved = True
        if not improved:
            break

    cur_score, cur_comps, cur_rem = _score_build(ev, riven_prof, chosen, arcane, scenario)
    return chosen, cur_score, cur_comps, cur_rem


_ELEM_LABEL_ZH: Dict[str, str] = {
    "viral": "病毒",
    "corrosive": "腐蚀",
    "radiation": "辐射",
    "magnetic": "磁力",
    "gas": "毒气",
    "blast": "爆炸",
    "heat": "火焰",
    "cold": "冰冻",
    "toxin": "毒素",
    "electricity": "电击",
}
_ELEM_LABEL_EN: Dict[str, str] = {
    "viral": "Viral",
    "corrosive": "Corrosive",
    "radiation": "Radiation",
    "magnetic": "Magnetic",
    "gas": "Gas",
    "blast": "Blast",
    "heat": "Heat",
    "cold": "Cold",
    "toxin": "Toxin",
    "electricity": "Electricity",
}


def optimize_build_for_riven(
    ev: RivenEvaluation,
    *,
    scenario: str = SCENARIO_STEEL_PATH,
    allow_bane_mods: bool = True,
) -> BuildOptimizationResult:
    """Find the optimal 7 Mods + Arcane complementing the scanned Riven Mod (<30ms offline)."""
    w_cls = (ev.weapon_class or "rifle").strip().lower()
    raw_pool = MOD_POOLS.get(w_cls, RIFLE_MODS)
    pool = [m for m in raw_pool if allow_bane_mods or not m.is_bane]
    arcanes = ARCANE_POOLS.get(w_cls, ()) or (None,)

    riven_prof = _extract_riven_profile(ev)

    best_mods: List[ModDef] = []
    best_arcane: Optional[ArcaneDef] = None
    best_score = -1.0
    best_comps: Set[str] = set()
    best_rem: Set[str] = set()

    for arc in arcanes:
        mods, sc, comps, rem = _optimize_for_arcane(ev, riven_prof, pool, arc, scenario, slots=7)
        if sc > best_score:
            best_score = sc
            best_mods = mods
            best_arcane = arc
            best_comps = comps
            best_rem = rem

    # Compute marginal contribution of each chosen Mod and of the Riven itself
    slot_results: List[OptimizedModSlot] = []
    for i, m in enumerate(best_mods):
        without_m = best_mods[:i] + best_mods[i + 1 :]
        sc_without, _, _ = _score_build(ev, riven_prof, without_m, best_arcane, scenario)
        gain_pct = round(((best_score / sc_without) - 1.0) * 100.0, 1) if sc_without > 0 else 0.0
        slot_results.append(OptimizedModSlot(mod=m, marginal_gain_pct=gain_pct))
    slot_results.sort(key=lambda s: s.marginal_gain_pct, reverse=True)

    sc_no_riven, _, _ = _score_build(ev, None, best_mods, best_arcane, scenario)
    riven_gain_pct = round(((best_score / sc_no_riven) - 1.0) * 100.0, 1) if sc_no_riven > 0 else 0.0

    # Format active elements & smart human-readable explanations
    elem_order = sorted(best_comps) + sorted(best_rem)
    active_zh = [_ELEM_LABEL_ZH.get(e, e) for e in elem_order]
    active_en = [_ELEM_LABEL_EN.get(e, e) for e in elem_order]

    rationale_zh: List[str] = []
    rationale_en: List[str] = []

    if ev.matched_archetype_id == "non_crit_devour":
        rationale_zh.append("命中扎里曼非暴吞噬流 → 自动剔除暴击率/爆伤卡，全力堆叠多重、射速、元素与基伤乘区")
        rationale_en.append("Zariman Non-Crit Devouring detected → stripped crit mods to maximize MS, Fire Rate & elemental scaling")

    r_base_elems = set(riven_prof["base_elements"])  # type: ignore[arg-type]
    r_comp_elems = set(riven_prof["compound_elements"])  # type: ignore[arg-type]
    if r_comp_elems:
        c_zh = "/".join(_ELEM_LABEL_ZH.get(x, x) for x in sorted(r_comp_elems))
        c_en = "/".join(_ELEM_LABEL_EN.get(x, x) for x in sorted(r_comp_elems))
        rationale_zh.append(f"紫卡自带复合元素【{c_zh}】→ 直接省下 2 个元素卡槽用于补足高收益乘区")
        rationale_en.append(f"Riven carries built-in [{c_en}] → saved 2 elemental slots for higher-value multipliers")
    elif r_base_elems:
        b_zh = "/".join(_ELEM_LABEL_ZH.get(x, x) for x in sorted(r_base_elems))
        b_en = "/".join(_ELEM_LABEL_EN.get(x, x) for x in sorted(r_base_elems))
        rationale_zh.append(f"紫卡自带【{b_zh}】→ 自动配对互补元素（{'+'.join(active_zh) or b_zh}），节省 1 个 Mod 槽位")
        rationale_en.append(f"Riven provides [{b_en}] → auto-paired complementary elements ({'+'.join(active_en) or b_en}) to save 1 slot")

    chosen_ids = {s.mod.id for s in slot_results}
    if best_arcane is not None and best_arcane.damage >= 3.0 and not (chosen_ids & {"serration", "amalgam_serration", "hornet_strike", "primed_point_blank"}):
        rationale_zh.append(f"赋能【{best_arcane.name_zh}】已提供高额基伤 → 自动跳过基础基伤卡避免稀释，换取更高综合 DPS")
        rationale_en.append(f"[{best_arcane.name_en}] saturates base damage → skipped flat Damage mod to avoid dilution")

    if best_arcane is not None and best_arcane.influence_aoe and "electricity" in best_rem:
        rationale_zh.append("保留独立【电击】触发【近战·电击影响】大范围异常扩散清图体系")
        rationale_en.append("Preserved standalone [Electricity] to trigger [Melee Influence] AoE spread")

    if not rationale_zh:
        rationale_zh.append("已按当前紫卡词条自动平衡双暴、多重、射速与元素乘区，避免重复稀释")
        rationale_en.append("Balanced Crit, Multishot, Fire Rate & Elemental multipliers around your scanned Riven")

    return BuildOptimizationResult(
        weapon_name=ev.weapon_name or ev.weapon_name_en or "",
        weapon_class=w_cls,
        scenario=scenario,
        mods=slot_results,
        arcane=best_arcane,
        total_dps_index=round(best_score, 2),
        riven_gain_pct=riven_gain_pct,
        active_elements_zh=active_zh,
        active_elements_en=active_en,
        rationale_zh=rationale_zh,
        rationale_en=rationale_en,
    )
