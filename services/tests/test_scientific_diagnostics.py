import builtins
import pytest

from env_worker.scientific_diagnostics import Diagnostics


def test_prior_cell_function_locations_use_submitted_source_without_opening_files(monkeypatch):
    diagnostics = Diagnostics()
    namespace = {}
    first = 'def measure(x):\n    return 12 / x\n'
    second = 'measure(0)'
    diagnostics.remember('<Hooke Python 1>', first)
    diagnostics.remember('<Hooke Python 2>', second)
    exec(compile(first, '<Hooke Python 1>', 'exec'), namespace)
    def forbidden(*args, **kwargs):
        raise AssertionError('diagnostics may not read files or format locals')
    namespace['private_data'] = type('Secret', (), {'__repr__': forbidden})()
    monkeypatch.setattr(builtins, 'open', forbidden)
    with pytest.raises(ZeroDivisionError) as failed:
        exec(compile(second, '<Hooke Python 2>', 'exec'), namespace)
    parts = []
    diagnostics.describe(failed.value, parts.append)
    text = ''.join(parts)
    assert text.startswith('ZeroDivisionError: division by zero')
    assert '<Hooke Python 2>, line 1' in text and 'measure(0)' in text
    assert '<Hooke Python 1>, line 2, in measure' in text and 'return 12 / x' in text
    assert 'private_data' not in text


def test_syntax_error_locates_its_original_multiline_cell():
    diagnostics = Diagnostics()
    diagnostics.remember('<Hooke Python 1>', 'first = 3\nsecond =')
    with pytest.raises(SyntaxError) as failed:
        compile('first = 3\nsecond =', '<Hooke Python 1>', 'exec')
    parts = []
    diagnostics.describe(failed.value, parts.append)
    assert '<Hooke Python 1>, line 2\n    second =' in ''.join(parts)


def test_diagnostic_cache_is_bounded_and_does_not_invent_evicted_source():
    diagnostics = Diagnostics(allowance=10)
    diagnostics.remember('old', 'secret = 1')
    diagnostics.remember('new', 'x = 2')
    assert list(diagnostics.sources) == ['new'] and diagnostics.size == 5
    diagnostics.remember('new', 'y = 3')
    assert diagnostics.size == 5
    diagnostics.clear()
    assert not diagnostics.sources and diagnostics.size == 0


def test_exception_causes_are_bounded_and_do_not_repeat_cycles():
    first, second = ValueError('inner cause'), RuntimeError('outer failure')
    second.__cause__ = first
    first.__cause__ = second
    parts = []
    Diagnostics().describe(second, parts.append)
    text = ''.join(parts)
    assert text.startswith('RuntimeError: outer failure')
    assert text.count('ValueError: inner cause') == 1
    assert 'Repeated exception context omitted.' in text


def test_subclass_traceback_descriptors_cannot_break_error_presentation():
    class Unusual(SyntaxError):
        @property
        def __traceback__(self):
            raise AssertionError('must read the original exception descriptor')
        @property
        def filename(self):
            raise AssertionError('must read the original syntax descriptor')
    parts = []
    Diagnostics().describe(Unusual('bad syntax'), parts.append)
    assert ''.join(parts) == 'Unusual: bad syntax'
