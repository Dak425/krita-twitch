from typing import Any

from kritatwitch.vendor.rx.core import Observable
from kritatwitch.vendor.rx.core.typing import Mapper, Predicate
from kritatwitch.vendor.rx.scheduler import CurrentThreadScheduler
from kritatwitch.vendor.rx.disposable import MultipleAssignmentDisposable


def _generate(initial_state: Any,
              condition: Predicate,
              iterate: Mapper
              ) -> Observable:

    def subscribe(observer, scheduler=None):
        scheduler = scheduler or CurrentThreadScheduler.singleton()
        first = True
        state = initial_state
        mad = MultipleAssignmentDisposable()

        def action(scheduler, state1=None):
            nonlocal first
            nonlocal state

            has_result = False
            result = None

            try:
                if first:
                    first = False
                else:
                    state = iterate(state)

                has_result = condition(state)
                if has_result:
                    result = state

            except Exception as exception:  # pylint: disable=broad-except
                observer.on_error(exception)
                return

            if has_result:
                observer.on_next(result)
                mad.disposable = scheduler.schedule(action)
            else:
                observer.on_completed()

        mad.disposable = scheduler.schedule(action)
        return mad
    return Observable(subscribe)
