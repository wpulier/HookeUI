"""Actual manual/Registry completion with synthetic tucked-window observations."""
import asyncio

import pytest

from tests.test_native_manual_ingress import Trace, _accepted_hello, _canonical, _offers, _settled, _submit
from tests.test_workspace_actions_app import WORKSPACE, built, open_window, saved_picture


@pytest.mark.parametrize('visible', [False, True])
@pytest.mark.parametrize('corruption', [None, 'membership', 'closed', 'source', 'owner', 'payload', 'disconnected', 'zero-size-visible'])
def test_group_confirms_membership_without_claiming_tucked_media_draw(tmp_path, monkeypatch, corruption, visible):
    if corruption == 'zero-size-visible' and not visible:
        pytest.skip('zero geometry is valid for a tucked root')
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    replies = _offers(studio, monkeypatch)
    trace = Trace(app, monkeypatch)

    async def go():
        await _accepted_hello(app, studio)
        parent, _, _ = await open_window(app, entry)
        child, _, _ = await open_window(app, entry)
        send_report = studio.send_report

        async def report_tucked(user=()):
            row = studio.items[parent]
            row.update(visible=visible, phase='loaded', drawRef=None, viewDrawRef=None)
            if not visible or corruption == 'zero-size-visible':
                row.update(rect=[0, 0, 0, 0], contentRect=[0, 0, 0, 0], clipRect=[0, 0, 0, 0])
            row['load'].update(state='loaded', drawToken=None)
            for ack in studio.command_results:
                if ack['presentationId'] == parent:
                    ack.update(phase='loaded', drawRef=None, viewDrawRef=None)
            if any(message.get('op') == 'group' for message in studio.messages):
                if corruption == 'membership': row['view']['group'] = None
                elif corruption == 'closed': row['open'] = False
                elif corruption == 'source': row['sourceToken'] = 'unrelated-source'
                elif corruption == 'owner': row['propertyOwners']['group'] = 'unrelated-owner'
                elif corruption == 'payload': row['payloadBinding']['servedDigest'] = 'wrong-digest'
                elif corruption == 'disconnected': row['connected'] = False
            await send_report(user)

        monkeypatch.setattr(studio, 'send_report', report_tucked)
        await studio.send_report()
        packet = _canonical(studio, child, 1)
        packet.update(op='group', targets=[parent, child], values={})
        packet['base'][parent] = _canonical(studio, parent, 1)['base'][parent]
        await _submit(app, studio, packet, replies)
        run = await _settled(trace.calls[-1])
        if corruption:
            assert run.ok is False, run.wire()
            return
        assert run.ok is True, run.wire()
        assert studio.items[parent]['visible'] is visible
        assert studio.items[parent]['drawRef'] is None
        assert studio.store().get(WORKSPACE, parent).observed.phase == 'committed'
        assert studio.items[parent]['view']['group'] == studio.items[child]['view']['group']
        separate = _canonical(studio, child, 2)
        separate.update(op='ungroup', values={})
        await _submit(app, studio, separate, replies)
        assert (await _settled(trace.calls[-1])).ok is True
        assert all(studio.store().get(WORKSPACE, iid).view.group is None for iid in (parent, child))

    asyncio.run(go())
