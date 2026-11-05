"""Reproduce a VCC 2026 entry inside Hooke, through the one door, as an outside agent would (ADR 0031).

  python -m bench.vcc_entry_reproduction connect --door http://127.0.0.1:PORT --token-file TOKEN
  python -m bench.vcc_entry_reproduction entry --door ... --token-file TOKEN --data DIR --record OUT.json [--expect-factors SHA --expect-package SHA]
  python -m bench.vcc_entry_reproduction gate  --door ... --token-file TOKEN --data DIR --record OUT.json --project ID --study ID

`entry` imports the declared inputs into a new Project, opens a Study, builds the frozen five-step workflow from
the capabilities' current generations (screen effects -> transfer factors -> count emission -> entry check ->
packaging), starts it in the Study, waits, downloads the factors and the package and compares their sha256 with
the expected ones. `gate` freezes the decision rule in that Study first, then scores two fold predictions and
decides between them under the frozen rule. Nothing is uploaded anywhere; the door is loopback only.

DIR holds: K562_gwps_raw_bulk_01.h5ad, k562-contest-targets-registry-v2.proposed.json, gene_names.csv,
pert_counts.csv, manifest.json, context_D.h5ad, context_E.h5ad, context_F.h5ad (and for `gate`: real.h5ad,
pred-baseline.h5ad, pred-challenger.h5ad, rule.json). Files are read by Hooke from their absolute paths.
"""
import argparse
import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ENTRY_FILES = {'screen': 'K562_gwps_raw_bulk_01.h5ad', 'registry': 'k562-contest-targets-registry-v2.proposed.json',
               'genes': 'gene_names.csv', 'perts': 'pert_counts.csv', 'manifest': 'manifest.json',
               'controls_d': 'context_D.h5ad', 'controls_e': 'context_E.h5ad', 'controls_f': 'context_F.h5ad'}
GATE_FILES = {'real': 'real.h5ad', 'pred_baseline': 'pred-baseline.h5ad', 'pred_challenger': 'pred-challenger.h5ad'}
C3 = {'a': 1.0, 's': 0.5, 'rank': 0, 'k': 50.0, 'clip_low': -5.0, 'clip_high': 2.0, 'strength_weight': 'saturating',
      'strength_half': 100.0, 'strength_scale': 400.0, 'contexts': 'D,E,F'}
EFFECTS = {'pseudocount': 1.0, 'control_marker': 'non-targeting', 'cells_column': 'num_cells_filtered',
           'gene_symbol_column': 'gene_name', 'strength_column': 'mann_whitney_counts'}
EMISSION = {'contexts': 'D,E,F', 'cells_per_target': 400, 'context_column': 'context', 'seed': 0}
CHECK = {'contexts': 'D,E,F', 'cells_per_target': 400, 'sampled_targets': 6, 'max_cell_total': 1e6, 'seed': 1}
CONTROLS = {'controls_1': 'input:controls_d', 'controls_2': 'input:controls_e', 'controls_3': 'input:controls_f'}


class Door:
    def __init__(self, url, token=None):
        self.url, self.token = url.rstrip('/') + '/api/agent/v1/', token

    def call(self, method, path, body=None, raw=False):
        request = urllib.request.Request(self.url + path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={'Hooke-Contract': '1', 'Content-Type': 'application/json',
                                                  **({'Authorization': 'Bearer ' + self.token} if self.token else {})})
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return response.read() if raw else json.loads(response.read())
        except urllib.error.HTTPError as error:
            reply = error.read()
            if error.code == 202:
                return json.loads(reply)
            raise SystemExit(f'{method} {path} -> {error.code}: {reply[:2000].decode(errors="replace")}') from None

    def operation(self, body, says):
        started = self.call('POST', 'operations', body)['operation']
        print(says, started['id'], flush=True)
        last = None
        while True:
            op = self.call('GET', f"operations/{started['id']}")['operation']
            line = op.get('state'), (op.get('progress') or {}).get('says')
            if line != last:
                print(' ', *line, flush=True)
                last = line
            if op.get('state') in ('succeeded', 'failed', 'cancelled', 'interrupted'):
                if op['state'] != 'succeeded':
                    raise SystemExit(json.dumps(op, indent=1)[:4000])
                return op
            time.sleep(5)


def node(id_, name, capability, generation, parameters, needs, bindings):
    return {'id': id_, 'name': name, 'kind': 'tool', 'needs': needs,
            'settings': {'capability': capability, 'generation': generation, 'parameters': parameters,
                         'mode': 'automatic', 'bindings': bindings}}


def entry_graph(generations):
    g = generations
    return {'format': 1, 'input': {'kind': 'source-file'}, 'nodes': [
        node('effects', 'Screen effects', 'screen-effects', g['screen-effects'], EFFECTS,
             ['input:screen', 'input:genes', 'input:registry'],
             {'screen': 'input:screen', 'genes': 'input:genes', 'registry': 'input:registry'}),
        node('factors', 'Transfer factors (C3)', 'transfer-factors', g['transfer-factors'], C3,
             ['effects', 'input:genes', 'input:perts'], {'effects': 'effects', 'genes': 'input:genes', 'perts': 'input:perts'}),
        node('emission', 'Emit counts', 'vcc-count-emission', g['vcc-count-emission'], EMISSION,
             ['factors', *CONTROLS.values(), 'input:genes', 'input:perts'],
             {'factors': 'factors', **CONTROLS, 'genes': 'input:genes', 'perts': 'input:perts'}),
        node('check', 'Check the entry', 'vcc-entry-check', g['vcc-entry-check'], CHECK,
             ['emission', 'factors', *CONTROLS.values(), 'input:genes', 'input:perts'],
             {'entry': 'emission', 'factors': 'factors', **CONTROLS, 'genes': 'input:genes', 'perts': 'input:perts'}),
        node('package', 'Package (official vcc prep)', 'vcc-package', g['vcc-package'], {},
             ['emission', 'input:genes', 'input:perts', 'input:manifest'],
             {'matrix': 'emission', 'genes': 'input:genes', 'perts': 'input:perts', 'manifest': 'input:manifest'}),
    ]}


def gate_graph(generations):
    g = generations
    score = {'preset': 'vcc2026', 'pert_col': 'target_gene', 'control_label': 'non-targeting'}
    return {'format': 1, 'input': {'kind': 'source-file'}, 'nodes': [
        node('score-baseline', 'Score baseline', 'vcc-fold-score', g['vcc-fold-score'], score,
             ['input:pred_baseline', 'input:real'], {'prediction': 'input:pred_baseline', 'real': 'input:real'}),
        node('score-challenger', 'Score challenger', 'vcc-fold-score', g['vcc-fold-score'], score,
             ['input:pred_challenger', 'input:real'], {'prediction': 'input:pred_challenger', 'real': 'input:real'}),
        node('decide', 'Decide under the frozen rule', 'frozen-gate', g['frozen-gate'], {},
             ['score-baseline', 'score-challenger', 'input:rule'],
             {'baseline': 'score-baseline', 'challenger': 'score-challenger', 'rule': 'input:rule'}),
    ]}


def connect(args):
    door = Door(args.door)
    secret = secrets.token_hex(32)
    pending = door.call('POST', 'connect', {'label': 'VCC entry reproduction', 'kind': 'claude-code',
                                            'secretSha256': hashlib.sha256(secret.encode()).hexdigest()})['pending']
    print('Confirm in the Hooke window; code', pending['code'], flush=True)
    while True:
        reply = door.call('POST', f"connect/{pending['id']}/claim", {'secret': secret})
        if 'token' in reply:
            Path(args.token_file).write_text(reply['token'])
            Path(args.token_file).chmod(0o600)
            print('connected', reply['connection']['id'])
            return
        time.sleep(2)


def imported(door, project, data, files, key):
    refs = {}
    for name, filename in files.items():
        path = (Path(data) / filename).resolve()
        op = door.operation({'tool': 'project.import', 'project': project, 'requestKey': f'{key}-import-{name}',
                             'args': {'file': str(path)}}, f'import {filename}')
        refs[name] = op['outcome']['artifact']
    return refs


def generations(door, names):
    found = {c['id']: c for c in door.call('GET', 'workflows/capabilities')['capabilities']}
    missing = [n for n in names if n not in found or not found[n].get('ready')]
    if missing:
        raise SystemExit(f'Not ready here: {[(n, found.get(n, {}).get("reason")) for n in missing]}')
    return {n: found[n]['generation'] for n in names}


def run_workflow(door, project, study, name, graph, inputs, bindings, key):
    made = door.call('POST', 'workflows/command', {'project': project, 'action': 'create', 'name': name, 'graph': graph,
                                                   'request': hashlib.sha256(f'{key}-workflow'.encode()).hexdigest()[:32]})
    workflow = made.get('workflow') or made
    op = door.operation({'tool': 'workflow', 'requestKey': f'{key}-run', 'args': {
        'study': study, 'session': project, 'workflow': workflow['id'], 'revision': workflow['revision'],
        'inputs': inputs, 'bindings': bindings}}, f'run {name}')
    return workflow, op


def download(door, project, ref, path):
    """Stream a retained result to disk, hashing as it arrives (packages are gigabytes)."""
    request = urllib.request.Request(door.url + f"artifacts/{ref['id']}/bytes?session={project}&revision={ref['revision']}",
                                     headers={'Hooke-Contract': '1', 'Authorization': 'Bearer ' + door.token})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=600) as response, open(path, 'wb') as out:
        while chunk := response.read(1 << 22):
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def outputs(op):
    outcome = op.get('outcome') or {}
    attempts = (outcome.get('workflow') or op.get('workflow') or {}).get('attempts') or outcome.get('attempts') or []
    if not attempts:
        raise SystemExit('No workflow attempts in the operation: ' + json.dumps(op)[:3000])
    return {a['node']: a['output'] for a in attempts if a.get('status') == 'completed' and a.get('output')}


def entry(args):
    door = Door(args.door, Path(args.token_file).read_text().strip())
    key = args.key or 'vcc-entry-' + secrets.token_hex(4)
    names = ['screen-effects', 'transfer-factors', 'vcc-count-emission', 'vcc-entry-check', 'vcc-package']
    gens = generations(door, names)
    project = door.operation({'tool': 'project.create', 'project': 'new', 'requestKey': f'{key}-project',
                              'args': {'name': 'VCC 2026 C3 reproduction'}}, 'create Project')['outcome']['project']['session']
    refs = imported(door, project, args.data, ENTRY_FILES, key)
    study = door.call('POST', 'studies', {'session': project, 'requestKey': f'{key}-study', 'title': 'Reproduce VCC 2026 C3',
                                          'objective': 'Rebuild the submitted C3 entry from the public K562 screen inside Hooke',
                                          'criteria': 'Factors and package byte-identical to the submitted ones',
                                          'budgetUsd': 0, 'inputs': [{k: refs['screen'][k] for k in ('id', 'revision', 'digest')}]})['study']['id']
    started = time.time()
    workflow, op = run_workflow(door, project, study, 'VCC 2026 entry (C3)', entry_graph(gens), [refs['screen']],
                                {k: v for k, v in refs.items()}, key)
    produced = outputs(op)
    record = {'project': project, 'study': study, 'workflow': workflow, 'operation': op['id'], 'generations': gens,
              'inputs': refs, 'outputs': produced, 'seconds': time.time() - started}
    folder = Path(args.record).parent
    for node_id, filename in (('factors', 'factors-c3.npz'), ('check', 'entry-check.json')):
        record[f'{node_id}Sha256'] = download(door, project, produced[node_id], folder / filename)
    if not args.skip_package_download:
        record['packageSha256'] = download(door, project, produced['package'], folder / 'entry-c3.vcc')
    record['matches'] = {'factors': record['factorsSha256'] == args.expect_factors if args.expect_factors else None,
                         'package': record.get('packageSha256') == args.expect_package if args.expect_package else None}
    Path(args.record).write_text(json.dumps(record, indent=1))
    print(json.dumps(record['matches']), record.get('factorsSha256'), record.get('packageSha256'))


def gate(args):
    door = Door(args.door, Path(args.token_file).read_text().strip())
    key = args.key or 'vcc-gate-' + secrets.token_hex(4)
    gens = generations(door, ['vcc-fold-score', 'frozen-gate'])
    rule = imported(door, args.project, args.data, {'rule': 'rule.json'}, key)['rule']
    frozen = door.call('POST', f'studies/{args.study}/rules', {'session': args.project, 'requestKey': f'{key}-freeze',
                                                                 'name': json.loads((Path(args.data) / 'rule.json').read_text())['name'],
                                                                 'artifact': rule})
    print('frozen', frozen['study']['frozenRules'][-1]['at'])
    refs = imported(door, args.project, args.data, GATE_FILES, key)
    workflow, op = run_workflow(door, args.project, args.study, 'VCC fold decision under a frozen rule',
                                gate_graph(gens), [refs['real']], {**refs, 'rule': rule}, key)
    produced = outputs(op)
    folder = Path(args.record).parent
    record = {'project': args.project, 'study': args.study, 'workflow': workflow, 'operation': op['id'],
              'generations': gens, 'rule': rule, 'outputs': produced}
    for node_id in ('score-baseline', 'score-challenger', 'decide'):
        download(door, args.project, produced[node_id], folder / f'{node_id}.json')
    record['decision'] = json.loads((folder / 'decide.json').read_text())
    Path(args.record).write_text(json.dumps(record, indent=1))
    print(json.dumps({k: record['decision'][k] for k in ('passed', 'decision', 'overall', 'subsets')}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('step', choices=['connect', 'entry', 'gate'])
    p.add_argument('--door', required=True)
    p.add_argument('--token-file', required=True)
    p.add_argument('--data')
    p.add_argument('--record')
    p.add_argument('--key')
    p.add_argument('--project')
    p.add_argument('--study')
    p.add_argument('--expect-factors')
    p.add_argument('--expect-package')
    p.add_argument('--skip-package-download', action='store_true')
    args = p.parse_args(argv)
    {'connect': connect, 'entry': entry, 'gate': gate}[args.step](args)


if __name__ == '__main__':
    sys.exit(main())
