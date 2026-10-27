import json

from orchestrator.execution_export import redacted


def test_export_omits_private_content_and_preserves_step_relationships():
    detail = {'status': 'available', 'root': {'id': 'private-root', 'at': 123, 'outcome': 'failed', 'thread': 'private-thread'},
              'context': {'words': 'private-words', 'owner': {'grant': 'private-grant'},
                          'build': {'revision': 'abc', 'secret': 'private-build'}},
              'steps': [{'id': 'private-step', 'parent': 'private-root', 'operation': 'fetch', 'ms': 34,
                         'url': 'private-url', 'error': 'private-error'}], 'marker': {'note': 'private-note'}}
    exported = redacted(detail)
    assert 'private-' not in json.dumps(exported)
    assert exported['steps'][0]['parent'] == exported['root']['id']
    assert exported['steps'][0]['ms'] == 34
    assert exported['build'] == {'revision': 'abc'}
    assert redacted(detail)['root']['id'] != exported['root']['id']
