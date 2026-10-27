"""Finite actual-provider qualification over a frozen private retained-input manifest.

Uses production PresentationJobs, process transport, validators and session writes.
No camera, acquisition, research rounds, auto retries or production-profile writes.
Run explicitly: python -m bench.presentation_qualification MANIFEST OUTPUT_DIRECTORY
The manifest contains three {id, source, sha256, kind, request, lessonOutput?} cases.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

from orchestrator.keys import load_keys
from orchestrator.options import Settings
from orchestrator.research import ResearchManager, Session
from orchestrator.research.content import lesson_from_html
from orchestrator.research.findings import reference, retain
from research_worker.presentation import PROVIDERS


async def run(manifest_path: Path, output: Path, *, validate_only=False):
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if len(manifest['cases']) != 3 or manifest['attempts'] != 3 or manifest['models'] != list(PROVIDERS):
        raise ValueError('qualification requires the frozen two-provider × three-case × three-attempt campaign')
    # Validate the entire frozen corpus before the first paid dispatch.
    for case in manifest['cases']:
        source = Path(case['source']).expanduser()
        if hashlib.sha256(source.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('a frozen retained source changed')
        saved = json.loads(source.read_bytes())
        if not saved.get('result'):
            raise ValueError('the retained input has no findings')
        if case['kind'] == 'lesson':
            lesson_path = source.parent / case['lessonOutput']
            if hashlib.sha256(lesson_path.read_bytes()).hexdigest() != case['lessonSha256']:
                raise ValueError('frozen lesson HTML changed')
            if not (lesson_from_html(lesson_path.read_text()) or {}).get('steps'):
                raise ValueError('legacy lesson has no readable retained steps')
    if validate_only:
        print('All three frozen retained inputs validated; no jobs or provider calls created.')
        return []
    output.mkdir(parents=True, exist_ok=True)
    frozen = output / 'manifest.json'
    if frozen.exists() and frozen.read_bytes() != raw:
        raise ValueError('this output directory belongs to a different frozen campaign')
    frozen.write_bytes(raw)
    load_keys()
    os.environ['MICROMIND_HOME'] = str((output / 'profile').resolve())
    records = []
    start = time.monotonic()
    for case in manifest['cases']:
        source = Path(case['source']).expanduser()
        if hashlib.sha256(source.read_bytes()).hexdigest() != case['sha256']:
            raise ValueError('a frozen retained source changed; no calls dispatched for this case')
        saved = json.loads(source.read_bytes())
        for model in manifest['models']:
            for attempt in range(1, 4):
                if time.monotonic() - start > 2400:
                    raise RuntimeError('40-minute campaign deadline reached; no further dispatch')
                key = f"{case['id']}-{model}-{attempt}".replace('.', '-')
                root = output / key
                day = source.parent.parent.name
                hub = SimpleNamespace(settings=Settings(presentation='off', research_cap=None))
                manager = ResearchManager(hub, root=root, today=lambda:day)
                rid = saved['rid']
                session = manager.get(rid)
                if session is None:
                    result = copy.deepcopy(saved['result'])
                    if case['kind'] == 'lesson':
                        lesson_path = source.parent / case['lessonOutput']
                        if hashlib.sha256(lesson_path.read_bytes()).hexdigest() != case['lessonSha256']:
                            raise ValueError('frozen lesson HTML changed')
                        result['lesson'] = lesson_from_html(lesson_path.read_text())
                        if not (result['lesson'] or {}).get('steps'):
                            raise ValueError('legacy lesson has no readable retained steps')
                    session = Session(rid=rid, n=saved['n'], brief=saved['brief'], deliverable=case['kind'],
                        effort='quick', engine=saved['engine'], purpose='research', label=saved.get('label') or '',
                        object_id=None, origin={}, day=day, status='done', result=result, root=root,
                        has_crop=saved.get('hasCrop', False))
                    result['findingRevision'] = reference(retain(session.finding_revisions, result, [], None))
                    manager.sessions[rid] = session
                    inputs = source.parent / 'inputs'
                    if inputs.exists():
                        shutil.copytree(inputs, root / day / rid / 'inputs', dirs_exist_ok=True)
                    manager._save(session)
                basis = session.result['findingRevision']
                before = time.monotonic()
                manager.presentations.begin(rid, key, version=basis['version'], digest=basis['digest'], model=model,
                                            change=case['request'], kind=case['kind'], expected_base=None)
                job = session.presentation_jobs[0]
                while job['state'] in ('queued', 'preparing'):
                    await asyncio.sleep(.1)
                row = {'case':case['id'], 'model':model, 'effort':'low' if model.startswith('gpt') else 'medium (provider default)',
                       'attempt':attempt, 'elapsedSeconds':round(time.monotonic() - before, 3),
                       'basis':basis, 'job':copy.deepcopy(job), 'output':str(root.resolve()),
                       'evidenceClass':'actual model through production persistence' if job.get('dispatchedAt') else 'provider availability'}
                records.append(row)
                temporary = output / 'results.json.tmp'
                temporary.write_text(json.dumps(records, indent=2))
                temporary.replace(output / 'results.json')
                print(json.dumps({key:row[key] for key in ('case','model','attempt','elapsedSeconds')}
                                 | {'state':job['state'], 'cost':job['cost'], 'estimated':job['estimatedCost']}), flush=True)
                manager.stop()
    return records


if __name__ == '__main__':
    asyncio.run(run(Path(sys.argv[1]), Path(sys.argv[2]), validate_only='--validate-only' in sys.argv[3:]))
