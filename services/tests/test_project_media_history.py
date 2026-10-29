"""Real Library edits and immutable intake; synthetic pixels, no playback claim."""
import json

import pytest

from orchestrator.library import Library
from orchestrator.project_media import ProjectMedia, _MediaIntake
from orchestrator.project_media_history import capture
from orchestrator.project_roots import ProjectLocationError
from tests.test_project_media import setup


def source_library(tmp_path, asset):
    library=Library(tmp_path/'library.jsonl')
    identity=library.register('retained:test-image',asset.path,title='Initial title',at=1,
                              origin=None,group='added')
    library.set_meta(identity,{'umPerPx':0.4,'notes':'First observation'},by='user')
    library.set_meta(identity,{'umPerPx':0.5,'notes':'Corrected observation'},by='agent:historical')
    return library,identity


def test_media_retains_historical_calibration_without_copying_unrelated_library(tmp_path):
    locations,day,sid,stores,asset=setup(tmp_path)
    try:
        library,identity=source_library(tmp_path,asset)
        unrelated=tmp_path/'unrelated.png'; unrelated.write_bytes(b'unrelated')
        other=library.register('unrelated',unrelated,title='Private unrelated item',at=2,origin=None)
        library.set_meta(other,{'notes':'Must not enter this Project'})
        media=ProjectMedia(stores)
        ref=media.retain_library(sid,library,identity,request='library-copy-001',max_bytes=4096,
                                 producer={'actor':'user'})
        metadata,source=media.read(ref)
        assert source.read(0,source.size)==asset.path.read_bytes()
        assert metadata['asset']['optics']['umPerPx']==0.5
        rows=metadata['libraryHistory']['records']
        assert [r['set']['umPerPx'] for r in rows if 'set' in r]==[0.4,0.5]
        assert rows[-1]['by']=='agent:historical'
        assert all(r.get('id')==identity for r in rows)
        assert 'Private unrelated' not in json.dumps(metadata)
        library.set_meta(other,{'notes':'Unrelated subsequent edit'})
        assert media.retain_library(sid,library,identity,request='library-copy-001',max_bytes=4096,
                                    producer={'actor':'user'}) == ref
        library.set_meta(identity,{'notes':'Later work'})
        asset.path.unlink(); library.path.unlink()
        retained,_=media.read(ref)
        assert retained==metadata
    finally: locations.close()


def test_metadata_change_during_pixel_copy_refuses_visible_publication(tmp_path,monkeypatch):
    locations,day,sid,stores,asset=setup(tmp_path)
    try:
        library,identity=source_library(tmp_path,asset)
        original=_MediaIntake.write
        def changed(self,body):
            original(self,body)
            library.set_meta(identity,{'umPerPx':0.6})
        monkeypatch.setattr(_MediaIntake,'write',changed)
        with pytest.raises(ProjectLocationError,match='metadata changed'):
            ProjectMedia(stores).retain_library(sid,library,identity,request='library-copy-002',
                max_bytes=4096,producer={'actor':'user'})
        assert stores.list(sid)==[]
        assert asset.path.exists()
    finally: locations.close()


@pytest.mark.parametrize('damage',['truncated','symlink','conflicting','oversized'])
def test_incomplete_library_history_cannot_be_claimed_as_collected(tmp_path,damage):
    locations,day,sid,stores,asset=setup(tmp_path)
    try:
        library,identity=source_library(tmp_path,asset)
        if damage=='truncated':
            with library.path.open('ab') as out: out.write(b'{"id":')
        if damage=='symlink':
            original=library.path.with_suffix('.original'); library.path.rename(original)
            library.path.symlink_to(original)
        if damage=='conflicting':
            with library.path.open('a') as out: out.write(json.dumps({'id':identity,'key':'different'})+'\n')
        if damage=='oversized':
            with library.path.open('a') as out: out.write('x'*65537+'\n')
        with pytest.raises((OSError,ProjectLocationError)):
            capture(library,identity)
        assert stores.list(sid)==[]
    finally: locations.close()


def test_metadata_acceptance_holds_the_same_library_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    locations,day,sid,stores,asset=setup(tmp_path)
    try:
        library,identity=source_library(tmp_path,asset)
        selected=capture(library,identity)
        started,done=Event(),Event()
        def edit():
            started.set(); library.set_meta(identity,{'notes':'After publication'}); done.set()
        with ThreadPoolExecutor(1) as pool:
            with selected.acceptance():
                future=pool.submit(edit)
                assert started.wait(5)
                assert not done.is_set()
            future.result(timeout=5)
        assert done.is_set()
    finally: locations.close()
