"""A saved flight includes the actual request's pixels and basis, not its final frame."""
import asyncio
import copy

import cv2
import numpy as np

from sim.harness import Outcome, Viewer, _outline_and_follow, save_flight


def test_follow_flight_keeps_the_clicked_view_and_initial_outline(tmp_path, monkeypatch):
    async def check():
        jpeg = cv2.imencode('.jpg', np.full((100, 100), 127, np.uint8))[1].tobytes()
        first = {'id': 1, 'fov': None, 'epoch': 0, 'dx': 0, 'dy': 0}
        later = {**first, 'id': 2}
        initial = {'id': 'o1', 'polygons': [[[40, 40], [60, 40], [60, 60], [40, 60]]],
                   'track': 'tracking', 'anchorEpoch': 0, 'anchorOffset': [0, 0]}

        class Stack:
            async def wait(self, *args):
                return True, 0

        class Browser(Viewer):
            async def send(self, message):
                self.frames.append((2., later, jpeg))
                self.scene = {'o1': copy.deepcopy(initial)}

            async def result_for(self, *args):
                return {'ok': True, 'objectId': 'o1'}

        viewer = Browser('unused', frames=[(1., first, jpeg)], candidates=[{'x': 50, 'y': 50, 'r': 8}])
        outcome = Outcome('flight')
        await _outline_and_follow(Stack(), viewer, outcome, seconds=.001)
        monkeypatch.setattr('sim.harness.RESULTS', tmp_path)
        save_flight('follow', outcome.flight)
        assert outcome.flight['clicked']['id'] == 1
        assert outcome.flight['initialObject'] == initial
        assert (tmp_path / 'flight-follow' / '0000001.jpg').read_bytes() == jpeg
        assert outcome.flight['click'] == {'frameId': 1, 'x': 50, 'y': 50}

    asyncio.run(check())
