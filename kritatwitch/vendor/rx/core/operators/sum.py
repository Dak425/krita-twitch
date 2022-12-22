from typing import Callable, Optional

from kritatwitch.vendor.rx import operators as ops
from kritatwitch.vendor.rx.core import Observable, pipe
from kritatwitch.vendor.rx.core.typing import Mapper


def _sum(key_mapper: Optional[Mapper] = None) -> Callable[[Observable], Observable]:
    if key_mapper:
        return pipe(
            ops.map(key_mapper),
            ops.sum()
        )

    return ops.reduce(seed=0, accumulator=lambda prev, curr: prev + curr)
