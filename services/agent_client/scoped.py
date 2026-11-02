"""Project-bound read transport. No credentials, scientific execution or storage here."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit

from orchestrator.door import schema


# Transport permissions, not a catalog of scientific methods. Default no: adding
# a read requires examining its indirect access as well as its argument schema.
READS = frozenset({'project.state', 'artifact.read'})
ARGUMENTS = {'project.state': frozenset({'cursor', 'limit'}),
             'artifact.read': frozenset({'reference', 'offset', 'length'})}
MAX_REPLY = 2 * 1024 * 1024


@dataclass(frozen=True)
class Configuration:
    cli: Path
    agent: str
    connection: str
    project: str
    door_url: str
    study: str | None = None

    def __post_init__(self):
        if not self.cli.is_absolute() or not self.cli.is_file():
            raise ValueError('Choose the existing absolute Hooke CLI path')
        if self.agent not in ('codex', 'claude-code', 'cursor', 'other'):
            raise ValueError('Choose a supported connection kind')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', self.connection):
            raise ValueError('Choose a named connection')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,239}', self.project):
            raise ValueError('Choose a Project identity')
        if self.study is not None and not re.fullmatch(r'p[a-f0-9]{32}', self.study):
            raise ValueError('Choose a retained Study identity')
        url = urlsplit(self.door_url)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment):
            raise ValueError('The door must be a loopback HTTP origin')


class Door:
    """Use the same Keychain CLI as a person; do not read its credential ourselves."""
    def __init__(self, config: Configuration):
        self.config = config

    async def request(self, method: str, path: str, body=None) -> dict:
        config = self.config
        args = [str(config.cli), 'api', method, path]
        if body is not None:
            args.append(json.dumps(body, allow_nan=False))
        args += ['--agent', config.agent, '--connection', config.connection, '--json']
        env = {k: os.environ[k] for k in ('HOME', 'USER', 'LOGNAME', 'PATH', 'TMPDIR') if k in os.environ}
        env['HOOKE_DOOR_URL'] = config.door_url
        process = await asyncio.create_subprocess_exec(*args, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            async with asyncio.timeout(40):
                raw = bytearray()
                while chunk := await process.stdout.read(min(65536, MAX_REPLY + 1 - len(raw))):
                    raw.extend(chunk)
                    if len(raw) > MAX_REPLY:
                        raise ValueError('Hooke response exceeded the client limit; use a smaller page')
                await process.wait()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Hooke returned an invalid response')
            if process.returncode or result.get('ok') is not True:
                # Do not echo raw CLI output, environment or subprocess diagnostics.
                raise ValueError('Hooke refused the request; check connection access and service readiness')
            return result
        except (TimeoutError, json.JSONDecodeError) as error:
            raise ValueError('Hooke did not return a complete response; admitted work is not restarted') from error
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()


def inline_schema(value, root=None, seen=()):
    """Resolve only the shared local schemas; never fetch a schema URL."""
    if not isinstance(value, dict):
        return [inline_schema(v, root, seen) for v in value] if isinstance(value, list) else value
    root = value if root is None else root
    if '$ref' in value:
        ref = value['$ref']
        if ref in seen or len(seen) >= 12:
            raise ValueError('Unsupported recursive input schema')
        if ref.startswith('#/$defs/'):
            return inline_schema(root['$defs'][ref[8:]], root, (*seen, ref))
        name = ref.removesuffix('.schema.json')
        if ref != name + '.schema.json' or name not in schema.NAMES:
            raise ValueError('Unsupported external input schema')
        target = schema.load(name)
        return inline_schema(target, target, (*seen, ref))
    return {k: inline_schema(v, root, seen) for k, v in value.items() if k != '$id'}


class ProjectReader:
    """Immutable operator scope over dynamically discovered door read contracts.

    This narrows the client's tools. It is not a sandbox for the caller, nor a
    replacement for server grants. The operator must restrict alternate access.
    """
    operation_tools = READS

    def __init__(self, config: Configuration, door=None):
        self.config = config
        self.door = door or Door(config)
        self._operations: set[str] = set()  # readable handles, never execution state

    async def descriptions(self) -> list[dict]:
        found = {}
        for page in range(1, 21):
            result = await self.door.request('GET', f'tools?page={page}&perPage=100')
            if result.get('contract') != '1' or result.get('page') != page:
                raise ValueError('Unsupported Hooke discovery response')
            for row in result['tools']:
                if row['id'] in READS:
                    if row['id'] in found:
                        raise ValueError('Duplicate Hooke tool declaration')
                    found[row['id']] = row
            if page >= result['pages']:
                return list(found.values())
        raise ValueError('Hooke discovery exceeds the client page limit')

    async def read(self, name: str, arguments: dict, request_key: str) -> dict:
        if name not in READS:
            raise ValueError('This action is not admitted by the Project read client')
        if not isinstance(arguments, dict) or set(arguments) - ARGUMENTS[name]:
            raise ValueError('These arguments have not been qualified for this client')
        rows = {r['id']: r for r in await self.descriptions()}
        if name not in rows or rows[name]['state'] != 'available':
            raise ValueError('This read is not currently available in Hooke')
        schema.validate(arguments, rows[name]['inputSchema'])
        schema.check(request_key, 'request-key')
        if name == 'artifact.read':
            reference = arguments['reference']
            if set(reference) != {'id', 'session', 'revision', 'digest'}:
                raise ValueError('This reference shape has not been qualified for this client')
            if reference['session'] != self.config.project:
                raise ValueError('The reference belongs to another Project')
        response = await self.door.request('POST', 'operations', {
            'tool': name, 'args': arguments, 'project': self.config.project, 'requestKey': request_key})
        operation = self._scoped_operation(response)
        self._operations.add(operation['id'])
        # Return the handle promptly; subsequent calls observe this same execution.
        return response

    def _scoped_operation(self, response: dict) -> dict:
        operation = response['operation']
        if operation.get('project') != self.config.project or operation.get('tool') not in self.operation_tools:
            raise ValueError('Hooke returned an operation outside this client scope')
        if not re.fullmatch(r'op_[a-f0-9]{24}', operation.get('id', '')):
            raise ValueError('Hooke returned an invalid operation identity')
        return operation

    async def observe(self, operation_id: str) -> dict:
        if operation_id not in self._operations:
            raise ValueError('Only read operations admitted by this client may be observed')
        response = await self.door.request('GET', 'operations/' + operation_id)
        self._scoped_operation(response)
        return response

    async def study(self) -> dict:
        if self.config.study is None:
            raise ValueError('No Study was selected by the operator')
        return await self.door.request('GET', 'studies/' + self.config.study + '?' +
                                       urlencode({'session': self.config.project}))
