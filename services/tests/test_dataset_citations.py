import json
from orchestrator.datasets.citations import recorded_models


def test_uses_recorded_model_not_settings_or_prose_and_does_not_invent_citation(tmp_path):
    events = [
        {'stream': 'settings', 'record': {'model': 'never-used'}},
        {'stream': 'interaction', 'record': {'model': 'gemini-3.8-flash', 'text': 'edgetam'}},
        {'stream': 'interaction', 'record': {'model': 'gemini-3.8-flash'}},
        {'stream': 'interaction', 'record': {'text': 'using some-model'}},
    ]
    (tmp_path / 'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    facts = recorded_models(tmp_path)
    assert facts['complete']
    assert len(facts['models']) == 1
    row = facts['models'][0]
    assert row['model'] == 'gemini-3.8-flash' and row['events'] == 2
    assert row['citation'] is None and row['version'] is None and row['sources']


def test_bounded_or_torn_dataset_is_reported_incomplete(tmp_path):
    path = tmp_path / 'events.jsonl'
    path.write_text('{"stream":"interaction","record":{"model":"unknown-model"}}\n'*3)
    assert not recorded_models(tmp_path, line_limit=1)['complete']
    assert not recorded_models(tmp_path, byte_limit=5)['complete']
    path.write_text(path.read_text() + '{"torn":')
    assert not recorded_models(tmp_path)['complete']
    path.unlink()
    assert not recorded_models(tmp_path)['complete']
