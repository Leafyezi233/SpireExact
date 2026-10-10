"""Unit tests for the skirmish option-tree walker (walker.py)."""
import unittest
from spire_exact.mode1 import context
from spire_exact.planning.search import SearchConfig
from spire_exact.planning.walker import merge_walks, strategic_rows, walk_request


def row(index, kind, action, act, floor, room=None, menu=None, labels=(), fight=None, gold=None):
    return {"index": index, "kind": kind, "action": action, "act": act, "floor": floor,
            "room": room, "menu": menu, "labels": list(labels), "entered_fight": fight,
            "gold": gold}


def decision(golds=()):
    """A minimal walk decision: one strategic evidence row per gold, against
    an otherwise identical trace."""
    trace = []
    evidence = []
    for index, ((act, floor), gold) in enumerate(golds):
        kind = "map" if index % 2 == 0 else "card_reward"
        trace.append({"kind": kind, "col": index, "row": 0})
        evidence.append({"observation": {"act": act, "floor": floor, "gold": gold},
                         "available_actions": [{"kind": kind, "index": index}],
                         "option_labels": [[f"{kind}:X"]]})
    return {"decision_evidence": evidence, "trace": trace}


def walk_rows():
    return [
        row(0, "map", {"kind": "map", "col": 1, "row": 1}, 0, 1),
        row(5, "rewards", {"kind": "rewards", "index": 0}, 0, 2, "Monster",
            fight=(0, 2, "Monster", ("JAW_WORM",))),
        row(9, "map", {"kind": "map", "col": 0, "row": 3}, 0, 3,
            fight=(0, 2, "Monster", ("JAW_WORM",))),
        row(10, "event", {"kind": "event", "index": 0}, 0, 3),
        row(11, "map", {"kind": "map", "col": 2, "row": 5}, 0, 5),
        row(14, "rewards", {"kind": "rewards", "index": 0}, 0, 6, "Elite",
            fight=(0, 6, "Elite", ("GREMLIN_NOB",))),
        row(20, "card_reward", {"kind": "card_reward", "index": 0}, 0, 6, "Elite",
            menu=[{"kind": "card_reward", "index": 0, "card": "TWIN_STRIKE"}],
            labels=("card:TWIN_STRIKE",), fight=(0, 6, "Elite", ("GREMLIN_NOB",))),
    ]


class WalkerTests(unittest.TestCase):
    def test_map_action_is_attributed_the_fight_it_leads_into(self):
        facts = merge_walks([walk_rows()])
        # col1/row1 leads into the JAW_WORM fight; the event room between the
        # second map and the elite fight must NOT steal the attribution.
        self.assertEqual(facts["node_encounters"],
                         {"a0f2c1r1": {"JAW_WORM": 1}, "a0f6c2r5": {"GREMLIN_NOB": 1}})
        first_map = next(n for n in facts["tree"] if n["kind"] == "map" and n["action"].get("col") == 1)
        self.assertEqual(first_map["leads_to"], {"room": "Monster", "enemies": "JAW_WORM"})

    def test_second_map_gets_no_fight_when_it_leads_to_a_non_combat_room(self):
        facts = merge_walks([walk_rows()])
        second_map = next(n for n in facts["tree"] if n["kind"] == "map" and n["action"].get("col") == 0)
        self.assertIsNone(second_map.get("leads_to"))

    def test_map_into_non_combat_room_records_the_room(self):
        rows = [row(0, "map", {"kind": "map", "col": 1, "row": 1}, 0, 4),
                row(2, "rest", {"kind": "rest", "option": "Rest"}, 0, 4, "RestSite")]
        facts = merge_walks([rows])
        first_map = next(n for n in facts["tree"] if n["kind"] == "map")
        self.assertEqual(first_map["leads_to"], {"room": "RestSite"})

    def test_map_into_rest_not_attributed_the_previous_fight(self):
        # The rest decision carries the previous monster fight as stale
        # context; the map into the rest site must record the room, not that
        # fight.
        rows = [row(0, "map", {"kind": "map", "col": 0, "row": 0}, 0, 1),
                row(1, "rewards", {"kind": "rewards", "index": 0}, 0, 2, "Monster",
                    fight=(0, 2, "Monster", ("JAW_WORM",))),
                row(2, "map", {"kind": "map", "col": 1, "row": 0}, 0, 2,
                    fight=(0, 2, "Monster", ("JAW_WORM",))),
                row(3, "rest", {"kind": "rest", "option": "Rest"}, 0, 3, "RestSite",
                    fight=(0, 2, "Monster", ("JAW_WORM",)))]
        facts = merge_walks([rows])
        second_map = next(n for n in facts["tree"] if n["kind"] == "map" and n["action"].get("col") == 1)
        self.assertEqual(second_map["leads_to"], {"room": "RestSite"})

    def test_bosses_counted_once_per_walk_not_per_row(self):
        rows = [row(0, "map", {"kind": "map", "col": 2, "row": 5}, 1, 48),
                row(2, "rewards", {"kind": "rewards", "index": 0}, 1, 49, "Boss",
                    fight=(1, 49, "Boss", ("THE_BOSS",))),
                row(4, "card_reward", {"kind": "card_reward", "index": 0}, 1, 49, "Boss",
                    fight=(1, 49, "Boss", ("THE_BOSS",)))]
        facts = merge_walks([rows, rows])
        self.assertEqual(facts["bosses"], {"act1": {"THE_BOSS": 2}})

    def test_identical_paths_merge_into_one_tree_node(self):
        facts = merge_walks([walk_rows(), walk_rows()])
        self.assertEqual(len(facts["tree"]), 8)  # root + 7 strategic decisions

    def test_walk_request_is_synthetic_and_skirmished(self):
        ctx = context("123", "IRONCLAD", 10, "all")
        config = SearchConfig(walker_walks=4, solver_seed=7)
        request = walk_request(ctx, config, 0)
        self.assertTrue(request["skirmish"])
        self.assertFalse(request["capture_checkpoints"])
        self.assertNotIn("advisor", request)
        self.assertNotEqual(request["policy_seed"], walk_request(ctx, config, 1)["policy_seed"])

    def test_walk_count_defaults_to_thirty(self):
        from spire_exact.planning.walker import walk_count, WALK_DEFAULT
        self.assertEqual(WALK_DEFAULT, 30)
        # The default is a fixed reconnaissance breadth, not a multiple of the
        # worker count: the planner can only prescribe routes the walks
        # reached, so a top-5 plan needs the walks to have found 5 routes.
        self.assertEqual(walk_count(SearchConfig(walker_walks=0), 2), 30)
        self.assertEqual(walk_count(SearchConfig(walker_walks=0), 6), 30)
        self.assertEqual(walk_count(SearchConfig(walker_walks=1), 6), 30)   # still raised to the default
        self.assertEqual(walk_count(SearchConfig(walker_walks=24), 2), 30)  # below the floor -> raised
        self.assertEqual(walk_count(SearchConfig(walker_walks=80), 2), 80)  # explicit request above the floor wins
        self.assertEqual(walk_count(SearchConfig(walker_walks=0), 1), 30)   # breadth is worker-independent


class WalkerPoolUsageTests(unittest.TestCase):
    """Walks run on the pool's ordinary shared workers (player brief of
    2026-10-07). A disposable worker would force a fresh native process per
    walk, paying its ~1 s bootstrap on every one; measured 2026-10-07, that
    was 45 % of a 2.17 s walk."""

    def test_walks_do_not_request_disposable_workers(self):
        from pathlib import Path
        from spire_exact.planning import walker as walker_module

        class _Future:
            def result(self): return decision([((0, 1), 99)]), {"host_sha256": "x"}

        class _Executor:
            def __init__(self): self.calls = []
            def submit(self, fn, *args, **kwargs):
                self.calls.append(kwargs)
                return _Future()

        class _Pool:
            def __init__(self):
                self.resources = type("Resources", (), {"workers": 2})()
                self.executor = _Executor()
            def run(self, request, folder, timeout, **kwargs):
                return decision([((0, 1), 99)]), {"host_sha256": "x"}

        pool = _Pool()
        facts = walker_module.run(pool, context("123", "IRONCLAD", 10, "all"),
                                  SearchConfig(walker_walks=3, solver_seed=1), Path("unused"))
        self.assertEqual(facts["walks_requested"], 30)
        self.assertEqual(len(pool.executor.calls), 30)
        for kwargs in pool.executor.calls:
            self.assertNotIn("disposable", kwargs)


class RunStateTests(unittest.TestCase):
    """The hp line reads its inputs off the first observation that carries
    them. The character id belongs to that set because the planner pays a
    class's post-combat relic heal back out of its fight wounds (Ironclad's
    Burning Blood heals 6 at combat end); without the id every run silently
    kept the no-heal behaviour, which is right for other classes and wrong for
    this one."""

    def test_character_reaches_the_route_planner(self):
        from spire_exact.planning.walker import initial_run_state

        observed = {"decision_evidence": [{"observation": {
            "act": 0, "floor": 1, "hp": "80", "max_hp": "80", "gold": 99,
            "character": "CHARACTER.IRONCLAD"}}]}
        self.assertEqual(initial_run_state(observed)["character"],
                         "CHARACTER.IRONCLAD")

    def test_a_walk_without_the_field_is_still_usable(self):
        from spire_exact.planning.walker import initial_run_state

        # Evidence that does not name a character must stay usable: the
        # planner may use the id, it must not require it.
        plain = {"decision_evidence": [{"observation": {
            "act": 0, "floor": 1, "hp": "80", "max_hp": "80"}}]}
        self.assertEqual(initial_run_state(plain), {"hp": "80", "max_hp": "80"})


class GoldTraceTests(unittest.TestCase):
    """Gold observed per floor (spec 3.1-3.2): strategic_rows carries the
    observation gold on every row, merge_walks aggregates the gold each walk
    ENTERED a floor with, and nothing else about the merge changes."""

    def test_strategic_row_carries_the_observed_gold(self):
        rows = strategic_rows(decision([((0, 6), 137)]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gold"], 137)
        self.assertEqual(rows[0]["act"], 0)
        self.assertEqual(rows[0]["floor"], 6)

    def test_gold_is_none_when_the_observation_has_none(self):
        # No gold key at all, and an explicit null, both yield None and no crash.
        without_key = {"decision_evidence": [{"observation": {"act": 0, "floor": 1},
                                           "available_actions": [{"kind": "map"}],
                                           "option_labels": [["map:X"]]}],
                       "trace": [{"kind": "map"}]}
        for candidate in (without_key, decision([((0, 1), None)])):
            rows = strategic_rows(candidate)
            self.assertEqual(len(rows), 1)
            self.assertIsNone(rows[0]["gold"])
            self.assertEqual(merge_walks([rows])["gold_trace"], {})

    def test_two_walks_entering_a_floor_with_the_same_gold(self):
        walk = [row(0, "map", {"kind": "map", "col": 0, "row": 0}, 0, 6, gold=137)]
        facts = merge_walks([walk, walk])
        self.assertEqual(facts["gold_trace"]["a0f6"],
                         {"min": 137, "max": 137, "mean": 137.0, "n": 2})

    def test_only_the_first_row_of_a_floor_counts(self):
        # Second row of the same floor saw gold the walk already spent.
        rows = [row(0, "map", {"kind": "map", "col": 0, "row": 0}, 0, 6, gold=137),
                row(1, "buy", {"kind": "buy", "index": 0}, 0, 6, "Merchant", gold=64)]
        facts = merge_walks([rows])
        self.assertEqual(facts["gold_trace"]["a0f6"],
                         {"min": 137, "max": 137, "mean": 137.0, "n": 1})

    def test_gold_aggregated_across_walks_per_floor(self):
        walks = [[row(0, "map", {"kind": "map", "col": 0, "row": 0}, 0, 1, gold=99),
                  row(1, "map", {"kind": "map", "col": 1, "row": 0}, 0, 6, gold=137)],
                 [row(0, "map", {"kind": "map", "col": 2, "row": 0}, 0, 1, gold=99),
                  row(1, "map", {"kind": "map", "col": 3, "row": 0}, 0, 6, gold=64)]]
        trace = merge_walks(walks)["gold_trace"]
        self.assertEqual(trace["a0f1"], {"min": 99, "max": 99, "mean": 99.0, "n": 2})
        self.assertEqual(trace["a0f6"], {"min": 64, "max": 137, "mean": 100.5, "n": 2})

    def test_merge_keys_other_than_gold_trace_are_unchanged(self):
        rows = walk_rows() + [row(21, "buy", {"kind": "buy", "index": 0}, 0, 7, "Merchant",
                                 labels=("buy_removal",), gold=50),
                             row(22, "event", {"kind": "event", "index": 0}, 1, 9, "Event",
                                 labels=("event:B",))]
        facts = merge_walks([rows, rows])
        # gold_trace is the only addition to the returned key set.
        self.assertEqual(sorted(facts),
                         ["bosses", "card_offers", "events", "gold_trace", "max_floor",
                          "node_encounters", "routes", "shops", "tree"])
        self.assertEqual(facts["shops"], {"a0f7": {"buy_removal": 2}})
        self.assertEqual(facts["card_offers"], {"TWIN_STRIKE": 2})
        # The label-less event row of walk_rows still keys an empty counter.
        self.assertEqual(facts["events"], {"a0f3": {}, "a1f9": {"event:B": 2}})
        # Both walks contributed, so every counter doubles (like bosses above).
        self.assertEqual(facts["node_encounters"],
                         {"a0f2c1r1": {"JAW_WORM": 2}, "a0f6c2r5": {"GREMLIN_NOB": 2}})
        self.assertEqual(facts["max_floor"], 9)  # the act-1 floor-9 event row
        self.assertEqual(len(facts["routes"]), 2)
        self.assertEqual(len(facts["routes"][0]), 3)
        # The tree node shape is untouched by the gold bookkeeping.
        self.assertEqual(sorted(facts["tree"][1]),
                         ["act", "action", "children", "fight", "floor", "kind", "labels",
                          "leads_to", "menu"])


if __name__ == "__main__":
    unittest.main()
