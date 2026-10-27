"""Real local resource HTTP reads, synthetic retained data; no model or camera."""
import json

from tests.test_workspace_transport import api


def test_resource_pages_reconstruct_complete_rich_content_and_refuse_revision_mix(tmp_path):
    web, http = api(tmp_path)
    app = web.state.app
    entry = app.hub.resources.results.add('compare', {}, 'A complete synthetic finding.',
        values={'observations': [{'cell': i, 'note': 'Microscopy µm 数据 ' * 20} for i in range(100)]},
        request='Retain all evidence', thread=app.day.active_id)
    url = '/api/resources/' + entry['id']
    # The canonical reading a researcher reads (its view, ticket 66): what the pages reconstruct and their revision.
    whole = http.get('/for-researchers' + url).json()
    # The studio's reading of the same entry adds the listing's fields and how it was made, never to the pages.
    studio = http.get(url).json()
    assert set(studio) == {'entry', 'detail', 'how'} and studio['detail'] == whole['detail']
    assert {k: v for k, v in studio['entry'].items() if k not in ('role', 'maker', 'run')} == whole['entry']
    offset, revision, fragments = 0, None, []
    while True:
        args = {'offset': offset}
        if revision is not None:
            args['revision'] = revision
        response = http.get(url, params=args)
        assert response.status_code == 200
        page = response.json()
        assert len(page['text']) <= 8000
        fragments.append(page['text'])
        revision = page['page']['revision']
        offset = page['page']['nextOffset']
        if offset is None:
            break
    assert len(fragments) > 1
    assert json.loads(''.join(fragments)) == whole
    assert http.get(url, params={'offset': 8000}).status_code == 400
    assert http.get(url, params={'offset': 8000, 'revision': 'stale-revision'}).status_code == 409
    assert http.get(url, params={'offset': -1}).status_code == 400


def test_model_read_pages_reach_the_last_observation_without_silent_truncation(tmp_path):
    import pytest
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.untrusted import strip

    web, _ = api(tmp_path)
    index = web.state.app.hub.resources
    entry = index.results.add('compare', {}, 'All observations.',
        values={'observations': ['first'] + ['细胞 µm ' * 20 for _ in range(150)] + ['LAST OBSERVATION']},
        request='Read all observations', thread=web.state.app.day.active_id)
    rid = entry['id']
    complete = index.read_full(rid)
    pieces, offset, revision = [], 0, None
    while True:
        reading = index.read(rid, offset=offset, revision=revision)
        metadata, wrapped = reading.split('\n', 1)
        page = json.loads(metadata.removeprefix('Resource page: '))
        pieces.append(wrapped.split('\n', 1)[1].rsplit('\n', 1)[0])
        offset, revision = page['nextOffset'], page['revision']
        if offset is None:
            break
    assert len(pieces) > 1
    assert ''.join(pieces) == strip(complete)
    assert 'LAST OBSERVATION' in pieces[-1]
    with pytest.raises(ToolFailed, match='original revision'):
        index.read(rid, offset=12000)
    with pytest.raises(ToolFailed, match='changed'):
        index.read(rid, offset=12000, revision='older-reading')
