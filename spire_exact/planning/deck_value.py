"""Three-dimensional deck value model, second edition (player plan of
2026-10-05, "STS2_铁甲战士_A10_评分驱动选卡方案_workbuddy_Hy4_第二版").

Every card carries a full-value vector V_full = (A, D, U) (one cost point =
10 points). Conditional cards (~30) split it into

    V_self          the value alone in an unsupported deck (often negative),
    V_pool          the value that supply unlocks,
    V(c|D) = V_self + V_pool * sigma_eff(c|D),
    sigma_eff = min(sigma_max, supply(condition) / need).

Trigger cards (Feel No Pain, Dark Embrace, Rupture, ...) keep sigma_max > 1:
their output grows linearly with supply; quality-change cards (Barricade,
Pact's End) cap at 1.0. Cards outside the tables score (0, 0, 0) and their
dilution charge makes them strict skips; single-player-unavailable cards are
never taken. Curses: the A5 curse Ascender's Bane is Ethereal AND Eternal --
it is worth (0, -8, 0) with a 0.45 dilution factor and can NEVER be removed.

Player deltas over the document (2026-10-05): THE_GAMBIT is worth (0, 4, 0)
with named synergy pairs into BODY_SLAM / BARRICADE / UNMOVABLE; PYRE is
(0, 0, 8); the second copy of a Power pays a heavier repeat penalty
(POWER_REPEAT_MIN in route.py).

Supply counters marked "rough" (SKILL_IN_HAND, CARDS_PER_TURN, ATTACK for
STOMP) approximate the plan's wording until the real combat loops exist.

Not implemented from the plan (recorded): the tag matrix / five-engine
completion bonuses (card_mechanics synergy stays layered on top), Ctx_A10,
X-cost modelling, and the non-Bane curse table (ids unverified).
"""
from __future__ import annotations

# (A, D, U) full values, second-edition appendix A (+ player deltas).
V_TABLE = {
    'AGGRESSION': (8, 0, 8),
    'ALCHEMIZE': (0, 3, 4),
    'ANGER': (6, 0, -2),
    'ANOINTED': (0, 0, 4),
    # Player brief 2026-10-06: Armaments is a take-on-sight card -- its
    # all-hand upgrade (upgraded copy) compounds with every future card, and
    # one copy is enough for the whole run (route.py charges a heavy extra
    # repeat on the second). The old (0,-1,0) read the bare 5-block face and
    # ignored the upgrade payload entirely.
    'ARMAMENTS': (0, 6, 0),
    'ASHEN_STRIKE': (9, 0, 1),
    'ASCENDERS_BANE': (0, -8, 0),
    'AUTOMATION': (0, 0, 5),
    'BARRICADE': (0, 6, 0),
    'BASH': (-3, 0, 0),
    'BATTLE_TRANCE': (0, 0, 8),
    'BEAT_DOWN': (8, 0, 1),
    'BLOOD_WALL': (0, -4, 0),
    'BLOODLETTING': (0, -3, 10),
    # BLUDGEON (重锤) was MISSING from this table, so every lookup fell through
    # to the (0,0,0) default and the DP scored the biggest single hit in the
    # pool at zero while charging it the full thickness price -- it was
    # effectively a guaranteed skip. Source data: native catalog
    # outputs/native-catalog-check/data/catalog.json id "BLUDGEON", Attack,
    # Uncommon, energy 3, Damage 32; _sts2_zhs/cards.json:103 "造成{Damage:diff()}点伤害。".
    #
    # Priced BELOW its BIG_ATTACK peers on purpose (BREAK 28 / PACTS_END 25 /
    # STOMP 19). Measured over the 33 saved A10 IRONCLAD runs, BLUDGEON was
    # offered 21 times and taken 2 (0.095) -- a human takes a 32-damage card
    # when the deck can use it, not on sight, and the same is true of STOMP
    # (1/21, 0.048). 18 keeps it clearly worth having while leaving the
    # thickness price a real decision.
    'BLUDGEON': (18, 0, 0),
    'BODY_SLAM': (18, 0, 0),
    'BOLAS': (3, 0, 4),
    'BRAND': (5, -1, 4),
    'BREAK': (28, 0, 0),
    'BREAKTHROUGH': (5, -1, 0),
    'BULLY': (10, 0, 0),
    'BURNING_PACT': (0, 0, 4),
    'CATASTROPHE': (2, 1, 2),
    'CINDER': (-1, 0, 1),
    'COLOSSUS': (0, 3, 0),
    'CONFLAGRATION': (6, 0, 0),
    'CORRUPTION': (3, 10, 12),
    'CRIMSON_MANTLE': (0, 12, 0),
    'CRUELTY': (6, 0, 0),
    'DARK_EMBRACE': (0, 0, 10),
    'DARK_SHACKLES': (0, 9, 2),
    'DEFEND_DEFECT': (0, -5, 0),
    'DEFEND_IRONCLAD': (0, -5, 0),
    'DEFEND_NECROBINDER': (0, -5, 0),
    'DEFEND_REGENT': (0, -5, 0),
    'DEFEND_SILENT': (0, -5, 0),
    'DEMON_FORM': (10, 0, 0),
    'DISCOVERY': (2, 2, 3),
    'DISMANTLE': (6, 0, 0),
    'DOMINATE': (10, 0, 2),
    'DRAMATIC_ENTRANCE': (11, 0, 2),
    'DRUM_OF_BATTLE': (0, 0, 4),
    'ENTROPY': (0, 0, 2),
    'EQUILIBRIUM': (0, 8, 4),
    'ETERNAL_ARMOR': (0, 13, 0),
    'EVIL_EYE': (0, 8, 0),
    'EXPECT_A_FIGHT': (0, 14, 0),
    'FEED': (1, 5, 2),
    'FIEND_FIRE': (14, 0, 2),
    'FIGHT_ME': (8, -3, 0),
    'FINESSE': (0, 4, 4),
    'FLASH_OF_STEEL': (5, 0, 4),
    'FLAME_BARRIER': (1, 2, 0),
    'FORGOTTEN_RITUAL': (0, 0, 9),
    'FASTEN': (0, 7, 0),
    'FEEL_NO_PAIN': (0, 12, 0),
    'FISTICUFFS': (5, 6, 0),
    'GOLD_AXE': (9, 0, 0),
    'HAND_OF_GREED': (14, 0, 1),
    'HAVOC': (1, 0, 3),
    'HEADBUTT': (2, 0, 1),
    'HEMOKINESIS': (4.75, -1, 0),
    'HELLRAISER': (6, 0, 1),
    'HIDDEN_GEM': (2, 2, 3),
    'HOWL_FROM_BEYOND': (2, 0, 4),
    'IMPATIENCE': (0, 0, 6),
    'IMPERVIOUS': (0, 12, 0),
    'INFERNO': (14, -2, 2),
    'INFLAME': (4, 0, 0),
    'INFERNAL_BLADE': (2, 0, 1),
    'IRON_WAVE': (1, -1, 0),
    'JACK_OF_ALL_TRADES': (1, 1, 3),
    'JUGGLING': (4, 0, 8),
    'JUGGERNAUT': (13, 0, 0),
    'MANGLE': (-12, 6, 0),
    'MASTER_OF_STRATEGY': (0, 0, 10),
    'MAYHEM': (2, 2, 3),
    'MIND_BLAST': (8, 0, 0),
    # Player brief 2026-10-07: base cut (8,0,2) -> (6,0,2). Molten Fist is
    # the vulnerable deck's CONSUMER (its text doubles the enemy's stacks) and
    # the native catalog gives it no VulnerablePower variable; it was priced as
    # if it supplied the status. See dataset section 18.23.
    'MOLTEN_FIST': (6, 0, 2),
    'NOSTALGIA': (0, 0, 8),
    'NOT_YET': (0, -8, 0),
    'OFFERING': (0, -5, 22),
    'OMNISLICE': (10, 0, 0),
    'ONE_TWO_PUNCH': (2, 0, 2),
    'PACTS_END': (25, 0, 0),
    'PANACHE': (7, 0, 2),
    'PANIC_BUTTON': (0, 10, 0),
    'PERFECTED_STRIKE': (-2, 0, 0),
    'PILLAGE': (0, 0, 3),
    'POMMEL_STRIKE': (2, 0, 1),
    'PREP_TIME': (6, 0, 0),
    'PRIMAL_FORCE': (3, 0, 2),
    'PRODUCTION': (0, 0, 10),
    'PROLONG': (0, 6, 0),
    'PROWESS': (6, 5, 0),
    'PURITY': (0, 0, 10),
    'PYRE': (0, 0, 4),   # player brief 2026-10-06: lower (energy, not draw)
    'RAGE': (0, 14, 0),
    'RAMPAGE': (7, 0, 0),
    'REND': (11, 0, 0),
    'RESTLESSNESS': (0, 0, 4),
    'ROLLING_BOULDER': (14, 0, 0),
    'RUPTURE': (8, 0, 0),
    'SALVO': (4, 0, 8),
    'SCRAWL': (0, 0, 10),
    'SECOND_WIND': (0, 8, 2),
    'SECRET_TECHNIQUE': (0, 0, 9),
    'SECRET_WEAPON': (0, 0, 9),
    'SEEKER_STRIKE': (5, 0, 6),
    'SETUP_STRIKE': (2, 0, 0),
    'SHOCKWAVE': (8, 6, 2),
    'SHRUG_IT_OFF': (0, 1, 1),
    'SPITE': (10, 0, 0),
    'SPLASH': (3, 0, 2),
    'STAMPEDE': (2, 0, 0),
    'STOMP': (19, 0, 0),
    'STONE_ARMOR': (0, 6, 0),
    'STRATAGEM': (0, 0, 4),   # player brief 2026-10-06: lower (shuffle-gated)
    'STRIKE_DEFECT': (-4, 0, 0),
    'STRIKE_IRONCLAD': (-4, 0, 0),
    'STRIKE_NECROBINDER': (-4, 0, 0),
    'STRIKE_REGENT': (-4, 0, 0),
    'STRIKE_SILENT': (-4, 0, 0),
    'STOKE': (2, 2, 10),   # player brief 2026-10-07: raise all three dims -- the
                           # exhaust-and-refill loop earns extra attacks (A) and
                           # block plays (D) beyond the raw draw value (U 7->10)
    'SWORD_BOOMERANG': (-1, 0, 0),
    'TAUNT': (2, 1, 0),
    'TEAR_ASUNDER': (3, 0, 0),
    'THE_BOMB': (6, 0, 0),
    'THE_GAMBIT': (0, 4, 0),
    'THINKING_AHEAD': (0, 0, 8),
    'THRASH': (7, 0, 1),
    'THUNDERCLAP': (3, 0, 0),
    'THRUMMING_HATCHET': (7, 0, 6),
    'TREMBLE': (2, 0, 3),
    'TRUE_GRIT': (0, -1, 0),
    'TWIN_STRIKE': (1, 0, 0),
    'ULTIMATE_DEFEND': (0, 10, 0),
    'ULTIMATE_STRIKE': (13, 0, 0),
    'UNMOVABLE': (0, 12, 0),
    'UNRELENTING': (0, 0, 2),
    'UPPERCUT': (1, 1, 0),
    'VICIOUS': (0, 0, 4),
    'VOLLEY': (8, 0, 0),
    'WHIRLWIND': (3, 0, 0),
}

UNAVAILABLE = frozenset({
    'TANK', 'DEMONIC_SHIELD', 'BEACON_OF_HOPE', 'MIMIC', 'LIFT',
    'COORDINATE', 'TAG_TEAM', 'GANG_UP', 'RALLY', 'HUDDLE_UP', 'INTERCEPT',
    'BELIEVE_IN_YOU', 'KNOCKDOWN',
})

# Second-edition appendix C: (V_self, V_pool, (condition tag, need), sigma_max).
CONDITION_POOL = {
    # Player brief 2026-10-06: Body Slam's bare copy is slightly positive, not
    # a dead card (it still swings with Bash/potions block now and then); the
    # curve rises gently for the first big-block source and only pays off at
    # 2+ (sigma table below): supply 0/1/2/3+ -> attack +0.5/+6/+18/+32. The
    # invariant anchor stays V_self + V_pool = V_TABLE at the sigma=1.0 step.
    'BODY_SLAM': ((0.5, 0, 0), (17.5, 0, 0), ('BIG_BLOCK_SRC', 2), 1.5),
    'TEAR_ASUNDER': ((-15, 0, 0), (18, 0, 0), ('HPL_SRC', 3), 2.0),
    'SECOND_WIND': ((0, -10, 0), (0, 18, 2), ('SKILL_IN_HAND', 2), 2.0),
    'PERFECTED_STRIKE': ((-14, 0, 0), (12, 0, 0), ('STRIKE', 4), 2.0),
    'STOMP': ((-11, 0, 0), (30, 0, 0), ('ATTACK', 3), 1.5),
    'EXPECT_A_FIGHT': ((0, -16, 0), (0, 30, 0), ('STR_SRC', 2), 2.0),
    'PACTS_END': ((0, 0, 0), (25, 0, 0), ('EXH_SRC', 3), 1.0),
    'FEEL_NO_PAIN': ((0, -1, 0), (0, 13, 0), ('EXH_SRC', 3), 2.5),
    'DARK_EMBRACE': ((0, 0, -1), (0, 0, 11), ('EXH_SRC', 3), 2.5),
    'ASHEN_STRIKE': ((-4, 0, 0), (13, 0, 1), ('EXH_SRC', 3), 2.0),
    'RUPTURE': ((0, 0, -1), (8, 0, 1), ('HPL_SRC', 3), 2.5),
    'JUGGERNAUT': ((0, 0, -1), (13, 0, 1), ('BLK_SRC', 3), 2.5),
    'HELLRAISER': ((0, 0, -1), (6, 0, 2), ('STRIKE', 4), 2.0),
    'CRUELTY': ((0, 0, -1), (6, 0, 1), ('VULN_SRC', 2), 1.0),
    'JUGGLING': ((0, 0, -1), (4, 0, 9), ('ATTACK', 4), 1.5),
    'STAMPEDE': ((0, 0, -1), (2, 0, 1), ('ATTACK', 4), 1.5),
    'AGGRESSION': ((0, 0, -1), (8, 0, 9), ('ATTACK', 6), 1.5),
    'VICIOUS': ((0, 0, -1), (0, 0, 5), ('VULN_SRC', 2), 2.0),
    'BARRICADE': ((0, 0, -2), (0, 6, 2), ('BLK_SRC', 3), 1.0),
    # Player brief 2026-10-06: Colossus is the vulnerable deck's defensive
    # payoff -- it must scale with the deck's vulnerable supply (its card text
    # reduces damage by 50% from enemies that ARE vulnerable). V_self + V_pool
    # must stay equal to V_TABLE['COLOSSUS'] = (0, 3, 0).
    'COLOSSUS': ((0, -5, 0), (0, 8, 0), ('VULN_SRC', 2), 2.0),
    'BURNING_PACT': ((0, 0, -3), (0, 0, 7), ('EXH_SELF', 1), 1.0),
    'DISMANTLE': ((-2, 0, 0), (8, 0, 0), ('VULN_SRC', 1), 1.5),
    'EVIL_EYE': ((0, -2, 0), (0, 10, 0), ('EXH_SRC', 1), 1.5),
    'RAGE': ((0, 0, 0), (0, 14, 0), ('ATTACK', 4), 2.0),
    'BULLY': ((4, 0, 0), (6, 0, 0), ('VULN_SRC', 2), 1.5),
    'SPITE': ((5, 0, 0), (5, 0, 0), ('HPL_SRC', 1), 1.0),
    # Player brief 2026-10-07: base cut to (6,0,2), so V_self (2,0,2) +
    # V_pool (4,0,0); the invariant V_self + V_pool = V_TABLE holds.
    'MOLTEN_FIST': ((2, 0, 2), (4, 0, 0), ('VULN_SRC', 1), 1.0),
    'DEMON_FORM': ((4, 0, 0), (6, 0, 0), ('STR_AMP', 2), 1.5),
    'INFERNO': ((6, -2, 2), (8, 0, 0), ('HPL_SRC', 2), 2.5),  # self counts 1
    'FASTEN': ((0, -1, 0), (0, 8, 0), ('DEFEND_CARDS', 3), 1.5),
    'MIND_BLAST': ((-2, 0, 0), (10, 0, 0), ('DRAW_PILE', 12), 1.0),
    'REND': ((-3, 0, 0), (14, 0, 0), ('DEBUFF_SRC', 2), 1.5),
    'PANACHE': ((3, 0, 1), (4, 0, 1), ('CARDS_PER_TURN', 5), 1.0),
}

# 高伤单卡（方案 3.4 表「高伤单卡（>=14 伤）」）。成员由原生目录
# outputs/native-catalog-check/data/catalog.json 推导：IRONCLAD_CARDS 中
# Damage 变量 >= 14 的卡；需要时从该文件重新生成，不要手工增删。
# 2026-10-07 实测核对：ironclad_tiers.IRONCLAD_CARDS(87) ∩ catalog 的
# Damage>=14 == 这 8 张（BLUDGEON 32 / MANGLE 20 / BREAK 20 / PACTS_END 18 /
# CINDER 18 / HOWL_FROM_BEYOND 18 / HEMOKINESIS 15 / UNRELENTING 14）。
BIG_ATTACK = frozenset({
    'BLUDGEON', 'MANGLE', 'BREAK', 'PACTS_END', 'HOWL_FROM_BEYOND',
    'CINDER', 'HEMOKINESIS', 'UNRELENTING',
})
# PAIR_BONUS 的 `other` 槽可以写成 '*SET_NAME'，指向下面某个集合；这样一条
# 配合规则就能覆盖一整类卡，而不必为每张卡各写一行。
WILDCARD_SETS = {'*BIG_ATTACK': BIG_ATTACK}

# Player delta (2026-10-05): THE_GAMBIT earns named synergy with the
# hand-retention / block engines, credited in the DEFENSE dimension.
# 条目形如 (bonus, dim)，dim 是 DIMS 的下标：0 = A（进攻）, 1 = D（防御）,
# 2 = U（引擎）。
PAIR_BONUS = {
    ('THE_GAMBIT', 'BODY_SLAM'): (4.0, 1),
    ('THE_GAMBIT', 'BARRICADE'): (4.0, 1),
    ('THE_GAMBIT', 'UNMOVABLE'): (2.0, 1),
    # 用户 2026-10-07 裁决（D4）：只补 THRASH x 高伤单卡。THRASH 卡面为
    # 「造成伤害两次。消耗你的手牌中随机一张攻击牌，并将它的伤害添加给这张牌」，
    # 与高伤单卡是真实配合；方案 3.4 表把这一行记在头槌名下，属方案自身的错配。
    ('THRASH', '*BIG_ATTACK'): (3.0, 0),
}

# Exhaust sources, split by HOW MANY cards each actually exhausts. Player
# correction of 2026-10-06: 「那种只消耗一两张的牌确实不应该多拿，消耗源本来就是
# 需要类似消耗所有手牌这种高质量的多，每回合只有 3 点费用」. Membership and class
# are read off the card text in D:/dsh工作区1/_card_names.json; regenerate from that
# file, do not hand-edit individual entries.
#   MASS   exhausts the whole hand, or exhausts continuously -> 1.0 each
#   SINGLE exhausts exactly one card                     -> EXH_SINGLE_WEIGHT each
# Two cards the earlier flat list wrongly carried are REMOVED, because the card
# text says they exhaust nothing:
#   DRUM_OF_BATTLE  "抽2张牌。这张牌被消耗时，获得能量"  -> it is a DRAW/NRG card
#   PRIMAL_FORCE    "将手牌中的所有攻击牌变化为巨石"     -> Transform is not Exhaust
# Both keep their DRAW / NRG membership, which the plan's appendix B does list.
EXH_SRC_MASS = frozenset({
    'CORRUPTION',       # 每当你打出一张技能牌时，将其消耗（持续）
    'FIEND_FIRE',       # 消耗所有手牌
    'STOKE',            # 消耗所有手牌
    'SECOND_WIND',      # 消耗手牌中所有非攻击牌
})
EXH_SRC_SINGLE = frozenset({
    'BRAND',            # 消耗1张牌
    'BURNING_PACT',     # 消耗1张牌
    'CINDER',           # 随机消耗1张牌
    'TRUE_GRIT',        # （随机）消耗1张牌
    'HAVOC',            # 打出抽牌堆顶部的牌并将其消耗
    'THRASH',           # 消耗手牌中随机一张攻击牌
})
EXH_SINGLE_WEIGHT = 0.35
EXH_SRC = EXH_SRC_MASS | EXH_SRC_SINGLE


def exhaust_weight(card_id: str) -> float:
    """How many cards one copy of card_id counts as exhausting (2026-10-06)."""
    if card_id in EXH_SRC_MASS:
        return 1.0
    if card_id in EXH_SRC_SINGLE:
        return EXH_SINGLE_WEIGHT
    return 0.0
# 消耗自身的牌（EXH_SELF），8 个成员。用户 2026-10-07 裁决（D3）移除了两个：
#   BRAND           消耗的是别的牌，不属于消耗自身；它仍在 EXH_SRC_SINGLE 里
#                   （消耗1张牌），一分没丢。
#   HOWL_FROM_BEYOND 它是被消耗的受益者（消耗你的手牌中所有攻击牌），不是消耗
#                   自身的那一张。
# 依据：原生目录 outputs/native-catalog-check/data/catalog.json 中这两张卡的
# keywords 都不含 Exhaust，所以它们本就不该拿 0.7 的 EXHAUST_PHI。
EXH_SELF = frozenset({
    'IMPERVIOUS', 'OFFERING', 'FEED', 'MOLTEN_FIST', 'TREMBLE',
    'FORGOTTEN_RITUAL', 'FIEND_FIRE', 'INFERNAL_BLADE'})
HPL_SRC = frozenset({
    'BLOODLETTING', 'OFFERING', 'HEMOKINESIS', 'BREAKTHROUGH', 'BRAND',
    'BLOOD_WALL', 'CRIMSON_MANTLE', 'INFERNO'})
BLK_SRC = frozenset({
    'IMPERVIOUS', 'BLOOD_WALL', 'FLAME_BARRIER', 'CRIMSON_MANTLE',
    'STONE_ARMOR', 'RAGE', 'EVIL_EYE', 'COLOSSUS', 'TAUNT', 'DEFEND_IRONCLAD',
    'DEFEND_SILENT', 'DEFEND_DEFECT', 'DEFEND_NECROBINDER', 'DEFEND_REGENT',
    'IRON_WAVE', 'ARMAMENTS', 'TRUE_GRIT', 'SECOND_WIND', 'FEEL_NO_PAIN',
    'SHRUG_IT_OFF'})
BIG_BLOCK_SRC = frozenset({
    'IMPERVIOUS', 'BLOOD_WALL', 'FLAME_BARRIER', 'CRIMSON_MANTLE',
    'STONE_ARMOR', 'UNMOVABLE'})
# Player brief 2026-10-07: MOLTEN_FIST removed. Its text doubles the enemy's
# EXISTING vulnerable stacks -- a consumer -- and the native catalog gives it no
# VulnerablePower variable, so it never supplied the status it was counted for.
# VICIOUS is misclassified the same way (its text triggers on GIVING vulnerable)
# but the brief named only Molten Fist, so VICIOUS stays pending a ruling.
# Dataset section 18.23.
VULN_SRC = frozenset({
    'BASH', 'TREMBLE', 'UPPERCUT', 'TAUNT', 'THUNDERCLAP',
    'SHOCKWAVE', 'BREAK', 'VICIOUS'})
STR_SRC = frozenset({
    'INFLAME', 'DEMON_FORM', 'BRAND', 'RUPTURE', 'FIGHT_ME', 'DOMINATE',
    'SETUP_STRIKE'})
STR_AMP = frozenset({'TWIN_STRIKE', 'SWORD_BOOMERANG', 'WHIRLWIND',
                     'CONFLAGRATION'})
ETERNAL = frozenset({'ASCENDERS_BANE'})
ETHEREAL = frozenset({'ASCENDERS_BANE'})
POWER_PHI = 0.45
EXHAUST_PHI = 0.7
ETHEREAL_PHI = 0.45
# 方案 :652 的 phi 四档里没有「消耗他牌」这一档，所以纯消耗源（EXH_SRC，即
# 消耗别的牌的那些）取常态 1.00，而不是消耗自身的 0.7。用户 2026-10-07 裁决 D2。
EXH_SRC_PHI = 1.0

# Player brief 2026-10-06: strengthen the THREE-DIMENSIONAL demands rather than
# leaning on the total score, and raise the DRAW (U) axis in particular -- the
# model was letting U sit satisfied and therefore cheap (marginal_weight falls
# to ETA once gap <= 0). Multipliers apply per dimension to the plan's lines.
REQ_SCALE = (1.05, 1.05, 1.5)
REQ_PER_ACT = tuple(
    tuple(base[i] * REQ_SCALE[i] for i in range(3))
    for base in (
        (25 * 79 / 60, 22 * 79 / 55, 18 * 57 / 45),
        (45 * 79 / 60, 40 * 79 / 55, 32 * 57 / 45),
        (79.0, 79.0, 57.0),
    )
)
RHO = (1.0, 1.2, 1.1)
ETA = 0.35
WEAK_THETA = 6.0
WEAK_MU = 1.0
DIMS = ('A', 'D', 'U')

# --- Deck-size treatment of draw cards (player brief 2026-10-06) -----------
# Three tiers, from the player's own wording:
#   过牌卡享受较低规模惩罚   draw cards pay a REDUCED deck-size penalty
#   优质过牌不受到规模惩罚   premium draw pays NO deck-size penalty
#   战斗专注享受额外重复惩罚 Battle Trance carries an extra repeat charge
#
# PREMIUM DRAW: the reliable, self-contained engines -- they fetch or draw
# regardless of what else the deck does. BATTLE_TRANCE is deliberately NOT
# here (its text blocks all further draw that turn) and neither is STRATAGEM
# (it only fires on a shuffle).
PREMIUM_DRAW = frozenset({
    'OFFERING', 'DARK_EMBRACE', 'MASTER_OF_STRATEGY', 'SCRAWL',
    'SECRET_TECHNIQUE', 'SECRET_WEAPON', 'THINKING_AHEAD',
})
# No deck-size penalty in ANY case (player brief: 'a card that adds energy AND
# draws, or a 0-cost card that draws, is exempt from the deck-size penalty in
# any case'). Costs verified against D:/dsh工作区1/_codex_cards.json (577 rows).
SIZE_EXEMPT = frozenset({
    # energy + draw
    'OFFERING', 'AUTOMATION', 'DRUM_OF_BATTLE', 'RESTLESSNESS',
    # 0-cost and draws / fetches
    'MASTER_OF_STRATEGY', 'SECRET_TECHNIQUE', 'SECRET_WEAPON',
    # BATTLE_TRANCE removed 2026-10-09: its text blocks all further draw that
    # turn, so it is not a 「0-cost draw」 in the spirit of this list, and the
    # exemption made its second copy nearly free (repeat_penalty 1.25 against a
    # take_dims of ~11 with size_term 0). Measured on seed 2098492050: charging
    # it removed the duplicate outright (25 -> 24 cards). It now falls into
    # DRAW_CARDS and pays the reduced 0.25 scale.
    'THINKING_AHEAD', 'IMPATIENCE', 'FINESSE',
    'FLASH_OF_STEEL', 'JACK_OF_ALL_TRADES',
})
# Every other card that actually puts cards in hand: reduced, not exempt.
# Membership is by card text (draws, or fetches from the draw pile into hand).
DRAW_CARDS = frozenset({
    'AGGRESSION', 'ANOINTED', 'AUTOMATION', 'BATTLE_TRANCE', 'BURNING_PACT',
    'CATASTROPHE', 'DARK_EMBRACE', 'DISCOVERY', 'DRUM_OF_BATTLE', 'FINESSE',
    'FLASH_OF_STEEL', 'HAVOC', 'HEADBUTT', 'HELLRAISER', 'HIDDEN_GEM',
    'IMPATIENCE', 'INFERNAL_BLADE', 'JACK_OF_ALL_TRADES', 'JUGGLING',
    'MASTER_OF_STRATEGY', 'MAYHEM', 'MIND_BLAST', 'NOSTALGIA', 'OFFERING',
    'PILLAGE', 'POMMEL_STRIKE', 'RESTLESSNESS', 'SCRAWL', 'SECRET_TECHNIQUE',
    'SECRET_WEAPON', 'SEEKER_STRIKE', 'SHRUG_IT_OFF', 'SPLASH', 'STOKE',
    'STRATAGEM', 'THINKING_AHEAD', 'VICIOUS',
})
# The reduced share of the normal deck-size charge a plain draw card pays.
DRAW_PENALTY_SCALE = 0.25


def _count(deck, cards) -> int:
    return sum(deck.get(c, 0) for c in cards)


def _attacks(deck) -> int:
    from .card_types import get_category
    return sum(n for c, n in deck.items() if get_category(c) == 'attack')


# The ids the STRIKE supply counts (same predicate the old inline sum used).
_STRIKE_IDS = frozenset({'POMMEL_STRIKE', 'TWIN_STRIKE', 'SETUP_STRIKE',
                         'ASHEN_STRIKE', 'PERFECTED_STRIKE'})
# The non-attack ids CARDS_PER_TURN adds on top of the deck's attacks.
_CARDS_PER_TURN_EXTRA = frozenset({'POMMEL_STRIKE', 'BATTLE_TRANCE',
                                   'BURNING_PACT', 'OFFERING'})


def _is_strike(card_id: str) -> bool:
    return card_id.startswith('STRIKE_') or card_id in _STRIKE_IDS


def supply(tag: str, deck, card_id: str = '') -> float:
    """Supply count of one condition in the deck (excluding the card itself).

    2026-10-07 fix (defect 4, self-supply off-by-one). take_dims and
    weak_penalty judge a card on the PRE-take deck, where the card itself is
    NOT yet in `deck`. The unconditional "- 1" therefore subtracted a copy
    that was never counted, so the supply -- and hence sigma -- came out a
    full step too low at the take point (BURNING_PACT's EXH_SELF sigma even
    went negative). Every self-subtraction is now conditional on the card
    actually being present, so supply(tag, D, c) is correct on BOTH sides of
    the take: on the pre-take deck (nothing of c to subtract) and on the
    post-take deck D+c (exactly one copy of c to subtract).

    The HPL_SRC branch folds the INFERNO special case into that same rule,
    which also removes an older double count: INFERNO is a member of HPL_SRC,
    so it was counted once as a member and then +1 again for self-supply.
    Plan :235 「不含本卡；自带供给的卡如狱火计入自身 1 张」 asks for exactly one.

    STRIKE / DRAW_PILE / DEFEND_CARDS / SKILL_IN_HAND / CARDS_PER_TURN are
    untouched: they never subtracted the card itself, so they were never part
    of this defect.
    """
    present = deck.get(card_id, 0) > 0 if card_id else False
    if tag == 'EXH_SRC':
        # Weighted by how many cards each source exhausts (2026-10-06): a
        # whole-hand source counts 1.0, a one-card source EXH_SINGLE_WEIGHT.
        return (sum(n * exhaust_weight(card) for card, n in deck.items())
                - (exhaust_weight(card_id) if present else 0.0))
    if tag == 'HPL_SRC':
        n = _count(deck, HPL_SRC)
        if card_id in HPL_SRC and present:
            n -= 1
        if card_id == 'INFERNO':
            n += 1                      # self-supply, plan appendix E.3 / :235
        return n
    if tag == 'BLK_SRC':
        return _count(deck, BLK_SRC) - (1 if card_id in BLK_SRC and present else 0)
    if tag == 'BIG_BLOCK_SRC':
        return _count(deck, BIG_BLOCK_SRC) - (1 if card_id in BIG_BLOCK_SRC and present else 0)
    if tag == 'VULN_SRC':
        return _count(deck, VULN_SRC) - (1 if card_id in VULN_SRC and present else 0)
    if tag == 'STR_SRC':
        return _count(deck, STR_SRC) - (1 if card_id in STR_SRC and present else 0)
    if tag == 'STR_AMP':
        return _count(deck, STR_AMP) - (1 if card_id in STR_AMP and present else 0)
    if tag == 'STRIKE':
        total = sum(k for c, k in deck.items()
                    if _is_strike(c))
        return total - (1 if present and _is_strike(card_id) else 0)
    if tag == 'ATTACK':
        from .card_types import get_category
        return _attacks(deck) - (
            1 if present and get_category(card_id) == 'attack' else 0)
    if tag == 'SKILL_IN_HAND':
        # Rough: non-attack cards beyond the basic hand skeleton. Fixed
        # 2026-10-09: no self-supply subtraction. SECOND_WIND is the only
        # member of this tag, so subtracting it meant the card counted itself
        # as consumable fuel for its own payoff -- every extra copy bought more
        # supply and kept clearing the need, which is opposite to what
        # SECOND_WIND is worth (one copy already answers a whole hand). The
        # drop of 4 is the hand skeleton; whether the new card is one of those
        # 4 is not modelled here.
        from .card_types import get_category
        total = sum(deck.values()) - _attacks(deck)
        return max(0.0, total - 4)
    if tag == 'DEFEND_CARDS':
        total = sum(k for c, k in deck.items() if c.startswith('DEFEND_'))
        return total - (1 if present and card_id.startswith('DEFEND_') else 0)
    if tag == 'DRAW_PILE':
        return sum(deck.values()) - (1 if present else 0)
    if tag == 'DEBUFF_SRC':
        return _count(deck, VULN_SRC) - (1 if card_id in VULN_SRC and present else 0)
    if tag == 'CARDS_PER_TURN':
        from .card_types import get_category
        total = _attacks(deck) + _count(deck, _CARDS_PER_TURN_EXTRA)
        if present and (get_category(card_id) == 'attack'
                        or card_id in _CARDS_PER_TURN_EXTRA):
            total -= 1
        return total
    if tag == 'EXH_SELF':
        return _count(deck, EXH_SRC | EXH_SELF) - (
            1 if (card_id in EXH_SRC or card_id in EXH_SELF) and present else 0)
    return 0.0


# Player brief 2026-10-06: qualitative (质变) pool payoffs do not stack -- a
# second copy changes nothing the first copy did not already change. For these,
# every copy already in the deck DOUBLES the sigma need, so the pool payoff of
# duplicate takes collapses toward V_self. Membership is the player's explicit
# list (2026-10-06): the sigma_max<=1.0 state-change cards MINUS the five the
# player moved out (MIND_BLAST / MOLTEN_FIST / PACTS_END / PANACHE /
# BURNING_PACT -- their payoffs do scale with copies), PLUS DARK_EMBRACE /
# DEMON_FORM / HELLRAISER which the player moved in. COLOSSUS is included: its
# 50% vulnerable-damage reduction is per-turn and non-cumulative (its
# sigma_max 2.0 rated the two vulnerability windows, not two copies). All
# other CONDITION_POOL cards are trigger cards: each copy adds real per-turn
# triggers, so duplicates keep full pool value.
QUALITATIVE_POOL = frozenset({
    'BARRICADE', 'CRUELTY', 'SPITE',    # sigma_max 1.0 originals kept
    'COLOSSUS',
    'DARK_EMBRACE', 'DEMON_FORM', 'HELLRAISER',   # player move-ins
})

# Player brief 2026-10-06: per-card sigma step tables, overriding the default
# linear supply/need ramp where the payoff curve is genuinely stepped. Keyed
# by supply count clamped at the last entry. BODY_SLAM's curve from the same
# brief: 0/1/2/3+ big-block sources -> sigma 0 / 5.5/17.5 / 1.0 / 1.8, so with
# V_self +0.5 and V_pool +17.5 the realized attack is +0.5 / +6 / +18 / +32
# (the entry's sigma_max 1.5 does not apply to stepped cards; the last step is
# the effective cap). Note 5.5/17.5 and 1.8 are curve points, not reachable
# states of the linear ramp.
SIGMA_STEPS = {
    'BODY_SLAM': (0.0, 5.5 / 17.5, 1.0, 1.8),
}


def realized_dims(card_id: str, deck):
    """V(c|D) = V_self + V_pool * sigma_eff; (0, 0, 0) when unlisted, None
    when the card cannot be used at all."""
    if card_id in UNAVAILABLE:
        return None
    full = V_TABLE.get(card_id, (0, 0, 0))
    entry = CONDITION_POOL.get(card_id)
    if entry is None:
        return full
    self_v, pool_v, (tag, need), smax = entry
    copies = deck.get(card_id, 0)
    if card_id in QUALITATIVE_POOL and copies:
        need = need * (2 ** copies)
    if card_id in SIGMA_STEPS:
        sigma = SIGMA_STEPS[card_id][min(int(supply(tag, deck, card_id)),
                                         len(SIGMA_STEPS[card_id]) - 1)]
    else:
        sigma = min(smax, supply(tag, deck, card_id) / need)
    return tuple(self_v[i] + pool_v[i] * sigma for i in range(3))


def phi(card_id: str) -> float:
    """Draw-frequency factor of the dilution charge (plan 4.3).

    Plan :652 has exactly four tiers: 常态 1.0 / 消耗自身 0.7 / Ethereal 0.45 /
    Power 0.45. It has NO tier for 消耗他牌, so a pure exhaust SOURCE (EXH_SRC)
    takes the normal 1.0 -- it is not itself consumed. Before 2026-10-07 both
    sets shared one branch, which charged every source the 0.7 of a
    self-exhausting card and inflated its dilution charge. Player ruling D2.

    EXH_SELF is tested first on purpose: FIEND_FIRE is in both sets (it
    exhausts itself AND the whole hand) and keeps the 0.7 of a self-exhausting
    card.
    """
    if card_id in ETHEREAL:
        return ETHEREAL_PHI
    if card_id in EXH_SELF:
        return EXHAUST_PHI
    if card_id in EXH_SRC:
        return EXH_SRC_PHI
    from .card_kinds import is_power
    if is_power(card_id):
        return POWER_PHI
    return 1.0


def dims_of(card_id: str):
    """V_full of one card; (0, 0, 0) when unlisted, None when unusable."""
    if card_id in UNAVAILABLE:
        return None
    return V_TABLE.get(card_id, (0, 0, 0))


def deck_dims(deck):
    """(A_deck, D_deck, U_deck) over REALIZED per-card values."""
    a = d = u = 0.0
    for card_id, n in deck.items():
        v = realized_dims(card_id, deck)
        if v:
            a, d, u = a + v[0] * n, d + v[1] * n, u + v[2] * n
    return (a, d, u)


def marginal_weight(dim_value, req, rho):
    gap = req - dim_value
    if gap <= 0:
        return ETA
    return 1.0 + 0.125 * rho * (gap ** 0.25)


def weights(deck_dims_value, act_index):
    req = REQ_PER_ACT[min(max(act_index, 0), 2)]
    return tuple(marginal_weight(deck_dims_value[i], req[i], RHO[i]) for i in range(3))


def _pair_holds(host: str, other: str, card_id: str, deck) -> bool:
    """True when the PAIR_BONUS entry (host, other) fires for taking card_id.

    'other' may be a '*SET_NAME' wildcard key of WILDCARD_SETS (2026-10-07,
    player ruling D4), in which case the pair fires when the deck already
    holds at least one member of that set.
    """
    if host != card_id:
        return False
    if other.startswith('*'):
        members = WILDCARD_SETS.get(other)
        return bool(members) and any(deck.get(m, 0) > 0 for m in members)
    return deck.get(other, 0) > 0


def take_dims(card_id, deck, act_index, synergy_bonus=0.0):
    """Marginal value of taking one card: the deck's shortfall weights applied
    to the card's FULL marginal contribution, plus the named pair bonus it
    earns with what the deck already holds, plus the synergy bonus on its
    dominant dim. None = never take (unusable or Eternal).

    2026-10-07 (player rulings D1/D6): the per-dim vector is now the difference
    of deck_dims across the take, deck_dims(D + c) - deck_dims(D), instead of
    the card's own realized_dims on the pre-take deck. That single change
    carries both fixes the plan asks for:

      * F3 / defect 4 -- every conditional card inside deck_dims is now
        evaluated on the post-take deck D+c, so a self-supplying card counts
        its own copy (plan :227, V(c | D 并 {c})).
      * F5 / delta_unlock (plan :133/:138, implementation II of plan 7.2) --
        the difference automatically includes what taking c does to the
        conditional cards ALREADY in the deck, which the old form ignored.
        Verified 2026-10-07: with four consumers held, taking CORRUPTION moves
        deck_dims by (A +15.667, D +14.333, U +14.167) while take_dims moved
        only 34.2182 -> 34.2816.

    The weights w are deliberately read from the PRE-take deck: the plan prices
    the marginal at the shortfall the deck has when the choice is made.

    PAIR_BONUS and the caller's synergy() bonus remain SEPARATE additive terms
    and are not inside deck_dims, so neither is double counted by the
    difference -- which is the current form of the plan :590 mutual-exclusion
    rule (player ruling D7).
    """
    if card_id in UNAVAILABLE or card_id in ETERNAL:
        return None
    deck_with_card = dict(deck)
    deck_with_card[card_id] = deck_with_card.get(card_id, 0) + 1
    w = weights(deck_dims(deck), act_index)
    before, after = deck_dims(deck), deck_dims(deck_with_card)
    v = tuple(after[i] - before[i] for i in range(3))
    main = max(range(3), key=lambda i: v[i])
    value = sum(w[i] * v[i] for i in range(3))
    for (a, b), (bonus, dim) in PAIR_BONUS.items():
        if _pair_holds(a, b, card_id, deck) or _pair_holds(b, a, card_id, deck):
            value += w[dim] * bonus
    if synergy_bonus:
        value += synergy_bonus * w[main]
    return value


def weak_penalty(card_id, deck=None):
    """Dilution charge for adding one weak card (plan 4.3): judged on the
    REALIZED value and weighted by the card's draw frequency phi."""
    v = realized_dims(card_id, deck) if deck else dims_of(card_id)
    if not v:
        return WEAK_MU * WEAK_THETA
    return WEAK_MU * max(0.0, WEAK_THETA - max(v)) * phi(card_id)


def remove_value(card_id, deck, act_index):
    """Marginal value of removing one copy (plan 5.1). None for Eternal cards
    (Ascender's Bane cannot leave the deck)."""
    if card_id in ETERNAL:
        return None
    v = realized_dims(card_id, deck)
    if v is None:
        return 0.0
    w = weights(deck_dims(deck), act_index)
    return (sum(w[i] * (-v[i]) for i in range(3))
            + weak_penalty(card_id, deck)
            + 1.0)
