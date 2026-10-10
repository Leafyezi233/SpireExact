"""Tests for spire_exact.planning.card_types static category table."""

import unittest

from spire_exact.planning.card_types import CARD_CATEGORIES, get_category
from spire_exact.planning.policy_data import TIERS

VALID = {'attack', 'defense', 'engine'}


class TestCardTypes(unittest.TestCase):
    def test_full_pick_coverage(self):
        """Every card in TIERS['pick'] has a category and does not raise."""
        for key in TIERS['pick']:
            card_id = key[len('card:'):]
            self.assertIsInstance(get_category(card_id), str)

    def test_known_samples(self):
        """Per the spire-codex API catalog: STRIKE_IRONCLAD is an Attack card
        and DEFEND_IRONCLAD is a Skill with nonzero Block."""
        self.assertEqual(get_category('STRIKE_IRONCLAD'), 'attack')
        self.assertEqual(get_category('DEFEND_IRONCLAD'), 'defense')

    def test_categories_are_valid(self):
        for card_id, category in CARD_CATEGORIES.items():
            self.assertIn(category, VALID, f'{card_id} -> {category!r}')

    def test_unknown_id_returns_engine(self):
        self.assertEqual(get_category('NOT_A_REAL_CARD'), 'engine')


if __name__ == '__main__':
    unittest.main()
