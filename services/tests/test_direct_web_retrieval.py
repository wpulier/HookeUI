"""Provider I/O is scripted; verify the configured web capability and source path."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import reference
from research_worker import retrieval, safe_fetch

PAGE_IMAGES = safe_fetch.page_images
LOOK_AT_PHOTO = retrieval.look_at_photo


def seen(matches, shows='a light micrograph of the requested subject', unmet=''):
    return retrieval.PhotoLook(matches, shows, unmet)


def search_result(text, candidates):
    # Interactions exposes search calls separately from final model output.
    queries = [q for c in candidates for q in c.grounding_metadata.web_search_queries]
    return SimpleNamespace(text=text, output_text=text, status='completed', steps=[
        SimpleNamespace(type='google_search_call', arguments={'queries': queries}),
    ] if queries else [])


@pytest.mark.parametrize('images', [False, True])
def test_lookup_uses_gemini38_with_google_search(monkeypatch, images):
    from google import genai
    calls = []

    async def generate(**args):
        calls.append(args)
        return search_result(text='{"matches":[{"title":"Source","page":"https://example.org/source"}]}',
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=['source']))])

    async def close():
        pass

    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    assert asyncio.run(retrieval.lookup('the requested fact or image', images=images))
    assert calls[0]['model'] == 'gemini-3.8-flash'
    assert calls[0]['tools'] == [{'type': 'google_search'}]
    assert calls[0]['store'] is False


def test_noncommons_image_keeps_original_source_without_catalogue_substitution(monkeypatch):
    page, photo = 'https://example.org/micrograph', 'https://images.example.org/cell.jpg'

    terms = 'human sperm optical microscopy high power objective -site:wikimedia.org -site:commons.wikimedia.org'

    async def lookup(query, **kwargs):
        assert terms in query
        assert "outside Wikimedia" in query
        return [{'title': 'Micrograph', 'page': page, 'image': photo}]

    async def fetch(url):
        assert url == photo
        return safe_fetch.FetchedImage(b'decoded-test-jpeg', 20, 20, photo, photo)

    async def catalogue(*args, **kwargs):
        pytest.fail('a web lookup must not silently fall back to a Commons-only search')

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(safe_fetch, 'commons_search', catalogue)
    found = asyncio.run(reference.retrieve('cell micrograph', search_terms=terms, request_context='Use a source outside Wikimedia'))
    assert found.page == page and found.title == 'Micrograph'
    assert found.jpeg == b'decoded-test-jpeg'

    async def empty(*args, **kwargs):
        return []

    monkeypatch.setattr(retrieval, 'lookup', empty)
    with pytest.raises(reference.NotFound, match='web search returned no displayable photo'):
        asyncio.run(reference.retrieve('no matching photo'))


def test_slow_image_host_does_not_delay_usable_match_and_is_cancelled(monkeypatch):
    cancelled = []

    async def lookup(*args, **kwargs):
        return [{'title': host, 'page': f'https://{host}.org/page',
                 'image': f'https://{host}.org/image.jpg'} for host in ('slow', 'ready')]

    async def fetch(url):
        if 'slow' in url:
            try:
                await asyncio.Future()
            finally:
                cancelled.append(url)
        return safe_fetch.FetchedImage(b'photo', 20, 20, url, url)

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(reference, 'WEB_DEADLINE_S', .5)
    found = asyncio.run(reference.retrieve('micrograph'))
    assert found.page == 'https://ready.org/page'
    assert cancelled == ['https://slow.org/image.jpg']


@pytest.fixture(autouse=True)
def scripted_photo_pixel_check(monkeypatch):
    from research_worker import retrieval
    async def look_at_photo(query, jpeg):
        return seen(True)
    async def no_page_images(url, **kwargs):
        return []
    monkeypatch.setattr(retrieval, 'look_at_photo', look_at_photo)
    monkeypatch.setattr(safe_fetch, 'page_images', no_page_images)


def test_downloaded_chart_is_not_displayed_as_a_photograph(monkeypatch):
    async def lookup(*args, **kwargs):
        return [{'title': 'Claimed micrograph', 'page': 'https://example.org/paper',
                 'image': 'https://example.org/chart.jpg'}]
    async def fetch(url):
        return safe_fetch.FetchedImage(b'chart-pixels', 20, 20, url, url)
    async def check(query, jpeg):
        assert jpeg == b'chart-pixels'
        return seen(False, 'a bar chart of counts', 'not a photograph')
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(retrieval, 'look_at_photo', check)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    with pytest.raises(reference.NotFound, match='none of the 1 photos found matched what was asked; they showed: a bar chart'):
        asyncio.run(reference.retrieve('sperm photograph'))


def test_source_page_photo_is_used_when_search_has_no_direct_image(monkeypatch):
    page = 'https://university.example/microscopy/stoma'
    photo = 'https://university.example/photos/stoma.jpg'
    async def lookup(*args, **kwargs):
        return [{'title': 'Stoma micrograph', 'page': page}]
    async def candidates(url):
        assert url == page
        return [photo]
    async def fetch(url):
        assert url == photo
        return safe_fetch.FetchedImage(b'actual-pixels', 300, 200, photo, photo)
    async def check(query, jpeg):
        assert jpeg == b'actual-pixels'
        return seen(True, 'a surface view of one stoma between two guard cells, light microscopy')
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'page_images', candidates, raising=False)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(retrieval, 'look_at_photo', check)
    found = asyncio.run(reference.retrieve('stoma photograph'))
    assert found.page == page and found.jpeg == b'actual-pixels'
    # Another picture on the page than the one the search named: titled and described by the look, not the request.
    assert found.title == 'a surface view of one stoma between two guard cells, light microscopy'
    assert found.shows == found.title and found.looked_by == retrieval.LOOKUP_MODEL


def test_page_image_discovery_uses_actual_html_and_resolves_relative_urls(monkeypatch):
    import httpx
    body = b'''<meta property="og:image" content="/photos/stoma.jpg">
        <img src="/logo.png"><img src="/photos/stoma.jpg">
        <img data-src="https://cdn.example/second.jpg">
        <script>const fake = '<img src="https://invented.example/fake.jpg">';</script>'''
    def respond(request):
        return httpx.Response(200, content=body, headers={'content-type': 'text/html'})
    # Restore the real reader; other tests script all network discovery.
    monkeypatch.setattr(safe_fetch, 'page_images', PAGE_IMAGES)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await safe_fetch.page_images('https://university.example/article', client=client)
    assert asyncio.run(go()) == ['https://university.example/photos/stoma.jpg',
                                'https://university.example/logo.png', 'https://cdn.example/second.jpg']


def test_blocked_direct_image_recovers_from_page_and_rejects_its_logo(monkeypatch):
    page = 'https://collection.example/stoma'
    attempted, checked = [], []
    async def lookup(*args, **kwargs):
        return [{'title': 'Stoma', 'page': page, 'image': page + '/blocked.jpg'}]
    async def candidates(url):
        return [page + '/logo.jpg', page + '/photo.jpg']
    async def fetch(url):
        attempted.append(url)
        if url.endswith('blocked.jpg'):
            raise safe_fetch.FetchRefused('HTTP 403')
        return safe_fetch.FetchedImage(url.encode(), 300, 200, url, url)
    async def check(query, jpeg):
        checked.append(jpeg)
        return seen(jpeg.endswith(b'/photo.jpg'))
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(retrieval, 'look_at_photo', check)
    monkeypatch.setattr(safe_fetch, 'page_images', candidates)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    found = asyncio.run(reference.retrieve('stoma micrograph'))
    assert found.page == page and found.jpeg.endswith(b'/photo.jpg')
    assert len(attempted) == 3 and len(checked) == 2


def test_another_photo_uses_original_sources_and_does_not_discard_the_only_new_match(monkeypatch):
    import hashlib
    previous = [{'page': 'https://old.example/stoma', 'title': 'Previous stoma',
                 'digest': hashlib.sha256(b'old-photo').hexdigest()}]
    async def lookup(query, **kwargs):
        assert 'https://old.example/stoma' in query
        assert 'somewhere else' in query
        return [{'title': 'Repeated', 'page': 'https://mirror.example/stoma', 'image': 'https://mirror.example/old.jpg'},
                {'title': 'New stoma', 'page': 'https://new.example/stoma', 'image': 'https://new.example/new.jpg'}]
    async def fetch(url):
        return safe_fetch.FetchedImage(b'old-photo' if 'old.jpg' in url else b'new-photo', 300, 200, url, url)
    async def check(query, jpeg):
        assert jpeg == b'new-photo', 'known duplicate pixels need no paid relevance check'
        assert 'Candidate source site: new.example' in query
        return seen(True)
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(retrieval, 'look_at_photo', check)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    found = asyncio.run(reference.retrieve('stoma photograph', n=2,
        request_context='a different one from somewhere else online', previous_sources=previous))
    assert found.jpeg == b'new-photo' and found.page == 'https://new.example/stoma'


def test_blocked_sources_trigger_one_grounded_recovery_preserving_request(monkeypatch):
    searches, downloads = [], []
    async def lookup(query, **kwargs):
        searches.append(query)
        host = 'blocked' if len(searches) == 1 else 'new'
        return [{'title': 'Stoma', 'page': f'https://{host}.example/stoma',
                 'image': f'https://{host}.example/photo.jpg'}]
    async def fetch(url):
        downloads.append(url)
        if 'blocked' in url:
            raise safe_fetch.FetchRefused('HTTP 403')
        return safe_fetch.FetchedImage(b'new-pixels', 300, 200, url, url)
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    found = asyncio.run(reference.retrieve('stoma', n=2,
        request_context='another website, outside Commons',
        previous_sources=[{'page': 'https://old.example/stoma'}]))
    assert found.jpeg == b'new-pixels'
    assert len(searches) == 2 and len(downloads) == 2
    assert all('another website, outside Commons' in q and 'https://old.example/stoma' in q for q in searches)
    assert 'HTTP 403' in searches[1] and 'https://blocked.example/stoma' in searches[1]


def test_recovery_is_bounded_to_two_searches_six_unique_pages(monkeypatch):
    searches, pages = [], []
    async def lookup(query, **kwargs):
        searches.append(query)
        return [{'title': 'Stoma', 'page': f'https://source.example/{len(searches)}/{i}'} for i in range(10)]
    async def candidates(url):
        pages.append(url)
        raise safe_fetch.FetchRefused('HTTP 404')
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'page_images', candidates)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    with pytest.raises(reference.NotFound):
        asyncio.run(reference.retrieve('stoma'))
    assert len(searches) == 2
    assert len(pages) == len(set(pages)) == 6


def test_recovery_never_restarts_overall_deadline(monkeypatch):
    searches, cancelled = [], []
    async def lookup(query, **kwargs):
        searches.append(query)
        return [{'title': 'Stoma', 'page': f'https://source.example/{len(searches)}'}]
    async def candidates(url):
        if url.endswith('/1'):
            return []
        try:
            await asyncio.Future()
        finally:
            cancelled.append(url)
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'page_images', candidates)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(reference, 'WEB_DEADLINE_S', .1)
    with pytest.raises(reference.NotFound, match='TimeoutError'):
        asyncio.run(reference.retrieve('stoma'))
    assert len(searches) == 2 and cancelled == ['https://source.example/2']


def test_provider_preserves_long_request_recovery_and_candidate_identity(monkeypatch):
    from google import genai
    calls = []
    async def generate(**args):
        calls.append(args)
        return search_result(text='{"shows":"a stoma","unmet":"","matches":true}' if isinstance(args.get('contents'), list)
                               else '{"matches":[]}',
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=['source']))])
    async def close():
        pass
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    query = ('Original request: ' + 'context ' * 900
             + '\nOnly another website, outside Commons.\nRecovery: prior source returned HTTP 403'
             + '\nCandidate source page: https://new.example/stoma')
    asyncio.run(retrieval.lookup(query, images=True))
    assert asyncio.run(LOOK_AT_PHOTO(query, b'test-pixels')).matches
    assert calls[0]['input'] == query
    assert calls[1]['contents'][0].text == query
    with pytest.raises(ValueError, match='no constraints were dropped'):
        asyncio.run(retrieval.lookup('x' * (retrieval.MAX_QUERY_CHARS + 1)))
    assert len(calls) == 2

@pytest.mark.parametrize('raw, expected', [
    ('{"matches":[]}', {'returnedMatchCount': 0, 'acceptedMatchCount': 0, 'rejectedMatchCount': 0, 'responseStatus': 'empty'}),
    ('{"matches":[{"title":"Photo","unrecognized":"https://example.org/photo"}]}',
     {'returnedMatchCount': 1, 'acceptedMatchCount': 0, 'rejectedMatchCount': 1, 'responseStatus': 'invalid-matches'}),
    ('{"matches":[{"title":"Photo","page":"https://example.org/photo"}]}',
     {'returnedMatchCount': 1, 'acceptedMatchCount': 1, 'rejectedMatchCount': 0, 'responseStatus': 'matches-list'}),
    ('not JSON', {'responseStatus': 'invalid-json'}),
])
def test_lookup_observes_empty_vs_rejected_provider_output(monkeypatch, raw, expected):
    from google import genai
    observations = []
    async def generate(**args):
        return search_result(text=raw, candidates=[SimpleNamespace(
            finish_reason='STOP', grounding_metadata=SimpleNamespace(
                web_search_queries=['stoma microscope'], grounding_chunks=[]))])
    async def close():
        pass
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    try:
        asyncio.run(retrieval.lookup('a different photo', images=True,
                                    observe=lambda **facts: observations.append(facts)))
    except ValueError:
        assert expected['responseStatus'] in ('invalid-json', 'invalid-matches')
    facts = {k: v for row in observations for k, v in row.items()}
    assert facts['webSearchQueryCount'] == 1
    assert facts['groundingCount'] == 0
    for key, value in expected.items():
        assert facts[key] == value
    # No provider text or hidden reasoning is persisted by this observation.
    assert 'text' not in facts and 'raw' not in facts

@pytest.mark.parametrize('grounded', [False, True])
def test_observed_url_field_is_normalized_only_with_search_evidence(monkeypatch, grounded):
    from google import genai
    calls = []
    async def generate(**args):
        calls.append(args)
        return search_result(text='{"matches":[{"title":"Stoma","url":"https://example.org/stoma"}]}',
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(
                web_search_queries=['stoma microscope'] if grounded else [], grounding_chunks=[]))])
    async def close():
        pass
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    if grounded:
        assert asyncio.run(retrieval.lookup('a different photo', images=True)) == [
            {'title': 'Stoma', 'page': 'https://example.org/stoma'}]
    else:
        with pytest.raises(ValueError, match='did not return evidence of a web search'):
            asyncio.run(retrieval.lookup('a different photo', images=True))
    assert calls[0]['response_format']['mime_type'] == 'application/json'
    assert calls[0]['response_format']['schema']['properties']['matches']['items']['required'] == ['title', 'page']


def test_unverified_search_is_recovered_once_with_its_reason_not_fetched(monkeypatch):
    queries = []
    async def lookup(query, **kwargs):
        queries.append(query)
        raise retrieval.LookupUnverified('The provider did not return evidence of a web search')
    async def fetch(*args):
        pytest.fail('unverified candidates must not be fetched as searched sources')
    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    with pytest.raises(reference.NotFound, match='did not return evidence of a web search'):
        asyncio.run(reference.retrieve('stoma', request_context='different website, outside Commons'))
    assert len(queries) == 2
    assert 'did not return evidence of a web search' in queries[1]
    assert all('different website, outside Commons' in q for q in queries)

@pytest.mark.parametrize('raw', ['not JSON', '{"unexpected":[]}', '{"matches":[{"image":"https://example.org/photo.jpg"}]}'])
def test_malformed_response_uses_one_recovery_with_rejection_reason(monkeypatch, raw):
    from google import genai
    queries = []
    async def generate(**args):
        queries.append(args['input'])
        return search_result(text=raw if len(queries) == 1 else '{"matches":[{"title":"Stoma","page":"https://example.org/stoma","image":"https://example.org/photo.jpg"}]}',
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(web_search_queries=['stoma']))])
    async def close():
        pass
    async def fetch(url):
        return safe_fetch.FetchedImage(b'photo', 20, 20, url, url)
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    found = asyncio.run(reference.retrieve('stoma', request_context='another website outside Commons'))
    assert found.page == 'https://example.org/stoma'
    assert len(queries) == 2
    assert 'The search provider returned' in queries[1]
    assert all('another website outside Commons' in q for q in queries)


def test_lookup_diagnostics_survive_production_execution_filter_and_archive(monkeypatch, tmp_path):
    from google import genai
    from orchestrator import execution
    from orchestrator.execution_archive import ExecutionArchive
    async def generate(**args):
        return search_result(text='{"matches":[{"title":"Stoma","url":"https://example.org/stoma"}]}',
            candidates=[SimpleNamespace(finish_reason='STOP', grounding_metadata=SimpleNamespace(
                web_search_queries=['stoma'], grounding_chunks=[]))])
    async def close():
        pass
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), interactions=SimpleNamespace(create=generate), aclose=close)))
    archive = ExecutionArchive(tmp_path)
    log = execution.ExecutionLog(enabled=True, archive=archive)
    with log.request('ask') as request:
        with execution.span('reference-search'):
            asyncio.run(retrieval.lookup('stoma', images=True, observe=execution.note))
    archive.flush()
    [step] = archive.inspect(request.id)['steps']
    archive.close()
    assert step['webSearchQueryCount'] == 1
    assert step['returnedMatchCount'] == step['acceptedMatchCount'] == 1
    assert step['rejectedMatchCount'] == 0
    assert step['matchFields'] == 'title,url'
    assert step['finishReason'] == 'completed'


@pytest.mark.parametrize('raw, expected', [
    ('{"shows":"a surface view of one stoma","unmet":"","matches":true}', True),
    ('{"shows":"a leaf cross-section","unmet":"not a surface view","matches":false}', False),
    ('{"matches":true}', None),  # a decision with no account of the pixels says nothing true about the picture
    ('{"shows":"x","unmet":"","matches":', None), ('{"shows":"x","unmet":"","matches":"true"}', None), ('{}', None)])
def test_the_look_requires_what_the_pixels_show_and_a_complete_decision(monkeypatch, raw, expected):
    from google import genai
    calls = []
    async def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=raw)
    async def close():
        pass
    monkeypatch.setattr(genai, 'Client', lambda **_: SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(generate_content=generate), aclose=close)))
    if expected is None:
        with pytest.raises(retrieval.LookupFailed, match='Photo validation'):
            asyncio.run(LOOK_AT_PHOTO('another stoma photo', b'pixels'))
    else:
        look = asyncio.run(LOOK_AT_PHOTO('another stoma photo', b'pixels'))
        assert look.matches is expected and look.shows
    config = calls[0]['config']
    schema = config.response_schema
    assert schema['properties']['matches']['type'] == 'boolean'
    assert schema['property_ordering'][0] == 'shows'  # the pixels are described before they are judged
    assert 'kind of view' in config.system_instruction and 'how many' in config.system_instruction
    assert config.max_output_tokens >= 1024  # observed 100-token responses were cut off before the decision


def test_a_hung_first_search_is_hedged_and_the_photo_still_comes(monkeypatch):
    """Live 2026-10-11 12:21 (by voice): the first web search hung, one 20 s deadline covered every search and
    download, so the second never ran and nothing was shown. Searches took 6.6 s (p50), 9.8 s (p90), 13.4 s at most
    over 42 live lookups. Now each search has its own cap, and a second identical search starts beside one not back
    by HEDGE_AFTER_S: the first usable photo wins, and the hung search is cancelled."""
    searches, cancelled = [], []

    async def lookup(query, **kwargs):
        searches.append(query)
        if len(searches) == 1:
            try:
                await asyncio.Future()  # the provider never answers
            finally:
                cancelled.append('first search')
        return [{'title': 'Stoma', 'page': 'https://ready.org/page', 'image': 'https://ready.org/image.jpg'}]

    async def fetch(url):
        return safe_fetch.FetchedImage(b'photo', 20, 20, url, url)

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(reference, 'HEDGE_AFTER_S', .05)
    monkeypatch.setattr(reference, 'SEARCH_CAP_S', 5)
    found = asyncio.run(reference.retrieve('plant stoma under microscope'))
    assert found.page == 'https://ready.org/page'
    assert len(searches) == 2 and searches[0] == searches[1]  # the same search, sent again beside it
    assert cancelled == ['first search']


def test_a_search_past_its_own_cap_is_given_up_and_the_recovery_runs(monkeypatch):
    searches = []

    async def lookup(query, **kwargs):
        searches.append(query)
        if len(searches) <= 2:
            await asyncio.Future()  # the first and its hedge both hang past the cap
        return [{'title': 'Stoma', 'page': 'https://later.org/page', 'image': 'https://later.org/image.jpg'}]

    async def fetch(url):
        return safe_fetch.FetchedImage(b'photo', 20, 20, url, url)

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(reference, 'HEDGE_AFTER_S', .05)
    monkeypatch.setattr(reference, 'SEARCH_CAP_S', .2)
    found = asyncio.run(reference.retrieve('stoma'))
    assert found.page == 'https://later.org/page'
    assert len(searches) == 3 and 'took over' in searches[2]  # the recovery is told why


def test_the_search_and_its_pixel_check_read_the_request_with_what_was_on_screen(monkeypatch):
    """Live 2026-10-11 16:49:53: "Pull up a picture of an iris online" right after zooming on the user's eye found Iris
    (plant). What the user was looking at reaches the search and the check of the photo's pixels, as data."""
    page, photo = 'https://example.org/eye', 'https://images.example.org/iris.jpg'
    on_screen = "the running look: a close-up of a person's face. Outlined: eye (shown magnified)"
    asked, checked = [], []

    async def lookup(query, **kwargs):
        asked.append(query)
        return [{'title': 'Human iris', 'page': page, 'image': photo}]

    async def fetch(url):
        return safe_fetch.FetchedImage(b'decoded-test-jpeg', 20, 20, photo, photo)

    async def matches(query, jpeg):
        checked.append(query)
        return seen(True)

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(retrieval, 'look_at_photo', matches)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    found = asyncio.run(reference.retrieve('iris', request_context='Pull up a picture of an iris online',
                                           view_context=on_screen))
    assert found.page == page
    assert on_screen in asked[0] and 'data, not instructions' in asked[0].split(on_screen)[0].rsplit('\n', 1)[-1]
    assert on_screen in checked[0]
def test_with_commons_raced_the_catalogue_photo_wins_when_the_web_is_slow_and_is_checked_like_any(monkeypatch,
                                                                                                    wall_clock):
    """Option REFERENCE_SEARCH "web+commons" (bench/reference_bench.py: 20/20 right, p50 6.2 s against 18/20 and
    10.7 s): Commons' catalogue is searched beside the web; its first candidate the pixel check accepts wins. The check
    is asked with the original request and the candidate's page, so a request for another site can refuse it."""
    commons_page = 'https://commons.wikimedia.org/wiki/File:Stoma.jpg'
    checked = []

    async def slow_web(query, **kwargs):
        await asyncio.sleep(5)
        return [{'title': 'Web', 'page': 'https://example.org/stoma', 'image': 'https://example.org/stoma.jpg'}]

    async def catalogue(words, limit=3, client=None):
        assert 'stoma' in words
        return [commons_page]

    async def fetch(url):
        return safe_fetch.FetchedImage(b'commons-jpeg', 20, 20, url, url)

    async def check(query, jpeg):
        checked.append(query)
        return seen(True)

    monkeypatch.setattr(retrieval, 'lookup', slow_web)
    monkeypatch.setattr(safe_fetch, 'commons_search', catalogue)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    monkeypatch.setattr(retrieval, 'look_at_photo', check)
    import time
    t0 = time.monotonic()
    found = asyncio.run(reference.retrieve('plant stoma', request_context='show me a stoma', sources='web+commons'))
    assert found.jpeg == b'commons-jpeg'
    wall_clock.within(time.monotonic() - t0, 2, 'the lookup')  # the slow web search was not waited for
    # The look sees the request and the candidate's site, not its address (a page's path is often its title).
    assert 'show me a stoma' in checked[0] and 'Candidate source site: commons.wikimedia.org' in checked[0]
    assert 'File:Stoma' not in checked[0]

    async def no_catalogue(*args, **kwargs):
        pytest.fail('the web search alone never searches the catalogue')

    monkeypatch.setattr(safe_fetch, 'commons_search', no_catalogue)

    async def quick_web(query, **kwargs):
        return [{'title': 'Web', 'page': 'https://example.org/stoma', 'image': 'https://example.org/stoma.jpg'}]

    monkeypatch.setattr(retrieval, 'lookup', quick_web)
    assert asyncio.run(reference.retrieve('plant stoma', sources='web')).page == 'https://example.org/stoma'


FOUNDER = ('Can you pull up an image side-by-side, a different one than you have been, of a stoma, just a single stoma, '
           'and put it next to the magnification?')
CROSS_SECTION = seen(False, 'a stained transverse section of a leaf: epidermis and mesophyll cells in layers, two guard '
                            'cells cut across beside a closed pore; light microscopy', 'a cross-section, not a surface view')
SURFACE = seen(True, 'a surface view of one stoma: two kidney-shaped guard cells around an open pore, among '
                     'epidermal cells; brightfield light microscopy')


def scripted_candidates(monkeypatch, pages, looks):
    """The web search names `pages` (each with its image), each image's pixels are its page's, and the look at the
    pixels answers from `looks` by those pixels. Returns the looks' queries in the order asked."""
    asked = []

    async def lookup(query, **kwargs):
        return [{'title': title, 'page': page, 'image': page + '.jpg'} for page, title in pages]

    async def fetch(url):
        return safe_fetch.FetchedImage(url.encode(), 640, 480, url, url)

    async def look(query, jpeg):
        asked.append((jpeg.decode(), query))
        return looks[jpeg.decode().removesuffix('.jpg')]

    monkeypatch.setattr(retrieval, 'lookup', lookup)
    monkeypatch.setattr(retrieval, 'look_at_photo', look)
    monkeypatch.setattr(safe_fetch, 'fetch_image', fetch)
    monkeypatch.setattr(safe_fetch, 'check_url', lambda url: None)
    return asked


def test_a_cross_section_is_turned_down_for_a_single_stoma_in_surface_view_and_the_next_one_is_used(monkeypatch):
    """Live 2026-10-29 17:16: asked for "just a single stoma" to set beside the magnified leaf surface, the web search
    named a Commons file of a leaf cross-section; the bare yes/no pixel check passed it, and the voice said it was "a
    single stoma". The look now says what the pixels show and judges them against the whole request read with the
    view: the cross-section is turned down and the surface view after it is the one returned."""
    section = 'https://commons.wikimedia.org/wiki/File:Guard_cells_surrounding_closed_stoma_in_epidermis'
    surface = 'https://microscope.example/stoma-surface'
    asked = scripted_candidates(monkeypatch, [(section, 'File:Guard cells surrounding closed stoma'),
                                              (surface, 'Stoma surface')],
                                {section: CROSS_SECTION, surface: SURFACE})
    on_screen = 'the running look: a close-up view of green plant epidermis showing stomatal complexes'
    found = asyncio.run(reference.retrieve('single stoma microscope', request_context=FOUNDER, view_context=on_screen))
    assert found.page == surface and found.jpeg == (surface + '.jpg').encode()
    assert found.shows == SURFACE.shows and found.looked_by == retrieval.LOOKUP_MODEL
    looked = dict(asked)
    # Both looked at (the Commons file as its page, for its licence and author); the turned-down one never returned.
    assert set(looked) == {section, surface + '.jpg'}
    for query in looked.values():  # each look is given the whole request and the view, as data
        assert FOUNDER in query and on_screen in query
        assert 'Guard_cells' not in query.split('Candidate source site:')[1]  # the site, never the page's file name


def test_none_matching_is_said_plainly_with_what_the_photos_showed(monkeypatch):
    section = 'https://commons.wikimedia.org/wiki/File:Leaf_section'
    asked = scripted_candidates(monkeypatch, [(section, 'Leaf section')], {section: CROSS_SECTION})
    with pytest.raises(reference.NotFound) as caught:
        asyncio.run(reference.retrieve('single stoma, surface view', request_context=FOUNDER))
    said = str(caught.value)
    assert said.startswith('none of the') and 'matched what was asked' in said
    assert 'transverse section' in said and 'not a surface view' in said
    assert len(asked) == 1  # the recovery search found the same page again: it was not looked at twice


def test_another_one_never_returns_a_picture_already_shown_in_this_project(monkeypatch):
    """"A different one than you have been": a picture whose pixels are those of a reference already kept in this
    Project is never returned for another one (n > 1), and costs no look; the look is told what the earlier ones
    showed, as data."""
    import hashlib
    old = 'https://microscopyofnature.com/stomata'
    new = 'https://plants.example/one-stoma'
    earlier = [{'page': old, 'title': 'Stomata | Microscopy of Nature',
                'digest': hashlib.sha256((old + '.jpg').encode()).hexdigest(),
                'shows': 'a surface view of three stomata in a row'}]
    asked = scripted_candidates(monkeypatch, [(old, 'Stomata'), (new, 'One stoma')], {old: SURFACE, new: SURFACE})
    found = asyncio.run(reference.retrieve('single stoma', n=2, request_context=FOUNDER, previous_sources=earlier))
    assert found.page == new
    assert [url for url, _ in asked] == [new + '.jpg']  # the earlier picture was excluded before any paid look
    assert 'a surface view of three stomata in a row' in asked[0][1] and 'already shown in this Project' in asked[0][1]


def test_the_same_picture_asked_for_afresh_is_a_fact_the_look_judges(monkeypatch):
    """Not asked for another one (n = 1), an earlier picture is not excluded by code: the look is told it is the same
    picture as one already shown and judges that with the request."""
    import hashlib
    old = 'https://microscopyofnature.com/stomata'
    earlier = [{'page': old, 'digest': hashlib.sha256((old + '.jpg').encode()).hexdigest()}]
    asked = scripted_candidates(monkeypatch, [(old, 'Stomata')], {old: SURFACE})
    found = asyncio.run(reference.retrieve('stoma', request_context='show me a stoma', previous_sources=earlier))
    assert found.page == old
    assert 'Fact from the app: this candidate is the same picture as a reference photo already shown' in asked[0][1]
