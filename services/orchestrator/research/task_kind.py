"""Shared task classification. Coordination/grants never represent scientific execution."""


def scientific(task):
    return task.get('specification', {}).get('provider') not in ('collaboration', 'scientific-gcp-grant')


def coordination_counts(tasks):
    counts = dict(waiting=0, claimed=0, reports=0, stopped=0, unresolvedAdmissions=0)
    for task in tasks.values():
        if task.get('specification', {}).get('provider') != 'collaboration':
            continue
        key = {'planned': 'waiting', 'running': 'claimed', 'completed': 'reports',
               'cancelled': 'stopped'}.get(task.get('status'))
        if key:
            counts[key] += 1
        counts['unresolvedAdmissions'] += len(task.get('collaboration', {}).get('pendingAdmissions', {}))
    return counts


def budgeted(task):
    # A resource grant may hold budget without being an experiment.
    return task.get('specification', {}).get('provider') != 'collaboration'
