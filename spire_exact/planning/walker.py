"""Skirmish option-tree walker: maps one seed's decision options in seconds.

The host's skirmish mode (request field "skirmish") force-won fights by
dropping living enemies to 1 HP on every combat menu, so the hand-written
fallback scorer finishes each fight in one or two actions. Everything else —
rewards, gold, deaths, every menu — runs through the real game pipeline, and
the controlled equivalence test measured 210/210 byte-identical card-reward
menus at identical strategic path positions versus real advisor-fought runs
(2026-10-04, easy seed): menu contents depend on the strategic path only,
never on how a fight was fought.

A walk is therefore a full-run terrain pass at ~2.5 s that observes every
strategic menu along one route. Several walks with different policies merge
into an option tree: per map node the encounter it holds, per position the
reward menus / shops / events the seed offers. Everything here is an
allocation signal for the planner (consumers in search/strategy) — a walk
never becomes a trajectory: no checkpoints, no result
cache, no frontier, and a skirmished state proves nothing about any real one.
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from copy import deepcopy
from time import perf_counter
from .io import write_json
from .pool import WorkerError

# Kinds of decisions that shape the strategic path (everything but combat actions).
STRATEGIC_KINDS = frozenset({"map", "card_reward", "rewards", "buy", "event", "rest",
                             "treasure", "treasure_skip", "card_skip", "select_cards", "next_act"})
COMBAT_KINDS = frozenset({"play", "end_turn", "use_potion", "discard_potion"})


def walk_request(ctx: dict, config, ordinal: int) -> dict:
    request = {key: ctx[key] for key in ("seed", "character", "ascension", "unlocks")}
    request.update(history=[], generate_candidate=True,
                   policy_seed=(config.solver_seed * 1_000_003 + 6_577 * ordinal) & 0x7FFFFFFF,
                   max_decisions=config.max_decisions, capture_checkpoints=False,
                   low_io=config.low_io, skirmish=True)
    if config.event_driven_settle:
        request["event_driven_settle"] = True
    return request


def strategic_rows(decision: dict) -> list[dict]:
    """Strategic decisions of one walk with their menus and route context."""
    evidence = decision.get("decision_evidence") or []
    trace = decision.get("trace") or []
    rows = []
    last_room = None
    for index, row in enumerate(evidence[:len(trace)]):
        action = trace[index]
        kind = action.get("kind")
        observation = row.get("observation") or {}
        if observation.get("enemies"):
            last_room = (observation.get("act"), observation.get("floor"),
                         observation.get("room"),
                         tuple(sorted(enemy.get("id") or "?" for enemy in observation["enemies"])))
        if kind in STRATEGIC_KINDS:
            rows.append({"index": index, "kind": kind, "action": action,
                         "act": observation.get("act"), "floor": observation.get("floor"),
                         "room": observation.get("room"), "menu": row.get("available_actions") or [],
                         "labels": [label for labels in row.get("option_labels") or [] for label in labels],
                         "entered_fight": last_room,
                         "gold": observation.get("gold")})
    return rows


def initial_deck(decision: dict) -> Counter:
    """The starting deck of one walk: the deck list of the first decision's
    observation (before any reward changed it). Card ids, upgrade levels
    ignored — the route valuation only needs which cards dilute the deck."""
    evidence = decision.get("decision_evidence") or []
    for row in evidence:
        deck = (row.get("observation") or {}).get("deck")
        if deck:
            return Counter(card.get("id") or "?" for card in deck)
    return Counter()


def initial_run_state(decision: dict) -> dict:
    """Starting hp / max_hp / gold of one walk, from the first observation
    that carries them — the base of the route planner's hp simulation."""
    evidence = decision.get("decision_evidence") or []
    for row in evidence:
        observation = row.get("observation") or {}
        if observation.get("max_hp"):
            # `character` is a property of the run, not of the hp line, but the
            # planner needs it: a class whose starting relic heals at combat end
            # (Ironclad's Burning Blood) pays part of its fight wound back, and
            # that is only knowable from the character id. The observation has
            # carried it all along; it simply was not forwarded.
            return {key: observation[key] for key in ("hp", "max_hp", "gold", "character")
                    if observation.get(key) is not None}
    return {}


def merge_walks(walks: list[list[dict]]) -> dict:
    """Merge strategic rows of several walks into seed facts. The option tree
    is keyed by the STRATEGIC ACTION SEQUENCE: the equivalence test showed
    menu contents are fixed per strategic path, while the same map node can
    hold different encounters on different paths — so a node's facts live on
    its path, never on its coordinates."""
    root = {"id": 0, "parent": None, "action": None, "kind": None, "act": None,
            "floor": None, "fight": None, "menu": None, "labels": [], "children": {}}
    nodes = [root]
    node_encounters: dict[tuple, Counter] = defaultdict(Counter)
    bosses: dict[int, Counter] = defaultdict(Counter)
    rewards: Counter = Counter()
    shops: dict[tuple, Counter] = defaultdict(Counter)
    events: dict[tuple, Counter] = defaultdict(Counter)
    routes: list[list[dict]] = []
    max_floor = 0

    def fight_entered_by(rows, index):
        """The NEW fight the map action at `index` leads into: the first fight
        after the map row that is not the map row's own stale combat context
        (every row carries the last fight as context, and decision floors are
        not node positions, so the context's act/floor is the only reliable
        marker). None for non-combat rooms."""
        context = rows[index].get("entered_fight")
        context_pos = (context[0], context[1]) if context else None
        for row in rows[index + 1:]:
            if row["kind"] == "map":
                return None
            fight = row.get("entered_fight")
            if fight and (fight[0], fight[1]) != context_pos:
                return fight
        return None

    def room_entered_by(rows, index):
        """The non-combat room the map action at `index` leads into: the room
        of the first later row before the next map row. None for another map
        choice or when nothing was decided inside the room."""
        for row in rows[index + 1:]:
            if row["kind"] == "map":
                return None
            if row.get("room"):
                return row["room"]
        return None

    def walk_trie(rows):
        current = root
        for index, row in enumerate(rows):
            action_key = json.dumps(row["action"], sort_keys=True, ensure_ascii=False)
            child = current["children"].get(action_key)
            if child is None:
                child = {"id": len(nodes), "parent": current["id"], "action": row["action"],
                         "kind": row["kind"], "act": row["act"], "floor": row["floor"],
                         "fight": None, "menu": None, "labels": [], "children": {}}
                current["children"][action_key] = child
                nodes.append(child)
            current = child
            # Last fight observed before this strategic decision on THIS path.
            if row.get("entered_fight"):
                current["fight"] = "+".join(row["entered_fight"][3])
            # A map action additionally records the room it leads into: a
            # fight when one follows, otherwise the rest / shop / treasure /
            # event room the next decision happens in.
            if row["kind"] == "map":
                entered = fight_entered_by(rows, index)
                if entered:
                    current["leads_to"] = {"room": entered[2], "enemies": "+".join(entered[3])}
                else:
                    room = room_entered_by(rows, index)
                    if room:
                        current["leads_to"] = {"room": room}
            if row["menu"] is not None and current["menu"] is None:
                current["menu"] = [a for a in row["menu"]]
                current["labels"] = list(row["labels"])
        return current

    for rows in walks:
        max_floor = max(max_floor, max((r["floor"] or 0 for r in rows), default=0))
        walk_trie(rows)
        route = []
        counted_fights = set()
        for index, row in enumerate(rows):
            if row["kind"] == "map":
                route.append({"act": row["act"], "floor": row["floor"],
                              "col": row["action"].get("col"), "row_idx": row["action"].get("row")})
                entered = fight_entered_by(rows, index)
                if entered:
                    act, floor, room, enemies = entered
                    node = (act, floor, row["action"].get("col"), row["action"].get("row"))
                    node_encounters[node]["+".join(enemies)] += 1
                    if room == "Boss":
                        counted_fights.add((act, "+".join(enemies)))
            fight = row.get("entered_fight")
            if fight and row["kind"] != "map":
                act, floor, room, enemies = fight
                if room == "Boss":
                    counted_fights.add((act, "+".join(enemies)))
            if row["kind"] == "card_reward":
                rewards.update(label[5:] for label in row["labels"] if label.startswith("card:"))
            elif row["kind"] == "buy":
                shops[(row["act"], row["floor"])].update(label for label in row["labels"])
            elif row["kind"] == "event":
                events[(row["act"], row["floor"])].update(label for label in row["labels"])
        for act, enemies in counted_fights:
            bosses[act][enemies] += 1
        routes.append(route)
    # Dense trie: drop bookkeeping keys that only matter during merging.
    tree = [{"action": n["action"], "kind": n["kind"], "act": n["act"], "floor": n["floor"],
             "fight": n["fight"], "leads_to": n.get("leads_to"), "menu": n["menu"],
             "labels": n["labels"],
             "children": {key: child["id"] for key, child in n["children"].items()}}
            for n in nodes]
    # Gold per floor, aggregated across walks. A walk's FIRST row on a floor
    # carries the gold it ENTERED the floor with; every later row of the same
    # floor saw gold that walk already spent, so it is not comparable — only
    # the first one is. Rows without gold, and rows without a position (they
    # would key as "aNonefNone"), carry no floor entry and are skipped.
    gold_by_floor: dict[tuple, list[int]] = defaultdict(list)
    for rows in walks:
        entered: dict[tuple, int] = {}
        for row in rows:
            gold = row.get("gold")
            if gold is None or row.get("act") is None or row.get("floor") is None:
                continue
            entered.setdefault((row["act"], row["floor"]), gold)
        for position, gold in entered.items():
            gold_by_floor[position].append(gold)
    gold_trace = {f"a{act}f{floor}": {"min": min(golds), "max": max(golds),
                                      "mean": sum(golds) / len(golds), "n": len(golds)}
                  for (act, floor), golds in sorted(gold_by_floor.items())}
    return {
        "routes": routes,
        "tree": tree,
        "node_encounters": {f"a{a}f{f}c{c}r{r}": dict(counter.most_common(3))
                            for (a, f, c, r), counter in sorted(node_encounters.items())},
        "bosses": {f"act{act}": dict(counter.most_common(4)) for act, counter in sorted(bosses.items())},
        "card_offers": dict(rewards.most_common(64)),
        "shops": {f"a{a}f{f}": dict(counter.most_common(16)) for (a, f), counter in sorted(shops.items())},
        "events": {f"a{a}f{f}": dict(counter.most_common(8)) for (a, f), counter in sorted(events.items())},
        "max_floor": max_floor,
        "gold_trace": gold_trace,
    }


# Default reconnaissance breadth (player brief of 2026-10-05: 60 walks,
# reduced to 30 by the player brief of 2026-10-07). The route planner can only
# prescribe routes the walks actually reached, so a top-N prescription needs N
# complete routes in the merged tree. Every walk is submitted at once and runs
# one-per-worker, so the reconnaissance wall clock is ceil(N / workers) walks,
# NOT one walk: measured 2026-10-07, 60 walks on 2 workers cost 71-118 s against
# a 2.17 s single walk. Raising --workers is what shortens it.
WALK_DEFAULT = 30


def walk_count(config, workers: int) -> int:
    """Walks to run: at least WALK_DEFAULT, or more when --walker-walks asks
    for it. The search's workers are idle during reconnaissance, so each walk
    takes one worker. They are submitted all at once and run one-per-worker, so
    the reconnaissance wall clock is ceil(N / workers) walks, not N and not one
    (see WALK_DEFAULT). The count is worker-independent on purpose: the planner
    can only prescribe routes the walks reached, so a top-5 plan needs the walks
    to have found 5 routes however many workers exist."""
    return max(int(config.walker_walks), WALK_DEFAULT)


def run(pool, ctx: dict, config, out_dir) -> dict:
    """Run the skirmish walks and return the seed facts. Walks are synthetic
    requests; only the merged facts travel on. All walks are submitted at once
    and run one-per-worker in parallel, so the reconnaissance wall clock is
    ceil(N / workers) walks.

    The walks use the pool's ordinary shared workers (player brief of
    2026-10-07), not disposable ones: a walk is a complete run, so its process
    starts fresh at the run anyway, and the worker's Cleanup() between requests
    (Program.cs:52-62) already detaches the previous run. Reusing them removes
    the ~1 s per-walk bootstrap, which was 45 % of a walk's 2.17 s. Synthetic
    walk facts still reach no cache, checkpoint, frontier or bound — the host
    skirmish flag itself refuses checkpoint capture and verification replays
    (CampaignReplay.cs:126-131)."""
    started = perf_counter()
    folder = out_dir / "walks"
    count = walk_count(config, getattr(pool.resources, 'workers', 1))
    submissions = {ordinal: pool.executor.submit(pool.run, walk_request(ctx, config, ordinal),
                                                 folder / f"walk-{ordinal:03d}",
                                                 config.task_seconds)
                   for ordinal in range(count)}
    walks = []
    rows = []
    deck = None
    run_state = None
    for ordinal, future in submissions.items():
        label = f"walk-{ordinal:03d}"
        try:
            decision, _identity = future.result()
            walks.append(strategic_rows(decision))
            if deck is None:
                first = initial_deck(decision)
                if first:
                    deck = dict(first)
            if run_state is None:
                state = initial_run_state(decision)
                if state:
                    run_state = state
            searches = (decision.get("advisor_metrics") or {}).get("searches") or []
            rows.append({"label": label, "wall_seconds": round(perf_counter() - started, 1),
                         "won": bool(decision.get("native_terminal_observed")),
                         "max_floor": (decision.get("observation") or {}).get("floor"),
                         "decisions": len(decision.get("decision_evidence") or []),
                         "combat_search_seconds": sum(int(s.get("wall_us") or 0) for s in searches) / 1e6,
                         "error": None})
        except (WorkerError, OSError, ValueError, KeyError) as error:
            rows.append({"label": label, "error": str(error)})
    facts = merge_walks(walks)
    summary = {"schema": "spire-seed-facts/v1", "walks_requested": count,
               "walks_completed": len(walks), **facts, "walks": rows,
               "initial_deck": deck or {},
               "initial_run": run_state or {},
               "wall_seconds": perf_counter() - started,
               "allocation_signal_only": True,
               "equivalence_evidence": "210/210 identical reward menus at identical strategic positions, 2026-10-04"}
    return summary
