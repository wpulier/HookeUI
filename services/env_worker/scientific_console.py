"""Persistent Python inside an already confined, owned scientific worker.

This is execution, not a Python sandbox. The parent owns OS confinement, resource
limits, deadlines and explicit user admission. Nothing here publishes saved edits.
"""
from __future__ import annotations

import __future__
import ast
from contextlib import redirect_stderr, redirect_stdout, nullcontext
import io
import json
import reprlib
import secrets
import threading
from types import MappingProxyType

from .scientific_diagnostics import Diagnostics
from . import scientific_python_help

SOURCE_BYTES = 256 << 10
TEXT_BYTES = 16 << 10
FUTURE_FLAGS = sum(getattr(__future__, name).compiler_flag for name in __future__.all_feature_names)


def source(value):
    if type(value) is not str or not value.strip() or len(value) > SOURCE_BYTES or len(value.encode('utf-8')) > SOURCE_BYTES:
        raise ValueError('Enter a nonempty UTF-8 Python script of at most 256 KiB')
    return value


class _Output(io.TextIOBase):
    def __init__(self):
        self.parts, self.size, self.truncated = [], 0, False

    def write(self, value):
        if type(value) is not str:
            raise TypeError('Console output must be text')
        remaining = TEXT_BYTES - self.size
        safe = value[:remaining].encode('utf-8', errors='replace').decode('utf-8')
        characters = []
        for character in safe:
            cost = len(json.dumps(character, ensure_ascii=False).encode('utf-8')) - 2
            if cost > remaining:
                break
            remaining -= cost
            self.size += cost
            characters.append(character)
        kept = ''.join(characters)
        if kept:
            self.parts.append(kept)
        self.truncated |= len(kept) < len(value)
        return len(value)

    def text(self):
        return ''.join(self.parts)


def reply(value):
    """A bounded transcript, separate from scientific frame/presentation evidence."""
    fields = {'format', 'sequence', 'stdout', 'stderr', 'result', 'error', 'truncated', 'privatePreview'}
    if (type(value) is not dict or set(value) != fields or value['format'] != 'scientific-console-v1'
            or type(value['sequence']) is not int or not 1 <= value['sequence'] <= 2**53 - 1
            or value['privatePreview'] is not True or type(value['truncated']) is not bool):
        raise ValueError('The scientific console returned an invalid transcript')
    for key in ('stdout', 'stderr', 'result', 'error'):
        text = value[key]
        if type(text) is not str or len(text) > TEXT_BYTES or len(text.encode('utf-8')) > TEXT_BYTES:
            raise ValueError('The scientific console exceeded its transcript allowance')
    return dict(value)


class Console:
    def __init__(self, viewer, inputs=None, *, interrupts=None, files=None, families=None):
        self._interrupts = interrupts
        self._namespace = {'__name__': '__hooke_console__', 'viewer': viewer,
                           'inputs': MappingProxyType(dict(inputs or {})),
                           'files': MappingProxyType(dict(files or {})),
                           **({'families': families} if families is not None else {})}
        self._thread, self._sequence, self._flags = threading.get_ident(), 0, 0
        self._closed = False
        self._diagnostics = Diagnostics()

    def run(self, code, *, interrupt_token=None):
        if self._closed or threading.get_ident() != self._thread:
            raise ValueError('This Python console is closed or outside its scientific GUI thread')
        code = source(code)
        self._sequence += 1
        if self._sequence > 2**53 - 1:
            raise ValueError('Restart this Python console before continuing')
        output, errors, result, failure = _Output(), _Output(), _Output(), _Output()
        name = '__hooke_result_' + secrets.token_hex(16)
        filename = f'<Hooke Python {self._sequence}>'
        self._diagnostics.remember(filename, code)
        with redirect_stdout(output), redirect_stderr(errors):
            try:
                with self._interrupts.running(interrupt_token) if self._interrupts is not None else nullcontext():
                    tree = compile(code, filename, 'exec', ast.PyCF_ONLY_AST | self._flags, dont_inherit=True)
                    expression = bool(tree.body and isinstance(tree.body[-1], ast.Expr))
                    if expression:
                        last = tree.body[-1]
                        tree.body[-1] = ast.copy_location(ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())],
                                                                    value=last.value), last)
                        ast.fix_missing_locations(tree)
                    executable = compile(tree, filename, 'exec', self._flags, dont_inherit=True)
                    self._flags |= executable.co_flags & FUTURE_FLAGS
                    exec(executable, self._namespace)
                    if expression:
                        value = self._namespace.pop(name, None)
                        if value is not None:
                            result.write(reprlib.repr(value))
            except BaseException as error:
                # A Python exception leaves any private mutations visible for inspection.
                # Hangs, process exit and resource violations remain the parent's responsibility.
                self._diagnostics.describe(error, failure.write)
            finally:
                self._namespace.pop(name, None)
        return reply({'format': 'scientific-console-v1', 'sequence': self._sequence,
                      'stdout': output.text(), 'stderr': errors.text(), 'result': result.text(),
                      'error': failure.text(), 'truncated': any(s.truncated for s in (output, errors, result, failure)),
                      'privatePreview': True})

    def close(self):
        self._closed = True
        self._namespace.clear()
        self._diagnostics.clear()

    def assist(self, mode, expression):
        if self._closed or threading.get_ident() != self._thread:
            raise ValueError('This Python console is closed or outside its scientific GUI thread')
        return scientific_python_help.assist(self._namespace, mode, expression)
