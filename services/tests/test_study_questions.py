"""Real immutable Study storage; synthetic callers, not provider qualification."""
import pytest
from orchestrator.research.study_questions import Questions
from orchestrator.scientific_comparisons import identity
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_study_workflows import setup, A, B


def test_question_pins_substantive_context_and_answer_survives_reopen(tmp_path):
    s, _, study, image, _ = setup(tmp_path)
    q = Questions(s)
    fields = dict(request='question1', question='what does it mean?', basis=identity(study),
                  recipient={'actor':B,'label':'Second researcher'}, actor='studio', label='You')
    first = q.ask(study.id, 's1', **fields)
    assert q.ask(study.id, 's1', **fields) == first
    # New conclusions cannot silently change the question's captured context.
    s.publish(study.id, 's1', request='later', statement='Later finding', evidence=[{'artifact':identity(image),'relation':'supports'}], depends_on=[],
              actor=A, label='First researcher')
    captured = Questions(s).context(study.id, 's1', first['id'])
    assert captured['study']['revision'] == study.revision
    assert captured['study']['objective'] == 'Find a useful result'
    assert not captured['study'].get('claims')
    fields['question'] = 'different'
    with pytest.raises(Conflict): q.ask(study.id, 's1', **fields)
    reply = dict(statement='The retained input shows the measured result.', evidence=[identity(image)],
                 limitations='Synthetic', actor=B, label='Second researcher')
    answered = q.answer(study.id, 's1', first['id'], **reply)
    assert Questions(s).rows(study.id, 's1')[0] == answered
    assert q.answer(study.id, 's1', first['id'], **reply) == answered
    assert answered['answer']['by'] == B
    with pytest.raises(Conflict): q.answer(study.id, 's1', first['id'], **{**reply,'statement':'Replaced'})
    with pytest.raises(InvalidProject): q.answer(study.id, 's1', first['id'], **{**reply,'actor':A})
    with pytest.raises(InvalidProject): q.answer(study.id, 's1', first['id'], **{**reply,'evidence':[{**identity(image),'session':'other'}]})
