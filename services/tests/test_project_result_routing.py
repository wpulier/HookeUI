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


@pytest.mark.parametrize('failure', ['closed', 'missing'])
def test_storage_failure_wakes_admitted_picture_waiters(tmp_path, monkeypatch, failure):
    day, locations, results = setup(tmp_path)
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    write = results._write
    def delayed(date, name, data, **kwargs):
        if name.endswith('.jpg'):
            entered.set()
            assert release.wait(HANG_S)
        return write(date, name, data, **kwargs)
    monkeypatch.setattr(results, '_write', delayed)
    try:
        sid = day.new('Delayed writer')
        entry = results.add('measure', {}, 'Synthetic result', picture_bytes=[b'pixels'], session=sid)
        until(entered.is_set)
        error = []
        def wait():
            try:
                results.wait_for_picture(entry['day'], entry['pictures'][0], session=sid)
            except ValueError as exc:
                error.append(str(exc))
            finally:
                settled.set()
        waiter = threading.Thread(target=wait); waiter.start()
        if failure == 'closed':
            locations.close()
        else:
            folder = results.root_for(sid)
            folder.rename(folder.with_name('preserved'))
        release.set(); until(settled.is_set); waiter.join()
        assert error == ['This original could not be saved. Check local storage and rerun its producer.']
        assert not results._picture_writes
        assert results._pending  # bytes remain display-only, never declared retained
    finally:
        release.set(); locations.close()


def test_routed_picture_is_reusable_as_exact_capability_input_after_switch(tmp_path):
    import cv2
    import numpy as np
    from orchestrator import retained_inputs
    from tests.test_resources import app_at
    day, locations, results = setup(tmp_path)
    built = app_at(tmp_path / 'isolated-input-reader').state.app
    built.hub.resources.results = results
    try:
        sid = day.new('Retained RGB')
        png = cv2.imencode('.png', np.full((7, 9, 3), 71, np.uint8))[1].tobytes()
        entry = results.add('run_capability', {}, 'Synthetic pixels', values={
            'output': 'image.2d.rgb8',
            'scientific': {'dtype': 'uint8', 'axes': ['y', 'x', 'c'], 'shape': [7, 9, 3]}},
            picture_bytes=[png], session=sid)
        results.wait_for_picture(entry['day'], entry['pictures'][0], session=sid)
        day.new('Different visible Project')
        reference = retained_inputs.reference_for(built.hub.resources, entry['id'], sid)
        assert reference is not None
        assert retained_inputs.layout(built.hub, reference, sid)['shape'] == [7, 9, 3]
        capture, hold = retained_inputs.resolve(built.hub, reference, sid)
        try:
            assert capture.analysis_bytes == png
            assert capture.source_image['scientific']['reference'] == reference
        finally:
            hold.release()
    finally:
        locations.close()
