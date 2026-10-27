"""One headless cell using Hooke's existing scientific console inside parent confinement."""
import json
from pathlib import Path
import sys
import types


def main():
    # Bind this namespace directly: discovering it via sys.path would require
    # listing the services directory, which the confined worker cannot read.
    package = types.ModuleType('env_worker')
    package.__path__ = [str(Path(sys.argv[1]) / 'env_worker')]
    sys.modules['env_worker'] = package
    from env_worker.scientific_console import Console
    request = json.loads(Path(sys.argv[2]).read_text())
    console = Console(None, files={key: Path(value) for key, value in request['files'].items()})
    try:
        result = console.run(request['source'])
        Path('console.json').write_text(json.dumps(result))
    finally:
        console.close()


if __name__ == '__main__':
    main()
