"""TEST-only consumer of an unmodified real Studio Runtime/Windows report.

Vitest supplies synthetic fetched bytes and an external decoder/DOM boundary.
Only the declared prepared-source authority is synthetic here; accepted Screen
normalization, control registration/projection and completion.compare are real.
This is not an HTTP/server/browser/model acceptance harness.
"""
from copy import deepcopy
import json
import sys

from orchestrator.completion import compare
from orchestrator.screen import Screen
from orchestrator.workspace_observation import ClientRef


def consume(value):
    raw, command = value['report'], value['command']
    client = object()
    origin = raw['client']
    ref = ClientRef(origin['workspaceId'], origin['clientId'], origin['pageEpoch'], origin['connectionEpoch'])
    admitted = {item['sourceRef']['key']: item['sourceRef'] for item in command['instances']}

    def resolve(socket, supplied):
        known = admitted.get(supplied['key'])
        return deepcopy(known) if socket is client and known == supplied else None

    screen = Screen()
    screen.clock = lambda: 10.0
    screen.accept_client(client, ref, app_run_id=raw['appRunId'],
                         source_resolver=resolve, capture_authority=lambda *_args: False)
    screen.report(raw, client=client)
    projected = screen.completion_report(client)
    fence = deepcopy(command['fence'])
    # The wire uses pageEpoch; the existing completion core uses clientEpoch.
    # Preserve the original admitted value, never heal from observed metadata.
    fence['client']['clientEpoch'] = fence['client'].pop('pageEpoch')
    expected = []
    for item in command['instances']:
        for name in value['predicates']:
            facts = {'sourceRef': item['sourceRef']} if name == 'source_exact' else {
                'each': ['close', 'zoom', 'pan'], 'independent': True}
            expected.append({'id': name + ':' + item['id'], 'predicate': name,
                'targets': [item['id']], 'expected': facts, 'essential': True,
                'requiredPhase': 'drawn', 'tolerance': None})
    return {'projected': projected, 'result': compare(expected, projected, fence, now=10.0)}


if __name__ == '__main__':
    print(json.dumps(consume(json.load(sys.stdin)), allow_nan=False))
