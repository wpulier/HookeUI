"""Identity claims are checked against the answering scene, not a stale browser bracket."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from sim.harness import Viewer
from tests.test_sim_viewer import Connection
from tests.test_population import world  # noqa: F401 (the public-tool fixture)


def scene(version, tracks):
    return {'type': 'scene', 'version': version, 'objects': [
        {'id': f'o{n}', 'track': track, 'source': 'ai:measured:e1'}
        for n, track in enumerate(tracks, 1)]}


def answer(placed=False):
    where = 'in view now (its outline: its own pixels matched there)' if placed else "I can't confirm it's still there"
    return '\n'.join(f'cell (i{n}): where: {where}' for n in (1, 2))


@pytest.mark.parametrize('health_versions,coherent', [((7, 7), True), ((8, 8), False), ((7, 8), False)])
def test_an_identity_reply_needs_the_same_scene_version_on_both_sides(monkeypatch, health_versions, coherent):
    from sim.identity_probe import observe

    async def check():
        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    # Another tool's result must not be mistaken for this query.
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': 'other',
                                             'ok': True, 'summary': answer(True)})
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                             'ok': True, 'summary': answer()})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        viewer = Viewer('ws://scripted')
        receiver = asyncio.create_task(viewer.run())
        versions = iter(health_versions)

        async def health():
            return {'scene': {'version': next(versions)}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            await connection.incoming.put(scene(7, ['refinding', 'lost']))
            await connection.incoming.put({'type': 'result', 'for': 'barrier'})
            await viewer.result_for('barrier', 0)
            observed = await observe(SimpleNamespace(orch_health=health), viewer, asyncio.get_running_loop().time() + 1)
            assert observed['coherent'] is coherent
            assert observed['reply']['summary'] == answer()
            assert observed['versions'] == [7, *health_versions]
            # The next wire cannot rewrite the retained sample that the assertion uses.
            await connection.incoming.put(scene(9, ['tracking', 'tracking']))
            await connection.incoming.put({'type': 'result', 'for': 'next'})
            await viewer.result_for('next', 0)
            assert [o['track'] for o in observed['objects'].values()] == ['refinding', 'lost']
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize('refused_first,changes,passes', [(True, False, True), (False, False, False), (True, True, False)])
def test_identity_witnesses_need_a_coherent_refusal_and_later_placement(monkeypatch, refused_first, changes, passes):
    from sim.identity_probe import follow_return

    async def check():
        viewer = Viewer('ws://scripted')
        queries = []
        step = 0
        version = 7
        tracks = ['refinding', 'lost'] if refused_first else ['tracking', 'tracking']

        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    queries.append(command)
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                             'ok': True, 'summary': answer(not refused_first or len(queries) > 1)})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        receiver = asyncio.create_task(viewer.run())

        async def health():
            nonlocal step, version
            step += 1
            # After the first answer, verify the objects and publish the new wire.
            if step == 3:
                version = 8
                await connection.incoming.put(scene(version, ['tracking', 'tracking']))
                await connection.incoming.put({'type': 'result', 'for': 'scene-barrier'})
                await viewer.result_for('scene-barrier', 0)
            return {'scene': {'version': version + (1 if changes else 0)}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            await connection.incoming.put(scene(version, tracks))
            await connection.incoming.put({'type': 'result', 'for': 'initial'})
            await viewer.result_for('initial', 0)
            result = await follow_return(SimpleNamespace(orch_health=health), viewer,
                                         {'i2': 'o2', 'i1': 'o1'}, seconds=0.35)
            assert result['passed'] is passes
            assert result['placed'] is (not changes)
            assert result['refused'] is (refused_first and not changes)
            assert result['samples'], 'the claim retains its actual public observations'
            assert len(queries) <= 3, 'inconclusive scenes must not cause a busy polling loop'
            if changes:
                assert all(not sample['coherent'] for sample in result['samples'])
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


def test_a_coherent_false_placement_still_fails_and_retains_the_answer(monkeypatch):
    from sim.identity_probe import follow_return

    async def check():
        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                             'ok': True, 'summary': answer(True)})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        viewer = Viewer('ws://scripted')
        receiver = asyncio.create_task(viewer.run())

        async def health():
            return {'scene': {'version': 7}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            await connection.incoming.put(scene(7, ['refinding', 'lost']))
            await connection.incoming.put({'type': 'result', 'for': 'initial'})
            await viewer.result_for('initial', 0)
            result = await follow_return(SimpleNamespace(orch_health=health), viewer, {'i2': 'o2', 'i1': 'o1'})
            assert not result['passed']
            assert len(result['violations']) == 1
            assert result['violations'][0]['reply']['summary'] == answer(True)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize('slow_phase', ['before', 'reply', 'after'])
def test_one_deadline_covers_health_and_reply_and_never_claims_unobserved_refusal(monkeypatch, slow_phase):
    from sim.identity_probe import follow_return

    async def check():
        queries = []
        health_calls = []
        closed = []

        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    queries.append(command)
                    if slow_phase != 'reply':
                        await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                                 'ok': True, 'summary': answer()})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        viewer = Viewer('ws://scripted')
        receiver = asyncio.create_task(viewer.run())

        async def health():
            health_calls.append(True)
            if (len(health_calls) == 1 and slow_phase == 'before') or (len(health_calls) == 2 and slow_phase == 'after'):
                try:
                    await asyncio.Future()
                finally:
                    closed.append(True)
            return {'scene': {'version': 7}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            await connection.incoming.put(scene(7, ['refinding', 'lost']))
            await connection.incoming.put({'type': 'result', 'for': 'initial'})
            await viewer.result_for('initial', 0)
            # The enclosing deadline catches any accidental 10/30s inner wait extension.
            async with asyncio.timeout(0.5):
                result = await follow_return(SimpleNamespace(orch_health=health), viewer,
                                             {'i2': 'o2', 'i1': 'o1'}, seconds=0.025)
            assert not result['passed'] and not result['refused'] and not result['placed']
            assert len(result['samples']) == 1 and not result['samples'][0]['coherent']
            assert 'deadline' in result['samples'][0]['why']
            assert result['error'] == result['samples'][0]['error'], 'a missing witness is an explicit failed outcome'
            assert len(queries) <= 1
            if slow_phase != 'reply':
                assert closed == [True], 'a timed-out health read must not outlive the probe'
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


def test_bracketing_delivered_scenes_can_reject_a_truthful_identity_answer(world, tmp_path):
    import copy
    from tests.test_identity import ask, cells, followed_outline, fov, keeping, tools

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    registry = tools(world)
    members = [m.id for m in population.live()][:2]
    ask(world, registry, "follow_cells", {"members": members, "of": population.id})
    fov(world)
    objects = [identity.outline(followed_outline(identity, i)) for i in identity.individuals()]
    for obj in objects:
        world.scene.update(obj.id, track="refinding", lost_since=1.0)
    delivered = copy.deepcopy(world.scene.to_wire())
    refused = ask(world, registry, "individuals", {"ask": "where"})
    assert "in view now" not in refused and refused.count("can't confirm") == 2

    # The server verifies its pixels before its next periodic scene broadcast. The
    # client still holds the last public wire, before AND after this tool result.
    for obj in objects:
        world.scene.update(obj.id, track="tracking", lost_since=None)
    answer = ask(world, registry, "individuals", {"ask": "where"})
    before = after = [o["track"] for o in delivered["objects"] if o["source"].startswith("ai:measured")]
    assert before == ["refinding", "refinding"]
    assert answer.count("in view now") == answer.count("its own pixels matched there") == 2
    old_false_positive = before and after and all(t != "tracking" for t in before + after) and "in view now" in answer
    assert old_false_positive, "the original sim assertion rejects this truthful public-tool result"
    assert all(o["track"] == "tracking" for o in world.scene.to_wire()["objects"])

    for obj in objects:
        world.scene.update(obj.id, track="lost", lost_since=1.0)
    refused_again = ask(world, registry, "individuals", {"ask": "where"})
    assert "in view now" not in refused_again and refused_again.count("can't confirm") == 2


@pytest.mark.parametrize('middle_tracks,middle_answer,bad', [
    (['tracking', 'lost'], answer(True), True),
    (['refinding', 'lost'], answer(True).splitlines()[0], True),
    (['refinding', 'lost'], answer(True).replace('(i2)', '(i9)'), True),
    (['refinding', 'lost'], answer(True).replace('(i2)', '(i1)'), True),
    (['tracking', 'lost'], answer(True).splitlines()[0] + '\n' + answer().splitlines()[1], False),
])
def test_a_later_good_answer_cannot_erase_a_mixed_or_malformed_false_placement(
        monkeypatch, middle_tracks, middle_answer, bad):
    from sim.identity_probe import follow_return

    async def check():
        script = [(['refinding', 'lost'], answer()), (middle_tracks, middle_answer),
                  (['tracking', 'tracking'], answer(True))]
        health_calls = 0
        queries = []

        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    queries.append(command)
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                             'ok': True, 'summary': script[len(queries) - 1][1]})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        viewer = Viewer('ws://scripted')
        receiver = asyncio.create_task(viewer.run())

        async def health():
            nonlocal health_calls
            index = health_calls // 2
            if health_calls % 2 == 0:
                await connection.incoming.put(scene(7 + index, script[index][0]))
                await connection.incoming.put({'type': 'result', 'for': f'scene-{index}'})
                await viewer.result_for(f'scene-{index}', 0)
            health_calls += 1
            return {'scene': {'version': 7 + index}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            result = await follow_return(SimpleNamespace(orch_health=health), viewer,
                                         {'i2': 'o2', 'i1': 'o1'}, seconds=0.8)
            assert result['passed'] is (not bad)
            assert result['refused'], 'the first complete refusal remains evidence'
            if bad:
                assert result['violations'], 'an incomplete or misnamed answer cannot hide its false placement'
                assert result['violations'][0]['reply']['summary'] == middle_answer
            else:
                assert result['placed'] and not result['violations']
                assert len(result['samples']) == 3, 'a truthful mixed state must remain usable'
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


@pytest.mark.parametrize('failed_phase', ['health-before', 'send', 'receiver-closed', 'reply', 'health-after'])
def test_identity_transport_failure_retains_previous_and_partial_observations(monkeypatch, failed_phase):
    import httpx
    from sim.identity_probe import follow_return

    async def check():
        health_calls = 0
        queries = []

        class Replies(Connection):
            async def send(self, message):
                command = json.loads(message)
                if command['type'] == 'tool':
                    queries.append(command)
                    if len(queries) == 2 and failed_phase == 'send':
                        raise ConnectionResetError('scripted receiver disconnect')
                    if len(queries) == 2 and failed_phase == 'reply':
                        receiver.cancel()
                        await asyncio.gather(receiver, return_exceptions=True)
                        return  # the receiver closes after sending, before the exact reply arrives
                    await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                             'ok': True, 'summary': answer()})

        connection = Replies()
        monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
        viewer = Viewer('ws://scripted')
        receiver = asyncio.create_task(viewer.run())

        async def health():
            nonlocal health_calls
            health_calls += 1
            if health_calls == 3 and failed_phase == 'health-before':
                raise httpx.ConnectError('scripted health connection failure')
            if health_calls == 3 and failed_phase == 'receiver-closed':
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)
            if health_calls == 4 and failed_phase == 'health-after':
                raise httpx.RemoteProtocolError('scripted health protocol failure')
            return {'scene': {'version': 7}, 'motion': {'epoch': 0}}

        try:
            await connection.connected.wait()
            await connection.incoming.put(scene(7, ['refinding', 'lost']))
            await connection.incoming.put({'type': 'result', 'for': 'initial'})
            await viewer.result_for('initial', 0)
            result = await follow_return(SimpleNamespace(orch_health=health), viewer,
                                         {'i2': 'o2', 'i1': 'o1'}, seconds=0.8)
            assert not result['passed'] and not result['placed']
            assert result['refused'], 'the completed refusal must survive the next transport failure'
            assert len(result['samples']) == 2
            good, partial = result['samples']
            assert good['coherent'] and good['reply']['summary'] == answer()
            assert not partial['coherent'] and result['error'] == partial['error']
            assert partial['phase'] == ('send' if failed_phase == 'receiver-closed' else failed_phase)
            assert ('connection closed' if failed_phase == 'receiver-closed' else
                    'deadline' if failed_phase == 'reply' else 'scripted') in partial['error']
            if failed_phase != 'health-before':
                assert partial['versions'] == [7, 7] and partial['objects']['o2']['track'] == 'lost'
            if failed_phase == 'health-after':
                assert partial['reply']['summary'] == answer() and partial['replyAt']
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)

    asyncio.run(check())


def test_cancelling_identity_observation_still_cancels_its_pending_health_read():
    from sim.identity_probe import observe

    async def check():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def health():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        pending = asyncio.create_task(observe(SimpleNamespace(orch_health=health), Viewer('ws://unused'),
                                              asyncio.get_running_loop().time() + 15))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert stopped.is_set()

    asyncio.run(check())


def test_followed_outline_binding_uses_public_names_not_receipt_or_scene_order(world, tmp_path):
    from sim.identity_probe import followed_outlines
    from tests.test_identity import ask, cells, followed_outline, keeping, tools

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    registry = tools(world)
    members = [m.id for m in population.live()][:2]
    ask(world, registry, 'follow_cells', {'members': members, 'of': population.id})
    ids = identity.individuals()
    words = ask(world, registry, 'individuals', {'ask': 'where'})
    wire = world.scene.to_wire()
    objects = {o['id']: o for o in reversed(wire['objects'])}
    expected = {i: identity.outline(followed_outline(identity, i)).id for i in ids}
    assert followed_outlines(population.id, members, ids[::-1], objects, words) == expected
    assert followed_outlines(population.id, members, ids, objects, words.splitlines()[0]) == {}
    assert followed_outlines('other-population', members, ids, objects, words) == {}
    first = next(iter(objects.values()))
    objects['extra'] = {**first, 'id': 'extra'}
    assert followed_outlines(population.id, members, ids, objects, words) == {}, 'ambiguous outlines must fail setup'


def test_a_refusal_witnessed_for_one_individual_counts_when_the_other_was_already_verified(monkeypatch):
    """The 2026-10-15 gate: i1 still refinding and said "can't confirm" while i2 had been re-found by its own pixels in an
    earlier pass; then both placed. A truthful answer passes; a mixed sample placing the unverified one never does."""
    from sim.identity_probe import follow_return

    def mixed(i1_words):
        return '\n'.join([f'cell (i1): where: {i1_words}',
                          'cell (i2): where: in view now (its outline: its own pixels matched there)'])

    for i1_words, passes in (("I can't confirm it's still there", True),
                             ('in view now (its outline: its own pixels matched there)', False)):
        async def check():
            viewer = Viewer('ws://scripted')
            queries = []
            step = 0
            version = 7

            class Replies(Connection):
                async def send(self, message):
                    command = json.loads(message)
                    if command['type'] == 'tool':
                        queries.append(command)
                        summary = mixed(i1_words) if len(queries) == 1 else answer(True)
                        await self.incoming.put({'type': 'result', 'for': 'tool', 'requestId': command['requestId'],
                                                 'ok': True, 'summary': summary})

            connection = Replies()
            monkeypatch.setattr('sim.harness.websockets.connect', lambda *a, **kw: connection)
            receiver = asyncio.create_task(viewer.run())

            async def health():
                nonlocal step, version
                step += 1
                if step == 3:  # after the first answer i1 is verified too
                    version = 8
                    await connection.incoming.put(scene(version, ['tracking', 'tracking']))
                    await connection.incoming.put({'type': 'result', 'for': 'scene-barrier'})
                    await viewer.result_for('scene-barrier', 0)
                return {'scene': {'version': version}, 'motion': {'epoch': 0}}

            try:
                await connection.connected.wait()
                await connection.incoming.put(scene(version, ['refinding', 'tracking']))
                await connection.incoming.put({'type': 'result', 'for': 'initial'})
                await viewer.result_for('initial', 0)
                result = await follow_return(SimpleNamespace(orch_health=health), viewer,
                                             {'i2': 'o2', 'i1': 'o1'}, seconds=0.35)
                assert result['passed'] is passes
                assert result['refused'] is passes
                assert bool(result['violations']) is (not passes)
            finally:
                receiver.cancel()
                await asyncio.gather(receiver, return_exceptions=True)

        asyncio.run(check())
