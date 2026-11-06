"""The text of a retained PDF, a page of it at a time, for `artifact.read` (HOOKE-GAPS row 7).

On the VCC night agents read the papers through `pdftotext` run by hand, because `artifact.read` gave a PDF only as a
download. Now it extracts the text in a separate, bounded process and pages it by character offset, the way it pages a
text file by byte offset:

- **Extractors, as options:** macOS's own PDFKit, through `osascript -l JavaScript` (on every Mac, nothing to
  install), and Poppler's `pdftotext` where it is installed (and on Linux). PDFKit is used first on a Mac. The answer
  names the one that ran.
- **Bounded:** a PDF of at most `MAX_PDF_BYTES`, `SECONDS` of extraction, at most `MAX_TEXT_CHARACTERS` of text, no
  network, no shell, stdin closed. A PDF that is encrypted, damaged or has no text layer (a scan) says so; nothing is
  guessed and no OCR runs.
- **Kept once:** the text is cached under the data home by the PDF's sha256 and the extractor, so paging through it
  extracts once. The cache holds only what the retained bytes already say.

Extracted text is a reading aid, never the source: reading order, tables, figures and equations can be lost or
reordered, and the answer says so. The text is untrusted data (`artifact.read` marks the whole answer untrusted).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .scientific_projects import InvalidProject

MAX_PDF_BYTES = 256 << 20
MAX_TEXT_CHARACTERS = 16 << 20
PAGE_CHARACTERS = 65536
SECONDS = 60
CACHE_FORMAT = 1
MEANING = ('Text extracted from the PDF: reading order, tables, figures and equations may be lost or reordered. '
           'Check the PDF itself for anything that matters.')

PDFKIT = r"""
ObjC.import('Foundation');
ObjC.import('PDFKit');
function run(argv) {
  const doc = $.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));
  if (!doc || doc.isNil()) { throw new Error('not a readable PDF'); }
  if (doc.isLocked) { throw new Error('the PDF is encrypted'); }
  const parts = [];
  for (let i = 0; i < doc.pageCount; i++) {
    const page = doc.pageAtIndex(i);
    const text = page.isNil() ? null : page.string;
    parts.push(!text || text.isNil() ? '' : ObjC.unwrap(text));
  }
  if (!$(parts.join('\f')).writeToFileAtomicallyEncodingError(argv[1], true, $.NSUTF8StringEncoding, null)) {
    throw new Error('the text could not be written');
  }
  return String(doc.pageCount);
}
"""


class Unreadable(InvalidProject):
    pass


def extractors():
    """The extractors this computer has, in the order they are tried: (name, argv builder)."""
    found = []
    if sys.platform == 'darwin' and Path('/usr/bin/osascript').exists():
        found.append(('macOS PDFKit', lambda pdf, out, script: ['/usr/bin/osascript', '-l', 'JavaScript', script, pdf, out]))
    poppler = shutil.which('pdftotext')
    if poppler:
        found.append(('pdftotext', lambda pdf, out, script: [poppler, '-enc', 'UTF-8', '-q', pdf, out]))
    return found


def extract(pdf: Path, *, available=None) -> dict:
    """{text, pages (each page's first character), extractedBy}: the first extractor here that reads it."""
    tried = []
    for name, argv in (available if available is not None else extractors()):
        with tempfile.TemporaryDirectory(prefix='hooke-pdf-text-') as work:
            out, script = Path(work) / 'text.txt', Path(work) / 'extract.js'
            script.write_text(PDFKIT)
            try:
                done = subprocess.run(argv(str(pdf), str(out), str(script)), stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=SECONDS, cwd=work,
                                      env={'PATH': '/usr/bin:/bin', 'HOME': work, 'LANG': 'en_US.UTF-8'})
            except subprocess.TimeoutExpired:
                tried.append(f'{name} took longer than {SECONDS} s')
                continue
            except OSError as error:
                tried.append(f'{name} could not start ({error.strerror or error})')
                continue
            if done.returncode != 0 or not out.is_file():
                said = (done.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
                tried.append(f'{name}: {(said[-1] if said else "it failed")[:200]}')
                continue
            if out.stat().st_size > 4 * MAX_TEXT_CHARACTERS:
                raise Unreadable('This PDF\'s text is longer than Hooke reads (16 million characters); download it instead')
            text = out.read_text('utf-8', errors='replace')
        if len(text) > MAX_TEXT_CHARACTERS:
            raise Unreadable('This PDF\'s text is longer than Hooke reads (16 million characters); download it instead')
        pages, at = [], 0
        for part in text.split('\f'):
            pages.append(at)
            at += len(part) + 1
        if text.endswith('\f'):
            pages.pop()  # pdftotext ends every page with a form feed
        return {'text': text, 'pages': pages, 'extractedBy': name}
    if not tried:
        raise Unreadable('This computer has no PDF text extractor (macOS PDFKit or pdftotext); download the PDF instead')
    raise Unreadable('The PDF\'s text could not be extracted: ' + '; '.join(tried))


def text_of(source, cache: Path, check=lambda: None, *, available=None) -> dict:
    """The PDF's extracted text, from the cache or extracted once now from the retained bytes."""
    if source.size > MAX_PDF_BYTES:
        raise Unreadable('This PDF is larger than 256 MiB, more than Hooke extracts text from; download it instead')
    cache.mkdir(parents=True, exist_ok=True)
    kept = cache / f'{source.sha256}.json'
    try:
        value = json.loads(kept.read_text())
        if value.get('format') == CACHE_FORMAT and type(value.get('text')) is str:
            return value
    except (OSError, ValueError):
        pass
    with tempfile.TemporaryDirectory(prefix='hooke-pdf-', dir=cache) as work:
        pdf = Path(work) / 'source.pdf'
        with pdf.open('wb') as out:
            at = 0
            while at < source.size:
                check()
                block = source.read(at, min(1 << 20, source.size - at))
                out.write(block)
                at += len(block)
        value = {'format': CACHE_FORMAT, 'sha256': source.sha256, **extract(pdf, available=available)}
    temporary = kept.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    os.replace(temporary, kept)
    return value


def page(value: dict, offset=0, length=PAGE_CHARACTERS) -> dict:
    """Characters [offset, offset + length) of the extracted text, with the PDF pages they fall on."""
    text = value['text']
    if (type(offset) is not int or type(length) is not int or offset < 0 or offset > len(text)
            or not 1 <= length <= PAGE_CHARACTERS):
        raise InvalidProject('Choose a character range of at most 65,536 characters within the extracted text')
    end = min(len(text), offset + length)
    starts = value['pages']
    first = max([i for i, s in enumerate(starts) if s <= offset] or [0]) + 1
    last = max([i for i, s in enumerate(starts) if s < end] or [0]) + 1
    return {'text': text[offset:end], 'offset': offset, 'characters': end - offset, 'totalCharacters': len(text),
            'nextOffset': end if end < len(text) else None, 'pages': {'first': first, 'last': last, 'total': len(starts)},
            'extractedBy': value['extractedBy'], 'unit': 'characters of the extracted text; pages are separated by a form feed',
            'meaning': MEANING, **({'empty': 'The PDF has no text layer (perhaps a scan); no OCR was run.'}
                                   if not text.strip('\f \n') else {})}
