"""Static card id -> native card kind (Power and friends) for the route planner.

Data source: the bundled native catalog `outputs/native-catalog-check/data/
catalog.json` (schema `spire-native-catalog/v1`, 596 cards, built from the
installed sts2.dll ModelDb — see the `source` field). The Power set below is
every catalog entry with `type == "Power"` (113 ids). Regenerate from
that catalog; do not hand-edit individual entries.

This module answers "is this card a Power?" for the player's repeat-penalty
brief (2026-10-04): a Power's second and later copies cost at least 2 points.
It is never a card value, a bound or a proof.
"""
from __future__ import annotations

POWER_CARDS: frozenset[str] = frozenset([
    'ABRASIVE', 'ACCELERANT', 'ACCURACY', 'AFTERIMAGE',
    'AGGRESSION', 'ARSENAL', 'AUTOMATION', 'BARRICADE',
    'BEACON_OF_HOPE', 'BIASED_COGNITION', 'BLACK_HOLE', 'BUFFER',
    'BULK_UP', 'CACOPHONY', 'CALAMITY', 'CALCIFY',
    'CALL_OF_THE_VOID', 'CALTROPS', 'CAPACITOR', 'CHILD_OF_THE_STARS',
    'CONSUMING_SHADOW', 'COOLANT', 'CORRUPTION', 'COUNTDOWN',
    'CREATIVE_AI', 'CRIMSON_MANTLE', 'CRUELTY', 'DANSE_MACABRE',
    'DARK_EMBRACE', 'DEFRAGMENT', 'DEMESNE', 'DEMON_FORM',
    'DEVOUR_LIFE', 'ECHO_FORM', 'ENTROPY', 'ENVENOM',
    'ETERNAL_ARMOR', 'FAN_OF_KNIVES', 'FASTEN', 'FEEL_NO_PAIN',
    'FERAL', 'FOOTWORK', 'FORBIDDEN_GRIMOIRE', 'FRIENDSHIP',
    'FURNACE', 'GENESIS', 'HAILSTORM', 'HAMMER_TIME',
    'HAUNT', 'HELLO_WORLD', 'HELLRAISER', 'INFERNO',
    'INFINITE_BLADES', 'INFLAME', 'ITERATION', 'JUGGERNAUT',
    'JUGGLING', 'LETHALITY', 'LOOP', 'MACHINE_LEARNING',
    'MASTER_PLANNER', 'MAYHEM', 'MONARCHS_GAZE', 'NECRO_MASTERY',
    'NEUROSURGE', 'NEUTRON_AEGIS', 'NOSTALGIA', 'NOXIOUS_FUMES',
    'ONE_FOR_ALL', 'ORBIT', 'PAGESTORM', 'PALE_BLUE_DOT',
    'PANACHE', 'PARRY', 'PHANTOM_BLADES', 'PILLAR_OF_CREATION',
    'PREP_TIME', 'PROWESS', 'PYRE', 'REAPER_FORM',
    'ROLLING_BOULDER', 'ROYALTIES', 'RUPTURE', 'SEEKING_EDGE',
    'SENTRY_MODE', 'SERPENT_FORM', 'SHROUD', 'SLEIGHT_OF_FLESH',
    'SMOKESTACK', 'SNEAKY', 'SOULBOUND', 'SPECTRUM_SHIFT',
    'SPEEDSTER', 'SPINNER', 'SPIRIT_OF_ASH', 'STAMPEDE',
    'STONE_ARMOR', 'STORM', 'STRATAGEM', 'SUBROUTINE',
    'SWORD_SAGE', 'TANK', 'THE_SEALED_THRONE', 'THUNDER',
    'TOOLS_OF_THE_TRADE', 'TRACKING', 'TRASH_TO_TREASURE', 'TYRANNY',
    'UNMOVABLE', 'VICIOUS', 'VOID_FORM', 'WELL_LAID_PLANS',
    'WRAITH_FORM',
])

# Every kind the catalog carries, for reference and validation.
CARD_KINDS = ('Attack', 'Skill', 'Power', 'Curse', 'Status', 'Quest')


def is_power(card_id: str) -> bool:
    """True when card_id is a Power card in the bundled native catalog."""
    return card_id in POWER_CARDS
