"""Retained Study conversation, not an agent launcher or execution ledger.

A question pins the Study revision that the person saw. Connected contributors
read that exact evidence and publish an attributed answer through the same door.
"""
import time

from .efforts import KEY, fingerprint, summary, text
from .studies import service
from ..door.projects import access
from ..door.schema import check
from ..scientific_comparisons import exact, identity
from ..scientific_projects import Conflict, InvalidProject


def recipients(built, session):
    """Authorized means allowed to respond, not online or currently executing."""
    registry = getattr(built, 'agent_connections', None)
    return [{'actor': 'agent:' + c.id, 'label': c.label}
            for c in (registry.connections() if registry else [])
            if c.revoked_at is None and access(built).role(session, 'agent:' + c.id) in ('owner', 'contribute')]


def require_recipient(built, session, actor):
    row = next((r for r in recipients(built, session) if r['actor'] == actor), None)
    if row is None:
        raise InvalidProject('Choose a connected agent with contribute access to this Project')
    return row


class Questions:
    def __init__(self, studies):
        self.studies = studies
        self.efforts = studies.efforts

    def snapshot(self, study, session):
        saved = self.efforts.read(study, session)
        return {'questions': summary(saved).get('questions', []), 'basis': identity(saved)}

    def rows(self, study, session):
        return self.snapshot(study, session)['questions']

    def ask(self, study, session, *, request, question, basis, recipient, actor, label):
        text(request, 'Question request', 128)
        text(question, 'Question', 8000)
        check(basis, 'artifact-reference')
        if basis.get('id') != study:
            raise InvalidProject('Ask about the selected Study revision')
        captured = exact(self.studies.store, basis, session)
        if KEY not in captured.document.get('state', {}):
            raise InvalidProject('Select a saved Study')
        attribution = self.studies.attribution(actor, label)
        row = {'id': 'q_' + fingerprint({'actor': actor, 'request': request})[:32],
               'request': request, 'question': question, 'basis': basis, 'recipient': recipient,
               **attribution, 'at': time.time(), 'answer': None}
        check(row, 'study-question')
        def change(state):
            rows = state.setdefault('questions', [])
            old = next((q for q in rows if q['id'] == row['id']), None)
            if old:
                fields = ('request', 'question', 'basis', 'by')
                if any(old[k] != row[k] for k in fields) or old['recipient']['actor'] != row['recipient']['actor']:
                    raise Conflict('This question request already belongs to other words or context')
                return False
            if len(rows) >= 128:
                raise InvalidProject('This Study conversation is full; retain it before starting another')
            rows.append(row)
            return True
        saved = self.efforts._change(study, session, change)
        return next(q for q in saved.document['state'][KEY]['questions'] if q['id'] == row['id'])

    def context(self, study, session, question):
        row = next((q for q in self.rows(study, session) if q['id'] == question), None)
        if row is None:
            raise InvalidProject('Choose an existing Study question')
        saved = exact(self.studies.store, row['basis'], session)
        # Full substantive Study state, including claims, failures' operation locators,
        # exact inputs and prior answers. Files remain bounded explicit evidence reads.
        return {'question': row, 'study': summary(saved), 'untrusted': True,
                'next': 'Read cited artifact revisions and operation results through the connector; report omissions.'}

    def answer(self, study, session, question, *, statement, evidence, limitations, actor, label):
        row = next((q for q in self.rows(study, session) if q['id'] == question), None)
        if row is None or row['recipient']['actor'] != actor:
            raise InvalidProject('Only the selected connection may answer this question')
        answer = {'statement': statement, 'evidence': evidence, 'limitations': limitations,
                  **self.studies.attribution(actor, label), 'at': time.time()}
        check(answer, 'study-answer')
        for ref in evidence:
            exact(self.studies.store, ref, session)
        def change(state):
            target = next(q for q in state['questions'] if q['id'] == question)
            old = target['answer']
            if old:
                if any(old[k] != answer[k] for k in ('statement', 'evidence', 'limitations', 'by')):
                    raise Conflict('An answer is already retained; ask a follow-up instead of replacing it')
                return False
            target['answer'] = answer
            return True
        saved = self.efforts._change(study, session, change)
        return next(q for q in saved.document['state'][KEY]['questions'] if q['id'] == question)


def for_app(built):
    return Questions(service(built))
