"""Source transport keeps the small control/reply contract and exact UTF-8 bytes."""
import asyncio
import hashlib
import io
import json
from pathlib import Path
import uuid

import httpx
import numpy as np
import pytest

from env_worker import scientific_source_wire as wire
from env_worker.scientific_console import SOURCE_BYTES, TEXT_BYTES
from env_worker.server import read_message, write_message
from orchestrator import scientific_host
from orchestrator.scientific_projects import ProjectStore
from tests.test_scientific_host_worker import actual_host  # noqa: F401
from tests.test_native_regions import source
from tests.test_scientific_review_api import api, opening, HEADERS, ORIGIN


def request(code):
    return {'op':'python-resources','source':code,'interruptToken':'a'*32}


def test_source_body_is_exact_utf8_without_increasing_control_header_or_transcript_limits():
    code='sample = "'+'µ\\\"'*20000+'"\n'
    header,payload=wire.encode(request(code))
    assert len(payload)>16<<10 and len(json.dumps(header))<1024
    stream=io.BytesIO();write_message(stream,header,payload);stream.seek(0)
    h,b=read_message(stream,max_header=128<<10,max_payload=SOURCE_BYTES,admit=wire.admit)
    assert wire.decode(h,b)==request(code)
    assert TEXT_BYTES==16<<10
    for bad in [b'x'+payload[1:],payload[:-1],b'\xff'*len(payload)]:
        with pytest.raises(ValueError):wire.decode(header,bad)
    invalid=b'\xff'*len(payload)
    with pytest.raises(UnicodeDecodeError):
        wire.decode({**header,'sourceSha256':hashlib.sha256(invalid).hexdigest()},invalid)
    assert wire.decode({'op':'render','width':10,'height':10},b'')['op']=='render'


@pytest.mark.parametrize('patch',[
 {'op':'render'},{'source':'second source'},{'sourceBytes':True},{'sourceBytes':SOURCE_BYTES+1},
 {'sourceSha256':'no'},{'interruptToken':'no'},{'actor':'studio'}])
def test_source_metadata_rejected_before_body_read(patch):
    header,payload=wire.encode(request('x=1'));header.update(patch)
    stream=io.BytesIO();write_message(stream,header,payload);stream.seek(0)
    with pytest.raises(ValueError):read_message(stream,max_header=128<<10,max_payload=SOURCE_BYTES,admit=wire.admit)
    assert stream.read()==payload


def test_script_route_preserves_origin_resource_and_duplicate_execution_authority(tmp_path):
    with api(tmp_path) as (rig,transport,_):
        async def go():
            async with httpx.AsyncClient(transport=transport,base_url=ORIGIN,headers=HEADERS) as http:
                try:
                    identity=(await http.post('/api/scientific-reviews',json=opening(rig))).json()['review']
                    base='/api/scientific-reviews/'+identity
                    code='# retained scientific script\n'+'# µ calibration\n'*9000+'1+1'
                    fields={'request':{'op':'python-resources','resource':rig.ref,'cell':uuid.uuid4().hex,'source':code},'draw':None}
                    assert (await http.post(base+'/request',json=fields)).status_code==409
                    for forged in [dict(fields,request={**fields['request'],'actor':'studio'}),
                                   dict(fields,request={**fields['request'],'resource':{**rig.ref,'version':999}}),
                                   {'request':{'op':'render','width':2,'height':3},'draw':None}]:
                        assert (await http.post(base+'/script',json=forged)).status_code in (400,409)
                    assert not rig.workers[0].requests
                    denied=await http.post(base+'/script',json=fields,headers={**HEADERS,'origin':'https://elsewhere.invalid'})
                    assert denied.status_code==403
                    result=await http.post(base+'/script',json=fields)
                    assert result.status_code==200,result.text
                    assert result.json()['console']['result']=='2'
                    assert len(rig.workers[0].requests)==1
                    again=await http.post(base+'/script',json=fields)
                    assert again.status_code==200 and len(rig.workers[0].requests)==1
                    owner=rig.manager._owners[identity]
                    history=owner.history.record
                    reopened=ProjectStore(rig.store.root).read(history.id,history.session)
                    assert reopened.document['state']['cells'][0]['source']==code
                    assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
                finally:await rig.manager.close()
        asyncio.run(go())


def test_actual_upstream_geometry_script_runs_through_bounded_body_and_keeps_kernel(actual_host):
    # Execute the pinned upstream's real ~30 KiB geometry module as a user script,
    # not a tiny cell with padding. Its numerical output has an independent oracle.
    folder=Path(actual_host['folder'])
    paths=list(folder.glob('lib/python*/site-packages/napari/utils/geometry.py'))
    assert len(paths)==1
    code=paths[0].read_text()
    assert 16<<10 < len(code.encode()) < SOURCE_BYTES
    code+='\npoints, distances = project_points_onto_plane(np.array([[1,2,3],[4,5,6]]),np.array([0,0,0]),np.array([0,0,1]))\nassert points.tolist()==[[1,2,0],[4,5,0]]\nassert distances.tolist()==[3,6]\nviewer.add_points(points, name="Projected geometry")\nlarge_script_value=int(distances.sum())\nlarge_script_value\n'
    values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);original=values.tobytes()
    from dataclasses import replace
    import threading
    from tests.waiting import HANG_S
    entered, release, block = threading.Event(), threading.Event(), threading.Event()
    base = source(values, [])
    def read(slices):
        if block.is_set():
            entered.set()
            assert release.wait(HANG_S)
        return base.read(slices)
    with scientific_host.review(actual_host['key'],folder.name,{'volume':replace(base, read=read)}) as worker:
        result,_=worker.request({'op':'python-resources','source':code})
        assert not result['console']['error'],result
        assert result['console']['result']=='9'
        assert worker.request({'op':'python-resources','source':'large_script_value+1'})[0]['console']['result']=='10'
        with pytest.raises(ValueError):worker.request({'op':'python-resources','source':'#'*(SOURCE_BYTES+1)})
        assert worker.request({'op':'python-resources','source':'large_script_value'})[0]['console']['result']=='9'
        from concurrent.futures import ThreadPoolExecutor
        block.set()
        with ThreadPoolExecutor(max_workers=1) as pool:
            running=pool.submit(worker.request,{'op':'python-resources','source':code+'\nlarge_script_value=73\ninputs["volume"].read([[0,1],[0,2],[0,3]])\nwhile True:\n    pass\n'})
            try:
                assert entered.wait(HANG_S)
                assert worker.interrupt_python()
            finally:
                release.set()
            interrupted,_=running.result(timeout=HANG_S)
            assert interrupted['console']['error'].startswith('KeyboardInterrupt:')
        assert worker.request({'op':'python-resources','source':'large_script_value'})[0]['console']['result']=='73'
    assert values.tobytes()==original
