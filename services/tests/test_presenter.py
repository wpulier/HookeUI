"""The shelf of researchers' work (orchestrator/presenter.py): a chip the user removes stays off the
shelf for the day, through reloads and restarts (the day's journal), and is never told to the models."""
from types import SimpleNamespace

from orchestrator.day.context import day_digest
from orchestrator.day.store import DayStore
from orchestrator.presenter import Presenter


def presenter_for(store: DayStore) -> tuple[Presenter, list]:
    sent: list = []
    hub = SimpleNamespace(broadcast=sent.append)
    return Presenter(hub, research=SimpleNamespace(get=lambda rid: None), prefs=(store.prefs.get, store.set_pref)), sent


def test_a_chip_taken_off_the_shelf_stays_off_after_a_restart_and_can_come_back(tmp_path):
    store = DayStore(root=tmp_path)
    presenter, sent = presenter_for(store)
    assert presenter.wire()["shelfRemoved"] == []
    presenter.remove_from_shelf("r1")
    presenter.remove_from_shelf("r1")  # once is enough
    assert presenter.wire()["shelfRemoved"] == ["r1"] and sent[-1]["shelfRemoved"] == ["r1"]

    again, _ = presenter_for(DayStore(root=tmp_path))  # the app restarted: the journal is read again
    assert again.removed() == ["r1"]
    again.back_on_shelf("r1")
    assert presenter_for(DayStore(root=tmp_path))[0].removed() == []


def test_the_shelf_is_never_in_what_the_models_are_told(tmp_path):
    store = DayStore(root=tmp_path)
    presenter_for(store)[0].remove_from_shelf("r7")
    assert "shelfRemoved" not in day_digest(store) and "r7" not in day_digest(store)


def test_connected_viewers_open_and_close_independently(tmp_path):
    import json
    from tests.test_research_versions import lesson_session
    manager, session = lesson_session(tmp_path)
    broadcasts = []
    presenter = Presenter(SimpleNamespace(broadcast=broadcasts.append), manager)
    class Client:
        def __init__(self): self.messages = []
        def offer(self, value): self.messages.append(json.loads(value))
    a, b = Client(), Client()
    presenter.for_client(a).show(session.rid)
    assert a.messages[-1]['open'] and not b.messages and not broadcasts
    presenter.for_client(b).show(session.rid)
    presenter.for_client(a).close()
    assert not a.messages[-1]['open'] and b.messages[-1]['open']
    assert presenter.for_client(b).open and not presenter.open

    presenter.reset_all()
    assert not presenter.for_client(b).open and not b.messages[-1]['open']
    c = Client()
    assert not presenter.for_client(c).wire()['open']


def test_saved_media_reopens_exact_day_version_without_changing_today(tmp_path):
    import copy
    import json
    import pytest
    from tests.test_research_versions import lesson_session
    from orchestrator.research import ResearchManager
    from tests.test_research import FakeHub

    manager, current = lesson_session(tmp_path)
    old = current.completed_view()
    old.day = '2026-10-03'
    old.outputs[0]['caption'] = 'Original saved lesson'
    old.status = 'done'
    manager._save(old)
    unchanged = copy.deepcopy(current.outputs)
    restored = ResearchManager(FakeHub(), root=tmp_path)
    restored.sessions[current.rid] = current
    presenter = Presenter(SimpleNamespace(broadcast=lambda _: pytest.fail('must target the owning browser')), restored)
    class Client:
        def __init__(self): self.messages = []
        def offer(self, value): self.messages.append(json.loads(value))
    a, b = Client(), Client()
    view = presenter.for_client(a)
    view.show_saved(old.day, old.rid, old.outputs[0]['id'], old.outputs[0]['version'])
    saved = a.messages[-1]['savedSession']
    assert saved['day'] == '2026-10-03' and saved['outputs'][0]['caption'] == 'Original saved lesson'
    assert current.outputs == unchanged and not b.messages
    assert restored._worker is None and not restored.queue
    with pytest.raises(KeyError, match='exact saved output'):
        view.show_saved(old.day, old.rid, old.outputs[0]['id'], 99)
    assert view.saved_session.day == old.day
    with pytest.raises(ValueError, match='read-only'):
        view.go_to_step(old.rid, 'next')
    view.close()
    view.show_saved(old.day, old.rid, old.outputs[0]['id'], old.outputs[0]['version'])
    assert a.messages[-1]['savedSession']['outputs'] == saved['outputs']


def test_exact_current_lesson_retains_navigation_and_outcomes(tmp_path):
    from tests.test_research_versions import lesson_session
    manager, session = lesson_session(tmp_path)
    presenter = Presenter(SimpleNamespace(broadcast=lambda _: None), manager, outcomes=lambda rid: {'q1':'right'})
    output = next(o for o in session.current_outputs() if o['kind'] == 'html')
    presenter.show_saved(session.day, session.rid, output['id'], output['version'])
    assert presenter.saved_session is None and presenter.wire()['outcomes'] == {'q1':'right'}
    assert presenter.go_to_step(session.rid, 'q2')[0]['id'] == 'q2'


def test_switching_from_saved_work_to_another_lesson_clears_the_frozen_reading(tmp_path):
    from tests.test_research_versions import lesson_session
    manager, session = lesson_session(tmp_path)
    frozen = session.completed_view()
    frozen.rid = 'other-researcher'
    presenter = Presenter(SimpleNamespace(broadcast=lambda _: None), manager)
    presenter.saved_session = frozen
    presenter.rid = frozen.rid
    assert presenter.go_to_step(session.rid, 'q2')[0]['id'] == 'q2'
    assert presenter.saved_session is None
    assert presenter.wire()['rid'] == session.rid
    assert presenter.current_step()[1]['id'] == 'q2'


def test_report_defaults_only_to_a_presentation_of_current_findings(tmp_path):
    from tests.test_research_versions import lesson_session
    from orchestrator.presenter import first_shown, items
    manager, session = lesson_session(tmp_path)
    session.deliverable = 'report'
    manager._finish(session, 'done', {'summary':'First findings', 'report':{'observations':'First findings'}})
    basis = dict(session.result['findingRevision'])
    session.outputs.append({'id':'p1', 'kind':'presentation', 'version':1, 'path':'outputs/p1.json', 'findingRevision':basis})
    assert items(session)[first_shown(session)]['id'] == 'p1'
    session.status = 'running'
    manager._finish(session, 'done', {'summary':'New findings', 'report':{'observations':'New findings'}})
    assert items(session)[first_shown(session)]['kind'] == 'summary'


def test_conflicting_presentation_is_never_the_default(tmp_path):
    from tests.test_research_versions import lesson_session
    from orchestrator.presenter import first_shown, items
    manager, session = lesson_session(tmp_path)
    session.deliverable = 'report'
    manager._finish(session, 'done', {'summary':'Current science', 'report':{'observations':'Retained'}})
    basis = dict(session.result['findingRevision'])
    session.outputs.extend([
        {'id':'p1', 'kind':'presentation', 'version':1, 'path':'outputs/p1.json', 'findingRevision':basis},
        {'id':'p2', 'kind':'presentation', 'version':2, 'replaces':'p1', 'path':'outputs/p2.json', 'findingRevision':{'digest':'other'}},
    ])
    assert items(session)[first_shown(session)]['kind'] == 'summary'


def test_reference_selection_stays_a_reference_when_findings_have_versions(tmp_path):
    from tests.test_research_versions import lesson_session
    from orchestrator.presenter import items
    manager, session = lesson_session(tmp_path)
    manager._finish(session, 'done', {'summary':'Retained', 'report':{'observations':'Retained'},
                                    'refs':[{'path':'sources/one.jpg', 'title':'Original reference'}]})
    presenter = Presenter(SimpleNamespace(broadcast=lambda _: None), manager)
    presenter.show(session.rid, next(i for i, item in enumerate(items(session)) if item['kind'] == 'reference'))
    assert presenter.wire()['findingRevision'] is None
    session.result['refs'] = []
    assert presenter.wire()['savedSession']['items'][presenter.index]['kind'] == 'reference'


def test_reference_tabs_select_within_the_frozen_reading_after_new_outputs(tmp_path):
    from orchestrator.presenter import items
    from tests.test_research_versions import lesson_session
    manager, session = lesson_session(tmp_path)
    manager._finish(session, 'done', {'summary':'Original', 'report':{'observations':'Original'},
        'refs':[{'path':'sources/a.jpg', 'title':'A'}, {'path':'sources/b.jpg', 'title':'B'}]})
    presenter = Presenter(SimpleNamespace(broadcast=lambda _:None), manager)
    reference = next(i for i, item in enumerate(items(session)) if item['kind'] == 'reference')
    presenter.show(session.rid, reference)
    snapshot = presenter.saved_session
    second = next(item for item in items(snapshot) if item.get('ref') == 1)
    session.result['refs'] = [{'path':'sources/new.jpg', 'title':'New'}]
    session.outputs.append({'id':'late', 'kind':'video', 'path':'outputs/late.mp4', 'version':1})
    presenter.show_retained_item(session.day, session.rid, second['work'])
    assert presenter.saved_session is snapshot
    assert items(snapshot)[presenter.index]['path'] == 'sources/b.jpg'
    assert presenter.wire()['savedSession']['items'][presenter.index]['work'] == second['work']


def test_html_report_is_described_as_written_work_not_a_lesson(tmp_path):
    from tests.test_research_versions import lesson_session
    from orchestrator.presenter import describe, index_of
    manager, session = lesson_session(tmp_path)
    report = {'id':'html-report', 'kind':'html', 'contentKind':'report', 'path':'outputs/report.html',
              'caption':'A report', 'version':1}
    session.outputs.append(report)
    assert 'lesson' not in describe(session, index_of(session, report['id'])).lower()
    presenter = Presenter(SimpleNamespace(broadcast=lambda _: None), manager)
    presenter.go_to_step(session.rid, 'q2')
    assert presenter.wire()['outputId'] != report['id']
