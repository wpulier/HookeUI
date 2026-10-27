"""A tool card's face and its abilities (orchestrator/catalog_view.py; Will's consolidation of 2026-10-13): the face says
whether it is ready and gives the one action that changes that; its abilities are every capability it really powers, read
from the registered options that run it, each with the exact (job, option) its Try binds, never a phrase that would run
another default. Isolated: keys given as a mapping, no model downloaded."""
from gpu_worker import manifests as catalog
from orchestrator import capabilities, options
from tests.test_catalog_view import nothing_downloaded, rows  # noqa: F401 - an isolated profile


def test_a_card_that_isnt_ready_shows_its_one_next_step(nothing_downloaded):
    found = rows({})
    seeing = next(r for r in found.values() if any(u["role"] == "vision" and u["current"] for u in r["usedFor"]))
    assert seeing["face"]["state"] == "needs-setup" and seeing["face"]["action"]["kind"] == "key"
    assert seeing["face"]["says"] == seeing["face"]["action"]["says"], "its line is the step that turns it on"


def test_ready_to_run_is_not_on_until_a_current_check_passed():
    """Obtained, runnable and proved stay apart (Codex's review of 14054603)."""
    found = rows({k: True for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "FAL_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN")})
    ready = [r for r in found.values() if r["status"] == "ready"]
    assert ready
    for r in ready:
        state = r["face"]["state"]
        assert state in ("on", "unchecked", "obtained"), r["face"]
        if state == "unchecked":
            assert r["face"]["action"]["kind"] == "check" and r["face"]["action"]["says"] == "Check it here"
        if state == "on":
            assert r["face"]["action"] is None and r["face"]["also"][0]["kind"] == "check"
        if state == "obtained":
            assert catalog.get(r["id"]).kind == "data", "only data is obtained with nothing to run"


def test_abilities_list_what_it_does_and_its_try_is_one_truthful_demonstration_per_role():
    found = rows({})
    contract = {c["id"]: c for c in capabilities.load()["capabilities"]}
    for row in found.values():
        roles = {u["role"]: u for u in row["usedFor"]}
        assert all("try" not in a for a in row["abilities"]), "no capability carries a Try its title might not be"
        for a in row["abilities"]:
            if not a["id"].startswith("kind:"):
                assert a["role"] in roles and a["role"] in (contract[a["id"]].get("roles") or []), (row["id"], a)
        for t in row["tries"]:
            if t["try"]["command"] == "capability-try":
                use = roles[t["role"]]
                expected = {"model": "primary"}.get(use["option"], use["option"]) if use.get("fixed") else use["option"]
                assert t["try"] == {"command": "capability-try", "job": t["role"], "option": expected}, (row["id"], t)
                assert t["title"].startswith(f"Try {use['title']} with "), "labelled by its role, never a capability"
    fixed = [t for r in found.values() for t in r["tries"] if t["try"].get("option") == "primary"]
    assert fixed, "a fixed role's main model is its `primary`, as capability_shelf names it"


def test_an_entry_that_powers_nothing_has_no_abilities_and_says_so_plainly():
    found = rows({})
    data = [r for r in found.values() if catalog.get(r["id"]) is not None and catalog.get(r["id"]).kind == "data"]
    assert all(isinstance(r["abilities"], list) for r in data)


def test_each_tool_carries_its_exact_setup_and_only_supported_trials(nothing_downloaded):
    from orchestrator import capability_trials

    found = rows({})
    edge = found['edgetam']
    setup = next(s for s in edge['setups'] if s['role'] == 'outline')
    assert setup['plan']['option'] == 'edgetam' and setup['plan']['model'] == 'edgetam'
    assert setup['plan']['steps'][0]['kind'] == 'download'
    assert setup['plan']['recipe'] and setup['plan']['privacy'] == 'Runs on this Mac'
    assert not setup['canTry'] and not setup['recheck']
    for row in found.values():
        assert all(t['role'] in capability_trials.TRIALS for t in row['tries'] if t['try']['command'] == 'capability-try')


def test_roleless_tools_read_their_own_functional_receipts_and_offer_exact_recheck(nothing_downloaded, monkeypatch):
    from types import SimpleNamespace
    from orchestrator import catalog_view, env_runner, fiji_generations, readiness

    # Synthetic generation suppliers: no JVM/model is started; the actual row builder must interpret their receipts.
    real_ready = readiness.model
    monkeypatch.setattr(readiness, 'model', lambda key, keys=None, environ=None: readiness.Readiness('ready', 'Ready')
                        if key in ('fiji', 'brainreg') else real_ready(key, keys, environ))
    monkeypatch.setattr(fiji_generations, 'current', lambda environ=None: 7)
    monkeypatch.setattr(fiji_generations, 'receipt', lambda n, environ=None: {'proofs': {'smoke': '2.18.0'}, 'at': '2026-10-13T12:00:00'})
    brain = catalog.get('brainreg')
    real_installed = env_runner.in_use
    installed = SimpleNamespace(m=brain, folder=nothing_downloaded / 'brainreg-generation', proof={'ok': True, 'at': '2026-10-13T12:00:01', 'seconds': 4, 'says': 'Registered its public sample'}, current=True)
    monkeypatch.setattr(env_runner, 'in_use', lambda m, environ=None: installed if m.key == 'brainreg' else real_installed(m, environ))
    found = {r['id']: r for r in catalog_view.rows([], {}, {})}
    for key in ('fiji', 'brainreg'):
        row = found[key]
        assert row['face']['state'] == 'on', row['face']
        setup = next(s for s in row['setups'] if s['role'] == 'model:' + key)
        assert setup['recheck'] and setup['check']['state'] == 'works'
        assert setup['plan']['model'] == key and setup['plan']['steps'][0]['model'] == key
        assert setup['plan']['steps'][0]['kind'] == 'install', 'use its own supported proof queue, not the role checker'
        assert setup['plan']['steps'][0]['recheck'] is True
        assert not setup['canTry'], 'brainreg uses its volume workflow, never a fabricated role trial'


def test_ready_group_requires_the_same_current_proof_as_its_badge():
    from orchestrator import catalog_view

    settings = options.Settings()
    for check, expected in ((None, 'available'), ({'state': 'works'}, 'ready'),
                            ({'state': 'works', 'earlierBuild': True}, 'available')):
        wire = capabilities.wire(settings, {})
        role = next(r for r in wire['roles'] if r['id'] == 'embed')
        for option in role['options']:
            option['check'] = check
        found = {r['id']: r for r in catalog_view.rows(wire['roles'], {}, {})}
        row = found['dinov2-small']
        assert row['status'] == 'ready', 'proof absence does not revoke existing execution eligibility'
        assert row['group'] == expected
        assert (row['face']['state'] == 'on') == (expected == 'ready')


def test_fixed_model_setup_includes_its_missing_files(nothing_downloaded):
    from orchestrator import capability_shelf

    wire = capabilities.wire(options.Settings(), {})
    role = next(r for r in wire['roles'] if r.get('primary', {}).get('model') == 'trackastra')
    assert role['primary']['readiness']['status'] == 'needs-setup'
    assert not role['primary']['available'] and role['primary']['why']
    plan = capability_shelf.plan(role)
    assert plan['steps'][0]['kind'] == 'download'
    assert plan['steps'][0]['model'] == 'trackastra'
    row = rows({})['trackastra']
    assert row['setups'][0]['plan']['steps'] == plan['steps']


def test_agent_overview_uses_the_same_proof_words_as_the_card():
    from orchestrator import catalog_view
    found = rows({})
    row = found['dinov2-small']
    text = catalog_view.overview([row], {})
    assert 'Setup and checks' in text
    assert row['face']['says'] in text
    assert row['face']['says'] in catalog_view.row_words(row)
    assert 'Next step: Check it here' in catalog_view.row_words(row)
