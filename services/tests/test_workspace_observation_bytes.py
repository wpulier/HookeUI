import json
from copy import deepcopy

import pytest

from orchestrator.workspace_observation import Limits, ObservationError, check_target_current
from tests.test_workspace_observation import normalize, interaction_only_report, content_interaction, item, source, pinned, report


def large_windows(kind='focus'):
    message=interaction_only_report()
    message['items'].append(item('window-c','source-c'))
    message['sources'].append(source('source-c'))
    for row in message['items']:
        row['view']={'privateScientificState':'x'*24_000}
    content_interaction(message,kind)
    message['foreground']={'scope':'workspace-windows','hostId':'stage','complete':True,
        'roots':[{'rootId':row['rootId'],'z':row['z']} for row in message['items']]}
    return message


@pytest.mark.parametrize('kind',['focus','pointer','contact'])
def test_byte_admission_keeps_whole_current_window_and_truthful_omissions(kind):
    message=large_windows(kind); before=deepcopy(message)
    observed=normalize(message).wire()
    assert len(json.dumps(observed,separators=(',',':')).encode())<=Limits().normalized_bytes
    assert len(observed['items'])==2
    current=next(row for row in observed['items'] if row['id']=='window-b')
    assert current['view']==message['items'][1]['view']
    assert current['controls']==message['items'][1]['controls']
    assert observed['omitted']['items']==1 and not observed['complete']
    assert not observed['foreground']['complete']
    assert observed['foreground']['roots']==message['foreground']['roots']
    assert message==before and normalize(message).wire()==observed


def test_required_scientific_state_is_not_shortened_to_fit():
    message=large_windows();message['items'][1]['view']['privateScientificState']='x'*70_000
    with pytest.raises(ObservationError,match='REPORT_TOO_LARGE|TARGET_NOT_REPRESENTABLE'):
        normalize(message)


def test_invalid_optional_window_still_fails_validation():
    message=large_windows();message['items'][2]['view']['invalid']=float('nan')
    with pytest.raises(ObservationError,match='INVALID_REPORT'):
        normalize(message)


def test_byte_admission_keeps_cross_window_control_closure_or_omits_it_all():
    message=large_windows()
    message['items'][0]['controls'][0]['targetId']='window-c'
    message['items'][2]['controls'][0]['targetId']='window-a'
    observed=normalize(message).wire()
    assert [row['id'] for row in observed['items']]==['window-b']
    assert observed['omitted']['items']==2


def test_optional_source_payload_cannot_exhaust_normalized_budget():
    message=interaction_only_report();content_interaction(message,'focus')
    extra=source('spare');extra['provenance']['parents']=['x'*70_000]
    message['sources'].append(extra)
    observed=normalize(message).wire()
    assert observed['omitted']['sources']==1
    assert 'spare' not in {row['key'] for row in observed['sources']}


def test_causal_results_and_escaped_unicode_count_toward_exact_byte_budget():
    message=large_windows();message['applied']=[{'text':'μ'*1000}]
    baseline=normalize(message).wire()
    size=len(json.dumps(baseline,separators=(',',':')).encode())
    assert normalize(message,limits=Limits(normalized_bytes=size)).wire()==baseline
    smaller=normalize(message,limits=Limits(normalized_bytes=size-1)).wire()
    assert len(json.dumps(smaller,separators=(',',':')).encode())<=size-1
    assert sum(smaller['omitted'].values())>sum(baseline['omitted'].values())
    assert smaller['applied']==message['applied']


def test_selected_target_and_captured_control_keep_both_complete_dependencies():
    message=large_windows('pointer')
    message['targets']=report()['targets']
    message['interaction']['selected']=['target-a']
    message['interaction']['pointer']['capturedBy']='close:window-b'
    observed=normalize(message).wire()
    assert {row['id'] for row in observed['items']}=={'window-a','window-b'}
    assert observed['targets']==message['targets']
    assert observed['interaction']['pointer']['capturedBy']=='close:window-b'
    assert {row['key'] for row in observed['sources']} >= {'source-a','source-b'}


def test_required_causal_results_are_never_discarded_to_make_room():
    message=large_windows();message['commandResults']=[{'text':'x'*70_000}]
    with pytest.raises(ObservationError,match='REPORT_TOO_LARGE|TARGET_NOT_REPRESENTABLE'):
        normalize(message)


def test_byte_omissions_are_cumulative_and_cannot_prove_target_gone():
    message=large_windows();message['items'][0]['view']['privateScientificState']='x'*70_000
    message['seq']=11;message['omitted']['items']=7
    current=normalize(message)
    assert current.wire()['totals']['items']==10
    assert current.wire()['omitted']['items']==8
    with pytest.raises(ObservationError,match='OBSERVATION_INCOMPLETE'):
        check_target_current(pinned(),'target-a',current)


def test_many_optional_edges_are_bounded_without_trimming_causal_data():
    message=large_windows();message['applied']=[{'text':'x'*10_000}]
    message['edges']=[{'type':'shows','from':'window-b','to':'source-b'} for _ in range(1000)]
    observed=normalize(message).wire()
    assert 0<len(observed['edges'])<Limits().edges
    assert observed['applied']==message['applied']
    assert len(json.dumps(observed,separators=(',',':')).encode())<=Limits().normalized_bytes
    assert observed['omitted']['edges']==1000-len(observed['edges'])
