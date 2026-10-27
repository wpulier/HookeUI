"""Repository reads through the builder tool with a synthetic HTTP boundary."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from research_worker import safe_fetch
from tests.test_research_content import researcher


@pytest.mark.parametrize('host,url,api,raw', [
    ('github', 'https://github.com/maya/model/tree/release-1',
     'https://api.github.com/repos/maya/model/commits/release-1',
     'https://raw.githubusercontent.com/maya/model/'),
    ('hf', 'https://huggingface.co/maya/model/tree/release-1',
     'https://huggingface.co/api/models/maya/model/revision/release-1',
     'https://huggingface.co/maya/model/raw/'),
])
def test_revision_reads_pin_once_and_reuse_the_same_commit(tmp_path, monkeypatch, host, url, api, raw):
    sha, asked = 'a' * 40, []

    async def get(client, target, **kwargs):
        asked.append(target)
        if target == api:
            body = json.dumps({'sha': sha, 'siblings': [{'rfilename': 'README.md'}]}).encode()
        elif target == f'https://api.github.com/repos/maya/model/git/trees/{sha}?recursive=1':
            body = json.dumps({'sha': 'b' * 40, 'tree': [{'type': 'blob', 'path': 'README.md'}]}).encode()
        elif target in (raw + sha + '/README.md', raw + sha + '/model.py'):
            body = b'controlled source'
        else:
            pytest.fail(f'unpinned or wrong revision request: {target}')
        return NS(encoding='utf-8'), body, target

    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(safe_fetch, '_get', get)
    r, _ = researcher(tmp_path, {'effort': 'build'})

    async def scenario():
        listing, _ = await r.tool_read_source({'url': url})
        file, _ = await r.tool_read_source({'url': url, 'path': 'README.md'})
        again, _ = await r.tool_read_source({'url': url, 'path': 'model.py'})
        assert listing['revision'] == file['revision'] == again['revision'] == sha
        assert 'README.md' in listing['text']

    asyncio.run(scenario())
    assert asked.count(api) == 1
    assert sha in (r.dir / 'source-pins.json').read_text()
