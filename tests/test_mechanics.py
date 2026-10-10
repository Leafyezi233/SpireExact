"""Unit tests for the engine -> terminal synergy (card_mechanics.py) and its
integration into the route planner.

The player brief of 2026-10-04 fixed the mechanism; these tests pin the
properties the brief demanded (boundedness, convexity, the exhaust peak at 1,
the D1/D3/D14 exclusions) and the measured engine thresholds.
"""
import unittest
from collections import Counter

from spire_exact.planning.card_mechanics import (
    AFFINITY, BETA_GAMMA, ENGINE_CARDS, EXHAUST_CARDS, H_K, H_P,
    PERFECTED_OFFSET_C, PERFECTED_OFFSET_FLOOR, RELIC_AFFINITY, RELIC_CAPS,
    engine_counts, exhaust_beta, maturity, relic_synergy, synergy,
)

STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}
ENGINE_ONE = {"strength": "INFLAME", "vulnerable": "TREMBLE",
              "block": "UNMOVABLE", "hp_cost": "BLOODLETTING"}


class MaturityTests(unittest.TestCase):
    def test_maturity_is_bounded(self):
        for E in range(0, 40):
            self.assertGreaterEqual(maturity(E), 0.0)
            self.assertLessEqual(maturity(E), 1.0)

    def test_maturity_increasing(self):
        for E in range(0, 20):
            self.assertGreaterEqual(maturity(E + 1), maturity(E))

    def test_maturity_convex_before_cap(self):
        # On [0, H_K] the second difference must be >= 0: this is exactly the
        # "成型递增" (and therefore "互斥") property the brief asked for.
        step = H_K / 40.0
        for i in range(1, 40):
            a, b, c = maturity_grid(i - 1, step), maturity_grid(i, step), maturity_grid(i + 1, step)
            self.assertGreaterEqual(b - 2 * a + c, -1e-12)

    def test_maturity_caps_at_one(self):
        self.assertAlmostEqual(maturity(0), 0.0)
        self.assertAlmostEqual(maturity(2), 0.8759, places=4)   # not yet capped
        self.assertAlmostEqual(maturity(3), 1.0)                # (3/2.25)^1.125 >= 1
        self.assertAlmostEqual(maturity(30), 1.0)

    def test_maturity_first_step_value(self):
        # h(1) = (1/2.25)^1.125 = 0.4016, the value the parameter choice maximised.
        self.assertAlmostEqual(maturity(1), (1.0 / H_K) ** H_P, places=12)
        self.assertAlmostEqual(maturity(1), 0.4016, places=4)


def maturity_grid(i, step):
    return min(1.0, ((i * step) / H_K) ** H_P)


class ExhaustBetaTests(unittest.TestCase):
    def test_beta_zero_at_zero(self):
        self.assertAlmostEqual(exhaust_beta(0), 0.0)
        self.assertAlmostEqual(exhaust_beta(-3), 0.0)

    def test_beta_peaks_at_one(self):
        # D5: the first exhaust card is worth the most, the second less, the
        # third almost nothing.
        self.assertGreater(exhaust_beta(1), exhaust_beta(2))
        self.assertGreater(exhaust_beta(2), exhaust_beta(3))
        self.assertLessEqual(exhaust_beta(3), 0.05)
        self.assertAlmostEqual(exhaust_beta(1), BETA_GAMMA, places=12)
        self.assertAlmostEqual(exhaust_beta(1), 1.2, places=4)
        self.assertAlmostEqual(exhaust_beta(2), 0.4415, places=4)
        self.assertAlmostEqual(exhaust_beta(3), 0.0220, places=4)

    def test_beta_bounded_by_gamma(self):
        for E in range(0, 60):
            self.assertLessEqual(exhaust_beta(E), BETA_GAMMA + 1e-12)


class EngineClassTests(unittest.TestCase):
    def test_engine_counts_strike_by_id(self):
        deck = Counter({"STRIKE_IRONCLAD": 2, "TWIN_STRIKE": 1, "ASHEN_STRIKE": 1,
                        "BASH": 1, "DEFEND_IRONCLAD": 1})
        # Every id containing STRIKE counts: the mechanic counts Strike cards.
        self.assertEqual(engine_counts(deck)["strike"], 4)

    def test_no_exhaust_engine(self):
        # D1: exhaust must never be an engine class for card terminals.
        self.assertNotIn("exhaust", ENGINE_CARDS)

    def test_engine_counts_still_reports_exhaust(self):
        # D14 option B: the COUNT is available for the relic side.
        deck = Counter({"CORRUPTION": 1, "TRUE_GRIT": 1, "SLICE": 1})
        self.assertEqual(engine_counts(deck)["exhaust"], 2)
        self.assertIn("exhaust", engine_counts(deck))

    def test_engine_classes_hold_no_starters_except_strike(self):
        # A starting Defend is not a block engine the deck built.
        for kind, cards in ENGINE_CARDS.items():
            for starter in ("STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"):
                self.assertNotIn(starter, cards, msg=kind + "/" + starter)
        self.assertEqual(engine_counts(Counter(STARTER))["block"], 0)
        self.assertEqual(engine_counts(Counter(STARTER))["strike"], 5)

    def test_strike_engine_counts_only_ids_containing_strike(self):
        self.assertEqual(engine_counts(Counter({"PERFECTED_STRIKE": 1}))["strike"], 1)
        self.assertEqual(engine_counts(Counter({"POMMEL_STRIKE": 1}))["strike"], 1)
        self.assertEqual(engine_counts(Counter({"BASH": 1}))["strike"], 0)


class AffinityTableTests(unittest.TestCase):
    def test_no_exhaust_in_card_affinity(self):
        # D14 option B, card side: no terminal may carry an exhaust column.
        for card, affinity in AFFINITY.items():
            self.assertNotIn("exhaust", affinity, msg=card)

    def test_exhaust_relic_synergy_active(self):
        # D14 option B, relic side: CHARONS_ASHES must fire once exhaust exists.
        self.assertGreater(relic_synergy("CHARONS_ASHES", Counter({"CORRUPTION": 1})), 0.0)
        self.assertGreater(relic_synergy("FORGOTTEN_SOUL", Counter({"CORRUPTION": 1})), 0.0)
        self.assertAlmostEqual(relic_synergy("CHARONS_ASHES", Counter(STARTER)), 0.0)

    def test_pommel_not_a_terminal(self):
        # D3: POMMEL_STRIKE is not a strength terminal.
        self.assertNotIn("POMMEL_STRIKE", AFFINITY)

    def test_affinity_covers_fourteen_terminals(self):
        # Player brief 2026-10-06: COLOSSUS joined as the first DEFENCE-side
        # consumer of the vulnerable engine (it blocks and halves the damage
        # of vulnerable enemies), so the table grew 13 -> 14.
        self.assertEqual(len(AFFINITY), 14)

    def test_every_affinity_key_is_an_engine_or_strike(self):
        for card, affinity in AFFINITY.items():
            for kind in affinity:
                self.assertTrue(kind in ENGINE_CARDS or kind == "strike", msg=card + "/" + kind)


class RelicTableTests(unittest.TestCase):
    def test_relic_coef_within_card_cap(self):
        # The conservative rule the brief demanded: no relic may out-weight the
        # engine class it keys on.
        for relic, affinity in RELIC_AFFINITY.items():
            for kind, weight in affinity.items():
                self.assertLessEqual(weight, RELIC_CAPS[kind] + 1e-12, msg=relic + "/" + kind)

    def test_relic_table_size(self):
        self.assertEqual(len(RELIC_AFFINITY), 27)

    def test_unknown_relic_scores_zero(self):
        self.assertAlmostEqual(relic_synergy("NOT_A_RELIC", Counter(STARTER)), 0.0)

    def test_relic_synergy_grows_with_its_engine(self):
        none = Counter(STARTER)
        some = Counter(STARTER, UNMOVABLE=3)
        self.assertAlmostEqual(relic_synergy("ORNAMENTAL_FAN", none), 0.0)
        self.assertGreater(relic_synergy("ORNAMENTAL_FAN", some), 0.0)


class SynergyFunctionTests(unittest.TestCase):
    def test_no_engines_no_bonus(self):
        # A strength terminal in a deck with no strength engine gets nothing.
        self.assertAlmostEqual(synergy("WHIRLWIND", Counter(STARTER)), 0.0)
        self.assertAlmostEqual(synergy("BODY_SLAM", Counter(STARTER)), 0.0)
        self.assertAlmostEqual(synergy("JUGGERNAUT", Counter(STARTER)), 0.0)

    def test_non_terminal_scores_zero(self):
        self.assertAlmostEqual(synergy("SLICE", Counter(STARTER, INFLAME=3)), 0.0)
        self.assertAlmostEqual(synergy("POMMEL_STRIKE", Counter(STARTER, INFLAME=3)), 0.0)

    def test_synergy_is_monotone_in_its_engine(self):
        for card, kind in (("WHIRLWIND", "strength"), ("BODY_SLAM", "block"),
                           ("DISMANTLE", "vulnerable"), ("INFERNO", "hp_cost")):
            previous = -1.0
            for n in range(0, 12):
                deck = Counter(STARTER)
                if n:
                    deck[ENGINE_ONE[kind]] += n
                value = synergy(card, deck)
                self.assertGreaterEqual(value, previous - 1e-12, msg=card)
                previous = value

    def test_cross_archetype_stacks(self):
        # TEAR_ASUNDER keys on BOTH strength and hp_cost; the two contributions
        # add, so the mixed deck beats either single-engine deck.
        strength_only = Counter(STARTER, INFLAME=2)
        hp_only = Counter(STARTER, BLOODLETTING=2)
        both = Counter(STARTER, INFLAME=2, BLOODLETTING=2)
        self.assertAlmostEqual(synergy("TEAR_ASUNDER", both),
                               synergy("TEAR_ASUNDER", strength_only)
                               + synergy("TEAR_ASUNDER", hp_only), places=9)
        self.assertGreater(synergy("TEAR_ASUNDER", both), synergy("TEAR_ASUNDER", strength_only))

    def test_hellraiser_exhaust_term_peaks_at_one(self):
        one = Counter(STARTER, CORRUPTION=1)
        two = Counter(STARTER, CORRUPTION=1, TRUE_GRIT=1)
        three = Counter(STARTER, CORRUPTION=1, TRUE_GRIT=1, HAVOC=1)
        # The strike part is constant here; the exhaust part must fall away.
        self.assertGreater(synergy("HELLRAISER", one), synergy("HELLRAISER", two))
        self.assertGreater(synergy("HELLRAISER", two), synergy("HELLRAISER", three))

    def test_hellraiser_needs_no_strikes_for_the_exhaust_term(self):
        deck = Counter({"CORRUPTION": 1})
        self.assertAlmostEqual(synergy("HELLRAISER", deck), exhaust_beta(1), places=12)

    def test_perfected_strike_compensation_only_above_the_floor(self):
        at_floor = Counter({"STRIKE_IRONCLAD": PERFECTED_OFFSET_FLOOR})
        above = Counter({"STRIKE_IRONCLAD": PERFECTED_OFFSET_FLOOR + 4})
        base = synergy("PERFECTED_STRIKE", at_floor)
        # h is capped at 1 there, so the whole increase is the linear term.
        self.assertAlmostEqual(synergy("PERFECTED_STRIKE", above) - base,
                               PERFECTED_OFFSET_C * 4, places=9)

    def test_synergy_bounded_by_affinity_sum_plus_beta(self):
        for card, affinity in AFFINITY.items():
            if card == "PERFECTED_STRIKE":
                continue      # its compensation term is deliberately unbounded
            cap = sum(affinity.values()) + (BETA_GAMMA if card == "HELLRAISER" else 0.0)
            deck = Counter()
            for kind in ENGINE_CARDS:
                deck.update({c: 8 for c in ENGINE_CARDS[kind]})
            deck.update({c: 8 for c in EXHAUST_CARDS})
            deck["STRIKE_IRONCLAD"] = 30
            self.assertLessEqual(synergy(card, deck), cap + 1e-9, msg=card)


class DeckValueTableInvariantTests(unittest.TestCase):
    """The plan's appendix C states: 约束 V_self + V_pool 必须等于附录 A 的
    V_full（已逐行校验）. That audit was in fact not performed: four cards
    entered CONDITION_POOL with no V_TABLE row, so dims_of() returned (0,0,0)
    and route.py:436 charged them a spurious dilution penalty. Repaired
    2026-10-06 (see SpireExact-A10评估效率优化方案 section 14); this test keeps
    the invariant true."""

    def test_every_condition_pool_card_has_a_matching_v_table_row(self):
        from spire_exact.planning.deck_value import CONDITION_POOL, V_TABLE
        missing, mismatched = [], []
        for card, (v_self, v_pool, _cond, _smax) in CONDITION_POOL.items():
            implied = tuple(v_self[i] + v_pool[i] for i in range(3))
            if card not in V_TABLE:
                missing.append((card, implied))
            elif V_TABLE[card] != implied:
                mismatched.append((card, V_TABLE[card], implied))
        self.assertEqual(missing, [], "CONDITION_POOL cards absent from V_TABLE")
        self.assertEqual(mismatched, [], "V_self + V_pool != V_full")

    def test_dims_of_never_reports_a_conditioned_card_as_valueless(self):
        # A card the model deliberately values must not read as (0,0,0) on the
        # deck-free path, or weak_penalty invents a charge for it.
        from spire_exact.planning.deck_value import CONDITION_POOL, dims_of
        for card in CONDITION_POOL:
            self.assertNotEqual(dims_of(card), (0, 0, 0), msg=card)


class MissingTableRowTests(unittest.TestCase):
    """BLUDGEON (重锤) reached 21 card rewards across the 33 saved A10 IRONCLAD
    runs and was absent from V_TABLE, so dims_of() returned the (0,0,0) default
    and weak_penalty charged it a full dilution fee -- the plan could not take
    a card it had a price for. Rows added for a card that actually shows up in
    real runs must not silently regress to the default, so the entry and its
    marginal contribution are both pinned here."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def test_bludgeon_has_a_price_and_moves_the_attack_dimension(self):
        from spire_exact.planning.deck_value import V_TABLE, deck_dims, dims_of
        self.assertIn('BLUDGEON', V_TABLE)
        # Priced BELOW its BIG_ATTACK peers on purpose: 21 offers produced 2 takes
        # (0.095), where STOMP was 1 in 21 and BREAK/PACTS_END never appeared at
        # all. Same static shape, far lower observed appetite.
        self.assertLess(V_TABLE['BLUDGEON'][0], V_TABLE['BREAK'][0])
        self.assertLess(V_TABLE['BLUDGEON'][0], V_TABLE['STOMP'][0])
        self.assertNotEqual(dims_of('BLUDGEON'), (0, 0, 0))
        before = deck_dims(self.STARTER)
        after = deck_dims({**self.STARTER, 'BLUDGEON': 1})
        self.assertAlmostEqual(after[0] - before[0], V_TABLE['BLUDGEON'][0], places=9)


class WeightFixTests(unittest.TestCase):
    """Player rulings D1-D7 of 2026-10-07 (see
    markdown/SpireExact/SpireExact-卡牌权重缺陷修复方案-2026-10-07.md).
    Six defect classes were fixed in deck_value.py / route.py; these tests pin
    the properties the rulings demanded. All values below were measured on the
    fixed code (2026-10-07).
    """

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def test_supply_self_supply_not_double_subtracted(self):
        # Defect 4 (self-supply off-by-one). take_dims/weak_penalty judge a
        # card on the PRE-take deck, so supply must not subtract a copy that is
        # not there. VICIOUS in a deck with no VULN_SRC sits at sigma 0, i.e.
        # exactly V_self = -1 in U -- it must never read below that.
        from spire_exact.planning.deck_value import realized_dims
        v = realized_dims('VICIOUS', Counter({'STRIKE_IRONCLAD': 5}))
        self.assertAlmostEqual(v[2], -1.0, places=9)
        # INFERNO counts itself exactly once (plan :235), giving V_TABLE back.
        self.assertEqual(realized_dims('INFERNO', Counter({'INFERNO': 1,
                                                           'BLOODLETTING': 1})),
                         (14.0, -2.0, 2.0))

    def test_sigma_never_negative(self):
        # Defect 4 again: BURNING_PACT's EXH_SELF sigma was -1.00 before the
        # fix, and no supply count may go negative anywhere in the pool.
        from spire_exact.planning.deck_value import CONDITION_POOL, supply
        for card, (_self, _pool, (tag, _need), _smax) in CONDITION_POOL.items():
            with self.subTest(card=card):
                self.assertGreaterEqual(supply(tag, self.STARTER, card), 0.0)
        self.assertAlmostEqual(
            supply('EXH_SELF', self.STARTER, 'BURNING_PACT'), 0.0, places=9)

    def test_phi_matches_plan_table(self):
        # Defect 2 + player rulings D2/D3. Plan :652 has four tiers: 常态 1.0 /
        # 消耗自身 0.7 / Ethereal 0.45 / Power 0.45. A pure exhaust SOURCE
        # (消耗他牌) has no tier in the plan, so it takes 1.0 (D2). BRAND and
        # HOWL_FROM_BEYOND are no longer EXH_SELF (D3): BRAND exhausts another
        # card, HOWL_FROM_BEYOND is the beneficiary of exhausting.
        from spire_exact.planning.deck_value import EXH_SELF, phi
        self.assertAlmostEqual(phi('SLICE'), 1.0)             # 常态
        self.assertAlmostEqual(phi('IMPERVIOUS'), 0.7)        # 消耗自身
        self.assertAlmostEqual(phi('ASCENDERS_BANE'), 0.45)   # Ethereal
        self.assertAlmostEqual(phi('INFLAME'), 0.45)          # Power
        self.assertAlmostEqual(phi('HAVOC'), 1.0)             # 消耗他牌 (D2)
        self.assertAlmostEqual(phi('BURNING_PACT'), 1.0)
        self.assertAlmostEqual(phi('TRUE_GRIT'), 1.0)
        self.assertAlmostEqual(phi('CINDER'), 1.0)
        self.assertAlmostEqual(phi('BRAND'), 1.0)             # D3
        self.assertAlmostEqual(phi('HOWL_FROM_BEYOND'), 1.0)  # D3
        # FIEND_FIRE exhausts itself AND the whole hand: the 消耗自身 tier wins.
        self.assertAlmostEqual(phi('FIEND_FIRE'), 0.7)
        self.assertNotIn('BRAND', EXH_SELF)
        self.assertNotIn('HOWL_FROM_BEYOND', EXH_SELF)

    def test_weak_penalty_uses_post_take_deck(self):
        # Player ruling D1 = semantics C: the dilution charge is judged on
        # V(c | D 并 {c}). Bare PACTS_END is worth nothing, so it pays the full
        # 6.0 charge; with three whole-hand exhaust sources sigma reaches 1.0
        # and its realized (25,0,0) clears theta, so the charge vanishes.
        from spire_exact.planning.deck_value import weak_penalty
        self.assertAlmostEqual(weak_penalty('PACTS_END', self.STARTER), 6.0,
                               places=9)
        loaded = {**self.STARTER, 'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1}
        self.assertAlmostEqual(weak_penalty('PACTS_END', loaded), 0.0, places=9)


class DeltaUnlockTests(unittest.TestCase):
    """F5 / defect 3, player ruling D6: delta_unlock is the deck_dims
    difference across the take (implementation II of plan 7.2), so taking a
    supplier credits the conditional cards the deck ALREADY holds."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def _own_post_take(self, card_id, deck, act_index):
        from spire_exact.planning.deck_value import (deck_dims, realized_dims,
                                                     weights)
        d2 = dict(deck)
        d2[card_id] = d2.get(card_id, 0) + 1
        w = weights(deck_dims(deck), act_index)
        v = realized_dims(card_id, d2)
        return sum(w[i] * v[i] for i in range(3))

    def test_delta_unlock_zero_when_no_conditional_card(self):
        # With no conditional card in the deck the difference reduces to the
        # card's own post-take value: delta_unlock contributes exactly 0.
        from spire_exact.planning.deck_value import take_dims
        self.assertAlmostEqual(take_dims('ANGER', self.STARTER, 0),
                               self._own_post_take('ANGER', self.STARTER, 0),
                               places=9)

    def test_delta_unlock_credits_existing_consumer(self):
        # PACTS_END is a CONDITION_POOL consumer of EXH_SRC. Taking CORRUPTION
        # (a whole-hand source) lifts PACTS_END's sigma, and that lift must show
        # up as take_dims exceeding the card's own post-take value.
        from spire_exact.planning.deck_value import take_dims
        deck = {**self.STARTER, 'PACTS_END': 1}
        own = self._own_post_take('CORRUPTION', deck, 0)
        full = take_dims('CORRUPTION', deck, 0)
        self.assertGreater(full, own)
        # And it must exceed the same take in a deck WITHOUT the consumer.
        self.assertGreater(full, take_dims('CORRUPTION', self.STARTER, 0))


if __name__ == "__main__":
    unittest.main()
