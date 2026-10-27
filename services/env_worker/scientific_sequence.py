"""Versioned integration-owned frame selection; transport and publication are host-owned."""
import re

FORMAT = 'scientific-frame-sequence-v1'
MAX_FRAMES = 4096


def description(value):
    if (type(value) is not dict or set(value) != {'format', 'name', 'count'}
            or value['format'] != FORMAT or type(value['name']) is not str
            or not re.fullmatch(r'[a-z][a-z0-9.-]{0,79}', value['name'])
            or type(value['count']) is not int or not 1 <= value['count'] <= MAX_FRAMES):
        raise ValueError('This sequence needs a supported version, stable name and 1–4096 frames')
    return value


class Binding:
    def __init__(self, *, version, name, count, select):
        if type(version) is not int or version != 1 or not callable(count) or not callable(select):
            raise ValueError('Declare frame-sequence version 1 with count and select callbacks')
        description({'format': FORMAT, 'name': name, 'count': 1})
        self.name, self.count, self.select = name, count, select
        self.current = None
        self.next_index = 0

    def begin(self):
        if self.current is not None:
            raise ValueError('A sequence has already started in this private export session')
        self.current = description({'format': FORMAT, 'name': self.name, 'count': self.count()})
        return dict(self.current)

    def frame(self, index):
        if (self.current is None or type(index) is not int or index != self.next_index
                or not 0 <= index < self.current['count']):
            raise ValueError('Export frames must be requested exactly once in sequence order')
        count = self.count()
        if type(count) is not int or count != self.current['count']:
            raise ValueError('The sequence changed during export; save a stable tool document and retry')
        self.select(index)
        self.next_index += 1
