"""Optional frozen-workflow scope over the existing door, never another runner."""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlencode

from orchestrator.door import schema
from .scoped import ProjectReader, READS


@dataclass(frozen=True)
class WorkflowPin:
    id: str
    revision: int
    digest: str

    def __post_init__(self):
        schema.check({'session': 'personal', **vars(self)}, 'artifact-reference')


def load_pins(path: Path) -> tuple[WorkflowPin, ...]:
    """Trusted operator file, read once; no request or evidence can change it."""
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Workflow scope exceeds its configuration limit')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'format', 'workflows'}
            or type(value['format']) is not int or value['format'] != 1
            or type(value['workflows']) is not list or not 1 <= len(value['workflows']) <= 32):
        raise ValueError('Choose a bounded frozen workflow scope')
    pins = tuple(WorkflowPin(**row) for row in value['workflows'])
    if len({(p.id, p.revision) for p in pins}) != len(pins):
        raise ValueError('Duplicate frozen workflow identity')
    return pins


def finding_schema():
    shared = schema.load('claim')['properties']
    properties = {name: shared[name] for name in ('statement', 'evidence', 'dependsOn', 'limitations')}
    properties['requestKey'] = schema.load('request-key')
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def load_admissions(path: Path):
    """Optional fixed intents, not an execution ledger or a model-editable budget."""
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('Admission scope exceeds its configuration limit')
    value = json.loads(raw)
    if (type(value) is not dict or set(value) != {'format', 'admissions'}
            or type(value['format']) is not int or value['format'] != 1
            or type(value['admissions']) is not list or len(value['admissions']) > 32):
        raise ValueError('Choose a bounded admission scope')
    return value['admissions']


class ProjectResearcher(ProjectReader):
    """The operator qualifies definitions/runtimes; the agent chooses inputs and runs.

    Only the server retains execution state. Operation recovery reads the selected
    Study's original experiment links; it never reconstructs or replays work.
    """
    operation_tools = READS | {'workflow'}

    def __init__(self, config, pins, door=None, *, admissions=None):
        super().__init__(config, door)
        if not config.study or not pins or len(pins) > 32:
            raise ValueError('Research requires a selected Study and frozen workflows')
        self.pins = tuple(pins)
        if any(not isinstance(p, WorkflowPin) for p in self.pins):
            raise ValueError('Invalid workflow scope')
        if len({(p.id, p.revision) for p in self.pins}) != len(self.pins):
            raise ValueError('Duplicate frozen workflow identity')
        self._admissions = None
        if admissions is not None:
            if type(admissions) is not list or len(admissions) > 32:
                raise ValueError('Choose a bounded admission scope')
            self._admissions = {}
            for row in admissions:
                if type(row) is not dict or set(row) != {'requestKey', 'arguments'}:
                    raise ValueError('An admission needs its exact arguments and request key')
                schema.check(row['requestKey'], 'request-key')
                if row['requestKey'] in self._admissions:
                    raise ValueError('Duplicate admission request key')
                args, _ = self._arguments(row['arguments'])
                self._admissions[row['requestKey']] = self._intent(args)

    @property
    def can_admit(self):
        return self._admissions is None or bool(self._admissions)

    @staticmethod
    def _intent(args):
        return json.dumps({**args, 'bindings': args.get('bindings', {})}, sort_keys=True, allow_nan=False)

    def _pin(self, workflow, revision):
        pin = next((p for p in self.pins if p.id == workflow and p.revision == revision), None)
        if pin is None:
            raise ValueError('This workflow revision is outside the operator scope')
        return pin

    async def _definition(self, pin):
        result = await self.door.request('GET', 'workflows/' + pin.id + '?' + urlencode({
            'session': self.config.project, 'revision': pin.revision}))
        definition = result['workflow']
        if any(definition.get(k) != v for k, v in vars(pin).items()):
            raise ValueError('The frozen workflow does not match its qualified identity')
        return definition

    async def coordination(self):
        return await self.door.request('GET', 'studies/' + self.config.study + '/coordination?' + urlencode({'session': self.config.project}))

    async def coordinate(self, action, fields):
        # A scoped solver may claim and return work, but cannot alter its operator's
        # reviewed plan, grants, method scope or allowance.
        allowed = {'claim': {'assignment', 'harness', 'model'},
                   'complete': {'assignment', 'statement', 'evidence', 'limitations'}}
        if action not in allowed or type(fields) is not dict or set(fields) != allowed[action]:
            raise ValueError('Only assignment claim and completion are admitted')
        if action == 'complete':
            if type(fields['evidence']) is not list or len(fields['evidence']) > 32:
                raise ValueError('Use bounded exact evidence')
            for ref in fields['evidence']:
                self._reference(ref)
        return await self.door.request('POST', 'studies/' + self.config.study + '/coordination/' + action,
                                       {'session': self.config.project, **fields})

    async def workflows(self):
        # Explicit pins only: do not enumerate the operator's unrelated library.
        result = {'workflows': [await self._definition(pin) for pin in self.pins]}
        if self._admissions is not None:
            result['admissions'] = [{'requestKey': key, 'arguments': {
                k: v for k, v in json.loads(intent).items() if k not in ('session', 'study')}}
                for key, intent in self._admissions.items()]
            result['admissionMeaning'] = 'Only these fixed requests may run. Retrying or reconnecting recovers the same server operation.'
        return result

    def _reference(self, ref):
        schema.check(ref, 'artifact-reference')
        if set(ref) != {'session', 'id', 'revision', 'digest'} or ref['session'] != self.config.project:
            raise ValueError('Choose an exact reference in the selected Project')

    def _admission(self, args):
        schema.check(args, 'workflow-admission')
        if set(args) - {'study', 'session', 'workflow', 'revision', 'inputs', 'bindings', 'assignment'}:
            raise ValueError('Unqualified workflow arguments')
        if args['study'] != self.config.study or args['session'] != self.config.project:
            raise ValueError('The experiment belongs to another Study or Project')
        pin = self._pin(args['workflow'], args['revision'])
        for ref in args['inputs']:
            self._reference(ref)
        bindings = args.get('bindings', {})
        if len(bindings) > 64:
            raise ValueError('Too many named inputs')
        for ref in bindings.values():
            self._reference(ref)
        return pin

    def _arguments(self, arguments):
        if type(arguments) is not dict or set(arguments) - {'workflow', 'revision', 'inputs', 'bindings', 'assignment'}:
            raise ValueError('Only exact inputs to qualified frozen workflows are admitted')
        args = {**arguments, 'study': self.config.study, 'session': self.config.project}
        return args, self._admission(args)

    async def run(self, arguments, request_key):
        args, pin = self._arguments(arguments)
        schema.check(request_key, 'request-key')
        if self._admissions is not None and self._admissions.get(request_key) != self._intent(args):
            raise ValueError('This request is outside the operator admission allowance')
        if self._admissions is not None:
            # Send the same canonical body across retries and process restarts.
            args = json.loads(self._admissions[request_key])
        # Check immutable definition before admission; the server checks runtime
        # generations and input hashes. No caller-controlled parameters or code.
        await self._definition(pin)
        response = await self.door.request('POST', 'operations', {
            'tool': 'workflow', 'args': args, 'project': self.config.project, 'requestKey': request_key})
        operation = self._scoped_operation(response)
        self._operations.add(operation['id'])
        return response

    def _scoped_operation(self, response):
        operation = response['operation']
        if operation.get('tool') != 'workflow':
            return super()._scoped_operation(response)
        # A Project permission alone does not admit every run.
        super()._scoped_operation(response)
        params = operation['params']
        self._admission({**params, 'session': operation['project']})
        return operation

    async def observe(self, operation_id):
        if operation_id not in self._operations:
            # Recover only an existing link in this Study, including another
            # authorized contributor's work. The server retains its authorship.
            study = (await self.study())['study']
            linked = next((row for row in study.get('experiments', {}).values()
                           if row.get('operation') == operation_id), None)
            if linked is None:
                raise ValueError('This operation is not linked to the selected Study')
            self._admission({**linked['specification'], 'session': self.config.project})
            response = await self.door.request('GET', 'operations/' + operation_id)
            if response['operation'].get('tool') != 'workflow':
                raise ValueError('The Study link is not a workflow operation')
            self._scoped_operation(response)
            if response['operation']['params'] != linked['specification']:
                raise ValueError('The operation does not match its Study admission')
            if response['operation']['id'] != operation_id:
                raise ValueError('The original operation identity changed')
            self._operations.add(operation_id)
            return response
        response = await super().observe(operation_id)
        if response['operation']['id'] != operation_id:
            raise ValueError('The original operation identity changed')
        return response

    async def cancel(self, operation_id):
        original = await self.observe(operation_id)
        if original['operation']['tool'] != 'workflow':
            raise ValueError('This control is only for Study workflows')
        response = await self.door.request('POST', 'operations/' + operation_id + '/cancel', {})
        self._scoped_operation(response)
        if response['operation']['id'] != operation_id:
            raise ValueError('The original operation identity changed')
        return response

    async def publish(self, arguments):
        schema.validate(arguments, finding_schema())
        for evidence in arguments['evidence']:
            self._reference(evidence['artifact'])
        # Server derives author/time and validates evidence. A report is a claim,
        # not a human acceptance or an independent scientific verification.
        return await self.door.request('POST', 'studies/' + self.config.study + '/claims', {
            **arguments, 'session': self.config.project})
