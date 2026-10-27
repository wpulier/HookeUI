"""Two real Qt consumers of the public output binding, inside the owned test sandbox."""
import sys
sys.path.insert(0, sys.argv[1])
from env_worker.server import write_message
output = sys.stdout.buffer
sys.stdout = sys.stderr

import numpy as np
from magicgui.widgets import Container, PushButton
from env_worker.scientific_host import ReviewSession
for kind, semantic, data in [
    ('labels', 'instance-labels', np.arange(6, dtype='uint16').reshape(2,3)),
    ('points', 'point-set', np.array([[1.,2.],[3.,4.]])),
]:
    def factory(context):
        button = PushButton(text='Create declared output')
        def create():
            layer = getattr(context.viewer, 'add_' + kind)(data, name='Synthetic result')
            context.bind_view_output(version=1, layer=layer, key='result',
                meaning={'semantic':semantic, 'axes':['y','x']})
        button.changed.connect(create)
        return Container(widgets=[button])
    session = ReviewSession(factory, {})
    try:
        session.container[0].changed.emit()
        row = session.inventory()['layers'][0]
        assert row['outputMeaning'] == {'semantic':semantic,'axes':['y','x']}
        assert 'editableInput' not in row and 'inputMeaning' not in row
        frame, pixels = session.render(128,128)
        assert frame['layers'][0]['outputMeaning'] == row['outputMeaning']
        assert pixels and np.array_equal(session.viewer.layers[0].data, data)
        expected = session.describe_output(row['id'])
        try:
            session.stage_output(row['id'], expected,
                {'semantic':semantic,'axes':['x','y']}, {}, None)
        except ValueError as error:
            assert 'integration-declared' in str(error), str(error)
        else:
            raise AssertionError('Conflicting declaration reached output grants')
        assert np.array_equal(session.viewer.layers[0].data, data)
    finally:
        session.close()
print('PASS: two public factories, native outputs, validated meaning and no edit grant')

write_message(output, {'ok':True,'consumers':['labels','points']})
