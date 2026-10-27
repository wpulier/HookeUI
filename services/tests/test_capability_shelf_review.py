"""Shelf facts keep observed proof, selection and every destination distinct."""
from orchestrator import capabilities, capability_shelf as shelf, options
from tests.test_capability_shelf import KEY_FIRST, READY, models, role


def test_a_downloaded_builtin_without_a_passed_check_is_not_green(models):
    card = shelf.card(role(("a", READY, "mac"), proven=False), "Find every cell", "", "", [], [])
    assert card["colour"] != "green", "downloaded and runnable do not mean proven on this Mac"
    assert card["turnOn"], "a check is an actionable setup step"


def test_a_failed_check_has_a_repair_action_instead_of_an_unavailable_card(models):
    broken = {"status": "broken", "word": "Broken", "steps": [
        {"kind": "check", "says": "Its last check failed: worker stopped", "how": "Check again"}]}
    card = shelf.card(role(("a", broken, "mac")), "Find every cell", "", "", [], [])
    assert card["colour"] == "red" and card["turnOn"], "Fix it must lead to another owned check"


def test_a_ready_alternative_is_not_reported_as_the_selected_method(models):
    r = role(("a", READY, "mac"), ("b", KEY_FIRST, "cloud"), current="b")
    r["options"][0]["check"] = {"state": "works", "at": "2026-10-13T12:00:00"}
    card = shelf.card(r, "Find every cell", "", "", [], [])
    assert card["using"] != "A", "the user's B selection has not been changed to A"


def test_a_mixed_local_cloud_choice_discloses_the_remote_destination_and_bill():
    settings = options.Settings()
    settings.update({"outline": "edgetam>sam3.1-fal"})
    wire = capabilities.wire(settings, {"FAL_KEY": True})
    r = next(r for r in wire["roles"] if r["id"] == "outline")
    card = shelf.card(r, "Outline what you click", "", "", [], [])
    # Setup may correctly choose another option when this selection is unavailable. Its own row must still
    # disclose every component, independent of the separate admitted-plan facts at the top of the card.
    selected = next(o for o in card["options"] if o["id"] == r["current"])
    assert "fal.ai" in selected["where"] and "Mac" in selected["where"]
    assert selected["cost"] != "Free" and "provider" in selected["cost"].lower()


def test_shelf_exposes_recorded_demo_check_and_specimen_outcome_without_invented_quality(models):
    r = role(('a', READY, 'mac'), ('b', KEY_FIRST, 'cloud'))
    r['options'][0]['check'] = {'state': 'works', 'ms': 123, 'detail': 'three objects', 'at': '2026-10-13T12:00:00'}
    caps = [{'id': 'find-cells', 'roles': ['cells'], 'media': 'find-cells.jpg', 'verdict': {'state': 'works', 'note': 'checked'}}]
    result = shelf.cards([r], [], [], capabilities=caps, outcomes={'cells': ['A on diatoms: 3 runs today, 0 corrected']})
    c = result['cards'][0]
    assert c['demonstration']['url'] == '/api/capabilities/media/find-cells.jpg'
    assert c['check']['ms'] == 123 and c['outcomes'] == ['A on diatoms: 3 runs today, 0 corrected']
    assert c['options'][0]['default'] is True


def test_observation_refresh_keeps_real_outcome_subjects_counts_and_timing_apart(tmp_path):
    import asyncio
    from orchestrator.outcomes import Outcomes
    from tests.capability_rig import Rig
    from tests.test_outcomes import execution, write_log

    app = Rig(tmp_path).app
    log = tmp_path / 'observed.jsonl'
    write_log(log, [execution(i, 'cellpose-sam', subject='diatoms', ms=125) for i in range(3)]
              + [execution(4, 'cellpose-sam', subject='blood smear', ms=1000)])
    app.hub.outcomes = Outcomes([log], None, None)
    asyncio.run(app.refresh_shelf_observations())
    card = next(c for c in app.capabilities_wire()['setup']['shelf']['cards'] if c['id'] == 'cells')
    assert any('diatoms' in line and '3 runs' in line for line in card['outcomes'])
    assert any('blood smear' in line and 'too few runs' in line for line in card['outcomes'])
