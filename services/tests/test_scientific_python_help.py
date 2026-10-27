from types import SimpleNamespace

import pytest

from env_worker.scientific_python_help import assist, query, reply


def test_borrowed_dict_descriptor_cannot_end_the_console():
    from env_worker.scientific_console import Console
    console = Console(None)
    console.run('saved = 42\nclass Unusual:\n    __dict__ = int.real\nvalue = Unusual()')
    result = console.assist('complete', 'value.')
    assert result['format'] == 'scientific-python-assist-v1'
    assert console.run('saved + 1')['result'] == '43'


@pytest.mark.parametrize('expression, target', [('target','globals()'), ('value.target','value.__dict__')])
def test_non_string_dictionary_keys_never_run_equality_during_lookup(expression, target):
    from env_worker.scientific_console import Console
    console = Console(None)
    code = 'saved = 42\ncalls = []\nclass Key:\n    def __hash__(self):\n        return hash("target")\n    def __eq__(self, other):\n        calls.append("compared")\n        raise SystemExit("lookup must not execute equality")\nclass Value:\n    pass\nvalue = Value()\n'
    assert not console.run(code + target + '[Key()] = 1')['error']
    console.assist('help', expression)
    assert console.run('(saved + 1, calls)')['result'] == '(43, [])'


def test_large_docstrings_and_names_are_bounded_before_encoding():
    import tracemalloc
    def operation():
        pass
    operation.__doc__ = 'α' * (2 << 20)
    namespace = {'operation':operation, 'a' * (2 << 20):None}
    tracemalloc.start()
    try:
        help = assist(namespace, 'help', 'operation')
        names = assist(namespace, 'complete', 'a')
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert help['truncated'] and names['truncated']
    assert peak < 150_000, 'lookup must not duplicate the unbounded stored strings'


def test_namespace_mutation_during_scan_is_recoverable_and_keeps_variables(monkeypatch):
    from env_worker import scientific_python_help
    from env_worker.scientific_console import Console
    class Viewer:
        pass
    viewer = Viewer()
    viewer.first, viewer.second = 1, 2
    console = Console(viewer)
    console.run('saved = 42')
    original = scientific_python_help._name
    def interleaved(value):
        # Deterministically interleave the same dictionary mutation a plugin's
        # background callback can make between iterator reads.
        viewer.new_attribute = 3
        return original(value)
    monkeypatch.setattr(scientific_python_help, '_name', interleaved)
    result = console.assist('complete', 'viewer.')
    assert 'retry' in result['text'].lower()
    assert console.run('saved + viewer.new_attribute')['result'] == '45'


def test_names_follow_the_live_namespace_without_executing_callbacks():
    calls = []
    class Viewer:
        def __init__(self):
            self.layers = SimpleNamespace(first=1)
        def __dir__(self):
            calls.append('dir')
            raise AssertionError('must not run')
        @property
        def dangerous(self):
            """An explicitly evaluated value."""
            calls.append('property')
            raise AssertionError('must not run')
    namespace = {'viewer': Viewer(), 'measurement': 3}
    assert assist(namespace, 'complete', 'mea')['matches'] == ['measurement']
    namespace['measured'] = 4
    assert assist(namespace, 'complete', 'mea')['matches'] == ['measured', 'measurement']
    assert assist(namespace, 'complete', 'viewer.la')['matches'] == ['layers']
    assert assist(namespace, 'complete', 'viewer.layers.fi')['matches'] == ['first']
    help = assist(namespace, 'help', 'viewer.dangerous')
    assert 'Property; its value was not evaluated.' in help['text']
    assert 'An explicitly evaluated value.' in help['text'] and not calls


def test_help_uses_docs_without_repr_or_a_replaced_dict_descriptor():
    class Unusual:
        """Useful class documentation."""
        @property
        def __dict__(self):
            raise AssertionError('must not evaluate this descriptor')
        def __repr__(self):
            raise AssertionError('must not format this value')
        def operation(self):
            """Run a scientific operation."""
    namespace = {'value': Unusual()}
    assert 'Useful class documentation.' in assist(namespace, 'help', 'value')['text']
    assert assist(namespace, 'complete', 'value.op')['matches'] == ['operation']
    assert assist(namespace, 'help', 'value.operation')['text'] == 'Run a scientific operation.'


def test_assistance_is_bounded_and_unknown_names_are_actionable():
    namespace = {f'item_{i}': i for i in range(5000)}
    result = assist(namespace, 'complete', 'item_')
    assert len(result['matches']) == 64 and result['truncated']
    assert reply(result, 'complete', 'item_') is result
    assert 'not defined' in assist({}, 'help', 'missing')['text']
    assert 'print' in assist({}, 'complete', 'pri')['matches']
    assert assist({}, 'help', 'print')['text']


@pytest.mark.parametrize('expression', ['viewer.layers[0]', 'viewer.clear()', 'viewer..layers', 'x;import os', 'x.' * 17, 'é' * 129])
def test_assistance_refuses_execution_syntax_and_oversized_queries(expression):
    with pytest.raises(ValueError):
        query('complete', expression)


def test_assistance_replies_are_bound_to_query_and_allowances():
    result = assist({}, 'complete', 'pri')
    with pytest.raises(ValueError):
        reply(result, 'complete', 'oth')
    with pytest.raises(ValueError):
        reply({**result, 'text': 'a' * 8193}, 'complete', 'pri')
    with pytest.raises(ValueError):
        reply({**result, 'matches': ['bad()']}, 'complete', 'pri')
