"""Private integration example; no catalog qualification or biological claim."""

ADAPTER = '''
from magicgui.widgets import Container
from qtpy.QtWidgets import QWidget, QVBoxLayout, QPushButton

def open_host(context):
    source = context.inputs['volume']
    layer = context.viewer.add_image(source.read([[0, n] for n in source.shape]), name='Native input')
    context.bind_editable(layer=layer, input='volume')
    context.bind_view(layer=layer, key='native-input')
    root = QWidget()
    layout = QVBoxLayout(root)
    def opacity(): layer.opacity = .35
    def broken():
        layer.opacity = .45
        raise ValueError('Controlled slot failure after private change')
    def fatal(): raise MemoryError('Controlled fatal callback, no memory exhaustion')
    for title, action in [('Change opacity', opacity), ('Fail safely', broken), ('Fatal failure', fatal)]:
        button = QPushButton(title)
        button.setAccessibleName(title)
        button.clicked.connect(action)
        layout.addWidget(button)
    context.bind_native_panel(root)
    return Container()
'''
