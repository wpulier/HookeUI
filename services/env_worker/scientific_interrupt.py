"""Owned out-of-band interruption of one exact Python execution.

The pipe carries an opaque execution token, not code or process authority. Late
requests cannot interrupt a later cell. Protocol exchanges defer KeyboardInterrupt
until their reply is consumed, so the worker's main stream stays synchronized.
"""
from contextlib import contextmanager
import os
import re
import signal

TOKEN = re.compile(r'[0-9a-f]{32}')


class Control:
    def __init__(self, descriptor, *, started=lambda token: None):
        if type(descriptor) is not int or descriptor < 0:
            raise ValueError('Python interrupt requires its owned control descriptor')
        self.started = started
        self.fd = descriptor
        os.set_blocking(self.fd, False)
        self.active = self.pending = None
        self.depth = 0
        self.previous = signal.signal(signal.SIGUSR1, self._receive)

    def _receive(self, *_):
        matched = False
        # One token per execution is admitted by the parent. Drain delayed tokens
        # before raising, so a coalesced signal cannot strand a newer request.
        for _ in range(128):
            try:
                token = os.read(self.fd, 32)
            except BlockingIOError:
                break
            if not token:
                break
            matched |= self.active is not None and token == self.active
        if matched:
            if self.depth:
                self.pending = self.active
            else:
                self.active = None
                raise KeyboardInterrupt('Interrupted by the researcher; partial private changes are kept')

    @contextmanager
    def running(self, token):
        if type(token) is not str or TOKEN.fullmatch(token) is None:
            raise ValueError('Python execution needs its exact interrupt identity')
        try:
            self.active, self.pending = token.encode('ascii'), None
            with self.protocol():
                self.started(token)
            yield
        finally:
            self.active = self.pending = None

    @contextmanager
    def protocol(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if self.depth == 0 and self.active is not None and self.pending == self.active:
                self.active = self.pending = None
                raise KeyboardInterrupt('Interrupted by the researcher; partial private changes are kept')

    def close(self):
        self.active = self.pending = None
        signal.signal(signal.SIGUSR1, self.previous)
        os.close(self.fd)
