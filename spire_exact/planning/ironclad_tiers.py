"""Ironclad card tiers, hand-provided by the player (2026-10-04).

The player brief, verbatim:
  - Tier 3 (一定要抓, very high): 坚定不移 UNMOVABLE / 祭品 OFFERING / 绯红披风 CRIMSON_MANTLE
  - Tier 2 (次强): 战斗专注 BATTLE_TRANCE / 凶恶 VICIOUS / 原始力量 PRIMAL_FORCE /
    好勇斗狠 AGGRESSION / 添柴 STOKE
  - Tier 1: 黑暗之拥 DARK_EMBRACE / 放血 BLOODLETTING / 巨像 COLOSSUS / 无惧疼痛 FEEL_NO_PAIN /
    时候未到 NOT_YET / 归然不动 IMPERVIOUS / 烙印 BRAND / 契约终结 PACTS_END /
    恶魔之焰 FIEND_FIRE / 扯碎 TEAR_ASUNDER / 与我一战 FIGHT_ME / 上勾拳 UPPERCUT /
    无情猛攻 UNRELENTING / 灰烬打击 ASHEN_STRIKE / 燃烧 INFLAME / 武装 ARMAMENTS /
    破灭 HAVOC / 腐化 CORRUPTION / 头槌 HEADBUTT
  - Ironclad cards NOT listed may only score LOWER than these (capped at 5,
    i.e. strictly below T1=6). Correction the same day: the T1 slot is 燃烧
    INFLAME, not 贪婪 GREED (a curse); T3=10 / T2=8 / T1=6.
  - Other characters are not scored for now.

These override the community pick tiers in route._tier for Ironclad cards.
Other characters keep their community tiers untouched.
"""
from __future__ import annotations

TIER3 = ('UNMOVABLE', 'OFFERING', 'CRIMSON_MANTLE')
TIER2 = ('BATTLE_TRANCE', 'VICIOUS', 'PRIMAL_FORCE', 'AGGRESSION', 'STOKE')
TIER1 = ('DARK_EMBRACE', 'BLOODLETTING', 'COLOSSUS', 'FEEL_NO_PAIN', 'NOT_YET', 'IMPERVIOUS',
         'BRAND', 'PACTS_END', 'FIEND_FIRE', 'TEAR_ASUNDER', 'FIGHT_ME', 'UPPERCUT',
         'UNRELENTING', 'ASHEN_STRIKE', 'INFLAME', 'ARMAMENTS', 'HAVOC', 'CORRUPTION', 'HEADBUTT')

# Points on the player's own scale (T3=10 / T2=8 / T1=6); unlisted Ironclad
# cards are capped at 5. This scale replaces — not mixes with — the community
# pick tiers, which top out at 2.
T3_POINTS, T2_POINTS, T1_POINTS, UNLISTED_CAP = 10, 8, 6, 5
# Terminal base scores the player set explicitly (D11 / D13, 2026-10-04). These
# are NOT the 10/8/6 hand tiers: they are the flat base of a synergy terminal,
# whose real worth comes from the engine bonus in card_mechanics.synergy.
#   D13: BODY_SLAM = 1, PERFECTED_STRIKE = 1, PYRE = 1
#   D11: TWIN_STRIKE = 0, SPITE = 0 (their value must come from strength alone)
# A 0 must be listed explicitly: an absent id falls back to the community tier.
TERMINAL_POINTS: dict[str, int] = {
    'BODY_SLAM': 1,
    'PERFECTED_STRIKE': 1,
    'PYRE': 1,
    'TWIN_STRIKE': 0,
    'SPITE': 0,
}

OVERRIDES: dict[str, int] = {**{c: T3_POINTS for c in TIER3}, **{c: T2_POINTS for c in TIER2},
                             **{c: T1_POINTS for c in TIER1}, **TERMINAL_POINTS}

# The Ironclad card pool (spire-codex.com API color == 'ironclad', 2026-10-04).
IRONCLAD_CARDS = frozenset([
    'AGGRESSION', 'ANGER', 'ARMAMENTS', 'ASHEN_STRIKE',
    'BARRICADE', 'BASH', 'BATTLE_TRANCE', 'BLOODLETTING',
    'BLOOD_WALL', 'BLUDGEON', 'BODY_SLAM', 'BRAND',
    'BREAK', 'BREAKTHROUGH', 'BULLY', 'BURNING_PACT',
    'CASCADE', 'CINDER', 'COLOSSUS', 'CONFLAGRATION',
    'CORRUPTION', 'CRIMSON_MANTLE', 'CRUELTY', 'DARK_EMBRACE',
    'DEFEND_IRONCLAD', 'DEMONIC_SHIELD', 'DEMON_FORM', 'DISMANTLE',
    'DOMINATE', 'DRUM_OF_BATTLE', 'EVIL_EYE', 'EXPECT_A_FIGHT',
    'FEED', 'FEEL_NO_PAIN', 'FIEND_FIRE', 'FIGHT_ME',
    'FLAME_BARRIER', 'FORGOTTEN_RITUAL', 'HAVOC', 'HEADBUTT',
    'HELLRAISER', 'HEMOKINESIS', 'HOWL_FROM_BEYOND', 'IMPERVIOUS',
    'INFERNAL_BLADE', 'INFERNO', 'INFLAME', 'IRON_WAVE',
    'JUGGERNAUT', 'JUGGLING', 'MANGLE', 'MOLTEN_FIST',
    'NOT_YET', 'OFFERING', 'ONE_TWO_PUNCH', 'PACTS_END',
    'PERFECTED_STRIKE', 'PILLAGE', 'POMMEL_STRIKE', 'PRIMAL_FORCE',
    'PYRE', 'RAGE', 'RAMPAGE', 'RUPTURE',
    'SECOND_WIND', 'SETUP_STRIKE', 'SHRUG_IT_OFF', 'SPITE',
    'STAMPEDE', 'STOKE', 'STOMP', 'STONE_ARMOR',
    'STRIKE_IRONCLAD', 'SWORD_BOOMERANG', 'TANK', 'TAUNT',
    'TEAR_ASUNDER', 'THRASH', 'THUNDERCLAP', 'TREMBLE',
    'TRUE_GRIT', 'TWIN_STRIKE', 'UNMOVABLE', 'UNRELENTING',
    'UPPERCUT', 'VICIOUS', 'WHIRLWIND',
])


def ironclad_tier(card_id: str) -> int | None:
    """The player's tier points for an Ironclad card (10/8/6), or None for
    unlisted / non-Ironclad cards."""
    return OVERRIDES.get(card_id)
