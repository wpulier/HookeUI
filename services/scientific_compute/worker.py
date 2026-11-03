"""One confined operation. No connection, scheduling, retries or network authority."""
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
    # macOS does not provide a usable RLIMIT_AS; the local parent supervises RSS.
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'], limits['memory_bytes']))
    started = time.monotonic()
    from engine import OPERATIONS
    operation = OPERATIONS[request['capability']]
    output, summary = operation({k: Path(v) for k, v in request['inputs'].items()}, request['params'],
                                Path(request['output']), lambda: None)
    if 'resources' in request:
        summary = {**summary, 'workerResources': limits,
                   'workerUsage': {'elapsedSeconds': time.monotonic() - started,
                                   'peakRssBytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1024 if sys.platform == 'linux' else 1)}}
    Path(request['receipt']).write_text(json.dumps({'file': output.name, 'summary': summary}, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
