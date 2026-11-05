"""One confined folder capability (ADR 0031). No connection, scheduling, retries or network authority.

The request names a reviewed catalog folder and its `module:function`. The function is called as
`function(inputs, params, output, check)` with materialized input paths by port, checked parameters
and its own output directory, and returns `(result_path, summary)` like an engine operation.
Only the folder is put on the import path: it shares no code with engine.py.
"""
import importlib
import json
from pathlib import Path
import resource
import sys
import time


def main():
    request = json.loads(Path(sys.argv[1]).read_text())
    import resource_profile
    resource_profile.configure(request.get('resources', resource_profile.DEFAULT))
    limits = resource_profile.current()
    resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'], limits['cpu_seconds']))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits['file_bytes'], limits['file_bytes']))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'], limits['memory_bytes']))
    started = time.monotonic()
    # The host package is not importable from here: a folder capability cannot reach engine.py.
    host = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if not p or Path(p).resolve() != host]
    folder = Path(request['folder'])
    module_name, _, function_name = request['adapter'].partition(':')
    if not module_name.isidentifier() or not function_name.isidentifier():
        raise ValueError('A folder capability adapter is module:function beside its manifest')
    sys.path.insert(0, str(folder))
    operation = getattr(importlib.import_module(module_name), function_name)
    output, summary = operation({k: Path(v) for k, v in request['inputs'].items()}, request['params'],
                                Path(request['output']), lambda: None)
    summary = {**summary, 'workerResources': limits,
               'workerUsage': {'elapsedSeconds': time.monotonic() - started,
                               'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform == 'linux' else 1)}}
    Path(request['receipt']).write_text(json.dumps({'file': Path(output).name, 'summary': summary}, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
