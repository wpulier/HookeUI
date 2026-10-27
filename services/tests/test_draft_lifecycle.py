"""Builder provenance and lifecycle through saved state; synthetic input, no adapter execution."""
import asyncio
import hashlib
import json

import numpy as np

from orchestrator import drafts, env_runner
from orchestrator.research.manager import ResearchManager
from tests.test_link_recognise import mine_home
from tests.test_research import FakeHub
from tests.test_research_answer_kept import finished_with_a_question


def test_fallback_draft_retains_its_actual_input_origin(mine_home, monkeypatch):
    origin = {'evidenceClass': 'generated-fixture', 'identity': 'synthetic-discs'}
    monkeypatch.setattr(env_runner, 'representative', lambda environ: (np.zeros((8, 8, 3), np.uint8), origin))
    did = drafts.create('https://github.com/maya/tool')
    row = next(d for d in drafts.wire() if d['id'] == did)
    assert row['input']['evidenceClass'] == origin['evidenceClass']
    assert row['input']['identity'] == origin['identity']
    assert row['input']['sha256'] == hashlib.sha256((drafts.folder(did) / 'picture.jpg').read_bytes()).hexdigest()


def test_builder_terminal_receipt_and_restart_end_active_looking_drafts(mine_home):
    async def scenario():
        manager = ResearchManager(FakeHub(), root=mine_home / 'research')
        did = drafts.create('https://github.com/maya/tool', b'synthetic-user-input')
        session = finished_with_a_question(manager, status='running', deliverable='adapter')
        drafts.assign(did, session.rid)
        manager.drafts = {session.rid: did}
        manager._finish(session, 'cancelled', None)
        assert drafts.wire()[0]['state'] == 'cancelled'
        other = drafts.create('https://github.com/maya/other', b'synthetic-user-input')
        drafts.assign(other, 'lost-owner')
        ResearchManager(FakeHub(), root=mine_home / 'research')
        row = next(d for d in drafts.wire() if d['id'] == other)
        assert row['state'] == 'interrupted'
        assert row['terminal']['status'] == 'interrupted'

    asyncio.run(scenario())


def test_community_draft_is_local_and_excludes_private_input_and_raw_diagnostics(mine_home):
    did = drafts.create('https://github.com/maya/tool', b'PRIVATE-PICTURE')
    state_path = drafts.folder(did) / 'draft.json'
    state = json.loads(state_path.read_text())
    state['tries'] = [{'ok': False, 'error': 'SECRET /Users/researcher/private/image.tif'}]
    state_path.write_text(json.dumps(state))
    drafts.finish(did, 'failed')
    report = drafts.community(did)
    assert report['localOnly'] is True
    assert 'https://github.com/maya/tool' in report['text'] and '1 attempt' in report['text']
    assert not any(s in report['text'] for s in ('PRIVATE-PICTURE', 'SECRET', '/Users/', 'image.tif'))
    assert (drafts.folder(did) / 'community-draft.md').read_text() == report['text']
