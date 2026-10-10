"""Seed reconnaissance: cheap full-run rollouts that map one seed's macro structure.

Before the real search starts, a handful of disposable rollouts play the whole
run with a heavily reduced combat budget and the run's root policies. Their
fights locate this seed's death hotspots per (act, room type) and identify the
bosses a route actually meets. Everything here is an allocation signal only:
recon states never existed on a real route, so a recon rollout never enters the
checkpoint archive, the result cache, the frontier, a scheduler or a gate
model, and losing or winning one proves nothing about any real state. Each
rollout runs in a worker that is discarded afterwards (pool.run disposable).

The consumed signal is `route_hints`: per (act, room type) death rates handed
to the native map scoring as a route preference. Nothing else.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from time import perf_counter
from .archive import classify_failure
from .io import write_json
from .policies import NATIVE, POLICIES
from .probes import offered_cards

# Room types a map decision can walk into and that recon fights measure.
COMBAT_ROOMS = ('Monster', 'Elite', 'Unknown')
# Root rollout policies a recon run cycles through, for deck diversity.
RECON_POLICIES = (NATIVE, 'pick', 'elo')


def recon_request(ctx: dict, advisor: dict | None, config, ordinal: int) -> dict:
    """One full-run rollout request: micro combat budgets, varied policy."""
    request = {key: ctx[key] for key in ('seed', 'character', 'ascension', 'unlocks')}
    seed = (config.solver_seed * 1_000_003 + 7_919 * ordinal) & 0x7FFFFFFF
    request.update(history=[], generate_candidate=True, policy_seed=seed,
                   max_decisions=config.max_decisions, capture_checkpoints=False,
                   low_io=config.low_io)
    if config.event_driven_settle:
        request['event_driven_settle'] = True
    if advisor:
        patch = deepcopy(advisor)
        patch.setdefault('fix_consumed_block_compensation', True)
        if config.low_io:
            patch.setdefault('quiet_diagnostics', True)
        # Recon wants breadth, not tactical depth: tiny budgets, no gate plans.
        patch.pop('gate_plans', None)
        patch['nodes'] = config.analyzer_nodes
        patch['normal_nodes'] = config.analyzer_normal_nodes
        patch['budget_ms'] = config.analyzer_budget_ms
        patch['boss_budget_ms'] = config.analyzer_boss_budget_ms
        request['advisor'] = patch
    table = POLICIES[RECON_POLICIES[ordinal % len(RECON_POLICIES)]]
    if table:
        request['policy_prior'] = deepcopy(table)
    return request


def fights_of(result: dict) -> list[dict]:
    """Consecutive combat decision rows grouped into fights. Each fight carries
    its (act, room) key, the enemy ids seen and the HP fraction at its last
    observed decision."""
    fights = []
    current = None
    for row in result.get('decision_evidence') or []:
        observation = row.get('observation') or {}
        in_combat = row.get('phase') == 'combat' and observation.get('enemies')
        if in_combat:
            key = (observation.get('act'), observation.get('room'),
                   tuple(sorted(enemy.get('id') or '?' for enemy in observation['enemies'])))
            if current is not None and current['key'] != key:
                fights.append(current)
                current = None
            if current is None:
                current = {'key': key, 'floor': observation.get('floor'),
                           'hp': float(observation.get('hp') or 0.0),
                           'max_hp': max(1.0, float(observation.get('max_hp') or 1.0))}
            else:
                current['hp'] = float(observation.get('hp') or 0.0)
                current['max_hp'] = max(1.0, float(observation.get('max_hp') or 1.0))
        elif current is not None:
            fights.append(current)
            current = None
    if current is not None:
        fights.append(current)
    return fights


def aggregate(results: list[dict]) -> dict:
    """Merge rollout fights into per-(act, room) statistics. A run that died
    attributes its death to the fight it died in; every earlier fight counts as
    survived there."""
    stats: dict[tuple, dict] = {}
    bosses: dict[int, Counter] = {}
    max_floor = 0
    for result in results:
        died = classify_failure(result) == 'NATIVE_ROUTE_DEATH'
        fights = fights_of(result)
        for index, fight in enumerate(fights):
            act, room, enemies = fight['key']
            if act is None or room is None:
                continue
            row = stats.setdefault((act, room), {'fought': 0, 'died': 0, 'enemies': Counter(), 'hp_kept': 0.0})
            row['fought'] += 1
            row['enemies']['+'.join(enemies)] += 1
            row['hp_kept'] += max(0.0, fight['hp'] / fight['max_hp'])
            if died and index == len(fights) - 1:
                row['died'] += 1
            if room == 'Boss':
                bosses.setdefault(act, Counter())['+'.join(enemies)] += 1
        observation = result.get('observation') or {}
        if observation.get('floor'):
            max_floor = max(max_floor, int(observation['floor']))
    return {'stats': stats, 'bosses': bosses, 'max_floor': max_floor}


def route_hints(stats: dict) -> dict:
    """Death rates per (act, room type), the one signal the native host reads."""
    hints = {}
    for (act, room), row in sorted(stats.items()):
        if row['fought'] and room in COMBAT_ROOMS:
            hints[f'a{act}:{room}'] = {'fought': row['fought'], 'died': row['died']}
    return hints


def run(pool, ctx: dict, config, advisor: dict | None, out_dir) -> dict:
    """Run the recon rollouts and return the analysis summary. Rollouts are
    disposable full-run requests; their results are reduced and discarded."""
    started = perf_counter()
    folder = out_dir / 'analysis'
    results = []
    rows = []
    for ordinal in range(config.analyzer_rollouts):
        request = recon_request(ctx, advisor, config, ordinal)
        label = f'recon-{ordinal:03d}'
        target = folder / label
        try:
            decision, _identity = pool.run(request, target, config.task_seconds, disposable=True)
            results.append(decision)
            searches = (decision.get('advisor_metrics') or {}).get('searches') or []
            rows.append({'label': label, 'classification': classify_failure(decision),
                         'floor': (decision.get('observation') or {}).get('floor'),
                         'combat_search_seconds': sum(int(s.get('wall_us') or 0) for s in searches) / 1e6,
                         'expanded_combat_nodes': sum(int(s.get('expanded_nodes') or 0) for s in searches),
                         'error': None})
        except (OSError, ValueError, KeyError) as error:
            rows.append({'label': label, 'classification': 'UNKNOWN', 'floor': None,
                         'combat_search_seconds': 0.0, 'expanded_combat_nodes': 0, 'error': str(error)})
    merged = aggregate(results)
    offers = Counter()
    for result in results:
        offers.update(offered_cards(result))
    probe_rows = (probe_boss_gates(pool, ctx, config, advisor, results, offers, folder)
                  if config.analyzer_probe_cards else [])
    summary = {'schema': 'spire-seed-recon/v1', 'rollouts_requested': config.analyzer_rollouts,
               'rollouts_completed': len(results), 'max_floor': merged['max_floor'],
               'fights': {f'a{act}:{room}': {'fought': row['fought'], 'died': row['died'],
                                             'hp_kept_avg': round(row['hp_kept'] / row['fought'], 4) if row['fought'] else None,
                                             'enemies': dict(row['enemies'].most_common(4))}
                          for (act, room), row in sorted(merged['stats'].items())},
               'bosses': {f'act{act}': dict(counter.most_common(4)) for act, counter in sorted(merged['bosses'].items())},
               'card_offers': dict(offers.most_common(32)),
               'route_hints': route_hints(merged['stats']),
               'probe_gates': [{'gate': list(gate), 'table': table, 'probes': len(rows)} for gate, table, rows in probe_rows],
               'rollouts': rows, 'wall_seconds': perf_counter() - started,
               'allocation_signal_only': True, 'proves_nothing_about_real_states': True}
    summary['probe_rows'] = probe_rows
    return summary


def probe_boss_gates(pool, ctx: dict, config, advisor: dict | None, results: list[dict], offers: Counter, folder):
    """Synthetic one-card tables at the deepest act's boss gates recon reached.

    This is the reconnaissance answer to the known hard-seed failure point:
    bosses are probed before the search has spent its budget reaching them.
    Each table tries the unedited base plus one-card additions of the
    cards this seed offered most; a won or lost probe proves nothing about any
    real state. Returns [(gate_id, table_id, [(label, outcome) | ...])]; the
    caller feeds the rows to the run's gate model, which turns them into card
    tiers via its prior. Nothing else consumes them."""
    from .probes import gate_entries, probe_request, probe_outcome, edit_candidates
    template = {key: ctx[key] for key in ('seed', 'character', 'ascension', 'unlocks')}
    template.update(history=[], generate_candidate=True, policy_seed=0,
                    max_decisions=config.max_decisions, capture_checkpoints=False, low_io=config.low_io)
    if config.event_driven_settle:
        template['event_driven_settle'] = True
    if advisor:
        patch = deepcopy(advisor)
        patch.setdefault('fix_consumed_block_compensation', True)
        if config.low_io:
            patch.setdefault('quiet_diagnostics', True)
        if config.gate_probe_plan:
            patch.update(deepcopy(config.gate_probe_plan))
        else:
            patch['boss_budget_ms'] = min(int(patch.get('boss_budget_ms') or 0) or 30_000, 30_000)
        template['advisor'] = patch
    last_act = max((entry['gate'][0] for result in results for entry in gate_entries(result)), default=None)
    if last_act is None:
        return []
    picked = {}
    for result in results:
        for entry in gate_entries(result):
            # Recon fights with micro budgets rarely reach the last act, so
            # probe the highest act rollouts actually reached: its bosses are
            # the gates the search will meet next.
            if entry['gate'][0] != last_act or entry['gate'] in picked:
                continue
            picked[entry['gate']] = (result, entry)
    tables = []
    ordinal = 0
    for gate_id, (result, entry) in sorted(picked.items()):
        # The unedited base first, exactly like the search's own probe tables.
        candidates = [(None, [])] + edit_candidates(entry['entry'], offers, config.analyzer_probe_cards)
        # Chunked one-worker batches keep each disposable worker short-lived.
        requests = [probe_request(template, result['trace'], entry, edits) for _, edits in candidates]
        outcomes = []
        for start in range(0, len(requests), config.analyzer_probe_chunk):
            chunk = requests[start:start + config.analyzer_probe_chunk]
            try:
                completed = pool.run_probes(chunk, folder / f'recon-probe-g{gate_id[0]}-{gate_id[1]}-{start:03d}',
                                            timeout=max(600, 120 * len(chunk)))
            except (OSError, ValueError, KeyError) as error:
                write_note(folder, gate_id, start, str(error))
                completed = [None] * len(chunk)
            outcomes.extend((label, probe_outcome(decision) if decision is not None else None)
                            for (label, _), decision in zip(candidates[start:start + len(chunk)], completed))
        tables.append((gate_id, 10_000 + ordinal, outcomes))
        ordinal += 1
    return tables


def write_note(folder, gate_id, start, error):
    write_json(folder / f'recon-probe-g{gate_id[0]}-{gate_id[1]}-{start:03d}' / 'failure.json',
               {'status': 'UNKNOWN', 'error': error, 'synthetic': True})
