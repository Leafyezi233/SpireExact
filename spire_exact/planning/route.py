"""Route planner: scores the seed's full strategic routes from skirmish facts.

Input is the merged option tree of walker.merge_walks: every root-to-leaf path
is one complete strategic route whose card-reward menus, shops, events and
encounters are seed-fixed per strategic path position (equivalence evidence in
walker). The planner enumerates the paths and scores each one; the best route
is an allocation signal for the search — it never changes legality and the
search may fall back to an unconstrained run when the route fails.

Valuation model (layered, per the player brief of 2026-10-04): acts 1-2 buy
deck / relic development while staying alive, act 3 buys survival. Deck
thinning is a first-class term: taking a card costs deck size, skipping is
always available and free, and shop removal is worth points — the deck-size
value is seed- and deck-dependent (gatemodel size experiment, sign flipped by
seed), so it stays a fixed small penalty here, not a learned signal.

Allocation only: a route never bounds the search, never enters a trajectory,
and its facts come from skirmish walks that reach no cache or checkpoint.
"""
from __future__ import annotations
from collections import Counter
from .policy_data import TIERS
from .card_types import get_category
from .card_kinds import is_power
from .ironclad_tiers import IRONCLAD_CARDS, OVERRIDES, UNLISTED_CAP
from .card_mechanics import (ARCHETYPE_BIAS_SCALE, ARCHETYPE_CORES,
                          ARCHETYPE_ORDER, archetype_bias, relic_synergy,
                          synergy)
from .deck_value import (REQ_PER_ACT, deck_dims, dims_of, take_dims,
                         weak_penalty, remove_value as dim_remove_value,
                         exhaust_weight, PREMIUM_DRAW,
                         SIZE_EXEMPT, DRAW_CARDS, DRAW_PENALTY_SCALE)

# Per-act community pick tiers (-2..2), the same table the host reads as
# policy_prior. A negative tier card is one the table says not to pick.
PICK_TIERS = TIERS['pick']

# Points: development bought in acts 1-2 vs survival spent in act 3. Act 3
# triples every danger. Unknown rooms carry monster-lite danger (could be an
# elite). A rest site banks credit against the next fights of the same act.
ELITE_VALUE = 18.0       # act 1-2 elite fought: relic economy. Was 6, then 9
                         # on the old ~2-10 card scale; the plan's 1-cost=10
                         # scale doubles card values, so the credit doubles too.
REST_CREDIT = 5.0        # one rest site offsets about one monster fight
# Deck thinning (player brief of 2026-10-04: more cards is not better). Taking
# a card costs the CURRENT act's deck-size penalty, a literal step table, not
# a slope; skip is always free (宁缺毋滥). Per act:
#   act 1: size <14 -> 1, ==14 -> 2, ==15 -> 5, then +1 per card past 15
#   act 2: size <17 -> 4, ==17 -> 5, then +1 per card past 17
#   act 3: size <18 -> 4, ==18 -> 5, then +1 per card past 18
# Each row is (below the step, at the step, the step size, the high value, the
# size the high value starts at). Past the high value every extra card costs
# TAKE_PENALTY_SLOPE, not 1.0 (player instruction 2026-10-09): 1.5 was the
# smallest slope that measurably changed a real route -- seed 2098492050's best
# path went 25 -> 23 cards and lost its MOLTEN_FIST duplicate. 2.0 was also
# measured (22 cards) and tightens more than the player asked for.
TAKE_PENALTY_SLOPE = 1.5
TAKE_PENALTY_STEPS = (
    (1.0, 2.0, 14, 5.0, 15),
    (4.0, 5.0, 17, 5.0, 17),
    (4.0, 5.0, 18, 5.0, 18),
)
TAKE_BASE_LEGACY = 1.0   # act-1 low step: penalty when the starting deck is unknown
# Same-card repeat penalty (player brief of 2026-10-04, scaled 2026-10-07):
# the (N+1)-th copy of a card costs N * REPEAT_SCALE points, and a Power's
# copies from the second on cost at least 2 -- the two rules take the HEAVIER,
# never the sum. PERFECTED_STRIKE is exempt from the repeat penalty outright
# (it grows with the Strikes the deck holds).
REPEAT_EXEMPT = frozenset({'PERFECTED_STRIKE'})
# Player brief 2026-10-07: 「略微增加所有卡牌的同名惩罚」, raised again
# 2026-10-09: 「所有卡牌的重复抓取惩罚增加」. The linear charge of the (N+1)-th
# copy is scaled by this factor (1 -> 1.25 ... ) before the Power floor and the
# EXTRA_REPEAT table take the heavier of the two, so a Power's second copy still
# pays POWER_REPEAT_MIN and only its later copies feel the raise. 1.25 was the
# original 'slightest step that moves a duplicate take'; on seed 2098492050 it
# moved nothing at all, and neither did 2.0 or 3.0 -- a duplicate take here nets
# 40+ points, so the charge has to be near 3.5 before a copy is actually
# refused. Measured on that seed's 12 paths:
#   1.25 / 2.0 / 2.5 / 3.0 -> deck 23 cards, MOLTEN_FIST x3, SECOND_WIND x2
#   3.5 / 4.0               -> deck 22 cards, MOLTEN_FIST x2, SECOND_WIND x2
# 3.5 is the first scale at which a duplicate is actually dropped, and 4.0 was
# also measured (73.32 vs 73.82) for no extra change, so 3.5 is the setting
# that does the job without overshooting.
REPEAT_SCALE = 3.5
POWER_REPEAT_MIN = 4.0    # player delta 2026-10-05: was 2, raised so the
                          # second copy of a listed Power is a hard sell
# Player brief 2026-10-06: '战斗专注享受额外重复惩罚'. Added on top of the
# linear charge -- a second Battle Trance eats a draw AND blocks every other
# draw that turn, so it is a worse second copy than an ordinary card's.
# ARMAMENTS (same brief): one copy is a full-run engine and a second upgrades
# nothing the first cannot; the +8 charge pushes the second copy past its own
# take value, so the plan holds at most one.
# SECOND_WIND added 2026-10-09: 「重振精神额外增加」. Its CONDITION_POOL entry
# (deck_value.py:220) pays 18 defense points against the SKILL_IN_HAND supply,
# and supply counts every non-attack card, so each extra copy buys its own
# fuel -- the card still cleared the need at three copies with take_dims 35.4.
# On seed 2098492050 the extra charge has to exceed 26 before the second copy
# is refused (26 -> still 2 copies, 28+ -> 1 copy); 30.0 is used as the value
# past that threshold rather than the 26.0 boundary itself.
EXTRA_REPEAT = {'BATTLE_TRANCE': 6.0, 'ARMAMENTS': 8.0, 'SECOND_WIND': 30.0}
# Card taking is a beam-search dynamic program, not a greedy walk (player brief
# of 2026-10-04): every reward menu and every shop card is a branch (take it or
# skip it), so skipping a mediocre card now to take a better one later, and the
# deck-size cost each take imposes on every later take, are both expressible.
# Each decision point keeps the DP_BEAM best states by formation; branches that
# reached the same deck, removals and life total collapse to the best of them.
# The beam is a width bound, never a claim of global optimality.
DP_BEAM = 512
# Starter Strike/Defend removal (player brief: delete the initial Strikes and
# Defends): the first removal is worth a lot, each further one less; a deck
# with no starters left barely values removal.
REMOVAL_FIRST = 4.0
REMOVAL_STEP = 0.5
REMOVAL_FLOOR = 1.5
REMOVAL_OTHER = 0.75
SHOP_RELIC_VALUE = 2.0
SHOP_CARD_SCALE = 1.0
# Elite strength gate (player brief of 2026-10-04): an elite is only worth
# fighting when the deck AT ENTRY is strong enough, judged on the CURRENT act's
# card tiers. Named act-1 elites carry their own demand; everything else falls
# back to the act's value. Only acts 1-2 pay ELITE_VALUE at all (act 3 buys
# survival), so the act-3 number is recorded but inert by design.
# Thresholds on the plan's A-dimension scale (1 cost = 10): the old 28/36/38
# and 30/36/45 were set on the ~0-10 per-card scale; the ratios to the act-1
# baseline are preserved against the new, roughly doubled card values.
ELITE_MIN_PREFIX = (
    ('TERROR_EEL', 33),          # 骇鳗
    ('PHANTASMAL_GARDEN', 42),   # 花园幽灵鳗
    ('SKULKING_COLONY', 44),     # 珊瑚群
)
ELITE_MIN_BY_ACT = {0: 35, 1: 42, 2: 52}
# ACT-1 ELITE STRICTNESS (player instruction 2026-10-09: the first act should not
# seek out an elite unless the deck is very strong). The act-1 bar was 35 on the
# plan's A-dimension scale (1 cost = 10 points). Measured on seed 2098492050's
# route plan, the best route finished act 1 at A=47.0 with deck_size 21 -- well
# OVER 35 -- so the gate credited it and the DP walked it in. But the strict-mode
# end-to-end run of the same seed died in act 0 (all five strict routes died on
# floors 6-7) with insufficient_block on 315 of 337 failures. The bar was too low
# for what an act-1 elite asks of a 21-card deck. 47.0 is the act-1 A that the
# best route actually reached on that seed, so a deck below it has NOT shown it
# can take an elite. The act-2/act-3 bars are unchanged: the player scoped this
# instruction to the FIRST act only.
ACT1_ELITE_MIN = 47.0
# BOSS_MIN_BY_ACT (player ruling 2026-10-08: 「每一幕的最终 boss 要求都只是能过
# 就行」). The boss is a PASS/FAIL gate, not a scoring term -- BOSS_DANGER is 0
# and the act-3 multiplier is gone -- so the deck still has to be asked whether
# it can clear the climax at all, otherwise the planner would happily route a
# starter deck into a boss for free.
#
# Same A-dimension scale as ELITE_MIN_BY_ACT (deck_score, 1 cost = 10). MEASURED
# on the 33 saved A10 IRONCLAD runs, lowest A at an act boss among the 16 that
# WON: act 0 -1.5, act 1 3.1, act 2 13.0. The runs that died AT a boss held
# -15.0, -19.0, -19.0 -- below every winning run. The thresholds are set just
# under the winning minima so no route that actually cleared an act is ever
# blocked, while the decks that demonstrably died there are cut. Deck strength
# alone does not separate every loss (one loss met act-0 at A=0.0 and still
# died), which is expected: this is a plausibility floor, not a predictor, and
# the hp projection stays in charge of the rest.
BOSS_MIN_BY_ACT = {0: -5.0, 1: 0.0, 2: 10.0}
# First-three-battles card requirement (player brief of 2026-10-04): within the
# first three battles at least one NON-POWER card must be taken. Event card
# acquisition (a select_cards "obtain:" node) counts; Power cards never do.
EARLY_BATTLES = 3
# Player delta 2026-10-05: before the ACT-1 BOSS at least FOUR cards and
# before an ACT-1 ELITE at least THREE must be taken, Powers INCLUDED. Both
# are HARD constraints, and among the compliant states only the one with the
# HIGHEST formation AT the checkpoint continues (checkpoint-optimal: the
# deck at the elite / boss is as strong as the menus allowed, even when a
# globally richer branch exists).
EARLY_BOSS_MIN_TAKES = 4
EARLY_ELITE_MIN_TAKES = 3
# MIN_TAKE_VALUE (player instruction 2026-10-09: 「收益小于1不拿」): the take /
# skip threshold. See _apply_take for why 0 was not good enough.
MIN_TAKE_VALUE = 1.0
_BATTLE_ROOMS = ('Monster', 'Elite', 'Boss')
# ACT-1 ELITE PLACEMENT (player instruction 2026-10-09: if an elite route is
# unavoidable, take the one whose elite comes LATER in the act). An act-1 elite
# before the deck is built is the exact failure the strict-mode run showed (all
# five strict routes died on floors 6-7, i.e. right at the act-1 elite / boss
# gate), so when a route must fight one it should be fought as late as possible,
# with as many reward menus banked behind it.
#
# The charge is a PREFERENCE, not a hard gate: it must never make an elite route
# infeasible, only rank a later elite above an earlier one of equal merit. It is
# applied as negative danger so it is subtracted exactly like every other danger
# term in `total = formation - danger`.
#
# Measured on seed 2098492050: act-1 elites land on floors 8, 11 and 14 and the
# act-1 boss on floor 16, so a fixed "late floor" cutoff cannot rank them. The
# charge is therefore the elite's position WITHIN its act, measured against that
# act's own boss floor, which is read from the route rather than assumed:
#   floor 8  -> (16-8)/15 = 0.53 of the full charge
#   floor 11 -> 0.33
#   floor 14 -> 0.13
# A missing floor on either side charges nothing -- an absent field is not a
# reason to invent a penalty.
ACT1_ELITE_LATE_BONUS = 18.0  # full charge for an act-1 elite on the first floor

# Formation-side danger, rescaled to the plan's card values (was 6/2/3 on the
# ~0-10 scale): a route must weigh fight pain against the same points its
# cards are worth.
ELITE_DANGER = 12.0
MONSTER_DANGER = 4.0
UNKNOWN_DANGER = 6.0
# Boss had NO key in this table, so the hardest fight in the run fell through
# to MONSTER_DANGER: a route could end an act on the boss for less danger than
# a single trash room, which is a mechanism defect, not a tuning choice.
#
# REVISED 2026-10-08 (player ruling: 「每一幕的最终 boss 要求都只是能过就行」).
# BOSS_DANGER is now ZERO. The boss is no longer a term in the danger sum and
# therefore no longer ranks routes against each other: reaching an act's boss
# is a PASS/FAIL question, not "how well prepared am I" -- and the old number
# was double-counting the same worry as the hard gate below (BOSS_MIN_BY_ACT)
# AND as the hp projection (FIGHT_HP_COST['Boss']). The key is kept rather than
# deleted so _ROOM_DANGER stays a complete room table and the zero is visible
# instead of a silent .get() fall-through.
BOSS_DANGER = 0.0
# The act-3 danger multiplier is ALSO gone. It tripled EVERY battle's danger
# in act 3 on the theory that the final act must be survived at a higher cost,
# and the 33 saved A10 IRONCLAD runs say the opposite: among the winning runs
# the act-3 ELITE cost LESS than the act-1 elite (median 12.0 vs 15.5 gross hp)
# and the act-3 BOSS cost less than the act-1 boss (21.0 vs 28.0). A 3x charge
# was therefore not a safety margin, it was a straight bias against every
# act-3 route. It existed only to make bosses matter more; with the boss gone
# from the sum its rationale is gone with it.
ACT3_DANGER_SCALE = 1.0
_ROOM_DANGER = {'Elite': ELITE_DANGER, 'Monster': MONSTER_DANGER, 'Unknown': UNKNOWN_DANGER,
                'Boss': BOSS_DANGER}

# HP simulation (when the walk facts carry the starting hp/max_hp): every
# fight costs projected life, a rest site either heals or smiths — whichever
# is worth more — and a route that projects below the low-hp line is charged.
# MEASURED 2026-10-08 on 33 saved A10 IRONCLAD runs: the GROSS damage taken at
# a node that dealt any, per room type -- Monster 8.0 (n=298), Elite 16.0
# (n=87), Boss 26.5 (n=80). Boss had NO key here and fell through to
# MONSTER_DANGER, so the hp simulation charged an act climax less than an
# ordinary trash fight.
#
# RE-MEASURED 2026-10-08 on the 16 runs that WON, where the hp simulation is
# only ever measured along a surviving path: gross medians Monster 8.0
# (n=162, mean 9.4), Elite 15.0 (n=68, mean 17.7), Boss 23.0 (n=60, mean
# 25.3). These are GROSS (damage_taken), the quantity the hp line actually
# loses -- NOT the net hp change. The difference is BURNING_BLOOD_HEAL below,
# and reading net medians as the cost while omitting the heal would count the
# same 6 points twice: it would charge 26.5 for a boss while also pretending
# the run ends a fight 6 points lighter than it started.
#
# These are medians, not fits: the spread is wide (boss p90 44, max 92), so
# this fixes the missing mechanism and the level, not the variance.
BOSS_HP_COST = 23.0
FIGHT_HP_COST = {'Elite': 15.0, 'Monster': 8.0, 'Unknown': 9.0, 'Boss': BOSS_HP_COST}
# The Ironclad's starting relic heals 6 at the end of every combat (player
# correction 2026-10-08: 燃烧之血). The hp line charged the gross wound and
# never paid the heal back, so every route was projected ~6 points poorer per
# battle than it is measured to be -- 33 fights is ~200 hp, more than two
# acts of slack, and the low-hp penalty then fired on routes that survive.
# MEASURED: hp_healed is exactly 6 at 195 of the 290 wounded combat nodes of
# the 16 winning runs (67%); the other 33% sit at 7-24 from other relics and
# potions, which this line does not model. The heal is applied only when the
# facts name an Ironclad run (see HP_HEAL_CHARACTER) so a class without the
# relic is not credited with it.
BURNING_BLOOD_HEAL = 6.0
HP_HEAL_CHARACTER = 'IRONCLAD'
# The act-3 hp multiplier is gone for the same reason ACT3_DANGER_SCALE is.
# It charged act-3 fights 1.5x on the assumption that the final act is where
# runs die, and the saved runs contradict it: among the winning A10 IRONCLAD runs
# the act-3 ELITE cost LESS than act 1 (median 12.0 vs 15.5 gross hp) and the
# act-3 BOSS cost less than act 1 (21.0 vs 28.0). A 1.5x charge pushed the hp
# line toward LOW_HP_FRACTION on routes that measurably clear act 3 comfortably.
ACT3_HP_SCALE = 1.0
REST_HEAL_FRACTION = 0.3      # a Rest heals this share of max hp
REST_HEAL_WEIGHT = 20.0       # points of a full-missing-hp heal (plan scale)
REST_SMITH_PER_CARD = 1.2     # per taken (upgradeable) card: ~half a card
REST_SMITH_CAP = 6.0
# Player brief 2026-10-07: the Armaments smith bonus (2026-10-06) is REMOVED
# -- no linkage bonuses for Armaments; its value lives in the V_TABLE entry
# and the extra repeat charge alone.
LOW_HP_FRACTION = 0.25
LOW_HP_PENALTY = 20.0
# Shop gold model (player brief 2026-10-07). The route DP had NO gold term at
# all: every shop card was a free independent purchase and every removal was
# taken unconditionally, so a plan could buy 360 gold of shelf against the 137
# the run actually held (seed 524130501, act 1 floor 6). Gold is now a
# path-level budget -- the walk facts measure what the run holds, a purchase
# that does not fit is skipped instead of planned, and the leftover is worth
# nothing (an unspent purse scores no points, so the DP spends what it can).
#
# Per-room income, MEASURED on the 60 reconnaissance walks of seed 524130501
# (1744 credited gains): Monster 7-14 (mean 10.6), Elite 27-33 (mean 30.0),
# Treasure 33-37 (mean 34.8), Boss 75 flat, Event 10-30 (mean 21.2). These are
# a FALLBACK ONLY: when the facts carry a gold_trace entry for a shop at its
# (act, floor) the observed value overrides the accumulated total outright, so
# the table only decides shops the walks never reached.
ROOM_GOLD = {'Monster': 10.0, 'Elite': 30.0, 'Treasure': 34.0, 'Boss': 75.0,
             'Unknown': 10.0, 'Event': 20.0}
# Strict prescription (player brief of 2026-10-05): the host's labelled menus
# decide lexicographically by (tier, native score), so a label tier of ±8
# forces the choice without touching legality. The strict tiers name, per act,
# what this route's DP decided: card takes (card: / obtain:), shop removals
# (buy_removal) and the route's event options get +8; menu cards the plan
# skipped in that act get -8 so MarginalCard cannot overrule the plan. Rest
# sites are deliberately NOT forced (the host keeps its own rest policy).
STRICT_TIER = 8

# Category scoring (player brief: score attack / defense / engine separately):
# a card's worth moves with what the deck LACKS. The targets are shares of the
# current deck; the adjustment is half the shortfall, clamped to ±BONUS.
CATEGORY_TARGETS = {'attack': 0.40, 'defense': 0.35, 'engine': 0.25}
CATEGORY_BONUS = 1.5


def category_adjust(counts: Counter, size: int, card_id: str) -> float:
    want = CATEGORY_TARGETS[get_category(card_id)] * max(1, size)
    deficit = want - counts[get_category(card_id)]
    return max(-CATEGORY_BONUS, min(CATEGORY_BONUS, 0.5 * deficit))


def _is_starter(card_id: str) -> bool:
    return card_id.split('_', 1)[0] in ('STRIKE', 'DEFEND')


def _should_keep_strikes(deck) -> bool:
    """D4 (player brief of 2026-10-04): with HELLRAISER or PERFECTED_STRIKE in
    the deck, Strike cards are fuel, not dead draws -- removal skips them and
    takes a Defend instead (and removes nothing at all if only Strikes remain)."""
    return deck.get('PERFECTED_STRIKE', 0) > 0 or deck.get('HELLRAISER', 0) > 0


def take_penalty(size: int, act_index: int = 0) -> float:
    """Deck-size cost of taking one more card in this act (player brief)."""
    low, mid, mid_at, high, high_from = TAKE_PENALTY_STEPS[min(max(act_index, 0), 2)]
    if size < mid_at:
        return low
    if size == mid_at:
        return mid
    return high + (size - high_from) * TAKE_PENALTY_SLOPE




# --- 够用即停 (player definition of 2026-10-06) ----------------------------
# The player's strength ladder, strongest first:
#   1 = 无限循环        a CLOSED loop: exhaust payoff + enough exhaust sources
#                       + draw or energy to keep it going (the plan's E1 slots,
#                       SpireExact-A10方案 §3 / appendix B: EXH_PAY>=2,
#                       EXH_SRC>=3, DRAW or NRG>=2).
#   2 = 数值很强可以秒杀防住  the deck's OWN A and D already clear this act's
#                       requirement line: it kills and it blocks without setup.
#   3 = 启动之后小循环或足够数值  setup is present and EITHER a smaller loop
#                       closes OR one axis already clears the line.
#   0 = 未成型          none of the above.
# Once a tier is reached, every further card buys dilution rather than
# strength, so it pays an extra charge. This is a PER-CARD charge, not a cap
# on `formation`: capping the running total made states tie and let the beam
# keep them by enumeration order (measured 2026-10-06: the best route changed
# on 4 of 8 seeds), whereas a per-card charge leaves the DP its ordering.
# Tags are the plan's appendix B lists, mapped to ids by its appendix A table.
SATIATION_MU = 3.0
DRAW_TAGS = frozenset({
    'POMMEL_STRIKE', 'SHRUG_IT_OFF', 'OFFERING', 'BATTLE_TRANCE',
    'BURNING_PACT', 'DARK_EMBRACE', 'PILLAGE', 'DRUM_OF_BATTLE',
    'CASCADE', 'HEADBUTT',
})
NRG_TAGS = frozenset({
    'BLOODLETTING', 'OFFERING', 'PYRE', 'FORGOTTEN_RITUAL',
    'DRUM_OF_BATTLE',
})
EXH_PAY_TAGS = frozenset({
    'DARK_EMBRACE', 'FEEL_NO_PAIN', 'ASHEN_STRIKE', 'PACTS_END',
    'EVIL_EYE', 'THRASH',
})
# The exhaust-source sets live in deck_value (EXH_SRC_MASS / EXH_SRC_SINGLE)
# so the tier ladder and the per-card sigma_eff can never disagree about which
# cards are sources or how much each one counts. Read through
# deck_value.exhaust_weight below.


def _tag_count(deck, tags) -> int:
    return sum(n for card, n in deck.items() if card in tags)


def strength_tier(deck, act_index: int = 0) -> int:
    """The player's strength ladder for the deck AS IT STANDS (2026-10-06).

    1 = 无限循环, 2 = 数值很强可以秒杀防住, 3 = 启动之后小循环或足够数值,
    0 = 未成型. A pure read of the deck; never a bound and never a proof.
    """
    exh_pay = _tag_count(deck, EXH_PAY_TAGS)
    # Weighted: a whole-hand source counts 1.0, a one-card source 0.35, so
    # "EXH_SRC >= 3" means three whole-hand sources or about nine one-card ones.
    exh_src = sum(n * exhaust_weight(card) for card, n in deck.items())
    draw = _tag_count(deck, DRAW_TAGS)
    nrg = _tag_count(deck, NRG_TAGS)
    dims = deck_dims(deck)
    req = REQ_PER_ACT[min(max(act_index, 0), 2)]
    if exh_pay >= 2 and exh_src >= 3 and (draw >= 2 or nrg >= 2):
        return 1                       # the loop is closed
    if dims[0] >= req[0] and dims[1] >= req[1]:
        return 2                       # kills and blocks on its own numbers
    if ((exh_pay >= 1 and exh_src >= 2 and (draw >= 1 or nrg >= 1))
            or dims[0] >= req[0] or dims[1] >= req[1]):
        return 3                       # setup lands, then loop or numbers
    return 0


def satiation_penalty(deck, act_index: int = 0) -> float:
    """够用即停: once a tier is reached, one more card is dilution, not
    strength. Charged per card (strongest tier pays most), so the DP still
    ranks states normally and only stops taking cards it no longer needs."""
    tier = strength_tier(deck, act_index)
    if tier == 0:
        return 0.0
    return SATIATION_MU * (4 - tier)   # tier 1 -> 9.0, 2 -> 6.0, 3 -> 3.0


def deck_score(deck, act_index: int) -> int:
    """Deck strength for the elite gate: the plan's A-dimension sum (1 cost =
    10 points). The starting deck sits BELOW zero (its basics carry negative
    dims), so the demands read as "an offense core worth about N points"."""
    return deck_dims(deck)[0]


def elite_min(enemies, act_index: int) -> int:
    """The deck score an elite demands (player brief of 2026-10-04).

    Matched by PREFIX on each '+'-separated enemy id: the walk facts and the
    native reports spell these ids differently (ENTOMANCER vs TERROR_EEL_ELITE,
    PHANTASMAL_GARDENERS), so an exact match would miss."""
    clamped = min(max(act_index, 0), 2)
    floor = ACT1_ELITE_MIN if clamped == 0 else None
    for part in (enemies or '').split('+'):
        for prefix, need in ELITE_MIN_PREFIX:
            if part.startswith(prefix):
                # A named elite carries its own demand, but in the FIRST act it
                # is still an act-1 elite: the player's 「十分强势」 floor applies
                # to it too. Taking the max keeps 骇鳗 (33) from being the cheap
                # way back into an act-1 elite fight.
                return max(need, floor) if floor is not None else need
    if floor is not None:
        return int(floor)
    return ELITE_MIN_BY_ACT[clamped]


def boss_min(act_index: int) -> float:
    """The deck score a boss demands on the given act (see BOSS_MIN_BY_ACT).

    Unlike elite_min there is nothing to match on: the player ruled that an act
    boss is the same 'can you clear it at all' question every act, so the act
    index only selects the bar."""
    return BOSS_MIN_BY_ACT[min(max(act_index, 0), 2)]


def repeat_penalty(card_id: str, copies: int) -> float:
    """Cost of the NEXT copy when the deck already holds `copies` of it.

    Linear in the copies held (the second copy costs REPEAT_SCALE, the third
    2*REPEAT_SCALE, ...), and a Power's copies from the second on cost at least
    POWER_REPEAT_MIN; the two rules take the heavier, not the sum.
    PERFECTED_STRIKE pays neither."""
    if card_id in REPEAT_EXEMPT or copies <= 0:
        return 0.0
    linear = float(copies) * REPEAT_SCALE
    if is_power(card_id):
        return max(linear, POWER_REPEAT_MIN)
    # Player brief 2026-10-06: '战斗专注享受额外重复惩罚'. A second Battle
    # Trance is worse than a second copy of an ordinary card -- it eats a draw
    # AND blocks every other draw that turn -- so it carries an extra charge.
    if card_id in EXTRA_REPEAT:
        return linear + EXTRA_REPEAT[card_id]
    return linear


class DeckSim:
    """Deck evolution along one route: takes, starter removals, and the
    per-act take log the host's label tiers are built from. Pure bookkeeping,
    no game rules."""

    def __init__(self, initial: dict):
        self.deck = Counter({k: int(v) for k, v in initial.items()})
        self.categories = Counter()
        for card_id, count in self.deck.items():
            self.categories[get_category(card_id)] += count
        # Per-category scores (player brief: 分数分为攻击分/防御分/运转分):
        # each card's net contribution lands in its own bucket; a removal's
        # value lands in the removed card's category.
        self.score = {'attack': 0.0, 'defense': 0.0, 'engine': 0.0}
        self.removed_starters = 0
        self.takes = []            # (act_index, card id) in path order

    def add_score(self, card_id: str, value: float):
        self.score[get_category(card_id)] += value

    def size(self) -> int:
        return sum(self.deck.values())

    def starters(self) -> int:
        return sum(count for card_id, count in self.deck.items() if _is_starter(card_id))

    def take(self, card_id: str, act_index: int):
        self.deck[card_id] += 1
        self.categories[get_category(card_id)] += 1
        self.takes.append((act_index, card_id))

    def remove_starter(self) -> str | None:
        """Remove one starter basic, returning its id (None if none left) —
        the caller books the removal value under the removed card's category.
        With HELLRAISER / PERFECTED_STRIKE held, Strikes are fuel (D4), so the
        Defends go first and a Strike-only deck removes nothing."""
        keep_strikes = _should_keep_strikes(self.deck)
        for card_id in sorted(self.deck, key=lambda c: (not c.startswith('STRIKE'), c)):
            # Strikes before Defends: the worse late-game draw goes first.
            if keep_strikes and card_id.split('_', 1)[0] == 'STRIKE':
                continue
            if _is_starter(card_id) and self.deck[card_id] > 0:
                self.deck[card_id] -= 1
                self.categories[get_category(card_id)] -= 1
                self.removed_starters += 1
                return card_id
        return None
        return False

    def removal_value(self) -> float:
        if self.starters() > 0:
            return max(REMOVAL_FLOOR, REMOVAL_FIRST - REMOVAL_STEP * self.removed_starters)
        return REMOVAL_OTHER


def _tier(card_id: str, act_index: int) -> int:
    # The player's hand-provided Ironclad tiers (ironclad_tiers.py, 2026-10-04)
    # replace the community table for Ironclad cards outright: listed cards
    # score 10/8/6, and Ironclad cards the player did NOT list are capped at 5
    # — strictly below T1, per the brief. Other characters keep the community
    # tiers untouched.
    if card_id in OVERRIDES:
        return OVERRIDES[card_id]
    row = PICK_TIERS.get('card:' + card_id)
    tier = row[min(act_index, len(row) - 1)] if row else 0
    if card_id in IRONCLAD_CARDS:
        tier = min(tier, UNLISTED_CAP)
    return tier


def enumerate_paths(facts: dict) -> list[list[dict]]:
    """Every complete route of the option tree: [node dict] from the root's
    first child to a leaf, skipping incomplete walks (died well before the
    last observed floor)."""
    tree = facts['tree']
    max_floor = facts.get('max_floor') or 0
    paths = []

    def visit(node_id: int, path: list[dict]):
        node = tree[node_id]
        if not node['children']:
            if (node['floor'] or 0) >= max_floor - 1:
                paths.append(path + [node])
            return
        for child_id in node['children'].values():
            visit(child_id, path + [node])

    for child_id in tree[0]['children'].values():
        visit(child_id, [])
    return paths


def _deck_size(deck) -> int:
    return sum(deck.values())


def _starters(deck) -> int:
    return sum(count for card_id, count in deck.items() if _is_starter(card_id))


def _categories(deck) -> Counter:
    counts = Counter()
    for card_id, count in deck.items():
        if count:
            counts[get_category(card_id)] += count
    return counts


def _removal_value(deck, removed: int) -> float:
    if _starters(deck) > 0:
        return max(REMOVAL_FLOOR, REMOVAL_FIRST - REMOVAL_STEP * removed)
    return REMOVAL_OTHER


def _remove_target(deck) -> str | None:
    """The removal target (second-edition plan 5.4 + player brief): Strikes,
    then Defends, then Bash. ASCENDERS_BANE is Eternal (cannot be removed or
    transformed) and never a target. With HELLRAISER / PERFECTED_STRIKE held
    the Strikes are fuel (D4) and are skipped, so the Defends go first."""
    keep_strikes = _should_keep_strikes(deck)
    for card_id in sorted(deck):
        if not keep_strikes and card_id.startswith('STRIKE') and deck[card_id] > 0:
            return card_id
    for card_id in sorted(deck):
        if card_id.startswith('DEFEND') and deck[card_id] > 0:
            return card_id
    if deck.get('BASH', 0) > 0:
        return 'BASH'
    return None


def _kept_shops(path: list[dict]) -> set:
    """The shop nodes to expand, deduped to ONE per (act, floor).
    The walk facts record a shop once per purchase screen the walker saw, and
    a second screen is the shelf LEFT OVER after the walker itself bought --
    seed 524130501 stores act 0 floor 6 as two CHAINED buy nodes, the second
    holding 4 of the original 10 labels because the walk spent 73 + 50 gold
    between them (137 -> 64 -> 14). Expanding both would (a) offer the same
    shelf twice, letting the DP buy one card from one shop through two
    independent branches, and (b) make the plan view of a shop depend on
    what reconnaissance happened to spend. One shop is one shelf: keep the
    FIRST node, which is the full one, and drop the rest."""
    seen = set()
    keep = set()
    for node in path:
        if node['kind'] != 'buy':
            continue
        act_index = node['act'] if node['act'] is not None else 0
        key = (act_index, node.get('floor'))
        if key in seen:
            continue
        seen.add(key)
        keep.add(id(node))
    return keep


def _shop_prices(node: dict) -> dict:
    """label -> gold cost for one shop node, read from the node own menu.
    The facts keep a shop price list in `menu` (one `kind == buy` entry per
    purchasable item, each carrying its `cost`) while `labels` lists the same
    items in the same order -- measured 120/120 aligned on the walks of seed
    524130501, card/potion/removal types included. When the two disagree in
    length (older facts, or a menu the merge only partly kept) every price
    degrades to absent and the caller treats the shop as unpriced, which is
    the historical free-shelf behaviour -- never an invented price."""
    labels = list(node.get('labels') or [])
    buys = [action for action in (node.get('menu') or []) if action.get('kind') == 'buy']
    if len(labels) != len(buys):
        return {}
    return {label: action.get('cost') for label, action in zip(labels, buys)}

def _route_steps(path: list[dict]) -> list[tuple]:
    """Flatten a route into the ordered steps the DP consumes: the
    deterministic rooms, starter removals and shop relics, plus the card
    decision points. One reward menu is ONE decision among its cards (take at
    most one); one shop card is one independent purchase (buy any subset).
    Label order inside a shop is preserved, so a removal still cheapens the
    cards the same shop offers after it, exactly as the greedy walk did.

    Shop steps: ('removal', act, price, floor) and ('shopcard', card, act,
    the card). Buy
    relic is ('relic', id, act, None, None): free. price is None when the facts
    held none. A relic step is ('relic', id, act, price, floor) and a treasure
    nodes are deduped per (act, floor) -- see _kept_shops."""
    steps = []
    kept_shops = _kept_shops(path)
    for node in path:
        act_index = node['act'] if node['act'] is not None else 0
        labels = node['labels'] or []
        kind = node['kind']
        if kind == 'buy' and id(node) not in kept_shops:
            continue                      # a leftover shelf of an already-seen shop
        if kind == 'map':
            leads = node.get('leads_to')
            if leads:
                # The node floor rides along as step[4]: the act-1 elite
                # preference below needs to know WHERE in the act the fight
                # lands, not just which act it is.
                steps.append(('room', leads.get('room'), act_index,
                              leads.get('enemies'), node.get('floor')))
        elif kind == 'card_reward':
            cards = tuple(label[5:] for label in labels if label.startswith('card:'))
            if cards:
                steps.append(('reward', cards, act_index))
        elif kind == 'buy':
            prices = _shop_prices(node)
            floor = node.get('floor')
            for label in labels:
                price = prices.get(label)
                if label == 'buy_removal':
                    steps.append(('removal', act_index, price, floor))
                elif label.startswith('card:'):
                    steps.append(('shopcard', label[5:], act_index, price, floor))
                elif label.startswith('relic:'):
                    steps.append(('relic', label[6:], act_index, price, floor))
        elif kind == 'select_cards':
            # Event / card-select acquisition (player brief: 事件抓牌也算): the
            # same take-one decision as a battle reward, so it feeds the
            # first-three-battles requirement. Only "obtain:" labels are cards;
            # upgrade:/remove: labels are not takes.
            cards = tuple(label[7:] for label in labels if label.startswith('obtain:'))
            if cards:
                steps.append(('reward', cards, act_index))
        elif kind == 'treasure':
            # Treasure relics used to be scored nowhere at all, although the
            # walk facts record them as often as the shops do (seed 2098492050:
            # 7 treasure vs 16 buy). Skipping the chest is the treasure_skip label.
            for label in labels:
                if label.startswith('relic:'):
                    steps.append(('relic', label[6:], act_index, None, None))
    return steps


def _state_key(state: tuple) -> tuple:
    """DP identity: the deck, the removals, the take count, the life total
    and the shop gold already spent. Two branches that agree on all of these
    have identical futures, so only the better-formation one is kept. Full
    bytes, no digest or approximation.

    The spent gold is part of the identity because a PRICED relic purchase
    records itself nowhere else -- not in the deck, the removals, the take log
    or the choice log -- so without it a branch that bought a shelf relic and
    a branch that skipped it would collapse into one state and _keep would
    silently drop whichever had the smaller purse left. It is a small integer
    here (one purse per shop), not a bucketed dimension: measured on seed
    524130501 it moves no total and raises no peak."""
    deck = tuple(sorted((card, count) for card, count in state['deck'].items() if count))
    hp = None if state['hp'] is None else round(state['hp'], 6)
    return (deck, state['removed'], len(state['takes']), hp, state['early_ok'],
            tuple(state.get('choices') or ()), round(state.get('gold_spent', 0.0), 6))


def _branch(state: dict) -> dict:
    return {'deck': Counter(state['deck']), 'removed': state['removed'],
            'takes': state['takes'], 'choices': state.get('choices', ()), 'hp': state['hp'],
            'formation': state['formation'], 'score': dict(state['score']),
            'synergy': dict(state['synergy']), 'relic_synergy': state['relic_synergy'],
            'archetype_bonus': dict(state.get('archetype_bonus') or {}),
            'early_ok': state['early_ok'],
            'early_takes': state.get('early_takes', 0),
            'boss_checked': state.get('boss_checked', False),
            'elites_credited': state['elites_credited'],
            'elites_blocked': state['elites_blocked'],
            'bosses_cleared': state.get('bosses_cleared', 0),
            'bosses_blocked': state.get('bosses_blocked', 0),
            'gold_spent': state.get('gold_spent', 0.0),
            'gold_budget': state.get('gold_budget'),
            'gold_budget_shop': state.get('gold_budget_shop'),
            'removal_acts': state.get('removal_acts', ())}


def _keep(table: dict, state: dict):
    key = _state_key(state)
    known = table.get(key)
    if known is None or state['formation'] > known['formation']:
        table[key] = state


# CHECKPOINT_KEEP (player instruction 2026-10-09: the act-1 elite / boss checkpoint
# gates used to collapse every compliant state to the single highest-formation
# one, discarding whole lines of play on a tie-break, at exactly the point where
# the remaining menu choices have to stay expressible). Three are kept instead:
# the best by formation, then the next two. This is a WIDTH bound on the
# checkpoint, never an optimality claim -- the beam (DP_BEAM) still does the real
# pruning, and the kept states are re-keyed by the same _state_key the DP uses,
# so genuinely identical states still collapse.
CHECKPOINT_KEEP = 3


def _checkpoint_keeps(survivors: list) -> dict:
    """The compliant states that continue past a checkpoint gate, keyed by
    _state_key. See CHECKPOINT_KEEP."""
    ranked = sorted(survivors, key=lambda st: -st['formation'])
    return {_state_key(state): state for state in ranked[:CHECKPOINT_KEEP]}


def archetype_take_bonus(card: str, base_value: float, archetype: str | None) -> float:
    """Archetype pull on ONE take decision, expressed in the same deck-value
    points as base_value (the raw take_dims of the card, before the repeat /
    weak / size / satiation penalties).

    Returns ARCHETYPE_BIAS_SCALE * archetype_bias(card, archetype) *
    abs(base_value), and exactly 0.0 whenever archetype is None -- which is
    what every caller that does not ask for a specific archetype passes, so
    the historical single-objective plan still scores identically.

    abs() is deliberate rather than cosmetic. A signed multiplier would turn
    the -0.35 pull away from another archetype's core into a REWARD whenever
    that card's base value happened to be negative (0.65 * -4 = -2.6, i.e.
    less bad), so the bias would have helped the plan take exactly the cards
    it is supposed to push away from. Tilted magnitude is monotone in the
    intended direction for both signs."""
    bias = archetype_bias(card, archetype)
    if not bias:
        return 0.0
    return ARCHETYPE_BIAS_SCALE * bias * abs(base_value)

def _apply_take(state: dict, card: str, act_index: int, scale: float, track: bool,
                in_early_window: bool = False,
                archetype: str | None = None) -> float | None:
    """Take one copy of card when its net value beats the CURRENT deck
    penalty, mutating the state. Returns the value, or None when the take is
    not worth it (宁缺毋滥: skip stays free and is never worse here — a taken
    card only grows the deck and can never pay that back later)."""
    if not track:
        value = _tier(card, act_index) * scale - TAKE_BASE_LEGACY
        if value <= 0:
            return None
    else:
        deck = state['deck']
        size = _deck_size(deck)
        # Three-dimensional marginal value (player plan of 2026-10-05): the
        # card's (A, D, U) weighted by the deck's CURRENT shortfall per
        # dimension, so the same card is worth 3x more into a short deck. The
        # synergy bonus rides on the card's dominant-dimension weight. The
        # category_adjust term is superseded by the per-dimension weights.
        bonus = synergy(card, deck)
        value = take_dims(card, deck, act_index, synergy_bonus=bonus)
        if value is None:            # single-player-unusable card
            return None
        if scale != 1.0:
            value *= scale
        # Deck-size treatment of draw cards (player brief 2026-10-06), three
        # tiers: 优质过牌 pays NO size penalty; other 过牌卡 pay a REDUCED one;
        # every other card pays the full charge. `size_term` is ADDED below,
        # so a charge is negative and a zero term is neutral.
        if card in PREMIUM_DRAW or card in SIZE_EXEMPT:
            # 优质过牌不受到规模惩罚; and energy+draw / 0-cost draw are exempt
            # 'in any case'. Neither charged nor bonused.
            size_term = 0.0
        elif card in DRAW_CARDS:
            # 过牌卡享受较低规模惩罚.
            size_term = -DRAW_PENALTY_SCALE * take_penalty(size, act_index)
        else:
            size_term = -take_penalty(size, act_index)
        # Player ruling D1 of 2026-10-07: the dilution charge is judged on the
        # deck the card is about to JOIN, V(c | D 并 {c}) -- plan :434 -- not on
        # the deck-free V_full. deck_with_card is only for the charge; the real
        # increment stays at the single line below.
        deck_with_card = dict(deck)
        deck_with_card[card] = deck_with_card.get(card, 0) + 1
        # Captured BEFORE the penalties below: the archetype pull scales with how
        # much the base model cares about the card (its raw take_dims), not with
        # how much of that survives the repeat / weak / size charges. Reading
        # `value` inline in the expression below would be correct only by
        # accident of evaluation order.
        archetype_bonus = archetype_take_bonus(card, value, archetype)
        # Ledger, like `synergy` below: keyed by card so a reader can see WHICH
        # takes the pull moved, not just how much it moved in total.
        state.setdefault('archetype_bonus', {})[card] = (
            state.get('archetype_bonus', {}).get(card, 0.0) + archetype_bonus)
        value = (value
                 - repeat_penalty(card, deck.get(card, 0))
                 - weak_penalty(card, deck_with_card)
                 + size_term
                 - satiation_penalty(deck, act_index)
                 + archetype_bonus)
        # MIN_TAKE_VALUE (player instruction 2026-10-09: 「收益小于1不拿」). The
        # threshold used to be 0, which admitted every take that merely cleared
        # it -- and the beam then ranked those states against real ones on
        # floating-point noise alone. Measured on seed 2098492050's winning
        # route, three takes sat inside that noise band:
        #   THUNDERCLAP 0.033 / INFERNAL_BLADE 0.104 / PILLAGE 0.748
        # while the next cheapest (TREMBLE 1.841) is nearly twenty times the
        # THUNDERCLAP margin. A take must therefore be worth MIN_TAKE_VALUE, not
        # merely non-negative. MIN_TAKE_VALUE is a PRUNING floor on an
        # allocation signal, never a rule about the game: it cannot make a legal
        # play illegal, only stop the planner from spending a deck slot on it.
        if value < MIN_TAKE_VALUE:
            return None
        deck[card] = deck.get(card, 0) + 1
        if bonus:
            state['synergy'][card] = state['synergy'].get(card, 0.0) + bonus
    state['takes'] = state['takes'] + ((act_index, card),)
    state['early_takes'] += 1          # player correction 2026-10-05: Powers count
    state['formation'] += value
    state['score'][get_category(card)] += value
    # The first-three-battles requirement: a NON-POWER take inside the window
    # satisfies it; a Power never does (player brief of 2026-10-04).
    if in_early_window and not is_power(card):
        state['early_ok'] = True
    return value


def score_path(path: list[dict], initial_deck: dict | None = None, run_state: dict | None = None,
               beam: int = DP_BEAM, archetype: str | None = None) -> dict:
    """Value of one route with its breakdown, simulating the deck and (when
    the walk facts carry the starting hp) the life total along the path.

    Card taking is a BEAM-SEARCH DYNAMIC PROGRAM over the route's decision
    points (player brief of 2026-10-04), not a greedy walk: every reward menu
    and every shop card branches into take / skip, so skipping a mediocre card
    now in order to take a better one later is expressible, and the deck-size
    cost each take imposes on every later take is paid in the same objective.
    Each step keeps the `beam` best states by formation and collapses states
    that agree on deck, removals, takes and life. The beam is a WIDTH BOUND,
    never a claim of global optimality; pruned counts are reported.
    Shared costs (the fixed bosses, the act transitions) are excluded: every
    route pays them."""
    steps = _route_steps(path)
    has_hp = bool(run_state and run_state.get('max_hp'))
    max_hp = float(run_state['max_hp']) if has_hp else None
    hp0 = min(max_hp, float(run_state.get('hp') or max_hp)) if has_hp else None
    # Post-combat healing is a property of the character, not of the route, so
    # it is resolved once from the walk facts. A run whose facts do not name a
    # character keeps the historical behaviour: every fight costs its gross
    # wound and nothing pays it back.
    heal_after_fight = 0.0
    if has_hp:
        character = str((run_state or {}).get('character') or '').rsplit('.', 1)[-1]
        if character == HP_HEAL_CHARACTER:
            heal_after_fight = BURNING_BLOOD_HEAL
    track = initial_deck is not None

    # Shop gold (player brief 2026-10-07): the purse at every step is a
    # function of the PATH alone -- the starting gold plus the measured income
    # of every room already passed -- so it is precomputed here and never
    # enters the DP state. A shop the walks actually reached uses its OBSERVED
    # purse from gold_trace instead, which is exact where the table is not
    # (seed 524130501: act 0 floor 6, 137 gold, identical on 60/60 walks).
    gold_on = bool(run_state and (run_state.get('gold') is not None
                                  or (run_state.get('gold_trace') or {})))
    try:
        purse = float((run_state or {}).get('gold') or 0.0)
    except (TypeError, ValueError):
        purse = 0.0
    gold_start = purse
    gold_trace = (run_state or {}).get('gold_trace') or {}
    purses = []
    for step in steps:
        if step[0] == 'room' and step[1] not in ('RestSite', 'Shop'):
            purse += ROOM_GOLD.get(step[1], ROOM_GOLD['Unknown'])
        purses.append(purse)

    def shop_budget(act, floor, fallback):
        """The purse a shop at (act, floor) sees: the OBSERVED minimum from
        the walks when they reached it, otherwise the accumulated fallback."""
        if floor is not None:
            entry = gold_trace.get('a%df%s' % (act if act is not None else 0, floor))
            if isinstance(entry, dict):
                value = entry.get('min')
                if value is None:
                    value = entry.get('mean')
                if value is not None:
                    return float(value)
        return fallback

    def open_purse(state, act, floor, fallback):
        """Open the purse for the shop a step belongs to. A step only knows
        its own shop, so the budget is re-based whenever the (act, floor) key
        changes -- one purse per shop, spent down across that shop's steps."""
        key = (act, floor)
        if state.get('gold_budget_shop') != key:
            state['gold_budget_shop'] = key
            state['gold_spent'] = 0.0
            state['gold_budget'] = shop_budget(act, floor, fallback)
        return state['gold_budget']

    # Danger, elites and rests do not depend on any card decision: they are a
    # function of the path alone (the rest-site credit only applies without the
    # hp simulation, where it is likewise path-determined).
    danger = 0.0
    rest_credit = 0.0
    elites = 0
    rests = 0
    removals = 0
    early_act1_elites = 0
    # Act-1 boss floor per route: the act-1 elite charge is measured against it,
    # so it has to be known before the rooms are walked (see
    # ACT1_ELITE_LATE_BONUS). Read from the route, never assumed.
    act1_boss_floor = None
    for step in steps:
        if (step[0] == 'room' and step[1] == 'Boss' and step[2] == 0
                and len(step) > 4 and step[4] is not None):
            act1_boss_floor = step[4]
            break
    for step in steps:
        if step[0] == 'room':
            room, act_index = step[1], step[2]
            floor = step[4] if len(step) > 4 else None
            if room == 'RestSite':
                rests += 1
                if not has_hp:
                    rest_credit += REST_CREDIT
                continue
            if room == 'Elite' and act_index < 2:  # acts 1-2: the relic economy
                elites += 1
            if (room == 'Elite' and act_index == 0
                    and floor is not None and act1_boss_floor):
                # Earlier act-1 elite costs more: the charge falls linearly to
                # zero at the act's boss floor. Negative danger, so it is
                # subtracted like every other danger term.
                early_act1_elites += 1
                span = max(1, act1_boss_floor - 1)
                share = max(0.0, min(1.0, (act1_boss_floor - floor) / span))
                danger -= ACT1_ELITE_LATE_BONUS * share
            cost = _ROOM_DANGER.get(room, MONSTER_DANGER) * (ACT3_DANGER_SCALE if act_index >= 2 else 1.0)
            if not has_hp and rest_credit > 0:
                offset = min(rest_credit, cost)
                rest_credit -= offset
                cost -= offset
            danger += cost
        elif step[0] == 'removal':
            removals += 1

    # Without a known deck the elite gate cannot be judged, so the historical
    # path-level credit is kept; with a deck the credit is paid per state below,
    # on the deck AT ENTRY (player brief: 用进入的卡组).
    start = {'deck': Counter(initial_deck) if track else Counter(), 'removed': 0,
             'takes': (), 'choices': (), 'hp': hp0, 'formation': 0.0 if track else ELITE_VALUE * elites,
             'score': {'attack': 0.0, 'defense': 0.0, 'engine': 0.0},
             'synergy': {}, 'relic_synergy': 0.0,
             'early_ok': False, 'early_takes': 0, 'boss_checked': False,
             'elites_credited': 0, 'elites_blocked': 0,
             'bosses_cleared': 0, 'bosses_blocked': 0,
             'gold_spent': 0.0, 'gold_budget': None, 'gold_budget_shop': None,
             'removal_acts': ()}
    states = {_state_key(start): start}
    peak = 1
    pruned = 0
    battles_seen = 0
    early_closed = False
    early_infeasible = False
    early_boss_done = False
    early_boss_infeasible = False
    early_elite_done = False
    early_elite_infeasible = False
    boss_infeasible = False
    for index, step in enumerate(steps):
        in_early_window = (not early_closed) and battles_seen <= EARLY_BATTLES
        kind = step[0]
        if kind == 'room':
            room, act_index = step[1], step[2]
            enemies = step[3] if len(step) > 3 else None
            if room in _BATTLE_ROOMS:
                battles_seen += 1
                if room == 'Elite' and act_index == 0 and not early_elite_done:
                    early_elite_done = True
                    survivors = [st for st in states.values()
                                 if st['early_takes'] >= EARLY_ELITE_MIN_TAKES]
                    if survivors:
                        states = _checkpoint_keeps(survivors)
                    else:
                        early_elite_infeasible = True
                if room == 'Boss' and act_index == 0 and not early_boss_done:
                    # Same hard + checkpoint-optimal contract as the elite.
                    early_boss_done = True
                    survivors = [st for st in states.values()
                                 if st['early_takes'] >= EARLY_BOSS_MIN_TAKES]
                    if survivors:
                        states = _checkpoint_keeps(survivors)
                    else:
                        early_boss_infeasible = True
                if battles_seen > EARLY_BATTLES and not early_closed:
                    # The first three battles are over: a route that never took a
                    # non-Power card inside the window is not a legal plan. If NO
                    # state satisfied it, the demand was unreachable (every menu
                    # offered only Powers or non-positive cards) and the window is
                    # left open rather than emptying the table.
                    early_closed = True
                    survivors = {key: state for key, state in states.items() if state['early_ok']}
                    if survivors:
                        states = survivors
                    else:
                        early_infeasible = True
            if track and room == 'Elite':
                # Elite strength gate (player brief of 2026-10-04): the relic
                # economy is only credited when the deck AT ENTRY clears the
                # elite's demand on the current act. A blocked elite still pays
                # its danger, so a weak deck is naturally steered away from it.
                need = elite_min(enemies, act_index)
                for state in states.values():
                    if act_index < 2 and deck_score(state['deck'], act_index) >= need:
                        state['formation'] += ELITE_VALUE
                        state['elites_credited'] += 1
                    else:
                        state['elites_blocked'] += 1
            if track and room == 'Boss':
                # Boss pass/fail gate (player ruling 2026-10-08: 「每一幕的最终 boss
                # 要求都只是能过就行」). The boss no longer contributes danger, so
                # without this the planner would walk a starter deck into the act
                # climax for free. The test is BOSS_MIN_BY_ACT, calibrated from the
                # saved runs, and a failing state is DROPPED rather than merely
                # penalised -- the route cannot be taken at all, not taken at a
                # discount.
                #
                # If EVERY state fails the table is left intact and the flag is
                # raised instead, mirroring the early-window contract
                # (early_infeasible, route.py:888): an unreachable gate is a
                # diagnostic, not a reason to return no plan at all.
                need = boss_min(act_index)
                cleared = [key for key, state in states.items()
                           if deck_score(state['deck'], act_index) >= need]
                for key, state in states.items():
                    if key in cleared:
                        state['bosses_cleared'] += 1
                    else:
                        state['bosses_blocked'] += 1
                if cleared:
                    states = {key: states[key] for key in cleared}
                else:
                    boss_infeasible = True
            if not has_hp:
                continue
            if room == 'RestSite':
                # The site is worth whatever its best use is worth: heal (more
                # when hurt) or smith the cards the plan took so far. Both the
                # missing life and the take count are state, so this is applied
                # per state; relic-driven extras stay free upside.
                for state in states.values():
                    missing = 1.0 - state['hp'] / max_hp
                    heal_value = REST_HEAL_WEIGHT * missing
                    smith_value = min(REST_SMITH_CAP, REST_SMITH_PER_CARD * len(state['takes'])) if track else 0.0
                    state['formation'] += max(heal_value, smith_value)
                    if heal_value >= smith_value:
                        state['hp'] = min(max_hp, state['hp'] + REST_HEAL_FRACTION * max_hp)
            elif room in _BATTLE_ROOMS:
                cost = FIGHT_HP_COST.get(room, MONSTER_DANGER) * (ACT3_HP_SCALE if act_index >= 2 else 1.0)
                for state in states.values():
                    # Gross wound first, then the post-combat relic heal. The
                    # order matters: healing before the wound would let a full
                    # hp bar absorb a free 6 and make a fight look free.
                    state['hp'] = max(0.0, state['hp'] - cost)
                    if heal_after_fight and state['hp'] > 0.0:
                        state['hp'] = min(max_hp, state['hp'] + heal_after_fight)
            continue
        if kind == 'removal':
            # A shop removal costs gold like anything else (seed 524130501: 100
            # against a 137 purse, so it and even the cheapest 49 card cannot
            # both be bought). It used to be taken unconditionally, which with
            # a budget would silently eat 73% of the shop and leave the plan
            # unable to afford a single card -- so it is now a branch like any
            # other purchase, and the DP decides whether the thinning is worth
            # more than the cards the same gold would buy.
            price = step[2] if len(step) > 2 else None
            floor = step[3] if len(step) > 3 else None
            table = {}
            for state in states.values():
                open_purse(state, step[1], floor, purses[index])
                _keep(table, state)
                if gold_on and price is not None and state['gold_spent'] + price > state['gold_budget']:
                    continue              # the purse does not cover this removal
                option = _branch(state)
                if track:
                    # Plan 5.1: a removal is worth the card's dimensions at the
                    # CURRENT shortfall weights plus the dilution it charged.
                    removed = _remove_target(option['deck'])
                    value = (dim_remove_value(removed, option['deck'], step[1])
                             if removed else 0.0)
                    option['formation'] += value
                    option['removed'] += 1
                    if removed:
                        option['deck'][removed] -= 1
                        if option['deck'][removed] <= 0:
                            del option['deck'][removed]
                else:
                    option['formation'] += REMOVAL_FIRST * (1.0 if step[1] < 2 else 0.5)
                if price is not None:
                    option['gold_spent'] = option.get('gold_spent', 0.0) + price
                option['removal_acts'] = option.get('removal_acts', ()) + (step[1],)
                _keep(table, option)
            states = table
            peak = max(peak, len(states))
            if len(states) > beam:
                pruned += len(states) - beam
                states = dict(sorted(states.items(), key=lambda item: -item[1]['formation'])[:beam])
            continue
        if kind == 'relic':
            # Every relic keeps the flat value; the synergy bonus is what makes
            # a relic this deck can use worth more than a blind one. A PRICED
            # relic is a purchase and branches (gold permitting); a TREASURE
            # relic is free and is credited to every state outright -- until
            # 2026-10-07 both were credited the same flat amount, so a free
            # chest scored exactly like a paid shelf relic.
            act_index = step[2] if len(step) > 2 else 0
            price = step[3] if len(step) > 3 else None
            if price is None:
                for state in states.values():
                    bonus = relic_synergy(step[1], state['deck'])
                    state['formation'] += SHOP_RELIC_VALUE + bonus
                    state['relic_synergy'] += bonus
                continue
            floor = step[4] if len(step) > 4 else None
            table = {}
            for state in states.values():
                open_purse(state, act_index, floor, purses[index])
                _keep(table, state)
                if gold_on and state['gold_spent'] + price > state['gold_budget']:
                    continue              # the purse does not cover this relic
                option = _branch(state)
                bonus = relic_synergy(step[1], option['deck'])
                option['formation'] += SHOP_RELIC_VALUE + bonus
                option['relic_synergy'] += bonus
                option['gold_spent'] = option.get('gold_spent', 0.0) + price
                _keep(table, option)
            states = table
            peak = max(peak, len(states))
            if len(states) > beam:
                pruned += len(states) - beam
                states = dict(sorted(states.items(), key=lambda item: -item[1]['formation'])[:beam])
            continue
        # A card decision: skip stays free, and every card on the menu is its
        # own branch, so the DP can defer to a later, better offer.
        table = {}
        if kind == 'reward':
            # Battle rewards AND event card acquisition (obtain:) both count
            # toward the first-three-battles requirement. A shop purchase is a
            # separate economy and does not (documented in the plan).
            cards, act_index = step[1], step[2]
            for state in states.values():
                # Every branch records its choice at this decision point, so
                # the best state's choices align with the steps and the strict
                # prescription can name what the DP took or skipped where.
                state['choices'] = state['choices'] + (('skip',),)
                _keep(table, state)
                for card in cards:
                    option = _branch(state)
                    option['choices'] = option['choices'][:-1] + (('card', card),)
                    if _apply_take(option, card, act_index, 1.0, track,
                                   in_early_window=in_early_window,
                                   archetype=archetype) is not None:
                        _keep(table, option)
        else:  # shopcard
            card, act_index = step[1], step[2]
            price = step[3] if len(step) > 3 else None
            floor = step[4] if len(step) > 4 else None
            for state in states.values():
                state['choices'] = state['choices'] + (('skip',),)
                open_purse(state, act_index, floor, purses[index])
                _keep(table, state)
                option = _branch(state)
                option['choices'] = option['choices'][:-1] + (('card', card),)
                # Skip the branch outright when the purse cannot cover the
                # price: an unaffordable card is not a decision.
                if gold_on and price is not None and option['gold_spent'] + price > option['gold_budget']:
                    continue
                if _apply_take(option, card, act_index, SHOP_CARD_SCALE, track,
                               archetype=archetype) is not None:
                    if price is not None:
                        option['gold_spent'] = option['gold_spent'] + price
                    _keep(table, option)
        states = table
        peak = max(peak, len(states))
        if len(states) > beam:
            pruned += len(states) - beam
            states = dict(sorted(states.items(), key=lambda item: -item[1]['formation'])[:beam])

    best = max(states.values(), key=lambda state: state['formation'])
    formation = best['formation']
    breakdown = {'elites': elites, 'cards_taken': len(best['takes']),
                 'removals': removals, 'rests': rests,
                 # Act-1 elite placement (see ACT1_ELITE_LATE_BONUS).
                 'act1_early_elites': early_act1_elites,
                 # Elite strength gate + first-three-battles diagnostics.
                 'elites_credited': best['elites_credited'],
                 'elites_blocked': best['elites_blocked'],
                 'early_ok': best['early_ok'],
                 'early_infeasible': early_infeasible,
                 'early_boss_infeasible': early_boss_infeasible,
                 'early_elite_infeasible': early_elite_infeasible,
                 'bosses_cleared': best['bosses_cleared'],
                 'bosses_blocked': best['bosses_blocked'],
                 'boss_infeasible': boss_infeasible}
    # Shop ledger (player brief 2026-10-07): what the plan thought it could
    # spend, what it actually spent, and what it left. gold_start is the
    # starting purse, not a shop's; gold_budget is the purse at the LAST shop
    # the plan entered, so a plan with no shop reports None there.
    # Which acts the plan BOUGHT a shop removal in. strict_tiers used to force
    # buy_removal +8 for every shop that merely OFFERED one, on the old rule
    # that the DP always removes; a removal is a priced branch now, so the
    # prescription has to follow the purchase, not the offer.
    breakdown['removal_acts'] = sorted(set(best.get('removal_acts', ())))
    breakdown['gold_start'] = round(gold_start, 1) if gold_on else None
    breakdown['gold_spent'] = round(best.get('gold_spent', 0.0), 1) if gold_on else None
    breakdown['gold_budget'] = (round(best['gold_budget'], 1)
                                if gold_on and best.get('gold_budget') is not None else None)
    breakdown['gold_left'] = (round(best['gold_budget'] - best.get('gold_spent', 0.0), 1)
                              if breakdown['gold_budget'] is not None else None)
    if track:
        deck = best['deck']
        breakdown['deck_size'] = _deck_size(deck)
        breakdown['starters_left'] = _starters(deck)
        breakdown['categories'] = dict(_categories(deck))
        breakdown['attack_score'] = round(best['score']['attack'], 2)
        breakdown['defense_score'] = round(best['score']['defense'], 2)
        breakdown['engine_score'] = round(best['score']['engine'], 2)
        # How much of the above came from the engine -> terminal synergy rather
        # than a flat tier. Read this to see whether the bonus actually fired.
        breakdown['synergy_total'] = round(sum(best['synergy'].values()), 2)
        breakdown['synergy_detail'] = {card: round(v, 2)
                                       for card, v in sorted(best['synergy'].items()) if v}
        breakdown['relic_synergy_total'] = round(best['relic_synergy'], 2)
    # The take log and per-step take/skip log drive the strict prescription,
    # so both are emitted with or without a known starting deck.
    breakdown['takes'] = list(best['takes'])
    breakdown['choices'] = [list(choice) if isinstance(choice, tuple) else choice
                            for choice in best['choices']]
    if track:
        dims = deck_dims(best['deck'])
        req = REQ_PER_ACT[2]          # the plan's A10 terminal line (79, 79, 57)
        breakdown['deck_dims'] = {'A': round(dims[0], 1), 'D': round(dims[1], 1), 'U': round(dims[2], 1)}
        breakdown['req_gap_terminal'] = [round(req[i] - dims[i], 1) for i in range(3)]
    if has_hp:
        breakdown['hp_left_frac'] = round(best['hp'] / max_hp, 3)
        if best['hp'] < LOW_HP_FRACTION * max_hp:   # the act-3 brief: arrive alive
            formation -= LOW_HP_PENALTY
    if not track:
        breakdown['relic_synergy_total'] = round(best['relic_synergy'], 2)
    # Which archetype plan scored this route, and what the pull contributed.
    # Recorded next to the take log so a reader can see the five plans differ by
    # preference and not by accident. archetype=None is the historical
    # single-objective score and always reports 0.0.
    archetype_ledger = best.get('archetype_bonus') or {}
    breakdown['archetype'] = archetype
    breakdown['archetype_bonus_total'] = round(sum(archetype_ledger.values()), 2)
    breakdown['archetype_bonus_detail'] = {card: round(v, 2)
                                           for card, v in sorted(archetype_ledger.items()) if v}
    breakdown['dp_mode'] = 'beam'
    breakdown['dp_beam'] = beam
    breakdown['dp_states_peak'] = peak
    breakdown['dp_states_pruned'] = pruned
    total = formation - danger
    return {'total': round(total, 2), 'formation': round(formation, 2),
            'danger': round(danger, 2), **breakdown}


def _route_action_lists(routes):
    # Normalise every accepted shape into a list of routes, each a list of
    # actions. Accepted: one route dict with an 'actions' key, a list of such
    # dicts (the top-N plan), or the legacy bare action list of a single route.
    # The legacy form MUST be recognised explicitly: mistaking it for route
    # dicts would silently yield empty plans and a filter that admits all.
    if isinstance(routes, dict):
        routes = [routes]
    routes = list(routes)
    if routes and all(isinstance(r, dict) and 'actions' not in r for r in routes):
        return [routes]
    return [list(r.get('actions') or []) if isinstance(r, dict) else list(r) for r in routes]


def map_filter(routes):
    """Constraint callable for the schedulers: allowed(map_actions_so_far,
    action) is True exactly when the map action is the next step of AT LEAST
    ONE of the planned routes. Matched on the kind/col/row keys the menus share
    with executed trace actions.

    Top-N union by position (player brief of 2026-10-04): position k admits
    {route_i[k] for i in top-N}. Before the routes diverge the union is a single
    step, so N=1 reproduces the single-route filter byte for byte; after they
    diverge the branching factor opens up. Past a route's end (a shorter
    branch) that route stops constraining. The plan is a preference carved out
    of the branching factor, never a bound on where the search may go."""
    planned = [[(a.get('col'), a.get('row')) for a in actions if a.get('kind') == 'map']
               for actions in _route_action_lists(routes)]

    def allowed(taken: int, action: dict) -> bool:
        here = (action.get('col'), action.get('row'))
        live = [steps[taken] for steps in planned if taken < len(steps)]
        if not live:                  # every route has ended: free again
            return True
        return here in live
    return allowed


def route_tiers(takes: list[tuple[int, str]] | list[list[tuple[int, str]]]) -> dict:
    """Label tiers for the host's policy_prior: +1 in every act where a
    planned deck simulation took the card, so the host's MarginalCard picks
    align with the plan. Only cards a plan actually took appear.

    Accepts one take log or several (top-N routes): the logs are UNIONED, the
    maximum mark per act wins, so a card any planned route takes is preferred
    in that act. Allocation only — never legality."""
    logs = list(takes)
    if logs and isinstance(logs[0], tuple):
        logs = [logs]
    tiers: dict[str, list[int]] = {}
    for log in logs:
        for act_index, card_id in log:
            row = tiers.setdefault(card_id, [0, 0, 0])
            row[min(act_index, 2)] = 1
    return {'card:' + card_id: row for card_id, row in tiers.items()}


def _card_sites(path: list[dict]) -> list[tuple]:
    """The card decision points of a route in _route_steps order, each as
    (kind, act_index, offered labels, menu labels): 'reward' is ONE decision
    among its menu cards, 'shopcard' is one independent single-card decision,
    'removal' has no CHOICE to record -- the DP decides whether to buy it, but
    a removal takes no card, so it contributes no entry to `choices` and is
    skipped by the caller's index walk. Buy nodes are deduped exactly as
    _route_steps dedupes them, so a choice per step still aligns."""
    sites = []
    kept_shops = _kept_shops(path)
    for node in path:
        act_index = node['act'] if node['act'] is not None else 0
        labels = node.get('labels') or []
        kind = node['kind']
        if kind == 'buy' and id(node) not in kept_shops:
            continue                      # matches _route_steps: one shelf per shop
        if kind == 'card_reward':
            cards = [label[5:] for label in labels if label.startswith('card:')]
            if cards:
                sites.append(('reward', act_index, tuple(cards), tuple(labels)))
        elif kind == 'buy':
            for label in labels:
                if label == 'buy_removal':
                    sites.append(('removal', act_index, (), (label,)))
                elif label.startswith('card:'):
                    sites.append(('shopcard', act_index, (label[5:],), (label,)))
        elif kind == 'select_cards':
            cards = [label[7:] for label in labels if label.startswith('obtain:')]
            if cards:
                sites.append(('reward', act_index, tuple(cards), tuple(labels)))
    return sites


def card_universe(facts: dict) -> set:
    """Every card label the seed can offer, from the walk facts: tree node
    labels (reward menus, shops, event obtains) plus the global card_offers
    counter. The strict tiers use it to cover cards the plan's own path never
    saw, closing the cross-act / never-seen tier holes."""
    universe = set()
    for node in facts.get('tree') or []:
        for label in node.get('labels') or []:
            if isinstance(label, str) and (label.startswith('card:')
                                           or label.startswith('obtain:')):
                universe.add(label)
    universe.update('card:' + c for c in (facts.get('card_offers') or {}))
    for entry in (facts.get('shops') or {}).values():
        for label in entry:
            if isinstance(label, str) and label.startswith('card:'):
                universe.add(label)
    # The walk facts only cover what walker.WALK_DEFAULT skirmishes saw; reward
    # pools can still surface cards none of them observed (measured: a seed
    # offered Drum of Battle to the real run but to no walk). Close the hole
    # completely by covering the whole known card pool.
    from .deck_value import V_TABLE
    universe.update('card:' + c for c in V_TABLE)
    return universe


def strict_tiers(path: list[dict], score: dict, universe: set | None = None) -> dict[str, list[int]]:
    """Per-act label tiers that FORCE the host's labelled menus to follow this
    route's DP decisions (player brief of 2026-10-05: 严格按 dp 路线走, rest
    sites excluded). Magnitude STRICT_TIER, the host's channel limit, where the
    (tier, native score) lexicographic order makes tier dominant.

    Per act: a card the plan took at any reward menu or shop gets card:+8; a
    card the plan saw and skipped gets card:-8 (so MarginalCard cannot overrule
    the plan); event obtain cards the same under obtain:; a shop removal the
    plan BOUGHT gets buy_removal +8 and a declined one is untiered; chosen
    event options get event:+8 and the other options of those menus event:-8.

    Known granularity limit: the channel is per LABEL per ACT, so a take
    forced at one menu is forced at every menu of the same act that offers the
    label (reward takes leak into shop buys and vice versa) — a deviation the
    route-plan records but cannot express. The map is forced by the `route`
    field's +500 preference, not by tiers. Rest sites are never tiered."""
    choices = score.get('choices') or []
    take_acts: dict[str, set] = {}
    for act, card in score.get('takes') or []:
        take_acts.setdefault(card, set()).add(min(max(act, 0), 2))
    seen: dict[str, set] = {}
    events: dict[int, dict] = {}
    index = 0
    for kind, act, cards, labels in _card_sites(path):
        if kind == 'removal':
            continue                      # no card chosen; never indexed against `choices`
        choice = tuple(choices[index]) if index < len(choices) else ('skip',)
        index += 1
        prefix = 'card:'
        if any(label.startswith('obtain:') for label in labels):
            prefix = 'obtain:'
        for card in cards:
            if choice == ('card', card):
                seen.setdefault(prefix + card, set()).add(min(max(act, 0), 2))
    for node in path:
        if node['kind'] != 'event':
            continue
        action = node.get('action') or {}
        key = action.get('key')
        if key is None:
            continue
        act = node['act'] if node['act'] is not None else 0
        menu = events.setdefault(act, {})
        for label in node.get('labels') or []:
            if label.startswith('event:'):
                menu[label] = label == 'event:' + key
    rows: dict[str, list[int]] = {}

    def mark(label: str, act: int, tier: int):
        row = rows.setdefault(label, [0, 0, 0])
        row[min(max(act, 0), 2)] = tier

    # Plan-wide coverage (fixes 2026-10-05: the per-menu rows left cross-act
    # and never-seen cards at tier 0, so the host took them off-plan). A card
    # the plan takes is +8 in every act it takes it and -8 elsewhere; every
    # other card the seed can offer is -8 everywhere; removals and the route's
    # event options keep their dedicated rows. `seen` is kept for diagnostics.
    for label in sorted(universe or ()):
        card = label.split(':', 1)[1] if ':' in label else label
        acts = take_acts.get(card) or set()
        for a in range(3):
            mark(label, a, STRICT_TIER if a in acts else -STRICT_TIER)
    # Only the removals the plan actually PAID for are forced. The DP used to
    # take every removal it saw, so offering one was the same as buying it; a
    # priced removal is a branch now and the plan may decline it, in which case
    # forcing buy_removal would contradict the plan these tiers describe.
    for act in score.get('removal_acts') or ():
        mark('buy_removal', act, STRICT_TIER)
    for act, menu in events.items():
        for label, chosen in menu.items():
            mark(label, act, STRICT_TIER if chosen else -STRICT_TIER)
    return {label: row for label, row in rows.items() if any(row)}


def _archetype_plans(facts: dict, initial, run_state, paths: list, archetypes: list[str],
                     top_n: int) -> list[dict]:
    """One best route per archetype (player brief of 2026-10-09: 「dp 的前五个
    方案分别是对战士的不同流派有特殊偏好的方案」).

    Every route is re-scored under each archetype's take preference and the
    best route of that archetype is returned, so the five plans differ in the
    DECK they build, not in how they were ranked. Before the archetype axis
    existed, all routes were scored by one identical objective and the top-N
    were N copies of the same optimum -- measured on seed 70753827, where
    ranks 1/2/3 and 4/5 were byte-identical take logs.

    Ties inside an archetype break on enumeration id, the same rule the
    historical single-objective ranking used."""
    scored = []
    for archetype in archetypes:
        best = None
        for index, path in enumerate(paths):
            score = score_path(path, initial, run_state, archetype=archetype)
            row = {'id': index, 'score': score, 'archetype': archetype,
                   'actions': [node['action'] for node in path if node['kind'] == 'map']}
            if best is None or (-row['score']['total'], row['id']) < (-best['score']['total'], best['id']):
                best = row
        if best is not None:
            scored.append(best)
    scored.sort(key=lambda route: (-route['score']['total'], route['id']))
    return scored[:max(1, top_n)]

def _freeze(value):
    """A hashable projection of a JSON value, for de-duplication only."""
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plan_fingerprint(path: list[dict], score: dict) -> tuple:
    """The identity of a plan for de-duplication: the map steps it walks
    together with the room each step leads to, and the take log it collects.
    Two routes that walk the same map and take the same cards are the same
    plan even when their scores differ in the last decimal; two routes that
    share a map coordinate but enter different rooms are NOT the same plan."""
    steps = tuple((node['action'].get('col'), node['action'].get('row'),
                   _freeze(node.get('leads_to')))
                  for node in path if node['kind'] == 'map')
    takes = score.get('takes') or ()
    return steps, tuple(tuple(take) for take in takes)


def _distinct_plans(routes: list[dict], paths: dict, count: int) -> list[dict]:
    """The first `count` ranked routes whose plans are pairwise different.

    The single-objective ranking can hand back the same plan N times: measured
    on seed 70753827, ranks 1/2/3 and 4/5 were byte-identical take logs, so a
    top-5 prescription spent five evaluation slots on two distinct requests.
    Duplicates are skipped and the next-ranked distinct route takes their
    place; the ranking itself is never re-ordered."""
    plans = []
    seen = set()
    for route in routes:
        fingerprint = _plan_fingerprint(paths[route['id']], route['score'])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        plans.append(route)
        if len(plans) >= count:
            break
    return plans


def _score_path_worker(job: tuple) -> dict:
    """ProcessPool entry point for one enumerated path. Module level on
    purpose: the Windows spawn start method has to pickle it by name."""
    path, initial, run_state = job
    return score_path(path, initial, run_state)


def plan(facts: dict, limit: int = 3, top_n: int = 1,
         archetypes: list[str] | None = None, workers: int = 1) -> dict:
    """Score every route and rank it. Returns the top `top_n` routes as the
    constraint the search follows (their map actions are unioned by the
    scheduler filter and their take logs unioned into the label tiers), plus
    the runner-ups for diagnosis.

    top_n=1 reproduces the historical single-best-route plan exactly; the
    routes are ranked by score and ties broken by enumeration id, so the top-1
    is always the same route the single-route plan returned.

    archetypes (player brief of 2026-10-09) changes that: when it is given,
    every route is re-scored once per archetype and the top of EACH archetype
    becomes one of the returned plans, so top_n=5 returns five DIFFERENT
    archetype builds instead of five copies of the single optimum. It
    defaults to None (no archetype axis), which keeps the historical ranking
    byte-for-byte."""
    initial = facts.get('initial_deck') or None
    # The measured purse is carried INTO the run state the DP scores with, so
    # score_path sees the same starting gold and the same per-floor trace the
    # walks recorded. Both are optional: a facts file without them keeps the
    # old no-gold behaviour.
    run_state = dict(facts.get('initial_run') or {}) or None
    if run_state is not None and facts.get('gold_trace'):
        run_state['gold_trace'] = facts['gold_trace']
    paths = list(enumerate_paths(facts))
    if workers > 1 and len(paths) > 1:
        # Scoring one path runs a whole-act simulation with the initial deck,
        # so the loop dominates the planner: measured 22.2 s for 18 paths
        # against 1.1 s without the deck. score_path is a pure function of its
        # arguments, so the paths are scored in a process pool. Opt-in on
        # purpose: the default stays the serial loop, which is what the unit
        # tests and every existing caller exercise.
        from concurrent.futures import ProcessPoolExecutor
        jobs = [(path, initial, run_state) for path in paths]
        with ProcessPoolExecutor(max_workers=min(workers, len(paths))) as executor:
            scores = list(executor.map(_score_path_worker, jobs))
    else:
        scores = [score_path(path, initial, run_state) for path in paths]
    routes = []
    for index, path in enumerate(paths):
        routes.append({'id': index, 'score': scores[index],
                       'actions': [node['action'] for node in path if node['kind'] == 'map']})
    routes.sort(key=lambda route: (-route['score']['total'], route['id']))
    order = {route['id']: paths[route['id']] for route in routes}
    if archetypes:
        # Archetype plans are scored by their OWN objective, so they are not
        # comparable with the single-objective ranking above and must not be
        # mixed into `routes` or `alternatives` (those stay the honest
        # un-preferenced ranking of the same seed).
        top = _archetype_plans(facts, initial, run_state, paths, list(archetypes), top_n)
    else:
        top = _distinct_plans(routes, order, max(1, top_n)) if routes else []
    for route in top:
        if route['score'].get('takes'):
            route['route_tiers'] = route_tiers(route['score']['takes'])
        # The strict prescription (player brief of 2026-10-05): per-act label
        # tiers that force this route's card / removal / event decisions,
        # covering EVERY card the seed can offer (plan-wide +/-8, no tier-0
        # holes). `seen` sites no longer drive the rows; kept in strict_tiers
        # for diagnostics.
        route['strict_tiers'] = strict_tiers(order[route['id']], route['score'],
                                             card_universe(facts))
    best = top[0] if top else None
    if best is not None and len(top) > 1:
        # The union the search actually consumes, kept next to the per-route
        # tiers so a reader can see what each route contributed.
        best = dict(best)
        best['route_tiers'] = route_tiers([r['score']['takes'] for r in top if r['score'].get('takes')])
    chosen = {route['id'] for route in top}
    alternatives = [route for route in routes if route['id'] not in chosen][:limit]
    return {'schema': 'spire-route-plan/v2', 'routes_scored': len(routes),
            'top_n': len(top),
            'top': top,
            'best': best, 'alternatives': alternatives,
            'allocation_signal_only': True}
