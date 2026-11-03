"""A publication carries cleaned copies of imported images; the Project keeps its originals (ADR 0028, ticket 06)."""
import hashlib
import io
import json

import pytest
from PIL import Image

from orchestrator import scientific_files as files
from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_metadata_scan import kinds_of
from orchestrator.project_publish import prepare, publish
from orchestrator.project_remote_folder import folder_remote
from orchestrator.project_repository import ProjectRepository, document_root
from orchestrator.project_start_from import start_from, update_from
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from tests.test_project_metadata_scan import jpeg
from tests.test_project_publish import SIGNOFF, elsewhere
from tests.test_project_remote import project


def photo():
    """A bench photo that names its owner and where it was taken, over 1 MiB so it spans two chunks."""
    exif = Image.Exif()
    exif[0x013B] = 'Ada Researcher'
    exif[0x8825] = {1: 'N', 2: (51.0, 30.0, 0.0)}
    out = io.BytesIO()
    Image.effect_noise((1100, 900), 90).convert('RGB').save(out, 'JPEG', exif=exif, quality=98)
    body = out.getvalue()
    assert len(body) > files.CHUNK_BYTES
    return body


def imported(root, body, name, request='synthetic-photo-012345'):
    intake = FileIntake(ProjectStore(document_root(root)), root.session, request, name=name, size=len(body),
                        max_bytes=len(body))
    try:
        for start in range(0, len(body), files.CHUNK_BYTES):
            intake.write(body[start:start + files.CHUNK_BYTES])
        return intake.publish()
    finally:
        intake.close()


def read(store, saved, session):
    ref = saved.document['state']['sourceFile']['reference']
    source = store.file_source(saved.id, session, saved.revision, ref)
    return source, b''.join(source.read(at, min(files.CHUNK_BYTES, source.size - at))
                            for at in range(0, source.size, files.CHUNK_BYTES))


def pixels(body):
    with Image.open(io.BytesIO(body)) as image:
        return image.tobytes()


def everything(folder):
    return b''.join(p.read_bytes() for p in sorted(folder.rglob('*')) if p.is_file())


def test_a_publication_carries_a_cleaned_photo_and_the_project_keeps_its_original(tmp_path):
    root, *_ = project(tmp_path)
    original = photo()
    saved = imported(root, original, 'bench-photo.jpg')
    review = prepare(lambda d: copy_snapshot(root, d), tmp_path / 'Review')
    assert review['cleanedFiles'] == 1 and review['cleanMetadata']
    item = review['cleaned'][0]
    assert item['path'].startswith('bench-photo.jpg') and {'location', 'person'} <= set(item['removed'])
    assert item['pixels'] == 'identical'
    assert not any(f['kind'] in ('metadata-location', 'metadata-person') for f in review['findings'])
    sent = everything(tmp_path / 'Review' / 'publication')
    assert b'Ada Researcher' not in sent and original[:4096] not in sent
    # The Project's own file is untouched.
    _, mine = read(ProjectStore(document_root(root)), saved, root.session)
    assert mine == original
    # Another Mac starts from it and reads the cleaned copy: the same pixels, no personal metadata.
    remote = folder_remote(tmp_path / 'Public', create=True)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    readme = next(e for e in remote.records.read_commit(remote.records.head())['entries'] if e['path'] == 'README.md')
    assert 'Personal metadata' in remote.records.read_blob(readme['ref'], 1 << 20).decode()
    working, _, came = start_from(remote, elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    assert saved.id in came['added'] and not came['refused']
    copy = ProjectRepository(working).read(saved.id, 1)
    assert copy.document == saved.document and copy.digest == saved.digest  # the document byte for byte
    source, theirs = read(ProjectStore(document_root(working)), copy, root.session)
    assert kinds_of(theirs) == set() and b'Ada Researcher' not in theirs and pixels(theirs) == pixels(original)
    assert source.cleaned['originalSha256'] == hashlib.sha256(original).hexdigest()
    assert source.sha256 == hashlib.sha256(theirs).hexdigest() and 'location' in source.cleaned['removed']
    # Readers compare a file with what its documents name: the original's identity, which the copy keeps.
    assert (source.original_sha256, source.original_size) == (hashlib.sha256(original).hexdigest(), len(original))
    assert not update_from(working, remote, origin=root.origin, staging=tmp_path / 'Update')['refused']


def test_cleaning_can_be_turned_off_and_the_finding_stays_for_the_owner(tmp_path):
    root, *_ = project(tmp_path)
    original = photo()
    imported(root, original, 'bench-photo.jpg')
    review = prepare(lambda d: copy_snapshot(root, d), tmp_path / 'Review', clean_metadata=False)
    assert review['cleanedFiles'] == 0 and not review['cleanMetadata']
    assert any(f['kind'] == 'metadata-location' for f in review['findings']) and not review['blocked']
    assert original[:4096] in everything(tmp_path / 'Review' / 'publication')


def test_a_file_hooke_cannot_check_is_named_and_an_image_with_nothing_to_remove_goes_as_it_is(tmp_path):
    root, *_ = project(tmp_path)
    imported(root, b'\x00\x00\x00\x18ftypheic' + bytes(4096), 'phone.heic')
    plain = io.BytesIO()
    Image.new('L', (8, 8)).save(plain, 'PNG')
    imported(root, plain.getvalue(), 'plain.png', request='synthetic-plain-0123456')
    review = prepare(lambda d: copy_snapshot(root, d), tmp_path / 'Review')
    assert [f['path'].split(' ')[0] for f in review['findings'] if f['kind'] == 'metadata-unchecked'] == ['phone.heic']
    assert review['cleanedFiles'] == 0 and plain.getvalue() in everything(tmp_path / 'Review' / 'publication')


def held(tmp_path):
    """A Project whose photo's original bytes were left out for a cleaned copy, as a publication holds it."""
    root, *_ = project(tmp_path)
    saved = imported(root, jpeg(), 'bench-photo.jpg')
    folder = document_root(root) / saved.id / 'files'
    ref = saved.document['state']['sourceFile']['reference']
    meta = json.loads((folder / f'{ref["revision"]}.json').read_bytes())
    return root, saved, folder, ref, meta


def clean_manifest(folder, ref, meta, body, **change):
    import os
    from orchestrator.scientific_arrays import DIRECTORY, _put
    fd = os.open(folder, DIRECTORY)
    try:
        chunk = _put(fd, body, '.bin')
        manifest = {'format': files.CLEAN_FORMAT, 'of': ref['revision'], 'originalSha256': meta['sha256'],
                    'removed': ['location'], 'tool': 'test', 'name': meta['name'], 'size': len(body),
                    'sha256': hashlib.sha256(body).hexdigest(), 'chunkBytes': files.CHUNK_BYTES, 'chunks': [chunk],
                    **change}
        return _put(fd, files._encode(manifest), '.json')
    finally:
        os.close(fd)


def test_the_original_wins_wherever_its_bytes_are_present(tmp_path):
    root, saved, folder, ref, meta = held(tmp_path)
    clean_manifest(folder, ref, meta, b'something else entirely')
    source, body = read(ProjectStore(document_root(root)), saved, root.session)
    assert body == jpeg() and source.cleaned is None


def test_a_cleaned_copy_that_does_not_match_its_manifest_is_refused(tmp_path):
    root, saved, folder, ref, meta = held(tmp_path)
    for chunk in meta['chunks']:
        (folder / f'{chunk}.bin').chmod(0o600)
        (folder / f'{chunk}.bin').unlink()
    clean_manifest(folder, ref, meta, b'cleaned bytes', sha256='0' * 64)
    with pytest.raises(InvalidProject, match='hash changed'):
        read(ProjectStore(document_root(root)), saved, root.session)


def test_a_file_with_two_cleaned_copies_or_one_for_another_original_is_refused(tmp_path):
    root, saved, folder, ref, meta = held(tmp_path)
    for chunk in meta['chunks']:
        (folder / f'{chunk}.bin').unlink()
    clean_manifest(folder, ref, meta, b'cleaned bytes')
    clean_manifest(folder, ref, meta, b'other cleaned bytes')
    with pytest.raises(InvalidProject, match='more than one'):
        read(ProjectStore(document_root(root)), saved, root.session)
    for path in folder.glob('*.json'):
        if json.loads(path.read_bytes()).get('format') == files.CLEAN_FORMAT:
            path.unlink()
    clean_manifest(folder, ref, meta, b'cleaned bytes', originalSha256='1' * 64)
    with pytest.raises(InvalidProject, match='invalid'):
        read(ProjectStore(document_root(root)), saved, root.session)


def tail_text_png():
    """A PNG over 2 MiB whose text comes after its image data, so its first chunk is the same once cleaned."""
    import struct
    import zlib
    out = io.BytesIO()
    Image.effect_noise((1100, 1100), 90).convert('RGB').save(out, 'PNG', compress_level=0)
    body = out.getvalue()
    data = b'Author\0Ada Researcher at /Users/ada/bench'
    chunk = struct.pack('>I', len(data)) + b'tEXt' + data + struct.pack('>I', zlib.crc32(b'tEXt' + data))
    body = body[:-12] + chunk + body[-12:]
    assert len(body) > 2 * files.CHUNK_BYTES
    return body


def test_metadata_after_the_first_chunk_is_cleaned_and_the_copy_still_starts_from(tmp_path):
    root, *_ = project(tmp_path)
    original = tail_text_png()
    saved = imported(root, original, 'late-text.png')
    review = prepare(lambda d: copy_snapshot(root, d), tmp_path / 'Review')
    assert review['cleanedFiles'] == 1 and not any(f['kind'].startswith('metadata-') for f in review['findings'])
    remote = folder_remote(tmp_path / 'Public', create=True)
    publish(review['staging'], review_digest=review['reviewDigest'], signoff=SIGNOFF, remote=remote)
    working, _, came = start_from(remote, elsewhere(tmp_path)[3], staging=tmp_path / 'Fetch')
    assert not came['refused']
    source, theirs = read(ProjectStore(document_root(working)), ProjectRepository(working).read(saved.id, 1),
                          root.session)
    assert b'Ada Researcher' not in theirs and pixels(theirs) == pixels(original) and source.cleaned
