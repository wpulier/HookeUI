"""Which running processes `make start` and `make stop` take as this checkout's: a fake process table, nothing killed."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'studio.sh'

# pgrep -f, ps -o <field>= -p <pid> and lsof -a -p <pid> -d cwd -Fn, answered from the table (pid, ppid, cwd, command).
FAKES = {
    'pgrep': '#!/bin/sh\n'
             'awk -F "\\t" \'{ print $1 "\\t" $4 }\' "$FAKE_PROCESSES" | while IFS="$(printf \'\\t\')" read -r pid cmd; do\n'
             '  printf "%s\\n" "$cmd" | grep -Eq -- "$2" && echo "$pid"\n'
             'done\n'
             'exit 0\n',
    'ps': '#!/bin/sh\n'
          'awk -F "\\t" -v pid="$4" -v field="$2" \'$1 == pid { print (field == "ppid=" ? $2 : $4); found = 1 }'
          ' END { exit !found }\' "$FAKE_PROCESSES"\n',
    'lsof': '#!/bin/sh\n'
            'awk -F "\\t" -v pid="$3" \'$1 == pid { print "p" $1; print "fcwd"; print "n" $3 }\' "$FAKE_PROCESSES"\n',
}


def table(live):
    """The live app; a test run, a sim and a `make start` in a worktree of it; two other projects, one in a folder
    whose name starts with the live checkout's (microscope-mind-analysis). Pids no real process has."""
    tree = f'{live}/.claude/worktrees/other-session'
    python = '/opt/homebrew/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python'
    return {
        9000001: (live, 'bash ./scripts/studio.sh start'),
        9000002: (f'{live}/services', f'{python} -m orchestrator.server --port 8770'),
        9000003: ('/', f'{python} {live}/experiments/iphone-camera-feed/scripts/iphone_bridge_server.py --port 8765'),
        9000004: (f'{live}/services', f'{python} -B -c from multiprocessing.resource_tracker import main;main(15)'),
        9000011: (f'{tree}/services', f'{python} -B -c from multiprocessing.spawn import spawn_main; spawn_main()'),
        9000012: (f'{tree}/services', f'{python} -B -c from multiprocessing.resource_tracker import main;main(9)'),
        9000013: ('/private/tmp/pytest-of-someone/stand-in', f'{tree}/services/.venv/bin/python -m gpu_worker.worker'),
        9000014: (f'{tree}/services', f'{python} -m sim.harness'),
        9000015: (tree, 'bash ./scripts/studio.sh start'),
        9000021: ('/Users/someone/other-project', 'node /Users/someone/other-project/node_modules/.bin/vite'),
        9000022: (f'{live}-analysis', f'node {live}-analysis/node_modules/.bin/vite --port 5190'),
    }


def matched(tmp_path, root, function):
    live = str(tmp_path / 'microscope-mind')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    for name, text in FAKES.items():
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    processes = tmp_path / 'processes.tsv'
    processes.write_text(''.join(f'{pid}\t1\t{cwd}\t{cmd}\n' for pid, (cwd, cmd) in table(live).items()))
    env = {key: value for key, value in os.environ.items() if not key.startswith('MICROMIND_')}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_PROCESSES=str(processes),
               MICROMIND_HOME=str(tmp_path / 'profile'), MICROMIND_ASSETS=str(tmp_path / 'assets'))
    returned = subprocess.run(['bash', '-c', 'source "$1"; ROOT="$2"; "$3"', 'process-check', str(SCRIPT),
                               root.replace('LIVE', live), function], env=env, text=True, capture_output=True, check=True)
    return sorted(int(pid) for pid in returned.stdout.split())


def test_the_live_checkout_leaves_its_worktrees_runs_alone(tmp_path):
    """A live restart must not end another session's pytest workers, stand-in GPU worker, sim or own launcher."""
    assert matched(tmp_path, 'LIVE', 'ours') == [9000001, 9000002, 9000003, 9000004]
    assert matched(tmp_path, 'LIVE', 'launchers') == [9000001]


def test_a_start_inside_a_worktree_still_finds_its_own_processes(tmp_path):
    tree = 'LIVE/.claude/worktrees/other-session'
    assert matched(tmp_path, tree, 'ours') == [9000011, 9000012, 9000013, 9000014, 9000015]
    assert matched(tmp_path, tree, 'launchers') == [9000015]
