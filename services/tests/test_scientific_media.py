"""Actual HTTP/immutable storage with synthetic media, not live camera evidence."""
import io

import pytest
from PIL import Image
from tests.test_scientific_file_api import web, upload  # noqa: F401
from orchestrator.scientific_media import byte_range, RangeRefused, media_type


def image_bytes():
    output=io.BytesIO();Image.new('RGB',(3,2),(17,88,139)).save(output,format='PNG');return output.getvalue()


def preview(client,saved,**kwargs):
    return client.get('/api/scientific-files/'+saved['id']+'/media',params={
        'session':saved['session'],'revision':saved['revision'],
        'sha256':saved['document']['state']['sourceFile']['sha256']},**kwargs)


def test_media_keeps_exact_bytes_after_restart_and_never_uses_filename(web):
    from fastapi.testclient import TestClient
    from orchestrator.server import create_app
    client,built=web;body=image_bytes()
    saved=upload(client,built,body,name='misleading.txt').json()['project']
    response=preview(client,saved)
    assert response.status_code==200 and response.content==body
    assert response.headers['content-type']=='image/png'
    assert response.headers['content-disposition']=='inline'
    assert response.headers['x-content-type-options']=='nosniff'
    assert response.headers['accept-ranges']=='bytes'
    assert Image.open(io.BytesIO(response.content)).getpixel((0,0))==(17,88,139)
    restarted=TestClient(create_app('http://127.0.0.1:1',use_gpu=False,paths=built.paths))
    assert preview(restarted,saved).content==body
    # Original-byte download remains an attachment.
    original=client.get('/api/scientific-files/'+saved['id'],params={'session':saved['session'],'revision':1})
    assert original.headers['content-disposition'].startswith('attachment;')


@pytest.mark.parametrize('header,part',[('bytes=2-6',slice(2,7)),('bytes=8-',slice(8,None)),('bytes=-9',slice(-9,None)),('bytes=0-99999',slice(None))])
def test_preview_seeks_only_within_retained_bytes(web,header,part):
    client,built=web;body=image_bytes();saved=upload(client,built,body).json()['project']
    response=preview(client,saved,headers={'Range':header})
    assert response.status_code==206 and response.content==body[part]
    assert int(response.headers['content-length'])==len(body[part])
    assert response.headers['content-range'].endswith('/'+str(len(body)))


@pytest.mark.parametrize('header',['bytes=99999-','bytes=3-2','bytes=-0','bytes=','bytes=1-2,4-5','items=0-1','bytes=+1-2'])
def test_invalid_ranges_refuse_without_full_response(web,header):
    client,built=web;saved=upload(client,built,image_bytes()).json()['project']
    response=preview(client,saved,headers={'Range':header})
    assert response.status_code==416 and response.headers['content-range'].startswith('bytes */')


def test_wrong_hash_owner_and_active_document_are_not_media(web):
    client,built=web;saved=upload(client,built,b'<svg onload="alert(1)"></svg>',name='fake.png').json()['project']
    assert preview(client,saved).status_code==415
    params={'session':saved['session'],'revision':1,'sha256':'0'*64}
    assert client.get('/api/scientific-files/'+saved['id']+'/media',params=params).status_code==409
    params.update(session='2020-01-01',sha256=saved['document']['state']['sourceFile']['sha256'])
    assert client.get('/api/scientific-files/'+saved['id']+'/media',params=params).status_code==400


def test_format_recognition_does_not_render_active_or_unrecognized_content():
    assert media_type(b'<html>') is None and media_type(b'<svg>') is None
    assert media_type(b'\0\0\0\x18ftypmp42')=='video/mp4'
    assert media_type(b'\0\0\0\x18ftypqt  ')=='video/quicktime'
    assert media_type(b'\0\0\0\x18ftypavif') is None
    assert byte_range(None,0)==(0,0,False)
    with pytest.raises(RangeRefused):byte_range('bytes=0-',0)
