"""Independent presentation jobs; durable state stays in each research session."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time

from research_worker.presentation import DEADLINE_SECONDS, MAX_INPUT_CHARS, PROVIDERS, choose, validate

from .. import versions
from .ipc import ProcessChannel
from .policy import ResearchRefused

TERMINAL = {'ready', 'failed', 'unavailable', 'over_budget', 'cancelled', 'interrupted', 'conflict'}
# An automatic presentation (a researcher's findings ready, setting "auto") does not ask a provider that refused one for
# want of credit this long ago: the standard layout stays, said once, not as a failure per researcher (live 2026-10-25:
# every researcher ended with "OpenAI has no credits left" in red). A presentation the user asks for still asks it.
CREDIT_HOLD_S = 1800.0


def content_kind(session, output):
    """Format alone cannot choose a lesson producer; legacy pages use retained content. With no output named, the
    researcher's own deliverable: its lesson when it made one (2026-10-11: "make it prettier" with the lesson on
    screen made a report presentation instead), else its report."""
    if not output:
        lesson = next((o for o in reversed(session.current_outputs()) if o.get('kind') == 'html'
                       and content_kind(session, o) == 'lesson'), None)
        return 'lesson' if lesson is not None else 'report'
    if output.get('contentKind'):
        return output['contentKind']
    content = session.content_of(output)
    return 'lesson' if isinstance(content, dict) and content.get('kind') == 'lesson' else 'report'


def retained_document(session, output):
    """Read a saved presentation against its original science; never substitute current findings."""
    basis = output.get('findingRevision') or {}
    finding = next((row for row in session.finding_revisions
                    if row['version'] == basis.get('version') and row['digest'] == basis.get('digest')), None)
    if finding is None:
        raise ValueError('the presentation’s exact findings basis is unavailable')
    result = copy.deepcopy(finding['result'])
    if result.get('refs'):
        result['refs'] = [{**ref, **({'url': f"{session.base}/{ref['path']}"} if ref.get('path') else {})} for ref in result['refs']]
    return {**result, 'findingRevision': copy.deepcopy(basis),
            'presentation': {'id': output['id'], 'version': output.get('version', 1), 'model': output.get('model'),
                             'plan': validate(output.get('content'), finding['result'])}}


class PresentationJobs:
    def __init__(self, manager):
        self.manager = manager
        self.worker = None
        self.starting = None
        self.tasks = {}
        self.receipts_due = set()  # jobs this process dispatched whose receipt may still come (never after a restart)
        self.origins = {}
        self.slots = asyncio.Semaphore(2)
        self.refused = {}  # provider -> (monotonic time, its service's name): its last refusal for want of credit

    def begin(self, rid: str, request_id: str, *, version: int, digest: str, model: str | None = None,
              change: str = '', expected_base: str | None = None, kind: str = 'report', notify: bool = False,
              delivery=None, automatic: bool = False) -> dict:
        """A presentation job. notify: its end is said where it was asked for (announcer.presentation_finished), and
        with the request's delivery (a restyle the user asked for), its new version is shown in that browser's viewer
        when the viewer shows the version it replaces. automatic: started by findings being ready, not asked for; it
        is not dispatched to a provider that refused for want of credit within CREDIT_HOLD_S (the standard layout
        stays; the job says why, with no notice)."""
        manager = self.manager
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', request_id):
            raise ResearchRefused('a durable presentation request identity is required', next=None)  # no next: the transport supplies this identity; it is not user-entered content
        session = manager.get(rid)
        if session is None:
            raise ResearchRefused('there is no such researcher', next='open a researcher of this session to change its presentation')
        manager._this_session_only(session)  # only this session's work is continued (ADR 0017)
        finding = next((row for row in session.finding_revisions if row['version'] == version and row['digest'] == digest), None)
        if finding is None:
            raise ResearchRefused('the exact retained finding is unavailable', next='reopen its saved findings to inspect the available versions')
        if kind not in ('report', 'lesson'):
            raise ResearchRefused('this producer cannot create that kind of artifact', next='choose a report or a retained lesson')
        lesson = None
        if kind == 'lesson':
            source = session.output(expected_base) if expected_base else None
            lesson = (finding['result'].get('lesson') or
                      (session.content_of(source) if source and source.get('kind') == 'html' else None))
            if not isinstance(lesson, dict) or not lesson.get('steps'):
                raise ResearchRefused('no retained lesson content is available', next='ask the researcher to prepare the lesson content first')
        if len(json.dumps(finding['result'], ensure_ascii=False)) > MAX_INPUT_CHARS:
            raise ResearchRefused('this finding exceeds the producer input bound; its full standard layout remains available', next='read the complete finding in the standard layout')
        payload = {'version': version, 'digest': digest, 'model': model, 'change': change, 'expectedBase': expected_base, 'kind':kind}
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        existing = next((job for job in session.presentation_jobs if job['id'] == request_id), None)
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise ResearchRefused('presentation request identity was reused for different content', next='inspect the original presentation request before submitting a new one')
            return copy.deepcopy(existing)
        current = self.current(session, kind)
        if expected_base != (current or {}).get('id'):
            raise ResearchRefused('the presentation changed; inspect its current version before revising', next='select the current presentation before revising')
        manager._portable_execution_ready(session.sitting)
        # `day`: the calendar day it is charged to (ledger.py), whichever date folder its researcher is in.
        job = {'id': request_id, 'fingerprint': fingerprint, 'state': 'queued', 'created': time.time(), 'day': manager.today(),
               'contentKind':kind, **({'lesson':copy.deepcopy(lesson)} if lesson else {}),
               # The slide's own picture goes in the lesson when the researcher kept one (write_output inlines it).
               **({'picture':True} if kind == 'lesson' and self.photo(session) is not None else {}),
               'findingRevision': {'version': version, 'digest': digest, 'complete': finding['complete']},
               'expectedBase': expected_base, 'change': change, 'cost': 0.0, 'estimatedCost': 0.0}
        try:
            provider = choose(getattr(manager.hub.settings, 'presentation', 'off'), finding['result'], model, kind=kind)
        except ValueError as exc:
            job.update(state='unavailable', error=str(exc))
            provider = None
        held = self.refused.get(provider) if provider and automatic else None
        if held is not None and time.monotonic() - held[0] < CREDIT_HOLD_S:
            job.update(state='unavailable', error=f'{held[1]} has no credits left.')
            provider = None
        if provider is None and job['state'] == 'queued':
            job.update(state='unavailable', error='Standard layout only is selected; no generation was dispatched')
        if provider:
            job.update(model=provider, reservation=PROVIDERS[provider]['cap'], deadlineSeconds=DEADLINE_SECONDS)
            try:
                manager.spending.check(manager.hub.settings.research_cap, job['reservation'])
            except ResearchRefused as exc:
                job.update(state='over_budget', error=str(exc))
        session.presentation_jobs.append(job)
        try:
            manager._save(session)
        except OSError:
            session.presentation_jobs.remove(job)
            raise
        if job['state'] == 'queued':
            if notify:
                view = session.completed_view()
                if delivery is not None:
                    view._delivery = delivery
                self.origins[self.key(session, job)] = view
            manager.spending.reserve(self.key(session, job), job['reservation'])
            self.tasks[self.key(session, job)] = asyncio.create_task(self._run(session, job, copy.deepcopy(finding['result'])))
        manager._changed(session)
        return copy.deepcopy(job)

    @staticmethod
    def key(session, job):
        return f"{session.rid}#presentation#{job['id']}"

    @staticmethod
    def current(session, kind='report'):
        return next((output for output in reversed(session.current_outputs())
                     if (output.get('kind') == 'html' and content_kind(session, output) == 'lesson' if kind == 'lesson'
                         else output.get('kind') == 'presentation' or output.get('kind') == 'html' and output.get('contentKind') == 'report')), None)

    async def _ensure_worker(self):
        if self.worker is not None and self.worker.alive:
            return
        if self.starting is None:
            from research_worker.presentation import serve
            async def no_tools(_):
                return {'ok': False, 'error': 'presentation producers have no tools'}
            self.worker = ProcessChannel(serve, 'research-presentations', self.event, no_tools, self.gone)
            self.starting = asyncio.create_task(self.worker.start())
        try:
            await self.starting
        finally:
            self.starting = None

    async def _run(self, session, job, result):
        try:
            async with self.slots:
                await self._ensure_worker()
                if job['state'] != 'queued':
                    return
                job.update(state='preparing', dispatchedAt=time.time())
                self.receipts_due.add(self.key(session, job))
                try:
                    self.manager._save(session)
                except OSError:
                    job.pop('dispatchedAt', None)
                    raise
                self.manager._changed(session)
                base = session.output(job['expectedBase']) if job.get('expectedBase') else None
                current = (base or {}).get('content') if (base or {}).get('kind') == 'presentation' else None
                reply = await self.worker.call('produce', rid=session.rid, job=job['id'], model=job['model'], result=result,
                                              change=job['change'], lesson=job.get('lesson'), picture=job.get('picture', False),
                                              current=current)
                if isinstance(reply, dict) and reply.get('ok') is False:
                    raise RuntimeError(reply.get('error') or 'presentation worker refused dispatch')
                deadline = time.monotonic() + DEADLINE_SECONDS
                while job['state'] == 'preparing' and time.monotonic() < deadline:
                    await asyncio.sleep(.25)
                if job['state'] == 'preparing':
                    await self.cancel(session.rid, job['id'], state='failed', why='Presentation deadline reached; provider outcome unknown')
        except asyncio.CancelledError:
            if job['state'] not in TERMINAL:
                self.fail(session, job, 'interrupted', 'Presentation interrupted; provider outcome may be unknown')
            raise
        except Exception as exc:
            self.fail(session, job, 'failed', str(exc))
        finally:
            self.tasks.pop(self.key(session, job), None)

    def receipt_due(self, session, job) -> bool:
        """Whether this job's provider receipt may still come: this process dispatched it, its worker still runs and
        no receipt came yet. A job restored after a restart never gets one (its worker is gone), whatever it says."""
        return self.key(session, job) in self.receipts_due and self.worker is not None and bool(self.worker.alive)

    def fail(self, session, job, state, error, *, status=None, credit=None):
        """A failed job, said in words a person reads: a provider's refusal for want of credit (S9) in fixed words, its
        raw text kept apart (`errorDetail`, for Activity). A dispatched job with no receipt is counted at its
        reservation, as maybe billed, except an HTTP 429 or a refusal for want of credit, which the provider refused
        before any work (engines.py's rule: `billed = why != 'HTTP 429'`; ledger.never_billed)."""
        words = f'{credit} has no credits left.' if credit else error  # the studio's line says "Presentation not made"
        if credit and job.get('model'):
            self.refused[job['model']] = (time.monotonic(), credit)
        job.update(state=state, error=words, finished=time.time())
        if words != error:
            job['errorDetail'] = str(error)[:2000]
        if status is not None:
            job['providerStatus'] = status
        if job.get('dispatchedAt') and not job.get('receipt') and status != 429 and not credit:
            job['estimatedCost'] = job.get('reservation', 0.0)
            job['outcomeUnknown'] = True
        self.save_outcome(session, job)
        self.manager.spending.release(self.key(session, job))
        self.manager._changed(session)
        self.notify(session, job)

    def save_outcome(self, session, job):
        """A failed disk write is visible, never a successfully saved terminal state."""
        try:
            job.pop('persistenceError', None)
            job.pop('recovery', None)
            self.manager._save(session)
            return True
        except OSError as exc:
            job['persistenceError'] = str(exc)
            job['recovery'] = 'Restore storage, then save this outcome again; do not regenerate'
            return False

    async def cancel(self, rid, job_id, *, state='cancelled', why='Presentation stopped; findings are unchanged'):
        session = self.manager.get(rid)
        job = next((row for row in session.presentation_jobs if row['id'] == job_id), None) if session else None
        if job is None or job['state'] in TERMINAL:
            return False
        self.fail(session, job, state, why)
        if self.worker is not None and self.worker.alive and job.get('dispatchedAt'):
            await self.worker.call('cancel', rid=rid, job=job_id)
        return True

    def event(self, message):
        session = self.manager.get(message.get('rid'))
        job = next((row for row in session.presentation_jobs if row['id'] == message.get('job')), None) if session else None
        if job is None:
            return
        receipt = message.get('receipt')
        if receipt:
            self.receipts_due.discard(self.key(session, job))
        if receipt and (not job.get('receipt') or job['receipt'].get('usd') is None):
            job['receipt'] = copy.deepcopy(receipt)
            if isinstance(receipt.get('usd'), (int, float)) and receipt['usd'] >= 0:
                job.update(cost=receipt['usd'], estimatedCost=0.0, outcomeUnknown=False)
            else:
                job.update(estimatedCost=job.get('reservation', 0.0), outcomeUnknown=True)
            from research_worker import api_usage
            api_usage.record(job['model'], receipt.get('usd'), area='Presentations',
                             key=f"presentation:{session.day}:{session.rid}:{job['id']}", day=job.get('day') or session.day,
                             basis='Provider usage at configured list rates' if receipt.get('usd') is not None else 'Provider usage unavailable')
        if job['state'] in TERMINAL:
            # A late receipt reconciles its original charge once, without reviving output or screen effects.
            self.save_outcome(session, job)
            self.manager._changed(session)
            return
        plan = message.get('plan') or {}
        if message.get('event') == 'presentation-failed' or plan.get('error'):
            credit = message.get('credit') if isinstance(message.get('credit'), str) else None
            status = message.get('status') if isinstance(message.get('status'), int) else None
            self.fail(session, job, 'failed', message.get('error') or plan['error'], status=status, credit=credit)
            return
        finding = next((row for row in session.finding_revisions if row['digest'] == job['findingRevision']['digest']), None)
        if finding is None:  # the findings it was made from are not retained: a failed presentation, said so
            self.fail(session, job, 'failed', 'the findings this presentation was made from are no longer retained')
            return
        output = None
        try:
            kind = job.get('contentKind', 'report')
            if kind == 'lesson':
                from research_worker.presentation_lesson import validate as validate_lesson
                plan = validate_lesson(plan, finding['result'], job['lesson'], picture=job.get('picture', False))
                content = copy.deepcopy(job['lesson'])
            else:
                content = validate(plan, finding['result'])
            current = self.current(session, kind)
            conflict = (current or {}).get('id') != job['expectedBase']
            oid = versions.next_id(session.outputs)
            relative = f'outputs/{oid}-presentation.json'
            base = next((row for row in session.outputs if row['id'] == job['expectedBase']), {})
            output = {'id': oid, 'kind': 'presentation', 'format': 'blocks', 'contentKind': 'report', 'path': relative,
                      'caption': finding['result'].get('title') or 'Research presentation', 'content': content,
                      'findingRevision': copy.deepcopy(job['findingRevision']), 'model': job['model'],
                      'generated': True, 'altered': False, 'version': base.get('version', 0) + 1,
                      'replaces': job['expectedBase'], 'change': job['change'], 'conflict': conflict, 'job': job['id']}
            if kind == 'lesson':
                output.update(kind='html', format='html', contentKind='lesson', path=f'outputs/{oid}-lesson.html',
                              document=plan['html'], evidenceRefs=plan['evidenceRefs'])
            self.write_output(session, output)
            session.outputs.append({key:value for key,value in output.items() if key != 'document'})
            job.update(state='conflict' if conflict else 'ready', output=oid, finished=time.time())
            self.refused.pop(job.get('model'), None)  # it answered: its credit is back
            self.manager._save(session)
        except (OSError, ValueError, KeyError) as exc:
            if output is not None:
                session.outputs[:] = [row for row in session.outputs if row.get('id') != output['id']]
            if output is not None:
                job['pendingOutput'] = output
            job.pop('output', None)
            self.fail(session, job, 'failed', f'Could not retain presentation: {exc}')
            return
        self.manager.spending.release(self.key(session, job))
        self.manager._changed(session)  # never on_done: an optional report cannot reopen or rearrange a browser
        self.notify(session, job)

    def notify(self, session, job):
        if job.get('persistenceError') or job.get('pendingOutput'):
            return
        original = self.origins.pop(self.key(session, job), None)
        if original is None:
            return
        output = session.output(job.get('output'))
        if output:
            original.outputs.append(copy.deepcopy(output))
        for hook in self.manager.on_presentation:
            try:
                hook(original, copy.deepcopy(job))
            except Exception:
                import logging
                logging.getLogger(__name__).exception('presentation listener failed; output remains retained')

    def photo(self, session):
        """The researcher's kept picture of the slide (its close-up, else the whole view), or None."""
        directory = self.manager.directory(session) / 'inputs'
        found = directory / ('crop.jpg' if session.has_crop else 'frame.jpg')
        return found if found.exists() else None

    def write_output(self, session, output):
        path = self.manager.directory(session, write=True) / output['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        if output.get('format') == 'html':
            from research_worker.work import inline_photo
            photo = self.photo(session)
            html = inline_photo(output['document'], photo.read_bytes() if photo is not None else None)
            tmp.write_text(html)
        else:
            tmp.write_text(json.dumps({'plan': output['content'], 'findingRevision': output['findingRevision']}))
        os.replace(tmp, path)

    def recover(self, rid, job_id):
        """Retry only the storage boundary; never dispatch or bill a producer again."""
        session = self.manager.get(rid)
        job = next((row for row in session.presentation_jobs if row['id'] == job_id), None) if session else None
        if job is None or session.sitting != self.manager.session():
            raise ResearchRefused('this presentation job is unavailable for saving', next='open the researcher to inspect its saved presentation jobs')
        pending = job.get('pendingOutput')
        if pending is None:
            saved = self.save_outcome(session, job)
            self.manager._changed(session)
            return saved
        output = copy.deepcopy(pending)
        # Another completed job may have claimed the candidate ID while storage was unavailable.
        output['id'] = versions.next_id(session.outputs)
        output['path'] = f"outputs/{output['id']}-" + ('lesson.html' if output.get('format') == 'html' else 'presentation.json')
        output['conflict'] = (self.current(session, job.get('contentKind', 'report')) or {}).get('id') != job['expectedBase']
        try:
            self.write_output(session, output)
            session.outputs.append({key:value for key,value in output.items() if key != 'document'})
            job.update(state='conflict' if output['conflict'] else 'ready', output=output['id'])
            job.pop('pendingOutput', None)
            job.pop('persistenceError', None)
            job.pop('error', None)
            job.pop('recovery', None)
            self.manager._save(session)
        except OSError as exc:
            session.outputs[:] = [row for row in session.outputs if row.get('id') != output['id']]
            job.pop('output', None)
            job['pendingOutput'] = pending
            self.fail(session, job, 'failed', f'Could not save presentation: {exc}')
        self.manager._changed(session)
        if not job.get('pendingOutput') and not job.get('persistenceError'):
            self.notify(session, job)
        return not job.get('pendingOutput') and not job.get('persistenceError')

    def gone(self):
        for session in self.manager.sessions.values():
            for job in session.presentation_jobs:
                if job['state'] in ('queued', 'preparing'):
                    self.fail(session, job, 'interrupted', 'Presentation worker stopped; no automatic regeneration')

    def stop(self):
        for task in list(self.tasks.values()):
            task.cancel()
        if self.worker is not None:
            self.worker.stop()
