"""Connect durable effort admission to one configured managed runtime."""
from .efforts import KEY, FINAL
from ..scientific_projects import InvalidProject


class Dispatcher:
    def __init__(self, efforts, runtime):
        self.efforts, self.runtime = efforts, runtime

    def start(self, effort, session, request):
        saved = self.efforts.claim(effort, session, request)
        task = saved.document['state'][KEY]['tasks'][request]
        try:
            identity = self.runtime.start(task_key=f'{session}/{effort}/{request}', query=task['specification'])
            return self.efforts.started(effort, session, request, identity)
        except Exception:
            self.efforts.uncertain(effort, session, request,
                                  'Dispatch did not return a durable execution receipt. Reconcile before running again.')
            raise

    def reconcile(self, effort, session, request):
        saved = self.efforts.read(effort, session)
        task = saved.document['state'][KEY]['tasks'].get(request)
        if task is None:
            raise InvalidProject('Unknown research task')
        if task['status'] in FINAL:
            return saved
        if task['execution'] is None:
            raise InvalidProject('No execution identity was retained; verify the provider operation before continuing')
        if task['status'] == 'cancel_requested':
            self.runtime.cancel(task['execution'])
        result = self.runtime.inspect(task['execution'])
        if result['status'] in FINAL:
            if task['status'] == 'cancel_requested' and result['status'] == 'completed':
                return self.efforts.uncertain(effort, session, request,
                    'The provider completed after Stop. Its output remains unaccepted; reconcile usage and cancellation.')
            return self.efforts.finish(effort, session, request, status=result['status'], result=result['receipt'])
        return self.efforts.read(effort, session)
