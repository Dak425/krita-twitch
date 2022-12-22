from typing import Callable, Optional
from kritatwitch.vendor.rx.core import Observable, pipe
from kritatwitch.vendor.rx.core.typing import Predicate

from kritatwitch.vendor.rx import operators as ops


def _count(predicate: Optional[Predicate] = None) -> Callable[[Observable], Observable]:

    if predicate:
        filtering = ops.filter(predicate)
        return pipe(filtering, ops.count())

    counter = ops.reduce(lambda n, _: n + 1, seed=0)
    return pipe(counter)
