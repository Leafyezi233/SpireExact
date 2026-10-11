"""DP route plugin modes: the switch, its defaults and its validation.

The plugin is the local tree's whole-campaign DP route planner (route.py +
walker.py + analyzer.py) sitting on top of the upstream i100 solver. These
tests pin the two properties the merge depends on:

  * mode 'off' leaves the upstream i100 entry alone, knob for knob;
  * a non-off mode only ever changes the search SHAPE (which scheduler runs,
    whether the clinic steers) and never overrides an explicit flag.
"""
import argparse
import unittest

from spire_exact.planning.dp_defaults import (MODE_FIELDS, PUBLIC_ROUTE_MODE,
                                              ROUTE_MODES, ROUTE_MODE_TABLE,
                                              describe, resolve_route_mode,
                                              route_mode_argv)
from spire_exact.planning.search import SearchConfig


def planner_args(argv):
    """parse_planner of the real CLI: the entry every launcher goes through."""
    from spire_exact.planning.__main__ import parse_planner
    return parse_planner(argv)


def resolved(**options):
    """A bare Namespace with every mode field unset, as argparse leaves it."""
    args = argparse.Namespace(**options)
    for key in MODE_FIELDS:
        if not hasattr(args, key):
            setattr(args, key, None)
    return resolve_route_mode(args)


class ModeTableTests(unittest.TestCase):
    def test_every_mode_names_only_real_search_config_fields(self):
        fields = set(SearchConfig.__dataclass_fields__)
        for mode, table in ROUTE_MODE_TABLE.items():
            self.assertTrue(set(table) <= fields, mode + ': ' + str(set(table) - fields))

    def test_mode_fields_match_the_table(self):
        for mode, table in ROUTE_MODE_TABLE.items():
            self.assertEqual(tuple(sorted(table)), MODE_FIELDS, mode)

    def test_public_default_is_off(self):
        self.assertEqual(PUBLIC_ROUTE_MODE, 'off')
        self.assertEqual(resolved().route_mode, 'off')

    def test_off_is_the_upstream_knob_values(self):
        args = resolved()
        self.assertEqual(args.walker_walks, 0)
        self.assertEqual(args.analyzer_rollouts, 0)
        self.assertFalse(args.route_prescribed)
        self.assertFalse(args.route_strict)
        self.assertFalse(args.strict_then_search)
        self.assertEqual(args.route_top_n, 1)

    def test_guide_is_soft_and_force_is_hard(self):
        guide = resolved(route_mode='guide')
        self.assertTrue(guide.route_prescribed)
        self.assertFalse(guide.route_strict)
        force = resolved(route_mode='force')
        self.assertTrue(force.route_strict)
        self.assertFalse(force.route_prescribed)
        self.assertFalse(force.strict_then_search)
        both = resolved(route_mode='force-then-search')
        self.assertTrue(both.route_strict and both.strict_then_search)

    def test_explicit_value_beats_the_mode_table(self):
        args = resolved(route_mode='guide', walker_walks=60, route_top_n=3)
        self.assertEqual(args.walker_walks, 60)
        self.assertEqual(args.route_top_n, 3)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            resolved(route_mode='nonsense')
        with self.assertRaises(ValueError):
            route_mode_argv('nonsense')

    def test_every_mode_is_described(self):
        for mode in ROUTE_MODES:
            self.assertTrue(describe(mode))


class ModeReconciliationTests(unittest.TestCase):
    """A non-off mode needs a route-capable scheduler and cannot share the
    budget with the upstream clinic, so it turns those off - unless the user
    said otherwise, in which case the caller rejects the combination."""

    def test_a_non_off_mode_clears_the_clinic_and_selects_a_route_scheduler(self):
        args = resolved(route_mode='guide', clinic=True, scheduler='focus',
                        strategy_prior='builtin', macro_plateau=True, tail_mode='off')
        self.assertFalse(args.clinic)
        self.assertEqual(args.scheduler, 'weighted')
        self.assertEqual(args.strategy_prior, 'off')
        self.assertFalse(args.macro_plateau)
        self.assertEqual(args.route_mode_changes['scheduler'], 'weighted')

    def test_force_clears_root_policies_but_guide_keeps_them(self):
        self.assertEqual(resolved(route_mode='force', root_policies='pick,elo').root_policies, '')
        self.assertEqual(resolved(route_mode='guide', root_policies='pick,elo').root_policies, 'pick,elo')

    def test_an_explicit_flag_is_never_overridden(self):
        args = resolve_route_mode(
            argparse.Namespace(route_mode='guide', clinic=True, scheduler='focus',
                               strategy_prior='builtin', macro_plateau=True, macro_widening=True,
                               macro_fair=True, macro_routes='shadow', tail_mode='shadow',
                               focus_stall=32, root_policies='pick',
                               **{key: None for key in MODE_FIELDS}),
            explicit={'clinic', 'scheduler', 'tail_mode', 'root_policies'})
        self.assertTrue(args.clinic)
        self.assertEqual(args.scheduler, 'focus')
        self.assertEqual(args.tail_mode, 'shadow')
        self.assertEqual(args.root_policies, 'pick')

    def test_off_never_reconciles_anything(self):
        args = resolved(route_mode='off', clinic=True, scheduler='focus', root_policies='pick,elo')
        self.assertTrue(args.clinic)
        self.assertEqual(args.scheduler, 'focus')
        self.assertEqual(args.route_mode_changes, {})

    def test_an_explicit_route_flag_implies_its_mode(self):
        self.assertEqual(resolved(route_strict=True, route_prescribed=None).route_mode, 'force')
        self.assertEqual(resolved(strict_then_search=True, route_strict=None, route_prescribed=None).route_mode,
                         'force-then-search')
        self.assertEqual(resolved(route_prescribed=True, route_strict=None, walker_walks=None).route_mode, 'guide')


class PlannerEntryTests(unittest.TestCase):
    """The real CLI: mode resolution happens last, after every upstream check."""

    def test_off_matches_the_upstream_i100_entry_knob_for_knob(self):
        base = planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100'])[1]
        off = planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                            '--route-mode', 'off'])[1]
        for key in MODE_FIELDS:
            self.assertEqual(getattr(base, key), getattr(off, key), key)
        # and the whole upstream search shape is untouched
        for key in ('scheduler', 'clinic', 'clinic_hints', 'strategy_prior', 'macro_plateau',
                    'macro_routes', 'tail_mode', 'root_policies', 'paired_card_first'):
            self.assertEqual(getattr(base, key), getattr(off, key), key)

    def test_guide_on_the_i100_entry_reconciles_the_search_shape(self):
        _, args, _, _, _ = planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                                         '--route-mode', 'guide'])
        self.assertEqual(args.route_mode, 'guide')
        self.assertEqual(args.scheduler, 'weighted')
        self.assertFalse(args.clinic)
        self.assertTrue(args.route_prescribed)
        self.assertEqual(args.walker_walks, 30)

    def test_force_on_the_i100_entry_drops_root_policies(self):
        _, args, _, _, _ = planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                                         '--route-mode', 'force'])
        self.assertTrue(args.route_strict)
        self.assertEqual(args.root_policies, '')

    def test_a_dp_mode_with_an_explicit_clinic_is_rejected(self):
        with self.assertRaises(SystemExit):
            planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                          '--route-mode', 'guide', '--clinic'])

    def test_a_dp_mode_with_an_explicit_focus_scheduler_is_rejected(self):
        with self.assertRaises(SystemExit):
            planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                          '--route-mode', 'guide', '--scheduler', 'focus'])

    def test_force_with_explicit_root_policies_is_rejected(self):
        with self.assertRaises(SystemExit):
            planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                          '--route-mode', 'force', '--root-policies', 'pick'])

    def test_an_unknown_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            planner_args(['--seed', '1', '--out', 'X:/t', '--feature-profile', 'i100',
                          '--route-mode', 'nonsense'])


class ArgvRoundTripTests(unittest.TestCase):
    def test_route_mode_argv_reproduces_the_mode(self):
        from spire_exact.planning.final_defaults import resolve_entry_defaults
        from spire_exact.planning.__main__ import build_parser
        parser = build_parser()
        for mode in ROUTE_MODES:
            argv = ['--out', 'X:/t'] + route_mode_argv(mode)
            args = resolve_entry_defaults(parser.parse_args(argv))
            seen = {s.split('=', 1)[0] for s in argv if s.startswith('--')}
            dests = {o.dest for o in parser._actions for s in o.option_strings if s in seen}
            args = resolve_route_mode(args, dests)
            for key, value in ROUTE_MODE_TABLE[mode].items():
                self.assertEqual(getattr(args, key), value, mode + ':' + key)


class SearchConfigTests(unittest.TestCase):
    def test_route_mode_defaults_to_off(self):
        self.assertEqual(SearchConfig().route_mode, 'off')

    def test_route_mode_is_validated(self):
        for mode in ROUTE_MODES:
            self.assertEqual(SearchConfig(route_mode=mode).route_mode, mode)
        with self.assertRaises(ValueError):
            SearchConfig(route_mode='nonsense')


class RouteWorkerTests(unittest.TestCase):
    """--route-workers defaults to the machine, but only when the plugin runs."""

    def test_detect_route_workers_reports_physical_performance_cores(self):
        from spire_exact.planning.dp_defaults import detect_route_workers
        workers = detect_route_workers()
        self.assertGreaterEqual(workers, 1)
        try:
            from tools.cpu_topology import inventory
            rows = inventory()
        except Exception:
            self.assertEqual(workers, 1)  # non-Windows / no tools package
            return
        classes = sorted({row['efficiency_class'] for row in rows}, reverse=True)
        expected = {row['core_index'] for row in rows
                    if row['efficiency_class'] == classes[0]}
        self.assertEqual(workers, max(1, len(expected)))

    def test_route_workers_is_auto_only_when_the_plugin_runs(self):
        from spire_exact.planning.__main__ import search_config
        from spire_exact.planning.dp_defaults import detect_route_workers
        _, off, gates, probes, roots = planner_args(['--out', 'X:/t'])
        self.assertIsNone(off.route_workers)
        self.assertEqual(search_config(off, gates, probes, roots).route_workers, 1)
        _, dp, gates, probes, roots = planner_args(['--out', 'X:/t', '--route-mode',
                                                    'force-then-search'])
        self.assertEqual(search_config(dp, gates, probes, roots).route_workers,
                         detect_route_workers())

    def test_an_explicit_route_workers_always_wins(self):
        from spire_exact.planning.__main__ import search_config
        _, dp, gates, probes, roots = planner_args(['--out', 'X:/t', '--route-mode',
                                                    'force-then-search',
                                                    '--route-workers', '3'])
        self.assertEqual(search_config(dp, gates, probes, roots).route_workers, 3)

    def test_route_workers_follows_the_main_loop_parallelism(self):
        # The panel passes the topology as --workers/--dop, so the planner's
        # worker count tracks the search's own parallelism instead of probing
        # the machine again.
        from spire_exact.planning.__main__ import search_config
        _, dp, gates, probes, roots = planner_args(['--out', 'X:/t', '--route-mode',
                                                    'force-then-search',
                                                    '--workers', '3', '--dop', '2'])
        self.assertEqual(search_config(dp, gates, probes, roots).route_workers, 6)
        _, single, gates, probes, roots = planner_args(['--out', 'X:/t', '--route-mode',
                                                       'force-then-search',
                                                       '--workers', '7', '--dop', '1'])
        self.assertEqual(search_config(single, gates, probes, roots).route_workers, 7)
        # An explicit --route-workers still beats the derived value.
        _, pinned, gates, probes, roots = planner_args(['--out', 'X:/t', '--route-mode',
                                                       'force-then-search',
                                                       '--workers', '3', '--dop', '2',
                                                       '--route-workers', '1'])
        self.assertEqual(search_config(pinned, gates, probes, roots).route_workers, 1)


if __name__ == '__main__':
    unittest.main()
