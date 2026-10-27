"""Search/export via the running application's same inspection services.

PYTHONPATH=services services/.venv/bin/python -m orchestrator.diagnostics_cli search 'zoom'
PYTHONPATH=services services/.venv/bin/python -m orchestrator.diagnostics_cli export e... out.json
"""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import build_opener, ProxyHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    commands = parser.add_subparsers(dest='command', required=True)
    search = commands.add_parser('search')
    search.add_argument('query', nargs='?', default='')
    search.add_argument('--cursor')
    export = commands.add_parser('export')
    export.add_argument('receipt')
    export.add_argument('destination', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    path = ('/api/execution/history/search?' + urlencode({'q': args.query, **({'cursor': args.cursor} if args.cursor else {})})
            if args.command == 'search' else '/api/execution/' + quote(args.receipt, safe='') + '/export')
    with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{args.port}{path}', timeout=10) as response:
        data = json.load(response)
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    if args.command == 'search':
        print(text, end='')
    else:
        # Never overwrite another export or write a world-readable artifact.
        with args.destination.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
        print(f'Saved redacted local export: {args.destination}')


if __name__ == '__main__':
    main()
