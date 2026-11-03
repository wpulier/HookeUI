"""The human-facing files of a publication: README, licence notice, citation and RO-Crate metadata.

Every value from the Project (its name, document titles) is escaped for its format; nothing from it is executed or
interpreted. The licence file names the licence and links its legal code rather than reproducing it.
"""
from __future__ import annotations

import json
import re

LINKS = {'CC-BY-4.0': 'https://creativecommons.org/licenses/by/4.0/',
         'CC0-1.0': 'https://creativecommons.org/publicdomain/zero/1.0/'}
MAX_LISTED = 50


def _md(text: str) -> str:
    """Plain text safe inside Markdown: no links, images, HTML or headings from Project values."""
    text = ' '.join(str(text).split())
    return re.sub(r'([\\`*_{}\[\]()<>#+!|~])', r'\\\1', text) or '(untitled)'


def _yaml(text: str) -> str:
    return json.dumps(' '.join(str(text).split()), ensure_ascii=False)


def readme(name, licence, licence_name, documents, review, today) -> str:
    kinds = {}
    for document in documents:
        kinds.setdefault(document['kind'] or 'document', []).append(document)
    lines = [f'# {_md(name)}', '',
             f'A research project published from [Hooke](https://hookeui.org) on {today}.', '',
             '## Open it', '',
             'In Hooke, choose **Start from this project** and paste this repository’s address. Hooke checks every '
             'file against the project’s manifest before showing it, and nothing in it runs until you choose to.', '',
             '## What it holds', '']
    for kind in sorted(kinds):
        items = kinds[kind]
        lines.append(f'- **{_md(kind)}** ({len(items)})')
        for document in items[:MAX_LISTED]:
            revisions = document['revisions']
            lines.append(f'  - {_md(document["title"])}' + (f' ({revisions} revisions)' if revisions > 1 else ''))
        if len(items) > MAX_LISTED:
            lines.append(f'  - and {len(items) - MAX_LISTED} more')
    megabytes = review['bytes'] / (1 << 20)
    lines += ['', f'{review["recordFiles"]} record files and {review["largeFiles"]} large data objects, '
                  f'{megabytes:.1f} MB in all.', '',
              '## How it was made', '',
              'Each result in this project keeps its receipt: the inputs it read, the tool and version that made it, '
              'and the settings used. Open a result in Hooke to see its full history.', '',
              '## Licence', '',
              f'{licence_name} ([{licence}]({LINKS.get(licence, "")})). See `LICENSE` and `CITATION.cff`.', '',
              '## Integrity', '',
              f'Snapshot manifest SHA-256: `{review["snapshotSha256"]}`. Conversations were not published; '
              'only each journal’s identity line was kept.', '']
    if review.get('cleanedFiles'):
        lines += [f'Personal metadata (locations, names, dates, device details) was removed from '
                  f'{review["cleanedFiles"]} imported image file(s) before publishing. Their pixels are unchanged; '
                  'each file’s manifest names the original’s SHA-256, which differs from the published copy’s.', '']
    return '\n'.join(lines)


def licence_notice(licence, name) -> str:
    title = {'CC-BY-4.0': 'Creative Commons Attribution 4.0 International',
             'CC0-1.0': 'CC0 1.0 Universal'}.get(licence, licence)
    return (f'{" ".join(str(name).split())}\n\nThe data and findings in this project are released under the '
            f'{title} licence ({licence}).\nLegal code: {LINKS.get(licence, "")}legalcode\n\n'
            'Software tools named in the project keep their own licences.\n')


def citation(name, licence, author, today, origin) -> str:
    authors = f'  - name: {_yaml(author)}\n' if author else '  - name: "Hooke project author"\n'
    return ('cff-version: 1.2.0\n'
            'message: "If you use this project, please cite it as below."\n'
            'type: dataset\n'
            f'title: {_yaml(name)}\n'
            f'authors:\n{authors}'
            f'license: {licence}\n'
            f'date-released: {today}\n'
            f'identifiers:\n  - type: other\n    value: "hooke-project-origin:{origin}"\n')


def ro_crate(name, licence, author, today, documents) -> str:
    graph = [
        {'@id': 'ro-crate-metadata.json', '@type': 'CreativeWork',
         'conformsTo': {'@id': 'https://w3id.org/ro/crate/1.1'}, 'about': {'@id': './'}},
        {'@id': './', '@type': 'Dataset', 'name': ' '.join(str(name).split()), 'datePublished': today,
         'license': {'@id': LINKS.get(licence, licence)},
         'description': f'A Hooke research project with {len(documents)} accepted documents.',
         **({'author': {'@id': '#author'}} if author else {})},
    ]
    if author:
        graph.append({'@id': '#author', '@type': 'Person', 'name': ' '.join(str(author).split())})
    return json.dumps({'@context': 'https://w3id.org/ro/crate/1.1/context', '@graph': graph}, indent=2,
                      ensure_ascii=False) + '\n'
