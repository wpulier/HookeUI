from types import SimpleNamespace

import pytest

from env_worker.scientific_view_bindings import ViewBindings


def setup():
    first, second = object(), object()
    viewer = SimpleNamespace(layers=[first, second])
    return ViewBindings(viewer), viewer, first, second


def test_explicit_keys_follow_layer_identity_not_names_or_order():
    bindings, viewer, first, second = setup()
    bindings.bind(layer=first, key='raw')
    bindings.bind(layer=second, key='instances')
    bindings.seal()
    viewer.layers.reverse()
    assert bindings.key_for(first) == 'raw'
    assert bindings.key_for(second) == 'instances'
    assert bindings.key_for(object()) is None


@pytest.mark.parametrize('key', ['', 'a' * 65, '../raw', 'raw space', 7, None])
def test_keys_are_bounded_integration_identifiers(key):
    bindings, _, first, _ = setup()
    with pytest.raises(ValueError):
        bindings.bind(layer=first, key=key)
    assert bindings.key_for(first) is None


def test_duplicate_key_and_alias_bindings_are_refused():
    bindings, _, first, second = setup()
    bindings.bind(layer=first, key='raw')
    for layer, key in [(second, 'raw'), (first, 'alias'), (object(), 'foreign')]:
        with pytest.raises(ValueError):
            bindings.bind(layer=layer, key=key)
    assert bindings.key_for(first) == 'raw' and bindings.key_for(second) is None


def test_sealed_binding_cannot_reassign_or_add_an_identity():
    bindings, _, first, second = setup()
    bindings.bind(layer=first, key='raw')
    bindings.seal()
    with pytest.raises(ValueError):
        bindings.bind(layer=second, key='new')
    assert bindings.key_for(second) is None


def test_removing_a_layer_revokes_identity_even_if_the_same_object_returns():
    bindings, viewer, first, _ = setup()
    bindings.bind(layer=first, key='raw')
    bindings.seal()
    viewer.layers.remove(first)
    bindings.refresh()
    viewer.layers.append(first)
    assert bindings.key_for(first) is None


def test_layer_removed_during_factory_cannot_leave_a_live_binding():
    bindings, viewer, first, _ = setup()
    bindings.bind(layer=first, key='raw')
    viewer.layers.remove(first)
    bindings.seal()
    viewer.layers.append(first)
    assert bindings.key_for(first) is None


def test_async_output_identity_is_unique_revocable_and_never_reassigned():
    bindings,viewer,first,second=setup()
    bindings.bind(layer=first,key='raw')
    with pytest.raises(ValueError):bindings.bind_output(layer=second,key='result')
    bindings.seal();bindings.bind_output(layer=second,key='result')
    assert bindings.key_for(second)=='result'
    viewer.layers.remove(second);bindings.refresh();viewer.layers.append(second)
    assert bindings.key_for(second) is None
    with pytest.raises(ValueError):bindings.bind_output(layer=second,key='result')
    bindings.close()
    with pytest.raises(ValueError):bindings.bind_output(layer=second,key='fresh')


def test_output_binding_requires_owning_thread_and_supported_version():
    import threading
    from env_worker.scientific_host import ReviewSession,Stale,ChangeRefused
    session=object.__new__(ReviewSession)
    session._closed=False;session._thread=threading.get_ident()
    session.view_bindings,_,first,second=setup()
    session.view_bindings.bind(layer=first,key='raw');session.view_bindings.seal()
    failures=[]
    def outside():
        try:session.bind_view_output(version=1,layer=second,key='result')
        except Exception as error:failures.append(error)
    thread=threading.Thread(target=outside);thread.start();thread.join()
    assert len(failures)==1 and isinstance(failures[0],Stale)
    for version in (True,2,None):
        with pytest.raises(ChangeRefused):session.bind_view_output(version=version,layer=second,key='result')
    session.bind_view_output(version=1,layer=second,key='result')
    assert session.view_bindings.key_for(second)=='result'


@pytest.mark.parametrize('kind,semantic,axes', [
    ('labels', 'instance-labels', ['y', 'x']),
    ('points', 'point-set', ['z', 'y', 'x']),
    ('tracks', 'track-coordinates', ['t', 'y', 'x']),
])
def test_output_meaning_follows_identity_and_never_grants_input_edits(kind, semantic, axes):
    layer = SimpleNamespace(_type_string=kind, ndim=len(axes), rgb=False)
    viewer = SimpleNamespace(layers=[layer])
    bindings = ViewBindings(viewer); bindings.seal()
    meaning = {'semantic': semantic, 'axes': axes.copy()}
    bindings.bind_output(layer=layer, key='result', meaning=meaning)
    meaning['axes'][0] = 'changed'
    assert bindings.meaning_for(layer) == {'semantic': semantic, 'axes': axes}
    returned = bindings.meaning_for(layer); returned['axes'][0] = 'another'
    assert bindings.meaning_for(layer)['axes'] == axes
    bindings.check_meaning(layer, {'semantic': semantic, 'axes': axes})
    with pytest.raises(ValueError, match='declared'):
        bindings.check_meaning(layer, {'semantic': semantic, 'axes': axes[::-1]})
    layer.ndim += 1
    with pytest.raises(ValueError, match='dimensions'):
        bindings.meaning_for(layer)
    layer.ndim -= 1
    viewer.layers.clear(); bindings.refresh(); viewer.layers.append(layer)
    assert bindings.meaning_for(layer) is None


@pytest.mark.parametrize('kind,rgb,meaning', [
    ('labels', False, {'semantic':'intensity','axes':['y','x']}),
    ('image', False, {'semantic':'probabilities','axes':['x','x']}),
    ('image', True, {'semantic':'probabilities','axes':['y','x']}),
    ('image', True, {'semantic':'intensity','axes':['y','color']}),
    ('tracks', False, {'semantic':'track-coordinates','axes':['y','x']}),
    ('points', False, {'semantic':'point-set','axes':['z','y','x']}),
])
def test_invalid_meaning_cannot_consume_output_binding_or_invent_semantics(kind, rgb, meaning):
    layer = SimpleNamespace(_type_string=kind, ndim=2, rgb=rgb)
    bindings = ViewBindings(SimpleNamespace(layers=[layer])); bindings.seal()
    with pytest.raises(ValueError):
        bindings.bind_output(layer=layer, key='result', meaning=meaning)
    assert bindings.key_for(layer) is None
    bindings.bind_output(layer=layer, key='result')
    assert bindings.meaning_for(layer) is None
    bindings.check_meaning(layer, {'semantic':'intensity','axes':['y','x']})
