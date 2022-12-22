from typing import Callable
from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable, pipe


def _is_empty() -> Callable[[Observable], Observable]:
    """Determines whether an observable sequence is empty.

    Returns:
        An observable sequence containing a single element
        determining whether the source sequence is empty.
    """

    return pipe(
        ops.some(),
        ops.map(lambda b: not b)
    )
