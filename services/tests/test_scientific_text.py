"""Synthetic byte payloads through real retained storage and the ordinary HTTP reader."""
import pytest
from tests.test_scientific_project_api import web  # noqa: F401
from tests.test_scientific_file_api import upload
from orchestrator.scientific_text import page


def preview(client, saved, **params):
    return client.get('/api/scientific-files/'+saved['id']+'/text', params={
        'session':saved['session'],'revision':saved['revision'],**params})


def test_pages_retain_exact_ownership_and_connector_text_semantics(web):
    client,built=web
    body=b'<script>not executed</script>\n'+b'x'*65536+b'\xffend'
    saved=upload(client,built,body=body,name='protocol.md').json()['project']
    first=preview(client,saved)
    assert first.status_code==200
    data=first.json()
    assert data['session']==saved['session'] and data['digest']==saved['digest']
    assert data['sha256']==saved['document']['state']['sourceFile']['sha256']
    assert data['verification']=='displayed-chunks-only' and data['untrusted']
    assert data['content']['text']==body[:65536].decode()
    source=built.hub.resources.projects.file_source(saved['id'],saved['session'],saved['revision'],
        saved['document']['state']['sourceFile']['reference'])
    assert data['content']==page(source)
    last=preview(client,saved,offset=65536).json()['content']
    assert last==page(source,65536) and last['nextOffset'] is None and last['text'].endswith('�end')
    assert built.hub.resources.projects.read(saved['id'],saved['session']).digest==saved['digest']


@pytest.mark.parametrize('params',[{'offset':-1},{'offset':99999},{'length':0},{'length':65537},
    {'revision':0},{'revision':2},{'session':'2020-01-01'}])
def test_invalid_or_foreign_preview_refuses_without_mutation(web,params):
    client,built=web;saved=upload(client,built).json()['project']
    response=preview(client,saved,**params)
    assert response.status_code==400,response.text
    assert built.hub.resources.projects.read(saved['id'],saved['session']).digest==saved['digest']


def test_preview_checks_displayed_bytes_but_does_not_scan_unread_chunks(web,monkeypatch):
    from orchestrator.scientific_files import FileSource,CHUNK_BYTES
    client,built=web;saved=upload(client,built,body=b'a'*(CHUNK_BYTES+1)).json()['project']
    reads=[];original=FileSource.read
    def observed(source,offset,length):
        reads.append((offset,length));return original(source,offset,length)
    monkeypatch.setattr(FileSource,'read',observed)
    assert preview(client,saved).status_code==200
    assert reads==[(0,65536)]
    store=built.hub.resources.projects
    source=store.file_source(saved['id'],saved['session'],1,saved['document']['state']['sourceFile']['reference'])
    chunk=store._folder(saved['id'])/'files'/(source.chunks[0]+'.bin')
    chunk.chmod(0o600);chunk.write_bytes(b'b'*CHUNK_BYTES);chunk.chmod(0o400)
    assert preview(client,saved).status_code==400


def test_empty_source_has_an_empty_terminal_page(web):
    client,built=web;saved=upload(client,built,body=b'').json()['project']
    data=preview(client,saved).json()['content']
    assert data['text']=='' and data['bytes']==0 and data['nextOffset'] is None
