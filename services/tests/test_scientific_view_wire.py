import copy
import json
import io
from pathlib import Path

import pytest

from env_worker import scientific_view_wire as wire
from orchestrator import scientific_host

CASES = json.loads((Path(__file__).resolve().parents[2] / 'contract/scientific-view-cases.json').read_text())


def test_view_descriptor_is_detached_and_parent_admits_exact_pixels():
    value = copy.deepcopy(CASES['valid'])
    parsed = wire.frame(value)
    value['dimensions']['steps'][0] = 4
    assert parsed['dimensions']['steps'][0] == 1
    scientific_host.checked_reply({'op': 'render', 'width': 32, 'height': 24},
                                  {'ok': True, 'frame': parsed}, bytes(32 * 24 * 4))


@pytest.mark.parametrize('case', CASES['otherValid'], ids=lambda case: case['name'])
def test_native_dimension_states_and_integral_json_numbers(case):
    assert wire.frame(case['value']) == case['value']


def test_unicode_frame_budget_is_the_actual_protocol_budget():
    from env_worker.server import read_message, write_message
    value = copy.deepcopy(CASES['valid'])
    value['layers'] = [{**value['layers'][0], 'id': f'{i:032x}', 'name': '細胞' * 500} for i in range(30)]
    wire.frame(value)
    assert len(json.dumps(value).encode()) > 128 << 10
    pipe = io.BytesIO()
    write_message(pipe, {'ok': True, 'frame': value}, bytes(32 * 24 * 4), compact=True)
    pipe.seek(0)
    header, body = read_message(pipe, max_header=128 << 10, max_payload=4 << 20)
    scientific_host.checked_reply({'op': 'render', 'width': 32, 'height': 24}, header, body)


@pytest.mark.parametrize('case', CASES['invalid'], ids=lambda case: case['name'])
def test_same_invalid_descriptors_are_refused_in_parent_and_browser(case):
    value = copy.deepcopy(CASES['valid'])
    target = value
    for part in case['path'][:-1]:
        target = target[int(part) if type(target) is list else part]
    target[int(case['path'][-1]) if type(target) is list else case['path'][-1]] = case['value']
    with pytest.raises(ValueError):
        wire.frame(value)
    with pytest.raises(scientific_host.Failed):
        scientific_host.checked_reply({'op': 'render', 'width': 32, 'height': 24},
                                      {'ok': True, 'frame': value}, bytes(32 * 24 * 4))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 2**54, True])
def test_non_json_or_inexact_coordinate_is_refused(value):
    frame = copy.deepcopy(CASES['valid'])
    frame['camera']['center'][0] = value
    with pytest.raises(ValueError):
        wire.frame(frame)


def test_layer_inventory_has_no_frame_authority_and_uses_shared_validation():
    value={'format':'scientific-layers-v1','layers':copy.deepcopy(CASES['valid']['layers'])}
    parsed=wire.inventory(value)
    scientific_host.checked_reply({'op':'layer-inventory'}, {'ok':True,'inventory':parsed}, b'')
    value['layers'][0]['name']='changed'
    assert parsed['layers'][0]['name']!='changed'
    with pytest.raises(ValueError): wire.frame(parsed)
    with pytest.raises(ValueError): wire.inventory({**parsed,'frame':'a'*64})
    with pytest.raises(ValueError):
        scientific_host.checked_reply({'op':'layer-inventory'},{'ok':True,'inventory':parsed},b'pixels')


@pytest.mark.parametrize('case',[v for v in CASES['invalid'] if v['path'][0]=='layers'],ids=lambda v:v['name'])
def test_inventory_rejects_the_same_invalid_layers_as_rendered_frames(case):
    value={'format':'scientific-layers-v1','layers':copy.deepcopy(CASES['valid']['layers'])}
    target=value
    for part in case['path'][:-1]: target=target[int(part) if type(target) is list else part]
    target[int(case['path'][-1]) if type(target) is list else case['path'][-1]]=case['value']
    with pytest.raises(ValueError): wire.inventory(value)


@pytest.mark.parametrize('count',[0,1,2**53-1])
def test_structured_element_count_is_explicit_but_not_a_frame_receipt(count):
    layer={'id':'a'*32,'name':'Detections','type':'points','visible':True,'opacity':1.,'scale':[1.,1.],'translate':[0.,0.],'elementCount':count}
    assert wire.inventory({'format':'scientific-layers-v1','layers':[layer]})['layers'][0]['elementCount']==count
    for bad in (-1,True,.5,2**53):
        with pytest.raises(ValueError):wire.inventory({'format':'scientific-layers-v1','layers':[{**layer,'elementCount':bad}]})
    with pytest.raises(ValueError):wire.inventory({'format':'scientific-layers-v1','layers':[{**layer,'type':'image'}]})


def test_bound_input_meaning_is_explicit_and_checked_on_every_surface():
    value = copy.deepcopy(CASES['valid'])
    layer = value['layers'][0]
    layer.update(type='labels', editableInput='specimen', inputMeaning={'semantic':'instance-labels', 'axes':['z','y','x']})
    assert wire.frame(value)['layers'][0]['inputMeaning'] == layer['inputMeaning']
    for bad in ({'semantic':'intensity','axes':['z','y','x']},
                {'semantic':'instance-labels','axes':['y','x']},
                {'semantic':'instance-labels','axes':['z','y','y']}):
        changed=copy.deepcopy(value);changed['layers'][0]['inputMeaning']=bad
        with pytest.raises(ValueError):wire.frame(changed)
    del layer['editableInput']
    with pytest.raises(ValueError):wire.frame(value)


@pytest.mark.parametrize('kind,semantic,axes', [
    ('labels','instance-labels',['z','y','x']),
    ('image','probabilities',['z','y','x']),
    ('points','point-set',['z','y','x']),
    ('tracks','track-coordinates',['t','y','x']),
])
def test_declared_outputs_cross_inventory_and_frame_without_becoming_editable(kind,semantic,axes):
    value=copy.deepcopy(CASES['valid']);layer=value['layers'][0]
    layer.update(type=kind,viewKey='result',viewGeometry={'affine':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],
        'axes':['z','y','x'],'units':['um']*3},outputMeaning={'semantic':semantic,'axes':axes})
    parsed=wire.inventory({'format':'scientific-layers-v1','layers':[layer]})
    assert parsed['layers'][0]['outputMeaning']==layer['outputMeaning']
    assert wire.frame(value)['layers'][0]['outputMeaning']==layer['outputMeaning']
    assert 'editableInput' not in parsed['layers'][0]
    for change in ({'viewKey':None},{'editableInput':'raw'},
                   {'outputMeaning':{'semantic':'unrecognized','axes':axes}},
                   {'outputMeaning':{'semantic':semantic,'axes':['x','x','x']}},
                   {'outputMeaning':{'semantic':semantic,'axes':['y','x']}},
                   {'outputMeaning':{'semantic':semantic,'axes':axes,'authority':True}}):
        invalid=copy.deepcopy(value);invalid['layers'][0].update(change)
        with pytest.raises(ValueError):wire.frame(invalid)
        with pytest.raises(ValueError):wire.inventory({'format':'scientific-layers-v1','layers':invalid['layers']})
