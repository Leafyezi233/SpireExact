"""DP route plugin modes: off / guide / force / force-then-search.

The local tree's whole-campaign DP route planner (route.py + walker.py +
analyzer.py) is a PLUGIN on top of the upstream i100 solver, not a change to
it. This module is its single switch, and it is deliberately orthogonal to
final_defaults.py's --feature-profile: that table decides how the UPSTREAM
search runs, this one decides whether the LOCAL DP planner participates and
how hard it is allowed to steer.

    off                the upstream i100 entry, unchanged. Every DP knob keeps
                       its upstream-visible value and no DP request field is
                       sent to the native host.
    guide              soft steering. The DP planner still runs and its plan is
                       an ALLOCATION signal only: the route's take log becomes
                       extra policy_prior tiers, the best route's map actions
                       become the host's +500 map preference, the reconnaissance
                       death rates become route_hints, and the top-N routes
                       restrict which map branches the scheduler queues. The
                       search itself stays the upstream failure-directed search.
    force              hard steering. The search becomes one evaluation per
                       prescribed route, best plan score first, and each
                       evaluation's card / removal / event menus are FORCED by
                       the route's per-act strict label tiers (+/-8, the host
                       channel limit). No failure-directed branching.
    force-then-search  force first, then continue the upstream failure-directed
                       search on top of its outcome. The best strict route keeps
                       its map preference; the forcing tiers are dropped.

Every mode is allocation only: a tier never changes legality, never excludes an
option from the macro search and never bounds anything (see policies.py and
route.py). 'force' is the one mode that REPLACES the search shape, which is why
it also drops learned priors and rejects --root-policies (search.py validation).

Usage (launcher / manifest), mirroring final_defaults:

    from .dp_defaults import resolve_route_mode
    args = resolve_route_mode(args)

An explicit command-line value always wins over the mode table, exactly like
final_defaults.resolve_entry_defaults.
"""
from __future__ import annotations

ROUTE_MODES = ('off', 'guide', 'force', 'force-then-search')
# The default is 'off': the upstream search stays the default entry (user
# requirement 2026-10-09), and a DP run has to ask for its mode explicitly.
PUBLIC_ROUTE_MODE = 'off'

# Values of the DP knobs per mode. Keys are SearchConfig field names, so the
# table is checkable against the dataclass rather than kept in sync by hand.
ROUTE_MODE_TABLE = {
    'off': {
        'analyzer_rollouts': 0,
        'walker_walks': 0,
        'route_prescribed': False,
        'route_top_n': 1,
        'route_strict': False,
        'strict_then_search': False,
    },
    # Soft steering: plan, then let the ordinary search use the plan.
    'guide': {
        'analyzer_rollouts': 0,
        'walker_walks': 30,
        'route_prescribed': True,
        'route_top_n': 1,
        'route_strict': False,
        'strict_then_search': False,
    },
    # Hard steering: the strict prescription IS the search.
    'force': {
        'analyzer_rollouts': 0,
        'walker_walks': 30,
        'route_prescribed': False,
        'route_top_n': 5,
        'route_strict': True,
        'strict_then_search': False,
    },
    # Hard steering, then back to the ordinary search.
    'force-then-search': {
        'analyzer_rollouts': 0,
        'walker_walks': 30,
        'route_prescribed': False,
        'route_top_n': 5,
        'route_strict': True,
        'strict_then_search': True,
    },
}

# The DP knobs a mode may fill in. --route-top-n / --route-archetypes /
# --reuse-route-plan stay caller-controlled; the mode only sets their defaults
# when the caller left them at None.
MODE_FIELDS = tuple(sorted(ROUTE_MODE_TABLE['off']))

# Knobs that argparse must leave at None so a mode (or the user) can resolve
# them. A concrete argparse default would silently beat the mode table.
NONE_DEFAULTS = MODE_FIELDS


def route_mode_argv(mode: str) -> list[str]:
    """The mode as explicit planner arguments, for a launcher or a manifest.

    Round-trippable: passing this argv back to the planner reproduces the same
    SearchConfig. The switch flags are store_true, so a False value is rendered
    by NOT passing the flag (the mode table and resolve_route_mode restore it);
    only numeric knobs are rendered with their value.
    """
    if mode not in ROUTE_MODES:
        raise ValueError('unknown route mode: ' + str(mode))
    argv = ['--route-mode', mode]
    for key, value in ROUTE_MODE_TABLE[mode].items():
        flag = '--' + key.replace('_', '-')
        if value is True:
            argv.append(flag)
        elif value is False:
            continue
        else:
            argv += [flag, str(value)]
    return argv


def resolve_route_mode(args, explicit=()):
    """Fill the DP knobs from --route-mode, then reconcile the search shape.

    Only a knob still at None is filled, so an explicit command-line value
    always wins over the mode table (the final_defaults rule). A caller that
    never passes --route-mode gets 'off': the upstream entry, unchanged.

    A DP mode steers through the SCHEDULER, which only the weighted and dfs
    schedulers expose, while the upstream i100 entry runs the focus scheduler
    with the gate clinic. A non-off mode therefore has to reconcile the two:
    it turns off the clinic and selects a route-capable scheduler, but ONLY
    where the user did not say otherwise on the command line. Every change it
    makes is returned in `changes` so the run can record what the mode did
    instead of silently rewriting the entry.
    """
    mode = getattr(args, 'route_mode', None) or PUBLIC_ROUTE_MODE
    if mode not in ROUTE_MODES:
        raise ValueError('unknown route mode: ' + str(mode))
    args.route_mode = mode
    # dest names (argparse), not option strings: 'clinic', 'scheduler', ...
    explicit = set(explicit)
    for key, value in ROUTE_MODE_TABLE[mode].items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)
    # Explicit route flags imply the mode they belong to, so the two spellings
    # cannot disagree: --route-strict is 'force', --route-prescribed is 'guide'.
    if mode == 'off':
        if getattr(args, 'route_strict', False) or getattr(args, 'strict_then_search', False):
            args.route_mode = mode = 'force-then-search' if args.strict_then_search else 'force'
        elif getattr(args, 'route_prescribed', False) or getattr(args, 'walker_walks', 0):
            args.route_mode = mode = 'guide'
    changes = {}
    if mode != 'off':
        # The clinic and the DP route filter both decide which evaluations get
        # the budget, and the clinic is focus-scheduler-only. An explicit
        # --clinic stays and the validation below rejects the combination.
        if 'clinic' not in explicit and getattr(args, 'clinic', False):
            args.clinic = False
            args.clinic_hints = False
            if getattr(args, 'strategy_prior', 'off') != 'off':
                args.strategy_prior = 'off'
            changes['clinic'] = False
            changes['strategy_prior'] = 'off'
        if 'scheduler' not in explicit and getattr(args, 'scheduler', None) not in ('weighted', 'dfs'):
            args.scheduler = 'weighted'
            changes['scheduler'] = 'weighted'
        # The upstream i100 entry also carries the i085 macro allocation
        # switches and the tail search, and BOTH are focus-scheduler-only.
        # A route-capable scheduler has to turn them off; an explicit
        # --macro-* / --tail-mode is left alone and rejected by the caller.
        for key in ('macro_plateau', 'macro_widening', 'macro_fair'):
            if key not in explicit and getattr(args, key, False):
                setattr(args, key, False)
                changes[key] = False
        if 'macro_routes' not in explicit and getattr(args, 'macro_routes', 'off') != 'off':
            args.macro_routes = 'off'
            changes['macro_routes'] = 'off'
        if 'tail_mode' not in explicit and getattr(args, 'tail_mode', 'off') != 'off':
            args.tail_mode = 'off'
            changes['tail_mode'] = 'off'
        if 'focus_stall' not in explicit and getattr(args, 'focus_stall', 0):
            args.focus_stall = 0
            changes['focus_stall'] = 0
        # 'force' produces its own card tiers; the upstream i100 entry carries
        # --root-policies pick,elo, which would dilute them (search.py).
        if mode in ('force', 'force-then-search') and 'root_policies' not in explicit and getattr(args, 'root_policies', ''):
            args.root_policies = ''
            changes['root_policies'] = ''
    args.route_mode_changes = changes
    return args


def describe(mode: str) -> str:
    """One line for the run manifest and the result file."""
    return {
        'off': 'DP route plugin off: the upstream i100 entry, unchanged',
        'guide': 'DP route plugin guide: plan as an allocation signal, upstream search shape kept',
        'force': 'DP route plugin force: one evaluation per prescribed route, menus forced by strict tiers',
        'force-then-search': 'DP route plugin force-then-search: strict pass first, then the upstream failure-directed search',
    }[mode]


def detect_route_workers() -> int:
    """Size the DP planner's process pool to the machine's PHYSICAL P cores.

    The planner scores its enumerated map paths in parallel (route.py). The
    score is a pure function, so the worker count moves the wall clock only.
    Logical SMT siblings share a core's execution units and the slower
    efficiency class would drag the pool down, so neither is counted: the
    answer is the number of distinct physical cores in the FASTEST efficiency
    class, which Windows reports through CPU Sets (tools/cpu_topology).

    Anything that goes wrong -- a non-Windows host, a missing tools package,
    an odd topology -- falls back to 1, the historical serial loop, so a
    hardware probe can never break a run.
    """
    try:
        from tools.cpu_topology import inventory
        rows = inventory()
    except Exception:
        return 1
    classes = sorted({row.get('efficiency_class') for row in rows if row.get('group') == 0},
                     reverse=True)
    for cls in classes:
        cores = {row.get('core_index') for row in rows
                 if row.get('group') == 0 and row.get('efficiency_class') == cls
                 and (not row.get('allocated') or row.get('allocated_to_process'))}
        cores.discard(None)
        if cores:
            return max(1, len(cores))
    return 1
