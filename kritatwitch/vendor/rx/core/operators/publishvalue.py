from typing import Any, Callable, Optional

from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable
from kritatwitch.vendor.rx.subject import BehaviorSubject
from kritatwitch.vendor.rx.core.typing import Mapper


def _publish_value(initial_value: Any, mapper: Optional[Mapper] = None) -> Callable[[Observable], Observable]:
    if mapper:
        def subject_factory(scheduler):
            return BehaviorSubject(initial_value)

        return ops.multicast(subject_factory=subject_factory, mapper=mapper)
    return ops.multicast(BehaviorSubject(initial_value))
