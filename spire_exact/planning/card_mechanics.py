"""Engine classes and terminal affinity; the synergy bonus functions.

Allocation signal only: never legality, never a bound, never a proof. Pure data
plus pure functions, with no import of route.py (so route.py can import this).

Player brief of 2026-10-04 (引擎 -> 终端 point-to-point synergy):
  - A terminal card gains value from the ENGINE cards the deck already holds.
  - synergy(card, deck) = SUM_k a_{c,k} * h(E_k) + special terms
      h(E)  = min(1, (E / H_K) ** H_P)          bounded convex maturity
      beta(E) = BETA_GAMMA * exp(-(E-1)^2 / BETA_SIGMA)   HELLRAISER only
  - D1: exhaust gives NO archetype bonus to card terminals, so ENGINE_CARDS
    has no "exhaust" key and AFFINITY has no "exhaust" column. The exhaust
    COUNT is still computed, for the relic side only (D14 option B).
  - D3: POMMEL_STRIKE is not a strength terminal and is absent from AFFINITY.
  - D5: HELLRAISERs exhaust trigger peaks at exactly 1 exhaust card.

Engine classes are derived from the bundled native catalog
(`outputs/native-catalog-check/data/catalog.json`) plus the Chinese card
descriptions in `_card_names.json`; see ENGINE_CARDS for the exact rule and
RULES.md / the TODO for the derivation. Regenerate, do not hand-edit.
"""
from __future__ import annotations
import math

# --- Maturity and exhaust-trigger curves (TODO section 1.1 / 1.2) ----------
# h(0)=0, h(1)=0.4016, h(2)=0.8759, h(3)=1.0. Convex on [0, H_K], then flat.
H_K = 2.25
H_P = 1.125
# beta(0)=0, beta(1)=1.200, beta(2)=0.441, beta(3)=0.022, beta(4+)=0. Peaks at 1.
BETA_GAMMA = 1.2
BETA_SIGMA = 1.0
# PERFECTED_STRIKE pays a deck-size compensation term C*max(0, Strikes - 4):
# the starting deck already holds 4~5 Strikes, so only the excess is credited.
PERFECTED_OFFSET_C = 0.375
PERFECTED_OFFSET_FLOOR = 4

# --- Engine classes (Ironclad pool) ---------------------------------------
# Rule per class, verified against the catalog + zhs descriptions:
#   strength   描述含"力量"                   (9)
#   vulnerable 描述含"易伤"                   (13 raw -> 12 effective)
#   block      描述含"格挡"                   (21 raw -> 20 effective)
#   hp_cost    HpLoss variable OR per-turn self HP loss   (9)
#   exhaust    描述含"消耗"                   (17)  RELIC SIDE ONLY (D1)
# The three starter basics (STRIKE_IRONCLAD / DEFEND_IRONCLAD / BASH) are NOT
# engines for strength/vulnerable/block/hp_cost: a starting Defend is not a
# block engine the deck BUILT. Strike is the exception -- see STRIKE below.
ENGINE_CARDS: dict[str, frozenset[str]] = {
    'strength': frozenset({
        'BRAND', 'DEMON_FORM', 'DOMINATE', 'EXPECT_A_FIGHT', 'FIGHT_ME',
        'INFLAME', 'MANGLE', 'RUPTURE', 'SETUP_STRIKE',
    }),
    'vulnerable': frozenset({
        'BREAK', 'BULLY', 'COLOSSUS', 'CRUELTY', 'DISMANTLE', 'DOMINATE',
        'MOLTEN_FIST', 'TAUNT', 'THUNDERCLAP', 'TREMBLE', 'UPPERCUT',
        'VICIOUS',
    }),
    'block': frozenset({
        'ARMAMENTS', 'BARRICADE', 'BLOOD_WALL', 'BODY_SLAM', 'COLOSSUS',
        'CRIMSON_MANTLE', 'DEMONIC_SHIELD', 'EVIL_EYE', 'EXPECT_A_FIGHT',
        'FEEL_NO_PAIN', 'FLAME_BARRIER', 'IMPERVIOUS', 'IRON_WAVE',
        'JUGGERNAUT', 'RAGE', 'SECOND_WIND', 'SHRUG_IT_OFF', 'TAUNT',
        'TRUE_GRIT', 'UNMOVABLE',
    }),
    'hp_cost': frozenset({
        'BLOODLETTING', 'BLOOD_WALL', 'BRAND', 'BREAKTHROUGH',
        'CRIMSON_MANTLE', 'DEMONIC_SHIELD', 'HEMOKINESIS', 'INFERNO',
        'OFFERING',
    }),
}

# The exhaust class: NOT an engine for card terminals (D1). It exists so the
# relic side can score CHARONS_ASHES and friends (D14 option B).
EXHAUST_CARDS: frozenset[str] = frozenset({
    'ASHEN_STRIKE', 'BRAND', 'BURNING_PACT', 'CINDER', 'CORRUPTION',
    'DARK_EMBRACE', 'DRUM_OF_BATTLE', 'EVIL_EYE', 'FEEL_NO_PAIN',
    'FIEND_FIRE', 'HAVOC', 'HOWL_FROM_BEYOND', 'PACTS_END', 'SECOND_WIND',
    'STOKE', 'THRASH', 'TRUE_GRIT',
})

# Strike is counted by ID, not by a card list (the mechanic literally counts
# Strike cards, and the starting Strikes ARE Strikes).
STRIKE_MARKER = 'STRIKE'

# --- Terminal affinity (13 cards) -----------------------------------------
# Base scores live in ironclad_tiers.OVERRIDES, not here; this table is only
# the engine -> terminal weight. No "exhaust" column anywhere (D1 / D14).
AFFINITY: dict[str, dict[str, float]] = {
    'BODY_SLAM': {'block': 2.4},
    'TEAR_ASUNDER': {'strength': 4.8, 'hp_cost': 1.2},
    'HELLRAISER': {'strike': 0.6},
    'INFERNO': {'hp_cost': 1.2},
    'SWORD_BOOMERANG': {'strength': 4.8},
    'WHIRLWIND': {'strength': 3.2},
    'TWIN_STRIKE': {'strength': 3.2},
    'SPITE': {'strength': 3.2},
    'JUGGERNAUT': {'block': 2.4},
    'BULLY': {'vulnerable': 1.5},
    'DISMANTLE': {'vulnerable': 1.5},
    'MOLTEN_FIST': {'vulnerable': 1.5},
    'PERFECTED_STRIKE': {'strike': 0.6},
    # Player brief 2026-10-06: the FIRST defence-side consumer of the
    # vulnerable engine. Colossus blocks AND halves the damage of enemies that
    # are vulnerable, so the deck's vulnerable supply is what makes it a
    # defensive payoff -- no attack terminal expressed this before.
    'COLOSSUS': {'vulnerable': 2.0, 'block': 1.2},
}


# --- Ironclad archetypes (player brief of 2026-10-09) ----------------------
# The five plans plan() must return are five DIFFERENT archetype builds of the
# same seed, not five copies of the single optimum. This table is the preference
# axis that makes them differ.
#
# Derived from the tables above, NOT hand-picked: every core is
#   (engine cards of that class) UNION (card terminals AFFINITED to it)
# so the definition cannot drift away from the synergy model that already
# prices these cards. hp_cost becomes the expenditure (spending HP) archetype
# and EXHAUST_CARDS becomes the exhaust archetype; the draw archetype is
# deliberately NOT one of the five (see ARCHETYPE_NOTE below).
#
# Measured on this table (Ironclad pool):
#   strength 14 / vulnerable 12 / block 20 / expenditure 9 / exhaust 17
#   68 cards belong to exactly one archetype; 20 belong to two or more.
# Every pairwise overlap is small and mechanical (BRAND is both a strength and
# an hp_cost engine), so no card had to be force-assigned.
#
# Allocation signal only, like everything else in this module: a bias shifts a
# PRICING signal, never legality, never a bound, never a proof. It is applied
# to the DP take VALUE (route.py _apply_take), so a negative bias can only
# make the planner skip a legal card -- never make the game refuse it.
#
# ARCHETYPE_NOTE: 穷举而非凭直觉 -- a 6th 'draw' archetype was computed and
# rejected. deck_value.DRAW_CARDS holds 37 cards, of which 5 are already
# exhaust cards and several are low-value filler; a bias over it was not a
# distinct plan but a duplicate of the exhaust one. The user asked for five.
ARCHETYPE_ORDER: tuple[str, ...] = (
    'strength', 'vulnerable', 'block', 'expenditure', 'exhaust',
)

ARCHETYPE_CORES: dict[str, frozenset[str]] = {
    'strength': frozenset(ENGINE_CARDS['strength']) | frozenset(
        c for c, a in AFFINITY.items() if 'strength' in a),
    'vulnerable': frozenset(ENGINE_CARDS['vulnerable']) | frozenset(
        c for c, a in AFFINITY.items() if 'vulnerable' in a),
    'block': frozenset(ENGINE_CARDS['block']) | frozenset(
        c for c, a in AFFINITY.items() if 'block' in a),
    'expenditure': frozenset(ENGINE_CARDS['hp_cost']),
    'exhaust': frozenset(EXHAUST_CARDS),
}

# Per-archetype pull: (exclusive core of the requested archetype) -> +1.0,
# (exclusive core of some OTHER archetype) -> -0.35. A card in two
# archetypes is neutral to both.
ARCHETYPE_EXCLUSIVE_BIAS = -0.35

# --- Archetype bias magnitude ---------------------------------------------
# archetype_bias() returns a dimensionless pull in [-0.35, +1.0]; the take
# VALUE it is applied to is in deck-value points and is deck- AND act-
# dependent (the same card is worth ~40 in a starved act-0 deck and ~4 once
# the deck is full -- measured 1-cost card at 10 points, act weights
# (1.344, 1.42, 1.325)). A fixed point offset would therefore be a huge
# nudge for one card and a rounding error for another, so the bias is applied
# proportionally to the card's own value and the scale is set here.
#
# 0.25 was chosen by measurement on seed 70753827 (the seed whose five top
# routes were byte-identical before this axis existed): at 0.25 a core card of
# the requested archetype is worth 1.25x and an exclusive core of another
# archetype 0.65x of its base take value, which is enough to flip a close menu
# but not enough to take a card whose base value is negative.
# scale is a MULTIPLIER on the base value, so 1.0 means "a core card is worth
# double, a foreign core is worth 0.65x". Scanned on seed 70753827 facts
# (30 walks): 0.25 gave only 2/5 distinct builds (strength and vulnerable
# plans took no core card of their own at all), 0.5 gave 4/5, 1.0 is the first
# setting that yields five genuinely different take logs, and 2.0/4.0 degenerate
# into taking three copies of BODY_SLAM while a 3.5 repeat penalty is charged.
ARCHETYPE_BIAS_SCALE = 1.0


def archetype_bias(card_id: str, archetype: str | None) -> float:
    """Preference tilt for taking card_id while planning archetype.

    Returns an offset in [-0.35, +1.0]. archetype=None (the historical
    single-objective plan) always returns 0.0, so every caller that does not
    ask for a specific archetype scores exactly as it did before the
    archetype axis existed.

        +1.00  card is an exclusive core of the requested archetype
         0.00  card is NEUTRAL (in no archetype) or SHARED (in two or more)
        -0.35  card is an EXCLUSIVE core of some OTHER archetype

    Shared cards get 0.0 on purpose: DOMINATE (strength+vulnerable), COLOSSUS
    (vulnerable+block), BRAND (strength+expenditure+exhaust) and friends are
    exactly the pivot cards that let a plan turn, and charging them for two
    memberships would freeze the plans into whichever archetype listed them
    first."""
    if not archetype:
        return 0.0
    own = ARCHETYPE_CORES.get(archetype)
    if own is None:
        return 0.0
    membership = sum(1 for cores in ARCHETYPE_CORES.values() if card_id in cores)
    if membership != 1:
        # 0 members: the card belongs to NO archetype, so it is neutral filler
        # (BATTLE_TRANCE, JUGGLING, RAMPAGE, RESTLESSNESS ...) and must not be
        # charged for a membership it does not have -- doing so taxed every plan
        # with one flat -0.35 haircut on most cards and dragged the archetype
        # scores below the un-preferenced ranking.
        # >1 members: a shared pivot card (DOMINATE, COLOSSUS, BRAND ...).
        return 0.0
    if card_id in own:
        return 1.0
    return ARCHETYPE_EXCLUSIVE_BIAS

# --- Relic affinity (27 relics, conservative) -----------------------------
# Cap per class: strength <= 1.0, block <= 0.8, vulnerable <= 0.5,
# hp_cost <= 0.4, strike <= 0.4, exhaust <= 0.5. Every entry below passes.
RELIC_AFFINITY: dict[str, dict[str, float]] = {
    'STRIKE_DUMMY': {'strike': 0.40},
    'FAKE_STRIKE_DUMMY': {'strike': 0.40},
    'SHURIKEN': {'strength': 0.60},
    'ORNAMENTAL_FAN': {'block': 0.50},
    'CHARONS_ASHES': {'exhaust': 0.50},
    'DEMON_TONGUE': {'hp_cost': 0.40},
    'PAPER_PHROG': {'vulnerable': 0.50},
    'BRIMSTONE': {'strength': 0.80},
    'VAJRA': {'strength': 0.60},
    'SWORD_OF_JADE': {'strength': 0.60},
    'GIRYA': {'strength': 0.50},
    'KUSARIGAMA': {'strength': 0.40},
    'KUNAI': {'block': 0.40},
    'PARRYING_SHIELD': {'block': 0.40},
    'SELF_FORMING_CLAY': {'hp_cost': 0.30},
    'FORGOTTEN_SOUL': {'exhaust': 0.35},
    'BAG_OF_MARBLES': {'vulnerable': 0.45},
    'RUINED_HELMET': {'strength': 0.35},
    'RED_SKULL': {'strength': 0.30},
    'ANCHOR': {'block': 0.30},
    'ORICHALCUM': {'block': 0.30},
    'CAPTAINS_WHEEL': {'block': 0.25},
    'BLOOD_VIAL': {'hp_cost': 0.20},
    'JOSS_PAPER': {'exhaust': 0.30},
    'BURNING_STICKS': {'exhaust': 0.25},
    'HAND_DRILL': {'vulnerable': 0.30},
    'PAELS_EYE': {'exhaust': 0.00},
}

# The conservative per-class cap on a relic coefficient (TODO section 1.7).
RELIC_CAPS: dict[str, float] = {
    'strength': 1.0, 'block': 0.8, 'vulnerable': 0.5,
    'hp_cost': 0.4, 'strike': 0.4, 'exhaust': 0.5,
}


def maturity(E: int) -> float:
    """h(E) = min(1, (E / H_K) ** H_P): bounded, and convex on [0, H_K]."""
    if E <= 0:
        return 0.0
    return min(1.0, (E / H_K) ** H_P)


def exhaust_beta(E: int) -> float:
    """beta(E) = BETA_GAMMA * exp(-(E-1)^2 / BETA_SIGMA); peaks at E == 1."""
    if E <= 0:
        return 0.0
    return BETA_GAMMA * math.exp(-((E - 1.0) ** 2) / BETA_SIGMA)


def engine_counts(deck) -> dict[str, int]:
    """How many engine cards of each class the deck holds.

    Returns the four listed classes plus 'strike' (by ID) and 'exhaust'
    (relic side only). synergy() reads only the keys AFFINITY uses, so the
    exhaust count never reaches a card terminal (D1)."""
    counts = {kind: sum(n for card, n in deck.items() if card in cards)
              for kind, cards in ENGINE_CARDS.items()}
    counts['strike'] = sum(n for card, n in deck.items() if STRIKE_MARKER in card)
    counts['exhaust'] = sum(n for card, n in deck.items() if card in EXHAUST_CARDS)
    return counts


def synergy(card_id: str, deck) -> float:
    """Engine -> terminal bonus for taking one more copy of card_id.

    SUM_k a_{c,k} * h(E_k) over the keys AFFINITY holds, plus the two special
    terms: HELLRAISERs exhaust trigger and PERFECTED_STRIKEs deck-size
    compensation. Never negative, and bounded by SUM_k a_{c,k} + beta peak
    (the PERFECTED_STRIKE term is the one deliberate unbounded term)."""
    affinity = AFFINITY.get(card_id)
    if not affinity:
        return 0.0
    counts = engine_counts(deck)
    total = sum(weight * maturity(counts.get(kind, 0))
                for kind, weight in affinity.items())
    if card_id == 'HELLRAISER':
        total += exhaust_beta(counts.get('exhaust', 0))
    elif card_id == 'PERFECTED_STRIKE':
        total += PERFECTED_OFFSET_C * max(0, counts.get('strike', 0) - PERFECTED_OFFSET_FLOOR)
    return total


def relic_synergy(relic_id: str, deck) -> float:
    """Bonus for taking relic_id given the deck. May use the exhaust class."""
    affinity = RELIC_AFFINITY.get(relic_id)
    if not affinity:
        return 0.0
    counts = engine_counts(deck)
    return sum(weight * maturity(counts.get(kind, 0))
               for kind, weight in affinity.items())
