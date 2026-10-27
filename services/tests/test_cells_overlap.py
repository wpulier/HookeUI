"""Scripted observer events establish overlap; no models or timing certification."""
import asyncio
from types import SimpleNamespace

import httpx

from sim import cells_overlap


def rpc(**facts):
    return dict(scope='cells', selected='cellpose-sam', worker='analysis', rpcId=9, **facts)


def test_residency_and_overlap_refuse_loading_and_short_completed_work():
    worker = dict(name='analysis', ready=True, resident=['cellpose-sam'], loading={})
    assert cells_overlap.resident_worker({'workers':[worker]}, 'cellpose-sam') == worker
    assert cells_overlap.resident_worker({'workers':[{**worker,'loading':{'cellpose-sam':1}}]}, 'cellpose-sam') is None
    clicks = [dict(sentAt=10, receivedAt=11, ok=True), dict(sentAt=11, receivedAt=13, ok=True),
              dict(sentAt=10, receivedAt=11, ok=False)]
    assert cells_overlap.overlap(clicks, rpc(workerStartedAt=9, workerFinishedAt=12)) == [0]
    assert cells_overlap.overlap(clicks, rpc(workerStartedAt=9, workerFinishedAt=9.9)) == []
    assert cells_overlap.overlap(clicks, rpc(workerStartedAt=9)) == []
    assert cells_overlap.cell_rpc({'steps':[rpc(),rpc()]}, 'cellpose-sam') is None


def test_wait_requires_started_exact_model_residency_and_retains_identity(monkeypatch):
    original_sleep = asyncio.sleep
    async def yield_only(seconds): await original_sleep(0)
    monkeypatch.setattr(cells_overlap.asyncio, 'sleep', yield_only)
    seen=[]
    states = [rpc(), rpc(workerStartedAt=10), rpc(workerStartedAt=10)]
    def respond(request):
        if request.url.path == '/api/execution':
            return httpx.Response(200,json={'entries':[{'id':'old','at':1,'kind':'cells'},
                                                      {'id':'new','at':9,'kind':'cells'}]})
        row=states.pop(0);seen.append(row)
        return httpx.Response(200,json={'steps':[row]})
    health_calls=[]
    async def health():
        health_calls.append(True)
        return {'workers':[dict(name='analysis',ready=True,resident=['cellpose-sam'],
                                loading={'cellpose-sam':1} if len(health_calls)==1 else {})]}
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            stack=SimpleNamespace(http=client,orch_url='http://test',orch_health=health)
            result=await cells_overlap.wait_started(stack,SimpleNamespace(results=[]),0,8,'cellpose-sam')
            assert result['receipt']=='new' and result['rpc']['rpcId']==9
            assert result['rpc']['workerStartedAt']==10
            assert len(seen)==3 and len(health_calls)==2
    asyncio.run(run())


def test_already_completed_rpc_cannot_admit_even_before_ui_completion():
    def respond(request):
        value=({'entries':[{'id':'r','at':9,'kind':'cells'}]} if request.url.path=='/api/execution'
               else {'steps':[rpc(workerStartedAt=10,workerFinishedAt=11)]})
        return httpx.Response(200,json=value)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result=await cells_overlap.wait_started(SimpleNamespace(http=client,orch_url='http://test'),
                                                   SimpleNamespace(results=[]),0,8,'cellpose-sam')
            assert 'already finished' in result['error']
    asyncio.run(run())
