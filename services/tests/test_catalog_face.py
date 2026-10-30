"""A tool card's face and its abilities (orchestrator/catalog_view.py; Will's consolidation of 2026-10-13): the face says
whether it is ready and gives the one action that changes that; its abilities are every capability it really powers, read
from the registered options that run it, each with the exact (job, option) its Try binds, never a phrase that would run
another default. Isolated: keys given as a mapping, no model downloaded."""
import asyncio

import pytest

from gpu_worker import manifests as catalog
from orchestrator import capabilities, catalog_view, observed, options
from tests.test_catalog_view import nothing_downloaded, rows  # noqa: F401 - an isolated profile


def test_a_card_that_isnt_ready_shows_its_one_next_step(nothing_downloaded):
    found = rows({})
    seeing = next(r for r in found.values() if any(u["role"] == "vision" and u["current"] for u in r["usedFor"]))
    assert seeing["face"]["state"] == "needs-setup" and seeing["face"]["action"]["kind"] == "key"
    assert seeing["face"]["says"] == seeing["face"]["action"]["says"], "its line is the step that turns it on"


def test_ready_to_run_is_not_on_until_a_current_check_passed():
    """Obtained, runnable and proved stay apart (Codex's review of 14054603)."""
    found = rows({k: True for k in ("GEMINI_API_KEY", "OPENAI_API_KEY", "FAL_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN")})
    ready = [r for r in found.values() if r["status"] == "ready" and not r.get("execution")]  # nothing blocks its runs
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


def test_a_cloud_model_its_provider_retired_says_so_and_is_never_offered_to_run():
    """Will's try-out of 29 Oct: Google had retired Veo 3.1 Fast. The catalog's notice (catalog/models/maintenance.toml,
    revocations.py) is what its card says, with its successor; the successor is the one researchers use."""
    found = rows({"GEMINI_API_KEY": True})
    veo, omni = found["veo-3.1-fast"], found["gemini-omni-flash"]
    assert veo["group"] == "unavailable" and veo["execution"]["action"] == "unsupported"
    assert veo["face"] == {"state": "unavailable", "action": None, "says": "Veo 3.1 Fast can't be used: Google retired "
                           "it on 22 October 2026; Gemini Omni 1.1 Flash replaces it"}
    assert veo["tries"] == [] and omni["face"]["state"] in ("on", "unchecked") and not omni.get("execution")


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


# --- real use is evidence (observed.py "use"; Will, 2026-10-29) ---------------------------------------------------------
# Claude Opus 5.5 answered him all session while its card said "Not checked", grey, like a model that wasn't there, and
# so did Bright objects, a built-in method. A model that ran here is working here; one with its key and no run is ready,
# not checked yet; a failure is said only from a recorded failure, with its reason.
CLAUDE = {"ANTHROPIC_API_KEY": True}


@pytest.fixture
def recording(nothing_downloaded, monkeypatch):
    """The running app's record of real use, in a home of its own (server.py opens it in the app only)."""
    monkeypatch.setattr(observed, "_use_store", observed.Store(nothing_downloaded / "home" / observed.USES))
    monkeypatch.setattr(observed, "_use_changed", None)
    return nothing_downloaded


def test_a_model_that_ran_here_is_working_here_with_when(recording):
    observed.used("claude-opus-5-5", True)
    row = rows(CLAUDE)["claude-opus-5-5"]
    assert row["status"] == "ready" and row["group"] == "ready"
    face = row["face"]
    assert face["state"] == "on" and face["by"] == "use" and face["says"].startswith("Working here · last ran ")
    assert face["action"] is None and face["also"][0] == {"kind": "check", "says": "Check it here", "model": "claude-opus-5-5"}
    assert any(f["kind"] == "use" and f["ok"] is True for f in row["facts"])
    assert "Working here" in catalog_view.row_words(row)


def test_a_built_in_method_that_ran_here_is_working_here(recording):
    from orchestrator import operations

    record = operations.Execution("cells", "basic-bright-objects", "raw", "user-chosen", None, "explicit",
                                  attempts=[operations.Attempt("basic-bright-objects", True, ms=12.0, why="chosen")])
    operations._used(record)
    face = rows({})["basic-bright-objects"]["face"]
    assert face["state"] == "on" and face["by"] == "use", face


def test_a_model_with_its_key_and_no_run_is_ready_and_not_checked_yet(recording):
    row = rows(CLAUDE)["claude-opus-5-5"]
    assert row["status"] == "ready"
    assert row["face"]["state"] == "unchecked" and row["face"]["says"] == "Ready to run; not checked on this Mac yet"
    assert row["face"]["action"]["says"] == "Check it here"
    assert not any(f["kind"] == "use" for f in row["facts"]), "nothing ran: nothing is said to have"


def test_a_recorded_failure_is_a_failure_with_its_reason_and_the_next_success_clears_it(recording):
    observed.used("claude-opus-5-5", True)
    observed.used("claude-opus-5-5", False, "Anthropic 400: Your credit balance is too low")
    row = rows(CLAUDE)["claude-opus-5-5"]
    assert row["status"] == "broken" and row["face"]["state"] == "broken"
    assert "Its last use here failed" in row["face"]["says"] and "credit balance is too low" in row["face"]["says"]
    assert any(f["kind"] == "use" and f["ok"] is False and "credit balance" in f["says"] for f in row["facts"])
    observed.used("claude-opus-5-5", True)
    assert rows(CLAUDE)["claude-opus-5-5"]["face"]["by"] == "use"


def test_the_newer_of_a_check_and_a_real_use_says_how_it_is(recording):
    from orchestrator import readiness

    observed.used("claude-opus-5-5", False, "connection failed")
    use = observed.last_use("claude-opus-5-5")
    later = {"state": "works", "at": "2999-01-01T00:00:00+00:00"}
    earlier = {"state": "works", "at": "2000-01-01T00:00:00+00:00"}
    assert readiness.newest_use("claude-opus-5-5", later) is None, "a check that passed after it is newer"
    assert readiness.newest_use("claude-opus-5-5", earlier) == use
    assert readiness.newest_use("claude-opus-5-5", {**later, "earlierBuild": "x"}) == use, "an earlier build's check proves nothing now"


def test_only_what_reached_the_model_is_use(recording):
    from orchestrator import operations

    def run(*attempts, origin="user-chosen", trial=None):
        operations._used(operations.Execution("cells", "micro-sam-lm", "raw", origin, None, "explicit",
                                              attempts=list(attempts), trial=trial))

    run(operations.Attempt("micro-sam-lm", False, error="", not_run="cancelled"),
        operations.Attempt("micro-sam-lm", False, error="worker busy", not_run="busy"))
    run(operations.Attempt("micro-sam-lm", True), origin="check")
    assert observed.last_use("micro-sam-lm") is None, "cancelled, busy and the app's own check are not use"
    run(operations.Attempt("micro-sam-lm", False, error="RuntimeError: MPS out of memory"))
    assert observed.last_use("micro-sam-lm")["state"] == "no"
    assert "MPS out of memory" in observed.last_use("micro-sam-lm")["says"]
    with observed.not_use():
        observed.used("micro-sam-lm", True)
    assert observed.last_use("micro-sam-lm")["state"] == "no", "a model check's calls are its own record"


def test_nothing_is_recorded_outside_the_running_app(nothing_downloaded, monkeypatch):
    monkeypatch.setattr(observed, "_use_store", None)
    assert observed.used("claude-opus-5-5", True) is False
    assert observed.last_use("claude-opus-5-5") is None
    assert not (nothing_downloaded / "catalog" / observed.USES).exists()


def test_real_use_survives_a_restart_in_its_own_home(recording, monkeypatch):
    observed.used("claude-opus-5-5", True)
    monkeypatch.setattr(observed, "_use_store", observed.Store(recording / "home" / observed.USES))  # a fresh process
    assert observed.last_use("claude-opus-5-5")["state"] == "yes"


def test_a_cloud_call_is_use_of_its_entry_and_the_apps_own_cut_off_is_not(recording, monkeypatch):
    from research_worker import api_usage

    monkeypatch.setattr(api_usage, "_outcome", observed.call_used)
    monkeypatch.setattr(api_usage, "_active", None)
    monkeypatch.setattr(api_usage, "_provider_allowed", None)

    async def call(error: BaseException | None) -> None:
        async with api_usage.attempt("claude-opus-5-5", provider="Anthropic"):
            if error is not None:
                raise error

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call(asyncio.CancelledError()))
    assert observed.last_use("claude-opus-5-5") is None, "cut off by the app (a newer request): says nothing of the model"
    asyncio.run(call(None))
    assert observed.last_use("claude-opus-5-5")["state"] == "yes"

    class Refused(Exception):
        status_code = 401

    with pytest.raises(Refused):
        asyncio.run(call(Refused("invalid x-api-key")))
    last = observed.last_use("claude-opus-5-5")
    # Why, in a person's words with what to do (api_usage.refusal), and the provider's own words beside it.
    assert last["state"] == "no" and last["says"].startswith("Your Anthropic key was refused; check your Anthropic key "
                                                             "in Models › Keys") and "invalid x-api-key" in last["says"]
    assert observed.call_entry("veo-3.1-fast-generate-preview") == "veo-3.1-fast" and observed.call_entry("google_search") == ""
    assert observed.call_entry("gemini-omni-1.1-flash") == "gemini-omni-flash"


def test_the_studios_are_told_once_when_models_turn_never_on_every_call(nothing_downloaded, monkeypatch):
    told = []
    monkeypatch.setattr(observed, "TELL_EVERY_S", 0.01)
    observed.record_use(nothing_downloaded / "home" / observed.USES, changed=lambda: told.append(1))
    try:
        async def calls() -> None:
            observed.used("claude-opus-5-5", True)  # turned working
            observed.used("claude-opus-5-5", True)  # the same a moment later: not even written
            observed.used("micro-sam-lm", False, "RuntimeError: MPS out of memory")  # another turned failed
            await asyncio.sleep(0.05)

        asyncio.run(calls())
        assert told == [1], "one Models page for both turns"
    finally:
        observed.record_use(None)
