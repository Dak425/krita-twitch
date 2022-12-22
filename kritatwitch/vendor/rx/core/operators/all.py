from typing import Callable

from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable, pipe
from kritatwitch.vendor.rx.core.typing import Predicate


def _all(predicate: Predicate) -> Callable[[Observable], Observable]:

    filtering = ops.filter(lambda v: not predicate(v))
    mapping = ops.map(lambda b: not b)
    some = ops.some()

    return pipe(
        filtering,
        some,
        mapping
    )
