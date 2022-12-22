from typing import Iterable

from kritatwitch.vendor.rx.disposable import Disposable
from kritatwitch.vendor.rx.core import Observable
from kritatwitch.vendor.rx.disposable import SingleAssignmentDisposable, CompositeDisposable, SerialDisposable
from kritatwitch.vendor.rx.scheduler import CurrentThreadScheduler


def _concat_with_iterable(sources: Iterable[Observable]) -> Observable:

    def subscribe(observer, scheduler_=None):
        _scheduler = scheduler_ or CurrentThreadScheduler.singleton()

        sources_ = iter(sources)

        subscription = SerialDisposable()
        cancelable = SerialDisposable()
        is_disposed = False

        def action(action1, state=None):
            nonlocal is_disposed
            if is_disposed:
                return

            def on_completed():
                cancelable.disposable = _scheduler.schedule(action)

            try:
                current = next(sources_)
            except StopIteration:
                observer.on_completed()
            except Exception as ex:  # pylint: disable=broad-except
                observer.on_error(ex)
            else:
                d = SingleAssignmentDisposable()
                subscription.disposable = d
                d.disposable = current.subscribe_(observer.on_next, observer.on_error, on_completed, scheduler_)

        cancelable.disposable = _scheduler.schedule(action)

        def dispose():
            nonlocal is_disposed
            is_disposed = True

        return CompositeDisposable(subscription, cancelable, Disposable(dispose))
    return Observable(subscribe)
