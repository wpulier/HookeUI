"""Explicit interactive-tool choice over an exact accepted scientific scene.

Choice records a new revision without executing code or copying scientific bytes.
Existing windows and private sessions keep their captured revisions. The normal
scientific host later admits the chosen generation and restores retained layers.
"""
import hashlib
import os

from gpu_worker import bundle_contract, manifests as catalog
from . import bundle_admission, channels, env_runner, envs, own_trials, prepared_bundles, readiness
from .scientific_project_review import _resolve, native_inputs
from .scientific_projects import InvalidProject, _json


def accepts(member):
    return (member.bridge == 'scientific-host-v1' and not member.reader
            and len(member.data_inputs) == 1 and member.data_inputs[0].type == 'project.scientific'
            and not member.params)


def scene(store, project, session, revision):
    if type(revision) is not int or revision < 1:
        raise InvalidProject('Choose an exact saved scientific document revision before choosing its tools')
    saved = store.read(project, session, revision=revision)
    if saved.document['kind'] == 'population-labels':
        spec = saved.document['state'].get('scientificView')
        if type(spec) is not dict or set(spec) != {'inputs'}:
            raise InvalidProject('Retained native data needs exact named inputs before choosing a tool')
        native_inputs(store, saved, spec['inputs'])
        return saved
    saved, _, sources = _resolve(store, project, session, revision)
    if saved.document['kind'] != 'scene' or not sources or saved.document['state']['scientificView'].get('files'):
        raise InvalidProject('Save native scientific outputs before choosing their next interactive tool')
    return saved


def comparison_availability(saved):
    available = saved.document['kind'] == 'scene'
    return {'available': available, 'reason': '' if available else
            'Choose an interactive tool first; compare alternatives from its saved scene.'}


def discover(store, project, session, revision, environ=os.environ):
    saved = scene(store, project, session, revision)
    rows = []
    for owner in {**catalog.manifests(), **catalog.mine(environ)}.values():
        if owner.runner != 'python-env' or not owner.members:
            continue
        try:
            selected = env_runner.in_use(owner, environ)
        except (OSError, ValueError):
            selected = None
        active = selected.m if selected is not None and bundle_admission.local_workflow(selected.m) else owner
        members = [member for member in bundle_contract.parse_members(list(active.members), active) if accepts(member)]
        if not members:
            continue
        generation = None
        reason = readiness.misfit(active) or ''
        if not reason and not channels.trusted(owner, environ):
            reason = 'Allow this catalog’s code in Models before choosing its tool'
        try:
            generation = envs.generation(owner, environ)
            if not (envs.root(owner, environ) / generation / envs.RECEIPT).is_file():
                reason = reason or 'Prepare this integration in Models, then check tools again'
        except (OSError, ValueError):
            reason = reason or 'This integration’s preparation is incomplete. Repair it in Models'
        if selected is not None and bundle_admission.local_workflow(selected.m):
            generation = selected.folder.name
            reason = readiness.misfit(active) or ('' if channels.trusted(owner, environ) else 'Allow this catalog’s code in Models')
            if own_trials.pending(selected.m, environ):
                reason = reason or 'Review and enable the observed local workflow in Models before choosing it'
        for member in members:
            rows.append({'key': bundle_contract.capability_key(owner.key, member.id), 'name': member.name,
                         'category': member.category, 'description': member.description, 'searchAliases': list(member.search_aliases),
                         'generation': generation, 'owner': owner.key, 'canReview': not bool(reason), 'reason': reason,
                         'scope': ('Observed local controls, Save and reopen; scientific accuracy remains unverified'
                                   if selected is not None and active is selected.m else
                                   'Local trial: use controls, Save tool and reopen, then review in Models; scientific results require verification')})
        if selected is not None:
            from .maintenance import workflow
            try:
                candidate, candidate_generation = workflow.target(owner, environ=environ)
                if (envs.root(owner, environ)/candidate_generation/envs.RECEIPT).is_file():
                    for member in bundle_contract.parse_members(list(candidate.members), candidate):
                        if accepts(member):
                            reason = readiness.misfit(candidate) or ('' if channels.trusted(owner, environ) else 'Allow this catalog’s code in Models')
                            rows.append({'key':bundle_contract.capability_key(owner.key,member.id), 'name':member.name,
                                'category':member.category,'description':member.description,'searchAliases':list(member.search_aliases),
                                'generation':candidate_generation,'owner':owner.key,'canReview':not bool(reason),'reason':reason,
                                'updateTrial':True,'scope':'Try this update on the saved input; use controls, Save tool and reopen, then review in Models. The active version stays selected.'})
            except (OSError, ValueError):
                pass  # a missing/stale/noninteractive update is not a trial choice
    return {'source': {'id': saved.id, 'session': session, 'revision': revision, 'digest': saved.digest}, 'tools': rows,
            'comparison': comparison_availability(saved)}


def bind(store, project, session, revision, key, generation, *, environ=os.environ, check=lambda: None, update_trial=False):
    check()
    saved = scene(store, project, session, revision)
    with prepared_bundles.prepared_member(key, generation, environ) as prepared:
        if not accepts(prepared.m):
            raise InvalidProject('Choose a declared interactive scientific-scene integration')
        owner_manifest = prepared.m.owner
        if update_trial:
            from .maintenance import workflow
            workflow.target(owner_manifest, generation, environ)
        elif bundle_admission.local_workflow(owner_manifest):
            selected = env_runner.in_use(owner_manifest, environ)
            proven = env_runner.generation(owner_manifest, generation, environ) is not None
            if (selected is not None and selected.folder.name != generation
                    or proven and (selected is None or own_trials.pending(selected.m, environ))):
                raise InvalidProject('The selected local tool or accepted workflow changed; review it in Models and choose it again')
        owner, member = bundle_contract.parse_capability_key(key)
        runtime = {'bundle': owner, 'generation': generation, 'operation': member}
        document = saved.document
        view = document['state']['scientificView']
        origin = {'format': 1, 'source': {'id': saved.id, 'session': session, 'revision': revision, 'digest': saved.digest},
                  'capability': key, 'generation': generation, 'name': prepared.m.name}
        same_runtime = view.get('runtime') == runtime
        document['kind'] = 'scene'
        view['runtime'] = runtime
        # A tool's codec belongs to its author. The original revision keeps it;
        # another integration receives retained layers, never foreign tool state.
        if not same_runtime:
            view.pop('tool', None)
        view.pop('initializeTool', None)
        view.pop('recipeTool', None)
        if 'layers' in view and 'tool' not in view:
            view['initializeTool'] = 1
        if update_trial:
            origin['updateTrial'] = True
        document['state']['scientificToolChoice'] = origin
        if runtime not in document['runtimes']:
            document['runtimes'].append(runtime)
        transaction = hashlib.sha256(_json(origin)).hexdigest()
        check()
        if update_trial:
            workflow.target(owner_manifest, generation, environ)
        return store.save(project, session, document, expected_revision=revision, transaction=transaction, check=check)


def fork(store, source, key, generation, token, *, recipe=None, environ=os.environ, check=lambda: None):
    """One deterministic private working scene for a comparison or recipe step."""
    from .scientific_copies import PreparedArray
    from .scientific_projects import Conflict, DIGEST
    from env_worker.scientific_tool_document import document as parameters
    if type(token) is not str or not DIGEST.fullmatch(token):
        raise InvalidProject('A working scene needs its stable transaction identity')
    if recipe is not None:
        recipe = parameters(recipe)
    source = scene(store, source.id, source.session, source.revision)
    with prepared_bundles.prepared_member(key, generation, environ) as prepared:
        if not accepts(prepared.m):
            raise InvalidProject('This operation no longer declares an interactive scientific scene')
    ref = next(iter(source.document['state']['scientificView']['inputs'].values()))
    frozen = PreparedArray(store, source.id, source.session, source.revision, ref, ref)
    candidate = store.copy_candidate(source, frozen)
    candidate.identity, candidate.transaction = 'p'+token[:32], token
    try:
        check()
        if store._folder(candidate.identity).exists():
            copied = store.read(candidate.identity, source.session, revision=1)
            lineage = copied.document['state'].get('hookeCopy', {})
            entries = lineage.get('history', [lineage])
            expected = {'id':source.id,'session':source.session,'revision':source.revision,'digest':source.digest}
            if copied.transaction != token or not entries or entries[-1].get('source') != expected:
                raise Conflict('This working destination belongs to other work; nothing was replaced')
        else:
            def stopped():
                check()
                return False
            copied = candidate.publish(stopped)
        chosen = bind(store, copied.id, copied.session, 1, key, generation, environ=environ, check=check)
        if recipe is None:
            return chosen
        doc = chosen.document
        view = doc['state']['scientificView']
        if 'layers' not in view:
            raise InvalidProject('Retain native layers before replaying this tool configuration')
        view.pop('tool', None)
        view['initializeTool'], view['recipeTool'] = 1, recipe
        transaction = hashlib.sha256(_json({'fork':token,'recipe':recipe})).hexdigest()
        return store.save(chosen.id, chosen.session, doc, expected_revision=chosen.revision, transaction=transaction, check=check)
    finally:
        candidate.close()
