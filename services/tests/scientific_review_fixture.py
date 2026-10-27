"""Adapter source for explicitly prepared private actual-runtime failure probes."""

ADAPTER = '''
from pathlib import Path
from magicgui import magicgui

def open_host(context):
    resource = context.inputs['volume']
    data = resource.read([[0, n] for n in resource.shape])
    context.viewer.add_image(data, name='volume', rendering='mip')
    @magicgui(call_button='Apply opacity', opacity={'min':0., 'max':1.},
              operation={'choices':['apply', 'crash', 'hang', 'oversized', 'forbidden']})
    def settings(opacity:float=.4, operation:str='apply'):
        if operation == 'crash':
            raise ValueError('controlled callback failure')
        if operation == 'hang':
            import time
            Path('callback-started').touch()
            while True:
                time.sleep(.1)
        if operation == 'oversized':
            import sys, struct
            sys.__stdout__.buffer.write(struct.pack('>I', 1 << 30))
            sys.__stdout__.buffer.flush()
            return
        if operation == 'forbidden':
            import os, socket, sys
            assert 'ANTHROPIC_API_KEY' not in os.environ
            def denied(call):
                try:
                    call()
                except PermissionError:
                    return
                raise AssertionError('forbidden access was allowed')
            denied(lambda: (Path.cwd().parent / 'private-canary').read_bytes())
            denied(lambda: (Path(sys.prefix) / 'forbidden-write').write_bytes(b'must not write'))
            denied(lambda: socket.create_connection(('127.0.0.1', 9), timeout=1))
            denied(lambda: os.kill(os.getppid(), 0))
        context.viewer.layers['volume'].opacity=opacity
    return settings
'''
