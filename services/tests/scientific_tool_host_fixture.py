"""Private synthetic document integration for actual confined runtime checks."""
ADAPTER = '''
from magicgui.widgets import Container
from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton, QLineEdit

def open_host(context):
    if context.restored_layers is None:
        source = context.inputs['specimen']
        add = context.viewer.add_labels if source.descriptor['semantic'] == 'instance-labels' else context.viewer.add_image
        layer = add(source.read([[0, n] for n in source.shape]).copy(), name='Retained specimen')
        context.bind_editable(layer=layer, input='specimen')
        context.bind_view(layer=layer, key='specimen')
    else:
        layer = context.restored_layers['specimen']
    state = {'title': 'Untitled sequence', 'keyframes': [], 'corruptOnRestore': False, 'mutateOnCapture': False}
    context.viewer._hooke_tool_state = state
    root = QWidget(); layout = QVBoxLayout(root)
    title = QLineEdit(state['title']); title.setAccessibleName('Sequence title')
    title.textChanged.connect(lambda value: state.update(title=value)); layout.addWidget(title)
    def capture_keyframe():
        state['keyframes'].append({'point': list(context.viewer.dims.point), 'steps': 15})
    button = QPushButton('Capture keyframe'); button.setAccessibleName('Capture keyframe')
    button.clicked.connect(capture_keyframe); layout.addWidget(button)
    def restore(value):
        state.clear(); state.update(value); title.setText(state['title'])
        if state['corruptOnRestore']: layer.data.flat[0] += 1
    def capture():
        if state['mutateOnCapture']: layer.data.flat[0] += 1
        return state
    context.bind_document(schema='hooke.test-sequence', version=1, capture=capture, restore=restore)
    context.bind_native_panel(root)
    return Container()
'''
