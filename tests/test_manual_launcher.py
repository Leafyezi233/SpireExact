import os
import types
import unittest

from tools.run_solver import DEFAULTS, build_settings, default_out_root, parse_overrides, merge_overrides


class ManualLauncherTests(unittest.TestCase):
    def test_bare_invocation_matches_the_recorded_run_defaults(self):
        args = types.SimpleNamespace(seed='123', minutes=30)
        settings = build_settings(args, parse_overrides([]))
        pairs = dict(zip(settings[::2], settings[1::2]))
        self.assertEqual(pairs['--seed'], '123')
        self.assertEqual(pairs['--seconds'], '1770')
        self.assertEqual(pairs['--character'], DEFAULTS['character'])
        self.assertEqual(pairs['--ascension'], '10')
        self.assertEqual(pairs['--solver-seed'], '271828')
        self.assertEqual(pairs['--archive-entries'], '256')
        self.assertNotIn('--prior', settings)

    def test_a_set_pair_replaces_the_default_and_keeps_ordering(self):
        args = types.SimpleNamespace(seed='7', minutes=2)
        settings = build_settings(args, parse_overrides(['scheduler=focus', 'walker-walks=60']))
        pairs = dict(zip(settings[::2], settings[1::2]))
        self.assertEqual(pairs['--scheduler'], 'focus')
        self.assertEqual(pairs['--walker-walks'], '60')
        self.assertEqual(pairs['--seconds'], '90')
        self.assertEqual(settings[-4:], ['--scheduler', 'focus', '--walker-walks', '60'])

    def test_bare_flags_travel_last_and_repeatable_sets_accumulate(self):
        args = types.SimpleNamespace(seed='7', minutes=5)
        settings = build_settings(args, parse_overrides(['prior', 'prior', 'beam=8']))
        self.assertEqual(settings[-2:], ['--prior', '--prior'])
        self.assertEqual(settings.count('--prior'), 2)
        pairs = dict(zip(settings[::2], settings[1::2]))
        self.assertEqual(pairs['--beam'], '8')

    def test_a_bare_known_flag_may_also_be_written_as_a_pair(self):
        values = parse_overrides(['prior=1', 'low-io=1', 'scheduler=focus'])
        self.assertEqual(values['scheduler'], 'focus')
        self.assertEqual(sorted(values['__flags__']), ['low-io', 'prior'])

    def test_an_unknown_bare_word_is_rejected_instead_of_becoming_a_positional(self):
        with self.assertRaises(SystemExit):
            parse_overrides(['--set'])
        with self.assertRaises(SystemExit):
            parse_overrides(['walker-walks'])

    def test_a_config_flag_survives_a_command_line_set_pair(self):
        merged = merge_overrides(['low-io', 'prior', 'evaluations=400'], ['walker-walks=30'])
        self.assertEqual(merged['__flags__'], ['low-io', 'prior'])
        self.assertEqual(merged['evaluations'], '400')
        self.assertEqual(merged['walker-walks'], '30')

    def test_a_command_line_pair_replaces_the_config_value_in_place(self):
        args = types.SimpleNamespace(seed='7', minutes=30)
        settings = build_settings(args, merge_overrides(['budget-ms=1200', 'prior'],
                                                       ['budget-ms=4800', 'route-top-n=5']))
        pairs = dict(zip(*[iter(settings)] * 2))
        self.assertEqual(settings.count('--budget-ms'), 1)
        self.assertEqual(pairs['--budget-ms'], '4800')
        self.assertEqual(pairs['--route-top-n'], '5')
        self.assertIn('--prior', settings)

    def test_an_explicit_seconds_beats_the_minutes_derived_budget(self):
        args = types.SimpleNamespace(seed='7', minutes=34)
        pairs = dict(zip(*[iter(build_settings(args, merge_overrides(['seconds=2000'], [])))] * 2))
        self.assertEqual(pairs['--seconds'], '2000')

    def test_the_default_output_root_is_ascii_so_the_native_host_can_open_it(self):
        self.assertTrue(str(default_out_root()).isascii())

    def test_a_chinese_output_root_is_refused_before_the_budget_is_spent(self):
        root = default_out_root()
        try:
            os.environ['SPIRE_MANUAL_ROOT'] = str(root.parent / '\u5de5\u4f5c\u533a')
            self.assertFalse(str(default_out_root()).isascii())
        finally:
            os.environ.pop('SPIRE_MANUAL_ROOT', None)

    def test_a_very_short_run_still_leaves_a_usable_search_budget(self):
        for minutes, seconds in ((1, 30), (2, 90), (30, 1770)):
            args = types.SimpleNamespace(seed='7', minutes=minutes)
            pairs = dict(zip(*[iter(build_settings(args, parse_overrides([])))] * 2))
            self.assertEqual(pairs['--seconds'], str(seconds))
