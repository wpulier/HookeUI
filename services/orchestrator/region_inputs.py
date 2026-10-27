"""Per-invocation regional access to an already admitted scientific RGB source."""
import copy
import secrets

import numpy as np

from env_worker.region_reader import MAX_READ_BYTES, MAX_READ_REQUESTS, validate_region


class RegionInputs:
    def __init__(self, picture, descriptors):
        if len(descriptors) != 1:
            raise ValueError('Regional input currently requires one checked scientific image')
        self.picture = picture
        self.token = secrets.token_hex(24)
        self.port, self.descriptor = next(iter(descriptors.items()))
        self.requests = 0
        self.read_bytes = 0

    def wire(self):
        return {self.port: {'token': self.token, 'shape': list(self.picture.shape), 'dtype': str(self.picture.dtype),
                            'scientific': copy.deepcopy(self.descriptor)}}

    @staticmethod
    def is_request(header):
        return isinstance(header, dict) and header.get('op') == 'read-region'

    def check_frame(self, header, size):
        if type(header) is not dict:
            raise ValueError('A regional worker frame must contain an object header')
        if self.is_request(header) and size:
            raise ValueError('A region request cannot include a payload')

    def answer(self, header):
        if (type(header) is not dict or set(header) != {'op', 'token', 'region'}
                or header['op'] != 'read-region' or header['token'] != self.token):
            raise ValueError('This region request has no grant for the selected input')
        slices, shape, size = validate_region(header['region'], self.picture.shape)
        if self.requests >= MAX_READ_REQUESTS or self.read_bytes + size > MAX_READ_BYTES:
            raise ValueError('This operation exceeded its regional read allowance')
        self.requests += 1
        self.read_bytes += size
        data = np.ascontiguousarray(self.picture[slices])
        return {'ok': True, 'kind': 'region', 'shape': list(shape), 'dtype': 'uint8'}, memoryview(data)
