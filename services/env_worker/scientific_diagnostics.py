"""Bounded Python error context from submitted cells, without reading source files.

No locals or array reprs are collected. Old source text may leave this display
cache; the independently retained scientific history remains authoritative.
"""
from collections import OrderedDict, deque


class Diagnostics:
    def __init__(self, *, allowance=1 << 20):
        self.allowance = allowance
        self.size = 0
        self.sources = OrderedDict()

    def remember(self, filename, source):
        cost = len(source.encode('utf-8'))
        previous = self.sources.pop(filename, None)
        if previous is not None:
            self.size -= len(previous.encode('utf-8'))
        if cost <= self.allowance:
            self.sources[filename] = source
            self.size += cost
        while self.size > self.allowance or len(self.sources) > 128:
            _, old = self.sources.popitem(last=False)
            self.size -= len(old.encode('utf-8'))

    def clear(self):
        self.sources.clear()
        self.size = 0

    def describe(self, error, write):
        """Keep the exception summary first for existing transcript consumers."""
        seen = set()
        for _ in range(8):
            if id(error) in seen:
                write('\nRepeated exception context omitted.')
                return
            seen.add(id(error))
            self._exception(error, write)
            cause = BaseException.__cause__.__get__(error)
            if cause is not None:
                write('\nCaused by:\n')
            elif not BaseException.__suppress_context__.__get__(error):
                cause = BaseException.__context__.__get__(error)
                if cause is not None:
                    write('\nWhile handling this earlier exception:\n')
            if cause is None:
                return
            error = cause
        write('\nFurther exception context omitted.')

    def _exception(self, error, write):
        kind = type.__getattribute__(type(error), '__name__')
        try:
            message = str(error)
        except BaseException:
            message = 'exception text unavailable'
        write(f'{kind}: {message}')
        trace = BaseException.__traceback__.__get__(error)
        frames = deque(maxlen=16)
        seen = 0
        while trace is not None and seen < 256:
            code = trace.tb_frame.f_code
            # Omit the execution wrapper; keep upstream library locations too.
            if code.co_filename != __file__ and not (
                    code.co_filename.replace('\\', '/').endswith('/scientific_console.py') and code.co_name == 'run'):
                frames.append((code.co_filename, trace.tb_lineno, code.co_name))
            seen += 1
            trace = trace.tb_next
        if frames:
            write('\nTraceback (most recent call last; locations only, no local values):')
            if seen > len(frames) + 1 or trace is not None:
                write('\n  Some traceback frames omitted.')
            for filename, line, function in frames:
                write(f'\n  {filename[:512]}, line {line}, in {function[:240]}')
                self._line(filename, line, write)
        if issubclass(type(error), SyntaxError):
            filename = SyntaxError.filename.__get__(error)
            line = SyntaxError.lineno.__get__(error)
            if type(filename) is str and type(line) is int:
                write(f'\n  {filename[:512]}, line {line}')
                self._line(filename, line, write)

    def _line(self, filename, number, write):
        source = self.sources.get(filename)
        if source is None:
            return
        # Submitted cells are already bounded; do not consult linecache, imports,
        # filesystem source, descriptors or the failed frame's locals.
        lines = source.splitlines()
        if 1 <= number <= len(lines):
            write('\n    ' + lines[number-1][:1024])
