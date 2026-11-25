"""Regex-based parser for Simplified Chinese Warframe Riven Mod OCR output.

Extracts structured fields from raw OCR text lines:
  - weapon_name (武器名称)
  - riven_suffix & riven_name (紫卡词缀与完整名称)
  - mastery_rank (段位需求)
  - reroll_count (循环/洗卡次数)
  - drain (容量消耗, optional)
  - positive_stats / negative_stats / stats (所有正负词条与数值)
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.i18n import detect_text_language


# Canonical bilingual (Simplified Chinese + English) Riven stat mapping
# Keys are compacted lowercase strings (whitespace stripped) -> canonical stat name.
STAT_CANONICAL_MAP: Dict[str, str] = {
    # Crit & Multi & Base Damage
    "暴击几率": "暴击几率",
    "暴击率": "暴击几率",
    "爆击几率": "暴击几率",
    "暴击几串": "暴击几率",
    "暴击机率": "暴击几率",
    "criticalchance": "暴击几率",
    "critchance": "暴击几率",
    "暴击伤害": "暴击伤害",
    "爆击伤害": "暴击伤害",
    "criticaldamage": "暴击伤害",
    "critdamage": "暴击伤害",
    "多重射击": "多重射击",
    "多重射出": "多重射击",
    "multishot": "多重射击",
    "伤害": "伤害",
    "近战伤害": "伤害",
    "damage": "伤害",
    "meleedamage": "伤害",
    "basedamage": "伤害",
    # Status
    "触发几率": "触发几率",
    "触发机率": "触发几率",
    "statuschance": "触发几率",
    "触发时间": "触发时间",
    "触发持续时间": "触发时间",
    "statusduration": "触发时间",
    "异常状态伤害": "异常状态伤害",
    "statusdamage": "异常状态伤害",
    # Speed & Reload & Ammo
    "攻击速度": "攻击速度",
    "attackspeed": "攻击速度",
    "射速": "射速",
    "firerate": "射速",
    "装填速度": "装填速度",
    "reloadspeed": "装填速度",
    "弹匣容量": "弹匣容量",
    "弹夹容量": "弹匣容量",
    "magazinecapacity": "弹匣容量",
    "弹药最大值": "弹药最大值",
    "ammomaximum": "弹药最大值",
    "ammomax": "弹药最大值",
    "弹药效率": "弹药效率",
    "ammoefficiency": "弹药效率",
    "收起武器时弹匣每秒自动装填": "收起武器时弹匣每秒自动装填",
    "magazinereloaded/swhenholstered": "收起武器时弹匣每秒自动装填",
    # Elemental & Combined Elemental
    "火焰伤害": "火焰伤害",
    "heat": "火焰伤害",
    "heatdamage": "火焰伤害",
    "冰冻伤害": "冰冻伤害",
    "cold": "冰冻伤害",
    "colddamage": "冰冻伤害",
    "电击伤害": "电击伤害",
    "electricity": "电击伤害",
    "electric": "电击伤害",
    "electricdamage": "电击伤害",
    "毒素伤害": "毒素伤害",
    "toxin": "毒素伤害",
    "toxindamage": "毒素伤害",
    "爆炸伤害": "爆炸伤害",
    "blast": "爆炸伤害",
    "blastdamage": "爆炸伤害",
    "腐蚀伤害": "腐蚀伤害",
    "corrosive": "腐蚀伤害",
    "corrosivedamage": "腐蚀伤害",
    "毒气伤害": "毒气伤害",
    "gas": "毒气伤害",
    "gasdamage": "毒气伤害",
    "磁力伤害": "磁力伤害",
    "magnetic": "磁力伤害",
    "magneticdamage": "磁力伤害",
    "辐射伤害": "辐射伤害",
    "radiation": "辐射伤害",
    "radiationdamage": "辐射伤害",
    "病毒伤害": "病毒伤害",
    "viral": "病毒伤害",
    "viraldamage": "病毒伤害",
    # Physical (IPS)
    "冲击伤害": "冲击伤害",
    "impact": "冲击伤害",
    "impactdamage": "冲击伤害",
    "穿刺伤害": "穿刺伤害",
    "puncture": "穿刺伤害",
    "puncturedamage": "穿刺伤害",
    "切割伤害": "切割伤害",
    "slash": "切割伤害",
    "slashdamage": "切割伤害",
    # Utility / Handling / Weakpoint
    "穿透": "穿透",
    "punchthrough": "穿透",
    "投射物速度": "投射物速度",
    "投射物飞行速度": "投射物速度",
    "弹道速度": "投射物速度",
    "projectilespeed": "投射物速度",
    "flightspeed": "投射物速度",
    "变焦": "变焦",
    "zoom": "变焦",
    "武器后坐力": "武器后坐力",
    "武器后座力": "武器后坐力",
    "后坐力": "武器后坐力",
    "weaponrecoil": "武器后坐力",
    "recoil": "武器后坐力",
    "弱点伤害": "弱点伤害",
    "weakpointdamage": "弱点伤害",
    "弱点暴击几率": "弱点暴击几率",
    "weakpointcriticalchance": "弱点暴击几率",
    # Melee specific
    "攻击范围": "攻击范围",
    "range": "攻击范围",
    "meleerange": "攻击范围",
    "连击持续时间": "连击持续时间",
    "comboduration": "连击持续时间",
    "初始连击": "初始连击",
    "initialcombo": "初始连击",
    "近战重击效率": "重击效率",
    "重击效率": "重击效率",
    "heavyattackefficiency": "重击效率",
    "重击准备速度": "重击准备速度",
    "heavyattackwindupspeed": "重击准备速度",
    "在重击时的近战伤害": "在重击时的近战伤害",
    "meleedamageonheavyattack": "在重击时的近战伤害",
    "震地攻击伤害": "震地攻击伤害",
    "slamattackdamage": "震地攻击伤害",
    "招架角度": "招架角度",
    "parryangle": "招架角度",
    "处决伤害": "处决伤害",
    "终结伤害": "处决伤害",
    "finisherdamage": "处决伤害",
    "滑行攻击时有几率造成暴击": "滑行攻击暴击几率",
    "滑行攻击暴击几率": "滑行攻击暴击几率",
    "criticalchanceforslideattack": "滑行攻击暴击几率",
    "criticalchanceonslideattack": "滑行攻击暴击几率",
    "critchanceonslideattack": "滑行攻击暴击几率",
    "critchanceforslideattack": "滑行攻击暴击几率",
    "slideattackcritchance": "滑行攻击暴击几率",
    "额外连击几率": "额外连击几率",
    "额外连击数几率": "额外连击几率",
    "获得额外连击数的几率": "额外连击几率",
    "攻击时有几率获得额外连击数": "额外连击几率",
    "的几率来获得连击数": "额外连击几率",
    "的几率来获得额外连击数": "额外连击几率",
    "的几率获得连击数": "额外连击几率",
    "的几率获得额外连击数": "额外连击几率",
    "有几率不获得连击数": "额外连击几率",
    "来获得连击数": "额外连击几率",
    "获得连击数": "额外连击几率",
    "additionalcombocountchance": "额外连击几率",
    "chancetogainextracombocount": "额外连击几率",
    "chancetogaincombocount": "额外连击几率",
    "chancetonotgaincombocount": "额外连击几率",
    # Faction damage
    "对grineer伤害": "对 Grineer 伤害",
    "对grineer的伤害": "对 Grineer 伤害",
    "damagetogrineer": "对 Grineer 伤害",
    "damagevsgrineer": "对 Grineer 伤害",
    "对corpus伤害": "对 Corpus 伤害",
    "对corpus的伤害": "对 Corpus 伤害",
    "damagetocorpus": "对 Corpus 伤害",
    "damagevscorpus": "对 Corpus 伤害",
    "对infested伤害": "对 Infested 伤害",
    "对infested的伤害": "对 Infested 伤害",
    "damagetoinfested": "对 Infested 伤害",
    "damagevsinfested": "对 Infested 伤害",
    "对奥罗金伤害": "对奥罗金伤害",
    "对奥罗金的伤害": "对奥罗金伤害",
    "damagetoorokin": "对奥罗金伤害",
    "damagevsorokin": "对奥罗金伤害",
    "对炽蛇军伤害": "对炽蛇军伤害",
    "对炽蛇军的伤害": "对炽蛇军伤害",
    "damagetoscaldra": "对炽蛇军伤害",
    "对科腐者伤害": "对科腐者伤害",
    "对科腐者的伤害": "对科腐者伤害",
    "damagetotechrot": "对科腐者伤害",
    "对堕落者伤害": "对堕落者伤害",
    "对堕落者的伤害": "对堕落者伤害",
    "damagetocorrupted": "对堕落者伤害",
    "对低语者伤害": "对低语者伤害",
    "对低语者的伤害": "对低语者伤害",
    "damagetomurmur": "对低语者伤害",
    # --- Additional 13 Official Warframe Languages (WFCD i18n stat aliases) ---
    # Traditional Chinese (zh_TW)
    "暴擊機率": "暴击几率",
    "暴擊傷害": "暴击伤害",
    "多重射擊": "多重射击",
    "傷害": "伤害",
    "近戰傷害": "伤害",
    "觸發機率": "触发几率",
    "觸發時間": "触发时间",
    "異常狀態傷害": "异常状态伤害",
    "攻擊速度": "攻击速度",
    "射速": "射速",
    "裝填速度": "装填速度",
    "彈匣容量": "弹匣容量",
    "彈藥最大值": "弹药最大值",
    "火焰傷害": "火焰伤害",
    "冰凍傷害": "冰冻伤害",
    "電擊傷害": "电击伤害",
    "毒素傷害": "毒素伤害",
    "衝擊傷害": "冲击伤害",
    "穿刺傷害": "穿刺伤害",
    "切割傷害": "切割伤害",
    "穿透": "穿透",
    "攻擊範圍": "攻击范围",
    "連擊持續時間": "连击持续时间",
    "初始連擊": "初始连击",
    "投射物速度": "投射物速度",
    "武器後座力": "武器后坐力",
    "變焦": "变焦",
    "對grineer傷害": "对 Grineer 伤害",
    "對corpus傷害": "对 Corpus 伤害",
    "對infested傷害": "对 Infested 伤害",
    # Japanese (ja_JP)
    "クリティカル率": "暴击几率",
    "クリティカルダメージ": "暴击伤害",
    "マルチショット": "多重射击",
    "ダメージ": "伤害",
    "近接ダメージ": "伤害",
    "状態異常確率": "触发几率",
    "状態異常時間": "触发时间",
    "状態異常ダメージ": "异常状态伤害",
    "攻撃速度": "攻击速度",
    "発射速度": "射速",
    "リロード速度": "装填速度",
    "マガジン容量": "弹匣容量",
    "最大弾薬数": "弹药最大值",
    "火炎": "火焰伤害",
    "冷気": "冰冻伤害",
    "電気": "电击伤害",
    "毒": "毒素伤害",
    "衝撃": "冲击伤害",
    "貫通": "穿刺伤害",
    "切断": "切割伤害",
    "貫通距離": "穿透",
    "範囲": "攻击范围",
    "コンボ持続時間": "连击持续时间",
    "初期コンボ": "初始连击",
    "弾速": "投射物速度",
    "武器の反動": "武器后坐力",
    "ズーム": "变焦",
    "グリニアへのダメージ": "对 Grineer 伤害",
    "コーパスへのダメージ": "对 Corpus 伤害",
    "感染体へのダメージ": "对 Infested 伤害",
    # Korean (ko_KR)
    "치명타확률": "暴击几率",
    "치명타피해": "暴击伤害",
    "멀티샷": "多重射击",
    "피해": "伤害",
    "근접피해": "伤害",
    "상태이상확률": "触发几率",
    "상태이상지속시간": "触发时间",
    "상태이상피해": "异常状态伤害",
    "공격속도": "攻击速度",
    "연사력": "射速",
    "재장전속도": "装填速度",
    "탄창용량": "弹匣容量",
    "최대탄약": "弹药最大值",
    "화염": "火焰伤害",
    "냉기": "冰冻伤害",
    "전기": "电击伤害",
    "독성": "毒素伤害",
    "충격": "冲击伤害",
    "관통": "穿刺伤害",
    "베기": "切割伤害",
    "두께꿰뚫기": "穿透",
    "범위": "攻击范围",
    "연속기지속시간": "连击持续时间",
    "초기연속기": "初始连击",
    "투사체속도": "投射物速度",
    "무기반동": "武器后坐力",
    "줌": "变焦",
    "그리니어에대한피해": "对 Grineer 伤害",
    "코퍼스에대한피해": "对 Corpus 伤害",
    "인페스티드에대한피해": "对 Infested 伤害",
    # Russian (ru_RU)
    "шанскрита": "暴击几率",
    "шанскрит.урона": "暴击几率",
    "крит.урон": "暴击伤害",
    "критическийурон": "暴击伤害",
    "мультивыстрел": "多重射击",
    "урон": "伤害",
    "уронвближнембою": "伤害",
    "шансстатуса": "触发几率",
    "длительностьстатуса": "触发时间",
    "уронстатуса": "异常状态伤害",
    "скоростьатаки": "攻击速度",
    "скорострельность": "射速",
    "скоростьперезарядки": "装填速度",
    "вместимостьмагазина": "弹匣容量",
    "макс.патронов": "弹药最大值",
    "огонь": "火焰伤害",
    "холод": "冰冻伤害",
    "электричество": "电击伤害",
    "токсин": "毒素伤害",
    "удар": "冲击伤害",
    "пронзание": "穿刺伤害",
    "разрез": "切割伤害",
    "пронзаниенавылет": "穿透",
    "зонапоражения": "攻击范围",
    "длительностькомбо": "连击持续时间",
    "начальноекомбо": "初始连击",
    "скоростьполетаснаряда": "投射物速度",
    "отдачаоружия": "武器后坐力",
    "зум": "变焦",
    "уронпогринир": "对 Grineer 伤害",
    "уронпокорпусу": "对 Corpus 伤害",
    "уронпозараженным": "对 Infested 伤害",
    # Ukrainian (uk_UA)
    "імовірністькрит.шкоди": "暴击几率",
    "критичнашкода": "暴击伤害",
    "мультипостріл": "多重射击",
    "шкода": "伤害",
    "імовірністьефектустану": "触发几率",
    "тривалістьстатусу": "触发时间",
    "швидкістьатаки": "攻击速度",
    "швидкострільність": "射速",
    "швидкістьперезаряджання": "装填速度",
    "ємністьмагазину": "弹匣容量",
    "термічна": "火焰伤害",
    "крижана": "冰冻伤害",
    "електрична": "电击伤害",
    "токсична": "毒素伤害",
    "ударна": "冲击伤害",
    "пробивна": "穿刺伤害",
    "різальна": "切割伤害",
    "пробивання": "穿透",
    "досяжність": "攻击范围",
    "віддача": "武器后坐力",
    "прицілювання": "变焦",
    # French (fr_FR)
    "chancesdecritique": "暴击几率",
    "dégâtscritiques": "暴击伤害",
    "degatscritiques": "暴击伤害",
    "tirmultiple": "多重射击",
    "dégâts": "伤害",
    "degats": "伤害",
    "dégâtsdemêlée": "伤害",
    "chancesdestatut": "触发几率",
    "duréedestatut": "触发时间",
    "vitessed'attaque": "攻击速度",
    "cadencedetir": "射速",
    "vitessederechargement": "装填速度",
    "tailleduchargeur": "弹匣容量",
    "munitionsmaximum": "弹药最大值",
    "feu": "火焰伤害",
    "glace": "冰冻伤害",
    "électrique": "电击伤害",
    "poison": "毒素伤害",
    "perforation": "穿刺伤害",
    "tranchant": "切割伤害",
    "pénétration": "穿透",
    "portée": "攻击范围",
    "duréedecombo": "连击持续时间",
    "comboinitial": "初始连击",
    "vitessedesprojectiles": "投射物速度",
    "reculdel'arme": "武器后坐力",
    # German (de_DE)
    "krit.chance": "暴击几率",
    "kritischechance": "暴击几率",
    "krit.schaden": "暴击伤害",
    "kritischerschaden": "暴击伤害",
    "mehrfachschuss": "多重射击",
    "schaden": "伤害",
    "nahkampfschaden": "伤害",
    "statuschance": "触发几率",
    "statusdauer": "触发时间",
    "angriffsgeschwindigkeit": "攻击速度",
    "feuerrate": "射速",
    "nachladegeschwindigkeit": "装填速度",
    "magazingröße": "弹匣容量",
    "maximale munition": "弹药最大值",
    "hitze": "火焰伤害",
    "kälte": "冰冻伤害",
    "elektrizität": "电击伤害",
    "gift": "毒素伤害",
    "einschlag": "冲击伤害",
    "durchschlag": "穿刺伤害",
    "schnitt": "切割伤害",
    "durchdringung": "穿透",
    "reichweite": "攻击范围",
    "kombo-dauer": "连击持续时间",
    "anfangskombo": "初始连击",
    "projektilgeschwindigkeit": "投射物速度",
    "waffenrückstoß": "武器后坐力",
    # Spanish (es_ES)
    "probabilidadcrítica": "暴击几率",
    "dañocrítico": "暴击伤害",
    "multidisparo": "多重射击",
    "daño": "伤害",
    "dañocuerpoacuerpo": "伤害",
    "probabilidaddeestado": "触发几率",
    "duracióndeestado": "触发时间",
    "velocidaddeataque": "攻击速度",
    "cadenciadefuego": "射速",
    "velocidadederecarga": "装填速度",
    "velocidadderecarga": "装填速度",
    "capacidaddelcargador": "弹匣容量",
    "municiónmáxima": "弹药最大值",
    "calor": "火焰伤害",
    "frío": "冰冻伤害",
    "electricidad": "电击伤害",
    "toxina": "毒素伤害",
    "impacto": "冲击伤害",
    "perforación": "穿刺伤害",
    "cortante": "切割伤害",
    "atravesar": "穿透",
    "alcance": "攻击范围",
    "duracióndecombo": "连击持续时间",
    "comboinicial": "初始连击",
    "velocidaddeproyectil": "投射物速度",
    "retrocesodelarma": "武器后坐力",
    # Italian (it_IT)
    "probabilitàcritico": "暴击几率",
    "dannocritico": "暴击伤害",
    "sparamultiplo": "多重射击",
    "danno": "伤害",
    "dannocorpocorpo": "伤害",
    "probabilitàeffetto": "触发几率",
    "durataeffetto": "触发时间",
    "velocitàattacco": "攻击速度",
    "cadenza": "射速",
    "velocitàricarica": "装填速度",
    "capacitàcaricatore": "弹匣容量",
    "fuoco": "火焰伤害",
    "ghiaccio": "冰冻伤害",
    "elettricità": "电击伤害",
    "tossina": "毒素伤害",
    "impatto": "冲击伤害",
    "perforazione": "穿刺伤害",
    "taglio": "切割伤害",
    "penetrazione": "穿透",
    "portata": "攻击范围",
    "rinculo": "武器后坐力",
    # Portuguese-BR (pt_BR)
    "chancecrítica": "暴击几率",
    "danocrítico": "暴击伤害",
    "tiromúltiplo": "多重射击",
    "dano": "伤害",
    "danocorpoacorpo": "伤害",
    "chancedestatus": "触发几率",
    "duraçãodestatus": "触发时间",
    "velocidadedeataque": "攻击速度",
    "cadênciadetiro": "射速",
    "velocidadederecarga": "装填速度",
    "capacidaderelativa": "弹匣容量",
    "capacidadedocarregador": "弹匣容量",
    "ígneo": "火焰伤害",
    "glacial": "冰冻伤害",
    "elétrico": "电击伤害",
    "tóxico": "毒素伤害",
    "perfurante": "穿刺伤害",
    "perfuração": "穿刺伤害",
    "cortante": "切割伤害",
    "recuodaarma": "武器后坐力",
    # Polish (pl_PL)
    "szansanakryt": "暴击几率",
    "szansanaobr.krytyczne": "暴击几率",
    "obrażeniakrytyczne": "暴击伤害",
    "wielostrzał": "多重射击",
    "obrażenia": "伤害",
    "obrażeniawalcewręcz": "伤害",
    "szansastatusu": "触发几率",
    "czastrwaniastatusu": "触发时间",
    "prędkośćataku": "攻击速度",
    "szybkostrzelność": "射速",
    "prędkośćprzeładowania": "装填速度",
    "pojemnośćmagazynka": "弹匣容量",
    "ogień": "火焰伤害",
    "zimno": "冰冻伤害",
    "elektryczność": "电击伤害",
    "toksyna": "毒素伤害",
    "miażdżące": "冲击伤害",
    "przebijające": "穿刺伤害",
    "tnące": "切割伤害",
    "przebicienaodlot": "穿透",
    "zasięg": "攻击范围",
    "odrzutbroni": "武器后坐力",
    "przybliżenie": "变焦",
    # Turkish (tr_TR)
    "kritikşansı": "暴击几率",
    "kritikhasar": "暴击伤害",
    "çokluatış": "多重射击",
    "hasar": "伤害",
    "yakındövüşhasarı": "伤害",
    "statüşansı": "触发几率",
    "statüsüresi": "触发时间",
    "saldırıhızı": "攻击速度",
    "atışhızı": "射速",
    "şarjördolumhızı": "装填速度",
    "şarjörkapasitesi": "弹匣容量",
    "ateş": "火焰伤害",
    "buz": "冰冻伤害",
    "elektrik": "电击伤害",
    "toksin": "毒素伤害",
    "darbe": "冲击伤害",
    "delme": "穿刺伤害",
    "kesme": "切割伤害",
    "delipgeçme": "穿透",
    "menzil": "攻击范围",
    "silahgeritepmesi": "武器后坐力",
    "yakınlaştırma": "变焦",
    # Thai (th_TH)
    "โอกาสคริติคอล": "暴击几率",
    "ความเสียหายคริติคอล": "暴击伤害",
    "มัลติช็อต": "多重射击",
    "ความเสียหาย": "伤害",
    "โอกาสสถานะ": "触发几率",
    "ระยะเวลาสถานะ": "触发时间",
    "ความเร็วโจมตี": "攻击速度",
    "อัตรายิง": "射速",
    "ความเร็วรีโหลด": "装填速度",
    "ขนาดแม็กกาซีน": "弹匣容量",
    "ความร้อน": "火焰伤害",
    "ความเย็น": "冰冻伤害",
    "ไฟฟ้า": "电击伤害",
    "พิษ": "毒素伤害",
    "กระแทก": "冲击伤害",
    "เจาะเกราะ": "穿刺伤害",
    "ฟัน": "切割伤害",
    "ระยะโจมตี": "攻击范围",
    "แรงดีด": "武器后坐力",
    "ซูม": "变焦",
}

# Unified internal key mapping (canonical stat -> official English slug)
CANONICAL_TO_INTERNAL_KEY: Dict[str, str] = {
    "暴击几率": "critical_chance",
    "暴击伤害": "critical_damage",
    "多重射击": "multishot",
    "伤害": "base_damage_/_melee_damage",
    "触发几率": "status_chance",
    "触发时间": "status_duration",
    "异常状态伤害": "status_damage",
    "攻击速度": "attack_speed",
    "射速": "fire_rate",
    "装填速度": "reload_speed",
    "弹匣容量": "magazine_capacity",
    "弹药最大值": "ammo_maximum",
    "弹药效率": "ammo_efficiency",
    "收起武器时弹匣每秒自动装填": "magazine_reloaded_s_when_holstered",
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
    "穿透": "punch_through",
    "投射物速度": "projectile_speed",
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
    "对堕落者伤害": "damage_vs_corrupted",
    "对低语者伤害": "damage_vs_murmur",
}

# Stats where a '-' sign is actually a BENEFICIAL (positive) effect in Warframe
INVERTED_BENEFICIAL_STATS = {"武器后坐力"}

# The 6 compound elemental attributes can NEVER roll as negative curses in Warframe Rivens
POSITIVE_ONLY_COMPOUND_ELEMENTS = frozenset(
    {"病毒伤害", "腐蚀伤害", "辐射伤害", "磁力伤害", "毒气伤害", "爆炸伤害"}
)

# Stats that do NOT use '%' (flat numeric or multiplicative faction stats)
NON_PERCENT_STATS = {
    "穿透",
    "攻击范围",
    "连击持续时间",
    "初始连击",
    "招架角度",
}

# Known valid canonical stat names (whitelist for strict anti-noise validation)
KNOWN_CANONICAL_STATS = set(STAT_CANONICAL_MAP.values())

# Dynamic local supplemental stat tables loaded with priority from `data/custom_riven_stats.json`
CUSTOM_STAT_ALIAS_MAP: Dict[str, str] = {}
CUSTOM_KNOWN_STATS: Set[str] = set()
CUSTOM_NON_PERCENT_STATS: Set[str] = set()
CUSTOM_STAT_KEYS: Dict[str, str] = {}
CUSTOM_STAT_META: Dict[str, Dict[str, Any]] = {}
CUSTOM_HYBRID_PAIRS: Dict[str, Dict[str, Any]] = {}
DEFAULT_HYBRID_SCALE_FACTOR: float = 0.6


def get_custom_stats_path() -> Path:
    """Resolve `data/custom_riven_stats.json` (compatible with both source & frozen PyInstaller)."""
    if getattr(sys, "frozen", False):
        exe_data = Path(sys.executable).resolve().parent / "_internal" / "data" / "custom_riven_stats.json"
        if exe_data.is_file():
            return exe_data
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cand = Path(meipass) / "data" / "custom_riven_stats.json"
            if cand.is_file():
                return cand
    return Path(__file__).resolve().parent.parent / "data" / "custom_riven_stats.json"


def load_custom_riven_stats(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Read local supplemental `data/custom_riven_stats.json` safely."""
    target = Path(path) if path else get_custom_stats_path()
    if not target.is_file():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def reload_custom_stats(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Reload `data/custom_riven_stats.json` and hydrate priority parser alias/key maps."""
    global DEFAULT_HYBRID_SCALE_FACTOR
    CUSTOM_STAT_ALIAS_MAP.clear()
    CUSTOM_KNOWN_STATS.clear()
    CUSTOM_NON_PERCENT_STATS.clear()
    CUSTOM_STAT_KEYS.clear()
    CUSTOM_STAT_META.clear()
    CUSTOM_HYBRID_PAIRS.clear()
    DEFAULT_HYBRID_SCALE_FACTOR = 0.6

    data = load_custom_riven_stats(path)
    if not data:
        return {}

    try:
        sf = float(data.get("default_hybrid_scale_factor", 0.6))
        if 0.2 <= sf <= 1.2:
            DEFAULT_HYBRID_SCALE_FACTOR = sf
    except (TypeError, ValueError):
        pass

    raw_stats = data.get("custom_stats") or {}
    if isinstance(raw_stats, dict):
        for canon_name, meta in raw_stats.items():
            if not isinstance(meta, dict):
                continue
            cname = str(canon_name).strip()
            if not cname:
                continue
            CUSTOM_KNOWN_STATS.add(cname)
            CUSTOM_STAT_META[cname] = dict(meta)
            compact_canon = re.sub(r"\s+", "", cname).lower()
            CUSTOM_STAT_ALIAS_MAP[compact_canon] = cname

            stat_key = str(meta.get("stat_key") or "").strip()
            if stat_key:
                CUSTOM_STAT_KEYS[cname] = stat_key

            en_name = str(meta.get("en_name") or "").strip()
            if en_name:
                CUSTOM_STAT_ALIAS_MAP[re.sub(r"\s+", "", en_name).lower()] = cname

            for alias in meta.get("aliases") or []:
                al_compact = re.sub(r"\s+", "", str(alias)).lower()
                if al_compact:
                    CUSTOM_STAT_ALIAS_MAP[al_compact] = cname

            if meta.get("is_percent") is False:
                CUSTOM_NON_PERCENT_STATS.add(cname)

    raw_pairs = data.get("hybrid_pairs") or {}
    if isinstance(raw_pairs, dict):
        for pair_key, pmeta in raw_pairs.items():
            if isinstance(pmeta, dict):
                CUSTOM_HYBRID_PAIRS[str(pair_key).strip()] = dict(pmeta)

    return data


# Hydrate local supplemental stats immediately on import
reload_custom_stats()

# Strictly valid Riven stat count combinations: (num_positive, num_negative)
# 1) 2+0- (2 stats)  2) 2+1- (3 stats)  3) 3+0- (3 stats)  4) 3+1- (4 stats)
VALID_STAT_COMBOS = frozenset({(2, 0), (2, 1), (3, 0), (3, 1)})

# Community / WeGame / alternate Simplified Chinese weapon name aliases
WEAPON_COMMUNITY_ALIASES: Dict[str, str] = {
    "悦音": "奏凯",
    "逐电": "逐枭",
    "凤凰": "凤殁",
    "执政官之怒": "执法者",
    "灵化之刃": "清刚",
    "双子毒妇": "毒囊双枪",
    "双子毒牙": "毒囊双枪",
    "盗贼": "雷克斯",
    "托理德": "托里德",
    "冰松": "冰凇",
}


def normalize_weapon_alias(name: Optional[str]) -> Optional[str]:
    """Normalize community/WeGame weapon aliases to canonical Simplified Chinese weapon names."""
    if not name:
        return name
    cleaned = name.strip()
    return WEAPON_COMMUNITY_ALIASES.get(cleaned, cleaned)


# Known English weapon variant keywords that should NOT be mistaken for Riven suffixes
WEAPON_ENGLISH_TAGS = {
    "prime",
    "vandal",
    "wraith",
    "prisma",
    "kuva",
    "tenet",
    "coda",
    "dex",
    "mk1",
    "mk",
    "mara",
    "carmine",
    "telos",
    "synoid",
    "secura",
    "sancti",
    "rakta",
    "vaykor",
    "umbra",
}

# Known Warframe Riven prefix/suffix morphemes (lowercase) to verify Riven suffix tokens
RIVEN_MORPHEMES = (
    "acri", "ampi", "argi", "arma", "arvi", "ata", "bin", "bo", "can", "con",
    "crita", "cron", "croni", "dec", "deci", "dex", "dra", "eli", "ent", "ev",
    "exi", "feva", "forti", "geli", "gel", "hex", "hexa", "igni", "ign", "in",
    "ins", "inti", "lex", "lexi", "loct", "locti", "magna", "mant", "manti",
    "mar", "nak", "ner", "nes", "nok", "parc", "pha", "plaga", "plec", "pleci", "pura", "pur",
    "ran", "ric", "sat", "sati", "scia", "sci", "sus", "tak", "tempi", "temp",
    "tio", "tis", "tor", "toxi", "tox", "tron", "utis", "vex", "vexi", "visi",
    "vis", "zeti", "zet",
)


@dataclass
class RivenStat:
    """Single parsed attribute line (or split hybrid sub-attribute) on a Riven Mod."""

    stat_name: str
    raw_name: str
    value: float
    signed_value: float
    sign: str  # "+", "-", or "x"
    unit: str  # "%", "x", "秒", "米", or ""
    is_negative: bool
    raw_text: str
    is_locked: bool = False
    stat_key: str = ""  # Unified bilingual internal key (e.g. "critical_chance")
    is_hybrid: bool = False  # True when split from a dual-attribute hybrid Riven line
    hybrid_group_id: Optional[int] = None  # Links the 2 sub-stats from the same hybrid line
    is_custom_stat: bool = False  # True when matched from custom_riven_stats.json or new hybrid fallback

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RivenData:
    """Structured representation of a parsed Simplified Chinese or English Riven Mod."""

    weapon_name: Optional[str] = None
    riven_suffix: Optional[str] = None
    riven_name: Optional[str] = None
    mastery_rank: Optional[int] = None
    reroll_count: Optional[int] = None
    drain: Optional[int] = None
    is_veiled: bool = False
    is_valid: bool = False
    validation_error: Optional[str] = None
    detected_language: str = "zh_CN"  # "zh_CN" | "en_US"
    stats: List[RivenStat] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def positive_stats(self) -> List[RivenStat]:
        return [s for s in self.stats if not s.is_negative]

    @property
    def negative_stats(self) -> List[RivenStat]:
        return [s for s in self.stats if s.is_negative]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weapon_name": self.weapon_name,
            "riven_suffix": self.riven_suffix,
            "riven_name": self.riven_name,
            "mastery_rank": self.mastery_rank,
            "reroll_count": self.reroll_count,
            "drain": self.drain,
            "is_veiled": self.is_veiled,
            "is_valid": self.is_valid,
            "validation_error": self.validation_error,
            "detected_language": self.detected_language,
            "positive_stats": [s.to_dict() for s in self.positive_stats],
            "negative_stats": [s.to_dict() for s in self.negative_stats],
            "stats": [s.to_dict() for s in self.stats],
            "raw_lines": self.raw_lines,
            "warnings": self.warnings,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class RivenParser:
    """Parse Simplified Chinese Warframe Riven OCR lines into structured JSON."""

    # Multilingual letter range covering all 15 official Warframe languages
    # (CJK, Hiragana/Katakana, Hangul, Cyrillic, Extended Latin, Thai, ASCII)
    _ML_LETTER = r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z"

    # Stat line pattern:
    # - Allows optional leading lock-icon artifact before + / - / x (e.g. "6 +73.1%", "θ +109.9%")
    # - Allows optional elemental icon artifact between unit and stat name (e.g. "+127.2% *冰冻伤害", "+110% 🔥火焰伤害")
    STAT_LINE_RE = re.compile(
        r"^(?:(?P<lock>[^+\-x×\s]{1,2})\s*)?"
        r"(?P<sign>[+\-x×])\s*"
        r"(?P<val>\d+(?:\.\d+)?)\s*"
        r"(?P<unit>%|秒|s|米|m)?\s*"
        r"(?:[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z0-9\s]{1,2}\s*)?"
        r"(?P<name>[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z]"
        r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z0-9\s（）()·\-.'’]+)$",
        re.IGNORECASE,
    )

    # Hybrid dual-attribute line splitter:
    # Matches a connector (与/和/及/并且/并/且///|/&/,/、/and/with) or whitespace directly
    # preceding the second signed stat token (e.g. "+95% 暴击几率 与 +75% 暴击伤害",
    # "+80% 伤害 / +60% 多重射击", "+90% Critical Chance and +70% Critical Damage").
    HYBRID_BOUNDARY_RE = re.compile(
        r"(?:\s*(?:并且|与|和|及|并|且|/|\||&|、|,|\band\b|\bwith\b)\s*(?=[+\-x×]\s*\d)"
        r"|\s+(?=[+\-]\s*\d+(?:\.\d+)?\s*(?:%|秒|s|米|m)?\s*"
        r"(?:[^\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z0-9\s]{1,2}\s*)?"
        r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z]))",
        re.IGNORECASE,
    )

    # Detects when a wrapped hybrid line ends with a connector and the next line starts with a stat
    HYBRID_TRAILING_CONNECTOR_RE = re.compile(
        r"(?:并且|与|和|及|并|且|/|\||&|、|,|\band|\bwith)\s*$",
        re.IGNORECASE,
    )

    STAT_START_RE = re.compile(
        r"^(?:[^+\-x×\s]{1,2}\s*)?[+\-x×]\s*\d+(?:\.\d+)?",
        re.IGNORECASE,
    )

    # Unsigned percentage stat line (e.g. "28.7% 弹匣容量" when OCR drops a faint leading '-')
    UNSIGNED_PCT_STAT_RE = re.compile(
        r"^(?P<val>\d+(?:\.\d+)?)\s*%\s*"
        r"(?P<name>[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z]"
        r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u0400-\u04ff\u00c0-\u024f\u0e00-\u0e7fA-Za-z0-9\s（）()·\-.'’]+)$",
        re.IGNORECASE,
    )

    # Mastery rank patterns (ZH + EN): 段位 12 / Mastery 14 / Mastery Rank 16 / MR 12
    MASTERY_RE = re.compile(
        r"(?:精通段位|段位需求|段位|Mastery\s*Rank|Mastery|MR)\s*[:：]?\s*(\d{1,2})\b",
        re.IGNORECASE,
    )

    # Explicit reroll patterns (ZH + EN): 循环 18 / Rerolls 18 / Cycles 18
    EXPLICIT_REROLL_RE = re.compile(
        r"(?:循环次数|洗卡次数|重洗次数|循环|洗卡|重洗|Rerolls?|Cycles?|Rolls?)\s*[:：]?\s*(\d{1,4})\b",
        re.IGNORECASE,
    )

    # Bottom-bar inline reroll pattern right after Mastery Rank (ZH + EN)
    # Allows any 1~2 OCR-garbled cycle-icon glyphs between MR and reroll count (e.g. "段位 12  ↻ 66", "段位 12  c 66")
    INLINE_REROLL_AFTER_MR_RE = re.compile(
        r"(?:精通段位|段位需求|段位|Mastery\s*Rank|Mastery|MR)\s*[:：]?\s*\d{1,2}\s+"
        r"(?:[^\d\s]{1,2}\s*)?(\d{1,4})\b",
        re.IGNORECASE,
    )

    # Standalone reroll line immediately after MR line: "O 18" / "↻18" / "c 66" / "18"
    STANDALONE_REROLL_RE = re.compile(
        r"^(?:[^\d\s]{1,2}\s*)?(\d{1,4})$"
    )

    # Top-right drain line (may include trailing glyph noise like "18-", "18 V", "18")
    # Kept purely to extract drain and prevent top-right noise from polluting weapon_name.
    DRAIN_LINE_RE = re.compile(
        r"^(?P<drain>1[0-8]|[89])(?:\s*[^\u4e00-\u9fff0-9\s]{1,2}|\s*一)?$",
        re.IGNORECASE,
    )

    # Hyphenated 3-stat Riven suffix (e.g. "Crita-visican", "哈尔武Acri-critaada")
    # Note: do NOT use `\b` because CJK characters are treated as `\w` in Python regex.
    HYPHEN_SUFFIX_RE = re.compile(r"(?<![A-Za-z])([A-Za-z]{3,11}-[A-Za-z]{3,14})(?![A-Za-z])")

    # Single-word 2-stat Riven suffix (e.g. "Critatis", "Parciata")
    SINGLE_SUFFIX_RE = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{4,16})(?![A-Za-z])")

    # Veiled Riven pattern (ZH + EN)
    VEILED_RE = re.compile(
        r"(?:步枪|手枪|近战|霰弹枪|组合枪|Zaw|同伴武器)?\s*裂罅\s*Mod|"
        r"(?:Rifle|Pistol|Melee|Shotgun|Kitgun|Zaw|Companion)?\s*Riven\s*Mod",
        re.IGNORECASE,
    )

    # Continuation fragments belonging to a wrapped preceding stat line
    WRAP_CONTINUATION_RE = re.compile(
        r"^(?:造成暴击|获得额外连击数|的几率|伤害|效果加倍|[（(].+[)）]|"
        r"Slide\s+Attack|Combo\s+Count|Heavy\s+Attacks?\)|to\s+(?:Grineer|Corpus|Infested|Orokin))$",
        re.IGNORECASE,
    )

    # Surrounding UI labels from Warframe reroll/mod screens (ZH + EN) that should never pollute title
    UI_NOISE_RE = re.compile(
        r"^(?:当前|新循环|新属性|旧属性|保留|选择|确认|取消|循环|剩余赤毒.*|用.*来循环|"
        r"锁定属性.*|显示已升级.*|裂罅熔接|适用于|仓库\s*/\s*MOD|"
        r"Current|New|Cycle|Confirm|Cancel|Select|Keep|Show\s+Maxed|Reroll|Kuva.*)$",
        re.IGNORECASE,
    )

    @staticmethod
    def normalize_line(text: str) -> str:
        """Normalize full-width characters and common OCR spacing/punctuation artifacts."""
        s = text.strip()
        if not s:
            return ""

        # Replace full-width symbols & dash variants
        replacements = {
            "＋": "+",
            "－": "-",
            "—": "-",
            "–": "-",
            "−": "-",
            "％": "%",
            "×": "x",
            "：": ":",
            "\u3000": " ",
        }
        for old, new in replacements.items():
            s = s.replace(old, new)

        # Fix decimal points mistakenly read as Chinese period or comma between digits
        s = re.sub(r"(\d)\s*[。.,，]\s*(\d)", r"\1.\2", s)

        # Fix spaces between leading sign and digits: "+ 145.2 %" -> "+145.2%"
        s = re.sub(r"^([+\-xX])\s+(\d)", r"\1\2", s)
        s = re.sub(r"(\d)\s+%", r"\1%", s)

        # Strip stray single OCR digit artifact glued right after '%' before CJK stat text
        # e.g. "+70.9%6弹药效率" -> "+70.9% 弹药效率"
        s = re.sub(r"%\s*\d(?=\s*[\u4e00-\u9fff])", "% ", s)

        # Collapse excessive spaces
        s = re.sub(r"\s{2,}", " ", s).strip()
        return s

    @classmethod
    def _looks_like_riven_suffix(cls, token: str) -> bool:
        """Check whether an ASCII word resembles a Warframe Riven affix name."""
        tok = token.strip().rstrip(".!?,;:")
        low = tok.lower()
        if not re.fullmatch(r"[a-z0-9]{2,}(?:-[a-z0-9]{2,})?", low):
            return False
        if low in WEAPON_ENGLISH_TAGS:
            return False
        if "-" in low:
            parts = low.split("-", 1)
            if len(parts[0]) >= 3 and len(parts[1]) >= 3:
                return True
            return False
        # Single-word 2-stat suffix: must match at least one known Riven morpheme
        if len(low) >= 5 and tok[0].isupper() and tok[1:].islower():
            return any(m in low for m in RIVEN_MORPHEMES)
        return False

    @classmethod
    def _canonicalize_stat_name(cls, raw_name: str) -> Tuple[str, str]:
        """Clean and map raw stat description to canonical Simplified Chinese name.

        Checks priority local `data/custom_riven_stats.json` aliases FIRST, then falls
        back to built-in `STAT_CANONICAL_MAP`.

        Returns:
            (canonical_name, cleaned_raw_name)
        """
        cleaned = raw_name.strip().rstrip("，,、/|&").strip()
        # Separate parenthetical gameplay note like "（重击时为双倍）" for matching
        base = re.sub(r"[（(][^）)]*[）)]", "", cleaned).strip()
        compact = re.sub(r"\s+", "", base).lower()

        # 1. Priority match against local `data/custom_riven_stats.json`
        if compact in CUSTOM_STAT_ALIAS_MAP:
            return CUSTOM_STAT_ALIAS_MAP[compact], cleaned
        for key in sorted(CUSTOM_STAT_ALIAS_MAP.keys(), key=len, reverse=True):
            if len(key) >= 4 and key in compact:
                return CUSTOM_STAT_ALIAS_MAP[key], cleaned

        # 2. Built-in bilingual canonical map
        if compact in STAT_CANONICAL_MAP:
            return STAT_CANONICAL_MAP[compact], cleaned

        # Partial/fuzzy lookup for faction damage (ZH + EN) with spacing variations
        for faction in ("Grineer", "Corpus", "Infested"):
            if faction.lower() in compact and ("伤害" in compact or "damage" in compact):
                return f"对 {faction} 伤害", cleaned

        # 3. Substring matches for longer canonical names (longest key first)
        for key in sorted(STAT_CANONICAL_MAP.keys(), key=len, reverse=True):
            if len(key) >= 4 and key in compact:
                return STAT_CANONICAL_MAP[key], cleaned

        # 4. High-precision OCR typo fuzzy fallback (e.g. "暴去几率" -> "暴击几率", "弹匝容量" -> "弹匣容量")
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", compact))
        if has_cjk and len(compact) >= 3:
            cjk_candidates = [
                k for k in (*CUSTOM_STAT_ALIAS_MAP.keys(), *STAT_CANONICAL_MAP.keys())
                if len(k) >= 3 and re.search(r"[\u4e00-\u9fff]", k)
            ]
            close = difflib.get_close_matches(compact, cjk_candidates, n=1, cutoff=0.74)
            if close:
                best_k = close[0]
                return CUSTOM_STAT_ALIAS_MAP.get(best_k) or STAT_CANONICAL_MAP[best_k], cleaned
        elif not has_cjk and len(compact) >= 6:
            non_cjk_candidates = [
                k for k in (*CUSTOM_STAT_ALIAS_MAP.keys(), *STAT_CANONICAL_MAP.keys())
                if len(k) >= 6 and not re.search(r"[\u4e00-\u9fff]", k)
            ]
            close = difflib.get_close_matches(compact, non_cjk_candidates, n=1, cutoff=0.82)
            if close:
                best_k = close[0]
                return CUSTOM_STAT_ALIAS_MAP.get(best_k) or STAT_CANONICAL_MAP[best_k], cleaned

        return base or cleaned, cleaned

    @classmethod
    def _has_unclosed_paren(cls, text: str) -> bool:
        opens = text.count("（") + text.count("(")
        closes = text.count("）") + text.count(")")
        return opens > closes

    @classmethod
    def _is_stat_continuation_line(cls, prev: str, curr: str) -> bool:
        """Detect whether `curr` is a wrapped continuation fragment of stat line `prev` (ZH or EN)."""
        if not cls.STAT_LINE_RE.match(prev):
            return False
        # NEVER glue a line that starts with its own stat value/sign/percentage onto the previous stat!
        if (
            cls.STAT_LINE_RE.match(curr)
            or cls.STAT_START_RE.match(curr)
            or cls.UNSIGNED_PCT_STAT_RE.match(curr)
            or cls.MASTERY_RE.search(curr)
            or cls.EXPLICIT_REROLL_RE.search(curr)
            or cls.STANDALONE_REROLL_RE.match(curr)
            or cls.UI_NOISE_RE.match(curr)
        ):
            return False
        if cls.WRAP_CONTINUATION_RE.match(curr):
            return True
        if cls._has_unclosed_paren(prev):
            return True
        # General wrapped Chinese stat tail (e.g. "击数", "造成暴击", "的伤害", "额外连击数")
        if len(curr) <= 12 and re.search(r"[\u4e00-\u9fff]", curr):
            return True
        # General wrapped English stat tail (e.g. "Slide Attack", "Combo Count", "to Grineer")
        if len(curr) <= 24 and re.fullmatch(r"[A-Za-z0-9\s()/%-]+", curr):
            return True
        return False

    @classmethod
    def _preprocess_lines(cls, lines: Sequence[str]) -> List[str]:
        """Normalize lines and re-join wrapped stat/title continuation lines."""
        normalized = [cls.normalize_line(ln) for ln in lines]
        normalized = [ln for ln in normalized if ln]

        merged: List[str] = []
        for ln in normalized:
            if merged:
                prev = merged[-1]
                # Join hyphen-wrapped Riven title lines: e.g. "哈尔武 Acri-" + "critaada"
                if prev.endswith("-") and re.fullmatch(r"[A-Za-z]{3,14}", ln):
                    merged[-1] = f"{prev}{ln}"
                    continue
                # Join wrapped hybrid dual-stat line: e.g. "+95% 暴击几率 与" + "+75% 暴击伤害"
                if (
                    cls.STAT_START_RE.match(prev)
                    and cls.HYBRID_TRAILING_CONNECTOR_RE.search(prev)
                    and cls.STAT_START_RE.match(ln)
                ):
                    merged[-1] = f"{prev} {ln}"
                    continue
                # Join wrapped stat continuation lines
                if cls._is_stat_continuation_line(prev, ln):
                    sep = " " if (prev[-1].isascii() and ln[0].isascii() and not prev.endswith("(")) else ""
                    merged[-1] = f"{prev}{sep}{ln}"
                    continue
            merged.append(ln)
        return merged

    @classmethod
    def _parse_single_stat_segment(
        cls,
        segment: str,
        *,
        raw_text: str,
        is_hybrid: bool = False,
        hybrid_group_id: Optional[int] = None,
        inherited_locked: bool = False,
    ) -> Optional[RivenStat]:
        seg = segment.strip()
        if not seg:
            return None
        # Normalize spaces around sign & percent inside split segments too: "+ 75 % 暴击伤害" -> "+75% 暴击伤害"
        seg = re.sub(r"^([+\-xX×])\s+(\d)", r"\1\2", seg)
        seg = re.sub(r"(\d)\s+%", r"\1%", seg)
        match = cls.STAT_LINE_RE.match(seg)
        if not match:
            return None

        raw_sign = match.group("sign").lower()
        sign = "x" if raw_sign in ("x", "×") else raw_sign
        val = float(match.group("val"))
        unit = match.group("unit") or ("x" if sign == "x" else "")
        if unit.lower() == "s":
            unit = "秒"
        elif unit.lower() == "m":
            unit = "米"

        raw_name = match.group("name").strip()
        canonical_name, cleaned_raw_name = cls._canonicalize_stat_name(raw_name)

        if sign == "x":
            signed_val = val
            is_negative = val < 1.0
        elif sign == "-":
            signed_val = -val
            # In Warframe, negative recoil ("-武器后坐力") is a positive buff!
            is_negative = canonical_name not in INVERTED_BENEFICIAL_STATS
        else:
            signed_val = val
            # Positive recoil ("+武器后坐力") is a negative curse!
            is_negative = canonical_name in INVERTED_BENEFICIAL_STATS

        is_custom = (canonical_name in CUSTOM_KNOWN_STATS) or (
            is_hybrid and canonical_name not in KNOWN_CANONICAL_STATS
        )
        stat_key = CUSTOM_STAT_KEYS.get(canonical_name) or CANONICAL_TO_INTERNAL_KEY.get(canonical_name, "")

        return RivenStat(
            stat_name=canonical_name,
            raw_name=cleaned_raw_name,
            value=val,
            signed_value=signed_val,
            sign=sign,
            unit=unit,
            is_negative=is_negative,
            raw_text=raw_text,
            is_locked=bool(match.group("lock")) or inherited_locked,
            stat_key=stat_key,
            is_hybrid=is_hybrid,
            hybrid_group_id=hybrid_group_id if is_hybrid else None,
            is_custom_stat=is_custom,
        )

    @classmethod
    def _parse_stat_line_entries(
        cls,
        line: str,
        next_hybrid_group_id: int = 1,
    ) -> List[RivenStat]:
        """Parse one OCR line into 1 standard `RivenStat` OR 2+ split hybrid sub-stats (`is_hybrid=True`)."""
        stripped = line.strip()
        if not stripped or not cls.STAT_START_RE.match(stripped):
            return []

        # Attempt hybrid dual-attribute splitting first
        parts = [p.strip() for p in cls.HYBRID_BOUNDARY_RE.split(stripped) if p and p.strip()]
        if len(parts) >= 2:
            # Check optional leading lock glyph on first part and propagate to all sub-stats
            lead_m = re.match(r"^(?P<lock>[^+\-x×\s]{1,2})\s+(?=[+\-x×]\s*\d)", parts[0])
            inherited_lock = bool(lead_m)
            parsed_subs: List[RivenStat] = []
            for p in parts:
                sub = cls._parse_single_stat_segment(
                    p,
                    raw_text=stripped,
                    is_hybrid=True,
                    hybrid_group_id=next_hybrid_group_id,
                    inherited_locked=inherited_lock,
                )
                if sub is not None:
                    parsed_subs.append(sub)
            if len(parsed_subs) >= 2:
                return parsed_subs

        # Normal single-stat line
        single = cls._parse_single_stat_segment(stripped, raw_text=stripped, is_hybrid=False)
        return [single] if single is not None else []

    @classmethod
    def _parse_stat_line(cls, line: str) -> Optional[RivenStat]:
        entries = cls._parse_stat_line_entries(line)
        return entries[0] if entries else None

    @classmethod
    def _extract_title(
        cls,
        header_lines: Sequence[str],
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract (weapon_name, riven_suffix, full_riven_name) from lines above stats."""
        candidates = [
            ln
            for ln in header_lines
            if ln and not cls.DRAIN_LINE_RE.match(ln) and not cls.UI_NOISE_RE.match(ln)
        ]
        if not candidates:
            return None, None, None

        # Strategy 1: single line containing both weapon name and Riven suffix
        # e.g. "托里德 Crita-visican" or "赤毒 布拉玛 Visi-satiata"
        for raw_line in candidates:
            # Clean trailing OCR punctuation & spaces around hyphen in ASCII suffixes
            line = re.sub(r"([A-Za-z0-9])\s*-\s*([A-Za-z0-9])", r"\1-\2", raw_line).rstrip(".!?,;:").strip()
            m_hyph = cls.HYPHEN_SUFFIX_RE.search(line)
            if m_hyph and cls._looks_like_riven_suffix(m_hyph.group(1)):
                suffix = m_hyph.group(1)
                weapon = line[: m_hyph.start()].strip()
                if weapon:
                    return weapon, suffix, f"{weapon} {suffix}"

            # Check trailing single-word suffix
            for m_single in cls.SINGLE_SUFFIX_RE.finditer(line):
                token = m_single.group(1)
                if cls._looks_like_riven_suffix(token) and m_single.end() == len(line):
                    weapon = line[: m_single.start()].strip()
                    if weapon:
                        return weapon, token, f"{weapon} {token}"

            # Fallback for OCR-digit-glitched hyphenated suffix after CJK weapon name
            # e.g. "托里德 Cr1ta-acr1can" -> weapon="托里德", suffix="Cr1ta-acr1can"
            m_cjk_hyph = re.match(r"^([\u4e00-\u9fff][\u4e00-\u9fff\s·•A-Za-z0-9-]*?)\s+([A-Za-z0-9]{3,}-[A-Za-z0-9]{3,})$", line)
            if m_cjk_hyph:
                weapon = m_cjk_hyph.group(1).strip()
                suffix = m_cjk_hyph.group(2).strip()
                if weapon:
                    return weapon, suffix, f"{weapon} {suffix}"

        # Strategy 2: two-line split (Line i = Weapon Name, Line i+1 = Riven Suffix)
        for idx in range(len(candidates) - 1):
            next_line = candidates[idx + 1].strip()
            if (
                cls.HYPHEN_SUFFIX_RE.fullmatch(next_line)
                or (cls.SINGLE_SUFFIX_RE.fullmatch(next_line) and cls._looks_like_riven_suffix(next_line))
            ):
                weapon = candidates[idx].strip()
                suffix = next_line
                return weapon, suffix, f"{weapon} {suffix}"

        # Strategy 3: fallback — if only a suffix line was found or only a weapon line
        if len(candidates) == 1:
            only = candidates[0].strip()
            if cls._looks_like_riven_suffix(only):
                return None, only, only
            return only, None, only

        joined = " ".join(candidates).strip()
        return candidates[0].strip(), None, joined

    @staticmethod
    def count_effective_slots(stats: Sequence[RivenStat]) -> Tuple[int, int]:
        """Count original card stat slots `(pos_slots, neg_slots)` where a split hybrid line counts as 1 slot."""
        pos_groups: Set[int] = set()
        neg_groups: Set[int] = set()
        pos_slots = 0
        neg_slots = 0
        for st in stats:
            if st.is_hybrid and st.hybrid_group_id is not None:
                if st.is_negative:
                    if st.hybrid_group_id not in neg_groups:
                        neg_groups.add(st.hybrid_group_id)
                        neg_slots += 1
                else:
                    if st.hybrid_group_id not in pos_groups:
                        pos_groups.add(st.hybrid_group_id)
                        pos_slots += 1
            else:
                if st.is_negative:
                    neg_slots += 1
                else:
                    pos_slots += 1
        return pos_slots, neg_slots

    @staticmethod
    def validate_riven_structure(data: RivenData) -> Tuple[bool, Optional[str]]:
        """Enforce strict Warframe Riven stat constraints (with full hybrid dual-stat slot support).

        Valid standard combinations are strictly:
          - 2 positive, 0 negative (2 total)
          - 2 positive, 1 negative (3 total)
          - 3 positive, 0 negative (3 total)
          - 3 positive, 1 negative (4 total)

        When a card contains hybrid dual-attribute lines (`is_hybrid=True`), each hybrid
        line counts as 1 card slot while expanding into 2 independent sub-stats.
        """
        if data.is_veiled:
            return False, "未开紫卡（裂罅 Mod），无有效词条"

        n_total = len(data.stats)
        n_pos = len(data.positive_stats)
        n_neg = len(data.negative_stats)
        has_hybrid = any(s.is_hybrid for s in data.stats)

        if has_hybrid:
            pos_slots, neg_slots = RivenParser.count_effective_slots(data.stats)
            valid_hybrid_combo = (
                n_total >= 2
                and n_total <= 8
                and (
                    (pos_slots, neg_slots) in VALID_STAT_COMBOS
                    or (1 <= pos_slots <= 3 and neg_slots <= 1 and (n_pos >= 2 or n_total >= 2))
                )
            )
            if not valid_hybrid_combo:
                return (
                    False,
                    f"非法融合词条组合: {pos_slots}正槽{neg_slots}负槽 (共{n_total}条子属性)",
                )
        else:
            if n_total < 2 or n_total > 4 or (n_pos, n_neg) not in VALID_STAT_COMBOS:
                return (
                    False,
                    f"非法词条组合: {n_pos}正{n_neg}负 (共{n_total}条，合法组合仅限 2+0-/2+1-/3+0-/3+1-)",
                )

        allowed_known = KNOWN_CANONICAL_STATS | CUSTOM_KNOWN_STATS
        allowed_non_pct = NON_PERCENT_STATS | CUSTOM_NON_PERCENT_STATS
        seen_names = set()
        for stat in data.stats:
            is_known = stat.stat_name in allowed_known
            is_graceful_new_hybrid = stat.is_hybrid and stat.is_custom_stat and len(stat.stat_name.strip()) >= 2
            if not is_known and not is_graceful_new_hybrid:
                return False, f"疑似噪点或未知词条名: '{stat.stat_name}'"
            if stat.stat_name in POSITIVE_ONLY_COMPOUND_ELEMENTS and stat.is_negative:
                return False, f"复合元素属性 '{stat.stat_name}' 仅限正面词条（永不为负），疑似符号误识别"
            if stat.value <= 0:
                return False, f"词条 '{stat.stat_name}' 数值非法: {stat.value}"
            if stat.stat_name in seen_names:
                return False, f"出现重复词条: '{stat.stat_name}'"
            seen_names.add(stat.stat_name)

            # Percentage stats must explicitly have '%' parsed (unless multiplicative faction 'x' or non-percent)
            if stat.sign != "x" and stat.stat_name not in allowed_non_pct and stat.unit != "%":
                return False, f"词条 '{stat.stat_name}' 缺少百分号 '%'"

        return True, None

    def parse(self, text_or_lines: str | Sequence[str]) -> RivenData:
        """Parse OCR output (multi-line string or list of lines) into `RivenData`."""
        if isinstance(text_or_lines, str):
            input_lines = text_or_lines.splitlines()
        else:
            input_lines = list(text_or_lines)

        lines = self._preprocess_lines(input_lines)
        result = RivenData(raw_lines=lines, detected_language=detect_text_language(lines))

        if not lines:
            result.warnings.append("Empty OCR text input.")
            return result

        # Check veiled Riven
        for ln in lines:
            if self.VEILED_RE.search(ln):
                result.is_veiled = True
                break

        first_stat_idx: Optional[int] = None
        mr_line_idx: Optional[int] = None
        next_hybrid_gid = 1

        # Pass 1: Extract stats (including hybrid dual-stat splits), MR, explicit rerolls, and top drain
        for idx, ln in enumerate(lines):
            # Top-right drain (usually within first 2 lines)
            if idx <= 1 and result.drain is None:
                m_drain = self.DRAIN_LINE_RE.match(ln)
                if m_drain:
                    result.drain = int(m_drain.group("drain"))
                    continue

            # Stat line (may yield 1 standard stat or 2+ split hybrid sub-stats)
            parsed_stats = self._parse_stat_line_entries(ln, next_hybrid_group_id=next_hybrid_gid)
            if parsed_stats:
                if first_stat_idx is None:
                    first_stat_idx = idx
                if any(s.is_hybrid for s in parsed_stats):
                    next_hybrid_gid += 1
                result.stats.extend(parsed_stats)
                continue

            # Recover bottom negative stat when OCR drops the tiny leading '-' sign (e.g. "28.7% 弹匣容量")
            if (
                first_stat_idx is not None
                and len(result.positive_stats) in (2, 3)
                and len(result.negative_stats) == 0
                and self.UNSIGNED_PCT_STAT_RE.match(ln)
            ):
                recovered = self._parse_single_stat_segment(f"-{ln}", raw_text=ln, is_hybrid=False)
                if (
                    recovered is not None
                    and recovered.stat_name in (KNOWN_CANONICAL_STATS | CUSTOM_KNOWN_STATS)
                    and recovered.stat_name not in POSITIVE_ONLY_COMPOUND_ELEMENTS
                ):
                    result.stats.append(recovered)
                    continue

            # Mastery Rank
            m_mr = self.MASTERY_RE.search(ln)
            if m_mr and result.mastery_rank is None:
                mr_val = int(m_mr.group(1))
                result.mastery_rank = mr_val
                mr_line_idx = idx
                # Also check inline reroll count on the same bottom line ("段位 12  O 24")
                m_inline_roll = self.INLINE_REROLL_AFTER_MR_RE.search(ln)
                if m_inline_roll and result.reroll_count is None:
                    result.reroll_count = int(m_inline_roll.group(1))

            # Explicit reroll keyword ("循环 24", "洗卡 24")
            m_roll = self.EXPLICIT_REROLL_RE.search(ln)
            if m_roll:
                result.reroll_count = int(m_roll.group(1))

        # Pass 2: Check line immediately following Mastery Rank for standalone reroll number
        if result.reroll_count is None and mr_line_idx is not None:
            for follow_idx in range(mr_line_idx + 1, min(len(lines), mr_line_idx + 3)):
                m_standalone = self.STANDALONE_REROLL_RE.match(lines[follow_idx])
                if m_standalone:
                    result.reroll_count = int(m_standalone.group(1))
                    break

        # If Mastery Rank was visible on the card but no roll indicator exists,
        # an unrolled Warframe Riven omits the cycle counter -> treat as 0 rolls.
        if result.reroll_count is None and result.mastery_rank is not None:
            result.reroll_count = 0

        # Pass 3: Extract weapon name & riven name from lines above the first stat line
        if first_stat_idx is not None:
            header_lines = lines[:first_stat_idx]
        else:
            # Exclude MR / explicit reroll lines
            header_lines = [
                ln
                for ln in lines[:3]
                if not self.MASTERY_RE.search(ln)
                and not self.EXPLICIT_REROLL_RE.search(ln)
            ]

        weapon, suffix, full_name = self._extract_title(header_lines)
        result.weapon_name = weapon
        result.riven_suffix = suffix
        result.riven_name = full_name

        # Sanity warnings & strict 4-combo validation
        is_valid, val_err = self.validate_riven_structure(result)
        result.is_valid = is_valid
        result.validation_error = val_err
        if not is_valid and val_err:
            result.warnings.append(val_err)
        if result.weapon_name is None and not result.is_veiled:
            result.warnings.append("Weapon name could not be confidently separated.")

        return result
