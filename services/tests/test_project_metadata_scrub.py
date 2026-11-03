"""The publication cleaner removes personal metadata from JPEG, PNG and TIFF without touching a pixel (ticket 06)."""
import io
import json

import numpy as np
import pytest
import tifffile
from PIL import Image, PngImagePlugin

from orchestrator import project_metadata_scrub as scrub
from orchestrator.project_metadata_scan import kinds_of
from tests.test_project_metadata_scan import jpeg, png, tiff


def cleaned(tmp_path, body, name='image'):
    before, after = tmp_path / f'{name}-before', tmp_path / f'{name}-after'
    before.write_bytes(body)
    after.write_bytes(scrub.clean_bytes(body))
    return before, after


def test_a_phone_photo_loses_its_location_and_owner_and_keeps_its_pixels_and_orientation(tmp_path):
    exif = Image.Exif()
    exif[0x013B] = 'Ada Researcher'
    exif[0x0112] = 6
    exif[0x8825] = {1: 'N', 2: (51.0, 30.0, 0.0)}
    out = io.BytesIO()
    Image.effect_noise((32, 24), 40).convert('RGB').save(out, 'JPEG', exif=exif, quality=90)
    before, after = cleaned(tmp_path, out.getvalue())
    body = after.read_bytes()
    assert kinds_of(body) == set() and b'Ada Researcher' not in body
    assert scrub.same_pixels(before, after) is True
    assert Image.open(after).getexif().get(0x0112) == 6
    assert not Image.open(after).getexif().get_ifd(0x8825)


def test_png_text_goes_and_its_rendering_chunks_stay(tmp_path):
    out = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text('Author', 'Ada Researcher')
    Image.effect_noise((16, 16), 30).save(out, 'PNG', pnginfo=info, dpi=(300, 300))
    before, after = cleaned(tmp_path, out.getvalue())
    assert b'Ada' not in after.read_bytes() and kinds_of(after.read_bytes()) == set()
    assert scrub.same_pixels(before, after) is True
    assert round(Image.open(after).info['dpi'][0]) == 300
    assert scrub.clean_bytes(after.read_bytes()) == after.read_bytes()  # nothing left to remove


def test_a_tiff_loses_its_free_text_and_home_path_and_keeps_its_scale(tmp_path):
    out = io.BytesIO()
    Image.effect_noise((16, 16), 30).save(out, 'TIFF', dpi=(2540, 2540),
                                          tiffinfo={270: 'run 4 by /Users/ada/scope', 315: 'Ada Researcher'})
    before, after = cleaned(tmp_path, out.getvalue())
    body = after.read_bytes()
    assert b'/Users/ada' not in body and b'Ada Researcher' not in body and kinds_of(body) == set()
    assert scrub.same_pixels(before, after) is True
    assert round(Image.open(after).info['dpi'][0]) == 2540
    assert len(body) == len(before.read_bytes())  # rewritten in place; the freed bytes are zeroed


OME = ('<?xml version="1.0"?><OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06" Creator="Scope 2.1">'
       '<Experimenter ID="Experimenter:0" UserName="ada" Email="ada@example.org"/>'
       '<Instrument ID="Instrument:0"><Microscope SerialNumber="SN-99812" Model="Eclipse"/></Instrument>'
       '<Image ID="Image:0" Name="/Users/ada/Desktop/plate 3.tif"><AcquisitionDate>2026-10-01T09:00:00</AcquisitionDate>'
       '<Description>patient sample 17</Description><ExperimenterRef ID="Experimenter:0"/>'
       '<Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint8" SizeX="16" SizeY="16" SizeZ="1" SizeC="1" SizeT="1" '
       'PhysicalSizeX="0.325" PhysicalSizeY="0.325"><Channel ID="Channel:0:0" SamplesPerPixel="1"/>'
       '<TiffData/></Pixels></Image></OME>')


def test_ome_xml_keeps_its_pixels_and_physical_size_and_loses_people_dates_serials_and_paths(tmp_path):
    out = io.BytesIO()
    tifffile.imwrite(out, np.arange(256, dtype=np.uint8).reshape(16, 16), description=OME, metadata=None)
    before, after = cleaned(tmp_path, out.getvalue())
    body = after.read_bytes()
    for gone in (b'ada', b'SN-99812', b'2026-10-01', b'patient sample', b'/Users', b'Scope 2.1'):
        assert gone not in body
    with tifffile.TiffFile(after) as copy:
        assert 'PhysicalSizeX="0.325"' in copy.pages[0].description and 'plate 3.tif' in copy.pages[0].description
    assert scrub.same_pixels(before, after) is True


def test_imagej_keeps_its_spacing_and_stack_and_loses_its_info(tmp_path):
    out = io.BytesIO()
    stack = np.arange(3 * 8 * 8, dtype=np.uint16).reshape(3, 8, 8)
    tifffile.imwrite(out, stack, imagej=True, resolution=(2.0, 2.0),
                     metadata={'spacing': 1.5, 'unit': 'um', 'axes': 'ZYX', 'Info': 'saved by ada in /Users/ada/x'})
    before, after = cleaned(tmp_path, out.getvalue())
    assert b'/Users/ada' not in after.read_bytes()
    with tifffile.TiffFile(after) as copy:
        assert copy.is_imagej and copy.imagej_metadata['spacing'] == 1.5
        assert copy.series[0].shape == (3, 8, 8)
        assert np.array_equal(copy.asarray(), stack)


def test_a_stack_written_by_hooke_keeps_its_shape(tmp_path):
    out = io.BytesIO()
    stack = np.arange(2 * 3 * 4 * 4, dtype=np.uint8).reshape(2, 3, 4, 4)
    tifffile.imwrite(out, stack, metadata={'axes': 'TZYX', 'Author': 'Ada Researcher'})
    before, after = cleaned(tmp_path, out.getvalue())
    assert b'Ada Researcher' not in after.read_bytes()
    with tifffile.TiffFile(after) as copy:
        assert copy.series[0].shape == (2, 3, 4, 4) and copy.series[0].axes == 'TZYX'


def test_a_slide_scan_drops_its_label_and_macro_photographs_and_keeps_its_scale(tmp_path):
    slide = np.arange(64 * 64, dtype=np.uint8).reshape(64, 64)
    label = np.frombuffer(b'PATIENT-JONES-' * 74, dtype=np.uint8)[:32 * 32].reshape(32, 32)
    path = tmp_path / 'scan.svs'
    with tifffile.TiffWriter(path) as writer:
        writer.write(slide, description='Aperio Image Library v12.0.15\r\n64x64 (256x256) JPEG/RGB Q=70|AppMag = 20'
                                        '|MPP = 0.4990|User = 7f1e-ada|Filename = Jones biopsy|Date = 10/01/26')
        writer.write(label, description='Aperio Image Library v12.0.15\r\nlabel 32x32')
        writer.write(label, description='Aperio Image Library v12.0.15\r\nmacro 32x32')
    copy = tmp_path / 'clean.svs'
    copy.write_bytes(path.read_bytes())
    import mmap
    with copy.open('r+b') as stream, mmap.mmap(stream.fileno(), 0) as view:
        extra, dropped = scrub.clean_tiff_in_place(view, len(view))
    with copy.open('ab') as stream:
        stream.write(extra)
    body = copy.read_bytes()
    assert dropped == 2 and b'PATIENT-JONES' not in body and b'Jones biopsy' not in body and b'7f1e-ada' not in body
    with tifffile.TiffFile(copy) as cleaned_scan:
        assert len(cleaned_scan.pages) == 1
        assert 'MPP = 0.4990' in cleaned_scan.pages[0].description and 'AppMag = 20' in cleaned_scan.pages[0].description
    assert scrub.same_pixels(path, copy, dropped) is True


def test_bigtiff_is_cleaned_too(tmp_path):
    out = io.BytesIO()
    tifffile.imwrite(out, np.ones((8, 8), np.uint8), bigtiff=True, description='made at /home/ada/lab', metadata=None)
    before, after = cleaned(tmp_path, out.getvalue())
    assert b'/home/ada' not in after.read_bytes() and scrub.same_pixels(before, after) is True


def test_other_formats_and_damaged_files_are_refused_never_guessed():
    with pytest.raises(scrub.UnknownImageFormat):
        scrub.clean_bytes(b'\x00\x00\x00\x18ftypheic' + bytes(64))
    with pytest.raises(scrub.ImageUnreadable):
        scrub.clean_bytes(jpeg()[:40])
    with pytest.raises(scrub.ImageUnreadable):
        scrub.clean_bytes(png()[:30])
    with pytest.raises(scrub.ImageUnreadable):
        scrub.clean_bytes(b'II*\0' + b'\xff\xff\xff\x7f' + bytes(8))


def test_a_copy_that_no_longer_decodes_is_caught(tmp_path):
    before = tmp_path / 'before.png'
    before.write_bytes(png())
    broken = tmp_path / 'broken.png'
    broken.write_bytes(png()[:60])
    assert scrub.same_pixels(before, broken) is False
    assert scrub.same_pixels(tmp_path / 'before.png', before) is True
    assert scrub.shaped(json.dumps({'shape': [2, 2], 'Author': 'x'})) == '{"shape": [2, 2]}'


def test_the_common_test_images_all_clean(tmp_path):
    for index, body in enumerate((jpeg(), png(), tiff())):
        before, after = cleaned(tmp_path, body, name=str(index))
        assert kinds_of(after.read_bytes()) == set() and scrub.same_pixels(before, after) is True


def test_image_data_the_cleaner_does_not_follow_is_left_exactly_as_it_was(tmp_path):
    """An ImageJ stack read from its first directory alone: its later planes are referenced by nothing the cleaner
    reads, so they must not be wiped."""
    import struct
    stack = (np.arange(3 * 16 * 16, dtype=np.uint16).reshape(3, 16, 16) * 7 + 1).astype(np.uint16)
    out = io.BytesIO()
    tifffile.imwrite(out, stack, imagej=True, metadata={'axes': 'ZYX', 'Info': 'by /Users/ada'})
    body = bytearray(out.getvalue())
    first = struct.unpack_from('<I', body, 4)[0]
    count = struct.unpack_from('<H', body, first)[0]
    struct.pack_into('<I', body, first + 2 + count * 12, 0)  # the first directory alone, as ImageJ writes large stacks
    before, after = cleaned(tmp_path, bytes(body))
    assert b'/Users/ada' not in after.read_bytes()
    with tifffile.TiffFile(before) as a, tifffile.TiffFile(after) as z:
        assert np.array_equal(a.series[0].asarray(), z.series[0].asarray())
        assert z.series[0].shape == (3, 16, 16)
    assert stack[2].tobytes() in after.read_bytes()
