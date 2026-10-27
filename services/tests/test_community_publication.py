"""Publisher/recipient seams against controlled GitHub and acquisition transports; no public actions."""
import pytest

from orchestrator import channels, made_with, publish, publish_api
from tests.test_publish_api import RecoverableGitHub, proven
from tests.test_publish import home


class CommunityGitHub(RecoverableGitHub):
    def call(self, method, path, payload=None):
        if path == '/repos/maya/ObservationUI-community':
            self.calls.append((method, path, payload))
            return 404, {}
        if path == '/user/repos' and method == 'POST':
            self.calls.append((method, path, payload))
            assert payload['name'] == 'ObservationUI-community' and payload['private'] is False
            return 201, {'full_name': 'maya/ObservationUI-community'}
        return super().call(method, path, payload)


def test_explicit_community_publication_returns_a_pinned_link_without_an_official_review(home, monkeypatch):
    m = proven(home, monkeypatch)
    api = CommunityGitHub(None)
    package = publish_api.prepare(m.key, http=api, destination='community')
    assert 'community' in package['review'].lower() and 'maya/ObservationUI-community' in package['review']
    assert not any(method == 'POST' for method, _, _ in api.calls)
    with pytest.raises(publish.CantPublish):
        publish_api.submit(package['package'], False, http=api, model=m.key)
    sent = publish_api.submit(package['package'], True, http=api, model=m.key, wait=lambda seconds: None)
    assert sent['url'] == 'https://github.com/maya/ObservationUI-community/tree/' + 'c' * 40
    assert not any(path.endswith('/forks') for _, path, _ in api.calls), 'works for the official repository owner too'
    assert sent['state'] == 'community'
    assert not any(method == 'POST' and path.endswith('/pulls') for method, path, _ in api.calls)
    before = len(api.calls)
    again = publish_api.submit(package['package'], True, http=api, model=m.key)
    assert again['url'] == sent['url']
    assert all(method == 'GET' for method, _, _ in api.calls[before:])


def test_shared_result_offers_immutable_missing_entry_acquisition(home, monkeypatch):
    m = proven(home, monkeypatch)
    api = CommunityGitHub(None)
    package = publish_api.prepare(m.key, http=api, destination='community')
    publish_api.submit(package['package'], True, http=api, model=m.key, wait=lambda seconds: None)
    stamp = made_with.capture(m)
    assert stamp['acquire']['commit'] == 'c' * 40
    monkeypatch.setattr(made_with.catalog, 'get', lambda key: None)
    monkeypatch.setattr(made_with.catalog, 'mine', lambda: {})
    checked = made_with._valid({'format': 1, 'capabilities': [stamp]})
    offer = made_with.offers(checked)[0]
    assert offer['state'] == 'acquire' and offer['acquire']['commit'] == 'c' * 40
    assert 'nothing runs' in offer['says']


def test_mutable_or_unsafe_acquisition_metadata_is_not_an_action():
    for url, commit in [('file:///private/catalog', 'a' * 40), ('https://github.com/lab/repo', 'main')]:
        got = made_with._valid({'format': 1, 'capabilities': [{'key': 'missing', 'name': 'Missing',
            'acquire': {'url': url, 'commit': commit, 'entry': 'missing'}}]})
        assert 'acquire' not in got[0]


def test_publisher_tree_link_is_consumed_at_its_exact_commit(home, monkeypatch):
    fetched = []
    monkeypatch.setattr(channels, 'fetch', lambda url, commit, *args: fetched.append((url, commit)) or 1)
    monkeypatch.setattr(channels, 'newest', lambda url: pytest.fail('an immutable link must not resolve newest'))
    got = channels.add('https://github.com/maya/ObservationUI-community/tree/' + 'a' * 40)
    assert got['commit'] == 'a' * 40
    assert fetched == [('https://github.com/maya/ObservationUI-community', 'a' * 40)]
    assert channels.wire()[0]['trusted'] is False
