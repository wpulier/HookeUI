"""One bounded latest-target slot for an already admitted dimension interaction."""
import copy
import re
import threading


class Navigation:
    def __init__(self, identity, frame, draw, value):
        if type(identity) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', identity):
            raise ValueError('Navigation needs its original request identity')
        self.identity, self.frame, self.draw = identity, frame, draw
        self.lock = threading.Lock()
        self.sequence = 1
        self.accepting = True
        self.value = self.validate(value)

    def validate(self, value):
        dims = self.frame[0]['dimensions']
        if (type(value) is not dict or set(value) != {'order', 'ndisplay', 'steps'}
                or type(value['order']) is not list or any(type(axis) is not int for axis in value['order'])
                or type(value['ndisplay']) is not int or value['order'] != dims['order'] or value['ndisplay'] != dims['ndisplay']
                or type(value['steps']) is not list or len(value['steps']) != len(dims['counts'])
                or any(type(step) is not int or not 0 <= step < count
                       for step, count in zip(value['steps'], dims['counts']))):
            raise ValueError('Latest navigation changes absolute slice indices only; apply axis or display changes separately')
        return copy.deepcopy(value)

    def replace(self, sequence, value):
        value = self.validate(value)
        with self.lock:
            if not self.accepting:
                raise ValueError('This navigation finished; inspect the current view before applying another target')
            if type(sequence) is not int or not 1 < sequence <= 256:
                raise ValueError('Navigation reached its bounded replacement allowance; wait for the current view')
            if sequence > self.sequence:
                self.sequence, self.value = sequence, value
            return self.sequence

    def target(self):
        with self.lock:
            return self.sequence, copy.deepcopy(self.value)

    def superseded(self, sequence):
        with self.lock:
            return sequence != self.sequence

    def finish(self, sequence):
        with self.lock:
            if sequence != self.sequence:
                return False
            self.accepting = False
            return True
