"""Real isolated result bytes/journals; synthetic measured values, no model claims."""
import threading

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot
from orchestrator.resources import Results
from tests.waiting import until, HANG_S


def setup(tmp_path):
    legacy = tmp_path / 'host/sessions'
    SessionStore(legacy, session='2026-10-29')
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects')
    day = SessionStore(legacy, session='2026-10-29', locations=locations)
    results = Results(tmp_path / 'host/results', day=day)
    return day, locations, results


def test_background_result_keeps_original_project_across_stage_switch_and_relocation(tmp_path, monkeypatch):
    day, locations, results = setup(tmp_path)
    entered, release = threading.Event(), threading.Event()
    write = results._write
    def delayed(date, name, data, **kwargs):
        if name.endswith('.jpg'):
            entered.set()
            assert release.wait(HANG_S)
        return write(date, name, data, **kwargs)
    monkeypatch.setattr(results, '_write', delayed)
    try:
        first = day.new('Microscopy')
        root = locations.project(first)
        entry = results.add('measure', {}, 'Synthetic measurements', values={'area': 12},
                            picture_bytes=[b'original microscopy image bytes'], session=first)
        until(entered.is_set)
        second = day.new('Another Project')
        release.set()
        results.wait_for_picture(entry['day'], entry['pictures'][0], session=first)
        assert results.values(entry['id'], first) == {'area': 12}
        assert results.picture(entry['id'], day=first) == b'original microscopy image bytes'
        assert not results.entries(second)
        assert not results.root.exists()
        original = results.root_for(first)
        assert original == root.path / 'artifacts/results'
        assert (original / entry['day'] / (entry['id'] + '.json')).is_file()
        locations.close()
        moved = tmp_path / 'Moved microscope research'
        root.path.rename(moved)
        fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
            resumed = SessionStore(fresh.legacy, session=first, locations=fresh)
            reader = Results(results.root, day=resumed)
            assert reader.values(entry['id'], first) == {'area': 12}
            assert reader.picture(entry['id'], day=first) == b'original microscopy image bytes'
            assert reader.metadata(first)[1] == []
        finally:
            fresh.close()
    finally:
        release.set(); locations.close()


def test_legacy_results_stay_in_legacy_folder(tmp_path):
    day, locations, results = setup(tmp_path)
    try:
        entry = results.add('count', {}, 'Synthetic count', values={'count': 3})
        assert (results.root / entry['day'] / (entry['id'] + '.json')).is_file()
        assert results.values(entry['id']) == {'count': 3}
    finally:
        locations.close()


def test_replaced_or_missing_result_folder_refuses_without_recreating(tmp_path):
    day, locations, results = setup(tmp_path)
    try:
        sid = day.new('Retained results')
        entry = results.add('count', {}, 'Synthetic count', values={'count': 3})
        folder = results.root_for(sid)
        folder.rename(folder.with_name('preserved-results'))
        with pytest.raises(ProjectLocationError, match='missing'):
            results.add('count', {}, 'Late result', values={'count': 4})
        assert not folder.exists()
        folder.mkdir()
        with pytest.raises(ProjectLocationError, match='changed'):
            results.values(entry['id'])
    finally:
        locations.close()


def test_second_host_may_read_results_but_cannot_publish(tmp_path):
    day, locations, results = setup(tmp_path)
    second = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        sid = day.new('Writer owned')
        entry = results.add('count', {}, 'Synthetic count', values={'count': 3})
        # A reader uses the legacy active session, with the same read-only locator.
        read_day = SessionStore(second.legacy, session='2026-10-29', locations=second)
        reader = Results(results.root, day=read_day)
        assert reader.values(entry['id'], sid) == {'count': 3}
        with pytest.raises(ProjectLocationError, match='read-only'):
            reader.add('count', {}, 'Not admitted', values={'count': 4}, session=sid)
        assert len(reader.entries(sid)) == 1
    finally:
        second.close(); locations.close()


def test_date_folder_link_is_not_a_project_result_destination(tmp_path):
    day, locations, results = setup(tmp_path)
    try:
        sid = day.new('Results')
        folder = results.root_for(sid, write=True)
        elsewhere = tmp_path / 'unrelated'; elsewhere.mkdir()
        (folder / day.date).symlink_to(elsewhere, target_is_directory=True)
        with pytest.raises(ProjectLocationError, match='replaced'):
            results.add('count', {}, 'Not retained', values={'count': 3})
        assert not list(elsewhere.iterdir())
    finally:
        locations.close()
