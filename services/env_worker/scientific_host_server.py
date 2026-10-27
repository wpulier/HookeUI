"""Private scientific-host protocol. The parent owns confinement and every input grant."""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from env_worker import native_regions, source_files, scientific_inspection, scientific_source_wire
from env_worker.scientific_interrupt import Control
from env_worker.scientific_host import ReviewSession, RestoreRefused, ChangeRefused, ControlFailed, STARTUP_STEPS
from env_worker.server import read_message, write_message
from env_worker.scientific_input_refusal import InputRefused


def load_factory(entry, adapter):
    entry = Path(entry).resolve()
    module, _, function = adapter.partition(':')
    expected = entry.joinpath(*module.split('.')).with_suffix('.py')
    sys.path.insert(0, str(entry))
    try:
        spec = importlib.util.find_spec(module)
        if spec is None or not spec.origin or Path(spec.origin).resolve() != expected:
            raise ValueError('The declared adapter conflicts with another runtime module')
        loaded = importlib.import_module(module)
        if Path(loaded.__file__).resolve() != expected:
            raise ValueError('The declared adapter did not load from its prepared file')
        return getattr(loaded, function)
    finally:
        sys.path.remove(str(entry))



def _view_superseded(error):
    if type(error) is native_regions.ViewSuperseded:
        return True
    try:
        from psygnal import EmitLoopError
    except ImportError:
        return False
    # psygnal explicitly raises EmitLoopError from its callback exception.
    # An arbitrary cleanup error with cancellation in its context is still a failure.
    for _ in range(16):
        if type(error) is not EmitLoopError:
            return False
        cause = BaseException.__cause__.__get__(error)
        context = BaseException.__context__.__get__(error)
        if context is not None and context is not cause:
            return False
        if type(cause) is native_regions.ViewSuperseded:
            return True
        error = cause
    return False


def main(entry, adapter, interrupt_fd):
    output, incoming = sys.stdout.buffer, sys.stdin.buffer
    sys.stdout = sys.stderr
    interrupts = Control(interrupt_fd, started=lambda token: write_message(output, {'op':'python-started','token':token}))

    navigation_superseded = False

    def regional(request):
        nonlocal navigation_superseded
        with interrupts.protocol():
            write_message(output, request)
            reply = read_message(incoming, max_header=16 << 10, max_payload=1 << 20)
            if operation == 'navigate' and reply == ({'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'}, b''):
                navigation_superseded = True
                raise native_regions.ViewSuperseded('This display read was superseded')
            return reply

    def native_write(header, payload):
        with interrupts.protocol():
            write_message(output, header, payload)
            return read_message(incoming, max_header=16 << 10, max_payload=0)

    def starting(step):
        if step not in STARTUP_STEPS:
            raise ValueError('Unknown scientific startup step')
        with interrupts.protocol():
            write_message(output, {'op': 'starting', 'step': step})

    host = None
    operation = None
    try:
        first, _ = read_message(incoming, max_header=128 << 10, max_payload=0)
        if type(first) is not dict or set(first) not in ({'op','inputs'}, {'op','inputs','retainedLayers'}, {'op','inputs','files'}, {'op','inputs','files','retainedLayers'}, {'op','inputs','retainedLayers','retainedTool'}, {'op','inputs','retainedLayers','initializeTool'}, {'op','inputs','retainedLayers','initializeTool','recipeTool'}) or first['op'] != 'open':
            raise ValueError('Scientific review requires its authorized inputs')
        if 'initializeTool' in first and (type(first['initializeTool']) is not int or first['initializeTool'] != 1):
            raise ValueError('Unsupported scientific tool initialization version')
        files=source_files.bind(first.get('files',{}),regional)
        resources = native_regions.bind(first['inputs'], regional,allow_empty=bool(files))
        if 'retainedLayers' not in first or 'retainedTool' in first or 'initializeTool' in first:
            starting('code')
            factory = load_factory(entry, adapter)
        else:
            factory = lambda context: None
        sys.path.insert(0, str(Path(entry).resolve()))
        host = ReviewSession(factory, resources, retained_layers=first.get('retainedLayers'),
                             retained_tool=first.get('retainedTool'), initialize_tool=first.get('initializeTool'), recipe_tool=first.get('recipeTool'), interrupts=interrupts, files=files, progress=starting)
        write_message(output, {'ok': True, 'ready': adapter})
        while True:
            request, payload = read_message(incoming, max_header=128 << 10,
                max_payload=scientific_source_wire.PAYLOAD_BYTES, admit=scientific_source_wire.admit)
            request = scientific_source_wire.decode(request, payload)
            if type(request) is not dict:
                raise ValueError('Scientific requests must be objects')
            operation = request.get('op')
            if operation != 'navigate':
                host._navigation_anchor = None
            if operation == 'navigate' and set(request) == {'op', 'navigation', 'first', 'request', 'width', 'height'}:
                navigation_superseded = False
                try:
                    try:
                        host.navigate(request['navigation'], request['first'], request['request'])
                    except ChangeRefused as error:
                        write_message(output, {'ok': False, 'code': 'SCIENTIFIC_CHANGE_NOT_APPLIED', 'error': str(error)[:2000] or 'Refresh the view before navigation'})
                        continue
                    frame, pixels = host.render(request['width'], request['height'])
                except Exception as error:
                    if not _view_superseded(error):
                        raise
                    navigation_superseded = True
                if navigation_superseded:
                    host._frame = None
                    host._navigation_needs_refresh = True
                    write_message(output, {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'})
                else:
                    write_message(output, {'ok': True, 'frame': frame}, pixels, compact=True)
            elif request == {'op': 'layer-inventory'}:
                write_message(output, {'ok': True, 'inventory': host.inventory(), 'nativePanelAvailable': host.native_panel is not None, 'toolDocumentAvailable': host.tool_document is not None}, compact=True)
            elif operation in ('observe', 'native-panel-render', 'native-panel-change'):
                try:
                    if request == {'op': 'observe'}:
                        write_message(output, {'ok': True, **host.observe()})
                    elif operation == 'native-panel-render' and set(request) == {'op', 'width', 'height'}:
                        frame, pixels = host.render_native_panel(request['width'], request['height'])
                        write_message(output, {'ok': True, 'nativePanel': frame, 'imageFrame': host._frame}, pixels, compact=True)
                    elif operation == 'native-panel-change' and set(request) == {'op', 'request'}:
                        host.change_native_panel(request['request'])
                        write_message(output, {'ok': True, 'nativeChanged': True})
                    else:
                        raise ValueError('Native panel requests need their declared fields')
                except ChangeRefused as error:
                    write_message(output, {'ok': False, 'code': 'SCIENTIFIC_CHANGE_NOT_APPLIED',
                                           'error': str(error)[:2000] or 'Inspect the current panel first'})
                except ControlFailed as error:
                    write_message(output, {'ok': False, 'code': 'SCIENTIFIC_CONTROL_FAILED',
                                           'error': str(error)[:2000] or 'The panel callback failed'})
            elif request == {'op': 'reader-proof'}:
                from env_worker.scientific_reader_probe import observe
                write_message(output, {'ok': True, 'readerProof': observe(host)}, compact=True)
            elif request == {'op': 'sequence-begin'}:
                write_message(output, {'ok': True, 'sequence': host.begin_sequence()}, compact=True)
            elif operation == 'sequence-render' and set(request) == {'op', 'index', 'width', 'height'}:
                frame, pixels = host.render_sequence(request['index'], request['width'], request['height'])
                write_message(output, {'ok': True, 'frame': frame}, pixels, compact=True)
            elif operation == 'render' and set(request) == {'op', 'width', 'height'}:
                frame, pixels = host.render(request['width'], request['height'])
                write_message(output, {'ok': True, 'frame': frame}, pixels, compact=True)
            elif operation == 'change' and set(request) == {'op', 'request', 'width', 'height'}:
                try:
                    host.change(request['request'])
                except RestoreRefused as error:
                    write_message(output, {'ok': False, 'code': 'VIEW_RESTORE_NOT_APPLIED',
                                           'error': str(error)[:2000] or 'Saved view does not match current layers'})
                    continue
                except ChangeRefused as error:
                    write_message(output, {'ok': False, 'code': 'SCIENTIFIC_CHANGE_NOT_APPLIED',
                                           'error': str(error)[:2000] or 'Refresh the view before changing it'})
                    continue
                except ControlFailed as error:
                    write_message(output, {'ok': False, 'code': 'SCIENTIFIC_CONTROL_FAILED',
                                           'error': str(error)[:2000] or 'The tool callback failed'})
                    continue
                frame, pixels = host.render(request['width'], request['height'])
                write_message(output, {'ok': True, 'frame': frame}, pixels, compact=True)
            elif operation == 'labels':
                outcome = host.labels(request)
                frame, pixels = host.render(request['width'], request['height'])
                write_message(output, {'ok': True, 'frame': frame, 'edit': outcome}, pixels, compact=True)
            elif operation == 'inspect':
                try:
                    result = scientific_inspection.inspect(host, request)
                except (ValueError, RuntimeError) as error:
                    write_message(output, {'ok':False, 'code':'SCIENTIFIC_CHANGE_NOT_APPLIED',
                                           'error':str(error)[:2000] or 'Refresh the preview before inspection'})
                    continue
                write_message(output, {'ok':True, 'inspection':result}, compact=True)
            elif operation == 'pick' and set(request) == {'op', 'frame', 'layer', 'position'}:
                point = host.data_position(request['frame'], request['layer'], request['position'])
                write_message(output, {'ok': True, 'position': point})
            elif operation in ('describe-output', 'describe-tool-layer') and set(request) == {'op','layer'}:
                try:
                    describe = host.describe_tool_layer if operation == 'describe-tool-layer' else host.describe_output
                    result = {'ok':True, 'output':describe(request['layer'])}
                except ValueError as error:
                    result = {'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':str(error)[:2000] or 'Output refused'}
                write_message(output,result)
            elif operation == 'stage-output' and set(request) == {'op','layer','metadata','meaning','edit','snapshot'}:
                try:
                    result = host.stage_output(request['layer'],request['metadata'],request['meaning'],request['edit'],native_write,snapshot=request['snapshot'])
                except ValueError as error:
                    result = {'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':str(error)[:2000] or 'Output refused'}
                write_message(output,result)
            elif operation == 'stage-output-components' and set(request) == {'op','layer','metadata','meaning','components','snapshot'}:
                try:
                    result = host.stage_output_components(request['layer'],request['metadata'],request['meaning'],
                        request['components'],native_write,snapshot=request['snapshot'])
                except ValueError as error:
                    result = {'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':str(error)[:2000] or 'Surface capture refused'}
                write_message(output,result)
            elif operation in ('stage-outputs', 'stage-tool') and set(request) == {'op','layers','snapshot'}:
                try:
                    result=host.stage_outputs(request['layers'],native_write,snapshot=request['snapshot'], tool=operation == 'stage-tool')
                except ValueError as error:
                    result={'ok':False,'code':'NATIVE_CAPTURE_NOT_STARTED','error':str(error)[:2000] or 'Output set refused'}
                write_message(output,result)
            elif operation == 'stage-resources' and set(request) - {'_base'} == {'op', 'layers', 'snapshot'}:
                try:
                    base = request.get('_base')
                    if base is not None and len(base) != len(request['layers']):
                        raise ValueError('Base chunk digests must name each staged layer')
                    result = host.stage_resources(request['layers'], native_write, snapshot=request['snapshot'], base=base)
                except ValueError as error:
                    result = {'ok': False, 'code': 'NATIVE_CAPTURE_NOT_STARTED', 'error': str(error)[:2000] or 'Native capture refused'}
                write_message(output, result)
            elif operation in ('stage-layer', 'stage-resource'):
                expected = {'op', 'layer', 'input', 'edit', 'snapshot'}
                if operation == 'stage-layer': expected.add('frame')
                if set(request) - {'_base'} != expected:
                    raise ValueError('Native capture request fields are invalid')
                base = request.get('_base')
                if base is not None and len(base) != 1:
                    raise ValueError('Base chunk digests must name the one staged layer')
                base = base[0] if base else None
                try:
                    if operation == 'stage-layer':
                        result = host.stage_layer(request['frame'], request['layer'], request['input'], request['edit'], native_write,
                                                  snapshot=request['snapshot'], base=base)
                    else:
                        result = host.stage_resource(request['layer'], request['input'], request['edit'], native_write,
                                                     snapshot=request['snapshot'], base=base)
                except ValueError as error:
                    # The parent accepts this refusal only before the first chunk;
                    # partial transport remains fatal and is never retried as a new capture.
                    result = {'ok': False, 'code': 'NATIVE_CAPTURE_NOT_STARTED', 'error': str(error)[:2000] or 'Native capture refused'}
                write_message(output, result)
            elif operation == 'python' and set(request) == {'op', 'frame', 'source', 'interruptToken'}:
                transcript = host.python(request['frame'], request['source'], interrupt_token=request['interruptToken'])
                write_message(output, {'ok': True, 'console': transcript}, compact=True)
            elif operation == 'python-resources' and set(request) == {'op', 'source', 'interruptToken'}:
                transcript = host.python_resources(request['source'], interrupt_token=request['interruptToken'])
                write_message(output, {'ok': True, 'console': transcript}, compact=True)
            elif request == {'op': 'family-drafts'}:
                write_message(output, {'ok': True, 'drafts': host.family_drafts()}, compact=True)
            elif operation == 'stage-families' and set(request) - {'_base'} == {'op', 'names', 'unchanged'}:
                try:
                    base = request.get('_base') or []
                    if len(base) != len(request['unchanged']):
                        raise ValueError('Accepted chunk digests must name each input the drafts refer to')
                    result = host.stage_families(request['names'], native_write, unchanged=request['unchanged'], base=base)
                except ValueError as error:
                    # Accepted by the parent only before the first part; partial transport stays fatal.
                    result = {'ok': False, 'code': 'NATIVE_CAPTURE_NOT_STARTED', 'error': str(error)[:2000] or 'Draft save refused'}
                write_message(output, result)
            elif operation == 'python-assist' and set(request) == {'op', 'mode', 'expression'}:
                write_message(output, {'ok': True, 'assistance': host.python_assist(request['mode'], request['expression'])}, compact=True)
            elif request == {'op': 'close'}:
                host.close()
                host = None
                write_message(output, {'ok': True, 'closed': True})
                return
            else:
                raise ValueError('This scientific request needs a declared integration')
    except Exception as error:
        if operation is None and isinstance(error, InputRefused):
            write_message(output, {'ok': False, 'code': 'SCIENTIFIC_INPUT_REFUSED',
                                   'error': str(error), 'next': error.next_step})
        elif operation in ('sequence-begin', 'sequence-render'):
            from env_worker.scientific_callback_errors import callback_summary
            write_message(output, {'ok': False, 'code': 'SCIENTIFIC_SEQUENCE_FAILED', 'error': callback_summary(error)})
        else:
            write_message(output, {'ok': False, 'error': str(error)[:2000]})
        raise
    finally:
        try:
            if host is not None:
                host.close()
        finally:
            interrupts.close()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))
