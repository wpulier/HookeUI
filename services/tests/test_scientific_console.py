from concurrent.futures import ThreadPoolExecutor
import io
import json
from types import SimpleNamespace

import pytest

from env_worker.scientific_console import Console, SOURCE_BYTES, TEXT_BYTES, reply, source
from env_worker.server import read_message, write_message
from orchestrator.scientific_host import checked_reply, MAX_HEADER


def test_python_cells_share_variables_viewer_and_future_flags():
    viewer = SimpleNamespace(value=3)
    console = Console(viewer)
    first = console.run('x = 4\nviewer.value += x\nprint("hello")\nviewer.value')
    assert first['sequence'] == 1 and first['stdout'] == 'hello\n' and first['result'] == '7'
    assert viewer.value == 7
    assert console.run('x * 3')['result'] == '12'
    assert not console.run('from __future__ import annotations')['error']
    assert not console.run('def fn(x: Undefined):\n    return x')['error']
    assert console.run('fn.__annotations__')['result'] == "{'x': 'Undefined'}"
    console.close()
    with pytest.raises(ValueError, match='closed'):
        console.run('x')
    assert Console(viewer).run('x')['error'].startswith('NameError:'), 'restart never silently reexecutes old cells'


def test_assistance_uses_the_live_console_without_execution_or_sequence_changes():
    console = Console(object())
    assert console.run('measured = 42')['sequence'] == 1
    assert console.assist('complete', 'mea')['matches'] == ['measured']
    assert console.assist('help', 'print')['text']
    assert console.run('measured')['sequence'] == 2
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError, match='GUI thread'):
            pool.submit(console.assist, 'help', 'print').result()
    console.close()
    with pytest.raises(ValueError, match='closed'):
        console.assist('help', 'print')


def test_resource_names_are_a_private_copy_and_not_reassignable():
    original = {'volume': object()}
    console = Console(None, original)
    original.clear()
    assert console.run("list(inputs)")['result'] == "['volume']"
    assert console.run("inputs['volume'] = 2")['error'].startswith('TypeError:')
    assert console.run("list(inputs)")['result'] == "['volume']"


@pytest.mark.parametrize('code, error', [('x =', 'SyntaxError'), ('1 / 0', 'ZeroDivisionError'),
                                      ('raise SystemExit(2)', 'SystemExit'), ('raise KeyboardInterrupt()', 'KeyboardInterrupt')])
def test_python_error_keeps_private_values_and_allows_correction(code, error):
    console = Console(object())
    console.run('saved = [1, 2]')
    assert console.run(code)['error'].startswith(error + ':')
    assert console.run('saved.append(3)\nsaved')['result'] == '[1, 2, 3]'


def test_partial_private_mutation_is_reported_after_python_failure():
    viewer = SimpleNamespace(value=3)
    console = Console(viewer)
    failed = console.run('viewer.value = 9\nraise ValueError("correct this")')
    assert failed['error'].startswith('ValueError: correct this\n') and failed['privatePreview']
    assert '<Hooke Python 1>, line 2' in failed['error']
    assert 'raise ValueError("correct this")' in failed['error']
    assert viewer.value == 9 and console.run('viewer.value')['result'] == '9'


def test_broken_exception_formatter_keeps_the_console_and_saved_variables():
    console = Console(object())
    console.run('saved = 12')
    error = console.run('class Broken(Exception):\n    def __str__(self):\n        raise SystemExit(1)\nraise Broken()')
    assert error['error'].startswith('Broken: exception text unavailable\n')
    assert console.run('saved')['result'] == '12'


def test_exception_with_raising_class_property_keeps_the_kernel_and_variables():
    console = Console(object())
    console.run('saved = 42')
    transcript = console.run('class Unusual(Exception):\n    @property\n    def __class__(self):\n        raise RuntimeError("class lookup must not run")\nraise Unusual("original error")')
    assert transcript['error'].startswith('Unusual: original error')
    assert console.run('saved + 1')['result'] == '43'


@pytest.mark.parametrize('character', ['x', '\x00', '🎥'])
def test_transcripts_are_bounded_by_actual_encoded_protocol(character):
    console = Console(object())
    transcript = console.run(f'import sys\nprint({character!r} * 100000)\nprint({character!r} * 100000, file=sys.stderr)')
    assert transcript['truncated'] and not transcript['error']
    assert all(len(transcript[k].encode('utf-8')) <= TEXT_BYTES for k in ('stdout', 'stderr'))
    wire = io.BytesIO()
    write_message(wire, {'ok': True, 'console': transcript}, compact=True)
    wire.seek(0)
    header, body = read_message(wire, max_header=MAX_HEADER, max_payload=0)
    checked_reply({'op': 'python'}, header, body)
    assert len(json.dumps(header, ensure_ascii=False).encode()) < MAX_HEADER


def test_console_refuses_foreign_thread_oversized_source_and_forged_reply():
    console = Console(object())
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(ValueError, match='GUI thread'):
            pool.submit(console.run, '1').result()
    for code in ('', ' ', 'x' * (SOURCE_BYTES + 1), '🎥' * SOURCE_BYTES, None):
        with pytest.raises(ValueError):
            source(code)
    good = console.run('3')
    for changes in ({'privatePreview': False}, {'sequence': True}, {'stdout': 'x' * (TEXT_BYTES + 1)}, {'extra': 1}):
        with pytest.raises(ValueError):
            reply({**good, **changes})


def test_owned_interrupt_preserves_variables_and_ignores_old_cell_tokens():
    import os, signal
    from env_worker.scientific_interrupt import Control
    incoming, outgoing = os.pipe()
    with_control = Control(incoming)
    try:
        console = Console(object(), interrupts=with_control)
        first, second = 'a'*32, 'b'*32
        console._namespace['interrupt_me'] = lambda: (os.write(outgoing, first.encode()), os.kill(os.getpid(), signal.SIGUSR1))
        stopped = console.run('kept = 17\ninterrupt_me()\nkept = 99', interrupt_token=first)
        assert stopped['error'].startswith('KeyboardInterrupt:')
        # Late delivery belongs only to its original execution, not the next cell.
        console._namespace['late_interrupt'] = lambda: (os.write(outgoing, first.encode()), os.kill(os.getpid(), signal.SIGUSR1))
        assert console.run('late_interrupt()\nkept', interrupt_token=second)['result'] == '17'
    finally:
        with_control.close()
        os.close(outgoing)


def test_interrupt_waits_for_complete_native_protocol_exchange():
    import os,signal
    from env_worker.scientific_interrupt import Control
    incoming,outgoing=os.pipe(); control=Control(incoming); token='c'*32
    completed=[]
    try:
        def native_read():
            with control.protocol():
                os.write(outgoing,token.encode());os.kill(os.getpid(),signal.SIGUSR1)
                completed.append('full protocol reply consumed')
            completed.append('returned to user')
        console=Console(object(),interrupts=control);console._namespace['native_read']=native_read
        reply=console.run('kept = 23\nnative_read()\nkept = 99',interrupt_token=token)
        assert reply['error'].startswith('KeyboardInterrupt:')
        assert completed==['full protocol reply consumed']
        assert console.run('kept',interrupt_token='d'*32)['result']=='23'
    finally:
        control.close();os.close(outgoing)
