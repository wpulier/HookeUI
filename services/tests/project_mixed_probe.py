"""Synthetic mixed material through real storage services, never a model-quality claim."""
from dataclasses import asdict
import hashlib
import json

from PIL import Image

from orchestrator.library import Asset
from orchestrator.project_media import ProjectMedia, MediaReference
from orchestrator.project_media_decoder import retained_path
from orchestrator.project_evidence import resolve
from orchestrator.workspace_views import read, save, save_note
from tests.test_agent_projects import run
from tests.test_evidence import population, view, saved
from tests.test_workspace_views import layout


def create(built, client, headers, base, sid):
    # Import numerical material by the same general connector as the protocol.
    matrix = base / 'counts.csv'
    matrix.write_text('cell,gene_a,gene_b\n1,0,8\n2,3,4\n')
    imported = run(client, headers, 'project.import', {'file': str(matrix)}, 'mixed-counts', sid)
    ref = imported['outcome']['artifact']
    matrix.unlink()

    source = base / 'microscopy.png'
    Image.new('RGB', (13, 17), (4, 83, 212)).save(source)
    asset = Asset('a1', 'footage:mixed-fixture', 'still', source, 'added', 'Synthetic specimen',
                  optics={'umPerPx': 0.4})
    media = ProjectMedia(built.hub.resources.projects)
    retained = media.retain(sid, asset, request='mixed-retention-0001', max_bytes=4096,
                            producer={'actor': 'user'})
    source.unlink()

    evidence = built.hub.evidence
    item = population(evidence, view(), thread=built.day.active_id)
    item.session, item.day = sid, built.day.date
    evidence.add(item)
    evidence.schedule_save(item)
    assert saved(item).status == 'saved', item.status_why
    original = dict(built.day.evidence[item.id])
    with evidence.keeping_pixels(item):
        evidence.reload_masks(item)
        evidence.revise(item, 'mark', 'human', [next(iter(item.members))])
        assert saved(item).status == 'saved', item.status_why
    revised = dict(built.day.evidence[item.id])

    note = {'id': 'a' + 'f' * 32, 'title': 'Limits', 'text': 'Synthetic material; no biological accuracy claim.', 'base': 0}
    save_note(built.day, note)
    save_note(built.day, {**note, 'base': 1, 'text': 'Retain both accepted image revisions.'})
    arrangement = layout()
    arrangement['views'][0]['notes'] = [{'id': note['id'], 'rect': [10, 10, 320, 300]}]
    save(built.day, arrangement, 0)
    built.day.add_line({'role': 'user', 'text': 'Compare the matrix and microscopy evidence.', 'lineId': 'mixed-question'})
    built.day.add_line({'role': 'assistant', 'text': 'These are separate synthetic modalities.', 'lineId': 'mixed-answer'})
    return {'matrix': ref, 'media': asdict(retained), 'population': item.id,
            'populationRevisions': [original, revised], 'note': note['id']}


def inspect(built, receipt, base):
    sid = receipt['session']
    mixed = receipt['mixed']
    stores = built.hub.resources.projects
    ref = mixed['matrix']
    matrix = stores.read(ref['id'], sid, revision=ref['revision'])
    source = stores.file_source(matrix.id, sid, matrix.revision, matrix.document['state']['sourceFile']['reference'])
    assert source.read(0, source.size) == b'cell,gene_a,gene_b\n1,0,8\n2,3,4\n'
    media = ProjectMedia(stores)
    ref = MediaReference(**mixed['media'])
    assert media.candidates(sid, 'a1') == [ref]
    with retained_path(media, ref, host_cache=base / 'decoder-cache', max_bytes=4096) as retained:
        with Image.open(retained.path) as image:
            assert image.tobytes() == bytes((4, 83, 212)) * (13 * 17)
    evidence = built.hub.evidence
    population = evidence.get(mixed['population'])
    assert population is not None and population.revision == 1
    assert evidence.reload_masks(population)
    manifests = []
    for record in mixed['populationRevisions']:
        body = (resolve(evidence, sid, record) / 'manifest.json').read_bytes()
        assert hashlib.sha256(body).hexdigest() == record['manifestSha256']
        manifests.append(json.loads(body))
    assert [value['revision'] for value in manifests] == [0, 1]
    lines = [row['line'] for row in built.day.journal.load() if row.get('type') == 'line']
    assert {row.get('lineId') for row in lines} >= {'mixed-question', 'mixed-answer'}
    return {'matrixDigest': matrix.digest, 'media': mixed['media'], 'populationManifests': manifests,
            'layout': read(built.day), 'notes': built.day.annotations[mixed['note']], 'lines': [line for line in lines if line.get('type') != 'app-notice']}
