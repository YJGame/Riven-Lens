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

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
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

# Hot Meta Build Archetypes (auto-pulled from weapon Incarnon / recommended_tiers / Riven traits, or user-selectable)
ARCHETYPE_AUTO = "auto"
ARCHETYPE_STANDARD = "standard_crit"
ARCHETYPE_INCARNON_META = "incarnon_meta"
ARCHETYPE_NON_CRIT = "non_crit_devour"
ARCHETYPE_MELEE_INFLUENCE = "melee_influence"
ARCHETYPE_HEAVY_ATTACK = "heavy_attack"
ARCHETYPE_HEAVY_SLAM = "heavy_slam"

ARCHETYPE_LABELS_ZH: Dict[str, str] = {
    ARCHETYPE_AUTO: "自动热门流派",
    ARCHETYPE_STANDARD: "常规双暴直伤",
    ARCHETYPE_INCARNON_META: "灵化热门流派",
    ARCHETYPE_NON_CRIT: "灵化非暴吞噬流",
    ARCHETYPE_MELEE_INFLUENCE: "近战侵染清图流",
    ARCHETYPE_HEAVY_ATTACK: "近战重击爆发流",
    ARCHETYPE_HEAVY_SLAM: "近战重击震地流",
}
ARCHETYPE_LABELS_EN: Dict[str, str] = {
    ARCHETYPE_AUTO: "Auto Meta",
    ARCHETYPE_STANDARD: "Standard Crit",
    ARCHETYPE_INCARNON_META: "Incarnon Meta",
    ARCHETYPE_NON_CRIT: "Non-Crit Devouring",
    ARCHETYPE_MELEE_INFLUENCE: "Melee Influence AoE",
    ARCHETYPE_HEAVY_ATTACK: "Heavy Attack",
    ARCHETYPE_HEAVY_SLAM: "Heavy Slam",
}

_HEAVY_META_WEAPONS = frozenset(
    {
        "glaive", "glaive_prime", "xoris", "falcor", "cerata", "orvius", "pathocyst", "coda_pathocyst",
        "ceramic_dagger", "stropha", "redeemer", "redeemer_prime", "corufell", "syam", "azothane",
        "edun", "pennant", "quassus", "quassus_prime", "harmony", "hate", "reaper_prime",
    }
)
_SLAM_META_WEAPONS = frozenset(
    {
        "sampotes", "arca_titron", "magistar", "sancti_magistar", "fragor_prime", "wolf_sledge", "exec", "tenet_exec",
    }
)
_INCARNON_GUN_SLUGS = frozenset(
    {
        "torid", "burston", "burston_prime", "braton", "braton_prime", "latron", "latron_prime",
        "strun", "strun_prime", "boar", "boar_prime", "soma", "soma_prime", "boltor", "boltor_prime",
        "paris", "paris_prime", "dread", "miter", "gorgon", "dera", "sybaris", "sybaris_prime",
        "lex", "lex_prime", "furis", "dual_toxocyst", "atomos", "angstrum", "sicarus", "sicarus_prime",
        "vasto", "vasto_prime", "bronco", "bronco_prime", "kunai", "despair", "zylok", "gammacor", "cestra",
        "phenmor", "felarx", "laetum", "onorix", "okina", "dual_ichor", "hate", "ceramic_dagger",
        "magistar", "nami_solo", "sibear", "prisma_ohma", "innodem", "praedos", "thalys",
    }
)
_HEAVY_CORE_MOD_IDS = frozenset(
    {
        "killing_blow", "amalgam_organ_shatter", "sacrificial_steel", "galvanized_reflex",
        "corrupt_charge", "reflex_coil", "focus_radon", "focus_energy", "seismic_wave",
        "primed_pressure_point", "sacrificial_pressure", "primed_fever_strike", "primed_smite_grineer",
        "shocking_touch", "voltaic_strike", "virulent_scourge", "vicious_frost", "volcanic_edge",
        "primed_reach", "condition_overload", "galvanized_steel", "gladiator_might", "organ_shatter",
        "volatile_quick_return", "power_throw",
    }
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
    element: Optional[str] = None  # "toxin" | "cold" | "heat" | "electricity" | "radiation" | "magnetic" | ...
    element_value: float = 0.0
    ips_type: str = ""             # "impact" | "puncture" | "slash" | ""
    ips_value: float = 0.0
    co_per_status: float = 0.0     # Galvanized Aptitude/Savvy/Shot or Condition Overload
    blood_rush_cc: float = 0.0     # Blood Rush at 12x combo
    weeping_sc: float = 0.0        # Weeping Wounds at 12x combo
    hunter_munitions: bool = False
    is_bane: bool = False
    is_exilus: bool = False
    compat_weapon: str = ""
    optimizer_core: bool = True
    stat_display_zh: str = ""
    stat_display_en: str = ""
    raw_stats_zh: Tuple[str, ...] = ()
    raw_stats_en: Tuple[str, ...] = ()


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
    reload: float = 0.0
    status_chance: float = 0.0
    corrosive_bonus: float = 0.0
    heat_bonus: float = 0.0
    influence_aoe: bool = False
    afflictions_bonus: bool = False
    optimizer_core: bool = True
    desc_zh: str = ""
    desc_en: str = ""


# ---- Curated Mod Pools (100% verified WFSim share IDs) ----

RIFLE_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_chamber", "镀层 分裂膛室", "Galvanized Chamber", exclusive_group="ms", multishot=2.30),
    ModDef("critical_delay", "关键延迟", "Critical Delay", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("point_strike", "致命一击", "Point Strike", exclusive_group="cc", crit_chance=1.50),
    ModDef("vital_sense", "弱点感应", "Vital Sense", exclusive_group="cd", crit_damage=1.20),
    ModDef("hammer_shot", "重锤射击", "Hammer Shot", crit_damage=0.60, status_chance=0.80),
    ModDef("bladed_rounds", "尖刃弹头", "Bladed Rounds", crit_damage=1.20),
    ModDef("galvanized_scope", "镀层 氩晶瞄具", "Galvanized Scope", crit_chance=3.20),
    ModDef("serration", "膛线", "Serration", exclusive_group="base_dmg", damage=1.65),
    ModDef("amalgam_serration", "并合 膛线", "Amalgam Serration", exclusive_group="base_dmg", damage=1.55),
    ModDef("galvanized_aptitude", "镀层 步枪才能", "Galvanized Aptitude", status_chance=0.80, co_per_status=0.80),
    ModDef("vile_acceleration", "卑劣加速", "Vile Acceleration", exclusive_group="fr", fire_rate=0.90, damage=-0.15),
    ModDef("primed_shred", "撕裂 Prime", "Primed Shred", exclusive_group="fr", fire_rate=0.55, punch_through=2.2),
    ModDef("speed_trigger", "灵敏扳机", "Speed Trigger", exclusive_group="fr", fire_rate=0.60),
    ModDef("hunter_munitions", "猎人 战备", "Hunter Munitions", hunter_munitions=True),
    ModDef("rifle_elementalist", "步枪元素师", "Rifle Elementalist", status_damage=0.90, punch_through=0.6),
    ModDef("malignant_force", "致命火力", "Malignant Force", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("infected_clip", "污染弹匣", "Infected Clip", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("rime_rounds", "白霜弹头", "Rime Rounds", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("primed_cryo_rounds", "低温弹头 Prime", "Primed Cryo Rounds", exclusive_group="cold90", element="cold", element_value=1.65),
    ModDef("hellfire", "地狱火", "Hellfire", exclusive_group="heat90", element="heat", element_value=0.90),
    ModDef("thermite_rounds", "铝热焊弹", "Thermite Rounds", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("high_voltage", "高压电流", "High Voltage", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("stormbringer", "暴风使者", "Stormbringer", exclusive_group="elec90", element="electricity", element_value=0.90),
    ModDef("primed_bane_of_grineer", "灭亡 Grineer Prime", "Primed Bane of Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("vigilante_armaments", "私法 军备", "Vigilante Armaments", multishot=0.60),
    ModDef("primed_fast_hands", "爆发装填 Prime", "Primed Fast Hands", reload=0.55),
)

SHOTGUN_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_hell", "镀层 地狱弹膛", "Galvanized Hell", exclusive_group="ms", multishot=2.30),
    ModDef("critical_deceleration", "降速暴击", "Critical Deceleration", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("blunderbuss", "雷筒", "Blunderbuss", exclusive_group="cc", crit_chance=0.90),
    ModDef("primed_ravage", "破灭 Prime", "Primed Ravage", exclusive_group="cd", crit_damage=1.10),
    ModDef("primed_point_blank", "抵近射击 Prime", "Primed Point Blank", exclusive_group="base_dmg", damage=1.65),
    ModDef("galvanized_savvy", "镀层 通晓霰弹枪", "Galvanized Savvy", status_chance=0.80, co_per_status=0.80),
    ModDef("shotgun_barrage", "霰弹弹幕", "Shotgun Barrage", exclusive_group="fr", fire_rate=0.90),
    ModDef("shotgun_elementalist", "霰弹枪元素师", "Shotgun Elementalist", status_damage=0.90),
    ModDef("toxic_barrage", "毒素弹幕", "Toxic Barrage", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("contagious_spread", "传染蔓延", "Contagious Spread", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("chilling_reload", "激冷装填", "Chilling Reload", exclusive_group="cold_util", element="cold", element_value=0.60, reload=0.40),
    ModDef("frigid_blast", "冰冷疾风", "Frigid Blast", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("primed_chilling_grasp", "急冻控场 Prime", "Primed Chilling Grasp", exclusive_group="cold90", element="cold", element_value=1.65),
    ModDef("incendiary_coat", "燃烧外壳", "Incendiary Coat", exclusive_group="heat90", element="heat", element_value=0.90),
    ModDef("scattering_inferno", "炼狱轰击", "Scattering Inferno", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("blaze", "烈焰", "Blaze", damage=0.60, element="heat", element_value=0.60),
    ModDef("shell_shock", "电冲弹药", "Shell Shock", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("primed_charged_shell", "充电弹头 Prime", "Primed Charged Shell", exclusive_group="elec90", element="electricity", element_value=1.65),
    ModDef("primed_cleanse_grineer", "净化 Grineer Prime", "Primed Cleanse Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("vigilante_armaments", "私法 军备", "Vigilante Armaments", multishot=0.60),
    ModDef("seeking_fury", "狂暴追猎", "Seeking Fury", punch_through=1.2, reload=0.15),
)

PISTOL_MODS: Tuple[ModDef, ...] = (
    ModDef("galvanized_diffusion", "镀层 弹头扩散", "Galvanized Diffusion", exclusive_group="ms", multishot=2.30),
    ModDef("lethal_torrent", "致命洪流", "Lethal Torrent", multishot=0.60, fire_rate=0.60),
    ModDef("creeping_bullseye", "匍匐靶心", "Creeping Bullseye", exclusive_group="cc", crit_chance=2.00, fire_rate=-0.20),
    ModDef("primed_pistol_gambit", "手枪精通 Prime", "Primed Pistol Gambit", exclusive_group="cc", crit_chance=1.87),
    ModDef("primed_target_cracker", "弱点专精 Prime", "Primed Target Cracker", exclusive_group="cd", crit_damage=1.10),
    ModDef("galvanized_crosshairs", "镀层 液压准心", "Galvanized Crosshairs", crit_chance=3.20),
    ModDef("hornet_strike", "黄蜂螫刺", "Hornet Strike", exclusive_group="base_dmg", damage=2.20),
    ModDef("galvanized_shot", "镀层 准确射手", "Galvanized Shot", status_chance=0.80, co_per_status=0.80),
    ModDef("anemic_agility", "乏能迅敏", "Anemic Agility", exclusive_group="fr", fire_rate=0.90, damage=-0.15),
    ModDef("gunslinger", "神枪手", "Gunslinger", exclusive_group="fr", fire_rate=0.72),
    ModDef("pistol_elementalist", "手枪元素师", "Pistol Elementalist", status_damage=0.90, reload=0.30),
    ModDef("pistol_pestilence", "瘟疫手枪", "Pistol Pestilence", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("pathogen_rounds", "病原弹头", "Pathogen Rounds", exclusive_group="tox90", element="toxin", element_value=0.90),
    ModDef("frostbite", "结霜侵蚀", "Frostbite", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("deep_freeze", "深层冷冻", "Deep Freeze", exclusive_group="cold90", element="cold", element_value=0.90),
    ModDef("primed_heated_charge", "火焰装填 Prime", "Primed Heated Charge", exclusive_group="heat90", element="heat", element_value=1.65),
    ModDef("scorch", "灼痕焦点", "Scorch", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
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
    ModDef("berserker_fury", "嗜血 狂暴", "Berserker Fury", exclusive_group="as", fire_rate=0.70),
    ModDef("quickening", "加速", "Quickening", exclusive_group="as", fire_rate=0.40),
    ModDef("primed_fury", "狂暴 Prime", "Primed Fury", exclusive_group="as", fire_rate=0.55),
    ModDef("primed_reach", "剑风 Prime", "Primed Reach", exclusive_group="range", range_m=3.0),
    ModDef("galvanized_elementalist", "镀层 元素师", "Galvanized Elementalist", exclusive_group="elem_st", status_damage=0.80, status_chance=1.20),
    ModDef("voltaic_strike", "伏打电能", "Voltaic Strike", exclusive_group="elec60", element="electricity", element_value=0.60, status_chance=0.60),
    ModDef("shocking_touch", "电击触点", "Shocking Touch", exclusive_group="elec90", element="electricity", element_value=0.90),
    ModDef("virulent_scourge", "剧毒灾害", "Virulent Scourge", exclusive_group="tox60", element="toxin", element_value=0.60, status_chance=0.60),
    ModDef("primed_fever_strike", "热病打击 Prime", "Primed Fever Strike", exclusive_group="tox90", element="toxin", element_value=1.65),
    ModDef("vicious_frost", "蚀骨寒霜", "Vicious Frost", exclusive_group="cold60", element="cold", element_value=0.60, status_chance=0.60),
    ModDef("volcanic_edge", "爆裂刀刃", "Volcanic Edge", exclusive_group="heat60", element="heat", element_value=0.60, status_chance=0.60),
    ModDef("primed_smite_grineer", "毁灭 Grineer Prime", "Primed Smite Grineer", exclusive_group="bane", faction=0.55, is_bane=True),
    ModDef("galvanized_reflex", "镀层 增幅线圈", "Galvanized Reflex", exclusive_group="heavy_init", initial_combo=80.0),
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
    ModDef("sabot_rounds", "覆壳弹药", "Sabot Rounds", damage=0.60, punch_through=3.0),
    ModDef("venomous_clip", "恶毒弹匣", "Venomous Clip", element="toxin", element_value=1.20),
    ModDef("polar_magazine", "极地弹仓", "Polar Magazine", element="cold", element_value=1.20),
    ModDef("combustion_rounds", "燃烧弹头", "Combustion Rounds", element="heat", element_value=1.20),
    ModDef("electrified_barrel", "带电枪管", "Electrified Barrel", element="electricity", element_value=1.20),
    ModDef("contamination_casing", "毒染套管", "Contamination Casing", element="toxin", element_value=0.60, status_chance=0.60),
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
        ArcaneDef("primary_merciless", "主要·无情", "Primary Merciless", damage=3.60, reload=0.30),
        ArcaneDef("primary_deadhead", "主要·死首", "Primary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("primary_frostbite", "主要·霜冻", "Primary Frostbite", crit_damage=1.20, multishot=0.90),
        ArcaneDef("primary_blight", "主要·毒侵", "Primary Blight", crit_damage=1.48, multishot=0.90),
    ),
    "shotgun": (
        ArcaneDef("primary_merciless", "主要·无情", "Primary Merciless", damage=3.60, reload=0.30),
        ArcaneDef("primary_deadhead", "主要·死首", "Primary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("primary_frostbite", "主要·霜冻", "Primary Frostbite", crit_damage=1.20, multishot=0.90),
    ),
    "pistol": (
        ArcaneDef("secondary_merciless", "次要·无情", "Secondary Merciless", damage=3.60, reload=0.30),
        ArcaneDef("secondary_deadhead", "次要·死首", "Secondary Deadhead", damage=3.60, weakpoint=0.30),
        ArcaneDef("cascadia_flare", "瀑流·耀炎", "Cascadia Flare", damage=4.80),
    ),
    "melee": (
        ArcaneDef("melee_influence", "近战·侵染", "Melee Influence", influence_aoe=True),
        ArcaneDef("melee_exposure", "近战·暴露", "Melee Exposure", corrosive_bonus=2.40),
        ArcaneDef("melee_afflictions", "近战·苦痛", "Melee Afflictions", afflictions_bonus=True),
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


@dataclass(frozen=True)
class BuildStatSummary:
    """Aggregated combat statistics for the active build (Weapon Base + Riven + Mods + Arcane)."""

    weapon_class: str
    damage_bonus_pct: float = 0.0
    eff_damage: float = 100.0
    multishot_pct: float = 0.0
    eff_multishot: float = 1.0
    crit_chance_bonus_pct: float = 0.0
    eff_crit_chance_pct: float = 0.0
    crit_damage_bonus_pct: float = 0.0
    eff_crit_mult: float = 2.0
    fire_rate_pct: float = 0.0
    eff_fire_rate: float = 1.0
    status_chance_bonus_pct: float = 0.0
    eff_status_chance_pct: float = 0.0
    elemental_pct: float = 0.0
    element_breakdown: Tuple[Tuple[str, str, float, float], ...] = ()  # (zh, en, dmg_value, bonus_pct)
    status_damage_pct: float = 0.0
    faction_pct: float = 0.0
    range_m: float = 0.0
    eff_range_m: float = 0.0
    punch_through_m: float = 0.0
    reload_pct: float = 0.0


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
    delta_vs_recommended_pct: Optional[float] = None
    stat_summary: Optional[BuildStatSummary] = None
    exilus_mod: Optional[ModDef] = None
    active_archetype_id: str = ARCHETYPE_STANDARD
    active_archetype_zh: str = ""
    active_archetype_en: str = ""

    @property
    def mod_ids(self) -> List[str]:
        return [slot.mod.id for slot in self.mods]

    @property
    def arcane_ids(self) -> List[str]:
        return [self.arcane.id] if self.arcane is not None else []

    @property
    def exilus_mod_id(self) -> Optional[str]:
        return self.exilus_mod.id if self.exilus_mod is not None else None


# ---- Dynamic Catalog Loader (`data/optimizer_catalog.json`) ----

_CATALOG_CACHE: Optional[
    Tuple[Dict[str, Tuple[ModDef, ...]], Dict[str, Tuple[ArcaneDef, ...]], Dict[str, Dict[str, float]]]
] = None


def _resolve_catalog_path() -> Optional[Path]:
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "data" / "optimizer_catalog.json")
        candidates.append(exe_dir / "_internal" / "data" / "optimizer_catalog.json")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "data" / "optimizer_catalog.json")
    candidates.append(Path(__file__).resolve().parent.parent / "data" / "optimizer_catalog.json")
    for p in candidates:
        if p.is_file():
            return p
    return None


def _load_catalog() -> Tuple[
    Dict[str, Tuple[ModDef, ...]],
    Dict[str, Tuple[ArcaneDef, ...]],
    Dict[str, Dict[str, float]],
]:
    global _CATALOG_CACHE
    if _CATALOG_CACHE is not None:
        return _CATALOG_CACHE

    cat_path = _resolve_catalog_path()
    if cat_path is None:
        _CATALOG_CACHE = (MOD_POOLS, ARCANE_POOLS, {})
        return _CATALOG_CACHE

    try:
        raw = json.loads(cat_path.read_text(encoding="utf-8"))
        mods_map: Dict[str, Tuple[ModDef, ...]] = {}
        arcanes_map: Dict[str, Tuple[ArcaneDef, ...]] = {}
        weapons_map: Dict[str, Dict[str, float]] = {}

        raw_weapons = raw.get("weapons")
        if isinstance(raw_weapons, dict):
            for wk, wv in raw_weapons.items():
                if isinstance(wv, dict):
                    weapons_map[str(wk).lower()] = {
                        "base_damage": float(wv.get("base_damage") or 100.0),
                        "fire_rate": float(wv.get("fire_rate") or 1.0),
                        "multishot": float(wv.get("multishot") or 1.0),
                        "range_m": float(wv.get("range_m") or 0.0),
                    }

        for cls, fallback_pool in MOD_POOLS.items():
            items = (raw.get("mods") or {}).get(cls)
            if not isinstance(items, list) or not items:
                mods_map[cls] = fallback_pool
                continue
            parsed_mods: List[ModDef] = []
            for d in items:
                if not isinstance(d, dict) or not d.get("id"):
                    continue
                parsed_mods.append(
                    ModDef(
                        id=str(d["id"]),
                        name_zh=str(d.get("name_zh") or d["id"]),
                        name_en=str(d.get("name_en") or d["id"]),
                        exclusive_group=str(d.get("exclusive_group") or ""),
                        damage=float(d.get("damage") or 0.0),
                        multishot=float(d.get("multishot") or 0.0),
                        crit_chance=float(d.get("crit_chance") or 0.0),
                        crit_damage=float(d.get("crit_damage") or 0.0),
                        fire_rate=float(d.get("fire_rate") or 0.0),
                        status_chance=float(d.get("status_chance") or 0.0),
                        status_damage=float(d.get("status_damage") or 0.0),
                        faction=float(d.get("faction") or 0.0),
                        weakpoint=float(d.get("weakpoint") or 0.0),
                        punch_through=float(d.get("punch_through") or 0.0),
                        range_m=float(d.get("range_m") or 0.0),
                        reload=float(d.get("reload") or 0.0),
                        initial_combo=float(d.get("initial_combo") or 0.0),
                        heavy_dmg=float(d.get("heavy_dmg") or 0.0),
                        slam_dmg=float(d.get("slam_dmg") or 0.0),
                        element=str(d["element"]) if d.get("element") else None,
                        element_value=float(d.get("element_value") or 0.0),
                        ips_type=str(d.get("ips_type") or ""),
                        ips_value=float(d.get("ips_value") or 0.0),
                        co_per_status=float(d.get("co_per_status") or 0.0),
                        blood_rush_cc=float(d.get("blood_rush_cc") or 0.0),
                        weeping_sc=float(d.get("weeping_sc") or 0.0),
                        hunter_munitions=bool(d.get("hunter_munitions")),
                        is_bane=bool(d.get("is_bane")),
                        is_exilus=bool(d.get("is_exilus")),
                        compat_weapon=str(d.get("compat_weapon") or ""),
                        optimizer_core=bool(d.get("optimizer_core", False)),
                        stat_display_zh=str(d.get("stat_display_zh") or ""),
                        stat_display_en=str(d.get("stat_display_en") or ""),
                        raw_stats_zh=tuple(str(x) for x in (d.get("raw_stats_zh") or [])),
                        raw_stats_en=tuple(str(x) for x in (d.get("raw_stats_en") or [])),
                    )
                )
            mods_map[cls] = tuple(parsed_mods) if parsed_mods else fallback_pool

        for cls, fallback_arcs in ARCANE_POOLS.items():
            items = (raw.get("arcanes") or {}).get(cls)
            if not isinstance(items, list):
                arcanes_map[cls] = fallback_arcs
                continue
            parsed_arcs: List[ArcaneDef] = []
            for d in items:
                if not isinstance(d, dict) or not d.get("id"):
                    continue
                parsed_arcs.append(
                    ArcaneDef(
                        id=str(d["id"]),
                        name_zh=str(d.get("name_zh") or d["id"]),
                        name_en=str(d.get("name_en") or d["id"]),
                        damage=float(d.get("damage") or 0.0),
                        multishot=float(d.get("multishot") or 0.0),
                        crit_damage=float(d.get("crit_damage") or 0.0),
                        crit_chance=float(d.get("crit_chance") or 0.0),
                        weakpoint=float(d.get("weakpoint") or 0.0),
                        reload=float(d.get("reload") or 0.0),
                        status_chance=float(d.get("status_chance") or 0.0),
                        corrosive_bonus=float(d.get("corrosive_bonus") or 0.0),
                        heat_bonus=float(d.get("heat_bonus") or 0.0),
                        influence_aoe=bool(d.get("influence_aoe")),
                        afflictions_bonus=bool(d.get("afflictions_bonus")),
                        optimizer_core=bool(d.get("optimizer_core", False)),
                        desc_zh=str(d.get("desc_zh") or ""),
                        desc_en=str(d.get("desc_en") or ""),
                    )
                )
            arcanes_map[cls] = tuple(parsed_arcs)

        _CATALOG_CACHE = (mods_map, arcanes_map, weapons_map)
        return _CATALOG_CACHE
    except Exception:
        _CATALOG_CACHE = (MOD_POOLS, ARCANE_POOLS, {})
        return _CATALOG_CACHE


def get_weapon_base_stats(
    weapon_slug: str = "",
    weapon_name_en: str = "",
    weapon_class: str = "rifle",
) -> Dict[str, float]:
    """Return real WFCD weapon base stats (`base_damage`, `fire_rate`, `multishot`, `range_m`)."""
    _, _, weapons_map = _load_catalog()
    wkey = _norm_weapon_key(weapon_slug, weapon_name_en)
    hit = weapons_map.get(wkey)
    if hit is not None:
        return hit
    # Fallback defaults by weapon class
    cls = (weapon_class or "rifle").strip().lower()
    if cls == "melee":
        return {"base_damage": 180.0, "fire_rate": 1.0, "multishot": 1.0, "range_m": 2.5}
    if cls == "shotgun":
        return {"base_damage": 300.0, "fire_rate": 2.5, "multishot": 8.0, "range_m": 0.0}
    if cls == "pistol":
        return {"base_damage": 80.0, "fire_rate": 5.0, "multishot": 1.0, "range_m": 0.0}
    return {"base_damage": 100.0, "fire_rate": 5.0, "multishot": 1.0, "range_m": 0.0}


_SUBTYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "bow": (
        "bramma", "cernos", "dread", "paris", "nataruk", "daikyu", "lenz",
        "zhuge", "attica", "nagantaka", "cinta", "evensong", "artemis_bow", "bow",
    ),
    "sniper": (
        "rubico", "vectis", "lanka", "vulkar", "snipetron", "komorex",
        "sporothrix", "perigale", "vadarya",
    ),
    "glaive": (
        "glaive", "xoris", "falcor", "cerata", "orvius", "pathocyst",
        "kestrel", "halikar", "wolf_sledge",
    ),
    "tome": ("grimoire", "noctua"),
    "dagger": (
        "karyst", "ceramic_dagger", "innodem", "balla", "dark_dagger",
        "heat_dagger", "rumblejack", "sheev",
    ),
}


def _norm_weapon_key(weapon_slug: str = "", weapon_name_en: str = "") -> str:
    s = (weapon_slug or "").strip().lower()
    if s:
        return s.replace("-", "_").replace(" ", "_")
    en = (weapon_name_en or "").strip().lower().replace("&", " and ")
    return "".join(ch if ch.isalnum() else "_" for ch in en).strip("_")


def is_mod_compatible_with_weapon(
    mod: ModDef,
    *,
    weapon_slug: str = "",
    weapon_name_en: str = "",
) -> bool:
    """Return True if `mod` is universal (`compat_weapon == ''`) or matches the specified weapon."""
    cw = (mod.compat_weapon or "").strip().lower()
    if not cw:
        return True
    wkey = _norm_weapon_key(weapon_slug, weapon_name_en)
    if not wkey:
        return False
    if cw.startswith("subtype:"):
        st = cw.split(":", 1)[1]
        kws = _SUBTYPE_KEYWORDS.get(st, ())
        return any(kw == wkey or f"_{kw}" in wkey or f"{kw}_" in wkey for kw in kws)
    return wkey == cw or wkey.endswith(f"_{cw}") or wkey.startswith(f"{cw}_")


def get_mod_pool(
    weapon_class: str,
    *,
    allow_bane_mods: bool = True,
    optimizer_only: bool = False,
    weapon_slug: str = "",
    weapon_name_en: str = "",
) -> List[ModDef]:
    """Return candidate Mods for `weapon_class`, filtering weapon-exclusive Mods to the active weapon."""
    w_cls = (weapon_class or "rifle").strip().lower()
    mods_map, _, _ = _load_catalog()
    raw_pool = mods_map.get(w_cls, mods_map.get("rifle", RIFLE_MODS))
    has_weapon_ctx = bool((weapon_slug or "").strip() or (weapon_name_en or "").strip())
    out: List[ModDef] = []
    for m in raw_pool:
        if not allow_bane_mods and m.is_bane:
            continue
        if optimizer_only and not m.optimizer_core:
            continue
        if has_weapon_ctx:
            if not is_mod_compatible_with_weapon(m, weapon_slug=weapon_slug, weapon_name_en=weapon_name_en):
                continue
        elif optimizer_only and m.compat_weapon:
            continue
        out.append(m)
    return out if out else list(MOD_POOLS.get(w_cls, RIFLE_MODS))


def get_exilus_pool(
    weapon_class: str,
    *,
    weapon_slug: str = "",
    weapon_name_en: str = "",
) -> List[ModDef]:
    """Return all weapon-compatible Exilus (`is_exilus=True`) Mods for Slot #9."""
    full = get_mod_pool(
        weapon_class,
        allow_bane_mods=True,
        optimizer_only=False,
        weapon_slug=weapon_slug,
        weapon_name_en=weapon_name_en,
    )
    return [m for m in full if m.is_exilus]


def get_available_archetypes_for_class(weapon_class: str) -> List[str]:
    """Return ordered archetype keys selectable for `weapon_class`."""
    cls = (weapon_class or "rifle").strip().lower()
    if cls == "melee":
        return [
            ARCHETYPE_AUTO,
            ARCHETYPE_MELEE_INFLUENCE,
            ARCHETYPE_HEAVY_ATTACK,
            ARCHETYPE_HEAVY_SLAM,
            ARCHETYPE_STANDARD,
        ]
    if cls == "archgun":
        return [ARCHETYPE_AUTO, ARCHETYPE_STANDARD]
    return [
        ARCHETYPE_AUTO,
        ARCHETYPE_INCARNON_META,
        ARCHETYPE_STANDARD,
        ARCHETYPE_NON_CRIT,
    ]


def detect_meta_archetype(
    ev: RivenEvaluation,
    riven_prof: Optional[Dict[str, object]] = None,
) -> Tuple[str, str, str]:
    """Auto-detect the hottest meta build archetype from weapon Incarnon rules & scanned Riven stats."""
    w_cls = (ev.weapon_class or "rifle").strip().lower()
    wkey = _norm_weapon_key(ev.weapon_slug, ev.weapon_name_en)
    matched_id = (ev.matched_archetype_id or "").strip().lower()
    matched_zh = (ev.matched_archetype_name or "").strip()
    matched_en = (ev.matched_archetype_name_en or "").strip()

    if matched_id == "non_crit_devour" or wkey in ("laetum", "phenmor", "felarx"):
        return (
            ARCHETYPE_NON_CRIT,
            matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_NON_CRIT],
            matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_NON_CRIT],
        )

    if w_cls == "melee":
        r_slam = float((riven_prof or {}).get("slam_dmg", 0.0))
        r_heavy = float((riven_prof or {}).get("heavy_dmg", 0.0))
        r_ic = float((riven_prof or {}).get("initial_combo", 0.0))
        if matched_id == "heavy_slam" or wkey in _SLAM_META_WEAPONS or r_slam >= 0.4:
            return (
                ARCHETYPE_HEAVY_SLAM,
                matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_HEAVY_SLAM],
                matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_HEAVY_SLAM],
            )
        if matched_id in ("heavy_statstick", "heavy_attack") or wkey in _HEAVY_META_WEAPONS or r_heavy >= 0.35 or r_ic >= 15.0:
            return (
                ARCHETYPE_HEAVY_ATTACK,
                matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_HEAVY_ATTACK],
                matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_HEAVY_ATTACK],
            )
        if matched_id == "melee_influence" or wkey in _INCARNON_GUN_SLUGS:
            return (
                ARCHETYPE_MELEE_INFLUENCE,
                matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_MELEE_INFLUENCE],
                matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_MELEE_INFLUENCE],
            )
        return (
            ARCHETYPE_MELEE_INFLUENCE,
            ARCHETYPE_LABELS_ZH[ARCHETYPE_MELEE_INFLUENCE],
            ARCHETYPE_LABELS_EN[ARCHETYPE_MELEE_INFLUENCE],
        )

    if wkey in _INCARNON_GUN_SLUGS or (matched_zh and "灵化" in matched_zh):
        return (
            ARCHETYPE_INCARNON_META,
            matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_INCARNON_META],
            matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_INCARNON_META],
        )

    return (
        ARCHETYPE_STANDARD,
        matched_zh or ARCHETYPE_LABELS_ZH[ARCHETYPE_STANDARD],
        matched_en or ARCHETYPE_LABELS_EN[ARCHETYPE_STANDARD],
    )


def categorize_mods_for_menu(
    mods: Sequence[ModDef],
    weapon_class: str = "rifle",
    lang: str = "zh_CN",
) -> List[Tuple[str, List[ModDef]]]:
    """Group compatible Mods into crisp top-level categories (single Elemental, Physical IPS, Exilus, Exclusive)."""
    is_zh = lang in ("zh_CN", "zh_TW")
    is_melee = (weapon_class or "").lower() == "melee"

    buckets: Dict[str, List[ModDef]] = {
        "exclusive": [],
        "dmg_ms": [],
        "crit": [],
        "speed_range": [],
        "elemental": [],
        "physical": [],
        "status_slash": [],
        "bane": [],
        "combat_util": [],
        "exilus": [],
    }

    for m in mods:
        if m.compat_weapon:
            buckets["exclusive"].append(m)
        elif m.is_exilus:
            buckets["exilus"].append(m)
        elif m.is_bane:
            buckets["bane"].append(m)
        elif m.element:
            buckets["elemental"].append(m)
        elif m.ips_type:
            buckets["physical"].append(m)
        elif m.crit_chance or m.crit_damage or m.blood_rush_cc or m.weakpoint:
            buckets["crit"].append(m)
        elif m.damage or m.multishot or m.co_per_status:
            buckets["dmg_ms"].append(m)
        elif m.fire_rate or (is_melee and m.range_m):
            buckets["speed_range"].append(m)
        elif m.status_chance or m.status_damage or m.weeping_sc or m.hunter_munitions:
            buckets["status_slash"].append(m)
        else:
            buckets["combat_util"].append(m)

    labels_zh = {
        "exclusive": "★ 武器专属",
        "dmg_ms": "⚔ 伤害 / 多重" if not is_melee else "⚔ 基础伤害 / 异况",
        "crit": "🎯 暴击 / 爆伤",
        "speed_range": "⚡ 攻速 / 范围" if is_melee else "⚡ 射速",
        "elemental": "🧪 元素伤害",
        "physical": "🗡 物理伤害（冲/穿/切）",
        "status_slash": "🩸 触发 / 异常 / 切割",
        "bane": "🛡 种族歧视",
        "combat_util": "🔧 重击 / 震地 / 连击" if is_melee else "🔧 装填 / 穿透 / 弹药",
        "exilus": "⚙ 特殊功能（第 9 格）",
    }
    labels_en = {
        "exclusive": "★ Weapon Exclusive",
        "dmg_ms": "⚔ Damage & Multishot" if not is_melee else "⚔ Base Damage / CO",
        "crit": "🎯 Crit Chance & Damage",
        "speed_range": "⚡ Attack Speed & Range" if is_melee else "⚡ Fire Rate",
        "elemental": "🧪 Elemental Damage",
        "physical": "🗡 Physical (Impact/Puncture/Slash)",
        "status_slash": "🩸 Status & Slash",
        "bane": "🛡 Faction Bane",
        "combat_util": "🔧 Heavy / Slam / Combo" if is_melee else "🔧 Reload / Punch / Utility",
        "exilus": "⚙ Exilus (Slot #9)",
    }
    labels = labels_zh if is_zh else labels_en

    elem_sort_order = {
        "toxin": 0, "cold": 1, "heat": 2, "electricity": 3,
        "radiation": 4, "magnetic": 5, "viral": 6, "corrosive": 7, "blast": 8, "gas": 9,
    }
    ips_sort_order = {"slash": 0, "puncture": 1, "impact": 2}

    ordered_keys = (
        "exclusive",
        "dmg_ms",
        "crit",
        "speed_range",
        "elemental",
        "physical",
        "status_slash",
        "bane",
        "combat_util",
        "exilus",
    )
    result: List[Tuple[str, List[ModDef]]] = []
    for k in ordered_keys:
        items = buckets[k]
        if not items:
            continue
        if k == "elemental":
            items.sort(key=lambda x: (elem_sort_order.get(x.element or "", 99), 0 if x.optimizer_core else 1, x.name_zh if is_zh else x.name_en))
        elif k == "physical":
            items.sort(key=lambda x: (ips_sort_order.get(x.ips_type, 9), -x.ips_value, x.name_zh if is_zh else x.name_en))
        else:
            items.sort(key=lambda x: (0 if x.optimizer_core else 1, x.name_zh if is_zh else x.name_en))
        result.append((labels[k], items))
    return result


def get_arcane_pool(weapon_class: str, *, optimizer_only: bool = False) -> List[ArcaneDef]:
    """Return candidate Weapon Arcanes for `weapon_class`."""
    w_cls = (weapon_class or "rifle").strip().lower()
    _, arcs_map, _ = _load_catalog()
    raw_pool = arcs_map.get(w_cls, ())
    if optimizer_only:
        core = [a for a in raw_pool if a.optimizer_core]
        return core if core else list(raw_pool)
    return list(raw_pool)


# ---- Mod Stat Formatting & Compound-Element Smart Search ----

_COMPOUND_QUERY_EXPANSION: Dict[str, Tuple[str, ...]] = {
    "腐蚀": ("toxin", "electricity", "corrosive"),
    "corrosive": ("toxin", "electricity", "corrosive"),
    "病毒": ("toxin", "cold", "viral"),
    "viral": ("toxin", "cold", "viral"),
    "辐射": ("heat", "electricity", "radiation"),
    "radiation": ("heat", "electricity", "radiation"),
    "磁力": ("cold", "electricity", "magnetic"),
    "magnetic": ("cold", "electricity", "magnetic"),
    "毒气": ("toxin", "heat", "gas"),
    "gas": ("toxin", "heat", "gas"),
    "爆炸": ("heat", "cold", "blast"),
    "blast": ("heat", "cold", "blast"),
    "毒素": ("toxin",),
    "毒": ("toxin",),
    "冰冻": ("cold",),
    "冰": ("cold",),
    "火焰": ("heat",),
    "火": ("heat",),
    "电击": ("electricity",),
    "电": ("electricity",),
}


def format_mod_stats(mod: ModDef, weapon_class: str = "rifle", lang: str = "zh_CN") -> str:
    """Format concise official in-game stat lines (with min~max conditional ranges) for Mod cards & menus."""
    is_zh = lang in ("zh_CN", "zh_TW")
    if is_zh:
        if mod.stat_display_zh:
            return mod.stat_display_zh
        if mod.raw_stats_zh:
            return " · ".join(mod.raw_stats_zh[:2])
    else:
        if mod.stat_display_en:
            return mod.stat_display_en
        if mod.raw_stats_en:
            return " · ".join(mod.raw_stats_en[:2])

    is_melee = (weapon_class or "").lower() == "melee"
    parts: List[str] = []

    def _pct(val: float) -> str:
        p = val * 100.0
        return f"{p:+.0f}%" if abs(p - round(p)) < 0.05 else f"{p:+.1f}%"

    if mod.damage:
        parts.append(f"{_pct(mod.damage)} {'近战伤害' if is_melee else '伤害'}" if is_zh else f"{_pct(mod.damage)} Damage")
    if mod.multishot:
        parts.append(f"{_pct(mod.multishot)} 多重射击" if is_zh else f"{_pct(mod.multishot)} Multishot")
    if mod.blood_rush_cc:
        parts.append("+40%~440% 暴击几率（随连击）" if is_zh else "+40%~440% Crit Chance (Combo)")
    elif mod.crit_chance:
        parts.append(f"{_pct(mod.crit_chance)} 暴击几率" if is_zh else f"{_pct(mod.crit_chance)} Crit Chance")
    if mod.crit_damage:
        parts.append(f"{_pct(mod.crit_damage)} 暴击伤害" if is_zh else f"{_pct(mod.crit_damage)} Crit Damage")
    if mod.fire_rate:
        parts.append(
            f"{_pct(mod.fire_rate)} {'攻击速度' if is_melee else '射速'}"
            if is_zh
            else f"{_pct(mod.fire_rate)} {'Attack Speed' if is_melee else 'Fire Rate'}"
        )
    if mod.element and mod.element_value:
        el_zh = _ELEM_LABEL_ZH.get(mod.element, mod.element)
        el_en = _ELEM_LABEL_EN.get(mod.element, mod.element)
        parts.append(f"{_pct(mod.element_value)} {el_zh}伤害" if is_zh else f"{_pct(mod.element_value)} {el_en}")
    if mod.co_per_status:
        parts.append(f"每异常 {_pct(mod.co_per_status)} 伤害" if is_zh else f"{_pct(mod.co_per_status)} DMG / Status")
    if mod.weeping_sc:
        parts.append("+40%~440% 触发几率（随连击）" if is_zh else "+40%~440% Status (Combo)")
    elif mod.status_chance:
        parts.append(f"{_pct(mod.status_chance)} 触发几率" if is_zh else f"{_pct(mod.status_chance)} Status Chance")
    if mod.status_damage:
        parts.append(f"{_pct(mod.status_damage)} 异常状态伤害" if is_zh else f"{_pct(mod.status_damage)} Status DMG")
    if mod.hunter_munitions:
        parts.append("暴击 30% 几率触发切割" if is_zh else "30% Slash on Crit")
    if mod.faction:
        parts.append(f"x{1.0 + mod.faction:.2f} 阵营增伤" if is_zh else f"x{1.0 + mod.faction:.2f} Faction DMG")
    if mod.weakpoint:
        parts.append(f"{_pct(mod.weakpoint)} 弱点伤害" if is_zh else f"{_pct(mod.weakpoint)} Weak Point")
    if mod.range_m:
        parts.append(f"{mod.range_m:+.1f} 米攻击范围" if is_zh else f"{mod.range_m:+.1f}m Range")
    if mod.punch_through:
        parts.append(f"{mod.punch_through:+.1f} 米穿透" if is_zh else f"{mod.punch_through:+.1f}m Punch Through")
    if mod.reload:
        parts.append(f"{_pct(mod.reload)} 装填速度" if is_zh else f"{_pct(mod.reload)} Reload")
    if mod.initial_combo:
        parts.append(f"{mod.initial_combo:+.0f} 初始连击" if is_zh else f"{mod.initial_combo:+.0f} Initial Combo")
    if mod.heavy_dmg:
        parts.append(f"{_pct(mod.heavy_dmg)} 重击伤害" if is_zh else f"{_pct(mod.heavy_dmg)} Heavy DMG")
    if mod.slam_dmg:
        parts.append(f"{_pct(mod.slam_dmg)} 震地攻击伤害" if is_zh else f"{_pct(mod.slam_dmg)} Slam DMG")

    return " · ".join(parts[:3]) if parts else "-"


def match_mod_search(mod: ModDef, query: str, weapon_class: str = "rifle") -> bool:
    """Return True if `mod` matches user's search text (supports name, stats, & compound elements!)."""
    q = (query or "").strip().lower()
    if not q:
        return True

    tokens = [t for t in q.split() if t]
    stat_zh = format_mod_stats(mod, weapon_class, "zh_CN").lower()
    stat_en = format_mod_stats(mod, weapon_class, "en_US").lower()
    haystack = " ".join(
        [
            mod.name_zh.lower(),
            mod.name_en.lower(),
            mod.id.lower(),
            stat_zh,
            stat_en,
            " ".join(mod.raw_stats_en).lower(),
            "歧视 种族" if mod.is_bane else "",
            "爆伤 暴伤" if mod.crit_damage else "",
            "暴击 双暴" if (mod.crit_chance or mod.blood_rush_cc) else "",
            "多重" if mod.multishot else "",
            "射速 攻速" if mod.fire_rate else "",
            "基伤 伤害" if (mod.damage or mod.co_per_status) else "",
            "范围" if mod.range_m else "",
            "穿透" if mod.punch_through else "",
            "触发" if (mod.status_chance or mod.weeping_sc) else "",
            "异伤 异常" if mod.status_damage else "",
            "重击" if (mod.heavy_dmg or mod.initial_combo) else "",
            "震地" if mod.slam_dmg else "",
            "切割" if mod.hunter_munitions else "",
        ]
    )

    for tok in tokens:
        if tok in haystack:
            continue
        # Compound & single element expansion (e.g. "腐蚀" -> toxin + electricity + corrosive!)
        expanded_elems = _COMPOUND_QUERY_EXPANSION.get(tok)
        if expanded_elems and mod.element in expanded_elems:
            continue
        # Also allow substring match for compound keywords like "腐" or "病"
        matched_comp = False
        if mod.element:
            for k, elems in _COMPOUND_QUERY_EXPANSION.items():
                if len(tok) >= 2 and tok in k and mod.element in elems:
                    matched_comp = True
                    break
        if not matched_comp:
            return False
    return True


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
        "elem_values": {},
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
                ek = _RIVEN_ELEM_MAP[name]
                prof["base_elements"].add(ek)  # type: ignore[union-attr]
                ev_map: Dict[str, float] = prof["elem_values"]  # type: ignore[assignment]
                ev_map[ek] = ev_map.get(ek, 0.0) + delta
        elif name in _RIVEN_COMPOUND_MAP and not se.is_negative:
            prof["elem_total"] = float(prof["elem_total"]) + delta
            ck = _RIVEN_COMPOUND_MAP[name]
            prof["compound_elements"].add(ck)  # type: ignore[union-attr]
            ev_map = prof["elem_values"]  # type: ignore[assignment]
            ev_map[ck] = ev_map.get(ck, 0.0) + delta
        elif name in ("冲击伤害", "穿刺伤害", "切割伤害"):
            prof["ips_total"] = float(prof["ips_total"]) + delta
    return prof


_COMPOUND_PAIRS: Tuple[Tuple[str, str, str], ...] = (
    ("toxin", "cold", "viral"),
    ("toxin", "electricity", "corrosive"),
    ("toxin", "heat", "gas"),
    ("heat", "electricity", "radiation"),
    ("cold", "electricity", "magnetic"),
    ("heat", "cold", "blast"),
)


def _combine_elements_with_values(
    base_values: Dict[str, float],
    compound_values: Dict[str, float],
    *,
    prefer_influence: bool = False,
) -> Tuple[Set[str], Set[str], Dict[str, float]]:
    """Combine ALL Warframe element pairs completely (never leaving 2 uncombined base elements!).

    Returns `(compound_set, remaining_base_set, final_element_pct_map)`.
    """
    rem_vals: Dict[str, float] = {k: v for k, v in base_values.items() if v > 0}
    final_vals: Dict[str, float] = {k: v for k, v in compound_values.items() if v > 0}
    comps: Set[str] = set(final_vals.keys())

    # On Melee Influence, reserve standalone Electricity first, then combine remaining pairs
    saved_elec = 0.0
    if prefer_influence and "electricity" in rem_vals:
        saved_elec = rem_vals.pop("electricity")

    for e1, e2, comp in _COMPOUND_PAIRS:
        if e1 in rem_vals and e2 in rem_vals:
            v1 = rem_vals.pop(e1)
            v2 = rem_vals.pop(e2)
            comps.add(comp)
            final_vals[comp] = final_vals.get(comp, 0.0) + v1 + v2

    if saved_elec > 0:
        rem_vals["electricity"] = saved_elec

    for rk, rv in rem_vals.items():
        final_vals[rk] = final_vals.get(rk, 0.0) + rv

    return comps, set(rem_vals.keys()), final_vals


def _combine_elements(
    base_elems: Set[str],
    compound_elems: Set[str],
    *,
    prefer_influence: bool = False,
) -> Tuple[Set[str], Set[str]]:
    """Simulate complete Warframe elemental combination across all 6 compound pairs."""
    b_map = {e: 1.0 for e in base_elems}
    c_map = {c: 1.0 for c in compound_elems}
    comps, rem, _ = _combine_elements_with_values(b_map, c_map, prefer_influence=prefer_influence)
    return comps, rem


def _score_build(
    ev: RivenEvaluation,
    riven_prof: Optional[Dict[str, object]],
    mods: Sequence[ModDef],
    arcane: Optional[ArcaneDef],
    scenario: str,
    archetype_id: str = ARCHETYPE_STANDARD,
) -> Tuple[float, Set[str], Set[str]]:
    """Compute realistic expected combat DPS score for `(weapon + riven + mods + arcane + archetype)`."""
    is_melee = ev.weapon_class == "melee"
    is_non_crit = archetype_id == ARCHETYPE_NON_CRIT or ev.matched_archetype_id == "non_crit_devour"
    is_heavy = is_melee and archetype_id in (ARCHETYPE_HEAVY_ATTACK, ARCHETYPE_HEAVY_SLAM)
    is_slam = is_melee and archetype_id == ARCHETYPE_HEAVY_SLAM

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
        if m.id in ("galvanized_scope", "galvanized_crosshairs"):
            # Requires continuous aimed headshot kills -> realistic uptime by scenario
            hs_uptime = 0.85 if scenario == SCENARIO_BOSS_DIRECT else 0.42
            cc_mod += m.crit_chance * hs_uptime
        elif is_heavy and m.id in ("sacrificial_steel", "true_steel", "galvanized_steel", "amalgam_true_steel"):
            # Steel mods double their Crit Chance on Heavy Attacks in Warframe!
            cc_mod += m.crit_chance * 2.0
        elif is_heavy and m.blood_rush_cc > 0:
            # Heavy builds spend combo frequently -> Blood Rush operates at ~4x instead of permanent 12x
            cc_mod += m.blood_rush_cc * 0.30
        else:
            cc_mod += m.crit_chance + m.blood_rush_cc
        cd_mod += m.crit_damage
        fr += m.fire_rate * (0.45 if is_heavy else 1.0)
        sc_mod += (m.status_chance + m.weeping_sc * (0.30 if is_heavy else 1.0))
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
            if m.element in ("viral", "corrosive", "radiation", "magnetic", "gas", "blast"):
                comp_elems.add(m.element)
            else:
                base_elems.add(m.element)
            elem += m.element_value
        if m.ips_value > 0:
            elem += m.ips_value * 0.25

    prefer_influence = bool(arcane is not None and arcane.influence_aoe and is_melee)
    if arcane is not None:
        dmg += arcane.damage
        if not is_melee:
            ms += arcane.multishot
        cd_mod += arcane.crit_damage
        cc_mod += arcane.crit_chance
        weakpoint += arcane.weakpoint * (0.85 if scenario == SCENARIO_BOSS_DIRECT else 0.15)
        reload_bonus += arcane.reload
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

    # Arcane conditional activation checks — require UNCOMBINED standalone element in `rem_elems`!
    # (e.g. Toxin+Cold merges into Viral -> 0 standalone Toxin/Cold procs -> 0 stacks for Blight/Frostbite!)
    if arcane is not None:
        if arcane.id == "primary_frostbite" and "cold" not in rem_elems:
            cd_mod -= arcane.crit_damage
            ms -= arcane.multishot
        elif arcane.id == "primary_blight" and "toxin" not in rem_elems:
            cd_mod -= arcane.crit_damage
            ms -= arcane.multishot
        elif arcane.id == "cascadia_flare" and "heat" not in rem_elems:
            dmg -= arcane.damage

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
            if is_heavy:
                inf_scale = 0.45  # Heavy/Slam builds focus on single huge hits rather than light-attack spread
            else:
                inf_scale = 2.40 if scenario != SCENARIO_BOSS_DIRECT else 0.35
            arch_boost = 1.35 if archetype_id == ARCHETYPE_MELEE_INFLUENCE else 1.0
            elem_synergy *= 1.0 + inf_scale * arch_boost * max(0.45, proc_uptime) * (0.75 + 0.30 * st_dmg)
        else:
            elem_synergy *= 0.85
    elif arcane is not None and arcane.afflictions_bonus and is_melee:
        elem_synergy *= 1.0 + (0.55 if is_heavy else 0.22) * st_dmg * max(0.35, proc_uptime)

    # Utility / AoE / Heavy / Sustained uptime scaling
    utility = 1.0
    if is_melee:
        range_w = 0.085 if (scenario != SCENARIO_BOSS_DIRECT and not is_heavy) else 0.03
        utility *= max(0.70, 1.0 + range_m * range_w)
        if is_heavy:
            # Heavy Attack / Slam builds multiply damage by Combo Multiplier (Initial Combo) + Heavy Wind-Up + Heavy/Slam DMG!
            combo_mult = 1.0 + min(11.0, max(0.0, init_combo) / 20.0)
            heavy_mult = 1.0 + max(0.0, heavy_dmg) * 1.15
            slam_mult = 1.0 + max(0.0, slam_dmg) * (1.35 if is_slam else 0.20)
            # Amalgam Organ Shatter / Killing Blow wind-up bonus
            windup_bonus = 1.0 + (0.35 if any(m.id in ("amalgam_organ_shatter", "killing_blow") for m in mods) else 0.0)
            utility *= combo_mult * heavy_mult * slam_mult * windup_bonus
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


def compute_build_stat_summary(
    ev: RivenEvaluation,
    riven_prof: Optional[Dict[str, object]],
    mods: Sequence[ModDef],
    arcane: Optional[ArcaneDef],
    scenario: str = SCENARIO_STEEL_PATH,
) -> BuildStatSummary:
    """Compute human-readable overall build stats with concrete values + percentages & per-element breakdown."""
    w_cls = (ev.weapon_class or "rifle").strip().lower()
    is_melee = w_cls == "melee"
    w_cc = float(ev.weapon_crit_chance) if (ev.weapon_crit_chance and ev.weapon_crit_chance > 0) else (0.24 if is_melee else 0.22)
    w_cd = float(ev.weapon_crit_mult) if (ev.weapon_crit_mult and ev.weapon_crit_mult > 1.0) else (2.2 if is_melee else 2.0)
    w_sc = float(ev.weapon_status_chance) if (ev.weapon_status_chance and ev.weapon_status_chance > 0) else 0.20

    base_stats = get_weapon_base_stats(ev.weapon_slug, ev.weapon_name_en, w_cls)
    w_base_dmg = float(base_stats.get("base_damage", 100.0))
    w_base_fr = float(base_stats.get("fire_rate", 1.0))
    w_base_ms = float(base_stats.get("multishot", 1.0))
    w_base_rng = float(base_stats.get("range_m", 2.5 if is_melee else 0.0))

    dmg = 0.0
    ms = 0.0
    cc_mod = 0.0
    cd_mod = 0.0
    fr = 0.0
    sc_mod = 0.0
    st_dmg = 0.0
    faction = 0.0
    elem = 0.0
    range_m = 0.0
    punch_through = 0.0
    reload_b = 0.0
    co_per_status = 0.0
    base_elem_vals: Dict[str, float] = {}
    comp_elem_vals: Dict[str, float] = {}

    if riven_prof is not None:
        dmg += float(riven_prof["damage"])
        if not is_melee:
            ms += float(riven_prof["multishot"])
        cc_mod += float(riven_prof["crit_chance"])
        cd_mod += float(riven_prof["crit_damage"])
        fr += float(riven_prof["fire_rate"])
        sc_mod += float(riven_prof["status_chance"])
        st_dmg += float(riven_prof["status_damage"])
        faction += float(riven_prof["faction"])
        elem += float(riven_prof["elem_total"])
        range_m += float(riven_prof["range_m"])
        punch_through += float(riven_prof["punch_through"])
        reload_b += float(riven_prof["reload"])
        r_ev: Dict[str, float] = riven_prof.get("elem_values") or {}  # type: ignore[assignment]
        for ek, val in r_ev.items():
            if val <= 0:
                continue
            if ek in ("viral", "corrosive", "radiation", "magnetic", "gas", "blast"):
                comp_elem_vals[ek] = comp_elem_vals.get(ek, 0.0) + val
            else:
                base_elem_vals[ek] = base_elem_vals.get(ek, 0.0) + val

    for m in mods:
        dmg += m.damage
        if not is_melee:
            ms += m.multishot
        cc_mod += m.crit_chance + m.blood_rush_cc
        cd_mod += m.crit_damage
        fr += m.fire_rate
        sc_mod += m.status_chance + m.weeping_sc
        st_dmg += m.status_damage
        faction += m.faction
        range_m += m.range_m
        punch_through += m.punch_through
        reload_b += m.reload
        co_per_status += m.co_per_status
        if m.element and m.element_value > 0:
            if m.element in ("viral", "corrosive", "radiation", "magnetic", "gas", "blast"):
                comp_elem_vals[m.element] = comp_elem_vals.get(m.element, 0.0) + m.element_value
            else:
                base_elem_vals[m.element] = base_elem_vals.get(m.element, 0.0) + m.element_value
            elem += m.element_value

    prefer_influence = bool(arcane is not None and arcane.influence_aoe and is_melee)
    if arcane is not None and arcane.corrosive_bonus > 0:
        elem += arcane.corrosive_bonus
        comp_elem_vals["corrosive"] = comp_elem_vals.get("corrosive", 0.0) + arcane.corrosive_bonus

    comps, rem_elems, final_elem_vals = _combine_elements_with_values(
        base_elem_vals,
        comp_elem_vals,
        prefer_influence=prefer_influence,
    )

    if arcane is not None:
        arc_active = True
        if arcane.id == "primary_frostbite" and "cold" not in rem_elems:
            arc_active = False
        elif arcane.id == "primary_blight" and "toxin" not in rem_elems:
            arc_active = False
        elif arcane.id == "cascadia_flare" and "heat" not in rem_elems:
            arc_active = False
        if arc_active:
            dmg += arcane.damage
            if not is_melee:
                ms += arcane.multishot
            cd_mod += arcane.crit_damage
            cc_mod += arcane.crit_chance
            sc_mod += arcane.status_chance
            reload_b += arcane.reload

    distinct_statuses = 2.0 + len(comps) + len(rem_elems)
    if co_per_status > 0:
        dmg += co_per_status * min(5.0, distinct_statuses)

    modded_base_hit = max(1.0, w_base_dmg * max(0.15, 1.0 + dmg))
    eff_total_hit = modded_base_hit * (1.0 + max(0.0, elem))
    eff_ms = max(0.1, w_base_ms * max(0.15, 1.0 + ms))
    eff_fr = max(0.1, w_base_fr * max(0.25, 1.0 + fr))
    eff_rng = max(0.0, w_base_rng + range_m) if is_melee else 0.0
    eff_cc = max(0.0, w_cc * (1.0 + cc_mod)) * 100.0
    eff_cd = max(1.0, w_cd * (1.0 + cd_mod))
    eff_sc = max(0.0, w_sc * (1.0 + sc_mod)) * 100.0

    # Per-element concrete damage & percentage breakdown (sorted highest % first)
    elem_order = sorted(final_elem_vals.items(), key=lambda kv: (-kv[1], kv[0]))
    elem_breakdown: List[Tuple[str, str, float, float]] = []
    for ek, ratio in elem_order:
        if ratio <= 0:
            continue
        el_dmg = round(modded_base_hit * ratio, 1)
        el_pct = round(ratio * 100.0, 1)
        elem_breakdown.append((_ELEM_LABEL_ZH.get(ek, ek), _ELEM_LABEL_EN.get(ek, ek), el_dmg, el_pct))

    return BuildStatSummary(
        weapon_class=w_cls,
        damage_bonus_pct=round(dmg * 100.0, 1),
        eff_damage=round(eff_total_hit, 1),
        multishot_pct=round(ms * 100.0, 1),
        eff_multishot=round(eff_ms, 2),
        crit_chance_bonus_pct=round(cc_mod * 100.0, 1),
        eff_crit_chance_pct=round(eff_cc, 1),
        crit_damage_bonus_pct=round(cd_mod * 100.0, 1),
        eff_crit_mult=round(eff_cd, 2),
        fire_rate_pct=round(fr * 100.0, 1),
        eff_fire_rate=round(eff_fr, 2),
        status_chance_bonus_pct=round(sc_mod * 100.0, 1),
        eff_status_chance_pct=round(eff_sc, 1),
        elemental_pct=round(elem * 100.0, 1),
        element_breakdown=tuple(elem_breakdown),
        status_damage_pct=round(st_dmg * 100.0, 1),
        faction_pct=round(faction * 100.0, 1),
        range_m=round(range_m, 2),
        eff_range_m=round(eff_rng, 2),
        punch_through_m=round(punch_through, 2),
        reload_pct=round(reload_b * 100.0, 1),
    )


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
    archetype_id: str = ARCHETYPE_STANDARD,
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
            sc, _, _ = _score_build(ev, riven_prof, chosen + [cand], arcane, scenario, archetype_id=archetype_id)
            if sc > best_score:
                best_score = sc
                best_next = cand

        # 2-step lookahead for elemental pairs when >= 2 slots remain
        if len(chosen) <= slots - 2:
            base_now, _, _ = _score_build(ev, riven_prof, chosen, arcane, scenario, archetype_id=archetype_id)
            best_pair_avg = (best_score - base_now) if best_score > base_now else 0.0
            for i, m1 in enumerate(elem_mods):
                if _conflict_with(m1, chosen):
                    continue
                for m2 in elem_mods[i + 1 :]:
                    if m1.element == m2.element or _conflict_with(m2, chosen) or _conflict_with(m2, [m1]):
                        continue
                    sc2, _, _ = _score_build(ev, riven_prof, chosen + [m1, m2], arcane, scenario, archetype_id=archetype_id)
                    avg_gain = (sc2 - base_now) * 0.52
                    if avg_gain > best_pair_avg:
                        best_pair_avg = avg_gain
                        best_next = m1

        if best_next is None:
            break
        chosen.append(best_next)

    # Step 2: Coordinate-descent 1-swap local refinement until convergence
    cur_score, cur_comps, cur_rem = _score_build(ev, riven_prof, chosen, arcane, scenario, archetype_id=archetype_id)
    for _ in range(4):
        improved = False
        for idx in range(len(chosen)):
            others = chosen[:idx] + chosen[idx + 1 :]
            best_m = chosen[idx]
            best_sc = cur_score
            for cand in pool:
                if cand.id == chosen[idx].id or _conflict_with(cand, others):
                    continue
                sc, _, _ = _score_build(ev, riven_prof, others + [cand], arcane, scenario, archetype_id=archetype_id)
                if sc > best_sc * 1.0005:
                    best_sc = sc
                    best_m = cand
            if best_m.id != chosen[idx].id:
                chosen[idx] = best_m
                cur_score = best_sc
                improved = True
        if not improved:
            break

    cur_score, cur_comps, cur_rem = _score_build(ev, riven_prof, chosen, arcane, scenario, archetype_id=archetype_id)
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
    archetype: str = ARCHETYPE_AUTO,
) -> BuildOptimizationResult:
    """Find the optimal 7 Mods + Arcane complementing the scanned Riven & hot meta archetype (<30ms offline)."""
    w_cls = (ev.weapon_class or "rifle").strip().lower()
    riven_prof = _extract_riven_profile(ev)

    if not archetype or archetype == ARCHETYPE_AUTO:
        arch_id, arch_zh, arch_en = detect_meta_archetype(ev, riven_prof)
    else:
        arch_id = archetype
        arch_zh = ARCHETYPE_LABELS_ZH.get(arch_id, arch_id)
        arch_en = ARCHETYPE_LABELS_EN.get(arch_id, arch_id)

    pool = get_mod_pool(
        w_cls,
        allow_bane_mods=allow_bane_mods,
        optimizer_only=True,
        weapon_slug=ev.weapon_slug,
        weapon_name_en=ev.weapon_name_en,
    )
    # Include Heavy/Slam core mods (e.g. Corrupt Charge, Focus Radon, Volatile Quick Return) when Heavy archetype active
    if w_cls == "melee" and arch_id in (ARCHETYPE_HEAVY_ATTACK, ARCHETYPE_HEAVY_SLAM):
        full_melee = get_mod_pool(
            w_cls,
            allow_bane_mods=allow_bane_mods,
            optimizer_only=False,
            weapon_slug=ev.weapon_slug,
            weapon_name_en=ev.weapon_name_en,
        )
        seen_ids = {m.id for m in pool}
        for m in full_melee:
            if m.id in _HEAVY_CORE_MOD_IDS and m.id not in seen_ids:
                pool.append(m)
                seen_ids.add(m.id)

    arcanes = get_arcane_pool(w_cls, optimizer_only=True) or [None]
    if w_cls == "melee" and arch_id in (ARCHETYPE_HEAVY_ATTACK, ARCHETYPE_HEAVY_SLAM):
        all_m_arcs = get_arcane_pool(w_cls, optimizer_only=False)
        for a in all_m_arcs:
            if a.id == "melee_animosity" and all(x is None or x.id != a.id for x in arcanes):
                arcanes.append(a)

    best_mods: List[ModDef] = []
    best_arcane: Optional[ArcaneDef] = None
    best_score = -1.0
    best_comps: Set[str] = set()
    best_rem: Set[str] = set()

    for arc in arcanes:
        mods, sc, comps, rem = _optimize_for_arcane(
            ev, riven_prof, pool, arc, scenario, slots=7, archetype_id=arch_id
        )
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
        sc_without, _, _ = _score_build(ev, riven_prof, without_m, best_arcane, scenario, archetype_id=arch_id)
        gain_pct = round(((best_score / sc_without) - 1.0) * 100.0, 1) if sc_without > 0 else 0.0
        slot_results.append(OptimizedModSlot(mod=m, marginal_gain_pct=gain_pct))
    slot_results.sort(key=lambda s: s.marginal_gain_pct, reverse=True)

    sc_no_riven, _, _ = _score_build(ev, None, best_mods, best_arcane, scenario, archetype_id=arch_id)
    riven_gain_pct = round(((best_score / sc_no_riven) - 1.0) * 100.0, 1) if sc_no_riven > 0 else 0.0

    # Format active elements & smart human-readable explanations
    elem_order = sorted(best_comps) + sorted(best_rem)
    active_zh = [_ELEM_LABEL_ZH.get(e, e) for e in elem_order]
    active_en = [_ELEM_LABEL_EN.get(e, e) for e in elem_order]

    rationale_zh: List[str] = []
    rationale_en: List[str] = []

    if arch_id == ARCHETYPE_NON_CRIT:
        rationale_zh.append(f"已拉取热门流派【{arch_zh}】→ 自动剔除暴击率/爆伤卡，全力堆叠多重、射速、元素与基伤乘区")
        rationale_en.append(f"Meta Archetype [{arch_en}] → stripped crit mods to maximize MS, Fire Rate & elemental scaling")
    elif arch_id == ARCHETYPE_HEAVY_SLAM:
        rationale_zh.append(f"已拉取热门流派【{arch_zh}】→ 自动强化初始连击、重击蓄力与震地攻击乘区（牺牲斩铁双倍暴击）")
        rationale_en.append(f"Meta Archetype [{arch_en}] → prioritized Initial Combo, Heavy Wind-Up & Slam multipliers")
    elif arch_id == ARCHETYPE_HEAVY_ATTACK:
        rationale_zh.append(f"已拉取热门流派【{arch_zh}】→ 自动搭配重击/初始连击体系（牺牲斩铁重击双倍暴击 + 一击必杀/并合肢解）")
        rationale_en.append(f"Meta Archetype [{arch_en}] → built around Initial Combo, Heavy Attack Damage & Sacrificial Steel 2x crit")
    elif arch_id == ARCHETYPE_INCARNON_META:
        rationale_zh.append(f"已拉取热门灵化流派【{arch_zh}】→ 按当前武器灵化定位与紫卡词条自动避开稀释")
        rationale_en.append(f"Incarnon Meta Archetype [{arch_en}] → balanced high-synergy multipliers around your Riven")

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
        rationale_zh.append("保留独立【电击】触发【近战·侵染】大范围异常扩散清图体系")
        rationale_en.append("Preserved standalone [Electricity] to trigger [Melee Influence] AoE spread")

    if not rationale_zh:
        rationale_zh.append("已按当前紫卡词条自动平衡双暴、多重、射速与元素乘区，避免重复稀释")
        rationale_en.append("Balanced Crit, Multishot, Fire Rate & Elemental multipliers around your scanned Riven")

    stat_summary = compute_build_stat_summary(ev, riven_prof, best_mods, best_arcane, scenario)
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
        delta_vs_recommended_pct=0.0,
        stat_summary=stat_summary,
        exilus_mod=None,
        active_archetype_id=arch_id,
        active_archetype_zh=arch_zh,
        active_archetype_en=arch_en,
    )


def evaluate_custom_build_for_riven(
    ev: RivenEvaluation,
    mod_ids: Sequence[Optional[str]],
    arcane_id: Optional[str],
    *,
    scenario: str = SCENARIO_STEEL_PATH,
    reference_score: Optional[float] = None,
    exilus_mod_id: Optional[str] = None,
    archetype: str = ARCHETYPE_AUTO,
) -> BuildOptimizationResult:
    """Evaluate a user-customized 0..7 Mod + Exilus (#9) + Arcane combination around the scanned Riven (<2ms)."""
    w_cls = (ev.weapon_class or "rifle").strip().lower()
    pool = get_mod_pool(
        w_cls,
        allow_bane_mods=True,
        optimizer_only=False,
        weapon_slug=ev.weapon_slug,
        weapon_name_en=ev.weapon_name_en,
    )
    mod_by_id: Dict[str, ModDef] = {m.id: m for m in pool}
    arc_by_id: Dict[str, ArcaneDef] = {a.id: a for a in get_arcane_pool(w_cls, optimizer_only=False)}

    chosen_mods: List[ModDef] = []
    seen_ids: Set[str] = set()
    for mid in list(mod_ids)[:7]:
        if not mid:
            continue
        m = mod_by_id.get(str(mid).strip())
        if m is not None and m.id not in seen_ids:
            chosen_mods.append(m)
            seen_ids.add(m.id)

    exilus_mod: Optional[ModDef] = None
    if exilus_mod_id:
        cand_ex = mod_by_id.get(str(exilus_mod_id).strip())
        if cand_ex is not None and cand_ex.id not in seen_ids:
            exilus_mod = cand_ex

    chosen_arcane: Optional[ArcaneDef] = arc_by_id.get(str(arcane_id).strip()) if arcane_id else None
    riven_prof = _extract_riven_profile(ev)

    if not archetype or archetype == ARCHETYPE_AUTO:
        arch_id, arch_zh, arch_en = detect_meta_archetype(ev, riven_prof)
    else:
        arch_id = archetype
        arch_zh = ARCHETYPE_LABELS_ZH.get(arch_id, arch_id)
        arch_en = ARCHETYPE_LABELS_EN.get(arch_id, arch_id)

    all_active_mods = chosen_mods + ([exilus_mod] if exilus_mod is not None else [])
    score, comps, rem = _score_build(ev, riven_prof, all_active_mods, chosen_arcane, scenario, archetype_id=arch_id)

    slot_results: List[OptimizedModSlot] = []
    for i, m in enumerate(chosen_mods):
        without_m = chosen_mods[:i] + chosen_mods[i + 1 :] + ([exilus_mod] if exilus_mod is not None else [])
        sc_without, _, _ = _score_build(ev, riven_prof, without_m, chosen_arcane, scenario, archetype_id=arch_id)
        gain_pct = round(((score / sc_without) - 1.0) * 100.0, 1) if sc_without > 0 else 0.0
        slot_results.append(OptimizedModSlot(mod=m, marginal_gain_pct=gain_pct))

    sc_no_riven, _, _ = _score_build(ev, None, all_active_mods, chosen_arcane, scenario, archetype_id=arch_id)
    riven_gain_pct = round(((score / sc_no_riven) - 1.0) * 100.0, 1) if sc_no_riven > 0 else 0.0

    delta_vs_rec: Optional[float] = None
    if reference_score is not None and reference_score > 0:
        delta_vs_rec = round(((score / reference_score) - 1.0) * 100.0, 1)

    elem_order = sorted(comps) + sorted(rem)
    active_zh = [_ELEM_LABEL_ZH.get(e, e) for e in elem_order]
    active_en = [_ELEM_LABEL_EN.get(e, e) for e in elem_order]

    rationale_zh: List[str] = []
    rationale_en: List[str] = []

    if chosen_arcane is not None:
        if chosen_arcane.id == "melee_influence" and "electricity" not in rem:
            rationale_zh.append("⚠ 当前赋能为【近战·侵染】，但配卡缺少独立【电击】元素，无法稳定触发大范围扩散")
            rationale_en.append("⚠ [Melee Influence] requires standalone [Electricity] to trigger AoE spread")
        elif chosen_arcane.id == "primary_frostbite" and "cold" not in rem:
            rationale_zh.append("⚠ 当前赋能为【主要·霜冻】，但成型元素中无独立【冰冻】（已被合成为复合元素或未装配），无法叠层")
            rationale_en.append("⚠ [Primary Frostbite] requires uncombined standalone [Cold] procs to stack")
        elif chosen_arcane.id == "primary_blight" and "toxin" not in rem:
            rationale_zh.append("⚠ 当前赋能为【主要·毒侵】，但成型元素中无独立【毒素】（已被合成为病毒/腐蚀等复合元素或未装配），无法叠层")
            rationale_en.append("⚠ [Primary Blight] requires uncombined standalone [Toxin] procs to stack")
        elif chosen_arcane.id == "cascadia_flare" and "heat" not in rem:
            rationale_zh.append("⚠ 当前赋能为【瀑流·耀炎】，但成型元素中无独立【火焰】触发，建议补火卡或改用无情/死首")
            rationale_en.append("⚠ [Cascadia Flare] requires uncombined standalone [Heat] procs; add a Heat mod or switch arcane")

    if len(chosen_mods) < 7:
        empty_n = 7 - len(chosen_mods)
        rationale_zh.append(f"当前有 {empty_n} 个空闲 Mod 槽位，可在卡片下拉菜单中继续补齐")
        rationale_en.append(f"{empty_n} empty Mod slot(s) remaining; pick a Mod from any slot dropdown to fill")

    if delta_vs_rec is not None:
        if delta_vs_rec > 0.1:
            rationale_zh.append(f"已应用你的手动配卡，本地模型预计比自动推荐高 {delta_vs_rec:+.1f}% DPS")
            rationale_en.append(f"Custom build evaluated at {delta_vs_rec:+.1f}% vs auto-recommended baseline")
        elif delta_vs_rec < -0.1:
            rationale_zh.append(f"已应用你的手动配卡（相对自动推荐 {delta_vs_rec:+.1f}% DPS），可根据手感保留或点「恢复自动推荐」")
            rationale_en.append(f"Custom build evaluated at {delta_vs_rec:+.1f}% vs auto-recommended (click Reset anytime)")
        else:
            rationale_zh.append("当前自定义配卡与自动推荐强度基本持平，可一键跳转 WFSim 精确逐帧验证")
            rationale_en.append("Custom build matches the auto-recommended DPS closely; verify in WFSim anytime")

    stat_summary = compute_build_stat_summary(ev, riven_prof, all_active_mods, chosen_arcane, scenario)
    return BuildOptimizationResult(
        weapon_name=ev.weapon_name or ev.weapon_name_en or "",
        weapon_class=w_cls,
        scenario=scenario,
        mods=slot_results,
        arcane=chosen_arcane,
        total_dps_index=round(score, 2),
        riven_gain_pct=riven_gain_pct,
        active_elements_zh=active_zh,
        active_elements_en=active_en,
        rationale_zh=rationale_zh,
        rationale_en=rationale_en,
        delta_vs_recommended_pct=delta_vs_rec,
        stat_summary=stat_summary,
        exilus_mod=exilus_mod,
        active_archetype_id=arch_id,
        active_archetype_zh=arch_zh,
        active_archetype_en=arch_en,
    )
