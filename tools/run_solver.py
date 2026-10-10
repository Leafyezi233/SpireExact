"""Manual launcher for one solver run against the CURRENT working tree.

Why this exists: tools/run_seed.py is the recorded-experiment runner. It writes to
<artifact root>/<iteration>/<name> through storage-policy.json, and that file does not
exist in the main tree, so its run_base() path cannot be used for ad-hoc work. The
dashboard runs a frozen snapshot (dashboard/solver-profile.json), which is the opposite
of what an A/B against today's edits needs. This launcher runs the live tree, defaults
its output outside experiments/, and exposes almost every solve-p5 knob.

    python tools/run_solver.py --seed 2098492050 --minutes 30
    python tools/run_solver.py --seed 2098492050 --minutes 30 --set scheduler=focus --set walker-walks=60
    python tools/run_solver.py --config my-run.json --dry-run
    python tools/run_solver.py --seed 2098492050 --minutes 30 --detach

Resource enforcement is identical to tools/run_seed.py and tools/limited_cli.py: one
Windows Job Object (8 or 16 logical CPUs, 14 GiB workload + 2 GiB reserve by default),
wall cap, 8 GB job write cap, NTFS-compressed output directory. Nothing here claims a
win: only certificate.json, written by the solver after an independent fresh replay,
counts.
"""
from pathlib import Path
import argparse, ctypes as c, datetime, json, os, subprocess, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spire_exact.planning.io import read_json, write_json
from tools.experiment_storage import prepare_storage
from tools.limited_cli import A10_LOGICAL_CPUS

NL = chr(10)
GAME_SUFFIX = Path('runtime/steamapps/common/Slay the Spire 2/data_sts2_windows_x86_64')

# Defaults mirror the hardcoded settings list in tools/run_seed.py:149-155, so a bare
# invocation stays comparable with recorded runs. --set overrides any of them.
DEFAULTS = {
    'character': 'IRONCLAD', 'ascension': '10', 'unlocks': 'all',
    'workers': '7', 'dop': '1', 'reserve-mib': '1024', 'evaluations': '1000000',
    'task-seconds': '1200', 'max-decisions': '12000', 'lookahead-actions': '12000',
    'lookahead-floors': '99', 'alternatives': '8', 'survivors': '2',
    'budget-ms': '600000', 'boss-budget-ms': '600000', 'nodes': '60000',
    'profile': 'Low', 'dispatch': 'ordered', 'dispatch-window': '56',
    'solver-seed': '271828', 'checkpoint-mib': '1024', 'cache-mib': '128',
    'archive-entries': '256',
}
# Bare switches passed with no value.
FLAGS = {'low-io', 'event-driven-settle', 'prior', 'no-continuation-reuse',
         'complete-continuations-only', 'route-prescribed', 'route-strict',
         'strict-then-search', 'measure-search-phases'}


def spawn_detached(command, log, cwd):
    """Start command outside the caller's Windows Job and process tree (run_seed.py:61-75).

    Win32_Process.Create makes the WMI provider host the parent, so restarting the
    application that launched this does not take the run down."""
    # cmd.exe /s strips one level of quotes, so the redirect target needs them doubled.
    line = ('cmd.exe /d /s /c "' + subprocess.list2cmdline([str(x) for x in command])
            + ' > ""' + str(log) + '"" 2>&1"')
    env = dict(os.environ, SPIRE_DETACH_COMMAND=line, SPIRE_DETACH_CWD=str(cwd))
    done = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
         "@{CommandLine=$env:SPIRE_DETACH_COMMAND; CurrentDirectory=$env:SPIRE_DETACH_CWD}; "
         "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId"],
        env=env, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if done.returncode:
        raise SystemExit('detached start failed: ' + (done.stderr or done.stdout).strip())
    return int(done.stdout.strip().splitlines()[-1])


def parse_overrides(pairs):
    """--set name=value, or a known bare flag. Values stay strings; argparse validates."""
    values, flags = {}, []
    for item in pairs:
        if item in FLAGS:
            flags.append(item)
            continue
        if '=' not in item:
            raise SystemExit('--set needs name=value or a known bare flag, got: ' + item)
        key, value = item.split('=', 1)
        key = key.lstrip('-')
        if key in FLAGS:
            flags.append(key)
        else:
            values[key] = value
    values['__flags__'] = flags
    return values


def merge_overrides(file_pairs, cli_pairs):
    """Combine config-file --set pairs with command-line ones; later wins.

    A bare flag collected from either source survives: the command line must not
    erase flags the config file already collected."""
    from_file = parse_overrides(list(file_pairs))
    from_cli = parse_overrides(list(cli_pairs))
    merged = dict(from_file)
    merged.update(from_cli)
    merged['__flags__'] = sorted(set(from_file['__flags__']) | set(from_cli['__flags__']))
    return merged


def build_settings(a, overrides):
    """Ordered solve-p5 argument list: seed and seconds first, bare flags last.

    Overrides replace a default in place, so a value never appears twice. An
    explicit --seconds (config or --set) wins over the minutes-derived value."""
    values = dict(DEFAULTS)
    flags = overrides.pop('__flags__', [])
    values.update(overrides)
    settings = ['--seed', a.seed]
    if 'seconds' in values:
        settings += ['--seconds', str(values.pop('seconds'))]
    else:
        settings += ['--seconds', str(max(10, a.minutes * 60 - 30))]
    for key, value in values.items():
        settings += ['--' + key, str(value)]
    for flag in flags:
        settings.append('--' + flag)
    return settings


def find_dotnet(explicit):
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit('dotnet.exe not found: ' + str(explicit))
        return Path(explicit)
    local = ROOT.parent / '.tools/dotnet/dotnet.exe'
    if local.is_file():
        return local
    system = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet/dotnet.exe'
    if system.is_file():
        return system
    raise SystemExit('dotnet.exe not found; pass --dotnet <path-to-dotnet.exe>')


def default_out_root():
    """Where a run lands when --out-root is absent.

    The workspace folder is usually named in Chinese, and the native host is a
    .NET process that receives request.json as a plain argument: a non-ASCII
    path reaches it mangled, every evaluation dies with
    NATIVE_WORKER_FAILED / System.IO.IOException, and the run burns its whole
    evaluation budget in seconds. So default to an ASCII root on the same drive.
    """
    configured = os.environ.get('SPIRE_MANUAL_ROOT')
    if configured:
        return Path(configured)
    return Path(ROOT.anchor) / 'dsh_runs' / 'manual'


def main():
    p = argparse.ArgumentParser(description='Run one solver job against the current working tree.')
    p.add_argument('--seed', required=True)
    p.add_argument('--minutes', type=int, default=None,
                   help='wall clock cap, 1..180 (protocol cap); may come from --config')
    p.add_argument('--name', default=None, help='output folder name (default: seed-<seed>-<stamp>)')
    p.add_argument('--out-root', type=Path, default=None,
                   help='parent of the run folder (default: experiments/manual)')
    p.add_argument('--config', type=Path, default=None,
                   help='JSON object with any of these options; CLI flags win')
    p.add_argument('--game-dir', type=Path, default=None, help='default: ' + str(GAME_SUFFIX))
    p.add_argument('--dotnet', type=Path, default=None, help='dotnet.exe placed first on PATH')
    p.add_argument('--protocol', choices=sorted(A10_LOGICAL_CPUS), default=None,
                   help='solver protocol; picks the enforced logical-CPU count (default: --logical-cpus decides)')
    p.add_argument('--logical-cpus', type=int, choices=[6, 8, 16], default=8)
    p.add_argument('--cpu-list', default=None, metavar='N,N,...',
                   help='explicit logical CPUs to bind (default: the fastest class with --logical-cpus free CPUs)')
    p.add_argument('--workers', type=int, default=7)
    p.add_argument('--job-memory-mib', type=int, default=14336)
    p.add_argument('--resource-note', default='', help='authorization note required for a resource override')
    p.add_argument('--solver-seed', type=int, default=None)
    p.add_argument('--detach', action='store_true', help='return at once; the run survives this application')
    p.add_argument('--dry-run', action='store_true', help='print the command and exit')
    p.add_argument('--set', dest='sets', action='append', default=[], metavar='NAME=VALUE',
                   help='repeatable extra solve-p5 arg, e.g. --set scheduler=focus --set walker-walks=60 --set prior')
    a = p.parse_args()

    config = read_json(a.config) if a.config else {}
    if not isinstance(config, dict):
        raise SystemExit('--config must be a JSON object')
    # A config file supplies defaults for the resource knobs too; an explicit
    # command-line flag always wins.
    for dest, flag, cast in (('minutes', '--minutes', int), ('workers', '--workers', int),
                             ('logical_cpus', '--logical-cpus', int),
                             ('job_memory_mib', '--job-memory-mib', int),
                             ('protocol', '--protocol', str), ('name', '--name', str),
                             ('out_root', '--out-root', Path), ('game_dir', '--game-dir', Path),
                             ('dotnet', '--dotnet', Path), ('solver_seed', '--solver-seed', int),
                             ('resource_note', '--resource-note', str)):
        if dest not in config or flag in sys.argv:
            continue
        value = config[dest]
        setattr(a, dest, cast(value) if not isinstance(value, Path) else Path(value))
    if a.minutes is None:
        raise SystemExit('--minutes is required on the command line or in --config')
    if not 1 <= a.minutes <= 180:
        raise SystemExit('minutes must be within the 180 min protocol cap')
    logical_cpus = A10_LOGICAL_CPUS[a.protocol] if a.protocol else a.logical_cpus
    if not 1 <= a.workers <= logical_cpus or not 1024 <= a.job_memory_mib <= 28672:
        raise SystemExit('workers must fit CPUs; job memory must be within 1024..28672 MiB')
    if (logical_cpus != 8 or a.workers != 7 or a.job_memory_mib != 14336) and not a.resource_note.strip():
        raise SystemExit('resource overrides need an explicit --resource-note')
    cpu_list = a.cpu_list
    if cpu_list is None and 'cpu_list' in config:
        raw = config['cpu_list']
        cpu_list = ','.join(str(int(x)) for x in raw) if isinstance(raw, (list, tuple)) else str(raw)
    if cpu_list:
        cpu_list = sorted({int(x) for x in str(cpu_list).split(',')})
        if len(cpu_list) != logical_cpus:
            raise SystemExit('--cpu-list must name exactly the enforced logical-CPU count')

    overrides = merge_overrides(list(config.get('set', [])), a.sets)
    if a.solver_seed is not None:
        overrides['solver-seed'] = str(a.solver_seed)
    # Enforced-resource knobs are chosen by this launcher, so they must reach
    # solve-p5 as well; otherwise --workers 6 would still run 7 workers.
    overrides['workers'] = str(a.workers)
    if 'worker_memory_mib' in config:
        overrides['worker-memory-mib'] = str(config['worker_memory_mib'])
    if 'seconds' in config:
        overrides['seconds'] = str(config['seconds'])
    for key in ('character', 'ascension', 'unlocks'):
        if key in config:
            overrides[key] = str(config[key])

    protocol = a.protocol or ('A10-seed-v2-scaling16' if logical_cpus == 16 else 'A10-seed-v2')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    name = a.name or 'seed-' + a.seed + '-' + str(a.minutes) + 'm-' + stamp
    root = (a.out_root or default_out_root()).resolve()
    folder = (root / name).resolve()
    target = folder / ('seed-' + a.seed)
    if folder.exists() and any(folder.iterdir()):
        raise SystemExit('output folder exists and is not empty; never overwrite prior evidence')
    # The native host is a .NET process that receives request.json as a plain
    # argument; a non-ASCII path reaches it mangled and every evaluation dies
    # with NATIVE_WORKER_FAILED / System.IO.IOException. Fail here instead.
    if not str(folder).isascii():
        raise SystemExit('output path must be ASCII, the native host cannot open it: ' + str(folder)
                         + NL + 'pass --out-root with an ASCII directory, e.g. --out-root D:\\dsh_runs')

    settings = build_settings(a, overrides)
    game_dir = Path(a.game_dir or (ROOT / GAME_SUFFIX)).resolve()
    cmd = [sys.executable, str(ROOT / 'tools/limited_cli.py'), 'solve-p5',
           '--out', str(target), '--game-dir', str(game_dir)] + settings

    if a.dry_run:
        print(json.dumps({'cmd': cmd, 'out': str(target), 'protocol': protocol,
                          'wall_seconds': a.minutes * 60, 'settings': settings},
                         ensure_ascii=False, indent=2))
        return

    if a.detach:
        root.mkdir(parents=True, exist_ok=True)
        log = root / (name + '.launcher.log')
        own = [x for x in sys.argv[1:] if x != '--detach']
        pid = spawn_detached([sys.executable, str(Path(__file__).resolve())] + own, log, ROOT)
        print(json.dumps({'detached_pid': pid, 'run': str(folder), 'launcher_log': str(log)}))
        return

    if not game_dir.is_dir():
        raise SystemExit('game data directory not found: ' + str(game_dir))
    dotnet = find_dotnet(a.dotnet)
    os.environ['PATH'] = str(dotnet.parent) + os.pathsep + os.environ.get('PATH', '')
    os.environ['DOTNET_ROOT'] = str(dotnet.parent)

    from tools.cpu_topology import inventory, homogeneous_cpus
    from tools.rolling_storage import admission, logical_bytes
    if cpu_list:
        rows = [r for r in inventory() if r['logical_cpu'] in cpu_list]
        classes = {r['efficiency_class'] for r in rows}
        if len(rows) != logical_cpus or len(classes) != 1:
            raise SystemExit('--cpu-list names unavailable or mixed-class processors: ' + str(cpu_list))
        cpus, cls = cpu_list, classes.pop()
    else:
        cpus, cls = homogeneous_cpus(inventory(), logical_cpus, None)
    k = c.WinDLL('kernel32', use_last_error=True)
    k.GetCurrentProcess.restype = c.c_void_p
    k.SetProcessAffinityMask.argtypes = [c.c_void_p, c.c_size_t]
    if not k.SetProcessAffinityMask(k.GetCurrentProcess(), sum(1 << i for i in cpus)):
        raise c.WinError(c.get_last_error())

    admission(ROOT)
    folder.mkdir(parents=True)
    prepare_storage(folder)

    from spire_exact.native import build_host
    from tools.experiment import version_hash
    built = subprocess.run(
        [sys.executable, '-c',
         'import sys;from pathlib import Path;from spire_exact.native import build_host;'
         'print(build_host(Path(sys.argv[1]))[2]["host_sha256"])', str(game_dir)],
        cwd=ROOT, capture_output=True, text=True)
    if built.returncode:
        raise SystemExit('native host build failed:' + NL + built.stdout + built.stderr)

    manifest = {
        'run_id': name, 'kind': 'manual_interactive', 'not_a_holdout_result': True,
        'timestamp': datetime.datetime.now().astimezone().isoformat(),
        'protocol': protocol, 'seed': a.seed,
        'character': overrides.get('character', DEFAULTS['character']),
        'ascension': overrides.get('ascension', DEFAULTS['ascension']),
        'wall_cap_seconds': a.minutes * 60, 'logical_cpus': logical_cpus,
        'workload_bytes': a.job_memory_mib * 1024 ** 2, 'os_reserve_bytes': 2 * 1024 ** 3,
        'memory_protocol_override': a.resource_note or 'user authorized 16 GiB total; 14 GiB workload plus 2 GiB reserve',
        'version': version_hash(), 'host_sha256': built.stdout.strip().splitlines()[-1],
        'workspace': str(ROOT), 'workspace_frozen': False,
        'settings': settings, 'overrides': overrides, 'cpu_set': cpus, 'efficiency_class': cls,
        'old_prefixes_loaded': False,
    }
    if a.resource_note:
        manifest['resource_protocol_override'] = {
            'logical_cpus': logical_cpus, 'requested_workers': a.workers, 'cpu_list': cpu_list,
            'job_memory_mib': a.job_memory_mib, 'authorization': a.resource_note}
    write_json(folder / 'validation-manifest.json', manifest)

    env = dict(os.environ, SPIRE_PROTOCOL=protocol,
               SPIRE_WALL_LIMIT_SECONDS=str(a.minutes * 60), PYTHONIOENCODING='utf-8')
    if a.job_memory_mib != 14336:
        env['SPIRE_JOB_MEMORY_MIB'] = str(a.job_memory_mib)
        env['SPIRE_RESOURCE_OVERRIDE_NOTE'] = a.resource_note
    started = time.perf_counter()
    code = subprocess.run(cmd, cwd=ROOT, env=env).returncode
    wall = time.perf_counter() - started

    result = read_json(target / 'result.json') if (target / 'result.json').exists() else {}
    verified = (code == 0 and result.get('status') == 'VERIFIED_WIN_IN_NATIVE_HOST'
                and (target / 'certificate.json').is_file())
    report = {'protocol': protocol, 'seed': a.seed, 'run': name, 'verified_win': verified,
              'censored': not verified, 'exit_code': code, 'wall_seconds': wall,
              'status': result.get('status'), 'result_json': str(target / 'result.json'),
              'certificate': str(target / 'certificate.json') if verified else None,
              'file_bytes': logical_bytes(folder), 'normal_godot_verified': False,
              'not_a_seed_infeasibility_proof': True, 'user_interactive': True}
    write_json(folder / 'baseline-report.json', report)
    print(json.dumps({k: report[k] for k in ('seed', 'verified_win', 'exit_code', 'wall_seconds', 'status')}))


if __name__ == '__main__':
    main()
