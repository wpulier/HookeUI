"""One confined operation. No connection, scheduling, retries or network authority."""
import json
from pathlib import Path
import resource
import sys


def main():
    # macOS does not provide a usable RLIMIT_AS here; the parent supervises RSS.
    resource.setrlimit(resource.RLIMIT_CPU, (3600, 3600))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 << 30, 64 << 30))
    request = json.loads(Path(sys.argv[1]).read_text())
    from engine import OPERATIONS
    operation = OPERATIONS[request['capability']]
    output, summary = operation({k: Path(v) for k, v in request['inputs'].items()}, request['params'],
                                Path(request['output']), lambda: None)
    Path(request['receipt']).write_text(json.dumps({'file': output.name, 'summary': summary}, allow_nan=False, sort_keys=True))


if __name__ == '__main__':
    main()
