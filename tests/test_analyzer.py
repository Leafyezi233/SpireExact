"""Unit tests for seed reconnaissance (analyzer.py); native evidence lives in validate runs."""
import unittest
from spire_exact.mode1 import context
from spire_exact.planning.analyzer import aggregate, fights_of, recon_request, route_hints
from spire_exact.planning.search import SearchConfig


def fight_row(act, room, hp, enemies=('jaw_worm',), floor=None, phase='combat'):
    return {'phase': phase, 'observation': {'act': act, 'room': room, 'hp': hp, 'max_hp': '80',
            'floor': floor if floor is not None else act * 50,
            'enemies': [{'id': name, 'combat_id': i} for i, name in enumerate(enemies)]}}


def rollout(rows, status='BUDGET'):
    return {'status': status, 'trace': [], 'decision_evidence': rows,
            'observation': {'act': 0, 'floor': rows[-1]['observation']['floor'] if rows else 0}}


class ReconTests(unittest.TestCase):
    def test_consecutive_combat_rows_are_one_fight_and_rooms_split_them(self):
        rows = [fight_row(0, 'Monster', '60'), fight_row(0, 'Monster', '40'),
                {'phase': 'map', 'observation': {'act': 0, 'floor': 5, 'enemies': None}},
                fight_row(0, 'Elite', '70', enemies=('gremlin_nob',))]
        fights = fights_of(rollout(rows))
        self.assertEqual(len(fights), 2)
        self.assertEqual(fights[0]['key'], (0, 'Monster', ('jaw_worm',)))
        self.assertEqual(fights[0]['hp'], 40.0)
        self.assertEqual(fights[1]['key'], (0, 'Elite', ('gremlin_nob',)))

    def test_death_blames_the_last_fight_and_survivors_count(self):
        died = rollout([fight_row(0, 'Monster', '60'), fight_row(0, 'Elite', '0')], status='TERMINAL')
        died['observation']['hp'] = '0'
        alive = rollout([fight_row(0, 'Monster', '60'), fight_row(0, 'Elite', '30')])
        merged = aggregate([died, alive])
        stats = merged['stats'][(0, 'Elite')]
        self.assertEqual(stats['fought'], 2)
        self.assertEqual(stats['died'], 1)
        self.assertEqual(merged['stats'][(0, 'Monster')]['died'], 0)

    def test_route_hints_only_cover_mappable_rooms(self):
        merged = aggregate([rollout([fight_row(0, 'Boss', '10'), fight_row(0, 'Elite', '70')])])
        hints = route_hints(merged['stats'])
        self.assertEqual(set(hints), {'a0:Elite'})
        self.assertEqual(hints['a0:Elite'], {'fought': 1, 'died': 0})

    def test_recon_request_cuts_combat_budgets_and_keeps_identity_out_of_real_paths(self):
        ctx = context('123', 'IRONCLAD', 10, 'all')
        config = SearchConfig(analyzer_rollouts=3, solver_seed=271828, max_decisions=12000)
        advisor = {'nodes': 60000, 'budget_ms': 600000, 'boss_budget_ms': 600000,
                   'gate_plans': {'Boss': []}, 'dop': 3}
        request = recon_request(ctx, advisor, config, ordinal=1)
        self.assertEqual(request['advisor']['nodes'], config.analyzer_nodes)
        self.assertEqual(request['advisor']['budget_ms'], config.analyzer_budget_ms)
        self.assertNotIn('gate_plans', request['advisor'])
        self.assertFalse(request['capture_checkpoints'])
        self.assertIsNone(request.get('probe'))
        self.assertNotEqual(request['policy_seed'], recon_request(ctx, advisor, config, 2)['policy_seed'])
        # A policy prior is only attached for non-native tables.
        self.assertNotIn('policy_prior', recon_request(ctx, advisor, config, 0))
        self.assertIn('policy_prior', recon_request(ctx, advisor, config, 1))


if __name__ == '__main__':
    unittest.main()
