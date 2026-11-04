"""Two reviewed alternatives reuse ordinary Study workflow admission and receipts."""
from ..scientific_comparisons import identity
from ..scientific_projects import InvalidProject
from ..door.schema import check


def method_signature(graph):
    """Compare wiring and runnable settings independently of node labels and ordering."""
    from functools import lru_cache
    from .efforts import fingerprint
    nodes = {node['id']: node for node in graph['nodes']}

    @lru_cache(None)
    def signature(key):
        if key not in nodes:
            return key  # A named or primary workflow input.
        node = nodes[key]
        settings = dict(node['settings'])
        if 'bindings' in settings:
            settings['bindings'] = {port: signature(source) for port, source in settings['bindings'].items()}
        return fingerprint({'kind': node['kind'], 'settings': settings,
                            'needs': sorted(signature(source) for source in node['needs'])})

    return (graph['input'], sorted(signature(key) for key in nodes))


def validate_spec(studies, plan, session):
    spec = plan['comparison']
    if plan['actorMode'] == 'local-researcher':
        raise InvalidProject('Choose manual execution or a connected researcher with enforceable limits')
    if plan['limits']['experiments'] != 2:
        raise InvalidProject('A method comparison must allow exactly two candidate experiments')
    if spec['baseline'] == spec['challenger']:
        raise InvalidProject('Choose two distinct frozen methods')
    for value in (spec['input'], *spec['bindings'].values()):
        check(value, 'artifact-reference')
        if value not in plan['materials'] or value['session'] != session:
            raise InvalidProject('Select each comparison input from the exact materials in this plan')
    graphs = []
    for role in ('baseline', 'challenger'):
        ref = spec[role]
        saved = studies.workflows.workflows.definition(ref['id'], ref['revision'])
        if identity(saved) != ref:
            raise InvalidProject(f'The {role} workflow revision does not match the selected version')
        graph = saved.document['state']['workflow']['graph']
        needed = {d[6:] for n in graph['nodes'] for d in n['needs'] if d.startswith('input:')}
        if needed != set(spec['bindings']):
            raise InvalidProject(f'Choose the declared named inputs for the {role} workflow')
        for node in graph['nodes']:
            if node['kind'] == 'researcher' or 'execution' in node['settings']:
                raise InvalidProject('Choose a local workflow without a nested researcher or remote execution profile')
        graphs.append(graph)
    if method_signature(graphs[0]) == method_signature(graphs[1]):
        raise InvalidProject('Choose methods with different workflow steps or settings')


def candidate_role(spec, params):
    if params.get('repeat') or params['inputs'] != [spec['input']] or params['bindings'] != spec['bindings']:
        raise InvalidProject('Use the same reviewed inputs and bindings, once per candidate')
    for role in ('baseline', 'challenger'):
        ref = spec[role]
        if (params['workflow'], params['revision']) == (ref['id'], ref['revision']):
            return role
    raise InvalidProject('Choose one of the two frozen methods in the reviewed comparison')


async def preflight(studies, study, session, basis, actor):
    import asyncio
    from .efforts import KEY
    from ..scientific_comparisons import exact
    from ..scientific_projects import Conflict
    current = await asyncio.to_thread(studies.efforts.read, study, session)
    reviewed = await asyncio.to_thread(exact, studies.store, basis, session)
    if basis['id'] != study:
        raise InvalidProject('Select the current Study')
    state = current.document['state'][KEY]
    plan = state.get('researchPlan')
    if state['status'] != 'active' or not plan or plan.get('status') != 'reviewed' or 'comparison' not in plan:
        raise InvalidProject('Review this comparison before starting')
    if plan != reviewed.document['state'][KEY].get('researchPlan'):
        raise Conflict('The comparison changed. Review the current plan before starting')
    await asyncio.to_thread(validate_spec, studies, plan, session)
    spec = plan['comparison']
    for role in ('baseline', 'challenger'):
        method = spec[role]
        readiness = (await studies.workflows.command({'action': 'validate', 'session': session,
            'workflow': method['id'], 'revision': method['revision'], 'inputs': [spec['input']],
            'bindings': spec['bindings']}, actor=actor))['readiness']
        if not readiness['available']:
            reasons = [step['reason'] for step in readiness['steps'] if step.get('reason')]
            raise InvalidProject(f'{role.capitalize()} unavailable. ' + ' '.join(reasons))
    return plan


def check_report(report, evidence):
    """Validate an attributed interpretation and its links. Score reports remain authoritative."""
    check(report, 'method-comparison-report')
    names = [row['name'] for row in report['metrics']]
    if len(set(names)) != len(names):
        raise InvalidProject('Name each comparison metric once')
    if report['baseline'] == report['challenger']:
        raise InvalidProject('Each candidate needs its own prediction and score evidence')
    for field in ('prediction', 'score'):
        if report['baseline'][field] == report['challenger'][field]:
            raise InvalidProject('Select a separate retained ' + field + ' for each method')
        for role in ('baseline', 'challenger'):
            if report[role][field] not in evidence:
                raise InvalidProject('Attach the exact prediction and score for both methods')


def describe_methods(studies, plan, study, session):
    from .efforts import KEY
    state = studies.efforts.read(study, session).document['state'][KEY]
    from .efforts import fingerprint
    result = {'available': True, 'candidates': {}, 'reviewedPlan': fingerprint(plan)}
    for role in ('baseline', 'challenger'):
        pin = plan['comparison'][role]
        definition = studies.workflows.workflows.definition(pin['id'], pin['revision'])
        row = {'name': definition.document['title'], 'outputs': [], 'status': 'planned'}
        for run in state.get('experiments', {}).values():
            spec = run['specification']
            if (spec['workflow'] != pin['id'] or spec['revision'] != pin['revision']
                    or spec['inputs'] != [plan['comparison']['input']]
                    or spec.get('bindings', {}) != plan['comparison']['bindings'] or spec.get('repeat')):
                continue
            observed = studies.workflows.observed_status(run['execution']['id'], session)
            row.update(status=observed['status'], operation=run['operation'], author=run['byLabel'])
            row['outputs'] = [{'step': a['node'], 'reference': a['output']} for a in observed['attempts']
                              if a.get('output') and a['status'] == 'completed']
            break
        result['candidates'][role] = row
    return result


def verify_score_pair(report, sources, hashes):
    from scientific_compute.paired_scores import compare
    try:
        metrics = compare(sources['baseline'], sources['challenger'], [hashes['baseline'], hashes['challenger']])
    except (TypeError, KeyError, ValueError) as e:
        raise InvalidProject('Incompatible score evidence. ' + str(e)) from e
    if report.get('metrics') is not None and metrics != report['metrics']:
        raise InvalidProject('The proposed measures differ from the retained scoring reports')
    return metrics


def verify_report_files(store, session, comparison):
    import json
    from ..scientific_comparisons import exact
    from ..scientific_files import CHUNK_BYTES
    scores, predictions = {}, {}
    for role in ('baseline', 'challenger'):
        arm = comparison[role]
        pred = exact(store, arm['prediction'], session)
        score = exact(store, arm['score'], session)
        prediction = pred.document['state'].get('sourceFile', {})
        file = score.document['state'].get('sourceFile', {})
        if not prediction.get('sha256') or not file or file['size'] > CHUNK_BYTES:
            raise InvalidProject('Choose a retained prediction and a bounded scoring report')
        source = store.file_source(score.id, session, score.revision, file['reference'])
        scores[role] = json.loads(source.read(0, source.size))
        predictions[role] = prediction['sha256']
    return verify_score_pair(comparison, scores, predictions)
