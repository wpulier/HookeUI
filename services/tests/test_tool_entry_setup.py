"""Entry-level setup/recheck through its production admission and queue seam; no real installs or models."""
from types import SimpleNamespace

from orchestrator import capability_shelf as shelf


def test_roleless_recheck_runs_its_exact_owned_install_proof_then_finishes_once():
    row = {'id': 'fiji', 'name': 'Fiji', 'runs': 'this-mac', 'status': 'ready', 'word': 'Ready', 'steps': [],
           'functionalCheck': {'state': 'works', 'at': 'before'},
           'proofStep': {'kind': 'install', 'says': 'Run Fiji proof', 'model': 'fiji', 'recheck': True}}
    records, notices = [], []
    def start(model, **kw):
        record = {'model': model, 'state': 'installing', **kw}; records.append(record); return record
    wire = {'roles': [], 'models': [row], 'setup': {'downloads': [], 'installs': records}}
    app = SimpleNamespace(capabilities_wire=lambda **kw: wire, installs=SimpleNamespace(start=start),
                          turned_on=lambda job, says, **kw: notices.append(says))
    turn = shelf.TurnOn(app)
    role = shelf.entry_role(row); option = shelf._options(role)[0]
    plan = shelf.plan(role, [{**option, 'check': None}])
    turn.start('model:fiji', 'fiji', expected=plan, recheck=True)
    assert records[0].get('recheck') is True, 'recheck must actually rerun Fiji rather than reuse first()'
    assert records[0]['by'].startswith('capability:')
    records[0]['state'] = 'done'; row['functionalCheck'] = {'state': 'works', 'at': 'after'}
    turn.advance(); turn.advance()
    assert not turn.wire() and len(notices) == 1 and len(records) == 1


def test_fiji_recheck_uses_a_fresh_proven_generation(monkeypatch, tmp_path):
    from gpu_worker import manifests as catalog
    from orchestrator import envs, fiji_generations as fiji
    calls = []
    monkeypatch.setattr(fiji, 'current', lambda environ: 7)
    monkeypatch.setattr(fiji, 'renew', lambda environ, **kw: calls.append('renew') or 8)
    monkeypatch.setattr(fiji, 'first', lambda environ, **kw: calls.append('first') or 7)
    monkeypatch.setattr(fiji, 'root', lambda environ: tmp_path)
    folder, says = envs.install(catalog.get('fiji'), {}, recheck=True)
    assert folder == tmp_path / '8' and calls == ['renew']
