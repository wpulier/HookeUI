"""Personal metadata in a publication's imported files is shown for the owner to judge, never removed silently."""
import io
import struct

from PIL import Image, PngImagePlugin

from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_metadata_scan import kinds_of
from orchestrator.project_publish import prepare
from orchestrator.project_repository import document_root
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_remote import project


def jpeg(gps=True, artist='Ada Researcher'):
    exif = Image.Exif()
    if artist:
        exif[0x013B] = artist
    if gps:
        exif[0x8825] = {1: 'N', 2: (51.0, 30.0, 0.0)}
    out = io.BytesIO()
    Image.new('RGB', (8, 8), 'gray').save(out, 'JPEG', exif=exif)
    return out.getvalue()


def png(author='Ada Researcher', comment='saved from /Users/ada/Desktop/cells'):
    info = PngImagePlugin.PngInfo()
    info.add_text('Author', author)
    info.add_text('Comment', comment)
    out = io.BytesIO()
    Image.new('L', (8, 8)).save(out, 'PNG', pnginfo=info)
    return out.getvalue()


def tiff(description=b'acquired by /Users/ada/scope run 4'):
    out = io.BytesIO()
    Image.new('L', (8, 8)).save(out, 'TIFF', tiffinfo={270: description.decode()})
    return out.getvalue()


def test_locations_people_and_home_paths_are_found_in_common_formats():
    assert kinds_of(jpeg()) == {'location', 'person'}
    assert kinds_of(jpeg(gps=False, artist=None)) == set()
    assert kinds_of(png()) == {'person', 'home-path'}
    assert kinds_of(tiff()) == {'home-path'}
    assert kinds_of(tiff(b'<OME><Experimenter UserName="ada"/></OME>')) == {'person'}
    assert kinds_of(b'not an image at all') == set()
    assert kinds_of(b'\xff\xd8\xff\xe1\x00') == set()  # truncated header: nothing guessed, no crash


def test_a_publication_review_names_a_photo_that_carries_a_location(tmp_path):
    root, *_ = project(tmp_path)
    body = jpeg()
    intake = FileIntake(ProjectStore(document_root(root)), root.session, 'synthetic-photo-012345',
                        name='bench-photo.jpg', size=len(body), max_bytes=len(body))
    try:
        intake.write(body)
        intake.publish()
    finally:
        intake.close()
    review = prepare(lambda destination: copy_snapshot(root, destination), tmp_path / 'Review')
    found = {(f['path'].split(' ')[0], f['kind']) for f in review['findings']}
    assert ('bench-photo.jpg', 'metadata-location') in found and ('bench-photo.jpg', 'metadata-person') in found
    assert not review['blocked']  # the owner decides; accepted bytes are never edited
