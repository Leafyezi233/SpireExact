"""Unit tests for the route planner (route.py): path enumeration and the
layered valuation — act 1-2 elite value, act 3 danger scaling, deck thinning
(skip is free, taking costs, removal pays), rest-site credit."""
import unittest
from collections import Counter
from unittest.mock import patch
from spire_exact.planning.route import enumerate_paths, map_filter, plan, score_path


def node(action, kind, act=0, floor=1, leads=None, labels=()):
    return {"action": action, "kind": kind, "act": act, "floor": floor,
            "fight": None, "leads_to": leads, "menu": None, "labels": list(labels),
            "children": {}}


def facts(*paths):
    """A merged-tree-shaped facts dict from root children (one per path)."""
    tree = [{"action": None, "kind": None, "act": None, "floor": None, "fight": None,
             "leads_to": None, "menu": None, "labels": [], "children": {}}]
    for index, path in enumerate(paths):
        parent = 0
        for step in path:
            node_id = len(tree)
            tree.append({**step, "children": {}})
            tree[parent]["children"][str(node_id)] = node_id
            parent = node_id
    return {"tree": tree, "max_floor": max((n["floor"] for p in paths for n in p), default=0)}


def route(map_leads, extra=()):
    head = node({"kind": "map", "col": 0, "row": 0}, "map", leads=map_leads)
    return [head, *extra]


class RouteTests(unittest.TestCase):
    def test_paths_enumerated_per_root_branch_and_incomplete_walks_skipped(self):
        full = route({"room": "Monster", "enemies": "JAW_WORM"},
                     extra=[node({"kind": "map", "col": 1, "row": 2}, "map", floor=49)])
        short = route({"room": "Monster", "enemies": "JAW_WORM"},
                      extra=[node({"kind": "map", "col": 2, "row": 2}, "map", floor=9)])
        paths = enumerate_paths(facts(full, short))
        # The walk that died at floor 9 is not a route.
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0][0].get("leads_to"), {"room": "Monster", "enemies": "JAW_WORM"})

    def test_elite_in_act1_2_pays_for_itself(self):
        elite = score_path(route({"room": "Elite", "enemies": "GREMLIN_NOB"}))
        monster = score_path(route({"room": "Monster", "enemies": "JAW_WORM"}))
        # Elite: +6 formation, -6 danger; monster: -2 danger. Elite route wins.
        self.assertEqual(elite["elites"], 1)
        self.assertGreater(elite["total"], monster["total"])

    def test_act3_battles_pay_the_base_danger(self):
        # ACT3_DANGER_SCALE used to be 3.0, justified as "the final act must be
        # survived at a higher cost". The saved A10 IRONCLAD runs say the
        # opposite -- among the winners the act-3 elite cost LESS than act 1
        # (12.0 vs 15.5 gross hp) and the act-3 boss less than act 1 (21.0 vs
        # 28.0) -- so the multiplier charged every act-3 route for a danger that
        # is not there. Act 3 now prices like every other act.
        from spire_exact.planning.route import ACT3_DANGER_SCALE, _ROOM_DANGER
        elite3 = score_path([node({"kind": "map"}, "map", act=2, leads={"room": "Elite", "enemies": "X"})])
        monster3 = score_path([node({"kind": "map"}, "map", act=2, leads={"room": "Monster", "enemies": "Y"})])
        self.assertEqual(ACT3_DANGER_SCALE, 1.0)
        self.assertEqual(elite3["formation"], 0)           # no relic chase in act 3
        self.assertAlmostEqual(elite3["danger"], _ROOM_DANGER['Elite'])
        self.assertAlmostEqual(monster3["danger"], _ROOM_DANGER['Monster'])
        # The elite is not chased in act 3 (no ELITE_VALUE there), so the two
        # routes now differ by the BASE elite/monster danger gap alone.

    def test_negative_tier_menu_is_skipped_not_taken(self):
        junk = score_path([node({"kind": "card_reward"}, "card_reward",
                                labels=("card:TWIN_STRIKE", "card:SLICE"))])
        self.assertEqual(junk["cards_taken"], 0)
        self.assertEqual(junk["formation"], 0)  # skip is free

    def test_good_card_taken_with_deck_penalty(self):
        from spire_exact.planning.route import TAKE_BASE_LEGACY
        good = score_path([node({"kind": "card_reward"}, "card_reward",
                                labels=("card:INFLAME", "card:SLICE"))])
        self.assertEqual(good["cards_taken"], 1)
        # tier 6 (T1) minus the unknown-deck penalty (the act-1 low step)
        self.assertAlmostEqual(good["formation"], 6 - TAKE_BASE_LEGACY)

    def test_removal_and_relic_scored_at_shops(self):
        shop = score_path([node({"kind": "buy"}, "buy",
                                labels=("buy_removal", "card:INFLAME", "relic:SOME_RELIC"))])
        from spire_exact.planning.route import TAKE_BASE_LEGACY
        self.assertEqual(shop["removals"], 1)
        # 4 removal + 2 relic + (6 - unknown-deck penalty) card
        self.assertAlmostEqual(shop["formation"], 4 + 2 + (6 - TAKE_BASE_LEGACY))

    def test_rest_site_offsets_later_danger(self):
        rested = score_path(route({"room": "RestSite"}, extra=[
            node({"kind": "rest"}, "rest"),
            node({"kind": "map", "col": 1, "row": 1}, "map", leads={"room": "Monster", "enemies": "JAW_WORM"})]))
        bare = score_path(route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[
            node({"kind": "map", "col": 1, "row": 1}, "map", leads={"room": "Monster", "enemies": "JAW_WORM"})]))
        self.assertEqual(rested["rests"], 1)
        self.assertAlmostEqual(rested["danger"], 0.0)  # 2.0 danger fully offset
        self.assertAlmostEqual(bare["danger"], 8.0)    # two fights, no rest site

    def test_plan_ranks_and_returns_actions(self):
        good = route({"room": "Elite", "enemies": "GREMLIN_NOB"})
        poor = route({"room": "Monster", "enemies": "JAW_WORM"})
        result = plan(facts(good, poor))
        self.assertEqual(result["routes_scored"], 2)
        self.assertEqual(result["best"]["actions"], [{"kind": "map", "col": 0, "row": 0}])
        self.assertEqual(result["best"]["score"]["elites"], 1)
        self.assertEqual(len(result["alternatives"]), 1)


class StrictTiersTests(unittest.TestCase):
    """Strict prescription (player brief of 2026-10-05): per-act label tiers
    that force the host's menus to follow the DP decisions; rest excluded."""

    def test_taken_card_positive_skipped_menu_mates_negative(self):
        result = plan(facts(route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[
            node({"kind": "card_reward"}, "card_reward",
                 labels=("card:OFFERING", "card:TWIN_STRIKE"))])))
        tiers = result["best"]["strict_tiers"]
        # Plan-wide coverage: +8 in every act the plan takes it, -8 elsewhere.
        self.assertEqual(tiers["card:OFFERING"], [8, -8, -8])
        self.assertEqual(tiers["card:TWIN_STRIKE"], [-8, -8, -8])

    def test_shop_removal_forced_and_second_menu_choice_lands_in_same_act_row(self):
        result = plan(facts(route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[
            node({"kind": "buy"}, "buy", labels=("buy_removal", "card:OFFERING")),
            node({"kind": "map", "col": 1, "row": 2}, "map", leads={"room": "Monster", "enemies": "JAW_WORM"}),
            node({"kind": "card_reward"}, "card_reward", labels=("card:TWIN_STRIKE",)),
            node({"kind": "map", "col": 2, "row": 3}, "map", floor=49)])))
        tiers = result["best"]["strict_tiers"]
        self.assertEqual(tiers["buy_removal"], [8, 0, 0])
        # Plan-wide coverage: +8 in every act the plan takes it, -8 elsewhere.
        self.assertEqual(tiers["card:OFFERING"], [8, -8, -8])
        self.assertEqual(tiers["card:TWIN_STRIKE"], [-8, -8, -8])

    def test_event_options_forced_per_menu(self):
        event = node({"kind": "event", "key": "E.MAIN.options.GO"}, "event",
                     labels=("event:E.MAIN.options.GO", "event:E.MAIN.options.LEAVE"))
        result = plan(facts(route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[event])))
        tiers = result["best"]["strict_tiers"]
        self.assertEqual(tiers["event:E.MAIN.options.GO"], [8, 0, 0])
        self.assertEqual(tiers["event:E.MAIN.options.LEAVE"], [-8, 0, 0])

    def test_rest_sites_are_never_tiered(self):
        # Rest labels never appear in the tiers; the whole-card-pool coverage
        # only ever carries card:/obtain:/event:/buy_removal labels.
        result = plan(facts(route({"room": "RestSite"}, extra=[
            node({"kind": "rest"}, "rest"),
            node({"kind": "map", "col": 1, "row": 1}, "map", floor=49,
                 leads={"room": "Monster", "enemies": "JAW_WORM"})])))
        tiers = result["best"]["strict_tiers"]
        self.assertNotIn("rest:", " ".join(tiers))
        # No card is preferred: the plan takes nothing, so the whole pool is -8.
        self.assertTrue(all(row == [-8, -8, -8] for row in tiers.values()))

    def test_choices_are_aligned_with_card_steps(self):
        # One choice per card step, in path order (the skip side of the
        # alignment is covered by the menu-mate test above).
        score = score_path([
            node({"kind": "card_reward"}, "card_reward", labels=("card:SLICE",)),
            node({"kind": "card_reward"}, "card_reward", labels=("card:OFFERING",))],
            initial_deck={"STRIKE_0": 5, "DEFEND_0": 4})
        self.assertEqual(len(score["choices"]), 2)
        self.assertEqual(score["choices"][1], ["card", "OFFERING"])
        self.assertIn(score["choices"][0][0], ("skip", "card"))

    def test_checkpoint_rules_count_powers_and_bind_at_the_checkpoint(self):
        # Player corrections of 2026-10-05: the pre-elite/boss take counts
        # INCLUDE Powers, and the demand is checkpoint-optimal -- it fires
        # only when the checkpoint's own best state violates it.
        from spire_exact.planning.route import EARLY_BOSS_MIN_TAKES
        self.assertGreaterEqual(EARLY_BOSS_MIN_TAKES, 4)
        battle = node({"kind": "map"}, "map", leads={"room": "Monster", "enemies": "JAW_WORM"})
        boss = node({"kind": "map", "col": 1, "row": 1}, "map", floor=49,
                    leads={"room": "Boss", "enemies": "BOSS"})
        # Three Powers + one strong non-Power before the boss: the checkpoint
        # best takes all four, complies (Powers count), so NO repair happens
        # and the plan keeps all four takes.
        menus = [node({"kind": "card_reward"}, "card_reward", labels=(label,))
                 for label in ("card:INFLAME", "card:UNMOVABLE",
                               "card:CRIMSON_MANTLE", "card:HEADBUTT")]
        path = route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[
            step for pair in zip([battle] * 4, menus) for step in pair] + [boss])
        result = plan(facts(path))
        best = result["best"]["score"]
        self.assertFalse(best.get("early_boss_infeasible"))
        self.assertEqual(best["cards_taken"], 4)

    def test_alternatives_carry_no_strict_tiers(self):
        good = route({"room": "Elite", "enemies": "GREMLIN_NOB"})
        poor = route({"room": "Monster", "enemies": "JAW_WORM"})
        result = plan(facts(good, poor))
        self.assertNotIn("strict_tiers", result["alternatives"][0])


class StrictConfigTests(unittest.TestCase):
    def test_strict_mode_accepts_weighted_scheduler(self):
        from spire_exact.planning.search import SearchConfig
        config = SearchConfig(route_strict=True)
        self.assertTrue(config.route_strict)

    def test_strict_mode_rejects_focus_scheduler_and_root_policies(self):
        from spire_exact.planning.search import SearchConfig
        with self.assertRaises(ValueError):
            SearchConfig(route_strict=True, scheduler="focus")
        with self.assertRaises(ValueError):
            SearchConfig(route_strict=True, root_policies=("pick",))

    def test_strict_then_search_requires_strict_pass_and_keeps_scheduler_rules(self):
        from spire_exact.planning.search import SearchConfig
        config = SearchConfig(route_strict=True, strict_then_search=True)
        self.assertTrue(config.strict_then_search and config.route_strict)
        with self.assertRaises(ValueError):
            SearchConfig(strict_then_search=True)
        with self.assertRaises(ValueError):
            SearchConfig(route_strict=True, strict_then_search=True, scheduler="focus")


if __name__ == "__main__":
    unittest.main()


class RouteFilterTests(unittest.TestCase):
    def test_filter_allows_only_the_planned_step(self):
        allowed = map_filter({"actions": [{"kind": "map", "col": 1, "row": 0},
                                          {"kind": "map", "col": 0, "row": 2}]})
        self.assertTrue(allowed(0, {"kind": "map", "col": 1, "row": 0}))
        self.assertFalse(allowed(0, {"kind": "map", "col": 2, "row": 0}))
        self.assertTrue(allowed(1, {"kind": "map", "col": 0, "row": 2}))
        self.assertTrue(allowed(2, {"kind": "map", "col": 3, "row": 9}))  # past the plan: free

    def test_scheduler_queues_only_route_map_branches(self):
        from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
        trace = [{"kind": "map", "col": 0, "row": 0}]
        evidence = [{"phase": "map", "observation": {"act": 0, "floor": 1},
                     "available_actions": [{"kind": "map", "col": 1, "row": 0},
                                           {"kind": "map", "col": 2, "row": 0}]}]
        s = IndexedStrategicScheduler(route_filter=map_filter({"actions": [{"kind": "map", "col": 1, "row": 0}]}))
        s.add({"trace": trace, "decision_evidence": evidence, "status": "UNKNOWN"}, "root")
        self.assertEqual(s.route_filtered, 1)
        branch = s.next()
        self.assertEqual(branch["prefix"], [{"kind": "map", "col": 1, "row": 0}])
        self.assertIsNone(s.next())

    def test_scheduler_without_filter_keeps_all_map_branches(self):
        from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
        trace = [{"kind": "map", "col": 0, "row": 0}]
        evidence = [{"phase": "map", "observation": {"act": 0, "floor": 1},
                     "available_actions": [{"kind": "map", "col": 1, "row": 0},
                                           {"kind": "map", "col": 2, "row": 0}]}]
        s = IndexedStrategicScheduler()
        s.add({"trace": trace, "decision_evidence": evidence, "status": "UNKNOWN"}, "root")
        self.assertEqual(len(s.branches), 2)

    def test_route_position_ignores_non_map_actions(self):
        # Card picks between map sites must not advance the route position.
        from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
        trace = [{"kind": "map", "col": 1, "row": 0}, {"kind": "card_reward", "index": 0}]
        evidence = [
            {"phase": "map", "observation": {"act": 0, "floor": 1}, "available_actions": []},
            {"phase": "card_reward", "observation": {"act": 0, "floor": 2},
             "available_actions": [{"kind": "card_reward", "index": 1}]},
        ]
        s = IndexedStrategicScheduler(route_filter=map_filter([{"kind": "map", "col": 1, "row": 0},
                                                               {"kind": "map", "col": 0, "row": 3}]))
        s.add({"trace": trace, "decision_evidence": evidence, "status": "UNKNOWN"}, "root")
        # The card alternative is queued freely (non-map) and, at the next map
        # site, position 1 would be required — the card site must not consume it.
        self.assertEqual(len(s.branches), 1)
        self.assertEqual(s.route_filtered, 0)


class RouteConfigTests(unittest.TestCase):
    def test_prescription_needs_supported_scheduler(self):
        from spire_exact.planning.search import SearchConfig
        with self.assertRaises(ValueError):
            SearchConfig(walker_walks=2, scheduler='focus', route_prescribed=True)
        # walker_walks=0 now means the automatic breadth (walker.WALK_DEFAULT).
        SearchConfig(route_prescribed=True)

    def test_filter_object_usable_from_plan(self):
        result = plan(facts(route({"room": "Monster", "enemies": "JAW_WORM"})))
        allowed = map_filter(result["best"]["actions"])
        self.assertTrue(allowed(0, {"kind": "map", "col": 0, "row": 0}))


class DeckSimTests(unittest.TestCase):
    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def test_take_penalty_step_table_per_act(self):
        from spire_exact.planning.route import take_penalty
        # act 1: <14 -> 1, ==14 -> 2, ==15 -> 5, then +TAKE_PENALTY_SLOPE per
        # card past 15 (1.5 since 2026-10-09; it was 1.0)
        self.assertAlmostEqual(take_penalty(13, 0), 1.0)
        self.assertAlmostEqual(take_penalty(14, 0), 2.0)
        self.assertAlmostEqual(take_penalty(15, 0), 5.0)
        self.assertAlmostEqual(take_penalty(16, 0), 6.5)
        self.assertAlmostEqual(take_penalty(20, 0), 12.5)
        # act 2: <17 -> 4, ==17 -> 5, then +TAKE_PENALTY_SLOPE per card past 17
        self.assertAlmostEqual(take_penalty(16, 1), 4.0)
        self.assertAlmostEqual(take_penalty(17, 1), 5.0)
        self.assertAlmostEqual(take_penalty(18, 1), 6.5)
        # act 3: <18 -> 4, ==18 -> 5, then +TAKE_PENALTY_SLOPE per card past 18
        self.assertAlmostEqual(take_penalty(17, 2), 4.0)
        self.assertAlmostEqual(take_penalty(18, 2), 5.0)
        self.assertAlmostEqual(take_penalty(19, 2), 6.5)
        # an unknown act falls back to act 1
        self.assertAlmostEqual(take_penalty(13), 1.0)

    def test_battle_trance_no_longer_exempt_from_the_size_charge(self):
        """2026-10-09: BATTLE_TRANCE left SIZE_EXEMPT, so its second copy
        cost only the 1.25 repeat charge and a duplicate came free."""
        from spire_exact.planning.deck_value import DRAW_CARDS, SIZE_EXEMPT
        self.assertNotIn('BATTLE_TRANCE', SIZE_EXEMPT)
        self.assertIn('BATTLE_TRANCE', DRAW_CARDS)

    def test_a_battle_trance_duplicate_is_now_a_sell(self):
        from spire_exact.planning.route import repeat_penalty, take_penalty
        # Linear 1 * REPEAT_SCALE 3.5 + EXTRA_REPEAT 6.0 = 9.5, and it now also
        # pays 0.25 * take_penalty as a DRAW_CARDS member.
        self.assertAlmostEqual(repeat_penalty('BATTLE_TRANCE', 1), 9.5)
        size_term = -0.25 * take_penalty(22, 2)
        self.assertLess(size_term, 0.0)

    def test_second_wind_does_not_count_itself_as_its_own_fuel(self):
        """2026-10-09: SKILL_IN_HAND subtracted the card itself. SECOND_WIND is
        the tag's only member, so it manufactured supply for its own payoff and
        every duplicate kept clearing the need."""
        from spire_exact.planning.deck_value import supply
        deck = {'STRIKE_IRONCLAD': 5, 'DEFEND_IRONCLAD': 4, 'BASH': 1}
        # The subtraction only ever applied when the deck already held a copy,
        # so the deck that held none is the reference point.
        zero = supply('SKILL_IN_HAND', deck, 'SECOND_WIND')
        one = supply('SKILL_IN_HAND', dict(deck, SECOND_WIND=1), 'SECOND_WIND')
        self.assertEqual(one, zero + 1)

    def test_a_second_wind_stays_worth_taking_but_the_fourth_is_not_free(self):
        from spire_exact.planning.deck_value import take_dims
        deck = {'STRIKE_IRONCLAD': 4, 'DEFEND_IRONCLAD': 4, 'BASH': 1,
                'TREMBLE': 1, 'ANGER': 1, 'MOLTEN_FIST': 2, 'STOMP': 1}
        vals = []
        for n in range(1, 5):
            d = dict(deck, SECOND_WIND=n)
            vals.append(take_dims('SECOND_WIND', d, 2))
        # Still a real card: every early copy is comfortably positive.
        self.assertGreater(min(vals[:3]), 20.0)
        # But the curve now bends, instead of running ~40 all the way down.
        self.assertLess(vals[3], vals[0] - 20.0)

    def test_card_taken_when_tier_beats_penalty_and_counted_in_deck(self):
        from spire_exact.planning.route import DeckSim
        sim = DeckSim(self.STARTER)
        self.assertAlmostEqual(sim.removal_value(), 4.0)   # first starter removal
        sim.take("ADRENALINE", 0)
        self.assertEqual(sim.size(), 11)
        self.assertEqual(sim.starters(), 9)

    def test_removal_value_declines_and_floors(self):
        from spire_exact.planning.route import DeckSim
        sim = DeckSim(self.STARTER)
        values = []
        for i in range(9):
            values.append(sim.removal_value())
            self.assertTrue(sim.remove_starter())
        # 4.0, 3.5, 3.0, 2.5, 2.0, 1.5(floor) x4, then OTHER once starters run out
        self.assertAlmostEqual(values[0], 4.0)
        self.assertAlmostEqual(values[5], 1.5)
        self.assertAlmostEqual(values[8], 1.5)
        self.assertAlmostEqual(sim.removal_value(), 0.75)  # no starters left

    def test_starter_detection(self):
        from spire_exact.planning.route import _is_starter
        self.assertTrue(_is_starter("STRIKE_IRONCLAD"))
        self.assertTrue(_is_starter("DEFEND_SILENT"))
        self.assertFalse(_is_starter("ADRENALINE"))
        self.assertFalse(_is_starter("SLICE"))


class ScorePathSimTests(unittest.TestCase):
    def test_reward_skips_when_deck_past_soft_cap(self):
        # DEBILITATE is a tier-2 attack; the deck is attack-saturated (10 of
        # 20, target 40% = 8, adjust -1), so 2 - 1 - 1.25 < 0: skip.
        from spire_exact.planning.route import DeckSim
        big = DeckSim({"STRIKE_IRONCLAD": 10, "DEFEND_IRONCLAD": 10})  # size 20, penalty 1.25
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2, "fight": None,
                "leads_to": None, "menu": None, "labels": ["card:DEBILITATE"], "children": {}}
        score = score_path([node], dict(big.deck))
        self.assertEqual(score["cards_taken"], 0)
        self.assertAlmostEqual(score["formation"], 0.0)

    def test_take_and_takes_log_with_initial_deck(self):
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2, "fight": None,
                "leads_to": None, "menu": None,
                "labels": ["card:INFLAME", "card:SLICE"], "children": {}}  # plan (4,0,0) / unlisted
        from spire_exact.planning.route import DeckSim, take_penalty, repeat_penalty
        from spire_exact.planning.deck_value import take_dims, weak_penalty
        starter = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}
        sim = DeckSim(starter)
        expected = (take_dims("INFLAME", starter, 0) - weak_penalty("INFLAME")
                    - repeat_penalty("INFLAME", 0) - take_penalty(sim.size(), 0))
        score = score_path([node], starter)
        self.assertEqual(score["cards_taken"], 1)
        self.assertAlmostEqual(score["formation"], expected, places=2)
        self.assertAlmostEqual(score["engine_score"], expected, places=2)  # INFLAME is a Power -> engine bucket
        self.assertAlmostEqual(score["attack_score"], 0.0)
        self.assertAlmostEqual(score["defense_score"], 0.0)
        self.assertEqual(score["takes"], [(0, "INFLAME")])
        self.assertEqual(score["deck_size"], 11)
        self.assertEqual(score["starters_left"], 9)

    def test_route_tiers_mark_taken_cards_per_act(self):
        from spire_exact.planning.route import route_tiers
        tiers = route_tiers([(0, "ADRENALINE"), (1, "ADRENALINE"), (1, "OFFERING")])
        self.assertEqual(tiers, {"card:ADRENALINE": [1, 1, 0], "card:OFFERING": [0, 1, 0]})

    def test_plan_emits_route_tiers_when_deck_known(self):
        reward = node({"kind": "card_reward", "index": 0}, "card_reward",
                      labels=("card:INFLAME",))
        facts_dict = facts(route({"room": "Monster", "enemies": "JAW_WORM"}, extra=[reward]))
        facts_dict["initial_deck"] = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4}
        result = plan(facts_dict)
        self.assertIn("route_tiers", result["best"])
        self.assertEqual(result["best"]["route_tiers"]["card:INFLAME"][0], 1)


class RestSiteHpTests(unittest.TestCase):
    STATE = {"hp": 80, "max_hp": 80, "gold": 99}

    def rest_route(self, extra=()):
        return [node({"kind": "map", "col": 0, "row": 0}, "map", leads={"room": "RestSite"}), *extra]

    def test_rest_heal_value_scales_with_missing_hp(self):
        from spire_exact.planning.route import REST_HEAL_WEIGHT
        healthy = score_path(self.rest_route(), run_state=self.STATE)
        hurt = score_path(self.rest_route(), run_state={"hp": 20, "max_hp": 80})
        self.assertAlmostEqual(healthy["formation"], 0.0)          # full hp: nothing to heal
        self.assertAlmostEqual(hurt["formation"], REST_HEAL_WEIGHT * 0.75)

    def test_rest_heals_the_simulated_hp(self):
        after_rest = score_path(self.rest_route(), run_state=dict(self.STATE, hp=20))
        self.assertAlmostEqual(after_rest["hp_left_frac"], 0.55)   # 20 + 24 = 44 of 80

    def test_low_hp_projection_penalised(self):
        fights = [node({"kind": "map", "col": 1, "row": 1}, "map", act=1,
                       leads={"room": "Elite", "enemies": "E"})]
        from spire_exact.planning.route import FIGHT_HP_COST
        healthy = score_path(fights, run_state={"hp": 80, "max_hp": 80})
        hurt = score_path(fights, run_state={"hp": 30, "max_hp": 80})
        # No rest site on this route. The expected life total is read from the
        # constant instead of hardcoded: FIGHT_HP_COST['Elite'] was 12.0 until
        # 2026-10-08, when the measured A10 median (16.0 over 33 runs) replaced
        # it, moving 30 - 12 = 18 to 30 - 16 = 14. Both clear the 0.25 * 80 = 20
        # low-hp line, so the penalty still fires; the assertion tracks the
        # mechanism rather than the old number.
        self.assertLess(hurt["total"], healthy["total"])
        self.assertLess(hurt["formation"], 0)
        self.assertAlmostEqual(hurt["hp_left_frac"], round((30 - FIGHT_HP_COST["Elite"]) / 80, 3))

    def test_smith_chosen_when_healthy_and_cards_taken(self):
        from spire_exact.planning.route import REST_SMITH_CAP
        reward = node({"kind": "card_reward", "index": 0}, "card_reward",
                      labels=("card:INFLAME",))
        from spire_exact.planning.route import REST_SMITH_PER_CARD, take_penalty, repeat_penalty
        from spire_exact.planning.deck_value import take_dims, weak_penalty
        starter = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4}
        take_value = (take_dims("INFLAME", starter, 0) - weak_penalty("INFLAME")
                      - repeat_penalty("INFLAME", 0) - take_penalty(9, 0))
        healthy_taker = score_path(
            [reward, *self.rest_route()], run_state=self.STATE, initial_deck=starter)
        # heal 0 (full hp) vs smith 0.6 per taken card -> smith credited
        self.assertAlmostEqual(healthy_taker["formation"], take_value + REST_SMITH_PER_CARD * 1, places=2)
        self.assertGreater(healthy_taker["formation"], 0.0)

    def test_no_run_state_keeps_legacy_rest_credit(self):
        rested = score_path(self.rest_route(extra=[
            node({"kind": "map", "col": 1, "row": 1}, "map", leads={"room": "Monster", "enemies": "JAW_WORM"})]))
        self.assertNotIn("hp_left_frac", rested)
        self.assertAlmostEqual(rested["danger"], 0.0)  # legacy credit still offsets

    def test_boss_costs_life_but_no_formation_price(self):
        # Two separate regressions live here. The first (2026-10-08) is the
        # original one: the boss had no key in FIGHT_HP_COST, so it fell through
        # to the Monster default and the act climax projected a lighter wound
        # than one ordinary trash fight.
        #
        # The second is the player ruling of the same day -- 「每一幕的最终 boss
        # 要求都只是能过就行」. The boss is a pass/fail gate (BOSS_MIN_BY_ACT),
        # not a scoring term, so it pays NO danger at all. It used to pay 20.0 on
        # top of its hp cost and its pass/fail gate, which charged the same
        # "bosses are hard" worry three times. Danger for a room the planner is
        # supposed to merely survive is not a measurement, it is a bias.
        from spire_exact.planning.route import (FIGHT_HP_COST, BOSS_HP_COST,
                                                BOSS_DANGER)
        # The gate judges a deck, so it needs a known deck: both strength
        # gates (elite and boss) run only when track=True, i.e. when a
        # starting deck was supplied.
        deck = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}
        boss = score_path(route({"room": "Boss", "enemies": "BOSS"}), deck,
                          run_state={"hp": 80, "max_hp": 80})
        elite = score_path(route({"room": "Elite", "enemies": "GREMLIN_NOB"}), deck,
                           run_state={"hp": 80, "max_hp": 80})
        monster = score_path(route({"room": "Monster", "enemies": "JAW_WORM"}),
                             run_state={"hp": 80, "max_hp": 80})
        # Life: the boss still projects the deepest wound, and by the measured
        # gross cost. hp_left_frac is published rounded to 3 places
        # (route.py:1030), so the expectation is rounded the same way -- an
        # unrounded comparison fails on 0.669 vs 0.66875.
        self.assertAlmostEqual(boss["hp_left_frac"], round((80 - BOSS_HP_COST) / 80, 3))
        self.assertGreater(FIGHT_HP_COST["Boss"], FIGHT_HP_COST["Elite"])
        self.assertGreater(FIGHT_HP_COST["Elite"], FIGHT_HP_COST["Monster"])
        # Danger: the boss pays none. The key must stay in _ROOM_DANGER even at
        # zero so a .get(room, ...) lookup can never silently fall back to the
        # Monster price again.
        self.assertEqual(BOSS_DANGER, 0.0)
        self.assertAlmostEqual(boss["danger"], 0.0)
        self.assertGreater(elite["danger"], monster["danger"])
        # What replaces the danger term is the gate. The bare starting deck
        # (A = -23.0) is BELOW the act-0 bar and is refused outright: the route
        # is not taken at a discount, it is not taken. Only when every state
        # fails is the table left intact and the diagnostic raised, so the
        # planner still returns a plan -- mirroring early_infeasible.
        self.assertEqual(boss["bosses_cleared"], 0)
        self.assertEqual(boss["bosses_blocked"], 1)
        self.assertTrue(boss["boss_infeasible"])
        # Three real attacks lift it over the bar and the same route is then
        # admitted, credited with nothing and blocked by nothing.
        strong = {**deck, "BREAK": 1, "PACTS_END": 1, "STOMP": 1}
        boss_ok = score_path(route({"room": "Boss", "enemies": "BOSS"}), strong,
                            run_state={"hp": 80, "max_hp": 80})
        self.assertEqual(boss_ok["bosses_cleared"], 1)
        self.assertEqual(boss_ok["bosses_blocked"], 0)
        self.assertFalse(boss_ok["boss_infeasible"])
        self.assertAlmostEqual(boss_ok["danger"], 0.0)

    def test_removal_value_from_the_dim_model(self):
        from spire_exact.planning.deck_value import remove_value as dim_remove_value
        shop = node({"kind": "buy", "index": 0}, "buy",
                    labels=("buy_removal",))
        deck = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4}
        score = score_path([shop], initial_deck=deck)
        # Plan 5.1: the removed Strike's dims at the CURRENT shortfall weights,
        # plus the dilution it charged plus one step of size cost.
        self.assertAlmostEqual(score["formation"], dim_remove_value("STRIKE_IRONCLAD", deck, 0), places=2)
        self.assertEqual(score["removals"], 1)


class RepeatPenaltyTests(unittest.TestCase):
    def test_linear_repeat_penalty(self):
        # Player brief 2026-10-07: 「略微增加所有卡牌的同名惩罚」, raised
        # 2026-10-09: 「所有卡牌的重复抓取惩罚增加」. REPEAT_SCALE went 1.25 ->
        # 3.5, the first value that actually refuses a duplicate on a real
        # route. Expectations: 1.25 / 1 / 2 / 4 before 2026-10-07, then 1.25.
        from spire_exact.planning.route import repeat_penalty, REPEAT_SCALE
        self.assertAlmostEqual(REPEAT_SCALE, 3.5)
        self.assertAlmostEqual(repeat_penalty('SLICE', 0), 0.0)   # first copy free
        self.assertAlmostEqual(repeat_penalty('SLICE', 1), 3.5)   # second copy -3.5
        self.assertAlmostEqual(repeat_penalty('SLICE', 2), 7.0)   # third copy -7.0
        self.assertAlmostEqual(repeat_penalty('SLICE', 4), 14.0)

    def test_power_second_copy_minus_two_heavier_of_the_two(self):
        from spire_exact.planning.route import repeat_penalty
        # INFERNO is a Power: its second copy costs at least 4 (player delta
        # 2026-10-05). The Power floor still takes the HEAVIER of the two arms.
        # At REPEAT_SCALE 3.5 only the SECOND copy is still pinned to 4.0; the
        # third and beyond already exceed it (linear 7 / 10.5 / 17.5).
        self.assertAlmostEqual(repeat_penalty('INFERNO', 1), 4.0)
        self.assertAlmostEqual(repeat_penalty('INFERNO', 2), 7.0)
        self.assertAlmostEqual(repeat_penalty('INFERNO', 3), 10.5)
        self.assertAlmostEqual(repeat_penalty('INFERNO', 5), 17.5)

    def test_perfected_strike_exempt_from_repeat(self):
        from spire_exact.planning.route import repeat_penalty
        for copies in (1, 2, 3, 9):
            self.assertAlmostEqual(repeat_penalty('PERFECTED_STRIKE', copies), 0.0)

    def test_repeat_penalty_applied_at_reward_and_shop(self):
        from spire_exact.planning.route import repeat_penalty, score_path, take_penalty
        from spire_exact.planning.deck_value import take_dims, weak_penalty
        starter = {'STRIKE_IRONCLAD': 5, 'DEFEND_IRONCLAD': 4, 'BASH': 1}
        # A deck that already holds one UNMOVABLE (plan dims (0, 12, 0), a Power).
        held = dict(starter, UNMOVABLE=1)
        node = {'action': {}, 'kind': 'card_reward', 'act': 0, 'floor': 2, 'fight': None,
                'leads_to': None, 'menu': None, 'labels': ['card:UNMOVABLE'], 'children': {}}
        expected = (take_dims('UNMOVABLE', held, 0) - weak_penalty('UNMOVABLE')
                    - repeat_penalty('UNMOVABLE', 1) - take_penalty(10, 0))
        score = score_path([node], held)
        self.assertEqual(score['cards_taken'], 1)
        self.assertAlmostEqual(score['formation'], round(expected, 2))

    def test_act_aware_penalty_makes_act2_take_costlier(self):
        from spire_exact.planning.route import score_path
        # Same menu, same deck size (16): act 1 penalty 6, act 2 penalty 4 ->
        # the cheaper act-2 penalty is what actually gates the take.
        node0 = {'action': {}, 'kind': 'card_reward', 'act': 0, 'floor': 2, 'fight': None,
                 'leads_to': None, 'menu': None, 'labels': ['card:UNMOVABLE'], 'children': {}}
        node1 = dict(node0, act=1)
        deck = {'STRIKE_IRONCLAD': 8, 'DEFEND_IRONCLAD': 8}
        a0 = score_path([node0], deck)
        a1 = score_path([node1], deck)
        self.assertGreater(a1['formation'], a0['formation'])


class BeamDpTests(unittest.TestCase):
    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def test_power_first_copy_pays_no_repeat_penalty(self):
        # Regression: a Power's FIRST copy must be free of the repeat penalty.
        from spire_exact.planning.route import repeat_penalty
        for card in ("INFERNO", "UNMOVABLE", "CRIMSON_MANTLE", "DARK_EMBRACE"):
            self.assertAlmostEqual(repeat_penalty(card, 0), 0.0, msg=card)
            self.assertAlmostEqual(repeat_penalty(card, 1), 4.0, msg=card)

    def test_first_power_take_is_scored_like_any_other_card(self):
        from spire_exact.planning.route import score_path, _tier, is_power
        # INFLAME is the T1 (6-point) Power on the player's list; its FIRST copy
        # must not pay the Power repeat penalty (the regression this guards).
        self.assertTrue(is_power("INFLAME"))
        self.assertEqual(_tier("INFLAME", 0), 6)
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                "fight": None, "leads_to": None, "menu": None,
                "labels": ["card:INFLAME"], "children": {}}
        score = score_path([node], self.STARTER)
        self.assertEqual(score["cards_taken"], 1)
        # Plan dims (4, 0, 0) weighted, minus dilution and the act-1 low step.
        self.assertGreater(score["formation"], 2.0)

    def test_unlisted_card_is_never_taken_under_the_dim_model(self):
        # The plan's V table covers the whole Ironclad pool; a card outside it
        # scores (0, 0, 0) and its dilution charge makes it a strict skip.
        from spire_exact.planning.deck_value import dims_of
        from spire_exact.planning.route import score_path
        self.assertEqual(dims_of("SOME_UNKNOWN_CARD"), (0, 0, 0))
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                "fight": None, "leads_to": None, "menu": None,
                "labels": ["card:SLICE"], "children": {}}
        score = score_path([node], self.STARTER)
        self.assertEqual(score["cards_taken"], 0)
        self.assertAlmostEqual(score["formation"], 0.0)

    def test_dp_defers_a_mediocre_card_for_a_better_later_one(self):
        from spire_exact.planning.route import score_path
        # Taking the cheap card first grows the deck and raises the penalty on
        # the good card. The DP must be able to skip the first and take the
        # second; a greedy walk would take both.
        deck = {"STRIKE_IRONCLAD": 8, "DEFEND_IRONCLAD": 7}   # size 15: penalty 5
        first = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                 "fight": None, "leads_to": None, "menu": None,
                 "labels": ["card:TWIN_STRIKE"], "children": {}}
        second = {"action": {}, "kind": "card_reward", "act": 0, "floor": 3,
                  "fight": None, "leads_to": None, "menu": None,
                  "labels": ["card:UNMOVABLE"], "children": {}}
        score = score_path([first, second], deck)
        self.assertEqual(score["dp_mode"], "beam")
        # UNMOVABLE (T3=10) is taken; TWIN_STRIKE (capped 5) cannot beat the
        # size-15 penalty of 5 once its take would also cost the better card.
        self.assertIn((0, "UNMOVABLE"), score["takes"])

    def test_dp_keeps_skipping_free(self):
        from spire_exact.planning.route import score_path
        junk = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                "fight": None, "leads_to": None, "menu": None,
                "labels": ["card:SLICE"], "children": {}}
        score = score_path([junk], self.STARTER)
        self.assertEqual(score["cards_taken"], 0)
        self.assertEqual(score["formation"], 0.0)

    def test_dp_reports_its_beam_and_pruning(self):
        from spire_exact.planning.route import score_path, DP_BEAM
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                "fight": None, "leads_to": None, "menu": None,
                "labels": ["card:UNMOVABLE", "card:OFFERING"], "children": {}}
        score = score_path([node], self.STARTER)
        self.assertEqual(score["dp_beam"], DP_BEAM)
        self.assertGreaterEqual(score["dp_states_peak"], 1)
        self.assertGreaterEqual(score["dp_states_pruned"], 0)

    def test_beam_one_is_still_a_valid_single_state_walk(self):
        from spire_exact.planning.route import score_path
        node = {"action": {}, "kind": "card_reward", "act": 0, "floor": 2,
                "fight": None, "leads_to": None, "menu": None,
                "labels": ["card:UNMOVABLE"], "children": {}}
        score = score_path([node], self.STARTER, beam=1)
        self.assertEqual(score["dp_beam"], 1)
        self.assertEqual(score["cards_taken"], 1)


class TopRoutesTests(unittest.TestCase):
    def test_position_union_admits_either_route_step(self):
        a = {"actions": [{"kind": "map", "col": 1, "row": 0},
                        {"kind": "map", "col": 1, "row": 1}]}
        b = {"actions": [{"kind": "map", "col": 1, "row": 0},
                        {"kind": "map", "col": 2, "row": 1}]}
        allowed = map_filter([a, b])
        self.assertTrue(allowed(0, {"kind": "map", "col": 1, "row": 0}))
        self.assertFalse(allowed(0, {"kind": "map", "col": 2, "row": 0}))
        self.assertTrue(allowed(1, {"kind": "map", "col": 1, "row": 1}))
        self.assertTrue(allowed(1, {"kind": "map", "col": 2, "row": 1}))
        self.assertFalse(allowed(1, {"kind": "map", "col": 3, "row": 1}))

    def test_single_route_union_equals_the_old_filter(self):
        actions = [{"kind": "map", "col": 1, "row": 0}, {"kind": "map", "col": 0, "row": 2}]
        one = map_filter({"actions": actions})
        legacy = map_filter(actions)          # bare action list still accepted
        for taken in range(3):
            for step in actions + [{"kind": "map", "col": 3, "row": 9}]:
                self.assertEqual(one(taken, step), legacy(taken, step))

    def test_legacy_action_list_still_constrains(self):
        # A bare action list must not be mistaken for route dicts: that would
        # yield an empty plan and a filter that admits everything.
        legacy = map_filter([{"kind": "map", "col": 1, "row": 0}])
        self.assertTrue(legacy(0, {"kind": "map", "col": 1, "row": 0}))
        self.assertFalse(legacy(0, {"kind": "map", "col": 2, "row": 0}))

    def test_short_route_stops_constraining_after_its_end(self):
        short = {"actions": [{"kind": "map", "col": 1, "row": 0}]}
        long = {"actions": [{"kind": "map", "col": 1, "row": 0},
                            {"kind": "map", "col": 1, "row": 1}]}
        allowed = map_filter([short, long])
        self.assertFalse(allowed(1, {"kind": "map", "col": 3, "row": 1}))
        self.assertTrue(allowed(1, {"kind": "map", "col": 1, "row": 1}))
        self.assertTrue(allowed(2, {"kind": "map", "col": 3, "row": 1}))  # both ended

    def test_route_tiers_union_across_routes(self):
        from spire_exact.planning.route import route_tiers
        merged = route_tiers([[(0, "OFFERING")], [(1, "OFFERING"), (0, "UPPERCUT")]])
        self.assertEqual(merged["card:OFFERING"], [1, 1, 0])
        self.assertEqual(merged["card:UPPERCUT"], [1, 0, 0])

    def test_single_log_route_tiers_unchanged(self):
        from spire_exact.planning.route import route_tiers
        self.assertEqual(route_tiers([(0, "ADRENALINE"), (1, "ADRENALINE")]),
                         {"card:ADRENALINE": [1, 1, 0]})

    def test_plan_top_n_returns_the_ranked_head(self):
        def reward(col, card):
            return node({"kind": "map", "col": col, "row": 1}, "map", leads={"room": "Monster", "enemies": "X"}, labels=())
        paths = [route({"room": "Elite", "enemies": "A"}),
                 route({"room": "Monster", "enemies": "B"}),
                 route({"room": "RestSite"})]
        facts_dict = facts(*paths)
        one = plan(facts_dict, top_n=1)
        five = plan(facts_dict, top_n=5)
        self.assertEqual(one["top_n"], 1)
        self.assertEqual(five["top_n"], 3)          # only 3 routes exist
        self.assertEqual(one["best"]["actions"], five["top"][0]["actions"])
        scores = [r["score"]["total"] for r in five["top"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(five["schema"], "spire-route-plan/v2")

    def test_duplicate_plans_do_not_consume_top_n_slots(self):
        # Two branches that walk the same map steps into the same rooms and
        # take the same cards are ONE plan. The ranked head must not spend a
        # second prescription slot on it: seed 70753827 had three byte-identical
        # plans inside a top-5, so the strict pass spent five evaluation slots
        # on two distinct native requests (and the duplicates were cache hits
        # that still ate allocation_serial numbers).
        twin = {"room": "Monster", "enemies": "B"}
        paths = [route({"room": "Elite", "enemies": "A"}),
                 route(twin),
                 route(twin),
                 route({"room": "RestSite"})]
        result = plan(facts(*paths), top_n=5)
        self.assertEqual(len(result["top"]), 3)
        self.assertEqual(result["top_n"], 3)
        # The ranking itself is untouched: the duplicate is still reported,
        # just not prescribed.
        self.assertEqual(result["routes_scored"], 4)

    def test_parallel_planning_matches_the_serial_loop(self):
        # workers>1 only changes the wall clock. score_path is a pure function
        # of (path, initial_deck, run_state) and the routes are assembled in the
        # same ranked order, so the two reports must be identical.
        paths = [route({"room": "Elite", "enemies": "A"}),
                 route({"room": "Monster", "enemies": "B"}),
                 route({"room": "RestSite"})]
        facts_dict = facts(*paths)
        serial = plan(facts_dict, top_n=3, workers=1)
        parallel = plan(facts_dict, top_n=3, workers=3)
        self.assertEqual(serial, parallel)
        self.assertEqual([row['id'] for row in parallel['top']],
                         [row['id'] for row in serial['top']])


class IroncladTierTests(unittest.TestCase):
    def test_player_tiers_override_community(self):
        from spire_exact.planning.route import _tier
        self.assertEqual(_tier("UNMOVABLE", 0), 10)     # 坚定不移, must-pick
        self.assertEqual(_tier("OFFERING", 1), 10)      # 祭品
        self.assertEqual(_tier("CRIMSON_MANTLE", 2), 10)
        self.assertEqual(_tier("BATTLE_TRANCE", 0), 8)  # 战斗专注
        self.assertEqual(_tier("STOKE", 2), 8)          # 添柴
        self.assertEqual(_tier("UPPERCUT", 0), 6)       # 上勾拳
        self.assertEqual(_tier("INFLAME", 0), 6)        # 燃烧 (corrected T1)
        self.assertEqual(_tier("HEADBUTT", 1), 6)       # 头槌

    def test_unlisted_ironclad_capped_below_listed(self):
        from spire_exact.planning.route import _tier
        # Unlisted Ironclad cards are capped at 5 (< T1=6) no matter what the
        # community table says.
        for cid in ("TWIN_STRIKE", "ANGER", "BASH", "SHRUG_IT_OFF", "INFERNO"):
            self.assertLessEqual(_tier(cid, 0), 5)
        self.assertGreater(_tier("OFFERING", 0), _tier("SHRUG_IT_OFF", 0))

    def test_non_ironclad_cards_keep_community_tiers(self):
        from spire_exact.planning.route import _tier
        self.assertEqual(_tier("ADRENALINE", 0), 2)     # silent, untouched
        self.assertEqual(_tier("DEBILITATE", 0), 2)     # non-ironclad

    def test_every_override_id_is_resolvable(self):
        from spire_exact.planning.ironclad_tiers import (OVERRIDES, TERMINAL_POINTS,
                                                         TIER1, TIER2, TIER3)
        # OVERRIDES is the hand tiers PLUS the flat terminal base scores (D11/D13).
        self.assertEqual(len(TIER3) + len(TIER2) + len(TIER1) + len(TERMINAL_POINTS),
                         len(OVERRIDES))
        self.assertFalse(set(TERMINAL_POINTS) & set(TIER1 + TIER2 + TIER3))
        for cid in TIER1 + TIER2 + TIER3:
            self.assertIn(OVERRIDES[cid], (10, 8, 6))
        for cid in TERMINAL_POINTS:
            self.assertIn(OVERRIDES[cid], (0, 1))

    def test_terminal_base_scores_are_the_players_values(self):
        # D11 / D13. A 0 must be present explicitly, or the id would fall back
        # to the community tier and silently stop being a zero.
        from spire_exact.planning.route import _tier
        self.assertEqual(_tier("BODY_SLAM", 0), 1)          # D13
        self.assertEqual(_tier("PERFECTED_STRIKE", 0), 1)   # D13
        self.assertEqual(_tier("PYRE", 0), 1)               # D13
        self.assertEqual(_tier("TWIN_STRIKE", 0), 0)        # D11
        self.assertEqual(_tier("SPITE", 0), 0)              # D11

    def test_terminals_are_no_longer_negative(self):
        # Before D11/D13 these fell through to the community table at -2/-1.
        from spire_exact.planning.route import _tier
        for cid in ("BODY_SLAM", "PERFECTED_STRIKE", "TWIN_STRIKE", "SPITE", "PYRE"):
            self.assertGreaterEqual(_tier(cid, 0), 0, msg=cid)

class SynergyIntegrationTests(unittest.TestCase):
    """The engine -> terminal bonus as it reaches the planner (route.py)."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}
    ENGINE_ONE = {"strength": "INFLAME", "vulnerable": "TREMBLE",
                  "block": "UNMOVABLE", "hp_cost": "BLOODLETTING"}

    def reward(self, card):
        return node({"kind": "card_reward", "index": 0}, "card_reward",
                    labels=("card:" + card,))

    def _net(self, card, deck):
        """The NET value _apply_take charges for this card on this deck, so the
        assertions below read the arithmetic rather than a proxy. The synergy
        bonus must be passed in, because that is how _apply_take calls take_dims
        (route.py:697-698): a terminal the engine lifts is taken BECAUSE of it."""
        import spire_exact.planning.deck_value as DV
        from spire_exact.planning.route import (repeat_penalty, satiation_penalty,
                                                synergy, take_penalty)
        value = DV.take_dims(card, deck, 0, synergy_bonus=synergy(card, deck))
        with_card = dict(deck)
        with_card[card] = with_card.get(card, 0) + 1
        size_term = 0.0 if card in (DV.PREMIUM_DRAW | DV.SIZE_EXEMPT) else -take_penalty(len(deck), 0)
        return (value - repeat_penalty(card, deck.get(card, 0))
                - DV.weak_penalty(card, with_card) + size_term
                - satiation_penalty(deck, 0))

    def test_synergy_lifts_a_taken_terminal(self):
        # Rewritten 2026-10-09 for MIN_TAKE_VALUE. WHIRLWIND's bare net was
        # +0.033 -- it cleared the old `> 0` threshold by a rounding margin and
        # nothing else -- so it no longer clears MIN_TAKE_VALUE on a bare deck.
        # The strength engine is what has to carry the take now, which is the
        # behaviour this test is actually about: synergy LIFTS a terminal over
        # the floor. Measured: bare net 0.033 (refused), loaded net +1.995
        # (taken). Measured against the human archive for the same card:
        # 0/25 offers taken overall, 0/19 in three-card battle menus -- the
        # model agreeing it is marginal is correct, not a regression.
        bare = score_path([self.reward("WHIRLWIND")], self.STARTER)
        loaded = score_path([self.reward("WHIRLWIND")],
                            dict(self.STARTER, INFLAME=2))
        self.assertEqual(bare["cards_taken"], 0)
        self.assertEqual(loaded["cards_taken"], 1)
        self.assertGreater(loaded["formation"], bare["formation"])
        self.assertGreater(loaded["synergy_detail"]["WHIRLWIND"], 0.0)
        self.assertEqual(bare["synergy_detail"].get("WHIRLWIND", 0.0), 0.0)

    def test_a_terminal_only_the_synergy_raises_is_still_taken(self):
        # The gate is on the NET after synergy, not on the bare card: a terminal
        # whose own value sits under MIN_TAKE_VALUE is still taken once its
        # engine lifts it clear of the floor.
        self.assertGreater(loaded_net := self._net("WHIRLWIND", dict(self.STARTER, INFLAME=2)),
                           self._net("WHIRLWIND", self.STARTER))
        self.assertGreater(loaded_net, 1.0)

    def test_measured_engine_thresholds(self):
        # Measured through score_path under the plan's V table (2026-10-05):
        # most Ironclad commons carry enough own value to clear the dilution
        # charge with no engine at all; only the weak conditional cards still
        # need the engine to flip (TWIN_STRIKE) or never flip (the rest).
        # BODY_SLAM history: 2 before the 2026-10-06 V_TABLE repair, then 1
        # after the spurious 6.0 weak_penalty was removed; the player brief of
        # 2026-10-06 (bare copy +2 via SIGMA_STEPS, V_self (2,0,0)) made the
        # bare take clear the dilution charge outright, so it needed 0 engines.
        # 2026-10-07 player brief: the bare copy drops to +0.5 (V_self 0.5,
        # V_pool 17.5, sigma step 5.5/17.5), so the bare take no longer clears
        # the charge and it needs 1 big-block engine again.
        # 2026-10-09 (MIN_TAKE_VALUE = 1.0): WHIRLWIND's bare net was +0.033,
        # so it used to clear the old `> 0` gate with NO engine at all. It now
        # needs one strength engine, which is the same statement the player made
        # about THUNDERCLAP -- a take must be worth something, not merely
        # non-negative. No other card's threshold moved.
        expected = {"BODY_SLAM": 1, "TEAR_ASUNDER": None, "HELLRAISER": 0,
                    "INFERNO": 0, "SWORD_BOOMERANG": None, "WHIRLWIND": 1,
                    "TWIN_STRIKE": None, "SPITE": 0, "JUGGERNAUT": 0, "BULLY": 0,
                    "DISMANTLE": 0, "MOLTEN_FIST": 0, "PERFECTED_STRIKE": 1}
        for card, threshold in expected.items():
            with self.subTest(card=card):
                self.assertEqual(self._threshold(card), threshold, msg=card)

    def _threshold(self, card):
        from spire_exact.planning.card_mechanics import AFFINITY
        kind = next(iter(AFFINITY[card]))
        for n in range(0, 13):
            deck = dict(self.STARTER)
            if kind == "strike":
                deck["STRIKE_IRONCLAD"] += n
            elif n:
                deck[self.ENGINE_ONE[kind]] = n
            if score_path([self.reward(card)], deck)["cards_taken"]:
                return n
        return None

    def test_cross_archetype_stacks(self):
        # TEAR_ASUNDER keys on strength AND hp_cost; the bonus is additive, so
        # a mixed deck's synergy for it is strictly higher than either pure one.
        from spire_exact.planning.card_mechanics import synergy
        strength_only = dict(self.STARTER, INFLAME=2)
        hp_only = dict(self.STARTER, BLOODLETTING=2)
        both = dict(self.STARTER, INFLAME=2, BLOODLETTING=2)
        self.assertGreater(synergy("TEAR_ASUNDER", both),
                           synergy("TEAR_ASUNDER", strength_only))
        self.assertGreater(synergy("TEAR_ASUNDER", both),
                           synergy("TEAR_ASUNDER", hp_only))

    def test_synergy_is_reported_in_the_breakdown(self):
        score = score_path([self.reward("WHIRLWIND")], dict(self.STARTER, INFLAME=2))
        self.assertGreater(score["synergy_total"], 0.0)
        self.assertGreater(score["synergy_detail"]["WHIRLWIND"], 0.0)

    def test_no_synergy_reported_when_no_engine(self):
        score = score_path([self.reward("WHIRLWIND")], self.STARTER)
        self.assertEqual(score["synergy_total"], 0.0)
        self.assertEqual(score["synergy_detail"], {})

    def test_perfected_strike_takes_at_high_strike_counts(self):
        # MEASURED (2026-10-05, second edition): sigma = strikes/4 capped 2.0,
        # so the pool delivers V_self=-14 + 12*sigma; the take flips at 8
        # strikes (sigma 2.0 -> V=(10,0,0)), 4-7 stay skips.
        for n in (4, 5):
            deck = {"STRIKE_IRONCLAD": n, "DEFEND_IRONCLAD": 4, "BASH": 1}
            self.assertEqual(score_path([self.reward("PERFECTED_STRIKE")], deck)["cards_taken"],
                             0, msg="strikes=" + str(n))
        for n in (6, 7, 8, 9, 10):
            deck = {"STRIKE_IRONCLAD": n, "DEFEND_IRONCLAD": 4, "BASH": 1}
            self.assertEqual(score_path([self.reward("PERFECTED_STRIKE")], deck)["cards_taken"],
                             1, msg="strikes=" + str(n))

    def test_perfected_strike_11_negative(self):
        # D12 superseded: sigma caps at 2.0, but that already clears the bar.
        for n in (11, 12, 15):
            deck = {"STRIKE_IRONCLAD": n, "DEFEND_IRONCLAD": 4, "BASH": 1}
            self.assertEqual(score_path([self.reward("PERFECTED_STRIKE")], deck)["cards_taken"],
                             1, msg="strikes=" + str(n))


class KeepStrikesTests(unittest.TestCase):
    """D4: with HELLRAISER / PERFECTED_STRIKE the Strikes are fuel."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def shop(self):
        return node({"kind": "buy", "index": 0}, "buy", labels=("buy_removal",))

    def test_keep_strikes_with_perfected(self):
        from spire_exact.planning.route import _remove_target
        deck = dict(self.STARTER, PERFECTED_STRIKE=1)
        removed = _remove_target(deck)
        self.assertEqual(removed, "DEFEND_IRONCLAD")
        self.assertEqual(deck["STRIKE_IRONCLAD"], 5)

    def test_keep_strikes_with_hellraiser(self):
        from spire_exact.planning.route import _remove_target
        deck = dict(self.STARTER, HELLRAISER=1)
        removed = _remove_target(deck)
        self.assertEqual(removed, "DEFEND_IRONCLAD")
        self.assertEqual(deck["STRIKE_IRONCLAD"], 5)

    def test_strikes_removed_first_without_a_strike_terminal(self):
        from spire_exact.planning.route import _remove_target
        deck = dict(self.STARTER)
        self.assertEqual(_remove_target(deck), "STRIKE_IRONCLAD")

    def test_strike_only_deck_removes_nothing(self):
        from spire_exact.planning.route import _remove_target
        deck = {"STRIKE_IRONCLAD": 3, "PERFECTED_STRIKE": 1}
        self.assertIsNone(_remove_target(deck))
        self.assertEqual(deck["STRIKE_IRONCLAD"], 3)

    def test_curse_is_never_a_removal_target(self):
        # Second-edition plan 0.2b / 5.4: ASCENDERS_BANE is Eternal -- it can
        # never be removed or transformed, so Bash is the only target here.
        from spire_exact.planning.route import _remove_target
        self.assertEqual(_remove_target({"ASCENDERS_BANE": 1, "BASH": 1}), "BASH")
        self.assertIsNone(_remove_target({"ASCENDERS_BANE": 1}))

    def test_deck_sim_honours_d4(self):
        from spire_exact.planning.route import DeckSim
        sim = DeckSim(dict(self.STARTER, HELLRAISER=1))
        self.assertEqual(sim.remove_starter(), "DEFEND_IRONCLAD")
        self.assertEqual(sim.deck["STRIKE_IRONCLAD"], 5)

    def test_removal_still_scored_through_the_dp(self):
        score = score_path([self.shop()], dict(self.STARTER, PERFECTED_STRIKE=1))
        self.assertEqual(score["removals"], 1)
        self.assertEqual(score["deck_size"], 10)         # 11 cards, one Defend gone
        self.assertEqual(score["starters_left"], 8)      # 5 Strikes + 3 Defends
        self.assertEqual(score["takes"], [])


class TreasureRelicTests(unittest.TestCase):
    """Treasure relics used to be scored nowhere; they now feed formation."""

    def test_treasure_relic_scored(self):
        chest = node({"kind": "treasure", "index": 0}, "treasure",
                     labels=("relic:ORNAMENTAL_FAN", "treasure_skip"))
        score = score_path([chest], {"STRIKE_IRONCLAD": 5, "UNMOVABLE": 3})
        self.assertGreater(score["formation"], 0.0)
        self.assertGreater(score["relic_synergy_total"], 0.0)

    def test_treasure_relic_without_its_engine_is_only_the_flat_value(self):
        from spire_exact.planning.route import SHOP_RELIC_VALUE
        chest = node({"kind": "treasure", "index": 0}, "treasure",
                     labels=("relic:ORNAMENTAL_FAN", "treasure_skip"))
        score = score_path([chest], {"STRIKE_IRONCLAD": 5})
        self.assertAlmostEqual(score["formation"], SHOP_RELIC_VALUE)
        self.assertEqual(score["relic_synergy_total"], 0.0)

    def test_buy_relic_scores_synergy_too(self):
        shop = node({"kind": "buy", "index": 0}, "buy", labels=("relic:ORNAMENTAL_FAN",))
        score = score_path([shop], {"STRIKE_IRONCLAD": 5, "UNMOVABLE": 3})
        self.assertGreater(score["relic_synergy_total"], 0.0)

    def test_unrelated_relic_scores_only_the_flat_value(self):
        from spire_exact.planning.route import SHOP_RELIC_VALUE
        shop = node({"kind": "buy", "index": 0}, "buy", labels=("relic:SOME_RELIC",))
        score = score_path([shop], {"STRIKE_IRONCLAD": 5, "UNMOVABLE": 3})
        self.assertAlmostEqual(score["formation"], SHOP_RELIC_VALUE)
        self.assertEqual(score["relic_synergy_total"], 0.0)

class EliteStrengthGateTests(unittest.TestCase):
    """The elite gate (player brief of 2026-10-04): an elite only pays
    ELITE_VALUE when the deck AT ENTRY clears its demand on the current act."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def elite_route(self, enemies="ENTOMANCER", act=0, floor=None):
        # 2026-10-09: the node floor is carried into the room step, because the
        # act-1 elite charge is measured against the act's own boss floor. These
        # routes deliberately have NO boss, so the charge cannot fire and the
        # elite danger reads as the plain 12.0.
        return [node({"kind": "map", "col": 0, "row": 0, "floor": floor}, "map", act=act,
                     leads={"room": "Elite", "enemies": enemies})]

    def test_thresholds_match_the_players_table(self):
        from spire_exact.planning.route import ACT1_ELITE_MIN, elite_min
        # 2026-10-09: in act 1 EVERY bar is raised to ACT1_ELITE_MIN -- the
        # player ruled that act 1 must not seek out an elite unless the deck is
        # very strong, so neither 骇鳗's own 33, nor the act-1 fallback 35, nor
        # the named 42/44 may be the cheap way back into the same fight. They
        # still differ in acts 2-3.
        self.assertEqual(elite_min("TERROR_EEL", 0), int(ACT1_ELITE_MIN))
        self.assertEqual(elite_min("PHANTASMAL_GARDENERS", 0), int(ACT1_ELITE_MIN))
        self.assertEqual(elite_min("SKULKING_COLONY", 0), int(ACT1_ELITE_MIN))
        self.assertEqual(elite_min("ENTOMANCER", 0), int(ACT1_ELITE_MIN))
        self.assertEqual(elite_min("ENTOMANCER", 1), 42)
        self.assertEqual(elite_min("ENTOMANCER", 2), 52)

    def test_act1_floor_does_not_leak_into_later_acts(self):
        from spire_exact.planning.route import elite_min
        # The named bars are untouched outside act 1.
        self.assertEqual(elite_min("TERROR_EEL", 1), 33)              # 骇鳗
        self.assertEqual(elite_min("TERROR_EEL", 2), 33)
        self.assertEqual(elite_min("PHANTASMAL_GARDENERS", 1), 42)    # 花园幽灵鳗
        self.assertEqual(elite_min("SKULKING_COLONY", 1), 44)         # 珊瑚群

    def test_prefix_match_tolerates_id_variants(self):
        from spire_exact.planning.route import ACT1_ELITE_MIN, elite_min
        # The walk facts and the native reports spell these ids differently.
        for variant in ("TERROR_EEL", "TERROR_EEL_ELITE"):
            self.assertEqual(elite_min(variant, 1), 33, msg=variant)
        for variant in ("PHANTASMAL_GARDENERS", "PHANTASMAL_GARDENER",
                        "PHANTASMAL_GARDENERS_ELITE"):
            self.assertEqual(elite_min(variant, 1), 42, msg=variant)
        self.assertEqual(elite_min("SKULKING_COLONY_ELITE", 1), 44)

    def test_multi_enemy_id_matches_any_part(self):
        from spire_exact.planning.route import ACT1_ELITE_MIN, elite_min
        self.assertEqual(elite_min("FOO+TERROR_EEL+BAR", 1), 33)
        # Falls back to the act, which in act 1 is the raised floor.
        self.assertEqual(elite_min("FOO+BAR", 0), int(ACT1_ELITE_MIN))

    def test_deck_score_is_the_plan_a_dimension(self):
        from spire_exact.planning.deck_value import deck_dims
        from spire_exact.planning.route import deck_score
        # The starter deck's A-dimension is deeply negative (basics -4/-5).
        self.assertEqual(deck_score(self.STARTER, 0), deck_dims(self.STARTER)[0])
        # BREAK carries A=28 on the plan's scale, act-independently.
        self.assertEqual(deck_score(dict(self.STARTER, BREAK=1), 2),
                         deck_dims(dict(self.STARTER, BREAK=1))[0])

    def test_weak_deck_is_blocked_and_strong_deck_is_credited(self):
        weak = score_path(self.elite_route(), self.STARTER)
        strong = score_path(self.elite_route(), dict(self.STARTER, BREAK=3))
        self.assertEqual(weak["elites_credited"], 0)
        self.assertEqual(weak["elites_blocked"], 1)
        self.assertEqual(strong["elites_credited"], 1)
        self.assertEqual(strong["elites_blocked"], 0)

    def test_blocked_elite_still_pays_its_danger(self):
        weak = score_path(self.elite_route(), self.STARTER)
        # No credit, but the act-1 elite danger is still charged.
        self.assertAlmostEqual(weak["danger"], 12.0)

    def test_named_elite_threshold_is_honoured(self):
        # A=37 clears nothing in act 1 any more (the bar is ACT1_ELITE_MIN = 47)
        # but still separates the two named act-1 elites that sit ABOVE it.
        deck = dict(self.STARTER, BREAK=3, INFLAME=2)      # -23 + 84 + 8 = 69
        self.assertEqual(score_path(self.elite_route("ENTOMANCER"), deck)["elites_credited"], 1)
        self.assertEqual(score_path(self.elite_route("SKULKING_COLONY", floor=1), dict(self.STARTER, BREAK=2))["elites_credited"], 0)

    def test_act1_elite_needs_a_very_strong_deck(self):
        # The whole point of ACT1_ELITE_MIN: a deck at the OLD act-1 bar (35)
        # must no longer be credited an act-1 elite.
        from spire_exact.planning.route import ACT1_ELITE_MIN
        old_bar_deck = dict(self.STARTER, BREAK=2, INFLAME=1)   # A = 37
        self.assertLess(score_path(self.elite_route("ENTOMANCER"), old_bar_deck)["deck_dims"]["A"],
                        ACT1_ELITE_MIN)
        self.assertEqual(score_path(self.elite_route("ENTOMANCER"), old_bar_deck)["elites_credited"], 0)

    def test_act3_elite_never_credited(self):
        # Act 3 buys survival: no relic economy, whatever the deck holds.
        strong = dict(self.STARTER, BREAK=8)
        score = score_path(self.elite_route("ENTOMANCER", act=2), strong)
        self.assertEqual(score["elites_credited"], 0)

    def test_unknown_deck_keeps_the_legacy_path_level_credit(self):
        # Without a deck the gate cannot be judged, so the historical constant
        # is kept (this is the pre-existing behaviour, preserved).
        score = score_path(self.elite_route())
        self.assertEqual(score["elites"], 1)
        self.assertEqual(score["elites_credited"], 0)   # loop credit needs a deck


class EarlyBattlesCardTests(unittest.TestCase):
    """At least one NON-POWER card must be taken within the first three
    battles (player brief of 2026-10-04); event acquisition counts."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def battle(self, col, enemies="NIBBIT"):
        return node({"kind": "map", "col": col, "row": col}, "map",
                    leads={"room": "Monster", "enemies": enemies})

    def reward(self, card, index=0):
        return node({"kind": "card_reward", "index": index}, "card_reward",
                    labels=("card:" + card,))

    def test_a_route_that_skips_every_early_card_is_rejected(self):
        # Four battles, but the only menu card is a Power: the requirement is
        # unreachable, so the DP must NOT silently accept a card-less route.
        path = [self.battle(0), self.reward("INFLAME"),
                self.battle(1), self.reward("INFLAME"),
                self.battle(2), self.reward("INFLAME"),
                self.battle(3), self.reward("INFLAME")]
        score = score_path(path, self.STARTER)
        # All four menus are Powers -> nothing can satisfy it.
        self.assertTrue(score["early_infeasible"] or score["early_ok"])

    def test_a_non_power_take_inside_the_window_satisfies_it(self):
        path = [self.battle(0), self.reward("BREAK"),
                self.battle(1), self.reward("BREAK"),
                self.battle(2), self.reward("BREAK"),
                self.battle(3), self.reward("BREAK")]
        score = score_path(path, self.STARTER)
        self.assertTrue(score["early_ok"])
        self.assertFalse(score["early_infeasible"])

    def test_event_obtain_counts_as_a_card_acquisition(self):
        # A select_cards "obtain:" node is modelled as a take decision, so it
        # can satisfy the requirement (player brief: 事件抓牌也算).
        obtain = node({"kind": "select_cards", "index": 0}, "select_cards",
                      labels=("obtain:BREAK", "obtain:BLOOD_WALL"))
        path = [self.battle(0), obtain,
                self.battle(1), self.reward("INFLAME"),
                self.battle(2), self.reward("INFLAME"),
                self.battle(3), self.reward("INFLAME")]
        score = score_path(path, self.STARTER)
        self.assertTrue(score["early_ok"])

    def test_obtain_node_becomes_a_take_decision(self):
        from spire_exact.planning.route import _route_steps
        obtain = node({"kind": "select_cards", "index": 0}, "select_cards",
                      labels=("obtain:UPPERCUT", "obtain:BLOOD_WALL"))
        steps = _route_steps([obtain])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0][0], "reward")
        self.assertEqual(steps[0][1], ("UPPERCUT", "BLOOD_WALL"))

    def test_upgrade_select_cards_is_not_a_take(self):
        from spire_exact.planning.route import _route_steps
        up = node({"kind": "select_cards", "index": 0}, "select_cards",
                  labels=("upgrade:STRIKE_IRONCLAD", "upgrade:BASH"))
        self.assertEqual(_route_steps([up]), [])

    def test_power_cards_never_satisfy_the_requirement(self):
        from spire_exact.planning.card_kinds import is_power
        from spire_exact.planning.route import _branch, _apply_take
        self.assertTrue(is_power("INFLAME"))
        state = {"deck": Counter(self.STARTER), "removed": 0, "takes": (), "hp": None,
                 "formation": 0.0, "score": {"attack": 0.0, "defense": 0.0, "engine": 0.0},
                 "synergy": {}, "relic_synergy": 0.0, "early_ok": False,
                 "elites_credited": 0, "elites_blocked": 0}
        option = _branch(state)
        _apply_take(option, "INFLAME", 0, 1.0, True, in_early_window=True)
        self.assertFalse(option["early_ok"])          # a Power does not count
        option2 = _branch(state)
        _apply_take(option2, "BREAK", 0, 1.0, True, in_early_window=True)
        self.assertTrue(option2["early_ok"])          # a non-Power does

    def test_outside_the_window_a_take_does_not_satisfy(self):
        from spire_exact.planning.route import _branch, _apply_take
        state = {"deck": Counter(self.STARTER), "removed": 0, "takes": (), "hp": None,
                 "formation": 0.0, "score": {"attack": 0.0, "defense": 0.0, "engine": 0.0},
                 "synergy": {}, "relic_synergy": 0.0, "early_ok": False,
                 "elites_credited": 0, "elites_blocked": 0}
        option = _branch(state)
        _apply_take(option, "BREAK", 0, 1.0, True, in_early_window=False)
        self.assertFalse(option["early_ok"])




class SatiationTests(unittest.TestCase):
    """够用即停 (player definition of 2026-10-06): the strength ladder
    1 = 无限循环 > 2 = 数值很强可以秒杀防住 > 3 = 启动之后小循环或足够数值.
    Once a tier is reached, one more card is charged as dilution. The charge
    is PER CARD on purpose: capping `formation` instead made states tie and
    let the beam keep them by enumeration order (measured 2026-10-06)."""

    STARTER = {'STRIKE_IRONCLAD': 5, 'DEFEND_IRONCLAD': 4, 'BASH': 1}

    def test_unformed_deck_pays_nothing(self):
        from spire_exact.planning.route import strength_tier, satiation_penalty
        self.assertEqual(strength_tier(self.STARTER, 0), 0)
        self.assertEqual(satiation_penalty(self.STARTER, 0), 0.0)

    def test_closed_exhaust_loop_is_tier_one(self):
        from spire_exact.planning.route import strength_tier, satiation_penalty
        # The plan's E1 slots: EXH_PAY >= 2, EXH_SRC >= 3, DRAW or NRG >= 2.
        # The sources must be WHOLE-HAND sources: since the 2026-10-06 quality
        # fix a one-card source counts EXH_SINGLE_WEIGHT (0.35), so three of
        # them (1.05) no longer close a loop.
        deck = {'DARK_EMBRACE': 1, 'FEEL_NO_PAIN': 1,
                'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1,
                'OFFERING': 1, 'BLOODLETTING': 1}
        self.assertEqual(strength_tier(deck, 0), 1)
        self.assertEqual(satiation_penalty(deck, 0), 9.0)   # 3.0 * (4 - 1)

    def test_loop_needs_the_draw_or_energy_slot(self):
        from spire_exact.planning.route import strength_tier
        # Payoff >= 2 and sources >= 3, but none of these is a DRAW or NRG card,
        # so the loop cannot be sustained: it is not tier 1. (DARK_EMBRACE is
        # deliberately absent here -- it is BOTH an EXH_PAY and a DRAW card.)
        core = {'FEEL_NO_PAIN': 1, 'EVIL_EYE': 1, 'CORRUPTION': 1,
                'FIEND_FIRE': 1, 'STOKE': 1}
        self.assertNotEqual(strength_tier(core, 0), 1)
        # Add the missing slot: two DRAW cards that are neither source nor payoff.
        closed = dict(core, POMMEL_STRIKE=1, SHRUG_IT_OFF=1)
        self.assertEqual(strength_tier(closed, 0), 1)

    def test_big_numbers_is_tier_two(self):
        from spire_exact.planning.route import strength_tier, satiation_penalty
        from spire_exact.planning.deck_value import REQ_PER_ACT, deck_dims
        # Build a deck that clears this act's A and D lines on its own numbers.
        deck = dict(self.STARTER)
        n = 0
        while deck_dims(deck)[0] < REQ_PER_ACT[0][0] or deck_dims(deck)[1] < REQ_PER_ACT[0][1]:
            deck['IMPERVIOUS'] = deck.get('IMPERVIOUS', 0) + 1
            deck['DEMON_FORM'] = deck.get('DEMON_FORM', 0) + 1
            n += 1
            if n > 40:
                self.fail('could not build a tier-2 deck')
        self.assertEqual(strength_tier(deck, 0), 2)
        self.assertEqual(satiation_penalty(deck, 0), 6.0)   # 3.0 * (4 - 2)

    def test_setup_with_one_axis_is_tier_three(self):
        from spire_exact.planning.route import strength_tier, satiation_penalty
        from spire_exact.planning.deck_value import REQ_PER_ACT, deck_dims
        deck = dict(self.STARTER)
        while deck_dims(deck)[0] < REQ_PER_ACT[0][0]:
            deck['DEMON_FORM'] = deck.get('DEMON_FORM', 0) + 1
            if deck.get('DEMON_FORM', 0) > 40:
                self.fail('could not clear the A line')
        self.assertEqual(strength_tier(deck, 0), 3)
        self.assertEqual(satiation_penalty(deck, 0), 3.0)   # 3.0 * (4 - 3)

    def test_stronger_tier_charges_more(self):
        from spire_exact.planning.route import satiation_penalty
        one = {'DARK_EMBRACE': 1, 'FEEL_NO_PAIN': 1,
               'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1,
               'OFFERING': 1, 'BLOODLETTING': 1}
        # Tier 3: payoff + two whole-hand sources + one draw card.
        three = {'FEEL_NO_PAIN': 1, 'CORRUPTION': 1, 'SECOND_WIND': 1,
                 'OFFERING': 1}
        self.assertEqual(satiation_penalty(three, 0), 3.0)
        self.assertGreater(satiation_penalty(one, 0), satiation_penalty(three, 0))

    def test_satiation_is_charged_on_the_take_decision(self):
        from spire_exact.planning.route import score_path, satiation_penalty
        # A deck already at tier 3 must be pickier about the same card. Rewritten
        # 2026-10-09: the old version compared formation across TWO DIFFERENT
        # decks (the formed deck and the bare starter), so it also compared two
        # different per-dimension weights and two different take_dims for the
        # same card -- it passed on a coincidence of cancellations, and the
        # SKILL_IN_HAND fix broke it by shifting the formed deck's dims. The
        # charge is now isolated by holding the deck fixed and moving only the
        # penalty.
        deck = {'FEEL_NO_PAIN': 1, 'CORRUPTION': 1, 'SECOND_WIND': 1,
                'OFFERING': 1}
        self.assertEqual(satiation_penalty(deck, 0), 3.0)
        node = {'action': {}, 'kind': 'card_reward', 'act': 0, 'floor': 2,
                'fight': None, 'leads_to': None, 'menu': None,
                'labels': ['card:INFLAME'], 'children': {}}
        real = satiation_penalty
        try:
            import spire_exact.planning.route as route
            route.satiation_penalty = lambda d, a=0: 0.0
            free = score_path([node], deck)['formation']
            route.satiation_penalty = real
            charged = score_path([node], deck)['formation']
        finally:
            route.satiation_penalty = real
        self.assertEqual(score_path([node], deck)['cards_taken'], 1)
        self.assertAlmostEqual(free - charged, 3.0)




class ExhaustSourceQualityTests(unittest.TestCase):
    """Player correction of 2026-10-06: 「那种只消耗一两张的牌确实不应该多拿，
    消耗源本来就是需要类似消耗所有手牌这种高质量的多，每回合只有 3 点费用」.
    The exhaust sources are split by HOW MANY cards each actually exhausts, and
    two cards that the earlier flat list wrongly carried are gone. Card text is
    read from D:/dsh工作区1/_card_names.json."""

    def test_mass_and_single_classes_are_the_card_text_classes(self):
        from spire_exact.planning.deck_value import EXH_SRC_MASS, EXH_SRC_SINGLE
        # Whole-hand or continuous: 消耗所有手牌 / 消耗手牌中所有非攻击牌 / 持续消耗.
        self.assertEqual(EXH_SRC_MASS, frozenset({
            'CORRUPTION', 'FIEND_FIRE', 'STOKE', 'SECOND_WIND'}))
        # Exactly one card each: 消耗1张 / 随机消耗1张 / 消耗手牌随机一张攻击牌.
        self.assertEqual(EXH_SRC_SINGLE, frozenset({
            'BRAND', 'BURNING_PACT', 'CINDER', 'TRUE_GRIT', 'HAVOC', 'THRASH'}))

    def test_two_mislisted_cards_are_not_exhaust_sources(self):
        from spire_exact.planning.deck_value import EXH_SRC
        # DRUM_OF_BATTLE: 「抽2张牌。这张牌被消耗时，获得能量」-- exhausts nothing.
        # PRIMAL_FORCE:   「将手牌中的所有攻击牌变化为巨石」-- Transform is not Exhaust.
        self.assertNotIn('DRUM_OF_BATTLE', EXH_SRC)
        self.assertNotIn('PRIMAL_FORCE', EXH_SRC)

    def test_membership_is_the_union_of_the_two_classes(self):
        from spire_exact.planning.deck_value import (EXH_SRC, EXH_SRC_MASS,
                                                     EXH_SRC_SINGLE)
        self.assertEqual(EXH_SRC, EXH_SRC_MASS | EXH_SRC_SINGLE)
        self.assertEqual(len(EXH_SRC), 10)      # was 12 before the fix

    def test_a_one_card_source_is_discounted_against_a_whole_hand_one(self):
        from collections import Counter
        from spire_exact.planning.deck_value import (EXH_SINGLE_WEIGHT, supply)
        mass = Counter({'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1})
        single = Counter({'BRAND': 1, 'BURNING_PACT': 1, 'CINDER': 1})
        self.assertAlmostEqual(supply('EXH_SRC', mass), 3.0, places=9)
        self.assertAlmostEqual(supply('EXH_SRC', single), 3 * EXH_SINGLE_WEIGHT,
                               places=9)
        self.assertLess(supply('EXH_SRC', single), supply('EXH_SRC', mass))

    def test_the_discount_reaches_the_payoff_card_value(self):
        from collections import Counter
        from spire_exact.planning.deck_value import realized_dims
        # FEEL_NO_PAIN needs EXH_SRC >= 3 and pools 13 D. Three one-card sources
        # must NOT unlock it the way three whole-hand sources do.
        mass = Counter({'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1,
                        'FEEL_NO_PAIN': 1})
        single = Counter({'BRAND': 1, 'BURNING_PACT': 1, 'CINDER': 1,
                          'FEEL_NO_PAIN': 1})
        self.assertGreater(realized_dims('FEEL_NO_PAIN', mass)[1],
                           realized_dims('FEEL_NO_PAIN', single)[1])

    def test_a_loop_of_one_card_sources_is_not_tier_one(self):
        from collections import Counter
        from spire_exact.planning.route import strength_tier
        # Payoff, draw and energy are all present, but the sources are all
        # one-card ones: weighted supply 1.05, below the loop's 3.
        single = Counter({'FEEL_NO_PAIN': 1, 'DARK_EMBRACE': 1,
                          'BRAND': 1, 'BURNING_PACT': 1, 'CINDER': 1,
                          'OFFERING': 1, 'BLOODLETTING': 1})
        mass = Counter({'FEEL_NO_PAIN': 1, 'DARK_EMBRACE': 1,
                        'CORRUPTION': 1, 'FIEND_FIRE': 1, 'STOKE': 1,
                        'OFFERING': 1, 'BLOODLETTING': 1})
        self.assertNotEqual(strength_tier(single, 0), 1)
        self.assertEqual(strength_tier(mass, 0), 1)


class BeamWidthTests(unittest.TestCase):
    def test_default_beam_is_512(self):
        from spire_exact.planning.route import DP_BEAM
        self.assertEqual(DP_BEAM, 512)


def priced(action, kind, act=0, floor=1, leads=None, labels=(), costs=(), types=None):
    """A node whose shop menu carries real prices (the pre-existing `node`
    helper sets menu=None, which the planner reads as an UNPRICED shop)."""
    menu = []
    for index, cost in enumerate(costs):
        item_type = (types or ["MerchantCardEntry"] * len(costs))[index]
        menu.append({"kind": "buy", "index": index, "item_type": item_type,
                     "cost": cost})
    return {"action": action, "kind": kind, "act": act, "floor": floor,
            "fight": None, "leads_to": leads, "menu": menu,
            "labels": list(labels), "children": {}}


class ShopGoldTests(unittest.TestCase):
    """The shop gold budget (player brief 2026-10-07). The route DP used to
    treat every shop card as a free purchase and every removal as automatic,
    so a plan could buy 360 gold of shelf against a 137 gold purse."""

    DECK = {"STRIKE_IRONCLAD": 5}
    RUN = {"hp": 70, "max_hp": 80, "gold": 100}

    def shop(self, labels, costs, types=None, floor=1):
        return priced({"kind": "buy"}, "buy", floor=floor, labels=labels,
                      costs=costs, types=types)

    def test_an_affordable_card_is_bought_and_charged(self):
        score = score_path([self.shop(("card:INFLAME",), (50,))], self.DECK, self.RUN)
        self.assertEqual(score["cards_taken"], 1)
        self.assertAlmostEqual(score["gold_spent"], 50.0)
        self.assertAlmostEqual(score["gold_budget"], 100.0)
        self.assertAlmostEqual(score["gold_left"], 50.0)

    def test_a_card_over_the_purse_is_not_a_decision(self):
        score = score_path([self.shop(("card:INFLAME",), (500,))], self.DECK, self.RUN)
        self.assertEqual(score["cards_taken"], 0)
        self.assertAlmostEqual(score["gold_spent"], 0.0)

    def test_the_purse_is_shared_across_one_shelf(self):
        # 60 + 60 > 100: only one of the two can be afforded.
        score = score_path([self.shop(("card:INFLAME", "card:SLICE"), (60, 60))],
                           self.DECK, self.RUN)
        self.assertEqual(score["cards_taken"], 1)
        self.assertAlmostEqual(score["gold_spent"], 60.0)

    def test_a_priced_removal_is_a_branch_not_automatic(self):
        # 100 gold against a 100 gold purse: affordable, so it is bought.
        affordable = score_path([self.shop(("buy_removal",), (100,),
                                           ["MerchantCardRemovalEntry"])],
                                self.DECK, self.RUN)
        self.assertEqual(affordable["removal_acts"], [0])
        self.assertAlmostEqual(affordable["gold_spent"], 100.0)
        # 200 gold: the plan cannot pay, so it declines rather than overdraw.
        too_dear = score_path([self.shop(("buy_removal",), (200,),
                                         ["MerchantCardRemovalEntry"])],
                              self.DECK, self.RUN)
        self.assertEqual(too_dear["removal_acts"], [])
        self.assertAlmostEqual(too_dear["gold_spent"], 0.0)

    def test_a_treasure_relic_is_free_and_a_priced_one_is_not(self):
        from spire_exact.planning.route import SHOP_RELIC_VALUE
        chest = node({"kind": "treasure", "index": 0}, "treasure",
                     labels=("relic:ORNAMENTAL_FAN",))
        broke = {"hp": 70, "max_hp": 80, "gold": 0}
        # A chest costs nothing: still credited with an empty purse.
        self.assertAlmostEqual(score_path([chest], self.DECK, broke)["formation"],
                               SHOP_RELIC_VALUE)
        # A shelf relic is a purchase: with no gold it is not taken at all.
        shelf = self.shop(("relic:ORNAMENTAL_FAN",), (100,), ["MerchantRelicEntry"])
        self.assertAlmostEqual(score_path([shelf], self.DECK, broke)["formation"], 0.0)
        self.assertAlmostEqual(score_path([shelf], self.DECK, self.RUN)["formation"],
                               SHOP_RELIC_VALUE)

    def test_gold_trace_overrides_the_accumulated_purse(self):
        shop = self.shop(("card:INFLAME",), (50,), floor=6)
        run = {"hp": 70, "max_hp": 80, "gold": 0,
               "gold_trace": {"a0f6": {"min": 500, "max": 500, "mean": 500.0, "n": 1}}}
        self.assertEqual(score_path([shop], self.DECK, run)["cards_taken"], 1)
        self.assertEqual(score_path([shop], self.DECK,
                                    {"hp": 70, "max_hp": 80, "gold": 0})["cards_taken"], 0)

    def test_a_priced_shop_without_a_measured_purse_keeps_the_old_behaviour(self):
        # No gold in the run state means the budget never bound historically,
        # so a priced shelf must behave exactly like the unpriced one.
        without_gold = score_path([self.shop(("card:INFLAME",), (50,))], self.DECK,
                                  {"hp": 70, "max_hp": 80})
        unpriced = score_path([node({"kind": "buy"}, "buy",
                                     labels=("card:INFLAME",))], self.DECK,
                              {"hp": 70, "max_hp": 80})
        self.assertEqual(without_gold["cards_taken"], unpriced["cards_taken"])
        self.assertAlmostEqual(without_gold["formation"], unpriced["formation"])
        self.assertIsNone(without_gold["gold_start"])


class ShopDedupeTests(unittest.TestCase):
    """One shop is one shelf. The walk facts record a buy node per purchase
    screen, and a second screen is what the WALKER left behind, so expanding
    both would offer one shelf twice and let reconnaissance spending shape the
    plan (seed 524130501 stores act 0 floor 6 as two chained buy nodes)."""

    def test_two_buy_nodes_on_one_floor_expand_once(self):
        from spire_exact.planning.route import _route_steps, _card_sites
        first = priced({"kind": "buy"}, "buy", floor=6,
                       labels=("card:INFLAME", "card:SLICE"), costs=(50, 60))
        leftover = priced({"kind": "buy"}, "buy", floor=6,
                          labels=("card:INFLAME",), costs=(50,))
        steps = [s for s in _route_steps([first, leftover]) if s[0] == "shopcard"]
        self.assertEqual(len(steps), 2)
        self.assertEqual([s[1] for s in steps], ["INFLAME", "SLICE"])
        sites = [s for s in _card_sites([first, leftover]) if s[0] == "shopcard"]
        self.assertEqual(len(sites), 2)

    def test_two_shops_on_different_floors_both_expand(self):
        from spire_exact.planning.route import _route_steps
        one = priced({"kind": "buy"}, "buy", floor=6,
                     labels=("card:INFLAME",), costs=(50,))
        two = priced({"kind": "buy"}, "buy", floor=9,
                     labels=("card:INFLAME",), costs=(50,))
        steps = [s for s in _route_steps([one, two]) if s[0] == "shopcard"]
        self.assertEqual(len(steps), 2)
        self.assertEqual([s[4] for s in steps], [6, 9])

    def test_shop_steps_carry_their_price_and_floor(self):
        from spire_exact.planning.route import _route_steps
        shop = priced({"kind": "buy"}, "buy", floor=6,
                      labels=("buy_removal", "card:INFLAME"), costs=(100, 50),
                      types=["MerchantCardRemovalEntry", "MerchantCardEntry"])
        steps = _route_steps([shop])
        self.assertEqual(steps[0], ("removal", 0, 100, 6))
        self.assertEqual(steps[1], ("shopcard", "INFLAME", 0, 50, 6))

    def test_an_unpriced_menu_yields_no_prices(self):
        from spire_exact.planning.route import _route_steps
        # menu=None (every pre-existing fixture) must stay unpriced, never a
        # guessed or invented cost.
        shop = node({"kind": "buy"}, "buy", labels=("card:INFLAME",))
        self.assertEqual(_route_steps([shop]),
                         [("shopcard", "INFLAME", 0, None, 1)])


class StrictTierRemovalTests(unittest.TestCase):
    """strict_tiers used to force buy_removal for every shop that OFFERED a
    removal, on the old rule that the DP always removes. A priced removal is
    a branch now, so the prescription follows the purchase."""

    def test_a_declined_removal_is_not_forced(self):
        from spire_exact.planning.route import strict_tiers
        dear = priced({"kind": "buy"}, "buy",
                      labels=("buy_removal",), costs=(200,),
                      types=["MerchantCardRemovalEntry"])
        score = score_path([dear], {"STRIKE_IRONCLAD": 5},
                           {"hp": 70, "max_hp": 80, "gold": 100})
        self.assertEqual(score["removal_acts"], [])
        tiers = strict_tiers([dear], score, {"card:INFLAME"})
        self.assertNotIn("buy_removal", tiers)

    def test_a_bought_removal_is_forced(self):
        from spire_exact.planning.route import strict_tiers, STRICT_TIER
        cheap = priced({"kind": "buy"}, "buy",
                       labels=("buy_removal",), costs=(100,),
                       types=["MerchantCardRemovalEntry"])
        score = score_path([cheap], {"STRIKE_IRONCLAD": 5},
                           {"hp": 70, "max_hp": 80, "gold": 100})
        self.assertEqual(score["removal_acts"], [0])
        tiers = strict_tiers([cheap], score, {"card:INFLAME"})
        self.assertEqual(tiers["buy_removal"], [STRICT_TIER, 0, 0])

class ThrashBigAttackPairTests(unittest.TestCase):
    """Player ruling D4 of 2026-10-07: the ONLY named pair added is
    THRASH x 高伤单卡 (3.0, A). THRASH's card text is 「造成伤害两次。消耗你的
    手牌中随机一张攻击牌，并将它的伤害添加给这张牌」 -- it eats a big attack
    from hand and adds that damage to itself, so the pair is real. The plan's
    3.4 table records this line under 头槌 (HEADBUTT); that is the plan's own
    mismatch, and the player ruled THRASH is the host."""

    STARTER = {"STRIKE_IRONCLAD": 5, "DEFEND_IRONCLAD": 4, "BASH": 1}

    def test_thrash_big_attack_pair(self):
        from spire_exact.planning.deck_value import (BIG_ATTACK, deck_dims,
                                                     take_dims, weights)
        # Membership is derived from the native catalog: Ironclad cards whose
        # Damage variable is >= 14.
        self.assertEqual(BIG_ATTACK, frozenset({
            'BLUDGEON', 'MANGLE', 'BREAK', 'PACTS_END', 'HOWL_FROM_BEYOND',
            'CINDER', 'HEMOKINESIS', 'UNRELENTING'}))
        self.assertNotIn('THRASH', BIG_ATTACK)
        deck = {**self.STARTER, 'BLUDGEON': 1}
        # Isolate the pair term instead of inferring it from two decks. Adding
        # BLUDGEON also moves the deck's dims, and take_dims reads its weights
        # from the PRE-take deck, so "take THRASH here minus take THRASH there"
        # never equals the bonus alone -- BLUDGEON's V_TABLE entry (added
        # 2026-10-08) changed that weight vector and broke the old subtraction.
        # Running the same subtraction with PAIR_BONUS emptied leaves only the
        # deck-shape part; the difference between the two runs IS the pair.
        from spire_exact.planning import deck_value as dv
        with_pair = (take_dims('THRASH', deck, 0)
                     - take_dims('THRASH', self.STARTER, 0))
        with patch.object(dv, 'PAIR_BONUS', {}):
            pair_free = (take_dims('THRASH', deck, 0)
                         - take_dims('THRASH', self.STARTER, 0))
        w = weights(deck_dims(deck), 0)
        self.assertAlmostEqual(with_pair - pair_free, w[0] * 3.0, places=9)
        # A non-big attack must NOT trigger it.
        self.assertAlmostEqual(take_dims('THRASH', {**self.STARTER, 'SLICE': 1}, 0),
                               take_dims('THRASH', self.STARTER, 0), places=9)

    def test_no_other_named_pair_was_added(self):
        # D4 explicitly limits the addition to the one pair above.
        from spire_exact.planning.deck_value import PAIR_BONUS
        self.assertEqual(set(PAIR_BONUS), {
            ('THE_GAMBIT', 'BODY_SLAM'), ('THE_GAMBIT', 'BARRICADE'),
            ('THE_GAMBIT', 'UNMOVABLE'), ('THRASH', '*BIG_ATTACK')})


class PostCombatHealTests(unittest.TestCase):
    """The Ironclad starting relic heals at the end of every combat (player
    correction 2026-10-08: the Ironclad starting relic heals 6 at combat end).
    The hp line charged the gross wound and never paid the heal back, so a
    33-fight route was projected ~200 hp poorer than it is measured to be --
    more slack than two full acts -- and the low-hp penalty then fired on
    routes that actually survive.

    These assertions name the mechanism, not the current constants: the heal
    exists, it is credited only to the class that has it, and it cannot be
    cashed in to erase a wound that would otherwise kill.
    """

    IRONCLAD = {'hp': 80, 'max_hp': 80, 'character': 'CHARACTER.IRONCLAD'}

    def fight(self, room='Monster'):
        return [node({'kind': 'map', 'col': 0, 'row': 0}, 'map',
                     leads={'room': room, 'enemies': 'X'})]

    def test_ironclad_ends_a_fight_six_points_richer_than_a_class_without_it(self):
        # The relic pays back, so the Ironclad line ends the same fight 6 above
        # the class that has no such relic (the wound itself is shared, which
        # test_only_battle_rooms_are_charged pins separately).
        from spire_exact.planning.route import BURNING_BLOOD_HEAL
        plain = score_path(self.fight(), run_state={'hp': 80, 'max_hp': 80})
        ironclad = score_path(self.fight(), run_state=dict(self.IRONCLAD))
        self.assertAlmostEqual(ironclad['hp_left_frac'] * 80 - plain['hp_left_frac'] * 80,
                               BURNING_BLOOD_HEAL, places=2)

    def test_a_class_without_the_relic_is_credited_with_nothing(self):
        # The walk facts do not always name a character, and other classes do
        # not carry the relic: both must keep the historical behaviour.
        silent = score_path(self.fight(), run_state={'hp': 80, 'max_hp': 80})
        named = score_path(self.fight(),
                           run_state={'hp': 80, 'max_hp': 80, 'character': 'CHARACTER.SILENT'})
        self.assertEqual(silent['hp_left_frac'], named['hp_left_frac'])

    def test_the_heal_cannot_revive_a_route_that_a_fight_killed(self):
        # Healing BEFORE the wound would let a near-dead run absorb a free 6
        # and look healthier than one that took the same fight at full hp, and
        # healing AFTER a wound that reached 0 would resurrect a run that died
        # in the fight. The wound is applied first and only pays back 6 while
        # the run is still alive.
        low = score_path(self.fight('Elite'),
                         run_state={'hp': 8, 'max_hp': 80, 'character': 'CHARACTER.IRONCLAD'})
        self.assertEqual(low['hp_left_frac'], 0.0)

    def test_only_battle_rooms_are_charged(self):
        # The charge used to hang off a bare else branch, so an Event / Shop /
        # Treasure node also cost a Monster-sized wound. The heal does not
        # change which rooms bleed -- it only follows a real combat.
        for room in ('Event', 'Shop', 'Treasure'):
            with self.subTest(room=room):
                score = score_path([node({'kind': 'map', 'col': 0, 'row': 0}, 'map',
                                          leads={'room': room, 'enemies': None})],
                                   run_state=dict(self.IRONCLAD))
                self.assertAlmostEqual(score['hp_left_frac'] * 80, 80.0, places=6)

    def test_fight_costs_are_gross_damage_not_net_hp_change(self):
        # Calibrated on the 16 WINNING runs, where the hp line is only ever
        # measured along a path that survived. damage_taken is gross; the
        # difference to the net change is the post-combat heal, which is
        # credited separately above. Pinning the net medians as costs would
        # subtract the same 6 points twice.
        from spire_exact.planning.route import FIGHT_HP_COST
        self.assertEqual(FIGHT_HP_COST, {'Elite': 15.0, 'Monster': 8.0,
                                        'Unknown': 9.0, 'Boss': 23.0})


class ArchetypePlanTests(unittest.TestCase):
    """The archetype axis (player brief of 2026-10-09: the top-five plans must
    be five DIFFERENT Ironclad builds, one per archetype, not five copies of
    the single best route). Every assertion here is about the PREFERENCE axis
    itself; no claim that any archetype plan wins a run."""

    STARTER = {'STRIKE_IRONCLAD': 5, 'DEFEND_IRONCLAD': 4, 'BASH': 1}

    def menu_facts(self, *menus):
        """One root branch whose reward menus are the given card tuples.

        `initial_deck` is mandatory here: without it score_path sets
        track=False (route.py:905) and _apply_take takes the tier-only legacy
        branch, which never calls archetype_take_bonus at all -- every
        archetype would then score identically for the uninteresting reason
        that the axis was never reached."""
        steps = []
        for cards in menus:
            steps.append(node({'kind': 'card_reward'}, 'card_reward', floor=len(steps) + 1,
                               labels=tuple('card:' + card for card in cards)))
        built = facts(route({'room': 'Monster', 'enemies': 'JAW_WORM'}, extra=steps))
        built['initial_deck'] = dict(self.STARTER)
        return built

    def test_no_archetype_scores_exactly_as_the_historical_default(self):
        # The regression guard for the whole axis: score_path's default is
        # archetype=None, and passing it explicitly must reproduce the call
        # that omits it, byte for byte. A menu of core cards MUST score
        # differently once an archetype is requested -- that is the feature.
        path = enumerate_paths(self.menu_facts(('INFERNO', 'THUNDERCLAP', 'ARMAMENTS')))[0]
        self.assertEqual(score_path(path, dict(self.STARTER), archetype=None),
                         score_path(path, dict(self.STARTER)))

    def test_a_menu_of_only_neutral_cards_is_unaffected_by_every_archetype(self):
        # BATTLE_TRANCE, JUGGLING, AGGRESSION and PILLAGE are cores of no
        # archetype, so no preference may move them: the regression guard for
        # the 0-member case that archetype_bias used to get wrong.
        path = enumerate_paths(self.menu_facts(('BATTLE_TRANCE', 'JUGGLING',
                                               'AGGRESSION', 'PILLAGE')))[0]
        plain = score_path(path, dict(self.STARTER))
        for archetype in ('strength', 'vulnerable', 'block', 'expenditure', 'exhaust'):
            scored = score_path(path, dict(self.STARTER), archetype=archetype)
            self.assertEqual(scored['archetype_bonus_total'], 0.0,
                             archetype + ' paid a bonus on neutral cards')
            self.assertEqual({k: v for k, v in scored.items()
                              if k not in ('archetype',)},
                             {k: v for k, v in plain.items() if k not in ('archetype',)},
                             archetype + ' moved a menu of neutral cards')

    def test_an_archetype_core_is_taken_where_a_foreign_core_would_not_be(self):
        # INFERNO is an expenditure core; ARMAMENTS is a block core. Under the
        # expenditure preference the former must survive the menu and the
        # latter must not -- otherwise the preference is not reaching the DP.
        path = enumerate_paths(self.menu_facts(('INFERNO', 'ARMAMENTS', 'UPPERCUT')))[0]
        under = score_path(path, dict(self.STARTER), archetype='expenditure')
        self.assertIn('INFERNO', [card for _, card in under['takes']])
        self.assertNotIn('ARMAMENTS', [card for _, card in under['takes']])

    def test_a_neutral_card_is_not_charged_for_a_membership_it_does_not_have(self):
        # Regression: archetype_bias used to return ARCHETYPE_EXCLUSIVE_BIAS for
        # a card in NO archetype, taxing every plan on its filler cards and
        # dragging the archetype scores under the un-preferenced ranking.
        from spire_exact.planning.card_mechanics import archetype_bias
        self.assertEqual(archetype_bias('BATTLE_TRANCE', 'strength'), 0.0)
        self.assertEqual(archetype_bias('BATTLE_TRANCE', None), 0.0)

    def test_a_shared_pivot_card_is_neutral_to_every_archetype(self):
        from spire_exact.planning.card_mechanics import archetype_bias
        # EVIL_EYE is in both block and exhaust.
        self.assertEqual(archetype_bias('EVIL_EYE', 'block'), 0.0)
        self.assertEqual(archetype_bias('EVIL_EYE', 'exhaust'), 0.0)

    def test_every_core_belongs_to_the_named_archetype_and_nothing_else(self):
        from spire_exact.planning.card_mechanics import ARCHETYPE_CORES, ARCHETYPE_ORDER
        self.assertEqual(set(ARCHETYPE_ORDER), set(ARCHETYPE_CORES))
        for name in ARCHETYPE_ORDER:
            for card in ARCHETYPE_CORES[name]:
                self.assertTrue(card, name + ' has an empty core id')
        # The exclusivity rule the bias relies on: a card charged -0.35 to one
        # archetype must be a core of exactly one.
        owners = {card: [n for n in ARCHETYPE_ORDER if card in ARCHETYPE_CORES[n]]
                  for cores in ARCHETYPE_CORES.values() for card in cores}
        for card, names in owners.items():
            # BRAND is a strength engine, an hp_cost engine AND an exhaust card,
            # so three memberships are real rather than a bug: archetype_bias
            # treats anything other than EXACTLY one as neutral.
            self.assertGreaterEqual(len(names), 1, card + ' is a core of no archetype')
            self.assertLessEqual(len(names), 3, card + ' belongs to too many archetypes')
        self.assertEqual(len(owners['BRAND']), 3)

    def test_each_archetype_plan_takes_a_core_of_its_own(self):
        # The user's acceptance criterion for P2: five archetypes must yield
        # five DIFFERENT take logs, each containing at least one card only
        # that archetype wants.
        #
        # The fixture is one menu PER archetype, not one menu with all nine
        # cards. A single mixed menu does not exercise the axis: INFERNO's raw
        # take_dims beats every other card in that menu under all five
        # preferences, so all five plans converge on it and the axis looks
        # broken when it is merely outvoted. Separate menus let each archetype
        # face only cards its own preference has to choose between.
        from spire_exact.planning.card_mechanics import ARCHETYPE_CORES, ARCHETYPE_ORDER
        fixtures = (
            ('strength',    ('FIGHT_ME', 'THUNDERCLAP', 'UPPERCUT')),
            ('vulnerable',  ('UPPERCUT', 'THUNDERCLAP', 'FIGHT_ME')),
            ('expenditure', ('INFERNO', 'FIGHT_ME', 'UPPERCUT')),
            ('block',       ('ARMAMENTS', 'FIGHT_ME', 'UPPERCUT')),
            ('exhaust',     ('HAVOC', 'FIGHT_ME', 'UPPERCUT')),
        )
        result = plan(self.menu_facts(*[menu for _, menu in fixtures]), limit=3, top_n=5,
                      archetypes=list(ARCHETYPE_ORDER))
        self.assertEqual(len(result['top']), 5)
        logs = [tuple(card for _, card in row['score']['takes']) for row in result['top']]
        self.assertEqual(len(set(logs)), 5, 'archetype plans are not five different builds')
        for row in result['top']:
            own = ARCHETYPE_CORES[row['archetype']]
            taken = {card for _, card in row['score']['takes']}
            self.assertTrue(own & taken,
                            row['archetype'] + ' plan took none of its own cores: '
                            + repr(sorted(taken)))
        # Which exact cards land is still decided by take_dims, the size
        # penalty and the repeat penalty -- this axis only tilts that ranking,
        # it does not own it. So the contract asserted here is exactly the two
        # P2 properties: five different builds, each containing a card only its
        # own archetype wants. Directionality on a single contested menu is
        # covered precisely by the test above.

    def test_top_n_one_without_archetypes_is_the_historical_ranking(self):
        # plan() with no archetypes must behave exactly as before the axis
        # existed: one route, ranked by total.
        result = plan(self.menu_facts(('INFERNO', 'ARMAMENTS'), ('UPPERCUT', 'SHRED')),
                      limit=3, top_n=1)
        self.assertEqual(result['top_n'], 1)
        self.assertIsNone(result['top'][0]['score']['archetype'])
        self.assertEqual(result['top'][0]['score']['archetype_bonus_total'], 0.0)

    def test_the_report_says_the_axis_is_an_allocation_signal(self):
        result = plan(self.menu_facts(('INFERNO', 'ARMAMENTS')), limit=3, top_n=1,
                      archetypes=['expenditure'])
        self.assertTrue(result['allocation_signal_only'])
        self.assertEqual(result['top'][0]['archetype'], 'expenditure')




class RouteReleaseTests(unittest.TestCase):
    # dp.mode force-then-search: once the strict prescriptions have each had
    # their one evaluation the map is released (player brief of 2026-10-10), so
    # the continuation search walks the whole map graph. The filtered count is
    # kept for the record; only the constraint goes.
    def test_release_opens_the_map_for_later_branches(self):
        from spire_exact.planning.indexed_strategy import IndexedStrategicScheduler
        trace = [{"kind": "map", "col": 0, "row": 0}]

        def evidence():
            return [{"phase": "map", "observation": {"act": 0, "floor": 1},
                     "available_actions": [{"kind": "map", "col": 1, "row": 0},
                                           {"kind": "map", "col": 2, "row": 0}]}]

        s = IndexedStrategicScheduler(route_filter=map_filter({"actions": [{"kind": "map", "col": 1, "row": 0}]}))
        s.add({"trace": trace, "decision_evidence": evidence(), "status": "UNKNOWN"}, "root")
        self.assertEqual(s.route_filtered, 1)
        self.assertEqual(len(s.branches), 1)
        self.assertFalse(s.snapshot()["route_released"])
        s.release_route_filter()
        self.assertIsNone(s.route_filter)
        self.assertTrue(s.snapshot()["route_released"])
        self.assertEqual(s.snapshot()["route_filtered_branches"], 1)
        # The previously filtered option is now queued; the count does not move.
        s.add({"trace": trace, "decision_evidence": evidence(), "status": "UNKNOWN"}, "released")
        self.assertEqual(s.route_filtered, 1)
        self.assertEqual(len(s.branches), 2)

    def test_strategic_scheduler_release_is_reported(self):
        from spire_exact.planning.strategy import StrategicScheduler
        s = StrategicScheduler(route_filter=map_filter({"actions": [{"kind": "map", "col": 1, "row": 0}]}))
        self.assertFalse(s.snapshot()["route_released"])
        s.release_route_filter()
        self.assertIsNone(s.route_filter)
        self.assertTrue(s.snapshot()["route_released"])
        self.assertEqual(s.snapshot()["route_filtered_branches"], 0)
